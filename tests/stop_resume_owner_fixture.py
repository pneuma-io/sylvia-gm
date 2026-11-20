"""A real owner for the Stop and resume contract fixtures, with an offline scripted GM.

The child process runs the production owner. Only its model provider is replaced: the scripted
GM reads the latest player message and holds (until Stop), fails once (to offer Retry), or answers.
No real provider, credential, network call or model subprocess is used.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sylvia_gm.provider._base import ProviderStreamChunk
from sylvia_gm.provider._exceptions import ProviderConnectionError
from tests.test_alpha_f6e_owner_session_transport import _campaign_update_tool, _ScriptedProvider

ROOT = Path(__file__).resolve().parents[1]
HOLD = "[hold]"
FAIL = "[fail]"
PARTIAL = "Partial narration that Stop must discard."


def scripted_reply(player_text: str) -> str:
    """The narration the scripted GM gives once a message is answered."""

    return "The Game Master answers: " + re.sub(r"\[(hold|fail|late)\]", "", player_text).strip()


class JourneyGM(_ScriptedProvider):
    """Hold on [hold] until cancelled, fail [fail] once, otherwise narrate a fixed reply."""

    def __init__(self) -> None:
        super().__init__(())
        self.failed: set[str] = set()

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        from tests.gm_context_contract import latest_player_message

        del timeout, kwargs
        _, player = latest_player_message(messages)
        if HOLD in player:
            yield ProviderStreamChunk(content=PARTIAL)
            await asyncio.Event().wait()
        if FAIL in player and player not in self.failed:
            self.failed.add(player)
            yield ProviderStreamChunk(content=PARTIAL)
            raise ProviderConnectionError("Scripted provider disconnect.")
        reply = scripted_reply(player)
        if tool_name := _campaign_update_tool(tools):
            yield ProviderStreamChunk(
                tool_call_delta={"index": 0, "id": "journey-reply", "function": {
                    "name": tool_name,
                    "arguments": json.dumps({
                        "state_change": "unchanged",
                        "state_change_summary": "No durable state changes.",
                        "revised_player_facing_response": [{"kind": "narration", "text": reply}],
                    }),
                }},
                finish_reason="tool_calls",
            )
            return
        yield ProviderStreamChunk(content=reply, finish_reason="stop")


CHILD = (
    "import sys; "
    "from sylvia_gm.interfaces import owner_process; "
    "from tests.stop_resume_owner_fixture import JourneyGM; "
    "from tests.test_alpha_f6e_owner_session_transport import _HarnessResolver; "
    "owner_process._preinstall_bundled_srd = lambda _bus: None; "
    "owner_process._preinstall_bundled_riichi = lambda _bus: None; "
    "raise SystemExit(owner_process.run("
    "sys.stdin, sys.stdout, provider_resolver=_HarnessResolver(JourneyGM())))"
)


@asynccontextmanager
async def journey_owner(data_root: Path) -> AsyncIterator[Any]:
    """Start the production owner as the WebUI does, with JourneyGM as its only provider."""

    from sylvia_gm.interfaces.webui import OwnerProcess

    original = asyncio.create_subprocess_exec

    async def launch(*args: Any, **kwargs: Any) -> Any:
        return await original(args[0], "-B", "-c", CHILD, **kwargs)

    os.environ["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT)))
    asyncio.create_subprocess_exec = launch  # type: ignore[assignment]
    owner = OwnerProcess(data_root)
    try:
        await owner.start()
    finally:
        asyncio.create_subprocess_exec = original  # type: ignore[assignment]
    try:
        yield owner
    finally:
        await owner.close()


async def start_no_ruleset_campaign(call: Any, name: str) -> dict[str, str]:
    """Drive the real New Campaign interview with No Ruleset; return its ids and opening turn."""

    draft = await call("create_guided_campaign_draft", {
        "operation_id": f"{name}-draft", "display_name": name,
        "provider_id": "test.transport", "model_id": "test.transport/model0",
    })
    campaign_id, builder = draft["campaign_id"], draft["builder_session_id"]
    answers = (
        ("ruleset", {"candidate_id": None}),
        ("name", {"display_name": name}),
        ("boundaries_preferences", {"boundaries": "None", "preferences": "Mystery"}),
        ("specific_requests", {"no_additional_requests": True, "requests": ""}),
    )
    for revision, (step, answer) in enumerate(answers):
        await call("advance_guided_campaign_draft", {
            "operation_id": f"{name}-{step}", "campaign_id": campaign_id,
            "builder_session_id": builder, "expected_construction_revision": revision,
            "current_step": step, "visible_answer": str(answer), "answer": answer,
        })
    await call("start_guided_campaign", {
        "operation_id": f"{name}-start", "campaign_id": campaign_id,
        "builder_session_id": builder, "display_name": name, "candidate_id": None,
        "setting_id": None, "plan_id": None, "player_character_id": None,
        "companion_ids": [], "premise": "A courier shelters from a storm.",
    })
    finalized = await call("finalize_campaign_construction", {
        "campaign_id": campaign_id, "dm_provider_id": "test.transport",
        "dm_model_id": "test.transport/model0",
    })
    return {"campaign_id": campaign_id, "session_id": finalized["handoff"]["dm_session_id"],
            "turn_id": finalized["dm_turn_id"]}


if __name__ == "__main__":  # pragma: no cover - manual capture aid
    sys.exit("Import this module from a test.")
