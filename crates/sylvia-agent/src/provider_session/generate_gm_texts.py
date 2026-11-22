"""Export the GM request's long fixed texts as gm_texts.json, value for value.

Run from the repository root with the snapshot's interpreter:

    .venv/Scripts/python.exe -I crates/sylvia-agent/src/provider_session/generate_gm_texts.py

The texts are model-read, so they are read back from the Python functions and constants that
produce them, never retyped:

- `gm_system_prompt`: `_dm_system_prompt(...)`, the saved GM system contract (its arguments
  are unused);
- `gm_system_contract_version`: `_GM_SYSTEM_CONTRACT_VERSION`;
- `gm_context_privacy`: `_GM_CONTEXT_PRIVACY`;
- `gm_model_narration_block`: `_GM_MODEL_NARRATION_BLOCK`;
- `response_format_contract`: `_response_format_contract_message()`'s content;
- `opening_task_prefix`: `_campaign_opening_task_message`'s content before the footing JSON.
- the turn's control messages, each split around its JSON part (`<name>_prefix` and
  `<name>_suffix`): `engine_turn_control` (`_engine_turn_control_message`),
  `campaign_preparation` (`_campaign_preparation_message`), `gm_decision`
  (`_gm_decision_control_message`), `player_boundary` (`_player_boundary_narration_message`),
  `campaign_file_disposition` (`_campaign_file_disposition_message`) and
  `campaign_file_updated_narration` (`_campaign_file_updated_narration_message`);
- `preparation_scope_invalid`: `_preparation_keeps_intent_pending`'s refusal.

Reference tooling for the port; retired with the Python source.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.agent import _provider_session as session  # noqa: E402
from sylvia_gm.agent._stateless_mcp import ModelMechanicsTool  # noqa: E402
from sylvia_gm.application.authority import CommandRejected  # noqa: E402
from sylvia_gm.domain.authority import (  # noqa: E402
    ModelDecisionActor,
    ModelDecisionBoundary,
    ModelDecisionOwner,
    ModelToolBehavior,
)

OUTPUT = Path(__file__).with_name("gm_texts.json")


def split(content: object, data: str) -> tuple[str, str]:
    """The text before and after the one JSON part of a control message."""
    assert isinstance(content, str) and content.count(data) == 1, data
    prefix, suffix = content.split(data)
    return prefix, suffix


def compact(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def control_texts() -> dict[str, str]:
    tool = ModelMechanicsTool(
        package_id="p", package_version="1", package_sha256="0" * 64, name="advance_turn",
        capability="c", input_schema={}, description="Advance.",
        model_behavior=ModelToolBehavior.REQUIRED_ENGINE_TRANSITION)
    engine = session._engine_turn_control_message({"tool_x_advance_turn": tool})  # noqa: SLF001
    assert engine is not None
    boundary = ModelDecisionBoundary(
        owner=ModelDecisionOwner.GM, actor_id="a", actor_name="A", actor_state={"hp": 1},
        campaign_revision=3, active_side_id="s", focused_actor_id="a",
        eligible_actors=(ModelDecisionActor("a", "A", {"hp": 1}),), preparation={"queue": []})
    preparation = session._campaign_preparation_message(boundary)  # noqa: SLF001
    decision = session._gm_decision_control_message(boundary)  # noqa: SLF001
    player = session._player_boundary_narration_message(  # noqa: SLF001
        boundary, current_player_action="x", committed_operations=["o"],
        player_intent_pending=False)
    disposition = session._campaign_file_disposition_message(  # noqa: SLF001
        current_player_action="x")
    updated = session._campaign_file_updated_narration_message(  # noqa: SLF001
        current_player_action="x")
    parts = {
        "engine_turn_control": split(engine["content"], compact({"required_before_narration": [
            {"provider_tool_name": "tool_x_advance_turn", "operation": "advance_turn",
             "description": "Advance."}]})),
        "campaign_preparation": split(preparation["content"], json.dumps(
            {"queue": []}, sort_keys=True, ensure_ascii=False)),
        "gm_decision": split(decision["content"], compact({
            "decision_owner": "gm", "actor_id": "a", "actor_name": "A", "actor_state": {"hp": 1},
            "active_side_id": "s", "focused_actor_id": "a",
            "eligible_actors": [{"actor_id": "a", "actor_name": "A", "actor_state": {"hp": 1}}],
            "campaign_revision": 3})),
        "player_boundary": split(player["content"], compact({
            "decision_owner": "gm", "actor_id": "a", "actor_name": "A", "active_side_id": "s",
            "focused_actor_id": "a", "eligible_actor_ids": ["a"], "campaign_revision": 3,
            "current_player_action": "x", "committed_operations_for_this_request": ["o"],
            "declared_player_intent_pending": False})),
        "campaign_file_disposition": split(disposition["content"],
                                           compact({"current_player_action": "x"})),
        "campaign_file_updated_narration": split(updated["content"],
                                                 compact({"current_player_action": "x"})),
    }
    texts: dict[str, str] = {}
    for name, (prefix, suffix) in parts.items():
        texts[name + "_prefix"] = prefix
        texts[name + "_suffix"] = suffix
    try:
        session._preparation_keeps_intent_pending("other", pending=False)  # noqa: SLF001
    except CommandRejected as error:
        texts["preparation_scope_invalid"] = str(error)
        assert error.code == "provider_player_intent_scope_invalid"
    return texts


def main() -> None:
    prompts = {(mechanics, tools): session._dm_system_prompt(  # noqa: SLF001
        campaign_has_mechanics=mechanics, tools_available=tools)
        for mechanics in (False, True) for tools in (False, True)}
    assert len(set(prompts.values())) == 1, "the GM system prompt depends on its arguments"
    opening = session._campaign_opening_task_message({})["content"]  # noqa: SLF001
    assert isinstance(opening, str) and opening.endswith("{}")
    exported = {
        "gm_system_prompt": prompts[(False, False)],
        "gm_system_contract_version": session._GM_SYSTEM_CONTRACT_VERSION,  # noqa: SLF001
        "gm_context_privacy": session._GM_CONTEXT_PRIVACY,  # noqa: SLF001
        "gm_model_narration_block": session._GM_MODEL_NARRATION_BLOCK,  # noqa: SLF001
        "response_format_contract":
            session._response_format_contract_message()["content"],  # noqa: SLF001
        "opening_task_prefix": opening[: -len("{}")],
        **control_texts(),
    }
    text = json.dumps(exported, ensure_ascii=True, indent=1, allow_nan=False) + chr(10)
    assert json.loads(text) == exported
    OUTPUT.write_text(text, encoding="utf-8", newline=chr(10))
    print(len(exported), "values,", len(text), "bytes")


if __name__ == "__main__":
    main()
