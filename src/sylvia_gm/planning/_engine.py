"""PlanningEngine — one-shot LLM call that generates a complete campaign plan.

Orchestrates the full planning pipeline:
1. Builds system + user prompts from the interview manifest.
2. Makes a single LLM call requesting structured JSON output.
3. Parses and validates the JSON against Pydantic schemas.
4. Cross-validates secret and entity references.
5. Writes all output files atomically via a temp-directory pattern.

Defined in plan: ~/.hermes/plans/interview-planning-pipeline-v3.md §3, §8-9
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from sylvia_gm.builder._interview_manifest import InterviewManifest
from sylvia_gm.planning._campaign_bible import CampaignBible
from sylvia_gm.planning._planning_output import (
    PlanningBudgetError,
    PlanningError,
    PlanningOutput,
    PlanningResult,
    PlanningTimeoutError,
)
from sylvia_gm.planning._prompt import (
    build_planning_system_prompt,
    build_planning_user_prompt,
)
from sylvia_gm.planning._reveal_registry import RevealRegistry
from sylvia_gm.planning._validator import CrossPlanValidator
from sylvia_gm.provider._base import Provider, ProviderResponse
from sylvia_gm.provider._exceptions import (
    ProviderTimeoutError,
    TokenLimitError,
)

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────

_PLANNING_TIMEOUT_SEC = 60
_MAX_RETRIES = 1  # one retry after initial failure


# ── PlanningEngine ─────────────────────────────────────────────────────────


class PlanningEngine:
    """Runs a one-shot planning call, writes all output files atomically.

    The engine takes a validated InterviewManifest (already loaded from disk
    by the caller) and an output directory, then:

    1. Builds the planning system + user prompts.
    2. Calls the LLM provider with JSON mode.
    3. Parses, validates, and cross-validates the response.
    4. Writes campaign_bible.json, reveal_registry.json, and chapter_*.md
       files atomically to the output directory.

    Usage::

        engine = PlanningEngine(provider=provider, output_dir=planning_dir)
        result = await engine.generate(manifest)
        if result.success:
            print(f"Wrote {len(result.files_written)} files")
        else:
            print(f"Planning failed: {result.error}")
    """

    def __init__(self, provider: Provider, output_dir: Path) -> None:
        """Create a planning engine.

        Args:
            provider: An LLM provider instance (must implement ``Provider``).
            output_dir: Target directory for planning output files,
                e.g. ``~/.sylvia-gm/{campaign_id}/planning/``.
        """
        self._provider = provider
        self._output_dir = Path(output_dir)

    # ── Public API ────────────────────────────────────────────────────

    async def generate(self, manifest: InterviewManifest, ruleset: str) -> PlanningResult:
        """Execute the one-shot planning call.

        Args:
            manifest: The validated interview manifest containing the player's
                campaign preferences, content boundaries, and character data.

        Returns:
            PlanningResult with success/failure state and paths to written files.
        """
        # 1. Validate manifest
        if manifest is None:
            raise PlanningError("No interview manifest provided.")

        # 2. Build prompts
        system_prompt = build_planning_system_prompt()
        user_prompt = build_planning_user_prompt(manifest, ruleset)

        messages: list[dict[str, object]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        # 3. One-shot LLM call with retry on failure
        planning_output = await self._call_with_retry(messages, manifest)

        # 4. Atomic write all files
        return await self._atomic_write(planning_output, manifest)

    # ── LLM Call with Retry ───────────────────────────────────────────

    async def _call_with_retry(
        self,
        messages: list[dict[str, object]],
        manifest: InterviewManifest,
    ) -> PlanningOutput:
        """Call the LLM, retrying once on parse/validation failure.

        On first failure (non-JSON or schema validation), retries with
        the error message appended to the conversation so the model can
        self-correct.
        """
        last_error: str = ""
        last_raw: str = ""

        for attempt in range(_MAX_RETRIES + 1):
            try:
                response = await self._provider.generate(
                    messages=messages,
                    tools=[],
                    timeout=_PLANNING_TIMEOUT_SEC,
                )
                last_raw = response.content or ""
                return self._parse_response(last_raw)

            except asyncio.TimeoutError:
                raise PlanningTimeoutError(
                    "Planning took too long (60s timeout). "
                    "Try again or use a faster model."
                )

            except ProviderTimeoutError as e:
                raise PlanningTimeoutError(
                    f"Provider timed out during planning: {e}"
                ) from e

            except TokenLimitError as e:
                raise PlanningBudgetError(
                    "Campaign is too complex for the model's context window. "
                    "Try a larger model or reduce the manifest detail."
                ) from e

            except PlanningError as e:
                last_error = str(e)

                # On last attempt, re-raise immediately
                if attempt >= _MAX_RETRIES:
                    raise

                # Append error feedback for retry
                messages.append(
                    {
                        "role": "assistant",
                        "content": last_raw or "[response unavailable]",
                    }
                )
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"The previous output was invalid. "
                            f"Please fix the errors and return valid JSON "
                            f"matching the required schema exactly.\n\n"
                            f"Error details: {last_error}"
                        ),
                    }
                )

                logger.warning(
                    "Planning LLM call attempt %d/%d failed with: %s — "
                    "retrying with error feedback",
                    attempt + 1,
                    _MAX_RETRIES + 1,
                    last_error[:200],
                )

        # Should be unreachable — but safety net
        raise PlanningError(
            f"Planning failed after all retry attempts. "
            f"Last error: {last_error}"
        )

    # ── Response Parsing ──────────────────────────────────────────────

    def _parse_response(self, content: str) -> PlanningOutput:
        """Parse LLM JSON output, validate against schemas, cross-validate.

        Handles both bare JSON and JSON wrapped in markdown code blocks.
        """
        # 1. Extract JSON from content (handle markdown code blocks)
        data = self._extract_json(content)

        # 2. Validate against Pydantic models
        try:
            bible = CampaignBible.model_validate(data.get("campaign_bible", {}))
            registry = RevealRegistry.model_validate(
                data.get("reveal_registry", {})
            )
        except ValidationError as e:
            raise PlanningError(
                f"Output failed schema validation: {e}"
            ) from e

        # 3. Cross-validate references
        CrossPlanValidator.validate(bible, registry)

        # 4. Validate chapters list
        chapters = data.get("chapters", [])
        if not isinstance(chapters, list):
            raise PlanningError(
                f"'chapters' must be a list, got {type(chapters).__name__}"
            )

        for i, ch in enumerate(chapters):
            if not isinstance(ch, dict):
                raise PlanningError(
                    f"Chapter {i} must be a dict, got {type(ch).__name__}"
                )
            if "frontmatter" not in ch:
                raise PlanningError(
                    f"Chapter {i} missing required 'frontmatter' key"
                )
            fm = ch.get("frontmatter", {})
            if not isinstance(fm, dict):
                raise PlanningError(
                    f"Chapter {i} 'frontmatter' must be a dict, "
                    f"got {type(fm).__name__}"
                )
            if "chapter" not in fm:
                raise PlanningError(
                    f"Chapter {i} 'frontmatter' missing required 'chapter' number"
                )
            if "body" not in ch:
                raise PlanningError(
                    f"Chapter {i} missing required 'body' key (markdown content)"
                )

        return PlanningOutput(
            campaign_bible=bible,
            chapters=chapters,
            reveal_registry=registry,
        )

    @staticmethod
    def _extract_json(content: str) -> dict[str, object]:
        """Parse JSON from LLM content, handling markdown code blocks.

        Tries bare JSON first, then falls back to extracting from
        ```json ... ``` or ``` ... ``` code blocks.

        Raises:
            PlanningError: If the content is not valid JSON.
        """
        stripped = content.strip()

        # Try bare JSON first
        try:
            data = json.loads(stripped)
            if isinstance(data, dict):
                return data  # type: ignore[return-value]
        except json.JSONDecodeError:
            pass

        # Try extracting from markdown code blocks
        match = re.search(
            r"```(?:json)?\s*([\s\S]*?)\s*```", stripped
        )
        if match:
            try:
                data = json.loads(match.group(1))
                if isinstance(data, dict):
                    return data  # type: ignore[return-value]
            except json.JSONDecodeError as e:
                raise PlanningError(
                    f"LLM output is not valid JSON (even after extracting "
                    f"from code block): {e}"
                ) from e

        # Try finding any JSON object in the text
        match = re.search(r"\{[\s\S]*\}", stripped)
        if match:
            try:
                data = json.loads(match.group(0))
                if isinstance(data, dict):
                    return data  # type: ignore[return-value]
            except json.JSONDecodeError:
                pass

        raise PlanningError(
            f"LLM output is not valid JSON. "
            f"First 200 chars of response: {stripped[:200]}"
        )

    # ── Atomic Write (§9) ─────────────────────────────────────────────

    async def _atomic_write(
        self,
        output: PlanningOutput,
        manifest: InterviewManifest,
    ) -> PlanningResult:
        """Write all planning files atomically using a temp staging directory.

        Strategy from plan §9:
        1. Write all files to a temp directory in the same parent as the
           target directory (same filesystem → rename is atomic).
        2. If the target directory already exists, back it up first.
        3. Rename the temp directory to the target directory.
        4. On any failure, clean up the temp directory.
        """
        planning_dir = self._output_dir
        campaign_id = manifest.campaign_id

        temp_dir = Path(
            tempfile.mkdtemp(
                prefix=f".planning-{campaign_id}-",
                dir=str(planning_dir.parent),
            )
        )

        try:
            # 1. Write campaign bible
            bible_path = temp_dir / "campaign_bible.json"
            bible_path.write_text(
                output.campaign_bible.model_dump_json(indent=2),
                encoding="utf-8",
            )

            # 2. Write reveal registry
            registry_path = temp_dir / "reveal_registry.json"
            registry_path.write_text(
                output.reveal_registry.model_dump_json(indent=2),
                encoding="utf-8",
            )

            # 3. Write chapter files
            files_written: list[str] = [
                bible_path.name,
                registry_path.name,
            ]
            for chapter in output.chapters:
                fm = chapter.get("frontmatter", {})
                chapter_num = fm.get("chapter", 0)
                chapter_path = temp_dir / f"chapter_{chapter_num:02d}.md"
                chapter_path.write_text(
                    self._format_chapter_md(chapter),
                    encoding="utf-8",
                )
                files_written.append(chapter_path.name)

            # 4. Ensure parent dir exists
            planning_dir.parent.mkdir(parents=True, exist_ok=True)

            # 5. Backup existing planning directory if present
            if planning_dir.exists():
                backup_dir = planning_dir.with_name(
                    f"planning.backup-{int(datetime.now().timestamp())}"
                )
                # Remove stale backup if exists
                if backup_dir.exists():
                    shutil.rmtree(backup_dir, ignore_errors=True)
                shutil.move(str(planning_dir), str(backup_dir))
                logger.info(
                    "Backed up existing planning dir to %s", backup_dir
                )

            # 6. Atomic rename — temp_dir → planning_dir
            shutil.move(str(temp_dir), str(planning_dir))

            logger.info(
                "Planning complete — wrote %d files to %s",
                len(files_written),
                planning_dir,
            )

            return PlanningResult(
                success=True,
                planning_dir=planning_dir,
                files_written=files_written,
            )

        except OSError as e:
            # Clean up temp on failure
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise PlanningError(
                f"Cannot write to planning directory. Check permissions: {e}"
            ) from e

        except Exception:
            # Clean up temp on any failure
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise PlanningError(
                "Atomic write failed — partial output has been removed."
            )

    # ── Chapter Markdown Formatting ───────────────────────────────────

    @staticmethod
    def _format_chapter_md(chapter: dict[str, object]) -> str:
        """Format a chapter dict as markdown with YAML frontmatter.

        Args:
            chapter: Dict with 'frontmatter' (dict) and 'body' (str) keys.

        Returns:
            Formatted markdown string with YAML frontmatter block.
        """
        fm = chapter.get("frontmatter", {})
        body = chapter.get("body", "")

        # Build frontmatter as simple YAML-compatible block
        lines = ["---"]
        for key, value in fm.items():
            if isinstance(value, list):
                lines.append(f"{key}:")
                for item in value:
                    if isinstance(item, dict):
                        lines.append(f"  - {json.dumps(item)}")
                    else:
                        # Quote strings that might contain special chars
                        item_str = str(item)
                        if (
                            ":" in item_str
                            or "#" in item_str
                            or item_str.startswith("{")
                        ):
                            lines.append(f'  - "{item_str}"')
                        else:
                            lines.append(f"  - {item_str}")
            else:
                # Quote string values that contain special YAML chars
                val_str = str(value)
                if (
                    isinstance(value, str)
                    and (":" in val_str or "#" in val_str or val_str.startswith("{"))
                ):
                    lines.append(f'{key}: "{val_str}"')
                else:
                    lines.append(f"{key}: {val_str}")
        lines.append("---")
        lines.append("")

        # Append body
        body_str = str(body) if body else ""
        lines.append(body_str.strip())

        return "\n".join(lines) + "\n"
