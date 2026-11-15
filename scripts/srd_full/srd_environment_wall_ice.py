"""Wall of Ice panels retain their actual scene bodies and object-owned HP."""

from copy import deepcopy

if __package__:
    from .srd_spell_environment import (
        POSITION_EXPORTS,
        PROFILES,
        _array,
        _blade_barrier_tools,
        _call,
        _each,
        _effect_lists,
        _entity,
        _environment_tool,
        _optional,
        _param,
        _push,
        _require,
        _set,
        _snapshot,
        _spatial,
        _when,
    )
else:
    from srd_spell_environment import (
        POSITION_EXPORTS,
        PROFILES,
        _array,
        _blade_barrier_tools,
        _call,
        _each,
        _effect_lists,
        _entity,
        _environment_tool,
        _optional,
        _param,
        _push,
        _require,
        _set,
        _snapshot,
        _spatial,
        _when,
    )

SOURCE_SHA256 = "f47aa77fef781cc935dd1f3983da93a43c07d667bd51dfd8b4d874425c50c489"
PROFILE = PROFILES["wall_of_ice"]


def _panel_body(volume="_ice_panel_volume", scene="_ice_panel_scene"):
    return _set(
        volume,
        {
            "kind": "'box'",
            "min": [
                f"{scene}.position[0] - {scene}.footprint.half_extents[0]",
                f"{scene}.position[1] - {scene}.footprint.half_extents[1]",
                f"{scene}.position[2]",
            ],
            "max": [
                f"{scene}.position[0] + {scene}.footprint.half_extents[0]",
                f"{scene}.position[1] + {scene}.footprint.half_extents[1]",
                f"{scene}.position[2] + {scene}.height",
            ],
        },
    )


def _metadata_patch(object_id, metadata, *, movement=None, active=None):
    patch = {"metadata": metadata}
    if movement is not None:
        patch["movement"] = movement
    if active is not None:
        patch["active"] = active
    return [
        *_snapshot(),
        {
            "spatial_mutation": {
                "scene": "state.spatial_scene",
                "map_id": "{state.spatial_scene.map.id}",
                "expected_revision": "{_info_snapshot.geometry_revision}",
                "target": {"kind": "entity", "id": object_id},
                "patch": patch,
            },
            "store_as": "_ice_panel_updated",
        },
    ]


def tools():
    """Emit a source provider, actual panel placement, and exact section retirement."""
    source = deepcopy(_blade_barrier_tools()["qualify_srd_blade_barrier_source"])
    source["description"] = "Qualify this actual Wall of Ice concentration and clock."
    for effects in _effect_lists(source["effects"]):
        for effect in effects:
            value = effect.get("set", {}).get("value")
            if isinstance(value, str):
                effect["set"]["value"] = value.replace("'blade'", "'wall_ice'")
    source["effects"].append(
        _set(
            "_sphere_source_current",
            "_sphere_source_current == true and _sphere_source.spell == 'wall_of_ice'",
        )
    )
    panel = _environment_tool(
        "Validate the actual source, ten-section identity, fixed body and printed object stats.",
        [_param("object_id")],
        [
            _set("_ice_panel_current", False),
            _call("srd_information_scene_entity", {"subject": "{object_id}"}, POSITION_EXPORTS),
            _set("_ice_panel_scene", "_info_scene_entity"),
            _set("_ice_panel_profile", "_ice_panel_scene.metadata.srd_spell_object"),
            _when(
                "_ice_panel_profile.kind == 'wall_ice_panel'",
                [
                    _set("_ice_panel_source_id", "_ice_panel_profile.area_id"),
                    _set("_ice_panel_source", "state.persistent_spell_areas[_ice_panel_source_id]"),
                    _call(
                        "qualify_srd_wall_of_ice_source",
                        {
                            "caster_state": "{_ice_panel_source.caster}",
                            "area_id": "{_ice_panel_source_id}",
                        },
                        ("_sphere_source_current",),
                    ),
                    _when(
                        "_sphere_source_current == true",
                        [
                            _require(
                                "len(_ice_panel_profile) == 9 and "
                                f"_ice_panel_profile.source_sha256 == {SOURCE_SHA256!r} and "
                                "_ice_panel_profile.sequence == _ice_panel_source.sequence and "
                                "_ice_panel_profile.index >= 0 and _ice_panel_profile.index < "
                                "10 and "
                                "_ice_panel_profile.index == floor(_ice_panel_profile.index)",
                                "The Ice panel needs its exact installed source and section index.",
                            ),
                            _require(
                                "_ice_panel_profile.armor_class == 12 and "
                                "_ice_panel_profile.max_hp == 30 "
                                "and _ice_panel_profile.hp >= 0 and _ice_panel_profile.hp <= "
                                "30 and "
                                "_ice_panel_profile.destroyed == (_ice_panel_profile.hp == 0)",
                                "Use the printed Ice section AC12 and persistent HP30.",
                            ),
                            _call(
                                "format_srd_information_projection",
                                {
                                    "message": "{_ice_panel_source.caster}:wall_of_ice:"
                                    "{_ice_panel_source.sequence}:"
                                    "{_ice_panel_profile.index}"
                                },
                                {"_ice_expected_id": "_info_projection_message"},
                            ),
                            _set(
                                "_ice_expected_origin",
                                [
                                    "_ice_panel_source.origin[0] - 45 + "
                                    "_ice_panel_profile.index * 10",
                                    "_ice_panel_source.origin[1]",
                                    "_ice_panel_source.origin[2]",
                                ],
                            ),
                            _set("_ice_expected_extents", [5, 0.5]),
                            _when(
                                "_ice_panel_source.wall_axis == 'north_south'",
                                [
                                    _set(
                                        "_ice_expected_origin",
                                        [
                                            "_ice_panel_source.origin[0]",
                                            "_ice_panel_source.origin[1] - 45 + "
                                            "_ice_panel_profile.index * 10",
                                            "_ice_panel_source.origin[2]",
                                        ],
                                    ),
                                    _set("_ice_expected_extents", [0.5, 5]),
                                ],
                            ),
                            _require(
                                "_ice_panel_scene.id == _ice_expected_id and "
                                "_ice_panel_scene.id in _ice_panel_source.panel_ids and "
                                "_ice_panel_scene.metadata.participant_ref == null and "
                                "_ice_panel_scene.metadata.is_object == true and "
                                "_ice_panel_scene.metadata.plane == _ice_panel_source.plane and "
                                "_ice_panel_scene.metadata.wall_ice_plane == "
                                "_ice_panel_source.plane",
                                "Use the installed scene object panel on its exact source plane.",
                            ),
                            _require(
                                "_ice_panel_scene.footprint.kind == 'box' and "
                                "_ice_panel_scene.height == 10 "
                                "and "
                                + " and ".join(
                                    f"_ice_panel_scene.position[{i}] == _ice_expected_origin[{i}]"
                                    for i in range(3)
                                )
                                + " and "
                                + " and ".join(
                                    f"_ice_panel_scene.footprint.half_extents[{i}] == "
                                    f"_ice_expected_extents[{i}]"
                                    for i in range(2)
                                ),
                                "Relocated or reshaped Ice panels need an owning source-body "
                                "adapter.",
                            ),
                            _panel_body(),
                            _set("_ice_panel_current", "_ice_panel_scene.active == true"),
                        ],
                    ),
                ],
            ),
        ],
    )
    place = _environment_tool(
        "Qualify a flat hundred-foot wall on whole solid support and reject occupied placement.",
        [_entity("caster_state"), _array("origin"), _param("plane"), _param("wall_axis")],
        [
            _call(
                "qualify_srd_environment_ground",
                {"point": "{origin}", "plane": "{plane}"},
                ("_environment_ground_id",),
            ),
            _set("_ice_support_rows", "state.spatial_scene.geometry"),
            _set("_ice_support", {}),
            _each(
                "_ice_support_rows",
                "_ice_ground",
                [
                    _when(
                        "_ice_ground.id == _environment_ground_id",
                        [_set("_ice_support", "_ice_ground.volume")],
                    )
                ],
            ),
            _set("_ice_half_extents", [50, 0.5]),
            _when("wall_axis == 'north_south'", [_set("_ice_half_extents", [0.5, 50])]),
            _set(
                "_ice_cast_volume",
                {
                    "kind": "'box'",
                    "min": [
                        "origin[0] - _ice_half_extents[0]",
                        "origin[1] - _ice_half_extents[1]",
                        "origin[2]",
                    ],
                    "max": [
                        "origin[0] + _ice_half_extents[0]",
                        "origin[1] + _ice_half_extents[1]",
                        "origin[2] + 10",
                    ],
                },
            ),
            _require(
                "_ice_support.kind == 'box'", "The selected planar Ice wall needs flat Box support."
            ),
            _require(
                " and ".join(
                    f"_ice_cast_volume.min[{i}] >= _ice_support.min[{i}] and "
                    f"_ice_cast_volume.max[{i}] <= _ice_support.max[{i}]"
                    for i in range(2)
                )
                + " and abs(origin[2] - _ice_support.max[2]) < 0.000000001",
                "All ten panels must stand on the actual solid supporting surface.",
            ),
            _require(
                " and ".join(
                    f"_ice_cast_volume.min[{i}] >= state.spatial_scene.map.bounds.min[{i}] and "
                    f"_ice_cast_volume.max[{i}] <= state.spatial_scene.map.bounds.max[{i}]"
                    for i in range(3)
                ),
                "All ten printed panels must fit the admitted map.",
            ),
            _call(
                "resolve_srd_illumination_point_sight",
                {"observer_state": "{_caster_state_id}", "point": "{origin}"},
                ("_illumination_point_lighting",),
            ),
            _set("_ice_panel_lighting", "_illumination_point_lighting"),
            _call(
                "format_srd_information_projection",
                {"message": "{_caster_state_id}:wall_ice_space_probe"},
                {"_ice_probe_id": "_info_projection_message"},
            ),
            _set("_ice_prior_scene", "state.spatial_scene.entities"),
            _each(
                "_ice_prior_scene",
                "_ice_existing",
                [
                    _require(
                        "_ice_existing.id != _ice_probe_id",
                        "The placement probe needs a unique scene ID.",
                    )
                ],
            ),
            _set(
                "_ice_probe",
                {
                    "id": "_ice_probe_id",
                    "map_id": "state.spatial_scene.map.id",
                    "position": "origin",
                    "footprint": {"kind": "'box'", "half_extents": "_ice_half_extents"},
                    "height": 10,
                    "facing": [1, 0],
                    "active": True,
                    "movement": "'passable'",
                    "visibility": "'clear'",
                    "metadata": {"plane": "plane", "is_object": True},
                },
            ),
            _set("_ice_staged_scene", "_ice_prior_scene"),
            _push("_ice_staged_scene", "_ice_probe"),
            _set("state.spatial_scene.entities", "_ice_staged_scene"),
            _set("state.spatial_scene.map.revision", "state.spatial_scene.map.revision + 1"),
            *_snapshot(),
            _set("_ice_missing_plane", "plane == 'material'"),
            _spatial(
                "placement",
                {
                    "map_id": "{state.spatial_scene.map.id}",
                    "moving_entity_id": "{_ice_probe_id}",
                    "origin": "{origin}",
                    "exclude_entity_ids": [],
                    "metadata_filter": {
                        "key": "plane",
                        "value": "{plane}",
                        "missing_matches": "{_ice_missing_plane}",
                    },
                    "max_candidates": 10000,
                },
                "_ice_placement",
            ),
            _require(
                "_ice_placement.supported == true",
                "Ice panel placement needs supported actual solid geometry.",
            ),
            _require(
                "_ice_placement.found == true and _ice_placement.distance == 0",
                "The Ice wall cannot overlap an actual blocking body.",
            ),
            _call("qualify_srd_created_actor_unoccupied", {"summon_id": "{_ice_probe_id}"}),
            _set("_ice_restored_scene", []),
            _set("_ice_staged_scene", "state.spatial_scene.entities"),
            _each(
                "_ice_staged_scene",
                "_ice_existing",
                [
                    _when(
                        "_ice_existing.id != _ice_probe_id",
                        [_push("_ice_restored_scene", "_ice_existing")],
                    )
                ],
            ),
            _set("state.spatial_scene.entities", "_ice_restored_scene"),
            _set("state.spatial_scene.map.revision", "state.spatial_scene.map.revision + 1"),
            *_snapshot(),
        ],
    )
    install = _environment_tool(
        "Install ten printed contiguous panels as the sole actual object bodies and HP records.",
        [_param("area_id"), _param("lighting")],
        [
            _set("_ice_install_source", "state.persistent_spell_areas[area_id]"),
            _set("_ice_panel_ids", []),
            _set("_ice_panel_numbers", list(range(10))),
            _set("_ice_install_scene", "state.spatial_scene.entities"),
            _each(
                "_ice_panel_numbers",
                "_ice_number",
                [
                    _call(
                        "format_srd_information_projection",
                        {
                            "message": "{_ice_install_source.caster}:wall_of_ice:"
                            "{_ice_install_source.sequence}:{_ice_number}"
                        },
                        {"_ice_new_id": "_info_projection_message"},
                    ),
                    _set(
                        "_ice_new_origin",
                        [
                            "_ice_install_source.origin[0] - 45 + _ice_number * 10",
                            "_ice_install_source.origin[1]",
                            "_ice_install_source.origin[2]",
                        ],
                    ),
                    _set("_ice_new_extents", [5, 0.5]),
                    _when(
                        "_ice_install_source.wall_axis == 'north_south'",
                        [
                            _set(
                                "_ice_new_origin",
                                [
                                    "_ice_install_source.origin[0]",
                                    "_ice_install_source.origin[1] - 45 + _ice_number * 10",
                                    "_ice_install_source.origin[2]",
                                ],
                            ),
                            _set("_ice_new_extents", [0.5, 5]),
                        ],
                    ),
                    _set(
                        "_ice_new_panel",
                        {
                            "id": "_ice_new_id",
                            "map_id": "state.spatial_scene.map.id",
                            "position": "_ice_new_origin",
                            "footprint": {"kind": "'box'", "half_extents": "_ice_new_extents"},
                            "height": 10,
                            "facing": [1, 0],
                            "active": True,
                            "movement": "'blocking'",
                            "visibility": "'clear'",
                            "metadata": {
                                "is_object": True,
                                "plane": "_ice_install_source.plane",
                                "lighting": "lighting",
                                "wall_ice_plane": "_ice_install_source.plane",
                                "source_environment_id": "area_id",
                                "srd_spell_object": {
                                    "kind": "'wall_ice_panel'",
                                    "source_sha256": repr(SOURCE_SHA256),
                                    "area_id": "area_id",
                                    "sequence": "_ice_install_source.sequence",
                                    "index": "_ice_number",
                                    "armor_class": 12,
                                    "max_hp": 30,
                                    "hp": 30,
                                    "destroyed": False,
                                },
                                "ice_sheet_clock": "'none'",
                                "ice_sheet_turn": -1,
                                "ice_sheet_targets": [],
                            },
                        },
                    ),
                    _push("_ice_install_scene", "_ice_new_panel"),
                    _push("_ice_panel_ids", "_ice_new_id"),
                ],
            ),
            _set("state.spatial_scene.entities", "_ice_install_scene"),
            _set("state.spatial_scene.map.revision", "state.spatial_scene.map.revision + 1"),
            _set("state.persistent_spell_areas.{area_id}.panel_ids", "_ice_panel_ids"),
            *_snapshot(),
        ],
    )
    settle = _environment_tool(
        "Settle only the selected source object's destroyed section into frigid-air space.",
        [_param("object_id")],
        [
            _call(
                "qualify_srd_wall_of_ice_object",
                {"object_id": "{object_id}"},
                ("_ice_panel_current", "_ice_panel_scene", "_ice_panel_profile"),
            ),
            _when(
                "_ice_panel_current == true and _ice_panel_profile.hp == 0 and "
                "_ice_panel_scene.movement == 'blocking'",
                [
                    *_metadata_patch(
                        "{object_id}", "{_ice_panel_scene.metadata}", movement="passable"
                    ),
                ],
            ),
        ],
    )
    retire = _environment_tool(
        "Deactivate only actual panels of the exact retiring source sequence.",
        [_param("area_id")],
        [
            _set("_ice_retire_source", "state.persistent_spell_areas[area_id]"),
            _when(
                "_ice_retire_source.mode == 'wall_ice'",
                [
                    _set("_ice_retire_ids", "_ice_retire_source.panel_ids"),
                    _each(
                        "_ice_retire_ids",
                        "_ice_retire_id",
                        [
                            _call(
                                "srd_information_scene_entity",
                                {"subject": "{_ice_retire_id}"},
                                POSITION_EXPORTS,
                            ),
                            _set("_ice_retire_scene", "_info_scene_entity"),
                            _when(
                                "_ice_retire_scene.metadata.srd_spell_object.sequence == "
                                "_ice_retire_source.sequence and _ice_retire_scene.active == true",
                                [
                                    *_metadata_patch(
                                        "{_ice_retire_id}",
                                        "{_ice_retire_scene.metadata}",
                                        movement="passable",
                                        active=False,
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    return {
        "qualify_srd_wall_of_ice_source": source,
        "qualify_srd_wall_of_ice_object": panel,
        "qualify_srd_wall_of_ice_space": place,
        "install_srd_wall_of_ice_panels": install,
        "settle_srd_wall_of_ice_panel": settle,
        "retire_srd_wall_of_ice_panels": retire,
        **_sheet_tools(),
    }


def _sheet_tools():
    """Resolve frigid-air entry from the destroyed section's actual retained body."""
    body = deepcopy(_blade_barrier_tools()["resolve_srd_blade_barrier_body_segment"])
    body["parameters"].append(_param("object_id"))
    start = next(
        i
        for i, e in enumerate(body["effects"])
        if e.get("set", {}).get("field") == "_blade_body_source"
    )
    body["effects"][start : start + 2] = [
        _call(
            "qualify_srd_wall_of_ice_object",
            {"object_id": "{object_id}"},
            ("_ice_panel_current", "_ice_panel_profile", "_ice_panel_source", "_ice_panel_volume"),
        ),
        _require(
            "_ice_panel_current == true and _ice_panel_profile.hp == 0",
            "Only a destroyed actual Ice section leaves this sheet of frigid air.",
        ),
        _set(
            "_ice_sheet_body", {"volume": "_ice_panel_volume", "plane": "_ice_panel_source.plane"}
        ),
    ]
    for effects in _effect_lists(body["effects"]):
        for effect in effects:
            value = effect.get("set", {}).get("value")
            if isinstance(value, dict):
                for field in ("min", "max"):
                    if field in value:
                        value[field] = [
                            item.replace("_blade_body_source", "_ice_sheet_body")
                            if isinstance(item, str)
                            else item
                            for item in value[field]
                        ]
            if isinstance(value, str):
                effect["set"]["value"] = value.replace("_blade_body_source", "_ice_sheet_body")
            if "condition" in effect:
                effect["condition"] = effect["condition"].replace(
                    "_blade_body_source", "_ice_sheet_body"
                )
    sheet = _environment_tool(
        "Consume this exact section's first actual committed movement through cold air per turn.",
        [
            _entity("target_state"),
            _param("object_id"),
            _optional("witness_state_ids", "array", None, items={"type": "string"}),
        ],
        [
            _call(
                "qualify_srd_wall_of_ice_object",
                {"object_id": "{object_id}"},
                (
                    "_ice_panel_current",
                    "_ice_panel_scene",
                    "_ice_panel_profile",
                    "_ice_panel_source",
                    "_ice_panel_source_id",
                ),
            ),
            _set("_ice_sheet_triggered", False),
            _when(
                "_ice_panel_current == true and _ice_panel_profile.hp == 0 and "
                "target_state.is_alive == true",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_target_state_id}"},
                        POSITION_EXPORTS,
                    ),
                    _require(
                        "_info_scene_entity.metadata.previous_position != null",
                        "Cold-sheet entry needs an actual committed movement receipt.",
                    ),
                    _set("_ice_receipt_start", "_info_scene_entity.metadata.previous_position"),
                    _set("_ice_receipt_end", "_info_position"),
                    _call(
                        "resolve_srd_wall_of_ice_body_segment",
                        {
                            "moving_subject": "{_target_state_id}",
                            "object_id": "{object_id}",
                            "area_id": "{_ice_panel_source_id}",
                            "start": "{_ice_receipt_start}",
                            "end": "{_ice_receipt_end}",
                        },
                        ("_body_hit", "_body_entry", "_body_exit"),
                    ),
                    _set("_ice_sheet_clock", "'exploration'"),
                    _set("_ice_sheet_turn", "state.lasting_tick"),
                    _when(
                        "combat.turn_active == true",
                        [
                            _set("_ice_sheet_clock", "'combat'"),
                            _set("_ice_sheet_turn", "combat.turn_sequence"),
                        ],
                    ),
                    _set("_ice_sheet_done", []),
                    _when(
                        "_ice_panel_scene.metadata.ice_sheet_clock == _ice_sheet_clock and "
                        "_ice_panel_scene.metadata.ice_sheet_turn == _ice_sheet_turn",
                        [_set("_ice_sheet_done", "_ice_panel_scene.metadata.ice_sheet_targets")],
                    ),
                    _when(
                        "_body_hit == true and "
                        + " + ".join(
                            f"(_ice_receipt_start[{i}] - _ice_receipt_end[{i}]) ** 2"
                            for i in range(3)
                        )
                        + " > 0 and "
                        "_target_state_id not in _ice_sheet_done",
                        [
                            _push("_ice_sheet_done", "_target_state_id"),
                            _set(
                                "_ice_sheet_changes",
                                {
                                    "ice_sheet_clock": "_ice_sheet_clock",
                                    "ice_sheet_turn": "_ice_sheet_turn",
                                    "ice_sheet_targets": "_ice_sheet_done",
                                },
                            ),
                            _set(
                                "_ice_sheet_metadata",
                                "merge(_ice_panel_scene.metadata, _ice_sheet_changes)",
                            ),
                            *_metadata_patch("{object_id}", "{_ice_sheet_metadata}"),
                            _require(
                                "target_state.saving_throw_modifiers.constitution != null",
                                "Use the creature's actual saved Constitution modifier.",
                            ),
                            _call(
                                "d20_test",
                                {
                                    "actor_state": "{_target_state_id}",
                                    "kind": "saving_throw",
                                    "ability": "constitution",
                                    "ability_modifier": "{target_state.saving_throw_modifiers."
                                    "constitution}",
                                    "proficiency_bonus": 0,
                                    "proficiency_factor": "none",
                                    "circumstantial_modifier": 0,
                                    "advantage": False,
                                    "disadvantage": False,
                                    "target_number": "{_ice_panel_source.save_dc}",
                                },
                                ("_d20_test", "_outcome"),
                            ),
                            _set(
                                "_ice_sheet_dice", "5 + max(0, _ice_panel_source.spell_level - 6)"
                            ),
                            _call(
                                "format_srd_information_projection",
                                {"message": "{_ice_sheet_dice}d6"},
                                {"_ice_sheet_expression": "_info_projection_message"},
                            ),
                            {
                                "dice": {"expression": "{_ice_sheet_expression}"},
                                "store_as": "_ice_sheet_roll",
                            },
                            _call(
                                "resolve_srd_actor_spell_modifiers",
                                {
                                    "target_state": "{_ice_panel_source.caster}",
                                    "lasting_state": "state",
                                },
                            ),
                            _call(
                                "modify_srd_actor_raw_damage_roll",
                                {
                                    "actor_state": "{_ice_panel_source.caster}",
                                    "lasting_state": "state",
                                    "amount": "{_ice_sheet_roll.total}",
                                },
                                {"_ice_sheet_raw": "_ray_raw_damage"},
                            ),
                            _set("_ice_sheet_penalty", "max(0, -_ice_sheet_raw)"),
                            _set("_ice_sheet_base", "max(0, _ice_sheet_raw)"),
                            _set("_ice_sheet_divisor", 1),
                            _when("_outcome == 'success'", [_set("_ice_sheet_divisor", 2)]),
                            _set("_ice_sheet_amount", "_ice_sheet_base // _ice_sheet_divisor"),
                            _set("_ice_sheet_remainder", "_ice_sheet_base % _ice_sheet_divisor"),
                            _call(
                                "apply_srd_qualifying_damage",
                                {
                                    "target": "{_target_state_id}",
                                    "source_state_id": "{_ice_panel_source.caster}",
                                    "amount": "{_ice_sheet_amount}",
                                    "adjustment": 0,
                                    "damage_type": "cold",
                                    "witness_state_ids": "{witness_state_ids}",
                                    "game_damage_roll": True,
                                    "damage_roll_divisor": "{_ice_sheet_divisor}",
                                    "damage_roll_base_remainder": "{_ice_sheet_remainder}",
                                    "unallocated_roll_penalty": "{_ice_sheet_penalty}",
                                },
                                ("_effective_damage",),
                            ),
                            _set(
                                "target_state.last_wall_of_ice_sheet",
                                {
                                    "object_id": "object_id",
                                    "area_id": "_ice_panel_source_id",
                                    "sequence": "_ice_panel_source.sequence",
                                    "turn": "_ice_sheet_turn",
                                    "clock": "_ice_sheet_clock",
                                    "roll": "_d20_test",
                                    "outcome": "_outcome",
                                    "raw_damage": "_ice_sheet_roll",
                                    "applied_damage": "_effective_damage",
                                },
                            ),
                            _set("_ice_sheet_triggered", True),
                        ],
                    ),
                ],
            ),
        ],
    )
    solid = _environment_tool(
        "Resolve physical crossings of live panel bodies, without inventing visual opacity.",
        [
            _array("start"),
            _array("end"),
            _param("plane"),
            _optional("excluded_object", "string", ""),
        ],
        [
            _set("_ice_solid_blocked", False),
            _set("_ice_solid_entities", "state.spatial_scene.entities"),
            _each(
                "_ice_solid_entities",
                "_ice_solid_entity",
                [
                    _when(
                        "_ice_solid_entity.active == true and _ice_solid_entity.movement == "
                        "'blocking' "
                        "and _ice_solid_entity.id != excluded_object "
                        "and _ice_solid_entity.metadata.srd_spell_object.kind == "
                        "'wall_ice_panel' and "
                        "(plane == '' or plane == _ice_solid_entity.metadata.plane)",
                        [
                            _call(
                                "qualify_srd_wall_of_ice_object",
                                {"object_id": "{_ice_solid_entity.id}"},
                                ("_ice_panel_current", "_ice_panel_profile", "_ice_panel_volume"),
                            ),
                            _when(
                                "_ice_panel_current == true and _ice_panel_profile.hp > 0",
                                [
                                    _call(
                                        "srd_information_segment_interval",
                                        {
                                            "start": "{start}",
                                            "end": "{end}",
                                            "volume": "{_ice_panel_volume}",
                                        },
                                        ("_segment_hit", "_segment_entry", "_segment_exit"),
                                    ),
                                    _when(
                                        "_segment_hit == true and _segment_exit > _segment_entry",
                                        [
                                            _set(
                                                "_ice_solid_p",
                                                [
                                                    f"start[{i}] + (end[{i}] - start[{i}]) * "
                                                    "(_segment_entry + _segment_exit) * 0.5"
                                                    for i in range(3)
                                                ],
                                            ),
                                            _when(
                                                " and ".join(
                                                    f"_ice_solid_p[{i}] > "
                                                    f"_ice_panel_volume.min[{i}] and "
                                                    f"_ice_solid_p[{i}] < "
                                                    f"_ice_panel_volume.max[{i}]"
                                                    for i in range(3)
                                                ),
                                                [_set("_ice_solid_blocked", True)],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    )
                ],
            ),
        ],
    )
    return {
        "resolve_srd_wall_of_ice_body_segment": body,
        "resolve_srd_wall_of_ice_sheet_event": sheet,
        "resolve_srd_wall_of_ice_solid": solid,
    }


def _sheet_entry_effects():
    """Queue actual Box-body entry into each unconsumed destroyed section."""
    return _when(
        "_environment_area.mode == 'wall_ice'",
        [
            _set("_ice_entry_panels", "_environment_area.panel_ids"),
            _each(
                "_ice_entry_panels",
                "_ice_entry_panel",
                [
                    _call(
                        "qualify_srd_wall_of_ice_object",
                        {"object_id": "{_ice_entry_panel}"},
                        ("_ice_panel_current", "_ice_panel_profile", "_ice_panel_scene"),
                    ),
                    _when(
                        "_ice_panel_current == true and _ice_panel_profile.hp == 0",
                        [
                            _set("_ice_entry_done", []),
                            _when(
                                "_ice_panel_scene.metadata.ice_sheet_clock == _hazard_clock and "
                                "_ice_panel_scene.metadata.ice_sheet_turn == _hazard_turn",
                                [
                                    _set(
                                        "_ice_entry_done",
                                        "_ice_panel_scene.metadata.ice_sheet_targets",
                                    )
                                ],
                            ),
                            _when(
                                "_actor_state_id not in _ice_entry_done",
                                [
                                    _call(
                                        "resolve_srd_wall_of_ice_body_segment",
                                        {
                                            "moving_subject": "{_actor_state_id}",
                                            "area_id": "{_environment_id}",
                                            "object_id": "{_ice_entry_panel}",
                                            "start": "{_hazard_start}",
                                            "end": "{destination}",
                                        },
                                        (
                                            "_body_hit",
                                            "_body_entry",
                                            "_body_exit",
                                            "_body_bounds",
                                        ),
                                    ),
                                    _when(
                                        "_body_hit == true and _body_exit > _body_entry",
                                        [
                                            _set(
                                                "_ice_entry_p",
                                                [
                                                    f"_hazard_start[{i}] + (destination[{i}] - "
                                                    f"_hazard_start[{i}]) * "
                                                    "(_body_entry + _body_exit) * 0.5"
                                                    for i in range(3)
                                                ],
                                            ),
                                            _set(
                                                "_body_hit",
                                                " and ".join(
                                                    f"_ice_entry_p[{i}] > "
                                                    f"_body_bounds.min[{i}] and "
                                                    f"_ice_entry_p[{i}] < _body_bounds.max[{i}]"
                                                    for i in range(3)
                                                ),
                                            ),
                                            _when(
                                                "_body_hit == true",
                                                [
                                                    _call(
                                                        "measure_srd_information_distance",
                                                        {
                                                            "start": "{_hazard_start}",
                                                            "end": "{destination}",
                                                        },
                                                        ("_scene_distance",),
                                                    ),
                                                    _require(
                                                        "_scene_distance > 0",
                                                        "Cold-sheet entry needs actual "
                                                        "positive movement.",
                                                    ),
                                                    _set(
                                                        "_body_entry",
                                                        "_body_entry + min((_body_exit - "
                                                        "_body_entry) * 0.5, "
                                                        "0.000001 * _scene_distance ** -1)",
                                                    ),
                                                    _when(
                                                        "_body_entry < _hazard_entry_t - "
                                                        "0.000000000001",
                                                        [
                                                            _set("_hazard_entry_events", []),
                                                            _set("_hazard_entry_t", "_body_entry"),
                                                        ],
                                                    ),
                                                    _when(
                                                        "abs(_body_entry - _hazard_entry_t) < "
                                                        "0.000000000001",
                                                        [
                                                            _push(
                                                                "_hazard_entry_events",
                                                                {
                                                                    "area_id": "_environment_id",
                                                                    "target_id": "_actor_state_id",
                                                                    "panel_id": "_ice_entry_panel",
                                                                },
                                                            )
                                                        ],
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def bind_sources(changed):
    """Bind installation and source retirement without altering ordinary object tables."""
    cast = changed["cast_spell_environment"]
    before = next(
        i
        for i, e in enumerate(cast["effects"])
        if e.get("condition", "").startswith("_environment_profile.concentration == true and")
    )
    cast["effects"][before:before] = [
        _when(
            "_environment_profile.environment_mode == 'wall_ice'",
            [
                _require("save_dc >= 1", "Use the caster's actual Ice-wall save DC."),
                _require(
                    "wall_length == 100",
                    "This supported wall uses ten contiguous panels in one hundred-foot row.",
                ),
                _call(
                    "qualify_srd_wall_of_ice_space",
                    {
                        "caster_state": "{_caster_state_id}",
                        "origin": "{_environment_cast_origin}",
                        "plane": "{_environment_plane}",
                        "wall_axis": "{wall_axis}",
                    },
                    ("_ice_cast_volume", "_environment_ground_id", "_ice_panel_lighting"),
                ),
            ],
        )
    ]
    install = next(
        i
        for i, e in enumerate(cast["effects"])
        if e.get("set", {}).get("field", "").endswith(".has_spell_environments")
    )
    cast["effects"][install + 1 : install + 1] = [
        _when(
            "_environment_profile.environment_mode == 'wall_ice'",
            [
                _set(
                    "state.persistent_spell_areas.{_environment_cast_id}.volume", "_ice_cast_volume"
                ),
                _set("state.persistent_spell_areas.{_environment_cast_id}.wall_axis", "wall_axis"),
                _set(
                    "state.persistent_spell_areas.{_environment_cast_id}.ground_id",
                    "_environment_ground_id",
                ),
                _call(
                    "install_srd_wall_of_ice_panels",
                    {"area_id": "{_environment_cast_id}", "lighting": "{_ice_panel_lighting}"},
                ),
            ],
        )
    ]
    retire = changed["retire_srd_environment_ground_source"]
    first = (
        next(
            i
            for i, e in enumerate(retire["effects"])
            if "_environment_area_key" in e.get("export_context", {})
        )
        + 1
    )
    retire["effects"].insert(
        first,
        _when(
            "state.persistent_spell_areas[_environment_area_key].mode == 'wall_ice'",
            [_call("retire_srd_wall_of_ice_panels", {"area_id": "{_environment_area_key}"})],
        ),
    )
    index = changed["refresh_srd_environment_mode_indices"]
    index["effects"].insert(0, _set("state.has_wall_of_ice", False))
    loop = next(e["for_each"] for e in index["effects"] if "for_each" in e)
    loop["do"][-1]["on_true"].append(
        _when("_environment_area.mode == 'wall_ice'", [_set("state.has_wall_of_ice", True)])
    )
    for effects in _effect_lists(changed["dispel_srd_environment_source"]["effects"]):
        for effect in effects:
            requirement = effect.get("require", {})
            if "_environment_dispel_zone_found == true" in requirement.get("condition", ""):
                requirement["condition"] += " or _environment_dispel_source.mode == 'wall_ice'"
    entry = changed["resolve_srd_environment_hazard_entry"]
    loop = next(e["for_each"] for e in entry["effects"] if "for_each" in e)
    loop["do"][-1]["on_true"].append(_sheet_entry_effects())
    dispatch = changed["resolve_srd_environment_hazard_event"]
    dispatch["parameters"].append(_optional("panel_id", "string", ""))
    dispatch["effects"][-1:] = [
        _when(
            "_hazard_dispatch_source.mode == 'wall_ice'",
            [
                _call(
                    "resolve_srd_wall_of_ice_sheet_event",
                    {
                        "target_state": "{_target_state_id}",
                        "object_id": "{panel_id}",
                        "witness_state_ids": "{witness_state_ids}",
                    },
                ),
            ],
            dispatch["effects"][-1:],
        )
    ]
    movement = changed["move_srd_scene_creature_node"]
    for effects in list(_effect_lists(movement["effects"])):
        for effect in list(effects):
            if effect.get("call") == "resolve_srd_environment_hazard_event":
                effects.insert(effects.index(effect), _set("_ice_entered_panel", "''"))
                effects.insert(
                    effects.index(effect),
                    _when(
                        "_hazard_entered_event.panel_id != null",
                        [_set("_ice_entered_panel", "_hazard_entered_event.panel_id")],
                    ),
                )
                effect["params"]["panel_id"] = "{_ice_entered_panel}"


def bind_barriers(barrier):
    """Extend only solid line-of-effect with actual live source-object panel interiors."""
    if "resolve_srd_wall_of_ice_solid" not in str(barrier["effects"]):
        barrier["effects"].append(
            _when(
                "state.has_wall_of_ice == true and rule == 'solid'",
                [
                    _call(
                        "resolve_srd_wall_of_ice_solid",
                        {"start": "{start}", "end": "{end}", "plane": "{plane}"},
                        ("_ice_solid_blocked",),
                    ),
                    _when("_ice_solid_blocked == true", [_set("_information_blocked", True)]),
                ],
            )
        )


def bind_objects(runtime_tools):
    """Branch only actual Ice panel objects, preserving ordinary Breaking Objects validation."""
    changed = {}
    for name in (
        "resolve_srd_spell_object_relation",
        "resolve_srd_spell_object_attack",
        "resolve_srd_sorcerous_burst_object_attack",
    ):
        if name not in runtime_tools:
            continue
        tool = deepcopy(runtime_tools[name])
        if "qualify_srd_wall_of_ice_object" in str(tool["effects"]):
            continue
        for effects in list(_effect_lists(tool["effects"])):
            start = next(
                (
                    i
                    for i, e in enumerate(effects)
                    if e.get("set", {}).get("field") == "_object_profile"
                    and e.get("set", {}).get("value") == "_object_scene.metadata.srd_spell_object"
                ),
                None,
            )
            if start is not None and "qualify_srd_wall_of_ice_object" not in str(effects):
                end = (
                    next(
                        i
                        for i in range(start, len(effects))
                        if effects[i].get("require", {}).get("condition")
                        == "_object_profile.hp > 0"
                    )
                    + 1
                )
                ordinary = effects[start:end]
                effects[start:end] = [
                    _when(
                        "_object_scene.metadata.srd_spell_object.kind == 'wall_ice_panel'",
                        [
                            _call(
                                "qualify_srd_wall_of_ice_object",
                                {"object_id": "{object_id}"},
                                ("_ice_panel_current", "_ice_panel_profile"),
                            ),
                            _require(
                                "_ice_panel_current == true",
                                "This Ice panel's actual spell source has ended.",
                            ),
                            _set("_object_profile", "_ice_panel_profile"),
                            _set("_object_base_ac", 12),
                            _set("_object_max_hp", 30),
                            _require(
                                "_object_profile.hp > 0", "This Ice section is already destroyed."
                            ),
                        ],
                        ordinary,
                    )
                ]
            for effect in list(effects):
                if effect.get("set", {}).get("field") == "_object_intrinsic_immunities":
                    where = effects.index(effect) + 1
                    if "_object_profile.kind == 'wall_ice_panel'" not in str(
                        effects[where : where + 1]
                    ):
                        effects.insert(
                            where,
                            _when(
                                "_object_profile.kind == 'wall_ice_panel'",
                                [
                                    _push("_object_intrinsic_immunities", "'cold'"),
                                ],
                            ),
                        )
                setter = effect.get("set", {})
                if (
                    setter.get("field") == "_object_damage_applied"
                    and isinstance(setter.get("value"), str)
                    and setter["value"].startswith("min(_object_hp_before")
                ):
                    where = effects.index(effect)
                    actual_amount = setter["value"][len("min(_object_hp_before, ") : -1]
                    effects[where] = _when(
                        "_object_profile.kind == 'wall_ice_panel' and damage_type == 'fire'",
                        [
                            _set(
                                "_object_damage_applied",
                                f"min(_object_hp_before, ({actual_amount}) * 2)",
                            )
                        ],
                        [effect],
                    )
                if effect.get("spatial_mutation", {}).get("target", {}).get(
                    "id"
                ) == "{object_id}" and "metadata" in effect["spatial_mutation"].get("patch", {}):
                    where = effects.index(effect) + 1
                    if "settle_srd_wall_of_ice_panel" not in str(effects[where : where + 1]):
                        effects.insert(
                            where,
                            _when(
                                "_object_profile.kind == 'wall_ice_panel'",
                                [
                                    _call(
                                        "settle_srd_wall_of_ice_panel", {"object_id": "{object_id}"}
                                    )
                                ],
                            ),
                        )
        changed[name] = tool
    return changed


def bind_movement(runtime_tools):
    """Use the neutral body sweep for panels without broadening creature occupancy rules."""
    segment_name = "move_srd_scene_creature_environment_segment"
    segment = deepcopy(runtime_tools[segment_name])
    if "qualify_srd_wall_of_ice_movement" not in str(segment["effects"]):
        before = next(
            i
            for i, e in enumerate(segment["effects"])
            if e.get("spatial_mutation", {}).get("target", {}).get("kind") == "entity"
        )
        segment["effects"].insert(
            before,
            _when(
                "state.has_wall_of_ice == true",
                [
                    _call(
                        "qualify_srd_wall_of_ice_movement",
                        {"actor_subject": "{_actor_state_id}", "destination": "{destination}"},
                    ),
                ],
            ),
        )
    query = _environment_tool(
        "Validate live source panels, then ask the existing neutral body sweep for the real route.",
        [_param("actor_subject"), _array("destination")],
        [
            _call("srd_information_scene_entity", {"subject": "{actor_subject}"}, POSITION_EXPORTS),
            _set("_ice_mover_id", "_info_scene_entity.id"),
            _set("_ice_mover_plane", "_info_scene_entity.metadata.plane"),
            _require("_ice_mover_plane != null", "Use the mover's actual plane."),
            _set("_ice_movement_panels", "state.spatial_scene.entities"),
            _each(
                "_ice_movement_panels",
                "_ice_move_panel",
                [
                    _when(
                        "_ice_move_panel.active == true and "
                        "_ice_move_panel.metadata.wall_ice_plane == "
                        "_ice_mover_plane",
                        [
                            _call(
                                "qualify_srd_wall_of_ice_object",
                                {"object_id": "{_ice_move_panel.id}"},
                                ("_ice_panel_current",),
                            ),
                            _require(
                                "_ice_panel_current == true",
                                "Refresh or retire the exact Ice panel source before movement.",
                            ),
                        ],
                    )
                ],
            ),
            *_snapshot(),
            _spatial(
                "sweep",
                {
                    "map_id": "{state.spatial_scene.map.id}",
                    "moving_entity_id": "{_ice_mover_id}",
                    "destination": "{destination}",
                    "include_entities": True,
                    "exclude_entity_ids": [],
                    "metadata_filter": {
                        "key": "wall_ice_plane",
                        "value": "{_ice_mover_plane}",
                        "missing_matches": False,
                    },
                },
                "_ice_body_sweep",
            ),
            _require(
                "_ice_body_sweep.supported == true",
                "Ice wall movement needs a supported actual mover body.",
            ),
            _require(
                "_ice_body_sweep.clear == true",
                "An actual intact Ice section blocks this movement.",
            ),
        ],
    )
    return {segment_name: segment, "qualify_srd_wall_of_ice_movement": query}
