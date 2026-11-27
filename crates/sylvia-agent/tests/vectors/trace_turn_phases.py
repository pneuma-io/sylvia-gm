"""Run the Python owner with the GM turn's phases traced, for the turn-loop vector generators.

Launched by sylvia-compare as the Python owner (`python -B trace_turn_phases.py`), with
`SYLVIA_TURN_PHASES_TRACE` naming the JSON-lines log and `SYLVIA_TURN_PHASES` the phases of
`agent/_provider_turn_phases.py` to trace (comma-separated).

For each call of a traced phase it logs:
- `phase_start`: the phase, the turn id, the provider input and every assigned turn local
  (unassigned ones are left out, as they would raise `UnboundLocalError`), with each message
  list's ledger tags;
- every owner call the phase makes (the adapter's owner is wrapped in a proxy), with its
  arguments and full result, the catalog supplier's results, each `uuid4().hex` it draws and
  each `set_audit_context` payload;
- `phase_end`: the turn locals after the phase, or the exception it raised, and what it
  returned when that is not `None`.

A real call of a generator phase is relayed: each event is logged and passed to the session
loop as Python's own consumer would take it, with owner calls unlogged while the loop handles
it, and after an action the loop's answer is logged (`action_handled`: the rejection it wrote
back, or null). The phase then resumes as it would untraced. Variants are consumed here, their
events never reaching the loop.

The provider input's narrative writer, knowledge searcher and reference reader are logged as
`callback` rows (which one, its arguments and result, or what it raised), their own owner calls
unlogged: the Rust test answers each from its row.

A traced function may also be one of the tool batch's helpers (T3a), whose arguments after the
state vary: the provider input when there is one, any other positional argument and the
keyword arguments (`turn_id`, a refusal's `error`) are passed through as given.

Each call is then run again on copies of its state and input with the variants
`VARIANTS[phase]` names (and other phases on copies of its state, `CROSS_VARIANTS[phase]`): some turn locals set, and some owner reads answered with given values
(logged as ordinary calls), so the Rust tests reach branches a recorded turn does not.

The pseudo-phase `list_tools` traces `StatelessMcpMechanics._list_tools` instead (T3m-1):
`listing_start` (its arguments), every owner call the listing makes, and `listing_end` (the
tools, or what it raised). The first listing with a session is run again with the variants
`listing_variants` gives: owner answers overridden (an override may be a refusal, raised), a
different session or the fixed list.

The pseudo-phase `execute_action` traces `StatelessMcpMechanics.execute_action` (T3m-2) the same
way: `execution_start` (the command), every owner call and `uuid4` draw, and `execution_end`
(the receipt, or what it raised). The first call is run again with `execution_variants`; an
override may also be `Answers`, one answer per successive call.

The pseudo-phase `campaign_file` traces `StatelessMcpMechanics.commit_campaign_file`,
`search_knowledge` and `read_reference` (T3m-3): `campaign_file_start` (the method and its
arguments), every owner call, and `campaign_file_end`. The first real commit is followed by
`campaign_file_variants`.
Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import copy
import dataclasses
import inspect
import json
import os
import random
import sys
import uuid as uuid_module
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
TRACE_LOG = Path(os.environ["SYLVIA_TURN_PHASES_TRACE"])
PHASES = [name for name in os.environ.get("SYLVIA_TURN_PHASES", "").split(",") if name]

import sylvia_gm.interfaces.owner_process as owner_process  # noqa: E402
from sylvia_gm.agent import _provider_session, _provider_turn_phases  # noqa: E402
from sylvia_gm.agent._provider_turn_state import ProviderTurnState  # noqa: E402
from sylvia_gm.agent._stateless_mcp import ModelMechanicsTool  # noqa: E402
from sylvia_gm.domain.authority import (  # noqa: E402
    ModelDecisionActor,
    ModelDecisionBoundary,
    ModelDecisionOwner,
    ModelToolBehavior,
)

OVERRIDES: dict[str, object] = {}
#: Phases whose traces log the provider input's callbacks (T3b's batch); earlier traces keep
#: their rows unchanged.
BATCH_PHASES = {"prepare_tool_batch", "execute_tool_batch"}
ACTIVE = {"value": False}


def plain(value: object, depth: int = 0) -> object:
    """A JSON-safe copy: dataclasses as dicts, enums as values, frozen JSON as JSON."""
    import enum
    from collections.abc import Mapping
    if depth > 60:
        return "<deep>"
    if isinstance(value, enum.Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {"__class__": type(value).__name__,
                **{f.name: plain(getattr(value, f.name), depth + 1)
                   for f in dataclasses.fields(value)}}
    if callable(getattr(value, "model_dump", None)):
        # An SDK object (the OpenAI client's stream deltas): its fields, as the parsers read them.
        return plain(value.model_dump(), depth + 1)
    if isinstance(value, Mapping) or callable(getattr(value, "items", None)):
        # Frozen JSON objects (tuples of pairs with `items`) are objects, not lists.
        return {str(k): plain(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item, depth + 1) for item in value]
    if isinstance(value, (set, frozenset)):
        return {"__set__": sorted((plain(item, depth + 1) for item in value), key=repr)}
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if hasattr(value, "__iter__"):
        return [plain(item, depth + 1) for item in value]
    return {"__repr__": repr(value), "__class__": type(value).__name__}


def log(entry: dict[str, object]) -> None:
    with TRACE_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, default=str) + "\n")


def tags(messages: object) -> list[object]:
    return [plain(getattr(message, "ledger_tags", None) or {}) for message in messages]  # type: ignore[union-attr]


MESSAGE_LISTS = {"messages", "inference_messages", "resumed_messages"}


def dump_state(state: ProviderTurnState) -> dict[str, object]:
    groups: dict[str, object] = {}
    for group in ("obligations", "progress", "decisions"):
        target = getattr(state, group)
        values = {}
        for field in dataclasses.fields(target):
            try:
                values[field.name] = plain(getattr(target, field.name))
            except UnboundLocalError:
                continue
        groups[group] = values
    local: dict[str, object] = {}
    for field in dataclasses.fields(state):
        if field.name in ("obligations", "progress", "decisions"):
            continue
        try:
            value = getattr(state, field.name)
        except UnboundLocalError:
            continue
        local[field.name] = plain(value)
        if field.name in MESSAGE_LISTS:
            local[field.name + "__tags"] = tags(value)
        if field.name == "exchange_messages":
            local["exchange_messages__is_messages"] = value is getattr(state, "messages", None)
    groups["locals"] = local
    return groups


def copied_value(value: object) -> object:
    """`copy.deepcopy(value)`, or, for a value holding something it cannot copy (a frozen
    snapshot's `mappingproxy`), new containers around the same immutable leaves."""
    try:
        return copy.deepcopy(value)
    except TypeError:
        if isinstance(value, dict):
            return {key: copied_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [copied_value(item) for item in value]
        if isinstance(value, set):
            return set(value)
        return value


def copy_state(state: ProviderTurnState) -> ProviderTurnState:
    """A deep copy of the assigned locals (`copy.deepcopy` reads every slot, and an unassigned
    one raises `UnboundLocalError`)."""
    copied = ProviderTurnState()
    for group in ("obligations", "progress", "decisions"):
        source, target = getattr(state, group), getattr(copied, group)
        for field in dataclasses.fields(source):
            try:
                setattr(target, field.name, copy.deepcopy(getattr(source, field.name)))
            except UnboundLocalError:
                continue
    for field in dataclasses.fields(state):
        if field.name in ("obligations", "progress", "decisions"):
            continue
        try:
            value = getattr(state, field.name)
        except UnboundLocalError:
            continue
        setattr(copied, field.name, copied_value(value))
    try:
        if state.exchange_messages is state.messages:
            copied.exchange_messages = copied.messages
    except UnboundLocalError:
        pass
    return copied


def dump_input(provider_input: object) -> dict[str, object]:
    return plain({
        "session": provider_input.session,  # type: ignore[attr-defined]
        "history": provider_input.history,  # type: ignore[attr-defined]
        "catalog": provider_input.catalog,  # type: ignore[attr-defined]
        "basis_message_id": provider_input.basis_message_id,  # type: ignore[attr-defined]
        "recovery_kind": provider_input.recovery_kind,  # type: ignore[attr-defined]
        "retry_action_receipt": provider_input.retry_action_receipt,  # type: ignore[attr-defined]
        "cancelled": provider_input.control.cancelled,  # type: ignore[attr-defined]
        "has_catalog_supplier": provider_input.catalog_supplier is not None,  # type: ignore[attr-defined]
        "has_reference_reader": provider_input.reference_reader is not None,  # type: ignore[attr-defined]
    })  # type: ignore[return-value]


class Answers:
    """Overridden answers for successive calls of one owner method, in order."""

    def __init__(self, *answers: object) -> None:
        self.answers = list(answers)

    def next(self) -> object:
        return self.answers.pop(0)


def refusal_fields(error: BaseException) -> dict[str, object]:
    """A raised error as the Rust tests rebuild it: class, message, code and, for a
    `MechanicsRejected`, its correction fields."""
    found: dict[str, object] = {"class": type(error).__name__, "message": str(error),
                                "code": getattr(error, "code", None)}
    if hasattr(error, "validation_issues"):
        found.update({
            "field_paths": list(error.field_paths),  # type: ignore[attr-defined]
            "validation_issues": [issue.as_payload()
                                  for issue in error.validation_issues],  # type: ignore[attr-defined]
            "state_changed": error.state_changed,  # type: ignore[attr-defined]
            "refusal_reasons": list(error.refusal_reasons)})  # type: ignore[attr-defined]
    return found


class Traced:
    """The adapter's owner: every call is logged while a phase runs; overrides answer first."""

    def __init__(self, target: object) -> None:
        object.__setattr__(self, "_target", target)

    def __getattr__(self, name: str) -> object:
        target = object.__getattribute__(self, "_target")
        if ACTIVE["value"] and name in OVERRIDES:
            answer = OVERRIDES[name]

            def overridden(*args: object, **kwargs: object) -> object:
                answer = OVERRIDES[name]
                if isinstance(answer, Answers):
                    answer = answer.next()
                if isinstance(answer, BaseException):
                    # An override may also be a refusal, raised as the owner would raise it.
                    log({"event": "call", "name": name, "args": plain(list(args)),
                         "kwargs": plain(kwargs), "override": True,
                         "raised": refusal_fields(answer)})
                    raise answer
                log({"event": "call", "name": name, "args": plain(list(args)),
                     "kwargs": plain(kwargs), "result": plain(answer), "override": True})
                return answer
            return overridden
        value = getattr(target, name)
        if not callable(value) or not ACTIVE["value"]:
            return value

        def call(*args: object, **kwargs: object) -> object:
            try:
                result = value(*args, **kwargs)
            except BaseException as error:
                # The code and a `MechanicsRejected`'s fields too: a refusal the server turns
                # into a correction contract is rebuilt in its class.
                log({"event": "call", "name": name, "args": plain(list(args)),
                     "kwargs": plain(kwargs), "raised": refusal_fields(error)})
                raise
            log({"event": "call", "name": name, "args": plain(list(args)),
                 "kwargs": plain(kwargs), "result": plain(result)})
            return result
        return call

    def __hash__(self) -> int:
        return hash(object.__getattribute__(self, "_target"))

    def __eq__(self, other: object) -> bool:
        return object.__getattribute__(self, "_target") is other or self is other


class AuditedProvider:
    """The adapter's provider: `set_audit_context` payloads, every request and every streamed
    chunk are logged."""

    def __init__(self, target: object) -> None:
        object.__setattr__(self, "_target", target)

    def __getattr__(self, name: str) -> object:
        value = getattr(object.__getattribute__(self, "_target"), name)
        if name == "set_audit_context":
            def recorded(context: object) -> object:
                log({"event": "audit_context", "context": plain(context)})
                return value(context)
            return recorded
        if name == "generate_stream":
            def stream(messages, tools=None, timeout=120.0, **options):  # type: ignore[no-untyped-def]
                log({"event": "provider_stream", "messages": plain(messages), "tools": plain(tools),
                     "timeout": timeout, "options": plain(options)})
                inner = value(messages, tools=tools, timeout=timeout, **options)

                async def chunks():  # type: ignore[no-untyped-def]
                    try:
                        async for chunk in inner:
                            log({"event": "provider_chunk", "chunk": plain(chunk)})
                            yield chunk
                    except BaseException as error:
                        log({"event": "provider_chunk_error", "class": type(error).__name__,
                             "message": str(error)})
                        raise
                return chunks()
            return stream
        if name == "generate":
            async def generate(messages, tools=None, stream=False, timeout=60.0, **options):  # type: ignore[no-untyped-def]
                log({"event": "provider_generate", "messages": plain(messages), "tools": plain(tools),
                     "timeout": timeout, "options": plain(options)})
                response = await value(messages, tools=tools, stream=stream, timeout=timeout, **options)
                log({"event": "provider_response", "response": plain(response)})
                return response
            return generate
        return value


original_init = _provider_session.ProviderSessionAdapter.__init__


def traced_init(self, owner, provider, **kwargs):  # type: ignore[no-untyped-def]
    original_init(self, Traced(owner), provider, **kwargs)
    self._trace_adapter = plain({key: value for key, value in kwargs.items()  # noqa: SLF001
                                 if key != "record_warning"})


_provider_session.ProviderSessionAdapter.__init__ = traced_init


#: `uuid4()` for the traced modules, from a fixed seed, so a regenerated vector file is
#: byte-identical (the run's other identities come from the session file).
UUID_RANDOM = random.Random(20261123)


class Uuids:
    """`uuid.uuid4()` for the phases: seeded version-4 values, each one logged."""

    def __getattr__(self, name: str) -> object:
        return getattr(uuid_module, name)

    @staticmethod
    def uuid4() -> uuid_module.UUID:
        value = uuid_module.UUID(int=UUID_RANDOM.getrandbits(128), version=4)
        if ACTIVE["value"]:
            log({"event": "uuid", "hex": value.hex})
        return value


_provider_turn_phases.uuid = Uuids()  # type: ignore[assignment]
_provider_session.uuid = Uuids()  # type: ignore[assignment]
from sylvia_gm.agent import _working_context  # noqa: E402

_working_context.uuid4 = Uuids.uuid4  # type: ignore[assignment]


def tool(name: str, behavior: str = "optional", category: str = "mechanic",
         package_id: str = "sylvia.test", capability: str = "test") -> ModelMechanicsTool:
    return ModelMechanicsTool(
        package_id=package_id, package_version="1.0.0", package_sha256="0" * 64, name=name,
        capability=capability, input_schema={"type": "object", "properties": {}},
        description=f"{name} description", category=category,
        model_behavior=ModelToolBehavior(behavior))


def boundary(owner: str, preparation: object = None) -> ModelDecisionBoundary:
    return ModelDecisionBoundary(
        owner=ModelDecisionOwner(owner), actor_id="actor-1", actor_name="Mara",
        actor_state={"hp": 9}, campaign_revision=7, active_side_id="side-a",
        focused_actor_id="actor-1",
        eligible_actors=(ModelDecisionActor("actor-1", "Mara", {"hp": 9}),),
        preparation=preparation)


EXTRA_TOOLS = [
    tool("resolve_attack", "resolves_player_intent"), tool("move", "supports_player_intent"),
    tool("ready", "leaves_player_intent_pending"), tool("lookup", category="reference"),
    tool("advance_turn", "required_engine_transition"), tool("end_combat_encounter"),
    tool("choose_kit", package_id="sylvia.prep"),
]

# Each variant: turn locals to set (`set`), owner reads to answer (`answer`), tools to add to the
# current catalog (`tools`), a cancelled control (`cancelled`), or the round's calls replaced
# (`calls`: each a name, `None` for the first real call's, and argument text, `None` for its own;
# a `drop` list removes those argument keys, an `add` object sets some). Only the variant named
# as calling the narrative writer reaches it.
VARIANTS: dict[str, list[dict[str, object]]] = {
    "prepare_round_catalog": [
        {"name": "participant OOC only", "set": {"participant_ooc_only": True}},
        {"name": "cancelled", "cancelled": True},
        {"name": "combat review", "catalog_tools": True, "set": {"provider_round": 0},
         "combat": {"turn_active": True, "active_actor_kind": "monster", "action_available": False}},
        {"name": "combat review, player turn", "catalog_tools": True, "set": {"provider_round": 0},
         "combat": {"turn_active": True, "active_actor_kind": "pc", "action_available": False}},
        {"name": "queue tools after a stopped sequence", "lock": True,
         "progress": {"sequence_execution_stopped": True}},
        {"name": "queue tools", "lock": True, "progress": {"sequence_execution_stopped": False}},
    ],
    "stream_provider_round": [
        {"name": "narration facts", "set": {"pending_narration_facts": [
            {"tool": "roll_dice", "result": {"total": 14}}, {"note": "no tool"}]},
         "obligations": {"provisional_tool_prose_pending": False}},
        {"name": "narration facts with provisional prose", "set": {"pending_narration_facts": [
            {"tool": "roll_dice"}]}, "obligations": {"provisional_tool_prose_pending": True}},
        {"name": "application controls", "set": {"current_application_controls": [
            {"role": "system", "content": "SYLVIA_GM_OPTIONAL_REFERENCE_RECOVERY: retry."}]}},
        {"name": "Campaign File narration pending",
         "obligations": {"campaign_file_narration_pending": True}},
        {"name": "held disposition retried", "set": {"buffered_narration": "Held."},
         "obligations": {"campaign_file_narration_pending": False,
                         "force_campaign_file_update": True}},
        {"name": "player boundary", "set": {"decision_boundary": boundary("player"),
                                            "pending_narration_facts": [{"tool": "attack"}]},
         "decisions": {"player_boundary_narration": True}},
        {"name": "GM boundary", "set": {"decision_boundary": boundary("gm")},
         "decisions": {"player_boundary_narration": False}},
        {"name": "preparation", "set": {"decision_boundary": boundary(
            "player", {"package_id": "sylvia.prep", "intent": "choose_kit"})}},
        {"name": "corrections", "set": {
            "private_deliberation_correction_message": {"role": "system", "content": "Fix A."},
            "required_tool_call_correction_message": {"role": "system", "content": "Fix B."}}},
        {"name": "cancelled", "cancelled": True},
    ],
    "reject_ruleless_mechanics": [
        {"name": "blocks with mechanics", "set": {
            "selected_raw_blocks": ({"kind": "narration", "text": "Roll a d20 to see."},),
            "revised_narration_supplied": True}},
        {"name": "held prose with mechanics", "set": {
            "selected_raw_blocks": None, "revised_narration_supplied": False,
            "buffered_narration": "The wolf bites: you take 5 damage."}},
        {"name": "out-of-character negation", "set": {
            "selected_raw_blocks": ({"kind": "ooc", "text": "We never roll for initiative."},),
            "revised_narration_supplied": True}},
        {"name": "revised prose supplied", "set": {
            "selected_raw_blocks": None, "revised_narration_supplied": True,
            "buffered_narration": "Roll a d20."}},
    ],
    "validate_revision_speakers": [
        {"name": "unknown speaker", "set": {"selected_raw_blocks": (
            {"kind": "npc_dialogue", "speaker_id": "entity-nobody", "text": "Hello."},)}},
        {"name": "speaker this update admits", "set": {
            "selected_raw_blocks": ({"kind": "npc_dialogue", "speaker_id": "entity-new",
                                     "text": "Hello."},),
            "parameters": {"entities": [{"entity_id": "entity-new"}, {"entity_id": 3}, "x"]}}},
        {"name": "OOC acknowledgement missing", "set": {
            "ooc_ack_required": True,
            "selected_raw_blocks": ({"kind": "narration", "text": "Rain."},)}},
        {"name": "OOC acknowledgement given", "set": {
            "ooc_ack_required": True,
            "selected_raw_blocks": ({"kind": "ooc", "text": "Noted."},
                                    {"kind": "npc_dialogue", "speaker_id": "anonymous",
                                     "text": "Who goes?"})}},
        {"name": "no blocks", "set": {"selected_raw_blocks": None, "ooc_ack_required": True}},
    ],
    "reconcile_tool_batch": [
        {"name": "failed reference reads and a failed call", "results": [
            {"name": "read_campaign_reference", "failed": True,
             "result": {"status": "rejected", "code": "reference_unavailable"}},
            {"name": "search_campaign_knowledge", "failed": True,
             "result": {"status": "rejected", "code": "knowledge_search_invalid"}},
            {"name": None, "failed": True, "result": {"status": "rejected", "code": "x"}}]},
        {"name": "a reference read that succeeded", "results": [
            {"name": "read_campaign_reference", "failed": False, "result": {"status": "ok"}}]},
        {"name": "an executed sequence only", "results": [],
         "set": {"sequence_narration_facts": [
             {"tool": "execute_action_sequence", "sequence_id": "s-1", "committed_actions": []}]},
         "obligations": {"buffered_narration_needs_disposition": True},
         "progress": {"campaign_file_disposition_in_exchange": True}},
        {"name": "leaves the intent pending", "mechanic": {"behavior": "leaves_player_intent_pending"}},
        {"name": "leaves pending, awaiting the player's choice",
         "mechanic": {"behavior": "leaves_player_intent_pending", "scope": "await_player_choice"},
         "obligations": {"player_intent_pending": False}},
        {"name": "an invalid preparation scope",
         "mechanic": {"behavior": "leaves_player_intent_pending", "scope": "rewrite_history"}},
        {"name": "resolves the intent", "mechanic": {"behavior": "resolves_player_intent"},
         "obligations": {"player_intent_pending": True},
         "decisions": {"player_boundary_narration": True}},
        {"name": "ends combat", "mechanic": {"tool_name": "end_combat_encounter"},
         "obligations": {"engine_transition_review_pending": True}},
        {"name": "the action failed", "event": "fail"},
        {"name": "the result is unavailable", "event": "drop_result"},
    ],
    "execute_tool_batch": [
        {"name": "an unknown tool", "calls": [{"name": "no_such_tool", "arguments": "{}"}]},
        {"name": "invalid JSON", "calls": [{"name": None, "arguments": '{"state_change": '}]},
        {"name": "arguments not an object", "calls": [{"name": None, "arguments": "[1, 2]"}]},
        {"name": "two calls of the first tool",
         "calls": [{"name": None, "arguments": None}, {"name": None, "arguments": None}]},
        {"name": "a history read", "calls": [{"name": "read_session_history",
                                               "arguments": '{"query": "rain"}'}]},
        {"name": "a contract read without contract tools",
         "calls": [{"name": "read_tool_arguments", "arguments": '{"tool": "x", "value": "y"}'}]},
        {"name": "a reference read", "calls": [{"name": "read_campaign_reference", "arguments":
                                                '{"source": "campaign_state", "path": "scene"}'}]},
        {"name": "a reference read without a path", "calls": [{"name": "read_campaign_reference",
                                                                "arguments": '{"source": 3}'}]},
        {"name": "a knowledge search", "calls": [{"name": "search_campaign_knowledge",
                                                  "arguments": '{"query": "notice", "limit": 2}'}]},
        {"name": "a knowledge search with a bad limit",
         "calls": [{"name": "search_campaign_knowledge",
                    "arguments": '{"query": "notice", "limit": true}'}]},
        {"name": "a missing argument", "calls": [{"name": None, "arguments": None,
                                                  "drop": ["actor", "state_change"]}]},
        {"name": "a preparation scope the tool does not declare",
         "calls": [{"name": None, "arguments": None,
                    "add": {"player_intent_after_preparation": "unresolved_action"}}]},
        # These two reach the Campaign File path's later checks; the first calls the real
        # narrative writer, which commits or refuses the recorded change.
        {"name": "an update that records a change",
         "calls": [{"name": None, "arguments": None,
                    "add": {"state_change": "changed",
                            "memories": [{"text": "The notice is signed by the Quarterkeeper."}]}}]},
        {"name": "an unchanged review after an attempted change",
         "set": {"campaign_file_mutation_attempted": True},
         "calls": [{"name": None, "arguments": None}]},
    ],
    "prepare_provider_round": [
        {"name": "player boundary", "tools": True, "answer": {
            "campaign_preparation_boundary": boundary("player"),
            "model_decision_boundary": boundary("player")}},
        {"name": "player intent pending", "tools": True,
         "obligations": {"player_intent_pending": True}, "answer": {
             "campaign_preparation_boundary": boundary("player"),
             "model_decision_boundary": boundary("player")}},
        {"name": "player intent pending without a resolver",
         "obligations": {"player_intent_pending": True}, "answer": {
             "campaign_preparation_boundary": boundary("player"),
             "model_decision_boundary": boundary("player")}},
        {"name": "GM boundary", "tools": True, "answer": {
            "campaign_preparation_boundary": boundary("gm"),
            "model_decision_boundary": boundary("gm")}},
        {"name": "preparation", "tools": True, "answer": {
            "campaign_preparation_boundary": boundary(
                "player", {"package_id": "sylvia.prep", "intent": "choose_kit", "queue": [1]})}},
        {"name": "Campaign File narration pending",
         "obligations": {"campaign_file_narration_pending": True}},
        {"name": "Campaign File must be settled", "obligations": {
            "campaign_file_narration_pending": False, "force_campaign_file_update": True}},
        {"name": "held narration", "set": {"buffered_narration": "Held."}},
        {"name": "engine transition review", "tools": True,
         "obligations": {"engine_transition_review_pending": True}},
        {"name": "participant OOC only", "set": {"participant_ooc_only": True}},
    ],
}


def run_variant(function, self, state, args, kwargs, variant):  # type: ignore[no-untyped-def]
    state = copy_state(state)
    provider_input = args[0] if args else None
    for key, value in variant.get("set", {}).items():  # type: ignore[union-attr]
        setattr(state, key, value)
    for key, value in variant.get("obligations", {}).items():  # type: ignore[union-attr]
        setattr(state.obligations, key, value)
    for key, value in variant.get("progress", {}).items():  # type: ignore[union-attr]
        setattr(state.progress, key, value)
    for key, value in variant.get("decisions", {}).items():  # type: ignore[union-attr]
        setattr(state.decisions, key, value)
    if variant.get("tools"):
        state.current_catalog = (*state.current_catalog, *EXTRA_TOOLS)
    if "calls" in variant:
        real = state.ordered[0]
        calls = []
        for index, call in enumerate(variant["calls"]):  # type: ignore[union-attr]
            arguments = call["arguments"] if call["arguments"] is not None else real.arguments
            if call.get("drop") or call.get("add"):
                values = json.loads(arguments or "{}")
                for key in call.get("drop", ()):
                    values.pop(key, None)
                values.update(call.get("add", {}))
                arguments = json.dumps(values)
            calls.append(_provider_turn_phases._ToolCallAssembly(  # noqa: SLF001
                call_id=f"{real.call_id}-variant-{index}",
                name=call["name"] if call["name"] is not None else real.name,
                arguments=arguments))
        state.ordered = calls
    if "results" in variant or "mechanic" in variant:
        reconcile_variant(state, variant)
    supplier = getattr(provider_input, "catalog_supplier", None)
    if variant.get("catalog_tools") and supplier is not None:
        provider_input = dataclasses.replace(
            provider_input, catalog_supplier=lambda request: (*supplier(request), *EXTRA_TOOLS))
    if variant.get("cancelled"):
        control = copy.copy(provider_input.control)
        control._cancelled = __import__("threading").Event()  # noqa: SLF001
        control._cancelled.set()  # noqa: SLF001
        provider_input = dataclasses.replace(provider_input, control=control)
    OVERRIDES.clear()
    OVERRIDES.update(variant.get("answer", {}))  # type: ignore[arg-type]
    owner = object.__getattribute__(self._owner, "_target")  # noqa: SLF001
    if "combat" in variant or variant.get("lock"):
        real = owner.snapshot(state.campaign_id)
        changes: dict[str, object] = {}
        if "combat" in variant:
            changes["state"] = {**real.state, "sylvia.test.combat": variant["combat"]}
        if variant.get("lock"):
            changes["package_lock"] = lock_with_entry(real.package_lock)
        OVERRIDES["snapshot"] = dataclasses.replace(real, **changes)
    if "event" in variant:
        OVERRIDES["snapshot"] = changed_events(owner.snapshot(state.campaign_id), state, variant["event"])
    traced_call(function, self, state, (provider_input, *args[1:]) if args else args, kwargs,
                variant["name"])
    OVERRIDES.clear()


def reconcile_variant(state: ProviderTurnState, variant: dict[str, object]) -> None:
    """`reconcile_tool_batch`'s variants: calls answered with given local results (`results`:
    each a name, or `None` for the real call's, its result and whether it failed), and the
    real mechanic's recorded behavior, preparation scope or tool name (`mechanic`)."""
    real = state.ordered[0]
    if "results" in variant:
        calls = []
        for index, entry in enumerate(variant["results"]):  # type: ignore[union-attr]
            call_id = f"{real.call_id}-results-{index}"
            calls.append(_provider_turn_phases._ToolCallAssembly(  # noqa: SLF001
                call_id=call_id, name=entry["name"] if entry["name"] is not None else real.name,
                arguments="{}"))
            state.local_tool_results[call_id] = dict(entry["result"])
            if entry["failed"]:
                state.local_tool_failures.add(call_id)
        state.ordered = calls
    mechanic = variant.get("mechanic", {})
    if "behavior" in mechanic:  # type: ignore[operator]
        state.mechanics_tool_behaviors[real.call_id] = ModelToolBehavior(mechanic["behavior"])  # type: ignore[index]
    if "scope" in mechanic:  # type: ignore[operator]
        state.mechanics_preparation_scopes[real.call_id] = mechanic["scope"]  # type: ignore[index]
    if "tool_name" in mechanic:  # type: ignore[operator]
        state.mechanics_tool_names[real.call_id] = mechanic["tool_name"]  # type: ignore[index]


def changed_events(snapshot: object, state: ProviderTurnState, change: object) -> object:
    """The snapshot with the real mechanic's terminal event failed (`fail`) or its tool result
    removed (`drop_result`)."""
    from sylvia_gm.domain.authority import CanonicalEventType
    action_id = state.mechanics_action_ids.get(state.ordered[0].call_id)
    events = []
    for event in snapshot.events:  # type: ignore[attr-defined]
        if event.action_id == action_id:
            if change == "fail" and event.event_type is CanonicalEventType.ACTION_COMMITTED:
                event = dataclasses.replace(event, event_type=CanonicalEventType.ACTION_FAILED)
            if change == "drop_result" and event.event_type is CanonicalEventType.TOOL_RESULT:
                continue
        events.append(event)
    return dataclasses.replace(snapshot, events=tuple(events))


def lock_with_entry(lock: object) -> object:
    from sylvia_gm.domain.authority import PackageLock, PackageLockEntry, PackageRole
    entry = PackageLockEntry(package_id="sylvia.test", version="1.0.0", sha256="0" * 64,
                             role=PackageRole.PRIMARY, load_order=0, dependencies=(),
                             capabilities=())
    return PackageLock((entry,))


def logged_callbacks(provider_input: object) -> object:
    """The provider input with its writer, searcher and reader logged as `callback` rows."""

    def wrapped(name: str, function: object) -> object:
        def call(*arguments: object) -> object:
            was_active = ACTIVE["value"]
            ACTIVE["value"] = False
            try:
                result = function(*arguments)  # type: ignore[operator]
            except BaseException as error:
                ACTIVE["value"] = was_active
                log({"event": "callback", "name": name, "arguments": plain(list(arguments)),
                     "raised": {"class": type(error).__name__, "message": str(error),
                                "code": getattr(error, "code", None),
                                **({"field_paths": list(error.field_paths),
                                    "validation_issues": [
                                        issue.as_payload() for issue in error.validation_issues],
                                    "state_changed": error.state_changed,
                                    "refusal_reasons": list(error.refusal_reasons)}
                                   if hasattr(error, "validation_issues") else {})}})
                raise
            ACTIVE["value"] = was_active
            log({"event": "callback", "name": name, "arguments": plain(list(arguments)),
                 "result": plain(result)})
            return result
        return call

    changes = {name: wrapped(name, getattr(provider_input, name))
               for name in ("narrative_writer", "knowledge_searcher", "reference_reader")
               if getattr(provider_input, name, None) is not None}
    return dataclasses.replace(provider_input, **changes) if changes else provider_input


def traced_call(function, self, state, args, kwargs, variant):  # type: ignore[no-untyped-def]
    provider_input = args[0] if args else None
    turn_id = kwargs.get("turn_id")
    supplier = getattr(provider_input, "catalog_supplier", None)
    if supplier is not None:
        def logged(request_id: str) -> object:
            result = supplier(request_id)
            log({"event": "catalog", "request_id": request_id, "result": plain(result)})
            return result
        provider_input = dataclasses.replace(provider_input, catalog_supplier=logged)
        args = (provider_input, *args[1:])
    if provider_input is not None and BATCH_PHASES.intersection(PHASES):
        provider_input = logged_callbacks(provider_input)
        args = (provider_input, *args[1:])
    provider = self._provider  # noqa: SLF001
    self._provider = AuditedProvider(provider)  # noqa: SLF001
    log({"event": "phase_start", "phase": function.__name__, "variant": variant,
         "turn_id": turn_id, "adapter": getattr(self, "_trace_adapter", None),
         "model": plain(getattr(provider, "model_info", None)),
         "input": dump_input(provider_input) if provider_input is not None else None,
         "state": dump_state(state)})
    ACTIVE["value"] = True
    try:
        result = function(self, state, *args, **kwargs)
        generated = result is not None and hasattr(result, "__next__")
        if generated:
            events = []
            for event in result:
                log({"event": "phase_event", "value": plain(event)})
                events.append(event)
            result = events
    except BaseException as error:
        ACTIVE["value"] = False
        log({"event": "phase_end", "raised": {"class": type(error).__name__,
                                             "message": str(error),
                                             "code": getattr(error, "code", None)},
             "state": dump_state(state)})
        if variant is None:
            raise
        return None
    finally:
        ACTIVE["value"] = False
        self._provider = provider  # noqa: SLF001
    log({"event": "phase_end", "state": dump_state(state),
         **({"returned": plain(result)} if result is not None and not generated else {})})
    return result


def traced_relay(function, self, state, args, kwargs):  # type: ignore[no-untyped-def]
    """A real call of a generator phase, its events passed on as they come."""
    from sylvia_gm.agent._session_loop import ProviderEventKind

    provider_input = args[0] if args else None
    supplier = getattr(provider_input, "catalog_supplier", None)
    if supplier is not None:
        def logged(request_id: str) -> object:
            result = supplier(request_id)
            log({"event": "catalog", "request_id": request_id, "result": plain(result)})
            return result
        provider_input = dataclasses.replace(provider_input, catalog_supplier=logged)
        args = (provider_input, *args[1:])
    if provider_input is not None and BATCH_PHASES.intersection(PHASES):
        provider_input = logged_callbacks(provider_input)
        args = (provider_input, *args[1:])
    provider = self._provider  # noqa: SLF001
    self._provider = AuditedProvider(provider)  # noqa: SLF001
    log({"event": "phase_start", "phase": function.__name__, "variant": None,
         "turn_id": kwargs.get("turn_id"), "adapter": getattr(self, "_trace_adapter", None),
         "model": plain(getattr(provider, "model_info", None)),
         "input": dump_input(provider_input) if provider_input is not None else None,
         "state": dump_state(state)})
    ACTIVE["value"] = True
    try:
        for event in function(self, state, *args, **kwargs):
            log({"event": "phase_event", "value": plain(event)})
            ACTIVE["value"] = False
            yield event
            ACTIVE["value"] = True
            if event.kind is ProviderEventKind.ACTION:
                feedback = event.action_feedback
                log({"event": "action_handled",
                     "rejection": plain(feedback.rejection) if feedback is not None else None})
    except BaseException as error:
        ACTIVE["value"] = False
        log({"event": "phase_end", "raised": {"class": type(error).__name__,
                                             "message": str(error),
                                             "code": getattr(error, "code", None)},
             "state": dump_state(state)})
        raise
    finally:
        ACTIVE["value"] = False
        self._provider = provider  # noqa: SLF001
    log({"event": "phase_end", "state": dump_state(state)})


#: Each traced phase's own function, before `wrap` replaced it.
ORIGINAL: dict[str, object] = {}

#: Other phases run on a copy of a traced phase's state, for phases a recorded turn rarely
#: reaches (T4's narration phases, from the final narration's state). Each is a phase name and
#: a variant as `VARIANTS` gives them.
NARRATED = "The gate creaks open on the cold yard."

CROSS_VARIANTS: dict[str, list[tuple[str, dict[str, object]]]] = {
    # A stand-in turn narrates through the Campaign File, so the prose-only phases run on the
    # state a round's reconciliation starts from, with the round's prose set.
    "reconcile_tool_batch": [
        ("record_empty_completion", {"name": "no finish reason",
                                     "set": {"narration": "", "round_finish_reason": None}}),
        ("record_empty_completion", {"name": "an empty finish reason",
                                     "set": {"narration": "", "round_finish_reason": ""}}),
        ("record_empty_completion", {"name": "a finish reason",
                                     "set": {"narration": "", "round_finish_reason": "length"}}),
        ("retry_repeated_response", {"name": "a repeated response", "set": {"narration": NARRATED}}),
        ("retry_repeated_response", {"name": "a repeated non-ASCII response",
                                     "set": {"narration": "Ça va — l’œuvre est là. 🌒"}}),
        ("retry_missing_tool_call", {"name": "a missing tool call", "set": {"narration": NARRATED}}),
        ("retry_private_deliberation", {"name": "private deliberation",
                                        "set": {"narration": "I should roll first."}}),
        ("record_final_narration", {"name": "the final narration", "set": {"narration": NARRATED}}),
        ("record_held_narration", {"name": "held narration with native data", "set": {
            "narration": NARRATED, "assistant_reasoning": ["Thinking."],
            "native_chat_metadata": {"thought": "x"}}}),
        ("retry_missing_campaign_file_update", {"name": "a rejected Campaign File call", "set": {
            "narration": NARRATED, "buffered_narration": None, "buffered_raw_response_blocks": None}}),
        ("retry_missing_campaign_file_update", {"name": "a held response", "set": {
            "narration": NARRATED, "buffered_narration": "Held.", "buffered_raw_response_blocks": None}}),
        ("retry_missing_campaign_file_update", {"name": "held response blocks", "set": {
            "narration": NARRATED, "buffered_narration": None,
            "buffered_raw_response_blocks": [{"kind": "narration", "text": "Held."}]}}),
        ("preserve_boundary_narration", {"name": "a GM decision still active",
                                         "set": {"narration": NARRATED}}),
        ("preserve_player_intent_narration", {"name": "the player's action still pending",
                                              "set": {"narration": NARRATED}}),
        ("retain_campaign_file_narration", {"name": "held for the Campaign File",
                                            "set": {"narration": NARRATED}}),
    ],
}


def run_cross_variants(name, self, before, args, kwargs):  # type: ignore[no-untyped-def]
    for other, variant in CROSS_VARIANTS.get(name, []):
        # Only for a phase this run traces, so the anchor's own vectors stay as they were.
        if other not in PHASES:
            continue
        function = ORIGINAL.get(other) or getattr(_provider_turn_phases, other)
        run_variant(function, self, before, args, kwargs, variant)


def wrap(name: str) -> None:
    function = getattr(_provider_turn_phases, name)
    ORIGINAL[name] = function

    if inspect.isgeneratorfunction(function):
        def traced(self, state, *args, **kwargs):  # type: ignore[no-untyped-def]
            before = copy_state(state)
            yield from traced_relay(function, self, state, args, kwargs)
            for variant in VARIANTS.get(name, []):
                run_variant(function, self, before, args, kwargs, variant)
            run_cross_variants(name, self, before, args, kwargs)
    else:
        def traced(self, state, *args, **kwargs):  # type: ignore[no-untyped-def]
            before = copy_state(state)
            result = traced_call(function, self, state, args, kwargs, None)
            for variant in VARIANTS.get(name, []):
                run_variant(function, self, before, args, kwargs, variant)
            run_cross_variants(name, self, before, args, kwargs)
            return result

    traced.__name__ = name
    setattr(_provider_turn_phases, name, traced)


#: The pseudo-phase that traces `StatelessMcpMechanics._list_tools` (T3m-1).
LISTING = "list_tools"


@dataclasses.dataclass(frozen=True)
class PlanStub:
    """An accepted Campaign Plan for a listing variant: the listing reads only its content."""

    content: dict[str, object]


def catalog_tool(name: str, schema: dict[str, object], description: str = "",
                 behavior: str = "optional") -> object:
    from sylvia_gm.application.authority import CatalogTool
    return CatalogTool(package_id="sylvia.test", package_version="1.0.0", package_sha256="0" * 64,
                       name=name, capability=f"test.{name}", input_schema=schema,
                       execute=lambda *_: None, description=description,
                       model_behavior=ModelToolBehavior(behavior))


def probe_tools() -> list[object]:
    """Catalog tools for `_model_input_schema`'s and the exposure's edges."""
    spell = {"type": "string", "enum": ["fire_bolt", "magic_missile", "shield", "mm_alias", "wish"],
             "x-sylvia-gm-castable-spells": True,
             "x-sylvia-gm-spell-scope": ["magic_missile", "fire_bolt", "shield", "wish"],
             "x-sylvia-gm-value-aliases": {"mm_alias": "magic_missile"}}
    return [
        catalog_tool("probe_schema", {
            "title": None,
            "x-sylvia-gm-exposure": {"any_of": [{"weird": 1}]},
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "target": {"type": "string", "enum": ["old"], "x-sylvia-gm-reference": "participant"},
                "who": {"type": "string", "x-sylvia-gm-reference": "participant"},
                "derived_actor": {"type": "string", "x-sylvia-gm-derived": {"kind": "actor"}},
                "null_derived": {"type": "string", "x-sylvia-gm-derived": None},
                "secret": {"type": "string", "x-sylvia-gm-model-hidden": True},
                "loosely_hidden": {"type": "string", "x-sylvia-gm-model-hidden": 1},
                "need": {"type": "integer", "x-sylvia-gm-model-required": True},
                "spell": spell,
                "plain_enum": {"type": "string", "enum": ["a"], "x-sylvia-gm-spell-scope": ["a"]},
            },
            "required": ["secret", "target", "zzz", 1, "derived_actor"],
            "additionalProperties": False,
        }, behavior="required_ruleset_decision"),
        catalog_tool("probe_unasked_spell", {
            "type": "object", "properties": {},
            "x-sylvia-gm-exposure": {"any_of": [{"spells": ["wish"]}]},
        }, description="Shown only when a wish is in play."),
        catalog_tool("probe_state_spell", {
            "type": "object", "properties": {},
            "x-sylvia-gm-exposure": {"any_of": [{"spells": ["probe_lasting_effect"]}]},
        }),
        catalog_tool("probe_entity_field", {
            "type": "object", "properties": {"x": {"type": "string"}}, "required": "x",
            "x-sylvia-gm-exposure": {"any_of": [{"entity_field": "probe.level", "op": "gte1"},
                                                {"entity_field": "probe.flag", "op": "true"}]},
        }),
        catalog_tool("probe_absent_field", {
            "type": "object", "properties": {}, "required": {"x": 1},
            "x-sylvia-gm-exposure": {"any_of": [{"entity_field": "probe.missing", "op": "not_null"}]},
        }),
        catalog_tool("probe_no_spells", {
            "type": "object",
            "properties": {"spell": {"type": "string", "enum": ["wish"],
                                     "x-sylvia-gm-castable-spells": True}},
        }),
    ]


def listing_snapshot(owner: object, campaign_id: str) -> tuple[object, object]:
    """The model context with castable spells: a spellbook, prepared spells and probe entities."""
    snapshot, boundary_ = owner.model_context_snapshot(campaign_id)  # type: ignore[attr-defined]
    participants = tuple(
        dataclasses.replace(participant, data={**participant.data,
                                                "wizard_spellbook": ["magic_missile", 7, "shield"]})
        if index == 0 else participant
        for index, participant in enumerate(snapshot.participants))
    state = {**snapshot.state,
             "sylvia.probe.entities": {"e1": {"prepared_spells": ["fire_bolt", None]}, "e2": "x",
                                       "e3": {"prepared_spells": "shield"}},
             "sylvia.other.entities": ["not", "a", "map"]}
    return dataclasses.replace(snapshot, participants=participants, state=state), boundary_


def listing_variants(owner: object, campaign_id: str) -> list[dict[str, object]]:
    from sylvia_gm.domain.errors import CommandRejected
    real = owner.catalog(campaign_id)  # type: ignore[attr-defined]
    probes = (*real, *probe_tools())
    state = owner.snapshot(campaign_id)  # type: ignore[attr-defined]
    lasting = dataclasses.replace(state, state={
        **state.state, "sylvia.probe.effects": {"effect": {"source": "probe_lasting_effect"}},
        "sylvia.probe.entities": {"e1": {"probe": {"level": 2}}, "e2": {"probe": {"flag": True}},
                                  "e3": {"probe": {"level": True}}}})
    options = {"campaign_revision": 3, "character_update_schema": {
        "type": "object", "properties": {"hp": {"type": "integer"}}, "required": ["hp"]},
        "operations": ["record_memory", "record_entity", "update_scene", "update_plan_progress",
                       "reveal_secret", "update_character", "update_inventory"],
        "scene_act_ids": ["act-1", "act-2"], "plan_item_ids": ["objective:a", "act:1"],
        "eligible_secret_ids": ["secret-1"]}
    rejected = CommandRejected("Accepted Campaign Plan is unavailable")
    return [
        {"name": "probe_tools", "answer": {"catalog": probes,
                                           "model_context_snapshot": listing_snapshot(owner, campaign_id)}},
        {"name": "probe_exposure", "answer": {"catalog": probes, "snapshot": lasting}},
        {"name": "no_session_no_plan", "session_id": None,
         "answer": {"accepted_campaign_plan": rejected}},
        {"name": "readable_plan", "session_id": None,
         "answer": {"accepted_campaign_plan": PlanStub({"campaign_plan_markdown": "# Plan"})}},
        {"name": "structured_plan", "session_id": None,
         "answer": {"accepted_campaign_plan": PlanStub({"campaign_plan": {}})}},
        {"name": "every_note", "answer": {"campaign_narrative_options": options}},
        {"name": "no_notes", "answer": {"campaign_narrative_options": {**options, "operations": []}}},
        {"name": "invalid_notes",
         "answer": {"campaign_narrative_options": {**options, "scene_act_ids": "act-1"}}},
        {"name": "catalog_refused",
         "answer": {"catalog": CommandRejected("Campaign is transitioning", code="probe_code")}},
        {"name": "invalid_properties",
         "answer": {"catalog": (catalog_tool("bad", {"type": "object", "properties": []}),)}},
        {"name": "invalid_property",
         "answer": {"catalog": (catalog_tool("bad", {"type": "object", "properties": {"a": 1}}),)}},
        {"name": "invalid_derivation", "answer": {"catalog": (catalog_tool("bad", {
            "type": "object", "properties": {"a": {"x-sylvia-gm-derived": "actor"}}}),)}},
        {"name": "fixed", "fixed": True},
    ]


LISTING_DONE = {"value": False}


def described(error: BaseException) -> dict[str, object]:
    """What a listing raised: its class and message, an `MCPError`'s data code, and for an
    exception group the exceptions it holds."""
    data = getattr(error, "data", None)
    found: dict[str, object] = {
        "class": type(error).__name__, "message": str(error),
        "code": data.get("code") if isinstance(data, dict) else getattr(error, "code", None)}
    if isinstance(error, BaseExceptionGroup):
        found["exceptions"] = [described(inner) for inner in error.exceptions]
    return found


def wrap_listing() -> None:
    from sylvia_gm.agent import _stateless_mcp

    original = _stateless_mcp.StatelessMcpMechanics._list_tools  # noqa: SLF001

    async def run(self, campaign_id, variant, **kwargs):  # type: ignore[no-untyped-def]
        log({"event": "listing_start", "variant": variant, "campaign_id": campaign_id, **kwargs})
        ACTIVE["value"] = True
        try:
            tools = await original(self, campaign_id, **kwargs)
        except BaseException as error:
            ACTIVE["value"] = False
            log({"event": "listing_end", "raised": described(error)})
            if variant is None:
                raise
            return None
        finally:
            ACTIVE["value"] = False
        log({"event": "listing_end", "tools": plain(tools)})
        return tools

    async def traced(self, campaign_id, *, purpose, discovery_id, session_id, fixed=False):  # type: ignore[no-untyped-def]
        owner = self._owner  # noqa: SLF001
        self._owner = Traced(owner)  # noqa: SLF001
        try:
            kwargs = {"purpose": purpose, "discovery_id": discovery_id, "session_id": session_id,
                      "fixed": fixed}
            tools = await run(self, campaign_id, None, **kwargs)
            if session_id is not None and not fixed and not LISTING_DONE["value"]:
                LISTING_DONE["value"] = True
                try:
                    variants = listing_variants(owner, campaign_id)
                except BaseException:
                    import traceback
                    log({"event": "listing_error", "traceback": traceback.format_exc()})
                    raise
                for variant in variants:
                    OVERRIDES.clear()
                    OVERRIDES.update(variant.get("answer", {}))  # type: ignore[arg-type]
                    changed = {**kwargs, "discovery_id": f"{discovery_id}-{variant['name']}",
                               **{key: variant[key] for key in ("session_id", "fixed")
                                  if key in variant}}
                    await run(self, campaign_id, variant["name"], **changed)
                    OVERRIDES.clear()
            return tools
        finally:
            self._owner = owner  # noqa: SLF001

    _stateless_mcp.StatelessMcpMechanics._list_tools = traced  # noqa: SLF001


#: The pseudo-phase that traces `StatelessMcpMechanics.execute_action` (T3m-2).
EXECUTION = "execute_action"
EXECUTION_DONE = {"value": False}


def castable_catalog(owner: object, campaign_id: str, tool_name: str) -> tuple[object, ...]:
    """The catalog with `tool_name` given a castable `spell` choice."""
    found = []
    for tool in owner.catalog(campaign_id):  # type: ignore[attr-defined]
        if tool.name == tool_name:
            schema = copy.deepcopy(dict(tool.input_schema))
            schema["properties"] = {**schema["properties"], "spell": {
                "type": "string", "enum": ["fire_bolt", "magic_missile", "wish"],
                "x-sylvia-gm-castable-spells": True}}
            tool = dataclasses.replace(tool, input_schema=schema)
        found.append(tool)
    return tuple(found)


def execution_variants(owner: object, command: object) -> list[dict[str, object]]:
    from sylvia_gm.domain.authority import (
        AwaitingChoiceReceipt, MechanicsChoiceOption, MechanicsChoiceRequest)
    from sylvia_gm.domain.errors import (
        CommandRejected, IdempotencyConflict, MechanicsRejected, MechanicsValidationIssue,
        RulesetPreconditionRejected, StaleRevision, WrongOwner, WrongWriter)
    campaign_id = command.campaign_id  # type: ignore[attr-defined]
    tool_name = command.tool_name  # type: ignore[attr-defined]
    catalog = owner.catalog(campaign_id)  # type: ignore[attr-defined]
    without = tuple(tool for tool in catalog if tool.name != tool_name)
    issue = MechanicsValidationIssue(path="skill", kind="invalid_type", expected_type="string",
                                     actual_type="integer")
    enum_issue = MechanicsValidationIssue(path="ability", kind="invalid_enum_value",
                                          allowed_values=("strength", "dexterity"))
    spell_issue = MechanicsValidationIssue(path="spell", kind="invalid_enum_value",
                                           allowed_values=("fire_bolt", "magic_missile", "wish"))
    choice = AwaitingChoiceReceipt(
        campaign_id=campaign_id, action_id=command.action_id,  # type: ignore[attr-defined]
        basis_revision=7, turn_id=command.turn_id,  # type: ignore[attr-defined]
        choice=MechanicsChoiceRequest(choice_id="choice-1", prompt="Which way?", options=(
            MechanicsChoiceOption(option_id="left", label="Left"),
            MechanicsChoiceOption(option_id="right", label="Right"))),
        choice_sequence=2)
    return [
        {"name": "awaiting_choice", "answer": {"execute_action": choice}},
        {"name": "stale_revision", "answer": {"execute_action": StaleRevision("stale")}},
        {"name": "wrong_owner", "answer": {"execute_action": WrongOwner("owner")}},
        {"name": "wrong_writer", "answer": {"execute_action": WrongWriter("writer")}},
        {"name": "idempotency_conflict",
         "answer": {"execute_action": IdempotencyConflict("conflict")}},
        {"name": "command_rejected",
         "answer": {"execute_action": CommandRejected("Probe refusal", code="probe_refused")}},
        {"name": "plain_rejected", "answer": {"execute_action": CommandRejected("Probe refusal")}},
        {"name": "mechanics_rejected", "answer": {"execute_action": MechanicsRejected(
            "Probe mechanics refusal", code="probe_mechanics", field_paths=("skill",),
            validation_issues=(issue,), state_changed=True,
            refusal_reasons=("Reason one.", "Reason two."))}},
        {"name": "enum_not_castable", "answer": {"execute_action": MechanicsRejected(
            "Ability is not allowed", code="probe_enum", field_paths=("ability",),
            validation_issues=(enum_issue,))}},
        {"name": "castable_refusal", "answer": {
            "catalog": castable_catalog(owner, campaign_id, tool_name),
            "model_context_snapshot": listing_snapshot(owner, campaign_id),
            "execute_action": MechanicsRejected(
                "Spell is not allowed", code="probe_spell", field_paths=("spell",),
                validation_issues=(spell_issue, issue), state_changed=False,
                refusal_reasons=("Kept out.",))}},
        {"name": "precondition", "answer": {"execute_action": RulesetPreconditionRejected(
            "The target is out of reach.", reasons=("Missing input 'target_distance'",))}},
        {"name": "value_error", "answer": {"execute_action": ValueError("probe value")}},
        {"name": "key_error", "answer": {"execute_action": KeyError("probe")}},
        {"name": "revalidation_unavailable", "answer": {"catalog": without}},
        {"name": "no_longer_available", "answer": {"catalog": Answers(catalog, catalog, without)}},
    ]


def wrap_execution() -> None:
    from sylvia_gm.agent import _stateless_mcp

    _stateless_mcp.uuid = Uuids()  # type: ignore[attr-defined]
    original = _stateless_mcp.StatelessMcpMechanics.execute_action

    def run(self, command, variant):  # type: ignore[no-untyped-def]
        log({"event": "execution_start", "variant": variant, "command": plain(command)})
        ACTIVE["value"] = True
        try:
            receipt = original(self, command)
        except BaseException as error:
            ACTIVE["value"] = False
            log({"event": "execution_end",
                 "raised": {**refusal_fields(error), **described(error)}})
            if variant is None:
                raise
            return None
        finally:
            ACTIVE["value"] = False
        log({"event": "execution_end", "receipt": plain(receipt)})
        return receipt

    def traced(self, command):  # type: ignore[no-untyped-def]
        owner = self._owner  # noqa: SLF001
        self._owner = Traced(owner)  # noqa: SLF001
        try:
            receipt = run(self, command, None)
            if not EXECUTION_DONE["value"]:
                EXECUTION_DONE["value"] = True
                for variant in execution_variants(owner, command):
                    OVERRIDES.clear()
                    OVERRIDES.update(variant["answer"])  # type: ignore[arg-type]
                    run(self, command, variant["name"])
                    OVERRIDES.clear()
            return receipt
        finally:
            self._owner = owner  # noqa: SLF001

    _stateless_mcp.StatelessMcpMechanics.execute_action = traced


#: The pseudo-phase that traces the Campaign File commit and the reference reads (T3m-3).
CAMPAIGN_FILE = "campaign_file"
CAMPAIGN_FILE_DONE = {"value": False}


def campaign_file_variants(owner: object, campaign_id: str, session_id: str,
                           real: dict[str, object]) -> list[dict[str, object]]:
    """Calls of `commit_campaign_file`, `search_knowledge` and `read_reference` beside the
    first real commit: each validation branch, the owner's refusals and the turn lookup."""
    from sylvia_gm.domain.errors import CommandRejected, MechanicsRejected, MechanicsValidationIssue
    fact = {"fact_id": "fact-1", "category": "discovery", "text": "The ledger is hidden.",
            "visibility": "gm_private", "known_by": []}
    changed = {"state_change": "changed", "state_change_summary": "One discovery."}
    unchanged = {"state_change": "unchanged", "state_change_summary": "Nothing durable changed."}
    options = owner.campaign_narrative_options(session_id)  # type: ignore[attr-defined]
    every_note = {**options, "character_update_schema": {
        "type": "object", "properties": {"hp": {"type": "integer"}, "note": {"type": "string",
                                                                          "maxLength": 5}},
        "required": ["hp"], "additionalProperties": False},
        "operations": ["record_memory", "record_entity", "update_scene", "update_plan_progress",
                       "reveal_secret", "update_character", "update_inventory"],
        "scene_act_ids": ["act-1"], "plan_item_ids": ["act:1"], "eligible_secret_ids": ["secret-1"]}
    updated = {"status": "campaign_file_updated", "update_id": "update", "campaign_revision": 9,
               "state_changed": True}
    refusal = MechanicsRejected(
        "Character update is invalid", code="character_update_invalid", field_paths=("hp",),
        validation_issues=(MechanicsValidationIssue(path="hp", kind="wrong_type",
                                                    expected_type="integer", actual_type="string"),),
        state_changed=False)
    commit = {"method": "commit_campaign_file", "campaign_id": campaign_id, "session_id": session_id,
              "basis_message_id": real["basis_message_id"], "turn_id": real["turn_id"]}
    search = {"method": "search_knowledge", "campaign_id": campaign_id, "session_id": session_id}
    read = {"method": "read_reference", "campaign_id": campaign_id, "session_id": session_id}
    return [
        {**commit, "name": "nothing changed", "parameters": unchanged},
        {**commit, "name": "no assessment", "parameters": {}},
        # 600 characters, 1200 UTF-8 bytes: within the 1000-character bound.
        {**commit, "name": "a long non-ASCII summary", "parameters": {
            **unchanged, "state_change_summary": "é" * 600}},
        {**commit, "name": "a non-ASCII summary over the bound", "parameters": {
            **unchanged, "state_change_summary": "é" * 1001}},
        {**commit, "name": "foreign and mistyped collections", "parameters": {
            **changed, "bogus": 1, "facts": "not a list", "entities": [], "zz_other": None}},
        {**commit, "name": "too many items", "parameters": {**changed, "facts": [fact] * 65}},
        {**commit, "name": "item shapes", "parameters": {**changed, "facts": [
            1, {"fact_id": 5, "category": "oops", "text": "", "visibility": "player_known",
                "known_by": [1, "a"], "extra": True},
            {**fact, "text": "x" * 2001, "subjects": "one"}]}},
        {**commit, "name": "changed without changes", "parameters": changed},
        {**commit, "name": "unchanged with a change", "parameters": {**unchanged, "facts": [fact]}},
        {**commit, "name": "a committed change", "parameters": {**changed, "facts": [fact]},
         "answer": {"update_campaign_file": updated}},
        {**commit, "name": "the owner refuses", "parameters": {**changed, "facts": [fact]},
         "answer": {"update_campaign_file": refusal}},
        {**commit, "name": "the owner fails", "parameters": {**changed, "facts": [fact]},
         "answer": {"update_campaign_file": KeyError("probe")}},
        {**commit, "name": "every note type", "parameters": {**changed,
            "scenes": [{"summary": "At the gate.", "act_id": "act-2"}],
            "character_updates": [{"hp": "9"}, {"hp": 9, "note": "too long"}, {"hp": 9}],
            "plan_progress": [{"plan_item_id": "act:1", "progress_status": "engaged",
                               "progress_summary": "Met the smith."}]},
         "answer": {"campaign_narrative_options": every_note, "update_campaign_file": updated}},
        {**commit, "name": "invalid options", "parameters": {**changed, "scenes": [
            {"summary": "At the gate."}]},
         "answer": {"campaign_narrative_options": {**options, "scene_act_ids": "act-1"}}},
        {**commit, "name": "the open turn", "turn_id": None, "parameters": unchanged},
        {**commit, "name": "no open turn", "turn_id": None, "parameters": unchanged,
         "answer": {"current_open_turn": None}},
        {**search, "name": "a search", "query": "ledger", "sources": ["campaign_state"],
         "limit": 3, "offset": 0},
        {**search, "name": "a search of every source", "query": "smith", "sources": None,
         "limit": 5, "offset": 1},
        {**search, "name": "a refused search", "query": "ledger", "sources": None, "limit": 5,
         "offset": 0, "answer": {"search_campaign_knowledge": CommandRejected(
             "Knowledge search limit is invalid", code="campaign_knowledge_limit_invalid")}},
        {**search, "name": "a failed search", "query": "ledger", "sources": None, "limit": 5,
         "offset": 0, "answer": {"search_campaign_knowledge": KeyError("probe")}},
        {**read, "name": "a read of current state", "source": "campaign_state", "path": "current"},
        {**read, "name": "a read of a missing path", "source": "campaign_plan", "path": "nowhere"},
        {**read, "name": "a refused read", "source": "campaign_state", "path": "current",
         "answer": {"read_campaign_reference": CommandRejected("Reference path is unavailable",
                                                               code="campaign_reference_unavailable")}},
    ]


def wrap_campaign_file() -> None:
    from sylvia_gm.agent import _stateless_mcp

    methods = {name: getattr(_stateless_mcp.StatelessMcpMechanics, name)
               for name in ("commit_campaign_file", "search_knowledge", "read_reference")}

    def run(self, method, variant, campaign_id, **kwargs):  # type: ignore[no-untyped-def]
        log({"event": "campaign_file_start", "variant": variant, "method": method,
             "campaign_id": campaign_id, **plain(kwargs)})
        ACTIVE["value"] = True
        try:
            result = methods[method](self, campaign_id, **kwargs)
        except BaseException as error:
            ACTIVE["value"] = False
            log({"event": "campaign_file_end",
                 "raised": {**refusal_fields(error), **described(error)}})
            if variant is None:
                raise
            return None
        finally:
            ACTIVE["value"] = False
        log({"event": "campaign_file_end", "result": plain(result)})
        return result

    def traced_method(method):  # type: ignore[no-untyped-def]
        def traced(self, campaign_id, **kwargs):  # type: ignore[no-untyped-def]
            owner = self._owner  # noqa: SLF001
            self._owner = Traced(owner)  # noqa: SLF001
            try:
                result = run(self, method, None, campaign_id, **kwargs)
                if method == "commit_campaign_file" and not CAMPAIGN_FILE_DONE["value"]:
                    CAMPAIGN_FILE_DONE["value"] = True
                    for variant in campaign_file_variants(owner, campaign_id,
                                                          kwargs["session_id"], kwargs):
                        arguments = {key: value for key, value in variant.items()
                                     if key not in {"name", "method", "campaign_id", "answer"}}
                        OVERRIDES.clear()
                        OVERRIDES.update(variant.get("answer", {}))  # type: ignore[arg-type]
                        run(self, variant["method"], variant["name"], campaign_id, **arguments)
                        OVERRIDES.clear()
                return result
            finally:
                self._owner = owner  # noqa: SLF001
        return traced

    for method in methods:
        setattr(_stateless_mcp.StatelessMcpMechanics, method, traced_method(method))


for phase in PHASES:
    if phase == LISTING:
        wrap_listing()
    elif phase == EXECUTION:
        wrap_execution()
    elif phase == CAMPAIGN_FILE:
        wrap_campaign_file()
    else:
        wrap(phase)

_ = SimpleNamespace  # keep the import for future variants

if __name__ == "__main__":
    raise SystemExit(owner_process.main())
