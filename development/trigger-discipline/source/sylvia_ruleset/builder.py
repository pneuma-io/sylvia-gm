from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from balance_suite.parameters import default_manifest
from balance_suite.specialties import specialty_coverage
from sylvia_ruleset import (
    ground_weapons,
    medical,
    network_entry,
    network_functions,
    network_intrusion,
    network_reroute,
    network_security,
    network_state_schema,
    weapon_handling,
)
from sylvia_ruleset.character_build import eligibility_catalog, eligibility_constraints

PACKAGE_ID = "pneuma-io.trigger-discipline"
PACKAGE_VERSION = "0.1.5"
CONFORMANCE_SETTING_ID = "pneuma-io.trigger-discipline-conformance-setting"
CONFORMANCE_SETTING_VERSION = "0.1.3"
SYLVIA_BASELINE = "0eccde944309f25560dcf7d2dd182084642787e5"
HOST_COMPATIBILITY = {
    "host_contract": 1,
    "runtime_format": 1,
    "requires": [
        "panel_workspaces.v1",
        "shot_effects.v1",
        "spatial_clearance.v1",
        "spatial_mutation.v1",
        "spatial_path_moving_entity.v1",
        "starting_state_validation.v1",
        "tactical_grid.v1",
    ],
}

_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

CANONICAL_ROOTS = (
    "Character",
    "Classes",
    "Combat",
    "CQC",
    "Drones",
    "Environment",
    "Equipment",
    "Explosives",
    "Firearms",
    "Hacking",
    "Investigation",
    "Social",
    "Specialties",
    "Systems",
)
NAVIGATION_ROOTS = ("Hubs",)
INDEXED_REFERENCE_ROOTS = (*CANONICAL_ROOTS, *NAVIGATION_ROOTS)
TESTING_SOURCES = (
    "Testing/BALANCE CONTRACT.md",
    "Testing/SPECIALTY TEST APPROVAL.md",
    "Testing/BALANCE PARAMETERS.md",
    "Testing/BALANCE SCENARIOS.md",
    "Testing/BENCHMARK FIXTURES.md",
    "Testing/REFERENCE EQUIPMENT.md",
    "Testing/REFERENCE EQUIPMENT VALUES.md",
)


def _canonical_json(value: object, *, pretty: bool = False) -> bytes:
    if pretty:
        return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _authority_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    for directory in INDEXED_REFERENCE_ROOTS:
        paths.extend(sorted((root / directory).rglob("*.md")))
    paths.extend(root / value for value in TESTING_SOURCES)
    return sorted(set(paths), key=lambda value: value.relative_to(root).as_posix())


def _verify_source_commit(root: Path, source_commit: str, *, allow_worktree_sources: bool = False) -> str:
    try:
        resolved = _git(root, "rev-parse", "--verify", f"{source_commit}^{{commit}}")
    except subprocess.CalledProcessError as error:
        raise ValueError(f"source commit does not resolve: {source_commit}") from error

    scoped_roots = [*INDEXED_REFERENCE_ROOTS, *TESTING_SOURCES]
    status = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            *scoped_roots,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if status and not allow_worktree_sources:
        raise RuntimeError("canonical or testing authority sources contain uncommitted changes")

    comparison = subprocess.run(
        ["git", "-C", str(root), "diff", "--quiet", resolved, "--", *scoped_roots],
        check=False,
    )
    if comparison.returncode == 1 and not allow_worktree_sources:
        raise RuntimeError(
            f"source commit {resolved} does not contain the indexed authority source state"
        )
    if comparison.returncode not in (0, 1):
        raise RuntimeError("could not compare authority sources to the requested source commit")
    return resolved


def _source_index(root: Path, source_commit: str) -> dict[str, Any]:
    entries = []
    for path in _authority_paths(root):
        raw = path.read_bytes()
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": _sha(raw),
                "bytes": len(raw),
            }
        )
    return {
        "format": 1,
        "authority": "canonical atomic Markdown; Hubs files are navigation only; Testing files control implementation and measurement only",
        "source_commit": source_commit,
        "entries": entries,
    }


def _indexed_reference_files(
    root: Path, source_document: dict[str, Any]
) -> dict[str, bytes]:
    """Copy indexed source Markdown into Builder-readable package references."""

    files: dict[str, bytes] = {}
    for entry in source_document["entries"]:
        source_path = Path(entry["path"])
        if not source_path.parts or source_path.parts[0] not in INDEXED_REFERENCE_ROOTS:
            continue
        files[f"references/canon/{source_path.as_posix()}"] = (
            root / source_path
        ).read_bytes()
    return files


def _nested_parameters(values: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for identity, value in sorted(values.items()):
        current = result
        parts = identity.split(".")
        for part in parts[:-1]:
            child = current.setdefault(part, {})
            if not isinstance(child, dict):
                raise ValueError(f"parameter path collision at {identity}")
            current = child
        current[parts[-1]] = deepcopy(value)
    return result


def _param(
    name: str,
    kind: str,
    *,
    required: bool = True,
    resolve: str | None = None,
    enum: tuple[str, ...] = (),
    minimum: int | float | None = None,
    maximum: int | float | None = None,
    items: str | None = None,
    min_items: int | None = None,
    max_items: int | None = None,
    from_state: str | None = None,
    default: Any | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"name": name, "type": kind, "required": required}
    if resolve:
        result["resolve"] = resolve
    if enum:
        result["enum_values"] = list(enum)
    if minimum is not None:
        result["minimum"] = minimum
    if maximum is not None:
        result["maximum"] = maximum
    if items:
        result["items"] = {"type": items}
    if min_items is not None:
        result["min_items"] = min_items
    if max_items is not None:
        result["max_items"] = max_items
    if from_state is not None:
        result["from_state"] = from_state
    if default is not None:
        result["default"] = deepcopy(default)
    return result


def _schema(parameters: list[dict[str, Any]]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for parameter in parameters:
        if parameter.get("from_state") is not None:
            continue
        kind = parameter["type"]
        if kind == "enum":
            value: dict[str, Any] = {"type": "string", "enum": parameter["enum_values"]}
        else:
            value = {"type": {"number": "number", "integer": "integer", "array": "array"}.get(kind, "string")}
        if "minimum" in parameter:
            value["minimum"] = parameter["minimum"]
        if "maximum" in parameter:
            value["maximum"] = parameter["maximum"]
        if "default" in parameter:
            value["default"] = deepcopy(parameter["default"])
        if parameter.get("resolve") in {"entity", "participant"}:
            value["x-sylvia-gm-reference"] = parameter["resolve"]
        if kind == "array":
            value["items"] = parameter["items"]
            value["minItems"] = parameter["min_items"]
            value["maxItems"] = parameter["max_items"]
        properties[parameter["name"]] = value
        if parameter.get("required", False):
            required.append(parameter["name"])
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _action_guard_parts(name: str) -> list[tuple[str, str]]:
    """Return non-spatial effect prerequisites shared with player-control checks."""
    actor_name = (
        "attacker" if name in {"aim", "fire_single", "fire_burst", "fire_full_auto"} else "actor"
    )
    common_weapon = [
        (f"{actor_name}.weapon.location == 'READY'", "This actor's weapon is not ready."),
        (
            f"{actor_name}.weapon.controlled_by == null",
            "Another actor currently controls this weapon.",
        ),
    ]
    return {
        "move_actor": [
            ("actor.ap >= 1", "This actor has no action points remaining."),
            ("actor.active == true", "This actor is not active."),
        ],
        "change_stance": [
            ("actor.active == true", "This actor is not active."),
            ("actor.ap >= definitions.parameters.move.stance_change_ap", "This actor does not have enough action points to change stance."),
            ("actor.stance != stance", "This actor is already in that stance."),
        ],
        "aim": [
            ("attacker.ap >= ap", "This actor does not have enough action points to aim."),
            ("attacker.active == true", "This actor is not active."),
            *common_weapon,
        ],
        "fire_single": [
            (
                "attacker.ap >= definitions.parameters.fire.semi_single.ap",
                "This actor does not have enough action points to fire.",
            ),
            (
                "attacker.weapon.chamber == 'LIVE'",
                "This weapon does not have a live round chambered.",
            ),
            *common_weapon,
            ("attacker.active == true", "This actor is not active."),
            ("target.active == true", "The selected target is not active."),
        ],
        "fire_burst": [
            (
                "attacker.ap >= definitions.parameters.fire.burst_3.ap",
                "This actor does not have enough action points to fire a burst.",
            ),
            (
                "attacker.weapon.chamber == 'LIVE'",
                "This weapon does not have a live round chambered.",
            ),
            (
                "attacker.weapon.magazine.rounds + 1 >= 3",
                "This weapon does not have three rounds available.",
            ),
            ("'BURST' in attacker.weapon.fire_modes", "This weapon cannot fire in burst mode."),
            *common_weapon,
            ("attacker.active == true", "This actor is not active."),
            ("target.active == true", "The selected target is not active."),
        ],
        "fire_full_auto": [
            (
                "attacker.ap >= max(1, (rounds + definitions.parameters.fire.full_auto.rounds_per_ap - 1) // definitions.parameters.fire.full_auto.rounds_per_ap)",
                "This actor does not have enough action points for the selected burst.",
            ),
            (
                "attacker.weapon.chamber == 'LIVE'",
                "This weapon does not have a live round chambered.",
            ),
            (
                "attacker.weapon.magazine.rounds + 1 >= rounds",
                "This weapon does not have the selected number of rounds available.",
            ),
            (
                "'FULL_AUTO' in attacker.weapon.fire_modes",
                "This weapon cannot fire in full-auto mode.",
            ),
            *common_weapon,
            ("attacker.active == true", "This actor is not active."),
            ("target.active == true", "The selected target is not active."),
        ],
        "prepare_overwatch": [
            (
                "actor.ap >= definitions.parameters.overwatch.setup_ap",
                "This actor does not have enough action points to prepare overwatch.",
            ),
            ("actor.weapon.chamber == 'LIVE'", "This weapon does not have a live round chambered."),
            *common_weapon,
        ],
        "establish_suppression": [
            (
                "actor.ap >= definitions.parameters.suppression.setup_ap",
                "This actor does not have enough action points to establish suppression.",
            ),
            ("actor.weapon.chamber == 'LIVE'", "This weapon does not have a live round chambered."),
            (
                "actor.weapon.magazine.rounds + 1 >= rounds",
                "This weapon does not have the selected number of rounds available.",
            ),
            (
                "'FULL_AUTO' in actor.weapon.fire_modes",
                "This weapon cannot fire in full-auto mode.",
            ),
            *common_weapon,
        ],
        "cycle_weapon": [
            ("actor.weapon != null", "This actor has no current firearm."),
            (
                "actor.ap >= actor.weapon.cycle_ap",
                "This actor does not have enough action points to cycle the weapon.",
            ),
            ("actor.weapon.cycle_ap > 0", "This weapon cannot be cycled manually."),
            ("actor.weapon.needs_cycle == true", "This weapon does not need to be cycled."),
            *common_weapon,
        ],
        "fpv_fragmentation_strike": [
            (
                "actor.ap >= definitions.parameters.equipment.fpv_fragmentation_payload.attack_ap",
                "This actor does not have enough action points to launch the FPV strike.",
            ),
            ("actor.active == true", "This actor is not active."),
            ("drone.active == true", "The selected drone is not active."),
            ("drone.kind == 'FPV_DRONE'", "The selected unit is not an FPV drone."),
            ("drone.controlled_by == _actor_id", "This actor does not control the selected drone."),
            (
                "drone.payload == 'FPV_FRAGMENTATION'",
                "The selected drone has no fragmentation payload.",
            ),
            ("target.active == true", "The selected target is not active."),
        ],
        "autodrone_fire": [
            (
                "actor.ap >= definitions.parameters.equipment.autodrone_light_weapon.attack_ap_equivalent",
                "This drone does not have enough actions remaining to fire.",
            ),
            ("actor.active == true", "This drone is not active."),
            ("actor.kind == 'AUTONOMOUS_DRONE'", "The selected unit is not an autonomous drone."),
            (
                "actor.weapon.module == 'AUTONOMOUS_LIGHT_WEAPON'",
                "This drone has no autonomous light weapon.",
            ),
            (
                "actor.weapon.magazine.rounds > 0",
                "This drone's weapon has no ammunition remaining.",
            ),
            ("actor.standing_order.active == true", "This drone has no active standing order."),
            (
                "actor.standing_order.authorized_fire == true",
                "This drone is not authorized to fire.",
            ),
            (
                "actor.standing_order.target == _target_id",
                "The selected target is outside this drone's standing order.",
            ),
            (
                "_target_id in actor.sensor_confirmed_targets",
                "This drone has not confirmed the selected target.",
            ),
            ("target.active == true", "The selected target is not active."),
            ("target.downed == false", "The selected target is already downed."),
        ],
    }.get(name, [])


def _action_guard(name: str) -> str:
    return " and ".join(condition for condition, _reason in _action_guard_parts(name))


def _move_path_query(*, snapshot: str | None = None) -> dict[str, Any]:
    """Return the one bounded route query shared by movement and player admission."""
    query: dict[str, Any] = {
        "scene": "state.spatial_scene",
        "operation": "path",
        "query": {
            "map_id": "{map_id}",
            "start": "{actor.position_cell}",
            "goal": "{destination}",
            "allow_diagonal": True,
            "corner_policy": "forbid_cutting",
            "include_entities": True,
            "moving_entity_id": "{_actor_id}",
            "max_steps": 256,
        },
    }
    if snapshot is not None:
        query["snapshot"] = snapshot
    return {"spatial_query": query, "store_as": "_path"}


def _move_ap_expression() -> str:
    """Return TD's existing route-distance to AP conversion unchanged."""
    return "(_path.cost + actor.movement_m_per_ap - 0.000001) // actor.movement_m_per_ap"


def _stance_effects(actor_id: str, stance: str) -> list[dict[str, Any]]:
    """Update posture and its physical representation together, preserving private metadata."""
    actor = "actor" if actor_id == "_actor_id" else "target"
    return [
        {"spatial_query": {"scene": "state.spatial_scene", "operation": "snapshot"}, "store_as": "_stance_snapshot"},
        {"set": {"field": "_stance_profile", "value": f"definitions.stances[{stance}]"}},
        {"set": {"field": "_stance_height", "value": "_stance_profile.height"}},
        {"set": {"field": "_stance_metadata", "value": {}}},
        {"set": {"field": "_stance_movement", "value": f"{actor}.movement_m_per_ap * definitions.stances[{actor}.stance].movement_m_per_ap ** -1 * _stance_profile.movement_m_per_ap"}},
        {"for_each": {"collection": "stance_bodies", "as": "_stance_body", "do": [
            {"condition": f"_stance_body.id == {actor_id}", "on_true": [
                {"set": {"field": "_stance_metadata", "value": "_stance_body.metadata"}},
                {"set": {"field": "_stance_height", "value": f"_stance_body.height * definitions.stances[{actor}.stance].height ** -1 * _stance_profile.height"}},
                {"set": {"field": "_stance_origin", "value": [f"{actor}.position_cell[0] + 0.5", f"{actor}.position_cell[1] + 0.5", "_stance_body.position[2]"]}},
                {"condition": "_stance_body.metadata.player != null", "on_true": [
                    {"set": {"field": "_stance_label", "value": {"stance": "_stance_profile.label"}}},
                    {"set": {"field": "_stance_player", "value": {"player": "merge(_stance_body.metadata.player, _stance_label)"}}},
                    {"set": {"field": "_stance_metadata", "value": "merge(_stance_body.metadata, _stance_player)"}},
                ]},
            ]},
        ]}},
        # Mutate the exact map body before actor fields; a missing body rejects the whole action.
        {"spatial_mutation": {"scene": "state.spatial_scene", "map_id": "{map_id}", "expected_revision": "{_stance_snapshot.geometry_revision}", "target": {"kind": "entity", "id": f"{{{actor_id}}}"}, "patch": {"height": "{_stance_height}", "metadata": "{_stance_metadata}"}}, "store_as": "_stance_scene"},
        {"set": {"field": f"{{{actor_id}}}.stance", "value": stance}},
        {"set": {"field": f"{{{actor_id}}}.movement_m_per_ap", "value": "_stance_movement"}},
        {"set": {"field": f"{{{actor_id}}}.muzzle", "value": [f"_stance_origin[0] + {actor}.muzzle[0] - {actor}.center[0]", f"_stance_origin[1] + {actor}.muzzle[1] - {actor}.center[1]", "_stance_origin[2] + _stance_height * 0.7777777777777778"]}},
        {"set": {"field": f"{{{actor_id}}}.center", "value": ["_stance_origin[0]", "_stance_origin[1]", "_stance_origin[2] + _stance_height * 0.5"]}},
    ]


def _firing_exposure_query(
    *, store_as: str, snapshot: str | None = None,
) -> dict[str, Any]:
    """Return the firing solution's existing bounded silhouette query."""
    query: dict[str, Any] = {
        "scene": "state.spatial_scene",
        "operation": "exposure",
        "query": {
            "map_id": "{map_id}",
            "observer": {"point": "{attacker.muzzle}"},
            "target": {"entity_id": "{_target_id}"},
            "samples": {"horizontal": 3, "vertical": 3},
            "include": ["geometry", "entities"],
        },
    }
    if snapshot is not None:
        query["snapshot"] = snapshot
    return {"spatial_query": query, "store_as": store_as}


def _firing_ray_query(
    *, store_as: str, snapshot: str | None = None,
    include: tuple[str, ...] = ("geometry", "entities"),
) -> dict[str, Any]:
    """Return the firing solution's existing bounded muzzle-origin ray."""
    query: dict[str, Any] = {
        "scene": "state.spatial_scene",
        "operation": "ray",
        "query": {
            "map_id": "{map_id}",
            "origin": "{attacker.muzzle}",
            "destination": "{aim_point}",
            "include": list(include),
            "max_intersections": 64,
        },
    }
    if snapshot is not None:
        query["snapshot"] = snapshot
    return {"spatial_query": query, "store_as": store_as}


def _prepared_zone_check() -> list[dict[str, Any]]:
    """Resolve an authored zone against the current map before spending prepared-fire resources."""
    return [
        {"set": {"field": "_prepared_zone_exists", "value": "false"}},
        {"for_each": {"collection": "prepared_zones", "as": "_prepared_zone", "do": [
            {"set": {"field": "_prepared_zone_exists", "value": "true"},
             "condition": "_prepared_zone.id == zone_id and _prepared_zone.map_id == map_id"},
        ]}},
    ]


def _tool_specs(
    specialty_node_ids: list[str],
    specialty_branch_ids: list[str],
) -> dict[str, dict[str, Any]]:
    actor = _param("actor", "string", resolve="entity")
    attacker = _param("attacker", "string", resolve="entity")
    target = _param("target", "string", resolve="entity")
    cell = lambda name: _param(name, "array", items="integer", min_items=2, max_items=2)
    point = lambda name: _param(name, "array", items="number", min_items=3, max_items=3)
    specs = {
        "resolve_check": {
            "capability": "trigger-discipline.checks",
            "description": "Resolve one warranted Trigger Discipline CHECK.",
            "parameters": [
                actor,
                _param("skill", "enum", enum=("athletics", "cqc", "drone_operations", "engineering", "grenades", "influence", "investigation", "local_hacking", "marksmanship", "medicine", "networking", "recon", "social_read", "stealth", "tactics", "vehicles")),
                _param("aptitude", "enum", enum=("acuity", "force", "nerve", "precision", "presence", "reason")),
                _param("difficulty", "integer", minimum=1, maximum=60),
                _param("modifier", "integer", minimum=-20, maximum=20),
            ],
            "effects": [
                {"dice": {"expression": "d20"}, "store_as": "_check"},
                {"set": {"field": "_check_total", "value": "_check.total + actor.skills[skill] + actor.aptitudes[aptitude] + modifier"}},
                {"set": {"field": "state.last_check.actor", "value": "_actor_id"}},
                {"set": {"field": "state.last_check.roll", "value": "_check.total"}},
                {"set": {"field": "state.last_check.total", "value": "_check_total"}},
                {"set": {"field": "state.last_check.difficulty", "value": "difficulty"}},
                {"set": {"field": "state.last_check.success", "value": "false"}},
                {"set": {"field": "state.last_check.success", "value": "true"}, "condition": "_check_total >= difficulty"},
                {"log": {"message": "CHECK {_check_total} against {difficulty}", "level": "mechanic"}},
            ],
            "costs_action": False,
        },
        "move_actor": {
            "capability": "trigger-discipline.spatial",
            "description": (
                "Move the actor along a validated path and spend its movement AP. "
                "Destination is a zero-based [x, y] cell: increasing x moves right on Sylvia's map; "
                "increasing y moves down. Start from the actor's current position_cell. "
                "Use the authored scene for compass directions and the returned origin and "
                "destination for narration."
            ),
            "parameters": [actor, cell("destination")],
            "effects": [
                {
                    "condition": _action_guard("move_actor"),
                    "on_true": [
                        {"spatial_query": {"scene": "state.spatial_scene", "operation": "snapshot"}, "store_as": "_move_snapshot"},
                        _move_path_query(snapshot="{_move_snapshot}"),
                        {"set": {"field": "_move_ap", "value": _move_ap_expression()}, "condition": "_path.found == true"},
                        {
                            "condition": "_path.found == true and actor.ap >= _move_ap",
                            "on_true": [
                                {"set": {"field": "state.last_move.origin", "value": "actor.position_cell"}},
                                {"set": {"field": "_destination_position", "value": ["destination[0] + 0.5", "destination[1] + 0.5", 0]}},
                                {"set": {"field": "_destination_center", "value": ["destination[0] + 0.5", "destination[1] + 0.5", "actor.center[2]"]}},
                                {"set": {"field": "_destination_muzzle", "value": ["destination[0] + 0.91", "destination[1] + 0.5", "actor.muzzle[2]"]}},
                                {"set": {"field": "_destination_muzzle", "value": ["destination[0] - 0.09", "destination[1] + 0.5", "actor.muzzle[2]"]}, "condition": "actor.side == 'hostiles'"},
                                {"spatial_mutation": {"scene": "state.spatial_scene", "map_id": "{map_id}", "expected_revision": "{_move_snapshot.geometry_revision}", "target": {"kind": "entity", "id": "{_actor_id}"}, "patch": {"position": "{_destination_position}"}}, "store_as": "_move_scene"},
                                {"set": {"field": "{_actor_id}.position_cell", "value": "destination"}},
                                {"condition": "_path.cost > 0", "on_true": [
                                    *network_entry.movement_effects(),
                                    *weapon_handling.end_suppression(),
                                ], "on_false": []},
                                {"set": {"field": "{_actor_id}.center", "value": "_destination_center"}},
                                {"set": {"field": "{_actor_id}.muzzle", "value": "_destination_muzzle"}},
                                {"decrement": {"field": "{_actor_id}.ap", "amount": "_move_ap"}},
                                {"set": {"field": "state.focused_actor", "value": "_actor_id"}},
                                {"set": {"field": "state.last_move.actor", "value": "_actor_id"}},
                                {"set": {"field": "state.last_move.cost", "value": "_path.cost"}},
                                {"set": {"field": "state.last_move.ap", "value": "_move_ap"}},
                                {"set": {"field": "state.last_move.geometry_revision", "value": "_move_scene.geometry_revision"}},
                                {"set": {"field": "state.last_move.status", "value": "'MOVED'"}},
                            ],
                            "on_false": [{"set": {"field": "state.last_move.status", "value": "'ILLEGAL_OR_UNREACHABLE'"}}],
                        },
                    ],
                    "on_false": [{"set": {"field": "state.last_move.status", "value": "'NO_AP_OR_INACTIVE'"}}],
                },
            ],
            "costs_action": True,
        },
        "change_stance": {
            "capability": "trigger-discipline.spatial",
            "description": "Change an active actor's posture, spend the declared AP cost, and update its map height and movement rate.",
            "parameters": [actor, _param("stance", "enum", enum=("STANDING", "CROUCHED", "PRONE"))],
            "effects": [
                {"set": {"field": "_stance_changed", "value": "false"}},
                {"condition": _action_guard("change_stance"), "on_true": [
                    *_stance_effects("_actor_id", "stance"),
                    {"decrement": {"field": "{_actor_id}.ap", "amount": "definitions.parameters.move.stance_change_ap"}},
                    {"set": {"field": "state.focused_actor", "value": "_actor_id"}},
                    {"set": {"field": "_stance_changed", "value": "true"}},
                ]},
            ],
            "costs_action": True,
        },
        "aim": {
            "capability": "trigger-discipline.firearms",
            "description": "Spend AP to maintain a bounded firing solution.",
            "parameters": [attacker, target, point("aim_point"), _param("ap", "integer", minimum=1, maximum=2)],
            "effects": [
                {
                    "condition": _action_guard("aim"),
                    "on_true": [
                        {"decrement": {"field": "{_attacker_id}.ap", "amount": "ap"}},
                        {"set": {"field": "{_attacker_id}.aim.target", "value": "_target_id"}},
                        {"set": {"field": "{_attacker_id}.aim.point", "value": "aim_point"}},
                        {"set": {"field": "{_attacker_id}.aim.ap", "value": "ap"}},
                        {"set": {"field": "state.focused_actor", "value": "_attacker_id"}},
                    ],
                    "on_false": [{"set": {"field": "state.last_attack.outcome", "value": "'AIM_REJECTED'"}}],
                }
            ],
            "costs_action": True,
        },
        "fire_single": {
            "capability": "trigger-discipline.firearms",
            "description": "Resolve one deliberate projectile through authoritative exposure and ordered-ray facts, HIT QUALITY, target PENETRATION, RES, and HP.",
            "parameters": [
                attacker,
                target,
                point("aim_point"),
                _param("critical_effect", "enum", enum=("DEEP_HIT", "DEVASTATING_HIT")),
            ],
            "effects": _fire_single_effects(),
            "costs_action": True,
        },
        "fire_burst": {
            "capability": "trigger-discipline.firearms",
            "description": "Resolve one three-round BURST with one ATTACK ROLL, finite connection opportunities, and fresh authoritative geometry for every relevant projectile.",
            "parameters": [
                attacker,
                target,
                point("aim_point"),
                _param("critical_effect", "enum", enum=("DEEP_HIT", "DEVASTATING_HIT")),
            ],
            "effects": _fire_multi_effects("BURST"),
            "costs_action": True,
        },
        "fire_full_auto": {
            "capability": "trigger-discipline.firearms",
            "description": "Resolve one flexible FULL AUTO string with one ATTACK ROLL, finite connection opportunities, and fresh authoritative geometry for every relevant projectile.",
            "parameters": [
                attacker,
                target,
                point("aim_point"),
                _param("rounds", "integer", minimum=3, maximum=20),
                _param("critical_effect", "enum", enum=("DEEP_HIT", "DEVASTATING_HIT")),
            ],
            "effects": _fire_multi_effects("FULL_AUTO"),
            "costs_action": True,
        },
        "recover_recoil": {
            "capability": "trigger-discipline.firearms",
            "description": "Spend genuine non-firing AP to recover persistent RECOIL.",
            "parameters": [actor, _param("ap", "integer", minimum=1, maximum=6)],
            "effects": [
                {
                    "condition": "actor.weapon != null and actor.ap >= ap and actor.weapon.current_recoil > 0",
                    "on_true": [
                        *weapon_handling.end_suppression(),
                        {"set": {"field": "_recovered", "value": "min(actor.weapon.current_recoil, ap * actor.recoil_recovery_per_ap)"}},
                        {"decrement": {"field": "{_actor_id}.ap", "amount": "ap"}},
                        {"decrement": {"field": "{_actor_id}.weapon.current_recoil", "amount": "_recovered"}},
                    ],
                    "on_false": [{"set": {"field": "state.last_attack.outcome", "value": "'RECOIL_RECOVERY_REJECTED'"}}],
                }
            ],
            "costs_action": True,
        },
        "cycle_weapon": {
            "capability": "trigger-discipline.firearms",
            "description": "Spend the reference weapon's declared cycle AP to eject the spent chamber state and feed the next round for a manual-action firearm.",
            "parameters": [actor],
            "effects": _cycle_weapon_effects(),
            "costs_action": True,
        },
        "reload_weapon": {
            "capability": "trigger-discipline.firearms",
            "description": "Resolve one detachable-magazine change while preserving the ejected magazine as retained inventory or a ground object.",
            "parameters": [
                actor,
                _param("disposition", "enum", enum=("RETAIN", "DROP")),
                _param(
                    "ground_object_sequence",
                    "integer",
                    from_state="state.ground_object_sequence",
                ),
            ],
            "effects": _reload_weapon_effects(),
            "costs_action": True,
        },
        "prepare_overwatch": {
            "capability": "trigger-discipline.triggers",
            "description": "Spend AP and reserve a normal attack against the authored OVERWATCH zone.",
            "parameters": [actor, _param("zone_id", "string"),
                           _param("prepared_zones", "array", items="object", from_state="state.spatial_scene.zones")],
            "effects": [
                *_prepared_zone_check(),
                {
                    "condition": "_prepared_zone_exists == true and " + _action_guard("prepare_overwatch"),
                    "on_true": [
                        {"decrement": {"field": "{_actor_id}.ap", "amount": "definitions.parameters.overwatch.setup_ap"}},
                        {"set": {"field": "state.prepared.overwatch.owner", "value": "_actor_id"}},
                        {"set": {"field": "state.prepared.overwatch.zone_id", "value": "zone_id"}},
                        {"set": {"field": "state.prepared.overwatch.shots", "value": "definitions.parameters.overwatch.shot_allowance"}},
                        {"set": {"field": "state.prepared.overwatch.active", "value": "true"}},
                    ],
                    "on_false": [{"set": {"field": "state.last_trigger.status", "value": "'PREPARATION_REJECTED'"}}],
                }
            ],
            "costs_action": True,
        },
        "establish_suppression": {
            "capability": "trigger-discipline.triggers",
            "description": "Spend AP and real ammunition to establish active fire over the authored suppression zone; this creates no SUPPRESSED condition.",
            "parameters": [
                actor,
                _param("zone_id", "string"),
                _param("rounds", "integer", minimum=5, maximum=30),
                _param("prepared_zones", "array", items="object", from_state="state.spatial_scene.zones"),
                _param("suppression_zones", "array", items="object", from_state="state.spatial_scene.zones"),
            ],
            "effects": [
                *_prepared_zone_check(),
                {
                    "condition": "_prepared_zone_exists == true and " + _action_guard("establish_suppression"),
                    "on_true": [
                        *_suppression_setup_presentation_effects(),
                        {"decrement": {"field": "{_actor_id}.ap", "amount": "definitions.parameters.suppression.setup_ap"}},
                        {"set": {"field": "_pre_suppression_magazine_rounds", "value": "actor.weapon.magazine.rounds"}},
                        {"set": {"field": "{_actor_id}.weapon.magazine.rounds", "value": "max(0, _pre_suppression_magazine_rounds - rounds)"}},
                        {"set": {"field": "{_actor_id}.weapon.chamber", "value": "'EMPTY'"}, "condition": "_pre_suppression_magazine_rounds < rounds"},
                        {"increment": {"field": "{_actor_id}.weapon.current_recoil", "amount": "actor.weapon.recoil + (rounds + definitions.parameters.recoil.full_auto_round_step - 1) // definitions.parameters.recoil.full_auto_round_step - 1"}},
                        {"set": {"field": "state.prepared.suppression.owner", "value": "_actor_id"}},
                        {"set": {"field": "state.prepared.suppression.zone_id", "value": "zone_id"}},
                        {"set": {"field": "state.prepared.suppression.capacity", "value": "max(1, rounds // definitions.parameters.suppression.exposure_capacity_round_divisor)"}},
                        {"set": {"field": "state.prepared.suppression.active", "value": "true"}},
                    ],
                    "on_false": [{"set": {"field": "state.last_trigger.status", "value": "'PREPARATION_REJECTED'"}}],
                }
            ],
            "costs_action": True,
        },
        "inspect_trigger_crossing": {
            "capability": "trigger-discipline.triggers",
            "description": "Return exact ordered zone crossings for one intended movement path so trigger resolution can pause at the first physical crossing.",
            "parameters": [actor, point("start"), point("end")],
            "effects": [
                {"spatial_query": {"scene": "state.spatial_scene", "operation": "snapshot"}, "store_as": "_snapshot"},
                {"spatial_query": {"scene": "state.spatial_scene", "operation": "crossed_zones", "snapshot": "{_snapshot}", "query": {"map_id": "{map_id}", "path": ["{start}", "{end}"], "max_crossings": 32}}, "store_as": "_crossings"},
                {"set": {"field": "state.last_trigger.actor", "value": "_actor_id"}},
                {"set": {"field": "state.last_trigger.count", "value": "0"}},
                {"set": {"field": "state.last_trigger.count", "value": "1"}, "condition": "_crossings.crossings != null"},
            ],
            "costs_action": False,
        },
        "resolve_cqc": {
            "capability": "trigger-discipline.cqc",
            "description": "Resolve one declared non-displacing CQC result after an authoritative adjacent, unobstructed reach query.",
            "parameters": [
                actor,
                target,
                _param(
                    "maneuver",
                    "enum",
                    enum=("TRIP", "DISARM", "BODY_CONTROL", "WEAPON_CONTROL", "ESCAPE"),
                ),
                _param(
                    "held_item",
                    "enum",
                    required=False,
                    enum=("PRIMARY_WEAPON",),
                ),
            ],
            "effects": _cqc_effects(),
            "costs_action": True,
        },
        "resolve_cqc_displacement": {
            "capability": "trigger-discipline.cqc",
            "description": "Resolve SHOVE or THROW only when the exact forced-movement path and destination remain physically valid.",
            "parameters": [
                actor,
                target,
                _param("maneuver", "enum", enum=("SHOVE", "THROW")),
                cell("destination"),
            ],
            "effects": _cqc_displacement_effects(),
            "costs_action": True,
        },
        "treat_casualty": medical.specification(actor, _param),
        "resolve_blast_snapshot": {
            "capability": "trigger-discipline.explosives",
            "description": "Freeze pre-detonation geometry and resolve the declared blast area against that immutable snapshot.",
            "parameters": [actor, point("center"), _param("radius", "number", minimum=0.5, maximum=30)],
            "effects": [
                {"spatial_query": {"scene": "state.spatial_scene", "operation": "snapshot"}, "store_as": "_blast_snapshot"},
                {"spatial_query": {"scene": "state.spatial_scene", "operation": "area", "snapshot": "{_blast_snapshot}", "query": {"map_id": "{map_id}", "shape": {"kind": "cylinder", "center": "{center}", "radius": "{radius}", "height": 3}, "include": ["geometry", "entities", "zones"], "max_results": 128}}, "store_as": "_blast_area"},
                {"set": {"field": "state.last_blast.actor", "value": "_actor_id"}},
                {"set": {"field": "state.last_blast.snapshot_id", "value": "_blast_snapshot.snapshot_id"}},
                {"set": {"field": "state.last_blast.geometry_revision", "value": "_blast_snapshot.geometry_revision"}},
                {"set": {"field": "state.last_blast.status", "value": "'SNAPSHOT_RESOLVED'"}},
            ],
            "costs_action": False,
        },
        "resolve_trigger_crossing": {
            "capability": "trigger-discipline.triggers",
            "description": "Move to the first exact prepared-fire crossing, resolve OVERWATCH and SUPPRESSION in declared same-controller order with fresh geometry between effects, then resume only if the mover remains capable.",
            "parameters": [
                actor,
                _param("overwatcher", "string", resolve="entity"),
                _param("suppressor", "string", resolve="entity"),
                cell("destination"),
                _param("first_effect", "enum", enum=("OVERWATCH", "SUPPRESSION")),
                _param("overwatch_critical_effect", "enum", enum=("DEEP_HIT", "DEVASTATING_HIT")),
                _param("suppression_critical_effect", "enum", enum=("DEEP_HIT", "DEVASTATING_HIT")),
                _param("prepared_fire", "object", from_state="state.prepared"),
            ],
            "effects": _trigger_crossing_effects(),
            "costs_action": True,
        },
        "fpv_fragmentation_strike": {
            "capability": "trigger-discipline.drones",
            "description": "Commit one manually controlled FPV fragmentation attack run, resolve placement through real geometry, freeze the detonation snapshot, and consume the payload and drone.",
            "parameters": [
                actor,
                _param("drone", "string", resolve="entity"),
                target,
                point("landing_point"),
            ],
            "effects": _fpv_fragmentation_effects(),
            "costs_action": True,
        },
        "autodrone_fire": {
            "capability": "trigger-discipline.drones",
            "description": "Resolve one autonomous light-weapon shot only under a valid sensor-confirmed standing order using ordinary firearm, projectile, cover, ammunition, and recoil rules.",
            "parameters": [actor, target, point("aim_point")],
            "effects": _autodrone_fire_effects(),
            "costs_action": True,
        },
        "network_connection": {
            "capability": "trigger-discipline.network",
            "description": "Connect at an authored stationary local interface or through this actor's actual remote-service connection, paying its explicit cost, or disconnect. A remote connection must already exist in authored state; visibility, skill or a credential does not create connectivity. Connection alone grants no session or privilege.",
            "parameters": network_entry.parameters(_param),
            "effects": network_entry.effects(),
            "costs_action": True,
        },
        "respond_network_defense": {
            "capability": "trigger-discipline.hacking",
            "description": "GM-controlled defender takes one authored response using its real turn, evidence and AP. Automated responses run only from authored monitoring triggers.",
            "parameters": network_security.defense_parameters(_param),
            "effects": network_security.defense_effects(),
            "costs_action": True,
        },
        "preserve_network_route": {
            "capability": "trigger-discipline.hacking",
            "description": "Use ROUTE PERSISTENCE after one link loss to move one surviving session onto a known valid alternate route. Requires its specialty and explicit authored cost; does not restore an ended session or reroute an entire chain.",
            "parameters": network_intrusion.parameters(_param, pivot=True),
            "effects": network_reroute.effects(),
            "costs_action": True,
        },
        "intrude_network": {
            "capability": "trigger-discipline.network",
            "description": "Resolve one authored entry, discovery, escalation or foothold attempt for the selected actor; its cost and defended barrier come from current state.",
            "parameters": network_intrusion.parameters(_param),
            "effects": network_intrusion.effects(),
            "costs_action": True,
        },
        "pivot_network": {
            "capability": "trigger-discipline.network",
            "description": "Establish access through one selected real connection from this actor's live session. Authorized traversal is automatic; a defended link uses its authored barrier.",
            "parameters": network_intrusion.parameters(_param, pivot=True),
            "effects": network_intrusion.effects(pivot=True),
            "costs_action": True,
        },
        "operate_remote_function": {
            "capability": "trigger-discipline.network",
            "description": "Operate an authored physical device through this actor's live route and privilege, paying its authored operational cost without an intrusion roll.",
            "parameters": network_functions.operation_parameters(_param),
            "effects": network_functions.operation_effects(),
            "costs_action": True,
        },
        "exercise_specialty_counterfactual": {
            "capability": "trigger-discipline.specialty-testing",
            "description": "Execute one approved same-character specialty counterfactual from the hash-bound testing manifest without spending combat AP or claiming a live gameplay hook.",
            "parameters": [
                actor,
                _param("node_id", "enum", enum=tuple(specialty_node_ids)),
                _param("role", "enum", enum=("PRIMARY", "SECONDARY", "OFF_ROLE")),
                _param("benchmark_level", "integer", minimum=1, maximum=20),
                _param(
                    "prerequisite_mode",
                    "enum",
                    enum=("SATISFIED", "FIRST_MISSING"),
                ),
                _param(
                    "boundary_mode",
                    "enum",
                    required=False,
                    default="SATISFIED",
                    enum=(
                        "SATISFIED",
                        "FIRST_GUARD_VIOLATED",
                        "FIRST_TERMINATION",
                        "FIRST_INVARIANT_MUTATION",
                    ),
                ),
                _param(
                    "effect_branch",
                    "enum",
                    required=False,
                    default="DEFAULT",
                    enum=tuple(specialty_branch_ids),
                ),
            ],
            "effects": [
                {"set": {"field": "state.last_specialty_test", "value": {}}},
                {"table_lookup": {"table": "definitions.specialty_counterfactuals", "key": "node_id", "required": True}, "store_as": "_specialty_rule"},
                {"set": {"field": "_specialty_branch", "value": "_specialty_rule.branch_fixtures[effect_branch]"}},
                {"set": {"field": "state.last_specialty_test.actor", "value": "_actor_id"}},
                {"set": {"field": "state.last_specialty_test.node_id", "value": "node_id"}},
                {"set": {"field": "state.last_specialty_test.specialty", "value": "_specialty_rule.specialty"}},
                {"set": {"field": "state.last_specialty_test.mechanic_id", "value": "_specialty_rule.mechanic_id"}},
                {"set": {"field": "state.last_specialty_test.role", "value": "role"}},
                {"set": {"field": "state.last_specialty_test.benchmark_level", "value": "benchmark_level"}},
                {"set": {"field": "state.last_specialty_test.parameter_ids", "value": "_specialty_rule.parameter_ids"}},
                {"set": {"field": "state.last_specialty_test.changed_fields", "value": "_specialty_branch.changed_fields"}},
                {"set": {"field": "state.last_specialty_test.effect_branch", "value": "_specialty_branch.selected_branch"}},
                {"set": {"field": "state.last_specialty_test.source", "value": "_specialty_rule.source"}},
                {"set": {"field": "state.last_specialty_test.authored_rule_contract", "value": "_specialty_rule.authored_rule_contract"}},
                {"set": {"field": "state.last_specialty_test.authored_constraint_fields", "value": "_specialty_rule.authored_constraint_fields"}},
                {"set": {"field": "state.last_specialty_test.authored_effect_fields", "value": "_specialty_rule.authored_effect_fields"}},
                {"set": {"field": "state.last_specialty_test.authored_guard_fields", "value": "_specialty_rule.authored_guard_fields"}},
                {"set": {"field": "state.last_specialty_test.authored_termination_fields", "value": "_specialty_rule.authored_termination_fields"}},
                {"set": {"field": "state.last_specialty_test.authored_invariant_fields", "value": "_specialty_rule.authored_invariant_fields"}},
                {"set": {"field": "state.last_specialty_test.control", "value": "_specialty_rule.control"}},
                {"set": {"field": "state.last_specialty_test.result", "value": "_specialty_rule.control"}},
                {"set": {"field": "state.last_specialty_test.mechanical_operations_executed", "value": []}},
                {"set": {"field": "state.last_specialty_test.limitation_contract", "value": "_specialty_rule.limitation_contract"}},
                {"set": {"field": "state.last_specialty_test.limitation_results", "value": "_specialty_rule.limitation_results"}},
                {"set": {"field": "state.last_specialty_test.limitations_preserved", "value": "_specialty_rule.limitations_preserved"}},
                {"set": {"field": "state.last_specialty_test.prerequisite_contract", "value": "_specialty_branch.prerequisite_contract"}},
                {"set": {"field": "state.last_specialty_test.prerequisite_fixture", "value": "_specialty_branch.prerequisite_fixture"}},
                {"set": {"field": "state.last_specialty_test.prerequisite_fixture", "value": "_specialty_branch.negative_prerequisite_fixture"}, "condition": "prerequisite_mode == 'FIRST_MISSING'"},
                {"set": {"field": "state.last_specialty_test.prerequisite_mode", "value": "prerequisite_mode"}},
                {"set": {"field": "state.last_specialty_test.boundary_mode", "value": "boundary_mode"}},
                {"set": {"field": "_specialty_level_available", "value": "benchmark_level in _specialty_rule.available_benchmark_levels"}},
                {"set": {"field": "_specialty_level_matches", "value": "benchmark_level == actor.level"}},
                {"set": {"field": "_specialty_class_matches", "value": "actor.class_id == _specialty_rule.class_id"}},
                {"set": {"field": "_specialty_actor_has_node", "value": "node_id in actor.specialties"}},
                {"set": {"field": "_specialty_prerequisites_satisfied", "value": "prerequisite_mode != 'FIRST_MISSING'"}},
                {"set": {"field": "_specialty_fixture_eligible", "value": "_specialty_level_available == true and _specialty_level_matches == true and _specialty_class_matches == true and role != 'OFF_ROLE' and _specialty_actor_has_node == true and _specialty_prerequisites_satisfied == true"}},
                {"set": {"field": "_specialty_boundary_fixture", "value": "null"}},
                {"set": {"field": "_specialty_applied", "value": "false"}},
                {"set": {"field": "state.last_specialty_test.prerequisites_satisfied", "value": "_specialty_prerequisites_satisfied"}},
                {"set": {"field": "state.last_specialty_test.node_effect_applied", "value": "false"}},
                {"set": {"field": "state.last_specialty_test.status", "value": "'UNAVAILABLE_AT_LEVEL'"}},
                {"set": {"field": "state.last_specialty_test.status", "value": "'LEVEL_MISMATCH'"}, "condition": "_specialty_level_available == true and _specialty_level_matches == false"},
                {"set": {"field": "state.last_specialty_test.status", "value": "'WRONG_CLASS'"}, "condition": "_specialty_level_available == true and _specialty_level_matches == true and _specialty_class_matches == false"},
                {"set": {"field": "state.last_specialty_test.status", "value": "'ACTOR_MISSING_NODE'"}, "condition": "_specialty_level_available == true and _specialty_level_matches == true and _specialty_class_matches == true and _specialty_actor_has_node == false"},
                {"set": {"field": "state.last_specialty_test.status", "value": "'MISSING_PREREQUISITE'"}, "condition": "_specialty_level_available == true and _specialty_level_matches == true and _specialty_class_matches == true and role != 'OFF_ROLE' and _specialty_actor_has_node == true and _specialty_prerequisites_satisfied == false"},
                {"set": {"field": "state.last_specialty_test.status", "value": "'CONTROL'"}, "condition": "_specialty_level_available == true and _specialty_level_matches == true and _specialty_class_matches == true and role == 'OFF_ROLE' and _specialty_actor_has_node == true"},
                {"set": {"field": "state.last_specialty_test.status", "value": "'APPLIED'"}, "condition": "_specialty_level_available == true and _specialty_level_matches == true and _specialty_class_matches == true and role != 'OFF_ROLE' and _specialty_actor_has_node == true and _specialty_prerequisites_satisfied == true"},
                {"set": {"field": "_specialty_applied", "value": "true"}, "condition": "_specialty_level_available == true and _specialty_level_matches == true and _specialty_class_matches == true and role != 'OFF_ROLE' and _specialty_actor_has_node == true and _specialty_prerequisites_satisfied == true"},
                {"set": {"field": "state.last_specialty_test.node_effect_applied", "value": "true"}, "condition": "_specialty_applied == true"},
                {"set": {"field": "_specialty_operations", "value": "_specialty_branch.mechanical_operations"}},
                {
                    "for_each": {
                        "collection": "_specialty_operations",
                        "as": "_specialty_operation",
                        "do": [
                            {
                                "set": {
                                    "field": "state.last_specialty_test.result.{_specialty_operation.field}",
                                    "value": "_specialty_operation.value",
                                },
                                "condition": "_specialty_operation.operation == 'set' or _specialty_operation.operation == 'set_parameter'",
                            },
                            {
                                "increment": {
                                    "field": "state.last_specialty_test.result.{_specialty_operation.field}",
                                    "amount": "_specialty_operation.amount",
                                },
                                "condition": "_specialty_operation.operation == 'add' or _specialty_operation.operation == 'add_parameter'",
                            },
                            {
                                "decrement": {
                                    "field": "state.last_specialty_test.result.{_specialty_operation.field}",
                                    "amount": "_specialty_operation.amount",
                                },
                                "condition": "_specialty_operation.operation == 'subtract' or _specialty_operation.operation == 'subtract_parameter'",
                            },
                        ],
                    },
                    "condition": "_specialty_applied == true",
                },
                {
                    "set": {
                        "field": "state.last_specialty_test.mechanical_operations_executed",
                        "value": "_specialty_operations",
                    },
                    "condition": "_specialty_applied == true",
                },
                {"set": {"field": "state.last_specialty_test.status", "value": "_specialty_rule.boundary_fixtures.GUARD.status"}, "condition": "_specialty_fixture_eligible == true and boundary_mode == 'FIRST_GUARD_VIOLATED'"},
                {"set": {"field": "state.last_specialty_test.result", "value": "_specialty_rule.boundary_fixtures.GUARD.result"}, "condition": "_specialty_fixture_eligible == true and boundary_mode == 'FIRST_GUARD_VIOLATED'"},
                {"set": {"field": "state.last_specialty_test.node_effect_applied", "value": "_specialty_rule.boundary_fixtures.GUARD.node_effect_applied"}, "condition": "_specialty_fixture_eligible == true and boundary_mode == 'FIRST_GUARD_VIOLATED'"},
                {"set": {"field": "state.last_specialty_test.status", "value": "_specialty_rule.boundary_fixtures.TERMINATION.status"}, "condition": "_specialty_fixture_eligible == true and boundary_mode == 'FIRST_TERMINATION'"},
                {"set": {"field": "state.last_specialty_test.result", "value": "_specialty_rule.boundary_fixtures.TERMINATION.result"}, "condition": "_specialty_fixture_eligible == true and boundary_mode == 'FIRST_TERMINATION'"},
                {"set": {"field": "state.last_specialty_test.node_effect_applied", "value": "_specialty_rule.boundary_fixtures.TERMINATION.node_effect_applied"}, "condition": "_specialty_fixture_eligible == true and boundary_mode == 'FIRST_TERMINATION'"},
                {"set": {"field": "state.last_specialty_test.status", "value": "_specialty_rule.boundary_fixtures.INVARIANT.status"}, "condition": "_specialty_fixture_eligible == true and boundary_mode == 'FIRST_INVARIANT_MUTATION'"},
                {"set": {"field": "state.last_specialty_test.result", "value": "_specialty_rule.boundary_fixtures.INVARIANT.result"}, "condition": "_specialty_fixture_eligible == true and boundary_mode == 'FIRST_INVARIANT_MUTATION'"},
                {"set": {"field": "state.last_specialty_test.node_effect_applied", "value": "_specialty_rule.boundary_fixtures.INVARIANT.node_effect_applied"}, "condition": "_specialty_fixture_eligible == true and boundary_mode == 'FIRST_INVARIANT_MUTATION'"},
                {"set": {"field": "_specialty_boundary_fixture", "value": "_specialty_rule.boundary_fixtures.GUARD"}, "condition": "_specialty_fixture_eligible == true and boundary_mode == 'FIRST_GUARD_VIOLATED'"},
                {"set": {"field": "_specialty_boundary_fixture", "value": "_specialty_rule.boundary_fixtures.TERMINATION"}, "condition": "_specialty_fixture_eligible == true and boundary_mode == 'FIRST_TERMINATION'"},
                {"set": {"field": "_specialty_boundary_fixture", "value": "_specialty_rule.boundary_fixtures.INVARIANT"}, "condition": "_specialty_fixture_eligible == true and boundary_mode == 'FIRST_INVARIANT_MUTATION'"},
                {"set": {"field": "state.last_specialty_test.control", "value": "_specialty_boundary_fixture.control"}, "condition": "_specialty_boundary_fixture != null"},
                {"set": {"field": "state.last_specialty_test.violated_field", "value": "_specialty_boundary_fixture.violated_field"}, "condition": "_specialty_boundary_fixture != null"},
                {"set": {"field": "state.last_specialty_test.effect_established_before_termination", "value": "_specialty_boundary_fixture.effect_established_before_termination"}, "condition": "_specialty_boundary_fixture != null"},
                {"set": {"field": "state.last_specialty_test.mechanical_operations_executed", "value": "_specialty_boundary_fixture.mechanical_operations_executed"}, "condition": "_specialty_boundary_fixture != null"},
                {"set": {"field": "state.last_specialty_test.limitation_results", "value": "_specialty_boundary_fixture.limitation_results"}, "condition": "_specialty_boundary_fixture != null"},
                {"set": {"field": "state.last_specialty_test.limitations_preserved", "value": "_specialty_boundary_fixture.limitations_preserved"}, "condition": "_specialty_boundary_fixture != null"},
                {"set": {"field": "state.last_specialty_test.failed_guards", "value": "_specialty_boundary_fixture.failed_guards"}, "condition": "_specialty_boundary_fixture != null"},
            ],
            "costs_action": False,
        },
        "end_allies_turn": {
            "capability": "trigger-discipline.turns",
            "description": "End the allied TEAM TURN, discard unused AP, and open the hostile side.",
            "parameters": [_param("turn_roster", "array", items="string", min_items=0,
                                  max_items=256, from_state="state.turn_roster"),
                           _param("prepared_suppression", "object", from_state="state.prepared.suppression")],
            "effects": _end_turn_effects("hostiles"),
            "costs_action": True,
            "predicate": ("state.active_side", "allies"),
        },
        "end_hostiles_turn": {
            "capability": "trigger-discipline.turns",
            "description": "End the hostile TEAM TURN and refresh each eligible allied actor's independent AP for freely interleaved action.",
            "parameters": [_param("turn_roster", "array", items="string", min_items=0,
                                  max_items=256, from_state="state.turn_roster"),
                           _param("prepared_suppression", "object", from_state="state.prepared.suppression")],
            "effects": _end_turn_effects("allies"),
            "costs_action": True,
            "predicate": ("state.active_side", "hostiles"),
        },
    }
    specs["handle_weapon"] = weapon_handling.specification(actor, _param)
    specs["switch_weapon"] = weapon_handling.switch_specification(actor, _param)
    specs["transfer_reserve_weapon"] = weapon_handling.transfer_specification(actor, _param)
    specs["drop_weapon"] = ground_weapons.drop_specification(actor, _param)
    specs["pickup_weapon"] = ground_weapons.pickup_specification(actor, _param)
    specs["recover_between_encounters"] = {
        "capability": "trigger-discipline.medicine",
        "description": "GM-adjudicated RES recovery after genuine safety, time and ordinary support. Supply the warranted amount and current situation; this does not heal HP, reload, refresh AP or clear injuries.",
        "parameters": [actor, _param("amount", "integer", minimum=1),
                       _param("situation", "enum", enum=("SECURED_WITH_TIME_AND_SUPPORT", "UNSAFE_OR_INSUFFICIENT_TIME"))],
        "effects": [
            {"set": {"field": "state.last_recovery", "value": {}}},
            {"set": {"field": "state.last_recovery.status", "value": "'RECOVERY_REJECTED'"}},
            {"condition": "situation == 'SECURED_WITH_TIME_AND_SUPPORT' and actor.max_res != null and actor.max_res >= actor.res and actor.active == true",
             "on_true": [
                 {"set": {"field": "_restored_res", "value": "min(amount, actor.max_res - actor.res)"}},
                 {"increment": {"field": "{_actor_id}.res", "amount": "_restored_res"}},
                 {"set": {"field": "state.last_recovery.actor", "value": "_actor_id"}},
                 {"set": {"field": "state.last_recovery.restored_res", "value": "_restored_res"}},
                 {"set": {"field": "state.last_recovery.status", "value": "'RECOVERED'"}},
             ], "on_false": []},
        ],
        "costs_action": False,
    }
    for name, spec in specs.items():
        if name in {"end_allies_turn", "end_hostiles_turn", "recover_between_encounters"}:
            continue
        actor_name = (
            "attacker"
            if name in {"aim", "fire_single", "fire_burst", "fire_full_auto"}
            else "actor"
        )
        spec["parameters"].append(
            _param("active_side", "string", from_state="state.active_side")
        )
        original_effects = spec["effects"]
        spec["effects"] = [
            {
                "condition": f"{actor_name}.side == active_side",
                "on_true": original_effects,
                "on_false": [
                    {
                        "set": {
                            "field": "state.last_rejection",
                            "value": f"'INACTIVE_SIDE:{name}'",
                        }
                    }
                ],
            }
        ]
    for name in ("move_actor", "reload_weapon", "recover_recoil", "fire_single", "fire_burst", "fire_full_auto"):
        specs[name]["parameters"].append(_param("prepared", "object", from_state="state.prepared"))
    # Resolve the campaign's map through the existing hidden state-parameter binding.
    for name in (
        "move_actor", "change_stance", "fire_single", "fire_burst", "fire_full_auto",
        "inspect_trigger_crossing", "resolve_trigger_crossing", "resolve_cqc",
        "resolve_cqc_displacement", "resolve_blast_snapshot", "fpv_fragmentation_strike",
        "autodrone_fire", "operate_remote_function", "prepare_overwatch", "establish_suppression",
        "network_connection", "intrude_network", "pivot_network", "preserve_network_route", "treat_casualty",
    ):
        specs[name]["parameters"].append(
            _param("map_id", "string", from_state="state.spatial_scene.map.id")
        )
    for name in ("change_stance", "resolve_cqc", "resolve_cqc_displacement"):
        specs[name]["parameters"].append(
            _param("stance_bodies", "array", items="object", min_items=0, max_items=4096,
                   from_state="state.spatial_scene.entities")
        )
    return specs


def _shot_style() -> dict[str, Any]:
    """Return TD's bounded reusable shot presentation style."""
    return {
        "color": "#F2C14E",
        "width": 2,
        "travel_ms": 90,
        "impact_ms": 140,
        "fire_sound": {
            "waveform": "noise",
            "frequency": 180,
            "end_frequency": 70,
            "duration_ms": 80,
            "volume": 0.65,
        },
        "cues": {
            "character": {"color": "#D95D39"},
            "cover": {"color": "#8D99AE"},
            "stop": {"color": "#6C757D"},
            "penetration": {"color": "#F4A261"},
        },
    }


def _shot_effect(
    *,
    snapshot: str,
    source_id: str,
    target_id: str,
    origin: str,
    destination: str,
    delay_ms: int | str | None,
    miss: bool | str,
    contacts: str,
    condition: str | None = None,
    target_kind: str = "entity",
    cadence_source: str = "attacker",
) -> dict[str, Any]:
    """Evaluate presentation expressions, then present one resolved round."""
    setup: list[dict[str, Any]] = [
        {"set": {"field": "_fx_miss", "value": miss}},
        {"set": {"field": "_fx_delay", "value": delay_ms}},
    ]
    if isinstance(delay_ms, str):
        setup = [
            {"set": {"field": "_fx_delay", "value": "null"}},
            {
                "set": {"field": "_fx_delay", "value": delay_ms},
                "condition": f"{cadence_source}.weapon.cyclic_rate_rpm != null and {cadence_source}.weapon.cyclic_rate_rpm > 0",
            },
            {"set": {"field": "_fx_miss", "value": miss}},
        ]
    shot_body: dict[str, Any] = {
        "scene": "state.spatial_scene",
        "snapshot": f"{{{snapshot}}}",
        "source_id": f"{{{source_id}}}",
        "target_id": f"{{{target_id}}}",
        "origin": f"{{{origin}}}",
        "destination": f"{{{destination}}}",
        "delay_ms": "{_fx_delay}",
        "miss": "{_fx_miss}",
        "contacts": f"{{{contacts}}}",
        "style": _shot_style(),
    }
    if target_kind != "entity":
        shot_body["target_kind"] = target_kind
    setup.append({"shot_effect": shot_body})
    return {"condition": condition or "true", "on_true": setup, "on_false": []}


def _suppression_setup_presentation_effects() -> list[dict[str, Any]]:
    """Present committed area fire without inventing projectile resolution."""
    return [
        {"spatial_query": {"scene": "state.spatial_scene", "operation": "snapshot"}, "store_as": "_fx_suppression_snapshot"},
        {"set": {"field": "_fx_suppression_center", "value": "null"}},
        {"for_each": {"collection": "suppression_zones", "as": "_fx_suppression_zone", "do": [
            {"set": {"field": "_fx_suppression_center", "value": [
                "_fx_suppression_zone.shape.min[0] + (_fx_suppression_zone.shape.max[0] - _fx_suppression_zone.shape.min[0]) * 0.5",
                "_fx_suppression_zone.shape.min[1] + (_fx_suppression_zone.shape.max[1] - _fx_suppression_zone.shape.min[1]) * 0.5",
                "_fx_suppression_zone.shape.min[2] + (_fx_suppression_zone.shape.max[2] - _fx_suppression_zone.shape.min[2]) * 0.5",
            ]}, "condition": "_fx_suppression_zone.id == zone_id and _fx_suppression_zone.shape.kind == 'box'"},
        ]}},
        {"set": {"field": "_fx_suppression_zone", "value": "null"}},
        {"set": {"field": "_fx_suppression_slots", "value": list(range(1, 31))}},
        {"for_each": {"collection": "_fx_suppression_slots", "as": "_fx_suppression_slot", "do": [
            {"set": {"field": "_fx_suppression_contacts", "value": []}, "condition": "_fx_suppression_slot <= rounds"},
            _shot_effect(
                snapshot="_fx_suppression_snapshot",
                source_id="_actor_id",
                target_id="zone_id",
                target_kind="zone",
                origin="actor.muzzle",
                destination="_fx_suppression_center",
                delay_ms="(_fx_suppression_slot - 1) * 60000 // actor.weapon.cyclic_rate_rpm",
                cadence_source="actor",
                miss=False,
                contacts="_fx_suppression_contacts",
                condition="_fx_suppression_slot <= rounds",
            ),
        ]}},
    ]


def _ordered_material_route(
    *,
    prefix: str,
    intersections: str,
    target_id: str,
    damage: str,
    target_pen: str,
    material_pen: str,
    snapshot_revision: str,
    record: str,
    contacts: str | None = None,
    when: str | None = None,
) -> list[dict[str, Any]]:
    """Resolve one projectile through every physical intersection in ray order."""
    continues = f"{prefix}_continues"
    reached = f"{prefix}_target_reached"
    outcome = f"{prefix}_material_outcome"
    revision = f"{prefix}_geometry_revision"
    item = f"{prefix}_intersection"
    resistance = f"{prefix}_resistance"
    cover_before = f"{prefix}_cover_hp_before"
    cover_damage = f"{prefix}_cover_damage"
    cover_after = f"{prefix}_cover_hp_after"
    mutation = f"{prefix}_cover_mutation"
    geometry = f"{continues} == true and {item}.kind == 'geometry'"
    material = f"{geometry} and {item}.metadata.material_resistance != null"
    destructible = f"{material} and {item}.metadata.cover_hp != null"
    contact_effects: list[dict[str, Any]] = []
    if contacts is not None:
        contact_effects = [
            {"set": {"field": "_fx_contact", "value": {"kind": f"{item}.kind", "id": f"{item}.id", "point": f"{item}.point", "cue": "'stop'"}}, "condition": f"{geometry} and ({outcome} == 'STOP' or {outcome} == 'UNRESOLVED_MATERIAL')"},
            {"set": {"field": "_fx_contact", "value": {"kind": f"{item}.kind", "id": f"{item}.id", "point": f"{item}.point", "cue": "'penetration'"}}, "condition": f"{geometry} and ({outcome} == 'REDUCE' or {outcome} == 'PENETRATE')"},
            {"set": {"field": "_fx_contact", "value": {"kind": f"{item}.kind", "id": f"{item}.id", "point": f"{item}.point", "cue": "'character'"}}, "condition": f"{continues} == true and {item}.kind == 'entity' and {item}.id == {target_id}"},
            {"set": {"field": "_fx_contact", "value": {"kind": f"{item}.kind", "id": f"{item}.id", "point": f"{item}.point", "cue": "'stop'"}}, "condition": f"{continues} == true and {item}.kind == 'entity' and {item}.id != {target_id}"},
            {"list_push": {"list": contacts, "value": "_fx_contact"}, "store_as": contacts, "condition": f"{geometry} or ({continues} == true and {item}.kind == 'entity')"},
        ]
    effects: list[dict[str, Any]] = [
        {"set": {"field": continues, "value": "true"}},
        {"set": {"field": reached, "value": "false"}},
        {"set": {"field": outcome, "value": "'NO_INTERSECTION'"}},
        {"set": {"field": revision, "value": snapshot_revision}},
        {
            "for_each": {
                "collection": intersections,
                "as": item,
                "do": [
                    {"set": {"field": resistance, "value": f"{item}.metadata.material_resistance"}, "condition": material},
                    {"set": {"field": outcome, "value": "'STOP'"}, "condition": f"{material} and {material_pen} < {resistance}"},
                    {"set": {"field": outcome, "value": "'REDUCE'"}, "condition": f"{material} and {material_pen} == {resistance}"},
                    {"set": {"field": outcome, "value": "'PENETRATE'"}, "condition": f"{material} and {material_pen} > {resistance}"},
                    {"set": {"field": outcome, "value": "'UNRESOLVED_MATERIAL'"}, "condition": f"{geometry} and {item}.metadata.material_resistance == null"},
                    *contact_effects,
                    {"set": {"field": cover_before, "value": f"{item}.metadata.cover_hp"}, "condition": destructible},
                    {"set": {"field": cover_damage, "value": f"max(0, {damage} - {resistance})"}, "condition": destructible},
                    {"set": {"field": cover_after, "value": f"max(0, {cover_before} - {cover_damage})"}, "condition": destructible},
                    {
                        "spatial_mutation": {
                            "scene": "state.spatial_scene",
                            "map_id": "{map_id}",
                            "expected_revision": f"{{{revision}}}",
                            "target": {"kind": "geometry", "id": f"{{{item}.id}}"},
                            "patch": {"metadata": {"player": f"{{{item}.metadata.player}}", "material_resistance": f"{{{resistance}}}", "cover_hp": f"{{{cover_after}}}"}},
                        },
                        "store_as": mutation,
                        "condition": f"{destructible} and {cover_damage} > 0 and {cover_after} > 0",
                    },
                    {
                        "spatial_mutation": {
                            "scene": "state.spatial_scene",
                            "map_id": "{map_id}",
                            "expected_revision": f"{{{revision}}}",
                            "target": {"kind": "geometry", "id": f"{{{item}.id}}"},
                            "patch": {"active": False, "movement": "passable", "visibility": "clear", "metadata": {"player": f"{{{item}.metadata.player}}", "material_resistance": f"{{{resistance}}}", "cover_hp": f"{{{cover_after}}}"}},
                        },
                        "store_as": mutation,
                        "condition": f"{destructible} and {cover_damage} > 0 and {cover_after} == 0",
                    },
                    {"set": {"field": revision, "value": f"{mutation}.geometry_revision"}, "condition": f"{destructible} and {cover_damage} > 0"},
                    {"set": {"field": f"{record}.cover_id", "value": f"{item}.id"}, "condition": destructible},
                    {"set": {"field": f"{record}.cover_damage", "value": cover_damage}, "condition": destructible},
                    {"set": {"field": f"{record}.cover_hp_before", "value": cover_before}, "condition": destructible},
                    {"set": {"field": f"{record}.cover_hp_after", "value": cover_after}, "condition": destructible},
                    {"set": {"field": damage, "value": f"{damage} // 2"}, "condition": f"{geometry} and {outcome} == 'REDUCE'"},
                    {"set": {"field": target_pen, "value": f"{target_pen} // 2"}, "condition": f"{geometry} and {outcome} == 'REDUCE'"},
                    {"set": {"field": material_pen, "value": 0}, "condition": f"{geometry} and {outcome} == 'REDUCE'"},
                    {"set": {"field": material_pen, "value": f"{material_pen} - {resistance}"}, "condition": f"{geometry} and {outcome} == 'PENETRATE'"},
                    {"set": {"field": damage, "value": 0}, "condition": f"{geometry} and ({outcome} == 'STOP' or {outcome} == 'UNRESOLVED_MATERIAL')"},
                    {"set": {"field": target_pen, "value": 0}, "condition": f"{geometry} and ({outcome} == 'STOP' or {outcome} == 'UNRESOLVED_MATERIAL')"},
                    {"set": {"field": material_pen, "value": 0}, "condition": f"{geometry} and ({outcome} == 'STOP' or {outcome} == 'UNRESOLVED_MATERIAL')"},
                    {"set": {"field": continues, "value": "false"}, "condition": f"{geometry} and ({outcome} == 'STOP' or {outcome} == 'UNRESOLVED_MATERIAL')"},
                    {"set": {"field": outcome, "value": "'TARGET'"}, "condition": f"{continues} == true and {item}.kind == 'entity' and {item}.id == {target_id}"},
                    {"set": {"field": reached, "value": "true"}, "condition": f"{continues} == true and {item}.kind == 'entity' and {item}.id == {target_id}"},
                    {"set": {"field": continues, "value": "false"}, "condition": f"{reached} == true"},
                    {"set": {"field": outcome, "value": "'ENTITY_BLOCK'"}, "condition": f"{continues} == true and {item}.kind == 'entity' and {item}.id != {target_id}"},
                    {"set": {"field": continues, "value": "false"}, "condition": f"{outcome} == 'ENTITY_BLOCK'"},
                ],
            }
        },
        {"set": {"field": f"{record}.material_outcome", "value": outcome}},
        {"set": {"field": f"{record}.target_reached", "value": reached}},
        {"set": {"field": f"{record}.geometry_revision", "value": revision}},
    ]
    if when is not None:
        for effect in effects:
            effect["condition"] = when
    return effects


def _trigger_attack_effects(
    *,
    prefix: str,
    shooter: str,
    shooter_id: str,
    target: str,
    target_id: str,
    target_point: str,
    record: str,
    condition: str,
    difficulty_modifier: str,
    critical_effect: str,
    consume_round: bool,
) -> list[dict[str, Any]]:
    snapshot = f"{prefix}_snapshot"
    exposure = f"{prefix}_exposure"
    ray = f"{prefix}_ray"
    intersections = f"{prefix}_intersections"
    distance = f"{prefix}_distance"
    range_modifier = f"{prefix}_range_modifier"
    attack = f"{prefix}_attack"
    total = f"{prefix}_total"
    open_difficulty = f"{prefix}_open_difficulty"
    actual_difficulty = f"{prefix}_actual_difficulty"
    quality = f"{prefix}_quality"
    damage = f"{prefix}_damage"
    target_pen = f"{prefix}_target_pen"
    material_pen = f"{prefix}_material_pen"
    route_prefix = f"{prefix}_route"
    effects: list[dict[str, Any]] = [
        {"spatial_query": {"scene": "state.spatial_scene", "operation": "snapshot"}, "store_as": snapshot},
        {"spatial_query": {"scene": "state.spatial_scene", "operation": "exposure", "snapshot": f"{{{snapshot}}}", "query": {"map_id": "{map_id}", "observer": {"point": f"{{{shooter}.muzzle}}"}, "target": {"entity_id": f"{{{target_id}}}"}, "samples": {"horizontal": 3, "vertical": 3}, "include": ["geometry", "entities"]}}, "store_as": exposure},
        {"spatial_query": {"scene": "state.spatial_scene", "operation": "ray", "snapshot": f"{{{snapshot}}}", "query": {"map_id": "{map_id}", "origin": f"{{{shooter}.muzzle}}", "destination": f"{{{target_point}}}", "include": ["geometry", "entities"], "max_intersections": 64}}, "store_as": ray},
        {"set": {"field": intersections, "value": f"{ray}.intersections"}},
        {"set": {"field": distance, "value": 1000000}},
        {"for_each": {"collection": intersections, "as": f"{prefix}_range_intersection", "do": [{"set": {"field": distance, "value": f"{prefix}_range_intersection.distance"}, "condition": f"{prefix}_range_intersection.id == {target_id}"}]}},
        {"set": {"field": range_modifier, "value": f"{shooter}.weapon.range_modifiers[3]"}},
        {"set": {"field": range_modifier, "value": f"{shooter}.weapon.range_modifiers[2]"}, "condition": f"{distance} <= {shooter}.weapon.range_breakpoints_m[2]"},
        {"set": {"field": range_modifier, "value": f"{shooter}.weapon.range_modifiers[1]"}, "condition": f"{distance} <= {shooter}.weapon.range_breakpoints_m[1]"},
        {"set": {"field": range_modifier, "value": f"{shooter}.weapon.range_modifiers[0]"}, "condition": f"{distance} <= {shooter}.weapon.range_breakpoints_m[0]"},
        {"set": {"field": f"{prefix}_cover_offset", "value": 0}},
        {"set": {"field": f"{prefix}_cover_offset", "value": "definitions.parameters.cover.actual_difficulty_offset.half"}, "condition": f"{exposure}.exposure < 1 and {exposure}.exposure >= 0.5"},
        {"set": {"field": f"{prefix}_cover_offset", "value": "definitions.parameters.cover.actual_difficulty_offset.quarter"}, "condition": f"{exposure}.exposure < 0.5 and {exposure}.exposure > 0"},
        {"dice": {"expression": "d20"}, "store_as": attack},
        {"set": {"field": open_difficulty, "value": f"max(1, definitions.parameters.check.difficulty.standard + {range_modifier} + {shooter}.weapon.current_recoil + {difficulty_modifier})"}},
        {"set": {"field": actual_difficulty, "value": f"{open_difficulty} + {prefix}_cover_offset"}},
        {"set": {"field": total, "value": f"{attack}.total + {shooter}.skills.marksmanship + {shooter}.aptitudes.precision"}},
        {"set": {"field": f"{prefix}_margin", "value": f"{total} - {actual_difficulty}"}},
        {"set": {"field": quality, "value": "'MISS'"}},
        {"set": {"field": quality, "value": "'HIT'"}, "condition": f"{attack}.total != 1 and {total} >= {actual_difficulty} and {exposure}.exposure > 0"},
        {"set": {"field": quality, "value": "'CLEAN_HIT'"}, "condition": f"{quality} != 'MISS' and {prefix}_margin >= definitions.parameters.hit_quality.clean_margin"},
        {"set": {"field": quality, "value": "'DECISIVE_HIT'"}, "condition": f"{quality} != 'MISS' and {prefix}_margin >= definitions.parameters.hit_quality.decisive_margin"},
        {"set": {"field": quality, "value": "'CRITICAL_HIT'"}, "condition": f"{attack}.total != 1 and {exposure}.exposure > 0 and ({prefix}_margin >= definitions.parameters.hit_quality.critical_margin or {attack}.total == 20)"},
        {"set": {"field": damage, "value": f"{shooter}.weapon.damage"}},
        {"set": {"field": target_pen, "value": f"{shooter}.weapon.target_pen"}},
        {"set": {"field": material_pen, "value": f"{shooter}.weapon.material_pen"}},
        {"set": {"field": f"_fx_{prefix.lstrip('_')}_contacts", "value": []}},
        {"set": {"field": target_pen, "value": f"{target_pen} + definitions.parameters.hit_quality.clean_target_pen"}, "condition": f"{quality} == 'CLEAN_HIT' or {quality} == 'DECISIVE_HIT' or {quality} == 'CRITICAL_HIT'"},
        {"set": {"field": damage, "value": f"{damage} + definitions.parameters.hit_quality.decisive_damage"}, "condition": f"{quality} == 'DECISIVE_HIT' or {quality} == 'CRITICAL_HIT'"},
        {"set": {"field": target_pen, "value": f"{target_pen} + definitions.parameters.critical.deep_target_pen"}, "condition": f"{quality} == 'CRITICAL_HIT' and {critical_effect} == 'DEEP_HIT'"},
        {"set": {"field": damage, "value": f"{damage} + definitions.parameters.critical.devastating_damage"}, "condition": f"{quality} == 'CRITICAL_HIT' and {critical_effect} == 'DEVASTATING_HIT'"},
    ]
    effects.extend(
        _ordered_material_route(
            prefix=route_prefix,
            intersections=intersections,
            target_id=target_id,
            damage=damage,
            target_pen=target_pen,
            material_pen=material_pen,
            snapshot_revision=f"{snapshot}.geometry_revision",
            record=record,
            contacts=f"_fx_{prefix.lstrip('_')}_contacts",
            when=f"{quality} != 'MISS'",
        )
    )
    effects.extend(
        [
            {"set": {"field": f"{prefix}_hp_bypass", "value": f"min({target}.hp, min({damage}, {target_pen}))"}},
            {"set": {"field": f"{prefix}_ordinary", "value": f"max(0, {damage} - min({damage}, {target_pen}))"}},
            {"set": {"field": f"{prefix}_res_damage", "value": f"min({target}.res, {prefix}_ordinary)"}},
            {"set": {"field": f"{prefix}_hp_overflow", "value": f"min(max(0, {target}.hp - {prefix}_hp_bypass), max(0, {prefix}_ordinary - {target}.res))"}},
            {"decrement": {"field": f"{{{target_id}}}.hp", "amount": f"{prefix}_hp_bypass + {prefix}_hp_overflow"}, "condition": f"{quality} != 'MISS' and {route_prefix}_target_reached == true"},
            {"decrement": {"field": f"{{{target_id}}}.res", "amount": f"{prefix}_res_damage"}, "condition": f"{quality} != 'MISS' and {route_prefix}_target_reached == true"},
            {"set": {"field": f"{{{target_id}}}.downed", "value": "true"}, "condition": f"{quality} != 'MISS' and {route_prefix}_target_reached == true and {target}.hp <= 0"},
            {"set": {"field": f"{record}.roll", "value": f"{attack}.total"}},
            {"set": {"field": f"{record}.total", "value": total}},
            {"set": {"field": f"{record}.outcome", "value": quality}},
            {"set": {"field": f"{record}.status", "value": "'RESOLVED'"}},
            _shot_effect(
                snapshot=snapshot,
                source_id=shooter_id,
                target_id=target_id,
                origin=f"{shooter}.muzzle",
                destination=target_point,
                delay_ms=0,
                miss=f"{quality} == 'MISS'",
                contacts=f"_fx_{prefix.lstrip('_')}_contacts",
            ),
        ]
    )
    if consume_round:
        effects.extend(
            [
                {"set": {"field": f"{{{shooter_id}}}.weapon.chamber", "value": "'SPENT'"}},
                {"decrement": {"field": f"{{{shooter_id}}}.weapon.magazine.rounds", "amount": 1}, "condition": f"{shooter}.weapon.mechanism == 'SELF_LOADING' and {shooter}.weapon.magazine.rounds > 0"},
                {"set": {"field": f"{{{shooter_id}}}.weapon.chamber", "value": "'LIVE'"}, "condition": f"{shooter}.weapon.mechanism == 'SELF_LOADING' and {shooter}.weapon.magazine.rounds > 0"},
                {"set": {"field": f"{{{shooter_id}}}.weapon.chamber", "value": "'EMPTY'"}, "condition": f"{shooter}.weapon.mechanism == 'SELF_LOADING' and {shooter}.weapon.magazine.rounds == 0"},
                {"increment": {"field": f"{{{shooter_id}}}.weapon.current_recoil", "amount": f"max(1, {shooter}.weapon.recoil)"}},
            ]
        )
    # Keep the entire attack behind one runtime branch. Gating every individual
    # effect would still make Sylvia parse hundreds of conditions for the three
    # unselected ordering branches on every crossing.
    return [{"condition": condition, "on_true": effects, "on_false": []}]


def _trigger_crossing_effects() -> list[dict[str, Any]]:
    """Resolve movement against the saved preparation, including absent/inactive entries."""
    overwatch_ready = "prepared_fire.overwatch.active == true and prepared_fire.overwatch.owner == _overwatcher_id and prepared_fire.overwatch.shots > 0 and _overwatch_found == true and overwatcher.active == true and overwatcher.weapon.chamber == 'LIVE' and overwatcher.weapon.location == 'READY' and overwatcher.weapon.controlled_by == null"
    suppression_ready = "prepared_fire.suppression.active == true and prepared_fire.suppression.owner == _suppressor_id and prepared_fire.suppression.capacity > 0 and _suppression_found == true and suppressor.active == true and suppressor.weapon.location == 'READY' and suppressor.weapon.controlled_by == null"
    target_ready = "actor.active == true and actor.downed == false"
    overwatch_condition = f"{target_ready} and {overwatch_ready}"
    suppression_condition = f"{target_ready} and {suppression_ready}"
    effects: list[dict[str, Any]] = [
        {"set": {"field": "state.last_trigger", "value": {}}},
        {"spatial_query": {"scene": "state.spatial_scene", "operation": "snapshot"}, "store_as": "_trigger_move_snapshot"},
        {"spatial_query": {"scene": "state.spatial_scene", "operation": "path", "snapshot": "{_trigger_move_snapshot}", "query": {"map_id": "{map_id}", "start": "{actor.position_cell}", "goal": "{destination}", "allow_diagonal": True, "corner_policy": "forbid_cutting", "include_entities": True, "moving_entity_id": "{_actor_id}", "max_steps": 256}}, "store_as": "_trigger_path"},
        {"set": {"field": "_trigger_path_points", "value": []}},
        {"set": {"field": "_trigger_path_cells", "value": "_trigger_path.cells"}, "condition": "_trigger_path.found == true"},
        {"for_each": {"collection": "_trigger_path_cells", "as": "_trigger_path_cell", "do": [
            {"set": {"field": "_trigger_path_point", "value": ["_trigger_path_cell[0] + 0.5", "_trigger_path_cell[1] + 0.5", 0.9]}},
            {"list_push": {"list": "_trigger_path_points", "value": "_trigger_path_point"}, "store_as": "_trigger_path_points"},
        ]}, "condition": "_trigger_path.found == true"},
        {"spatial_query": {"scene": "state.spatial_scene", "operation": "crossed_zones", "snapshot": "{_trigger_move_snapshot}", "query": {"map_id": "{map_id}", "path": "{_trigger_path_points}", "max_crossings": 32}}, "store_as": "_trigger_crossings", "condition": "_trigger_path.found == true and len(_trigger_path_points) > 1"},
        {"set": {"field": "_overwatch_found", "value": "false"}},
        {"set": {"field": "_suppression_found", "value": "false"}},
        {"set": {"field": "_trigger_crossing_items", "value": []}},
        {"set": {"field": "_trigger_crossing_items", "value": "_trigger_crossings.crossings"}, "condition": "_trigger_path.found == true and len(_trigger_path_points) > 1"},
        {"for_each": {"collection": "_trigger_crossing_items", "as": "_trigger_crossing", "do": [
            {"set": {"field": "_overwatch_point", "value": "_trigger_crossing.point"}, "condition": "_overwatch_found == false and _trigger_crossing.zone_id == prepared_fire.overwatch.zone_id"},
            {"set": {"field": "_overwatch_distance", "value": "_trigger_crossing.distance"}, "condition": "_overwatch_found == false and _trigger_crossing.zone_id == prepared_fire.overwatch.zone_id"},
            {"set": {"field": "_overwatch_found", "value": "true"}, "condition": "_overwatch_found == false and _trigger_crossing.zone_id == prepared_fire.overwatch.zone_id"},
            {"set": {"field": "_suppression_point", "value": "_trigger_crossing.point"}, "condition": "_suppression_found == false and _trigger_crossing.zone_id == prepared_fire.suppression.zone_id"},
            {"set": {"field": "_suppression_distance", "value": "_trigger_crossing.distance"}, "condition": "_suppression_found == false and _trigger_crossing.zone_id == prepared_fire.suppression.zone_id"},
            {"set": {"field": "_suppression_found", "value": "true"}, "condition": "_suppression_found == false and _trigger_crossing.zone_id == prepared_fire.suppression.zone_id"},
        ]}, "condition": "_trigger_path.found == true"},
        {"set": {"field": "_trigger_found", "value": "false"}},
        {"set": {"field": "_trigger_distance", "value": 1000000}},
        {"set": {"field": "_trigger_point", "value": "_overwatch_point"}, "condition": overwatch_ready},
        {"set": {"field": "_trigger_distance", "value": "_overwatch_distance"}, "condition": overwatch_ready},
        {"set": {"field": "_trigger_found", "value": "true"}, "condition": overwatch_ready},
        {"set": {"field": "_trigger_point", "value": "_suppression_point"}, "condition": f"{suppression_ready} and _suppression_distance < _trigger_distance"},
        {"set": {"field": "_trigger_distance", "value": "_suppression_distance"}, "condition": f"{suppression_ready} and _suppression_distance < _trigger_distance"},
        {"set": {"field": "_trigger_found", "value": "true"}, "condition": suppression_ready},
        {"set": {"field": "_trigger_cell", "value": ["floor(_trigger_point[0])", "floor(_trigger_point[1])"]}, "condition": "_trigger_found == true"},
        {"set": {"field": "_trigger_position", "value": ["_trigger_point[0]", "_trigger_point[1]", 0]}, "condition": "_trigger_found == true"},
        {"set": {"field": "_trigger_center", "value": ["_trigger_point[0]", "_trigger_point[1]", 0.9]}, "condition": "_trigger_found == true"},
        # Sylvia 0eccde9's universal supercover walk does not terminate when a
        # ray destination lands exactly on an integer grid intersection. Keep
        # the entity at the exact crossing, but select an aim point one micron
        # inside the same occupied body volume until the host defect is fixed.
        {"set": {"field": "_trigger_aim_center", "value": ["_trigger_point[0] + 0.000001", "_trigger_point[1] + 0.000001", 0.9]}, "condition": "_trigger_found == true"},
        {"set": {"field": "_trigger_muzzle", "value": ["_trigger_point[0] + 0.41", "_trigger_point[1]", 1.4]}, "condition": "_trigger_found == true"},
        {"set": {"field": "_trigger_muzzle", "value": ["_trigger_point[0] - 0.09", "_trigger_point[1]", 1.4]}, "condition": "_trigger_found == true and actor.side == 'hostiles'"},
        {"set": {"field": "_trigger_partial_ap", "value": "max(1, (_trigger_distance + actor.movement_m_per_ap - 0.000001) // actor.movement_m_per_ap)"}, "condition": "_trigger_found == true"},
        {"set": {"field": "_trigger_full_ap", "value": "(_trigger_path.cost + actor.movement_m_per_ap - 0.000001) // actor.movement_m_per_ap"}, "condition": "_trigger_path.found == true"},
        {"set": {"field": "_trigger_available_ap", "value": "actor.ap"}},
        {"set": {"field": "_trigger_move_ready", "value": "_trigger_found == true and actor.ap >= _trigger_full_ap"}},
        {"spatial_mutation": {"scene": "state.spatial_scene", "map_id": "{map_id}", "expected_revision": "{_trigger_move_snapshot.geometry_revision}", "target": {"kind": "entity", "id": "{_actor_id}"}, "patch": {"position": "{_trigger_position}"}}, "store_as": "_trigger_pause_mutation", "condition": "_trigger_move_ready == true"},
        {"set": {"field": "{_actor_id}.position_cell", "value": "_trigger_cell"}, "condition": "_trigger_move_ready == true"},
        {"set": {"field": "{_actor_id}.center", "value": "_trigger_center"}, "condition": "_trigger_move_ready == true"},
        {"set": {"field": "{_actor_id}.muzzle", "value": "_trigger_muzzle"}, "condition": "_trigger_move_ready == true"},
        {"decrement": {"field": "{_actor_id}.ap", "amount": "_trigger_partial_ap"}, "condition": "_trigger_move_ready == true"},
        {"set": {"field": "state.last_trigger.actor", "value": "_actor_id"}},
        {"set": {"field": "state.last_trigger.point", "value": "_trigger_point"}, "condition": "_trigger_found == true"},
        {"set": {"field": "state.last_trigger.first_effect", "value": "first_effect"}, "condition": "_trigger_found == true"},
        {"set": {"field": "_card_trigger_status", "value": "'NO_ACTIVE_TRIGGER'"}},
        {"set": {"field": "_card_trigger_status", "value": "'NO_PATH'"}, "condition": "_trigger_path.found == false"},
        {"set": {"field": "state.last_trigger.required_ap", "value": "_trigger_full_ap"}, "condition": "_trigger_found == true"},
        {"set": {"field": "state.last_trigger.available_ap", "value": "_trigger_available_ap"}, "condition": "_trigger_found == true"},
        {"set": {"field": "_card_trigger_status", "value": "'INSUFFICIENT_AP'"}, "condition": "_trigger_found == true and _trigger_available_ap < _trigger_full_ap"},
        {"set": {"field": "_card_trigger_status", "value": "'PAUSED'"}, "condition": "_trigger_move_ready == true"},
        {"set": {"field": "_trigger_paused", "value": "false"}},
        {"set": {"field": "_trigger_paused", "value": "true"}, "condition": "_trigger_move_ready == true"},
        {"condition": "_trigger_paused == true", "on_true": network_entry.movement_effects(), "on_false": []},
    ]
    effects.append({"set": {"field": "_trigger_overwatch_first_ready", "value": "false"}})
    effects.append({"set": {"field": "_trigger_overwatch_first_ready", "value": "true"}, "condition": f"_trigger_paused == true and first_effect == 'OVERWATCH' and {overwatch_condition}"})
    effects.extend(_trigger_attack_effects(prefix="_trigger_overwatch_first", shooter="overwatcher", shooter_id="_overwatcher_id", target="actor", target_id="_actor_id", target_point="_trigger_aim_center", record="state.last_trigger.overwatch", condition="_trigger_overwatch_first_ready == true", difficulty_modifier="0", critical_effect="overwatch_critical_effect", consume_round=True))
    effects.extend([
        {"decrement": {"field": "state.prepared.overwatch.shots", "amount": 1}, "condition": "_trigger_overwatch_first_ready == true"},
        {"set": {"field": "state.prepared.overwatch.active", "value": "false"}, "condition": "_trigger_overwatch_first_ready == true and prepared_fire.overwatch.shots <= 1"},
    ])
    effects.append({"set": {"field": "_trigger_suppression_second_ready", "value": "false"}})
    effects.append({"set": {"field": "_trigger_suppression_second_ready", "value": "true"}, "condition": f"_trigger_paused == true and first_effect == 'OVERWATCH' and {suppression_condition}"})
    effects.extend(_trigger_attack_effects(prefix="_trigger_suppression_second", shooter="suppressor", shooter_id="_suppressor_id", target="actor", target_id="_actor_id", target_point="_trigger_aim_center", record="state.last_trigger.suppression", condition="_trigger_suppression_second_ready == true", difficulty_modifier="definitions.parameters.suppression.attack_difficulty_modifier", critical_effect="suppression_critical_effect", consume_round=False))
    effects.extend([
        {"decrement": {"field": "state.prepared.suppression.capacity", "amount": 1}, "condition": "_trigger_suppression_second_ready == true"},
        {"set": {"field": "state.prepared.suppression.active", "value": "false"}, "condition": "_trigger_suppression_second_ready == true and prepared_fire.suppression.capacity <= 1"},
    ])
    effects.append({"set": {"field": "_trigger_suppression_first_ready", "value": "false"}})
    effects.append({"set": {"field": "_trigger_suppression_first_ready", "value": "true"}, "condition": f"_trigger_paused == true and first_effect == 'SUPPRESSION' and {suppression_condition}"})
    effects.extend(_trigger_attack_effects(prefix="_trigger_suppression_first", shooter="suppressor", shooter_id="_suppressor_id", target="actor", target_id="_actor_id", target_point="_trigger_aim_center", record="state.last_trigger.suppression", condition="_trigger_suppression_first_ready == true", difficulty_modifier="definitions.parameters.suppression.attack_difficulty_modifier", critical_effect="suppression_critical_effect", consume_round=False))
    effects.extend([
        {"decrement": {"field": "state.prepared.suppression.capacity", "amount": 1}, "condition": "_trigger_suppression_first_ready == true"},
        {"set": {"field": "state.prepared.suppression.active", "value": "false"}, "condition": "_trigger_suppression_first_ready == true and prepared_fire.suppression.capacity <= 1"},
    ])
    effects.append({"set": {"field": "_trigger_overwatch_second_ready", "value": "false"}})
    effects.append({"set": {"field": "_trigger_overwatch_second_ready", "value": "true"}, "condition": f"_trigger_paused == true and first_effect == 'SUPPRESSION' and {overwatch_condition}"})
    effects.extend(_trigger_attack_effects(prefix="_trigger_overwatch_second", shooter="overwatcher", shooter_id="_overwatcher_id", target="actor", target_id="_actor_id", target_point="_trigger_aim_center", record="state.last_trigger.overwatch", condition="_trigger_overwatch_second_ready == true", difficulty_modifier="0", critical_effect="overwatch_critical_effect", consume_round=True))
    effects.extend([
        {"decrement": {"field": "state.prepared.overwatch.shots", "amount": 1}, "condition": "_trigger_overwatch_second_ready == true"},
        {"set": {"field": "state.prepared.overwatch.active", "value": "false"}, "condition": "_trigger_overwatch_second_ready == true and prepared_fire.overwatch.shots <= 1"},
        {"spatial_query": {"scene": "state.spatial_scene", "operation": "snapshot"}, "store_as": "_trigger_resume_snapshot", "condition": "_trigger_paused == true and actor.downed == false"},
        {"set": {"field": "_trigger_destination_position", "value": ["destination[0] + 0.5", "destination[1] + 0.5", 0]}, "condition": "_trigger_paused == true and actor.downed == false"},
        {"spatial_mutation": {"scene": "state.spatial_scene", "map_id": "{map_id}", "expected_revision": "{_trigger_resume_snapshot.geometry_revision}", "target": {"kind": "entity", "id": "{_actor_id}"}, "patch": {"position": "{_trigger_destination_position}"}}, "store_as": "_trigger_resume_mutation", "condition": "_trigger_paused == true and actor.downed == false"},
        {"set": {"field": "{_actor_id}.position_cell", "value": "destination"}, "condition": "_trigger_paused == true and actor.downed == false"},
        {"set": {"field": "{_actor_id}.center", "value": ["destination[0] + 0.5", "destination[1] + 0.5", 0.9]}, "condition": "_trigger_paused == true and actor.downed == false"},
        {"set": {"field": "{_actor_id}.muzzle", "value": ["destination[0] - 0.09", "destination[1] + 0.5", 1.4]}, "condition": "_trigger_paused == true and actor.downed == false and actor.side == 'hostiles'"},
        {"decrement": {"field": "{_actor_id}.ap", "amount": "max(0, _trigger_full_ap - _trigger_partial_ap)"}, "condition": "_trigger_paused == true and actor.downed == false"},
        {"set": {"field": "_card_trigger_status", "value": "'RESUMED'"}, "condition": "_trigger_paused == true and actor.downed == false"},
        {"set": {"field": "_card_trigger_status", "value": "'INTERRUPTED'"}, "condition": "_trigger_paused == true and actor.downed == true"},
    ])
    effects.append({"set": {"field": "state.last_trigger.status", "value": "_card_trigger_status"}})
    return effects


def _fire_single_effects() -> list[dict[str, Any]]:
    return [
        {"set": {"field": "state.last_attack", "value": {}}},
        {
            "condition": _action_guard("fire_single"),
            "on_true": [
                *weapon_handling.end_suppression("_attacker_id"),
                {"spatial_query": {"scene": "state.spatial_scene", "operation": "snapshot"}, "store_as": "_shot_snapshot"},
                _firing_exposure_query(store_as="_exposure", snapshot="{_shot_snapshot}"),
                _firing_ray_query(store_as="_ray", snapshot="{_shot_snapshot}"),
                _firing_ray_query(
                    store_as="_range_ray", snapshot="{_shot_snapshot}",
                    include=("entities",),
                ),
                {"set": {"field": "_first_intersection_id", "value": "null"}},
                {"set": {"field": "_first_intersection", "value": "null"}},
                {"set": {"field": "_shot_intersections", "value": "_ray.intersections"}},
                {"for_each": {"collection": "_shot_intersections", "as": "_shot_intersection", "do": [{"set": {"field": "_first_intersection", "value": "_shot_intersection"}, "condition": "_first_intersection_id == null"}, {"set": {"field": "_first_intersection_id", "value": "_shot_intersection.id"}, "condition": "_first_intersection_id == null"}]}},
                {"set": {"field": "_cover_intersection_id", "value": "null"}},
                {"set": {"field": "_cover_intersection", "value": "null"}},
                {"set": {"field": "_cover_occluders", "value": "_exposure.occluders"}},
                {"for_each": {"collection": "_cover_occluders", "as": "_cover_occluder", "do": [{"set": {"field": "_cover_intersection", "value": "_cover_occluder"}, "condition": "_cover_intersection_id == null"}, {"set": {"field": "_cover_intersection_id", "value": "_cover_occluder.id"}, "condition": "_cover_intersection_id == null"}]}},
                {"set": {"field": "_fx_cover_point", "value": "null"}},
                {"set": {"field": "_fx_exposure_samples", "value": "_exposure.samples"}},
                {"for_each": {"collection": "_fx_exposure_samples", "as": "_fx_exposure_sample", "do": [
                    {"set": {"field": "_fx_sample_occluders", "value": "_fx_exposure_sample.occluders"}},
                    {"for_each": {"collection": "_fx_sample_occluders", "as": "_fx_sample_occluder", "do": [
                        {"set": {"field": "_fx_cover_point", "value": "_fx_sample_occluder.point"}, "condition": "_fx_cover_point == null and _fx_sample_occluder.id == _cover_intersection_id"},
                    ]}},
                ]}},
                {"set": {"field": "_range_distance", "value": 1000000}},
                {"set": {"field": "_range_intersections", "value": "_range_ray.intersections"}},
                {"for_each": {"collection": "_range_intersections", "as": "_range_intersection", "do": [{"set": {"field": "_range_distance", "value": "_range_intersection.distance"}, "condition": "_range_intersection.id == _target_id"}]}},
                {"set": {"field": "_range_modifier", "value": "attacker.weapon.range_modifiers[3]"}},
                {"set": {"field": "_range_modifier", "value": "attacker.weapon.range_modifiers[2]"}, "condition": "_range_distance <= attacker.weapon.range_breakpoints_m[2]"},
                {"set": {"field": "_range_modifier", "value": "attacker.weapon.range_modifiers[1]"}, "condition": "_range_distance <= attacker.weapon.range_breakpoints_m[1]"},
                {"set": {"field": "_range_modifier", "value": "attacker.weapon.range_modifiers[0]"}, "condition": "_range_distance <= attacker.weapon.range_breakpoints_m[0]"},
                {"dice": {"expression": "d20"}, "store_as": "_attack"},
                {"set": {"field": "_aim_offset", "value": "0"}},
                {"set": {"field": "_aim_offset", "value": "min(attacker.aim.ap, definitions.parameters.fire.aim.max_ap_on_one_solution) * definitions.parameters.fire.aim.attack_difficulty_offset_per_ap"}, "condition": "attacker.aim.target == _target_id"},
                {"set": {"field": "_cover_offset", "value": "0"}},
                {"set": {"field": "_cover_offset", "value": "definitions.parameters.cover.actual_difficulty_offset.half"}, "condition": "_exposure.exposure < 1 and _exposure.exposure >= 0.5"},
                {"set": {"field": "_cover_offset", "value": "definitions.parameters.cover.actual_difficulty_offset.quarter"}, "condition": "_exposure.exposure < 0.5 and _exposure.exposure > 0"},
                {"set": {"field": "_open_solution_difficulty", "value": "max(1, definitions.parameters.check.difficulty.standard + _range_modifier + attacker.weapon.current_recoil + _aim_offset)"}},
                {"set": {"field": "_actual_difficulty", "value": "_open_solution_difficulty + _cover_offset"}},
                {"set": {"field": "_attack_total", "value": "_attack.total + attacker.skills.marksmanship + attacker.aptitudes.precision"}},
                {"set": {"field": "_margin", "value": "_attack_total - _actual_difficulty"}},
                {"set": {"field": "_quality", "value": "'MISS'"}},
                {"set": {"field": "_quality", "value": "'HIT'"}, "condition": "_attack.total != 1 and _attack_total >= _actual_difficulty and _exposure.exposure > 0"},
                {"set": {"field": "_quality", "value": "'CLEAN_HIT'"}, "condition": "_quality != 'MISS' and _margin >= definitions.parameters.hit_quality.clean_margin"},
                {"set": {"field": "_quality", "value": "'DECISIVE_HIT'"}, "condition": "_quality != 'MISS' and _margin >= definitions.parameters.hit_quality.decisive_margin"},
                {"set": {"field": "_quality", "value": "'CRITICAL_HIT'"}, "condition": "_attack.total != 1 and _exposure.exposure > 0 and (_margin >= definitions.parameters.hit_quality.critical_margin or _attack.total == 20)"},
                {"set": {"field": "_damage", "value": "attacker.weapon.damage"}},
                {"set": {"field": "_target_pen", "value": "attacker.weapon.target_pen"}},
                {"set": {"field": "_material_pen", "value": "attacker.weapon.material_pen"}},
                {"set": {"field": "_fx_single_contacts", "value": []}},
                {"set": {"field": "_fx_single_cover_intercept", "value": "false"}},
                {"set": {"field": "_target_pen", "value": "_target_pen + definitions.parameters.hit_quality.clean_target_pen"}, "condition": "_quality == 'CLEAN_HIT' or _quality == 'DECISIVE_HIT' or _quality == 'CRITICAL_HIT'"},
                {"set": {"field": "_damage", "value": "_damage + definitions.parameters.hit_quality.decisive_damage"}, "condition": "_quality == 'DECISIVE_HIT' or _quality == 'CRITICAL_HIT'"},
                {"set": {"field": "_target_pen", "value": "_target_pen + definitions.parameters.critical.deep_target_pen"}, "condition": "_quality == 'CRITICAL_HIT' and critical_effect == 'DEEP_HIT'"},
                {"set": {"field": "_damage", "value": "_damage + definitions.parameters.critical.devastating_damage"}, "condition": "_quality == 'CRITICAL_HIT' and critical_effect == 'DEVASTATING_HIT'"},
                *_ordered_material_route(
                    prefix="_single_route",
                    intersections="_shot_intersections",
                    target_id="_target_id",
                    damage="_damage",
                    target_pen="_target_pen",
                    material_pen="_material_pen",
                    snapshot_revision="_shot_snapshot.geometry_revision",
                    record="state.last_attack",
                    contacts="_fx_single_contacts",
                    when="_quality != 'MISS'",
                ),
                {"set": {"field": "_hp_bypass", "value": "min(target.hp, min(_damage, _target_pen))"}},
                {"set": {"field": "_ordinary_damage", "value": "max(0, _damage - min(_damage, _target_pen))"}},
                {"set": {"field": "_res_damage", "value": "min(target.res, _ordinary_damage)"}},
                {"set": {"field": "_hp_overflow", "value": "min(max(0, target.hp - _hp_bypass), max(0, _ordinary_damage - target.res))"}},
                {"set": {"field": "_total_hp_damage", "value": "_hp_bypass + _hp_overflow"}},
                {
                    "condition": "_quality != 'MISS' and _single_route_target_reached == true",
                    "on_true": [
                        {"decrement": {"field": "{_target_id}.hp", "amount": "_total_hp_damage"}},
                        {"decrement": {"field": "{_target_id}.res", "amount": "_res_damage"}},
                        {"set": {"field": "{_target_id}.downed", "value": "true"}, "condition": "target.hp <= 0"},
                    ],
                },
                {
                    "condition": "_attack.total != 1 and _attack_total < _actual_difficulty and _attack_total >= _open_solution_difficulty and _exposure.exposure > 0 and _cover_intersection.kind == 'geometry' and _cover_intersection.metadata.cover_hp != null",
                    "on_true": [
                        {"set": {"field": "_cover_hp_before", "value": "_cover_intersection.metadata.cover_hp"}},
                        {"set": {"field": "_cover_damage", "value": "max(0, _damage - _cover_intersection.metadata.material_resistance)"}},
                        {"set": {"field": "_cover_hp_after", "value": "max(0, _cover_hp_before - _cover_damage)"}},
                        {
                            "condition": "_cover_damage > 0 and _cover_hp_after > 0",
                            "on_true": [
                                {"spatial_mutation": {"scene": "state.spatial_scene", "map_id": "{map_id}", "expected_revision": "{_shot_snapshot.geometry_revision}", "target": {"kind": "geometry", "id": "{_cover_intersection_id}"}, "patch": {"metadata": {"player": "{_cover_intersection.metadata.player}", "material_resistance": "{_cover_intersection.metadata.material_resistance}", "cover_hp": "{_cover_hp_after}"}}}, "store_as": "_cover_mutation"}
                            ],
                        },
                        {
                            "condition": "_cover_damage > 0 and _cover_hp_after == 0",
                            "on_true": [
                                {"spatial_mutation": {"scene": "state.spatial_scene", "map_id": "{map_id}", "expected_revision": "{_shot_snapshot.geometry_revision}", "target": {"kind": "geometry", "id": "{_cover_intersection_id}"}, "patch": {"active": False, "movement": "passable", "visibility": "clear", "metadata": {"player": "{_cover_intersection.metadata.player}", "material_resistance": "{_cover_intersection.metadata.material_resistance}", "cover_hp": "{_cover_hp_after}"}}}, "store_as": "_cover_mutation"}
                            ],
                        },
                        {"set": {"field": "state.last_attack.cover_id", "value": "_cover_intersection_id"}},
                        {"set": {"field": "state.last_attack.cover_damage", "value": "_cover_damage"}},
                        {"set": {"field": "state.last_attack.cover_hp_before", "value": "_cover_hp_before"}},
                        {"set": {"field": "state.last_attack.cover_hp_after", "value": "_cover_hp_after"}},
                        {"set": {"field": "state.last_attack.geometry_revision", "value": "_cover_mutation.geometry_revision"}, "condition": "_cover_damage > 0"},
                    ],
                },
                {"decrement": {"field": "{_attacker_id}.ap", "amount": "definitions.parameters.fire.semi_single.ap"}},
                {"set": {"field": "{_attacker_id}.weapon.chamber", "value": "'SPENT'"}},
                {
                    "condition": "attacker.weapon.mechanism == 'SELF_LOADING'",
                    "on_true": [
                        {
                            "condition": "attacker.weapon.magazine.rounds > 0",
                            "on_true": [
                                {"decrement": {"field": "{_attacker_id}.weapon.magazine.rounds", "amount": 1}},
                                {"set": {"field": "{_attacker_id}.weapon.chamber", "value": "'LIVE'"}},
                            ],
                            "on_false": [
                                {"set": {"field": "{_attacker_id}.weapon.chamber", "value": "'EMPTY'"}}
                            ],
                        }
                    ],
                    "on_false": [
                        {"set": {"field": "{_attacker_id}.weapon.needs_cycle", "value": "true"}}
                    ],
                },
                {"increment": {"field": "{_attacker_id}.weapon.current_recoil", "amount": "max(1, attacker.weapon.recoil)"}},
                {"set": {"field": "{_attacker_id}.aim.ap", "value": 0}},
                {"set": {"field": "state.focused_actor", "value": "_attacker_id"}},
                {"set": {"field": "state.last_attack.mode", "value": "'SEMI_AUTO'"}},
                {"set": {"field": "state.last_attack.rounds_fired", "value": 1}},
                {"set": {"field": "state.last_attack.outcome", "value": "_quality"}},
                {"set": {"field": "state.last_attack.roll", "value": "_attack.total"}},
                {"set": {"field": "state.last_attack.total", "value": "_attack_total"}},
                {"set": {"field": "state.last_attack.open_difficulty", "value": "_open_solution_difficulty"}},
                {"set": {"field": "state.last_attack.actual_difficulty", "value": "_actual_difficulty"}},
                {"set": {"field": "state.last_attack.target", "value": "_target_id"}},
                {"set": {"field": "state.last_attack.first_intersection", "value": "_first_intersection_id"}},
                {"set": {"field": "state.last_attack.range_m", "value": "_range_distance"}},
                {"set": {"field": "state.last_attack.range_modifier", "value": "_range_modifier"}},
                {"set": {"field": "state.last_attack.outcome", "value": "'PROJECTILE_STOP'"}, "condition": "_quality != 'MISS' and _single_route_target_reached == false"},
                {"set": {"field": "state.last_attack.outcome", "value": "'COVER_INTERCEPT'"}, "condition": "_attack.total != 1 and _attack_total < _actual_difficulty and _attack_total >= _open_solution_difficulty and _exposure.exposure > 0"},
                {"set": {"field": "_fx_single_cover_intercept", "value": "true"}, "condition": "_attack.total != 1 and _attack_total < _actual_difficulty and _attack_total >= _open_solution_difficulty and _exposure.exposure > 0"},
                {"set": {"field": "state.last_attack.outcome", "value": "'WIDE_MISS'"}, "condition": "_attack.total == 1 or _attack_total < _open_solution_difficulty"},
                {"set": {"field": "_fx_contact", "value": {"kind": "'geometry'", "id": "_cover_intersection_id", "point": "_fx_cover_point", "cue": "'cover'"}}, "condition": "_fx_single_cover_intercept == true and _fx_cover_point != null"},
                {"list_push": {"list": "_fx_single_contacts", "value": "_fx_contact"}, "store_as": "_fx_single_contacts", "condition": "_fx_single_cover_intercept == true and _fx_cover_point != null"},
                _shot_effect(
                    snapshot="_shot_snapshot",
                    source_id="_attacker_id",
                    target_id="_target_id",
                    origin="attacker.muzzle",
                    destination="aim_point",
                    delay_ms=0,
                    miss="_quality == 'MISS' and _fx_single_cover_intercept == false",
                    contacts="_fx_single_contacts",
                ),
            ],
            "on_false": [{"set": {"field": "state.last_attack.outcome", "value": "'FIRE_REJECTED'"}}],
        },
    ]


def _fire_multi_effects(mode: str) -> list[dict[str, Any]]:
    if mode not in {"BURST", "FULL_AUTO"}:
        raise ValueError(f"unsupported multi-round fire mode {mode}")
    rounds = "3" if mode == "BURST" else "rounds"
    ap_cost = (
        "definitions.parameters.fire.burst_3.ap"
        if mode == "BURST"
        else "max(1, (rounds + definitions.parameters.fire.full_auto.rounds_per_ap - 1) // definitions.parameters.fire.full_auto.rounds_per_ap)"
    )
    string_modifier = (
        "floor(attacker.weapon.recoil * definitions.parameters.recoil.burst_attack_difficulty_multiplier + 0.5)"
        if mode == "BURST"
        else "floor(attacker.weapon.recoil * definitions.parameters.recoil.full_auto_attack_difficulty_multiplier + 0.5) + max(0, (rounds + definitions.parameters.recoil.full_auto_round_step - 1) // definitions.parameters.recoil.full_auto_round_step - 1) * definitions.parameters.recoil.full_auto_extra_difficulty_per_step"
    )
    recoil_gain = (
        "attacker.weapon.recoil + definitions.parameters.recoil.burst_extra_state"
        if mode == "BURST"
        else "attacker.weapon.recoil + (rounds + definitions.parameters.recoil.full_auto_round_step - 1) // definitions.parameters.recoil.full_auto_round_step - 1"
    )
    connection_effects: list[dict[str, Any]]
    if mode == "BURST":
        connection_effects = [
            {
                "set": {
                    "field": "_connection_opportunities",
                    "value": "definitions.parameters.burst.connections.hit",
                },
                "condition": "_quality == 'HIT'",
            },
            {
                "set": {
                    "field": "_connection_opportunities",
                    "value": "definitions.parameters.burst.connections.clean",
                },
                "condition": "_quality == 'CLEAN_HIT'",
            },
            {
                "set": {
                    "field": "_connection_opportunities",
                    "value": "definitions.parameters.burst.connections.decisive",
                },
                "condition": "_quality == 'DECISIVE_HIT'",
            },
            {
                "set": {
                    "field": "_connection_opportunities",
                    "value": "definitions.parameters.burst.connections.critical",
                },
                "condition": "_quality == 'CRITICAL_HIT'",
            },
        ]
    else:
        connection_effects = [
            {"set": {"field": "_quality_bonus", "value": 0}},
            {
                "set": {
                    "field": "_quality_bonus",
                    "value": "definitions.parameters.full_auto.quality_bonus.clean",
                },
                "condition": "_quality == 'CLEAN_HIT'",
            },
            {
                "set": {
                    "field": "_quality_bonus",
                    "value": "definitions.parameters.full_auto.quality_bonus.decisive",
                },
                "condition": "_quality == 'DECISIVE_HIT'",
            },
            {
                "set": {
                    "field": "_quality_bonus",
                    "value": "definitions.parameters.full_auto.quality_bonus.critical",
                },
                "condition": "_quality == 'CRITICAL_HIT'",
            },
            {
                "set": {
                    "field": "_connection_opportunities",
                    "value": "min(rounds, 1 + (rounds - 1) // definitions.parameters.full_auto.connection_round_step + _quality_bonus)",
                },
                "condition": "_quality != 'MISS'",
            },
        ]

    resolve_projectiles = {
        "for_each": {
            "collection": "_projectile_slots",
            "as": "_projectile_slot",
            "do": [
                {"set": {"field": "_fx_projectile_contacts", "value": []}},
                {
                    "set": {
                        "field": "_projectile_destination",
                        "value": "_visible_point",
                    },
                    "condition": "_projectile_slot <= _connection_opportunities",
                },
                {
                    "set": {
                        "field": "_projectile_destination",
                        "value": "_blocked_point",
                    },
                    "condition": "_projectile_slot <= _blocked_connections",
                },
                {
                    "spatial_query": {
                        "scene": "state.spatial_scene",
                        "operation": "snapshot",
                    },
                    "store_as": "_projectile_snapshot",
                    "condition": "_projectile_slot <= _rounds_fired",
                },
                {
                    "spatial_query": {
                        "scene": "state.spatial_scene",
                        "operation": "exposure",
                        "snapshot": "{_projectile_snapshot}",
                        "query": {
                            "map_id": "{map_id}",
                            "observer": {"point": "{attacker.muzzle}"},
                            "target": {"entity_id": "{_target_id}"},
                            "samples": {"horizontal": 3, "vertical": 3},
                            "include": ["geometry", "entities"],
                        },
                    },
                    "store_as": "_projectile_exposure",
                    "condition": "_projectile_slot <= _connection_opportunities",
                },
                {
                    "spatial_query": {
                        "scene": "state.spatial_scene",
                        "operation": "ray",
                        "snapshot": "{_projectile_snapshot}",
                        "query": {
                            "map_id": "{map_id}",
                            "origin": "{attacker.muzzle}",
                            "destination": "{_projectile_destination}",
                            "include": ["geometry", "entities"],
                            "max_intersections": 64,
                        },
                    },
                    "store_as": "_projectile_ray",
                    "condition": "_projectile_slot <= _connection_opportunities",
                },
                {
                    "set": {"field": "_projectile_first", "value": "null"},
                    "condition": "_projectile_slot <= _connection_opportunities",
                },
                {
                    "set": {
                        "field": "_projectile_first_id",
                        "value": "null",
                    },
                    "condition": "_projectile_slot <= _connection_opportunities",
                },
                {
                    "set": {
                        "field": "_projectile_intersections",
                        "value": "_projectile_ray.intersections",
                    },
                    "condition": "_projectile_slot <= _connection_opportunities",
                },
                {
                    "for_each": {
                        "collection": "_projectile_intersections",
                        "as": "_projectile_intersection",
                        "do": [
                            {
                                "set": {
                                    "field": "_projectile_first",
                                    "value": "_projectile_intersection",
                                },
                                "condition": "_projectile_first_id == null",
                            },
                            {
                                "set": {
                                    "field": "_projectile_first_id",
                                    "value": "_projectile_intersection.id",
                                },
                                "condition": "_projectile_first_id == null",
                            },
                        ],
                    },
                    "condition": "_projectile_slot <= _connection_opportunities",
                },
                {
                    "set": {
                        "field": "_projectile_damage",
                        "value": "attacker.weapon.damage",
                    },
                    "condition": "_projectile_slot <= _connection_opportunities",
                },
                {
                    "set": {
                        "field": "_projectile_target_pen",
                        "value": "attacker.weapon.target_pen",
                    },
                    "condition": "_projectile_slot <= _connection_opportunities",
                },
                {
                    "set": {
                        "field": "_projectile_material_pen",
                        "value": "attacker.weapon.material_pen",
                    },
                    "condition": "_projectile_slot <= _connection_opportunities",
                },
                {
                    "set": {
                        "field": "_projectile_target_pen",
                        "value": "_projectile_target_pen + definitions.parameters.hit_quality.clean_target_pen",
                    },
                    "condition": "_projectile_slot <= _connection_opportunities and (_quality == 'CLEAN_HIT' or _quality == 'DECISIVE_HIT' or _quality == 'CRITICAL_HIT')",
                },
                {
                    "set": {
                        "field": "_projectile_damage",
                        "value": "_projectile_damage + definitions.parameters.hit_quality.decisive_damage",
                    },
                    "condition": "_projectile_slot <= _connection_opportunities and (_quality == 'DECISIVE_HIT' or _quality == 'CRITICAL_HIT')",
                },
                {
                    "set": {
                        "field": "_projectile_target_pen",
                        "value": "_projectile_target_pen + definitions.parameters.critical.deep_target_pen",
                    },
                    "condition": "_projectile_slot <= _connection_opportunities and _quality == 'CRITICAL_HIT' and _critical_used == false and critical_effect == 'DEEP_HIT'",
                },
                {
                    "set": {
                        "field": "_projectile_damage",
                        "value": "_projectile_damage + definitions.parameters.critical.devastating_damage",
                    },
                    "condition": "_projectile_slot <= _connection_opportunities and _quality == 'CRITICAL_HIT' and _critical_used == false and critical_effect == 'DEVASTATING_HIT'",
                },
                *_ordered_material_route(
                    prefix="_projectile_route",
                    intersections="_projectile_intersections",
                    target_id="_target_id",
                    damage="_projectile_damage",
                    target_pen="_projectile_target_pen",
                    material_pen="_projectile_material_pen",
                    snapshot_revision="_projectile_snapshot.geometry_revision",
                    record="state.last_attack",
                    contacts="_fx_projectile_contacts",
                    when="_projectile_slot <= _connection_opportunities",
                ),
                {
                    "set": {
                        "field": "_projectile_hp_bypass",
                        "value": "min(target.hp, min(_projectile_damage, _projectile_target_pen))",
                    },
                    "condition": "_projectile_slot <= _connection_opportunities and _projectile_route_target_reached == true and target.downed == false",
                },
                {
                    "set": {
                        "field": "_projectile_ordinary_damage",
                        "value": "max(0, _projectile_damage - min(_projectile_damage, _projectile_target_pen))",
                    },
                    "condition": "_projectile_slot <= _connection_opportunities and _projectile_route_target_reached == true and target.downed == false",
                },
                {
                    "set": {
                        "field": "_projectile_res_damage",
                        "value": "min(target.res, _projectile_ordinary_damage)",
                    },
                    "condition": "_projectile_slot <= _connection_opportunities and _projectile_route_target_reached == true and target.downed == false",
                },
                {
                    "set": {
                        "field": "_projectile_hp_overflow",
                        "value": "min(max(0, target.hp - _projectile_hp_bypass), max(0, _projectile_ordinary_damage - target.res))",
                    },
                    "condition": "_projectile_slot <= _connection_opportunities and _projectile_route_target_reached == true and target.downed == false",
                },
                {
                    "decrement": {
                        "field": "{_target_id}.hp",
                        "amount": "_projectile_hp_bypass + _projectile_hp_overflow",
                    },
                    "condition": "_projectile_slot <= _connection_opportunities and _projectile_route_target_reached == true and target.downed == false",
                },
                {
                    "decrement": {
                        "field": "{_target_id}.res",
                        "amount": "_projectile_res_damage",
                    },
                    "condition": "_projectile_slot <= _connection_opportunities and _projectile_route_target_reached == true and target.downed == false",
                },
                {
                    "set": {"field": "{_target_id}.downed", "value": "true"},
                    "condition": "_projectile_slot <= _connection_opportunities and _projectile_route_target_reached == true and target.hp <= 0",
                },
                {
                    "set": {
                        "field": "_realized_target_hits",
                        "value": "_realized_target_hits + 1",
                    },
                    "condition": "_projectile_slot <= _connection_opportunities and _projectile_route_target_reached == true",
                },
                {
                    "set": {"field": "_critical_used", "value": "true"},
                    "condition": "_projectile_slot <= _connection_opportunities and _projectile_route_target_reached == true and _quality == 'CRITICAL_HIT' and _critical_used == false",
                },
                {
                    "set": {
                        "field": "_cover_impacts",
                        "value": "_cover_impacts + 1",
                    },
                    "condition": "_projectile_slot <= _connection_opportunities and (_projectile_route_material_outcome == 'STOP' or _projectile_route_material_outcome == 'REDUCE' or _projectile_route_material_outcome == 'PENETRATE')",
                },
                _shot_effect(
                    snapshot="_projectile_snapshot",
                    source_id="_attacker_id",
                    target_id="_target_id",
                    origin="attacker.muzzle",
                    destination="aim_point",
                    delay_ms="(_projectile_slot - 1) * 60000 // attacker.weapon.cyclic_rate_rpm",
                    miss="_projectile_slot > _connection_opportunities",
                    contacts="_fx_projectile_contacts",
                    condition="_projectile_slot <= _rounds_fired",
                ),
            ],
        }
    }

    return [
        {"set": {"field": "state.last_attack", "value": {}}},
        {
            "condition": _action_guard("fire_burst" if mode == "BURST" else "fire_full_auto"),
            "on_true": [
                *weapon_handling.end_suppression("_attacker_id"),
                {"set": {"field": "_rounds_fired", "value": rounds}},
                {"set": {"field": "_fire_ap", "value": ap_cost}},
                {
                    "spatial_query": {
                        "scene": "state.spatial_scene",
                        "operation": "snapshot",
                    },
                    "store_as": "_string_snapshot",
                },
                _firing_exposure_query(
                    store_as="_string_exposure", snapshot="{_string_snapshot}",
                ),
                _firing_ray_query(
                    store_as="_string_range_ray", snapshot="{_string_snapshot}",
                    include=("entities",),
                ),
                {"set": {"field": "_range_distance", "value": 1000000}},
                {
                    "set": {
                        "field": "_range_intersections",
                        "value": "_string_range_ray.intersections",
                    }
                },
                {
                    "for_each": {
                        "collection": "_range_intersections",
                        "as": "_range_intersection",
                        "do": [
                            {
                                "set": {
                                    "field": "_range_distance",
                                    "value": "_range_intersection.distance",
                                },
                                "condition": "_range_intersection.id == _target_id",
                            }
                        ],
                    }
                },
                {
                    "set": {
                        "field": "_range_modifier",
                        "value": "attacker.weapon.range_modifiers[3]",
                    }
                },
                {
                    "set": {
                        "field": "_range_modifier",
                        "value": "attacker.weapon.range_modifiers[2]",
                    },
                    "condition": "_range_distance <= attacker.weapon.range_breakpoints_m[2]",
                },
                {
                    "set": {
                        "field": "_range_modifier",
                        "value": "attacker.weapon.range_modifiers[1]",
                    },
                    "condition": "_range_distance <= attacker.weapon.range_breakpoints_m[1]",
                },
                {
                    "set": {
                        "field": "_range_modifier",
                        "value": "attacker.weapon.range_modifiers[0]",
                    },
                    "condition": "_range_distance <= attacker.weapon.range_breakpoints_m[0]",
                },
                {"dice": {"expression": "d20"}, "store_as": "_attack"},
                {"set": {"field": "_aim_offset", "value": 0}},
                {
                    "set": {
                        "field": "_aim_offset",
                        "value": "min(attacker.aim.ap, definitions.parameters.fire.aim.max_ap_on_one_solution) * definitions.parameters.fire.aim.attack_difficulty_offset_per_ap",
                    },
                    "condition": "attacker.aim.target == _target_id",
                },
                {
                    "set": {"field": "_string_modifier", "value": string_modifier}
                },
                {"set": {"field": "_cover_offset", "value": 0}},
                {
                    "set": {
                        "field": "_cover_offset",
                        "value": "definitions.parameters.cover.actual_difficulty_offset.half",
                    },
                    "condition": "_string_exposure.exposure < 1 and _string_exposure.exposure >= 0.5",
                },
                {
                    "set": {
                        "field": "_cover_offset",
                        "value": "definitions.parameters.cover.actual_difficulty_offset.quarter",
                    },
                    "condition": "_string_exposure.exposure < 0.5 and _string_exposure.exposure > 0",
                },
                {
                    "set": {
                        "field": "_open_solution_difficulty",
                        "value": "max(1, definitions.parameters.check.difficulty.standard + _range_modifier + attacker.weapon.current_recoil + _string_modifier + _aim_offset)",
                    }
                },
                {
                    "set": {
                        "field": "_actual_difficulty",
                        "value": "_open_solution_difficulty + _cover_offset",
                    }
                },
                {
                    "set": {
                        "field": "_attack_total",
                        "value": "_attack.total + attacker.skills.marksmanship + attacker.aptitudes.precision",
                    }
                },
                {
                    "set": {
                        "field": "_margin",
                        "value": "_attack_total - _actual_difficulty",
                    }
                },
                {"set": {"field": "_quality", "value": "'MISS'"}},
                {
                    "set": {"field": "_quality", "value": "'HIT'"},
                    "condition": "_attack.total != 1 and _attack_total >= _actual_difficulty and _string_exposure.exposure > 0",
                },
                {
                    "set": {"field": "_quality", "value": "'CLEAN_HIT'"},
                    "condition": "_quality != 'MISS' and _margin >= definitions.parameters.hit_quality.clean_margin",
                },
                {
                    "set": {
                        "field": "_quality",
                        "value": "'DECISIVE_HIT'",
                    },
                    "condition": "_quality != 'MISS' and _margin >= definitions.parameters.hit_quality.decisive_margin",
                },
                {
                    "set": {"field": "_quality", "value": "'CRITICAL_HIT'"},
                    "condition": "_attack.total != 1 and _string_exposure.exposure > 0 and (_margin >= definitions.parameters.hit_quality.critical_margin or _attack.total == 20)",
                },
                {"set": {"field": "_connection_opportunities", "value": 0}},
                *connection_effects,
                {
                    "set": {"field": "_connection_opportunities", "value": 1},
                    "condition": "_quality == 'MISS' and _attack.total != 1 and _attack_total < _actual_difficulty and _attack_total >= _open_solution_difficulty and _string_exposure.exposure > 0",
                },
                {
                    "set": {
                        "field": "_exposure_cap",
                        "value": "_connection_opportunities",
                    }
                },
                {
                    "set": {
                        "field": "_exposure_cap",
                        "value": "definitions.parameters.exposure_cap.half",
                    },
                    "condition": "_string_exposure.exposure < 1 and _string_exposure.exposure >= 0.5",
                },
                {
                    "set": {
                        "field": "_exposure_cap",
                        "value": "definitions.parameters.exposure_cap.quarter",
                    },
                    "condition": "_string_exposure.exposure < 0.5 and _string_exposure.exposure > 0",
                },
                {
                    "set": {"field": "_exposure_cap", "value": 0},
                    "condition": "_string_exposure.exposure == 0",
                },
                {
                    "set": {
                        "field": "_target_connections",
                        "value": "min(_connection_opportunities, _exposure_cap)",
                    }
                },
                {
                    "set": {"field": "_target_connections", "value": 0},
                    "condition": "_quality == 'MISS'",
                },
                {
                    "set": {
                        "field": "_blocked_connections",
                        "value": "max(0, _connection_opportunities - _target_connections)",
                    }
                },
                {"set": {"field": "_visible_point", "value": "aim_point"}},
                {"set": {"field": "_blocked_point", "value": "aim_point"}},
                {"set": {"field": "_found_visible_point", "value": "false"}},
                {"set": {"field": "_found_blocked_point", "value": "false"}},
                {
                    "set": {
                        "field": "_string_samples",
                        "value": "_string_exposure.samples",
                    }
                },
                {
                    "for_each": {
                        "collection": "_string_samples",
                        "as": "_string_sample",
                        "do": [
                            {
                                "set": {
                                    "field": "_visible_point",
                                    "value": "_string_sample.point",
                                },
                                "condition": "_found_visible_point == false and _string_sample.visible == true",
                            },
                            {
                                "set": {
                                    "field": "_found_visible_point",
                                    "value": "true",
                                },
                                "condition": "_found_visible_point == false and _string_sample.visible == true",
                            },
                            {
                                "set": {
                                    "field": "_blocked_point",
                                    "value": "_string_sample.point",
                                },
                                "condition": "_found_blocked_point == false and _string_sample.visible == false",
                            },
                            {
                                "set": {
                                    "field": "_found_blocked_point",
                                    "value": "true",
                                },
                                "condition": "_found_blocked_point == false and _string_sample.visible == false",
                            },
                        ],
                    }
                },
                {
                    "set": {
                        "field": "_projectile_slots",
                        "value": list(range(1, 21)),
                    }
                },
                {"set": {"field": "_realized_target_hits", "value": 0}},
                {"set": {"field": "_cover_impacts", "value": 0}},
                {"set": {"field": "_critical_used", "value": "false"}},
                resolve_projectiles,
                {"set": {"field": "_pre_fire_magazine", "value": "attacker.weapon.magazine.rounds"}},
                {
                    "set": {
                        "field": "{_attacker_id}.weapon.magazine.rounds",
                        "value": f"max(0, _pre_fire_magazine - {rounds} + 1)",
                    }
                },
                {
                    "set": {
                        "field": "{_attacker_id}.weapon.magazine.rounds",
                        "value": f"_pre_fire_magazine - {rounds}",
                    },
                    "condition": f"_pre_fire_magazine >= {rounds}",
                },
                {
                    "set": {
                        "field": "{_attacker_id}.weapon.chamber",
                        "value": "'EMPTY'",
                    },
                    "condition": f"_pre_fire_magazine < {rounds}",
                },
                {
                    "increment": {
                        "field": "{_attacker_id}.weapon.current_recoil",
                        "amount": recoil_gain,
                    }
                },
                {"decrement": {"field": "{_attacker_id}.ap", "amount": "_fire_ap"}},
                {"set": {"field": "{_attacker_id}.aim.ap", "value": 0}},
                {"set": {"field": "state.focused_actor", "value": "_attacker_id"}},
                {"set": {"field": "state.last_attack.mode", "value": repr(mode)}},
                {"set": {"field": "state.last_attack.outcome", "value": "_quality"}},
                {
                    "set": {
                        "field": "state.last_attack.outcome",
                        "value": "'COVER_INTERCEPT'",
                    },
                    "condition": "_quality == 'MISS' and _connection_opportunities == 1",
                },
                {
                    "set": {
                        "field": "state.last_attack.outcome",
                        "value": "'WIDE_MISS'",
                    },
                    "condition": "_quality == 'MISS' and _connection_opportunities == 0",
                },
                {"set": {"field": "state.last_attack.roll", "value": "_attack.total"}},
                {"set": {"field": "state.last_attack.total", "value": "_attack_total"}},
                {"set": {"field": "state.last_attack.rounds_fired", "value": "_rounds_fired"}},
                {"set": {"field": "state.last_attack.connection_opportunities", "value": "_connection_opportunities"}},
                {"set": {"field": "state.last_attack.target_connections", "value": "_target_connections"}},
                {"set": {"field": "state.last_attack.blocked_connections", "value": "_blocked_connections"}},
                {"set": {"field": "state.last_attack.realized_target_hits", "value": "_realized_target_hits"}},
                {"set": {"field": "state.last_attack.cover_impacts", "value": "_cover_impacts"}},
                {"set": {"field": "state.last_attack.open_difficulty", "value": "_open_solution_difficulty"}},
                {"set": {"field": "state.last_attack.actual_difficulty", "value": "_actual_difficulty"}},
                {"set": {"field": "state.last_attack.target", "value": "_target_id"}},
                {"set": {"field": "state.last_attack.range_m", "value": "_range_distance"}},
                {"set": {"field": "state.last_attack.range_modifier", "value": "_range_modifier"}},
                {"set": {"field": "state.last_attack.outcome", "value": "'PROJECTILE_STOP'"}, "condition": "_quality != 'MISS' and _realized_target_hits == 0 and _cover_impacts > 0"},
            ],
            "on_false": [
                {"set": {"field": "state.last_attack.outcome", "value": "'FIRE_REJECTED'"}}
            ],
        },
    ]


def _cqc_resolve_check_effects() -> list[dict[str, Any]]:
    return [
        {"dice": {"expression": "d20"}, "store_as": "_cqc"},
        {"set": {"field": "_cqc_total", "value": "_cqc.total + actor.skills.cqc + _cqc_aptitude"}},
        {"set": {"field": "_cqc_defense", "value": "definitions.parameters.cqc.defense_base + floor(target.skills.cqc * definitions.parameters.cqc.defense_skill_coefficient) + floor(_defense_aptitude * definitions.parameters.cqc.defense_aptitude_coefficient)"}},
        {"decrement": {"field": "{_actor_id}.ap", "amount": "_cqc_ap"}},
        {"set": {"field": "state.last_cqc.success", "value": "false"}},
        {"set": {"field": "state.last_cqc.success", "value": "true"}, "condition": "_cqc_total >= _cqc_defense"},
        {"set": {"field": "state.last_cqc.maneuver", "value": "maneuver"}},
        {"set": {"field": "state.last_cqc.actor", "value": "_actor_id"}},
        {"set": {"field": "state.last_cqc.target", "value": "_target_id"}},
        {"set": {"field": "state.last_cqc.roll", "value": "_cqc.total"}},
        {"set": {"field": "state.last_cqc.total", "value": "_cqc_total"}},
        {"set": {"field": "state.last_cqc.defense", "value": "_cqc_defense"}},
        {"set": {"field": "state.last_cqc.status", "value": "'FAILED_CHECK'"}},
        {"set": {"field": "state.last_cqc.status", "value": "'COMMITTED'"}, "condition": "_cqc_total >= _cqc_defense"},
        {"condition": "_cqc_total >= _cqc_defense and maneuver == 'TRIP'", "on_true": _stance_effects("_target_id", "'PRONE'")},
        {"call": "td_drop_held_weapon", "params": {"actor": "{_target_id}", "world": "state", "map_id": "{map_id}"},
         "condition": "_cqc_total >= _cqc_defense and maneuver == 'DISARM'"},
        {"set": {"field": "{_target_id}.control", "value": {}}, "condition": "_cqc_total >= _cqc_defense and (maneuver == 'BODY_CONTROL' or maneuver == 'WEAPON_CONTROL')"},
        {"set": {"field": "{_target_id}.control.controller", "value": "_actor_id"}, "condition": "_cqc_total >= _cqc_defense and (maneuver == 'BODY_CONTROL' or maneuver == 'WEAPON_CONTROL')"},
        {"set": {"field": "{_target_id}.control.focus", "value": "maneuver"}, "condition": "_cqc_total >= _cqc_defense and (maneuver == 'BODY_CONTROL' or maneuver == 'WEAPON_CONTROL')"},
        {"set": {"field": "{_actor_id}.control", "value": {}}, "condition": "_cqc_total >= _cqc_defense and (maneuver == 'BODY_CONTROL' or maneuver == 'WEAPON_CONTROL')"},
        {"set": {"field": "{_actor_id}.control.target", "value": "_target_id"}, "condition": "_cqc_total >= _cqc_defense and (maneuver == 'BODY_CONTROL' or maneuver == 'WEAPON_CONTROL')"},
        {"set": {"field": "{_actor_id}.control.focus", "value": "maneuver"}, "condition": "_cqc_total >= _cqc_defense and (maneuver == 'BODY_CONTROL' or maneuver == 'WEAPON_CONTROL')"},
        {"set": {"field": "{_target_id}.weapon.controlled_by", "value": "_actor_id"}, "condition": "_cqc_total >= _cqc_defense and maneuver == 'WEAPON_CONTROL'"},
        {"set": {"field": "{_actor_id}.weapon.controlled_by", "value": "null"}, "condition": "_cqc_total >= _cqc_defense and maneuver == 'ESCAPE' and actor.weapon != null and actor.control.focus == 'WEAPON_CONTROL'"},
        {"set": {"field": "{_target_id}.control", "value": "null"}, "condition": "_cqc_total >= _cqc_defense and maneuver == 'ESCAPE'"},
        {"set": {"field": "{_actor_id}.control", "value": "null"}, "condition": "_cqc_total >= _cqc_defense and maneuver == 'ESCAPE'"},
    ]


def _cycle_weapon_effects() -> list[dict[str, Any]]:
    return [
        {
            "condition": _action_guard("cycle_weapon"),
            "on_true": [
                {"decrement": {"field": "{_actor_id}.ap", "amount": "actor.weapon.cycle_ap"}},
                {
                    "condition": "actor.weapon.magazine.rounds > 0",
                    "on_true": [
                        {"decrement": {"field": "{_actor_id}.weapon.magazine.rounds", "amount": 1}},
                        {"set": {"field": "{_actor_id}.weapon.chamber", "value": "'LIVE'"}},
                    ],
                    "on_false": [
                        {"set": {"field": "{_actor_id}.weapon.chamber", "value": "'EMPTY'"}}
                    ],
                },
                {"set": {"field": "{_actor_id}.weapon.needs_cycle", "value": "false"}},
                {"set": {"field": "state.last_reload.actor", "value": "_actor_id"}},
                {"set": {"field": "state.last_reload.operation", "value": "'CYCLE'"}},
                {"set": {"field": "state.last_reload.status", "value": "'COMMITTED'"}},
            ],
            "on_false": [
                {"set": {"field": "state.last_reload.actor", "value": "_actor_id"}},
                {"set": {"field": "state.last_reload.operation", "value": "'CYCLE'"}},
                {"set": {"field": "state.last_reload.status", "value": "'MISSING_PREREQUISITE'"}},
            ],
        }
    ]


def _reload_weapon_effects() -> list[dict[str, Any]]:
    return [
        {
            "condition": "actor.weapon != null and actor.ap >= actor.weapon.reload_ap and actor.weapon.feed == 'DETACHABLE_MAGAZINE' and actor.weapon.spare_magazines and actor.weapon.location == 'READY' and actor.weapon.controlled_by == null",
            "on_true": [
                *weapon_handling.end_suppression(),
                {"set": {"field": "_spare_magazines", "value": "actor.weapon.spare_magazines"}},
                {"set": {"field": "_ejected_magazine", "value": "actor.weapon.magazine"}},
                {"list_pop": {"list": "_spare_magazines"}, "store_as": "_replacement_magazine"},
                {"set": {"field": "{_actor_id}.weapon.spare_magazines", "value": "_spare_magazines"}},
                {"set": {"field": "_retained_magazines", "value": "actor.weapon.retained_magazines"}, "condition": "disposition == 'RETAIN'"},
                {"list_push": {"list": "_retained_magazines", "value": "_ejected_magazine"}, "store_as": "_retained_magazines", "condition": "disposition == 'RETAIN'"},
                {"set": {"field": "{_actor_id}.weapon.retained_magazines", "value": "_retained_magazines"}, "condition": "disposition == 'RETAIN'"},
                {"set": {"field": "_drop_index", "value": "ground_object_sequence + 1"}, "condition": "disposition == 'DROP'"},
                {"increment": {"field": "state.ground_object_sequence", "amount": 1}, "condition": "disposition == 'DROP'"},
                {"set": {"field": "state.ground_objects.magazine_{_drop_index}", "value": "_ejected_magazine"}, "condition": "disposition == 'DROP'"},
                {"set": {"field": "state.ground_objects.magazine_{_drop_index}.kind", "value": "'MAGAZINE'"}, "condition": "disposition == 'DROP'"},
                {"set": {"field": "state.ground_objects.magazine_{_drop_index}.position_cell", "value": "actor.position_cell"}, "condition": "disposition == 'DROP'"},
                {"set": {"field": "{_actor_id}.weapon.magazine", "value": "_replacement_magazine"}},
                {
                    "condition": "actor.weapon.chamber != 'LIVE' and actor.weapon.mechanism == 'SELF_LOADING' and actor.weapon.magazine.rounds > 0",
                    "on_true": [
                        {"decrement": {"field": "{_actor_id}.weapon.magazine.rounds", "amount": 1}},
                        {"set": {"field": "{_actor_id}.weapon.chamber", "value": "'LIVE'"}},
                        {"set": {"field": "{_actor_id}.weapon.needs_cycle", "value": "false"}},
                    ],
                },
                {"set": {"field": "{_actor_id}.weapon.needs_cycle", "value": "true"}, "condition": "actor.weapon.chamber != 'LIVE' and actor.weapon.mechanism == 'MANUAL_ACTION'"},
                {"decrement": {"field": "{_actor_id}.ap", "amount": "actor.weapon.reload_ap"}},
                {"set": {"field": "{_actor_id}.aim.ap", "value": 0}},
                {"set": {"field": "state.last_reload.actor", "value": "_actor_id"}},
                {"set": {"field": "state.last_reload.operation", "value": "'MAGAZINE_CHANGE'"}},
                {"set": {"field": "state.last_reload.disposition", "value": "disposition"}},
                {"set": {"field": "state.last_reload.status", "value": "'COMMITTED'"}},
            ],
            "on_false": [
                {"set": {"field": "state.last_reload.actor", "value": "_actor_id"}},
                {"set": {"field": "state.last_reload.operation", "value": "'MAGAZINE_CHANGE'"}},
                {"set": {"field": "state.last_reload.status", "value": "'MISSING_PREREQUISITE'"}},
            ],
        }
    ]


def _cqc_effects() -> list[dict[str, Any]]:
    resolve_check = _cqc_resolve_check_effects()
    return [
        {"spatial_query": {"scene": "state.spatial_scene", "operation": "snapshot"}, "store_as": "_cqc_snapshot"},
        {"spatial_query": {"scene": "state.spatial_scene", "operation": "path", "snapshot": "{_cqc_snapshot}", "query": {"map_id": "{map_id}", "start": "{actor.position_cell}", "goal": "{target.position_cell}", "allow_diagonal": True, "corner_policy": "forbid_cutting", "include_entities": False, "max_steps": 2}}, "store_as": "_reach"},
        {"set": {"field": "_cqc_ap", "value": "definitions.parameters.cqc.action_ap"}},
        {"set": {"field": "_cqc_ap", "value": "definitions.parameters.cqc.escape_ap"}, "condition": "maneuver == 'ESCAPE'"},
        {"set": {"field": "_cqc_aptitude", "value": "actor.aptitudes.precision"}},
        {"set": {"field": "_defense_aptitude", "value": "target.aptitudes.precision"}},
        {"set": {"field": "_cqc_aptitude", "value": "actor.aptitudes.force"}, "condition": "maneuver == 'BODY_CONTROL' or (maneuver == 'ESCAPE' and actor.control.focus == 'BODY_CONTROL')"},
        {"set": {"field": "_defense_aptitude", "value": "target.aptitudes.force"}, "condition": "maneuver == 'BODY_CONTROL' or (maneuver == 'ESCAPE' and actor.control.focus == 'BODY_CONTROL')"},
        {"set": {"field": "_prerequisites_valid", "value": "false"}},
        {"set": {"field": "_prerequisites_valid", "value": "true"}, "condition": "maneuver == 'TRIP'"},
        {"set": {"field": "_prerequisites_valid", "value": "true"}, "condition": "maneuver == 'DISARM' and held_item == 'PRIMARY_WEAPON' and target.weapon.location == 'READY'"},
        {"set": {"field": "_prerequisites_valid", "value": "true"}, "condition": "maneuver == 'BODY_CONTROL' and actor.control == null and target.control == null"},
        {"set": {"field": "_prerequisites_valid", "value": "true"}, "condition": "maneuver == 'WEAPON_CONTROL' and held_item == 'PRIMARY_WEAPON' and actor.control == null and target.control == null and target.weapon.location == 'READY'"},
        {"set": {"field": "_prerequisites_valid", "value": "true"}, "condition": "maneuver == 'ESCAPE' and actor.control != null and actor.control.controller == _target_id and target.control != null and target.control.target == _actor_id"},
        {
            "condition": "_reach.found == true and _reach.cost <= 1.5 and actor.ap >= _cqc_ap and actor.active == true and target.active == true",
            "on_true": [
                {
                    "condition": "_prerequisites_valid == true",
                    "on_true": resolve_check,
                    "on_false": [
                        {"set": {"field": "state.last_cqc.success", "value": "false"}},
                        {"set": {"field": "state.last_cqc.maneuver", "value": "maneuver"}},
                        {"set": {"field": "state.last_cqc.status", "value": "'MISSING_PREREQUISITE'"}},
                    ],
                }
            ],
            "on_false": [
                {"set": {"field": "state.last_cqc.success", "value": "false"}},
                {"set": {"field": "state.last_cqc.maneuver", "value": "maneuver"}},
                {"set": {"field": "state.last_cqc.status", "value": "'OUT_OF_REACH_OR_NO_AP'"}},
            ],
        }
    ]


def _cqc_displacement_effects() -> list[dict[str, Any]]:
    resolve_check = _cqc_resolve_check_effects()
    return [
        {"spatial_query": {"scene": "state.spatial_scene", "operation": "snapshot"}, "store_as": "_cqc_displacement_snapshot"},
        {"spatial_query": {"scene": "state.spatial_scene", "operation": "path", "snapshot": "{_cqc_displacement_snapshot}", "query": {"map_id": "{map_id}", "start": "{actor.position_cell}", "goal": "{target.position_cell}", "allow_diagonal": True, "corner_policy": "forbid_cutting", "include_entities": False, "max_steps": 2}}, "store_as": "_reach"},
        {"spatial_query": {"scene": "state.spatial_scene", "operation": "path", "snapshot": "{_cqc_displacement_snapshot}", "query": {"map_id": "{map_id}", "start": "{target.position_cell}", "goal": "{destination}", "allow_diagonal": True, "corner_policy": "forbid_cutting", "include_entities": True, "moving_entity_id": "{_target_id}", "max_steps": 16}}, "store_as": "_displacement"},
        {"set": {"field": "_cqc_ap", "value": "definitions.parameters.cqc.action_ap"}},
        {"set": {"field": "_cqc_aptitude", "value": "actor.aptitudes.force"}},
        {"set": {"field": "_defense_aptitude", "value": "target.aptitudes.force"}},
        {"set": {"field": "_displacement_limit", "value": "definitions.parameters.cqc.shove_distance_m"}},
        {"set": {"field": "_displacement_limit", "value": "definitions.parameters.cqc.throw_distance_m"}, "condition": "maneuver == 'THROW'"},
        {
            "condition": "_reach.found == true and _reach.cost <= 1.5 and _displacement.found == true and _displacement.cost > 0 and _displacement.cost <= _displacement_limit and actor.ap >= _cqc_ap and actor.active == true and target.active == true",
            "on_true": [
                *resolve_check,
                {
                    "condition": "_cqc_total >= _cqc_defense",
                    "on_true": [
                        {"set": {"field": "_displacement_position", "value": ["destination[0] + 0.5", "destination[1] + 0.5", 0]}},
                        {"set": {"field": "_displacement_center", "value": ["destination[0] + 0.5", "destination[1] + 0.5", "target.center[2]"]}},
                        {"set": {"field": "_displacement_muzzle", "value": ["destination[0] + 0.91", "destination[1] + 0.5", "target.muzzle[2]"]}},
                        {"set": {"field": "_displacement_muzzle", "value": ["destination[0] - 0.09", "destination[1] + 0.5", "target.muzzle[2]"]}, "condition": "target.side == 'hostiles'"},
                        {"spatial_mutation": {"scene": "state.spatial_scene", "map_id": "{map_id}", "expected_revision": "{_cqc_displacement_snapshot.geometry_revision}", "target": {"kind": "entity", "id": "{_target_id}"}, "patch": {"position": "{_displacement_position}"}}, "store_as": "_cqc_scene"},
                        {"set": {"field": "{_target_id}.position_cell", "value": "destination"}},
                        *network_entry.movement_effects("_target_id"),
                        {"set": {"field": "{_target_id}.center", "value": "_displacement_center"}},
                        {"set": {"field": "{_target_id}.muzzle", "value": "_displacement_muzzle"}},
                        {"set": {"field": "state.last_cqc.geometry_revision", "value": "_cqc_scene.geometry_revision"}},
                        {"condition": "maneuver == 'THROW'", "on_true": [
                            *_stance_effects("_target_id", "'PRONE'"),
                            {"set": {"field": "state.last_cqc.geometry_revision", "value": "_stance_scene.geometry_revision"}},
                        ]},
                    ],
                },
            ],
            "on_false": [
                {"set": {"field": "state.last_cqc.success", "value": "false"}},
                {"set": {"field": "state.last_cqc.maneuver", "value": "maneuver"}},
                {"set": {"field": "state.last_cqc.status", "value": "'OUT_OF_REACH_OR_INVALID_DESTINATION_OR_NO_AP'"}},
            ],
        },
    ]


def _fpv_fragmentation_effects() -> list[dict[str, Any]]:
    """Resolve the selected target from actual detonation distance and frozen material contacts."""
    return [
        {
            "condition": _action_guard("fpv_fragmentation_strike"),
            "on_true": [
                {"dice": {"expression": "d20"}, "store_as": "_fpv_placement"},
                {"set": {"field": "_fpv_total", "value": "_fpv_placement.total + actor.skills.drone_operations + actor.aptitudes.precision"}},
                {"set": {"field": "_fpv_difficulty", "value": "definitions.parameters.check.difficulty.standard"}},
                {"set": {"field": "_fpv_success", "value": "false"}},
                {"set": {"field": "_fpv_success", "value": "true"}, "condition": "_fpv_total >= _fpv_difficulty"},
                {"set": {"field": "_scatter_margin", "value": "max(1, _fpv_difficulty - _fpv_total)"}},
                {"set": {"field": "_scatter_band", "value": 0}},
                {"set": {"field": "_scatter_band", "value": 1}, "condition": "_scatter_margin >= 5"},
                {"set": {"field": "_scatter_band", "value": 2}, "condition": "_scatter_margin >= 10"},
                {"set": {"field": "_scatter_band", "value": 3}, "condition": "_scatter_margin >= 15"},
                {"set": {"field": "_scatter_distance", "value": "definitions.parameters.grenade.scatter.distance_by_failure_margin_m[_scatter_band]"}},
                {"dice": {"expression": "d8"}, "store_as": "_scatter_direction"},
                {"set": {"field": "_scatter_dx", "value": 0}},
                {"set": {"field": "_scatter_dy", "value": -1}},
                {"set": {"field": "_scatter_dx", "value": 1}, "condition": "_scatter_direction.total == 2 or _scatter_direction.total == 3 or _scatter_direction.total == 4"},
                {"set": {"field": "_scatter_dy", "value": 0}, "condition": "_scatter_direction.total == 3 or _scatter_direction.total == 7"},
                {"set": {"field": "_scatter_dy", "value": 1}, "condition": "_scatter_direction.total == 4 or _scatter_direction.total == 5 or _scatter_direction.total == 6"},
                {"set": {"field": "_scatter_dx", "value": 0}, "condition": "_scatter_direction.total == 5"},
                {"set": {"field": "_scatter_dx", "value": -1}, "condition": "_scatter_direction.total == 6 or _scatter_direction.total == 7 or _scatter_direction.total == 8"},
                {"set": {"field": "_scatter_dy", "value": -1}, "condition": "_scatter_direction.total == 8"},
                {"set": {"field": "_fpv_candidate", "value": ["landing_point[0] + _scatter_dx * _scatter_distance", "landing_point[1] + _scatter_dy * _scatter_distance", "landing_point[2]"]}},
                {"set": {"field": "_fpv_candidate", "value": "landing_point"}, "condition": "_fpv_success == true"},
                {"spatial_query": {"scene": "state.spatial_scene", "operation": "snapshot"}, "store_as": "_approach_snapshot"},
                {"spatial_query": {"scene": "state.spatial_scene", "operation": "ray", "snapshot": "{_approach_snapshot}", "query": {"map_id": "{map_id}", "origin": "{drone.muzzle}", "destination": "{_fpv_candidate}", "include": ["geometry", "entities"], "max_intersections": 64}}, "store_as": "_approach_ray"},
                {"set": {"field": "_actual_detonation", "value": "_fpv_candidate"}},
                {"set": {"field": "_approach_first_id", "value": "null"}},
                {"set": {"field": "_approach_intersections", "value": "_approach_ray.intersections"}},
                {"for_each": {"collection": "_approach_intersections", "as": "_approach_intersection", "do": [
                    {"set": {"field": "_actual_detonation", "value": "_approach_intersection.point"}, "condition": "_approach_first_id == null"},
                    {"set": {"field": "_approach_first_id", "value": "_approach_intersection.id"}, "condition": "_approach_first_id == null"},
                ]}},
                {"spatial_mutation": {"scene": "state.spatial_scene", "map_id": "{map_id}", "expected_revision": "{_approach_snapshot.geometry_revision}", "target": {"kind": "entity", "id": "{_drone_id}"}, "patch": {"position": "{_actual_detonation}", "active": False, "movement": "passable", "visibility": "clear", "metadata": {"player": {"label": "{drone.name}", "kind": "drone", "side": "allies", "state": "destroyed"}}}}, "store_as": "_fpv_mutation"},
                {"set": {"field": "{_drone_id}.active", "value": "false"}},
                {"set": {"field": "{_drone_id}.payload", "value": "null"}},
                {"set": {"field": "{_drone_id}.position_point", "value": "_actual_detonation"}},
                {"decrement": {"field": "{_actor_id}.ap", "amount": "definitions.parameters.equipment.fpv_fragmentation_payload.attack_ap"}},
                {"spatial_query": {"scene": "state.spatial_scene", "operation": "snapshot"}, "store_as": "_fpv_blast_snapshot"},
                {"set": {"field": "_fpv_max_radius", "value": "definitions.parameters.equipment.frag_grenade.band_end_m[3]"}},
                {"spatial_query": {"scene": "state.spatial_scene", "operation": "ray", "snapshot": "{_fpv_blast_snapshot}", "query": {"map_id": "{map_id}", "origin": "{_actual_detonation}", "destination": "{target.center}", "include": ["geometry", "entities"], "max_intersections": 64}}, "store_as": "_fpv_fragment_ray"},
                {"set": {"field": "_fragment_first", "value": "null"}},
                {"set": {"field": "_fragment_first_id", "value": "null"}},
                {"set": {"field": "_fragment_distance", "value": 1000000}},
                {"set": {"field": "_fragment_intersections", "value": "_fpv_fragment_ray.intersections"}},
                {"for_each": {"collection": "_fragment_intersections", "as": "_fragment_intersection", "do": [
                    {"set": {"field": "_fragment_first", "value": "_fragment_intersection"}, "condition": "_fragment_first_id == null"},
                    {"set": {"field": "_fragment_first_id", "value": "_fragment_intersection.id"}, "condition": "_fragment_first_id == null"},
                    {"set": {"field": "_fragment_distance", "value": "_fragment_intersection.distance"}, "condition": "_fragment_intersection.id == _target_id"},
                ]}},
                {"set": {"field": "_fpv_target_in_range", "value": "_fragment_distance <= _fpv_max_radius"}},
                {"set": {"field": "_fragment_band", "value": 3}},
                {"set": {"field": "_fragment_band", "value": 2}, "condition": "_fragment_distance <= definitions.parameters.equipment.frag_grenade.band_end_m[2]"},
                {"set": {"field": "_fragment_band", "value": 1}, "condition": "_fragment_distance <= definitions.parameters.equipment.frag_grenade.band_end_m[1]"},
                {"set": {"field": "_fragment_band", "value": 0}, "condition": "_fragment_distance <= definitions.parameters.equipment.frag_grenade.band_end_m[0]"},
                {"set": {"field": "_fragment_damage", "value": "definitions.parameters.equipment.frag_grenade.damage[_fragment_band]"}},
                {"set": {"field": "_fragment_target_pen", "value": "definitions.parameters.equipment.frag_grenade.target_pen[_fragment_band]"}},
                {"set": {"field": "_fragment_material_pen", "value": "definitions.parameters.equipment.frag_grenade.material_pen[_fragment_band]"}},
                *_ordered_material_route(
                    prefix="_fragment_route",
                    intersections="_fragment_intersections",
                    target_id="_target_id",
                    damage="_fragment_damage",
                    target_pen="_fragment_target_pen",
                    material_pen="_fragment_material_pen",
                    snapshot_revision="_fpv_blast_snapshot.geometry_revision",
                    record="state.last_drone_attack",
                    when="_fpv_target_in_range == true",
                ),
                {"set": {"field": "_fragment_reaches_target", "value": "false"}},
                {"set": {"field": "_fragment_reaches_target", "value": "true"}, "condition": "_fpv_target_in_range == true and _fragment_route_target_reached == true"},
                {"set": {"field": "_fragment_hp_bypass", "value": "min(target.hp, min(_fragment_damage, _fragment_target_pen))"}},
                {"set": {"field": "_fragment_ordinary", "value": "max(0, _fragment_damage - min(_fragment_damage, _fragment_target_pen))"}},
                {"set": {"field": "_fragment_res_damage", "value": "min(target.res, _fragment_ordinary)"}},
                {"set": {"field": "_fragment_hp_overflow", "value": "min(max(0, target.hp - _fragment_hp_bypass), max(0, _fragment_ordinary - target.res))"}},
                {"decrement": {"field": "{_target_id}.hp", "amount": "_fragment_hp_bypass + _fragment_hp_overflow"}, "condition": "_fragment_reaches_target == true"},
                {"decrement": {"field": "{_target_id}.res", "amount": "_fragment_res_damage"}, "condition": "_fragment_reaches_target == true"},
                {"set": {"field": "{_target_id}.downed", "value": "true"}, "condition": "_fragment_reaches_target == true and target.hp <= 0"},
                {"set": {"field": "state.focused_actor", "value": "_actor_id"}},
                {"set": {"field": "state.last_drone_attack.mode", "value": "'FPV_FRAGMENTATION'"}},
                {"set": {"field": "state.last_drone_attack.roll", "value": "_fpv_placement.total"}},
                {"set": {"field": "state.last_drone_attack.placement_success", "value": "_fpv_success"}},
                {"set": {"field": "state.last_drone_attack.actual_detonation", "value": "_actual_detonation"}},
                {"set": {"field": "state.last_drone_attack.target", "value": "_target_id"}},
                {"set": {"field": "state.last_drone_attack.target_reached", "value": "_fragment_reaches_target"}},
                {"set": {"field": "state.last_drone_attack.snapshot_id", "value": "_fpv_blast_snapshot.snapshot_id"}},
                {"set": {"field": "state.last_drone_attack.geometry_revision", "value": "_fpv_blast_snapshot.geometry_revision"}},
                {"set": {"field": "state.last_drone_attack.status", "value": "'DETONATED'"}},
            ],
            "on_false": [{"set": {"field": "state.last_drone_attack.status", "value": "'FPV_STRIKE_REJECTED'"}}],
        }
    ]


def _autodrone_fire_effects() -> list[dict[str, Any]]:
    return [
        {
            "condition": _action_guard("autodrone_fire"),
            "on_true": [
                {"spatial_query": {"scene": "state.spatial_scene", "operation": "snapshot"}, "store_as": "_drone_shot_snapshot"},
                {"spatial_query": {"scene": "state.spatial_scene", "operation": "exposure", "snapshot": "{_drone_shot_snapshot}", "query": {"map_id": "{map_id}", "observer": {"point": "{actor.muzzle}"}, "target": {"entity_id": "{_target_id}"}, "samples": {"horizontal": 3, "vertical": 3}, "include": ["geometry", "entities"]}}, "store_as": "_drone_exposure"},
                {"spatial_query": {"scene": "state.spatial_scene", "operation": "ray", "snapshot": "{_drone_shot_snapshot}", "query": {"map_id": "{map_id}", "origin": "{actor.muzzle}", "destination": "{aim_point}", "include": ["geometry", "entities"], "max_intersections": 64}}, "store_as": "_drone_ray"},
                {"set": {"field": "_drone_first", "value": "null"}},
                {"set": {"field": "_drone_first_id", "value": "null"}},
                {"set": {"field": "_drone_range", "value": 1000000}},
                {"set": {"field": "_drone_intersections", "value": "_drone_ray.intersections"}},
                {"for_each": {"collection": "_drone_intersections", "as": "_drone_intersection", "do": [
                    {"set": {"field": "_drone_first", "value": "_drone_intersection"}, "condition": "_drone_first_id == null"},
                    {"set": {"field": "_drone_first_id", "value": "_drone_intersection.id"}, "condition": "_drone_first_id == null"},
                    {"set": {"field": "_drone_range", "value": "_drone_intersection.distance"}, "condition": "_drone_intersection.id == _target_id"},
                ]}},
                {"set": {"field": "_drone_range_modifier", "value": "definitions.parameters.equipment.autodrone_light_weapon.range_modifiers[3]"}},
                {"set": {"field": "_drone_range_modifier", "value": "definitions.parameters.equipment.autodrone_light_weapon.range_modifiers[2]"}, "condition": "_drone_range <= definitions.parameters.equipment.autodrone_light_weapon.range_breakpoints_m[2]"},
                {"set": {"field": "_drone_range_modifier", "value": "definitions.parameters.equipment.autodrone_light_weapon.range_modifiers[1]"}, "condition": "_drone_range <= definitions.parameters.equipment.autodrone_light_weapon.range_breakpoints_m[1]"},
                {"set": {"field": "_drone_range_modifier", "value": "definitions.parameters.equipment.autodrone_light_weapon.range_modifiers[0]"}, "condition": "_drone_range <= definitions.parameters.equipment.autodrone_light_weapon.range_breakpoints_m[0]"},
                {"set": {"field": "_drone_cover_offset", "value": 0}},
                {"set": {"field": "_drone_cover_offset", "value": "definitions.parameters.cover.actual_difficulty_offset.half"}, "condition": "_drone_exposure.exposure < 1 and _drone_exposure.exposure >= 0.5"},
                {"set": {"field": "_drone_cover_offset", "value": "definitions.parameters.cover.actual_difficulty_offset.quarter"}, "condition": "_drone_exposure.exposure < 0.5 and _drone_exposure.exposure > 0"},
                {"set": {"field": "_drone_open_difficulty", "value": "definitions.parameters.check.difficulty.standard + _drone_range_modifier + actor.weapon.current_recoil"}},
                {"set": {"field": "_drone_actual_difficulty", "value": "_drone_open_difficulty + _drone_cover_offset"}},
                {"dice": {"expression": "d20"}, "store_as": "_drone_attack"},
                {"set": {"field": "_drone_total", "value": "_drone_attack.total + definitions.parameters.equipment.autodrone_light_weapon.targeting_bonus"}},
                {"set": {"field": "_drone_margin", "value": "_drone_total - _drone_actual_difficulty"}},
                {"set": {"field": "_drone_quality", "value": "'MISS'"}},
                {"set": {"field": "_drone_quality", "value": "'HIT'"}, "condition": "_drone_attack.total != 1 and _drone_total >= _drone_actual_difficulty and _drone_exposure.exposure > 0"},
                {"set": {"field": "_drone_quality", "value": "'CLEAN_HIT'"}, "condition": "_drone_quality != 'MISS' and _drone_margin >= definitions.parameters.hit_quality.clean_margin"},
                {"set": {"field": "_drone_quality", "value": "'DECISIVE_HIT'"}, "condition": "_drone_quality != 'MISS' and _drone_margin >= definitions.parameters.hit_quality.decisive_margin"},
                {"set": {"field": "_drone_quality", "value": "'CRITICAL_HIT'"}, "condition": "_drone_attack.total != 1 and _drone_exposure.exposure > 0 and (_drone_margin >= definitions.parameters.hit_quality.critical_margin or _drone_attack.total == 20)"},
                {"set": {"field": "_drone_damage", "value": "definitions.parameters.equipment.autodrone_light_weapon.damage"}},
                {"set": {"field": "_drone_target_pen", "value": "definitions.parameters.equipment.autodrone_light_weapon.target_pen"}},
                {"set": {"field": "_drone_material_pen", "value": "definitions.parameters.equipment.autodrone_light_weapon.material_pen"}},
                {"set": {"field": "_fx_drone_contacts", "value": []}},
                {"set": {"field": "_drone_target_pen", "value": "_drone_target_pen + definitions.parameters.hit_quality.clean_target_pen"}, "condition": "_drone_quality == 'CLEAN_HIT' or _drone_quality == 'DECISIVE_HIT' or _drone_quality == 'CRITICAL_HIT'"},
                {"set": {"field": "_drone_damage", "value": "_drone_damage + definitions.parameters.hit_quality.decisive_damage"}, "condition": "_drone_quality == 'DECISIVE_HIT' or _drone_quality == 'CRITICAL_HIT'"},
                {"set": {"field": "_drone_damage", "value": "_drone_damage + definitions.parameters.critical.devastating_damage"}, "condition": "_drone_quality == 'CRITICAL_HIT'"},
                *_ordered_material_route(
                    prefix="_drone_route",
                    intersections="_drone_intersections",
                    target_id="_target_id",
                    damage="_drone_damage",
                    target_pen="_drone_target_pen",
                    material_pen="_drone_material_pen",
                    snapshot_revision="_drone_shot_snapshot.geometry_revision",
                    record="state.last_drone_attack",
                    contacts="_fx_drone_contacts",
                    when="_drone_quality != 'MISS'",
                ),
                {"set": {"field": "_drone_hp_bypass", "value": "min(target.hp, min(_drone_damage, _drone_target_pen))"}},
                {"set": {"field": "_drone_ordinary", "value": "max(0, _drone_damage - min(_drone_damage, _drone_target_pen))"}},
                {"set": {"field": "_drone_res_damage", "value": "min(target.res, _drone_ordinary)"}},
                {"set": {"field": "_drone_hp_overflow", "value": "min(max(0, target.hp - _drone_hp_bypass), max(0, _drone_ordinary - target.res))"}},
                {"decrement": {"field": "{_target_id}.hp", "amount": "_drone_hp_bypass + _drone_hp_overflow"}, "condition": "_drone_quality != 'MISS' and _drone_route_target_reached == true"},
                {"decrement": {"field": "{_target_id}.res", "amount": "_drone_res_damage"}, "condition": "_drone_quality != 'MISS' and _drone_route_target_reached == true"},
                {"set": {"field": "{_target_id}.downed", "value": "true"}, "condition": "_drone_quality != 'MISS' and _drone_route_target_reached == true and target.hp <= 0"},
                {"decrement": {"field": "{_actor_id}.ap", "amount": "definitions.parameters.equipment.autodrone_light_weapon.attack_ap_equivalent"}},
                {"decrement": {"field": "{_actor_id}.weapon.magazine.rounds", "amount": 1}},
                {"increment": {"field": "{_actor_id}.weapon.current_recoil", "amount": "definitions.parameters.equipment.autodrone_light_weapon.recoil"}},
                {"set": {"field": "state.focused_actor", "value": "_actor_id"}},
                {"set": {"field": "state.last_drone_attack.mode", "value": "'AUTONOMOUS_LIGHT_WEAPON'"}},
                {"set": {"field": "state.last_drone_attack.roll", "value": "_drone_attack.total"}},
                {"set": {"field": "state.last_drone_attack.total", "value": "_drone_total"}},
                {"set": {"field": "state.last_drone_attack.outcome", "value": "_drone_quality"}},
                {"set": {"field": "state.last_drone_attack.outcome", "value": "'PROJECTILE_STOP'"}, "condition": "_drone_quality != 'MISS' and _drone_route_target_reached == false"},
                {"set": {"field": "state.last_drone_attack.target", "value": "_target_id"}},
                _shot_effect(
                    snapshot="_drone_shot_snapshot",
                    source_id="_actor_id",
                    target_id="_target_id",
                    origin="actor.muzzle",
                    destination="aim_point",
                    delay_ms=0,
                    miss="_drone_quality == 'MISS'",
                    contacts="_fx_drone_contacts",
                ),
            ],
            "on_false": [{"set": {"field": "state.last_drone_attack.status", "value": "'AUTONOMOUS_FIRE_REJECTED'"}}],
        }
    ]


def _end_turn_effects(next_side: str) -> list[dict[str, Any]]:
    """Pass control using the authored roster and each current actor's eligibility and AP pool."""
    return [
        {"set": {"field": "state.active_side", "value": repr(next_side)}},
        {"set": {"field": "state.decision_owner", "value": "'player'" if next_side == "allies" else "'gm'"}},
        {"set": {"field": "state.focused_actor", "value": "null"}},
        {"set": {"field": "_turn_eligible", "value": []}},
        {"for_each": {"collection": "turn_roster", "as": "_turn_actor_id", "do": [
            {"call": "td_refresh_turn_actor",
             "params": {"actor": "{_turn_actor_id}", "next_side": next_side,
                        "prepared_suppression": "{prepared_suppression}"},
             "export_context": {"_turn_actor_eligible": "_turn_actor_eligible"}},
            {"list_push": {"list": "_turn_eligible", "value": "_turn_actor_id"},
             "condition": "_turn_actor_eligible == true and _turn_actor_id not in _turn_eligible"},
        ]}},
        {"set": {"field": "state.eligible_actor_ids", "value": "_turn_eligible"}},
        # Roster identities belong to state, not the player-visible result of ending a turn.
        {"set": {"field": "_turn_eligible", "value": []}},
        {"set": {"field": "_turn_actor_id", "value": None}},
        {"set": {"field": "_turn_actor_eligible", "value": None}},
    ]


def _turn_actor_helper() -> dict[str, Any]:
    """Resolve one current actor inside the turn transaction; this helper is not a public action."""
    return {
        "description": "Refresh or exhaust one roster actor when its side's turn changes.",
        "parameters": [
            _param("actor", "entity_id", resolve="entity"),
            _param("next_side", "enum", enum=("allies", "hostiles")),
            _param("prepared_suppression", "object", from_state="state.prepared.suppression"),
        ],
        "effects": [
            # Committed suppressive fire lasts through the opposing turn, without refund.
            {"set": {"field": "state.prepared.suppression.active", "value": "false"},
             "condition": "prepared_suppression.owner == _actor_id and actor.side == next_side"},
            {"set": {"field": "state.prepared.suppression.capacity", "value": 0},
             "condition": "prepared_suppression.owner == _actor_id and actor.side == next_side"},
            {"set": {"field": "_turn_actor_eligible", "value": (
                "actor.side == next_side and actor.active == true and actor.downed == false "
                "and actor.max_ap > 0"
            )}},
            {"set": {"field": "{_actor_id}.ap", "value": 0}},
            {"set": {"field": "{_actor_id}.ap", "value": "actor.max_ap"},
             "condition": "_turn_actor_eligible == true"},
        ],
        "metadata": {"costs_action": False},
    }


def _specialty_execution_records(balance: dict[str, Any]) -> dict[str, dict[str, Any]]:
    nodes = {
        item["node"]["node_id"]: item
        for specialty in balance["specialties"].values()
        for item in specialty["nodes"]
    }
    boundary_fixtures: dict[str, dict[str, dict[str, Any]]] = {}
    for item in balance.get("boundary_violation_matrix", []):
        fixtures = boundary_fixtures.setdefault(item["node_id"], {})
        fixtures.setdefault(
            item["enforcement"],
            {
                "violated_field": item["violated_field"],
                "status": item["status"],
                "control": item["control_result"],
                "result": item["candidate_result"],
                "node_effect_applied": item["node_effect_applied"],
                "effect_established_before_termination": item[
                    "effect_established_before_termination"
                ],
                "mechanical_operations_executed": item[
                    "mechanical_operations_executed"
                ],
                "limitation_results": item["limitation_results"],
                "limitations_preserved": item["limitations_preserved"],
                "failed_guards": item["failed_guards"],
            },
        )
    records: dict[str, dict[str, Any]] = {}
    for counterfactual in balance["counterfactuals"]:
        node_id = counterfactual["node_id"]
        coverage = nodes[node_id]
        node = coverage["node"]
        node_boundary_fixtures = deepcopy(boundary_fixtures.get(node_id, {}))
        for enforcement in ("GUARD", "TERMINATION", "INVARIANT"):
            node_boundary_fixtures.setdefault(
                enforcement,
                {
                    "violated_field": None,
                    "status": "BOUNDARY_NOT_AUTHORED",
                    "control": deepcopy(counterfactual["control"]),
                    "result": deepcopy(counterfactual["control"]),
                    "node_effect_applied": False,
                    "effect_established_before_termination": False,
                    "mechanical_operations_executed": [],
                    "limitation_results": deepcopy(
                        counterfactual["limitation_results"]
                    ),
                    "limitations_preserved": True,
                    "failed_guards": [],
                },
            )
        def branch_prerequisite_fixtures(branch: str) -> tuple[dict[str, Any], dict[str, Any]]:
            contract = deepcopy(counterfactual["prerequisite_contract"])
            contract.update(
                deepcopy(
                    counterfactual.get("branch_prerequisite_contract", {}).get(
                        branch, {}
                    )
                )
            )
            negative = deepcopy(contract)
            if negative:
                first_field = sorted(negative)[0]
                expected = negative[first_field]
                negative[first_field] = (
                    not expected
                    if isinstance(expected, bool)
                    else f"NOT_{expected}"
                )
            return contract, negative

        branch_fixture_table = [
            {
                "effect_branch": branch,
                "selected_branch": branch,
                "mechanical_operations": fixture["mechanical_operations"],
                "changed_fields": fixture["changed_fields"],
                "prerequisite_contract": branch_prerequisite_fixtures(branch)[0],
                "prerequisite_fixture": branch_prerequisite_fixtures(branch)[0],
                "negative_prerequisite_fixture": branch_prerequisite_fixtures(branch)[1],
            }
            for branch, fixture in sorted(
                counterfactual["branch_fixtures"].items()
            )
        ]
        if counterfactual["default_branch"] != "DEFAULT":
            default_fixture = counterfactual["branch_fixtures"][
                counterfactual["default_branch"]
            ]
            branch_fixture_table.insert(
                0,
                {
                    "effect_branch": "DEFAULT",
                    "selected_branch": counterfactual["default_branch"],
                    "mechanical_operations": default_fixture[
                        "mechanical_operations"
                    ],
                    "changed_fields": default_fixture["changed_fields"],
                    "prerequisite_contract": branch_prerequisite_fixtures(
                        counterfactual["default_branch"]
                    )[0],
                    "prerequisite_fixture": branch_prerequisite_fixtures(
                        counterfactual["default_branch"]
                    )[0],
                    "negative_prerequisite_fixture": branch_prerequisite_fixtures(
                        counterfactual["default_branch"]
                    )[1],
                },
            )
        records[node_id] = {
            "node_id": node_id,
            "specialty": node["specialty"],
            "class_id": node["class_id"],
            "tier": node["tier"],
            "mechanic_id": counterfactual["mechanic_id"],
            "control": counterfactual["control"],
            "changed_fields": counterfactual["changed_fields"],
            "prerequisite_fields": counterfactual["prerequisite_fields"],
            "prerequisite_contract": counterfactual["prerequisite_contract"],
            "branch_prerequisite_contract": counterfactual.get(
                "branch_prerequisite_contract", {}
            ),
            "prerequisite_fixture": counterfactual["prerequisite_fixture"],
            "negative_prerequisite_fixture": counterfactual[
                "negative_prerequisite_fixture"
            ],
            "authored_rule_contract": counterfactual["authored_rule_contract"],
            "authored_effect_fields": counterfactual["authored_effect_fields"],
            "authored_constraint_fields": counterfactual[
                "authored_constraint_fields"
            ],
            "authored_guard_fields": counterfactual["authored_guard_fields"],
            "authored_termination_fields": counterfactual[
                "authored_termination_fields"
            ],
            "authored_invariant_fields": counterfactual[
                "authored_invariant_fields"
            ],
            "mechanical_operations": counterfactual["mechanical_operations"],
            "branch_options": counterfactual["branch_options"],
            "default_branch": counterfactual["default_branch"],
            "branch_fixture_table": branch_fixture_table,
            "branch_fixtures": {
                fixture["effect_branch"]: fixture
                for fixture in branch_fixture_table
            },
            "limitation_contract": counterfactual["limitation_contract"],
            "limitation_results": counterfactual["limitation_results"],
            "parameter_ids": counterfactual["parameter_ids"],
            "available_benchmark_levels": counterfactual["available_benchmark_levels"],
            "limitations_preserved": counterfactual["limitations_preserved"],
            "boundary_fixtures": node_boundary_fixtures,
            "source": {
                "path": node["source_path"],
                "sha256": node["source_sha256"],
                "source_commit": node["source_commit"],
            },
        }
    return records


def _card_effects(name: str, effects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Capture this action's explicit display values alongside their existing telemetry writes.

    Fresh context values prevent a rejected action from inheriting earlier state telemetry. Copy
    only the selected display assignments, preserving their guards and all existing game effects.
    """
    if name == "move_actor":
        fields = {"status": ("move_status", "INACTIVE_SIDE"),
                  "origin": ("move_origin", None),
                  "cost": ("move_distance", None), "ap": ("move_ap", None)}
        state_root = "state.last_move"
    elif name in {"fire_single", "fire_burst", "fire_full_auto"}:
        fields = {"outcome": ("attack_outcome", "INACTIVE_SIDE"),
                  "roll": ("attack_roll", None), "total": ("attack_total", None),
                  "actual_difficulty": ("attack_difficulty", None),
                  "rounds_fired": ("attack_rounds", 0)}
        state_root = "state.last_attack"
    elif name == "treat_casualty":
        fields = {"status": ("medical_status", "INACTIVE_SIDE"),
                  "roll": ("medical_roll", None), "total": ("medical_total", None),
                  "difficulty": ("medical_difficulty", None),
                  "ap_spent": ("medical_ap", 0), "res_restored": ("medical_res", 0)}
        state_root = "state.last_medical"
    else:
        return effects
    names = {f"{state_root}.{field}": f"_card_{alias}" for field, (alias, _) in fields.items()}

    def capture(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: capture(child) for key, child in value.items()}
        if not isinstance(value, list):
            return value
        result = []
        for original in value:
            item = capture(original)
            result.append(item)
            assignment = item.get("set") if isinstance(item, dict) else None
            if isinstance(assignment, dict) and assignment.get("field") in names:
                result.append({**deepcopy(item), "set": {
                    **deepcopy(assignment), "field": names[assignment["field"]],
                }})
        return result

    initial = [{"set": {"field": f"_card_{alias}",
                        "value": repr(default) if isinstance(default, str) else default}}
               for alias, default in fields.values()]
    return initial + capture(effects)


def _player_control_checks(name: str) -> list[dict[str, str]]:
    """Expose existing effect prerequisites that can be evaluated before an action runs."""
    actor_name = "attacker" if name in {"aim", "fire_single", "fire_burst", "fire_full_auto"} else "actor"
    checks = [{
        "condition": f"{actor_name}.side == active_side",
        "reason": "This actor cannot act during the other side's turn.",
    }]
    checks.extend(
        {"condition": condition, "reason": reason}
        for condition, reason in _action_guard_parts(name)
    )
    if name == "move_actor":
        checks.extend([
            {
                "condition": "_path.found == true",
                "reason": "A movement route to this destination could not be found.",
            },
            {
                "condition": "actor.ap >= _move_ap",
                "reason": "This actor does not have enough action points for the movement route.",
            },
        ])
    if name == "network_connection":
        checks.extend(network_entry.checks())
    if name in {"intrude_network", "pivot_network"}:
        checks.extend(network_intrusion.checks(pivot=name == "pivot_network"))
    if name == "operate_remote_function":
        checks.extend(network_functions.operation_checks())
    return checks


def _tool_metadata(name: str, costs_action: bool) -> dict[str, Any]:
    """Declare TD's direct-control policy and saved result presentation for Sylvia."""
    metadata: dict[str, Any] = {
        "costs_action": costs_action,
        "source_provenance": _tool_sources(name),
    }
    actor_parameters = {
        "move_actor": "actor", "change_stance": "actor", "aim": "attacker", "fire_single": "attacker",
        "fire_burst": "attacker", "fire_full_auto": "attacker",
        "fpv_fragmentation_strike": "actor", "autodrone_fire": "actor",
        "prepare_overwatch": "actor", "establish_suppression": "actor",
        "intrude_network": "actor", "pivot_network": "actor", "operate_remote_function": "actor",
        "network_connection": "actor",
        "cycle_weapon": "actor",
    }
    if name in actor_parameters:
        confirmation = "required" if name in {
            "fire_single", "fire_burst", "fire_full_auto",
            "fpv_fragmentation_strike", "autodrone_fire",
        } else "none"
        controls: dict[str, Any] = {
            "confirmation": confirmation,
            "response": "on_request",
            "actor_parameter": actor_parameters[name],
            "checks": _player_control_checks(name),
        }
        if name in {"intrude_network", "pivot_network"}:
            controls["calculations"] = network_intrusion.calculations(pivot=name == "pivot_network")
        if name == "operate_remote_function":
            controls["calculations"] = network_functions.operation_preview_calculations()
        if name in {"intrude_network", "pivot_network", "operate_remote_function"}:
            controls["preview"] = {
                "version": 1, "title": "Network access",
                "summary": [
                    {"label": "Route entry" if index == 0 else "Then",
                     "value": {"path": f"context._network_route_{index}"}, "optional": True}
                    for index in range(network_functions.MAX_ROUTE_NODES)
                ],
            }
            if name == "operate_remote_function":
                controls["preview"]["title"] = "Function access"
                controls["preview"]["summary"].extend([
                    {"label": "Access", "value": "Permitted; no intrusion check"},
                    {"label": "AP cost", "value": {"path": "context._network_function_ap"}},
                ])
        if name == "move_actor":
            controls["queries"] = [_move_path_query()]
            controls["calculations"] = [
                {"set": {"field": "_preview_actor_name", "value": "actor.name"}},
                {
                    "set": {"field": "_move_ap", "value": _move_ap_expression()},
                    "condition": "_path.found == true",
                },
            ]
            controls["map_preview"] = {"path": "_path"}
            controls["preview"] = {
                "version": 1,
                "title": "Move estimate",
                "summary": [
                    {"label": "Actor", "value": {"path": "context._preview_actor_name"}},
                    {"label": "Destination", "value": {"path": "parameters.destination"}},
                    {
                        "label": "Estimated AP",
                        "value": {"path": "context._move_ap"},
                        "optional": True,
                    },
                ],
            }
        elif name in {"fire_single", "fire_burst", "fire_full_auto"}:
            controls["calculations"] = [
                {"set": {"field": "_preview_attacker_name", "value": "attacker.name"}},
                {"set": {"field": "_preview_target_name", "value": "target.name"}},
            ]
            controls["queries"] = [
                _firing_exposure_query(store_as="_preview_exposure"),
                _firing_ray_query(store_as="_preview_ray"),
            ]
            controls["map_preview"] = {
                "ray": "_preview_ray",
                "exposure": "_preview_exposure",
            }
        if confirmation == "required":
            firearm_attack = name in {"fire_single", "fire_burst", "fire_full_auto"}
            controls.setdefault("preview", {
                "version": 1,
                "title": "Confirm fire",
                "summary": [
                    {"label": "Actor", "value": {"path": (
                        "context._preview_attacker_name" if firearm_attack
                        else f"parameters.{actor_parameters[name]}"
                    )}},
                    {"label": "Target", "value": {"path": (
                        "context._preview_target_name" if firearm_attack else "parameters.target"
                    )}, "optional": True},
                    {"label": "Landing point" if name == "fpv_fragmentation_strike" else "Aim point",
                     "value": {"path": "parameters.landing_point" if name == "fpv_fragmentation_strike" else "parameters.aim_point"}, "optional": True},
                    {"label": "Drone", "value": {"path": "parameters.drone"}, "optional": True},
                    {"label": "Rounds", "value": {"path": "parameters.rounds"}, "optional": True},
                ],
            })
        metadata["player_controls"] = controls
    elif name == "end_allies_turn":
        metadata["player_controls"] = {
            "confirmation": "required",
            "response": "automatic",
            "preview": {"version": 1, "title": "End allied team turn", "summary": []},
        }
    if name == "move_actor":
        metadata["result_card"] = {
            "version": 1, "title": "Movement",
            "summary": [
                {"label": "Actor", "value": {"path": "context.actor.name"}},
                {"label": "Status", "value": {"path": "context._card_move_status", "labels": {
                    "MOVED": "Moved", "NO_AP_OR_INACTIVE": "No AP available or actor inactive",
                    "ILLEGAL_OR_UNREACHABLE": "Route unavailable", "INACTIVE_SIDE": "Other team's turn",
                }}, "emphasis": True},
                {"label": "AP spent", "value": {"path": "context._card_move_ap"}, "optional": True},
            ],
            "details": [
                {"label": "Origin", "value": {"path": "context._card_move_origin"}, "optional": True},
                {"label": "Destination", "value": {"path": "parameters.destination"}},
                {"label": "Distance", "value": {"path": "context._card_move_distance"}, "optional": True},
            ],
        }
    elif name == "change_stance":
        metadata["result_card"] = {
            "version": 1, "title": "Stance",
            "summary": [
                {"label": "Actor", "value": {"path": "context.actor.name"}},
                {"label": "Stance", "value": {"path": "context.actor.stance"}},
            ],
            "details": [
                {"label": "AP remaining", "value": {"path": "context.actor.ap"}},
                {"label": "Metres per AP", "value": {"path": "context.actor.movement_m_per_ap"}},
            ],
        }
    elif name == "resolve_trigger_crossing":
        metadata["result_card"] = {
            "version": 1, "title": "Prepared fire crossing",
            "summary": [
                {"label": "Outcome", "value": {"path": "context._card_trigger_status", "labels": {
                    "NO_ACTIVE_TRIGGER": "No active trigger on this movement",
                    "NO_PATH": "No valid movement path",
                    "INSUFFICIENT_AP": "Not enough AP for this movement",
                    "RESUMED": "Reactions resolved; movement continued",
                    "INTERRUPTED": "Reactions stopped the movement",
                }}, "emphasis": True},
            ],
            "details": [
                {"label": "Moving actor", "value": {"path": "context.actor.name"}},
                {"label": "Movement AP required", "value": {"path": "context._trigger_full_ap"}, "optional": True},
                {"label": "AP before movement", "value": {"path": "context._trigger_available_ap"}},
            ],
        }
    elif name == "treat_casualty":
        metadata["result_card"] = {
            "version": 1, "title": "Treatment",
            "summary": [
                {"label": "Outcome", "value": {"path": "context._card_medical_status", "labels": {
                    "TREATED": "Treatment completed", "FAILED_CHECK": "Treatment unsuccessful",
                    "OUT_OF_REACH": "Cannot reach the casualty",
                    "MISSING_CONDITION_INTERVENTION_WINDOW_OR_AP": "Treatment prerequisites not met",
                    "MISSING_RES_MAXIMUM": "Casualty's RES maximum is not defined",
                    "INVALID_ISSUE_OR_TREATMENT_HISTORY": "Treatment identity unavailable",
                    "INACTIVE_SIDE": "Other team's turn",
                }}, "emphasis": True},
                {"label": "RES restored", "value": {"path": "context._card_medical_res"}},
                {"label": "AP spent", "value": {"path": "context._card_medical_ap"}},
            ],
            "details": [
                {"label": "Medic", "value": {"path": "context.actor.name"}},
                {"label": "Casualty", "value": {"path": "context.target.name"}},
                {"label": "Roll", "value": {"path": "context._card_medical_roll"}, "optional": True},
                {"label": "Total", "value": {"path": "context._card_medical_total"}, "optional": True},
                {"label": "Difficulty", "value": {"path": "context._card_medical_difficulty"}, "optional": True},
            ],
        }
    elif name in {"fire_single", "fire_burst", "fire_full_auto"}:
        metadata["result_card"] = {
            "version": 1, "title": "Fire",
            "summary": [
                {"label": "Outcome", "value": {"path": "context._card_attack_outcome", "labels": {
                    **{outcome: outcome.replace("_", " ").capitalize() for outcome in (
                        "HIT", "CLEAN_HIT", "DECISIVE_HIT", "CRITICAL_HIT", "MISS", "WIDE_MISS",
                    )},
                    "COVER_INTERCEPT": "Cover intercepted", "PROJECTILE_STOP": "Projectile stopped",
                    "FIRE_REJECTED": "Unable to fire", "INACTIVE_SIDE": "Other team's turn",
                }}, "emphasis": True},
                {"label": "Total", "value": {"path": "context._card_attack_total"}, "optional": True},
            ],
            "details": [
                {"label": "Attacker", "value": {"path": "context.attacker.name"}},
                {"label": "Target", "value": {"path": "context.target.name"}},
                {"label": "Roll", "value": {"path": "context._card_attack_roll"}, "optional": True},
                {"label": "Difficulty", "value": {"path": "context._card_attack_difficulty"}, "optional": True},
                {"label": "Rounds fired", "value": {"path": "context._card_attack_rounds"}},
            ],
        }
    return metadata


def _runtime(
    parameter_values: dict[str, Any],
    specialty_records: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    specialty_branch_ids = sorted(
        {"DEFAULT"}
        | {
            fixture["effect_branch"]
            for record in specialty_records.values()
            for fixture in record["branch_fixture_table"]
        }
    )
    tools = _tool_specs(sorted(specialty_records), specialty_branch_ids)
    runtime_tools = {
        name: {
            "description": value["description"],
            "parameters": value["parameters"],
            "effects": _card_effects(name, value["effects"]),
            "metadata": _tool_metadata(name, value["costs_action"]),
        }
        for name, value in tools.items()
    }
    runtime_tools["td_refresh_turn_actor"] = _turn_actor_helper()
    runtime_tools["td_drop_held_weapon"] = ground_weapons.drop_helper(_param)
    runtime_tools["td_place_dropped_weapon"] = ground_weapons.placement_helper(_param)
    return {
        "sylvia_gm_runtime": {"format": 1, "state_namespace": PACKAGE_ID},
        "model_decision_scope": {
            "decision_owner": {"namespace": PACKAGE_ID, "key": "state.decision_owner"},
            "active_side": {"namespace": PACKAGE_ID, "key": "state.active_side"},
            "eligible_actors": {"namespace": PACKAGE_ID, "key": "state.eligible_actor_ids"},
            "focused_actor": {"namespace": PACKAGE_ID, "key": "state.focused_actor"},
            "actor_entities": {"namespace": PACKAGE_ID, "key": "entities"},
        },
        "tools": runtime_tools,
        "procedures": {},
        "definitions": {
            "parameters": _nested_parameters(parameter_values),
            "stances": {
                stance: {"label": stance.lower(), "height": height,
                         "movement_m_per_ap": parameter_values[f"move.{rate}_m_per_ap"]}
                for stance, height, rate in (
                    ("STANDING", 1.8, "upright"), ("CROUCHED", 1.15, "crouched"),
                    ("PRONE", 0.45, "prone"),
                )
            },
            "authoring_defaults": {
                "campaign_authoring_reference": "references/campaign-authoring.md",
                "character_authoring_reference": "references/character-authoring.md",
                "cover_height_presets": [
                    {"id": "quarter", "label": "Quarter", "height_m": 0.6},
                    {"id": "half", "label": "Half", "height_m": 1.2},
                    {"id": "full", "label": "Full", "height_m": 2.4},
                ],
                "cover_height_guidance": (
                    "Editable starting values for newly authored map geometry. "
                    "Actual geometry height and size, target stance and silhouette, "
                    "and firing angle determine protection. Existing map geometry "
                    "keeps its authored dimensions."
                ),
            },
            "specialty_counterfactuals": specialty_records,

        },
        "narrative": {},
    }


def _tool_sources(name: str) -> list[str]:
    groups = {
        "drop_weapon": ["Equipment/INVENTORY.md", "Equipment/READY.md", "CQC/DISARM.md"],
        "pickup_weapon": ["Equipment/INVENTORY.md", "Equipment/READY.md", "Equipment/QUICK ACCESS.md", "Equipment/STOWED.md", "CQC/DISARM.md", "Combat/GRID.md"],
        "transfer_reserve_weapon": ["Equipment/INVENTORY.md", "Equipment/QUICK ACCESS.md", "Equipment/STOWED.md", "Combat/GRID.md"],
        "switch_weapon": ["Equipment/INVENTORY.md", "Equipment/READY.md", "Equipment/QUICK ACCESS.md", "Equipment/STOWED.md", "Firearms/AIM.md", "Combat/OVERWATCH.md", "Combat/SUPPRESSION.md"],
        "handle_weapon": ["Equipment/INVENTORY.md", "Equipment/READY.md", "Equipment/QUICK ACCESS.md", "Equipment/STOWED.md", "Firearms/AIM.md", "Combat/OVERWATCH.md", "Combat/SUPPRESSION.md"],
        "recover_between_encounters": ["Systems/RES RECOVERY.md", "Systems/DOWNTIME.md", "Combat/RES.md"],
        "resolve_check": ["Character/CHECK.md", "Character/DIFFICULTY.md"],
        "move_actor": ["Combat/GRID.md", "Combat/MOVEMENT.md", "Combat/AP.md"],
        "change_stance": ["Combat/STANCE.md", "Combat/MOVEMENT.md", "Combat/AP.md"],
        "aim": ["Firearms/AIM.md", "Combat/AP.md"],
        "fire_single": ["Combat/ATTACK ROLL.md", "Combat/HIT QUALITY.md", "Combat/COVER.md", "Combat/DESTRUCTIBLE COVER.md", "Firearms/PROJECTILE.md", "Firearms/MATERIAL PENETRATION.md", "Combat/PENETRATION.md", "Combat/RES.md", "Combat/HP.md"],
        "fire_burst": ["Firearms/BURST FIRE.md", "Combat/ATTACK ROLL.md", "Combat/HIT QUALITY.md", "Combat/COVER.md", "Combat/DESTRUCTIBLE COVER.md", "Firearms/PROJECTILE.md", "Firearms/MATERIAL PENETRATION.md", "Combat/PENETRATION.md", "Combat/RES.md", "Combat/HP.md", "Testing/BALANCE PARAMETERS.md"],
        "fire_full_auto": ["Firearms/FULL AUTO.md", "Combat/ATTACK ROLL.md", "Combat/HIT QUALITY.md", "Combat/COVER.md", "Combat/DESTRUCTIBLE COVER.md", "Firearms/PROJECTILE.md", "Firearms/MATERIAL PENETRATION.md", "Combat/PENETRATION.md", "Combat/RES.md", "Combat/HP.md", "Testing/BALANCE PARAMETERS.md"],
        "recover_recoil": ["Firearms/RECOIL.md"],
        "cycle_weapon": ["Firearms/CYCLING.md", "Firearms/CHAMBER.md", "Firearms/MAGAZINE.md", "Testing/REFERENCE EQUIPMENT VALUES.md"],
        "reload_weapon": ["Firearms/RELOAD.md", "Firearms/MAGAZINE.md", "Firearms/CHAMBER.md", "Equipment/INVENTORY.md", "Testing/REFERENCE EQUIPMENT VALUES.md"],
        "prepare_overwatch": ["Combat/PREP ACTION.md", "Combat/OVERWATCH.md"],
        "establish_suppression": ["Combat/SUPPRESSION.md"],
        "inspect_trigger_crossing": ["Combat/TRIGGER ORDERING.md", "Combat/OVERWATCH.md", "Combat/SUPPRESSION.md"],
        "resolve_trigger_crossing": ["Combat/TRIGGER ORDERING.md", "Combat/OVERWATCH.md", "Combat/SUPPRESSION.md", "Firearms/PROJECTILE.md", "Firearms/MATERIAL PENETRATION.md"],
        "resolve_cqc": ["CQC/CQC ACTION.md", "CQC/CQC DEFENSE.md", "CQC/TRIP.md", "CQC/DISARM.md", "CQC/CONTROL.md", "CQC/ESCAPE.md"],
        "resolve_cqc_displacement": ["CQC/CQC ACTION.md", "CQC/CQC DEFENSE.md", "CQC/SHOVE.md", "CQC/THROW.md", "Combat/GRID.md"],
        "treat_casualty": ["Combat/STABILIZE.md", "Combat/RESUSCITATION.md", "Combat/LIFE-THREATENING CONDITION.md", "Combat/INJURY.md", "Specialties/FIELD MEDIC.md"],
        "resolve_blast_snapshot": ["Explosives/BLAST GEOMETRY.md", "Explosives/GRENADE.md"],
        "fpv_fragmentation_strike": ["Drones/FPV DRONE.md", "Drones/MANUAL CONTROL.md", "Explosives/GRENADE PLACEMENT.md", "Explosives/BLAST GEOMETRY.md", "Testing/REFERENCE EQUIPMENT VALUES.md"],
        "autodrone_fire": ["Drones/AUTONOMOUS DRONE.md", "Drones/STANDING ORDER.md", "Drones/DRONE OVERWATCH.md", "Firearms/PROJECTILE.md", "Combat/COVER.md", "Testing/REFERENCE EQUIPMENT VALUES.md"],
        "intrude_network": ["Hacking/INTRUSION.md", "Hacking/NETWORK.md", "Hacking/NODE.md", "Hacking/LINK.md"],
        "network_connection": ["Hacking/LOCAL ACCESS.md", "Hacking/INTRUSION.md", "Hacking/NODE.md"],
        "respond_network_defense": ["Hacking/DETECTION.md", "Hacking/MONITORING.md", "Hacking/INTRUSION.md"],
        "preserve_network_route": ["Specialties/NETWORKER.md", "Hacking/LINK.md", "Hacking/INTRUSION.md"],
        "pivot_network": ["Hacking/PIVOT.md", "Hacking/LINK.md", "Hacking/INTRUSION.md"],
        "operate_remote_function": ["Hacking/OPERATE.md", "Hacking/REMOTE FUNCTION.md", "Hacking/PRIVILEGE.md"],
        "exercise_specialty_counterfactual": ["Character/SPECIALTY.md", "Specialties/*.md", "Testing/SPECIALTY TEST APPROVAL.md", "Testing/BALANCE PARAMETERS.md"],
        "end_allies_turn": ["Combat/TEAM TURN.md", "Combat/AP.md"],
        "end_hostiles_turn": ["Combat/TEAM TURN.md", "Combat/AP.md"],
    }
    return groups[name]


def _character_schema(
    specialty_node_ids: list[str], catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    skill_names = (
        "athletics", "cqc", "drone_operations", "engineering", "grenades", "influence",
        "investigation", "local_hacking", "marksmanship", "medicine", "networking", "recon",
        "social_read", "stealth", "tactics", "vehicles",
    )
    aptitude_names = ("acuity", "force", "nerve", "precision", "presence", "reason")

    def stats(names: tuple[str, ...]) -> dict[str, Any]:
        return {
            "type": "object",
            "required": list(names),
            "properties": {name: {"type": "integer", "minimum": 0} for name in names},
            "additionalProperties": False,
        }

    return {
        "$id": "pneuma-io.trigger-discipline.character.v0-1",
        "type": "object",
        "required": ["id", "name", "side", "class_id", "level", "skills", "aptitudes", "specialties"],
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "name": {"type": "string", "minLength": 1},
            "side": {"type": "string", "enum": ["allies", "hostiles", "neutral"]},
            "class_id": {"type": "string", "enum": ["BULWARK", "RIFLEMAN", "BREACHER", "GHOST", "SHARPSHOOTER", "TECH", "HACKER", "CORPSMAN", "INFLUENCER"]},
            "level": {"type": "integer", "minimum": 1, "maximum": 20},
            "skills": stats(skill_names),
            "aptitudes": stats(aptitude_names),
            "specialties": {
                "type": "array",
                "items": {"type": "string", "enum": specialty_node_ids},
                "uniqueItems": True,
                "maxItems": 20,
            },
        },
        "additionalProperties": False,
        **({"allOf": eligibility_constraints(catalog)} if catalog is not None else {}),
    }


def _character_declaration(schema: dict[str, Any]) -> dict[str, Any]:
    """Project complete initial state without inventing unresolved starting build choices."""

    state_schema = json.loads(
        Path(__file__).with_name("character_state_schema.json").read_text(encoding="utf-8")
    )
    state_schema["properties"]["weapon"]["properties"]["owner_id"] = {
        "type": "string", "minLength": 1,
        "description": "Explicitly recorded owner, separate from the character currently carrying the firearm. Omit if ownership is unknown; a handoff does not change it.",
    }
    reserve_weapon = deepcopy(state_schema["properties"]["weapon"])
    state_schema["properties"]["weapon"]["type"] = ["object", "null"]
    reserve_weapon["properties"]["location"]["enum"] = ["QUICK_ACCESS", "STOWED"]
    state_schema["properties"]["reserve_weapons"] = {
        "type": "array", "items": reserve_weapon, "maxItems": 16, "uniqueItems": True,
        "description": "Other physically carried firearms, with distinct physical IDs and complete state. Switching moves the existing objects; no starting allowance is implied.",
    }
    copied_fields = ("id", "name", "side", "class_id", "level", "skills", "aptitudes", "specialties")
    for field in copied_fields:
        state_schema["properties"][field] = deepcopy(schema["properties"][field])
    if "allOf" in schema:
        state_schema["allOf"] = deepcopy(schema["allOf"])
    return {
        "kind": "trigger-discipline.character",
        "schema_id": schema["$id"],
        "schema": schema,
        "entity_state_schema": state_schema,
        "initial_state_calculations": [
            *[{"set": {"field": field, "value": f"participant.{field}"}} for field in copied_fields],
            {"set": {"field": "downed", "value": "state.hp <= 0"}},
        ],
    }


def _conformance_character() -> dict[str, Any]:
    return {
        "schema_id": "pneuma-io.trigger-discipline.character.v0-1",
        "artifact": {
            "id": "hex",
            "name": "Hex",
            "side": "allies",
            "class_id": "HACKER",
            "level": 10,
            "skills": {"athletics": 0, "cqc": 2, "drone_operations": 6, "engineering": 0, "grenades": 0, "influence": 0, "investigation": 0, "local_hacking": 4, "marksmanship": 4, "medicine": 0, "networking": 6, "recon": 0, "social_read": 0, "stealth": 0, "tactics": 0, "vehicles": 0},
            "aptitudes": {"reason": 2, "precision": 1, "force": 0, "acuity": 2, "nerve": 1, "presence": 0},
            "specialties": [
                "specialty.networker.entry.map_the_links",
                "specialty.networker.tier_1.pivot",
                "specialty.networker.tier_1.quiet_enumeration",
                "specialty.networker.tier_2.foothold",
                "specialty.networker.tier_2.segment_map",
            ],
        },
    }


def _scene() -> dict[str, Any]:
    public = lambda label, kind, **extra: {"player": {"label": label, "kind": kind, **extra}}
    geometry = [
        {"id": "partial-cover", "map_id": "td-conformance-floor", "occupies_cells": True, "volume": {"kind": "box", "min": [6, 1, 0], "max": [6.5, 4, 1]}, "active": True, "movement": "blocking", "visibility": "opaque", "metadata": {**public("Destructible cover", "cover", material_label="Material resistance 3"), "material_resistance": 3, "cover_hp": 12}, "destroyed": {"active": False, "movement": "passable", "visibility": "clear"}},
        {"id": "full-wall", "map_id": "td-conformance-floor", "occupies_cells": False, "volume": {"kind": "box", "min": [6, 5, 0], "max": [6.5, 8, 3]}, "active": True, "movement": "blocking", "visibility": "opaque", "metadata": public("Full wall", "wall", height_label="Full"), "destroyed": {"active": True, "movement": "blocking", "visibility": "opaque"}},
        {"id": "security-door", "map_id": "td-conformance-floor", "occupies_cells": False, "volume": {"kind": "box", "min": [10, 4, 0], "max": [10.25, 5.5, 2.4]}, "active": True, "movement": "blocking", "visibility": "opaque", "metadata": {**public("Security door", "door", state="closed", material_label="Material resistance 2"), "material_resistance": 2, "cover_hp": 8}, "destroyed": {"active": False, "movement": "passable", "visibility": "clear"}},
    ]
    entities = [
        {"id": "vesper", "map_id": "td-conformance-floor", "position": [2.5, 2.5, 0], "footprint": {"kind": "box", "half_extents": [0.4, 0.4]}, "height": 1.8, "facing": [1, 0], "active": True, "movement": "blocking", "visibility": "opaque", "metadata": public("Vesper", "unit", stance="standing", side="allies")},
        {"id": "hex", "map_id": "td-conformance-floor", "position": [2.5, 3.5, 0], "footprint": {"kind": "box", "half_extents": [0.4, 0.4]}, "height": 1.8, "facing": [1, 0], "active": True, "movement": "blocking", "visibility": "opaque", "metadata": public("Hex", "unit", stance="standing", side="allies")},
        {"id": "hostile_alpha", "map_id": "td-conformance-floor", "position": [9.5, 2.5, 0], "footprint": {"kind": "box", "half_extents": [0.4, 0.4]}, "height": 1.8, "facing": [-1, 0], "active": True, "movement": "blocking", "visibility": "opaque", "metadata": public("Hostile Alpha", "unit", stance="standing", side="hostiles")},
        {"id": "hidden_hostile", "map_id": "td-conformance-floor", "position": [11.5, 7, 0], "footprint": {"kind": "circle", "radius": 0.4}, "height": 1.8, "facing": [-1, 0], "active": True, "movement": "blocking", "visibility": "opaque", "metadata": {"gm_private": "not player known"}},
        {"id": "terminal-device", "map_id": "td-conformance-floor", "position": [3.5, 5.5, 0], "footprint": {"kind": "box", "half_extents": [0.35, 0.35]}, "height": 1.3, "facing": [0, -1], "active": True, "movement": "passable", "visibility": "opaque", "metadata": public("Lobby access terminal", "device")},
        {"id": "door-controller-device", "map_id": "td-conformance-floor", "position": [9.5, 5, 0], "footprint": {"kind": "box", "half_extents": [0.25, 0.25]}, "height": 1.2, "facing": [-1, 0], "active": True, "movement": "passable", "visibility": "opaque", "metadata": public("Door controller", "device")},
        {"id": "fpv_alpha", "map_id": "td-conformance-floor", "position": [29.5, 30.5, 0], "footprint": {"kind": "circle", "radius": 0.2}, "height": 0.4, "facing": [1, 0], "active": True, "movement": "blocking", "visibility": "opaque", "metadata": public("FPV Alpha", "drone", side="allies", state="active")},
        {"id": "autodrone_alpha", "map_id": "td-conformance-floor", "position": [27.5, 30.5, 0], "footprint": {"kind": "circle", "radius": 0.35}, "height": 0.7, "facing": [1, 0], "active": True, "movement": "blocking", "visibility": "opaque", "metadata": public("Autodrone Alpha", "drone", side="allies", state="active")},
        {"id": "drone_target", "map_id": "td-conformance-floor", "position": [31.5, 30.5, 0], "footprint": {"kind": "box", "half_extents": [0.4, 0.4]}, "height": 1.8, "facing": [-1, 0], "active": True, "movement": "blocking", "visibility": "opaque", "metadata": public("Drone qualification target", "unit", stance="standing", side="hostiles")},
    ]
    zones = [
        {"id": "overwatch-lane", "map_id": "td-conformance-floor", "shape": {"kind": "box", "min": [5, 0, 0], "max": [8, 4, 2.5]}, "metadata": public("Overwatch lane", "overwatch")},
        {"id": "suppression-lane", "map_id": "td-conformance-floor", "shape": {"kind": "box", "min": [5, 0, 0], "max": [8, 4, 2.5]}, "metadata": public("Suppression lane", "suppression")},
        {"id": "objective-zone", "map_id": "td-conformance-floor", "shape": {"kind": "box", "min": [10.5, 3.5, 0], "max": [12, 6, 2.5]}, "metadata": public("Secure relay", "objective")},
    ]
    return {"format": 1, "map": {"id": "td-conformance-floor", "revision": 1, "bounds": {"min": [0, 0, 0], "max": [60, 60, 3]}, "grid": {"cell_size": 1, "unit": "m", "origin": [0, 0]}, "layers": [{"id": "floor", "min_elevation": 0, "max_elevation": 3}]}, "geometry": geometry, "entities": entities, "zones": zones, "terrain": []}


def _network() -> dict[str, Any]:
    return {
        "graph_id": "td-conformance-building",
        "revision": 1,
        "groups": {"office": {"label": "Office", "visible": True}, "security": {"label": "Security", "visible": False}},
        "nodes": {
            "lobby_terminal": {"label": "Lobby terminal", "kind": "access_terminal", "group_id": "office", "status": "online", "access": "local", "control": "hostile", "detection": "unknown", "functions": {}, "physical_binding": {"surface_id": "operations.map", "entity_id": "terminal-device"}, "visible": True},
            "camera_gateway": {"label": "Camera gateway", "kind": "gateway", "group_id": "office", "status": "online", "access": "network", "control": "hostile", "detection": "unknown", "functions": {}, "visible": False},
            "security_controller": {"label": "Security controller", "kind": "controller", "group_id": "security", "status": "online", "access": "network", "control": "hostile", "detection": "unknown", "functions": {"open_security_door": {"label": "Open security door", "state": "available", "enabled": True, "visible": True}}, "physical_binding": {"surface_id": "operations.map", "entity_id": "door-controller-device"}, "visible": False},
        },
        "links": {
            "terminal_gateway": {"source": "lobby_terminal", "target": "camera_gateway", "label": "Office LAN", "kind": "ethernet", "status": "online", "direction": "bidirectional", "visible": False},
            "gateway_security": {"source": "camera_gateway", "target": "security_controller", "label": "Security uplink", "kind": "ethernet", "status": "online", "direction": "bidirectional", "visible": False},
        },
        "selection": {},
    }


def _conformance_network_state(values: dict[str, Any]) -> dict[str, Any]:
    """Author the synthetic network using the same data interface as another scene."""
    graph = _network()
    rules = {}
    for node in graph["nodes"]:
        hard = node == "security_controller"
        prefix = "hacking.hard_intrusion" if hard else "hacking.standard_intrusion"
        action = {"ap": values[f"{prefix}_ap"], "difficulty": values[f"{prefix}_difficulty"],
                  "check_required": True, "privilege": 1, "reveal_links": []}
        visible = [identity for identity, link in graph["links"].items() if link["source"] == node]
        rules[node] = {"visible_links": visible, "actions": {
            "GET_IN": deepcopy(action), "DISCOVER": {**action, "reveal_links": visible},
            "ESCALATE": {**action, "privilege": 2},
            "FOOTHOLD": {**action, "persistence": {"method": "test_implant", "generation": 1}},
        }}
        rules[node]["reentry"] = {}
    return {
        "network": graph, "network_intrusions": rules, "network_sessions": {}, "network_footholds": {},
        "network_motion": {"hex": 0},
        "network_monitors": [], "network_defenders": {},
        "network_credentials": {},
        "network_interfaces": {"lobby_terminal": {
            "kind": "local_stationary", "active": True, "generation": 1,
            "map_id": "td-conformance-floor", "position_cell": [2, 3], "ap": 0,
        }},
        "network_entries": {"hex": {"nodes": {"lobby_terminal": {
            "active": True, "generation": 1, "interface_generation": 1,
            "node_generation": 1, "motion_generation": 0,
        }}}},
        "network_topology": {
            "nodes": {node: {"online": True, "generation": 1} for node in graph["nodes"]},
            "links": {identity: {"online": True, "generation": 1,
                                 "barrier": deepcopy(rules[link["target"]]["actions"]["GET_IN"])}
                      for identity, link in graph["links"].items()},
        },
        "network_functions": {"security_controller": {"functions": {"open_security_door": {
            "kind": "geometry", "map_id": "td-conformance-floor", "geometry_id": "security-door",
            "ap": values["hacking.authorized_function_ap"], "privilege": 2,
            "patch": {"active": False, "movement": "passable", "visibility": "clear"},
        }}}},
    }


def _entity(
    identity: str,
    *,
    side: str,
    class_id: str,
    position: list[int],
    weapon: str,
    parameter_values: dict[str, Any],
    networking: int = 0,
    needs_cycle: bool = False,
) -> dict[str, Any]:
    prefix = f"equipment.{weapon}"
    capacity = parameter_values[f"{prefix}.capacity"]
    mechanisms = {
        "pistol": "SELF_LOADING",
        "smg": "SELF_LOADING",
        "service_rifle": "SELF_LOADING",
        "lmg": "SELF_LOADING",
        "shotgun": "MANUAL_ACTION",
        "precision_rifle": "SELF_LOADING",
        "manual_precision_rifle": "MANUAL_ACTION",
    }
    fire_modes = {
        "pistol": ["SEMI_AUTO"],
        "smg": ["SEMI_AUTO", "FULL_AUTO"],
        "service_rifle": ["SEMI_AUTO", "BURST", "FULL_AUTO"],
        "lmg": ["FULL_AUTO"],
        "shotgun": ["SINGLE"],
        "precision_rifle": ["SEMI_AUTO"],
        "manual_precision_rifle": ["SINGLE"],
    }
    # Synthetic conformance cadence only; Campaign Settings own named weapon values.
    cyclic_rates_rpm = {"smg": 900, "service_rifle": 750, "lmg": 600}
    selected_weapon = {
        "id": weapon,
        "damage": parameter_values[f"{prefix}.damage"],
        "target_pen": parameter_values[f"{prefix}.target_pen"],
        "material_pen": parameter_values[f"{prefix}.material_pen"],
        "recoil": parameter_values[f"{prefix}.recoil"],
        "range_breakpoints_m": deepcopy(parameter_values[f"{prefix}.range_breakpoints_m"]),
        "range_modifiers": deepcopy(parameter_values[f"{prefix}.range_modifiers"]),
        "current_recoil": 0,
        "cyclic_rate_rpm": cyclic_rates_rpm.get(weapon),
        "magazine": {"id": f"{identity}-{weapon}-magazine-0", "rounds": capacity},
        "spare_magazines": [
            {"id": f"{identity}-{weapon}-magazine-1", "rounds": capacity},
            {"id": f"{identity}-{weapon}-magazine-2", "rounds": capacity},
        ],
        "retained_magazines": [],
        "chamber": "SPENT" if needs_cycle else "LIVE",
        "needs_cycle": needs_cycle,
        "mechanism": mechanisms[weapon],
        "feed": "TUBE" if weapon == "shotgun" else "DETACHABLE_MAGAZINE",
        "reload_ap": parameter_values[f"{prefix}.reload_ap"],
        "cycle_ap": parameter_values.get(f"{prefix}.cycle_ap", 0),
        "location": "READY",
        "controlled_by": None,
        "fire_modes": fire_modes[weapon],
    }
    return {
        "id": identity, "name": identity.replace("_", " ").title(), "side": side, "class_id": class_id, "level": 10, "active": True, "ap": 6, "max_ap": 6, "hp": 11, "res": 10, "downed": False, "stance": "STANDING", "position_cell": position, "center": [position[0] + 0.5, position[1] + 0.5, 0.9], "movement_m_per_ap": parameter_values["move.upright_m_per_ap"], "muzzle": [position[0] + (0.91 if side == "allies" else -0.09), position[1] + 0.5, 1.4], "skills": {"athletics": 0, "cqc": 3, "drone_operations": 6 if identity == "hex" else 0, "engineering": 0, "grenades": 0, "influence": 0, "investigation": 0, "local_hacking": 4 if identity == "hex" else 0, "marksmanship": 6 if identity == "vesper" else 4, "medicine": 2, "networking": networking, "recon": 0, "social_read": 0, "stealth": 0, "tactics": 0, "vehicles": 0}, "aptitudes": {"acuity": 0, "force": 1, "nerve": 0, "precision": 2, "presence": 0, "reason": 2 if identity == "hex" else 1}, "specialties": ["specialty.networker.entry.map_the_links", "specialty.networker.tier_1.pivot", "specialty.networker.tier_1.quiet_enumeration", "specialty.networker.tier_2.foothold", "specialty.networker.tier_2.segment_map"] if identity == "hex" else [], "weapon": selected_weapon, "aim": {"target": None, "point": None, "ap": 0}, "recoil_recovery_per_ap": 2 if class_id == "RIFLEMAN" else 1, "intrusion_ap_discount": 1 if class_id == "HACKER" else 0, "local_access": identity == "hex", "medical_kit_capable": False, "resuscitation_kit_capable": False, "medicine_bonus": 0, "life_threatening": {"state": "NONE", "within_reversible_window": False}, "control": None,
    }


def _drone_entity(
    identity: str,
    *,
    kind: str,
    position: list[int],
    parameter_values: dict[str, Any],
    target_id: str | None = None,
) -> dict[str, Any]:
    center = [position[0] + 0.5, position[1] + 0.5, 0.5]
    base: dict[str, Any] = {
        "id": identity,
        "name": identity.replace("_", " ").title(),
        "kind": kind,
        "side": "allies",
        "active": True,
        "ap": parameter_values["equipment.autodrone.actions_per_turn"] if kind == "AUTONOMOUS_DRONE" else 0,
        "max_ap": parameter_values["equipment.autodrone.actions_per_turn"] if kind == "AUTONOMOUS_DRONE" else 0,
        "hp": parameter_values["equipment.autodrone.durability"] if kind == "AUTONOMOUS_DRONE" else parameter_values["equipment.fpv_drone.durability"],
        "res": 0,
        "downed": False,
        "position_cell": position,
        "position_point": center,
        "center": center,
        "muzzle": [center[0] + 0.45, center[1], 0.65],
        "controlled_by": "hex" if kind == "FPV_DRONE" else None,
        "payload": "FPV_FRAGMENTATION" if kind == "FPV_DRONE" else None,
    }
    if kind == "AUTONOMOUS_DRONE":
        base.update(
            {
                "standing_order": {
                    "active": target_id is not None,
                    "authorized_fire": target_id is not None,
                    "target": target_id,
                    "collateral_limit": "NO_UNCONFIRMED_TARGETS",
                },
                "sensor_confirmed_targets": [target_id] if target_id else [],
                "weapon": {
                    "module": "AUTONOMOUS_LIGHT_WEAPON",
                    "damage": parameter_values["equipment.autodrone_light_weapon.damage"],
                    "target_pen": parameter_values["equipment.autodrone_light_weapon.target_pen"],
                    "material_pen": parameter_values["equipment.autodrone_light_weapon.material_pen"],
                    "recoil": parameter_values["equipment.autodrone_light_weapon.recoil"],
                    "current_recoil": 0,
                    "magazine": {
                        "id": f"{identity}-light-weapon-magazine-0",
                        "rounds": parameter_values["equipment.autodrone_light_weapon.capacity"],
                    },
                    "fire_modes": ["SEMI_AUTO"],
                    "location": "MOUNTED",
                },
            }
        )
    return base


def _drone_target_entity(parameter_values: dict[str, Any]) -> dict[str, Any]:
    target = _entity(
        "drone_target",
        side="hostiles",
        class_id="BULWARK",
        position=[31, 30],
        weapon="pistol",
        parameter_values=parameter_values,
    )
    target.update(
        {
            "hp": 50,
            "res": 20,
            "center": [31.5, 30.5, 0.9],
            "muzzle": [31.05, 30.5, 1.4],
        }
    )
    return target


def _conformance_objectives(
    hostile_status: str = "active", door_status: str = "active"
) -> list[dict[str, Any]]:
    return [
        {
            "id": "neutralize-hostile-alpha",
            "label": "Neutralize Hostile Alpha",
            "status": hostile_status,
            "position": [9.5, 2.5, 0],
            "visible": True,
        },
        {
            "id": "open-security-door",
            "label": "Open the security door through the building network",
            "status": door_status,
            "position": [10.0, 4.75, 0],
            "visible": True,
        },
    ]


def _scenario(
    source_commit: str,
    parameter_hash: str,
    parameter_values: dict[str, Any],
) -> dict[str, Any]:
    return {
        "format": 1,
        "id": "TD-SYLVIA-CONFORMANCE-01",
        "version": "0.1.0",
        "validation_content": True,
        "source_commit": source_commit,
        "parameter_set_sha256": parameter_hash,
        "initial_state": {
            f"{PACKAGE_ID}.state": {"decision_owner": "player", "active_side": "allies", "eligible_actor_ids": ["vesper", "hex", "autodrone_alpha"], "turn_roster": ["vesper", "hex", "autodrone_alpha", "hostile_alpha"], "focused_actor": None, "spatial_scene": _scene(), **_conformance_network_state(parameter_values), "objectives": _conformance_objectives(), "mission": {"status": "ACTIVE", "hostile_neutralized": False}, "selection": {}, "preview": {}, "prepared": {"overwatch": {"active": False, "owner": None, "zone_id": None, "shots": 0}, "suppression": {"active": False, "owner": None, "zone_id": None, "capacity": 0}}, "physical": {"security_door_open": False}, "ground_objects": {}, "ground_object_sequence": 0, "last_check": {}, "last_move": {}, "last_attack": {}, "last_trigger": {}, "last_cqc": {}, "last_reload": {}, "last_medical": {}, "last_blast": {}, "last_drone_attack": {}, "last_specialty_test": {}, "last_network": {}},
            f"{PACKAGE_ID}.entities": {
                "vesper": _entity("vesper", side="allies", class_id="RIFLEMAN", position=[2, 2], weapon="service_rifle", parameter_values=parameter_values),
                "hex": _entity("hex", side="allies", class_id="HACKER", position=[2, 3], weapon="service_rifle", parameter_values=parameter_values, networking=6),
                "hostile_alpha": _entity("hostile_alpha", side="hostiles", class_id="RIFLEMAN", position=[9, 2], weapon="manual_precision_rifle", parameter_values=parameter_values, needs_cycle=True),
                "fpv_alpha": _drone_entity("fpv_alpha", kind="FPV_DRONE", position=[29, 30], parameter_values=parameter_values),
                "autodrone_alpha": _drone_entity("autodrone_alpha", kind="AUTONOMOUS_DRONE", position=[27, 30], parameter_values=parameter_values, target_id="drone_target"),
                "drone_target": _drone_target_entity(parameter_values),
            },
        },
        "journey": ["install and activate exact package", "render operations.map and operations.network", "execute structural and parameterized specialty counterfactual fixtures", "Vesper acts, Hex hacks, Vesper acts again using independent AP", "inspect an authored trigger-zone crossing", "resolve exposed and partial-cover shots", "resolve adjacent TRIP, DISARM, CONTROL, and ESCAPE state transitions", "resolve one immutable blast snapshot", "resolve one consumed FPV fragmentation strike and one sensor-bound autonomous firearm shot", "GET_IN, PIVOT, ESCALATE, and operate open_security_door", "save, reopen, and idempotently replay selected actions"],
        "known_host_dependencies": [],
    }


def _coverage(
    root: Path, parameters: Any, source_commit: str, *, balance: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Describe package support without rerunning unrelated team-objective benchmarks."""
    if balance is None:
        balance = specialty_coverage(parameters, root, include_objective_scenarios=False)
    nodes: list[dict[str, Any]] = []
    authored_networker = {
        "specialty.networker.entry.map_the_links",
        "specialty.networker.tier_1.pivot",
        "specialty.networker.tier_2.foothold",
        "specialty.networker.tier_3.route_persistence",
    }
    for specialty in balance["specialties"].values():
        for item in specialty["nodes"]:
            node = item["node"]
            package_status = "authored_runtime_hook" if node["node_id"] in authored_networker else "test_subject_implemented"
            if item["state"] == "MISSING_DEPENDENCY":
                package_status = "unresolved_design"
            elif item["state"] == "STRUCTURAL_FINDING":
                package_status = "structural_finding"
            elif item["findings"]:
                package_status = "test_subject_partial"
            nodes.append(
                {
                    "id": node["node_id"],
                    "specialty": node["specialty"],
                    "tier": node["tier"],
                    "name": node["name"],
                    "source": {"path": node["source_path"], "sha256": node["source_sha256"]},
                    "balance_suite_status": item["state"],
                    "package_status": package_status,
                    "mechanic_id": item["mechanic_id"],
                    "parameter_ids": item["parameter_ids"],
                    "unsupported_dependencies": item["unsupported_dependencies"],
                    "findings": item["findings"],
                }
            )
    mechanics = [
        {"id": "checks", "source": ["Character/CHECK.md", "Character/DIFFICULTY.md"], "status": "implemented", "conformance": "resolve_check"},
        {"id": "ap-and-team-turn", "source": ["Combat/AP.md", "Combat/TEAM TURN.md"], "status": "implemented", "conformance": "end_allies_turn/end_hostiles_turn and shared actor AP"},
        {"id": "spatial-facts", "source": ["Combat/GRID.md", "Combat/LINE OF SIGHT.md", "Combat/COVER.md", "Firearms/PROJECTILE.md"], "status": "implemented", "conformance": "exposure/ray/crossed_zones/area/snapshot"},
        {"id": "entity-aware-path", "source": ["Combat/GRID.md", "Combat/MOVEMENT.md"], "status": "implemented", "conformance": "move_actor and resolve_cqc_displacement retain entity blocking while excluding exactly the moving entity"},
        {"id": "dynamic-spatial-state", "source": ["Combat/MOVEMENT.md", "Combat/DESTRUCTIBLE COVER.md", "Combat/BREACHING.md", "CQC/SHOVE.md", "CQC/THROW.md"], "status": "implemented", "conformance": "exact revision-guarded scene mutation for movement, forced movement, cover damage/destruction, and the authored security door"},
        {"id": "single-fire-and-damage", "source": _tool_sources("fire_single"), "status": "implemented", "conformance": "fire_single"},
        {"id": "ammunition-chamber-cycle-reload", "source": ["Firearms/AMMUNITION.md", "Firearms/MAGAZINE.md", "Firearms/CHAMBER.md", "Firearms/CYCLING.md", "Firearms/RELOAD.md", "Equipment/INVENTORY.md"], "status": "partial", "conformance": "fire_single consumes chamber/feed state; cycle_weapon executes declared manual-action cycle AP; reload_weapon preserves detachable magazines on retain/drop; handle_weapon commits GM-adjudicated ready/stow handling of the carried firearm without a default cost; switch_weapon and transfer_reserve_weapon move actual carried firearm objects with ammunition/ownership preserved; drop_weapon and pickup_weapon move full ground objects with explicit costs and physical reach, including successful DISARM; equipment creation, ownership changes and tube-fed shell loading remain unsupported by these actions"},
        {"id": "multi-round-sequential-geometry", "source": sorted(set(_tool_sources("fire_burst") + _tool_sources("fire_full_auto"))), "status": "implemented", "conformance": "fire_burst/fire_full_auto use one ATTACK ROLL, the v0.1 admitted connection/recoil parameters, ordered projectile rays, and a fresh live snapshot after every geometry mutation"},
        {"id": "overwatch-suppression-triggering", "source": ["Combat/OVERWATCH.md", "Combat/SUPPRESSION.md", "Combat/TRIGGER ORDERING.md", "Firearms/PROJECTILE.md", "Firearms/MATERIAL PENETRATION.md"], "status": "implemented", "conformance": "resolve_trigger_crossing pauses exact movement at the first prepared-zone crossing, resolves same-moment OVERWATCH/SUPPRESSION in the declared controller order through ordinary attack, cover, projectile, material, ammunition, and capacity rules with fresh geometry between sequential shots, then resumes or interrupts the exact scene entity"},
        {"id": "cqc", "source": sorted(set(_tool_sources("resolve_cqc") + _tool_sources("resolve_cqc_displacement"))), "status": "partial", "conformance": "resolve_cqc implements reach-gated TRIP, DISARM, BODY CONTROL, WEAPON CONTROL, and ESCAPE; resolve_cqc_displacement implements path-valid SHOVE and THROW with exact scene/entity synchronization; situational leverage and environmental hazards remain GM judgment"},
        {"id": "medical", "source": ["Combat/STABILIZE.md", "Combat/RESUSCITATION.md"], "status": "implemented", "conformance": "treat_casualty"},
        {"id": "blast-snapshot", "source": ["Explosives/BLAST GEOMETRY.md"], "status": "implemented", "conformance": "resolve_blast_snapshot"},
        {"id": "fpv-fragmentation-strike", "source": _tool_sources("fpv_fragmentation_strike"), "status": "implemented", "conformance": "fpv_fragmentation_strike resolves DRONE OPERATIONS + PRECISION placement, geometry-constrained scatter, one frozen blast snapshot, ordinary fragmentation routing, and exact drone consumption"},
        {"id": "autonomous-light-weapon", "source": _tool_sources("autodrone_fire"), "status": "implemented", "conformance": "autodrone_fire requires a sensor-confirmed standing-order target and uses ordinary ray, COVER, damage, ammunition, execution-AP, and RECOIL state"},
        {"id": "network-topology-and-intrusion", "source": ["Hacking/NETWORK.md", "Hacking/NODE.md", "Hacking/LINK.md", "Hacking/INTRUSION.md", "Hacking/MONITORING.md", "Hacking/DETECTION.md", "Hacking/CREDENTIAL.md", "Hacking/FOOTHOLD.md"], "status": "partial", "conformance": "Authored stationary or actor-bound remote-service entry, intrusion, credential/foothold re-entry, selected-link pivot and mapped operations use actor-owned sessions with bounded routes. Separate authored monitoring supports session termination, link isolation, node shutdown, credential revocation and foothold removal. Single-session ROUTE PERSISTENCE and owner-checked friendly route previews are supported. Plan admission validates graph presentation and map references. Moving local interfaces, whole-chain rerouting, private-state authoring validation and further specialty integration remain TDH-11 work. No default security tuning is selected."},
        {"id": "mixed-objective", "source": ["Testing/SYLVIA RULESET IMPLEMENTATION.md"], "status": "validation_only", "conformance": "Historical fixed-fixture objective helper is not a campaign mission resolver. Ordinary fire no longer writes mission progress; the authored Plan and GM own it."},
        {"id": "specialties", "source": ["Character/SPECIALTY.md", "Specialties/*.md", "Testing/SPECIALTY TEST APPROVAL.md"], "status": "structural_failure", "conformance": "all 364 approved tertiary nodes are accounted for: mechanically determinate nodes compile as deterministic same-character package counterfactuals with source and parameter provenance, while unimplementable magnitude/procedure gaps remain excluded from the runtime enum and are reported for human design; MAP THE LINKS reveals authored visible adjacent links; pivot/foothold numerical discounts and further network specialty integration remain unqualified. Authored network action costs do not inherit benchmark discounts"},
    ]
    counts: dict[str, int] = {}
    for node in nodes:
        counts[node["package_status"]] = counts.get(node["package_status"], 0) + 1
    return {
        "format": 1,
        "source_commit": source_commit,
        "mechanics": mechanics,
        "specialty_summary": {
            "specialties": 26,
            "nodes": len(nodes),
            "executable_counterfactuals": len(balance["counterfactuals"]),
            "unexecutable_nodes": len(nodes) - len(balance["counterfactuals"]),
            "rejected_parameter_bindings": len(
                balance["rejected_parameter_bindings"]
            ),
            "deferred_parameter_bindings": len(
                balance["deferred_parameter_bindings"]
            ),
            "package_status_counts": counts,
            "balance_suite_status_counts": balance["coverage_counts"],
        },
        "specialty_nodes": nodes,
        "rejected_parameter_bindings": balance["rejected_parameter_bindings"],
        "deferred_parameter_bindings": balance["deferred_parameter_bindings"],
        "findings": [
            *[
                {**finding, "status": "open", "scope": "specialty"}
                for finding in balance["findings"]
            ],
            {
                "code": "SYLVIA_RAY_GRID_INTERSECTION_NONTERMINATION",
                "status": "historical_resolved_in_host_v2",
                "scope": "host",
                "sylvia_commit": SYLVIA_BASELINE,
                "summary": "sylvia.spatial.supercover.v1 does not terminate when a ray destination lies exactly on an integer grid intersection for some slopes",
                "package_mitigation": "reaction shots preserve the exact trigger-crossing entity position and select an aim point one micrometre inside the same occupied body volume",
                "resolution": "Current Sylvia source uses sylvia.spatial.supercover.v2 with destination-axis termination and focused spatial regressions. Retained as historical provenance, not an open host task or installed-candidate claim.",
            },
        ],
    }


def _manifest(
    source_hash: str,
    specialty_node_ids: list[str],
    specialty_branch_ids: list[str],
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    specs = _tool_specs(specialty_node_ids, specialty_branch_ids)
    capabilities = sorted({value["capability"] for value in specs.values()} | {"trigger-discipline.presentation"})
    tools = []
    for name, value in specs.items():
        predicates = []
        if "predicate" in value:
            key, expected = value["predicate"]
            predicates.append({"namespace": PACKAGE_ID, "key": key, "equals": expected})
        tools.append({"name": name, "capability": value["capability"], "input_schema": _schema(value["parameters"]), "state_predicates": predicates, "turn_statuses": ["open"], "writers": ["dm"]})
        if name == "handle_weapon":
            tools[-1]["input_schema"]["x-sylvia-gm-inventory"] = {
                "format": "mechanics", "state": {"namespace": PACKAGE_ID, "key": "entities"},
                "surface_id": "operations.loadout",
                "tools": ["handle_weapon", "switch_weapon", "transfer_reserve_weapon", "drop_weapon", "pickup_weapon", "reload_weapon", "cycle_weapon"],
            }
    stance_actions = [
        {"id": f"stance-{stance.lower()}", "label": label, "intent": "change_stance",
         "params": {"stance": stance}, "bindings": {"actor": "actor"}}
        for stance, label in (("STANDING", "Stand"), ("CROUCHED", "Crouch"), ("PRONE", "Go prone"))
    ]
    tactical_actions = [
        *deepcopy(stance_actions),
        {"id": "move", "label": "Move", "intent": "move_actor", "params": {}, "bindings": {"actor": "actor", "destination": "destination"}},
        {"id": "aim", "label": "Aim", "intent": "aim", "params": {"ap": 1}, "bindings": {"attacker": "actor", "target": "target", "aim_point": "aim_point"}},
        {"id": "fire", "label": "Fire", "intent": "fire_single", "params": {"critical_effect": "DEVASTATING_HIT"}, "bindings": {"attacker": "actor", "target": "target", "aim_point": "aim_point"}},
        {"id": "burst", "label": "Burst", "intent": "fire_burst", "params": {"critical_effect": "DEVASTATING_HIT"}, "bindings": {"attacker": "actor", "target": "target", "aim_point": "aim_point"}},
        {"id": "full-auto", "label": "Full auto", "intent": "fire_full_auto", "params": {"rounds": 5, "critical_effect": "DEVASTATING_HIT"}, "bindings": {"attacker": "actor", "target": "target", "aim_point": "aim_point"}},
        {"id": "fpv-strike", "label": "FPV fragmentation strike", "intent": "fpv_fragmentation_strike", "params": {}, "bindings": {"actor": "actor", "drone": "device", "target": "target", "landing_point": "aim_point"}},
        {"id": "autodrone-fire", "label": "Autodrone fire", "intent": "autodrone_fire", "params": {}, "bindings": {"actor": "actor", "target": "target", "aim_point": "aim_point"}},
        {"id": "overwatch", "label": "Prepare overwatch", "intent": "prepare_overwatch", "params": {}, "bindings": {"actor": "actor", "zone_id": "zone"}},
        {"id": "suppress", "label": "Suppress", "intent": "establish_suppression", "params": {"rounds": 10}, "bindings": {"actor": "actor", "zone_id": "zone"}},
    ]
    network_actions = [
        {"id": "connect", "label": "Connect to interface", "intent": "network_connection", "params": {"action": "CONNECT"}, "bindings": {"actor": "actor", "node_id": "node"}},
        {"id": "disconnect", "label": "Disconnect", "intent": "network_connection", "params": {"action": "DISCONNECT"}, "bindings": {"actor": "actor", "node_id": "node"}},
        {"id": "get-in", "label": "Get in", "intent": "intrude_network", "params": {"action": "GET_IN"}, "bindings": {"actor": "actor", "node_id": "node"}},
        {"id": "discover", "label": "Discover connections", "intent": "intrude_network", "params": {"action": "DISCOVER"}, "bindings": {"actor": "actor", "node_id": "node"}},
        {"id": "foothold", "label": "Establish foothold", "intent": "intrude_network", "params": {"action": "FOOTHOLD"}, "bindings": {"actor": "actor", "node_id": "node"}},
        {"id": "reenter-local", "label": "Use local foothold", "intent": "intrude_network", "params": {"action": "GET_IN", "method": "FOOTHOLD"}, "bindings": {"actor": "actor", "node_id": "node"}},
        {"id": "pivot", "label": "Pivot along connection", "intent": "pivot_network", "params": {}, "bindings": {"actor": "actor", "node_id": "node", "route_step": "route_step"}},
        {"id": "reenter-linked", "label": "Use connected foothold", "intent": "pivot_network", "params": {"method": "FOOTHOLD"}, "bindings": {"actor": "actor", "node_id": "node", "route_step": "route_step"}},
        {"id": "escalate", "label": "Escalate", "intent": "intrude_network", "params": {"action": "ESCALATE"}, "bindings": {"actor": "actor", "node_id": "node"}},
        {"id": "operate", "label": "Operate function", "intent": "operate_remote_function", "params": {}, "bindings": {"actor": "actor", "node_id": "node", "remote_function": "remote_function"}},
    ]
    party_actions = [
        *deepcopy(stance_actions),
        {"id": "cycle", "label": "Cycle weapon", "intent": "cycle_weapon", "params": {}, "bindings": {"actor": "actor"}},
        {"id": "reload-retain", "label": "Reload · retain magazine", "intent": "reload_weapon", "params": {"disposition": "RETAIN"}, "bindings": {"actor": "actor"}},
        {"id": "recover-recoil", "label": "Recover recoil · 1 AP", "intent": "recover_recoil", "params": {"ap": 1}, "bindings": {"actor": "actor"}},
        {"id": "end-turn", "label": "End team turn", "intent": "end_allies_turn", "params": {}},
    ]
    schema = _character_schema(specialty_node_ids, catalog)
    return {
        "id": PACKAGE_ID,
        "version": PACKAGE_VERSION,
        "role": "primary",
        "content_kind": "ruleset",
        "metadata": {"label": "Trigger Discipline v0.1"},
        "provenance": {"publisher": "Pneuma IO", "source_sha256": source_hash},
        "compatibility": deepcopy(HOST_COMPATIBILITY),
        "minimum_model_capability_tier": "sylvia.capability.t02",
        "dependencies": [],
        "capabilities": capabilities,
        "tools": tools,
        "prompt_fragments": [{"id": "trigger-discipline.rules-guidance", "capability": "trigger-discipline.presentation"}],
        "ui_surfaces": [
            {"id": "operations.map", "capability": "trigger-discipline.spatial", "title": "Tactical map", "icon": "▦", "renderer": "tactical_grid", "source": {"kind": "object", "fields": {"scene": {"kind": "state", "namespace": PACKAGE_ID, "key": "state.spatial_scene"}, "objectives": {"kind": "state", "namespace": PACKAGE_ID, "key": "state.objectives"}, "selection": {"kind": "state", "namespace": PACKAGE_ID, "key": "state.selection"}, "preview": {"kind": "state", "namespace": PACKAGE_ID, "key": "state.preview"}}}, "group": "operations", "order": 0, "default_size": 640, "minimum_size": 320, "default_placement": "docked", "allowed_placements": ["docked", "window"], "actions": tactical_actions},
            {"id": "operations.network", "capability": "trigger-discipline.network", "title": "Network", "icon": "◇", "renderer": "network_graph", "source": {"kind": "state", "namespace": PACKAGE_ID, "key": "state.network"}, "group": "operations", "order": 1, "default_size": 520, "minimum_size": 320, "default_placement": "docked", "allowed_placements": ["docked", "window"], "actions": network_actions},
            {
                "id": "operations.status", "capability": "trigger-discipline.presentation",
                "title": "Operation status", "icon": "◎", "renderer": "key_value",
                # Project public status scalars; the complete state includes hidden map/network data.
                "source": {"kind": "object", "fields": {
                    "Active side": {"kind": "state", "namespace": PACKAGE_ID, "key": "state.active_side"},
                    "Decision owner": {"kind": "state", "namespace": PACKAGE_ID, "key": "state.decision_owner"},
                    "Mission status": {"kind": "state", "namespace": PACKAGE_ID, "key": "state.mission.status"},
                }},
                "group": "operations", "order": 2, "default_size": 240, "minimum_size": 160,
                "default_placement": "docked", "allowed_placements": ["docked", "floating", "window"],
                "actions": [],
            },
            {"id": "operations.party", "capability": "trigger-discipline.presentation", "title": "Squad", "icon": "◆", "renderer": "party", "source": {"kind": "object", "fields": {"entities": {"kind": "state", "namespace": PACKAGE_ID, "key": "entities"}, "participants": {"kind": "participants", "kinds": ["trigger-discipline.character"]}}}, "group": "operations", "order": 3, "default_size": 300, "minimum_size": 180, "default_placement": "docked", "allowed_placements": ["docked", "window"], "actions": []},
            {"id": "operations.character", "capability": "trigger-discipline.presentation", "title": "Character", "icon": "◇", "renderer": "unit_list", "source": {"kind": "object", "fields": {"entities": {"kind": "state", "namespace": PACKAGE_ID, "key": "entities"}, "participants": {"kind": "participants", "kinds": ["trigger-discipline.character"]}}}, "group": "operations", "order": 4, "default_size": 280, "minimum_size": 160, "default_placement": "docked", "allowed_placements": ["docked", "window"], "actions": party_actions},
            {"id": "operations.loadout", "capability": "trigger-discipline.presentation", "title": "Loadout", "icon": "▣", "renderer": "inventory", "source": {"kind": "object", "fields": {"entities": {"kind": "state", "namespace": PACKAGE_ID, "key": "entities"}, "participants": {"kind": "participants", "kinds": ["trigger-discipline.character"]}}}, "group": "operations", "order": 5, "default_size": 280, "minimum_size": 160, "default_placement": "docked", "allowed_placements": ["docked", "window"], "actions": []},
        ],
        "participant_artifacts": [_character_declaration(schema)],
        "state_schemas": network_state_schema.state_schemas(),
        "content_declarations": "content/content-declarations.json",
    }


def _panel_layout() -> dict[str, Any]:
    """Start new campaigns with map/network tabs above the squad and character/loadout."""
    def tabs(identity: str, *panels: str) -> dict[str, Any]:
        return {"kind": "tabs", "id": identity, "panels": list(panels), "active": panels[0]}
    return {"version": 1, "windows": [], "hidden": ["operations.status"], "sidebar": {
        "kind": "split", "id": "tactical-stack", "axis": "column", "ratio": 0.62,
        "first": tabs("map-and-network", "operations.map", "operations.network"),
        "second": {"kind": "split", "id": "party-and-character", "axis": "row", "ratio": 0.4,
            "first": tabs("squad", "operations.party"),
            "second": tabs("character-and-loadout", "operations.character", "operations.loadout")},
    }}


def _gameplay_support(coverage: dict[str, Any]) -> dict[str, Any]:
    """Publish real resolution boundaries separately from test-only counterfactual evidence."""
    hybrid_parts = {
        medical.COMBAT_MEDICINE: (
            "treat_casualty checks actual capability, reach, resources and the selected check; it stores first successful treatment per casualty/issue and restores at most 1 RES for COMBAT MEDICINE without healing HP or erasing an injury.",
            "Identify the actual distinct injury/condition with one stable issue_id, establish appropriate treatment and medical plausibility, and supply its explicit AP cost/difficulty. No automatic diagnosis, supply consumption, injury removal or unquantified specialty discount is claimed.",
        ),
        "specialty.networker.tier_1.pivot": (
            "pivot_network validates the discovered link, actor-owned route and session, resolves the authored barrier, and spends its explicit cost.",
            "Adjudicate the specialty's first-attempt reduction using an explicit cost; the runtime does not track or apply that discount automatically.",
        ),
        "specialty.networker.tier_2.foothold": (
            "intrude_network establishes an authored persistence method; re-entry checks its actual validity, scope and route without repeating intrusion.",
            "Adjudicate the specialty's remotely reached-node discount and minimum final cost of 1 AP; the runtime does not calculate that discount.",
        ),
        "specialty.networker.tier_3.route_persistence": (
            "preserve_network_route requires this specialty and preserves one surviving session after one lost link through a known valid alternate route.",
            "Author the reduced AP cost explicitly and adjudicate circumstances beyond the supported one-session transition; this does not implement whole-chain REROUTE.",
        ),
    }
    records = []
    for item in coverage["specialty_nodes"]:
        status = item["package_status"]
        resolution = ("engine_specialized" if status == "implemented" else
                      "unsupported" if status in {"unresolved_design", "structural_finding"}
                      else "gm_adjudicated")
        deterministic_part = gm_part = None
        if resolution != "unsupported":
            if item["id"] == "specialty.networker.entry.map_the_links":
                resolution = "engine_specialized"
            elif item["id"] in hybrid_parts:
                resolution = "hybrid"
                deterministic_part, gm_part = hybrid_parts[item["id"]]
        records.append({
            "id": item["id"], "reference_availability": "included",
            "gameplay_resolution": resolution, "mechanic_family": "specialty",
            "deterministic_part": deterministic_part, "gm_part": gm_part,
        })
    return {"format_id": "sylvia-gm-gameplay-support", "format_version": 1,
            "records": sorted(records, key=lambda item: item["id"])}


def _conformance_setting_package(
    ruleset_content_sha256: str,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Return a validation-only Setting owning the exact compiled Ruleset snapshot."""

    fixture = {
        "format": 1,
        "id": CONFORMANCE_SETTING_ID,
        "version": CONFORMANCE_SETTING_VERSION,
        "purpose": "validation_only",
        "ruleset_id": PACKAGE_ID,
        "scenario_artifact": "conformance/scenario.json",
        "rules_authority": "Trigger Discipline atomic Markdown vault",
    }
    fixture_bytes = _canonical_json(fixture, pretty=True)
    fixture_sha256 = _sha(fixture_bytes)
    declarations = {
        "format_id": "sylvia-gm-package-content-declarations",
        "format_version": 1,
        "scope": {
            "scope_id": "trigger-discipline-conformance-setting",
            "scope_kind": "bounded_package",
            "claim_status": "NOT_COMPLETE",
            "source_boundary_refs": [],
        },
        "source_authorities": [
            {
                "id": "trigger-discipline.conformance-fixture",
                "document_id": "conformance-setting",
                "version": CONFORMANCE_SETTING_VERSION,
                "content_sha256": fixture_sha256,
                "media_type": "application-json",
                "license_id": "LicenseRef-Proprietary",
            }
        ],
        "inclusions": [
            {
                "id": "trigger-discipline-conformance-setting",
                "kind": "campaign_setting",
                "family": "campaign-setting",
                "disposition": "included_nonmechanical",
                "classifications": ["sylvia_gm_implementation"],
                "sources": [],
                "links": {
                    "artifacts": ["conformance/setting.json"],
                    "capabilities": ["trigger-discipline.conformance.setting"],
                    "mechanics": [],
                    "procedures": [],
                    "prompt_fragments": [],
                    "schemas": [],
                    "state_namespaces": [],
                    "tools": [],
                    "ui_surfaces": [],
                },
                "evidence": {
                    "tests": ["trigger-discipline-installed-campaign-catalog"]
                },
            }
        ],
        "exclusions": [],
    }
    files = {
        "conformance/setting.json": fixture_bytes,
        "content/content-declarations.json": _canonical_json(declarations),
    }
    manifest = {
        "id": CONFORMANCE_SETTING_ID,
        "version": CONFORMANCE_SETTING_VERSION,
        "role": "dependency",
        "content_kind": "campaign_setting",
        "metadata": {"label": "Trigger Discipline Conformance Fixture"},
        "provenance": {
            "publisher": "Pneuma IO",
            "source_sha256": fixture_sha256,
        },
        "compatibility": deepcopy(HOST_COMPATIBILITY),
        "minimum_model_capability_tier": "sylvia.capability.t02",
        "dependencies": [
            {
                "namespace": "ruleset_snapshot",
                "kind": "ruleset_snapshot",
                "id": PACKAGE_ID,
                "version": PACKAGE_VERSION,
                "content_sha256": ruleset_content_sha256,
            }
        ],
        "capabilities": ["trigger-discipline.conformance.setting"],
        "tools": [],
        "prompt_fragments": [],
        "ui_surfaces": [],
        "participant_artifacts": [],
        "content_declarations": "content/content-declarations.json",
    }
    return manifest, files


def _complete_package_record(
    descriptor: dict[str, Any], payloads: dict[str, bytes]
) -> dict[str, Any]:
    """Complete one package descriptor using Sylvia's canonical record identity."""

    record = deepcopy(descriptor)
    artifacts = record.get("participant_artifacts")
    if isinstance(artifacts, list):
        record["participant_artifacts"] = [
            {
                **artifact,
                "schema_sha256": _sha(_canonical_json(
                    {
                        "schema": artifact["schema"],
                        "entity_state_schema": artifact["entity_state_schema"],
                        "initial_state_calculations": artifact["initial_state_calculations"],
                    } if "entity_state_schema" in artifact else artifact["schema"]
                )),
            }
            for artifact in artifacts
        ]
    record["files"] = [
        {"path": path, "bytes": len(value), "sha256": _sha(value)}
        for path, value in sorted(payloads.items())
    ]
    record["content_sha256"] = _sha(_canonical_json(record))
    return record


def _read_package_draft(root: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    descriptor = json.loads((root / "package.json").read_text(encoding="utf-8"))
    files_root = root / "files"
    payloads = {
        path.relative_to(files_root).as_posix(): path.read_bytes()
        for path in sorted(files_root.rglob("*"))
        if path.is_file()
    }
    return descriptor, payloads


def _archive_bytes(records: list[tuple[dict[str, Any], dict[str, bytes]]]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        members = [
            (
                "manifest.json",
                _canonical_json(
                    {"format": 4, "packages": [record for record, _ in records]}
                ),
            )
        ]
        members.extend(
            (f"packages/{record['id']}/{path}", value)
            for record, payloads in records
            for path, value in sorted(payloads.items())
        )
        for name, value in members:
            info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, value)
    return output.getvalue()


def _content_declarations(
    source_hash: str,
    parameter_hash: str,
    artifacts: list[str],
    manifest: dict[str, Any],
    exclusions: list[dict[str, Any]],
) -> dict[str, Any]:
    tools = sorted(value["name"] for value in manifest["tools"])
    capabilities = sorted(manifest["capabilities"])
    ui = sorted(value["id"] for value in manifest["ui_surfaces"])
    prompts = sorted(value["id"] for value in manifest["prompt_fragments"])
    return {
        "format_id": "sylvia-gm-package-content-declarations",
        "format_version": 1,
        "scope": {
            "scope_id": "trigger-discipline-v0-1",
            "scope_kind": "bounded_package",
            "claim_status": "NOT_COMPLETE",
            "source_boundary_refs": [
                {"artifact_id": "parameter-manifest", "sha256": parameter_hash},
                {"artifact_id": "source-index", "sha256": source_hash},
            ],
        },
        "source_authorities": [
            {"id": "trigger-discipline.atomic-vault", "document_id": "source-index", "version": PACKAGE_VERSION, "content_sha256": source_hash, "media_type": "application-json", "license_id": "LicenseRef-Proprietary"},
            {"id": "trigger-discipline.balance-parameters", "document_id": "balance-parameter-manifest", "version": "v0.1", "content_sha256": parameter_hash, "media_type": "application-json", "license_id": "LicenseRef-Proprietary"},
        ],
        "inclusions": [{"id": "trigger-discipline-runtime", "kind": "mechanic", "family": "trigger-discipline", "disposition": "included_mechanical", "classifications": ["sylvia_gm_implementation"], "sources": [], "links": {"artifacts": sorted(artifacts), "capabilities": capabilities, "mechanics": tools, "procedures": [], "prompt_fragments": prompts, "schemas": ["pneuma-io.trigger-discipline.character.v0-1"], "state_namespaces": sorted([f"{PACKAGE_ID}.state", f"{PACKAGE_ID}.entities"]), "tools": tools, "ui_surfaces": ui}, "evidence": {"tests": ["trigger-discipline-format4", "trigger-discipline-owner-journey", "trigger-discipline-replay"]}}],
        "exclusions": exclusions,
    }


def build_draft(
    workspace: Path,
    *,
    source_commit: str | None = None,
    draft_path: Path | None = None,
    include_test_tools: bool = False,
    allow_worktree_sources: bool = False,
) -> dict[str, str]:
    """Build package bytes; test-only counterfactual discovery requires explicit selection."""
    root = workspace.resolve()
    source_commit = _verify_source_commit(
        root,
        source_commit or _git(root, "rev-parse", "HEAD"),
        allow_worktree_sources=allow_worktree_sources,
    )
    parameters = default_manifest().seed_set()
    parameter_document = {"format": 1, "manifest": parameters.manifest.to_dict(), "values": parameters.values, "parameter_set_sha256": parameters.hash}
    parameter_bytes = _canonical_json(parameter_document, pretty=True)
    parameter_hash = _sha(parameter_bytes)
    source_document = _source_index(root, source_commit)
    if allow_worktree_sources:
        source_document["source_state"] = "worktree"
        source_document["source_commit_role"] = "base revision; entry hashes identify the actual reviewed bytes"
    source_bytes = _canonical_json(source_document, pretty=True)
    source_hash = _sha(source_bytes)
    specialty_balance = specialty_coverage(parameters, root, include_objective_scenarios=False)
    specialty_records = _specialty_execution_records(specialty_balance)
    coverage = _coverage(root, parameters, source_commit, balance=specialty_balance)
    # The coverage artifact accounts for every tertiary node, including formal
    # spec gaps.  Only nodes with an executable same-character counterfactual
    # may be admitted to the runtime tool's closed enum/table.
    specialty_node_ids = sorted(specialty_records)
    specialty_branch_ids = sorted(
        {"DEFAULT"}
        | {
            fixture["effect_branch"]
            for record in specialty_records.values()
            for fixture in record["branch_fixture_table"]
        }
    )
    catalog = eligibility_catalog(root)
    manifest = _manifest(
        source_hash,
        specialty_node_ids,
        specialty_branch_ids,
        catalog,
    )
    runtime = _runtime(parameters.values, specialty_records)
    if not include_test_tools:
        manifest["tools"] = [
            item for item in manifest["tools"] if item["name"] != "exercise_specialty_counterfactual"
        ]
        runtime["tools"].pop("exercise_specialty_counterfactual", None)
        runtime["definitions"].pop("specialty_counterfactuals", None)
    coverage["test_tools_included"] = include_test_tools
    coverage["objective_benchmark_status"] = "NOT_RUN_DURING_PACKAGE_BUILD"
    scenario = _scenario(source_commit, parameters.hash, parameters.values)
    files: dict[str, bytes] = {
        "runtime/ruleset.yaml": _canonical_json(runtime, pretty=True),
        "ui/panel-layout.json": _canonical_json(_panel_layout()),
        "references/campaign-authoring.md": (
            Path(__file__).parent / "references" / "campaign-authoring.md"
        ).read_bytes(),
        "references/character-authoring.md": (
            Path(__file__).parent / "references" / "character-authoring.md"
        ).read_bytes(),
        "conformance/specialty-counterfactuals.json": _canonical_json(
            {
                "format": 1,
                "source_commit": source_commit,
                "parameter_set_sha256": parameters.hash,
                "node_count": len(specialty_records),
                "records": list(specialty_records.values()),
            },
            pretty=True,
        ),
        "conformance/character.json": _canonical_json(_conformance_character(), pretty=True),
        "conformance/scenario.json": _canonical_json(scenario, pretty=True),
        "conformance/coverage.json": _canonical_json(coverage, pretty=True),
        "support/gameplay-resolution.json": _canonical_json(_gameplay_support(coverage)),
        "references/specialty-eligibility.json": _canonical_json(catalog),
        "provenance/source-index.json": source_bytes,
        "provenance/parameter-manifest.json": parameter_bytes,
    }
    files.update(_indexed_reference_files(root, source_document))
    specialty_exclusions = sorted(
        [
            {
                "id": (
                    item["id"]
                    if item["package_status"] == "unresolved_design"
                    else f"{item['id']}.unimplemented_boundary"
                ),
                "kind": "mechanic",
                "family": "specialty",
                "reason": "unsupported",
                "classifications": ["unsupported"],
                "sources": [],
                "within_declared_scope": True,
            }
            for item in coverage["specialty_nodes"]
            if item["findings"]
        ],
        key=lambda item: item["id"],
    )
    declarations = _content_declarations(
        source_hash,
        parameter_hash,
        sorted(files),
        manifest,
        specialty_exclusions,
    )
    # Sylvia admits this artifact with its canonical-JSON parser because its
    # bytes participate directly in the signed package closure.
    files["content/content-declarations.json"] = _canonical_json(declarations)
    ruleset_record = _complete_package_record(manifest, files)
    setting_manifest, setting_files = _conformance_setting_package(
        str(ruleset_record["content_sha256"])
    )
    draft = (draft_path or (root / "ruleset-draft")).resolve()
    for path, value in files.items():
        target = draft / "files" / Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)
    draft.mkdir(parents=True, exist_ok=True)
    (draft / "package.json").write_bytes(_canonical_json(manifest, pretty=True))
    dependency_draft = draft / "dependencies" / "conformance-setting"
    for path, value in setting_files.items():
        target = dependency_draft / "files" / Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)
    dependency_draft.mkdir(parents=True, exist_ok=True)
    (dependency_draft / "package.json").write_bytes(
        _canonical_json(setting_manifest, pretty=True)
    )
    try:
        draft_locator = draft.relative_to(root).as_posix()
    except ValueError:
        draft_locator = str(draft)
    return {"draft": draft_locator, "source_sha256": source_hash, "parameter_sha256": parameter_hash, "parameter_set_sha256": parameters.hash}


def publish_candidate(workspace: Path, sylvia_root: Path) -> dict[str, str]:
    root = workspace.resolve()
    sylvia = sylvia_root.resolve()
    commit = _git(sylvia, "rev-parse", "HEAD")
    if not _git(sylvia, "merge-base", "--is-ancestor", SYLVIA_BASELINE, commit) == "":
        raise RuntimeError("unexpected git merge-base output")
    source_path = str(sylvia / "src")
    if source_path not in sys.path:
        sys.path.insert(0, source_path)
    from sylvia_gm.domain.package_library import (
        closure_sha256,
        manifest_from_record,
        resolve_exact_closure,
    )
    from sylvia_gm.infrastructure.package_archive import admit_package_archive

    draft = root / "ruleset-draft"
    primary_descriptor, primary_files = _read_package_draft(draft)
    setting_descriptor, setting_files = _read_package_draft(
        draft / "dependencies" / "conformance-setting"
    )
    primary_record = _complete_package_record(primary_descriptor, primary_files)
    expected_dependency = {
        "namespace": "ruleset_snapshot",
        "kind": "ruleset_snapshot",
        "id": str(primary_record["id"]),
        "version": str(primary_record["version"]),
        "content_sha256": str(primary_record["content_sha256"]),
    }
    if primary_descriptor.get("dependencies") != []:
        raise RuntimeError("primary Ruleset draft must not declare dependencies")
    if setting_descriptor.get("dependencies") != [expected_dependency]:
        raise RuntimeError(
            "conformance Setting draft is not bound to the exact Ruleset snapshot"
        )
    setting_record = _complete_package_record(setting_descriptor, setting_files)
    manifests = (
        manifest_from_record(primary_record, format_version=4),
        manifest_from_record(setting_record, format_version=4),
    )
    package_lock = resolve_exact_closure(manifests)
    closure = closure_sha256(package_lock)
    value = _archive_bytes(
        [(primary_record, primary_files), (setting_record, setting_files)]
    )
    with admit_package_archive(io.BytesIO(value)) as admitted:
        if admitted.package_lock != package_lock:
            raise RuntimeError("candidate package lock changed during pre-admission")

    output = draft / "output"
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"candidate-{closure}.sylvia-gm-package"
    if not target.is_file() or target.read_bytes() != value:
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w+b",
                prefix=".candidate-",
                suffix=".tmp",
                dir=output,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(value)
                temporary.flush()
                os.fsync(temporary.fileno())
            temporary_path.replace(target)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
    return {
        "artifact": target.relative_to(root).as_posix(),
        "closure_sha256": closure,
        "package_sha256": str(primary_record["content_sha256"]),
        "setting_sha256": str(setting_record["content_sha256"]),
        "sylvia_commit": commit,
    }
