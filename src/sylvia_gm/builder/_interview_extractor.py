"""Legacy one-shot extraction of interview history into InterviewManifest.

This helper belongs to the original transcript-extraction pipeline. Current
Campaign Interview state and construction are coordinated by application.authority
and agent._builder_session under the contracts listed in docs/INDEX.md.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from sylvia_gm.builder._interview_manifest import InterviewManifest
from sylvia_gm.provider._base import Provider, ProviderResponse

logger = logging.getLogger(__name__)

# ── Extraction System Prompt ──────────────────────────────────────

EXTRACTION_SYSTEM_PROMPT = """You are an interview answer extractor. Given a transcript of a TTRPG campaign
builder interview, extract the following structured information as JSON:

{
  "version": "1.0",
  "campaign_id": "provided by caller",
  "extracted_at": "ISO timestamp provided by caller",
  "genre": "high fantasy | dark fantasy | sci-fi | cyberpunk | post-apocalyptic | horror | ...",
  "setting": "2-3 sentence description of the world",
  "tone": "dark | heroic | whimsical | gritty | mysterious | mixed",
  "world_name": "name of the world",
  "content_boundaries": ["list", "of", "things", "to", "avoid"],
  "difficulty": "easy | normal | hard | deadly",
  "character_concept": "the player's character concept as described",
  "character_block": {
    "name": "...",
    "species": "...",
    "archetype": "...",
    "max_hp": 18,
    "hp": 18,
    "ac": 10,
    "abilities": {"str": 10, "dex": 10, "wil": 10},
    "traits": [],
    "backstory": "...",
    "personality": "..."
  },
  "raw_answers": {"question_id": "user's answer", ...}
}

RULES:
1. genre, setting, tone, character_concept are REQUIRED and non-empty.
2. If the interview doesn't specify a value, use reasonable defaults.
3. tone MUST be one of: dark, heroic, whimsical, gritty, mysterious, mixed.
4. difficulty MUST be one of: easy, normal, hard, deadly.
5. Output ONLY valid JSON. No markdown wrappers. No code blocks."""


class ExtractionError(RuntimeError):
    """Raised when manifest extraction fails after retries."""


class InterviewExtractor:
    """One-shot LLM call to extract structured manifest from interview history.

    Walks the builder _history list, collects all widget answers,
    builds a single LLM call, validates the result against
    InterviewManifest, and writes the manifest to disk.
    """

    EXTRACTION_TIMEOUT = 120.0  # seconds — extraction can be a large prompt

    def __init__(self, data_dir: Path) -> None:
        """Initialise with the data directory for manifest persistence.

        Args:
            data_dir: Root data directory (typically ~/.sylvia-gm/).
        """
        self._data_dir = Path(data_dir)

    # ── Public API ───────────────────────────────────────────────

    async def extract(
        self,
        history: list[dict[str, Any]],
        provider: Provider,
        campaign_id: str,
    ) -> InterviewManifest:
        """Extract a structured InterviewManifest from builder interview history.

        Args:
            history: The full _history list from AgentRunner. Contains
                user messages, assistant responses, and tool results
                including WIDGET_ANSWER entries.
            provider: LLM provider for the extraction call.
            campaign_id: Campaign identifier for the manifest metadata.

        Returns:
            Parsed and validated InterviewManifest (also written to disk).

        Raises:
            ExtractionError: If extraction fails after the retry path.
        """
        # 1. Collect raw answers from tool results
        raw_answers = self._collect_raw_answers(history)
        logger.info(
            "Collected %d raw answers for campaign %s",
            len(raw_answers), campaign_id,
        )

        # 2. Build the extraction prompt
        user_prompt = self._build_extraction_prompt(history, raw_answers)

        # 3. One-shot LLM call with JSON mode
        manifest = await self._extract_with_retry(
            provider, user_prompt, campaign_id, raw_answers,
        )

        # 4. Write to disk atomically
        self._write_manifest(manifest, campaign_id)

        return manifest

    # ── Private: Raw Answer Collection ────────────────────────────

    def _collect_raw_answers(self, history: list[dict[str, Any]]) -> dict[str, str]:
        """Walk history and extract widget answers from tool results.

        Scans for tool messages responding to ask_question tool calls.
        The tool result content is JSON with 'widget_id' and 'answer' keys.

        Later answers for the same widget_id overwrite earlier ones
        (the player may have changed their answer).
        """
        raw_answers: dict[str, str] = {}

        # Build a map of tool_call_id → widget_id from pending_widget tracking.
        # We need to find tool results that correspond to ask_question calls.
        # Strategy: scan assistant messages for ask_question tool calls,
        # then find the matching tool result by tool_call_id.
        call_id_to_widget: dict[str, str] = {}
        for msg in history:
            if msg.get("role") != "assistant":
                continue
            tool_calls = msg.get("tool_calls", [])
            for tc in tool_calls:
                if tc.get("function", {}).get("name") == "ask_question":
                    try:
                        args = json.loads(tc["function"].get("arguments", "{}"))
                    except json.JSONDecodeError:
                        continue
                    wid = args.get("id", "")
                    if wid:
                        call_id_to_widget[tc["id"]] = wid

        # Now scan tool messages for answers
        for msg in history:
            if msg.get("role") != "tool":
                continue
            tcid = msg.get("tool_call_id", "")
            if tcid not in call_id_to_widget:
                continue

            widget_id = call_id_to_widget[tcid]
            try:
                result = json.loads(msg.get("content", "{}"))
            except json.JSONDecodeError:
                result = {}
            answer = result.get("answer", "")
            if answer:
                raw_answers[widget_id] = answer

        return raw_answers

    # ── Private: Prompt Building ──────────────────────────────────

    def _build_extraction_prompt(
        self,
        history: list[dict[str, Any]],
        raw_answers: dict[str, str],
    ) -> str:
        """Build the user prompt from interview history and raw answers.

        Includes a transcript summary (user/assistant messages) and the
        collected raw answers dictionary.
        """
        lines: list[str] = [
            "TRANSCRIPT OF BUILDER INTERVIEW (user/assistant messages only):",
            "",
        ]

        for msg in history:
            role = msg.get("role", "")
            if role == "user":
                content = msg.get("content", "")
                if content.startswith("WIDGET_ANSWER|"):
                    # Redact WIDGET_ANSWER prefixes to keep the transcript clean
                    parts = content.split("|", 2)
                    if len(parts) >= 3:
                        content = f"[Answered '{parts[1]}']: {parts[2]}"
                lines.append(f"USER: {content}")
            elif role == "assistant":
                content = msg.get("content", "")
                if content:
                    lines.append(f"ASSISTANT: {content}")

        lines.append("")
        lines.append("RAW ANSWERS COLLECTED:")
        lines.append(json.dumps(raw_answers, indent=2))
        lines.append("")
        lines.append("Extract the structured campaign manifest from the above interview transcript.")
        lines.append("Output ONLY valid JSON matching the schema described in the system prompt.")

        return "\n".join(lines)

    # ── Private: LLM Extraction with Retry ────────────────────────

    async def _extract_with_retry(
        self,
        provider: Provider,
        user_prompt: str,
        campaign_id: str,
        raw_answers: dict[str, str],
    ) -> InterviewManifest:
        """Make the extraction LLM call with one retry on validation failure."""
        system_prompt = EXTRACTION_SYSTEM_PROMPT
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        result = await provider.generate(
            messages=messages,
            tools=None,
            stream=False,
            timeout=self.EXTRACTION_TIMEOUT,
        )

        # Parse and validate, retry once on failure
        manifest = self._parse_and_validate(
            result, campaign_id, raw_answers,
        )
        if manifest is not None:
            return manifest

        # Retry with error feedback
        logger.info(
            "First extraction attempt failed; retrying with error feedback "
            "for campaign %s", campaign_id,
        )
        retry_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
            {"role": "assistant", "content": result.content or ""},
            {
                "role": "user",
                "content": (
                    "The previous output was invalid. Please fix and return "
                    "valid JSON matching the schema described in the system prompt. "
                    "Output ONLY the JSON object — no markdown wrappers, no code blocks."
                ),
            },
        ]

        result = await provider.generate(
            messages=retry_messages,
            tools=None,
            stream=False,
            timeout=self.EXTRACTION_TIMEOUT,
        )

        manifest = self._parse_and_validate(
            result, campaign_id, raw_answers,
        )
        if manifest is not None:
            return manifest

        raise ExtractionError(
            f"Failed to extract valid InterviewManifest for campaign "
            f"'{campaign_id}' after retry."
        )

    def _parse_and_validate(
        self,
        result: ProviderResponse,
        campaign_id: str,
        raw_answers: dict[str, str],
    ) -> InterviewManifest | None:
        """Attempt to parse and validate the LLM response.

        Returns the InterviewManifest on success, None on failure.
        """
        content = result.content or ""
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code blocks
            import re
            match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', content)
            if not match:
                logger.warning(
                    "LLM output is not valid JSON. Content preview: %s",
                    content[:200],
                )
                return None
            try:
                data = json.loads(match.group(1))
            except json.JSONDecodeError:
                logger.warning(
                    "Extracted markdown block is not valid JSON. "
                    "Content preview: %s",
                    match.group(1)[:200],
                )
                return None

        try:
            manifest = InterviewManifest(**data)
        except ValidationError as e:
            logger.warning(
                "Interview manifest validation failed for campaign %s: %s",
                campaign_id, e,
            )
            return None

        # Enrich with caller-provided metadata
        manifest.campaign_id = campaign_id
        manifest.extracted_at = datetime.now(timezone.utc).isoformat()
        manifest.raw_answers = raw_answers

        return manifest

    # ── Private: Atomic File Write ────────────────────────────────

    def _write_manifest(
        self,
        manifest: InterviewManifest,
        campaign_id: str,
    ) -> None:
        """Write the manifest to disk atomically.

        Uses a temp-file + rename pattern for atomicity.
        """
        campaign_dir = self._data_dir / campaign_id
        campaign_dir.mkdir(parents=True, exist_ok=True)

        manifest_path = campaign_dir / "interview_manifest.json"
        tmp_path = campaign_dir / "interview_manifest.json.tmp"

        # Write to temp file first, then rename for atomicity
        json_text = manifest.model_dump_json(indent=2)
        tmp_path.write_text(json_text, encoding="utf-8")
        tmp_path.replace(manifest_path)

        logger.info(
            "Interview manifest written to %s for campaign %s",
            manifest_path, campaign_id,
        )
