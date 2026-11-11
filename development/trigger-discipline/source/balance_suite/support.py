from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .geometry import GridMap
from .model import ConditionState, DetectionState, KnowledgeState, LifeThreateningCondition, NetworkNodeState, Position
from .parameters import ParameterSet
from .rules import resolve_check, resuscitate_condition, stabilize_condition


KNOWLEDGE_PRECISION = {
    KnowledgeState.UNAWARE: 0,
    KnowledgeState.SUSPICIOUS: 1,
    KnowledgeState.APPROXIMATE: 2,
    KnowledgeState.LAST_KNOWN: 3,
    KnowledgeState.CONFIRMED: 4,
}


@dataclass(frozen=True, slots=True)
class PoweredDevice:
    device_id: str
    runtime_s: int


@dataclass(frozen=True, slots=True)
class PowerBudgetResult:
    eligible: bool
    ap_spent: int
    transfer_s: int
    donor: PoweredDevice
    recipient: PoweredDevice
    recipient_runtime_if_disconnected_s: int
    output_bonus: bool
    reason: str


def reroute_power_budget(
    donor: PoweredDevice,
    recipient: PoweredDevice,
    *,
    connected: bool,
    compatible: bool,
    parameters: ParameterSet,
) -> PowerBudgetResult:
    if not connected or not compatible or donor.runtime_s <= 0:
        return PowerBudgetResult(
            False,
            0,
            0,
            donor,
            recipient,
            recipient.runtime_s,
            False,
            "requires an intact power-capable connection and compatible available donor power",
        )
    transfer_s = min(
        donor.runtime_s,
        int(parameters.values["specialty.field_engineer.power_budget.transfer_seconds"]),
    )
    return PowerBudgetResult(
        True,
        int(parameters.values["specialty.field_engineer.power_budget.action_ap"]),
        transfer_s,
        replace(donor, runtime_s=donor.runtime_s - transfer_s),
        replace(recipient, runtime_s=recipient.runtime_s + transfer_s),
        recipient.runtime_s,
        False,
        "conserved equal-load runtime reroute",
    )


@dataclass(frozen=True, slots=True)
class PersistentInjury:
    injury_id: str
    persistent_state: str
    difficulty_offset: int
    lower_persistent_state: str | None = None
    salvage_applied: bool = False
    irreparable: bool = False


@dataclass(frozen=True, slots=True)
class SalvageResult:
    eligible: bool
    applied: bool
    injury: PersistentInjury
    immediate_hp_restored: int
    reason: str


def repairable_forearm_injury(parameters: ParameterSet) -> PersistentInjury:
    return PersistentInjury(
        "INJURY_REPAIRABLE_FOREARM_TRAUMA_V1",
        "SEVERE_HAND_IMPAIRMENT",
        int(parameters.values["injury.repairable_forearm.ordinary_persistent_difficulty"]),
        "MODERATE_HAND_IMPAIRMENT",
    )


def apply_salvage(
    injury: PersistentInjury,
    *,
    successful_definitive_care: bool,
    parameters: ParameterSet,
) -> SalvageResult:
    eligible = (
        successful_definitive_care
        and not injury.irreparable
        and not injury.salvage_applied
        and injury.lower_persistent_state is not None
    )
    if not eligible:
        return SalvageResult(
            False,
            False,
            injury,
            0,
            "requires successful definitive care, repairable anatomy, an authored lower state, and no prior SALVAGE",
        )
    reduction = int(
        parameters.values["specialty.trauma.salvage.persistent_difficulty_reduction"]
    )
    return SalvageResult(
        True,
        True,
        replace(
            injury,
            persistent_state=injury.lower_persistent_state,
            difficulty_offset=max(1, injury.difficulty_offset - reduction),
            lower_persistent_state=None,
            salvage_applied=True,
        ),
        0,
        "one authored persistent severity step reduced",
    )


def share_knowledge(source: KnowledgeState, requested: KnowledgeState | None = None) -> KnowledgeState:
    if requested is None or KNOWLEDGE_PRECISION[requested] <= KNOWLEDGE_PRECISION[source]:
        return requested or source
    return source


def lose_exact_observation(current: KnowledgeState) -> KnowledgeState:
    return KnowledgeState.LAST_KNOWN if current is KnowledgeState.CONFIRMED else current


def degrade_knowledge(current: KnowledgeState, *, bounded_area_still_credible: bool, presence_still_relevant: bool) -> KnowledgeState:
    if current in {KnowledgeState.CONFIRMED, KnowledgeState.LAST_KNOWN}:
        return KnowledgeState.APPROXIMATE if bounded_area_still_credible else KnowledgeState.SUSPICIOUS
    if current is KnowledgeState.APPROXIMATE and not bounded_area_still_credible:
        return KnowledgeState.SUSPICIOUS
    if current is KnowledgeState.SUSPICIOUS and not presence_still_relevant:
        return KnowledgeState.UNAWARE
    return current


@dataclass(frozen=True, slots=True)
class MedicalActionResult:
    action: str
    eligible: bool
    roll: int | None
    success: bool
    ap_spent: int
    supplies_spent: int
    before: str
    after: str
    reason: str


def medical_action(
    condition: LifeThreateningCondition,
    action: str,
    *,
    roll: int,
    skill: int,
    aptitude: int,
    parameters: ParameterSet,
    corpsman: bool,
    capable_intervention: bool,
    within_window: bool = True,
) -> MedicalActionResult:
    action = action.upper()
    before = condition.state.value
    if action == "STABILIZE":
        base_ap = parameters.values["medicine.stabilize_ap"]
        difficulty = parameters.values["medicine.stabilize_difficulty"]
        eligible = condition.state is ConditionState.ACTIVE and capable_intervention
    elif action == "RESUSCITATION":
        base_ap = parameters.values["medicine.resuscitation_ap"]
        difficulty = parameters.values["medicine.resuscitation_difficulty"]
        eligible = condition.state is ConditionState.FAILED and capable_intervention and within_window
    else:
        raise ValueError(f"unsupported medical action {action}")
    ap = max(1, base_ap - (parameters.values["class.corpsman.medicine_ap_discount"] if corpsman else 0))
    check = resolve_check(
        roll,
        skill,
        aptitude,
        difficulty,
        parameters.values["class.corpsman.medicine_check_bonus"] if corpsman else 0,
        legitimate=eligible,
    )
    success = False
    if action == "STABILIZE":
        success = stabilize_condition(condition, capable_intervention=capable_intervention, success=check.success)
    else:
        success = resuscitate_condition(
            condition,
            capable_intervention=capable_intervention,
            within_window=within_window,
            success=check.success,
        )
    reason = "resolved" if eligible else "missing actual condition/intervention/reversible path"
    return MedicalActionResult(action, eligible, roll if eligible else None, success, ap if eligible else 0, 1 if eligible else 0, before, condition.state.value, reason)


def discover_neighbors(nodes: dict[str, NetworkNodeState], node_id: str) -> set[str]:
    if node_id not in nodes or not nodes[node_id].compromised:
        return set()
    discovered: set[str] = set()
    for neighbor in nodes[node_id].links:
        if neighbor in nodes:
            nodes[neighbor].discovered = True
            discovered.add(neighbor)
    return discovered


def valid_network_route(nodes: dict[str, NetworkNodeState], start: str, goal: str) -> list[str] | None:
    if start not in nodes or goal not in nodes:
        return None
    queue: list[tuple[str, list[str]]] = [(start, [start])]
    seen = {start}
    while queue:
        current, path = queue.pop(0)
        if current == goal:
            return path
        for neighbor in sorted(nodes[current].links):
            if neighbor in nodes and neighbor not in seen:
                seen.add(neighbor)
                queue.append((neighbor, [*path, neighbor]))
    return None


@dataclass(frozen=True, slots=True)
class CasualtyMovementResult:
    mode: str
    hands_occupied: int
    setup_ap: int
    lower_ap: int
    movement_m_per_ap: float
    trailing_space: bool
    eligible: bool


def casualty_movement(
    mode: str,
    *,
    current_movement_m_per_ap: float,
    parameters: ParameterSet,
    drag_hands: int = 2,
    physically_capable: bool = True,
) -> CasualtyMovementResult:
    normalized = mode.upper()
    if normalized == "DRAG":
        if drag_hands not in {1, 2}:
            raise ValueError("DRAG requires one or two hands")
        key = "one_hand" if drag_hands == 1 else "two_hand"
        return CasualtyMovementResult(
            normalized,
            drag_hands,
            int(parameters.values["casualty.drag.engage_ap"]),
            0,
            current_movement_m_per_ap * float(parameters.values[f"casualty.drag.{key}_rate_multiplier"]),
            True,
            physically_capable,
        )
    if normalized == "CARRY":
        return CasualtyMovementResult(
            normalized,
            2,
            int(parameters.values["casualty.carry.lift_ap"]),
            int(parameters.values["casualty.carry.lower_ap"]),
            current_movement_m_per_ap * float(parameters.values["casualty.carry.rate_multiplier"]),
            False,
            physically_capable,
        )
    raise ValueError(f"unknown casualty movement mode {mode}")


SCATTER_DIRECTIONS = (
    (0, -1),
    (1, -1),
    (1, 0),
    (1, 1),
    (0, 1),
    (-1, 1),
    (-1, 0),
    (-1, -1),
)


def grenade_scatter_distance(failure_margin: int, parameters: ParameterSet) -> int:
    if failure_margin <= 0:
        return 0
    index = 0 if failure_margin <= 4 else 1 if failure_margin <= 9 else 2 if failure_margin <= 14 else 3
    return int(parameters.values["grenade.scatter.distance_by_failure_margin_m"][index])


def grenade_scatter_landing(
    intended: Position,
    *,
    failure_margin: int,
    direction_index: int,
    grid: GridMap,
    parameters: ParameterSet,
) -> Position:
    direction_count = int(parameters.values["grenade.scatter.direction_count"])
    if not 0 <= direction_index < direction_count or direction_count != len(SCATTER_DIRECTIONS):
        raise ValueError("scatter direction index is outside the authored eight-direction fixture")
    distance = grenade_scatter_distance(failure_margin, parameters)
    dx, dy = SCATTER_DIRECTIONS[direction_index]
    current = intended
    for _ in range(distance):
        candidate = Position(current.x + dx, current.y + dy)
        cell = (round(candidate.x), round(candidate.y))
        if not grid.in_bounds(candidate) or grid.occupied(cell):
            break
        current = candidate
    return current


DETECTION_ORDER = (
    DetectionState.UNDETECTED,
    DetectionState.SUSPICIOUS,
    DetectionState.LOCATED,
    DetectionState.CONFIRMED,
)


def advance_detection(current: DetectionState, steps: int) -> DetectionState:
    return DETECTION_ORDER[min(len(DETECTION_ORDER) - 1, DETECTION_ORDER.index(current) + max(0, steps))]


@dataclass(frozen=True, slots=True)
class IntrusionResult:
    action: str
    node_id: str
    eligible: bool
    authorized: bool
    roll: int | None
    total: int | None
    difficulty: int | None
    success: bool
    ap_spent: int
    state_change: str | None
    detection_before: str
    detection_after: str
    stay_quiet_roll: int | None
    retry_available: bool
    reason: str
    visibility_band: str
    stay_quiet_difficulty: int | None


HACKING_VISIBILITY_BANDS = ("LOW", "STANDARD", "HIGH")


def resolve_hacking_visibility(
    base_band: str,
    *shifts: int,
) -> str:
    normalized = base_band.upper()
    if normalized not in HACKING_VISIBILITY_BANDS:
        raise ValueError(f"unknown HACKING VISIBILITY band {base_band}")
    index = HACKING_VISIBILITY_BANDS.index(normalized)
    for shift in shifts:
        index = max(0, min(len(HACKING_VISIBILITY_BANDS) - 1, index + shift))
    return HACKING_VISIBILITY_BANDS[index]


def intrusion_action(
    nodes: dict[str, NetworkNodeState],
    node_id: str,
    action: str,
    *,
    roll: int,
    skill: int,
    aptitude: int,
    parameters: ParameterSet,
    valid_route: bool,
    authorized: bool = False,
    hacker: bool = False,
    local_hacker: bool = False,
    networker: bool = False,
    stay_quiet_roll: int | None = None,
    base_visibility: str = "STANDARD",
    parallel_probe: bool = False,
) -> IntrusionResult:
    normalized = action.upper().replace(" ", "_")
    node = nodes.get(node_id)
    if node is None:
        raise KeyError(node_id)
    detection_before = node.detection_state
    visibility_shifts: list[int] = []
    if local_hacker and node.local_access:
        visibility_shifts.append(
            -int(
                parameters.values[
                    "specialty.local_hacker.quiet_console.visibility_band_shift"
                ]
            )
        )
    if networker and normalized == "DISCOVER":
        visibility_shifts.append(
            -int(
                parameters.values[
                    "specialty.networker.quiet_enumeration.visibility_band_shift"
                ]
            )
        )
    if parallel_probe:
        if not networker or normalized != "DISCOVER":
            raise ValueError("PARALLEL PROBE requires a NETWORKER DISCOVER action")
        visibility_shifts.append(
            int(
                parameters.values[
                    "specialty.networker.parallel_probe.visibility_band_shift"
                ]
            )
        )
    visibility_band = resolve_hacking_visibility(
        base_visibility,
        *visibility_shifts,
    )
    prerequisites = valid_route and (
        normalized not in {"ESCALATE", "OPERATE", "DISCOVER", "FOOTHOLD"} or node.compromised
    )
    if not prerequisites:
        return IntrusionResult(normalized, node_id, False, authorized, None, None, None, False, 0, None, detection_before.value, node.detection_state.value, None, False, "missing route/access prerequisite", visibility_band, None)
    if authorized:
        state_change = f"AUTHORIZED_{normalized}"
        operation_ap = int(parameters.values["hacking.authorized_function_ap"]) if normalized == "OPERATE" else 0
        return IntrusionResult(normalized, node_id, True, True, None, None, None, True, operation_ap, state_change, detection_before.value, node.detection_state.value, None, True, "authorized route/function", visibility_band, None)

    hard = normalized in {"ESCALATE", "FOOTHOLD"} or (node.access_difficulty or 0) >= int(parameters.values["hacking.hard_intrusion_difficulty"])
    difficulty = int(parameters.values["hacking.hard_intrusion_difficulty" if hard else "hacking.standard_intrusion_difficulty"])
    if normalized == "GET_IN" and local_hacker and node.local_access:
        difficulty += int(parameters.values["specialty.local_hacker.service_access.difficulty_offset"])
    base_ap = int(parameters.values["hacking.hard_intrusion_ap" if hard else "hacking.standard_intrusion_ap"])
    ap_spent = max(1, base_ap - (int(parameters.values["class.hacker.intrusion_ap_discount"]) if hacker else 0))
    if normalized == "FOOTHOLD" and networker and not node.local_access:
        ap_spent = max(
            1,
            ap_spent
            - int(parameters.values["specialty.networker.foothold.ap_discount"]),
        )
    check = resolve_check(roll, skill, aptitude, difficulty)
    state_change: str | None = None
    if check.success:
        if normalized in {"GET_IN", "PIVOT"}:
            node.compromised = True
            node.privilege = max(node.privilege, 1)
            state_change = "ACCESS_ESTABLISHED"
            if networker:
                discovered = discover_neighbors(nodes, node_id)
                if discovered:
                    state_change += ":REVEALED=" + ",".join(sorted(discovered))
        elif normalized == "ESCALATE":
            node.privilege = max(node.privilege, node.required_privilege)
            state_change = f"PRIVILEGE={node.privilege}"
        elif normalized == "DISCOVER":
            discovered = discover_neighbors(nodes, node_id)
            state_change = "REVEALED=" + ",".join(sorted(discovered))
        elif normalized == "FOOTHOLD":
            node.foothold = True
            state_change = "FOOTHOLD_ESTABLISHED"
        elif normalized == "OPERATE":
            state_change = "REMOTE_FUNCTION_OPERATED"
        else:
            raise ValueError(f"unsupported intrusion action {action}")

    stay_quiet_difficulty: int | None = None
    if stay_quiet_roll is not None and node.monitoring_difficulty is not None:
        stay_quiet_difficulty = int(parameters.values["hacking.stay_quiet_difficulty"]) + int(
            parameters.values[
                f"hacking.visibility.{visibility_band.lower()}_difficulty_offset"
            ]
        )
        quiet = resolve_check(
            stay_quiet_roll,
            skill,
            aptitude,
            stay_quiet_difficulty,
        )
        if not quiet.success:
            node.detection_state = advance_detection(node.detection_state, int(parameters.values["hacking.detection.failure_steps"]))
    retry_available = bool(parameters.values["hacking.retry.lock_without_state_change"] is False and valid_route)
    return IntrusionResult(
        normalized,
        node_id,
        True,
        False,
        roll,
        check.total,
        difficulty,
        check.success,
        ap_spent,
        state_change,
        detection_before.value,
        node.detection_state.value,
        stay_quiet_roll,
        retry_available,
        "resolved",
        visibility_band,
        stay_quiet_difficulty,
    )


@dataclass(frozen=True, slots=True)
class SocialResult:
    plausible: bool
    leverage: str | None
    total: int | None
    difficulty: int
    success: bool
    setup_ap: int
    reason: str


def resolve_social_attempt(
    *,
    plausible: bool,
    relevant_fact: bool,
    leverage: str | None,
    roll: int,
    skill: int,
    aptitude: int,
    difficulty: int,
    parameters: ParameterSet,
    influencer: bool,
) -> SocialResult:
    if not plausible:
        return SocialResult(False, leverage, None, difficulty, False, 0, "psychologically/physically impossible outcome")
    if leverage and not relevant_fact:
        leverage = None
    modifier = 0
    if leverage:
        modifier = parameters.values[f"social.leverage.{leverage.lower()}_modifier"]
        if influencer:
            ordered = ["weak", "strong", "decisive"]
            index = min(2, ordered.index(leverage.lower()) + parameters.values["class.influencer.leverage_strength_steps"])
            modifier = parameters.values[f"social.leverage.{ordered[index]}_modifier"]
    check = resolve_check(roll, skill, aptitude, difficulty + modifier)
    setup = parameters.values["social.fact_to_leverage_setup_ap"] if relevant_fact else 0
    if influencer and relevant_fact:
        setup = max(1, setup - parameters.values["class.influencer.leverage_setup_ap_discount"])
    return SocialResult(True, leverage, check.total, check.difficulty, check.success, setup, "resolved bounded social CHECK")
