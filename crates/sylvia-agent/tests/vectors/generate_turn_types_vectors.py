"""Generate reference vectors for the GM turn's types and decisions (T1).

Run from the repository root with the snapshot's interpreter:

    .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_turn_types_vectors.py

Writes `turn_types_vectors.json`, every value read back from the Python functions:

- `decisions`: every `_provider_turn_decisions` predicate over seeded random turn states, each
  local assigned or left unassigned, with its result or the `UnboundLocalError` it raises;
- `operation_category` and `attempt_progress` (`_activity`), the progress driven by a fake clock;
- `provider_control`: scripted `ProviderControl` cancellation and steering sequences;
- `control_texts`: the turn's control messages over sample boundaries and tools;
- `preparation_scope`: `_preparation_keeps_intent_pending`;
- `rejection_feedback`: `_mechanics_rejection_feedback` over sample `MechanicsRejected`;
- `private_continuation`: `ProviderEvent.private_continuation`.

Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.agent import _provider_session as session  # noqa: E402
from sylvia_gm.agent import _provider_turn_decisions as decisions  # noqa: E402
from sylvia_gm.agent._activity import AttemptProgress, operation_category  # noqa: E402
from sylvia_gm.agent._provider_turn_state import ProviderTurnState  # noqa: E402
from sylvia_gm.agent._session_loop import (  # noqa: E402
    ProviderControl,
    ProviderEvent,
    _mechanics_rejection_feedback,
)
from sylvia_gm.agent._stateless_mcp import ModelMechanicsTool  # noqa: E402
from sylvia_gm.application.authority import CommandRejected  # noqa: E402
from sylvia_gm.domain.authority import (  # noqa: E402
    ModelDecisionActor,
    ModelDecisionBoundary,
    ModelDecisionOwner,
    ModelToolBehavior,
    WriterRole,
)
from sylvia_gm.domain.errors import MechanicsRejected, MechanicsValidationIssue  # noqa: E402

OUTPUT = Path(__file__).with_name("turn_types_vectors.json")
RANDOM = random.Random(20261123)


def tool(name: str, behavior: str = "optional", category: str = "mechanic",
         description: str = "") -> ModelMechanicsTool:
    return ModelMechanicsTool(
        package_id="sylvia.test", package_version="1.0.0", package_sha256="0" * 64, name=name,
        capability="test", input_schema={"type": "object"}, description=description,
        category=category, model_behavior=ModelToolBehavior(behavior))


def boundary(case: dict[str, object]) -> ModelDecisionBoundary:
    return ModelDecisionBoundary(
        owner=ModelDecisionOwner(case["owner"]), actor_id=case["actor_id"],
        actor_name=case["actor_name"], actor_state=case["actor_state"],
        campaign_revision=case["campaign_revision"], active_side_id=case["active_side_id"],
        focused_actor_id=case["focused_actor_id"],
        eligible_actors=tuple(ModelDecisionActor(a["actor_id"], a["actor_name"], a["actor_state"])
                              for a in case["eligible_actors"]),
        preparation=case["preparation"])


# Each local the decisions read, with JSON samples; the Rust test builds the same values.
BOOL = [False, True]
SAMPLES: dict[str, list[object]] = {
    "obligations.buffered_narration_needs_disposition": BOOL,
    "obligations.campaign_file_narration_pending": BOOL,
    "obligations.force_campaign_file_update": BOOL,
    "obligations.pending_failed_tool_call": BOOL,
    "obligations.player_intent_pending": BOOL,
    "obligations.player_intent_resolution_required": BOOL,
    "obligations.engine_transition_review_pending": BOOL,
    "obligations.provisional_tool_prose_pending": BOOL,
    "progress.campaign_file_disposition_in_exchange": BOOL,
    "progress.any_commit_in_exchange": BOOL,
    "progress.campaign_file_commit_in_exchange": BOOL,
    "progress.state_changing_call_submitted": BOOL,
    "progress.round_failed": BOOL,
    "decisions.gm_boundary_decision": BOOL,
    "decisions.player_boundary_narration": BOOL,
    "buffered_narration": [None, "", "Held narration."],
    "required_transitions": [0, 1, 2],
    "participant_ooc_only": BOOL,
    "round_has_non_narrative_call": BOOL,
    "narration": ["", "The keeper nods.", "The player wants to go north.\nThe road is wet.",
                  "  THE USER WANTS tea", "Line one\r\nthe declared player action is x"],
    "provider_tool_call_required": BOOL,
    "current_player_action": [None, "go north", "drink tea"],
    "previous_player_action": [None, "go north", "drink tea"],
    "previous_player_facing_response": [None, "The keeper nods.", "Other."],
    "campaign_file_disposition_supported": BOOL,
    "opening_request": BOOL,
    "decision_boundary": [None, "boundary"],
    "post_commit_tool_required": BOOL,
    "ooc_ack_required": BOOL,
    "buffered_response_blocks": [None, [], [{"kind": "narration", "text": "x"}],
                                 [{"kind": "narration"}, {"kind": "ooc", "text": "y"}]],
    "buffered_raw_response_blocks": [None, [], [{"kind": "ooc"}]],
    "catalog_tool": ["mechanic", "reference"],
    "behavior": [b.value for b in ModelToolBehavior],
}

PREDICATES = [
    ("buffered_narration_is_settled", ()), ("required_transition_can_run", ()),
    ("actor_decision_can_be_requested", ()), ("tool_round_needs_correction", ()),
    ("tool_round_can_release_narration", ()), ("held_campaign_file_narration_is_needed", ()),
    ("narration_repeats_previous_player_response", ()),
    ("player_boundary_prose_can_end_turn", ()), ("required_tool_call_is_missing", ()),
    ("narration_needs_private_correction", ()), ("campaign_file_needs_narration", ()),
    ("campaign_file_must_be_settled", ()), ("required_tool_correction_was_abandoned", ()),
    ("round_needs_gm_decision", ()), ("round_needs_player_intent_resolution", ()),
    ("opening_needs_campaign_file_update", ()),
    ("committed_work_needs_campaign_file_disposition", ()),
    ("saved_gm_boundary_should_be_read", ()), ("engine_transition_needs_review", ()),
    ("campaign_file_requires_atomic_completion", ("role",)),
    ("round_requires_tool_call", ("atomic",)),
    ("settled_disposition_still_owes_ooc_acknowledgement", ()),
    ("held_disposition_must_be_retried", ()), ("tool_call_needs_fresh_catalog", ("position",)),
    ("exchange_has_committed_work", ()), ("round_allows_history_tool", ()),
    ("player_intent_remains_pending", ()), ("buffered_narration_can_be_replaced", ()),
    ("committed_tool_resolves_player_boundary", ()), ("round_is_at_player_boundary", ()),
    ("provisional_tool_prose_needs_review", ()),
]

SAMPLE_BOUNDARY = {
    "owner": "gm", "actor_id": "npc-1", "actor_name": "Captain Orin",
    "actor_state": {"hp": 7, "conditions": ["prone"]}, "campaign_revision": 12,
    "active_side_id": "side-npc", "focused_actor_id": "npc-1",
    "eligible_actors": [{"actor_id": "npc-1", "actor_name": "Captain Orin",
                         "actor_state": {"hp": 7}},
                        {"actor_id": "npc-2", "actor_name": "Mära", "actor_state": {}}],
    "preparation": {"queue": [{"event": "choose_kit", "actor": "pc-1"}], "ünï": "ok"},
}


def value_for(name: str, sample: object) -> object:
    if name == "required_transitions":
        return tuple(tool(f"transition_{index}", "required_engine_transition")
                     for index in range(int(sample)))  # type: ignore[call-overload]
    if name == "decision_boundary":
        return None if sample is None else boundary(SAMPLE_BOUNDARY)
    if name == "catalog_tool":
        return tool("lookup", category=str(sample))
    if name == "behavior":
        return ModelToolBehavior(sample)
    if name in {"buffered_response_blocks", "buffered_raw_response_blocks"}:
        return None if sample is None else tuple(sample)  # type: ignore[arg-type]
    return sample


def build_state(assigned: dict[str, object]) -> ProviderTurnState:
    state = ProviderTurnState()
    for name, sample in assigned.items():
        target = state
        attribute = name
        if "." in name:
            group, attribute = name.split(".")
            target = getattr(state, group)
        setattr(target, attribute, value_for(name, sample))
    return state


# Long conjunctions random states rarely make true or false: each starts from a state that
# makes it true (or false), then flips each local once and leaves each out once.
FOCUSED: list[dict[str, object]] = [
    {"obligations.force_campaign_file_update": False,
     "obligations.campaign_file_narration_pending": False, "provider_tool_call_required": False,
     "current_player_action": "drink tea", "previous_player_action": "go north",
     "previous_player_facing_response": "The keeper nods.", "narration": "The keeper nods."},
    {"decisions.player_boundary_narration": True,
     "obligations.player_intent_resolution_required": False,
     "decisions.gm_boundary_decision": False,
     "obligations.engine_transition_review_pending": False,
     "obligations.pending_failed_tool_call": False, "progress.any_commit_in_exchange": True,
     "campaign_file_disposition_supported": True, "provider_tool_call_required": True,
     "obligations.force_campaign_file_update": False},
    {"obligations.engine_transition_review_pending": False,
     "obligations.force_campaign_file_update": False, "post_commit_tool_required": False,
     "decisions.gm_boundary_decision": False,
     "obligations.player_intent_resolution_required": False},
]


def flipped(name: str, value: object) -> object:
    if isinstance(value, bool):
        return not value
    choices = [sample for sample in SAMPLES[name] if sample != value]
    return choices[0]


def focused_states() -> list[dict[str, object]]:
    states = []
    for base in FOCUSED:
        states.append(dict(base))
        for name, value in base.items():
            states.append({**base, name: flipped(name, value)})
            states.append({key: item for key, item in base.items() if key != name})
    return states


def decision_cases() -> list[dict[str, object]]:
    names = list(SAMPLES)
    cases = []
    focused = focused_states()
    for index in range(600 + len(focused)):
        probability = [0.0, 1.0, 0.5, 0.8, 0.95][index % 5]
        assigned = ({name: RANDOM.choice(SAMPLES[name]) for name in names
                     if RANDOM.random() < probability}
                    if index < 600 else focused[index - 600])
        state = build_state(assigned)
        role = RANDOM.choice(["dm", "builder"])
        atomic = RANDOM.choice(BOOL) if index < 600 else False
        position = RANDOM.choice([0, 1, 2])
        results = {}
        for predicate, extra in PREDICATES:
            function = getattr(decisions, "_" + predicate)
            arguments: list[object] = []
            if "role" in extra:
                arguments.append(SimpleNamespace(session=SimpleNamespace(role=WriterRole(role))))
            if "atomic" in extra:
                arguments.append(atomic)
            if "position" in extra:
                arguments.append(position)
            try:
                results[predicate] = function(state, *arguments)
            except UnboundLocalError as error:
                results[predicate] = {"unbound": str(error)}
        cases.append({"assigned": assigned, "role": role, "atomic": atomic,
                      "position": position, "results": results})
    return cases


def operation_cases() -> list[dict[str, object]]:
    names = [None, "", "read_file", "tool_0123456789ab_read_file", "tool_0123456789AB_read_file",
             "tool_0123456789a_read_file", "tool_0123456789ab_", "tool_0123456789abc_write_file",
             "write_file", "check_campaign_plan", "publish_ruleset_candidate", "ask_question",
             "roll_dice", "tool_deadbeef0000_search_campaign_knowledge", "tool_", "Read_File",
             "tool_aaaaaaaaaaaa_tool_bbbbbbbbbbbb_read_file"]
    return [{"name": name, "category": operation_category(name)} for name in names]


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def progress_cases() -> list[dict[str, object]]:
    scripts: list[list[list[object]]] = [
        [["snapshot"], ["tick", 0.25], ["update", "provider", None], ["tick", 1.5],
         ["observe"], ["snapshot"], ["update", "tool", "tool_0123456789ab_read_file"],
         ["tick", 0.0005], ["update", "working", None], ["finish", "completed"], ["tick", 3],
         ["snapshot"], ["update", "provider", None], ["finish", "failed"]],
        [["update", "retrying", None], ["update", "provider", None], ["snapshot"],
         ["update", "working", None], ["update", "provider", None], ["snapshot"],
         ["expect_correction"], ["update", "provider", None], ["observe"], ["snapshot"]],
        [["update", "stopping", None], ["update", "provider", None], ["observe"],
         ["snapshot"], ["finish", "stopped"], ["snapshot"]],
        [["update", "bogus", None], ["finish", "bogus"], ["update", "tool", "roll_dice"],
         ["tick", 2.9999], ["snapshot"], ["update", "compacting", None], ["snapshot"],
         ["update", "tool", None], ["snapshot"]],
        [["update", "queued", None], ["observe"], ["snapshot"], ["tick", -5], ["snapshot"]],
    ]
    cases = []
    for script in scripts:
        clock = FakeClock()
        progress = AttemptProgress(clock)
        outputs: list[object] = []
        for step in script:
            operation = step[0]
            try:
                if operation == "tick":
                    clock.now += float(step[1])  # type: ignore[arg-type]
                    outputs.append(None)
                elif operation == "update":
                    progress.update(step[1], step[2])  # type: ignore[arg-type]
                    outputs.append(None)
                elif operation == "observe":
                    progress.observe()
                    outputs.append(None)
                elif operation == "expect_correction":
                    progress.expect_correction()
                    outputs.append(None)
                elif operation == "finish":
                    outputs.append(progress.finish(step[1]))  # type: ignore[arg-type]
                else:
                    outputs.append(progress.snapshot())
            except ValueError as error:
                outputs.append({"error": str(error)})
        cases.append({"script": script, "outputs": outputs})
    return cases


def control_cases() -> list[dict[str, object]]:
    scripts: list[list[list[object]]] = [
        [["cancelled"], ["count"], ["admit"], ["cancel"], ["cancel"], ["cancelled"], ["count"],
         ["admit"], ["wait_cancelled"], ["snapshot"]],
        [["wait_cancelled"], ["add_steering", "north"], ["add_steering", "south"],
         ["wait_for_steering"], ["wait_for_steering"], ["add_steering", "east"],
         ["take_steering_or_close"], ["take_steering_or_close"], ["add_steering", "west"],
         ["take_steering"]],
        [["status_hint", "Compacting the conversation"], ["hint"], ["snapshot"],
         ["status_hint", ""], ["hint"], ["snapshot"], ["status_hint", None], ["hint"],
         ["snapshot"], ["cancel"], ["status_hint", "late"], ["snapshot"]],
        [["admit_failing", "north"], ["take_steering"], ["add_steering", "south"],
         ["take_steering"], ["take_steering"]],
    ]
    cases = []
    for script in scripts:
        control = ProviderControl()
        clock = FakeClock()
        control.progress = AttemptProgress(clock)
        outputs: list[object] = []
        for step in script:
            operation = step[0]
            try:
                if operation == "cancelled":
                    outputs.append(control.cancelled)
                elif operation == "count":
                    outputs.append(control.cancellation_count)
                elif operation == "admit":
                    outputs.append(control.admit(lambda: "admitted"))
                elif operation == "cancel":
                    control.cancel()
                    outputs.append(None)
                elif operation == "wait_cancelled":
                    outputs.append(control.wait_cancelled(0))
                elif operation == "add_steering":
                    control.add_steering(step[1])  # type: ignore[arg-type]
                    outputs.append(None)
                elif operation == "admit_failing":
                    def fail() -> None:
                        raise CommandRejected("persist failed")
                    control.admit_steering(step[1], fail)  # type: ignore[arg-type]
                    outputs.append(None)
                elif operation == "wait_for_steering":
                    outputs.append(list(control.wait_for_steering(0)))
                elif operation == "take_steering":
                    outputs.append(list(control.take_steering()))
                elif operation == "take_steering_or_close":
                    outputs.append(list(control.take_steering_or_close()))
                elif operation == "status_hint":
                    control.set_status_hint(step[1])  # type: ignore[arg-type]
                    outputs.append(None)
                elif operation == "hint":
                    outputs.append(control.status_hint)
                else:
                    outputs.append(control.progress.snapshot())
            except CommandRejected as error:
                outputs.append({"error": str(error)})
        cases.append({"script": script, "outputs": outputs})
    return cases


def text_cases() -> list[dict[str, object]]:
    boundaries = [
        SAMPLE_BOUNDARY,
        {"owner": "player", "actor_id": None, "actor_name": None, "actor_state": {},
         "campaign_revision": 0, "active_side_id": None, "focused_actor_id": None,
         "eligible_actors": [], "preparation": None},
    ]
    alias_sets = [
        [],
        [["tool_a_roll", "roll", "optional", "Roll dice."]],
        [["tool_a_advance", "advance_turn", "required_engine_transition", "Advance the turn."],
         ["tool_a_roll", "roll", "optional", ""],
         ["tool_b_end", "end_encounter", "required_engine_transition", "End — now ünïcode."]],
    ]
    cases: list[dict[str, object]] = []
    for aliases in alias_sets:
        projected = {alias: tool(name, behavior, description=description)
                     for alias, name, behavior, description in aliases}
        cases.append({"kind": "engine_turn_control", "aliases": aliases,
                      "message": session._engine_turn_control_message(projected)})  # noqa: SLF001
    for case in boundaries:
        value = boundary(case)
        cases.append({"kind": "campaign_preparation", "boundary": case,
                      "message": session._campaign_preparation_message(value)})  # noqa: SLF001
        cases.append({"kind": "gm_decision", "boundary": case,
                      "message": session._gm_decision_control_message(value)})  # noqa: SLF001
        for action, operations, pending in [(None, [], False), ("attack the wolf — ü", ["roll"],
                                                                True)]:
            cases.append({
                "kind": "player_boundary", "boundary": case, "current_player_action": action,
                "committed_operations": operations, "player_intent_pending": pending,
                "message": session._player_boundary_narration_message(  # noqa: SLF001
                    value, current_player_action=action, committed_operations=operations,
                    player_intent_pending=pending)})
    for footing, action in [(None, None), (None, ""), (None, "open the door — ü"),
                            ({"scene": "inn", "ünï": [1, 2]}, "ignored")]:
        for kind, function in [("campaign_file_disposition",
                                session._campaign_file_disposition_message),  # noqa: SLF001
                               ("campaign_file_updated_narration",
                                session._campaign_file_updated_narration_message)]:  # noqa: SLF001
            cases.append({"kind": kind, "opening_footing": footing,
                          "current_player_action": action,
                          "message": function(opening_footing=footing,
                                              current_player_action=action)})
    return cases


def scope_cases() -> list[dict[str, object]]:
    cases = []
    unspecified = session._UNSPECIFIED_PREPARATION_INTENT  # noqa: SLF001
    for scope in ["<unspecified>", "unresolved_action", "await_player_choice", "other", None, 3,
                  ["unresolved_action"]]:
        for pending in BOOL:
            value = unspecified if scope == "<unspecified>" else scope
            try:
                result: object = session._preparation_keeps_intent_pending(  # noqa: SLF001
                    value, pending=pending)
            except CommandRejected as error:
                result = {"code": error.code, "error": str(error)}
            cases.append({"scope": scope, "pending": pending, "result": result})
    return cases


def rejection_cases() -> list[dict[str, object]]:
    issues = [
        {"path": "target", "kind": "missing"},
        {"path": "weapon", "kind": "enum", "expected_type": "string", "actual_type": "integer",
         "parent_path": "attack", "allowed_values": ["sword", "bow"], "minimum": 0,
         "maximum": 99999999999999999999, "actual_length": 4},
    ]
    specs = [
        {"message": "Target unavailable", "code": "mechanics_rejected", "field_paths": [],
         "issues": [], "state_changed": False, "reasons": []},
        {"message": "Two problems", "code": "ruleset_precondition", "field_paths": ["a", "b.c"],
         "issues": issues, "state_changed": True, "reasons": ["first reason", "second"]},
        {"message": "One reason", "code": "x", "field_paths": ["z"], "issues": issues[:1],
         "state_changed": False, "reasons": ["only one"]},
    ]
    cases = []
    for spec in specs:
        error = MechanicsRejected(
            spec["message"], code=spec["code"], field_paths=tuple(spec["field_paths"]),
            validation_issues=tuple(MechanicsValidationIssue(
                **{**issue, "allowed_values": tuple(issue.get("allowed_values", ()))})
                for issue in spec["issues"]),
            state_changed=spec["state_changed"], refusal_reasons=tuple(spec["reasons"]))
        cases.append({**spec, "feedback": _mechanics_rejection_feedback(error)})
    return cases


def continuation_cases() -> list[dict[str, object]]:
    cases = []
    for payload, response_id in [({"a": 1, "ünï": "✓", "nested": {"b": [1.5, None]}}, "resp-1"),
                                 ({}, "r"), ({"a": 1}, "")]:
        try:
            event = ProviderEvent.private_continuation(payload, response_id=response_id)
            cases.append({"payload": payload, "response_id": response_id, "text": event.text})
        except ValueError as error:
            cases.append({"payload": payload, "response_id": response_id, "error": str(error)})
    return cases


def main() -> None:
    vectors = {
        "decisions": decision_cases(),
        "operation_category": operation_cases(),
        "attempt_progress": progress_cases(),
        "provider_control": control_cases(),
        "control_texts": text_cases(),
        "preparation_scope": scope_cases(),
        "rejection_feedback": rejection_cases(),
        "private_continuation": continuation_cases(),
        "sample_boundary": SAMPLE_BOUNDARY,
    }
    text = json.dumps(vectors, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
    OUTPUT.write_bytes(text.encode("utf-8"))
    print({key: len(value) for key, value in vectors.items()}, len(text), "bytes")


if __name__ == "__main__":
    main()
