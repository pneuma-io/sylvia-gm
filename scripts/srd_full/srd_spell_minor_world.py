"""Four source-pinned cantrips change admitted scenery and retain temporary wonders."""

from copy import deepcopy

import yaml

if __package__:
    from .srd_information_spells import ACTIVE
    from .srd_scene_relations import POSITION_EXPORTS, _array, _snapshot, _tool_scene
    from .srd_spell_object_content import (
        GEOMETRY_EXPORTS,
        _admission_guard,
        _held_object_geometry,
        _lookup_object,
        _metadata_patch,
        _new_scene_object,
        _now,
    )
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from .srd_timed_senses import _loop, _optional, _push, _replace_tools, _RuntimeDumper
else:
    from srd_information_spells import ACTIVE
    from srd_scene_relations import POSITION_EXPORTS, _array, _snapshot, _tool_scene
    from srd_spell_object_content import (
        GEOMETRY_EXPORTS,
        _admission_guard,
        _held_object_geometry,
        _lookup_object,
        _metadata_patch,
        _new_scene_object,
        _now,
    )
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from srd_timed_senses import _loop, _optional, _push, _replace_tools, _RuntimeDumper


SOURCE = {
    "druidcraft": ([126, 127], "Druidcraft", "Earthquake"),
    "elementalism": (127, "Elementalism", "Enhance Ability"),
    "prestidigitation": (154, "Prestidigitation", "Prismatic Spray"),
    "thaumaturgy": (169, "Thaumaturgy", "Thunderwave"),
}
SOURCE_PAGES = {
    126: "b15c07da3a8dd3547ba596372da6f13f6523d40cd3da746224fc3d75cf6b29b4",
    127: "17be018e35fb0282e55954f4b5ddb8fb3336e715278c1d31fd854fec8f00cd84",
    154: "c3b2f28d796a87cb70e0a6f46eaa5e148320b4ceb0ad3c28ab351c2b413e0da0",
    169: "49b2000fd8566ea098e4052af87257f659dc929358cae97ddd4eb3740ca77e84",
}
PROFILES = {
    spell: {
        "family": "minor_world_spell",
        "base_level": 0,
        "casting_time": "action",
        "range_kind": "distance",
        "range_feet": feet,
        "concentration": False,
        "minor_world_mode": spell,
    }
    for spell, feet in (
        ("druidcraft", 30),
        ("elementalism", 30),
        ("prestidigitation", 10),
        ("thaumaturgy", 30),
    )
}
PROFILES["druidcraft"].update(sensor_rounds=1, forecast_hours=24, sensory_cube_feet=5)
PROFILES["elementalism"].update(
    cube_feet=5,
    sculpt_cube_feet=1,
    scent_rounds=10,
    water_cups=1,
    evaporation_rounds=10,
    sculpt_rounds=600,
)
PROFILES["prestidigitation"].update(
    maximum_noninstantaneous_effects=3,
    maximum_material_cubic_feet=1,
    sensation_rounds=600,
    mark_rounds=600,
)
PROFILES["thaumaturgy"].update(
    maximum_minute_effects=3, wonder_rounds=10, maximum_voice_multiplier=3
)
MODES = {
    "druidcraft": ["weather_sensor", "bloom", "sensory_effect", "fire_play"],
    "elementalism": ["beckon_air", "beckon_earth", "beckon_fire", "beckon_water", "sculpt_element"],
    "prestidigitation": [
        "sensory_effect",
        "fire_play",
        "clean_soil",
        "minor_sensation",
        "magic_mark",
        "minor_creation",
    ],
    "thaumaturgy": [
        "altered_eyes",
        "booming_voice",
        "fire_play",
        "invisible_hand",
        "phantom_sound",
        "tremors",
    ],
}
KINDS = [
    "flower",
    "seed_pod",
    "leaf_bud",
    "dirt",
    "sand",
    "water",
    "fire",
    "smoke",
    "mist",
    "candle",
    "torch",
    "lamp",
    "campfire",
    "small_campfire",
    "door",
    "window",
    "shutter",
    "surface",
    "other",
]
PUBLIC_TOOLS = {
    "record_srd_minor_world_subject",
    "record_srd_local_weather_forecast",
    "record_srd_hand_sized_form",
    "inspect_srd_minor_world_effects",
    "consume_srd_minor_world_water",
}
CAST_CHOICES = [
    _optional(
        "minor_mode", "enum", "sensory_effect", enum_values=sorted(set(sum(MODES.values(), [])))
    ),
    _optional("minor_object_id", default=""),
    _array("minor_point", default=[0, 0, 0]),
    _optional(
        "minor_cube_face",
        "enum",
        "up",
        enum_values=["east", "west", "north", "south", "up", "down"],
    ),
    _optional("minor_description", default=""),
    _optional(
        "minor_operation",
        "enum",
        "light",
        enum_values=[
            "light",
            "snuff",
            "flicker",
            "brighten",
            "dim",
            "color",
            "clean",
            "soil",
            "chill",
            "warm",
            "flavor",
            "open",
            "close",
        ],
    ),
    _optional("minor_water_mode", "enum", "mist", enum_values=["mist", "cup"]),
    _optional("minor_earth_mode", "enum", "shroud", enum_values=["shroud", "word"]),
    _optional("minor_earth_material", "enum", "dirt", enum_values=["dirt", "sand"]),
    _optional("minor_color", default=""),
    _optional("minor_scent", default=""),
    _optional("minor_retire_source_id", default=""),
    _optional("minor_form_id", default=""),
    _optional("minor_output_id", default=""),
]


def validate_profiles(definitions):
    """Reject alterations of printed cantrip quantities, timing or mechanical benefits."""
    for spell, definition in definitions.items():
        if definition.get("family") == "minor_world_spell" and definition != PROFILES.get(spell):
            raise SystemExit(f"Minor world source profile differs: {spell}")


def _schema():
    fields = {
        name: {
            "type": "boolean"
            if type(value) is bool
            else "integer"
            if type(value) is int
            else "string"
        }
        for profile in PROFILES.values()
        for name, value in profile.items()
        if name != "family"
    }
    return {
        "type": "object",
        "properties": fields,
        "required": sorted(set.intersection(*(set(p) for p in PROFILES.values())) - {"family"}),
        "additionalProperties": False,
    }


def _body_source():
    """Bind fictional hand-fit classification to the actual body's current form source."""
    return [
        _set("_minor_body_source", "''"),
        _when(
            "caster_state.form_current_source_id != null",
            [_set("_minor_body_source", "caster_state.form_current_source_id")],
        ),
    ]


def _fact_tools():
    subject = _tool_scene(
        "Admit an actual object's fictional kind, nonliving material and held-open facts once.",
        [
            _parameter("object_id"),
            _parameter("kind", "enum", enum_values=KINDS),
            _parameter("nonliving", "boolean"),
            _optional("held_open", "boolean", False),
            _optional("lit", "boolean", False),
            _optional("exposed", "boolean", False),
        ],
        [
            *_admission_guard(),
            _require(
                "_object_scene.metadata.srd_minor_world_subject == null",
                "This object's kind is already admitted.",
            ),
            _require(
                "_object_scene.footprint.kind == 'box'",
                "Admit actual box geometry for this object.",
            ),
            *_metadata_patch(
                {
                    "srd_minor_world_subject": {
                        "kind": "kind",
                        "nonliving": "nonliving",
                        "held_open": "held_open",
                    }
                }
            ),
            *_lookup_object(),
            _when(
                (
                    "kind == 'candle' or kind == 'torch' or kind == 'lamp' or kind == "
                    "'campfire' or kind == 'small_campfire' or kind == 'fire'"
                ),
                [
                    _set("_minor_flame", None),
                    _when(
                        "_object_scene.metadata.srd_ordinary_flame != null",
                        [_set("_minor_flame", "_object_scene.metadata.srd_ordinary_flame")],
                    ),
                    _when(
                        "_minor_flame == null",
                        [
                            *_metadata_patch(
                                {
                                    "srd_ordinary_flame": {
                                        "kind": "'ordinary_flame'",
                                        "burning": "lit",
                                        "exposed": "exposed",
                                    }
                                }
                            )
                        ],
                    ),
                ],
            ),
        ],
    )
    forecast = _tool_scene(
        (
            "Admit the GM's fictional local next-24-hour weather forecast at the actual "
            "caster location."
        ),
        [_entity("caster_state"), _parameter("forecast")],
        [
            _require(
                "combat.encounter_active != true and combat.pending_action == null",
                "Admit fictional weather outside combat.",
            ),
            _require("forecast != ''", "Describe the actual local forecast."),
            *_now(),
            _set("_minor_forecast_origin", None),
            _set("_minor_forecast_plane", None),
            _when(
                "state.spatial_scene != null",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_caster_state_id}"},
                        POSITION_EXPORTS,
                    ),
                    _set("_minor_forecast_origin", "_info_scene_entity.position"),
                    _set("_minor_forecast_plane", "_info_scene_entity.metadata.plane"),
                ],
            ),
            _set(
                "caster_state.srd_local_forecast",
                {
                    "origin": "_minor_forecast_origin",
                    "plane": "_minor_forecast_plane",
                    "forecast": "forecast",
                    "recorded_tick": "_object_now",
                    "through_tick": "_object_now + 28800",
                },
            ),
            _when(
                "state.spatial_scene == null",
                [
                    _set("caster_state.srd_local_forecast.origin", None),
                    _set("caster_state.srd_local_forecast.plane", None),
                ],
            ),
        ],
    )
    hand = _tool_scene(
        (
            "Classify a fictional trinket or image as fitting this actual caster's hand, with"
            " its admitted depiction geometry."
        ),
        [
            _entity("caster_state"),
            _parameter("form_id"),
            _parameter("fiction"),
            _parameter("kind", "enum", enum_values=["trinket", "image"]),
            _array("extent"),
        ],
        [
            _require(
                "combat.encounter_active != true and combat.pending_action == null",
                "Admit hand-sized fictional forms outside combat.",
            ),
            _require(
                "caster_state.is_alive == true and form_id != '' and fiction != ''",
                "Use an actual living caster and a unique fictional form.",
            ),
            *_body_source(),
            *[
                _require(f"extent[{i}] > 0", "Depiction geometry must have positive extent.")
                for i in range(3)
            ],
            _set("_minor_hand_forms", []),
            _when(
                "caster_state.srd_hand_sized_forms != null",
                [_set("_minor_hand_forms", "caster_state.srd_hand_sized_forms")],
            ),
            _loop(
                "_minor_hand_forms",
                "_minor_form",
                [_require("_minor_form.id != form_id", "This form ID is already classified.")],
            ),
            _push(
                "_minor_hand_forms",
                {
                    "id": "form_id",
                    "fiction": "fiction",
                    "kind": "kind",
                    "extent": "extent",
                    "caster": "_caster_state_id",
                    "body_source": "_minor_body_source",
                },
            ),
            _set("caster_state.srd_hand_sized_forms", "_minor_hand_forms"),
        ],
    )
    return {
        "record_srd_minor_world_subject": subject,
        "record_srd_local_weather_forecast": forecast,
        "record_srd_hand_sized_form": hand,
    }


def _source_record(*, next_turn=False, cube=False):
    return {
        "kind": "'minor_world_spell'",
        "spell": "spell",
        "source_id": "_minor_source_id",
        "spell_level": 0,
        "source_sha256": "_minor_source_hash",
        "caster": "_caster_state_id",
        "target": "_caster_state_id",
        "owner": "_caster_state_id",
        "phase": "'end'",
        "turn": "_minor_expiry_turn",
        "mode": "minor_mode",
        "object_id": "_minor_subject_id",
        "origin": "_world_origin",
        "plane": "_world_plane",
        "started_tick": "_object_now",
        "expires_tick": None if next_turn else "_minor_expires_tick",
        "dispel_ongoing": "_minor_ongoing",
        "description": "_minor_description",
        "operation": "minor_operation",
        "color": "minor_color",
        "scent": "minor_scent",
        "output_id": "_minor_created_id",
        "water_gallons": "_minor_water_gallons",
        "geometry": {
            "kind": "'cube'",
            "face": "minor_cube_face",
            "min": "_minor_cube_min",
            "max": "_minor_cube_max",
        }
        if cube
        else {
            "kind": "'point'",
            "origin": "_world_origin",
            "plane": "_world_plane",
        },
    }


def _retain(rounds, *, ongoing=False, next_turn=False, cube=False):
    "Retain exact source identity; temporary consequences of Instantaneous spells remain distinct."
    return [
        _set("_minor_expires_tick", None if next_turn else f"_object_now + {rounds} * 2"),
        _set("_minor_expiry_turn", 1 if next_turn else 2147483647),
        _when(
            "caster_state.turns_started != null and minor_mode == 'minor_creation'",
            [_set("_minor_expiry_turn", "caster_state.turns_started + 1")],
        ),
        _set("_minor_ongoing", ongoing),
        _set("_minor_source", _source_record(next_turn=next_turn, cube=cube)),
        _when(
            "state.spatial_scene == null",
            [
                _set("_minor_no_scene", {"origin": None, "plane": None, "geometry": None}),
                _set("_minor_source", "merge(_minor_source, _minor_no_scene)"),
            ],
        ),
        *(
            [
                _when(
                    "combat.encounter_active != true",
                    [
                        _set("_minor_clock_fallback", {"expires_tick": "_object_now + 4"}),
                        _set("_minor_source", "merge(_minor_source, _minor_clock_fallback)"),
                    ],
                )
            ]
            if next_turn
            else []
        ),
        _call(
            "retain_srd_minor_world_effect",
            {
                "caster_state": "{_caster_state_id}",
                "record": "{_minor_source}",
                "retire_source_id": "{minor_retire_source_id}",
            },
            ["_minor_source_id"],
        ),
    ]


def _object_geometry():
    return [
        _require("minor_object_id != ''", "Choose an actual scene object."),
        _call(
            "resolve_srd_object_content_geometry",
            {
                "caster_state": "{_caster_state_id}",
                "object_id": "{minor_object_id}",
                "range_feet": "{_minor_profile.range_feet}",
                "requires_sight": False,
            },
            GEOMETRY_EXPORTS,
        ),
        _require(
            "object_id == minor_object_id", "The owning wrapper must bind the actual object ID."
        ),
        _set("_minor_subject_id", "minor_object_id"),
        _set("_world_origin", "_object_scene.position"),
        _set("_world_plane", "_object_scene.metadata.plane"),
        _require(
            "_object_scene.metadata.srd_minor_world_subject != null",
            "Admit the actual fictional object kind first.",
        ),
        _set("_minor_subject", "_object_scene.metadata.srd_minor_world_subject"),
    ]


def _box_limit(volume=False):
    if volume:
        return [
            _require(
                (
                    "_object_scene.footprint.half_extents[0] * 2 * "
                    "_object_scene.footprint.half_extents[1] * 2 * _object_scene.height <= 1"
                ),
                "The actual object exceeds one cubic foot.",
            )
        ]
    return [
        _require(
            (
                "_object_scene.footprint.half_extents[0] * 2 <= 1 and "
                "_object_scene.footprint.half_extents[1] * 2 <= 1 and _object_scene.height <="
                " 1"
            ),
            "The actual element must fit inside a one-foot Cube.",
        )
    ]


def _fire_play():
    return [
        *_object_geometry(),
        _set("_minor_fire_kinds", ["candle", "torch", "campfire", "small_campfire"]),
        _when(
            "spell == 'prestidigitation'",
            [_set("_minor_fire_kinds", ["candle", "torch", "small_campfire"])],
        ),
        _require(
            "_object_scene.metadata.srd_ordinary_flame != null",
            "The actual ordinary flame must be admitted.",
        ),
        _set("_minor_flame", "_object_scene.metadata.srd_ordinary_flame"),
        {
            "condition": "spell == 'thaumaturgy'",
            "on_true": [
                _require(
                    "_minor_flame.burning == true", "Thaumaturgy alters an actual burning flame."
                ),
                _require(
                    (
                        "minor_operation == 'flicker' or minor_operation == 'brighten' or "
                        "minor_operation == 'dim' or minor_operation == 'color'"
                    ),
                    "Choose a printed flame appearance change.",
                ),
                *_retain(10, ongoing=True),
            ],
            "on_false": [
                _require(
                    "_minor_subject.kind in _minor_fire_kinds",
                    "This source cannot light or snuff that kind of object.",
                ),
                _require(
                    "minor_operation == 'light' or minor_operation == 'snuff'",
                    "Choose light or snuff.",
                ),
                _set("_minor_change", {"burning": "minor_operation == 'light'"}),
                _set("_minor_flame", "merge(_minor_flame, _minor_change)"),
                *_metadata_patch({"srd_ordinary_flame": "_minor_flame"}),
            ],
        },
    ]


def _area_cube():
    """Qualify the fixed five-foot Cube within the actual map before consuming its contents."""
    effects = [
        _set("_minor_has_cube", True),
        _set(
            "_minor_cube_min",
            [f"_world_origin[{axis}] - 2.5" for axis in range(3)],
        ),
        _set(
            "_minor_cube_max",
            [f"_world_origin[{axis}] + 2.5" for axis in range(3)],
        ),
    ]
    for face, axis, sign in [
        ("east", 0, 1),
        ("west", 0, -1),
        ("north", 1, 1),
        ("south", 1, -1),
        ("up", 2, 1),
        ("down", 2, -1),
    ]:
        effects += [
            _when(
                f"minor_cube_face == '{face}'",
                [
                    _set(
                        "_minor_cube_min",
                        [
                            (f"_world_origin[{i}]" if sign > 0 else f"_world_origin[{i}] - 5")
                            if i == axis
                            else f"_world_origin[{i}] - 2.5"
                            for i in range(3)
                        ],
                    ),
                    _set(
                        "_minor_cube_max",
                        [
                            (f"_world_origin[{i}] + 5" if sign > 0 else f"_world_origin[{i}]")
                            if i == axis
                            else f"_world_origin[{i}] + 2.5"
                            for i in range(3)
                        ],
                    ),
                ],
            )
        ]
    effects += [
        *[
            _require(
                f"_minor_cube_min[{i}] >= state.spatial_scene.map.bounds.min[{i}] and "
                f"_minor_cube_max[{i}] <= state.spatial_scene.map.bounds.max[{i}]",
                "The actual five-foot Cube must fit inside the admitted map.",
            )
            for i in range(3)
        ],
    ]
    return effects


def _area_objects():
    return [
        *_area_cube(),
        _set("_minor_candidates", "state.spatial_scene.entities"),
        _loop(
            "_minor_candidates",
            "_minor_candidate",
            [
                _when(
                    "_minor_candidate.active == true",
                    [
                        _call(
                            "apply_srd_minor_world_area_subject",
                            {
                                "caster_state": "{_caster_state_id}",
                                "object_id": "{_minor_candidate.id}",
                                "mode": "{minor_mode}",
                                "origin": "{_world_origin}",
                                "plane": "{_world_plane}",
                                "cube_min": "{_minor_cube_min}",
                                "cube_max": "{_minor_cube_max}",
                                "source_id": "{_minor_source_id}",
                            },
                        )
                    ],
                )
            ],
        ),
    ]


def _area_subject_tool(*, object_visibility=False):
    effects = [
        _call("srd_information_scene_entity", {"subject": "{object_id}"}, POSITION_EXPORTS),
        _set("_object_scene", "_info_scene_entity"),
        *(
            [
                _when(
                    "_object_scene.metadata.is_object == true",
                    _held_object_geometry("{_caster_state_id}"),
                )
            ]
            if object_visibility
            else []
        ),
        _set("_minor_subject_min", "_object_scene.position"),
        _set("_minor_subject_max", "_object_scene.position"),
        _when(
            "_object_scene.footprint.kind == 'box'",
            [
                _set(
                    "_minor_subject_min",
                    [
                        "_object_scene.position[0] - _object_scene.footprint.half_extents[0]",
                        "_object_scene.position[1] - _object_scene.footprint.half_extents[1]",
                        "_object_scene.position[2]",
                    ],
                ),
                _set(
                    "_minor_subject_max",
                    [
                        "_object_scene.position[0] + _object_scene.footprint.half_extents[0]",
                        "_object_scene.position[1] + _object_scene.footprint.half_extents[1]",
                        "_object_scene.position[2] + _object_scene.height",
                    ],
                ),
            ],
        ),
        _call(
            "srd_information_barriers",
            {"start": "{origin}", "end": "{_object_scene.position}", "rule": "solid"},
            ["_information_blocked"],
        ),
        _when(
            "_information_blocked != true and _object_scene.metadata.plane == plane and "
            + " and ".join(
                f"_minor_subject_max[{i}] >= cube_min[{i}] and "
                f"_minor_subject_min[{i}] <= cube_max[{i}]"
                for i in range(3)
            ),
            [
                _when(
                    "mode == 'beckon_water'",
                    [
                        *_snapshot(),
                        _set(
                            "_minor_damp_change",
                            {
                                "srd_minor_dampened": {
                                    "source_id": "source_id",
                                    "caster": "_caster_state_id",
                                }
                            },
                        ),
                        _set(
                            "_minor_damp_metadata",
                            "merge(_object_scene.metadata, _minor_damp_change)",
                        ),
                        {
                            "spatial_mutation": {
                                "scene": "state.spatial_scene",
                                "map_id": "{state.spatial_scene.map.id}",
                                "expected_revision": "{_info_snapshot.geometry_revision}",
                                "target": {"kind": "entity", "id": "{object_id}"},
                                "patch": {"metadata": "{_minor_damp_metadata}"},
                            },
                            "store_as": "_minor_damp_mutation",
                        },
                    ],
                ),
                _set("_minor_subject", None),
                _when(
                    "_object_scene.metadata.srd_minor_world_subject != null",
                    [_set("_minor_subject", "_object_scene.metadata.srd_minor_world_subject")],
                ),
                _when(
                    "_minor_subject != null",
                    [
                        _when(
                            (
                                "mode == 'beckon_air' and (_minor_subject.kind == 'door' or "
                                "_minor_subject.kind == 'shutter') and "
                                "_minor_subject.held_open != true"
                            ),
                            [
                                _when(
                                    (
                                        "_object_scene.metadata.srd_object_access != null and"
                                        " _object_scene.metadata.srd_object_access.closed != "
                                        "true"
                                    ),
                                    [
                                        _call(
                                            "apply_srd_minor_world_door",
                                            {
                                                "caster_state": "{_caster_state_id}",
                                                "object_id": "{object_id}",
                                                "operation": "close",
                                                "range_feet": 30,
                                            },
                                        )
                                    ],
                                )
                            ],
                        ),
                        _when(
                            (
                                "mode == 'beckon_fire' and (_minor_subject.kind == 'candle' "
                                "or _minor_subject.kind == 'torch' or _minor_subject.kind == "
                                "'lamp')"
                            ),
                            [
                                _require(
                                    "_object_scene.metadata.srd_ordinary_flame != null",
                                    "The actual lamp or flame must be admitted.",
                                ),
                                _set("_minor_flame", "_object_scene.metadata.srd_ordinary_flame"),
                                _set("_minor_change", {"burning": True}),
                                _set("_minor_flame", "merge(_minor_flame, _minor_change)"),
                                *_metadata_patch({"srd_ordinary_flame": "_minor_flame"}),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    ]
    return _tool_scene(
        "Apply the actual Cube to admitted subjects without selecting caller success verdicts.",
        [
            _entity("caster_state"),
            _parameter("object_id"),
            _parameter("mode"),
            _array("origin"),
            _parameter("plane"),
            _array("cube_min"),
            _array("cube_max"),
            _parameter("source_id"),
        ],
        effects,
    )


def _door_tool():
    return _tool_scene(
        "Change an actual unlocked door's geometry through its existing access owner.",
        [
            _entity("caster_state"),
            _parameter("object_id"),
            _parameter("operation", "enum", enum_values=["open", "close"]),
            _parameter("range_feet", "integer"),
        ],
        [
            _call(
                "resolve_srd_object_content_geometry",
                {
                    "caster_state": "{_caster_state_id}",
                    "object_id": "{object_id}",
                    "range_feet": "{range_feet}",
                    "requires_sight": False,
                },
                GEOMETRY_EXPORTS,
            ),
            _require(
                "_object_scene.metadata.srd_object_access != null", "Admit actual access facts."
            ),
            _set("_minor_access", "_object_scene.metadata.srd_object_access"),
            _require(
                (
                    "len(_minor_access.locks) == 0 and _minor_access.stuck != true and "
                    "_minor_access.barred != true"
                ),
                "An actual mundane lock or restriction blocks the door.",
            ),
            _set("_minor_lock_active", False),
            _set("_minor_locks", []),
            *_now(),
            _when(
                "_object_scene.metadata.srd_arcane_locks != null",
                [_set("_minor_locks", "_object_scene.metadata.srd_arcane_locks")],
            ),
            _loop(
                "_minor_locks",
                "_minor_lock",
                [
                    _when(
                        (
                            "_minor_lock.suppressed_until_tick == null or "
                            "_minor_lock.suppressed_until_tick <= _object_now"
                        ),
                        [_set("_minor_lock_active", True)],
                    )
                ],
            ),
            _require("_minor_lock_active != true", "An actual Arcane Lock blocks this wonder."),
            _set("_minor_access_change", {"closed": "operation == 'close'"}),
            _set("_minor_access", "merge(_minor_access, _minor_access_change)"),
            _set("_minor_movement", "'blocking'"),
            _set("_minor_visibility", "'opaque'"),
            _when(
                "operation == 'open'",
                [_set("_minor_movement", "'passable'"), _set("_minor_visibility", "'clear'")],
            ),
            *_metadata_patch(
                {"srd_object_access": "_minor_access"},
                movement="{_minor_movement}",
                visibility="{_minor_visibility}",
            ),
        ],
    )


def _casting_tool():
    hashes = {
        spell: SOURCE_PAGES[pages[0] if isinstance(pages, list) else pages]
        for spell, (pages, _, _) in SOURCE.items()
    }
    effects = [
        _require(ACTIVE, "A capable living caster is required."),
        {
            "table_lookup": {"table": "definitions.spells", "key": "spell", "required": True},
            "store_as": "_minor_profile",
        },
        _require(
            "_minor_profile.family == 'minor_world_spell'",
            "Use an actual minor world source profile.",
        ),
        _set("_minor_modes", MODES),
        _set("_minor_allowed_modes", "_minor_modes[spell]"),
        _require("minor_mode in _minor_allowed_modes", "This spell does not print that effect."),
        *_now(),
        _call("refresh_srd_minor_world_effects", {}),
        _set("_world_origin", None),
        _set("_world_plane", None),
        _set("_world_lighting", None),
        {
            "condition": "state.spatial_scene != null",
            "on_true": [
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_caster_state_id}"},
                    POSITION_EXPORTS,
                ),
                _set("_world_origin", "_info_scene_entity.position"),
                _set("_world_plane", "_info_scene_entity.metadata.plane"),
                _set("_world_lighting", "_info_scene_entity.metadata.lighting"),
                _require("_world_plane != null", "The actual caster needs an admitted plane."),
            ],
            "on_false": [
                _require(
                    "minor_mode == 'weather_sensor' or minor_mode == 'sensory_effect' or "
                    "minor_mode == 'phantom_sound' or minor_mode == 'tremors' or "
                    "minor_mode == 'booming_voice' or minor_mode == 'altered_eyes'",
                    "This object, area or creation effect needs the admitted spatial scene.",
                ),
            ],
        },
        _set("_minor_subject_id", "''"),
        _set("_minor_created_id", "''"),
        _set("_minor_has_cube", False),
        _when(
            "minor_mode == 'weather_sensor' or minor_mode == 'sensory_effect' or "
            "minor_mode == 'beckon_air' or minor_mode == 'beckon_fire' or "
            "(minor_mode == 'beckon_water' and minor_water_mode == 'mist') or "
            "(minor_mode == 'beckon_earth' and minor_earth_mode == 'shroud') or "
            "minor_mode == 'minor_creation' or minor_mode == 'phantom_sound' or "
            "minor_mode == 'tremors'",
            [
                {
                    "condition": "state.spatial_scene != null",
                    "on_true": [
                        _call(
                            "resolve_srd_object_content_point",
                            {
                                "caster_state": "{_caster_state_id}",
                                "point": "{minor_point}",
                                "range_feet": "{_minor_profile.range_feet}",
                            },
                            ["_world_origin", "_world_plane", "_world_lighting"],
                        ),
                    ],
                    "on_false": [
                        _require(
                            "origin_distance != null and origin_distance >= 0 and "
                            "origin_distance <= _minor_profile.range_feet and "
                            "origin_has_clear_path == true",
                            "Admit a clear sensory origin within the printed spell range.",
                        ),
                    ],
                },
            ],
        ),
        _set("_minor_source_hashes", hashes),
        _set("_minor_source_hash", "_minor_source_hashes[spell]"),
        _when("state.lasting_serial == null", [_set("state.lasting_serial", 0)]),
        {"increment": {"field": "state.lasting_serial", "amount": 1}},
        _call(
            "format_srd_information_projection",
            {"message": "{_caster_state_id}:{spell}:{state.lasting_serial}"},
            ["_info_projection_message"],
        ),
        _set("_minor_source_id", "_info_projection_message"),
        _set("_minor_description", "minor_description"),
        _set("_minor_water_gallons", 0),
        _set("_spell_summary_rows", []),
        _set("_spell_detail_rows", []),
        _when(
            "minor_mode == 'weather_sensor'",
            [
                _require(
                    "caster_state.srd_local_forecast != null",
                    "Admit the actual fictional local weather forecast first.",
                ),
                _set("_minor_forecast", "caster_state.srd_local_forecast"),
                _require(
                    "_minor_forecast.through_tick >= _object_now + 28800",
                    "The forecast must cover the actual caster location for the next 24 hours.",
                ),
                {
                    "condition": "state.spatial_scene != null",
                    "on_true": [
                        _call(
                            "srd_information_scene_entity",
                            {"subject": "{_caster_state_id}"},
                            POSITION_EXPORTS,
                        ),
                        _require(
                            "_minor_forecast.plane == _info_scene_entity.metadata.plane and "
                            + " and ".join(
                                f"_minor_forecast.origin[{i}] == _info_scene_entity.position[{i}]"
                                for i in range(3)
                            ),
                            "The forecast must cover the actual caster location for the next "
                            "24 hours.",
                        ),
                    ],
                    "on_false": [
                        _require(
                            "_minor_forecast.origin == null and _minor_forecast.plane == null",
                            "Admit a current local forecast without stale scene geometry.",
                        ),
                    ],
                },
                _set("_minor_description", "_minor_forecast.forecast"),
                *_retain(1),
                _push(
                    "_spell_detail_rows",
                    {"label": "'Actual next-24-hour forecast'", "value": "_minor_description"},
                ),
            ],
        ),
        _when(
            "minor_mode == 'bloom'",
            [
                *_object_geometry(),
                _require(
                    (
                        "_minor_subject.kind == 'flower' or _minor_subject.kind == 'seed_pod'"
                        " or _minor_subject.kind == 'leaf_bud'"
                    ),
                    "Bloom requires an actual flower, seed pod or leaf bud.",
                ),
                *_metadata_patch({"srd_bloomed": True}),
            ],
        ),
        _when(
            "minor_mode == 'sensory_effect' and spell == 'druidcraft' and "
            "state.spatial_scene != null",
            [*_area_cube()],
        ),
        _when("minor_mode == 'fire_play'", _fire_play()),
        _when(
            (
                "minor_mode == 'beckon_air' or minor_mode == 'beckon_fire' or (minor_mode == "
                "'beckon_water' and minor_water_mode == 'mist')"
            ),
            _area_objects(),
        ),
        _when("minor_mode == 'beckon_fire'", _retain(10, cube=True)),
        _when(
            "minor_mode == 'beckon_earth'",
            [
                {
                    "condition": "minor_earth_mode == 'word'",
                    "on_true": [
                        *_object_geometry(),
                        _require(
                            "_minor_subject.kind == 'dirt' or _minor_subject.kind == 'sand'",
                            "The single word requires actual dirt or sand.",
                        ),
                        _require(
                            (
                                "minor_description != '' and ' ' not in minor_description and"
                                " '\\n' not in minor_description and '\\t' not in "
                                "minor_description and '\\r' not in minor_description"
                            ),
                            "Choose a single word.",
                        ),
                        *_metadata_patch(
                            {
                                "srd_minor_earth_word": {
                                    "caster": "_caster_state_id",
                                    "word": "minor_description",
                                    "handwriting": "_caster_state_id",
                                }
                            }
                        ),
                    ],
                    "on_false": [
                        *[
                            _require(
                                f"_world_origin[{axis}] - 2.5 >= "
                                f"state.spatial_scene.map.bounds.min[{axis}] and "
                                f"_world_origin[{axis}] + 2.5 <= "
                                f"state.spatial_scene.map.bounds.max[{axis}]",
                                "The actual five-foot square must fit the admitted map.",
                            )
                            for axis in (0, 1)
                        ],
                        _set("_minor_ground", []),
                        _when(
                            "state.srd_minor_earth_shrouds != null",
                            [
                                _set("_minor_ground", "state.srd_minor_earth_shrouds"),
                            ],
                        ),
                        _push(
                            "_minor_ground",
                            {
                                "source_id": "_minor_source_id",
                                "source_sha256": repr(SOURCE_PAGES[127]),
                                "caster": "_caster_state_id",
                                "origin": "_world_origin",
                                "plane": "_world_plane",
                                "square_side_feet": 5,
                                "area_square_feet": 25,
                                "layer": "'Thin dirt or sand'",
                                "material": "minor_earth_material",
                                "description": "minor_description",
                            },
                        ),
                        _set("state.srd_minor_earth_shrouds", "_minor_ground"),
                        _set(
                            "state.srd_last_minor_earth_shroud",
                            {
                                "origin": "_world_origin",
                                "plane": "_world_plane",
                                "square_side_feet": 5,
                                "area_square_feet": 25,
                                "description": "minor_description",
                            },
                        ),
                    ],
                }
            ],
        ),
        _when(
            "minor_mode == 'beckon_water' and minor_water_mode == 'cup'",
            [
                *_object_geometry(),
                _require(
                    (
                        "_minor_subject.kind == 'surface' or "
                        "_object_scene.metadata.srd_object_container != null"
                    ),
                    "Choose an actual open container or surface.",
                ),
                _when(
                    "_object_scene.metadata.srd_object_container != null",
                    [
                        _require(
                            "_object_scene.metadata.srd_object_container.is_open == true",
                            "The actual container must be open.",
                        ),
                        _call(
                            "read_srd_minor_world_water",
                            {"object_id": "{minor_object_id}"},
                            ["_minor_water_present"],
                        ),
                        _set(
                            "_minor_existing_water",
                            (
                                "_object_scene.metadata.srd_object_container.water_gallons + "
                                "_minor_water_present"
                            ),
                        ),
                        _when(
                            "_object_scene.metadata.srd_created_supply != null",
                            [
                                _set(
                                    "_minor_existing_water",
                                    (
                                        "_minor_existing_water + "
                                        "_object_scene.metadata.srd_created_supply.water_gallons"
                                    ),
                                )
                            ],
                        ),
                        _require(
                            (
                                "_minor_existing_water + 0.0625 <= "
                                "_object_scene.metadata.srd_object_container.capacity_gallons"
                            ),
                            "The actual vessel has insufficient free capacity for one cup.",
                        ),
                    ],
                ),
                _set("_minor_water_gallons", 0.0625),
                *_retain(10),
            ],
        ),
        _when(
            "minor_mode == 'sculpt_element'",
            [
                *_object_geometry(),
                _set("_minor_elements", ["dirt", "sand", "fire", "smoke", "mist", "water"]),
                _require(
                    "_minor_subject.kind in _minor_elements",
                    "Sculpt Element requires an actual printed element.",
                ),
                *_box_limit(),
                _when(
                    "_minor_subject.kind == 'fire'",
                    [
                        _require(
                            "_object_scene.metadata.srd_ordinary_flame != null and "
                            "_object_scene.metadata.srd_ordinary_flame.burning == true",
                            "Sculpt Fire requires an actual burning fire source.",
                        ),
                    ],
                ),
                *_retain(600),
            ],
        ),
        _when(
            "minor_mode == 'clean_soil'",
            [
                *_object_geometry(),
                *_box_limit(volume=True),
                _require(
                    "minor_operation == 'clean' or minor_operation == 'soil'",
                    "Choose clean or soil.",
                ),
                *_metadata_patch({"srd_minor_cleanliness": "minor_operation"}),
            ],
        ),
        _when(
            "minor_mode == 'minor_sensation'",
            [
                *_object_geometry(),
                *_box_limit(volume=True),
                _require(
                    "_minor_subject.nonliving == true",
                    "Minor Sensation requires actual nonliving material.",
                ),
                _require(
                    (
                        "minor_operation == 'chill' or minor_operation == 'warm' or "
                        "minor_operation == 'flavor'"
                    ),
                    "Choose chill, warm or flavor.",
                ),
                *_retain(600, ongoing=True),
            ],
        ),
        _when(
            "minor_mode == 'magic_mark'",
            [
                *_object_geometry(),
                _require(
                    "minor_description != '' or minor_color != ''",
                    "Choose an actual color, small mark or symbol.",
                ),
                *_retain(600, ongoing=True),
            ],
        ),
        _when(
            "minor_mode == 'minor_creation'",
            [
                *_body_source(),
                _set("_minor_hand_forms", []),
                _when(
                    "caster_state.srd_hand_sized_forms != null",
                    [_set("_minor_hand_forms", "caster_state.srd_hand_sized_forms")],
                ),
                _set("_minor_hand_form", None),
                _loop(
                    "_minor_hand_forms",
                    "_minor_form",
                    [
                        _when(
                            (
                                "_minor_form.id == minor_form_id and _minor_form.caster == "
                                "_caster_state_id and _minor_form.body_source == "
                                "_minor_body_source"
                            ),
                            [_set("_minor_hand_form", "_minor_form")],
                        )
                    ],
                ),
                _require(
                    "_minor_hand_form != null",
                    (
                        "Choose a fictional form classified as fitting this actual caster's "
                        "current hand."
                    ),
                ),
                _set("_minor_created_id", "minor_output_id"),
                *_retain(0, ongoing=True, next_turn=True),
                _set("_minor_extent", "_minor_hand_form.extent"),
                _set("_world_height", "_minor_extent[2]"),
                *_new_scene_object(
                    {
                        "plane": "_world_plane",
                        "lighting": "_world_lighting",
                        "fiction": "_minor_hand_form.fiction",
                        "srd_minor_creation": {
                            "source_id": "_minor_source_id",
                            "caster": "_caster_state_id",
                            "kind": "_minor_hand_form.kind",
                            "magical": "_minor_hand_form.kind != 'trinket'",
                            "damage_capable": False,
                            "monetary_worth_gp": 0,
                        },
                    },
                    identity="minor_output_id",
                    extent="_minor_extent",
                ),
            ],
        ),
        _when(
            (
                "minor_mode == 'altered_eyes' or minor_mode == 'booming_voice' or minor_mode "
                "== 'tremors'"
            ),
            _retain(10, ongoing=True),
        ),
        _when(
            "minor_mode == 'invisible_hand'",
            [
                *_object_geometry(),
                _require(
                    "_minor_subject.kind == 'door' or _minor_subject.kind == 'window'",
                    "Invisible Hand requires an actual door or window.",
                ),
                _require(
                    "minor_operation == 'open' or minor_operation == 'close'",
                    "Choose fly open or slam shut.",
                ),
                _call(
                    "apply_srd_minor_world_door",
                    {
                        "caster_state": "{_caster_state_id}",
                        "object_id": "{minor_object_id}",
                        "operation": "{minor_operation}",
                        "range_feet": 30,
                    },
                ),
            ],
        ),
        _push(
            "_spell_summary_rows",
            {"label": "minor_mode", "value": "'Source-owned minor world effect'"},
        ),
        _push("_spell_detail_rows", {"label": "'Description'", "value": "minor_description"}),
        _set(
            "state.srd_last_minor_world_event",
            {
                "spell": "spell",
                "mode": "minor_mode",
                "caster": "_caster_state_id",
                "origin": "_world_origin",
                "plane": "_world_plane",
                "source_id": "_minor_source_id",
                "description": "_minor_description",
                "deals_damage": False,
                "geometry": {
                    "kind": "'point'",
                    "origin": "_world_origin",
                    "plane": "_world_plane",
                },
            },
        ),
        _when(
            "state.spatial_scene == null",
            [
                _set("state.srd_last_minor_world_event.origin", None),
                _set("state.srd_last_minor_world_event.plane", None),
                _set("state.srd_last_minor_world_event.geometry", None),
            ],
        ),
        _when(
            "_minor_has_cube == true",
            [
                _set("_minor_event", "state.srd_last_minor_world_event"),
                _set(
                    "_minor_geometry_change",
                    {
                        "geometry": {
                            "kind": "'cube'",
                            "face": "minor_cube_face",
                            "min": "_minor_cube_min",
                            "max": "_minor_cube_max",
                        }
                    },
                ),
                _set("_minor_event", "merge(_minor_event, _minor_geometry_change)"),
                _set("state.srd_last_minor_world_event", "_minor_event"),
            ],
        ),
    ]
    return _tool_scene(
        (
            "Resolve source-owned minor cantrip effects after the ordinary cast owner pays "
            "actual costs."
        ),
        [
            _entity("caster_state"),
            _parameter("spell"),
            _optional("object_id", default=""),
            _optional("origin_distance", "number", model_hidden=True, minimum=0),
            _optional("origin_has_clear_path", "boolean", model_hidden=True),
            *deepcopy(CAST_CHOICES),
        ],
        effects,
    )


def _record_guard():
    hashes = {
        spell: SOURCE_PAGES[pages[0] if isinstance(pages, list) else pages]
        for spell, (pages, _, _) in SOURCE.items()
    }
    return [
        _require(
            "record.kind == 'minor_world_spell' and record.spell_level == 0",
            "Use an actual minor world source record.",
        ),
        _set("_minor_source_hashes", hashes),
        _set("_minor_expected_hash", "_minor_source_hashes[record.spell]"),
        _require(
            "record.source_sha256 == _minor_expected_hash",
            "The minor world source must match its pinned page.",
        ),
        _set("_minor_modes", MODES),
        _set("_minor_allowed_modes", "_minor_modes[record.spell]"),
        _require("record.mode in _minor_allowed_modes", "The source does not print that effect."),
    ]


def _retirement_effects(record="record"):
    """Remove only the exact temporary creation whose source is being retired."""
    return [
        _when(
            f"{record}.mode == 'minor_creation' and {record}.output_id != ''",
            [
                *_snapshot(),
                _set("_minor_scene_rows", "state.spatial_scene.entities"),
                _loop(
                    "_minor_scene_rows",
                    "_minor_scene_row",
                    [
                        _when(
                            f"_minor_scene_row.id == {record}.output_id and "
                            "_minor_scene_row.active == true",
                            [
                                _require(
                                    "_minor_scene_row.metadata.srd_minor_creation.source_id == "
                                    f"{record}.source_id",
                                    "The temporary object must retain its actual spell source.",
                                ),
                                {
                                    "spatial_mutation": {
                                        "scene": "state.spatial_scene",
                                        "map_id": "{state.spatial_scene.map.id}",
                                        "expected_revision": "{_info_snapshot.geometry_revision}",
                                        "target": {
                                            "kind": "entity",
                                            "id": "{" + record + ".output_id}",
                                        },
                                        "patch": {"active": False},
                                    },
                                    "store_as": "_minor_retired_object",
                                },
                            ],
                        )
                    ],
                ),
            ],
        )
    ]


def _source_tools():
    refresh = _tool_scene(
        (
            "Expire actual minor sources by the shared clock or their actual owning end-turn "
            "boundary."
        ),
        [
            _optional("boundary_actor", default=""),
            _optional("boundary_phase", default="time"),
            _optional("boundary_turn", "integer", 0),
        ],
        [
            *_now(),
            _set("_minor_records", []),
            _when("state.turn_effects != null", [_set("_minor_records", "state.turn_effects")]),
            _set("_minor_kept", []),
            _loop(
                "_minor_records",
                "_minor_record",
                [
                    _set("_minor_expired", False),
                    _when(
                        "_minor_record.kind == 'minor_world_spell'",
                        [
                            _set(
                                "_minor_expired",
                                (
                                    "(_minor_record.expires_tick != null and _object_now >= "
                                    "_minor_record.expires_tick) or (_minor_record.owner == "
                                    "boundary_actor and boundary_phase == 'end' and "
                                    "boundary_turn >= _minor_record.turn) or "
                                    "(_minor_record.mode == 'minor_creation' and "
                                    "combat.encounter_active != true and _object_now >= "
                                    "_minor_record.started_tick + 4)"
                                ),
                            ),
                            _when("_minor_expired == true", _retirement_effects("_minor_record")),
                        ],
                    ),
                    _when("_minor_expired != true", [_push("_minor_kept", "_minor_record")]),
                ],
            ),
            _set("state.turn_effects", "_minor_kept"),
        ],
    )
    retain = _tool_scene(
        "Retain a source-owned effect and enforce the spell's real simultaneous-effect limit.",
        [
            _entity("caster_state"),
            _parameter("record", "object"),
            _optional("retire_source_id", default=""),
        ],
        [
            *_record_guard(),
            _require(
                "record.caster == _caster_state_id", "The source must belong to the actual caster."
            ),
            _call("refresh_srd_minor_world_effects", {}),
            _when(
                "retire_source_id != ''",
                [
                    _call(
                        "retire_srd_minor_world_source",
                        {"caster_state": "{_caster_state_id}", "source_id": "{retire_source_id}"},
                    )
                ],
            ),
            _set("_minor_records", []),
            _when("state.turn_effects != null", [_set("_minor_records", "state.turn_effects")]),
            _set("_minor_count", 0),
            _loop(
                "_minor_records",
                "_minor_record",
                [
                    _when(
                        (
                            "_minor_record.kind == 'minor_world_spell' and "
                            "_minor_record.caster == _caster_state_id and _minor_record.spell"
                            " == record.spell and _minor_record.dispel_ongoing == true"
                        ),
                        [_set("_minor_count", "_minor_count + 1")],
                    )
                ],
            ),
            _when(
                "record.spell == 'prestidigitation' or record.spell == 'thaumaturgy'",
                [
                    _require(
                        "_minor_count < 3",
                        (
                            "Only three printed ongoing effects of this spell may be active "
                            "for this caster."
                        ),
                    )
                ],
            ),
            _push("_minor_records", "record"),
            _set("state.turn_effects", "_minor_records"),
            _set("_minor_source_id", "record.source_id"),
        ],
    )
    retire = _tool_scene(
        "Retire one exact actual ongoing minor source without changing other effects.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            _set("_minor_records", []),
            _when("state.turn_effects != null", [_set("_minor_records", "state.turn_effects")]),
            _set("_minor_kept", []),
            _set("_minor_found", False),
            _loop(
                "_minor_records",
                "_minor_record",
                [
                    {
                        "condition": (
                            "_minor_record.kind == 'minor_world_spell' and "
                            "_minor_record.caster == _caster_state_id and "
                            "_minor_record.source_id == source_id"
                        ),
                        "on_true": [
                            _require(
                                "_minor_record.dispel_ongoing == true",
                                "An Instantaneous consequence is not an ongoing spell source.",
                            ),
                            _set("_minor_found", True),
                            *_retirement_effects("_minor_record"),
                        ],
                        "on_false": [_push("_minor_kept", "_minor_record")],
                    }
                ],
            ),
            _require("_minor_found == true", "Select an actual ongoing minor world source."),
            _set("state.turn_effects", "_minor_kept"),
        ],
    )
    inspect = _tool_scene(
        "Read actual active minor world sources affecting an object or actual caster.",
        [_parameter("subject")],
        [
            *_now(),
            _set("_minor_effects", []),
            _set("_minor_records", []),
            _when("state.turn_effects != null", [_set("_minor_records", "state.turn_effects")]),
            _loop(
                "_minor_records",
                "_minor_record",
                [
                    _when(
                        (
                            "_minor_record.kind == 'minor_world_spell' and "
                            "(_minor_record.caster == subject or _minor_record.object_id == "
                            "subject or _minor_record.output_id == subject) and "
                            "(_minor_record.expires_tick == null or _object_now < "
                            "_minor_record.expires_tick)"
                        ),
                        [_push("_minor_effects", "_minor_record")],
                    )
                ],
            ),
        ],
    )
    advantage = _tool_scene(
        "Derive only active source-owned Booming Voice Advantage for Charisma (Intimidation).",
        [
            _entity("actor_state"),
            _parameter("kind"),
            _optional("ability", default=""),
            _optional("skill", default=""),
        ],
        [
            *_now(),
            _set("_minor_world_advantage", False),
            _set("_minor_records", []),
            _when("state.turn_effects != null", [_set("_minor_records", "state.turn_effects")]),
            _when(
                "kind == 'ability_check' and ability == 'charisma' and skill == 'intimidation'",
                [
                    _loop(
                        "_minor_records",
                        "_minor_record",
                        [
                            _when(
                                (
                                    "_minor_record.kind == 'minor_world_spell' and "
                                    "_minor_record.caster == _actor_state_id and "
                                    "_minor_record.spell == 'thaumaturgy' and "
                                    "_minor_record.mode == 'booming_voice' and "
                                    "_minor_record.source_sha256 == "
                                )
                                + repr(SOURCE_PAGES[169])
                                + " and _object_now < _minor_record.expires_tick",
                                [_set("_minor_world_advantage", True)],
                            )
                        ],
                    )
                ],
            ),
        ],
    )
    water = _tool_scene(
        "Read the actual unconsumed active one-cup water sources on this admitted object.",
        [_parameter("object_id")],
        [
            *_lookup_object(),
            *_now(),
            _set("_minor_water_present", 0),
            _set("_minor_records", []),
            _when("state.turn_effects != null", [_set("_minor_records", "state.turn_effects")]),
            _loop(
                "_minor_records",
                "_minor_record",
                [
                    _when(
                        (
                            "_minor_record.kind == 'minor_world_spell' and "
                            "_minor_record.spell == 'elementalism' and _minor_record.mode == "
                            "'beckon_water' and _minor_record.object_id == object_id and "
                            "_minor_record.source_sha256 == "
                        )
                        + repr(SOURCE_PAGES[127])
                        + " and _object_now < _minor_record.expires_tick",
                        [
                            _set(
                                "_minor_water_present",
                                "_minor_water_present + _minor_record.water_gallons",
                            )
                        ],
                    )
                ],
            ),
        ],
    )
    consume = _tool_scene(
        (
            "Consume only an actual still-present Elementalism cup; no healing or damage is "
            "invented."
        ),
        [_parameter("object_id"), _parameter("source_id")],
        [
            *_lookup_object(),
            *_now(),
            _set("_minor_records", []),
            _when("state.turn_effects != null", [_set("_minor_records", "state.turn_effects")]),
            _set("_minor_kept", []),
            _set("_minor_found", False),
            _loop(
                "_minor_records",
                "_minor_record",
                [
                    {
                        "condition": (
                            "_minor_record.kind == 'minor_world_spell' and "
                            "_minor_record.spell == 'elementalism' and _minor_record.mode == "
                            "'beckon_water' and _minor_record.object_id == object_id and "
                            "_minor_record.source_id == source_id and "
                            "_minor_record.source_sha256 == "
                        )
                        + repr(SOURCE_PAGES[127])
                        + (
                            " and _object_now < _minor_record.expires_tick and "
                            "_minor_record.water_gallons > 0 and "
                            "_minor_record.water_gallons <= 0.0625"
                        ),
                        "on_true": [_set("_minor_found", True)],
                        "on_false": [_push("_minor_kept", "_minor_record")],
                    }
                ],
            ),
            _require(
                "_minor_found == true", "Select one actual unconsumed source-owned cup of water."
            ),
            _set("state.turn_effects", "_minor_kept"),
        ],
    )
    qualify = _tool_scene(
        "Qualify one exact persisted minor spell source by its pinned cantrip contract.",
        [_parameter("record", "object")],
        _record_guard(),
    )
    read = _tool_scene(
        "Read one exact active ongoing minor source for selected dismissal or Dispel.",
        [_parameter("source_id")],
        [
            _call("refresh_srd_minor_world_effects", {}),
            *_now(),
            _set("_minor_records", []),
            _when("state.turn_effects != null", [_set("_minor_records", "state.turn_effects")]),
            _set("_minor_world_source", None),
            _loop(
                "_minor_records",
                "_minor_record",
                [
                    _when(
                        "_minor_record.kind == 'minor_world_spell' and "
                        "_minor_record.source_id == source_id and _minor_record.dispel_ongoing == "
                        "true and (_minor_record.expires_tick == null or "
                        "_object_now < _minor_record.expires_tick)",
                        [_set("_minor_world_source", "_minor_record")],
                    )
                ],
            ),
            _require(
                "_minor_world_source != null",
                "Select one actual active ongoing minor world source.",
            ),
            _call("qualify_srd_minor_world_source", {"record": "{_minor_world_source}"}),
        ],
    )
    return {
        "refresh_srd_minor_world_effects": refresh,
        "retain_srd_minor_world_effect": retain,
        "retire_srd_minor_world_source": retire,
        "inspect_srd_minor_world_effects": inspect,
        "check_srd_minor_world_d20_advantage": advantage,
        "read_srd_minor_world_water": water,
        "consume_srd_minor_world_water": consume,
        "qualify_srd_minor_world_source": qualify,
        "read_srd_minor_world_source": read,
    }


def bind_d20_runtime(text):
    """Bind the actual Booming Voice source before the shared D20 owner's final normalization."""
    tools = _load_runtime(text)["tools"]
    if "check_srd_minor_world_d20_advantage" not in tools:
        raise SystemExit("Minor D20 binding requires its actual source helper")
    d20 = deepcopy(tools["d20_test"])
    effects = d20["effects"]
    effects[:] = [
        e
        for e in effects
        if not any(
            child.get("call") == "check_srd_minor_world_d20_advantage"
            for child in e.get("on_true", [])
        )
    ]
    index = (
        next(
            i
            for i, e in enumerate(effects)
            if e.get("set", {}).get("field") == "_condition_advantage"
        )
        + 1
    )
    effects.insert(
        index,
        _when(
            "actor_state != null and kind == 'ability_check' and ability == 'charisma' and "
            "skill == 'intimidation'",
            [
                _call(
                    "check_srd_minor_world_d20_advantage",
                    {
                        "actor_state": "{_actor_state_id}",
                        "kind": "{kind}",
                        "ability": "{ability}",
                        "skill": "{skill}",
                    },
                    ["_minor_world_advantage"],
                ),
                _when("_minor_world_advantage == true", [_set("_condition_advantage", True)]),
            ],
        ),
    )
    result = _replace_tools(text, {"d20_test": d20})
    if "*d20_result_card" in result and "&d20_result_card" not in result:
        start = result.index("  d20_test:")
        index = result.index("      result_card:", start)
        result = result[:index] + result[index:].replace(
            "      result_card:", "      result_card: &d20_result_card", 1
        )
    return result


def _water_subtraction_tool():
    """Destroy only source-bound cup water selected by the owning Water cast's fixed budget."""
    return _tool_scene(
        "Subtract actual active Elementalism water before the owning Water family's other sources.",
        [_parameter("object_id"), _parameter("gallons", "number", minimum=0)],
        [
            *_lookup_object(),
            *_now(),
            _set("_minor_water_budget", "gallons"),
            _set("_minor_water_removed", 0),
            _set("_minor_water_records", []),
            _when(
                "state.turn_effects != null",
                [
                    _set("_minor_water_records", "state.turn_effects"),
                ],
            ),
            _set("_minor_water_kept", []),
            _loop(
                "_minor_water_records",
                "_minor_water_record",
                [
                    {
                        "condition": "_minor_water_budget > 0 and "
                        "_minor_water_record.kind == 'minor_world_spell' and "
                        "_minor_water_record.spell == 'elementalism' and "
                        "_minor_water_record.mode == 'beckon_water' and "
                        "_minor_water_record.object_id == object_id and "
                        "_minor_water_record.source_sha256 == "
                        + repr(SOURCE_PAGES[127])
                        + " and _object_now < _minor_water_record.expires_tick and "
                        "_minor_water_record.water_gallons > 0 and "
                        "_minor_water_record.water_gallons <= 0.0625",
                        "on_true": [
                            _set(
                                "_minor_water_taken",
                                "min(_minor_water_budget, _minor_water_record.water_gallons)",
                            ),
                            _set("_minor_water_budget", "_minor_water_budget - _minor_water_taken"),
                            _set(
                                "_minor_water_removed", "_minor_water_removed + _minor_water_taken"
                            ),
                            _set(
                                "_minor_water_change",
                                {
                                    "water_gallons": (
                                        "_minor_water_record.water_gallons - _minor_water_taken"
                                    )
                                },
                            ),
                            _set(
                                "_minor_water_record",
                                "merge(_minor_water_record, _minor_water_change)",
                            ),
                            _when(
                                "_minor_water_record.water_gallons > 0",
                                [
                                    _push("_minor_water_kept", "_minor_water_record"),
                                ],
                            ),
                        ],
                        "on_false": [_push("_minor_water_kept", "_minor_water_record")],
                    },
                ],
            ),
            _set("state.turn_effects", "_minor_water_kept"),
        ],
    )


def _bind_water_consumers(tools):
    """Include cup sources in the existing container totals without changing fixed spell gallons."""
    changed = {}

    def lists(effects):
        yield effects
        for effect in effects:
            for key in ("on_true", "on_false"):
                if key in effect:
                    yield from lists(effect[key])

    cast = deepcopy(tools["cast_spell_object_content_spell"])
    for effects in list(lists(cast["effects"])):
        effects[:] = [
            e
            for e in effects
            if e.get("call")
            not in {
                "read_srd_minor_world_water",
                "subtract_srd_minor_world_water",
            }
            and not any(
                c.get("call") == "subtract_srd_minor_world_water" for c in e.get("on_true", [])
            )
            and e.get("set", {}).get("field") != "_minor_water_removed"
        ]
        for effect in effects:
            setting = effect.get("set", {})
            field = setting.get("field")
            if field == "_world_actual_water":
                setting["value"] = "_world_container.water_gallons + _world_supply_water + "
                setting["value"] += "_minor_water_present"
            elif field == "_world_after_supply_water":
                if setting["value"] != "_world_supply_water":
                    setting["value"] = "max(0, _world_supply_water - "
                    setting["value"] += "max(0, _world_gallons - _minor_water_removed))"
            elif field == "_world_water_change":
                setting["value"]["water_gallons"] = "_world_after_water - "
                setting["value"]["water_gallons"] += "_world_after_supply_water - "
                setting["value"]["water_gallons"] += "(_minor_water_present - _minor_water_removed)"
        index = next(
            (
                i
                for i, e in enumerate(effects)
                if e.get("set", {}).get("field") == "_world_actual_water"
            ),
            None,
        )
        if index is not None:
            effects[index:index] = [
                _call(
                    "read_srd_minor_world_water",
                    {"object_id": "{object_id}"},
                    ["_minor_water_present"],
                ),
                _set("_minor_water_removed", 0),
                _when(
                    "object_mode == 'destroy_water'",
                    [
                        _call(
                            "subtract_srd_minor_world_water",
                            {
                                "object_id": "{object_id}",
                                "gallons": "{_world_gallons}",
                            },
                            ["_minor_water_removed"],
                        ),
                    ],
                ),
            ]
    changed["cast_spell_object_content_spell"] = cast
    supply = deepcopy(tools["place_srd_created_supply_container"])
    supply["effects"] = [
        e for e in supply["effects"] if e.get("call") != "read_srd_minor_world_water"
    ]
    index = next(
        i
        for i, e in enumerate(supply["effects"])
        if e.get("set", {}).get("field") == "_world_water_placed"
    )
    supply["effects"][index]["set"]["value"] = (
        "min(water_remaining, max(0, _world_container.capacity_gallons - "
        "_world_container.water_gallons - _minor_water_present))"
    )
    supply["effects"].insert(
        index,
        _call(
            "read_srd_minor_world_water",
            {
                "object_id": "{object_id}",
            },
            ["_minor_water_present"],
        ),
    )
    changed["place_srd_created_supply_container"] = supply
    return changed


def extend_runtime(text):
    """Compose after object content, leaving cast costs and D20 normalization with their owners."""
    tools = _load_runtime(text)["tools"]
    required = {"resolve_srd_object_content_geometry", "resolve_srd_object_content_point"}
    if not required <= tools.keys():
        raise SystemExit("Minor world spells require the admitted object content geometry owner")
    changed = _fact_tools() | _source_tools()
    changed["subtract_srd_minor_world_water"] = _water_subtraction_tool()
    changed.update(_bind_water_consumers(tools))
    changed["apply_srd_minor_world_area_subject"] = _area_subject_tool(
        object_visibility="check_srd_illumination_object_visibility" in tools
    )
    changed["apply_srd_minor_world_door"] = _door_tool()
    changed["cast_spell_minor_world_spell"] = _casting_tool()
    for name in PUBLIC_TOOLS & changed.keys():
        tool = changed[name]
        changed[name + "_node"] = deepcopy(tool)
        tool["parameters"] = [p for p in tool["parameters"] if p["name"] != "lasting_state"]
        tool["effects"] = [
            _call(
                name + "_node",
                {
                    **{
                        p["name"]: "{"
                        + ("_" + p["name"] + "_id" if p.get("resolve") == "entity" else p["name"])
                        + "}"
                        for p in tool["parameters"]
                    },
                    "lasting_state": "state",
                },
            )
        ]
    cast = deepcopy(tools["cast_spell"])
    prior = {p["name"] for p in cast["parameters"]}
    cast["parameters"] += [deepcopy(p) for p in CAST_CHOICES if p["name"] not in prior]

    def effect_lists(effects):
        yield effects
        for effect in effects:
            for key in ("on_true", "on_false"):
                if key in effect:
                    yield from effect_lists(effect[key])

    for effects in list(effect_lists(cast["effects"])):
        effects[:] = [
            effect
            for effect in effects
            if "_spell_definition.family == 'minor_world_spell'" not in effect.get("condition", "")
        ]

    resolution = next(
        e
        for e in effect_lists(cast["effects"])
        if any(
            "_spell_definition.family == 'information_spell'" in effect.get("condition", "")
            for effect in e
        )
    )
    branch = _when(
        "_spell_cast_failed != true and _spell_definition.family == 'minor_world_spell'",
        [
            _call(
                "cast_spell_minor_world_spell",
                {
                    "caster_state": "{_caster_state_id}",
                    "spell": "{spell}",
                    "object_id": "{minor_object_id}",
                    "origin_distance": "{origin_distance}",
                    "origin_has_clear_path": "{origin_has_clear_path}",
                    **{p["name"]: "{" + p["name"] + "}" for p in CAST_CHOICES},
                },
            )
        ],
    )
    branch["on_true"][0]["export_context"] = {
        "_minor_summary": "_spell_summary_rows",
        "_minor_details": "_spell_detail_rows",
    }
    branch["on_true"] += [
        _loop("_minor_summary", "_minor_card", [_push("_spell_summary_rows", "_minor_card")]),
        _loop("_minor_details", "_minor_card", [_push("_spell_detail_rows", "_minor_card")]),
    ]
    resolution.append(branch)
    changed["cast_spell"] = cast
    process = deepcopy(tools["process_srd_turn_effects"])
    process["effects"] = [
        effect
        for effect in process["effects"]
        if effect.get("call") != "refresh_srd_minor_world_effects"
    ]
    process["effects"][0:0] = [
        _call(
            "refresh_srd_minor_world_effects",
            {
                "boundary_actor": "{boundary_actor}",
                "boundary_phase": "{boundary_phase}",
                "boundary_turn": "{boundary_turn}",
            },
        )
    ]
    changed["process_srd_turn_effects"] = process
    text = _replace_tools(text, changed)
    if "      minor_world_spell:" not in text:
        schema = yaml.dump({"minor_world_spell": _schema()}, Dumper=_RuntimeDumper, sort_keys=False)
        text = text.replace(
            "      timed_senses:",
            "".join("      " + line for line in schema.splitlines(True)) + "\n      timed_senses:",
            1,
        )
    return text
