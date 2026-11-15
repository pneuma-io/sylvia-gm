"""Ice formation derives actual creatures and authenticates its sole forced push."""

from copy import deepcopy

if __package__:
    from .srd_environment_wall_ice import SOURCE_SHA256, _metadata_patch
    from .srd_spell_environment import (
        POSITION_EXPORTS,
        _array,
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
    from srd_environment_wall_ice import SOURCE_SHA256, _metadata_patch
    from srd_spell_environment import (
        POSITION_EXPORTS,
        _array,
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


def _living_body_tools(existing_tools):
    """Reuse the owning exact Box/Circle interiors and actual bound creature facts."""
    original = existing_tools["qualify_srd_created_actor_unoccupied"]
    collect = deepcopy(original)
    collect["description"] = "Collect real living creatures cut by the admitted formation body."
    collect["effects"][0:0] = [
        _set("_ice_formation_targets", []),
        _set("_ice_formation_actor_ids", []),
    ]
    found = 0
    for effects in _effect_lists(collect["effects"]):
        for i, effect in enumerate(effects):
            if effect.get("require", {}).get("condition") != "_occupant_living != true":
                continue
            effects[i] = _when(
                "_occupant_living == true",
                [
                    _require(
                        "_occupant.metadata.participant_ref not in _ice_formation_actor_ids",
                        "The forming wall needs one actual body for each bound creature.",
                    ),
                    _push("_ice_formation_actor_ids", "_occupant.metadata.participant_ref"),
                    _push(
                        "_ice_formation_targets",
                        {
                            "target_state_id": "_occupant.metadata.participant_ref",
                            "scene_entity_id": "_occupant.id",
                            "origin_position": "_occupant.position",
                        },
                    ),
                ],
            )
            found += 1
    assert found == 1
    collect["effects"].append(
        _require(
            "len(_ice_formation_targets) <= 64",
            "This formation supports at most sixty-four actual affected creatures.",
        )
    )

    destination = deepcopy(original)
    destination["description"] = "Reject a derived destination inside any actual living body."
    destination["parameters"].append(_array("destination"))
    body = next(
        i
        for i, e in enumerate(destination["effects"])
        if e.get("set", {}).get("field") == "_birth_body"
    )
    destination["effects"].insert(body + 1, _set("_birth_origin", "destination"))
    destination["effects"] = [
        e
        for e in destination["effects"]
        if e.get("require", {}).get("condition") != "len(_birth_candidates) > 0"
    ]
    return {
        "collect_srd_wall_ice_formation_creatures": collect,
        "qualify_srd_wall_ice_destination_unoccupied": destination,
    }


def _qualify_push_tool():
    """Bind movement permission only to the exact current paid formation receipt."""
    equal_origin = " and ".join(
        f"_ice_push_scene.position[{i}] == _ice_push_record.origin_position[{i}]" for i in range(3)
    )
    equal_destination = " and ".join(
        f"destination[{i}] == _ice_push_record.destination[{i}]" for i in range(3)
    )
    return _environment_tool(
        "Authenticate the source's unconsumed forced movement, without caller force flags.",
        [_param("subject"), _array("destination"), _param("plane")],
        [
            _set("_ice_push_allowed", False),
            _set("_ice_push_record", "state.wall_ice_pending_push"),
            _require(
                "_ice_push_record != null and _ice_push_record.kind == 'wall_ice_formation' "
                "and _ice_push_record.spell == 'wall_of_ice' and "
                "_ice_push_record.consumed == false",
                "Use the exact unconsumed Ice formation push receipt.",
            ),
            _require(
                f"_ice_push_record.source_sha256 == {SOURCE_SHA256!r}",
                "The push must belong to the pinned Wall of Ice source.",
            ),
            _set(
                "_ice_push_source", "state.persistent_spell_areas[_ice_push_record.source_area_id]"
            ),
            _call(
                "qualify_srd_wall_of_ice_source",
                {
                    "caster_state": "{_ice_push_record.caster}",
                    "area_id": "{_ice_push_record.source_area_id}",
                },
                ("_sphere_source_current",),
            ),
            _require(
                "_sphere_source_current == true and _ice_push_source.appearance_in_progress "
                "== true and _ice_push_source.appearance_sequence == _ice_push_source.sequence "
                "and _ice_push_record.source_sequence == _ice_push_source.sequence "
                "and _ice_push_record.caster == _ice_push_source.caster "
                "and plane == _ice_push_source.plane and plane == _ice_push_record.plane",
                "Only this actual active paid Ice cast can authorize the push.",
            ),
            _call("srd_information_scene_entity", {"subject": "{subject}"}, POSITION_EXPORTS),
            _set("_ice_push_scene", "_info_scene_entity"),
            _require(
                "_ice_push_scene.id == _ice_push_record.scene_entity_id and "
                "_ice_push_scene.metadata.participant_ref == _ice_push_record.target_state_id and "
                "(_ice_push_scene.metadata.plane == plane or (_ice_push_scene.metadata.plane == "
                "null and plane == 'material'))",
                "Use the exact affected creature on its actual plane.",
            ),
            _require(
                equal_origin, "The actual affected body has moved since its push was derived."
            ),
            _require(equal_destination, "The push must use its actual geometry-derived endpoint."),
            _set("_ice_push_member", False),
            _set("_ice_push_targets", "_ice_push_source.formation_targets"),
            _each(
                "_ice_push_targets",
                "_ice_push_target",
                [
                    _when(
                        "_ice_push_target.scene_entity_id == _ice_push_scene.id and "
                        "_ice_push_target.target_state_id == _ice_push_record.target_state_id",
                        [_set("_ice_push_member", True)],
                    )
                ],
            ),
            _require(
                "_ice_push_member == true", "The source must actually cut this creature's space."
            ),
            _call(
                "srd_information_scene_entity",
                {"subject": "{_ice_push_record.probe_id}"},
                POSITION_EXPORTS,
            ),
            _set("_ice_push_probe", "_info_scene_entity"),
            _require(
                "_ice_push_probe.active == true and _ice_push_probe.movement == 'passable' "
                "and _ice_push_probe.footprint.kind == 'box' and _ice_push_probe.height == 10 "
                "and _ice_push_probe.metadata.ice_formation_area_id == "
                "_ice_push_record.source_area_id "
                "and _ice_push_probe.metadata.ice_formation_sequence == _ice_push_source.sequence "
                "and _ice_push_probe.metadata.plane == plane "
                "and _ice_push_probe.id == _ice_push_source.formation_probe_id",
                "Use the forming source's actual unchanged whole-wall body.",
            ),
            _require(
                " and ".join(
                    f"_ice_push_probe.position[{i}] == _ice_push_source.origin[{i}]"
                    for i in range(3)
                ),
                "The forming Ice body must retain its actual cast origin.",
            ),
            _set("_ice_push_extents", [50, 0.5]),
            _set("_ice_push_face", "'north'"),
            _when(
                "_ice_push_source.ice_push_side == 'negative'", [_set("_ice_push_face", "'south'")]
            ),
            _when(
                "_ice_push_source.wall_axis == 'north_south'",
                [
                    _set("_ice_push_extents", [0.5, 50]),
                    _set("_ice_push_face", "'east'"),
                    _when(
                        "_ice_push_source.ice_push_side == 'negative'",
                        [_set("_ice_push_face", "'west'")],
                    ),
                ],
            ),
            _require(
                "_ice_push_probe.footprint.half_extents[0] == _ice_push_extents[0] and "
                "_ice_push_probe.footprint.half_extents[1] == _ice_push_extents[1] and "
                "_ice_push_record.reference_face == _ice_push_face",
                "The source must push toward the caster's actual chosen wall side.",
            ),
            *_chosen_side_body("_ice_push_source", "_ice_push_scene", "destination"),
            *_magic_delivery(
                "_ice_push_source.caster", "_ice_push_scene.id", "_ice_push_source.spell_level"
            ),
            _set("_ice_push_caster", "_ice_push_source.caster"),
            _set("_ice_push_allowed", True),
        ],
    )


def _magic_delivery(caster, target, level):
    """Use the actual protected-volume owner; mixed protected formation is unsupported."""
    return [
        _when(
            "state.has_spell_magic_zones == true",
            [
                _call(
                    "check_srd_magic_zone_effect",
                    {
                        "source_subject": "{" + caster + "}",
                        "target_subject": "{" + target + "}",
                        "effect_kind": "spell",
                        "actual_spell_level": "{" + level + "}",
                    },
                    ("_magic_zone_effect_allowed",),
                ),
                _require(
                    "_magic_zone_effect_allowed == true",
                    "Ice formation across protected magical space needs actual source clipping.",
                ),
            ],
        )
    ]


def _chosen_side_body(source, scene, destination):
    """Reject a body straddling the forming wall or landing on the opposite side."""
    return [
        _require(
            f"{scene}.footprint.kind == 'box'",
            "This chosen-side push requires the actual admitted Box creature body.",
        ),
        _set("_ice_face_axis", 1),
        _when(f"{source}.wall_axis == 'north_south'", [_set("_ice_face_axis", 0)]),
        _when(
            f"{source}.ice_push_side == 'positive'",
            [
                _require(
                    f"{destination}[_ice_face_axis] - "
                    f"{scene}.footprint.half_extents[_ice_face_axis] >= "
                    f"{source}.origin[_ice_face_axis] + 0.5",
                    "The whole actual body must land on the chosen positive wall side.",
                ),
            ],
            [
                _require(
                    f"{destination}[_ice_face_axis] + "
                    f"{scene}.footprint.half_extents[_ice_face_axis] <= "
                    f"{source}.origin[_ice_face_axis] - 0.5",
                    "The whole actual body must land on the chosen negative wall side.",
                ),
            ],
        ),
    ]


def _chosen_side_region():
    """Bind the neutral placement domain to the printed chosen map-bounded side."""
    directions = []
    for axis, normal in (("north_south", 0), ("east_west", 1)):
        minimum = [f"state.spatial_scene.map.bounds.min[{i}]" for i in range(3)]
        maximum = [f"state.spatial_scene.map.bounds.max[{i}]" for i in range(3)]
        positive = list(minimum)
        positive[normal] = f"_ice_prepare_source.origin[{normal}] + 0.5"
        negative = list(maximum)
        negative[normal] = f"_ice_prepare_source.origin[{normal}] - 0.5"
        directions.append(
            _when(
                f"_ice_prepare_source.wall_axis == '{axis}'",
                [
                    _set("_ice_region_min", minimum),
                    _set("_ice_region_max", maximum),
                    _when(
                        "_ice_prepare_source.ice_push_side == 'positive'",
                        [
                            _set("_ice_region_min", positive),
                        ],
                        [_set("_ice_region_max", negative)],
                    ),
                ],
            )
        )
    return [
        _call(
            "format_srd_information_projection",
            {"message": "{_ice_prepare_source.formation_probe_id}:chosen_side"},
            {"_ice_region_id": "_info_projection_message"},
        ),
        *directions,
        _set("_ice_prior_regions", "state.spatial_scene.zones"),
        _each(
            "_ice_prior_regions",
            "_ice_prior_region",
            [
                _require(
                    "_ice_prior_region.id != _ice_region_id",
                    "The source side needs a unique region ID.",
                )
            ],
        ),
        _set(
            "_ice_region",
            {
                "id": "_ice_region_id",
                "map_id": "state.spatial_scene.map.id",
                "shape": {"kind": "'box'", "min": "_ice_region_min", "max": "_ice_region_max"},
                "metadata": {
                    "plane": "_ice_prepare_source.plane",
                    "ice_formation_area_id": "area_id",
                    "ice_formation_sequence": "_ice_prepare_source.sequence",
                },
            },
        ),
        _push("_ice_prior_regions", "_ice_region"),
        _set("state.spatial_scene.zones", "_ice_prior_regions"),
        _set("state.spatial_scene.map.revision", "state.spatial_scene.map.revision + 1"),
    ]


def _clear_side_region():
    """Remove only this temporary source constraint before the actual position change."""
    return [
        _set("_ice_current_regions", "state.spatial_scene.zones"),
        _set("_ice_kept_regions", []),
        _each(
            "_ice_current_regions",
            "_ice_current_region",
            [
                _when(
                    "_ice_current_region.id != _ice_region_id",
                    [_push("_ice_kept_regions", "_ice_current_region")],
                )
            ],
        ),
        _set("state.spatial_scene.zones", "_ice_kept_regions"),
        _set("state.spatial_scene.map.revision", "state.spatial_scene.map.revision + 1"),
    ]


def _prepare_push_tool():
    """Derive one chosen-face endpoint from the actual source and creature bodies."""
    return _environment_tool(
        "Prepare the once-only paid formation receipt from actual wall-face placement.",
        [_param("area_id"), _entity("target_state")],
        [
            _set("_ice_prepare_source", "state.persistent_spell_areas[area_id]"),
            _call(
                "qualify_srd_wall_of_ice_source",
                {
                    "area_id": "{area_id}",
                    "caster_state": "{_ice_prepare_source.caster}",
                },
                ("_sphere_source_current",),
            ),
            _require(
                "_sphere_source_current == true and "
                "_ice_prepare_source.appearance_in_progress == true",
                "Only the current paid formation may prepare an Ice push.",
            ),
            _call(
                "srd_information_scene_entity", {"subject": "{_target_state_id}"}, POSITION_EXPORTS
            ),
            _set("_ice_prepare_scene", "_info_scene_entity"),
            _set("_ice_prepare_member", False),
            _set("_ice_prepare_targets", "_ice_prepare_source.formation_targets"),
            _each(
                "_ice_prepare_targets",
                "_ice_prepare_target",
                [
                    _when(
                        "_ice_prepare_target.target_state_id == _target_state_id and "
                        "_ice_prepare_target.scene_entity_id == _ice_prepare_scene.id",
                        [
                            _require(
                                " and ".join(
                                    f"_ice_prepare_target.origin_position[{i}] == "
                                    f"_ice_prepare_scene.position[{i}]"
                                    for i in range(3)
                                ),
                                "Use the actual unchanged creature cut by the forming wall.",
                            ),
                            _set("_ice_prepare_member", True),
                        ],
                    )
                ],
            ),
            _require("_ice_prepare_member == true", "The actual source must cut this body."),
            _set("_ice_prepare_face", "'north'"),
            _when(
                "_ice_prepare_source.ice_push_side == 'negative'",
                [_set("_ice_prepare_face", "'south'")],
            ),
            _when(
                "_ice_prepare_source.wall_axis == 'north_south'",
                [
                    _set("_ice_prepare_face", "'east'"),
                    _when(
                        "_ice_prepare_source.ice_push_side == 'negative'",
                        [_set("_ice_prepare_face", "'west'")],
                    ),
                ],
            ),
            _set("_ice_prepare_material", "_ice_prepare_source.plane == 'material'"),
            *_chosen_side_region(),
            *_snapshot(),
            _spatial(
                "placement",
                {
                    "map_id": "{state.spatial_scene.map.id}",
                    "moving_entity_id": "{_ice_prepare_scene.id}",
                    "origin": "{_ice_prepare_scene.position}",
                    "exclude_entity_ids": [],
                    "metadata_filter": {
                        "key": "plane",
                        "value": "{_ice_prepare_source.plane}",
                        "missing_matches": "{_ice_prepare_material}",
                    },
                    "max_candidates": 10000,
                    "containing_zone_id": "{_ice_region_id}",
                    "reference_face": {
                        "target": {
                            "kind": "entity",
                            "id": "{_ice_prepare_source.formation_probe_id}",
                        },
                        "face": "{_ice_prepare_face}",
                    },
                },
                "_ice_prepare_placement",
            ),
            *_clear_side_region(),
            _require(
                "_ice_prepare_placement.supported == true and _ice_prepare_placement.found == true",
                "The chosen side needs an actual supported clear placement.",
            ),
            _set("_ice_push_destination", "_ice_prepare_placement.position"),
            *_chosen_side_body(
                "_ice_prepare_source", "_ice_prepare_scene", "_ice_push_destination"
            ),
            _call(
                "qualify_srd_wall_ice_destination_unoccupied",
                {
                    "summon_id": "{_ice_prepare_scene.id}",
                    "destination": "{_ice_push_destination}",
                },
            ),
            _set(
                "state.wall_ice_pending_push",
                {
                    "kind": "'wall_ice_formation'",
                    "spell": "'wall_of_ice'",
                    "source_area_id": "area_id",
                    "source_sequence": "_ice_prepare_source.sequence",
                    "caster": "_ice_prepare_source.caster",
                    "target_state_id": "_target_state_id",
                    "scene_entity_id": "_ice_prepare_scene.id",
                    "origin_position": "_ice_prepare_scene.position",
                    "destination": "_ice_push_destination",
                    "plane": "_ice_prepare_source.plane",
                    "reference_face": "_ice_prepare_face",
                    "probe_id": "_ice_prepare_source.formation_probe_id",
                    "source_sha256": repr(SOURCE_SHA256),
                    "consumed": False,
                },
            ),
            _set("_ice_push_plane", "_ice_prepare_source.plane"),
        ],
    )


def _appearance_damage_tool():
    """Each captured initial target saves once against the source's shared damage roll."""
    return _environment_tool(
        "Settle the actual forming Ice source's once-only Dexterity-half Cold packet.",
        [
            _param("area_id"),
            _entity("target_state"),
            _optional("witness_state_ids", "array", [], items={"type": "string"}, max_items=64),
        ],
        [
            _set("_ice_appearance", "state.persistent_spell_areas[area_id]"),
            _require(
                "_ice_appearance.spell == 'wall_of_ice' and "
                "_ice_appearance.source_sha256 != null and "
                "_ice_appearance.appearance_in_progress == true and "
                "_ice_appearance.appearance_sequence == _ice_appearance.sequence and "
                "_ice_appearance.appearance_tick == state.lasting_tick",
                "Only this actual initial Ice formation can settle its appearance packet.",
            ),
            _require(
                f"_ice_appearance.source_sha256 == {SOURCE_SHA256!r}",
                "Use the pinned source's actual initial damage.",
            ),
            _set("_ice_appearance_member", False),
            _set("_ice_appearance_targets", "_ice_appearance.formation_targets"),
            _each(
                "_ice_appearance_targets",
                "_ice_appearance_target",
                [
                    _when(
                        "_ice_appearance_target.target_state_id == _target_state_id",
                        [
                            _set("_ice_appearance_member", True),
                            _set(
                                "_ice_appearance_origin", "_ice_appearance_target.origin_position"
                            ),
                        ],
                    )
                ],
            ),
            _require(
                "_ice_appearance_member == true and _target_state_id not in "
                "_ice_appearance.appearance_settled_ids",
                "Settle each actual initial target only once.",
            ),
            _set("_ice_appearance_done", "_ice_appearance.appearance_settled_ids"),
            _push("_ice_appearance_done", "_target_state_id"),
            _set(
                "state.persistent_spell_areas.{area_id}.appearance_settled_ids",
                "_ice_appearance_done",
            ),
            _require(
                "target_state.saving_throw_modifiers.dexterity != null",
                "Use the actual creature's saved Dexterity saving throw modifier.",
            ),
            _call(
                "d20_test",
                {
                    "actor_state": "{_target_state_id}",
                    "kind": "saving_throw",
                    "ability": "dexterity",
                    "ability_modifier": "{target_state.saving_throw_modifiers.dexterity}",
                    "proficiency_bonus": 0,
                    "proficiency_factor": "none",
                    "circumstantial_modifier": 0,
                    "advantage": False,
                    "disadvantage": False,
                    "target_number": "{_ice_appearance.save_dc}",
                },
                ("_d20_test", "_outcome"),
            ),
            _set("_ice_appearance_divisor", 1),
            _when("_outcome == 'success'", [_set("_ice_appearance_divisor", 2)]),
            _set("_ice_appearance_base", "max(0, _ice_appearance.appearance_raw_amount)"),
            _set("_ice_appearance_penalty", "max(0, -_ice_appearance.appearance_raw_amount)"),
            _set("_ice_appearance_amount", "_ice_appearance_base // _ice_appearance_divisor"),
            _set("_ice_appearance_remainder", "_ice_appearance_base % _ice_appearance_divisor"),
            _call(
                "apply_srd_qualifying_damage",
                {
                    "target": "{_target_state_id}",
                    "source_state_id": "{_ice_appearance.caster}",
                    "amount": "{_ice_appearance_amount}",
                    "adjustment": 0,
                    "damage_type": "cold",
                    "witness_state_ids": "{witness_state_ids}",
                    "game_damage_roll": True,
                    "damage_roll_divisor": "{_ice_appearance_divisor}",
                    "damage_roll_base_remainder": "{_ice_appearance_remainder}",
                    "unallocated_roll_penalty": "{_ice_appearance_penalty}",
                },
                ("_effective_damage",),
            ),
            _call(
                "srd_information_scene_entity", {"subject": "{_target_state_id}"}, POSITION_EXPORTS
            ),
            _set(
                "target_state.last_wall_of_ice_appearance",
                {
                    "area_id": "area_id",
                    "sequence": "_ice_appearance.sequence",
                    "origin": "_ice_appearance_origin",
                    "destination": "_info_position",
                    "chosen_side": "_ice_appearance.ice_push_side",
                    "roll": "_d20_test",
                    "outcome": "_outcome",
                    "raw_damage": "_ice_appearance.appearance_damage_roll",
                    "applied_damage": "_effective_damage",
                },
            ),
        ],
    )


def _formation_tool():
    """Push every actual cut body before installing the solid panels and initial packets."""
    return _environment_tool(
        "Finish this actual paid Ice formation through sole movement and damage owners.",
        [
            _param("area_id"),
            _param("probe_id"),
            _param("lighting"),
            _optional("targets", "array", [], items={"type": "object"}, max_items=64),
            _optional("ice_push_side", "string", "positive", enum_values=["positive", "negative"]),
        ],
        [
            _set("_ice_form_source", "state.persistent_spell_areas[area_id]"),
            _call(
                "qualify_srd_wall_of_ice_source",
                {
                    "caster_state": "{_ice_form_source.caster}",
                    "area_id": "{area_id}",
                },
                ("_sphere_source_current",),
            ),
            _require(
                "_sphere_source_current == true", "Install only the actual live paid Ice source."
            ),
            _require(
                "_ice_form_source.appearance_completed != true",
                "An actual Ice formation can settle only once.",
            ),
            _set("state.persistent_spell_areas.{area_id}.formation_targets", "targets"),
            _set("state.persistent_spell_areas.{area_id}.formation_probe_id", "probe_id"),
            _set("state.persistent_spell_areas.{area_id}.ice_push_side", "ice_push_side"),
            _set("state.persistent_spell_areas.{area_id}.appearance_in_progress", True),
            _set(
                "state.persistent_spell_areas.{area_id}.appearance_sequence",
                "_ice_form_source.sequence",
            ),
            _set("state.persistent_spell_areas.{area_id}.appearance_tick", "state.lasting_tick"),
            _set("state.persistent_spell_areas.{area_id}.appearance_settled_ids", []),
            _set("state.persistent_spell_areas.{area_id}.source_sha256", repr(SOURCE_SHA256)),
            _call("srd_information_scene_entity", {"subject": "{probe_id}"}, POSITION_EXPORTS),
            _set(
                "_ice_probe_tags",
                {
                    "ice_formation_area_id": "area_id",
                    "ice_formation_sequence": "_ice_form_source.sequence",
                },
            ),
            _set("_ice_probe_metadata", "merge(_info_scene_entity.metadata, _ice_probe_tags)"),
            *_metadata_patch("{probe_id}", "{_ice_probe_metadata}"),
            *_magic_delivery("_ice_form_source.caster", "probe_id", "_ice_form_source.spell_level"),
            _each(
                "targets",
                "_ice_form_target",
                [
                    _call(
                        "prepare_srd_wall_ice_push",
                        {
                            "area_id": "{area_id}",
                            "target_state": "{_ice_form_target.target_state_id}",
                        },
                        ("_ice_push_destination", "_ice_push_plane"),
                    ),
                    _call(
                        "push_srd_wall_ice_subject",
                        {
                            "subject": "{_ice_form_target.target_state_id}",
                            "destination": "{_ice_push_destination}",
                            "plane": "{_ice_push_plane}",
                        },
                    ),
                    _require(
                        "state.wall_ice_pending_push.consumed == true",
                        "The sole position owner must consume the actual source push receipt.",
                    ),
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_ice_form_target.target_state_id}"},
                        POSITION_EXPORTS,
                    ),
                    _require(
                        " and ".join(
                            f"_info_position[{i}] == _ice_push_destination[{i}]" for i in range(3)
                        ),
                        "The actual pushed body must reach its derived endpoint.",
                    ),
                    _set("state.wall_ice_pending_push", None),
                ],
            ),
            _set("_ice_form_scene", "state.spatial_scene.entities"),
            _set("_ice_form_restored_scene", []),
            _each(
                "_ice_form_scene",
                "_ice_form_entry",
                [
                    _when(
                        "_ice_form_entry.id != probe_id",
                        [_push("_ice_form_restored_scene", "_ice_form_entry")],
                    )
                ],
            ),
            _set("state.spatial_scene.entities", "_ice_form_restored_scene"),
            _set("state.spatial_scene.map.revision", "state.spatial_scene.map.revision + 1"),
            *_snapshot(),
            _call(
                "install_srd_wall_of_ice_panels", {"area_id": "{area_id}", "lighting": "{lighting}"}
            ),
            _when(
                "len(targets) > 0",
                [
                    _set("_ice_form_dice", "10 + max(0, _ice_form_source.spell_level - 6) * 2"),
                    _call(
                        "format_srd_information_projection",
                        {"message": "{_ice_form_dice}d6"},
                        {"_ice_form_expression": "_info_projection_message"},
                    ),
                    {
                        "dice": {"expression": "{_ice_form_expression}"},
                        "store_as": "_ice_form_roll",
                    },
                    _call(
                        "resolve_srd_actor_spell_modifiers",
                        {"target_state": "{_ice_form_source.caster}", "lasting_state": "state"},
                    ),
                    _call(
                        "modify_srd_actor_raw_damage_roll",
                        {
                            "actor_state": "{_ice_form_source.caster}",
                            "lasting_state": "state",
                            "amount": "{_ice_form_roll.total}",
                        },
                        {"_ice_form_raw": "_ray_raw_damage"},
                    ),
                    _set(
                        "state.persistent_spell_areas.{area_id}.appearance_damage_roll",
                        "_ice_form_roll",
                    ),
                    _set(
                        "state.persistent_spell_areas.{area_id}.appearance_raw_amount",
                        "_ice_form_raw",
                    ),
                    _each(
                        "targets",
                        "_ice_form_target",
                        [
                            _call(
                                "settle_srd_wall_ice_appearance",
                                {
                                    "area_id": "{area_id}",
                                    "target_state": "{_ice_form_target.target_state_id}",
                                },
                            )
                        ],
                    ),
                ],
            ),
            _set("state.persistent_spell_areas.{area_id}.appearance_in_progress", False),
            _set("state.persistent_spell_areas.{area_id}.appearance_completed", True),
        ],
    )


def bind_sources(changed):
    """Retain the actual forming body until all source-bound pushes are committed."""
    place = changed["qualify_srd_wall_of_ice_space"]
    place["description"] = "Qualify actual support and collect creatures cut by the flat Ice wall."
    for effect in place["effects"]:
        if effect.get("call") == "resolve_srd_illumination_point_sight":
            effect["params"]["perception_plane"] = "{plane}"
    query = next(
        i
        for i, e in enumerate(place["effects"])
        if e.get("spatial_query", {}).get("operation") == "placement"
    )
    place["effects"][query:query] = [
        _call(
            "collect_srd_wall_ice_formation_creatures",
            {"summon_id": "{_ice_probe_id}"},
            ("_ice_formation_targets",),
        ),
        _set("_ice_formation_scene_ids", []),
        _each(
            "_ice_formation_targets",
            "_ice_space_target",
            [_push("_ice_formation_scene_ids", "_ice_space_target.scene_entity_id")],
        ),
    ]
    query = next(
        e for e in place["effects"] if e.get("spatial_query", {}).get("operation") == "placement"
    )
    query["spatial_query"]["query"]["exclude_entity_ids"] = "{_ice_formation_scene_ids}"
    veto = next(
        i
        for i, e in enumerate(place["effects"])
        if e.get("call") == "qualify_srd_created_actor_unoccupied"
    )
    place["effects"][veto:] = []
    cast = changed["cast_spell_environment"]
    cast["parameters"].append(
        _optional("ice_push_side", "string", "positive", enum_values=["positive", "negative"])
    )
    for effects in _effect_lists(cast["effects"]):
        effects[:] = [e for e in effects if e.get("call") != "install_srd_wall_of_ice_panels"]
        for effect in effects:
            if effect.get("call") == "qualify_srd_wall_of_ice_space":
                effect["export_context"].update(
                    {n: n for n in ("_ice_formation_targets", "_ice_probe_id")}
                )
    install = next(
        i
        for i, e in enumerate(cast["effects"])
        if e.get("condition") == "_environment_profile.concentration == true"
    )
    cast["effects"].insert(
        install + 1,
        _when(
            "_environment_profile.environment_mode == 'wall_ice'",
            [
                _call(
                    "finish_srd_wall_ice_formation",
                    {
                        "area_id": "{_environment_cast_id}",
                        "probe_id": "{_ice_probe_id}",
                        "targets": "{_ice_formation_targets}",
                        "lighting": "{_ice_panel_lighting}",
                        "ice_push_side": "{ice_push_side}",
                    },
                ),
            ],
        ),
    )
    public = changed["cast_spell"]
    if not any(p["name"] == "environment_ice_push_side" for p in public["parameters"]):
        public["parameters"].append(
            _optional(
                "environment_ice_push_side",
                "string",
                "positive",
                enum_values=["positive", "negative"],
            )
        )
    for effects in _effect_lists(public["effects"]):
        for effect in effects:
            if effect.get("call") == "cast_spell_environment":
                effect["params"]["ice_push_side"] = "{environment_ice_push_side}"


def tools(existing_tools):
    """Emit formation qualification; no public cast branch precedes real push composition."""
    return _living_body_tools(existing_tools) | {
        "qualify_srd_wall_ice_push": _qualify_push_tool(),
        "prepare_srd_wall_ice_push": _prepare_push_tool(),
        "settle_srd_wall_ice_appearance": _appearance_damage_tool(),
        "finish_srd_wall_ice_formation": _formation_tool(),
    }
