from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from copy import deepcopy
import hashlib
import json
from typing import Any, Callable

from .model import PreparedAction, Side, SimulationState
from .parameters import ParameterSet
from .rng import RNGManager


class PendingTriggerOrder(Exception):
    def __init__(self, owners):
        self.owners = tuple(owners)
        super().__init__('same-side reactions require an explicit ordering choice')


@dataclass(slots=True)
class EventLogEntry:
    sequence: int
    pre_state_hash: str
    actor_id: str
    action: str
    choices: dict[str, Any]
    parameter_set_hash: str
    random_draws: list[dict[str, Any]]
    procedure_stages: list[dict[str, Any]]
    trigger_windows: list[dict[str, Any]]
    post_state_hash: str
    metrics: dict[str, Any]
    rejected_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TriggerEffect:
    effect_id: str
    owner_id: str
    controller_side: Side
    explicit_before: bool
    validate: Callable[[SimulationState], bool]
    resolve: Callable[[SimulationState], list["TriggerEffect"] | None]


@dataclass(slots=True)
class EventEngine:
    state: SimulationState
    parameters: ParameterSet
    master_seed: int
    capture_log: bool = True
    rng: RNGManager = field(init=False)
    log: list[EventLogEntry] = field(default_factory=list)
    event_count: int = 0
    pending: dict[str, Any] | None = None
    receipts: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.rng = RNGManager(self.master_seed)

    def record(
        self,
        actor_id: str,
        action: str,
        choices: dict[str, Any],
        resolver: Callable[[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]], str | None],
        *,
        stream_id: str | None = None,
        operation_id: str | None = None,
        atomic: bool = False,
    ) -> EventLogEntry:
        request = json.dumps({'actor_id': actor_id, 'action': action,
            'choices': {k: v for k, v in choices.items() if k != 'reaction_order'}}, sort_keys=True)
        if operation_id in self.receipts:
            old = self.receipts[operation_id]
            if old['request'] != request:
                raise ValueError('operation identity reused with changed inputs')
            return deepcopy(old['entry'])
        if self.pending is not None:
            if self.pending['operation_id'] != operation_id or self.pending['request'] != request:
                raise ValueError('resolve the pending reaction order before another action')
        checkpoint = (deepcopy(self.state), deepcopy(self.rng)) if atomic or operation_id else None
        sequence = self.event_count
        pre_hash = self.state.semantic_hash() if self.capture_log else "NOT_CAPTURED"
        stream = self.rng.stream(stream_id or f"event:{sequence}")
        draw_start = stream.position
        stages: list[dict[str, Any]] = []
        trigger_windows: list[dict[str, Any]] = []
        metrics: dict[str, Any] = {}
        try:
            rejected = resolver(stages, trigger_windows, metrics)
        except PendingTriggerOrder as choice:
            if checkpoint is None:
                raise RuntimeError('reaction choices require an atomic lab event') from choice
            saved_state, self.rng = checkpoint
            for item in fields(self.state):
                setattr(self.state, item.name, getattr(saved_state, item.name))
            self.pending = {'operation_id': operation_id, 'request': request,
                            'actor_id': actor_id, 'action': action, 'choices': choices,
                            'owners': list(choice.owners)}
            return EventLogEntry(sequence, pre_hash, actor_id, action, choices,
                self.parameters.hash, [], [], [], pre_hash, {}, 'PENDING_REACTION_ORDER')
        except Exception:
            if checkpoint is not None:
                saved_state, self.rng = checkpoint
                for item in fields(self.state):
                    setattr(self.state, item.name, getattr(saved_state, item.name))
            raise
        if rejected is None:
            self.state.revision += 1
        post_hash = self.state.semantic_hash() if self.capture_log else "NOT_CAPTURED"
        entry = EventLogEntry(
            sequence,
            pre_hash,
            actor_id,
            action,
            choices,
            self.parameters.hash,
            stream.drain_draws(draw_start),
            stages,
            trigger_windows,
            post_hash,
            metrics,
            rejected,
        )
        self.event_count += 1
        if self.capture_log:
            self.log.append(entry)
        self.pending = None
        if operation_id is not None:
            self.receipts[operation_id] = {'request': request, 'entry': deepcopy(entry)}
        return entry

    def resolve_trigger_window(self, effects: list[TriggerEffect], *, order=(), require_choice=False) -> list[dict[str, Any]]:
        queue = list(effects)
        resolved: list[dict[str, Any]] = []
        while queue:
            queue.sort(key=self._trigger_key)
            tier = self._trigger_key(queue[0])[:2]
            pool = [e for e in queue if self._trigger_key(e)[:2] == tier]
            selected = next((owner for owner in order if any(e.owner_id == owner for e in pool)), None)
            if require_choice and len(pool) > 1 and selected is None:
                raise PendingTriggerOrder([e.owner_id for e in pool])
            effect = next((e for e in pool if e.owner_id == selected), pool[0])
            queue.remove(effect)
            valid = effect.validate(self.state)
            record = {"effect_id": effect.effect_id, "owner_id": effect.owner_id, "valid": valid}
            if valid:
                nested = effect.resolve(self.state) or []
                self.state.revision += 1
                record["post_state_hash"] = self.state.semantic_hash() if self.capture_log else "NOT_CAPTURED"
                if nested:
                    record["nested"] = [item.effect_id for item in nested]
                    nested_log = self.resolve_trigger_window(nested)
                    record["nested_resolution"] = nested_log
            resolved.append(record)
        return resolved

    def _trigger_key(self, effect: TriggerEffect) -> tuple[int, int, str, str]:
        priority = 0 if effect.explicit_before else 1
        side_priority = 0 if effect.controller_side is not self.state.active_side else 1
        return priority, side_priority, effect.owner_id, effect.effect_id

    def log_hash(self) -> str:
        if self.capture_log:
            payload = json.dumps([entry.to_dict() for entry in self.log], sort_keys=True, separators=(",", ":"))
        else:
            payload = json.dumps({"event_count": self.event_count, "final_state_hash": self.state.semantic_hash()}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def prepared_effect(
    prepared: PreparedAction,
    owner_side: Side,
    resolver: Callable[[SimulationState], list[TriggerEffect] | None],
    validator: Callable[[SimulationState], bool],
) -> TriggerEffect:
    return TriggerEffect(
        prepared.prep_id,
        prepared.owner_id,
        owner_side,
        prepared.explicit_before,
        validator,
        resolver,
    )


def movement_with_triggers(
    engine: EventEngine,
    actor_id: str,
    path: list[Any],
    trigger_provider: Callable[[SimulationState, str], list[TriggerEffect]],
) -> dict[str, Any]:
    """Pause on each actual path point, resolve/revalidate triggers, then continue if capable."""
    actor = engine.state.actors[actor_id]
    visited: list[dict[str, Any]] = []
    trigger_log: list[dict[str, Any]] = []
    for position in path:
        actor.position = position
        engine.state.revision += 1
        visited.append({"x": position.x, "y": position.y})
        effects = trigger_provider(engine.state, actor_id)
        if effects:
            trigger_log.append(
                {
                    "position": {"x": position.x, "y": position.y},
                    "effects": engine.resolve_trigger_window(effects),
                }
            )
        if actor.downed:
            return {"visited": visited, "trigger_windows": trigger_log, "continued": False, "stopped_reason": "DOWNED"}
    return {"visited": visited, "trigger_windows": trigger_log, "continued": True, "stopped_reason": None}
