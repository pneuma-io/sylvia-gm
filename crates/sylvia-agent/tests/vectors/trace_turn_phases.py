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
- `phase_end`: the turn locals after the phase, or the exception it raised.

Each call is then run again on copies of its state and input with the variants
`VARIANTS[phase]` names: some turn locals set, and some owner reads answered with given values
(logged as ordinary calls), so the Rust tests reach branches a recorded turn does not.
Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import copy
import dataclasses
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
        setattr(copied, field.name, copy.deepcopy(value))
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


class Traced:
    """The adapter's owner: every call is logged while a phase runs; overrides answer first."""

    def __init__(self, target: object) -> None:
        object.__setattr__(self, "_target", target)

    def __getattr__(self, name: str) -> object:
        target = object.__getattribute__(self, "_target")
        if ACTIVE["value"] and name in OVERRIDES:
            answer = OVERRIDES[name]

            def overridden(*args: object, **kwargs: object) -> object:
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
                log({"event": "call", "name": name, "args": plain(list(args)),
                     "kwargs": plain(kwargs),
                     "raised": {"class": type(error).__name__, "message": str(error)}})
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
# current catalog (`tools`), or a cancelled control (`cancelled`).
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


def run_variant(function, self, state, provider_input, turn_id, variant):  # type: ignore[no-untyped-def]
    state = copy_state(state)
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
    supplier = provider_input.catalog_supplier
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
    traced_call(function, self, state, provider_input, turn_id, variant["name"])
    OVERRIDES.clear()


def lock_with_entry(lock: object) -> object:
    from sylvia_gm.domain.authority import PackageLock, PackageLockEntry, PackageRole
    entry = PackageLockEntry(package_id="sylvia.test", version="1.0.0", sha256="0" * 64,
                             role=PackageRole.PRIMARY, load_order=0, dependencies=(),
                             capabilities=())
    return PackageLock((entry,))


def traced_call(function, self, state, provider_input, turn_id, variant):  # type: ignore[no-untyped-def]
    supplier = provider_input.catalog_supplier
    if supplier is not None:
        def logged(request_id: str) -> object:
            result = supplier(request_id)
            log({"event": "catalog", "request_id": request_id, "result": plain(result)})
            return result
        provider_input = dataclasses.replace(provider_input, catalog_supplier=logged)
    provider = self._provider  # noqa: SLF001
    self._provider = AuditedProvider(provider)  # noqa: SLF001
    log({"event": "phase_start", "phase": function.__name__, "variant": variant,
         "turn_id": turn_id, "adapter": getattr(self, "_trace_adapter", None),
         "model": plain(getattr(provider, "model_info", None)),
         "input": dump_input(provider_input), "state": dump_state(state)})
    ACTIVE["value"] = True
    try:
        result = function(self, state, provider_input, turn_id=turn_id)
        if result is not None and hasattr(result, "__next__"):
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
    log({"event": "phase_end", "state": dump_state(state)})
    return result


def wrap(name: str) -> None:
    function = getattr(_provider_turn_phases, name)

    def traced(self, state, provider_input, *, turn_id):  # type: ignore[no-untyped-def]
        before = copy_state(state)
        result = traced_call(function, self, state, provider_input, turn_id, None)
        for variant in VARIANTS.get(name, []):
            run_variant(function, self, before, provider_input, turn_id, variant)
        return result

    traced.__name__ = name
    setattr(_provider_turn_phases, name, traced)


for phase in PHASES:
    wrap(phase)

_ = SimpleNamespace  # keep the import for future variants

if __name__ == "__main__":
    raise SystemExit(owner_process.main())
