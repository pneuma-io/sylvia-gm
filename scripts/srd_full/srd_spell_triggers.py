"""Saved wards separate admitted fictional triggers from actual geometry and consequences."""

from copy import deepcopy

import yaml

if __package__:
    from .srd_d20_normalization import _native_conditions
    from .srd_scene_relations import POSITION_EXPORTS, _array, _snapshot, _tool_scene
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
    from .srd_timed_senses import _loop, _optional, _public, _push, _replace_tools
else:
    from srd_d20_normalization import _native_conditions
    from srd_scene_relations import POSITION_EXPORTS, _array, _snapshot, _tool_scene
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
    from srd_timed_senses import _loop, _optional, _public, _push, _replace_tools


SOURCE = {
    "alarm": (107, "Alarm", "Alter Self"),
    "contingency": (119, "Contingency", "Continual Flame"),
    "glyph_of_warding": ([136, 137], "Glyph of Warding", "Goodberry"),
    "magic_mouth": (146, "Magic Mouth", "Magic Weapon"),
    "symbol": (167, "Symbol", "Telekinesis"),
}
PROFILES = {
    "alarm": {
        "family": "trigger_spell",
        "base_level": 1,
        "casting_time": "1_minute",
        "range_feet": 30,
        "effect_rounds": 4800,
        "concentration": False,
    },
    "magic_mouth": {
        "family": "trigger_spell",
        "base_level": 2,
        "casting_time": "1_minute",
        "range_feet": 30,
        "effect_rounds": 0,
        "concentration": False,
    },
    "glyph_of_warding": {
        "family": "trigger_spell",
        "base_level": 3,
        "casting_time": "1_hour",
        "range_feet": 5,
        "effect_rounds": 0,
        "concentration": False,
    },
    "symbol": {
        "family": "trigger_spell",
        "base_level": 7,
        "casting_time": "1_minute",
        "range_feet": 5,
        "effect_rounds": 0,
        "concentration": False,
    },
}
PUBLIC_TOOLS = {"resolve_srd_spell_trigger", "notice_srd_spell_glyph"}
EVENTS = ["touch", "enter", "step", "uncover", "approach", "open", "see", "visual", "audible"]
CHOICES = [
    _optional("trigger_anchor", default=""),
    _optional(
        "trigger_anchor_kind",
        "enum",
        "area",
        enum_values=["area", "door", "window", "surface", "object"],
    ),
    _array("trigger_position", default=[0, 0, 0]),
    _array("trigger_extent", default=[20, 20, 20]),
    _optional("trigger_description", default="A creature enters or touches the ward."),
    _optional(
        "trigger_events",
        "array",
        ["touch", "enter"],
        items={"type": "string", "enum": EVENTS},
        min_items=1,
        max_items=9,
        unique_items=True,
    ),
    _optional(
        "trigger_exempt_creatures",
        "entity_id_list",
        [],
        resolve="entity_list",
        max_items=64,
        unique_items=True,
    ),
    _optional(
        "trigger_creature_types",
        "array",
        [],
        items={"type": "string"},
        max_items=14,
        unique_items=True,
    ),
    _optional("trigger_password", default=""),
    _optional("trigger_approach_feet", "number", 5, minimum=0, maximum=30000),
    _optional("alarm_mode", "enum", "mental", enum_values=["mental", "audible"]),
    _optional(
        "mouth_words",
        "array",
        [],
        items={"type": "string", "minLength": 1},
        max_items=25,
    ),
    _optional("mouth_delivery_seconds", "integer", 1, minimum=1, maximum=600),
    _optional("mouth_repeat", "boolean", False),
    _optional(
        "glyph_damage_type",
        "enum",
        "fire",
        enum_values=["acid", "cold", "fire", "lightning", "thunder"],
    ),
    _optional("glyph_diameter", "number", 10, minimum=0.000001, maximum=10),
    _optional(
        "symbol_effect",
        "enum",
        "death",
        enum_values=["death", "discord", "fear", "pain", "sleep", "stunning"],
    ),
]
RECORD_EXPORTS = ("_trigger_active", "_trigger_origin", "_trigger_plane", "_trigger_retirement")


def validate_profiles(definitions):
    """Promote only reviewed independent leaves; stored-spell releases remain separate."""
    for spell, definition in definitions.items():
        if definition.get("family") == "trigger_spell" and definition != PROFILES.get(spell):
            raise SystemExit(f"Trigger source profile differs: {spell}")


def _schema():
    properties = {
        "base_level": {"type": "integer", "enum": [1, 2, 3, 7]},
        "casting_time": {"type": "string", "enum": ["1_minute", "1_hour"]},
        "range_feet": {"type": "integer", "enum": [5, 30]},
        "effect_rounds": {"type": "integer", "enum": [0, 4800]},
        "concentration": {"type": "boolean", "enum": [False]},
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _find():
    return [
        _set("_trigger_record", None),
        _set("_trigger_records", []),
        _when("state.has_spell_triggers == true", [_set("_trigger_records", "state.turn_effects")]),
        _loop(
            "_trigger_records",
            "_trigger_candidate",
            [
                _when(
                    (
                        "_trigger_candidate.kind == 'spell_trigger' and _trigger_candidate.insta"
                        "nce == trigger_id"
                    ),
                    [
                        _require("_trigger_record == null", "Saved trigger identity is ambiguous."),
                        _set("_trigger_record", "_trigger_candidate"),
                    ],
                ),
            ],
        ),
        _require("_trigger_record != null", "No saved spell trigger has this identity."),
    ]


def _current_anchor(subject, *, require_plane=True):
    """Resolve an actual anchor and follow its admitted carried/worn holder."""
    effects = [
        _call("srd_information_scene_entity", {"subject": subject}, POSITION_EXPORTS),
        _set("_trigger_origin", "_info_position"),
        _set("_trigger_plane", None),
        _when(
            "_info_scene_entity.metadata.plane != null",
            [
                _set("_trigger_plane", "_info_scene_entity.metadata.plane"),
            ],
        ),
        _set("_trigger_anchor_entity", "_info_scene_entity"),
        _set("_trigger_holder", None),
        _when(
            "_info_scene_entity.metadata.carried_by != null",
            [
                _set("_trigger_holder", "_info_scene_entity.metadata.carried_by"),
            ],
        ),
        _when(
            "(_trigger_holder == null or _trigger_holder == '') and "
            "_info_scene_entity.metadata.worn_by != null",
            [
                _set("_trigger_holder", "_info_scene_entity.metadata.worn_by"),
            ],
        ),
        _when(
            "_trigger_holder != null and _trigger_holder != ''",
            [
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_trigger_holder}"},
                    POSITION_EXPORTS,
                ),
                _set("_trigger_origin", "_info_position"),
                _set("_trigger_plane", None),
                _when(
                    "_info_scene_entity.metadata.plane != null",
                    [
                        _set("_trigger_plane", "_info_scene_entity.metadata.plane"),
                    ],
                ),
            ],
        ),
    ]
    if require_plane:
        effects.append(
            _require("_trigger_plane != null", "The trigger anchor needs its actual plane.")
        )
    return effects


def _anchor_liveness(anchor="record.anchor"):
    """Retire absent admitted anchors or carriers before any required scene lookup."""
    return [
        _set("_trigger_anchor_live", False),
        _set("_trigger_live_holder", None),
        _set("_trigger_scene_rows", "state.spatial_scene.entities"),
        _loop(
            "_trigger_scene_rows",
            "_trigger_scene_row",
            [
                _when(
                    "_trigger_scene_row.active == true and (_trigger_scene_row.id == "
                    f"{anchor} or _trigger_scene_row.metadata.participant_ref == {anchor})",
                    [
                        _set("_trigger_anchor_live", True),
                        _when(
                            "_trigger_scene_row.metadata.carried_by != null and "
                            "_trigger_scene_row.metadata.carried_by != ''",
                            [
                                _set(
                                    "_trigger_live_holder", "_trigger_scene_row.metadata.carried_by"
                                ),
                            ],
                        ),
                        _when(
                            "_trigger_live_holder == null and "
                            "_trigger_scene_row.metadata.worn_by != null and "
                            "_trigger_scene_row.metadata.worn_by != ''",
                            [
                                _set("_trigger_live_holder", "_trigger_scene_row.metadata.worn_by"),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        _when(
            "_trigger_anchor_live != true",
            [
                _set("_trigger_active", False),
                _set("_trigger_retirement", "'anchor no longer exists in the admitted scene'"),
            ],
        ),
        _when(
            "_trigger_anchor_live == true and _trigger_live_holder != null",
            [
                _set("_trigger_holder_live", False),
                _loop(
                    "_trigger_scene_rows",
                    "_trigger_holder_row",
                    [
                        _when(
                            "_trigger_holder_row.active == true and (_trigger_holder_row.id == "
                            "_trigger_live_holder or "
                            "_trigger_holder_row.metadata.participant_ref == _trigger_live_holder)",
                            [
                                _set("_trigger_holder_live", True),
                            ],
                        ),
                    ],
                ),
                _when(
                    "_trigger_holder_live != true",
                    [
                        _set("_trigger_active", False),
                        _set(
                            "_trigger_retirement", "'holder no longer exists in the admitted scene'"
                        ),
                    ],
                ),
            ],
        ),
    ]


def _update_record(patch_field):
    """Replace one immutable source row without losing unrelated turn effects."""
    return [
        _set("_trigger_records", "state.turn_effects"),
        _set("_trigger_kept", []),
        _loop(
            "_trigger_records",
            "_trigger_update",
            [
                _when(
                    (
                        "_trigger_update.kind == 'spell_trigger' and "
                        "_trigger_update.instance == trigger_id"
                    ),
                    [_set("_trigger_update", f"merge(_trigger_update, {patch_field})")],
                ),
                _push("_trigger_kept", "_trigger_update"),
            ],
        ),
        _set("state.turn_effects", "_trigger_kept"),
    ]


def _record_tools():
    check = _tool_scene(
        "Resolve source expiry and actual anchor movement before a saved ward can fire.",
        [_parameter("record", "object", from_state="state.turn_effects")],
        [
            _set(
                "_trigger_active",
                "record.expires_tick == null or state.lasting_tick < record.expires_tick",
            ),
            _when(
                "record.expires_seconds != null",
                [
                    _set(
                        "_trigger_active",
                        (
                            "_trigger_active == true and state.lasting_tick * 3 "
                            "< record.expires_seconds"
                        ),
                    ),
                ],
            ),
            _set("_trigger_retirement", "'expired'"),
            _set("_trigger_origin", "record.origin"),
            _set("_trigger_plane", "record.plane"),
            _when("_trigger_active == true and record.anchor != ''", _anchor_liveness()),
            _when(
                "_trigger_active == true and record.anchor != ''",
                _current_anchor("{record.anchor}")
                + [
                    _when(
                        (
                            "record.spell == 'glyph_of_warding' or (record.spell == 'symbol' and"
                            " record.anchor_kind == 'object')"
                        ),
                        [
                            _call(
                                "measure_srd_information_distance",
                                {"start": "{record.origin}", "end": "{_trigger_origin}"},
                                ["_scene_distance"],
                            ),
                            _set(
                                "_trigger_active",
                                "_scene_distance <= 10 and _trigger_plane == record.plane",
                            ),
                            _set(
                                "_trigger_retirement",
                                "'moved more than 10 feet from its creation location'",
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    retire = _tool(
        "Remove exactly one spent or broken trigger source.",
        [_parameter("trigger_id", "integer", minimum=1)],
        [
            _set("_trigger_kept", []),
            _set("_trigger_records", "state.turn_effects"),
            _loop(
                "_trigger_records",
                "_trigger_remove",
                [
                    _when(
                        (
                            "_trigger_remove.kind != 'spell_trigger' or _trigger_remove.instance"
                            " != trigger_id"
                        ),
                        [
                            _push("_trigger_kept", "_trigger_remove"),
                        ],
                    ),
                ],
            ),
            _set("state.turn_effects", "_trigger_kept"),
            _set("state.has_spell_triggers", False),
            _set("state.has_active_symbol_auras", False),
            _loop(
                "_trigger_kept",
                "_trigger_remaining",
                [
                    _when(
                        "_trigger_remaining.kind == 'spell_trigger'",
                        [_set("state.has_spell_triggers", True)],
                    ),
                    _when(
                        "_trigger_remaining.kind == 'spell_trigger' and "
                        "_trigger_remaining.spell == 'symbol' and "
                        "_trigger_remaining.activated_tick != null",
                        [
                            _set("state.has_active_symbol_auras", True),
                        ],
                    ),
                ],
            ),
        ],
    )
    refresh = _tool(
        "Retire expired or moved wards immediately after actual scene movement.",
        [],
        [
            _when(
                "state.has_spell_triggers == true",
                [
                    _set("_trigger_refresh_live", False),
                    _set("_symbol_refresh_live", False),
                    _set("_trigger_refresh_records", []),
                    _when(
                        "state.turn_effects != null",
                        [_set("_trigger_refresh_records", "state.turn_effects")],
                    ),
                    _loop(
                        "_trigger_refresh_records",
                        "_trigger_refresh_record",
                        [
                            _when(
                                "_trigger_refresh_record.kind == 'spell_trigger'",
                                [
                                    _call(
                                        "check_srd_spell_trigger_record",
                                        {"record": "{_trigger_refresh_record}"},
                                        RECORD_EXPORTS,
                                    ),
                                    _when(
                                        "_trigger_active == true",
                                        [
                                            _set("_trigger_refresh_live", True),
                                            _when(
                                                "_trigger_refresh_record.spell == 'symbol' and "
                                                "_trigger_refresh_record.activated_tick != null",
                                                [_set("_symbol_refresh_live", True)],
                                            ),
                                        ],
                                    ),
                                    _when(
                                        "_trigger_active != true",
                                        [
                                            _set("_refresh_id", "_trigger_refresh_record.instance"),
                                            _call(
                                                "retire_srd_spell_trigger",
                                                {"trigger_id": "{_refresh_id}"},
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _set("state.has_spell_triggers", "_trigger_refresh_live"),
                    _set("state.has_active_symbol_auras", "_symbol_refresh_live"),
                ],
            ),
        ],
    )
    return {
        "check_srd_spell_trigger_record": check,
        "retire_srd_spell_trigger": retire,
        "refresh_srd_spell_triggers": refresh,
    }


def _mouth_visibility():
    """Mouth creation uses actual object concealment, light, Fog and observer senses."""
    return [
        _set("_trigger_sight_distance", "ceil(_trigger_cast_distance) + 0"),
        _call(
            "check_srd_illumination_object_visibility",
            {"object_id": "{trigger_anchor}", "observer_state": "{_caster_state_id}"},
            ["_illumination_object_invisible", "_illumination_object_lighting"],
        ),
        _require(
            "_illumination_object_lighting != null",
            "The Mouth object needs actual ambient lighting.",
        ),
        _call(
            "resolve_srd_information_light",
            {
                "point": "{_trigger_origin}",
                "plane": "{_trigger_plane}",
                "ambient": "{_illumination_object_lighting}",
            },
            ["_information_light"],
        ),
        _call(
            "srd_information_barriers",
            {"start": "{_trigger_caster_origin}", "end": "{_trigger_origin}", "rule": "solid"},
            ["_information_blocked"],
        ),
        _set("_trigger_obstruction", "'clear'"),
        _when("_information_blocked == true", [_set("_trigger_obstruction", "'solid'")]),
        _call(
            "resolve_srd_environment_segment",
            {
                "start": "{_trigger_caster_origin}",
                "end": "{_trigger_origin}",
                "plane": "{_trigger_plane}",
            },
            ["_environment_sight_obscured"],
        ),
        _when(
            "_trigger_obstruction == 'clear' and _environment_sight_obscured == true",
            [_set("_trigger_obstruction", "'heavy_obscurement'")],
        ),
        _call(
            "resolve_srd_sight",
            {
                "observer_state": "{_caster_state_id}",
                "distance": "{_trigger_sight_distance}",
                "lighting": "{_information_light}",
                "obstruction": "{_trigger_obstruction}",
                "subject_invisible": "{_illumination_object_invisible}",
            },
            ["_sight_can_see"],
        ),
        _require(
            "_sight_can_see == true", "Magic Mouth requires an object the caster can actually see."
        ),
    ]


def _word_conditions():
    """Count actual spoken words without allowing embedded Unicode whitespace to hide words."""
    whitespace = " \t\n\r\v\f\x1c\x1d\x1e\x1f\x85\xa0\u1680"
    whitespace += "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    whitespace += "\u2028\u2029\u202f\u205f\u3000"
    error = "Supply each spoken word separately, without whitespace."
    return [_require("len(_mouth_word) > 0 and len(_mouth_word) <= 120", error)] + [
        _require(
            " and ".join("'" + char + "' not in _mouth_word" for char in whitespace[i : i + 10]),
            error,
        )
        for i in range(0, len(whitespace), 10)
    ]


def _install_tool():
    """This private completion owner receives source stats only from the paid root cast."""
    return _tool_scene(
        "Install a validated, paid Alarm, Magic Mouth or explosive Glyph; no second casting costs.",
        [
            _entity("caster_state"),
            _parameter("spell"),
            _parameter("slot_level", "integer"),
            _parameter("save_dc", "integer"),
            *deepcopy(CHOICES),
        ],
        [
            _require(
                "spell == 'alarm' or spell == 'magic_mouth' or spell == 'glyph_of_warding' or "
                "spell == 'symbol'",
                ("This trigger completion supports Alarm, Mouth, explosive Glyph and Symbol."),
            ),
            {
                "table_lookup": {"table": "definitions.spells", "key": "spell", "required": True},
                "store_as": "_trigger_profile",
            },
            _require(
                "slot_level >= _trigger_profile.base_level and slot_level <= 9",
                "The actual trigger spell slot is outside its source level.",
            ),
            _require(
                "save_dc >= 1 and save_dc <= 40", "Retain the validated source spell save DC."
            ),
            _require(
                "len(trigger_description) > 0 and len(trigger_description) <= 2000",
                "Describe the fictional trigger in at most 2000 characters.",
            ),
            _require(
                "len(trigger_events) > 0 and len(trigger_events) <= 9",
                "Choose at least one supported trigger event.",
            ),
            _call(
                "srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS
            ),
            _set("_trigger_caster_origin", "_info_position"),
            _set("_trigger_caster_plane", "_info_scene_entity.metadata.plane"),
            _set("_trigger_origin", "trigger_position"),
            _set("_trigger_plane", "_trigger_caster_plane"),
            _when("trigger_anchor != ''", _current_anchor("{trigger_anchor}")),
            _require(
                "_trigger_plane != null and _trigger_plane == _trigger_caster_plane",
                "Create the ward on the caster's actual plane.",
            ),
            _call(
                "measure_srd_information_distance",
                {"start": "{_trigger_caster_origin}", "end": "{_trigger_origin}"},
                ["_scene_distance"],
            ),
            _set("_trigger_cast_distance", "_scene_distance"),
            _require(
                "_trigger_cast_distance <= _trigger_profile.range_feet",
                "The actual trigger anchor is outside this spell's range.",
            ),
            _when(
                "spell == 'alarm'",
                [
                    _require(
                        (
                            "trigger_anchor_kind == 'area' or trigger_anchor_kind == 'door' or t"
                            "rigger_anchor_kind == 'window'"
                        ),
                        "Alarm wards an area, door or window.",
                    ),
                    _require(
                        (
                            "(trigger_anchor_kind == 'area' and trigger_anchor == '') or (trigge"
                            "r_anchor_kind != 'area' and trigger_anchor != '')"
                        ),
                        "An Alarm door or window needs its actual scene anchor.",
                    ),
                    *[
                        _require(
                            f"trigger_extent[{a}] > 0 and trigger_extent[{a}] <= 20",
                            "Alarm's ward cannot exceed a 20-foot Cube.",
                        )
                        for a in range(3)
                    ],
                    _loop(
                        "trigger_events",
                        "_trigger_event_choice",
                        [
                            _require(
                                (
                                    "_trigger_event_choice == 'touch' or _trigger_event_choice ="
                                    "= 'enter'"
                                ),
                                "Alarm responds to creature touching or entering.",
                            )
                        ],
                    ),
                ],
            ),
            _when(
                "spell == 'magic_mouth'",
                [
                    _require(
                        (
                            "trigger_anchor != '' and trigger_anchor_kind == 'object' and _trigg"
                            "er_anchor_entity.metadata.is_object == true"
                        ),
                        "Magic Mouth needs its actual object.",
                    ),
                    _require(
                        (
                            "_trigger_holder == null or _trigger_holder == '' or _trigger_holder"
                            " == _caster_state_id"
                        ),
                        "Magic Mouth cannot target an object worn or carried by another creature.",
                    ),
                    *_mouth_visibility(),
                    _require(
                        "len(mouth_words) >= 1 and len(mouth_words) <= 25",
                        "Magic Mouth speaks at most 25 words.",
                    ),
                    _loop(
                        "mouth_words",
                        "_mouth_word",
                        _word_conditions(),
                    ),
                    _require(
                        "mouth_delivery_seconds >= 1 and mouth_delivery_seconds <= 600",
                        "Magic Mouth's chosen delivery lasts at most 10 minutes.",
                    ),
                    _loop(
                        "trigger_events",
                        "_trigger_event_choice",
                        [
                            _require(
                                (
                                    "_trigger_event_choice == 'visual' or _trigger_event_choice "
                                    "== 'audible'"
                                ),
                                "Magic Mouth's trigger is visual or audible.",
                            )
                        ],
                    ),
                ],
            ),
            _when(
                "spell == 'glyph_of_warding' or spell == 'symbol'",
                [
                    _require(
                        (
                            "trigger_anchor != '' and (trigger_anchor_kind == 'surface' or trigg"
                            "er_anchor_kind == 'object')"
                        ),
                        "Glyph needs its actual surface or closable object.",
                    ),
                    _when(
                        "trigger_anchor_kind == 'object'",
                        [
                            _require(
                                (
                                    "_trigger_anchor_entity.metadata.is_object == true and _trig"
                                    "ger_anchor_entity.metadata.closable == true"
                                ),
                                "A contained Glyph needs an admitted closable object.",
                            )
                        ],
                    ),
                    _require(
                        ("glyph_diameter > 0 and glyph_diameter <= 10"),
                        ("The glyph occupies at most a 10-foot diameter area."),
                    ),
                    _loop(
                        "trigger_events",
                        "_trigger_event_choice",
                        [
                            _require(
                                (
                                    "_trigger_event_choice != 'visual' and _trigger_event_choice"
                                    " != 'audible' and _trigger_event_choice != 'enter'"
                                ),
                                "Choose a supported surface or contained Glyph trigger.",
                            )
                        ],
                    ),
                ],
            ),
            _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
            _when("state.turn_effects == null", [_set("state.turn_effects", [])]),
            _when(
                "state.spell_trigger_sequence == null", [_set("state.spell_trigger_sequence", 0)]
            ),
            _set("state.spell_trigger_sequence", "state.spell_trigger_sequence + 1"),
            _set("_trigger_id", "state.spell_trigger_sequence"),
            _set("_trigger_expiry", None),
            _when("spell == 'alarm'", [_set("_trigger_expiry", "state.lasting_tick + 9600")]),
            _set("_trigger_records", "state.turn_effects"),
            _set(
                "_trigger_new",
                {
                    "kind": "'spell_trigger'",
                    "spell": "spell",
                    "caster": "_caster_state_id",
                    "target": "_caster_state_id",
                    "instance": "_trigger_id",
                    "slot_level": "slot_level",
                    "save_dc": "save_dc",
                    "started_tick": "state.lasting_tick",
                    "expires_tick": None,
                    "concentration": False,
                    "anchor": "trigger_anchor",
                    "source": "trigger_anchor",
                    "anchor_kind": "trigger_anchor_kind",
                    "origin": "_trigger_origin",
                    "plane": "_trigger_plane",
                    "extent": "trigger_extent",
                    "description": "trigger_description",
                    "events": "trigger_events",
                    "exempt_creatures": "trigger_exempt_creatures",
                    "creature_types": "trigger_creature_types",
                    "password": "trigger_password",
                    "approach_feet": "trigger_approach_feet",
                    "alarm_mode": "alarm_mode",
                    "words": "mouth_words",
                    "delivery_seconds": "mouth_delivery_seconds",
                    "repeat": "mouth_repeat",
                    "damage_type": "glyph_damage_type",
                    "glyph_diameter": "glyph_diameter",
                    "symbol_effect": "symbol_effect",
                    "activated_tick": None,
                    "symbol_ends_tick": None,
                    "symbol_turn": -1,
                    "symbol_targets": [],
                    "last_delivery_ends_seconds": None,
                    "expires_seconds": None,
                    "last_sound_ends_seconds": None,
                    "last_sound_origin": None,
                    "last_sound_plane": None,
                },
            ),
            _when(
                "spell == 'alarm'",
                [
                    _set("_trigger_expiry_patch", {"expires_tick": "_trigger_expiry"}),
                    _set("_trigger_new", "merge(_trigger_new, _trigger_expiry_patch)"),
                ],
            ),
            _push("_trigger_records", "_trigger_new"),
            _set("state.turn_effects", "_trigger_records"),
            _set("state.has_spell_triggers", True),
            _set("_spell_summary_rows", [{"label": "'Ward'", "value": "'Saved trigger armed.'"}]),
            _set("_spell_detail_rows", [{"label": "'Trigger'", "value": "trigger_description"}]),
        ],
    )


def _sphere_tool():
    """Use actual box or circular creature volumes, including exact sphere boundaries."""
    return _tool_scene(
        "Intersect an admitted creature's actual volume with a fixed source Sphere.",
        [
            _parameter("candidate", "object", from_state="state.spatial_scene"),
            _array("origin"),
            _parameter("radius", "integer"),
        ],
        [
            _require(
                "candidate.footprint.kind == 'box' or candidate.footprint.kind == 'circle'",
                "The ward needs an admitted box or circular footprint.",
            ),
            _set(
                "_trigger_near_z",
                (
                    "max(0, candidate.position[2] - origin[2], origin[2] - candidate.position[2]"
                    " - candidate.height)"
                ),
            ),
            _when(
                "candidate.footprint.kind == 'box'",
                [
                    _set(
                        "_trigger_horizontal_squared",
                        (
                            "max(0, abs(candidate.position[0] - origin[0]) - candidate.footprint"
                            ".half_extents[0]) ** 2 + max(0, abs(candidate.position[1] - origin["
                            "1]) - candidate.footprint.half_extents[1]) ** 2"
                        ),
                    ),
                ],
            ),
            _when(
                "candidate.footprint.kind == 'circle'",
                [
                    _set(
                        "_trigger_horizontal_squared",
                        (
                            "max(0, ((candidate.position[0] - origin[0]) ** 2 + (candidate.posit"
                            "ion[1] - origin[1]) ** 2) ** 0.5 - candidate.footprint.radius) ** 2"
                        ),
                    ),
                ],
            ),
            _set(
                "_trigger_sphere_intersects",
                "_trigger_horizontal_squared + _trigger_near_z ** 2 <= radius ** 2",
            ),
        ],
    )


def _cube_tool():
    """Touching or entering a ward uses the creature's actual volume, including its edges."""
    return _tool_scene(
        "Intersect the saved Alarm Cube and an actual creature volume.",
        [
            _parameter("candidate", "object", from_state="state.spatial_scene"),
            _array("origin"),
            _array("extent"),
        ],
        [
            _require(
                "candidate.footprint.kind == 'box' or candidate.footprint.kind == 'circle'",
                "The ward needs an admitted box or circular footprint.",
            ),
            _set(
                "_trigger_cube_intersects",
                "candidate.position[2] <= origin[2] + extent[2] * 0.5 and "
                "candidate.position[2] + candidate.height >= origin[2] - extent[2] * 0.5",
            ),
            _when(
                "candidate.footprint.kind == 'box'",
                [
                    *[
                        _set(
                            "_trigger_cube_intersects",
                            "_trigger_cube_intersects == true and "
                            f"abs(candidate.position[{axis}] - origin[{axis}]) <= "
                            f"extent[{axis}] * 0.5 + candidate.footprint.half_extents[{axis}]",
                        )
                        for axis in (0, 1)
                    ],
                ],
            ),
            _when(
                "candidate.footprint.kind == 'circle'",
                [
                    _set(
                        "_trigger_cube_intersects",
                        "_trigger_cube_intersects == true and "
                        "max(0, abs(candidate.position[0] - origin[0]) - extent[0] * 0.5) ** 2 "
                        "+ max(0, abs(candidate.position[1] - origin[1]) - extent[1] * 0.5) ** 2 "
                        "<= candidate.footprint.radius ** 2",
                    ),
                ],
            ),
        ],
    )


def _symbol_save_tool(runtime):
    """Use the established condition save owner with Symbol's printed save ability."""
    tool = deepcopy(runtime["tools"]["resolve_srd_control_behavior_save"])
    tool["description"] = "Roll Symbol's retained DC with the actual saved target modifier."
    tool["parameters"].append(_parameter("ability", "enum", enum_values=["wisdom", "constitution"]))
    tool["effects"][:0] = [
        _set("_symbol_save_modifier", "target_state.saving_throw_modifiers[ability]"),
        _require(
            "_symbol_save_modifier != null",
            "Symbol needs the target's actual saved saving throw modifier.",
        ),
    ]

    def bind(value):
        if isinstance(value, list):
            for child in value:
                bind(child)
        elif isinstance(value, dict):
            if value.get("call") == "d20_test":
                value["params"]["ability"] = "{ability}"
                value["params"]["ability_modifier"] = "{_symbol_save_modifier}"
            if value.get("call") == "record_srd_end_turn_save":
                value["params"]["value"] = value["params"]["value"].replace(
                    "Wisdom save:", "{ability} save:"
                )
            for child in value.values():
                if isinstance(child, (dict, list)):
                    bind(child)

    bind(tool["effects"])
    return tool


def _symbol_turn_tool():
    """Keep real combat turns distinct from actual six-second exploration rounds."""
    return _tool(
        "Qualify Symbol's real saved combat turn or authoritative exploration round.",
        [],
        [
            _require("state.lasting_tick != null", "Symbol requires its actual source clock."),
            _set("_symbol_turn_mode", "'clock'"),
            _set("_symbol_turn", "state.lasting_tick // 2"),
            _when(
                "combat.encounter_active == true",
                [
                    _require(
                        "combat.turn_sequence != null",
                        "Combat Symbol needs the actual turn sequence.",
                    ),
                    _set("_symbol_turn_mode", "'combat'"),
                    _set("_symbol_turn", "combat.turn_sequence"),
                ],
            ),
        ],
    )


def _symbol_target_tool():
    """Target each actual creature once on the real turn, retaining the original source DC."""
    effects = _find() + [
        _require(
            "_trigger_record.spell == 'symbol' and _trigger_record.activated_tick != null",
            "This Symbol has not activated.",
        ),
        _call("qualify_srd_symbol_turn", {}, ["_symbol_turn", "_symbol_turn_mode"]),
        _set("_symbol_targets", []),
        _when(
            "_trigger_record.symbol_turn_mode == _symbol_turn_mode and "
            "_trigger_record.symbol_turn == _symbol_turn",
            [_set("_symbol_targets", "_trigger_record.symbol_targets")],
        ),
        _set("_symbol_targeted", False),
        _set("_symbol_target_details", []),
        _when(
            "_target_state_id not in _symbol_targets and target_state.is_alive != false",
            [
                _push("_symbol_targets", "_target_state_id"),
                _set(
                    "_symbol_turn_patch",
                    {
                        "symbol_turn": "_symbol_turn",
                        "symbol_targets": "_symbol_targets",
                        "symbol_turn_mode": "_symbol_turn_mode",
                    },
                ),
                *_update_record("_symbol_turn_patch"),
                _set("_symbol_targeted", True),
                _when(
                    "_trigger_record.symbol_effect == 'death'",
                    [
                        _call(
                            "resolve_spell_area_save_damage_target",
                            {
                                "spell": "symbol",
                                "target": "{_target_state_id}",
                                "save_ability": "constitution",
                                "spell_save_dc": "{_trigger_record.save_dc}",
                                "damage_total": "{death_damage}",
                                "damage_type": "necrotic",
                                "success_damage": "half",
                                "construct_save_disadvantage": False,
                                "source_state_id": "{_trigger_record.caster}",
                                "half_cover_targets": "{half_cover_targets}",
                                "three_quarters_cover_targets": "{three_quarters_cover_targets}",
                            },
                            ["_spell_summary_rows", "_spell_detail_rows"],
                        ),
                        _set("_symbol_target_details", "_spell_detail_rows"),
                    ],
                ),
                _when(
                    "_trigger_record.symbol_effect != 'death'",
                    [
                        _set("_symbol_condition", "'none'"),
                        _set("_symbol_save_ability", "'wisdom'"),
                        _set("_symbol_behavior", "'symbol'"),
                        _set("_symbol_duration", 10),
                        _set("_symbol_control_profile", "'none'"),
                        _when(
                            "_trigger_record.symbol_effect == 'discord'",
                            [_set("_symbol_behavior", "'confusion'")],
                        ),
                        _when(
                            "_trigger_record.symbol_effect == 'fear'",
                            [_set("_symbol_condition", "'frightened'")],
                        ),
                        _when(
                            "_trigger_record.symbol_effect == 'pain'",
                            [
                                _set("_symbol_condition", "'incapacitated'"),
                                _set("_symbol_save_ability", "'constitution'"),
                            ],
                        ),
                        _when(
                            "_trigger_record.symbol_effect == 'stunning'",
                            [_set("_symbol_condition", "'stunned'")],
                        ),
                        _when(
                            "_trigger_record.symbol_effect == 'sleep'",
                            [
                                _set("_symbol_condition", "'unconscious'"),
                                _set("_symbol_duration", 100),
                                _set("_symbol_control_profile", "'sleep'"),
                                _set("_symbol_behavior", "'eyebite_sleep'"),
                            ],
                        ),
                        _call(
                            "resolve_srd_symbol_save",
                            {
                                "target_state": "{_target_state_id}",
                                "ability": "{_symbol_save_ability}",
                                "condition": "{_symbol_condition}",
                                "behavior": "{_symbol_behavior}",
                                "save_dc": "{_trigger_record.save_dc}",
                                "source_state_id": "{_trigger_record.caster}",
                            },
                            [
                                "_control_save_immune",
                                "_control_save_outcome",
                                "_control_save_detail",
                            ],
                        ),
                        _set(
                            "_symbol_target_details",
                            [{"label": "'Saving throw'", "value": "_control_save_detail"}],
                        ),
                        _when(
                            "_control_save_immune != true and _control_save_outcome == 'failure'",
                            [
                                _call(
                                    "install_srd_timed_condition_source",
                                    {
                                        "target_state": "{_target_state_id}",
                                        "caster_state": "{_trigger_record.caster}",
                                        "lasting_state": "state",
                                        "spell": "symbol",
                                        "condition": "{_symbol_condition}",
                                        "save_dc": "{_trigger_record.save_dc}",
                                        "duration_rounds": "{_symbol_duration}",
                                        "repeat_save_timing": "none",
                                        "save_ability": "{_symbol_save_ability}",
                                        "control_profile": "{_symbol_control_profile}",
                                        "actual_spell_level": "{_trigger_record.slot_level}",
                                    },
                                    ["_timed_condition_source_id"],
                                ),
                                *[
                                    _set(
                                        "state.timed_condition_sources.{_timed_condition_source_id}."
                                        + field,
                                        value,
                                    )
                                    for field, value in [
                                        ("symbol_effect", "_trigger_record.symbol_effect"),
                                        ("symbol_trigger_instance", "trigger_id"),
                                        ("symbol_anchor", "_trigger_record.anchor"),
                                        ("symbol_origin", "_trigger_record.origin"),
                                        ("symbol_plane", "_trigger_record.plane"),
                                    ]
                                ],
                                _when(
                                    "_trigger_record.symbol_effect == 'sleep'",
                                    [
                                        _set(
                                            "state.timed_condition_sources.{_timed_condition_source_id}.stage",
                                            "'sleeping'",
                                        ),
                                    ],
                                ),
                                _set("target_state.symbol_affliction_managed", True),
                                _call(
                                    "refresh_srd_timed_condition_target",
                                    {
                                        "target_state": "{_target_state_id}",
                                        "lasting_state": "state",
                                    },
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    ]
    return _tool(
        "Apply the fixed Symbol effect to an actual qualified target once this turn.",
        [
            _parameter("trigger_id", "integer"),
            _entity("target_state"),
            _optional("death_damage", "integer", 0),
            _optional("half_cover_targets", "array", [], items={"type": "string"}),
            _optional("three_quarters_cover_targets", "array", [], items={"type": "string"}),
        ],
        effects,
    )


def _symbol_group_tool():
    """Reuse one actual Death damage roll for the simultaneously affected creatures."""
    return _tool_scene(
        "Apply the active Symbol to its actual area-qualified creature group.",
        [
            _parameter("trigger_id", "integer"),
            _parameter(
                "targets", "entity_id_list", resolve="entity_list", max_items=64, unique_items=True
            ),
        ],
        _find()
        + [
            _call(
                "check_srd_spell_trigger_record", {"record": "{_trigger_record}"}, RECORD_EXPORTS
            ),
            _require(
                "_trigger_active == true and _trigger_record.activated_tick != null",
                "The activated Symbol has ended.",
            ),
            _call("qualify_srd_symbol_turn", {}, ["_symbol_turn", "_symbol_turn_mode"]),
            _set("_symbol_group_candidates", []),
            _loop(
                "targets",
                "_symbol_group_candidate",
                [
                    _when(
                        "_trigger_record.symbol_turn_mode != _symbol_turn_mode or "
                        "_trigger_record.symbol_turn != _symbol_turn or "
                        "_symbol_group_candidate not in _trigger_record.symbol_targets",
                        [
                            _push("_symbol_group_candidates", "_symbol_group_candidate"),
                        ],
                    ),
                ],
            ),
            _call(
                "collect_srd_trigger_area_cover_targets",
                {
                    "anchor": "{_trigger_record.anchor}",
                    "plane": "{_trigger_plane}",
                    "targets": "{_symbol_group_candidates}",
                    "origin": "{_trigger_origin}",
                },
                ["_srd_area_targets", "_srd_area_half_cover", "_srd_area_three_quarters"],
            ),
            _set("_symbol_damage_total", 0),
            _set("_symbol_damage_roll", None),
            _set("_symbol_details", []),
            _when(
                "_trigger_record.symbol_effect == 'death' and len(_srd_area_targets) > 0",
                [
                    {"dice": {"expression": "10d10"}, "store_as": "_symbol_damage_roll"},
                    _set("_symbol_damage_total", "_symbol_damage_roll.total"),
                ],
            ),
            _loop(
                "_srd_area_targets",
                "_symbol_target",
                [
                    _call(
                        "apply_srd_symbol_target",
                        {
                            "trigger_id": "{trigger_id}",
                            "target_state": "{_symbol_target}",
                            "death_damage": "{_symbol_damage_total}",
                            "half_cover_targets": "{_srd_area_half_cover}",
                            "three_quarters_cover_targets": "{_srd_area_three_quarters}",
                        },
                        ["_symbol_targeted", "_symbol_target_details"],
                    ),
                    _loop(
                        "_symbol_target_details",
                        "_symbol_detail",
                        [_push("_symbol_details", "_symbol_detail")],
                    ),
                ],
            ),
        ],
    )


def _symbol_activation_tool():
    """Activate the saved ten-minute Sphere and derive its initial actual creature group."""
    return _tool_scene(
        "Activate this Symbol once, starting its fixed clock and retained effects.",
        [_parameter("trigger_id", "integer")],
        _find()
        + [
            _require(
                "_trigger_record.spell == 'symbol' and _trigger_record.activated_tick == null",
                "The Symbol has already activated.",
            ),
            _set("_symbol_activation_tick", "state.lasting_tick"),
            _set("_symbol_ends_tick", "state.lasting_tick + 200"),
            _set("state.has_active_symbol_auras", True),
            _set(
                "_symbol_activation_patch",
                {
                    "activated_tick": "_symbol_activation_tick",
                    "symbol_ends_tick": "_symbol_ends_tick",
                    "expires_tick": "_symbol_ends_tick",
                },
            ),
            *_update_record("_symbol_activation_patch"),
            _set("_trigger_record", "merge(_trigger_record, _symbol_activation_patch)"),
            _call(
                "check_srd_spell_trigger_record", {"record": "{_trigger_record}"}, RECORD_EXPORTS
            ),
            _set("_symbol_activation_targets", []),
            _set("_symbol_scene", "state.spatial_scene.entities"),
            _loop(
                "_symbol_scene",
                "_symbol_entry",
                [
                    _when(
                        "_symbol_entry.active == true and "
                        "_symbol_entry.metadata.participant_ref != null and "
                        "_symbol_entry.metadata.plane == _trigger_plane",
                        [
                            _call(
                                "qualify_srd_trigger_sphere_volume",
                                {
                                    "candidate": "{_symbol_entry}",
                                    "origin": "{_trigger_origin}",
                                    "radius": 60,
                                },
                                ["_trigger_sphere_intersects"],
                            ),
                            _when(
                                "_trigger_sphere_intersects == true",
                                [
                                    _push(
                                        "_symbol_activation_targets",
                                        "_symbol_entry.metadata.participant_ref",
                                    )
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _require(
                "len(_symbol_activation_targets) <= 64",
                "The Symbol contains more than 64 admitted creatures.",
            ),
            _call("install_srd_symbol_light", {"record": "{_trigger_record}"}),
            _call(
                "resolve_srd_symbol_targets",
                {"trigger_id": "{trigger_id}", "targets": "{_symbol_activation_targets}"},
                ["_symbol_details", "_symbol_damage_roll", "_symbol_damage_total"],
            ),
        ],
    )


def _symbol_boundary_tool():
    """Bind real scene entry/end-turn callbacks without accepting a caller outcome."""
    return _tool_scene(
        "Apply active Symbol effects at actual entry or end-turn boundaries.",
        [
            _entity("target_state"),
            _parameter("boundary_kind", "enum", enum_values=["entry", "end"]),
            _optional(
                "previous_position",
                "array",
                None,
                items={"type": "number"},
                min_items=3,
                max_items=3,
            ),
            _optional("previous_plane", default=""),
        ],
        [
            _set("_symbol_boundary_details", []),
            _when(
                "state.has_active_symbol_auras == true",
                [
                    _call("qualify_srd_symbol_turn", {}, ["_symbol_turn", "_symbol_turn_mode"]),
                    _require(
                        "boundary_kind != 'entry' or (previous_position != null and "
                        "previous_plane != '')",
                        "Actual Symbol entry needs the saved previous position and plane.",
                    ),
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_target_state_id}"},
                        POSITION_EXPORTS,
                    ),
                    _set("_symbol_boundary_target", "_info_scene_entity"),
                    _set("_symbol_boundary_records", "state.turn_effects"),
                    _loop(
                        "_symbol_boundary_records",
                        "_symbol_boundary_record",
                        [
                            _when(
                                "_symbol_boundary_record.kind == 'spell_trigger' and "
                                "_symbol_boundary_record.spell == 'symbol' and "
                                "_symbol_boundary_record.activated_tick != null",
                                [
                                    _call(
                                        "check_srd_spell_trigger_record",
                                        {"record": "{_symbol_boundary_record}"},
                                        RECORD_EXPORTS,
                                    ),
                                    _when(
                                        "_trigger_active == true and "
                                        "_symbol_boundary_target.metadata.plane == _trigger_plane",
                                        [
                                            _call(
                                                "qualify_srd_trigger_sphere_volume",
                                                {
                                                    "candidate": "{_symbol_boundary_target}",
                                                    "origin": "{_trigger_origin}",
                                                    "radius": 60,
                                                },
                                                ["_trigger_sphere_intersects"],
                                            ),
                                            _set(
                                                "_symbol_boundary_qualifies",
                                                "_trigger_sphere_intersects",
                                            ),
                                            _when(
                                                "boundary_kind == 'entry' and previous_plane "
                                                "== _trigger_plane",
                                                [
                                                    _set(
                                                        "_symbol_prior_patch",
                                                        {"position": "previous_position"},
                                                    ),
                                                    _set(
                                                        "_symbol_prior_candidate",
                                                        "merge(_symbol_boundary_target, "
                                                        "_symbol_prior_patch)",
                                                    ),
                                                    _call(
                                                        "qualify_srd_trigger_sphere_volume",
                                                        {
                                                            "candidate": (
                                                                "{_symbol_prior_candidate}"
                                                            ),
                                                            "origin": "{_trigger_origin}",
                                                            "radius": 60,
                                                        },
                                                        ["_trigger_sphere_intersects"],
                                                    ),
                                                    _set(
                                                        "_symbol_boundary_qualifies",
                                                        "_symbol_boundary_qualifies == true "
                                                        "and _trigger_sphere_intersects != true",
                                                    ),
                                                ],
                                            ),
                                            _when(
                                                "_symbol_boundary_qualifies == true",
                                                [
                                                    _set("_symbol_single_target", []),
                                                    _push(
                                                        "_symbol_single_target", "_target_state_id"
                                                    ),
                                                    _call(
                                                        "resolve_srd_symbol_targets",
                                                        {
                                                            "trigger_id": (
                                                                "{_symbol_boundary_record.instance}"
                                                            ),
                                                            "targets": "{_symbol_single_target}",
                                                        },
                                                        ["_symbol_details"],
                                                    ),
                                                    _loop(
                                                        "_symbol_details",
                                                        "_symbol_boundary_detail",
                                                        [
                                                            _push(
                                                                "_symbol_boundary_details",
                                                                "_symbol_boundary_detail",
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


def _symbol_exploration_targets():
    """Collect actual same-plane body volumes through the existing fixed Sphere qualifier."""
    return [
        _set("_symbol_clock_targets", []),
        _loop(
            "_symbol_clock_scene",
            "_symbol_clock_actor",
            [
                _when(
                    "_symbol_clock_actor.active == true and "
                    "_symbol_clock_actor.metadata.participant_ref != null and "
                    "_symbol_clock_actor.metadata.plane == _trigger_plane",
                    [
                        _call(
                            "qualify_srd_trigger_sphere_volume",
                            {
                                "candidate": "{_symbol_clock_actor}",
                                "origin": "{_trigger_origin}",
                                "radius": 60,
                            },
                            ["_trigger_sphere_intersects"],
                        ),
                        _when(
                            "_trigger_sphere_intersects == true and "
                            "_symbol_clock_actor.metadata.participant_ref "
                            "not in _symbol_clock_targets",
                            [
                                _push(
                                    "_symbol_clock_targets",
                                    "_symbol_clock_actor.metadata.participant_ref",
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    ]


def _symbol_exploration_tool():
    """Resolve one actual round; the clock owner must produce every elapsed boundary."""
    affected = _symbol_exploration_targets() + [
        _require("len(_symbol_clock_targets) <= 64", "Resolve at most 64 actual creatures."),
        _when(
            "len(_symbol_clock_targets) > 0",
            [
                _call(
                    "resolve_srd_symbol_targets",
                    {
                        "trigger_id": "{_symbol_clock_record.instance}",
                        "targets": "{_symbol_clock_targets}",
                    },
                    ["_symbol_details"],
                ),
                _loop(
                    "_symbol_details",
                    "_symbol_clock_detail",
                    [
                        _push("_symbol_clock_details", "_symbol_clock_detail"),
                    ],
                ),
            ],
        ),
    ]
    return _tool_scene(
        "Apply each live Symbol to actual creatures ending this exploration round.",
        [],
        [
            _set("_symbol_clock_details", []),
            _when(
                "state.has_active_symbol_auras == true and combat.encounter_active != true",
                [
                    _call("qualify_srd_symbol_turn", {}, ["_symbol_turn", "_symbol_turn_mode"]),
                    _set("_symbol_clock_records", "state.turn_effects"),
                    _set("_symbol_clock_scene", "state.spatial_scene.entities"),
                    _loop(
                        "_symbol_clock_records",
                        "_symbol_clock_record",
                        [
                            _when(
                                "_symbol_clock_record.kind == 'spell_trigger' and "
                                "_symbol_clock_record.spell == 'symbol' and "
                                "_symbol_clock_record.activated_tick != null",
                                [
                                    _call(
                                        "check_srd_spell_trigger_record",
                                        {
                                            "record": "{_symbol_clock_record}",
                                        },
                                        RECORD_EXPORTS,
                                    ),
                                    _when("_trigger_active == true", affected),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _symbol_light_tool():
    """Project Symbol through the existing magical light owner, linked to its actual source."""
    return _tool(
        "Install this activated Symbol's source-linked fixed sixty-foot Dim Light.",
        [_parameter("record", "object", from_state="state.turn_effects")],
        [
            _when(
                "state.illumination_cast_serial == null",
                [_set("state.illumination_cast_serial", 0)],
            ),
            _set("state.illumination_cast_serial", "state.illumination_cast_serial + 1"),
            _set("_symbol_light_records", "state.turn_effects"),
            _set(
                "_symbol_light_record",
                {
                    "kind": "'illumination_spell'",
                    "spell": "'symbol'",
                    "caster": "record.caster",
                    "source": "record.anchor",
                    "target": "record.caster",
                    "origin": "record.origin",
                    "plane": "record.plane",
                    "instance": "state.illumination_cast_serial",
                    "slot_level": "record.slot_level",
                    "expires_tick": "record.symbol_ends_tick",
                    "started_tick": "record.activated_tick",
                    "concentration": False,
                    "mode": "'light'",
                    "bright_radius": 0,
                    "dim_radius": 60,
                    "dark_radius": 0,
                    "symbol_trigger_instance": "record.instance",
                    "owner": "'__time_only__'",
                    "phase": "'time'",
                    "turn": 1000000000,
                    "magnitude": 0,
                    "dice_count": 0,
                    "damage_die": "''",
                    "damage_type": "''",
                },
            ),
            _push("_symbol_light_records", "_symbol_light_record"),
            _set("state.turn_effects", "_symbol_light_records"),
            _set("state.has_illumination_spells", True),
        ],
    )


def _symbol_light_guard():
    return _tool(
        "Keep Symbol light live only while its exact activated trigger source remains.",
        [_parameter("trigger_id", "integer")],
        [
            _set("_symbol_light_active", False),
            _set("_symbol_light_sources", []),
            _when(
                "state.has_spell_triggers == true",
                [_set("_symbol_light_sources", "state.turn_effects")],
            ),
            _loop(
                "_symbol_light_sources",
                "_symbol_light_source",
                [
                    _when(
                        "_symbol_light_source.kind == 'spell_trigger' and "
                        "_symbol_light_source.instance == trigger_id and "
                        "_symbol_light_source.activated_tick != null",
                        [
                            _call(
                                "check_srd_spell_trigger_record",
                                {"record": "{_symbol_light_source}"},
                                RECORD_EXPORTS,
                            ),
                            _set("_symbol_light_active", "_trigger_active"),
                        ],
                    ),
                ],
            ),
        ],
    )


SYMBOL_EXPORTS = (
    "_symbol_discord",
    "_symbol_fear",
    "_symbol_fear_in_sight",
    "_symbol_communication_blocked",
    "_symbol_fear_sources",
)


def _symbol_affliction_tool():
    """Read installed Symbol sources; independent conditions and native senses stay intact."""
    return _tool(
        "Qualify actual saved Symbol afflictions and the glyph's current visibility.",
        [_entity("target_state")],
        [
            _set("_symbol_discord", False),
            _set("_symbol_fear", False),
            _set("_symbol_fear_in_sight", False),
            _set("_symbol_communication_blocked", False),
            _set("_symbol_fear_sources", []),
            _set("_symbol_affliction_ids", []),
            _when(
                "target_state.symbol_affliction_managed == true and "
                "target_state.timed_condition_source_ids != null",
                [
                    _set("_symbol_affliction_ids", "target_state.timed_condition_source_ids"),
                    _loop(
                        "_symbol_affliction_ids",
                        "_symbol_affliction_id",
                        [
                            _set(
                                "_symbol_affliction",
                                "state.timed_condition_sources[_symbol_affliction_id]",
                            ),
                            _when(
                                "_symbol_affliction.installed == true and "
                                "_symbol_affliction.target_state_id == _target_state_id and "
                                "_symbol_affliction.spell_id == 'symbol' and "
                                "_symbol_affliction.suppressed != true and state.lasting_tick "
                                "< _symbol_affliction.expires_tick",
                                [
                                    _when(
                                        "_symbol_affliction.symbol_effect == 'discord'",
                                        [
                                            _set("_symbol_discord", True),
                                            _set("_symbol_communication_blocked", True),
                                        ],
                                    ),
                                    _when(
                                        "_symbol_affliction.symbol_effect == 'fear'",
                                        [
                                            _set("_symbol_fear", True),
                                            _push("_symbol_fear_sources", "_symbol_affliction"),
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


SYMBOL_FEAR_MOVEMENT_EXPORTS = (
    "_symbol_fear_movement_active",
    "_symbol_fear_movement_sources",
    "_symbol_fear_movement_origin",
    "_symbol_fear_movement_plane",
    "_symbol_fear_movement_unresolved",
    "_symbol_fear_movement_turn",
    "_symbol_fear_movement_turn_mode",
)


def _symbol_fear_source_live(source):
    """A stale actor index cannot borrow another target's or a recycled condition source."""
    return (
        f"{source}.installed == true and {source}.target_state_id == _actor_state_id and "
        f"{source}.spell_id == 'symbol' and {source}.symbol_effect == 'fear' and "
        f"{source}.condition == 'frightened' and {source}.suppressed != true and "
        f"state.lasting_tick < {source}.expires_tick"
    )


def _symbol_fear_movement_tools():
    """Credit only committed walking segments against each real turn and exact Fear source."""
    qualify = _tool_scene(
        "Derive Symbol Fear's current glyph geometry and actual walked retreat on this turn.",
        [_entity("actor_state")],
        [
            _set("_symbol_fear_movement_active", False),
            _set("_symbol_fear_movement_sources", []),
            _set("_symbol_fear_movement_origin", None),
            _set("_symbol_fear_movement_plane", None),
            _set("_symbol_fear_movement_unresolved", False),
            _call(
                "qualify_srd_symbol_turn",
                {"lasting_state": "{_lasting_state_id}"},
                ["_symbol_turn", "_symbol_turn_mode"],
            ),
            _set("_symbol_fear_movement_turn", "_symbol_turn"),
            _set("_symbol_fear_movement_turn_mode", "_symbol_turn_mode"),
            _when(
                "actor_state.symbol_affliction_managed == true and "
                "'frightened' in actor_state.conditions and "
                "actor_state.timed_condition_source_ids != null",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_actor_state_id}"},
                        POSITION_EXPORTS,
                    ),
                    _set("_symbol_fear_movement_origin", "_info_position"),
                    _set("_symbol_fear_actor_scene_id", "_info_scene_entity.id"),
                    _set("_symbol_fear_movement_plane", "'material'"),
                    _when(
                        "_info_scene_entity.metadata.plane != null",
                        [
                            _set(
                                "_symbol_fear_movement_plane", "_info_scene_entity.metadata.plane"
                            ),
                        ],
                    ),
                    _set("_symbol_fear_ids", "actor_state.timed_condition_source_ids"),
                    _loop(
                        "_symbol_fear_ids",
                        "_symbol_fear_id",
                        [
                            _set(
                                "_symbol_fear_source",
                                "state.timed_condition_sources[_symbol_fear_id]",
                            ),
                            _when(
                                _symbol_fear_source_live("_symbol_fear_source"),
                                [
                                    _set("_symbol_fear_movement_active", True),
                                    _set("_symbol_fear_moved_feet", 0),
                                    _when(
                                        "_symbol_fear_source.symbol_fear_walk_turn == "
                                        "_symbol_turn and "
                                        "_symbol_fear_source.symbol_fear_walk_mode == "
                                        "_symbol_turn_mode and "
                                        "_symbol_fear_source.symbol_fear_walk_feet != null",
                                        [
                                            _set(
                                                "_symbol_fear_moved_feet",
                                                "_symbol_fear_source.symbol_fear_walk_feet",
                                            )
                                        ],
                                    ),
                                    _set("_trigger_active", True),
                                    *_anchor_liveness("_symbol_fear_source.symbol_anchor"),
                                    _set("_symbol_fear_geometry_resolved", False),
                                    _set("_symbol_fear_glyph_origin", None),
                                    _set("_symbol_fear_glyph_plane", None),
                                    _set("_symbol_fear_follows_actor", False),
                                    _when(
                                        "_trigger_active == true",
                                        [
                                            *_current_anchor(
                                                "{_symbol_fear_source.symbol_anchor}",
                                                require_plane=False,
                                            ),
                                            _set("_symbol_fear_glyph_origin", "_trigger_origin"),
                                            _set("_symbol_fear_glyph_plane", "_trigger_plane"),
                                            _set(
                                                "_symbol_fear_geometry_resolved",
                                                "_trigger_plane != null and "
                                                "_trigger_plane == _symbol_fear_movement_plane",
                                            ),
                                            _set(
                                                "_symbol_fear_follows_actor",
                                                "_info_scene_entity.id == "
                                                "_symbol_fear_actor_scene_id",
                                            ),
                                        ],
                                    ),
                                    _when(
                                        "_symbol_fear_geometry_resolved != true",
                                        [
                                            _set("_symbol_fear_movement_unresolved", True),
                                        ],
                                    ),
                                    _set(
                                        "_symbol_fear_movement_source",
                                        {
                                            "source_id": "_symbol_fear_id",
                                            "source_sequence": "_symbol_fear_source.sequence",
                                            "glyph_anchor": "_symbol_fear_source.symbol_anchor",
                                            "glyph_origin": "_symbol_fear_glyph_origin",
                                            "glyph_plane": "_symbol_fear_glyph_plane",
                                            "moved_feet": "_symbol_fear_moved_feet",
                                            "follows_actor": "_symbol_fear_follows_actor",
                                            "geometry_resolved": "_symbol_fear_geometry_resolved",
                                        },
                                    ),
                                    _push(
                                        "_symbol_fear_movement_sources",
                                        "_symbol_fear_movement_source",
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    dot = " + ".join(
        f"(destination[{i}] - _symbol_fear_movement_origin[{i}]) * "
        f"(_symbol_fear_movement_origin[{i}] - _symbol_fear_segment_source.glyph_origin[{i}])"
        for i in range(3)
    )
    check = _tool(
        "Validate one intended walking segment against actual Symbol Fear glyph positions.",
        [_entity("actor_state"), _array("destination")],
        [
            _set("actor_state.symbol_fear_walk_pending", None),
            _call(
                "qualify_srd_symbol_fear_movement",
                {
                    "actor_state": "{_actor_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
                SYMBOL_FEAR_MOVEMENT_EXPORTS,
            ),
            _when(
                "_symbol_fear_movement_active == true",
                [
                    _require(
                        "_symbol_fear_movement_unresolved != true",
                        "Symbol Fear walking requires its actual same-plane glyph geometry; "
                        "a missing glyph or unresolved route cannot establish inability.",
                    ),
                    _loop(
                        "_symbol_fear_movement_sources",
                        "_symbol_fear_segment_source",
                        [
                            _when(
                                "_symbol_fear_segment_source.follows_actor != true",
                                [
                                    _require(
                                        f"{dot} >= -0.000000001",
                                        "While Frightened by Symbol, no part of this "
                                        "walking segment "
                                        "may move closer to its actual glyph.",
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _set(
                        "actor_state.symbol_fear_walk_pending",
                        {
                            "origin": "_symbol_fear_movement_origin",
                            "destination": "destination",
                            "plane": "_symbol_fear_movement_plane",
                            "revision": "state.spatial_scene.map.revision",
                            "turn": "_symbol_fear_movement_turn",
                            "turn_mode": "_symbol_fear_movement_turn_mode",
                            "sources": "_symbol_fear_movement_sources",
                        },
                    ),
                ],
            ),
        ],
    )
    matching_commit = " and ".join(
        f"_info_position[{i}] == _symbol_fear_pending.destination[{i}] and "
        f"_info_scene_entity.metadata.previous_position[{i}] == _symbol_fear_pending.origin[{i}]"
        for i in range(3)
    )
    record = _tool_scene(
        "Record one committed physical movement segment once, preserving exact source identity.",
        [_entity("actor_state")],
        [
            _set("_symbol_fear_segment_credited", False),
            _when(
                "actor_state.symbol_fear_walk_pending != null",
                [
                    _set("_symbol_fear_pending", "actor_state.symbol_fear_walk_pending"),
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_actor_state_id}"},
                        POSITION_EXPORTS,
                    ),
                    _require(
                        "state.spatial_scene.map.revision > _symbol_fear_pending.revision and "
                        "_info_scene_entity.metadata.motion_tick == state.lasting_tick and "
                        "_info_scene_entity.metadata.previous_position != null",
                        "Symbol Fear retreat is credited only after its checked physical segment "
                        "actually commits.",
                    ),
                    _require(
                        matching_commit,
                        "Symbol Fear retreat is credited only after its checked physical segment "
                        "actually commits.",
                    ),
                    _call(
                        "qualify_srd_symbol_turn",
                        {"lasting_state": "{_lasting_state_id}"},
                        ["_symbol_turn", "_symbol_turn_mode"],
                    ),
                    _require(
                        "_symbol_turn == _symbol_fear_pending.turn and "
                        "_symbol_turn_mode == _symbol_fear_pending.turn_mode",
                        "A physical segment must commit on the same real turn it qualified.",
                    ),
                    _call(
                        "measure_srd_information_distance",
                        {
                            "start": "{_symbol_fear_pending.origin}",
                            "end": "{_info_position}",
                        },
                        ["_scene_distance"],
                    ),
                    _set("_symbol_fear_segment_distance", "_scene_distance"),
                    _set("_symbol_fear_segment_sources", "_symbol_fear_pending.sources"),
                    _loop(
                        "_symbol_fear_segment_sources",
                        "_symbol_fear_segment_source",
                        [
                            _set("_symbol_fear_id", "_symbol_fear_segment_source.source_id"),
                            _set(
                                "_symbol_fear_source",
                                "state.timed_condition_sources[_symbol_fear_id]",
                            ),
                            _when(
                                _symbol_fear_source_live("_symbol_fear_source") + " and "
                                "_symbol_fear_source.sequence == "
                                "_symbol_fear_segment_source.source_sequence and "
                                "'frightened' in actor_state.conditions",
                                [
                                    _set(
                                        "_symbol_fear_walk_total",
                                        "_symbol_fear_segment_source.moved_feet",
                                    ),
                                    _when(
                                        "_symbol_fear_segment_source.follows_actor != true",
                                        [
                                            _set(
                                                "_symbol_fear_walk_total",
                                                "_symbol_fear_walk_total + "
                                                "_symbol_fear_segment_distance",
                                            ),
                                        ],
                                    ),
                                    _set(
                                        "state.timed_condition_sources.{_symbol_fear_id}.symbol_fear_walk_turn",
                                        "_symbol_turn",
                                    ),
                                    _set(
                                        "state.timed_condition_sources.{_symbol_fear_id}.symbol_fear_walk_mode",
                                        "_symbol_turn_mode",
                                    ),
                                    _set(
                                        "state.timed_condition_sources.{_symbol_fear_id}.symbol_fear_walk_feet",
                                        "_symbol_fear_walk_total",
                                    ),
                                    _set("_symbol_fear_segment_credited", True),
                                ],
                            ),
                        ],
                    ),
                    _set("actor_state.symbol_fear_walk_pending", None),
                ],
            ),
        ],
    )
    return {
        "qualify_srd_symbol_fear_movement": qualify,
        "check_srd_symbol_fear_segment": check,
        "record_srd_symbol_fear_segment": record,
    }


def _symbol_fear_movement_consumers(runtime):
    """Bind the sole final walking segment; teleports never receive retreat credit."""
    name = "move_srd_scene_creature_environment_segment"
    if name not in runtime["tools"]:
        return {}
    tool = deepcopy(runtime["tools"][name])
    if "check_srd_symbol_fear_segment" in str(tool["effects"]):
        return {name: tool}
    effects = tool["effects"]
    before = [i for i, e in enumerate(effects) if e.get("call") == "srd_information_movement_cost"]
    after = [
        i
        for i, e in enumerate(effects)
        if e.get("set", {}).get("field") == "_scene_movement_distance"
    ]
    if len(before) != 1 or len(after) != 1:
        raise ValueError("Symbol Fear needs the exact actual walking cost and commit boundaries.")
    params = {"actor_state": "{_actor_state_id}", "lasting_state": "{_lasting_state_id}"}
    effects.insert(
        after[0] + 1,
        _when(
            "actor_state.symbol_affliction_managed == true",
            [
                _call("record_srd_symbol_fear_segment", params),
            ],
        ),
    )
    effects.insert(
        before[0],
        _when(
            "actor_state.symbol_affliction_managed == true",
            [
                _call("check_srd_symbol_fear_segment", {**params, "destination": "{destination}"}),
            ],
        ),
    )
    return {name: tool}


def _symbol_communication_tool():
    """Qualify meaningful exchange from each participant's exact active Discord source."""
    effects = [_set("_symbol_exchange_blocked", False)]
    for role in ("speaker_state", "listener_state"):
        effects.append(
            _when(
                f"{role}.symbol_affliction_managed == true",
                [
                    _call(
                        "check_srd_symbol_afflictions",
                        {
                            "target_state": "{_" + role + "_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                        ["_symbol_communication_blocked"],
                    ),
                    _set(
                        "_symbol_exchange_blocked",
                        "_symbol_exchange_blocked == true or _symbol_communication_blocked == true",
                    ),
                ],
            )
        )
    return _tool(
        "Qualify meaningful communication against actual saved Symbol Discord afflictions.",
        [
            _entity("speaker_state"),
            _entity("listener_state"),
            _optional("lasting_state", default="state", resolve="entity"),
        ],
        effects,
    )


def _symbol_communication_consumers(runtime):
    """Compose actual delivery and exchange owners without prohibiting verbal spellcasting."""
    changed = {}

    def query(tool, speaker, listener):
        return _call(
            "qualify_srd_symbol_communication",
            {
                "speaker_state": "{_" + speaker + "_id}",
                "listener_state": "{_" + listener + "_id}",
                "lasting_state": (
                    "{_lasting_state_id}"
                    if any(p["name"] == "lasting_state" for p in tool["parameters"])
                    else "state"
                ),
            },
            ["_symbol_exchange_blocked"],
        )

    send = deepcopy(runtime["tools"]["cast_srd_spell_message"])
    if "qualify_srd_symbol_communication" not in str(send["effects"]):
        indices = [
            i
            for i, e in enumerate(send["effects"])
            if e.get("set", {}).get("field") == "_divination_install" and e["set"]["value"] is False
        ]
        if len(indices) != 1:
            raise SystemExit("Symbol Discord needs the actual message delivery boundary.")
        send["effects"].insert(
            indices[0],
            _when(
                "_message_delivered == true and (caster_state.symbol_affliction_managed == true "
                "or target_state.symbol_affliction_managed == true)",
                [
                    query(send, "caster_state", "target_state"),
                    _when(
                        "_symbol_exchange_blocked == true",
                        [
                            _set("_message_delivered", False),
                            _set(
                                "_message_block_reason",
                                "'Symbol Discord prevents meaningful communication.'",
                            ),
                        ],
                    ),
                ],
            ),
        )
        changed["cast_srd_spell_message"] = send

    for name, speaker, listener in (
        ("communicate_srd_spell", "speaker_state", "listener_state"),
        ("communicate_srd_telepathic_bond_node", "sender_state", "recipient_state"),
        ("reply_srd_spell_message_node", "recipient_state", "sender_state"),
    ):
        tool = deepcopy(runtime["tools"][name])
        if "qualify_srd_symbol_communication" in str(tool["effects"]):
            continue
        guard = f"{speaker}.symbol_affliction_managed == true or "
        guard += f"{listener}.symbol_affliction_managed == true"
        if name == "reply_srd_spell_message_node":
            guard = "len(reply_words) > 0 and (" + guard + ")"
        tool["effects"].insert(
            1,
            _when(
                guard,
                [
                    query(tool, speaker, listener),
                    _require(
                        "_symbol_exchange_blocked != true",
                        "Symbol Discord prevents meaningful communication.",
                    ),
                ],
            ),
        )
        changed[name] = tool
    member = deepcopy(runtime["tools"]["validate_srd_telepathic_bond_member"])
    if "check_srd_symbol_afflictions" not in str(member["effects"]):
        member["effects"].append(
            _when(
                "_bond_eligible == true and target_state.symbol_affliction_managed == true",
                [
                    _call(
                        "check_srd_symbol_afflictions",
                        {"target_state": "{_target_state_id}"},
                        ["_symbol_communication_blocked"],
                    ),
                    _set("_bond_eligible", "_symbol_communication_blocked != true"),
                ],
            ),
        )
        changed["validate_srd_telepathic_bond_member"] = member
    return changed


def _symbol_fear_sight_tool(*, projected=False):
    """Use the existing raw sight owner at the roll boundary without recursive expiry damage."""
    source_sight = [
        _call(
            "measure_srd_information_distance",
            {"start": "{_symbol_observer_origin}", "end": "{_trigger_origin}"},
            ["_scene_distance"],
        ),
        _set("_symbol_sight_distance", "ceil(_scene_distance) + 0"),
        _set(
            "_symbol_sight_permitted",
            "_symbol_observer_plane == _trigger_plane or "
            "(_symbol_observer_plane == 'material' and _trigger_plane == 'ethereal')",
        ),
    ]
    if projected:
        source_sight += [
            _set("_symbol_projection_live", False),
            _when(
                "_symbol_observer_plane == 'ethereal' and _scene_distance <= 60",
                [
                    _when(
                        "target_state.travel_ethereal != null and "
                        "target_state.travel_ethereal.spell == 'etherealness' and "
                        "state.lasting_tick < target_state.travel_ethereal.expires_tick and "
                        "target_state.travel_ethereal.return_plane == _trigger_plane",
                        [_set("_symbol_projection_live", True)],
                    ),
                    _when(
                        "target_state.travel_blink != null and "
                        "target_state.travel_blink.spell == 'blink' and "
                        "target_state.travel_blink.phase == true and "
                        "target_state.travel_blink.return_plane == _trigger_plane and "
                        "(state.lasting_tick < target_state.travel_blink.expires_tick or "
                        "target_state.travel_blink.return_in_progress == true)",
                        [_set("_symbol_projection_live", True)],
                    ),
                ],
            ),
            _when(
                "_symbol_projection_live == true",
                [
                    _call(
                        "qualify_srd_travel_perception",
                        {
                            "observer_state": "{_target_state_id}",
                            "point": "{_trigger_origin}",
                            "perception_plane": "{_trigger_plane}",
                        },
                        [
                            "_travel_perception_plane",
                            "_travel_perception_origin",
                            "_travel_perception_distance",
                        ],
                    ),
                    _set("_symbol_observer_origin", "_travel_perception_origin"),
                    _set("_symbol_sight_distance", "ceil(_travel_perception_distance) + 0"),
                    _set("_symbol_sight_permitted", "_travel_perception_plane == _trigger_plane"),
                ],
            ),
        ]
    return _tool_scene(
        "Derive current same-plane Symbol Fear sight from actual saved source geometry.",
        [_entity("target_state")],
        [
            _call(
                "check_srd_symbol_afflictions",
                {"target_state": "{_target_state_id}"},
                SYMBOL_EXPORTS,
            ),
            _call(
                "srd_information_scene_entity", {"subject": "{_target_state_id}"}, POSITION_EXPORTS
            ),
            _set("_symbol_observer_origin", "_info_position"),
            _set("_symbol_observer_plane", "_info_scene_entity.metadata.plane"),
            _loop(
                "_symbol_fear_sources",
                "_symbol_fear_source",
                [
                    _set("_trigger_active", True),
                    *_anchor_liveness("_symbol_fear_source.symbol_anchor"),
                    _when(
                        "_trigger_active == true",
                        [
                            *_current_anchor("{_symbol_fear_source.symbol_anchor}"),
                            *source_sight,
                            _when(
                                "_symbol_sight_permitted == true",
                                [
                                    _call(
                                        "check_srd_illumination_object_visibility",
                                        {
                                            "object_id": "{_symbol_fear_source.symbol_anchor}",
                                            "observer_state": "{_target_state_id}",
                                        },
                                        [
                                            "_illumination_object_invisible",
                                            "_illumination_object_lighting",
                                        ],
                                    ),
                                    _call(
                                        "measure_srd_information_distance",
                                        {
                                            "start": "{_symbol_observer_origin}",
                                            "end": "{_trigger_origin}",
                                        },
                                        ["_scene_distance"],
                                    ),
                                    _set("_symbol_sight_distance", "ceil(_scene_distance) + 0"),
                                    _call(
                                        "srd_information_barriers",
                                        {
                                            "start": "{_symbol_observer_origin}",
                                            "end": "{_trigger_origin}",
                                            "rule": "light",
                                            "plane": "{_trigger_plane}",
                                        },
                                        ["_information_blocked"],
                                    ),
                                    _set("_symbol_glyph_opaque", "_information_blocked"),
                                    _call(
                                        "srd_information_barriers",
                                        {
                                            "start": "{_symbol_observer_origin}",
                                            "end": "{_trigger_origin}",
                                            "rule": "solid",
                                            "plane": "{_trigger_plane}",
                                        },
                                        ["_information_blocked"],
                                    ),
                                    _set("_symbol_glyph_total_cover", "_information_blocked"),
                                    _call(
                                        "resolve_srd_environment_segment",
                                        {
                                            "start": "{_symbol_observer_origin}",
                                            "end": "{_trigger_origin}",
                                            "plane": "{_trigger_plane}",
                                        },
                                        ["_environment_sight_obscured"],
                                    ),
                                    _call(
                                        "resolve_srd_information_light",
                                        {
                                            "point": "{_trigger_origin}",
                                            "plane": "{_trigger_plane}",
                                            "ambient": "{_illumination_object_lighting}",
                                        },
                                        ["_information_light"],
                                    ),
                                    _set(
                                        "_symbol_glyph_ethereal",
                                        "_symbol_observer_plane == 'material' "
                                        "and _trigger_plane == 'ethereal'",
                                    ),
                                    _set("_symbol_sight_obstruction", "'clear'"),
                                    _when(
                                        "_environment_sight_obscured == true",
                                        [_set("_symbol_sight_obstruction", "'heavy_obscurement'")],
                                    ),
                                    _when(
                                        "_symbol_glyph_opaque == true",
                                        [_set("_symbol_sight_obstruction", "'solid'")],
                                    ),
                                    _call(
                                        "resolve_srd_symbol_glyph_sight",
                                        {
                                            "observer_state": "{_target_state_id}",
                                            "distance": "{_symbol_sight_distance}",
                                            "lighting": "{_information_light}",
                                            "obstruction": "{_symbol_sight_obstruction}",
                                            "subject_invisible": "{_illumination_object_invisible}",
                                            "subject_ethereal": "{_symbol_glyph_ethereal}",
                                            "visual_illusion": False,
                                            "test": "none",
                                        },
                                        ["_sight_can_see"],
                                    ),
                                    _when(
                                        "target_state.blindsight_feet != null and "
                                        "target_state.blindsight_feet >= "
                                        "_symbol_sight_distance and "
                                        "_symbol_glyph_total_cover != true and "
                                        "(_symbol_observer_plane == _trigger_plane or "
                                        "_symbol_projection_live == true)",
                                        [_set("_sight_can_see", True)],
                                    ),
                                    _set(
                                        "_symbol_fear_in_sight",
                                        "_symbol_fear_in_sight == true or _sight_can_see == true",
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _symbol_query_sight_tools(runtime):
    """Specialize existing sight to observation only, so later rolls cannot recursively roll."""
    names = {
        "resolve_srd_sight": "resolve_srd_symbol_glyph_sight",
        "resolve_srd_sight_node": "resolve_srd_symbol_glyph_sight_node",
    }
    changed = {}
    for source, name in names.items():
        tool = deepcopy(runtime["tools"][source])
        for parameter in tool["parameters"]:
            if parameter["name"] == "test":
                parameter["enum_values"] = ["none"]
                parameter["default"] = "none"
        if source == "resolve_srd_sight_node":
            tests = [e for e in tool["effects"] if e.get("condition") == "test != 'none'"]
            if len(tests) != 1 or "d20_test" not in str(tests[0]):
                raise SystemExit(
                    "The sight owner no longer has the expected sensory-test boundary."
                )
            tool["effects"].remove(tests[0])
        else:
            calls = [e for e in tool["effects"] if e.get("call") == "resolve_srd_sight_node"]
            if len(calls) != 1:
                raise SystemExit("The sight wrapper no longer delegates to its owning node.")
            calls[0]["call"] = names["resolve_srd_sight_node"]
        changed[name] = tool
    return changed


def _symbol_shake_tool():
    """Bind the existing paid awakening action to actual saved same-plane contact."""
    return _tool(
        "Awaken an actual Symbol sleeper through the established source-owned Sleep action.",
        [_entity("actor_state"), _entity("target_state")],
        [
            _call(
                "resolve_srd_scene_geometry_relation",
                {"actor_state": "{_actor_state_id}", "target_state": "{_target_state_id}"},
                [
                    "_scene_distance",
                    "_scene_same_plane",
                    "_scene_actor_position",
                    "_scene_target_position",
                    "_scene_actor_entity",
                ],
            ),
            _call(
                "srd_information_barriers",
                {
                    "start": "{_scene_actor_position}",
                    "end": "{_scene_target_position}",
                    "plane": "{_scene_actor_entity.metadata.plane}",
                    "rule": "solid",
                },
                ["_information_blocked"],
            ),
            _require(
                "_scene_same_plane == true and _scene_distance <= 5 and "
                "_information_blocked != true",
                "Awakening needs actual same-plane contact within five feet.",
            ),
            _set("_symbol_shake_distance", "ceil(_scene_distance)"),
            _call(
                "shake_srd_sleeping_creature",
                {
                    "actor_state": "{_actor_state_id}",
                    "target_state": "{_target_state_id}",
                    "target_distance": "{_symbol_shake_distance}",
                    "lasting_state": "state",
                },
            ),
        ],
    )


def _symbol_consumers(runtime):
    """Compose only source-qualified light, Fear projection and later Discord/Fear rolls."""
    changed = {}
    light = deepcopy(runtime["tools"]["check_srd_illumination_record"])
    if "check_srd_symbol_light_source" not in str(light["effects"]):
        light["effects"].append(
            _when(
                "_illumination_active == true and record.symbol_trigger_instance != null",
                [
                    _call(
                        "check_srd_symbol_light_source",
                        {"trigger_id": "{record.symbol_trigger_instance}"},
                        ["_symbol_light_active"],
                    ),
                    _set("_illumination_active", "_symbol_light_active"),
                ],
            )
        )
        changed["check_srd_illumination_record"] = light
    conditions = deepcopy(runtime["tools"]["resolve_srd_control_behavior_conditions"])
    if "check_srd_symbol_afflictions" not in str(conditions["effects"]):
        frightened = [
            effect
            for effect in conditions["effects"]
            if effect.get("condition") == "target_state.timed_condition_unowned_frightened != null"
        ]
        if len(frightened) != 1:
            raise SystemExit("Symbol requires the existing source-owned Frightened projection.")
        frightened[0]["on_true"].append(
            _when(
                "target_state.symbol_affliction_managed == true",
                [
                    _call(
                        "check_srd_symbol_afflictions",
                        {"target_state": "{_target_state_id}"},
                        SYMBOL_EXPORTS,
                    ),
                    _when(
                        "_symbol_fear == true",
                        [
                            {
                                "apply_condition": {
                                    "target": "target_state",
                                    "condition": "frightened",
                                }
                            }
                        ],
                    ),
                ],
            )
        )
        changed["resolve_srd_control_behavior_conditions"] = conditions
    roll = deepcopy(runtime["tools"]["d20_test"])
    if "check_srd_symbol_afflictions" not in str(roll["effects"]):
        native = [
            effect
            for effect in roll["effects"]
            if effect.get("condition") == "actor_state != null"
            and "'invisible' in actor_state.conditions" in str(effect)
        ]
        if len(native) != 1:
            raise SystemExit("Symbol requires the existing native attack/check condition branch.")
        body = native[0]["on_true"]
        branch = next(
            (
                effect
                for effect in body
                if effect.get("condition") == "kind == 'ability_check' or kind == 'attack_roll'"
            ),
            None,
        )
        if branch is None:
            _native_conditions(roll["effects"])
            branch = next(
                effect
                for effect in body
                if effect.get("condition") == "kind == 'ability_check' or kind == 'attack_roll'"
            )
        branch["on_true"].append(
            _when(
                "actor_state.symbol_affliction_managed == true",
                [
                    _call(
                        "check_srd_symbol_afflictions",
                        {"target_state": "{_actor_state_id}"},
                        SYMBOL_EXPORTS,
                    ),
                    _when(
                        "_symbol_fear == true",
                        [
                            _call(
                                "resolve_srd_symbol_fear_sight",
                                {"target_state": "{_actor_state_id}"},
                                ["_symbol_fear_in_sight"],
                            ),
                        ],
                    ),
                    _set(
                        "_condition_disadvantage",
                        "_condition_disadvantage == true or _symbol_discord == true or "
                        "_symbol_fear_in_sight == true",
                    ),
                ],
            ),
        )
        changed["d20_test"] = roll
    return changed


def _cover_tool(runtime):
    """Reuse actual radial-area Cover with the retained ward's anchor and saved plane."""
    tool = deepcopy(runtime["tools"]["collect_srd_spell_area_cover_targets"])
    tool["description"] = "Resolve existing actual area Cover from a retained ward's scene source."
    tool["parameters"] = [
        _parameter("anchor"),
        _parameter("plane"),
        *[p for p in tool["parameters"] if p["name"] not in {"source_state", "source_frames"}],
    ]
    radial = []
    for effect in tool["effects"]:
        if effect.get("condition") == "len(axis) == 2":
            radial.extend(effect.get("on_false", []))
        else:
            radial.append(effect)
    guard = []
    if any(p["name"] == "axis" for p in tool["parameters"]):
        guard = [_require("len(axis) == 0", "Retained wards use their actual radial area.")]
    tool["effects"] = guard + radial

    def bind(value):
        if isinstance(value, list):
            for child in value:
                bind(child)
        elif isinstance(value, dict):
            if value.get("call") == "srd_information_scene_entity":
                value["params"]["subject"] = "{anchor}"
            field = value.get("set", {}).get("field")
            if field == "_breath_source_position":
                value["set"]["value"] = "origin"
            elif field == "_breath_source_plane":
                value["set"]["value"] = "plane"
            for child in value.values():
                if isinstance(child, (dict, list)):
                    bind(child)

    bind(tool["effects"])
    return tool


def _glyph_tool():
    return _tool_scene(
        "Resolve one real explosive Rune at its saved source DC and actual 20-foot Sphere.",
        [
            _parameter("record", "object", from_state="state.turn_effects"),
            _array("origin"),
            _parameter("plane"),
        ],
        _snapshot()
        + [
            _set("_trigger_targets", []),
            _set("_trigger_scene", "state.spatial_scene.entities"),
            _loop(
                "_trigger_scene",
                "_trigger_scene_entry",
                [
                    _when(
                        (
                            "_trigger_scene_entry.active == true and _trigger_scene_entry.metada"
                            "ta.participant_ref != null and _trigger_scene_entry.metadata.plane "
                            "== plane"
                        ),
                        [
                            _call(
                                "qualify_srd_trigger_sphere_volume",
                                {
                                    "candidate": "{_trigger_scene_entry}",
                                    "origin": "{origin}",
                                    "radius": 20,
                                },
                                ["_trigger_sphere_intersects"],
                            ),
                            _when(
                                "_trigger_sphere_intersects == true",
                                [
                                    _push(
                                        "_trigger_targets",
                                        "_trigger_scene_entry.metadata.participant_ref",
                                    )
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _require(
                "len(_trigger_targets) <= 64",
                "The explosive Rune contains more than 64 admitted creatures.",
            ),
            _call(
                "collect_srd_trigger_area_cover_targets",
                {
                    "anchor": "{record.anchor}",
                    "plane": "{plane}",
                    "targets": "{_trigger_targets}",
                    "origin": "{origin}",
                },
                ["_srd_area_targets", "_srd_area_half_cover", "_srd_area_three_quarters"],
            ),
            _set("_trigger_dice_count", "record.slot_level + 2"),
            {"dice": {"expression": "{_trigger_dice_count}d8"}, "store_as": "_trigger_damage_roll"},
            _set("_trigger_damage_total", "_trigger_damage_roll.total"),
            _set("_trigger_damage_details", []),
            _loop(
                "_srd_area_targets",
                "_trigger_target_id",
                [
                    _call(
                        "resolve_spell_area_save_damage_target",
                        {
                            "spell": "glyph_of_warding",
                            "target": "{_trigger_target_id}",
                            "save_ability": "dexterity",
                            "spell_save_dc": "{record.save_dc}",
                            "damage_total": "{_trigger_damage_total}",
                            "damage_type": "{record.damage_type}",
                            "success_damage": "half",
                            "construct_save_disadvantage": False,
                            "half_cover_targets": "{_srd_area_half_cover}",
                            "three_quarters_cover_targets": "{_srd_area_three_quarters}",
                            "source_state_id": "{record.caster}",
                        },
                        ["_spell_summary_rows", "_spell_detail_rows"],
                    ),
                    _loop(
                        "_spell_detail_rows",
                        "_trigger_damage_row",
                        [_push("_trigger_damage_details", "_trigger_damage_row")],
                    ),
                ],
            ),
        ],
    )


def _wake_tool():
    """Wake actual sleep sources without clearing unrelated or zero-HP Unconscious."""
    return _tool(
        "Wake the Alarm recipient's actual ordinary or spell-induced sleep.",
        [_entity("target_state")],
        [
            _when(
                "target_state.hp > 0",
                [
                    _set("_alarm_sleep_ids", []),
                    _when(
                        "target_state.timed_condition_source_ids != null",
                        [_set("_alarm_sleep_ids", "target_state.timed_condition_source_ids")],
                    ),
                    _loop(
                        "_alarm_sleep_ids",
                        "_alarm_sleep_id",
                        [
                            _set(
                                "_alarm_sleep_source",
                                "state.timed_condition_sources[_alarm_sleep_id]",
                            ),
                            _when(
                                "_alarm_sleep_source.installed == true and "
                                "_alarm_sleep_source.condition == 'unconscious' and "
                                "(_alarm_sleep_source.control_profile == 'sleep' or "
                                "_alarm_sleep_source.control_profile == 'eyebite_sleep')",
                                [
                                    _call(
                                        "retire_srd_timed_condition_source",
                                        {
                                            "lasting_state": "state",
                                            "source_id": "{_alarm_sleep_id}",
                                        },
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _call("wake_srd_rest_sleeper", {"target_state": "{_target_state_id}"}),
                    _when(
                        "target_state.timed_condition_source_ids != null and "
                        "len(target_state.timed_condition_source_ids) > 0",
                        [
                            _call(
                                "refresh_srd_timed_condition_target",
                                {"target_state": "{_target_state_id}", "lasting_state": "state"},
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _alarm_effects():
    """Settle this source consequence using its retained fields and actual clock."""
    return [
        _set("_trigger_alarm_origin", "_trigger_origin"),
        _when(
            "_trigger_record.alarm_mode == 'mental'",
            [
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_trigger_record.caster}"},
                    POSITION_EXPORTS,
                ),
                _call(
                    "measure_srd_information_distance",
                    {"start": "{_trigger_alarm_origin}", "end": "{_info_position}"},
                    ["_scene_distance"],
                ),
                _set(
                    "_trigger_alert_delivered",
                    "_scene_distance <= 5280 and "
                    "_info_scene_entity.metadata.plane == _trigger_plane",
                ),
                _when(
                    "_trigger_alert_delivered == true",
                    [
                        _call("wake_srd_alarm_sleep", {"target_state": "{_trigger_record.caster}"}),
                        _set(
                            "_trigger_result",
                            "'Mental Alarm ping delivered.'",
                        ),
                    ],
                ),
                _when(
                    "_trigger_alert_delivered != true",
                    [_set("_trigger_result", "'Caster outside the mental Alarm range.'")],
                ),
            ],
        ),
        _when(
            "_trigger_record.alarm_mode == 'audible'",
            [
                _call(
                    "resolve_srd_environment_point",
                    {"point": "{_trigger_origin}", "plane": "{_trigger_plane}"},
                    ["_environment_silence"],
                ),
                _set("_trigger_alert_delivered", "_environment_silence != true"),
                _set("_trigger_sound_ends_seconds", "state.lasting_tick * 3 + 10"),
                _set(
                    "_trigger_sound_patch",
                    {
                        "last_sound_ends_seconds": "_trigger_sound_ends_seconds",
                        "last_sound_origin": "_trigger_origin",
                        "last_sound_plane": "_trigger_plane",
                    },
                ),
                *_update_record("_trigger_sound_patch"),
                _set("_trigger_result", "'Handbell Alarm for 10 seconds, audible within 60 feet.'"),
                _when(
                    "_environment_silence == true",
                    [
                        _set(
                            "_trigger_result",
                            "'Alarm activated; its handbell is blocked by Silence.'",
                        )
                    ],
                ),
            ],
        ),
    ]


def _mouth_effects():
    """Settle this source consequence using its retained fields and actual clock."""
    return [
        _set("_trigger_message", "''"),
        _set("_trigger_words", "_trigger_record.words"),
        _loop(
            "_trigger_words",
            "_trigger_word",
            [
                {
                    "condition": "_trigger_message == ''",
                    "on_true": [_set("_trigger_message", "_trigger_word")],
                    "on_false": [
                        _call(
                            "record_srd_result_text",
                            {"text": "{_trigger_message} {_trigger_word}"},
                            ["_recorded_result_text"],
                        ),
                        _set("_trigger_message", "_recorded_result_text"),
                    ],
                },
            ],
        ),
        _set(
            "_trigger_delivery_ends_seconds",
            "state.lasting_tick * 3 + _trigger_record.delivery_seconds",
        ),
        _set("_trigger_result", "_trigger_message"),
        _when(
            "_trigger_record.repeat != true",
            [
                _set(
                    "_trigger_delivery_patch",
                    {
                        "last_delivery_ends_seconds": "_trigger_delivery_ends_seconds",
                        "expires_seconds": "_trigger_delivery_ends_seconds",
                    },
                )
            ],
        ),
        *_update_record("_trigger_delivery_patch"),
    ]


def _event_tool():
    effects = _find() + [
        _call("check_srd_spell_trigger_record", {"record": "{_trigger_record}"}, RECORD_EXPORTS),
        _set("_trigger_fired", False),
        _set("_trigger_result", "'No matching trigger.'"),
        _set("_trigger_details", []),
        _when(
            "_trigger_active != true",
            [
                _call("retire_srd_spell_trigger", {"trigger_id": "{trigger_id}"}),
                _set("_trigger_result", "_trigger_retirement"),
            ],
        ),
        _when(
            "_trigger_active == true",
            [
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_event_actor_id}"},
                    POSITION_EXPORTS,
                ),
                _set("_trigger_event_position", "_info_position"),
                _set("_trigger_event_entity", "_info_scene_entity"),
                _require(
                    (
                        "_trigger_event_entity.metadata.participant_ref == _event_actor_id and e"
                        "vent_actor.creature_kind != 'object'"
                    ),
                    "A trigger event needs an actual admitted creature.",
                ),
                _call(
                    "measure_srd_information_distance",
                    {"start": "{_trigger_origin}", "end": "{_trigger_event_position}"},
                    ["_scene_distance"],
                ),
                _set("_trigger_event_distance", "_scene_distance"),
                _set(
                    "_trigger_matches",
                    (
                        "fictional_match == true and event_kind in _trigger_record.events and _t"
                        "rigger_event_entity.metadata.plane == _trigger_plane and _event_actor_i"
                        "d not in _trigger_record.exempt_creatures and (_trigger_record.password"
                        " == '' or password != _trigger_record.password)"
                    ),
                ),
                _when(
                    "_trigger_record.spell == 'symbol' and _trigger_record.activated_tick != null",
                    [
                        _set("_trigger_matches", False),
                    ],
                ),
                _when(
                    "len(_trigger_record.creature_types) > 0",
                    [
                        _set(
                            "_trigger_matches",
                            (
                                "_trigger_matches == true and event_actor.creature_type in _trig"
                                "ger_record.creature_types"
                            ),
                        )
                    ],
                ),
                _when(
                    "_trigger_record.spell == 'alarm' and _trigger_record.anchor_kind == 'area'",
                    [
                        _call(
                            "qualify_srd_trigger_cube_volume",
                            {
                                "candidate": "{_trigger_event_entity}",
                                "origin": "{_trigger_origin}",
                                "extent": "{_trigger_record.extent}",
                            },
                            ["_trigger_cube_intersects"],
                        ),
                        _set(
                            "_trigger_matches",
                            "_trigger_matches == true and _trigger_cube_intersects == true",
                        ),
                    ],
                ),
                _when(
                    "_trigger_record.spell == 'alarm' and _trigger_record.anchor_kind != 'area'",
                    [
                        _set(
                            "_trigger_matches",
                            "_trigger_matches == true and _trigger_event_distance <= 5",
                        )
                    ],
                ),
                _when(
                    "_trigger_record.spell == 'glyph_of_warding' or _trigger_record.spell == "
                    "'symbol'",
                    [
                        _set("_trigger_event_limit", 5),
                        _when(
                            "event_kind == 'approach'",
                            [_set("_trigger_event_limit", "_trigger_record.approach_feet")],
                        ),
                        _when(
                            "event_kind == 'step'",
                            [_set("_trigger_event_limit", "_trigger_record.glyph_diameter * 0.5")],
                        ),
                        _set(
                            "_trigger_matches",
                            (
                                "_trigger_matches == true and (event_kind == 'see' or _trigger_e"
                                "vent_distance <= _trigger_event_limit)"
                            ),
                        ),
                        _when(
                            "event_kind == 'see'",
                            [
                                _call(
                                    "resolve_srd_illumination_point_sight",
                                    {
                                        "observer_state": "{_event_actor_id}",
                                        "point": "{_trigger_origin}",
                                        "perception_plane": "{_trigger_plane}",
                                    },
                                    ["_illumination_point_can_see"],
                                ),
                                _set(
                                    "_trigger_matches",
                                    (
                                        "_trigger_matches == true and _illumina"
                                        "tion_point_can_see == true"
                                    ),
                                ),
                            ],
                        ),
                    ],
                ),
                _when(
                    "_trigger_record.spell == 'magic_mouth'",
                    [
                        _set(
                            "_trigger_matches",
                            (
                                "_trigger_matches == true and _trigger_event_distance <= 30 and "
                                "(_trigger_record.last_delivery_ends_seconds == null or state.la"
                                "sting_tick * 3 >= _trigger_record.last_delivery_ends_seconds)"
                            ),
                        ),
                        _when(
                            "event_kind == 'audible'",
                            [
                                _call(
                                    "resolve_srd_environment_segment",
                                    {
                                        "start": "{_trigger_origin}",
                                        "end": "{_trigger_event_position}",
                                        "plane": "{_trigger_plane}",
                                    },
                                    ["_environment_sound_blocked"],
                                ),
                                _set(
                                    "_trigger_matches",
                                    (
                                        "_trigger_matches == true and _environment_sound_blocked"
                                        " != true"
                                    ),
                                ),
                            ],
                        ),
                        _when(
                            "event_kind == 'visual'",
                            [
                                _call(
                                    "srd_information_barriers",
                                    {
                                        "start": "{_trigger_origin}",
                                        "end": "{_trigger_event_position}",
                                        "rule": "solid",
                                    },
                                    ["_information_blocked"],
                                ),
                                _call(
                                    "resolve_srd_environment_segment",
                                    {
                                        "start": "{_trigger_origin}",
                                        "end": "{_trigger_event_position}",
                                        "plane": "{_trigger_plane}",
                                    },
                                    ["_environment_sight_obscured"],
                                ),
                                _require(
                                    ("_trigger_event_entity.metadata.lighting != null"),
                                    ("The observed event needs actual ambient lighting."),
                                ),
                                _call(
                                    "resolve_srd_information_light",
                                    {
                                        "point": "{_trigger_event_position}",
                                        "plane": "{_trigger_plane}",
                                        "ambient": ("{_trigger_event_entity.metadata.lighting}"),
                                    },
                                    ["_information_light"],
                                ),
                                _call(
                                    "check_srd_illumination_subject",
                                    {"subject": "{_event_actor_id}"},
                                    ["_illumination_invisible", "_illumination_suppressed"],
                                ),
                                _set(
                                    "_trigger_event_invisible",
                                    "'invisible' in event_actor.conditions",
                                ),
                                _when(
                                    (
                                        "event_actor.illumination_invisible_managed == true an"
                                        "d event_actor.illumination_invisible_native != true"
                                    ),
                                    [_set("_trigger_event_invisible", "_illumination_invisible")],
                                ),
                                _when(
                                    "_illumination_suppressed == true",
                                    [_set("_trigger_event_invisible", False)],
                                ),
                                _set(
                                    "_trigger_matches",
                                    (
                                        "_trigger_matches == true and _information_blocked != tr"
                                        "ue and _environment_sight_obscured != true and (_inform"
                                        "ation_light == 'bright' or _information_light == 'dim')"
                                        " and _trigger_event_invisible != true"
                                    ),
                                ),
                            ],
                        ),
                    ],
                ),
                _when(
                    "_trigger_matches == true",
                    [
                        _set("_trigger_fired", True),
                        _set("_trigger_result", "'Trigger activated.'"),
                        _when(
                            "_trigger_record.spell == 'alarm'",
                            _alarm_effects(),
                        ),
                        _when(
                            "_trigger_record.spell == 'magic_mouth'",
                            _mouth_effects(),
                        ),
                        _when(
                            "_trigger_record.spell == 'glyph_of_warding'",
                            [
                                _call(
                                    "resolve_srd_explosive_glyph",
                                    {
                                        "record": "{_trigger_record}",
                                        "origin": "{_trigger_origin}",
                                        "plane": "{_trigger_plane}",
                                    },
                                    [
                                        "_trigger_damage_roll",
                                        "_trigger_damage_total",
                                        "_trigger_damage_details",
                                    ],
                                ),
                                _set("_trigger_details", "_trigger_damage_details"),
                                _call("retire_srd_spell_trigger", {"trigger_id": "{trigger_id}"}),
                            ],
                        ),
                        _when(
                            "_trigger_record.spell == 'symbol'",
                            [
                                _call(
                                    "activate_srd_symbol",
                                    {"trigger_id": "{trigger_id}"},
                                    [
                                        "_symbol_details",
                                        "_symbol_damage_roll",
                                        "_symbol_damage_total",
                                    ],
                                ),
                                _set("_trigger_details", "_symbol_details"),
                                _set("_trigger_damage_roll", "_symbol_damage_roll"),
                                _set("_trigger_damage_total", "_symbol_damage_total"),
                                _set(
                                    "_trigger_result",
                                    "'Symbol activated: sixty-foot Dim Light for ten minutes.'",
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    ]

    # The repeat record receives a typed patch after the actual delivery endpoint is known.
    def patch(value):
        if isinstance(value, list):
            for i, effect in enumerate(value):
                if effect.get("set", {}).get("field") == "_trigger_delivery_ends_seconds":
                    value.insert(
                        i + 1,
                        _set(
                            "_trigger_delivery_patch",
                            {"last_delivery_ends_seconds": "_trigger_delivery_ends_seconds"},
                        ),
                    )
                    break
            for effect in value:
                patch(effect)
        elif isinstance(value, dict):
            for child in value.values():
                if isinstance(child, (dict, list)):
                    patch(child)

    patch(effects)
    public = _public(
        _tool_scene(
            (
                "Classify an authored fictional trigger event; the package derives range, expiry"
                " and effects."
            ),
            [
                _parameter("trigger_id", "integer", minimum=1),
                _entity("event_actor"),
                _parameter("event_kind", "enum", enum_values=EVENTS),
                _parameter(
                    "fictional_match",
                    "boolean",
                    description=(
                        "GM classification that this event matches the saved fiction"
                        "al description; not a range, visibility, roll or damage ver"
                        "dict."
                    ),
                ),
                _optional("password", default=""),
            ],
            effects,
        ),
        "spells",
        "Saved spell trigger",
        [
            ("Activated", "_trigger_fired"),
            ("Result", "_trigger_result"),
        ],
    )
    public["metadata"]["result_card"]["details"] = {"path": "context._trigger_details"}
    return public


def _sound_tool():
    """The saved handbell endpoint survives reopen; hearing uses real listener geometry."""
    return _tool_scene(
        "Qualify an actual listener hearing the saved ten-second Alarm handbell.",
        [
            _parameter("trigger_id", "integer", minimum=1),
            _entity("listener_state"),
        ],
        _find()
        + [
            _set("_trigger_alarm_heard", False),
            _when(
                (
                    "_trigger_record.spell == 'alarm' and _trigger_record.alarm_mode == 'audible' "
                    "and _trigger_record.last_sound_ends_seconds != null and state.lasting_tick * "
                    "3 < _trigger_record.last_sound_ends_seconds and not ('deafened' in listener_s"
                    "tate.conditions)"
                ),
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_listener_state_id}"},
                        POSITION_EXPORTS,
                    ),
                    _set("_trigger_listener_plane", "_info_scene_entity.metadata.plane"),
                    _call(
                        "measure_srd_information_distance",
                        {"start": "{_trigger_record.last_sound_origin}", "end": "{_info_position}"},
                        ["_scene_distance"],
                    ),
                    _set(
                        "_trigger_alarm_heard",
                        (
                            "_scene_distance <= 60 and _trigger_listener_plane "
                            "== _trigger_record.last_sound_plane"
                        ),
                    ),
                    _call(
                        "resolve_srd_environment_segment",
                        {
                            "start": "{_trigger_record.last_sound_origin}",
                            "end": "{_info_position}",
                            "plane": "{_trigger_record.last_sound_plane}",
                        },
                        ["_environment_sound_blocked"],
                    ),
                    _set(
                        "_trigger_alarm_heard",
                        ("_trigger_alarm_heard == true and _environment_sound_blocked != true"),
                    ),
                ],
            ),
        ],
    )


def _notice_tool():
    return _public(
        _tool(
            "Make the actual Wisdom Perception check against a saved glyph's original DC.",
            [
                _parameter("trigger_id", "integer", minimum=1),
                _entity("observer_state"),
            ],
            _find()
            + [
                _require(
                    (
                        "_trigger_record.spell == 'glyph_of_warding' or _trigger_record.spell =="
                        " 'symbol'"
                    ),
                    "This source is not a nearly imperceptible glyph.",
                ),
                _call(
                    "check_srd_spell_trigger_record",
                    {"record": "{_trigger_record}"},
                    RECORD_EXPORTS,
                ),
                _require("_trigger_active == true", "The glyph has ended."),
                _require(
                    "observer_state.skill_modifiers.perception != null",
                    "The observer needs its actual saved Wisdom Perception modifier.",
                ),
                _call(
                    "resolve_srd_illumination_point_sight",
                    {
                        "observer_state": "{_observer_state_id}",
                        "point": "{_trigger_origin}",
                        "perception_plane": "{_trigger_plane}",
                    },
                    ["_illumination_point_can_see"],
                ),
                _require(
                    "_illumination_point_can_see == true",
                    "The observer cannot inspect the glyph at its actual location.",
                ),
                _call(
                    "d20_test",
                    {
                        "actor_state": "{_observer_state_id}",
                        "ability": "wisdom",
                        "kind": "ability_check",
                        "ability_modifier": "{observer_state.skill_modifiers.perception}",
                        "proficiency_bonus": 0,
                        "proficiency_factor": "none",
                        "circumstantial_modifier": 0,
                        "target_number": "{_trigger_record.save_dc}",
                        "advantage": False,
                        "disadvantage": False,
                    },
                    ["_d20_test", "_outcome", "_adjusted_total"],
                ),
                _set("_glyph_notice_dice", "_d20_test.results"),
            ],
        ),
        "spells",
        "Notice glyph",
        [("Result", "_outcome"), ("Dice", "_glyph_notice_dice"), ("Total", "_adjusted_total")],
    )


def _tools(runtime=None):
    tools = {
        **_record_tools(),
        **_symbol_fear_movement_tools(),
        "wake_srd_alarm_sleep": _wake_tool(),
        "apply_srd_trigger_spell": _install_tool(),
        "qualify_srd_trigger_sphere_volume": _sphere_tool(),
        "qualify_srd_trigger_cube_volume": _cube_tool(),
        "resolve_srd_explosive_glyph": _glyph_tool(),
        "resolve_srd_spell_trigger": _event_tool(),
        "notice_srd_spell_glyph": _notice_tool(),
        "check_srd_alarm_listener": _sound_tool(),
        "apply_srd_symbol_target": _symbol_target_tool(),
        "resolve_srd_symbol_targets": _symbol_group_tool(),
        "activate_srd_symbol": _symbol_activation_tool(),
        "resolve_srd_symbol_boundary": _symbol_boundary_tool(),
        "install_srd_symbol_light": _symbol_light_tool(),
        "check_srd_symbol_light_source": _symbol_light_guard(),
        "check_srd_symbol_afflictions": _symbol_affliction_tool(),
        "qualify_srd_symbol_communication": _symbol_communication_tool(),
        "qualify_srd_symbol_turn": _symbol_turn_tool(),
        "resolve_srd_symbol_exploration_round": _symbol_exploration_tool(),
        "resolve_srd_symbol_fear_sight": _symbol_fear_sight_tool(
            projected=runtime is not None and "qualify_srd_travel_perception" in runtime["tools"]
        ),
        "shake_srd_symbol_sleeper": _symbol_shake_tool(),
    }
    if runtime is not None:
        tools["collect_srd_trigger_area_cover_targets"] = _cover_tool(runtime)
        tools["resolve_srd_symbol_save"] = _symbol_save_tool(runtime)
        tools.update(_symbol_query_sight_tools(runtime))
        tools.update(_symbol_consumers(runtime))
        tools.update(_symbol_communication_consumers(runtime))
        tools.update(_symbol_fear_movement_consumers(runtime))
    return tools


def extend_runtime(text):
    """Emit the independent owner; root binds paid casts and actual movement callbacks."""
    runtime = _load_runtime(text)
    text = _replace_tools(text, _tools(runtime))
    schema = yaml.safe_dump({"trigger_spell": _schema()}, sort_keys=False)
    rendered = "".join("      " + line for line in schema.splitlines(True))
    if "      trigger_spell:" in text:
        lines = text.splitlines(keepends=True)
        start = next(i for i, line in enumerate(lines) if line == "      trigger_spell:\n")
        end = start + 1
        while end < len(lines) and (
            not lines[end].strip() or len(lines[end]) - len(lines[end].lstrip()) > 6
        ):
            end += 1
        text = "".join(lines[:start]) + rendered + "\n" + "".join(lines[end:])
    else:
        text = text.replace(
            "      divination_spell:",
            rendered + "\n      divination_spell:",
            1,
        )
    return text
