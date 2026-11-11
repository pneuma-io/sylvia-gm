from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
from enum import Enum
import hashlib
import json
from typing import Any


class Stance(str, Enum):
    STANDING = "STANDING"
    CROUCHED = "CROUCHED"
    PRONE = "PRONE"


class Side(str, Enum):
    A = "A"
    B = "B"


class KnowledgeState(str, Enum):
    UNAWARE = "UNAWARE"
    SUSPICIOUS = "SUSPICIOUS"
    APPROXIMATE = "APPROXIMATE"
    LAST_KNOWN = "LAST_KNOWN"
    CONFIRMED = "CONFIRMED"


class ConditionState(str, Enum):
    ACTIVE = "ACTIVE"
    STABILIZED = "STABILIZED"
    FAILED = "FAILED"


class DetectionState(str, Enum):
    UNDETECTED = "UNDETECTED"
    SUSPICIOUS = "SUSPICIOUS"
    LOCATED = "LOCATED"
    CONFIRMED = "CONFIRMED"


class HitQuality(str, Enum):
    MISS = "MISS"
    HIT = "HIT"
    CLEAN_HIT = "CLEAN HIT"
    DECISIVE_HIT = "DECISIVE HIT"
    CRITICAL_HIT = "CRITICAL HIT"


class CoverageState(str, Enum):
    IMPLEMENTED = "IMPLEMENTED"
    IMPLEMENTED_WITH_PARAMETERS = "IMPLEMENTED_WITH_PARAMETERS"
    UNRESOLVED_SOURCE = "UNRESOLVED_SOURCE"
    AMBIGUOUS_SOURCE = "AMBIGUOUS_SOURCE"
    MISSING_DEPENDENCY = "MISSING_DEPENDENCY"
    STRUCTURAL_FINDING = "STRUCTURAL_FINDING"
    DEFERRED_OUT_OF_SCOPE = "DEFERRED_OUT_OF_SCOPE"


@dataclass(frozen=True, slots=True)
class Position:
    x: float
    y: float

    def distance_to(self, other: "Position") -> float:
        return ((self.x - other.x) ** 2 + (self.y - other.y) ** 2) ** 0.5


@dataclass(slots=True)
class WeaponState:
    weapon_id: str
    magazine_capacity: int
    magazine_rounds: int
    chamber: str = "LIVE"
    reserve_rounds: int = 0
    current_recoil: int = 0
    needs_cycle: bool = False
    mechanism: str = "self-loader"
    fire_modes: tuple[str, ...] = ("SEMI AUTO",)
    retained_magazines: list[int] = field(default_factory=list)

    def total_rounds(self) -> int:
        return self.magazine_rounds + self.reserve_rounds + sum(self.retained_magazines) + (1 if self.chamber == "LIVE" else 0)


@dataclass(slots=True)
class LifeThreateningCondition:
    condition_id: str
    cause: str
    state: ConditionState
    deterioration_seconds: float
    stabilization_requirement: str
    reversible_window_seconds: float | None = None
    elapsed_seconds: float = 0.0


@dataclass(slots=True)
class ActorState:
    actor_id: str
    side: Side
    class_id: str
    level: int
    fixture_id: str
    skill: int
    aptitude: int
    hp: int
    max_hp: int
    res: int
    max_res: int
    ap: int
    max_ap: int
    position: Position
    stance: Stance = Stance.STANDING
    burden: int = 0
    weapon: WeaponState | None = None
    downed: bool = False
    conditions: list[LifeThreateningCondition] = field(default_factory=list)
    supplies: dict[str, int] = field(default_factory=dict)
    specialty_nodes: set[str] = field(default_factory=set)
    target_knowledge: dict[str, KnowledgeState] = field(default_factory=dict)
    target_last_known: dict[str, Position] = field(default_factory=dict)
    controlled_by: str | None = None
    control_focus: str | None = None
    casualty_mode: str | None = None
    casualty_id: str | None = None
    carried_by: str | None = None
    treatment_progress_ap: int = 0


@dataclass(slots=True)
class CoverObject:
    cover_id: str
    cells: set[tuple[int, int]]
    height_m: float
    material_resistance: int
    cover_hp: int | None
    facing_degrees: float | None = None
    active: bool = True
    destroyed_passable: bool = False
    minor_debris: bool = False
    debris_cleared: bool = False


@dataclass(slots=True)
class SuppressionZone:
    zone_id: str
    owner_id: str
    cells: set[tuple[int, int]]
    committed_rounds: int
    exposure_capacity: int
    remaining_capacity: int
    expires_on_side: Side


@dataclass(slots=True)
class PreparedAction:
    prep_id: str
    owner_id: str
    kind: str
    zone_cells: set[tuple[int, int]]
    shots_remaining: int = 1
    explicit_before: bool = False
    active: bool = True
    _zone_digest: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        payload = json.dumps(sorted(self.zone_cells), separators=(",", ":"))
        self._zone_digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class NetworkNodeState:
    node_id: str
    links: set[str] = field(default_factory=set)
    segment: str = ""
    privilege: int = 0
    required_privilege: int = 0
    access_difficulty: int | None = None
    monitoring_difficulty: int | None = None
    physical_position: Position | None = None
    local_access: bool = False
    discovered: bool = False
    compromised: bool = False
    foothold: bool = False
    detection_state: DetectionState = DetectionState.UNDETECTED
    remote_functions: set[str] = field(default_factory=set)


@dataclass(slots=True)
class SimulationState:
    scenario_id: str
    scenario_version: str
    actors: dict[str, ActorState]
    covers: dict[str, CoverObject] = field(default_factory=dict)
    suppression: dict[str, SuppressionZone] = field(default_factory=dict)
    prepared: dict[str, PreparedAction] = field(default_factory=dict)
    network: dict[str, NetworkNodeState] = field(default_factory=dict)
    active_side: Side = Side.A
    revision: int = 0
    elapsed_seconds: float = 0.0
    objective: dict[str, Any] = field(default_factory=dict)
    terminal: bool = False
    terminal_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _normalize(self)

    def semantic_hash(self) -> str:
        payload = json.dumps(_semantic_normalize(self), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {item.name: _normalize(getattr(value, item.name)) for item in fields(value) if not item.name.startswith("_")}
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (set, frozenset)):
        return sorted((_normalize(v) for v in value), key=repr)
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    return value


def _semantic_normalize(value: Any) -> Any:
    if isinstance(value, PreparedAction):
        return {
            "prep_id": value.prep_id,
            "owner_id": value.owner_id,
            "kind": value.kind,
            "zone_cells_count": len(value.zone_cells),
            "zone_cells_sha256": value._zone_digest,
            "shots_remaining": value.shots_remaining,
            "explicit_before": value.explicit_before,
            "active": value.active,
        }
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {
            item.name: _semantic_normalize(getattr(value, item.name))
            for item in fields(value)
            if not item.name.startswith("_")
        }
    if isinstance(value, dict):
        return {str(key): _semantic_normalize(child) for key, child in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (set, frozenset)):
        return sorted((_semantic_normalize(child) for child in value), key=repr)
    if isinstance(value, (list, tuple)):
        return [_semantic_normalize(child) for child in value]
    return value
