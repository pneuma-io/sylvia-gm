"""No Ruleset campaigns can use a structured Campaign Plan whose own entities are its world."""

from __future__ import annotations

import copy
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.ruleless_play import RULELESS_GM_GUIDE
from sylvia_gm.domain._content_schema_plan import EXAMPLE
from sylvia_gm.domain.authority import PackageLock
from sylvia_gm.domain.campaign_plan import PackageBinding, validate_campaign_plan
from sylvia_gm.domain.package_library import closure_sha256
from sylvia_gm.provider._base import ProviderStreamChunk
from tests.test_alpha_f6e_owner_session_transport import (
    _campaign_update_tool,
    _OwnerHarness,
    _ScriptedProvider,
)

EMPTY_CLOSURE = closure_sha256(PackageLock(entries=()))
_ASK_ABOUT_KEY = "I ask Warden Ilse about the brass key on her belt."
_WHERE_IS_KEY = "Where is the brass key now, and what is it for?"
_KEY_HANDED_OVER = "Ilse presses the brass key into your palm: 'The lamp room. Wind it twice.'"
_KEY_RECALLED = "The brass key is in your coat pocket; it opens the lamp-room lock."

PLAN_MARKDOWN = (
    "# Greyhook Light\n\nA courier shelters at a storm-bound lighthouse whose foghorn has "
    "fallen silent. Restore the light before the fishing fleet returns.\n"
)


def no_ruleset_plan() -> dict[str, Any]:
    """Return a complete No Ruleset Plan whose world is its own entities."""

    def ref(identity: str) -> dict[str, str]:
        return {"id": identity, "path": f"entities/{identity}"}

    return {
        "format_id": "sylvia-gm-campaign-plan",
        "format_version": 2,
        "id": "greyhook-light",
        "revision": 1,
        "title": "Greyhook Light",
        "premise": "A courier shelters at a lighthouse whose foghorn has fallen silent.",
        "tone": "Quiet coastal mystery",
        "boundaries": [],
        "intended_player_experience": "Explore, talk and decide how to restore the light.",
        "opening_direction": "Open in the lighthouse kitchen as the storm worsens.",
        "bindings": {
            "ruleset": None,
            "campaign_setting": None,
            "package_closure_sha256": EMPTY_CLOSURE,
        },
        "starting_party": {
            "player_character": {"mode": "compatible"},
            "required_companions": [],
            "custom_companions": {"minimum": 0, "maximum": 0},
        },
        "starting_location": ref("lighthouse"),
        "initial_state": {
            "entities": [
                {
                    "id": "lighthouse",
                    "kind": "place",
                    "source_ref": ref("lighthouse"),
                    "state": {
                        "name": "Greyhook Lighthouse",
                        "description": "A salt-stained tower with a flooded cellar.",
                        "visibility": "player_known",
                        "known_by": ["player"],
                    },
                },
                {
                    "id": "ilse",
                    "kind": "person",
                    "source_ref": ref("ilse"),
                    "state": {
                        "name": "Warden Ilse",
                        "location": "lighthouse",
                        "disposition": "Wary of strangers, devoted to the light",
                        "visibility": "player_known",
                        "known_by": ["player"],
                    },
                },
                {
                    "id": "brass-key",
                    "kind": "object",
                    "source_ref": ref("brass-key"),
                    "state": {
                        "name": "Brass key",
                        "description": "A heavy brass key green with verdigris.",
                        "purpose": "Opens the lamp-room lock; winding it twice restarts "
                                   "the foghorn.",
                        "location": "On Warden Ilse's belt",
                        "visibility": "player_known",
                        "known_by": ["player"],
                    },
                },
            ],
            "facts": ["You are a courier sheltering from a storm at Greyhook Lighthouse."],
        },
        "setting_references": {
            "creatures": [],
            "factions": [],
            "items": [ref("brass-key")],
            "locations": [ref("lighthouse")],
            "lore": [],
            "npcs": [ref("ilse")],
        },
        "structure": [
            {
                "id": "act-1",
                "title": "The silent horn",
                "summary": "The courier learns why the foghorn stopped and can restart it.",
                "situations": ["Ilse guards the only key to the lamp room."],
                "objectives": ["restore-foghorn"],
                "branches": ["Earn Ilse's trust, borrow the key, or find another way up."],
                "consequences": ["A restarted horn guides the fleet home."],
                "planned_encounters": [],
                "dynamic_encounter_guidance": ["Resolve danger in the story, without mechanics."],
                "setting_refs": ["entities/lighthouse", "entities/brass-key"],
            }
        ],
        "secrets": [
            {
                "id": "sea-door",
                "fact": "The brass key also opens the flooded sea-door in the cellar.",
                "known_by": [],
                "reveal": {
                    "condition": "The courier tries the key on the cellar door.",
                    "from_act": "act-1",
                },
            }
        ],
        "objectives": [
            {
                "id": "restore-foghorn",
                "title": "Restore the foghorn",
                "description": "Reach the lamp room and restart the horn.",
                "completion_conditions": [
                    "The courier reaches the lamp room with the brass key.",
                    "Closing event: the foghorn sounds again over the harbour.",
                ],
            }
        ],
        "gm_guidance": {
            "instructions": "Use the Plan's own entities as world authority. There are no "
                            "game mechanics.",
            "player_freedom": "Let the courier choose how to approach Ilse and the key.",
            "preparation_detail": "Reveal the sea-door only when play earns it.",
            "railroading": "Keep the silent horn central without forcing one solution.",
        },
        "completion_conditions": ["The foghorn sounds again."],
        "continuation_conditions": ["The courier may stay to explore the coast."],
        "ending": {
            "mode": "finite",
            "outcomes": [
                {
                    "id": "horn-restored",
                    "conditions": ["The foghorn sounds again over the harbour."],
                    "outcome": "The fleet finds its way home through the fog.",
                    "immediate_aftermath": ["Ilse thanks the courier at the lamp."],
                    "open_world": True,
                    "after_story": None,
                }
            ],
        },
        "failure_conditions": [],
        "side_content": [],
        "unsupported_mechanics": [],
    }


def _validate_no_ruleset(plan: object) -> object:
    return validate_campaign_plan(
        plan,
        readable_markdown=PLAN_MARKDOWN,
        expected_ruleset=None,
        expected_setting=None,
        expected_closure_sha256=EMPTY_CLOSURE,
        setting_paths=frozenset(),
    )


def test_no_ruleset_plan_validates_against_its_own_world() -> None:
    validated = _validate_no_ruleset(no_ruleset_plan())
    assert validated.referenced_setting_paths == (  # type: ignore[attr-defined]
        "entities/brass-key", "entities/ilse", "entities/lighthouse",
    )


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda plan: plan["initial_state"].update(
            mechanics=[{"namespace": "x", "key": "hp", "value": 3}]),
         "Ruleset mechanical state"),
        (lambda plan: plan["initial_state"].update(party_placements=[]),
         "party placements"),
        (lambda plan: plan["initial_state"]["entities"][2].update(
            source_ref={"id": "brass-key", "path": "items/brass-key.md"}),
         "must use source_ref"),
        (lambda plan: plan["initial_state"]["entities"][2]["state"].pop("name"),
         "needs a name"),
        (lambda plan: plan.update(starting_location={"id": "harbour",
                                                     "path": "entities/harbour"}),
         "missing Setting content"),
        (lambda plan: plan["bindings"].update(ruleset={
            "id": "x", "version": "1", "content_sha256": "1" * 64}),
         "bindings do not match"),
        (lambda plan: plan["bindings"].update(package_closure_sha256="3" * 64),
         "bindings do not match"),
    ],
)
def test_no_ruleset_plan_rejects_ruleset_content_and_unresolved_world(
    change: Any, reason: str,
) -> None:
    plan = no_ruleset_plan()
    change(plan)
    with pytest.raises(ValueError, match=reason):
        _validate_no_ruleset(plan)


def test_ruleset_bound_plans_validate_exactly_as_before() -> None:
    example = copy.deepcopy(EXAMPLE)
    bindings = example["bindings"]
    assert isinstance(bindings, dict)
    ruleset = PackageBinding("example.signal-rules", "1.0.0", "1" * 64)
    setting = PackageBinding("example.signal-setting", "1.0.0", "2" * 64)
    validated = validate_campaign_plan(
        example,
        readable_markdown="# Signal Relay\n",
        expected_ruleset=ruleset,
        expected_setting=setting,
        expected_closure_sha256="3" * 64,
        setting_paths=frozenset({"locations/relay.md"}),
    )
    assert validated.referenced_setting_paths == ("locations/relay.md",)
    with pytest.raises(ValueError, match="Ruleset binding must be an object"):
        validate_campaign_plan(
            no_ruleset_plan(),
            readable_markdown=PLAN_MARKDOWN,
            expected_ruleset=ruleset,
            expected_setting=setting,
            expected_closure_sha256=EMPTY_CLOSURE,
            setting_paths=frozenset({"entities/lighthouse"}),
        )


class _PlanAndPlayModel(_ScriptedProvider):
    """Synthetic Builder that writes the Plan files and GM that plays from them."""

    def __init__(self) -> None:
        super().__init__(())
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]] | None]] = []

    @staticmethod
    def _latest_user(messages: list[dict[str, Any]]) -> tuple[int, str]:
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].get("role") == "user":
                return index, str(messages[index].get("content", ""))
        return -1, ""

    def _builder_call(self, messages: list[dict[str, Any]]) -> dict[str, Any] | None:
        index, _player = self._latest_user(messages)
        written = sum(1 for message in messages[index + 1:] if message.get("role") == "tool")
        files = (
            ("campaign-plan.json", json.dumps(no_ruleset_plan(), indent=2)),
            ("campaign-plan.md", PLAN_MARKDOWN),
        )
        if written >= len(files):
            return None
        path, content = files[written]
        return {"name": "write_file", "arguments": {"path": path, "content": content}}

    def _gm_payload(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        _index, player = self._latest_user(messages)
        if _ASK_ABOUT_KEY in player:
            return {
                "state_change": "changed",
                "state_change_summary": "Ilse lent the courier the brass key.",
                "entities": [{
                    "entity_id": "brass-key",
                    "entity_kind": "object",
                    "name": "Brass key",
                    "attributes": {
                        "description": "A heavy brass key green with verdigris.",
                        "purpose": "Opens the lamp-room lock; winding it twice restarts "
                                   "the foghorn.",
                        "location": "In the courier's coat pocket, lent by Ilse",
                    },
                    "visibility": "player_known",
                    "known_by": ["player"],
                }],
                "plan_progress": [{
                    "plan_item_id": "restore-foghorn",
                    "progress_status": "engaged",
                    "progress_summary": "Ilse lent the courier the lamp-room key.",
                }],
                "revised_player_facing_response": [
                    {"kind": "narration", "text": _KEY_HANDED_OVER},
                ],
            }
        if _WHERE_IS_KEY in player:
            return {
                "state_change": "unchanged",
                "state_change_summary": "Recalling the key changes nothing.",
                "revised_player_facing_response": [
                    {"kind": "narration", "text": _KEY_RECALLED},
                ],
            }
        return {
            "state_change": "unchanged",
            "state_change_summary": "The opening restates the prepared footing.",
            "revised_player_facing_response": [{
                "kind": "narration",
                "text": "Rain lashes Greyhook. Warden Ilse watches you from the stove.",
            }],
        }

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        self.calls.append((messages.copy(), tools))
        names = {str(tool["function"]["name"]) for tool in tools or []}
        update_tool = _campaign_update_tool(tools)
        call: dict[str, Any] | None
        if update_tool:
            call = {"name": update_tool, "arguments": self._gm_payload(messages)}
        elif "write_file" in names:
            call = self._builder_call(messages)
        else:
            call = None
        if call is None:
            yield ProviderStreamChunk(content="The Plan is saved.", finish_reason="stop")
            return
        yield ProviderStreamChunk(
            tool_call_delta={
                "index": 0,
                "id": f"call-{len(self.calls)}",
                "function": {"name": call["name"], "arguments": json.dumps(call["arguments"])},
            },
            finish_reason="tool_calls",
        )


def _released(harness: _OwnerHarness, session_id: str, request_id: str) -> list[str]:
    context = harness.request(request_id, "session_context", {"session_id": session_id})
    return [
        str(message["content"]["safe_payload"]["text"])
        for message in context["messages"]
        if message["kind"] == "canonical_reference"
        and isinstance(message["content"].get("safe_payload"), dict)
        and "text" in message["content"]["safe_payload"]
    ]


def _fresh_state(messages: list[dict[str, Any]]) -> str:
    return next(
        str(message["content"]) for message in messages
        if str(message.get("content", "")).startswith("SYLVIA_GM_FRESH_CAMPAIGN_STATE")
    )


def test_no_ruleset_structured_plan_journey_survives_save_and_reopen(tmp_path: Path) -> None:
    database = tmp_path / "plan.sqlite3"
    model = _PlanAndPlayModel()
    harness = _OwnerHarness(database, model)
    try:
        draft = harness.request("draft", "create_guided_campaign_draft", {
            "operation_id": "plan-draft",
            "display_name": "Greyhook",
            "provider_id": "test.transport",
            "model_id": "test.transport/model0",
        })
        campaign_id = draft["campaign_id"]
        builder_session_id = draft["builder_session_id"]
        for revision, (step, answer) in enumerate((
            ("ruleset", {"candidate_id": None}),
            ("name", {"display_name": "Greyhook"}),
            ("boundaries_preferences", {"boundaries": "No gore", "preferences": "Mystery"}),
            ("specific_requests", {"no_additional_requests": True, "requests": ""}),
        )):
            harness.request(f"answer-{step}", "advance_guided_campaign_draft", {
                "operation_id": f"plan-answer-{step}",
                "campaign_id": campaign_id,
                "builder_session_id": builder_session_id,
                "expected_construction_revision": revision,
                "current_step": step,
                "visible_answer": str(answer),
                "answer": answer,
            })
        harness.request("start", "start_guided_campaign", {
            "operation_id": "plan-start",
            "campaign_id": campaign_id,
            "builder_session_id": builder_session_id,
            "display_name": "Greyhook",
            "candidate_id": None,
            "setting_id": None,
            "plan_id": None,
            "player_character_id": None,
            "companion_ids": [],
            "premise": "A courier at a storm-bound lighthouse.",
        })
        # Create: the synthetic Builder writes the structured Plan and its readable copy.
        harness.request("builder-plan", "submit_session_input", {
            "session_id": builder_session_id,
            "turn_id": "turn-builder-plan",
            "attempt_id": "attempt-builder-plan",
            "message_id": "message-builder-plan",
            "user_text": "Write a structured No Ruleset Campaign Plan for Greyhook.",
        })
        workspace = tmp_path / "campaigns" / campaign_id
        assert json.loads((workspace / "campaign-plan.json").read_text("utf-8"))["bindings"] == {
            "ruleset": None, "campaign_setting": None, "package_closure_sha256": EMPTY_CLOSURE,
        }
        # Bind: finalize validates and admits the structured Plan for the empty closure.
        finalized = harness.request("finalize", "finalize_campaign_construction", {
            "campaign_id": campaign_id,
            "dm_provider_id": "test.transport",
            "dm_model_id": "test.transport/model0",
        })
        session_id = finalized["handoff"]["dm_session_id"]
        turn_id = finalized["dm_turn_id"]
        harness.request("open", "start_dm_session", {
            "campaign_id": campaign_id, "session_id": session_id, "turn_id": turn_id,
        })
        opening, opening_tools = model.calls[-1]
        assert RULELESS_GM_GUIDE.splitlines()[0] in "\n".join(
            str(message.get("content", "")) for message in opening
        )
        fresh = _fresh_state(opening)
        for seeded in ("Opens the lamp-room lock", "On Warden Ilse's belt", "Warden Ilse",
                       "Greyhook Lighthouse"):
            assert seeded in fresh
        assert "flooded sea-door" not in fresh
        names = {tool["function"]["name"] for tool in opening_tools or []}
        assert len(names) == 3
        update_schema = next(
            tool["function"]["parameters"] for tool in opening_tools or []
            if tool["function"]["name"].endswith("_update_campaign_file")
        )
        assert "plan_progress" in update_schema["properties"]
        assert "reveals" in update_schema["properties"]

        # Load and play: the GM moves the item and records Plan Progress.
        harness.request("ask", "submit_session_input", {
            "session_id": session_id,
            "turn_id": turn_id,
            "attempt_id": "attempt-plan-ask",
            "message_id": "message-plan-ask",
            "user_text": _ASK_ABOUT_KEY,
        })
        assert _released(harness, session_id, "context-ask")[-1] == _KEY_HANDED_OVER
    finally:
        harness.close()

    # Save and reopen: plan, item identity, location and effect survive.
    with AuthorityOwner.open(database, "owner-plan-reopened") as owner:
        assert owner.current_package_lock(campaign_id).package_lock.entries == ()
        assert owner.catalog(campaign_id) == ()
        accepted = owner.accepted_campaign_plan(campaign_id)
        assert accepted.content["campaign_plan"]["id"] == "greyhook-light"
        key = owner.read_campaign_reference(campaign_id, source="campaign_state",
                                            path="brass-key")
        assert "lent by Ilse" in key["content"]
        assert "restarts the foghorn" in key["content"]
        act = owner.read_campaign_reference(campaign_id, source="campaign_plan",
                                            path="structured/objectives/restore-foghorn")
        assert "Closing event: the foghorn sounds again" in act["content"]
        progress = owner.snapshot(campaign_id).continuity.plan_progress
        assert any(
            item.get("plan_item_id") == "restore-foghorn"
            and item.get("status") == "engaged"
            for item in progress
        )

    resumed = _OwnerHarness(database, model)
    try:
        calls_before = len(model.calls)
        resumed.request("where", "submit_session_input", {
            "session_id": session_id,
            "turn_id": turn_id,
            "attempt_id": "attempt-plan-where",
            "message_id": "message-plan-where",
            "user_text": _WHERE_IS_KEY,
        })
        messages, tools = model.calls[calls_before]
        fresh = _fresh_state(messages)
        assert "lent by Ilse" in fresh
        assert "restarts the foghorn" in fresh
        assert len({tool["function"]["name"] for tool in tools or []}) == 3
        assert _released(resumed, session_id, "context-where")[-1] == _KEY_RECALLED
    finally:
        resumed.close()
