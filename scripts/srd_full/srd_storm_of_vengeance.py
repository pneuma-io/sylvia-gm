"""A paid Storm source owns its real cloud, appearance save and exact Deafened lifetime."""

import re
from copy import deepcopy

if __package__:
    from . import srd_delayed_blast_fireball as bead
    from . import srd_sunbeam as beam
    from . import srd_sunburst_geometry as sphere
    from .srd_scene_relations import _tool_scene
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
    from .srd_timed_senses import _loop, _push, _replace_tools
else:
    import srd_delayed_blast_fireball as bead
    import srd_sunbeam as beam
    import srd_sunburst_geometry as sphere
    from srd_scene_relations import _tool_scene
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
    from srd_timed_senses import _loop, _push, _replace_tools


SPELL = "storm_of_vengeance"
PROFILE = {
    "family": SPELL,
    "casting_time": "action",
    "base_level": 9,
    "range_kind": "distance",
    "range_feet": 5280,
    "concentration": True,
    "duration_rounds": 10,
    "base_dice": 2,
    "damage_die": "d6",
    "damage_type": "thunder",
    "save_ability": "constitution",
    "success_damage": "none",
}
PUBLIC_TOOLS = set()
REFRESH = "refresh_srd_storm_of_vengeance"
START = "advance_srd_storm_caster_start"


def schema():
    return {
        "type": "object",
        "properties": {
            name: {
                "type": "boolean"
                if isinstance(value, bool)
                else "integer"
                if isinstance(value, int)
                else "string",
                "enum": [value],
            }
            for name, value in PROFILE.items()
            if name != "family"
        },
        "required": [name for name in PROFILE if name != "family"],
        "additionalProperties": False,
    }


def validate_profiles(definitions):
    for spell, definition in definitions.items():
        if definition.get("family") == SPELL and (spell != SPELL or definition != PROFILE):
            raise ValueError("Storm of Vengeance differs from its pinned fixed source.")


def _rename(value):
    if isinstance(value, str):
        return (
            re.sub(r"(?<!\w)_sunbeam_", "_storm_", value)
            .replace("sunbeam", SPELL)
            .replace("Sunbeam", "Storm of Vengeance")
            .replace("has_storm_of_vengeance_sources", "has_storm_sources")
        )
    if isinstance(value, list):
        return [_rename(child) for child in value]
    if isinstance(value, dict):
        return {_rename(key): _rename(child) for key, child in value.items()}
    return value


def _intent():
    return _parameter(
        "storm_cloud_point",
        "array",
        required=False,
        default=[],
        items={"type": "number"},
        min_items=0,
        max_items=3,
    )


def _installer():
    tool = _rename(beam._installer())
    tool["parameters"] = [p for p in tool["parameters"] if p["name"] != "mote_offset"]
    next(p for p in tool["parameters"] if p["name"] == "slot_level")["minimum"] = 9
    tool["parameters"] += [
        _parameter("origin", "array", items={"type": "number"}, min_items=3, max_items=3),
        _parameter("plane"),
    ]
    tool["effects"][:0] = [
        _set("_storm_cast_turn", None),
        _when(
            "caster_state.turns_started != null",
            [_set("_storm_cast_turn", "caster_state.turns_started")],
        ),
    ]
    for effects in beam._lists(tool["effects"]):
        for effect in effects:
            row = effect.get("list_push", {}).get("value")
            if isinstance(row, dict) and row.get("kind") == SPELL:
                row.pop("mote_offset")
                row.update(
                    origin="origin",
                    plane="plane",
                    radius=300,
                    storm_ordinal=1,
                    last_caster_start="_storm_cast_turn",
                    later_effect_pending=False,
                )
    return tool


def _point_tool():
    tool = sphere._replace_names(
        bead._point_validator(),
        {
            "delayed_blast_origin": "storm_cloud_point",
            "_dbf_origin": "_storm_origin",
            "_dbf_plane": "_storm_plane",
            "_dbf_source_frame": "_storm_source_frame",
            "_dbf_chosen_plane": "_storm_chosen_plane",
        },
    )
    index = next(
        i
        for i, e in enumerate(tool["effects"])
        if e.get("set", {}).get("field") == "_sunburst_sphere_targets"
    )
    tool["effects"] = tool["effects"][:index]
    for effects in beam._lists(tool["effects"]):
        for effect in effects:
            require = effect.get("require")
            if require:
                require["condition"] = require["condition"].replace("150 ** 2", "5280 ** 2")
                require["error"] = (
                    require["error"]
                    .replace("Delayed Blast Fireball", "Storm of Vengeance")
                    .replace("beyond150 feet", "beyond1 mile")
                )
    next(p for p in tool["parameters"] if p["name"] == "_storm_chosen_plane")["model_hidden"] = True
    tool["description"] = "Qualify the actual chosen cloud altitude and one-mile source point."
    return tool


def _collector():
    """An actual scene-bounded vertical projection implements 'under' the chosen cloud."""
    return _tool_scene(
        "Collect supported creature Boxes actually below the saved300-foot cloud disk.",
        [_parameter("record", "object", from_state="state.turn_effects")],
        [
            _require(
                "record.kind == 'storm_of_vengeance' and record.radius == 300",
                "The actual Storm source owns the printed cloud radius.",
            ),
            _set("_storm_targets", []),
            _set("_storm_seen", []),
            _set("_storm_scene_rows", "state.spatial_scene.entities"),
            _loop(
                "_storm_scene_rows",
                "_storm_body",
                [
                    _when(
                        "_storm_body.active == true and "
                        "_storm_body.metadata.participant_ref != null and "
                        "_storm_body.metadata.is_object != true and "
                        "_storm_body.metadata.creature_type != 'spell_force' and "
                        "(_storm_body.metadata.plane == record.plane or "
                        "_storm_body.metadata.plane == null)",
                        [
                            _call(
                                "check_srd_cone_creature_actor",
                                {"target_state": "{_storm_body.metadata.participant_ref}"},
                                ("_cone_actual_creature",),
                            ),
                            _when(
                                "_cone_actual_creature == true",
                                [
                                    _require(
                                        "_storm_body.metadata.plane != null and "
                                        "_storm_body.footprint.kind == 'box'",
                                        "Storm currently needs actual creature Boxes and planes.",
                                    ),
                                    _require(
                                        "_storm_body.metadata.participant_ref not in _storm_seen",
                                        "Storm creature has ambiguous scene proxies.",
                                    ),
                                    _push("_storm_seen", "_storm_body.metadata.participant_ref"),
                                    _set(
                                        "_storm_near",
                                        sphere._nearest("_storm_body", "record.origin"),
                                    ),
                                    _when(
                                        "_storm_body.position[2] < record.origin[2] and "
                                        "(_storm_near[0] - record.origin[0]) ** 2 + "
                                        "(_storm_near[1] - record.origin[1]) ** 2 <= 300 ** 2",
                                        [
                                            _require(
                                                "len(_storm_targets) < 64",
                                                "Storm exceeds64 supported creatures.",
                                            ),
                                            _push(
                                                "_storm_targets",
                                                "_storm_body.metadata.participant_ref",
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


def _deaf_target():
    tool = _tool(
        "Resolve the authentic appearance save and link only its failed Deafened source.",
        [
            _entity("target"),
            _parameter("record", "object", from_state="state.turn_effects"),
            _parameter("damage_total", "integer", minimum=0),
        ],
        [
            _call(
                "resolve_spell_area_save_damage_target",
                {
                    "spell": SPELL,
                    "target": "{_target_id}",
                    "save_ability": "constitution",
                    "spell_save_dc": "{record.save_dc}",
                    "damage_total": "{damage_total}",
                    "damage_type": "thunder",
                    "success_damage": "none",
                    "construct_save_disadvantage": False,
                    "half_cover_targets": [],
                    "three_quarters_cover_targets": [],
                    "source_state_id": "{record.caster}",
                    "spell_cast_id": "{record.cast_id}",
                    "actual_spell_level": "{record.slot_level}",
                    "spell_cast_owner": "{record.caster}",
                },
                ("_spell_save_outcome", "_spell_summary_rows", "_spell_detail_rows"),
            ),
            _when(
                "_spell_save_outcome == 'failure'",
                [
                    _call(
                        "install_srd_timed_condition_source",
                        {
                            "target_state": "{_target_id}",
                            "lasting_state": "{_lasting_state_id}",
                            "caster_state": "{record.caster}",
                            "spell": SPELL,
                            "condition": "deafened",
                            "save_dc": "{record.save_dc}",
                            "duration_rounds": 10,
                            "repeat_save_timing": "none",
                            "save_ability": "constitution",
                            "spell_cast_id": "{record.cast_id}",
                            "actual_spell_level": "{record.slot_level}",
                            "spell_cast_owner": "{record.caster}",
                        },
                        ("_timed_condition_source_id",),
                    ),
                    _set(
                        "state.timed_condition_sources.{_timed_condition_source_id}.storm_source_id",
                        "record.source_id",
                    ),
                    _set(
                        "state.timed_condition_sources.{_timed_condition_source_id}.expires_tick",
                        "record.expires_tick",
                    ),
                    _set("_storm_deaf_ids", []),
                    _when(
                        "state.storm_deafened_source_ids != null",
                        [_set("_storm_deaf_ids", "state.storm_deafened_source_ids")],
                    ),
                    _push("_storm_deaf_ids", "_timed_condition_source_id"),
                    _set("state.storm_deafened_source_ids", "_storm_deaf_ids"),
                    _push(
                        "_spell_detail_rows",
                        {
                            "label": "'Effect'",
                            "value": "'Deafened for the original Storm duration; no repeat save.'",
                        },
                    ),
                ],
            ),
        ],
    )
    failed = next(
        e for e in tool["effects"] if e.get("condition") == "_spell_save_outcome == 'failure'"
    )
    failed["on_true"] = [
        _call(
            "check_srd_storm_of_vengeance_record",
            {"caster_state": "{record.caster}", "record": "{record}"},
            ("_storm_live",),
        ),
        _when("_storm_live == true", failed["on_true"]),
    ]
    return tool


def _retire_deaf():
    """Exact source IDs preserve independent/native Deafened while ending this Storm."""
    return _tool(
        "Retire only this original Storm's actual linked Deafened sources.",
        [_parameter("source_id")],
        [
            _set("_storm_deaf_ids", []),
            _set("_storm_deaf_kept", []),
            _when(
                "state.storm_deafened_source_ids != null",
                [_set("_storm_deaf_ids", "state.storm_deafened_source_ids")],
            ),
            _loop(
                "_storm_deaf_ids",
                "_storm_deaf_id",
                [
                    _set("_storm_deaf_row", "state.timed_condition_sources[_storm_deaf_id]"),
                    _when(
                        "_storm_deaf_row.installed == true and "
                        "_storm_deaf_row.spell_id == 'storm_of_vengeance'",
                        [
                            _when(
                                "_storm_deaf_row.storm_source_id == source_id",
                                [
                                    _call(
                                        "retire_srd_timed_condition_source",
                                        {
                                            "source_id": "{_storm_deaf_id}",
                                            "lasting_state": "{_lasting_state_id}",
                                        },
                                    ),
                                    _call(
                                        "refresh_srd_timed_condition_target",
                                        {
                                            "target_state": "{_storm_deaf_row.target_state_id}",
                                            "lasting_state": "{_lasting_state_id}",
                                        },
                                    ),
                                ],
                            )
                            | {"on_false": [_push("_storm_deaf_kept", "_storm_deaf_id")]},
                        ],
                    ),
                ],
            ),
            _set("state.storm_deafened_source_ids", "_storm_deaf_kept"),
        ],
    )


def _refresh_tool():
    tool = _rename(beam._rename(beam.retained._refresh_tool()))
    for effects in beam._lists(tool["effects"]):
        for effect in effects:
            if effect.get("condition") == "_storm_live == true":
                effect["on_false"] = [
                    _call("retire_srd_storm_deafened", {"source_id": "{_storm_record.source_id}"})
                ]
    return tool


def _retire_tool():
    tool = _rename(beam._rename(beam.retained._retire_tool()))
    tool["effects"].insert(0, _call("retire_srd_storm_deafened", {"source_id": "{source_id}"}))
    return tool


def _start_tool():
    """Actual caster starts count once; elapsed exploration rounds never fabricate turns."""
    return _tool(
        "Record this actual caster-start ordinal without inventing later storm consequences.",
        [_entity("caster_state"), _parameter("boundary_turn", "integer", minimum=0)],
        [
            _require(
                "caster_state.turns_started != null and "
                "caster_state.turns_started == boundary_turn",
                "Storm ordinal needs the creating caster's actual started turn.",
            ),
            _set("_storm_start_rows", "state.turn_effects"),
            _set("_storm_start_kept", []),
            _loop(
                "_storm_start_rows",
                "_storm_start_row",
                [
                    _when(
                        "_storm_start_row.kind == 'storm_of_vengeance' and "
                        "_storm_start_row.caster == _caster_state_id",
                        [
                            _call(
                                "check_srd_storm_of_vengeance_record",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "record": "{_storm_start_row}",
                                },
                                ("_storm_live",),
                            ),
                            _when(
                                "_storm_live == true and "
                                "(_storm_start_row.last_caster_start == null "
                                "or boundary_turn > _storm_start_row.last_caster_start)",
                                [
                                    _set("_storm_advance", 1),
                                    _when(
                                        "_storm_start_row.last_caster_start != null",
                                        [
                                            _set(
                                                "_storm_advance",
                                                "boundary_turn - "
                                                "_storm_start_row.last_caster_start",
                                            )
                                        ],
                                    ),
                                    _set(
                                        "_storm_start_change",
                                        {
                                            "last_caster_start": "boundary_turn",
                                            "storm_ordinal": (
                                                "_storm_start_row.storm_ordinal + _storm_advance"
                                            ),
                                            "later_effect_pending": True,
                                        },
                                    ),
                                    _set(
                                        "_storm_start_row",
                                        "merge(_storm_start_row, _storm_start_change)",
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _push("_storm_start_kept", "_storm_start_row"),
                ],
            ),
            _set("state.turn_effects", "_storm_start_kept"),
        ],
    )


def _cast_tool():
    return _tool(
        "Install the genuine paid cloud and resolve its one Thunder appearance event.",
        [
            _entity("caster_state"),
            _parameter("slot_level", "integer", minimum=9, maximum=9),
            _parameter("save_dc", "integer", minimum=1, maximum=40),
            _parameter(
                "casting_ability", "enum", enum_values=["intelligence", "wisdom", "charisma"]
            ),
            _parameter("concentration_save_modifier", "integer"),
            _parameter("spell_cast_id"),
            _intent(),
        ],
        [
            _call(
                "qualify_srd_storm_cloud_point",
                {"caster_state": "{_caster_state_id}", "storm_cloud_point": "{storm_cloud_point}"},
                ("_storm_origin", "_storm_plane"),
            ),
            _call(
                "install_srd_storm_of_vengeance",
                {
                    "caster_state": "{_caster_state_id}",
                    "slot_level": "{slot_level}",
                    "save_dc": "{save_dc}",
                    "casting_ability": "{casting_ability}",
                    "concentration_save_modifier": "{concentration_save_modifier}",
                    "spell_cast_id": "{spell_cast_id}",
                    "origin": "{_storm_origin}",
                    "plane": "{_storm_plane}",
                },
                ("_storm_source_id",),
            ),
            _set("_storm_record", None),
            _set("_storm_rows", "state.turn_effects"),
            _loop(
                "_storm_rows",
                "_storm_row",
                [
                    _when(
                        "_storm_row.kind == 'storm_of_vengeance' and "
                        "_storm_row.source_id == _storm_source_id",
                        [_set("_storm_record", "_storm_row")],
                    ),
                ],
            ),
            _require("_storm_record != null", "The actual paid Storm source must exist."),
            _call(
                "collect_srd_storm_creature_targets",
                {"record": "{_storm_record}"},
                ("_storm_targets",),
            ),
            _call(
                "collect_srd_storm_cover_targets",
                {
                    "source_state": "{_caster_state_id}",
                    "origin": "{_storm_record.origin}",
                    "area_plane": "{_storm_record.plane}",
                    "targets": "{_storm_targets}",
                },
                ("_srd_area_targets",),
            ),
            {"dice": {"expression": "2d6"}, "store_as": "_storm_damage_roll"},
            _call(
                "modify_srd_actor_damage_roll",
                {
                    "actor_state": "{_caster_state_id}",
                    "amount": "{_storm_damage_roll.total}",
                    "lasting_state": "{_lasting_state_id}",
                },
                ("_actor_modified_damage",),
            ),
            _set("_storm_damage_total", "_actor_modified_damage"),
            _set("_storm_summary", []),
            _set("_storm_details", []),
            _loop(
                "_srd_area_targets",
                "_storm_target",
                [
                    _call(
                        "resolve_srd_storm_appearance_target",
                        {
                            "target": "{_storm_target}",
                            "record": "{_storm_record}",
                            "damage_total": "{_storm_damage_total}",
                        },
                        ("_spell_summary_rows", "_spell_detail_rows"),
                    ),
                    _loop(
                        "_spell_summary_rows",
                        "_storm_display_row",
                        [_push("_storm_summary", "_storm_display_row")],
                    ),
                    _loop(
                        "_spell_detail_rows",
                        "_storm_display_row",
                        [_push("_storm_details", "_storm_display_row")],
                    ),
                ],
            ),
            _set("_spell_summary_rows", "_storm_summary"),
            _set("_spell_detail_rows", "_storm_details"),
        ],
    )


def bind_runtime_consumers(text):
    """Root owns common admission; this family binds only paid dispatch and exact lifecycle."""
    tools, changed = _load_runtime(text)["tools"], {}
    names = {
        "collect_srd_sunburst_cover_targets": "collect_srd_storm_cover_targets",
        "qualify_srd_sunburst_target_cover": "qualify_srd_storm_target_cover",
    }
    cover = {
        names[name]: sphere._replace_names(tool, names)
        for name, tool in sphere._point_cover(tools).items()
    }
    collection = cover["collect_srd_storm_cover_targets"]
    collection["parameters"].append(_parameter("area_plane"))
    for effects in beam._lists(collection["effects"]):
        for effect in effects:
            if effect.get("set", {}).get("field") == "_breath_source_plane":
                effect["set"]["value"] = "area_plane"
    generated = {
        "qualify_srd_storm_cloud_point": _point_tool(),
        "install_srd_storm_of_vengeance": _installer(),
        "check_srd_storm_of_vengeance_record": _rename(beam._rename(beam.retained._record_tool())),
        "collect_srd_storm_creature_targets": _collector(),
        "resolve_srd_storm_appearance_target": _deaf_target(),
        "retire_srd_storm_deafened": _retire_deaf(),
        REFRESH: _refresh_tool(),
        "retire_srd_storm_of_vengeance_source": _retire_tool(),
        "finish_srd_storm_of_vengeance_removal": _rename(
            beam._rename(beam.retained._finish_removal())
        ),
        START: _start_tool(),
        "cast_srd_storm_of_vengeance": _cast_tool(),
        **cover,
    }
    text = _replace_tools(text, {n: t for n, t in generated.items() if tools.get(n) != t})
    tools = _load_runtime(text)["tools"]
    for name in ("cast_spell", "cast_spell_outside_combat"):
        tool = deepcopy(tools[name])
        if "storm_cloud_point" not in {p["name"] for p in tool["parameters"]}:
            tool["parameters"].append(_intent())
        calls = {e.get("call") for seq in beam._lists(tool["effects"]) for e in seq}
        if "cast_srd_storm_of_vengeance" not in calls and "cast_spell" not in calls:
            index = next(
                i
                for i, e in enumerate(tool["effects"])
                if e.get("set", {}).get("field") == "_card_slots_after"
            )
            tool["effects"].insert(
                index,
                _when(
                    "_spell_cast_failed != true and _pending_spell_operation_lost != true and "
                    "_spell_definition.family == 'storm_of_vengeance'",
                    [
                        {
                            "call": "cast_srd_storm_of_vengeance",
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
                                "storm_cloud_point": "{storm_cloud_point}",
                            },
                        }
                    ],
                ),
            )
        if tool != tools[name]:
            changed[name] = tool
    tool = deepcopy(tools["end_srd_concentration_node"])
    if "retire_srd_storm_of_vengeance_source" not in str(tool["effects"]):
        tool["effects"].insert(
            0,
            _when(
                "source_state.concentration_effect_family == 'storm_of_vengeance' and "
                "source_state.storm_of_vengeance_source_id != null",
                [
                    _call(
                        "retire_srd_storm_of_vengeance_source",
                        {"source_id": "{source_state.storm_of_vengeance_source_id}"},
                    )
                ],
            ),
        )
        changed["end_srd_concentration_node"] = tool
    for name in ("advance_srd_lasting_clock", "process_srd_turn_effects"):
        tool = deepcopy(tools[name])
        if REFRESH in str(tool["effects"]):
            continue
        guard = _when(
            "lasting_state.has_storm_sources == true",
            [
                _call(REFRESH, {"lasting_state": "{_lasting_state_id}"}),
            ],
        )
        if name == "advance_srd_lasting_clock":
            index = (
                next(
                    i
                    for i, e in enumerate(tool["effects"])
                    if e.get("set", {}).get("field") == "lasting_state.lasting_tick"
                )
                + 1
            )
            tool["effects"].insert(index, guard)
        else:
            tool["effects"][:0] = [
                guard,
                _when(
                    "lasting_state.has_storm_sources == true and boundary_phase == 'start' and "
                    "combat.encounter_active == true",
                    [
                        _call(
                            START,
                            {
                                "caster_state": "{boundary_actor}",
                                "boundary_turn": "{boundary_turn}",
                                "lasting_state": "{_lasting_state_id}",
                            },
                        )
                    ],
                ),
            ]
        changed[name] = tool
    return _replace_tools(text, changed) if changed else text
