from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .fixtures import CLASS_SPECIALTIES, LEVELS, map_contracts
from .model import CoverageState
from .parameters import ParameterSet
from .provenance import ENCODED_SOURCE_COMMIT, repo_root
from .rng import derive_seed
from .specialty_contracts import SPECIALTY_RULE_CONTRACTS
from .specialty_manifest import (
    DEFERRED_SPECIALTY_PARAMETER_IDS,
    REJECTED_SPECIALTY_PARAMETER_IDS,
    SPECIALTY_PARAMETERS_BY_NODE,
    parameter_id as specialty_parameter_id,
)
from .specialty_audit import (
    AUTHORED_ALTERNATIVE_NODES,
    DEFERRED_NON_CUMULATIVE_PARAMETER_ROWS,
    REJECTED_SPECIALTY_PARAMETER_ROWS,
    SPECIALTY_MECHANICAL_GAPS,
)
from .tactical import TeamScenarioSpec, default_team_spec, run_team_scenario


@dataclass(frozen=True, slots=True)
class SpecialtyNode:
    node_id: str
    specialty: str
    class_id: str
    tier: str
    name: str
    source_path: str
    source_sha256: str
    source_commit: str
    source_excerpt: str
    canonical_span: str


@dataclass(frozen=True, slots=True)
class NodeCoverage:
    node: SpecialtyNode
    state: CoverageState
    mechanic_id: str | None
    parameter_ids: tuple[str, ...]
    unsupported_dependencies: tuple[str, ...]
    findings: tuple[dict[str, Any], ...]
    counterfactual_id: str | None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["node"].pop("canonical_span", None)
        result["state"] = self.state.value
        return result


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _class_for(specialty: str) -> str:
    for class_id, specialties in CLASS_SPECIALTIES.items():
        if specialty in specialties:
            return class_id
    raise KeyError(specialty)


_SPECIALTY_SCENARIO_CONTEXTS: dict[str, dict[str, tuple[str, str, str]]] = {
    "BULWARK": {
        "PRIMARY": ("MAP_COMPOUND_80", "hold_contested_objective", "service_rifle"),
        "SECONDARY": ("MAP_STREET_60", "screen_allied_advance", "service_rifle"),
        "OFF_ROLE": ("MAP_NETWORK_SITE_40", "intrude_remote_network", "pistol"),
    },
    "RIFLEMAN": {
        "PRIMARY": ("MAP_STREET_60", "sustain_fire_across_cover", "service_rifle"),
        "SECONDARY": ("MAP_COMPOUND_80", "support_objective_control", "service_rifle"),
        "OFF_ROLE": ("MAP_NETWORK_SITE_40", "intrude_remote_network", "pistol"),
    },
    "BREACHER": {
        "PRIMARY": ("MAP_BREACH_30", "enter_and_clear_breach", "shotgun"),
        "SECONDARY": ("MAP_TIGHT_20", "support_close_control", "service_rifle"),
        "OFF_ROLE": ("MAP_LONG_1000", "solve_long_range_target", "precision_rifle"),
    },
    "GHOST": {
        "PRIMARY": ("MAP_COMPOUND_80", "reposition_without_confirmed_exposure", "smg"),
        "SECONDARY": ("MAP_STREET_60", "create_flanking_angle", "service_rifle"),
        "OFF_ROLE": ("MAP_COMPOUND_80", "stabilize_reference_casualty", "pistol"),
    },
    "SHARPSHOOTER": {
        "PRIMARY": ("MAP_LONG_1000", "develop_long_range_solution", "precision_rifle"),
        "SECONDARY": ("MAP_STREET_60", "support_controlled_fire", "service_rifle"),
        "OFF_ROLE": ("MAP_NETWORK_SITE_40", "operate_physical_relay", "pistol"),
    },
    "TECH": {
        "PRIMARY": ("MAP_NETWORK_SITE_40", "deploy_and_operate_device", "service_rifle"),
        "SECONDARY": ("MAP_STREET_60", "support_remote_recon", "service_rifle"),
        "OFF_ROLE": ("MAP_TIGHT_20", "resolve_close_grapple", "pistol"),
    },
    "HACKER": {
        "PRIMARY": ("MAP_NETWORK_SITE_40", "intrude_remote_network", "service_rifle"),
        "SECONDARY": ("MAP_NETWORK_SITE_40", "support_mixed_physical_network_objective", "service_rifle"),
        "OFF_ROLE": ("MAP_STREET_60", "sustain_direct_fire", "service_rifle"),
    },
    "CORPSMAN": {
        "PRIMARY": ("MAP_COMPOUND_80", "stabilize_reference_casualty", "service_rifle"),
        "SECONDARY": ("MAP_STREET_60", "support_casualty_extraction", "service_rifle"),
        "OFF_ROLE": ("MAP_NETWORK_SITE_40", "intrude_remote_network", "pistol"),
    },
    "INFLUENCER": {
        "PRIMARY": ("MAP_OPEN_100", "convert_social_fact_to_leverage", "pistol"),
        "SECONDARY": ("MAP_COMPOUND_80", "support_team_information_objective", "pistol"),
        "OFF_ROLE": ("MAP_BREACH_30", "destroy_physical_barrier", "shotgun"),
    },
}

_CLASS_MECHANIC_DOMAINS = {
    "BULWARK": "POSITIONAL_DEFENSE",
    "RIFLEMAN": "FIREARM_SUPPORT",
    "BREACHER": "BREACH_AND_CQC",
    "GHOST": "STEALTH_AND_MOBILITY",
    "SHARPSHOOTER": "PRECISION_FIRE",
    "TECH": "TECHNICAL_DEVICE",
    "HACKER": "NETWORK_INTRUSION",
    "CORPSMAN": "CASUALTY_CARE",
    "INFLUENCER": "SOCIAL_INFLUENCE",
}

_OBJECTIVE_MECHANIC_DOMAINS = {
    "hold_contested_objective": {"POSITIONAL_DEFENSE"},
    "screen_allied_advance": {"POSITIONAL_DEFENSE"},
    "sustain_fire_across_cover": {"FIREARM_SUPPORT"},
    "support_objective_control": {"FIREARM_SUPPORT"},
    "enter_and_clear_breach": {"BREACH_AND_CQC"},
    "support_close_control": {"BREACH_AND_CQC"},
    "reposition_without_confirmed_exposure": {"STEALTH_AND_MOBILITY"},
    "create_flanking_angle": {"STEALTH_AND_MOBILITY"},
    "develop_long_range_solution": {"PRECISION_FIRE"},
    "support_controlled_fire": {"PRECISION_FIRE"},
    "deploy_and_operate_device": {"TECHNICAL_DEVICE"},
    "support_remote_recon": {"TECHNICAL_DEVICE"},
    "intrude_remote_network": {"NETWORK_INTRUSION"},
    "support_mixed_physical_network_objective": {"NETWORK_INTRUSION"},
    "stabilize_reference_casualty": {"CASUALTY_CARE"},
    "support_casualty_extraction": {"CASUALTY_CARE"},
    "convert_social_fact_to_leverage": {"SOCIAL_INFLUENCE"},
    "support_team_information_objective": {"SOCIAL_INFLUENCE"},
    "solve_long_range_target": {"PRECISION_FIRE"},
    "resolve_close_grapple": {"BREACH_AND_CQC"},
    "sustain_direct_fire": {"FIREARM_SUPPORT"},
    "operate_physical_relay": {"TECHNICAL_DEVICE"},
    "destroy_physical_barrier": {"BREACH_AND_CQC"},
}

_SPECIALTY_ACTUAL_OBJECTIVES: dict[str, dict[str, str]] = {
    "BULWARK": {"PRIMARY": "TEAM-CONTROL-4V4-V1", "SECONDARY": "TEAM-CONTROL-4V4-V1", "OFF_ROLE": "TEAM-RECON-HACK-4V4-V1"},
    "RIFLEMAN": {"PRIMARY": "TEAM-CONTROL-4V4-V1", "SECONDARY": "TEAM-CONTROL-4V4-V1", "OFF_ROLE": "TEAM-RECON-HACK-4V4-V1"},
    "BREACHER": {"PRIMARY": "TEAM-BREACH-4V4-V1", "SECONDARY": "TEAM-BREACH-4V4-V1", "OFF_ROLE": "TEAM-RECON-HACK-4V4-V1"},
    "GHOST": {"PRIMARY": "TEAM-CONTROL-4V4-V1", "SECONDARY": "TEAM-CONTROL-4V4-V1", "OFF_ROLE": "TEAM-EXTRACT-4V4-V1"},
    "SHARPSHOOTER": {"PRIMARY": "TEAM-CONTROL-4V4-V1", "SECONDARY": "TEAM-CONTROL-4V4-V1", "OFF_ROLE": "TEAM-RECON-HACK-4V4-V1"},
    "TECH": {"PRIMARY": "TEAM-RECON-HACK-4V4-V1", "SECONDARY": "TEAM-RECON-HACK-4V4-V1", "OFF_ROLE": "TEAM-BREACH-4V4-V1"},
    "HACKER": {"PRIMARY": "TEAM-RECON-HACK-4V4-V1", "SECONDARY": "TEAM-RECON-HACK-4V4-V1", "OFF_ROLE": "TEAM-BREACH-4V4-V1"},
    "CORPSMAN": {"PRIMARY": "TEAM-EXTRACT-4V4-V1", "SECONDARY": "TEAM-EXTRACT-4V4-V1", "OFF_ROLE": "TEAM-RECON-HACK-4V4-V1"},
    "INFLUENCER": {"PRIMARY": "TEAM-SOCIAL-4V4-V1", "SECONDARY": "TEAM-SOCIAL-4V4-V1", "OFF_ROLE": "TEAM-BREACH-4V4-V1"},
}

_OBJECTIVE_RUN_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}

_SPECIALTY_OBJECTIVE_EQUIPMENT = {
    "GUNNER": "lmg",
    "OPERATOR": "manual_precision_rifle",
    "SENTINEL": "precision_rifle",
    "SNAPSHOT": "precision_rifle",
    "SNIPER": "manual_precision_rifle",
    "SHOCK": "service_rifle",
}

_SPECIALTY_OBJECTIVE_POLICY = {
    "ANCHOR": "POLICY_STRONGPOINT",
    "ARSENAL": "POLICY_FIRE_DISCIPLINE",
    "FLANKER": "POLICY_FLANK",
    "GUNNER": "POLICY_VOLUME",
    "OPERATOR": "POLICY_FIRE_DISCIPLINE",
    "SENTINEL": "POLICY_PREPARED_FIRE",
    "SNAPSHOT": "POLICY_FIRE_DISCIPLINE",
    "SNIPER": "POLICY_FIRE_DISCIPLINE",
    "SHOCK": "POLICY_ASSAULT_MOMENTUM",
}


# Runtime-integration diagnostics identify missing test-harness surfaces without
# restating or modifying the canonical specialty rules.  The blocking Entry node
# remains the smallest executable prerequisite for every designated build.
_SPECIALTY_RUNTIME_GAP_AUDIT: dict[str, dict[str, Any]] = {}


def _specialty_scenario_roles(
    coverage: NodeCoverage,
    mechanic_id: str,
) -> dict[str, dict[str, Any]]:
    contexts = _SPECIALTY_SCENARIO_CONTEXTS[coverage.node.class_id]
    specialty_domain = _CLASS_MECHANIC_DOMAINS[coverage.node.class_id]
    roles: dict[str, dict[str, Any]] = {}
    for role, (map_id, objective_id, equipment_id) in contexts.items():
        active_domains = sorted(_OBJECTIVE_MECHANIC_DOMAINS[objective_id])
        scope_match = specialty_domain in active_domains
        roles[role] = {
            "scenario_id": (
                f"SPECIALTY-{_slug(coverage.node.specialty)}-"
                f"{_slug(coverage.node.name)}-{role}"
            ),
            "map_id": map_id,
            "objective_id": objective_id,
            "equipment_id": equipment_id,
            "active_mechanic_domains": active_domains,
            "specialty_mechanic_domain": specialty_domain,
            "specialty_mechanic_id": mechanic_id,
            "mechanic_scope_matches": scope_match,
            "prerequisite_mode": "SATISFY" if scope_match else "WITHHOLD",
            "prerequisites_satisfied": scope_match,
            "node_effect_applied": scope_match,
            "measured_fields": [],
        }
    return roles


def _clean_line(line: str) -> str:
    line = line.strip()
    if line.startswith(">"):
        line = line[1:].strip()
    return line


HEADING_PATTERN = re.compile(r"^#{2,3}\s+(ENTRY|Entry|TIER|Tier)\s*([1-5])?\s*(?:KEYSTONE)?\s*[—-]\s*(.+?)\s*$")
BOLD_HEADING_PATTERN = re.compile(r"^\*\*(ENTRY|TIER)\s*([1-5])?\s*(?:KEYSTONE)?\s*[—-]\s*(.+?)\*\*.*$", re.IGNORECASE)
BULLET_NODE_PATTERN = re.compile(r"^-\s+\*\*(.+?)(?::\*\*|\*\*:|\*\*)\s*(.*)$")


def parse_specialty(path: Path) -> list[SpecialtyNode]:
    text = path.read_text(encoding="utf-8-sig")
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    specialty_match = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
    if not specialty_match:
        raise ValueError(f"missing specialty title in {path}")
    specialty = specialty_match.group(1).strip()
    class_id = _class_for(specialty)
    relative = f"Specialties/{path.name}"
    nodes: list[SpecialtyNode] = []
    current_tier: str | None = None
    pending_entry_or_keystone: tuple[str, str, int] | None = None
    lines = text.splitlines()

    def add_node(tier: str, name: str, excerpt: str, canonical_span: str) -> None:
        name = name.strip().strip("—-").strip().rstrip(":").strip()
        node_id = f"specialty.{_slug(specialty)}.{_slug(tier)}.{_slug(name)}"
        if any(node.node_id == node_id for node in nodes):
            return
        nodes.append(
            SpecialtyNode(
                node_id,
                specialty,
                class_id,
                tier,
                name,
                relative,
                source_hash,
                ENCODED_SOURCE_COMMIT,
                excerpt.strip(),
                canonical_span,
            )
        )

    for index, raw in enumerate(lines):
        line = _clean_line(raw)
        heading = HEADING_PATTERN.match(line)
        bold_heading = BOLD_HEADING_PATTERN.match(line)
        match = heading or bold_heading
        if match:
            kind = match.group(1).upper()
            number = match.group(2)
            name = re.sub(
                r"\s*—\s*UNRESOLVED.*$",
                " — UNRESOLVED",
                match.group(3).strip(),
                flags=re.IGNORECASE,
            ).rstrip(":").strip()
            if kind == "ENTRY":
                current_tier = "ENTRY"
                if "UNRESOLVED" in name.upper():
                    add_node("ENTRY", name, line, raw)
                    pending_entry_or_keystone = None
                elif bold_heading:
                    inline_rule = line.split("**", 2)[-1].lstrip(":").strip()
                    if inline_rule:
                        add_node("ENTRY", name, f"{name}: {inline_rule}", raw)
                        pending_entry_or_keystone = None
                    else:
                        pending_entry_or_keystone = ("ENTRY", name, index)
                else:
                    pending_entry_or_keystone = ("ENTRY", name, index)
            else:
                current_tier = f"TIER {number}"
                if number == "5":
                    if bold_heading:
                        inline_rule = line.split("**", 2)[-1].lstrip(":").strip()
                        if inline_rule:
                            add_node("TIER 5", name, f"{name}: {inline_rule}", raw)
                            pending_entry_or_keystone = None
                        else:
                            pending_entry_or_keystone = ("TIER 5", name, index)
                    else:
                        pending_entry_or_keystone = ("TIER 5", name, index)
                else:
                    pending_entry_or_keystone = None
            continue
        bullet = BULLET_NODE_PATTERN.match(line)
        if bullet and current_tier in {"TIER 1", "TIER 2", "TIER 3", "TIER 4"}:
            add_node(
                current_tier,
                bullet.group(1),
                f"{bullet.group(1)}: {bullet.group(2)}",
                raw,
            )
        if pending_entry_or_keystone:
            tier, name, heading_index = pending_entry_or_keystone
            if index > heading_index and line and not line.startswith("#"):
                excerpt = f"{name}: {line.lstrip('- ').strip()}"
                add_node(
                    tier,
                    name,
                    excerpt,
                    "\n".join(lines[heading_index : index + 1]),
                )
                pending_entry_or_keystone = None
    if pending_entry_or_keystone:
        tier, name, heading_index = pending_entry_or_keystone
        add_node(tier, name, name, lines[heading_index])
    return nodes


def parse_all_specialties(root: Path | None = None) -> list[SpecialtyNode]:
    root = repo_root(root)
    nodes: list[SpecialtyNode] = []
    for path in sorted((root / "Specialties").glob("*.md")):
        nodes.extend(parse_specialty(path))
    return nodes


def _rule_text(node: SpecialtyNode) -> str:
    prefix = f"{node.name}:"
    if node.source_excerpt.startswith(prefix):
        return node.source_excerpt[len(prefix) :].strip()
    return node.source_excerpt.strip()


def _compile_authored_rule_contract(node: SpecialtyNode) -> dict[str, Any]:
    """Load one explicit, source-fragment-validated execution contract."""

    key = (node.specialty, node.name)
    if key not in SPECIALTY_RULE_CONTRACTS:
        raise ValueError(f"missing explicit specialty rule contract for {node.node_id}")
    source_contract = SPECIALTY_RULE_CONTRACTS[key]
    if not (
        source_contract["prerequisites"]
        or source_contract["constraints"]
        or source_contract["effects"]
    ):
        raise ValueError(f"empty explicit specialty rule contract for {node.node_id}")

    def compile_records(kind: str) -> list[dict[str, Any]]:
        compiled: list[dict[str, Any]] = []
        seen_fields: set[str] = set()
        for source in source_contract[kind]:
            field = str(source["field"])
            fragment = str(source["source_fragment"])
            if fragment not in node.canonical_span:
                raise ValueError(
                    f"{node.node_id} {kind} fragment drifted from canonical node span: {fragment}"
                )
            if field in seen_fields:
                raise ValueError(f"duplicate {kind} field {field} for {node.node_id}")
            seen_fields.add(field)
            compiled.append(
                {
                    "field": field,
                    "expected": deepcopy(source["expected"]),
                    "kind": "PREREQUISITE" if kind == "prerequisites" else "CONSTRAINT",
                    "source_clause": fragment,
                    **(
                        {"enforcement": str(source["enforcement"])}
                        if kind == "constraints"
                        else {}
                    ),
                }
            )
        return compiled

    def compile_effects() -> list[dict[str, Any]]:
        compiled: list[dict[str, Any]] = []
        seen_fields: set[str] = set()
        for source in source_contract["effects"]:
            field = str(source["field"])
            fragment = str(source["source_fragment"])
            if fragment not in node.canonical_span:
                raise ValueError(
                    f"{node.node_id} effect fragment drifted from canonical node span: {fragment}"
                )
            if field in seen_fields:
                raise ValueError(f"duplicate effect field {field} for {node.node_id}")
            seen_fields.add(field)
            compiled.append(
                {
                    "field": field,
                    "control": deepcopy(source["control"]),
                    "value": deepcopy(source["value"]),
                    "kind": "EFFECT",
                    "source_clause": fragment,
                }
            )
        return compiled

    rule_text = _rule_text(node)
    effect_clause = rule_text.split(". ", 1)[0].strip()
    return {
        "source_path": node.source_path,
        "source_sha256": node.source_sha256,
        "source_commit": node.source_commit,
        "source_excerpt": node.source_excerpt,
        "source_span_sha256": hashlib.sha256(
            node.canonical_span.encode("utf-8")
        ).hexdigest(),
        "effect_clause": effect_clause,
        "accounted_clauses": (
            [{"kind": "RULE", "source_clause": rule_text}] if rule_text else []
        ),
        "activation_clauses": compile_records("prerequisites"),
        "constraint_clauses": compile_records("constraints"),
        "effect_clauses": compile_effects(),
    }


SPECIALTY_MECHANICS: dict[tuple[str, str], dict[str, Any]] = {('ANCHOR', 'ESTABLISH STRONGPOINT'): {'mechanic_id': 'strongpoint_state',
                                       'parameter_ids': (),
                                       'initial_state': {'strongpoint': False, 'creates_cover': False},
                                       'prerequisites': {},
                                       'operations': ({'operation': 'set', 'field': 'strongpoint', 'value': True},),
                                       'limitations': ({'assertion': 'field_equals',
                                                        'field': 'creates_cover',
                                                        'value': False},)},
 ('ANCHOR', 'CONTROLLED APPROACH'): {'mechanic_id': 'strongpoint_trigger',
                                     'parameter_ids': (),
                                     'initial_state': {'declared_approach_trigger': False},
                                     'prerequisites': {},
                                     'operations': ({'operation': 'set',
                                                     'field': 'declared_approach_trigger',
                                                     'value': True},),
                                     'limitations': ()},
 ('ANCHOR', 'SETTLE IN'): {'mechanic_id': 'strongpoint_stance_fold',
                           'parameter_ids': ('move.stance_change_ap',),
                           'initial_state': {'stance_folded_into_setup': False, 'stance_change_ap': 0},
                           'prerequisites': {'valid_terrain': True},
                           'operations': ({'operation': 'set', 'field': 'stance_folded_into_setup', 'value': True},
                                          {'operation': 'set_parameter',
                                           'field': 'stance_change_ap',
                                           'parameter_id': 'move.stance_change_ap'}),
                           'limitations': ()},
 ('ANCHOR', 'FIRING EDGE'): {'mechanic_id': 'strongpoint_aim_fold',
                             'parameter_ids': ('specialty.anchor.firing_edge.aim_steps',),
                             'initial_state': {'aim_folded_into_setup': 0, 'aim_steps': 0},
                             'prerequisites': {'declared_solution': True},
                             'operations': ({'operation': 'set', 'field': 'aim_folded_into_setup', 'value': 1},
                                            {'operation': 'set_parameter',
                                             'field': 'aim_steps',
                                             'parameter_id': 'specialty.anchor.firing_edge.aim_steps'}),
                             'limitations': ()},
 ('ANCHOR', 'RALLY POINT'): {'mechanic_id': 'strongpoint_ally_stance_fold',
                             'parameter_ids': ('move.stance_change_ap',),
                             'initial_state': {'ally_stance_folded': False, 'stance_change_ap': 0},
                             'prerequisites': {'physical_reach': True},
                             'operations': ({'operation': 'set', 'field': 'ally_stance_folded', 'value': True},
                                            {'operation': 'set_parameter',
                                             'field': 'stance_change_ap',
                                             'parameter_id': 'move.stance_change_ap'}),
                             'limitations': ()},
 ('ANCHOR', 'HOLD UNDER FIRE'): {'mechanic_id': 'strongpoint_dormant_prep',
                                 'parameter_ids': ('specialty.anchor.hold_under_fire.dormant_team_turns',),
                                 'initial_state': {'prep_can_remain_dormant': False,
                                                   'los_required_to_trigger': True,
                                                   'dormant_team_turns': 0},
                                 'prerequisites': {},
                                 'operations': ({'operation': 'set', 'field': 'prep_can_remain_dormant', 'value': True},
                                                {'operation': 'set_parameter',
                                                 'field': 'dormant_team_turns',
                                                 'parameter_id': 'specialty.anchor.hold_under_fire.dormant_team_turns'}),
                                 'limitations': ({'assertion': 'field_equals',
                                                  'field': 'los_required_to_trigger',
                                                  'value': True},)},
 ('ANCHOR', 'FALLBACK POSITION'): {'mechanic_id': 'strongpoint_fallback',
                                   'parameter_ids': (),
                                   'initial_state': {'transfer_requires_full_setup': True},
                                   'prerequisites': {'physical_reach': True},
                                   'operations': ({'operation': 'set',
                                                   'field': 'transfer_requires_full_setup',
                                                   'value': False},),
                                   'limitations': ()},
 ('ANCHOR', 'ROTATE POSITIONS'): {'mechanic_id': 'strongpoint_internal_movement',
                                  'parameter_ids': (),
                                  'initial_state': {'rotation_preserves_setup': False,
                                                    'each_actor_spends_movement_ap': False},
                                  'prerequisites': {},
                                  'operations': ({'operation': 'set',
                                                  'field': 'rotation_preserves_setup',
                                                  'value': True},
                                                 {'operation': 'set',
                                                  'field': 'each_actor_spends_movement_ap',
                                                  'value': True}),
                                  'limitations': ()},
 ('ANCHOR', 'PREPARED GARRISON'): {'mechanic_id': 'strongpoint_prep_transfer',
                                   'parameter_ids': ('specialty.anchor.prepared_garrison.ap_discount',),
                                   'initial_state': {'prep_transfer_discount': 0, 'ap_discount': 0},
                                   'prerequisites': {'same_zone': True},
                                   'operations': ({'operation': 'set', 'field': 'prep_transfer_discount', 'value': 1},
                                                  {'operation': 'set_parameter',
                                                   'field': 'ap_discount',
                                                   'parameter_id': 'specialty.anchor.prepared_garrison.ap_discount'}),
                                   'limitations': ()},
 ('ANCHOR', 'COUNTER-BREACH'): {'mechanic_id': 'strongpoint_contact_prep',
                                'parameter_ids': (),
                                'initial_state': {'entry_trigger_response': False},
                                'prerequisites': {'physical_entry': True},
                                'operations': ({'operation': 'set', 'field': 'entry_trigger_response', 'value': True},),
                                'limitations': ()},
 ('ANCHOR', 'SECOND LINE'): {'mechanic_id': 'strongpoint_dual_position',
                             'parameter_ids': (),
                             'initial_state': {'independent_fallback_survives': False},
                             'prerequisites': {},
                             'operations': ({'operation': 'set',
                                             'field': 'independent_fallback_survives',
                                             'value': True},),
                             'limitations': ()},
 ('ANCHOR', 'REFUSE COLLAPSE'): {'mechanic_id': 'strongpoint_persistence',
                                 'parameter_ids': (),
                                 'initial_state': {'one_cover_loss_ends_state': True},
                                 'prerequisites': {'viable_position': True},
                                 'operations': ({'operation': 'set',
                                                 'field': 'one_cover_loss_ends_state',
                                                 'value': False},),
                                 'limitations': ()},
 ('ANCHOR', 'FORTRESS'): {'mechanic_id': 'strongpoint_composite',
                          'parameter_ids': (),
                          'initial_state': {'developed_position_components': 1},
                          'prerequisites': {'real_geometry': True},
                          'operations': ({'operation': 'set', 'field': 'developed_position_components', 'value': 4},),
                          'limitations': ()},
 ('ARSENAL', 'FIGHTING LOAD'): {'mechanic_id': 'inventory_access',
                                'parameter_ids': (),
                                'initial_state': {'additional_accessible_firearms': 0, 'burden_ignored': False},
                                'prerequisites': {},
                                'operations': ({'operation': 'set',
                                                'field': 'additional_accessible_firearms',
                                                'value': 1},),
                                'limitations': ({'assertion': 'field_equals',
                                                 'field': 'burden_ignored',
                                                 'value': False},)},
 ('ARSENAL', 'STAGED CACHE'): {'mechanic_id': 'inventory_cache',
                               'parameter_ids': (),
                               'initial_state': {'staged_cache': False, 'objects_physical': True},
                               'prerequisites': {},
                               'operations': ({'operation': 'set', 'field': 'staged_cache', 'value': True},),
                               'limitations': ({'assertion': 'field_equals',
                                                'field': 'objects_physical',
                                                'value': True},)},
 ('ARSENAL', 'FAMILIAR CONTROLS'): {'mechanic_id': 'firearm_control_information',
                                    'parameter_ids': (),
                                    'initial_state': {'inspection_reveals_controls': False,
                                                      'exotic_technology_excluded': True},
                                    'prerequisites': {},
                                    'operations': ({'operation': 'set',
                                                    'field': 'inspection_reveals_controls',
                                                    'value': True},),
                                    'limitations': ({'assertion': 'field_equals',
                                                     'field': 'exotic_technology_excluded',
                                                     'value': True},)},
 ('AUTODRONES', 'TACTICAL AUTONOMY'): {'mechanic_id': 'standing_order_complexity',
                                       'parameter_ids': (),
                                       'initial_state': {'conditional_responses': 0, 'sensor_bound': True},
                                       'prerequisites': {},
                                       'operations': ({'operation': 'set',
                                                       'field': 'conditional_responses',
                                                       'value': 1},),
                                       'limitations': ({'assertion': 'field_equals',
                                                        'field': 'sensor_bound',
                                                        'value': True},)},
 ('AUTODRONES', 'CONDITIONAL ORDER'): {'mechanic_id': 'standing_order_condition',
                                       'parameter_ids': (),
                                       'initial_state': {'additional_conditions': 0},
                                       'prerequisites': {},
                                       'operations': ({'operation': 'set',
                                                       'field': 'additional_conditions',
                                                       'value': 1},),
                                       'limitations': ()},
 ('AUTODRONES', 'HOLD POSITION'): {'mechanic_id': 'autonomous_prep',
                                   'parameter_ids': (),
                                   'initial_state': {'prepared_response': False},
                                   'prerequisites': {'standing_order_trigger': True},
                                   'operations': ({'operation': 'set', 'field': 'prepared_response', 'value': True},),
                                   'limitations': ()},
 ('AUTODRONES', 'SECTOR NETWORK'): {'mechanic_id': 'drone_team_knowledge',
                                    'parameter_ids': (),
                                    'initial_state': {'automatic_detection_relay': False},
                                    'prerequisites': {'signal': True},
                                    'operations': ({'operation': 'set',
                                                    'field': 'automatic_detection_relay',
                                                    'value': True},),
                                    'limitations': ()},
 ('AUTODRONES', 'AUTONOMOUS RECOVERY'): {'mechanic_id': 'standing_order_fallback',
                                         'parameter_ids': (),
                                         'initial_state': {'fallback_order': False},
                                         'prerequisites': {},
                                         'operations': ({'operation': 'set',
                                                         'field': 'fallback_order',
                                                         'value': True},),
                                         'limitations': ()},
 ('AUTODRONES', 'ESCORT ROUTINE'): {'mechanic_id': 'standing_order_relative_role',
                                    'parameter_ids': (),
                                    'initial_state': {'maintains_relative_role': False, 'route_and_speed_bound': True},
                                    'prerequisites': {},
                                    'operations': ({'operation': 'set',
                                                    'field': 'maintains_relative_role',
                                                    'value': True},),
                                    'limitations': ({'assertion': 'field_equals',
                                                     'field': 'route_and_speed_bound',
                                                     'value': True},)},
 ('BREAKER', 'READ THE BARRIER'): {'mechanic_id': 'barrier_information',
                                   'parameter_ids': (),
                                   'initial_state': {'vulnerable_point_known': False,
                                                     'hidden_construction_known': False},
                                   'prerequisites': {},
                                   'operations': ({'operation': 'set',
                                                   'field': 'vulnerable_point_known',
                                                   'value': True},),
                                   'limitations': ({'assertion': 'field_equals',
                                                    'field': 'hidden_construction_known',
                                                    'value': False},)},
 ('BREAKER', 'CONTROLLED BREACH'): {'mechanic_id': 'breach_choice',
                                    'parameter_ids': (),
                                    'initial_state': {'shape_choice': None, 'both': False},
                                    'prerequisites': {},
                                    'operations': ({'operation': 'set',
                                                    'field': 'shape_choice',
                                                    'value': 'opening_or_collateral'},),
                                    'limitations': ({'assertion': 'field_equals', 'field': 'both', 'value': False},)},
 ('BREAKER', 'FIRING PORT'): {'mechanic_id': 'breach_geometry',
                              'parameter_ids': (),
                              'initial_state': {'firing_aperture_available': False, 'remaining_geometry_applies': True},
                              'prerequisites': {},
                              'operations': ({'operation': 'set',
                                              'field': 'firing_aperture_available',
                                              'value': True},),
                              'limitations': ({'assertion': 'field_equals',
                                               'field': 'remaining_geometry_applies',
                                               'value': True},)},
 ('BREAKER', 'BREACH READY'): {'mechanic_id': 'breach_tool_access_state',
                               'parameter_ids': (),
                               'initial_state': {'breach_tool_inventory_state': 'STOWED', 'burden_unchanged': True},
                               'prerequisites': {},
                               'operations': ({'operation': 'set',
                                               'field': 'breach_tool_inventory_state',
                                               'value': 'QUICK ACCESS'},),
                               'limitations': ({'assertion': 'field_equals',
                                                'field': 'burden_unchanged',
                                                'value': True},)},
 ('BREAKER', 'CLEAR PASSAGE'): {'mechanic_id': 'breach_minor_debris',
                                'parameter_ids': ('breach.minor_debris.clear_ap',),
                                'initial_state': {'minor_debris_cleared_with_breach': False,
                                                  'major_collapse_excluded': True,
                                                  'clear_ap': 0},
                                'prerequisites': {},
                                'operations': ({'operation': 'set',
                                                'field': 'minor_debris_cleared_with_breach',
                                                'value': True},
                                               {'operation': 'set_parameter',
                                                'field': 'clear_ap',
                                                'parameter_id': 'breach.minor_debris.clear_ap'}),
                                'limitations': ({'assertion': 'field_equals',
                                                 'field': 'major_collapse_excluded',
                                                 'value': True},)},
 ('BREAKER', 'WIDE BREACH'): {'mechanic_id': 'breach_adjacent_section',
                              'parameter_ids': (),
                              'initial_state': {'adjacent_section_included': False},
                              'prerequisites': {'marginal_resource_cost': True},
                              'operations': ({'operation': 'set',
                                              'field': 'adjacent_section_included',
                                              'value': True},),
                              'limitations': ()},
 ('DIPLOMAT', 'NEGOTIATING POSITION'): {'mechanic_id': 'negotiation_counteroffer_window',
                                        'parameter_ids': ('specialty.diplomat.negotiating_position.counteroffers',),
                                        'initial_state': {'changed_counteroffers_after_rejection': 0,
                                                          'counteroffers': 0},
                                        'prerequisites': {'plausible_offer': True},
                                        'operations': ({'operation': 'set',
                                                        'field': 'changed_counteroffers_after_rejection',
                                                        'value': 1},
                                                       {'operation': 'set_parameter',
                                                        'field': 'counteroffers',
                                                        'parameter_id': 'specialty.diplomat.negotiating_position.counteroffers'}),
                                        'limitations': ()},
 ('DIPLOMAT', 'CEASEFIRE'): {'mechanic_id': 'social_agreement',
                             'parameter_ids': (),
                             'initial_state': {'temporary_agreement_action': False, 'betrayal_prevented': False},
                             'prerequisites': {},
                             'operations': ({'operation': 'set',
                                             'field': 'temporary_agreement_action',
                                             'value': True},),
                             'limitations': ({'assertion': 'field_equals',
                                              'field': 'betrayal_prevented',
                                              'value': False},)},
 ('DIPLOMAT', 'TERMS ON PAPER'): {'mechanic_id': 'agreement_evidence',
                                  'parameter_ids': (),
                                  'initial_state': {'recorded_terms_leverage': False},
                                  'prerequisites': {'evidence': True},
                                  'operations': ({'operation': 'set',
                                                  'field': 'recorded_terms_leverage',
                                                  'value': True},),
                                  'limitations': ()},
 ('DIPLOMAT', 'MEDIATOR'): {'mechanic_id': 'multi_party_leverage',
                            'parameter_ids': (),
                            'initial_state': {'other_party_facts_apply': False},
                            'prerequisites': {'two_decision_makers_remain': True},
                            'operations': ({'operation': 'set', 'field': 'other_party_facts_apply', 'value': True},),
                            'limitations': ()},
 ('DIPLOMAT', 'MULTI-PARTY ACCORD'): {'mechanic_id': 'coordinated_negotiation',
                                      'parameter_ids': (),
                                      'initial_state': {'common_terms_one_resolution': False,
                                                        'individual_rejection_preserved': True},
                                      'prerequisites': {},
                                      'operations': ({'operation': 'set',
                                                      'field': 'common_terms_one_resolution',
                                                      'value': True},),
                                      'limitations': ({'assertion': 'field_equals',
                                                       'field': 'individual_rejection_preserved',
                                                       'value': True},)},
 ('DIPLOMAT', 'LAST OFF-RAMP'): {'mechanic_id': 'negotiation_escalation_window',
                                 'parameter_ids': (),
                                 'initial_state': {'final_changed_offer_window': False},
                                 'prerequisites': {'materially_different_offer': True},
                                 'operations': ({'operation': 'set',
                                                 'field': 'final_changed_offer_window',
                                                 'value': True},),
                                 'limitations': ()},
 ('FIELD MEDIC', 'TRIAGE'): {'mechanic_id': 'medical_threat_information',
                             'parameter_ids': (),
                             'initial_state': {'greatest_observable_threat_identified': False,
                                               'hidden_conditions_excluded': True},
                             'prerequisites': {},
                             'operations': ({'operation': 'set',
                                             'field': 'greatest_observable_threat_identified',
                                             'value': True},),
                             'limitations': ({'assertion': 'field_equals',
                                              'field': 'hidden_conditions_excluded',
                                              'value': True},)},
 ('ENFORCER', 'HOLD THE DOOR'): {'mechanic_id': 'cqc_prep',
                                 'parameter_ids': (),
                                 'initial_state': {'contact_entry_prep': False, 'universal_opportunity_attack': False},
                                 'prerequisites': {},
                                 'operations': ({'operation': 'set', 'field': 'contact_entry_prep', 'value': True},),
                                 'limitations': ({'assertion': 'field_equals',
                                                  'field': 'universal_opportunity_attack',
                                                  'value': False},)},
 ('ENFORCER', 'HEAVY CONTACT'): {'mechanic_id': 'contact_res_damage',
                                 'parameter_ids': ('specialty.enforcer.heavy_contact.res_damage',),
                                 'initial_state': {'contact_res_damage': 0, 'res_damage': 0},
                                 'prerequisites': {'physical_contact': True},
                                 'operations': ({'operation': 'set', 'field': 'contact_res_damage', 'value': 1},
                                                {'operation': 'set_parameter',
                                                 'field': 'res_damage',
                                                 'parameter_id': 'specialty.enforcer.heavy_contact.res_damage'}),
                                 'limitations': ()},
 ('FIELD ENGINEER', 'FIELD MODIFICATION'): {'mechanic_id': 'device_modification',
                                            'parameter_ids': (),
                                            'initial_state': {'temporary_physical_modification': False},
                                            'prerequisites': {'tradeoff': True},
                                            'operations': ({'operation': 'set',
                                                            'field': 'temporary_physical_modification',
                                                            'value': True},),
                                            'limitations': ()},
 ('FIELD ENGINEER', 'PATCH JOB'): {'mechanic_id': 'device_patch',
                                   'parameter_ids': (),
                                   'initial_state': {'temporary_function_restored': False, 'reduced_durability': False},
                                   'prerequisites': {},
                                   'operations': ({'operation': 'set',
                                                   'field': 'temporary_function_restored',
                                                   'value': True},
                                                  {'operation': 'set', 'field': 'reduced_durability', 'value': True}),
                                   'limitations': ()},
 ('FIELD ENGINEER', 'REDUNDANT SYSTEM'): {'mechanic_id': 'device_redundancy',
                                          'parameter_ids': (),
                                          'initial_state': {'first_component_failure_disables_function': True,
                                                            'reduced_backup_effectiveness': False},
                                          'prerequisites': {},
                                          'operations': ({'operation': 'set',
                                                          'field': 'first_component_failure_disables_function',
                                                          'value': False},
                                                         {'operation': 'set',
                                                          'field': 'reduced_backup_effectiveness',
                                                          'value': True}),
                                          'limitations': ()},
 ('FIELD ENGINEER', 'RAPID DEPLOYMENT'): {'mechanic_id': 'technical_deployment_discount',
                                          'parameter_ids': ('specialty.field_engineer.rapid_deployment.ap_discount',),
                                          'initial_state': {'specialty_ap_discount': 0,
                                                            'minimum_final_ap': 1,
                                                            'ap_discount': 0},
                                          'prerequisites': {},
                                          'operations': ({'operation': 'set',
                                                          'field': 'specialty_ap_discount',
                                                          'value': 1},
                                                         {'operation': 'set_parameter',
                                                          'field': 'ap_discount',
                                                          'parameter_id': 'specialty.field_engineer.rapid_deployment.ap_discount'}),
                                          'limitations': ({'assertion': 'field_equals',
                                                           'field': 'minimum_final_ap',
                                                           'value': 1},)},
 ('FIELD ENGINEER', 'POWER BUDGET'): {'mechanic_id': 'power_budget_reroute',
                                      'parameter_ids': ('specialty.field_engineer.power_budget.action_ap',
                                                        'specialty.field_engineer.power_budget.transfer_seconds'),
                                      'initial_state': {'action_ap': 0,
                                                        'donor_runtime_s': {'parameter_id': 'equipment.field_relay.battery_s'},
                                                        'recipient_runtime_s': {'parameter_id': 'equipment.field_relay.battery_s'},
                                                        'output_bonus': False},
                                      'prerequisites': {'connected': True, 'compatible': True},
                                      'operations': ({'operation': 'set_parameter',
                                                      'field': 'action_ap',
                                                      'parameter_id': 'specialty.field_engineer.power_budget.action_ap'},
                                                     {'operation': 'subtract_parameter',
                                                      'field': 'donor_runtime_s',
                                                      'parameter_id': 'specialty.field_engineer.power_budget.transfer_seconds'},
                                                     {'operation': 'add_parameter',
                                                      'field': 'recipient_runtime_s',
                                                      'parameter_id': 'specialty.field_engineer.power_budget.transfer_seconds'}),
                                      'limitations': ({'assertion': 'sum_preserved',
                                                       'fields': ('donor_runtime_s', 'recipient_runtime_s')},
                                                      {'assertion': 'field_equals',
                                                       'field': 'output_bonus',
                                                       'value': False})},
 ('FIELD MEDIC', 'COMBAT MEDICINE'): {'mechanic_id': 'distinct_treatment_res_restore',
                                      'parameter_ids': ('specialty.field_medic.combat_medicine.res_restore',),
                                      'initial_state': {'first_treatment_res_restore': 0, 'res_restore': 0},
                                      'prerequisites': {'distinct_condition': True},
                                      'operations': ({'operation': 'set',
                                                      'field': 'first_treatment_res_restore',
                                                      'value': 1},
                                                     {'operation': 'set_parameter',
                                                      'field': 'res_restore',
                                                      'parameter_id': 'specialty.field_medic.combat_medicine.res_restore'}),
                                      'limitations': ()},
 ('FIELD MEDIC', 'CONTINUE CARE'): {'mechanic_id': 'treatment_progress_retention',
                                    'parameter_ids': ('specialty.field_medic.continue_care.progress_retained',),
                                    'initial_state': {'progress_retained': False},
                                    'prerequisites': {'condition_unchanged': True},
                                    'operations': ({'operation': 'set_parameter',
                                                    'field': 'progress_retained',
                                                    'parameter_id': 'specialty.field_medic.continue_care.progress_retained'},),
                                    'limitations': ()},
 ('FIELD MEDIC', 'SECOND WIND'): {'mechanic_id': 'post_treatment_nonattack_ap',
                                  'parameter_ids': ('specialty.field_medic.second_wind.temporary_ap',),
                                  'initial_state': {'temporary_nonattack_ap': 0,
                                                    'once_per_casualty': True,
                                                    'temporary_ap': 0},
                                  'prerequisites': {},
                                  'operations': ({'operation': 'set', 'field': 'temporary_nonattack_ap', 'value': 1},
                                                 {'operation': 'set_parameter',
                                                  'field': 'temporary_ap',
                                                  'parameter_id': 'specialty.field_medic.second_wind.temporary_ap'}),
                                  'limitations': ({'assertion': 'field_equals',
                                                   'field': 'once_per_casualty',
                                                   'value': True},)},
 ('FLANKER', 'SHADOW THE COVER'): {'mechanic_id': 'relocation_prep',
                                   'parameter_ids': (),
                                   'initial_state': {'cover_reorientation_trigger': False, 'limited_movement': True},
                                   'prerequisites': {},
                                   'operations': ({'operation': 'set',
                                                   'field': 'cover_reorientation_trigger',
                                                   'value': True},),
                                   'limitations': ({'assertion': 'field_equals',
                                                    'field': 'limited_movement',
                                                    'value': True},)},
 ('FPV DRONES', 'NAP-OF-THE-EARTH'): {'mechanic_id': 'drone_exposure_reduction',
                                      'parameter_ids': ('specialty.fpv_drones.nap_of_the_earth.exposure_band_reduction',),
                                      'initial_state': {'exposure_band_reduction': 0},
                                      'prerequisites': {'partial_mask': True},
                                      'operations': ({'operation': 'set_parameter',
                                                      'field': 'exposure_band_reduction',
                                                      'parameter_id': 'specialty.fpv_drones.nap_of_the_earth.exposure_band_reduction'},),
                                      'limitations': ()},
 ('FPV DRONES', 'SNAP PEEK'): {'mechanic_id': 'drone_peek_sequence',
                               'parameter_ids': ('specialty.fpv_drones.snap_peek.ap_discount',),
                               'initial_state': {'combined_sequence_discount': 0,
                                                 'actual_positions_resolve_triggers': True,
                                                 'ap_discount': 0},
                               'prerequisites': {},
                               'operations': ({'operation': 'set', 'field': 'combined_sequence_discount', 'value': 1},
                                              {'operation': 'set_parameter',
                                               'field': 'ap_discount',
                                               'parameter_id': 'specialty.fpv_drones.snap_peek.ap_discount'}),
                               'limitations': ({'assertion': 'field_equals',
                                                'field': 'actual_positions_resolve_triggers',
                                                'value': True},)},
 ('GUARDIAN', 'INTERPOSE'): {'mechanic_id': 'protective_prep',
                             'parameter_ids': (),
                             'initial_state': {'explicit_before_interpose': False},
                             'prerequisites': {'physical_route': True},
                             'operations': ({'operation': 'set', 'field': 'explicit_before_interpose', 'value': True},),
                             'limitations': ()},
 ('GUARDIAN', 'PULL BEHIND'): {'mechanic_id': 'protective_reposition',
                               'parameter_ids': (),
                               'initial_state': {'ally_reposition_option': False},
                               'prerequisites': {'consent': True},
                               'operations': ({'operation': 'set', 'field': 'ally_reposition_option', 'value': True},),
                               'limitations': ()},
 ('GUARDIAN', 'BODY COVER'): {'mechanic_id': 'body_screen_exposure_reduction',
                              'parameter_ids': ('specialty.guardian.body_cover.exposure_band_reduction',),
                              'initial_state': {'exposure_band_reduction': 0},
                              'prerequisites': {'actual_screen': True},
                              'operations': ({'operation': 'set_parameter',
                                              'field': 'exposure_band_reduction',
                                              'parameter_id': 'specialty.guardian.body_cover.exposure_band_reduction'},),
                              'limitations': ()},
 ('GUARDIAN', 'DEFENSIVE EXCHANGE'): {'mechanic_id': 'protective_exchange',
                                      'parameter_ids': (),
                                      'initial_state': {'position_exchange': False, 'both_positions_reachable': True},
                                      'prerequisites': {},
                                      'operations': ({'operation': 'set',
                                                      'field': 'position_exchange',
                                                      'value': True},),
                                      'limitations': ({'assertion': 'field_equals',
                                                       'field': 'both_positions_reachable',
                                                       'value': True},)},
 ('GUARDIAN', 'NO OPENING'): {'mechanic_id': 'persistent_interpose',
                              'parameter_ids': (),
                              'initial_state': {'interpose_ends_after_use': True, 'prerequisites_rechecked': True},
                              'prerequisites': {},
                              'operations': ({'operation': 'set',
                                              'field': 'interpose_ends_after_use',
                                              'value': False},),
                              'limitations': ({'assertion': 'field_equals',
                                               'field': 'prerequisites_rechecked',
                                               'value': True},)},
 ('GUARDIAN', 'GUARDED STEP'): {'mechanic_id': 'moving_interpose',
                                'parameter_ids': (),
                                'initial_state': {'interpose_moves_with_ally': False,
                                                  'guardian_spends_movement_ap': False},
                                'prerequisites': {},
                                'operations': ({'operation': 'set',
                                                'field': 'interpose_moves_with_ally',
                                                'value': True},
                                               {'operation': 'set',
                                                'field': 'guardian_spends_movement_ap',
                                                'value': True}),
                                'limitations': ()},
 ('GUARDIAN', 'CLOSE PROTECTION'): {'mechanic_id': 'contact_interpose',
                                    'parameter_ids': (),
                                    'initial_state': {'contact_threat_trigger': False},
                                    'prerequisites': {'reachable_route': True},
                                    'operations': ({'operation': 'set',
                                                    'field': 'contact_threat_trigger',
                                                    'value': True},),
                                    'limitations': ()},
 ('GUARDIAN', 'PROTECT THE OPERATOR'): {'mechanic_id': 'operator_interpose_discount',
                                        'parameter_ids': ('specialty.guardian.protect_the_operator.ap_discount',),
                                        'initial_state': {'interpose_ap_discount': 0, 'ap_discount': 0},
                                        'prerequisites': {'stationary_task': True},
                                        'operations': ({'operation': 'set',
                                                        'field': 'interpose_ap_discount',
                                                        'value': 1},
                                                       {'operation': 'set_parameter',
                                                        'field': 'ap_discount',
                                                        'parameter_id': 'specialty.guardian.protect_the_operator.ap_discount'}),
                                        'limitations': ()},
 ('GUARDIAN', 'EMERGENCY INTERCEPT'): {'mechanic_id': 'cover_loss_interpose',
                                       'parameter_ids': (),
                                       'initial_state': {'cover_loss_trigger': False},
                                       'prerequisites': {'reachable_route': True},
                                       'operations': ({'operation': 'set',
                                                       'field': 'cover_loss_trigger',
                                                       'value': True},),
                                       'limitations': ()},
 ('GUARDIAN', 'STAND OVER THEM'): {'mechanic_id': 'downed_ally_interpose',
                                   'parameter_ids': (),
                                   'initial_state': {'downed_ally_eligible': False, 'medical_effect': False},
                                   'prerequisites': {},
                                   'operations': ({'operation': 'set',
                                                   'field': 'downed_ally_eligible',
                                                   'value': True},),
                                   'limitations': ({'assertion': 'field_equals',
                                                    'field': 'medical_effect',
                                                    'value': False},)},
 ('GUARDIAN', 'EXTRACT'): {'mechanic_id': 'guarded_casualty_movement',
                           'parameter_ids': ('casualty.drag.two_hand_rate_multiplier',
                                             'casualty.carry.rate_multiplier'),
                           'initial_state': {'guarded_transport_combined': False,
                                             'physical_burden_preserved': True,
                                             'two_hand_rate_multiplier': 1.0,
                                             'rate_multiplier': 1.0},
                           'prerequisites': {},
                           'operations': ({'operation': 'set', 'field': 'guarded_transport_combined', 'value': True},
                                          {'operation': 'set_parameter',
                                           'field': 'two_hand_rate_multiplier',
                                           'parameter_id': 'casualty.drag.two_hand_rate_multiplier'},
                                          {'operation': 'set_parameter',
                                           'field': 'rate_multiplier',
                                           'parameter_id': 'casualty.carry.rate_multiplier'}),
                           'limitations': ({'assertion': 'field_equals',
                                            'field': 'physical_burden_preserved',
                                            'value': True},)},
 ('GUARDIAN', 'LAST GUARD'): {'mechanic_id': 'interpose_downing_reposition',
                              'parameter_ids': ('specialty.guardian.last_guard.move_m',),
                              'initial_state': {'ally_reposition_m': 0, 'triggers_remain_active': True, 'move_m': 0},
                              'prerequisites': {},
                              'operations': ({'operation': 'set', 'field': 'ally_reposition_m', 'value': 1},
                                             {'operation': 'set_parameter',
                                              'field': 'move_m',
                                              'parameter_id': 'specialty.guardian.last_guard.move_m'}),
                              'limitations': ({'assertion': 'field_equals',
                                               'field': 'triggers_remain_active',
                                               'value': True},)},
 ('GUARDIAN', 'COVERING WITHDRAWAL'): {'mechanic_id': 'route_interpose',
                                       'parameter_ids': (),
                                       'initial_state': {'withdrawal_route_screened': False,
                                                         'guardian_spends_movement_ap': False},
                                       'prerequisites': {},
                                       'operations': ({'operation': 'set',
                                                       'field': 'withdrawal_route_screened',
                                                       'value': True},
                                                      {'operation': 'set',
                                                       'field': 'guardian_spends_movement_ap',
                                                       'value': True}),
                                       'limitations': ()},
 ('GUARDIAN', 'NONE LEFT EXPOSED'): {'mechanic_id': 'reach_interpose',
                                     'parameter_ids': (),
                                     'initial_state': {'beneficiary_named_at_setup': True,
                                                       'one_physical_response_at_a_time': True},
                                     'prerequisites': {},
                                     'operations': ({'operation': 'set',
                                                     'field': 'beneficiary_named_at_setup',
                                                     'value': False},),
                                     'limitations': ({'assertion': 'field_equals',
                                                      'field': 'one_physical_response_at_a_time',
                                                      'value': True},)},
 ('GUNNER', 'SUPPRESSIVE SWEEP'): {'mechanic_id': 'suppression_connected_areas',
                                   'parameter_ids': ('suppression.rounds_per_5m_zone',),
                                   'initial_state': {'connected_areas': 1, 'rounds_per_5m_zone': 0},
                                   'prerequisites': {'ammunition_for_both': True},
                                   'operations': ({'operation': 'set', 'field': 'connected_areas', 'value': 2},
                                                  {'operation': 'set_parameter',
                                                   'field': 'rounds_per_5m_zone',
                                                   'parameter_id': 'suppression.rounds_per_5m_zone'}),
                                   'limitations': ()},
 ('GUNNER', 'COUNTERFIRE'): {'mechanic_id': 'suppression_counterfire_prep',
                             'parameter_ids': (),
                             'initial_state': {'return_fire_prep_added': False,
                                               'ordinary_attack_and_ammunition_apply': True},
                             'prerequisites': {},
                             'operations': ({'operation': 'set', 'field': 'return_fire_prep_added', 'value': True},),
                             'limitations': ({'assertion': 'field_equals',
                                              'field': 'ordinary_attack_and_ammunition_apply',
                                              'value': True},)},
 ('GUNNER', 'KEEP THE GUN UP'): {'mechanic_id': 'supported_posture_persistence',
                                 'parameter_ids': (),
                                 'initial_state': {'nonphysical_abandonment_ends_posture': True,
                                                   'physical_breaks_preserved': True},
                                 'prerequisites': {},
                                 'operations': ({'operation': 'set',
                                                 'field': 'nonphysical_abandonment_ends_posture',
                                                 'value': False},),
                                 'limitations': ({'assertion': 'field_equals',
                                                  'field': 'physical_breaks_preserved',
                                                  'value': True},)},
 ('GUNNER', 'WALKING FIRE'): {'mechanic_id': 'sustained_fire_state_movement',
                              'parameter_ids': (),
                              'initial_state': {'movement_ends_sustained_state': True,
                                                'movement_ap_and_recoil_apply': True},
                              'prerequisites': {},
                              'operations': ({'operation': 'set',
                                              'field': 'movement_ends_sustained_state',
                                              'value': False},),
                              'limitations': ({'assertion': 'field_equals',
                                               'field': 'movement_ap_and_recoil_apply',
                                               'value': True},)},
 ('GUNNER', 'FIRE THROUGH THE OPENING'): {'mechanic_id': 'narrow_suppression_effectiveness',
                                          'parameter_ids': ('specialty.gunner.fire_through_the_opening.difficulty_offset',
                                                            'specialty.gunner.fire_through_the_opening.max_width_m'),
                                          'initial_state': {'difficulty_offset': 0, 'max_width_m': 0},
                                          'prerequisites': {'narrow_zone': True},
                                          'operations': ({'operation': 'set_parameter',
                                                          'field': 'difficulty_offset',
                                                          'parameter_id': 'specialty.gunner.fire_through_the_opening.difficulty_offset'},
                                                         {'operation': 'set_parameter',
                                                          'field': 'max_width_m',
                                                          'parameter_id': 'specialty.gunner.fire_through_the_opening.max_width_m'}),
                                          'limitations': ()},
 ('GUNNER', 'FIRE SUPERIORITY'): {'mechanic_id': 'seamless_suppression_maintenance',
                                  'parameter_ids': ('specialty.gunner.fire_superiority.maintenance_ap_discount',),
                                  'initial_state': {'maintenance_ap_discount': 0},
                                  'prerequisites': {'fresh_ammunition': True},
                                  'operations': ({'operation': 'set_parameter',
                                                  'field': 'maintenance_ap_discount',
                                                  'parameter_id': 'specialty.gunner.fire_superiority.maintenance_ap_discount'},),
                                  'limitations': ()},
 ('HANDLER', 'INTRODUCTION'): {'mechanic_id': 'contact_graph',
                               'parameter_ids': (),
                               'initial_state': {'reachable_relationship_steps': 0},
                               'prerequisites': {'actual_relationship': True},
                               'operations': ({'operation': 'set',
                                               'field': 'reachable_relationship_steps',
                                               'value': 1},),
                               'limitations': ()},
 ('HANDLER', 'CUTOUT'): {'mechanic_id': 'contact_origin',
                         'parameter_ids': (),
                         'initial_state': {'origin_hidden': False},
                         'prerequisites': {'intermediary': True},
                         'operations': ({'operation': 'set', 'field': 'origin_hidden', 'value': True},),
                         'limitations': ()},
 ('HANDLER', 'CORROBORATE'): {'mechanic_id': 'evidence_confidence',
                              'parameter_ids': (),
                              'initial_state': {'independent_source_upgrade': False},
                              'prerequisites': {'independence': True},
                              'operations': ({'operation': 'set',
                                              'field': 'independent_source_upgrade',
                                              'value': True},),
                              'limitations': ()},
 ('HANDLER', 'HUMAN NETWORK'): {'mechanic_id': 'contact_graph_routing',
                                'parameter_ids': (),
                                'initial_state': {'multi_hop_relationship_route': False,
                                                  'contact_refusal_preserved': True},
                                'prerequisites': {},
                                'operations': ({'operation': 'set',
                                                'field': 'multi_hop_relationship_route',
                                                'value': True},),
                                'limitations': ({'assertion': 'field_equals',
                                                 'field': 'contact_refusal_preserved',
                                                 'value': True},)},
 ('ICON', 'RECOGNITION'): {'mechanic_id': 'reputation_leverage',
                           'parameter_ids': ('social.fact_to_leverage_setup_ap',),
                           'initial_state': {'leverage_ready': False,
                                             'setup_ap': 'baseline',
                                             'fact_to_leverage_setup_ap': 0},
                           'prerequisites': {'recognition': True},
                           'operations': ({'operation': 'set', 'field': 'leverage_ready', 'value': True},
                                          {'operation': 'set', 'field': 'setup_ap', 'value': 0},
                                          {'operation': 'set_parameter',
                                           'field': 'fact_to_leverage_setup_ap',
                                           'parameter_id': 'social.fact_to_leverage_setup_ap'}),
                           'limitations': ()},
 ('ICON', 'BROADCAST'): {'mechanic_id': 'social_channel',
                         'parameter_ids': (),
                         'initial_state': {'one_action_many_listeners': False},
                         'prerequisites': {'real_channel': True},
                         'operations': ({'operation': 'set', 'field': 'one_action_many_listeners', 'value': True},),
                         'limitations': ()},
 ('ICON', 'PUBLIC INTRODUCTION'): {'mechanic_id': 'reputation_endorsement',
                                   'parameter_ids': (),
                                   'initial_state': {'ally_temporary_leverage': False, 'scene_limited': True},
                                   'prerequisites': {},
                                   'operations': ({'operation': 'set',
                                                   'field': 'ally_temporary_leverage',
                                                   'value': True},),
                                   'limitations': ({'assertion': 'field_equals',
                                                    'field': 'scene_limited',
                                                    'value': True},)},
 ('ICON', 'TRANSFER PRESTIGE'): {'mechanic_id': 'multi_ally_endorsement',
                                 'parameter_ids': (),
                                 'initial_state': {'endorsed_allies': 1},
                                 'prerequisites': {'visible_effort': True},
                                 'operations': ({'operation': 'set', 'field': 'endorsed_allies', 'value': 'multiple'},),
                                 'limitations': ()},
 ('ICON', 'DEFINE THE MOMENT'): {'mechanic_id': 'reputation_fact_creation',
                                 'parameter_ids': (),
                                 'initial_state': {'new_true_reputation_fact': False},
                                 'prerequisites': {'public_evidence': True},
                                 'operations': ({'operation': 'set',
                                                 'field': 'new_true_reputation_fact',
                                                 'value': True},),
                                 'limitations': ()},
 ('ICON', 'LIVING SYMBOL'): {'mechanic_id': 'scene_reputation_channels',
                             'parameter_ids': (),
                             'initial_state': {'simultaneous_reputation_channels': 1},
                             'prerequisites': {'relevant_audience': True},
                             'operations': ({'operation': 'set',
                                             'field': 'simultaneous_reputation_channels',
                                             'value': 4},),
                             'limitations': ()},
 ('INFILTRATOR', 'APPROACH PLAN'): {'mechanic_id': 'approach_state',
                                    'parameter_ids': (),
                                    'initial_state': {'approach_plan': False, 'observed_threats_only': True},
                                    'prerequisites': {},
                                    'operations': ({'operation': 'set', 'field': 'approach_plan', 'value': True},),
                                    'limitations': ({'assertion': 'field_equals',
                                                     'field': 'observed_threats_only',
                                                     'value': True},)},
 ('INFILTRATOR', 'PHANTOM APPROACH'): {'mechanic_id': 'failed_stealth_fallback',
                                       'parameter_ids': (),
                                       'initial_state': {'abort_to_last_concealment': False,
                                                         'ap_refunded': False,
                                                         'suspicion_retained': True},
                                       'prerequisites': {},
                                       'operations': ({'operation': 'set',
                                                       'field': 'abort_to_last_concealment',
                                                       'value': True},),
                                       'limitations': ({'assertion': 'field_equals',
                                                        'field': 'ap_refunded',
                                                        'value': False},
                                                       {'assertion': 'field_equals',
                                                        'field': 'suspicion_retained',
                                                        'value': True})},
 ('LOCAL HACKER', 'SNAPSHOT'): {'mechanic_id': 'node_snapshot',
                                'parameter_ids': (),
                                'initial_state': {'configuration_snapshot': False},
                                'prerequisites': {'storage': True},
                                'operations': ({'operation': 'set', 'field': 'configuration_snapshot', 'value': True},),
                                'limitations': ()},
 ('LOCAL HACKER', 'PERSISTENT OVERRIDE'): {'mechanic_id': 'node_persistence',
                                           'parameter_ids': (),
                                           'initial_state': {'change_survives_restart': False,
                                                             'remediation_possible': True},
                                           'prerequisites': {},
                                           'operations': ({'operation': 'set',
                                                           'field': 'change_survives_restart',
                                                           'value': True},),
                                           'limitations': ({'assertion': 'field_equals',
                                                            'field': 'remediation_possible',
                                                            'value': True},)},
 ('LOCAL HACKER', 'SCRIPTED ROUTINE'): {'mechanic_id': 'node_routine',
                                        'parameter_ids': (),
                                        'initial_state': {'conditional_routine': False,
                                                          'existing_functions_only': True},
                                        'prerequisites': {},
                                        'operations': ({'operation': 'set',
                                                        'field': 'conditional_routine',
                                                        'value': True},),
                                        'limitations': ({'assertion': 'field_equals',
                                                         'field': 'existing_functions_only',
                                                         'value': True},)},
 ('LOCAL HACKER', 'DIAGNOSTIC VIEW'): {'mechanic_id': 'node_diagnostic_information',
                                       'parameter_ids': (),
                                       'initial_state': {'observable_state_bundle': False,
                                                         'node_knowledge_bound': True},
                                       'prerequisites': {},
                                       'operations': ({'operation': 'set',
                                                       'field': 'observable_state_bundle',
                                                       'value': True},),
                                       'limitations': ({'assertion': 'field_equals',
                                                        'field': 'node_knowledge_bound',
                                                        'value': True},)},
 ('LOCAL HACKER', 'INTERLOCK BYPASS'): {'mechanic_id': 'node_interlock_state',
                                        'parameter_ids': (),
                                        'initial_state': {'software_interlock_bypassed': False,
                                                          'hardware_limits_preserved': True},
                                        'prerequisites': {},
                                        'operations': ({'operation': 'set',
                                                        'field': 'software_interlock_bypassed',
                                                        'value': True},),
                                        'limitations': ({'assertion': 'field_equals',
                                                         'field': 'hardware_limits_preserved',
                                                         'value': True},)},
 ('LOCAL HACKER', 'HARD LOCKOUT'): {'mechanic_id': 'node_session_state',
                                    'parameter_ids': (),
                                    'initial_state': {'selected_remote_method_revoked': False},
                                    'prerequisites': {'node_authority': True},
                                    'operations': ({'operation': 'set',
                                                    'field': 'selected_remote_method_revoked',
                                                    'value': True},),
                                    'limitations': ()},
 ('LOCAL HACKER', 'LOCAL COUNTER-INTRUSION'): {'mechanic_id': 'node_intrusion_prep',
                                               'parameter_ids': (),
                                               'initial_state': {'incoming_intrusion_response': False},
                                               'prerequisites': {'controlled_node_route': True},
                                               'operations': ({'operation': 'set',
                                                               'field': 'incoming_intrusion_response',
                                                               'value': True},),
                                               'limitations': ()},
 ('LOCAL HACKER', 'SERVICE ACCESS'): {'mechanic_id': 'local_get_in_reliability',
                                      'parameter_ids': ('specialty.local_hacker.service_access.difficulty_offset',),
                                      'initial_state': {'difficulty_offset': 0},
                                      'prerequisites': {'direct_local_access': True},
                                      'operations': ({'operation': 'set_parameter',
                                                      'field': 'difficulty_offset',
                                                      'parameter_id': 'specialty.local_hacker.service_access.difficulty_offset'},),
                                      'limitations': ()},
 ('NETWORKER', 'MAP THE LINKS'): {'mechanic_id': 'network_discovery',
                                  'parameter_ids': (),
                                  'initial_state': {'neighbor_nodes_revealed': False, 'direct_visibility_only': True},
                                  'prerequisites': {},
                                  'operations': ({'operation': 'set',
                                                  'field': 'neighbor_nodes_revealed',
                                                  'value': True},),
                                  'limitations': ({'assertion': 'field_equals',
                                                   'field': 'direct_visibility_only',
                                                   'value': True},)},
 ('NETWORKER', 'FOOTHOLD'): {'mechanic_id': 'network_foothold',
                             'parameter_ids': ('specialty.networker.foothold.ap_discount',),
                             'initial_state': {'persistent_access_permission': True,
                                               'foothold_ap_discount': 0,
                                               'minimum_final_ap': 1,
                                               'ordinary_detection_and_remediation': True,
                                               'ap_discount': 0},
                             'prerequisites': {'compromised_remote_node': True, 'valid_route': True},
                             'operations': ({'operation': 'set', 'field': 'foothold_ap_discount', 'value': 1},
                                            {'operation': 'set_parameter',
                                             'field': 'ap_discount',
                                             'parameter_id': 'specialty.networker.foothold.ap_discount'}),
                             'limitations': ({'assertion': 'field_equals', 'field': 'minimum_final_ap', 'value': 1},
                                             {'assertion': 'field_equals',
                                              'field': 'ordinary_detection_and_remediation',
                                              'value': True})},
 ('NETWORKER', 'ROUTE PERSISTENCE'): {'mechanic_id': 'network_route',
                                      'parameter_ids': (),
                                      'initial_state': {'alternate_route_retains_access': False},
                                      'prerequisites': {'known_route': True},
                                      'operations': ({'operation': 'set',
                                                      'field': 'alternate_route_retains_access',
                                                      'value': True},),
                                      'limitations': ()},
 ('NETWORKER', 'NETWORK INTERCEPT'): {'mechanic_id': 'network_prep',
                                      'parameter_ids': (),
                                      'initial_state': {'traffic_trigger_prep': False},
                                      'prerequisites': {'controlled_route': True},
                                      'operations': ({'operation': 'set',
                                                      'field': 'traffic_trigger_prep',
                                                      'value': True},),
                                      'limitations': ()},
 ('NETWORKER', 'CREDENTIAL SWEEP'): {'mechanic_id': 'credential_candidate_information',
                                     'parameter_ids': (),
                                     'initial_state': {'plausible_candidate_systems': False,
                                                       'credential_validity_unknown': True},
                                     'prerequisites': {},
                                     'operations': ({'operation': 'set',
                                                     'field': 'plausible_candidate_systems',
                                                     'value': True},),
                                     'limitations': ({'assertion': 'field_equals',
                                                      'field': 'credential_validity_unknown',
                                                      'value': True},)},
 ('OPERATOR', 'COMPOUND RELOAD'): {'mechanic_id': 'firearm_atomic_overlap',
                                   'parameter_ids': (),
                                   'initial_state': {'compatible_steps_overlap': False, 'state_updates_atomic': True},
                                   'prerequisites': {},
                                   'operations': ({'operation': 'set',
                                                   'field': 'compatible_steps_overlap',
                                                   'value': True},),
                                   'limitations': ({'assertion': 'field_equals',
                                                    'field': 'state_updates_atomic',
                                                    'value': True},)},
 ('OPERATOR', 'NO WASTED MOTION'): {'mechanic_id': 'firearm_general_overlap',
                                    'parameter_ids': (),
                                    'initial_state': {'compatible_steps_overlap': False},
                                    'prerequisites': {'physical_compatibility': True},
                                    'operations': ({'operation': 'set',
                                                    'field': 'compatible_steps_overlap',
                                                    'value': True},),
                                    'limitations': ()},
 ('SENTINEL', 'COUNTER-PREP'): {'mechanic_id': 'overwatch_trigger',
                                'parameter_ids': (),
                                'initial_state': {'prep_exposure_trigger': False, 'full_cover_safe': True},
                                'prerequisites': {},
                                'operations': ({'operation': 'set', 'field': 'prep_exposure_trigger', 'value': True},),
                                'limitations': ({'assertion': 'field_equals',
                                                 'field': 'full_cover_safe',
                                                 'value': True},)},
 ('SENTINEL', 'SUPPRESSIVE WATCH'): {'mechanic_id': 'overwatch_to_suppression',
                                     'parameter_ids': ('suppression.rounds_per_5m_zone',),
                                     'initial_state': {'followup_suppression_available': False,
                                                       'rounds_per_5m_zone': 0},
                                     'prerequisites': {'additional_ammunition': True},
                                     'operations': ({'operation': 'set',
                                                     'field': 'followup_suppression_available',
                                                     'value': True},
                                                    {'operation': 'set_parameter',
                                                     'field': 'rounds_per_5m_zone',
                                                     'parameter_id': 'suppression.rounds_per_5m_zone'}),
                                     'limitations': ()},
 ('SENTINEL', 'SECOND TRIGGER'): {'mechanic_id': 'overwatch_retained_trigger',
                                  'parameter_ids': ('specialty.sentinel.second_trigger.additional_triggers',),
                                  'initial_state': {'additional_triggers': 0, 'ammunition_and_recoil_apply': True},
                                  'prerequisites': {},
                                  'operations': ({'operation': 'set_parameter',
                                                  'field': 'additional_triggers',
                                                  'parameter_id': 'specialty.sentinel.second_trigger.additional_triggers'},),
                                  'limitations': ({'assertion': 'field_equals',
                                                   'field': 'ammunition_and_recoil_apply',
                                                   'value': True},)},
 ('SENTINEL', 'HOLD UNDER FIRE'): {'mechanic_id': 'prepared_shot_priority',
                                   'parameter_ids': ('specialty.sentinel.hold_under_fire.trigger_priority',),
                                   'initial_state': {'prepared_shot_precedes_suppression': False,
                                                     'trigger_priority': 0},
                                   'prerequisites': {'same_enemy_action': True},
                                   'operations': ({'operation': 'set',
                                                   'field': 'prepared_shot_precedes_suppression',
                                                   'value': True},
                                                  {'operation': 'set_parameter',
                                                   'field': 'trigger_priority',
                                                   'parameter_id': 'specialty.sentinel.hold_under_fire.trigger_priority'}),
                                   'limitations': ()},
 ('SENTINEL', 'DEEP SECTOR'): {'mechanic_id': 'overwatch_sector_depth',
                               'parameter_ids': ('specialty.sentinel.deep_sector.extra_depth_m',),
                               'initial_state': {'extra_depth_m': 0},
                               'prerequisites': {'continuous_sightline': True},
                               'operations': ({'operation': 'set_parameter',
                                               'field': 'extra_depth_m',
                                               'parameter_id': 'specialty.sentinel.deep_sector.extra_depth_m'},),
                               'limitations': ()},
 ('SENTINEL', 'KILL ZONE'): {'mechanic_id': 'developed_overwatch_position',
                             'parameter_ids': ('specialty.sentinel.second_trigger.additional_triggers',
                                               'specialty.sentinel.deep_sector.extra_depth_m'),
                             'initial_state': {'connected_watched_areas': 1,
                                               'one_weapon_position': True,
                                               'additional_triggers': 0,
                                               'extra_depth_m': 0},
                             'prerequisites': {},
                             'operations': ({'operation': 'set', 'field': 'connected_watched_areas', 'value': 2},
                                            {'operation': 'set_parameter',
                                             'field': 'additional_triggers',
                                             'parameter_id': 'specialty.sentinel.second_trigger.additional_triggers'},
                                            {'operation': 'set_parameter',
                                             'field': 'extra_depth_m',
                                             'parameter_id': 'specialty.sentinel.deep_sector.extra_depth_m'}),
                             'limitations': ({'assertion': 'field_equals',
                                              'field': 'one_weapon_position',
                                              'value': True},)},
 ('SHIELD BEARER', 'SHIELD TRAINING'): {'mechanic_id': 'trained_shield_handling',
                                        'parameter_ids': ('specialty.shield_bearer.shield_training.burden_reduction',
                                                          'specialty.shield_bearer.shield_training.handling_reduction'),
                                        'initial_state': {'effective_burden_reduction': 0,
                                                          'effective_handling_reduction': 0,
                                                          'burden_reduction': 0,
                                                          'handling_reduction': 0},
                                        'prerequisites': {},
                                        'operations': ({'operation': 'set',
                                                        'field': 'effective_burden_reduction',
                                                        'value': 1},
                                                       {'operation': 'set',
                                                        'field': 'effective_handling_reduction',
                                                        'value': 1},
                                                       {'operation': 'set_parameter',
                                                        'field': 'burden_reduction',
                                                        'parameter_id': 'specialty.shield_bearer.shield_training.burden_reduction'},
                                                       {'operation': 'set_parameter',
                                                        'field': 'handling_reduction',
                                                        'parameter_id': 'specialty.shield_bearer.shield_training.handling_reduction'}),
                                        'limitations': ()},
 ('SHIELD BEARER', 'CONTROLLED RETREAT'): {'mechanic_id': 'shield_facing',
                                           'parameter_ids': (),
                                           'initial_state': {'forced_move_retains_facing': False,
                                                             'displacement_prevented': False},
                                           'prerequisites': {},
                                           'operations': ({'operation': 'set',
                                                           'field': 'forced_move_retains_facing',
                                                           'value': True},),
                                           'limitations': ({'assertion': 'field_equals',
                                                            'field': 'displacement_prevented',
                                                            'value': False},)},
 ('SHIELD BEARER', 'CORNER GUARD'): {'mechanic_id': 'shield_corner_prep',
                                     'parameter_ids': ('specialty.shield_bearer.corner_guard.prep_ap',),
                                     'initial_state': {'corner_prep_available': False, 'prep_ap': 0},
                                     'prerequisites': {'one_direction_declared': True},
                                     'operations': ({'operation': 'set',
                                                     'field': 'corner_prep_available',
                                                     'value': True},
                                                    {'operation': 'set_parameter',
                                                     'field': 'prep_ap',
                                                     'parameter_id': 'specialty.shield_bearer.corner_guard.prep_ap'}),
                                     'limitations': ()},
 ('SHIELD BEARER', 'STEP TOGETHER'): {'mechanic_id': 'shield_ally_stance_fold',
                                      'parameter_ids': ('specialty.shield_bearer.step_together.stance_ap_discount',),
                                      'initial_state': {'ally_stance_folded': False,
                                                        'both_spend_movement_ap': False,
                                                        'stance_ap_discount': 0},
                                      'prerequisites': {},
                                      'operations': ({'operation': 'set', 'field': 'ally_stance_folded', 'value': True},
                                                     {'operation': 'set',
                                                      'field': 'both_spend_movement_ap',
                                                      'value': True},
                                                     {'operation': 'set_parameter',
                                                      'field': 'stance_ap_discount',
                                                      'parameter_id': 'specialty.shield_bearer.step_together.stance_ap_discount'}),
                                      'limitations': ()},
 ('SHIELD BEARER', 'CROSSING SCREEN'): {'mechanic_id': 'shield_escort',
                                        'parameter_ids': (),
                                        'initial_state': {'coordinated_screen': False, 'bearer_spends_movement': False},
                                        'prerequisites': {},
                                        'operations': ({'operation': 'set',
                                                        'field': 'coordinated_screen',
                                                        'value': True},
                                                       {'operation': 'set',
                                                        'field': 'bearer_spends_movement',
                                                        'value': True}),
                                        'limitations': ()},
 ('SHIELD BEARER', 'SHIELDED RECOVERY'): {'mechanic_id': 'shielded_casualty_movement',
                                          'parameter_ids': ('casualty.drag.one_hand_rate_multiplier',),
                                          'initial_state': {'shielded_drag_available': False,
                                                            'hand_and_burden_limits_apply': True,
                                                            'one_hand_rate_multiplier': 1.0},
                                          'prerequisites': {},
                                          'operations': ({'operation': 'set',
                                                          'field': 'shielded_drag_available',
                                                          'value': True},
                                                         {'operation': 'set_parameter',
                                                          'field': 'one_hand_rate_multiplier',
                                                          'parameter_id': 'casualty.drag.one_hand_rate_multiplier'}),
                                          'limitations': ({'assertion': 'field_equals',
                                                           'field': 'hand_and_burden_limits_apply',
                                                           'value': True},)},
 ('SHIELD BEARER', 'DRAW THE BURST'): {'mechanic_id': 'suppression_shield_intercept',
                                       'parameter_ids': ('specialty.shield_bearer.draw_the_burst.extra_capacity_cost',),
                                       'initial_state': {'extra_capacity_cost': 0},
                                       'prerequisites': {'physical_intercept': True},
                                       'operations': ({'operation': 'set_parameter',
                                                       'field': 'extra_capacity_cost',
                                                       'parameter_id': 'specialty.shield_bearer.draw_the_burst.extra_capacity_cost'},),
                                       'limitations': ()},
 ('SHIELD BEARER', 'SEAL THE GAP'): {'mechanic_id': 'shield_prep',
                                     'parameter_ids': (),
                                     'initial_state': {'destroyed_cover_gap_response': False,
                                                       'shield_becomes_cover': False},
                                     'prerequisites': {},
                                     'operations': ({'operation': 'set',
                                                     'field': 'destroyed_cover_gap_response',
                                                     'value': True},
                                                    {'operation': 'set',
                                                     'field': 'shield_becomes_cover',
                                                     'value': True}),
                                     'limitations': ()},
 ('SHIELD BEARER', 'SHIELD CHECK'): {'mechanic_id': 'shield_shove_state',
                                     'parameter_ids': (),
                                     'initial_state': {'shove_requires_shield_stowed': True,
                                                       'shield_geometry_retained': True},
                                     'prerequisites': {},
                                     'operations': ({'operation': 'set',
                                                     'field': 'shove_requires_shield_stowed',
                                                     'value': False},),
                                     'limitations': ({'assertion': 'field_equals',
                                                      'field': 'shield_geometry_retained',
                                                      'value': True},)},
 ('SHIELD BEARER', 'INDEXED RELOAD'): {'mechanic_id': 'shield_reload_state',
                                       'parameter_ids': (),
                                       'initial_state': {'compatible_reload_requires_shield_stowed': True},
                                       'prerequisites': {'one_handed_compatibility': True},
                                       'operations': ({'operation': 'set',
                                                       'field': 'compatible_reload_requires_shield_stowed',
                                                       'value': False},),
                                       'limitations': ()},
 ('SHIELD BEARER', 'IMPACT CONTROL'): {'mechanic_id': 'shield_impact_choice',
                                       'parameter_ids': (),
                                       'initial_state': {'failure_consequence_choice': False},
                                       'prerequisites': {'real_consequence': True},
                                       'operations': ({'operation': 'set',
                                                       'field': 'failure_consequence_choice',
                                                       'value': True},),
                                       'limitations': ()},
 ('SHIELD BEARER', 'BREACH SCREEN'): {'mechanic_id': 'shield_threshold_geometry',
                                      'parameter_ids': (),
                                      'initial_state': {'shield_first_threshold_screen': False},
                                      'prerequisites': {'usable_opening': True},
                                      'operations': ({'operation': 'set',
                                                      'field': 'shield_first_threshold_screen',
                                                      'value': True},),
                                      'limitations': ()},
 ('TRAUMA', 'FIELD SURGERY'): {'mechanic_id': 'medical_scope',
                               'parameter_ids': (),
                               'initial_state': {'limited_invasive_field_treatment': False},
                               'prerequisites': {'tools_and_conditions': True},
                               'operations': ({'operation': 'set',
                                               'field': 'limited_invasive_field_treatment',
                                               'value': True},),
                               'limitations': ()},
 ('TRAUMA', 'SALVAGE'): {'mechanic_id': 'persistent_consequence_repair',
                         'parameter_ids': ('specialty.trauma.salvage.persistent_difficulty_reduction',),
                         'initial_state': {'injury_id': 'INJURY_REPAIRABLE_FOREARM_TRAUMA_V1',
                                           'persistent_state': 'SEVERE_HAND_IMPAIRMENT',
                                           'persistent_difficulty': {'parameter_id': 'injury.repairable_forearm.ordinary_persistent_difficulty'},
                                           'salvage_applications': 0,
                                           'immediate_hp_restored': 0},
                         'prerequisites': {'successful_definitive_care': True,
                                           'injury_repairable': True,
                                           'injury_irreparable': False},
                         'operations': ({'operation': 'set',
                                         'field': 'persistent_state',
                                         'value': 'MODERATE_HAND_IMPAIRMENT'},
                                        {'operation': 'subtract_parameter',
                                         'field': 'persistent_difficulty',
                                         'parameter_id': 'specialty.trauma.salvage.persistent_difficulty_reduction'},
                                        {'operation': 'add', 'field': 'salvage_applications', 'amount': 1}),
                         'limitations': ({'assertion': 'field_equals', 'field': 'immediate_hp_restored', 'value': 0},)},
 ('SHOCK', 'MOMENTUM CHAIN'): {'mechanic_id': 'assault_momentum_refresh',
                               'parameter_ids': (),
                               'initial_state': {'momentum_refresh_on_new_disruption': False},
                               'prerequisites': {'new_position': True},
                               'operations': ({'operation': 'set',
                                               'field': 'momentum_refresh_on_new_disruption',
                                               'value': True},),
                               'limitations': ()},
 ('RESUSCITATOR', 'RESUSCITATION'): {'mechanic_id': 'resuscitation_window_hold',
                                     'parameter_ids': ('specialty.resuscitator.resuscitation.window_hold',),
                                     'initial_state': {'window_held_during_valid_care': False, 'window_hold': 0},
                                     'prerequisites': {'care_began_in_time': True},
                                     'operations': ({'operation': 'set',
                                                     'field': 'window_held_during_valid_care',
                                                     'value': True},
                                                    {'operation': 'set_parameter',
                                                     'field': 'window_hold',
                                                     'parameter_id': 'specialty.resuscitator.resuscitation.window_hold'}),
                                     'limitations': ()},
 ('EVADER', 'SLIP THE FIRE'): {'mechanic_id': 'suppression_evasive_movement',
                               'parameter_ids': ('specialty.evader.slip_the_fire.movement_multiplier',
                                                 'specialty.evader.slip_the_fire.difficulty_offset'),
                               'initial_state': {'movement_multiplier': 1.0, 'difficulty_offset': 0},
                               'prerequisites': {},
                               'operations': ({'operation': 'set_parameter',
                                               'field': 'movement_multiplier',
                                               'parameter_id': 'specialty.evader.slip_the_fire.movement_multiplier'},
                                              {'operation': 'set_parameter',
                                               'field': 'difficulty_offset',
                                               'parameter_id': 'specialty.evader.slip_the_fire.difficulty_offset'}),
                               'limitations': ()}}

# Approved exclusive-choice adapters.  Each activation selects exactly one
# branch, so the optimizer may tune both admitted values without ever applying
# them cumulatively to the same specialty use.
SPECIALTY_MECHANICS.update(
    {
        ("FIELD ENGINEER", "EMPLACE"): {
            "mechanic_id": "exclusive_emplacement_benefit",
            "parameter_ids": (
                "specialty.field_engineer.emplace.difficulty_offset",
                "specialty.field_engineer.emplace.durability_or_restoration_bonus",
            ),
            "initial_state": {
                "difficulty_offset": 0,
                "durability_or_restoration_bonus": 0,
            },
            "prerequisites": {},
            "operations": (),
            "mechanical_branches": {
                "EFFECTIVENESS": (
                    {
                        "operation": "set_parameter",
                        "field": "difficulty_offset",
                        "parameter_id": "specialty.field_engineer.emplace.difficulty_offset",
                    },
                ),
                "DURABILITY": (
                    {
                        "operation": "set_parameter",
                        "field": "durability_or_restoration_bonus",
                        "parameter_id": "specialty.field_engineer.emplace.durability_or_restoration_bonus",
                    },
                ),
            },
            "default_branch": "EFFECTIVENESS",
            "limitations": (),
        },
        ("GUNNER", "SUPPRESSION DISCIPLINE"): {
            "mechanic_id": "exclusive_suppression_efficiency",
            "parameter_ids": (
                "specialty.gunner.suppression_discipline.ammunition_discount",
                "specialty.gunner.suppression_discipline.ap_discount",
            ),
            "initial_state": {"ammunition_discount": 0, "ap_discount": 0},
            "prerequisites": {},
            "operations": (),
            "mechanical_branches": {
                "AMMUNITION": (
                    {
                        "operation": "set_parameter",
                        "field": "ammunition_discount",
                        "parameter_id": "specialty.gunner.suppression_discipline.ammunition_discount",
                    },
                ),
                "FIRING_TIME": (
                    {
                        "operation": "set_parameter",
                        "field": "ap_discount",
                        "parameter_id": "specialty.gunner.suppression_discipline.ap_discount",
                    },
                ),
            },
            "default_branch": "AMMUNITION",
            "limitations": (),
        },
    }
)

_OVERDRIVE_OUTPUTS = {
    "MOVEMENT": (
        "movement_m_per_ap_bonus",
        "specialty.field_engineer.overdrive.movement_m_per_ap_bonus",
    ),
    "SENSOR": (
        "sensor_steps",
        "specialty.field_engineer.overdrive.sensor_steps",
    ),
    "OPERATION_RATE": (
        "operation_rate_steps",
        "specialty.field_engineer.overdrive.operation_rate_steps",
    ),
    "POWER_OUTPUT": (
        "power_output_steps",
        "specialty.field_engineer.overdrive.power_output_steps",
    ),
}

_OVERDRIVE_COSTS = {
    "WEAR": ("wear_steps", "specialty.field_engineer.overdrive.wear_steps"),
    "DAMAGE": ("damage_cost", "specialty.field_engineer.overdrive.damage_cost"),
    "HEAT": ("heat_steps", "specialty.field_engineer.overdrive.heat_steps"),
    "POWER_DEPLETION": (
        "power_depletion_steps",
        "specialty.field_engineer.overdrive.power_depletion_steps",
    ),
}

_OVERDRIVE_BRANCHES = {
    f"{output_name}_{cost_name}": (
        {
            "operation": "set_parameter",
            "field": output_field,
            "parameter_id": output_parameter,
        },
        {
            "operation": "set",
            "field": "selected_output",
            "value": output_name,
        },
        {
            "operation": "set_parameter",
            "field": cost_field,
            "parameter_id": cost_parameter,
        },
        {
            "operation": "set",
            "field": "selected_cost",
            "value": cost_name,
        },
    )
    for output_name, (output_field, output_parameter) in _OVERDRIVE_OUTPUTS.items()
    for cost_name, (cost_field, cost_parameter) in _OVERDRIVE_COSTS.items()
}

# The general SPECIALTY rule makes authored alternatives exclusive. These
# adapters expose every stated branch while ensuring a single activation never
# receives two alternatives from the same choice.
SPECIALTY_MECHANICS.update(
    {
        ("BREAKER", "CONTROLLED BREACH"): {
            "mechanic_id": "exclusive_controlled_breach_result",
            "parameter_ids": (
                "specialty.breaker.controlled_breach.area_or_range_m_bonus",
                "specialty.breaker.controlled_breach.collateral_damage_reduction",
            ),
            "initial_state": {
                "area_or_range_m_bonus": 0,
                "collateral_damage_reduction": 0,
            },
            "prerequisites": {},
            "operations": (),
            "mechanical_branches": {
                "LARGER_OPENING": (
                    {
                        "operation": "set_parameter",
                        "field": "area_or_range_m_bonus",
                        "parameter_id": "specialty.breaker.controlled_breach.area_or_range_m_bonus",
                    },
                ),
                "REDUCED_COLLATERAL": (
                    {
                        "operation": "set_parameter",
                        "field": "collateral_damage_reduction",
                        "parameter_id": "specialty.breaker.controlled_breach.collateral_damage_reduction",
                    },
                ),
            },
            "default_branch": "LARGER_OPENING",
            "limitations": (),
        },
        ("BREAKER", "NO FIXED ENTRY"): {
            "mechanic_id": "exclusive_breach_shape",
            "parameter_ids": (
                "specialty.breaker.no_fixed_entry.ap_discount",
            ),
            "initial_state": {
                "ap_discount": 0,
                "breach_shape": None,
            },
            "prerequisites": {},
            "operations": (),
            "mechanical_branches": {
                branch: (
                    {
                        "operation": "set_parameter",
                        "field": "ap_discount",
                        "parameter_id": "specialty.breaker.no_fixed_entry.ap_discount",
                    },
                    {
                        "operation": "set",
                        "field": "breach_shape",
                        "value": branch,
                    },
                )
                for branch in ("PASSAGE", "FIRING_APERTURE")
            },
            "default_branch": "PASSAGE",
            "limitations": (),
        },
        ("FIELD ENGINEER", "HOT REPAIR"): {
            "mechanic_id": "exclusive_hot_repair_tradeoff",
            "parameter_ids": (
                "specialty.field_engineer.hot_repair.repair_effectiveness_penalty_steps",
                "specialty.field_engineer.hot_repair.risk_steps",
            ),
            "initial_state": {
                "repair_effectiveness_penalty_steps": 0,
                "risk_steps": 0,
                "selected_tradeoff": None,
            },
            "prerequisites": {},
            "operations": (),
            "mechanical_branches": {
                "LOWER_EFFECTIVENESS": (
                    {
                        "operation": "set_parameter",
                        "field": "repair_effectiveness_penalty_steps",
                        "parameter_id": "specialty.field_engineer.hot_repair.repair_effectiveness_penalty_steps",
                    },
                    {
                        "operation": "set",
                        "field": "selected_tradeoff",
                        "value": "LOWER_EFFECTIVENESS",
                    },
                ),
                "HIGHER_RISK": (
                    {
                        "operation": "set_parameter",
                        "field": "risk_steps",
                        "parameter_id": "specialty.field_engineer.hot_repair.risk_steps",
                    },
                    {
                        "operation": "set",
                        "field": "selected_tradeoff",
                        "value": "HIGHER_RISK",
                    },
                ),
            },
            "default_branch": "LOWER_EFFECTIVENESS",
            "limitations": (),
        },
        ("FIELD ENGINEER", "OVERDRIVE"): {
            "mechanic_id": "exclusive_overdrive_output_and_cost",
            "parameter_ids": tuple(
                parameter_id
                for _, parameter_id in (
                    *_OVERDRIVE_OUTPUTS.values(),
                    *_OVERDRIVE_COSTS.values(),
                )
            ),
            "initial_state": {
                **{
                    field: 0.0 if field.endswith("per_ap_bonus") else 0
                    for field, _ in _OVERDRIVE_OUTPUTS.values()
                },
                **{field: 0 for field, _ in _OVERDRIVE_COSTS.values()},
                "selected_output": None,
                "selected_cost": None,
            },
            "prerequisites": {},
            "operations": (),
            "mechanical_branches": _OVERDRIVE_BRANCHES,
            "default_branch": "MOVEMENT_WEAR",
            "limitations": (),
        },
        ("FIELD MEDIC", "BATTLEFIELD RECOVERY"): {
            "mechanic_id": "exclusive_battlefield_recovery_outcome",
            "parameter_ids": (
                "specialty.field_medic.battlefield_recovery.res_bonus",
                "specialty.field_medic.battlefield_recovery.short_term_survivability_bonus",
            ),
            "initial_state": {
                "res_bonus": 0,
                "short_term_survivability_bonus": 0,
            },
            "prerequisites": {},
            "operations": (),
            "mechanical_branches": {
                "RES": (
                    {
                        "operation": "set_parameter",
                        "field": "res_bonus",
                        "parameter_id": "specialty.field_medic.battlefield_recovery.res_bonus",
                    },
                ),
                "SHORT_TERM_SURVIVABILITY": (
                    {
                        "operation": "set_parameter",
                        "field": "short_term_survivability_bonus",
                        "parameter_id": "specialty.field_medic.battlefield_recovery.short_term_survivability_bonus",
                    },
                ),
            },
            "default_branch": "RES",
            "limitations": (),
        },
        ("FPV DRONES", "EVASIVE PILOTING"): {
            "mechanic_id": "exclusive_active_evasion_cost",
            "parameter_ids": (
                "specialty.fpv_drones.evasive_piloting.opponent_difficulty_offset",
                "specialty.fpv_drones.evasive_piloting.action_ap",
                "specialty.fpv_drones.evasive_piloting.movement_m_per_ap_penalty",
            ),
            "initial_state": {
                "opponent_difficulty_offset": 0,
                "action_ap": 0,
                "movement_m_per_ap_penalty": 0.0,
            },
            "prerequisites": {},
            "operations": (),
            "mechanical_branches": {
                "EXTRA_AP": (
                    {
                        "operation": "set_parameter",
                        "field": "opponent_difficulty_offset",
                        "parameter_id": "specialty.fpv_drones.evasive_piloting.opponent_difficulty_offset",
                    },
                    {
                        "operation": "set_parameter",
                        "field": "action_ap",
                        "parameter_id": "specialty.fpv_drones.evasive_piloting.action_ap",
                    },
                ),
                "REDUCED_MOVEMENT": (
                    {
                        "operation": "set_parameter",
                        "field": "opponent_difficulty_offset",
                        "parameter_id": "specialty.fpv_drones.evasive_piloting.opponent_difficulty_offset",
                    },
                    {
                        "operation": "set_parameter",
                        "field": "movement_m_per_ap_penalty",
                        "parameter_id": "specialty.fpv_drones.evasive_piloting.movement_m_per_ap_penalty",
                    },
                ),
            },
            "default_branch": "EXTRA_AP",
            "limitations": (),
        },
        ("HANDLER", "FAVOR LEDGER"): {
            "mechanic_id": "exclusive_favor_value",
            "parameter_ids": (
                "specialty.handler.favor_ledger.leverage_steps",
                "specialty.handler.favor_ledger.persistence_steps",
            ),
            "initial_state": {
                "leverage_steps": 0,
                "persistence_steps": 0,
            },
            "prerequisites": {},
            "operations": (),
            "mechanical_branches": {
                "STRENGTH": (
                    {
                        "operation": "set_parameter",
                        "field": "leverage_steps",
                        "parameter_id": "specialty.handler.favor_ledger.leverage_steps",
                    },
                ),
                "PERSISTENCE": (
                    {
                        "operation": "set_parameter",
                        "field": "persistence_steps",
                        "parameter_id": "specialty.handler.favor_ledger.persistence_steps",
                    },
                ),
            },
            "default_branch": "STRENGTH",
            "limitations": (),
        },
        ("RESUSCITATOR", "BRIDGE TO TRAUMA"): {
            "mechanic_id": "exclusive_definitive_treatment_setup",
            "parameter_ids": (
                "specialty.resuscitator.bridge_to_trauma.difficulty_offset",
                "specialty.resuscitator.bridge_to_trauma.ap_discount",
            ),
            "initial_state": {
                "difficulty_offset": 0,
                "ap_discount": 0,
            },
            "prerequisites": {},
            "operations": (),
            "mechanical_branches": {
                "EFFECTIVENESS": (
                    {
                        "operation": "set_parameter",
                        "field": "difficulty_offset",
                        "parameter_id": "specialty.resuscitator.bridge_to_trauma.difficulty_offset",
                    },
                ),
                "SETUP_TIME": (
                    {
                        "operation": "set_parameter",
                        "field": "ap_discount",
                        "parameter_id": "specialty.resuscitator.bridge_to_trauma.ap_discount",
                    },
                ),
            },
            "default_branch": "EFFECTIVENESS",
            "limitations": (),
        },
        ("SHIELD BEARER", "IMPACT CONTROL"): {
            "mechanic_id": "exclusive_shield_impact_consequence",
            "parameter_ids": (),
            "initial_state": {
                "failure_changes_position": False,
                "failure_changes_facing": False,
            },
            "prerequisites": {},
            "operations": (),
            "mechanical_branches": {
                "POSITION": (
                    {
                        "operation": "set",
                        "field": "failure_changes_position",
                        "value": True,
                    },
                ),
                "FACING": (
                    {
                        "operation": "set",
                        "field": "failure_changes_facing",
                        "value": True,
                    },
                ),
            },
            "default_branch": "POSITION",
            "limitations": (),
        },
        ("TRAUMA", "TRAUMA CARE"): {
            "mechanic_id": "exclusive_trauma_care_outcome",
            "parameter_ids": (
                "specialty.trauma.trauma_care.hp_restoration_bonus",
                "specialty.trauma.trauma_care.injury_recovery_steps",
            ),
            "initial_state": {
                "hp_restoration_bonus": 0,
                "injury_recovery_steps": 0,
            },
            "prerequisites": {},
            "operations": (),
            "mechanical_branches": {
                "HP_RESTORATION": (
                    {
                        "operation": "set_parameter",
                        "field": "hp_restoration_bonus",
                        "parameter_id": "specialty.trauma.trauma_care.hp_restoration_bonus",
                    },
                ),
                "INJURY_RECOVERY": (
                    {
                        "operation": "set_parameter",
                        "field": "injury_recovery_steps",
                        "parameter_id": "specialty.trauma.trauma_care.injury_recovery_steps",
                    },
                ),
            },
            "default_branch": "HP_RESTORATION",
            "limitations": (),
        },
        ("TRAUMA", "RECOVERY PLAN"): {
            "mechanic_id": "exclusive_recovery_plan_outcome",
            "parameter_ids": (
                "specialty.trauma.recovery_plan.time_steps_reduction",
                "specialty.trauma.recovery_plan.injury_recovery_steps",
            ),
            "initial_state": {
                "time_steps_reduction": 0,
                "injury_recovery_steps": 0,
            },
            "prerequisites": {},
            "operations": (),
            "mechanical_branches": {
                "FASTER": (
                    {
                        "operation": "set_parameter",
                        "field": "time_steps_reduction",
                        "parameter_id": "specialty.trauma.recovery_plan.time_steps_reduction",
                    },
                ),
                "MORE_EFFECTIVE": (
                    {
                        "operation": "set_parameter",
                        "field": "injury_recovery_steps",
                        "parameter_id": "specialty.trauma.recovery_plan.injury_recovery_steps",
                    },
                ),
            },
            "default_branch": "FASTER",
            "limitations": (),
        },
    }
)

# These nodes already had an authored structural adapter before their approved
# numeric magnitude was admitted.  Extend that adapter in place so the new
# balance value is exercised alongside (rather than instead of) the existing
# state transition and canonical constraints.
APPROVED_NUMERIC_ADAPTERS: dict[
    tuple[str, str], tuple[tuple[str, str], ...]
] = {
    ("ANCHOR", "ESTABLISH STRONGPOINT"): (
        ("action_ap", "specialty.anchor.establish_strongpoint.action_ap"),
    ),
    ("BREAKER", "FIRING PORT"): (
        ("ap_discount", "specialty.breaker.firing_port.ap_discount"),
    ),
    ("BREAKER", "WIDE BREACH"): (
        ("resource_cost", "specialty.breaker.wide_breach.resource_cost"),
    ),
    ("DIPLOMAT", "TERMS ON PAPER"): (
        ("leverage_steps", "specialty.diplomat.terms_on_paper.leverage_steps"),
    ),
    ("FLANKER", "SHADOW THE COVER"): (
        (
            "prepared_movement_m",
            "specialty.flanker.shadow_the_cover.prepared_movement_m",
        ),
    ),
    ("HANDLER", "INTRODUCTION"): (
        ("difficulty_offset", "specialty.handler.introduction.difficulty_offset"),
    ),
    ("INFILTRATOR", "APPROACH PLAN"): (
        (
            "difficulty_offset",
            "specialty.infiltrator.approach_plan.difficulty_offset",
        ),
    ),
    ("GUARDIAN", "INTERPOSE"): (
        ("prepared_movement_m", "specialty.guardian.interpose.prepared_movement_m"),
    ),
}

for adapter_key, bindings in APPROVED_NUMERIC_ADAPTERS.items():
    adapter = SPECIALTY_MECHANICS[adapter_key]
    existing_parameter_ids = tuple(adapter["parameter_ids"])
    admitted_parameter_ids = tuple(parameter_id for _, parameter_id in bindings)
    overlap = set(existing_parameter_ids) & set(admitted_parameter_ids)
    if overlap:
        raise RuntimeError(
            f"duplicate approved specialty parameter bindings for {adapter_key}: "
            f"{sorted(overlap)}"
        )
    adapter["parameter_ids"] = (*existing_parameter_ids, *admitted_parameter_ids)
    adapter["initial_state"] = {
        **adapter["initial_state"],
        **{field: 0 for field, _ in bindings},
    }
    adapter["operations"] = (
        *adapter["operations"],
        *(
            {
                "operation": "set_parameter",
                "field": field,
                "parameter_id": parameter_id,
            }
            for field, parameter_id in bindings
        ),
    )

# Canonical procedures whose state transition and failure boundaries are fully
# stated in the atomic specialty note. These need no balance magnitude or GM
# invention, so they remain outside the optimizer surface.
SPECIALTY_MECHANICS.update(
    {
        ("AUTODRONES", "LOCAL RESPONSE"): {
            "mechanic_id": "configured_local_response_slot",
            "parameter_ids": (
                "specialty.autodrones.local_response.count_bonus",
            ),
            "initial_state": {
                "count_bonus": 0,
                "condition_response_pair_stored": False,
            },
            "prerequisites": {},
            "operations": (
                {
                    "operation": "set_parameter",
                    "field": "count_bonus",
                    "parameter_id": "specialty.autodrones.local_response.count_bonus",
                },
                {
                    "operation": "set",
                    "field": "condition_response_pair_stored",
                    "value": True,
                },
            ),
            "limitations": (),
        },
        ("RESUSCITATOR", "SUSTAIN LIFE"): {
            "mechanic_id": "stabilization_retention",
            "parameter_ids": (),
            "initial_state": {
                "stabilization_retained_through_ordinary_handling": False,
            },
            "prerequisites": {},
            "operations": (
                {
                    "operation": "set",
                    "field": "stabilization_retained_through_ordinary_handling",
                    "value": True,
                },
            ),
            "limitations": (),
        },
        ("RESUSCITATOR", "MOVE THE CRITICAL"): {
            "mechanic_id": "transport_intervention_retention",
            "parameter_ids": (),
            "initial_state": {
                "intervention_retained_during_ordinary_transport": False,
            },
            "prerequisites": {},
            "operations": (
                {
                    "operation": "set",
                    "field": "intervention_retained_during_ordinary_transport",
                    "value": True,
                },
            ),
            "limitations": (),
        },
        ("SHOCK", "BREAK THE LINE"): {
            "mechanic_id": "cascading_assault_sequence",
            "parameter_ids": (),
            "initial_state": {
                "assault_chain_active": False,
                "next_advance_efficiency_refreshed": False,
                "next_disruption_efficiency_refreshed": False,
            },
            "prerequisites": {},
            "operations": (
                {
                    "operation": "set",
                    "field": "assault_chain_active",
                    "value": True,
                },
                {
                    "operation": "set",
                    "field": "next_advance_efficiency_refreshed",
                    "value": True,
                },
                {
                    "operation": "set",
                    "field": "next_disruption_efficiency_refreshed",
                    "value": True,
                },
            ),
            "limitations": (),
        },
    }
)

# FIREARM MANIPULATION grants the baseline physical permission. OPERATOR only
# reduces the admitted one-handed surcharge and can never undercut the ordinary
# two-handed operation cost or create a physically impossible workaround.
SPECIALTY_MECHANICS[("OPERATOR", "ONE-HANDED MANIPULATION")] = {
    "mechanic_id": "one_handed_firearm_manipulation_efficiency",
    "parameter_ids": (
        "specialty.operator.one_handed_manipulation.one_handed_surcharge_reduction_ap",
    ),
    "initial_state": {
        "baseline_surcharge_ap": {
            "parameter_id": "fire.one_handed_manipulation_surcharge_ap"
        },
        "one_handed_surcharge_reduction_ap": 0,
        "effective_surcharge_ap": {
            "parameter_id": "fire.one_handed_manipulation_surcharge_ap"
        },
        "ability_creates_baseline_permission": False,
        "final_cost_below_normal_two_handed_cost": False,
        "literal_two_hand_operation_without_workaround": False,
    },
    "prerequisites": {
        "one_handed_procedure_physically_possible": True,
    },
    "operations": (
        {
            "operation": "set_parameter",
            "field": "one_handed_surcharge_reduction_ap",
            "parameter_id": "specialty.operator.one_handed_manipulation.one_handed_surcharge_reduction_ap",
        },
        {
            "operation": "subtract_parameter",
            "field": "effective_surcharge_ap",
            "parameter_id": "specialty.operator.one_handed_manipulation.one_handed_surcharge_reduction_ap",
        },
    ),
    "limitations": (
        {
            "assertion": "field_equals",
            "field": "ability_creates_baseline_permission",
            "value": False,
        },
        {
            "assertion": "field_equals",
            "field": "final_cost_below_normal_two_handed_cost",
            "value": False,
        },
        {
            "assertion": "field_equals",
            "field": "literal_two_hand_operation_without_workaround",
            "value": False,
        },
    ),
}

_FIELD_MODIFICATION_CHARACTERISTICS = (
    "SENSOR_ORIENTATION",
    "MOUNTING",
    "STABILITY",
    "POWER_ROUTING",
    "OPERATING_MODE",
)
_PERFECT_SOLUTION_FIELDS = {
    "EXTREME_RANGE_DIFFICULTY": "extreme_range_difficulty_reduction_steps",
    "EXPOSED_AREA_DIFFICULTY": "exposed_area_difficulty_reduction_steps",
    "CALLED_SHOT_DIFFICULTY": "called_shot_difficulty_reduction_steps",
    "HIT_QUALITY": "hit_quality_steps",
    "TARGET_PENETRATION": "target_pen_bonus",
    "MATERIAL_PENETRATION": "material_pen_bonus",
}

# The remaining approved non-hacking boundaries are explicit procedures rather
# than prose-shaped bonuses. Every authored alternative is exposed as one
# exclusive branch, and branches never synthesize a missing payload or state.
SPECIALTY_MECHANICS.update(
    {
        ("ARSENAL", "ROLE PACKAGE"): {
            "mechanic_id": "configured_role_package",
            "parameter_ids": (
                "specialty.arsenal.role_package.ap_discount",
                "specialty.arsenal.role_package.burden_reduction",
            ),
            "initial_state": {
                "ap_discount": 0,
                "burden_reduction": 0,
                "package_benefit": "NONE",
            },
            "prerequisites": {},
            "operations": (),
            "mechanical_branches": {
                "ACCESS": (
                    {
                        "operation": "set_parameter",
                        "field": "ap_discount",
                        "parameter_id": "specialty.arsenal.role_package.ap_discount",
                    },
                    {"operation": "set", "field": "package_benefit", "value": "ACCESS"},
                ),
                "BURDEN": (
                    {
                        "operation": "set_parameter",
                        "field": "burden_reduction",
                        "parameter_id": "specialty.arsenal.role_package.burden_reduction",
                    },
                    {"operation": "set", "field": "package_benefit", "value": "BURDEN"},
                ),
            },
            "default_branch": "ACCESS",
            "limitations": (),
        },
        ("DIPLOMAT", "GRAND BARGAIN"): {
            "mechanic_id": "linked_term_setup",
            "parameter_ids": (
                "specialty.diplomat.grand_bargain.additional_term_setup_ap",
            ),
            "initial_state": {
                "additional_term_setup_ap": 0,
                "linked_terms_share_negotiation": False,
            },
            "prerequisites": {},
            "operations": (
                {
                    "operation": "set_parameter",
                    "field": "additional_term_setup_ap",
                    "parameter_id": "specialty.diplomat.grand_bargain.additional_term_setup_ap",
                },
                {
                    "operation": "set",
                    "field": "linked_terms_share_negotiation",
                    "value": True,
                },
            ),
            "limitations": (),
        },
        ("FIELD ENGINEER", "PATCH JOB"): {
            "mechanic_id": "temporary_function_patch",
            "parameter_ids": (
                "specialty.field_engineer.patch_job.ap_discount",
                "specialty.field_engineer.patch_job.patch_durability",
                "specialty.field_engineer.patch_job.use_count",
            ),
            "initial_state": {
                "ap_discount": 0,
                "named_function_restored": False,
                "patch_durability": 0,
                "remaining_uses": 0,
                "patch_liability": "NONE",
                "next_qualifying_failure_disables": False,
                "expires_after_meaningful_use": False,
            },
            "prerequisites": {},
            "operations": (),
            "mechanical_branches": {
                "FRAGILE": (
                    {
                        "operation": "set_parameter",
                        "field": "ap_discount",
                        "parameter_id": "specialty.field_engineer.patch_job.ap_discount",
                    },
                    {"operation": "set", "field": "named_function_restored", "value": True},
                    {
                        "operation": "set_parameter",
                        "field": "patch_durability",
                        "parameter_id": "specialty.field_engineer.patch_job.patch_durability",
                    },
                    {"operation": "set", "field": "patch_liability", "value": "FRAGILE"},
                    {"operation": "set", "field": "next_qualifying_failure_disables", "value": True},
                ),
                "ONE_USE": (
                    {
                        "operation": "set_parameter",
                        "field": "ap_discount",
                        "parameter_id": "specialty.field_engineer.patch_job.ap_discount",
                    },
                    {"operation": "set", "field": "named_function_restored", "value": True},
                    {
                        "operation": "set_parameter",
                        "field": "remaining_uses",
                        "parameter_id": "specialty.field_engineer.patch_job.use_count",
                    },
                    {"operation": "set", "field": "patch_liability", "value": "ONE_USE"},
                    {"operation": "set", "field": "expires_after_meaningful_use", "value": True},
                ),
            },
            "default_branch": "FRAGILE",
            "limitations": (),
        },
        ("FIELD ENGINEER", "FIELD MODIFICATION"): {
            "mechanic_id": "temporary_physical_configuration",
            "parameter_ids": (),
            "initial_state": {
                "temporary_physical_modification": False,
                "selected_characteristic": "NONE",
                "tradeoff_declared": False,
            },
            "prerequisites": {},
            "operations": (),
            "mechanical_branches": {
                characteristic: (
                    {
                        "operation": "set",
                        "field": "temporary_physical_modification",
                        "value": True,
                    },
                    {
                        "operation": "set",
                        "field": "selected_characteristic",
                        "value": characteristic,
                    },
                    {"operation": "set", "field": "tradeoff_declared", "value": True},
                )
                for characteristic in _FIELD_MODIFICATION_CHARACTERISTICS
            },
            "default_branch": "SENSOR_ORIENTATION",
            "limitations": (),
        },
        ("FIELD ENGINEER", "TECHNICAL OBSTACLE"): {
            "mechanic_id": "placed_denial_hardware",
            "parameter_ids": (
                "specialty.field_engineer.technical_obstacle.movement_cost_ap",
            ),
            "initial_state": {
                "movement_cost_ap": 0,
                "denial_mode": "NONE",
                "placed_hardware": False,
                "named_existing_hazard_payload": False,
            },
            "prerequisites": {},
            "operations": (),
            "mechanical_branches": {
                "OBSTACLE": (
                    {
                        "operation": "set_parameter",
                        "field": "movement_cost_ap",
                        "parameter_id": "specialty.field_engineer.technical_obstacle.movement_cost_ap",
                    },
                    {"operation": "set", "field": "denial_mode", "value": "OBSTACLE"},
                    {"operation": "set", "field": "placed_hardware", "value": True},
                ),
                "HAZARD": (
                    {"operation": "set", "field": "denial_mode", "value": "HAZARD"},
                    {"operation": "set", "field": "placed_hardware", "value": True},
                    {
                        "operation": "set",
                        "field": "named_existing_hazard_payload",
                        "value": True,
                    },
                ),
            },
            "default_branch": "OBSTACLE",
            "limitations": (),
        },
        ("FIELD ENGINEER", "REDUNDANT SYSTEM"): {
            "mechanic_id": "named_function_redundancy",
            "parameter_ids": (
                "specialty.field_engineer.redundant_system.backup_effectiveness_penalty_steps",
            ),
            "initial_state": {
                "named_function_selected": False,
                "first_component_failure_disables_function": True,
                "backup_effectiveness_penalty_steps": 0,
            },
            "prerequisites": {},
            "operations": (
                {"operation": "set", "field": "named_function_selected", "value": True},
                {
                    "operation": "set",
                    "field": "first_component_failure_disables_function",
                    "value": False,
                },
                {
                    "operation": "set_parameter",
                    "field": "backup_effectiveness_penalty_steps",
                    "parameter_id": "specialty.field_engineer.redundant_system.backup_effectiveness_penalty_steps",
                },
            ),
            "limitations": (),
        },
        ("HANDLER", "PROTECT THE SOURCE"): {
            "mechanic_id": "contact_chain_containment",
            "parameter_ids": (
                "specialty.handler.protect_the_source.ap_discount",
                "specialty.handler.protect_the_source.count_bonus",
            ),
            "initial_state": {
                "ap_discount": 0,
                "count_bonus": 0,
            },
            "prerequisites": {},
            "operations": (
                {
                    "operation": "set_parameter",
                    "field": "ap_discount",
                    "parameter_id": "specialty.handler.protect_the_source.ap_discount",
                },
                {
                    "operation": "set_parameter",
                    "field": "count_bonus",
                    "parameter_id": "specialty.handler.protect_the_source.count_bonus",
                },
            ),
            "limitations": (),
        },
        ("SNIPER", "PERFECT SOLUTION"): {
            "mechanic_id": "exclusive_final_solution_allocation",
            "parameter_ids": (
                "specialty.sniper.perfect_solution.action_ap",
                "specialty.sniper.perfect_solution.solution_steps",
            ),
            "initial_state": {
                "action_ap": 0,
                "solution_steps": 0,
                **{field: 0 for field in _PERFECT_SOLUTION_FIELDS.values()},
            },
            "prerequisites": {},
            "operations": (),
            "mechanical_branches": {
                branch: (
                    {
                        "operation": "set_parameter",
                        "field": "action_ap",
                        "parameter_id": "specialty.sniper.perfect_solution.action_ap",
                    },
                    {
                        "operation": "set_parameter",
                        "field": "solution_steps",
                        "parameter_id": "specialty.sniper.perfect_solution.solution_steps",
                    },
                    {
                        "operation": "set_parameter",
                        "field": field,
                        "parameter_id": "specialty.sniper.perfect_solution.solution_steps",
                    },
                )
                for branch, field in _PERFECT_SOLUTION_FIELDS.items()
            },
            "default_branch": "EXTREME_RANGE_DIFFICULTY",
            "branch_prerequisites": {
                "MATERIAL_PENETRATION": {"material_window_applies": True},
            },
            "limitations": (
                {
                    "assertion": "sum_equals_field",
                    "fields": tuple(_PERFECT_SOLUTION_FIELDS.values()),
                    "reference_field": "solution_steps",
                },
            ),
        },
    }
)

# Hacking specialty procedures operate on the canonical NODE/LINK topology and
# HACKING VISIBILITY bands. These fixtures expose the exact state transition;
# they do not invent connectivity, credential validity, or automatic detection.
SPECIALTY_MECHANICS.update(
    {
        ("LOCAL HACKER", "QUIET CONSOLE"): {
            "mechanic_id": "local_hacking_visibility_shift",
            "parameter_ids": (
                "specialty.local_hacker.quiet_console.visibility_band_shift",
            ),
            "initial_state": {
                "visibility_change_direction": "UNCHANGED",
                "visibility_band_shift": 0,
            },
            "prerequisites": {},
            "operations": (
                {
                    "operation": "set",
                    "field": "visibility_change_direction",
                    "value": "LOWER",
                },
                {
                    "operation": "set_parameter",
                    "field": "visibility_band_shift",
                    "parameter_id": "specialty.local_hacker.quiet_console.visibility_band_shift",
                },
            ),
            "limitations": (),
        },
        ("LOCAL HACKER", "SNAPSHOT"): {
            "mechanic_id": "node_snapshot_capture_compare_restore",
            "parameter_ids": (
                "specialty.local_hacker.snapshot.ap_discount",
            ),
            "initial_state": {
                "configuration_snapshot": False,
                "comparison_ap_discount": 0,
                "restore_ap_discount": 0,
            },
            "prerequisites": {},
            "operations": (
                {
                    "operation": "set",
                    "field": "configuration_snapshot",
                    "value": True,
                },
                {
                    "operation": "set_parameter",
                    "field": "comparison_ap_discount",
                    "parameter_id": "specialty.local_hacker.snapshot.ap_discount",
                },
                {
                    "operation": "set_parameter",
                    "field": "restore_ap_discount",
                    "parameter_id": "specialty.local_hacker.snapshot.ap_discount",
                },
            ),
            "limitations": (),
        },
        ("LOCAL HACKER", "HARD LOCKOUT"): {
            "mechanic_id": "exclusive_node_lockout_action",
            "parameter_ids": (),
            "initial_state": {
                "active_remote_sessions_invalidated": False,
                "selected_access_method_revoked": False,
            },
            "prerequisites": {},
            "operations": (),
            "mechanical_branches": {
                "INVALIDATE_SESSIONS": (
                    {
                        "operation": "set",
                        "field": "active_remote_sessions_invalidated",
                        "value": True,
                    },
                ),
                "REVOKE_ACCESS_METHOD": (
                    {
                        "operation": "set",
                        "field": "selected_access_method_revoked",
                        "value": True,
                    },
                ),
            },
            "default_branch": "INVALIDATE_SESSIONS",
            "branch_prerequisites": {
                "REVOKE_ACCESS_METHOD": {
                    "selected_remote_access_method_currently_valid": True,
                },
            },
            "limitations": (),
        },
        ("LOCAL HACKER", "ROOT ON THE BOX"): {
            "mechanic_id": "independent_local_root_slots",
            "parameter_ids": (
                "specialty.local_hacker.root_on_the_box.ap_discount",
            ),
            "initial_state": {"ap_discount": 0},
            "prerequisites": {},
            "operations": (
                {
                    "operation": "set_parameter",
                    "field": "ap_discount",
                    "parameter_id": "specialty.local_hacker.root_on_the_box.ap_discount",
                },
            ),
            "limitations": (),
        },
        ("NETWORKER", "QUIET ENUMERATION"): {
            "mechanic_id": "network_discovery_visibility_shift",
            "parameter_ids": (
                "specialty.networker.quiet_enumeration.visibility_band_shift",
            ),
            "initial_state": {
                "visibility_change_direction": "UNCHANGED",
                "visibility_band_shift": 0,
            },
            "prerequisites": {},
            "operations": (
                {
                    "operation": "set",
                    "field": "visibility_change_direction",
                    "value": "LOWER",
                },
                {
                    "operation": "set_parameter",
                    "field": "visibility_band_shift",
                    "parameter_id": "specialty.networker.quiet_enumeration.visibility_band_shift",
                },
            ),
            "limitations": (),
        },
        ("NETWORKER", "CREDENTIAL SWEEP"): {
            "mechanic_id": "credential_candidate_set",
            "parameter_ids": (
                "specialty.networker.credential_sweep.action_ap",
            ),
            "initial_state": {
                "action_ap": 0,
                "plausible_candidate_set_identified": False,
            },
            "prerequisites": {},
            "operations": (
                {
                    "operation": "set_parameter",
                    "field": "action_ap",
                    "parameter_id": "specialty.networker.credential_sweep.action_ap",
                },
                {
                    "operation": "set",
                    "field": "plausible_candidate_set_identified",
                    "value": True,
                },
            ),
            "limitations": (),
        },
        ("NETWORKER", "GATEWAY PIVOT"): {
            "mechanic_id": "gateway_entry_topology_discovery",
            "parameter_ids": (
                "specialty.networker.gateway_pivot.ap_discount",
            ),
            "initial_state": {
                "first_further_discovery_ap_discount": 0,
            },
            "prerequisites": {},
            "operations": (
                {
                    "operation": "set_parameter",
                    "field": "first_further_discovery_ap_discount",
                    "parameter_id": "specialty.networker.gateway_pivot.ap_discount",
                },
            ),
            "limitations": (),
        },
        ("NETWORKER", "PARALLEL PROBE"): {
            "mechanic_id": "parallel_topology_probe",
            "parameter_ids": (
                "specialty.networker.parallel_probe.count_bonus",
                "specialty.networker.parallel_probe.visibility_band_shift",
            ),
            "initial_state": {
                "additional_directly_reachable_targets": 0,
                "visibility_change_direction": "UNCHANGED",
                "visibility_band_shift": 0,
            },
            "prerequisites": {},
            "operations": (
                {
                    "operation": "set_parameter",
                    "field": "additional_directly_reachable_targets",
                    "parameter_id": "specialty.networker.parallel_probe.count_bonus",
                },
                {
                    "operation": "set",
                    "field": "visibility_change_direction",
                    "value": "RAISE",
                },
                {
                    "operation": "set_parameter",
                    "field": "visibility_band_shift",
                    "parameter_id": "specialty.networker.parallel_probe.visibility_band_shift",
                },
            ),
            "limitations": (),
        },
        ("NETWORKER", "ROUTE PERSISTENCE"): {
            "mechanic_id": "single_downstream_route_switch",
            "parameter_ids": (
                "specialty.networker.route_persistence.ap_discount",
            ),
            "initial_state": {
                "ap_discount": 0,
                "one_downstream_connection_switched": False,
            },
            "prerequisites": {},
            "operations": (
                {
                    "operation": "set_parameter",
                    "field": "ap_discount",
                    "parameter_id": "specialty.networker.route_persistence.ap_discount",
                },
                {
                    "operation": "set",
                    "field": "one_downstream_connection_switched",
                    "value": True,
                },
            ),
            "limitations": (),
        },
        ("NETWORKER", "CASCADE ACCESS"): {
            "mechanic_id": "exclusive_shared_weakness_benefit",
            "parameter_ids": (
                "specialty.networker.cascade_access.ap_discount",
                "specialty.networker.cascade_access.difficulty_offset",
            ),
            "initial_state": {
                "ap_discount": 0,
                "difficulty_offset": 0,
            },
            "prerequisites": {},
            "operations": (),
            "mechanical_branches": {
                "FASTER": (
                    {
                        "operation": "set_parameter",
                        "field": "ap_discount",
                        "parameter_id": "specialty.networker.cascade_access.ap_discount",
                    },
                ),
                "EASIER": (
                    {
                        "operation": "set_parameter",
                        "field": "difficulty_offset",
                        "parameter_id": "specialty.networker.cascade_access.difficulty_offset",
                    },
                ),
            },
            "default_branch": "FASTER",
            "limitations": (),
        },
        ("NETWORKER", "REROUTE"): {
            "mechanic_id": "multi_node_route_recalculation",
            "parameter_ids": (
                "specialty.networker.reroute.ap_discount",
            ),
            "initial_state": {
                "ap_discount": 0,
                "connection_chain_recalculated": False,
            },
            "prerequisites": {},
            "operations": (
                {
                    "operation": "set_parameter",
                    "field": "ap_discount",
                    "parameter_id": "specialty.networker.reroute.ap_discount",
                },
                {
                    "operation": "set",
                    "field": "connection_chain_recalculated",
                    "value": True,
                },
            ),
            "limitations": (),
        },
        ("NETWORKER", "OWN THE TOPOLOGY"): {
            "mechanic_id": "persistent_topology_and_live_reachability",
            "parameter_ids": (
                "specialty.networker.own_the_topology.ap_discount",
            ),
            "initial_state": {
                "reachable_infrastructure_ap_discount": 0,
                "current_reachability_recalculated": False,
            },
            "prerequisites": {},
            "operations": (
                {
                    "operation": "set_parameter",
                    "field": "reachable_infrastructure_ap_discount",
                    "parameter_id": "specialty.networker.own_the_topology.ap_discount",
                },
                {
                    "operation": "set",
                    "field": "current_reachability_recalculated",
                    "value": True,
                },
            ),
            "limitations": (),
        },
    }
)

BASELINE_DUPLICATION_SUSPECTS: dict[tuple[str, str], str] = {}

AMBIGUOUS_NODES: dict[tuple[str, str], str] = {}


MISSING_DEPENDENCY_NODES: dict[tuple[str, str], tuple[str, str]] = {}

NUMERIC_LANGUAGE = re.compile(
    r"\b(less|faster|sooner|quicker|quickly|rapid|rapidly|efficient|efficiently|harder|easier|more|improve|improves|improved|increasing|increases|reducing|reduces|effectiveness|increase|reduce|reduced|additional|larger|broader|farther|longer|shorter|greater|stronger|beyond|substantially|dramatically|extra|bonus|penalty|difficulty|discount|costs? less|requires? less)\b",
    re.IGNORECASE,
)


def classify_node(node: SpecialtyNode, parameters: ParameterSet) -> NodeCoverage:
    key = (node.specialty, re.sub(r"\s+—\s+UNRESOLVED.*$", "", node.name, flags=re.IGNORECASE).strip())
    upper_name = node.name.upper()
    upper_evidence = f"{node.name} {node.source_excerpt}".upper()
    if "UNRESOLVED" in upper_name:
        duplicated = "BASELINE" in upper_evidence or "DUPLICAT" in upper_evidence
        node_findings = (
            (
                {"code": "BASELINE DUPLICATION", "severity": "REVIEW", "node_id": node.node_id, "evidence": node.source_excerpt},
                {"code": "DEAD NODE", "severity": "REVIEW", "node_id": node.node_id, "evidence": "the retired behavior has no specialty delta"},
            )
            if duplicated
            else ({"code": "UNIMPLEMENTABLE SPEC GAP", "severity": "REVIEW", "node_id": node.node_id, "evidence": node.source_excerpt},)
        )
        return NodeCoverage(
            node,
            CoverageState.UNRESOLVED_SOURCE,
            None,
            (),
            ("explicit UNRESOLVED source slot",),
            node_findings,
            None,
        )
    if key in BASELINE_DUPLICATION_SUSPECTS:
        evidence = BASELINE_DUPLICATION_SUSPECTS[key]
        code = (
            "PERMISSION PERK"
            if key in {("SHIELD BEARER", "SHIELD TRAINING")}
            else "BASELINE DUPLICATION"
        )
        return NodeCoverage(
            node,
            CoverageState.STRUCTURAL_FINDING,
            None,
            (),
            ("baseline counterfactual has no separate advantage",),
            (
                {"code": code, "severity": "REVIEW", "node_id": node.node_id, "evidence": evidence},
                {"code": "DEAD NODE", "severity": "REVIEW", "node_id": node.node_id, "evidence": "same-character present/absent counterfactual has no separate mechanical delta"},
            ),
            None,
        )
    if key in AMBIGUOUS_NODES:
        return NodeCoverage(
            node,
            CoverageState.AMBIGUOUS_SOURCE,
            None,
            (),
            (AMBIGUOUS_NODES[key],),
            ({"code": "AMBIGUOUS NODE", "severity": "REVIEW", "node_id": node.node_id, "evidence": AMBIGUOUS_NODES[key]},),
            None,
        )
    if key in MISSING_DEPENDENCY_NODES:
        evidence, code = MISSING_DEPENDENCY_NODES[key]
        return NodeCoverage(
            node,
            CoverageState.MISSING_DEPENDENCY,
            None,
            (),
            (evidence,),
            ({"code": code, "severity": "REVIEW", "node_id": node.node_id, "evidence": evidence},),
            None,
        )
    if key in SPECIALTY_MECHANICS:
        mechanic = str(SPECIALTY_MECHANICS[key]["mechanic_id"])
        parameter_ids = tuple(SPECIALTY_MECHANICS[key]["parameter_ids"])
        missing = tuple(parameter_id for parameter_id in parameter_ids if parameter_id not in parameters.manifest.specs)
        if missing:
            return NodeCoverage(
                node,
                CoverageState.MISSING_DEPENDENCY,
                mechanic,
                parameter_ids,
                missing,
                ({"code": "UNIMPLEMENTABLE SPEC GAP", "severity": "REVIEW", "node_id": node.node_id, "evidence": f"unregistered parameter IDs: {missing}"},),
                None,
            )
        state = CoverageState.IMPLEMENTED_WITH_PARAMETERS if parameter_ids else CoverageState.IMPLEMENTED
        return NodeCoverage(node, state, mechanic, parameter_ids, (), (), f"CF-{node.node_id}")
    admitted = SPECIALTY_PARAMETERS_BY_NODE.get(key, ())
    if key == ("AUTODRONES", "REACQUIRE"):
        admitted = ("specialty.autodrones.reacquire.search_radius_m",)
    elif key == ("AUTODRONES", "MACHINE SQUAD"):
        admitted = (
            "specialty.autodrones.machine_squad.additional_drones",
            "specialty.autodrones.machine_squad.group_command_ap",
            "specialty.autodrones.machine_squad.contingency_count",
        )
    if admitted:
        missing = tuple(parameter_id for parameter_id in admitted if parameter_id not in parameters.manifest.specs)
        if missing:
            return NodeCoverage(
                node,
                CoverageState.MISSING_DEPENDENCY,
                "parameterized_specialty_delta",
                admitted,
                missing,
                ({"code": "UNIMPLEMENTABLE SPEC GAP", "severity": "REVIEW", "node_id": node.node_id, "evidence": f"unregistered parameter IDs: {missing}"},),
                None,
            )
        return NodeCoverage(node, CoverageState.IMPLEMENTED_WITH_PARAMETERS, "parameterized_specialty_delta", admitted, (), (), f"CF-{node.node_id}")
    if SPECIALTY_RULE_CONTRACTS[key]["effects"]:
        return NodeCoverage(
            node,
            CoverageState.IMPLEMENTED,
            "authored_structural_delta",
            (),
            (),
            (),
            f"CF-{node.node_id}",
        )
    if NUMERIC_LANGUAGE.search(node.source_excerpt):
        expected_prefix = f"specialty.{_slug(node.specialty)}.{_slug(node.name)}."
        admitted = tuple(
            parameter_id
            for parameter_id in parameters.manifest.specs
            if parameter_id.startswith(expected_prefix)
            and parameter_id not in REJECTED_SPECIALTY_PARAMETER_IDS
            and parameter_id not in DEFERRED_SPECIALTY_PARAMETER_IDS
        )
        if not admitted:
            evidence = "clear specialty direction requires a numeric magnitude, but BALANCE PARAMETERS admits no stable specialty.* ID"
            return NodeCoverage(
                node,
                CoverageState.MISSING_DEPENDENCY,
                None,
                (),
                ("specialty parameter admission",),
                ({"code": "UNIMPLEMENTABLE SPEC GAP", "subtype": "PARAMETER_ADMISSION_GAP", "severity": "REVIEW", "node_id": node.node_id, "evidence": evidence},),
                None,
            )
    evidence = "source direction cannot be executed because its required operational procedure, state, or magnitude is not defined by an executable dependency"
    return NodeCoverage(
        node,
        CoverageState.MISSING_DEPENDENCY,
        None,
        (),
        ("deterministic operational procedure",),
        ({"code": "UNIMPLEMENTABLE SPEC GAP", "severity": "REVIEW", "node_id": node.node_id, "evidence": evidence},),
        None,
    )


def _with_specialty_audit_finding(coverage: NodeCoverage) -> NodeCoverage:
    key = (coverage.node.specialty, coverage.node.name)
    evidence = SPECIALTY_MECHANICAL_GAPS.get(key)
    if evidence is None:
        return coverage
    subtype = (
        "AUTHORED_ALTERNATIVE_PROCEDURE"
        if key in AUTHORED_ALTERNATIVE_NODES
        else "MISSING_MECHANICAL_DELTA"
    )
    finding = {
        "code": "UNIMPLEMENTABLE SPEC GAP",
        "subtype": subtype,
        "severity": "REVIEW",
        "node_id": coverage.node.node_id,
        "evidence": evidence,
        "partial_implementation": coverage.counterfactual_id is not None,
        "requires_human_design": True,
        "decision_track": (
            "HACKING_SEPARATE"
            if coverage.node.specialty in {"LOCAL HACKER", "NETWORKER"}
            else "GENERAL"
        ),
    }
    retained = tuple(
        item
        for item in coverage.findings
        if item.get("code") != "UNIMPLEMENTABLE SPEC GAP"
    )
    return replace(coverage, findings=(*retained, finding))


def _parameterized_mechanic_spec(coverage: NodeCoverage) -> dict[str, Any]:
    """Build the adapter declared by a node's admitted specialty parameters."""
    authored_rule_contract = _compile_authored_rule_contract(coverage.node)
    initial_state: dict[str, Any] = {}
    operations: list[dict[str, Any]] = []
    for parameter_id in coverage.parameter_ids:
        field = parameter_id.rsplit(".", 1)[-1]
        initial_state[field] = 1.0 if field.endswith("multiplier") else 0
        operations.append(
            {
                "operation": "set_parameter",
                "field": field,
                "parameter_id": parameter_id,
            }
        )
    for item in authored_rule_contract["effect_clauses"]:
        field = str(item["field"])
        if field in initial_state and initial_state[field] != item["control"]:
            raise ValueError(
                f"authored effect field collides with parameter state for {coverage.node.node_id}: {field}"
            )
        initial_state[field] = deepcopy(item["control"])
        operations.append(
            {
                "operation": "set",
                "field": field,
                "value": deepcopy(item["value"]),
                "source_clause": str(item["source_clause"]),
            }
        )
    for item in authored_rule_contract["constraint_clauses"]:
        field = str(item["field"])
        if field in initial_state:
            raise ValueError(
                f"authored constraint field collides with parameter state for {coverage.node.node_id}: {field}"
            )
        initial_state[field] = deepcopy(item["expected"])
    return {
        "mechanic_id": coverage.mechanic_id or "parameterized_specialty_delta",
        "parameter_ids": coverage.parameter_ids,
        "initial_state": initial_state,
        "prerequisites": {
            str(item["field"]): deepcopy(item["expected"])
            for item in authored_rule_contract["activation_clauses"]
        },
        "operations": tuple(operations),
        "limitations": tuple(
            {
                "assertion": "field_equals",
                "field": str(item["field"]),
                "value": deepcopy(item["expected"]),
                "kind": str(item["kind"]),
                "enforcement": str(item["enforcement"]),
                "source_clause": str(item["source_clause"]),
            }
            for item in authored_rule_contract["constraint_clauses"]
        ),
        "authored_rule_contract": authored_rule_contract,
    }


def _resolve_fixture_value(value: Any, parameters: ParameterSet) -> Any:
    if isinstance(value, dict) and set(value) == {"parameter_id"}:
        return deepcopy(parameters.values[str(value["parameter_id"])])
    return deepcopy(value)


def _materialize_operations(
    operations: tuple[dict[str, Any], ...],
    parameters: ParameterSet,
) -> list[dict[str, Any]]:
    materialized: list[dict[str, Any]] = []
    for source_operation in operations:
        operation = deepcopy(source_operation)
        operation_type = str(operation["operation"])
        parameter_id = operation.get("parameter_id")
        if parameter_id is not None:
            parameter_value = deepcopy(parameters.values[str(parameter_id)])
            if operation_type == "set_parameter":
                operation["value"] = parameter_value
            elif operation_type in {"add_parameter", "subtract_parameter"}:
                operation["amount"] = parameter_value
            else:
                raise ValueError(f"unsupported parameter-backed specialty operation {operation_type}")
        materialized.append(operation)
    return materialized


def _apply_specialty_operations(
    initial_state: dict[str, Any],
    operations: list[dict[str, Any]],
) -> dict[str, Any]:
    result = deepcopy(initial_state)
    for operation in operations:
        operation_type = str(operation["operation"])
        field = str(operation["field"])
        if field not in result:
            raise ValueError(f"specialty operation targets missing state field {field}")
        if operation_type in {"set", "set_parameter"}:
            result[field] = deepcopy(operation["value"])
        elif operation_type in {"add", "add_parameter"}:
            result[field] += operation["amount"]
        elif operation_type in {"subtract", "subtract_parameter"}:
            result[field] -= operation["amount"]
        else:
            raise ValueError(f"unsupported specialty operation {operation_type}")
    return result


def _evaluate_limitations(
    control: dict[str, Any],
    candidate: dict[str, Any],
    contract: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for assertion in contract:
        assertion_type = str(assertion["assertion"])
        if assertion_type == "field_equals":
            field = str(assertion["field"])
            expected = assertion["value"]
            actual = candidate.get(field)
            passed = actual == expected
        elif assertion_type == "sum_preserved":
            fields = [str(field) for field in assertion["fields"]]
            expected = sum(control[field] for field in fields)
            actual = sum(candidate[field] for field in fields)
            passed = actual == expected
        elif assertion_type == "sum_equals_field":
            fields = [str(field) for field in assertion["fields"]]
            reference_field = str(assertion["reference_field"])
            expected = candidate[reference_field]
            actual = sum(candidate[field] for field in fields)
            passed = actual == expected
        else:
            raise ValueError(f"unsupported specialty limitation assertion {assertion_type}")
        results.append(
            {
                **deepcopy(assertion),
                "expected": expected,
                "actual": actual,
                "passed": passed,
            }
        )
    return results


def node_counterfactual(coverage: NodeCoverage, parameters: ParameterSet) -> dict[str, Any] | None:
    if not coverage.counterfactual_id:
        return None
    key = (
        coverage.node.specialty,
        re.sub(r"\s+—\s+UNRESOLVED.*$", "", coverage.node.name, flags=re.IGNORECASE).strip(),
    )
    spec = deepcopy(SPECIALTY_MECHANICS.get(key) or _parameterized_mechanic_spec(coverage))
    parameter_ids = tuple(str(item) for item in spec["parameter_ids"])
    initial_state = {
        str(field): _resolve_fixture_value(value, parameters)
        for field, value in spec["initial_state"].items()
    }
    if key == ("ICON", "RECOGNITION"):
        initial_state["setup_ap"] = parameters.values["social.fact_to_leverage_setup_ap"]
    authored_rule_contract = deepcopy(
        spec.get("authored_rule_contract")
        or _compile_authored_rule_contract(coverage.node)
    )
    authored_effect_fields: list[str] = []
    for item in authored_rule_contract["effect_clauses"]:
        field = str(item["field"])
        authored_effect_fields.append(field)
        control_value = deepcopy(item["control"])
        if field in initial_state and initial_state[field] != control_value:
            raise ValueError(
                f"{coverage.node.node_id} has conflicting effect control state {field}"
            )
        initial_state[field] = control_value
    authored_constraint_fields: list[str] = []
    authored_guard_fields: list[str] = []
    authored_termination_fields: list[str] = []
    authored_invariant_fields: list[str] = []
    for item in authored_rule_contract["constraint_clauses"]:
        field = str(item["field"])
        authored_constraint_fields.append(field)
        enforcement = str(item["enforcement"])
        if enforcement == "GUARD":
            authored_guard_fields.append(field)
        elif enforcement == "TERMINATION":
            authored_termination_fields.append(field)
        elif enforcement == "INVARIANT":
            authored_invariant_fields.append(field)
        else:
            raise ValueError(
                f"{coverage.node.node_id} has unclassified constraint {field}"
            )
        expected = deepcopy(item["expected"])
        if field in initial_state and initial_state[field] != expected:
            raise ValueError(
                f"{coverage.node.node_id} has conflicting constraint state {field}"
            )
        initial_state[field] = expected
    prerequisites = {
        str(field): deepcopy(value) for field, value in spec["prerequisites"].items()
    }
    for clause in authored_rule_contract["activation_clauses"]:
        field = str(clause["field"])
        expected = deepcopy(clause["expected"])
        if field in prerequisites and prerequisites[field] != expected:
            raise ValueError(
                f"{coverage.node.node_id} has conflicting prerequisite {field}"
            )
        prerequisites[field] = expected
    branch_specs = spec.get("mechanical_branches")
    default_branch = str(spec.get("default_branch", "DEFAULT"))
    if branch_specs:
        mechanical_branches = {
            str(branch): _materialize_operations(tuple(branch_operations), parameters)
            for branch, branch_operations in branch_specs.items()
        }
        if default_branch not in mechanical_branches:
            raise ValueError(
                f"{coverage.node.node_id} default branch {default_branch} is not declared"
            )
    else:
        default_branch = "DEFAULT"
        mechanical_branches = {
            default_branch: _materialize_operations(
                tuple(spec["operations"]), parameters
            )
        }
    declared_branch_prerequisites = {
        str(branch): {
            str(field): deepcopy(expected)
            for field, expected in branch_contract.items()
        }
        for branch, branch_contract in spec.get("branch_prerequisites", {}).items()
    }
    unknown_branch_prerequisites = sorted(
        set(declared_branch_prerequisites) - set(mechanical_branches)
    )
    if unknown_branch_prerequisites:
        raise ValueError(
            f"{coverage.node.node_id} declares prerequisites for unknown branches "
            f"{unknown_branch_prerequisites}"
        )
    branch_prerequisite_contract = {
        branch: deepcopy(declared_branch_prerequisites.get(branch, {}))
        for branch in mechanical_branches
    }
    for branch, branch_operations in mechanical_branches.items():
        operation_fields = {
            str(operation["field"]) for operation in branch_operations
        }
        for item in authored_rule_contract["effect_clauses"]:
            field = str(item["field"])
            if field not in operation_fields:
                branch_operations.append(
                    {
                        "operation": "set",
                        "field": field,
                        "value": deepcopy(item["value"]),
                        "source_clause": str(item["source_clause"]),
                    }
                )
                operation_fields.add(field)
        if not branch_operations:
            raise ValueError(
                f"{coverage.node.node_id} branch {branch} has no mechanical operation"
            )
    mechanical_branches = {
        branch: tuple(branch_operations)
        for branch, branch_operations in mechanical_branches.items()
    }
    operations = mechanical_branches[default_branch]
    limitation_contract = [deepcopy(item) for item in spec["limitations"]]
    declared_limitations = {
        (str(item["assertion"]), str(item.get("field", "")))
        for item in limitation_contract
    }
    for item in authored_rule_contract["constraint_clauses"]:
        identity = ("field_equals", str(item["field"]))
        if identity not in declared_limitations:
            limitation_contract.append(
                {
                    "assertion": "field_equals",
                    "field": str(item["field"]),
                    "value": deepcopy(item["expected"]),
                    "kind": str(item["kind"]),
                    "enforcement": str(item["enforcement"]),
                    "source_clause": str(item["source_clause"]),
                }
            )
            declared_limitations.add(identity)
        else:
            existing = next(
                record
                for record in limitation_contract
                if (
                    str(record["assertion"]),
                    str(record.get("field", "")),
                )
                == identity
            )
            if existing.get("value") != item["expected"]:
                raise ValueError(
                    f"{coverage.node.node_id} has conflicting limitation {item['field']}"
                )
            existing["kind"] = str(item["kind"])
            existing["enforcement"] = str(item["enforcement"])
            existing["source_clause"] = str(item["source_clause"])
    branch_fixtures: dict[str, dict[str, Any]] = {}
    for branch, branch_operations in mechanical_branches.items():
        branch_result = _apply_specialty_operations(
            initial_state, list(branch_operations)
        )
        for item in authored_rule_contract["effect_clauses"]:
            field = str(item["field"])
            if branch_result.get(field) != item["value"]:
                raise ValueError(
                    f"{coverage.node.node_id} branch {branch} does not execute "
                    f"authored effect {field}"
                )
        branch_changed_fields: list[str] = []
        for operation in branch_operations:
            field = str(operation["field"])
            if (
                initial_state.get(field) != branch_result.get(field)
                and field not in branch_changed_fields
            ):
                branch_changed_fields.append(field)
        branch_limitation_results = _evaluate_limitations(
            initial_state,
            branch_result,
            limitation_contract,
        )
        if not branch_changed_fields:
            raise ValueError(
                f"{coverage.node.node_id} branch {branch} has no mechanical delta"
            )
        if not all(item["passed"] for item in branch_limitation_results):
            raise ValueError(
                f"{coverage.node.node_id} branch {branch} violates its canonical limits"
            )
        branch_fixtures[branch] = {
            "branch_id": branch,
            "mechanical_operations": branch_operations,
            "branch_prerequisite_contract": deepcopy(
                branch_prerequisite_contract[branch]
            ),
            "result": branch_result,
            "changed_fields": branch_changed_fields,
            "limitation_results": branch_limitation_results,
            "limitations_preserved": True,
        }
    applied_state = deepcopy(branch_fixtures[default_branch]["result"])
    changed_fields = list(branch_fixtures[default_branch]["changed_fields"])
    limitation_results = deepcopy(
        branch_fixtures[default_branch]["limitation_results"]
    )
    negative_prerequisite_fixture = deepcopy(prerequisites)
    if prerequisites:
        first_prerequisite = sorted(prerequisites)[0]
        expected = prerequisites[first_prerequisite]
        negative_prerequisite_fixture[first_prerequisite] = (
            not expected if isinstance(expected, bool) else f"NOT_{expected}"
        )
    minimum_choices = {
        "ENTRY": 1,
        "TIER 1": 2,
        "TIER 2": 4,
        "TIER 3": 6,
        "TIER 4": 8,
        "TIER 5": 10,
    }[coverage.node.tier]
    snapshot_choices = {1: 1, 5: 5, 10: 10, 15: 15, 20: 20}
    available_levels = [
        level for level, choices in snapshot_choices.items() if choices >= minimum_choices
    ]
    limitations_preserved = all(item["passed"] for item in limitation_results)
    return {
        "counterfactual_id": coverage.counterfactual_id,
        "node_id": coverage.node.node_id,
        "specialty": coverage.node.specialty,
        "class_id": coverage.node.class_id,
        "tier": coverage.node.tier,
        "mechanic_id": str(spec["mechanic_id"]),
        "prerequisite_fields": sorted(
            set(prerequisites).union(
                *(
                    set(contract)
                    for contract in branch_prerequisite_contract.values()
                )
            )
        ),
        "prerequisite_contract": prerequisites,
        "branch_prerequisite_contract": branch_prerequisite_contract,
        "prerequisite_fixture": deepcopy(prerequisites),
        "negative_prerequisite_fixture": negative_prerequisite_fixture,
        "authored_rule_contract": authored_rule_contract,
        "authored_effect_fields": authored_effect_fields,
        "authored_constraint_fields": authored_constraint_fields,
        "authored_guard_fields": authored_guard_fields,
        "authored_termination_fields": authored_termination_fields,
        "authored_invariant_fields": authored_invariant_fields,
        "mechanical_operations": operations,
        "mechanical_branches": mechanical_branches,
        "branch_fixtures": branch_fixtures,
        "branch_options": sorted(mechanical_branches),
        "default_branch": default_branch,
        "limitation_contract": limitation_contract,
        "control": initial_state,
        "delta_detected": bool(changed_fields),
        "changed_fields": changed_fields,
        "parameter_ids": list(parameter_ids),
        "limitation_results": limitation_results,
        "limitations_preserved": limitations_preserved,
        "minimum_specialty_choices": minimum_choices,
        "available_benchmark_levels": available_levels,
        "scenario_roles": {
            role: {
                **scenario,
                "measured_fields": changed_fields
                if scenario["mechanic_scope_matches"]
                else [],
            }
            for role, scenario in _specialty_scenario_roles(
                coverage,
                str(spec["mechanic_id"]),
            ).items()
        },
        "benchmark_resources": {
            level: {
                "hp": parameters.values["hp.vector_l01_l05_l10_l15_l20"][index],
                "res": parameters.values["res.vector_l01_l05_l10_l15_l20"][index],
                "ap": parameters.values["combat.ap.pool"],
            }
            for index, level in enumerate(LEVELS)
        },
        "meaningful_delta_gate": {
            "numeric_threshold_applicable": bool(parameter_ids),
            "structural_state_delta": bool(changed_fields),
            "rare_or_strategic_state": None if parameter_ids else str(spec["mechanic_id"]),
        },
    }


def execute_specialty_counterfactual(
    definition: dict[str, Any],
    *,
    role: str,
    level: int,
    branch_id: str | None = None,
    prerequisite_overrides: dict[str, Any] | None = None,
    limitation_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one same-character specialty fixture with the node absent/present."""
    normalized_role = role.upper()
    if normalized_role not in {"PRIMARY", "SECONDARY", "OFF_ROLE"}:
        raise ValueError(f"unsupported specialty scenario role {role}")
    if level not in definition["available_benchmark_levels"]:
        raise ValueError(
            f"{definition['node_id']} is unavailable at benchmark level {level}"
        )
    selected_branch = branch_id or str(definition["default_branch"])
    if selected_branch not in definition["mechanical_branches"]:
        raise ValueError(
            f"{definition['node_id']} does not admit branch {selected_branch}; "
            f"choose one of {definition['branch_options']}"
        )

    node_id = str(definition["node_id"])
    scenario_template = deepcopy(definition["scenario_roles"][normalized_role])
    resources = deepcopy(definition["benchmark_resources"][level])
    character_id = f"CF-{definition['specialty'].replace(' ', '_')}-L{level:02d}"
    base_character = {
        "character_id": character_id,
        "class_id": definition["class_id"],
        "level": level,
        "role": normalized_role,
        "specialty_nodes": [],
    }
    control_character = deepcopy(base_character)
    candidate_character = deepcopy(base_character)
    candidate_character["specialty_nodes"].append(node_id)

    control_result = deepcopy(definition["control"])
    candidate_result = deepcopy(control_result)
    prerequisite_contract = deepcopy(definition["prerequisite_contract"])
    prerequisites = deepcopy(definition["prerequisite_fixture"])
    selected_branch_prerequisites = deepcopy(
        definition.get("branch_prerequisite_contract", {}).get(
            selected_branch, {}
        )
    )
    prerequisite_contract.update(selected_branch_prerequisites)
    prerequisites.update(selected_branch_prerequisites)
    overrides = prerequisite_overrides or {}
    unknown_overrides = sorted(set(overrides) - set(prerequisite_contract))
    if unknown_overrides:
        raise ValueError(
            f"{node_id} received unknown prerequisite fields {unknown_overrides}"
        )
    prerequisites.update(deepcopy(overrides))
    failed_prerequisites = sorted(
        field
        for field, expected in prerequisite_contract.items()
        if prerequisites.get(field) != expected
    )
    prerequisites_satisfied = not failed_prerequisites
    limitation_probe = limitation_overrides or {}
    if len(limitation_probe) > 1:
        raise ValueError(
            f"{node_id} accepts one boundary probe at a time, got {sorted(limitation_probe)}"
        )
    # Explicit mechanic specs can also carry aggregate preservation assertions
    # (for example ``sum_preserved``) which deliberately have no single field.
    # Only authored constraints are addressable boundary probes.
    boundary_by_field = {
        str(item["field"]): item
        for item in definition["limitation_contract"]
        if item.get("field") in definition["authored_constraint_fields"]
    }
    unknown_limitation_overrides = sorted(
        set(limitation_probe) - set(definition["authored_constraint_fields"])
    )
    if unknown_limitation_overrides:
        raise ValueError(
            f"{node_id} received unknown limitation fields {unknown_limitation_overrides}"
        )
    probe_enforcement = None
    if limitation_probe:
        probe_field = next(iter(limitation_probe))
        probe_enforcement = str(boundary_by_field[probe_field]["enforcement"])

    # A guard is live same-character context. Apply its probe to both sides,
    # then suppress the node operation when the authored guard is false.
    if probe_enforcement == "GUARD":
        control_result.update(deepcopy(limitation_probe))
        candidate_result.update(deepcopy(limitation_probe))
    failed_guards = sorted(
        field
        for field in definition["authored_guard_fields"]
        if candidate_result.get(field) != boundary_by_field[field]["value"]
    )
    guards_satisfied = not failed_guards

    active_domains = set(scenario_template["active_mechanic_domains"])
    role_relevant = (
        scenario_template["specialty_mechanic_domain"] in active_domains
    )
    node_effect_applied = (
        role_relevant
        and node_id in candidate_character["specialty_nodes"]
        and prerequisites_satisfied
        and guards_satisfied
    )
    executed_operations: list[dict[str, Any]] = []
    established_result: dict[str, Any] | None = None
    if node_effect_applied:
        executed_operations = deepcopy(
            definition["mechanical_branches"][selected_branch]
        )
        candidate_result = _apply_specialty_operations(
            candidate_result,
            executed_operations,
        )
        established_result = deepcopy(candidate_result)

    effect_established_before_termination = False
    if limitation_probe and probe_enforcement in {"TERMINATION", "INVARIANT"}:
        candidate_result.update(deepcopy(limitation_probe))
    if node_effect_applied and probe_enforcement == "TERMINATION":
        effect_established_before_termination = True
        for field in definition["branch_fixtures"][selected_branch][
            "changed_fields"
        ]:
            candidate_result[field] = deepcopy(control_result.get(field))
        node_effect_applied = False

    measured_fields = []
    for operation in executed_operations:
        field = str(operation["field"])
        if (
            control_result.get(field) != candidate_result.get(field)
            and field not in measured_fields
        ):
            measured_fields.append(field)
    effect_measurements = {
        field: {
            "control": deepcopy(control_result.get(field)),
            "candidate": deepcopy(candidate_result.get(field)),
            "delta": (
                candidate_result[field] - control_result[field]
                if isinstance(candidate_result.get(field), (int, float))
                and not isinstance(candidate_result.get(field), bool)
                and isinstance(control_result.get(field), (int, float))
                and not isinstance(control_result.get(field), bool)
                else None
            ),
        }
        for field in measured_fields
    }
    limitation_results = _evaluate_limitations(
        control_result,
        candidate_result,
        definition["limitation_contract"],
    )
    limitations_preserved = all(item["passed"] for item in limitation_results)
    status = (
        "GUARD_VIOLATION"
        if probe_enforcement == "GUARD" and not guards_satisfied
        else "TERMINATED"
        if probe_enforcement == "TERMINATION" and not limitations_preserved
        else "INVARIANT_VIOLATION"
        if probe_enforcement == "INVARIANT" and not limitations_preserved
        else "CONTROL"
        if normalized_role == "OFF_ROLE"
        else "MISSING_PREREQUISITE"
        if not prerequisites_satisfied
        else "EXECUTED"
    )
    map_contract = next(
        item
        for item in map_contracts()
        if item["map_id"] == scenario_template["map_id"]
    )
    map_width, map_height = map_contract["size_m"]
    actor_position = [min(10, map_width - 1), min(15, map_height - 1)]
    opposition_position = [
        max(0, map_width - actor_position[0] - 1),
        actor_position[1],
    ]
    scenario_events = [
        {
            "sequence": 1,
            "event": "OBJECTIVE_ACTION_DECLARED",
            "objective_id": scenario_template["objective_id"],
            "active_mechanic_domains": sorted(active_domains),
        },
        {
            "sequence": 2,
            "event": "SPECIALTY_SCOPE_EVALUATED",
            "specialty_mechanic_domain": scenario_template[
                "specialty_mechanic_domain"
            ],
            "scope_match": role_relevant,
        },
        {
            "sequence": 3,
            "event": "PREREQUISITES_EVALUATED",
            "satisfied": prerequisites_satisfied,
            "failed_fields": failed_prerequisites,
        },
        {
            "sequence": 4,
            "event": (
                "SPECIALTY_OPERATIONS_APPLIED"
                if executed_operations
                else "BASELINE_ACTION_ONLY"
            ),
            "operation_count": len(executed_operations),
            "changed_fields": measured_fields,
        },
    ]
    scenario_final_state = {
        "objective_id": scenario_template["objective_id"],
        "control_result": control_result,
        "candidate_result": candidate_result,
        "specialty_delta_fields": measured_fields,
        "node_effect_applied": node_effect_applied,
        "remaining_ap": resources["ap"],
    }
    scenario_state_hash = hashlib.sha256(
        json.dumps(
            scenario_final_state,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "status": status,
        "role": normalized_role,
        "level": level,
        "selected_branch": selected_branch,
        "branch_options": deepcopy(definition["branch_options"]),
        "control_character": control_character,
        "candidate_character": candidate_character,
        "prerequisite_fixture": prerequisites,
        "prerequisite_contract": prerequisite_contract,
        "failed_prerequisites": failed_prerequisites,
        "prerequisites_satisfied": prerequisites_satisfied,
        "guards_satisfied": guards_satisfied,
        "failed_guards": failed_guards,
        "role_relevant": role_relevant,
        "scenario_fixture": {
            "fixture_id": f"{scenario_template['scenario_id']}:L{level:02d}",
            "scenario_id": scenario_template["scenario_id"],
            "map_id": scenario_template["map_id"],
            "geometry_fixture_id": scenario_template["map_id"],
            "map_geometry": {
                "width": map_width,
                "height": map_height,
                "uses": list(map_contract["uses"]),
                "geometry_usage": "CONTEXT_ONLY_NO_SPATIAL_QUERY",
            },
            "objective_id": scenario_template["objective_id"],
            "active_mechanic_domains": sorted(active_domains),
            "specialty_mechanic_domain": scenario_template[
                "specialty_mechanic_domain"
            ],
            "specialty_mechanic_id": scenario_template["specialty_mechanic_id"],
            "mechanic_scope_matches": role_relevant,
            "actor": {
                **deepcopy(candidate_character),
                **resources,
                "equipment_id": scenario_template["equipment_id"],
                "ammunition_state": "REFERENCE_FULL_LOAD",
                "posture": "STANDING",
                "position": actor_position,
                "knowledge_state": "FIXTURE_DECLARED",
            },
            "opposition": {
                "fixture_id": f"{scenario_template['scenario_id']}:CONTROL",
                "position": opposition_position,
                "knowledge_state": "FIXTURE_DECLARED",
            },
            "environmental_state": "FIXTURE_DEFAULT",
            "team_turn": {
                "active_side": "A",
                "remaining_ap": resources["ap"],
                "elapsed_seconds": 0,
            },
            "prerequisite_state": deepcopy(prerequisites),
            "limitation_state": deepcopy(definition["limitation_contract"]),
        },
        "scenario_execution": {
            "execution_kind": "DETERMINISTIC_SPECIALTY_COUNTERFACTUAL",
            "events": scenario_events,
            "terminal_state": scenario_final_state,
            "terminal_state_hash": scenario_state_hash,
            "objective_completed": None,
            "objective_evaluation_status": "NOT_CLAIMED_STRUCTURAL_ONLY",
            "structural_counterfactual": True,
        },
        "node_effect_applied": node_effect_applied,
        "effect_established_before_termination": effect_established_before_termination,
        "established_result": established_result,
        "mechanical_operations_executed": executed_operations,
        "limitation_contract": deepcopy(definition["limitation_contract"]),
        "authored_rule_contract": deepcopy(definition["authored_rule_contract"]),
        "authored_constraint_fields": deepcopy(
            definition["authored_constraint_fields"]
        ),
        "limitation_probe": bool(limitation_probe),
        "limitation_probe_enforcement": probe_enforcement,
        "limitation_results": limitation_results,
        "limitations_preserved": limitations_preserved,
        "control_result": control_result,
        "candidate_result": candidate_result,
        "measured_fields": measured_fields,
        "effect_measurements": effect_measurements,
        "generic_dominance_detected": None,
        "generic_dominance_evaluation": "NOT_CLAIMED_STRUCTURAL_ONLY",
    }


_SPECIALTY_CHOICE_BUDGETS = {1: 1, 5: 5, 10: 10, 15: 15, 20: 20}


def _designated_specialty_path(
    definitions: list[dict[str, Any]],
    level: int,
) -> list[dict[str, Any]]:
    """Select the deterministic legal benchmark path for one specialty."""
    budget = _SPECIALTY_CHOICE_BUDGETS[level]
    eligible = [
        definition
        for definition in definitions
        if definition["minimum_specialty_choices"] <= budget
    ]
    by_tier: dict[str, list[dict[str, Any]]] = {}
    for definition in eligible:
        by_tier.setdefault(str(definition["tier"]), []).append(definition)
    straight_path = [*by_tier.get("ENTRY", [])[:1]]
    for tier in ("TIER 1", "TIER 2", "TIER 3", "TIER 4"):
        straight_path.extend(by_tier.get(tier, [])[:2])
    straight_path.extend(by_tier.get("TIER 5", [])[:1])
    selected_ids = {item["node_id"] for item in straight_path}
    selected = straight_path + [
        item for item in eligible if item["node_id"] not in selected_ids
    ]
    return selected[:budget]


def _specialty_build_id(
    specialty: str,
    level: int,
    node_ids: list[str],
) -> str:
    digest = hashlib.sha256(
        json.dumps(node_ids, separators=(",", ":"), ensure_ascii=True).encode(
            "utf-8"
        )
    ).hexdigest()[:12]
    return f"TD-BUILD-{_slug(specialty).upper()}-L{level:02d}-{digest.upper()}"


def _specialty_objective_spec(
    *,
    scenario_id: str,
    class_id: str,
    level: int,
    equipment_id: str,
    node_ids: list[str],
    policy_id: str | None = None,
    initial_magazine_rounds: int | None = None,
    objective_fixture_id: str = "STANDARD",
) -> TeamScenarioSpec:
    base = default_team_spec(scenario_id, level)
    classes_a = list(base.classes_a)
    weapons_a = list(base.weapons_a)
    classes_a[0] = class_id
    weapons_a[0] = equipment_id
    policies_a = list(base.policies_a)
    if policy_id is not None:
        policies_a[0] = policy_id
    specialty_nodes_a = [tuple(nodes) for nodes in base.specialty_nodes_a]
    specialty_nodes_a[0] = tuple(node_ids)
    initial_magazine_rounds_a = list(base.initial_magazine_rounds_a)
    initial_magazine_rounds_a[0] = initial_magazine_rounds
    return replace(
        base,
        classes_a=tuple(classes_a),
        weapons_a=tuple(weapons_a),
        policies_a=tuple(policies_a),
        specialty_nodes_a=tuple(specialty_nodes_a),
        initial_magazine_rounds_a=tuple(initial_magazine_rounds_a),
        objective_fixture_id=objective_fixture_id,
    )


def _run_specialty_objective(
    spec: TeamScenarioSpec,
    parameters: ParameterSet,
    seed: int,
) -> dict[str, Any]:
    key = (
        parameters.hash,
        json.dumps(asdict(spec), sort_keys=True, separators=(",", ":")),
        seed,
    )
    if key not in _OBJECTIVE_RUN_CACHE:
        _OBJECTIVE_RUN_CACHE[key] = run_team_scenario(
            spec,
            parameters,
            seed,
            capture_events=True,
        )
    return deepcopy(_OBJECTIVE_RUN_CACHE[key])


def _numeric_objective_metrics(
    value: Any,
    prefix: str = "",
) -> dict[str, float]:
    metrics: dict[str, float] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            metrics.update(_numeric_objective_metrics(child, path))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        metrics[prefix] = float(value)
    return metrics


def _objective_result_summary(result: dict[str, Any]) -> dict[str, Any]:
    side_metrics = deepcopy(result["metrics"]["by_side"]["A"])
    return {
        "scenario_id": result["scenario_id"],
        "scenario_version": result["scenario_version"],
        "map_id": result["map_id"],
        "fixture_ids": deepcopy(result["fixture_ids"]),
        "builds": deepcopy(result["builds"]),
        "equipment": deepcopy(result["equipment"]),
        "policies": deepcopy(result["policies"]),
        "seed": result["seed"],
        "winner_side": result["winner_side"],
        "objective_completed": result["winner_side"] == "A",
        "terminal_reason": result["terminal_reason"],
        "elapsed_seconds": result["elapsed_seconds"],
        "event_count": result["event_count"],
        "event_log_hash": result["event_log_hash"],
        "final_state_hash": result["final_state_hash"],
        "subject_side_metrics": side_metrics,
        "runtime_specialty_effects": deepcopy(
            result["metrics"].get("specialty_effects", [])
        ),
        "replay_spec": deepcopy(result["replay_spec"]),
    }


def _execute_specialty_objective_scenario(
    *,
    specialty: str,
    class_id: str,
    definitions: list[dict[str, Any]],
    role: str,
    level: int,
    parameters: ParameterSet,
) -> dict[str, Any]:
    """Run a designated build through an actual deterministic team objective."""
    normalized_role = role.upper()
    selected = _designated_specialty_path(definitions, level)
    node_ids = [str(item["node_id"]) for item in selected]
    build_id = _specialty_build_id(specialty, level, node_ids)
    context = _SPECIALTY_SCENARIO_CONTEXTS[class_id][normalized_role]
    context_map_id, objective_id, equipment_id = context
    if normalized_role != "OFF_ROLE":
        equipment_id = _SPECIALTY_OBJECTIVE_EQUIPMENT.get(
            specialty, equipment_id
        )
    policy_id = (
        _SPECIALTY_OBJECTIVE_POLICY.get(specialty)
        if normalized_role != "OFF_ROLE"
        else None
    )
    actual_scenario_id = _SPECIALTY_ACTUAL_OBJECTIVES[class_id][normalized_role]
    initial_magazine_rounds = (
        0
        if specialty == "OPERATOR" and normalized_role != "OFF_ROLE"
        else None
    )
    objective_fixture_id = (
        f"SPECIALTY-{_slug(specialty).upper()}-ENTRY"
        if normalized_role != "OFF_ROLE"
        and specialty
        in {
            "AUTODRONES",
            "ARSENAL",
            "DIPLOMAT",
            "ENFORCER",
            "EVADER",
            "FIELD ENGINEER",
            "FIELD MEDIC",
            "FPV DRONES",
            "FLANKER",
            "GUARDIAN",
            "HANDLER",
            "ICON",
            "INFILTRATOR",
            "RESUSCITATOR",
            "SHIELD BEARER",
            "SHOCK",
            "SNIPER",
            "TRAUMA",
        }
        else "STANDARD"
    )
    seed = derive_seed(
        20260824,
        f"specialty-objective:{specialty}:{normalized_role}",
        level,
    )
    control_spec = _specialty_objective_spec(
        scenario_id=actual_scenario_id,
        class_id=class_id,
        level=level,
        equipment_id=equipment_id,
        node_ids=[],
        policy_id=policy_id,
        initial_magazine_rounds=initial_magazine_rounds,
        objective_fixture_id=objective_fixture_id,
    )
    candidate_spec = _specialty_objective_spec(
        scenario_id=actual_scenario_id,
        class_id=class_id,
        level=level,
        equipment_id=equipment_id,
        node_ids=node_ids,
        policy_id=policy_id,
        initial_magazine_rounds=initial_magazine_rounds,
        objective_fixture_id=objective_fixture_id,
    )
    control_objective = _objective_result_summary(
        _run_specialty_objective(control_spec, parameters, seed)
    )
    candidate_objective = _objective_result_summary(
        _run_specialty_objective(candidate_spec, parameters, seed)
    )
    node_executions = [
        execute_specialty_counterfactual(
            definition,
            role=normalized_role,
            level=level,
        )
        for definition in selected
    ]
    contributing = [
        {"node_id": definition["node_id"], "execution": execution}
        for definition, execution in zip(selected, node_executions, strict=True)
        if execution["node_effect_applied"]
        and bool(execution["effect_measurements"])
    ]
    action_records = [
        {
            "sequence": index,
            "node_id": node_id,
            "status": execution["status"],
            "prerequisites_satisfied": execution["prerequisites_satisfied"],
            "role_relevant": execution["role_relevant"],
            "node_effect_applied": execution["node_effect_applied"],
            "effect_measurements": deepcopy(execution["effect_measurements"]),
            "terminal_state_hash": execution["scenario_execution"][
                "terminal_state_hash"
            ],
        }
        for index, (node_id, execution) in enumerate(
            zip(node_ids, node_executions, strict=True),
            start=1,
        )
    ]
    exact_mechanical_vector = {
        item["node_id"]: deepcopy(item["execution"]["effect_measurements"])
        for item in contributing
    }
    control_numeric = _numeric_objective_metrics(
        {
            "elapsed_seconds": control_objective["elapsed_seconds"],
            "subject_side_metrics": control_objective["subject_side_metrics"],
        }
    )
    candidate_numeric = _numeric_objective_metrics(
        {
            "elapsed_seconds": candidate_objective["elapsed_seconds"],
            "subject_side_metrics": candidate_objective["subject_side_metrics"],
        }
    )
    objective_metric_deltas = {
        metric: candidate_numeric.get(metric, 0.0)
        - control_numeric.get(metric, 0.0)
        for metric in sorted(set(control_numeric) | set(candidate_numeric))
        if candidate_numeric.get(metric, 0.0)
        != control_numeric.get(metric, 0.0)
    }
    objective_runtime_effect_detected = (
        bool(objective_metric_deltas)
        or bool(candidate_objective["runtime_specialty_effects"])
        or control_objective["winner_side"] != candidate_objective["winner_side"]
        or control_objective["objective_completed"]
        != candidate_objective["objective_completed"]
        or control_objective["terminal_reason"]
        != candidate_objective["terminal_reason"]
    )
    generic_dominance_detected = (
        normalized_role == "OFF_ROLE" and objective_runtime_effect_detected
    )
    findings: list[dict[str, Any]] = []
    if generic_dominance_detected:
        findings.append(
            {
                "code": "GENERAL DOMINANCE",
                "severity": "REVIEW",
                "specialty": specialty,
                "build_id": build_id,
                "role": normalized_role,
                "evidence": [item["node_id"] for item in contributing],
            }
        )
    terminal_state = {
        "actual_scenario_id": actual_scenario_id,
        "objective_id": objective_id,
        "control_completed": control_objective["objective_completed"],
        "candidate_completed": candidate_objective["objective_completed"],
        "control_winner_side": control_objective["winner_side"],
        "candidate_winner_side": candidate_objective["winner_side"],
        "control_terminal_reason": control_objective["terminal_reason"],
        "candidate_terminal_reason": candidate_objective["terminal_reason"],
        "candidate_contributing_node_count": len(contributing),
        "candidate_contributing_node_ids": [
            item["node_id"] for item in contributing
        ],
        "objective_runtime_effect_detected": objective_runtime_effect_detected,
        "generic_dominance_detected": generic_dominance_detected,
        "objective_metric_deltas": objective_metric_deltas,
        "runtime_specialty_effects": deepcopy(
            candidate_objective["runtime_specialty_effects"]
        ),
        "runtime_contributing_node_ids": sorted(
            {
                str(effect["node_id"])
                for effect in candidate_objective["runtime_specialty_effects"]
            }
        ),
    }
    terminal_state_hash = hashlib.sha256(
        json.dumps(
            terminal_state,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "scenario_id": (
            f"SPECIALTY-OBJECTIVE-{_slug(specialty).upper()}-"
            f"{normalized_role}-L{level:02d}"
        ),
        "scenario_version": "1.0.0",
        "execution_kind": "DETERMINISTIC_SPECIALTY_OBJECTIVE",
        "qualification": "EXACT_OBJECTIVE_COUNTERFACTUAL",
        "specialty": specialty,
        "class_id": class_id,
        "role": normalized_role,
        "level": level,
        "build_id": build_id,
        "build_node_ids": node_ids,
        "fixture": {
            "fixture_id": f"{build_id}:{normalized_role}",
            "map_id": candidate_objective["map_id"],
            "context_map_id": context_map_id,
            "objective_id": objective_id,
            "actual_scenario_id": actual_scenario_id,
            "equipment_id": equipment_id,
            "initial_magazine_rounds": initial_magazine_rounds,
            "objective_fixture_id": objective_fixture_id,
            "terminal_condition": "ACTUAL_TEAM_OBJECTIVE_TERMINAL_STATE",
            "active_mechanic_domains": sorted(
                _OBJECTIVE_MECHANIC_DOMAINS[objective_id]
            ),
            "seed": seed,
        },
        "control_metrics": {
            **control_objective,
            "structurally_executed_specialty_nodes": 0,
            "specialty_mechanical_vector": {},
        },
        "candidate_metrics": {
            **candidate_objective,
            "structurally_executed_specialty_nodes": len(contributing),
            "specialty_mechanical_vector": exact_mechanical_vector,
        },
        "structural_contribution_detected": bool(contributing),
        "objective_runtime_effect_detected": objective_runtime_effect_detected,
        "contribution_detected": objective_runtime_effect_detected,
        "contribution_strength_evaluation": "ACTUAL_OBJECTIVE_AND_RESOURCE_METRICS_ONLY",
        "structural_vector_evaluation": "SEPARATE_NODE_EXECUTOR_EVIDENCE_NOT_OBJECTIVE_DELTA",
        "objective_metric_deltas": objective_metric_deltas,
        "runtime_specialty_effects": deepcopy(
            candidate_objective["runtime_specialty_effects"]
        ),
        "generic_dominance_detected": generic_dominance_detected,
        "generic_dominance_evaluation": "ACTUAL_OFF_ROLE_OBJECTIVE_AND_RESOURCE_METRICS",
        "events": [
            {
                "sequence": 0,
                "event": "ACTUAL_OBJECTIVE_REPLAY_EXECUTED",
                "control_event_log_hash": control_objective["event_log_hash"],
                "candidate_event_log_hash": candidate_objective["event_log_hash"],
                "control_final_state_hash": control_objective["final_state_hash"],
                "candidate_final_state_hash": candidate_objective["final_state_hash"],
            },
            *action_records,
        ],
        "raw_event_log_locators": {
            "control": "control_metrics.replay_spec",
            "candidate": "candidate_metrics.replay_spec",
        },
        "terminal_state": terminal_state,
        "terminal_state_hash": terminal_state_hash,
        "findings": findings,
    }


def specialty_coverage(
    parameters: ParameterSet, root: Path | None = None, *, include_objective_scenarios: bool = True
) -> dict[str, Any]:
    """Compile specialty contracts, optionally running the separate team-objective benchmark."""
    nodes = parse_all_specialties(root)
    node_contract_keys = {(node.specialty, node.name) for node in nodes}
    missing_contracts = sorted(node_contract_keys - set(SPECIALTY_RULE_CONTRACTS))
    extra_contracts = sorted(set(SPECIALTY_RULE_CONTRACTS) - node_contract_keys)
    if missing_contracts or extra_contracts:
        raise ValueError(
            "explicit specialty rule contract inventory mismatch: "
            f"missing={missing_contracts}, extra={extra_contracts}"
        )
    coverage = [
        _with_specialty_audit_finding(classify_node(node, parameters))
        for node in nodes
    ]
    counterfactuals = [result for item in coverage if (result := node_counterfactual(item, parameters)) is not None]
    scenario_matrix = [
        {
            "scenario_id": f"{counterfactual['counterfactual_id']}:{role}:L{level:02d}",
            "counterfactual_id": counterfactual["counterfactual_id"],
            "node_id": counterfactual["node_id"],
            "specialty": counterfactual["specialty"],
            "level": level,
            "role": role,
            **execute_specialty_counterfactual(
                counterfactual,
                role=role,
                level=level,
            ),
        }
        for counterfactual in counterfactuals
        for level in counterfactual["available_benchmark_levels"]
        for role in counterfactual["scenario_roles"]
    ]
    counterfactuals_by_specialty = {
        specialty: [
            item for item in counterfactuals if item["specialty"] == specialty
        ]
        for specialty in sorted({item["specialty"] for item in counterfactuals})
    }
    designated_builds = {
        specialty: {
            str(level): {
                "build_id": _specialty_build_id(
                    specialty,
                    level,
                    [item["node_id"] for item in selected],
                ),
                "class_id": selected[0]["class_id"],
                "specialty": specialty,
                "level": level,
                "node_ids": [item["node_id"] for item in selected],
            }
            for level in LEVELS
            for selected in [
                _designated_specialty_path(definitions, level)
            ]
        }
        for specialty, definitions in counterfactuals_by_specialty.items()
    }
    objective_scenario_matrix = [
        _execute_specialty_objective_scenario(
            specialty=specialty,
            class_id=definitions[0]["class_id"],
            definitions=definitions,
            role=role,
            level=level,
            parameters=parameters,
        )
        for specialty, definitions in counterfactuals_by_specialty.items()
        for level in LEVELS
        for role in ("PRIMARY", "SECONDARY", "OFF_ROLE")
    ] if include_objective_scenarios else []
    branch_selection_matrix = [
        {
            "scenario_id": (
                f"{counterfactual['counterfactual_id']}:PRIMARY:"
                f"L{counterfactual['available_benchmark_levels'][0]:02d}:"
                f"BRANCH-{branch}"
            ),
            "counterfactual_id": counterfactual["counterfactual_id"],
            "node_id": counterfactual["node_id"],
            "specialty": counterfactual["specialty"],
            "level": counterfactual["available_benchmark_levels"][0],
            "role": "PRIMARY",
            **execute_specialty_counterfactual(
                counterfactual,
                role="PRIMARY",
                level=counterfactual["available_benchmark_levels"][0],
                branch_id=branch,
            ),
        }
        for counterfactual in counterfactuals
        if len(counterfactual["branch_options"]) > 1
        for branch in counterfactual["branch_options"]
    ]
    negative_prerequisite_matrix = []
    for counterfactual in counterfactuals:
        contract = counterfactual["prerequisite_contract"]
        if not contract:
            continue
        for field in sorted(contract):
            expected = contract[field]
            failed_value = not expected if isinstance(expected, bool) else f"NOT_{expected}"
            level = counterfactual["available_benchmark_levels"][0]
            negative_prerequisite_matrix.append(
                {
                    "scenario_id": (
                        f"{counterfactual['counterfactual_id']}:PRIMARY:L{level:02d}:"
                        f"MISSING-{field}"
                    ),
                    "counterfactual_id": counterfactual["counterfactual_id"],
                    "node_id": counterfactual["node_id"],
                    "specialty": counterfactual["specialty"],
                    "level": level,
                    "role": "PRIMARY",
                    "failed_field": field,
                    **execute_specialty_counterfactual(
                        counterfactual,
                        role="PRIMARY",
                        level=level,
                        prerequisite_overrides={field: failed_value},
                    ),
                }
            )
    boundary_violation_matrix = []
    for counterfactual in counterfactuals:
        level = counterfactual["available_benchmark_levels"][0]
        for field in counterfactual["authored_constraint_fields"]:
            expected = counterfactual["control"][field]
            violating_value = (
                not expected if isinstance(expected, bool) else f"NOT_{expected}"
            )
            enforcement = next(
                item["enforcement"]
                for item in counterfactual["limitation_contract"]
                if item.get("field") == field
            )
            boundary_violation_matrix.append(
                {
                    "scenario_id": (
                        f"{counterfactual['counterfactual_id']}:PRIMARY:L{level:02d}:"
                        f"{enforcement}-{field}"
                    ),
                    "counterfactual_id": counterfactual["counterfactual_id"],
                    "node_id": counterfactual["node_id"],
                    "specialty": counterfactual["specialty"],
                    "level": level,
                    "role": "PRIMARY",
                    "violated_field": field,
                    "enforcement": enforcement,
                    **execute_specialty_counterfactual(
                        counterfactual,
                        role="PRIMARY",
                        level=level,
                        limitation_overrides={field: violating_value},
                    ),
                }
            )
    guard_violation_matrix = [
        item for item in boundary_violation_matrix if item["enforcement"] == "GUARD"
    ]
    termination_matrix = [
        item
        for item in boundary_violation_matrix
        if item["enforcement"] == "TERMINATION"
    ]
    invariant_mutation_matrix = [
        item
        for item in boundary_violation_matrix
        if item["enforcement"] == "INVARIANT"
    ]
    findings = [finding for item in coverage for finding in item.findings]
    findings.extend(
        finding
        for scenario in objective_scenario_matrix
        for finding in scenario["findings"]
    )
    for specialty in sorted(counterfactuals_by_specialty):
        affected = [
            scenario
            for scenario in objective_scenario_matrix
            if scenario["specialty"] == specialty
            and scenario["role"] in {"PRIMARY", "SECONDARY"}
            and scenario["build_node_ids"]
            and not scenario["objective_runtime_effect_detected"]
        ]
        if not affected:
            continue
        audit = _SPECIALTY_RUNTIME_GAP_AUDIT.get(
            specialty,
            {
                "classification": "UNCLASSIFIED_RUNTIME_GAP",
                "entry_node_id": None,
                "missing_surface": "unclassified specialty objective integration surface",
                "requires_game_design_decision": True,
                "design_question": "Classify the remaining runtime integration gap before implementation.",
            },
        )
        findings.append(
            {
                "code": "UNIMPLEMENTABLE SPEC GAP",
                "subtype": "SPECIALTY_OBJECTIVE_RUNTIME_INTEGRATION_GAP",
                "severity": "REVIEW",
                "specialty": specialty,
                "class_id": affected[0]["class_id"],
                "affected_scenario_ids": [
                    scenario["scenario_id"] for scenario in affected
                ],
                "affected_build_ids": sorted(
                    {scenario["build_id"] for scenario in affected}
                ),
                "affected_node_ids": sorted(
                    {
                        node_id
                        for scenario in affected
                        for node_id in scenario["build_node_ids"]
                    }
                ),
                **deepcopy(audit),
                "evidence": (
                    "The deterministic team objective carried the designated "
                    "build in its replay state, but no specialty node changed "
                    "an objective or resource metric. The separate node executor "
                    "vector is retained as structural evidence only."
                ),
            }
        )
    by_state = Counter(item.state.value for item in coverage)
    by_specialty: dict[str, dict[str, Any]] = {}
    for specialty in sorted({node.specialty for node in nodes}):
        items = [item for item in coverage if item.node.specialty == specialty]
        by_specialty[specialty] = {
            "class_id": items[0].node.class_id,
            "node_count": len(items),
            "coverage_counts": dict(Counter(item.state.value for item in items)),
            "nodes": [item.to_dict() for item in items],
        }
    expected_specialties = sum((list(values) for values in CLASS_SPECIALTIES.values()), [])
    missing_specialties = sorted(set(expected_specialties) - set(by_specialty))
    return {
        "version": "1.1.0",
        "source_commit": ENCODED_SOURCE_COMMIT,
        "specialty_count": len(by_specialty),
        "node_count": len(nodes),
        "coverage_counts": dict(by_state),
        "missing_specialties": missing_specialties,
        "specialties": by_specialty,
        "specialty_scenario_evidence_status": (
            "OBJECTIVE_NOT_RUN"
            if not include_objective_scenarios else
            "OBJECTIVE_EXECUTED_WITH_RUNTIME_INTEGRATION_GAPS"
            if any(
                finding.get("subtype")
                == "SPECIALTY_OBJECTIVE_RUNTIME_INTEGRATION_GAP"
                for finding in findings
            )
            else "OBJECTIVE_EXECUTED_WITH_FULL_RUNTIME_INTEGRATION"
        ),
        "generic_dominance_evaluation": (
            "ACTUAL_OFF_ROLE_OBJECTIVE_AND_RESOURCE_METRICS"
            if include_objective_scenarios else "NOT_EVALUATED"
        ),
        "counterfactuals": counterfactuals,
        "designated_builds": designated_builds,
        "scenario_matrix": scenario_matrix,
        "objective_scenario_matrix": objective_scenario_matrix,
        "branch_selection_matrix": branch_selection_matrix,
        "negative_prerequisite_matrix": negative_prerequisite_matrix,
        "boundary_violation_matrix": boundary_violation_matrix,
        "guard_violation_matrix": guard_violation_matrix,
        "termination_matrix": termination_matrix,
        "invariant_mutation_matrix": invariant_mutation_matrix,
        "rejected_parameter_bindings": [
            {
                "specialty": specialty,
                "node": node,
                "family": family,
                "parameter_id": specialty_parameter_id(specialty, node, family),
                "status": "REJECTED_INVALID_BINDING",
                "authority_source": "Testing/BALANCE PARAMETERS.md",
                "execution_policy": "FAIL_CLOSED",
            }
            for specialty, node, family in sorted(
                REJECTED_SPECIALTY_PARAMETER_ROWS
            )
        ],
        "deferred_parameter_bindings": [
            {
                "specialty": specialty,
                "node": node,
                "family": family,
                "parameter_id": specialty_parameter_id(specialty, node, family),
                "status": "DEFERRED_NON_CUMULATIVE_CHOICE",
                "authority_source": "Testing/BALANCE PARAMETERS.md",
                "execution_policy": "FAIL_CLOSED",
            }
            for specialty, node, family in sorted(
                DEFERRED_NON_CUMULATIVE_PARAMETER_ROWS
            )
        ],
        # Backward-compatible aggregate for artifact readers; every row now
        # carries its mechanically distinct enforcement mode.
        "limitation_violation_matrix": boundary_violation_matrix,
        "findings": findings,
    }
