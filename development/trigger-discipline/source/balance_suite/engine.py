from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any, Callable

from .model import PreparedAction, Side, SimulationState
from .parameters import ParameterSet
from .rng import RNGManager


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
    ) -> EventLogEntry:
        sequence = self.event_count
        pre_hash = self.state.semantic_hash() if self.capture_log else "NOT_CAPTURED"
        stream = self.rng.stream(stream_id or f"event:{sequence}")
        draw_start = stream.position
        stages: list[dict[str, Any]] = []
        trigger_windows: list[dict[str, Any]] = []
        metrics: dict[str, Any] = {}
        rejected = resolver(stages, trigger_windows, metrics)
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
        return entry

    def resolve_trigger_window(self, effects: list[TriggerEffect]) -> list[dict[str, Any]]:
        queue = list(effects)
        resolved: list[dict[str, Any]] = []
        while queue:
            queue.sort(key=self._trigger_key)
            effect = queue.pop(0)
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
