"""Sunbeam retains its paid source and resolves fresh Lines through existing save/packet owners."""

from copy import deepcopy

if __package__:
    from . import srd_dragon_breath as retained
    from .srd_flame_blade_light import _path_tool
    from .srd_retained_spell_attacks import _install_tool, _lists
    from .srd_spell_illumination import _distance
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
        _when,
    )
    from .srd_timed_senses import _loop, _public, _replace_tools
else:
    import srd_dragon_breath as retained
    from srd_flame_blade_light import _path_tool
    from srd_retained_spell_attacks import _install_tool, _lists
    from srd_spell_illumination import _distance
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
        _when,
    )
    from srd_timed_senses import _loop, _public, _replace_tools


PROFILE = {
    "family": "sunbeam",
    "casting_time": "action",
    "base_level": 6,
    "range_kind": "self",
    "range_feet": 0,
    "concentration": True,
    "duration_rounds": 10,
    "base_dice": 6,
    "damage_die": "d8",
    "damage_type": "radiant",
    "save_ability": "constitution",
    "success_damage": "half",
}
PUBLIC_TOOLS = {"use_srd_sunbeam"}
SOURCE_EXPORTS = ("_sunbeam_source", "_sunbeam_source_id", "_sunbeam_dice_count")
INTENTS = ("sunbeam_apex", "sunbeam_direction")


def schema():
    return {
        "type": "object",
        "properties": {
            key: {
                "type": "boolean"
                if isinstance(value, bool)
                else "integer"
                if isinstance(value, int)
                else "string",
                "enum": [value],
            }
            for key, value in PROFILE.items()
            if key != "family"
        },
        "required": [key for key in PROFILE if key != "family"],
        "additionalProperties": False,
    }


def validate_profiles(definitions):
    for spell, definition in definitions.items():
        if definition.get("family") == "sunbeam" and (spell != "sunbeam" or definition != PROFILE):
            raise ValueError("Sunbeam differs from the pinned fixed source.")


def _rename(value):
    if isinstance(value, str):
        return (
            value.replace("dragons_breath", "sunbeam")
            .replace("dragon_breath", "sunbeam")
            .replace("Dragon's Breath", "Sunbeam")
            .replace("has_sunbeam", "has_sunbeam_sources")
            .replace("breather_state", "caster_state")
            .replace("_breath_", "_sunbeam_")
        )
    if isinstance(value, list):
        return [_rename(child) for child in value]
    if isinstance(value, dict):
        return {_rename(key): _rename(child) for key, child in value.items()}
    return value


def _intents():
    return [
        _parameter(
            name,
            "array",
            required=False,
            default=[],
            items={"type": "number"},
            min_items=0,
            max_items=3,
        )
        for name in INTENTS
    ] + [
        _parameter("sunbeam_height", "number", required=False, default=0, minimum=0),
    ]


def _mote_intent():
    return _parameter(
        "sunbeam_mote_position",
        "array",
        required=False,
        default=[],
        items={"type": "number"},
        min_items=0,
        max_items=3,
    )


def _installer():
    tool = retained._rename(_install_tool())
    tool = _rename(tool)
    next(p for p in tool["parameters"] if p["name"] == "slot_level")["minimum"] = 6
    tool["parameters"].append(_parameter("save_dc", "integer", minimum=1, maximum=40))
    tool["parameters"].append(
        _parameter("mote_offset", "array", items={"type": "number"}, min_items=3, max_items=3)
    )
    for sequence in _lists(tool["effects"]):
        for effect in sequence:
            row = effect.get("list_push", {}).get("value")
            if isinstance(row, dict) and row.get("kind") == "sunbeam":
                row["save_dc"] = "save_dc"
                row["mote_offset"] = "mote_offset"
    return tool


def _prepare():
    tool = _rename(retained._prepare_tool())
    next(e for e in tool["effects"] if e.get("set", {}).get("field") == "_sunbeam_dice_count")[
        "set"
    ]["value"] = 6
    return tool


def _line_collector(tools):
    """Use four separating axes and height; discard only floating-point origin tangency."""
    tool = deepcopy(tools["collect_srd_cone_creature_targets"])
    for parameter in tool["parameters"]:
        if parameter["name"] in {"length_feet", "width_feet", "include_origin"}:
            parameter["model_hidden"] = True
    tool["parameters"].append(_parameter("sunbeam_height", "number", minimum=0))
    tool["effects"][:0] = [
        _require(
            "length_feet == 60 and width_feet == 5 and include_origin != true",
            "Sunbeam's printed Line is fixed at60 feet long and5 feet wide.",
        ),
        _require(
            "sunbeam_height > 0 and sunbeam_height <= state.spatial_scene.map.bounds.max[2] - "
            "state.spatial_scene.map.bounds.min[2]",
            "Admit the Line's positive unprinted height in this scene.",
        ),
    ]
    matched = 0
    for sequence in list(_lists(tool["effects"])):
        for effect in sequence:
            if (
                "_cone_target.active == true" in effect.get("condition", "")
                and "_cone_source.metadata.plane" in effect["condition"]
            ):
                matched += 1
                effect["on_true"] = [
                    _require(
                        "_cone_target.footprint.kind == 'box'",
                        "Sunbeam currently supports admitted Box creature bodies.",
                    ),
                    _set(
                        "_sunbeam_dx",
                        "_cone_target.position[0] - _srd_cone_origin[0] - _cone_axis[0] * 30",
                    ),
                    _set(
                        "_sunbeam_dy",
                        "_cone_target.position[1] - _srd_cone_origin[1] - _cone_axis[1] * 30",
                    ),
                    _set("_sunbeam_x", "_cone_target.footprint.half_extents[0]"),
                    _set("_sunbeam_y", "_cone_target.footprint.half_extents[1]"),
                    _set(
                        "_sunbeam_along",
                        "_sunbeam_dx * _cone_axis[0] + _sunbeam_dy * _cone_axis[1]",
                    ),
                    _set(
                        "_sunbeam_along_extent",
                        "_sunbeam_x * abs(_cone_axis[0]) + _sunbeam_y * abs(_cone_axis[1])",
                    ),
                    _set(
                        "_sunbeam_horizontal_intersection",
                        "abs(_sunbeam_along) <= 30 + _sunbeam_along_extent and "
                        "_sunbeam_along + _sunbeam_along_extent > -29.999999999 and "
                        "abs(-_sunbeam_dx * _cone_axis[1] + _sunbeam_dy * _cone_axis[0]) "
                        "<= 2.5 + _sunbeam_x * abs(_cone_axis[1]) + "
                        "_sunbeam_y * abs(_cone_axis[0]) and "
                        "abs(_sunbeam_dx) <= _sunbeam_x + 30 * abs(_cone_axis[0]) + "
                        "2.5 * abs(_cone_axis[1]) and "
                        "abs(_sunbeam_dy) <= _sunbeam_y + 30 * abs(_cone_axis[1]) + "
                        "2.5 * abs(_cone_axis[0])",
                    ),
                    _set(
                        "_srd_cone_intersects",
                        "_sunbeam_horizontal_intersection == true and "
                        "_cone_target.position[2] <= _srd_cone_origin[2] + "
                        "sunbeam_height * 0.5 and "
                        "_cone_target.position[2] + _cone_target.height >= "
                        "_srd_cone_origin[2] - sunbeam_height * 0.5",
                    ),
                ]
    if matched != 1:
        raise ValueError("Sunbeam needs the actual supported Cone frame/creature boundary.")
    tool["description"] = "Collect actual Box creatures in a fixed60×5 Line with admitted height."
    tool["effects"] = _tool(tool["description"], deepcopy(tool["parameters"]), tool["effects"])[
        "effects"
    ]
    return tool


def _target(tools):
    """Keep actual saves, mitigation and cards; install only Sunbeam's independent brief Blind."""
    tool = deepcopy(tools["resolve_spell_area_save_damage_target"])
    tool["parameters"].append(_entity("sunbeam_caster"))
    tool["effects"] = [
        e
        for e in tool["effects"]
        if "failed_save_condition == 'blinded'" not in e.get("condition", "")
    ]
    tool["effects"].append(
        _when(
            "_spell_save_outcome == 'failure'",
            [
                _call(
                    "install_srd_timed_condition_source",
                    {
                        "target_state": "{_target_id}",
                        "lasting_state": "{_lasting_state_id}",
                        "caster_state": "{_sunbeam_caster_id}",
                        "spell": "sunbeam",
                        "condition": "blinded",
                        "save_dc": "{spell_save_dc}",
                        "duration_rounds": 0,
                        "repeat_save_timing": "none",
                        "save_ability": "constitution",
                        "spell_cast_id": "{spell_cast_id}",
                        "actual_spell_level": "{actual_spell_level}",
                        "spell_cast_owner": "{_sunbeam_caster_id}",
                    },
                    ["_timed_condition_source_id"],
                ),
                _set("_sunbeam_blind_turn", 0),
                _when(
                    "sunbeam_caster.turns_started != null",
                    [_set("_sunbeam_blind_turn", "sunbeam_caster.turns_started")],
                ),
                _set(
                    "state.timed_condition_sources.{_timed_condition_source_id}.sunbeam_caster_turn",
                    "_sunbeam_blind_turn",
                ),
                _set(
                    "state.timed_condition_sources.{_timed_condition_source_id}.dispel_ongoing",
                    False,
                ),
                _set("_sunbeam_blind_ids", []),
                _when(
                    "state.sunbeam_blind_source_ids != null",
                    [_set("_sunbeam_blind_ids", "state.sunbeam_blind_source_ids")],
                ),
                _when(
                    "_timed_condition_source_id not in _sunbeam_blind_ids",
                    [
                        {
                            "list_push": {
                                "list": "_sunbeam_blind_ids",
                                "value": "_timed_condition_source_id",
                            }
                        }
                    ],
                ),
                _set("state.sunbeam_blind_source_ids", "_sunbeam_blind_ids"),
                _set("state.has_sunbeam_blinds", True),
                {
                    "call": "record_srd_result_text",
                    "params": {
                        "text": "Blinded until the creating caster's next turn starts; "
                        "no repeat save."
                    },
                    "export_context": {"_sunbeam_blind_text": "_recorded_result_text"},
                },
                {
                    "list_push": {
                        "list": "_spell_detail_rows",
                        "value": {"label": "'Effect'", "value": "_sunbeam_blind_text"},
                    }
                },
            ],
        )
    )
    tool["effects"] = _tool(tool["description"], deepcopy(tool["parameters"]), tool["effects"])[
        "effects"
    ]
    return tool


def _blind_source():
    """A live caster-start counter, rather than elapsed duration, owns the printed combat end."""
    return _tool(
        "Expire one genuine Sunbeam Blind source at its creating caster's next start.",
        [_entity("caster_state"), _entity("target_state"), _parameter("source_id")],
        [
            _set("_sunbeam_blind_row", "state.timed_condition_sources[source_id]"),
            _set("_sunbeam_blind_keep", "_sunbeam_blind_row.installed == true"),
            _when(
                "_sunbeam_blind_keep == true",
                [
                    _require(
                        "_sunbeam_blind_row.spell_id == 'sunbeam' and "
                        "_sunbeam_blind_row.condition == 'blinded' and "
                        "_sunbeam_blind_row.caster_state_id == _caster_state_id and "
                        "_sunbeam_blind_row.target_state_id == _target_state_id",
                        "Only the actual Sunbeam Blind source may be retired.",
                    ),
                    _set("_sunbeam_blind_due", False),
                    _when(
                        "caster_state.turns_started != null and caster_state.turns_started > "
                        "_sunbeam_blind_row.sunbeam_caster_turn",
                        [_set("_sunbeam_blind_due", True)],
                    ),
                    _when(
                        "combat.encounter_active != true and "
                        "state.lasting_tick >= _sunbeam_blind_row.started_tick + 2",
                        [_set("_sunbeam_blind_due", True)],
                    ),
                    _when(
                        "_sunbeam_blind_due == true",
                        [
                            _call(
                                "retire_srd_timed_condition_source",
                                {
                                    "source_id": "{source_id}",
                                    "lasting_state": "{_lasting_state_id}",
                                },
                            ),
                            _call(
                                "refresh_srd_timed_condition_target",
                                {
                                    "target_state": "{_target_state_id}",
                                    "lasting_state": "{_lasting_state_id}",
                                },
                            ),
                            _set("_sunbeam_blind_keep", False),
                        ],
                    ),
                ],
            ),
        ],
    )


def _blind_refresh():
    return _tool(
        "Refresh exact Sunbeam Blind IDs, preserving independent/native Blind.",
        [],
        [
            _set("_sunbeam_blind_ids", []),
            _set("_sunbeam_blind_kept", []),
            _when(
                "state.sunbeam_blind_source_ids != null",
                [_set("_sunbeam_blind_ids", "state.sunbeam_blind_source_ids")],
            ),
            _loop(
                "_sunbeam_blind_ids",
                "_sunbeam_blind_id",
                [
                    _set(
                        "_sunbeam_blind_source", "state.timed_condition_sources[_sunbeam_blind_id]"
                    ),
                    _when(
                        "_sunbeam_blind_source.installed == true and "
                        "_sunbeam_blind_source.spell_id == 'sunbeam' and "
                        "_sunbeam_blind_source.sunbeam_caster_turn != null",
                        [
                            _call(
                                "qualify_srd_sunbeam_blind_source",
                                {
                                    "caster_state": "{_sunbeam_blind_source.caster_state_id}",
                                    "target_state": "{_sunbeam_blind_source.target_state_id}",
                                    "source_id": "{_sunbeam_blind_id}",
                                },
                                ["_sunbeam_blind_keep"],
                            ),
                            _when(
                                "_sunbeam_blind_keep == true",
                                [
                                    {
                                        "list_push": {
                                            "list": "_sunbeam_blind_kept",
                                            "value": "_sunbeam_blind_id",
                                        }
                                    }
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _set("state.sunbeam_blind_source_ids", "_sunbeam_blind_kept"),
            _set("state.has_sunbeam_blinds", "len(_sunbeam_blind_kept) > 0"),
        ],
    )


def _line_tool(later):
    effects = [
        _call("prepare_srd_sunbeam_source", {"caster_state": "{_caster_state_id}"}, SOURCE_EXPORTS),
        _call(
            "collect_srd_sunbeam_creature_targets",
            {
                "source_state": "{_caster_state_id}",
                "length_feet": 60,
                "width_feet": 5,
                "include_origin": False,
                "apex": "{sunbeam_apex}",
                "direction": "{sunbeam_direction}",
                "sunbeam_height": "{sunbeam_height}",
            },
            ["_srd_cone_targets", "_srd_cone_origin", "_srd_cone_source"],
        ),
        _call(
            "collect_srd_spell_area_cover_targets",
            {
                "source_state": "{_caster_state_id}",
                "targets": "{_srd_cone_targets}",
                "origin": "{_srd_cone_origin}",
            },
            ["_srd_area_targets", "_srd_area_half_cover", "_srd_area_three_quarters"],
        ),
        _require("len(_srd_area_targets) <= 64", "Resolve at most64 affected creatures."),
        _when(
            "'charmed' in caster_state.conditions",
            [
                _call(
                    "validate_srd_spell_charmer_targets",
                    {"caster_state": "{_caster_state_id}", "targets": "{_srd_area_targets}"},
                ),
            ],
        ),
    ]
    if later:
        effects += [
            _when(
                "combat.encounter_active != true",
                [
                    _require(
                        "caster_state.sunbeam_used_tick == null or "
                        "state.lasting_tick >= caster_state.sunbeam_used_tick + 2",
                        "Advance the actual exploration clock before another Sunbeam Magic action.",
                    )
                ],
            ),
            _call(
                "spend_spell_casting_budget",
                {
                    "caster_state": "{_caster_state_id}",
                    "casting_time": "action",
                    "slot_resource": "no_slot",
                },
            ),
        ]
    effects += [
        {"dice": {"expression": "6d8"}, "store_as": "_sunbeam_damage_roll"},
        _set("_sunbeam_raw_damage", "_sunbeam_damage_roll.total"),
        _set("_sunbeam_damage_total", "_sunbeam_raw_damage"),
        _when(
            "caster_state.has_actor_spell_modifiers == true",
            [
                _call(
                    "modify_srd_actor_damage_roll",
                    {
                        "actor_state": "{_caster_state_id}",
                        "amount": "{_sunbeam_raw_damage}",
                        "lasting_state": "{_lasting_state_id}",
                    },
                    ["_actor_modified_damage"],
                ),
                _set("_sunbeam_damage_total", "_actor_modified_damage"),
            ],
        ),
        _set("_sunbeam_summary", []),
        _set("_sunbeam_details", []),
        _loop(
            "_srd_area_targets",
            "_sunbeam_target",
            [
                _call(
                    "resolve_srd_sunbeam_target",
                    {
                        "spell": "sunbeam",
                        "target": "{_sunbeam_target}",
                        "sunbeam_caster": "{_caster_state_id}",
                        "save_ability": "constitution",
                        "spell_save_dc": "{_sunbeam_source.save_dc}",
                        "damage_total": "{_sunbeam_damage_total}",
                        "damage_type": "radiant",
                        "success_damage": "half",
                        "construct_save_disadvantage": False,
                        "half_cover_targets": "{_srd_area_half_cover}",
                        "three_quarters_cover_targets": "{_srd_area_three_quarters}",
                        "source_state_id": "{_caster_state_id}",
                        "spell_cast_id": "{_sunbeam_source_id}",
                        "actual_spell_level": "{_sunbeam_source.slot_level}",
                        "spell_cast_owner": "{_caster_state_id}",
                    },
                    ["_spell_summary_rows", "_spell_detail_rows"],
                ),
                _loop(
                    "_spell_summary_rows",
                    "_sunbeam_row",
                    [{"list_push": {"list": "_sunbeam_summary", "value": "_sunbeam_row"}}],
                ),
                _loop(
                    "_spell_detail_rows",
                    "_sunbeam_row",
                    [{"list_push": {"list": "_sunbeam_details", "value": "_sunbeam_row"}}],
                ),
            ],
        ),
        _set("caster_state.sunbeam_used_tick", "state.lasting_tick"),
        _set("_spell_summary_rows", "_sunbeam_summary"),
        _set("_spell_detail_rows", "_sunbeam_details"),
    ]
    tool = _tool(
        "Create one actual retained Sunbeam Line.", [_entity("caster_state"), *_intents()], effects
    )
    if later:
        tool = _public(
            tool,
            "combat",
            "Sunbeam",
            [
                ("Source", "_sunbeam_source_id"),
                ("Original DC", "_sunbeam_source.save_dc"),
                ("Radiant dice", "_sunbeam_damage_roll.results"),
                ("Raw damage", "_sunbeam_raw_damage"),
                ("After source penalty", "_sunbeam_damage_total"),
            ],
        )
        tool["metadata"]["result_card"]["details"] = {"path": "context._sunbeam_details"}
    return tool


def _cast_tool():
    install = _installer()
    return _tool(
        "Install the genuine paid Sunbeam and create its first Line without another cost.",
        [deepcopy(p) for p in install["parameters"] if p["name"] != "mote_offset"]
        + _intents()
        + [_mote_intent()],
        [
            _require(
                "len(sunbeam_mote_position) == 3",
                "Admit Sunbeam's unprinted actual mote position above its caster.",
            ),
            _call(
                "srd_information_scene_entity",
                {"subject": "{_caster_state_id}"},
                ["_info_position", "_info_scene_entity"],
            ),
            _set("_sunbeam_mote_body", "_info_scene_entity"),
            _require(
                "_sunbeam_mote_body.metadata.plane != null",
                "Sunbeam needs its actual caster's admitted plane.",
            ),
            _require(
                "_sunbeam_mote_body.footprint.kind == 'box' and "
                "abs(sunbeam_mote_position[0] - _sunbeam_mote_body.position[0]) <= "
                "_sunbeam_mote_body.footprint.half_extents[0] and "
                "abs(sunbeam_mote_position[1] - _sunbeam_mote_body.position[1]) <= "
                "_sunbeam_mote_body.footprint.half_extents[1] and "
                "sunbeam_mote_position[2] > _sunbeam_mote_body.position[2] + "
                "_sunbeam_mote_body.height",
                "Sunbeam's factual mote point must be above this actual caster body.",
            ),
            *[
                _require(
                    f"sunbeam_mote_position[{i}] >= state.spatial_scene.map.bounds.min[{i}] and "
                    f"sunbeam_mote_position[{i}] <= state.spatial_scene.map.bounds.max[{i}]",
                    "Admit the Sunbeam mote point inside the actual scene.",
                )
                for i in range(3)
            ],
            _call(
                "resolve_srd_sunbeam_light_path",
                {
                    "start": "{_info_position}",
                    "end": "{sunbeam_mote_position}",
                    "plane": "{_sunbeam_mote_body.metadata.plane}",
                },
                ["_flame_light_blocked"],
            ),
            _require(
                "_flame_light_blocked != true", "An opaque barrier blocks this mote position."
            ),
            _set(
                "_sunbeam_mote_offset",
                [
                    f"sunbeam_mote_position[{i}] - _sunbeam_mote_body.position[{i}]"
                    for i in range(3)
                ],
            ),
            _call(
                "install_srd_sunbeam",
                {
                    p["name"]: "{_caster_state_id}"
                    if p["name"] == "caster_state"
                    else "{_sunbeam_mote_offset}"
                    if p["name"] == "mote_offset"
                    else "{" + p["name"] + "}"
                    for p in install["parameters"]
                    if p["name"] != "lasting_state"
                },
                ["_sunbeam_source_id"],
            ),
            {
                "call": "create_srd_sunbeam_line",
                "params": {
                    "caster_state": "{_caster_state_id}",
                    "sunbeam_height": "{sunbeam_height}",
                    **{n: "{" + n + "}" for n in INTENTS},
                },
                "export_context": "*",
            },
        ],
    )


def _current_mote():
    """Revalidate the saved factual offset against the current body, form and admitted map."""
    return [
        _require(
            "_info_scene_entity.metadata.plane != null",
            "Sunbeam light needs its actual caster plane.",
        ),
        _require(
            "_info_scene_entity.footprint.kind == 'box' and "
            "abs(_sunbeam_light_record.mote_offset[0]) <= "
            "_info_scene_entity.footprint.half_extents[0] and "
            "abs(_sunbeam_light_record.mote_offset[1]) <= "
            "_info_scene_entity.footprint.half_extents[1] and "
            "_sunbeam_light_record.mote_offset[2] > _info_scene_entity.height",
            "Changed caster body needs a new above-body Sunbeam mote fact.",
        ),
        _set(
            "_sunbeam_mote_point",
            [f"_info_position[{i}] + _sunbeam_light_record.mote_offset[{i}]" for i in range(3)],
        ),
        *[
            _require(
                f"_sunbeam_mote_point[{i}] >= state.spatial_scene.map.bounds.min[{i}] and "
                f"_sunbeam_mote_point[{i}] <= state.spatial_scene.map.bounds.max[{i}]",
                "Sunbeam mote must remain in the admitted scene.",
            )
            for i in range(3)
        ],
    ]


def _light_tool():
    """Existing current caster anchors qualify the fixed light; precise mote offset is explicit."""
    return _tool(
        "Resolve Sunbeam's current source-qualified Bright30/totalDim60 sunlight.",
        [
            _parameter("point", "array", items={"type": "number"}, min_items=3, max_items=3),
            _parameter("plane"),
        ],
        [
            _set("_sunbeam_magic_bright", False),
            _set("_sunbeam_magic_dim", False),
            _set("_sunbeam_sunlight", False),
            _set("_sunbeam_light_records", []),
            _when(
                "state.has_sunbeam_sources == true",
                [_set("_sunbeam_light_records", "state.turn_effects")],
            ),
            _loop(
                "_sunbeam_light_records",
                "_sunbeam_light_record",
                [
                    _when(
                        "_sunbeam_light_record.kind == 'sunbeam'",
                        [
                            _call(
                                "check_srd_sunbeam_record",
                                {
                                    "caster_state": "{_sunbeam_light_record.caster}",
                                    "record": "{_sunbeam_light_record}",
                                },
                                ["_sunbeam_active"],
                            ),
                            _when(
                                "_sunbeam_active == true",
                                [
                                    _call(
                                        "srd_information_scene_entity",
                                        {"subject": "{_sunbeam_light_record.caster}"},
                                        ["_info_position", "_info_scene_entity"],
                                    ),
                                    *_current_mote(),
                                    _when(
                                        "_info_scene_entity.metadata.plane == plane",
                                        [
                                            _distance(
                                                "{_sunbeam_mote_point}",
                                                "{point}",
                                                "_sunbeam_light_distance",
                                            ),
                                            _when(
                                                "_sunbeam_light_distance <= 60",
                                                [
                                                    _call(
                                                        "resolve_srd_sunbeam_light_path",
                                                        {
                                                            "start": "{_sunbeam_mote_point}",
                                                            "end": "{point}",
                                                            "plane": "{plane}",
                                                        },
                                                        ["_flame_light_blocked"],
                                                    ),
                                                    _when(
                                                        "_flame_light_blocked != true",
                                                        [
                                                            _set("_sunbeam_magic_dim", True),
                                                            _set("_sunbeam_sunlight", True),
                                                            _when(
                                                                "_sunbeam_light_distance <= 30",
                                                                [
                                                                    _set(
                                                                        "_sunbeam_magic_bright",
                                                                        True,
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
                    ),
                ],
            ),
        ],
    )


def extend_runtime(text):
    tools = _load_runtime(text)["tools"]
    required = {
        "collect_srd_cone_creature_targets",
        "collect_srd_spell_area_cover_targets",
        "resolve_spell_area_save_damage_target",
        "retire_srd_timed_condition_source",
    }
    if missing := required - tools.keys():
        raise ValueError("Sunbeam needs final owning tools: " + ", ".join(sorted(missing)))
    changed = {
        "install_srd_sunbeam": _installer(),
        "check_srd_sunbeam_record": _rename(retained._record_tool()),
        "prepare_srd_sunbeam_source": _prepare(),
        "refresh_srd_sunbeam": _rename(retained._refresh_tool()),
        "retire_srd_sunbeam_source": _rename(retained._retire_tool()),
        "finish_srd_sunbeam_removal": _rename(retained._finish_removal()),
        "collect_srd_sunbeam_creature_targets": _line_collector(tools),
        "resolve_srd_sunbeam_target": _target(tools),
        "qualify_srd_sunbeam_blind_source": _blind_source(),
        "refresh_srd_sunbeam_blinds": _blind_refresh(),
        "create_srd_sunbeam_line": _line_tool(False),
        "use_srd_sunbeam": _line_tool(True),
        "cast_srd_sunbeam": _cast_tool(),
        "resolve_srd_sunbeam_light": _light_tool(),
        "resolve_srd_sunbeam_light_path": _path_tool(),
    }
    return _replace_tools(text, {n: t for n, t in changed.items() if tools.get(n) != t})


def extend_cast_parameters(text):
    tools = _load_runtime(text)["tools"]
    changed = {}
    for name in ("cast_spell", "cast_spell_outside_combat"):
        tool = deepcopy(tools[name])
        names = {p["name"] for p in tool["parameters"]}
        tool["parameters"] += [p for p in _intents() + [_mote_intent()] if p["name"] not in names]
        if tool != tools[name]:
            changed[name] = tool
    return _replace_tools(text, changed) if changed else text


def bind_runtime_consumers(text):
    """Root joins this after final sources/light, before Reaction copies actual cast parameters."""
    text = extend_runtime(extend_cast_parameters(text))
    tools = _load_runtime(text)["tools"]
    changed = {}
    for name in ("cast_spell", "cast_spell_outside_combat"):
        tool = deepcopy(tools[name])
        calls = {e.get("call") for s in _lists(tool["effects"]) for e in s}
        if "cast_srd_sunbeam" in calls or "cast_spell" in calls:
            continue
        index = next(
            i
            for i, e in enumerate(tool["effects"])
            if e.get("set", {}).get("field") == "_card_slots_after"
        )
        tool["effects"].insert(
            index,
            _when(
                "_spell_cast_failed != true and _pending_spell_operation_lost != true and "
                "_spell_definition.family == 'sunbeam'",
                [
                    {
                        "call": "cast_srd_sunbeam",
                        "export_context": "*",
                        "params": {
                            "caster_state": "{_caster_state_id}",
                            "slot_level": "{_slot_level}",
                            "save_dc": "{_casting_profile.save_dc}",
                            "casting_ability": "{_casting_profile.ability}",
                            "concentration_save_modifier": (
                                "{_casting_profile.concentration_save_modifier}"
                            ),
                            "spell_cast_id": "{_spell_source_cast_id}",
                            "sunbeam_height": "{sunbeam_height}",
                            "sunbeam_mote_position": "{sunbeam_mote_position}",
                            **{n: "{" + n + "}" for n in INTENTS},
                        },
                    }
                ],
            ),
        )
        changed[name] = tool
    for name in ("advance_srd_lasting_clock", "process_srd_turn_effects"):
        tool = deepcopy(tools[name])
        if "refresh_srd_sunbeam_blinds" in str(tool["effects"]):
            continue
        tail = [
            _when(
                "lasting_state.has_sunbeam_sources == true",
                [_call("refresh_srd_sunbeam", {"lasting_state": "{_lasting_state_id}"})],
            ),
            _when(
                "lasting_state.has_sunbeam_blinds == true",
                [_call("refresh_srd_sunbeam_blinds", {"lasting_state": "{_lasting_state_id}"})],
            ),
        ]
        if name == "advance_srd_lasting_clock":
            index = next(
                i
                for i, e in enumerate(tool["effects"])
                if e.get("set", {}).get("field") == "lasting_state.lasting_tick"
            )
            tool["effects"][index + 1 : index + 1] = tail
        else:
            tool["effects"].extend(tail)
        changed[name] = tool
    name = "end_srd_concentration_node"
    tool = deepcopy(tools[name])
    if "retire_srd_sunbeam_source" not in str(tool["effects"]):
        tool["effects"].insert(
            0,
            _when(
                "source_state.concentration_effect_family == 'sunbeam' and "
                "source_state.sunbeam_source_id != null",
                [
                    _call(
                        "retire_srd_sunbeam_source",
                        {
                            "source_id": "{source_state.sunbeam_source_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                    )
                ],
            ),
        )
        changed[name] = tool
    name = (
        "resolve_srd_information_light_node"
        if "resolve_srd_information_light_node" in tools
        else "resolve_srd_information_light"
    )
    light = deepcopy(tools[name])
    if "resolve_srd_sunbeam_light" not in str(light["effects"]):
        index = next(
            i
            for i, e in enumerate(light["effects"])
            if e.get("condition") == "_light_magic_dark == true"
        )
        light["effects"].insert(
            index,
            _when(
                "lasting_state.has_sunbeam_sources == true",
                [
                    _call(
                        "resolve_srd_sunbeam_light",
                        {"point": "{point}", "plane": "{plane}"},
                        ["_sunbeam_magic_bright", "_sunbeam_magic_dim", "_sunbeam_sunlight"],
                    ),
                    _when("_sunbeam_magic_bright == true", [_set("_light_magic_bright", True)]),
                    _when("_sunbeam_magic_dim == true", [_set("_light_magic_dim", True)]),
                    _when("_sunbeam_sunlight == true", [_set("_information_sunlight", True)]),
                ],
            ),
        )
        changed[name] = light
    return _replace_tools(text, changed) if changed else text
