from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ScenarioDefinition:
    scenario_id: str
    version: str
    family: str
    map_id: str
    executor: str
    policies: tuple[str, ...]
    levels: tuple[int, ...]
    terminal_conditions: tuple[str, ...]
    fixture_ids: tuple[str, ...] = ()
    support_status: str = "IMPLEMENTED"
    unsupported_dependencies: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


POLICY_IDS = (
    "POLICY_DIRECT",
    "POLICY_COVER_FIRST",
    "POLICY_PRESSURE",
    "POLICY_FIRE_DISCIPLINE",
    "POLICY_VOLUME",
    "POLICY_FLANK",
    "POLICY_OBJECTIVE",
    "POLICY_SUPPORT",
)
LEVELS = (1, 5, 10, 15, 20)


def _scenario(
    scenario_id: str,
    family: str,
    map_id: str,
    executor: str,
    *,
    policies: tuple[str, ...] = (),
    levels: tuple[int, ...] = LEVELS,
    terminal: tuple[str, ...] = ("probe complete",),
    fixtures: tuple[str, ...] | None = None,
    support_status: str = "IMPLEMENTED_WITH_PARAMETERS",
    unsupported: tuple[str, ...] = (),
) -> ScenarioDefinition:
    return ScenarioDefinition(
        scenario_id,
        "1.0.0",
        family,
        map_id,
        executor,
        policies,
        levels,
        terminal,
        fixture_ids=fixtures
        or (
            map_id,
            f"{scenario_id}:SIDE-A",
            f"{scenario_id}:SIDE-B",
            f"{scenario_id}:STATE",
        ),
        support_status=support_status,
        unsupported_dependencies=unsupported,
    )


def scenario_registry() -> dict[str, ScenarioDefinition]:
    definitions = [
        _scenario("CHECK-CURVE-V1", "CHECK-CURVE", "MAP_OPEN_100", "exact_checks"),
        _scenario("FIRE-SINGLE-RANGE-V1", "FIRE-SINGLE-RANGE", "MAP_LONG_1000", "exact_fire"),
        _scenario("FIRE-HANDLING-TIGHT-V1", "FIRE-HANDLING-TIGHT", "MAP_TIGHT_20", "handling_probe"),
        _scenario("FIRE-AUTO-STRING-V1", "FIRE-AUTO-STRING", "MAP_OPEN_100", "auto_string_probe"),
        _scenario("PROTECTION-ARMOR-V1", "PROTECTION-ARMOR", "MAP_OPEN_100", "armor_probe"),
        _scenario("PROJECTILE-MATERIAL-V1", "PROJECTILE-MATERIAL", "MAP_OPEN_100", "material_probe"),
        _scenario("COVER-PARTIAL-V1", "COVER-PARTIAL", "MAP_STREET_60", "cover_probe"),
        _scenario("COVER-DESTRUCTION-V1", "COVER-DESTRUCTION", "MAP_STREET_60", "cover_destruction_probe"),
        _scenario("SUPPRESS-CROSSING-V1", "SUPPRESS-CROSSING", "MAP_STREET_60", "suppression_probe", policies=("POLICY_DIRECT", "POLICY_PRESSURE", "POLICY_COVER_FIRST")),
        _scenario("OW-CROSSING-V1", "OW-CROSSING", "MAP_STREET_60", "overwatch_probe"),
        _scenario("TRIGGER-SIMULTANEOUS-V1", "TRIGGER-SIMULTANEOUS", "MAP_STREET_60", "trigger_probe", support_status="IMPLEMENTED"),
        _scenario("CQC-MANEUVER-V1", "CQC-MANEUVER", "MAP_TIGHT_20", "cqc_probe"),
        _scenario("CQC-CONTACT-FIRE-V1", "CQC-CONTACT-FIRE", "MAP_TIGHT_20", "handling_probe"),
        _scenario("GRENADE-PLACEMENT-V1", "GRENADE-PLACEMENT", "MAP_TIGHT_20", "grenade_placement_probe"),
        _scenario("GRENADE-WARNING-V1", "GRENADE-WARNING", "MAP_TIGHT_20", "grenade_warning_probe"),
        _scenario("BLAST-COVER-V1", "BLAST-COVER", "MAP_STREET_60", "blast_probe"),
        _scenario("SMOKE-LOS-V1", "SMOKE-LOS", "MAP_STREET_60", "smoke_probe"),
        _scenario("MED-STABILIZE-V1", "MED-STABILIZE", "MAP_COMPOUND_80", "medical_probe"),
        _scenario("MED-RESUSCITATE-V1", "MED-RESUSCITATE", "MAP_COMPOUND_80", "medical_probe"),
        _scenario("STEALTH-INFO-V1", "STEALTH-INFO", "MAP_COMPOUND_80", "knowledge_probe"),
        _scenario("DRONE-RECON-V1", "DRONE-RECON", "MAP_COMPOUND_80", "drone_probe", policies=("POLICY_OBJECTIVE", "POLICY_SUPPORT")),
        _scenario("DRONE-ARMED-V1", "DRONE-ARMED", "MAP_STREET_60", "drone_armed_probe", policies=("POLICY_DIRECT", "POLICY_SUPPORT")),
        _scenario("TECH-DEPLOY-V1", "TECH-DEPLOY", "MAP_NETWORK_SITE_40", "tech_probe"),
        _scenario("HACK-LOCAL-V1", "HACK-LOCAL", "MAP_NETWORK_SITE_40", "hacking_probe"),
        _scenario("HACK-NETWORK-V1", "HACK-NETWORK", "MAP_NETWORK_SITE_40", "hacking_probe"),
        _scenario("SOCIAL-NO-LEVERAGE-V1", "SOCIAL-NO-LEVERAGE", "MAP_OPEN_100", "social_probe"),
        _scenario("SOCIAL-LEVERAGE-V1", "SOCIAL-LEVERAGE", "MAP_OPEN_100", "social_probe"),
        _scenario("SOCIAL-IMPOSSIBLE-V1", "SOCIAL-IMPOSSIBLE", "MAP_OPEN_100", "social_probe"),
        _scenario("CLASS-BASELINES-V1", "CLASS-BASELINES", "MAP_COMPOUND_80", "class_probe"),
        _scenario("SPECIALTY-COUNTERFACTUAL-V1", "SPECIALTY-COUNTERFACTUAL", "MAP_COMPOUND_80", "specialty_probe", support_status="IMPLEMENTED"),
        _scenario("TEAM-CONTROL-4V4-V1", "TEAM-CONTROL-4V4", "MAP_COMPOUND_80", "team_objective", policies=POLICY_IDS, terminal=("hold objective 12 seconds", "max rounds", "side eliminated")),
        _scenario("TEAM-EXTRACT-4V4-V1", "TEAM-EXTRACT-4V4", "MAP_COMPOUND_80", "team_objective", policies=POLICY_IDS, terminal=("casualty extracted", "max rounds", "side eliminated")),
        _scenario("TEAM-BREACH-4V4-V1", "TEAM-BREACH-4V4", "MAP_BREACH_30", "team_objective", policies=POLICY_IDS, terminal=("hold defender objective 8 seconds", "max rounds", "side eliminated")),
        _scenario("TEAM-RECON-HACK-4V4-V1", "TEAM-RECON-HACK-4V4", "MAP_NETWORK_SITE_40", "team_objective", policies=POLICY_IDS, terminal=("knowledge and network objective complete", "max rounds", "side eliminated")),
        _scenario("MIRROR-DUEL-V1", "MIRROR-NEUTRALITY", "MAP_OPEN_100", "duel_monte_carlo", policies=("POLICY_FIRE_DISCIPLINE", "POLICY_DIRECT"), terminal=("one side DOWNED", "max rounds")),
    ]
    return {definition.scenario_id: definition for definition in definitions}


def validate_scenarios() -> list[str]:
    errors: list[str] = []
    registry = scenario_registry()
    if len(registry) != len(set(registry)):
        errors.append("duplicate scenario IDs")
    allowed_maps = {"MAP_OPEN_100", "MAP_TIGHT_20", "MAP_STREET_60", "MAP_BREACH_30", "MAP_COMPOUND_80", "MAP_LONG_1000", "MAP_NETWORK_SITE_40"}
    allowed_statuses = {"IMPLEMENTED", "IMPLEMENTED_WITH_PARAMETERS", "UNRESOLVED_SOURCE", "AMBIGUOUS_SOURCE", "MISSING_DEPENDENCY", "STRUCTURAL_FINDING", "DEFERRED_OUT_OF_SCOPE"}
    for scenario in registry.values():
        if scenario.map_id not in allowed_maps:
            errors.append(f"{scenario.scenario_id}: unknown map {scenario.map_id}")
        unknown_policies = set(scenario.policies) - set(POLICY_IDS)
        if unknown_policies:
            errors.append(f"{scenario.scenario_id}: unknown policies {sorted(unknown_policies)}")
        if not scenario.version:
            errors.append(f"{scenario.scenario_id}: missing version")
        if scenario.support_status not in allowed_statuses:
            errors.append(f"{scenario.scenario_id}: invalid support status {scenario.support_status}")
        if scenario.support_status in {"IMPLEMENTED", "IMPLEMENTED_WITH_PARAMETERS"} and scenario.unsupported_dependencies:
            errors.append(f"{scenario.scenario_id}: implemented scenario declares unsupported dependencies")
        if scenario.support_status == "MISSING_DEPENDENCY" and not scenario.unsupported_dependencies:
            errors.append(f"{scenario.scenario_id}: missing-dependency scenario has no dependency evidence")
    return errors
