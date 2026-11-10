from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .model import KnowledgeState, Position, Stance


@dataclass(frozen=True, slots=True)
class KnownTarget:
    actor_id: str
    knowledge: KnowledgeState
    position: Position | None
    distance_m: float | None
    exposed: bool
    downed: bool


@dataclass(frozen=True, slots=True)
class PolicyObservation:
    actor_id: str
    position: Position
    stance: Stance
    ap: int
    hp: int
    res: int
    weapon_id: str | None
    ammunition: int
    recoil: int
    fire_modes: tuple[str, ...]
    known_targets: tuple[KnownTarget, ...]
    objective_position: Position | None
    ally_casualty_visible: bool = False
    behind_cover: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    action: str
    target_id: str | None
    mode: str | None
    rounds: int
    destination: Position | None
    reason: str


def _confirmed_targets(observation: PolicyObservation) -> list[KnownTarget]:
    return [
        target
        for target in observation.known_targets
        if target.knowledge is KnowledgeState.CONFIRMED and target.position is not None and not target.downed
    ]


def choose_action(policy_id: str, observation: PolicyObservation) -> PolicyDecision:
    targets = sorted(_confirmed_targets(observation), key=lambda target: (target.distance_m or float("inf"), target.actor_id))
    visible_targets = [target for target in targets if target.exposed]
    target = visible_targets[0] if visible_targets else None
    known_target = targets[0] if targets else None
    metadata = observation.metadata
    if observation.ap <= 0:
        return PolicyDecision("END", None, None, 0, None, "no AP remains")
    if metadata.get("needs_cycle", False):
        return PolicyDecision("CYCLE", None, None, 0, None, "manual mechanism must be cycled before another shot")
    if metadata.get("needs_reload", False) and metadata.get("can_reload", False):
        return PolicyDecision("RELOAD", None, None, 0, None, "feed is empty while carried ammunition remains")
    if (
        policy_id == "POLICY_STRONGPOINT"
        and metadata.get("can_establish_strongpoint", False)
        and not metadata.get("strongpoint_established", False)
    ):
        return PolicyDecision(
            "ESTABLISH_STRONGPOINT",
            None,
            None,
            0,
            observation.position,
            "establish the position-bound strongpoint from suitable cover",
        )
    if policy_id in {"POLICY_SUPPORT", "POLICY_OBJECTIVE"} and observation.ally_casualty_visible and metadata.get("can_treat", False):
        return PolicyDecision("MEDICAL", metadata.get("casualty_id"), None, 0, None, "visible ally casualty is the declared support objective")
    if policy_id in {"POLICY_SUPPORT", "POLICY_OBJECTIVE"} and metadata.get("can_hack", False):
        return PolicyDecision("HACK", None, None, 0, observation.objective_position, "valid route and physical access make network progress the support objective")
    if policy_id == "POLICY_SUPPORT" and metadata.get("can_deploy", False):
        return PolicyDecision("DEPLOY", None, None, 0, observation.objective_position, "deploy the declared physical support system")
    if policy_id == "POLICY_OBJECTIVE" and observation.objective_position is not None and not metadata.get("at_objective", False):
        return PolicyDecision("MOVE_OBJECTIVE", None, None, 0, observation.objective_position, "objective progress outranks personal damage output")
    if target and observation.ammunition > 0:
        if (
            policy_id == "POLICY_STRONGPOINT"
            and not observation.behind_cover
            and metadata.get("cover_destination") is not None
        ):
            return PolicyDecision(
                "MOVE_COVER",
                target.actor_id,
                None,
                0,
                metadata["cover_destination"],
                "reach a suitable real position before establishing the strongpoint",
            )
        if (
            policy_id == "POLICY_PREPARED_FIRE"
            and metadata.get("can_overwatch", False)
            and not metadata.get("overwatch_active", False)
        ):
            return PolicyDecision(
                "OVERWATCH",
                target.actor_id,
                None,
                0,
                target.position,
                "establish the declared prepared sightline before ordinary fire",
            )
        if metadata.get("can_cqc", False) and policy_id in {"POLICY_DIRECT", "POLICY_PRESSURE", "POLICY_OBJECTIVE"}:
            return PolicyDecision("CQC", target.actor_id, None, 0, None, "adjacent target permits one declared close-quarters maneuver")
        if policy_id == "POLICY_COVER_FIRST" and not observation.behind_cover and metadata.get("cover_destination") is not None:
            return PolicyDecision("MOVE_COVER", target.actor_id, None, 0, None, "seek real cover before deliberate fire")
        if policy_id == "POLICY_PRESSURE" and not metadata.get("inside_close_envelope", False):
            return PolicyDecision("MOVE_OBJECTIVE", target.actor_id, None, 0, target.position, "close distance through real geometry before applying pressure")
        if policy_id == "POLICY_PRESSURE" and "FULL AUTO" in observation.fire_modes and observation.ammunition >= 5 and not metadata.get("suppression_active", False):
            return PolicyDecision("SUPPRESS", target.actor_id, "FULL AUTO", 10, target.position, "threaten the known position with active fire")
        if policy_id == "POLICY_VOLUME" and "FULL AUTO" in observation.fire_modes:
            return PolicyDecision("FIRE", target.actor_id, "FULL AUTO", min(10, observation.ammunition), None, "volume policy prefers a supported automatic string")
        if policy_id == "POLICY_FIRE_DISCIPLINE" and observation.recoil > 2:
            return PolicyDecision("RECOVER", target.actor_id, None, 0, None, "recoil makes another immediate string inefficient")
        if policy_id == "POLICY_FIRE_DISCIPLINE" and (target.distance_m or 0) >= 50 and metadata.get("aim_ap", 0) == 0 and observation.ap >= 3:
            return PolicyDecision("AIM", target.actor_id, None, 0, None, "build a deliberate long-range firing solution")
        if policy_id == "POLICY_FLANK" and not metadata.get("flank_complete", False):
            return PolicyDecision("FLANK", target.actor_id, None, 0, None, "change the real cover angle before firing")
        if policy_id == "POLICY_COVER_FIRST" and observation.recoil == 0 and metadata.get("aim_ap", 0) == 0 and observation.ap >= 3:
            return PolicyDecision("AIM", target.actor_id, None, 0, None, "maintain cover and improve the specific firing solution")
        if policy_id == "POLICY_FIRE_DISCIPLINE" and "3-round BURST" in observation.fire_modes and observation.recoil == 0 and observation.ammunition >= 3:
            return PolicyDecision("FIRE", target.actor_id, "BURST", 3, None, "controlled burst has useful volume before recoil accumulates")
        mode = "SINGLE" if "SINGLE" in observation.fire_modes and "SEMI AUTO" not in observation.fire_modes else "SEMI AUTO"
        return PolicyDecision("FIRE", target.actor_id, mode, 1, None, "highest immediate legal value against a visible confirmed target")
    if known_target and policy_id == "POLICY_FLANK":
        return PolicyDecision("FLANK", known_target.actor_id, None, 0, known_target.position, "change angle toward the confirmed location without reading hidden state")
    if known_target and observation.objective_position is None:
        return PolicyDecision("MOVE_OBJECTIVE", known_target.actor_id, None, 0, known_target.position, "move toward the last precise known target position to regain a firing solution")
    if metadata.get("can_overwatch", False) and observation.ammunition > 0:
        return PolicyDecision("OVERWATCH", None, None, 0, observation.objective_position, "prepare a declared shot across the known approach")
    if observation.objective_position is not None:
        return PolicyDecision("MOVE_OBJECTIVE", None, None, 0, observation.objective_position, "advance toward the declared objective using known state only")
    return PolicyDecision("END", None, None, 0, None, "no confirmed target or declared objective")
