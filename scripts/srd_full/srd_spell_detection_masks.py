"""Detection masks retain exact spell sources without changing physical creature or object facts."""

import re
from copy import deepcopy

if __package__:
    from .srd_scene_relations import POSITION_EXPORTS, _snapshot, _spatial, _tool_scene
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
    from .srd_timed_senses import _loop, _optional, _push, _replace_tools
else:
    from srd_scene_relations import POSITION_EXPORTS, _snapshot, _spatial, _tool_scene
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
    from srd_timed_senses import _loop, _optional, _push, _replace_tools


SOURCE = {
    "arcanists_magic_aura": ([110, 111], "Arcanist’s Magic Aura", "Astral Projection"),
    "nondetection": (151, "Nondetection", "Passwall"),
    "pass_without_trace": (151, "Pass without Trace", "Phantasmal Force"),
    "sequester": (161, "Sequester", "Shapechange"),
}
PROFILES = {
    spell: {
        "family": "detection_mask_spell",
        "base_level": level,
        "casting_time": "action",
        "range_feet": distance,
        "effect_rounds": rounds,
        "concentration": concentration,
    }
    for spell, level, distance, rounds, concentration in [
        ("arcanists_magic_aura", 2, 5, 14400, False),
        ("nondetection", 3, 5, 4800, False),
        ("pass_without_trace", 2, 0, 600, True),
        ("sequester", 7, 5, 0, False),
    ]
}
CREATURE_TYPES = [
    "aberration",
    "beast",
    "celestial",
    "construct",
    "dragon",
    "elemental",
    "fey",
    "fiend",
    "giant",
    "humanoid",
    "monstrosity",
    "ooze",
    "plant",
    "undead",
]
SCHOOLS = [
    "abjuration",
    "conjuration",
    "divination",
    "enchantment",
    "evocation",
    "illusion",
    "necromancy",
    "transmutation",
]
SUBJECT_EXPORTS = (
    "_mask_divination_blocked",
    "_mask_sensor_blocked",
    "_mask_detect_magic_blocked",
    "_mask_sequestered",
    "_mask_creature_type",
    "_mask_object_aura",
    "_mask_school",
    "_mask_magic_aura_active",
)
CHOICES = [
    _optional("mask_subject", default=""),
    _optional("mask_willing", "boolean", False),
    _optional("mask_creature_type", "enum", "humanoid", enum_values=CREATURE_TYPES),
    _optional(
        "mask_object_aura", "enum", "magical", enum_values=["magical", "nonmagical", "school"]
    ),
    _optional("mask_school", "enum", "illusion", enum_values=SCHOOLS),
    _optional("mask_chosen_creatures", "array", [], items={"type": "string"}),
    _optional("mask_end_description", default=""),
    _optional("mask_end_after_rounds", "integer", 0, minimum=0, maximum=10000000000),
]


def validate_profiles(definitions):
    for spell, expected in PROFILES.items():
        if definitions.get(spell) != expected:
            raise SystemExit(f"Detection-mask profile does not match its pinned source: {spell}")


def _schema():
    properties = {
        name: {
            "type": "boolean"
            if type(value) is bool
            else "integer"
            if type(value) is int
            else "string"
        }
        for name, value in next(iter(PROFILES.values())).items()
        if name != "family"
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _subject_tool():
    """Resolve an actual creature, object or bounded place from the admitted scene only."""
    return _tool_scene(
        "Resolve one actual admitted detection-mask subject.",
        [_parameter("subject")],
        _snapshot()
        + [
            _set("_mask_scene", None),
            _set("_mask_scene_kind", "''"),
            _set("_mask_subject_actor", None),
            _set("_mask_entity_rows", "state.spatial_scene.entities"),
            _set("_mask_zone_rows", "state.spatial_scene.zones"),
            _loop(
                "_mask_entity_rows",
                "_mask_entity",
                [
                    _when(
                        "_mask_entity.active == true and (_mask_entity.id == subject or "
                        "_mask_entity.metadata.participant_ref == subject)",
                        [
                            _require("_mask_scene == null", "Detection-mask subject is ambiguous."),
                            _set("_mask_scene", "_mask_entity"),
                            _set("_mask_scene_kind", "'entity'"),
                        ],
                    )
                ],
            ),
            _loop(
                "_mask_zone_rows",
                "_mask_zone",
                [
                    _when(
                        "_mask_zone.active == true and _mask_zone.id == subject",
                        [
                            _require("_mask_scene == null", "Detection-mask subject is ambiguous."),
                            _set("_mask_scene", "_mask_zone"),
                            _set("_mask_scene_kind", "'zone'"),
                        ],
                    )
                ],
            ),
            _require("_mask_scene != null", "The spell needs an actual admitted subject."),
            _require("_mask_scene.metadata.plane != null", "Record the subject's actual plane."),
            _set("_mask_subject_object", "_mask_scene.metadata.is_object == true"),
            _when(
                "_mask_scene_kind == 'entity' and _mask_subject_object != true",
                [
                    _require(
                        "_mask_scene.metadata.participant_ref != null",
                        "A creature mask needs an actual actor reference.",
                    ),
                    _set("_mask_subject_actor", "_mask_scene.metadata.participant_ref"),
                ],
            ),
            _set("_mask_origin", None),
            _when("_mask_scene_kind == 'entity'", [_set("_mask_origin", "_mask_scene.position")]),
            _when(
                "_mask_scene_kind == 'zone'",
                [
                    _require(
                        "_mask_scene.volume.kind == 'box'",
                        "A protected place currently needs admitted box geometry.",
                    ),
                    _set(
                        "_mask_origin",
                        [
                            f"(_mask_scene.volume.min[{i}] + _mask_scene.volume.max[{i}]) * 0.5"
                            for i in range(3)
                        ],
                    ),
                ],
            ),
        ],
    )


def _trace_tool():
    """Reuse the guardian's exact box-volume distance and the actual source/target exposure."""
    return _tool_scene(
        "Derive current Pass without Trace membership, Stealth bonus and absence of tracks.",
        [_entity("target_state")],
        [
            _set("_mask_stealth_bonus", 0),
            _set("_mask_trackless", False),
            _when(
                "state.has_detection_masks == true",
                [
                    _set("_trace_records", "state.turn_effects"),
                    _loop(
                        "_trace_records",
                        "_trace_record",
                        [
                            _when(
                                "_trace_record.kind == 'detection_mask' and "
                                "_trace_record.spell == 'pass_without_trace' and "
                                "(_target_state_id == _trace_record.caster or "
                                "_target_state_id in _trace_record.chosen_creatures)",
                                [
                                    _call(
                                        "check_srd_detection_mask_record",
                                        {"record": "{_trace_record}"},
                                        ["_mask_active"],
                                    ),
                                    _when(
                                        "_mask_active == true",
                                        [
                                            _call(
                                                "srd_information_scene_entity",
                                                {"subject": "{_trace_record.caster}"},
                                                POSITION_EXPORTS,
                                            ),
                                            _set("_trace_a", "_info_scene_entity"),
                                            _call(
                                                "srd_information_scene_entity",
                                                {"subject": "{_target_state_id}"},
                                                POSITION_EXPORTS,
                                            ),
                                            _set("_trace_b", "_info_scene_entity"),
                                            _require(
                                                "_trace_a.footprint.kind == 'box' and "
                                                "_trace_b.footprint.kind == 'box'",
                                                "Exact Emanation requires admitted box footprints.",
                                            ),
                                            *[
                                                _set(
                                                    f"_trace_gap_{i}",
                                                    f"max(0, abs(_trace_b.position[{i}] - "
                                                    f"_trace_a.position[{i}]) - "
                                                    f"_trace_a.footprint.half_extents[{i}] - "
                                                    f"_trace_b.footprint.half_extents[{i}])",
                                                )
                                                for i in range(2)
                                            ],
                                            _set(
                                                "_trace_gap_2",
                                                "max(0, _trace_b.position[2] - "
                                                "_trace_a.position[2] - _trace_a.height, "
                                                "_trace_a.position[2] - _trace_b.position[2] - "
                                                "_trace_b.height)",
                                            ),
                                            _when(
                                                "_trace_a.metadata.plane == "
                                                "_trace_b.metadata.plane and "
                                                "_trace_gap_0 ** 2 + _trace_gap_1 ** 2 + "
                                                "_trace_gap_2 ** 2 <= 900",
                                                [
                                                    _set(
                                                        "_trace_origin",
                                                        [
                                                            "_trace_a.position[0]",
                                                            "_trace_a.position[1]",
                                                            "_trace_a.position[2] + "
                                                            "_trace_a.height * 0.5",
                                                        ],
                                                    ),
                                                    _spatial(
                                                        "exposure",
                                                        {
                                                            "map_id": (
                                                                "{state.spatial_scene.map.id}"
                                                            ),
                                                            "observer": {
                                                                "point": "{_trace_origin}"
                                                            },
                                                            "target": {
                                                                "entity_id": "{_trace_b.id}"
                                                            },
                                                            "samples": {
                                                                "horizontal": 3,
                                                                "vertical": 3,
                                                            },
                                                            "include": ["geometry"],
                                                        },
                                                        "_trace_exposure",
                                                    ),
                                                    _when(
                                                        "_trace_exposure.visible_samples > 0",
                                                        [
                                                            _set("_mask_stealth_bonus", 10),
                                                            _set("_mask_trackless", True),
                                                        ],
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
            ),
        ],
    )


def _record_tool():
    return _tool(
        "Check the exact saved mask against its actual clock and concentration source.",
        [_parameter("record", "object", from_state="state.turn_effects")],
        [
            _set(
                "_mask_active",
                "record.kind == 'detection_mask' and "
                "(record.expires_tick == null or state.lasting_tick < record.expires_tick) and "
                "(record.end_tick == null or state.lasting_tick < record.end_tick)",
            ),
            _when(
                "_mask_active == true and record.concentration == true",
                [
                    _call(
                        "qualify_srd_detection_mask_concentration",
                        {
                            "caster_state": "{record.caster}",
                            "record": "{record}",
                        },
                        ["_mask_concentration_active"],
                    ),
                    _set("_mask_active", "_mask_concentration_active"),
                ],
            ),
        ],
    )


def _chosen_tool():
    return _tool(
        "Normalize caster-selected creatures through their actual admitted actor identities.",
        [_parameter("creatures", "array", items={"type": "string"}, max_items=64)],
        [
            _set("_mask_chosen_actors", []),
            _loop(
                "creatures",
                "_mask_chosen",
                [
                    _call(
                        "resolve_srd_detection_mask_subject",
                        {"subject": "{_mask_chosen}"},
                        ["_mask_subject_actor"],
                    ),
                    _require(
                        "_mask_subject_actor != null", "Choose actual creatures for the Emanation."
                    ),
                    _when(
                        "_mask_subject_actor not in _mask_chosen_actors",
                        [
                            _push("_mask_chosen_actors", "_mask_subject_actor"),
                        ],
                    ),
                ],
            ),
        ],
    )


def _target_tool():
    return _tool(
        "Qualify a requested target against its actual spell school and source protections.",
        [_parameter("subject"), _parameter("spell")],
        [
            {
                "table_lookup": {
                    "table": "definitions.spell_casting",
                    "key": "spell",
                    "required": True,
                },
                "store_as": "_mask_spell_header",
            },
            _call("check_srd_detection_mask_subject", {"subject": "{subject}"}, SUBJECT_EXPORTS),
            _set(
                "_mask_target_permitted",
                "_mask_spell_header.school != 'divination' or _mask_divination_blocked != true",
            ),
        ],
    )


def _type_tool():
    return _tool(
        "Read a creature's type for spells and magical effects, preserving physical statistics.",
        [_entity("target_state")],
        [
            _require("target_state.creature_type != null", "The actual creature type is required."),
            _set("_physical_creature_type", "target_state.creature_type"),
            _set("_magical_creature_type", "_physical_creature_type"),
            _when(
                "state.has_detection_masks == true",
                [
                    _call(
                        "check_srd_detection_mask_subject",
                        {"subject": "{_target_state_id}"},
                        ["_mask_creature_type"],
                    ),
                    _when(
                        "_mask_creature_type != null",
                        [
                            _set("_magical_creature_type", "_mask_creature_type"),
                        ],
                    ),
                ],
            ),
        ],
    )


def _suspension_tool():
    return _tool(
        "Read only Sequester's exact active suspended-animation exemptions.",
        [_entity("target_state")],
        [
            _call(
                "check_srd_detection_mask_subject",
                {"subject": "{_target_state_id}"},
                ["_mask_sequestered"],
            ),
            *[
                _set(name, "_mask_sequestered")
                for name in [
                    "_sequester_suspended",
                    "_sequester_no_food_required",
                    "_sequester_no_water_required",
                    "_sequester_no_air_required",
                    "_sequester_no_aging",
                ]
            ],
        ],
    )


def _concentration_tool():
    return _tool(
        "Qualify the actual retained Pass without Trace concentration instance.",
        [_entity("caster_state"), _parameter("record", "object", from_state="state.turn_effects")],
        [
            _set(
                "_mask_concentration_active",
                "caster_state.concentration_spell_id == record.spell and "
                "caster_state.concentration_effect_family == 'detection_mask' and "
                "caster_state.mask_concentration_instance == record.instance and "
                "caster_state.concentration_started_tick == record.started_tick",
            )
        ],
    )


def _query_tool():
    effects = [_set(field, False) for field in SUBJECT_EXPORTS[:4]] + [
        _set("_mask_creature_type", None),
        _set("_mask_object_aura", None),
        _set("_mask_school", None),
        _set("_mask_magic_aura_active", False),
        _set("_mask_sequester_conditions", []),
        _set("_mask_aura_level", -1),
        _set("_mask_aura_tick", -1),
        _set("_mask_aura_instance", -1),
        _when(
            "state.has_detection_masks == true",
            [
                _set("_mask_query_records", "state.turn_effects"),
                _loop(
                    "_mask_query_records",
                    "_mask_record",
                    [
                        _when(
                            "_mask_record.kind == 'detection_mask' and "
                            "(_mask_record.source == subject or "
                            "_mask_record.masked_subject == subject)",
                            [
                                _call(
                                    "check_srd_detection_mask_record",
                                    {"record": "{_mask_record}"},
                                    ["_mask_active"],
                                ),
                                _when(
                                    "_mask_active == true",
                                    [
                                        _when(
                                            "_mask_record.spell == 'nondetection' or "
                                            "_mask_record.spell == 'sequester'",
                                            [
                                                _set("_mask_divination_blocked", True),
                                                _set("_mask_sensor_blocked", True),
                                            ],
                                        ),
                                        _when(
                                            "_mask_record.spell == 'sequester'",
                                            [
                                                _set("_mask_detect_magic_blocked", True),
                                                _set("_mask_sequestered", True),
                                                _when(
                                                    "_mask_record.condition_source_id != null",
                                                    [
                                                        _push(
                                                            "_mask_sequester_conditions",
                                                            "_mask_record.condition_source_id",
                                                        )
                                                    ],
                                                ),
                                            ],
                                        ),
                                        _when(
                                            "_mask_record.spell == 'arcanists_magic_aura' and "
                                            "(_mask_record.slot_level > _mask_aura_level or "
                                            "(_mask_record.slot_level == _mask_aura_level and "
                                            "(_mask_record.started_tick > _mask_aura_tick or "
                                            "(_mask_record.started_tick == _mask_aura_tick and "
                                            "_mask_record.instance > _mask_aura_instance))))",
                                            [
                                                _set("_mask_magic_aura_active", True),
                                                _set("_mask_aura_level", "_mask_record.slot_level"),
                                                _set(
                                                    "_mask_aura_tick", "_mask_record.started_tick"
                                                ),
                                                _set(
                                                    "_mask_aura_instance", "_mask_record.instance"
                                                ),
                                                _set("_mask_creature_type", None),
                                                _set("_mask_object_aura", None),
                                                _set("_mask_school", None),
                                                *[
                                                    _when(
                                                        "_mask_record." + source + " != null",
                                                        [
                                                            _set(target, "_mask_record." + source),
                                                        ],
                                                    )
                                                    for target, source in [
                                                        (
                                                            "_mask_creature_type",
                                                            "mask_creature_type",
                                                        ),
                                                        ("_mask_object_aura", "object_aura"),
                                                        ("_mask_school", "school"),
                                                    ]
                                                ],
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
    ]
    return _tool(
        "Read only active source-owned detection and magical targeting masks.",
        [_parameter("subject")],
        effects,
    )


def _aura_history():
    """Count actual distinct consecutive casting days; repeated same-day casts cannot advance it."""
    return [
        _set("_mask_day", "state.lasting_tick // 28800"),
        _set("_mask_days", 1),
        _set("_mask_history", []),
        _set("_mask_history_kept", []),
        _when(
            "state.magic_aura_cast_days != null",
            [_set("_mask_history", "state.magic_aura_cast_days")],
        ),
        _loop(
            "_mask_history",
            "_mask_prior_day",
            [
                {
                    "condition": "_mask_prior_day.caster == _caster_state_id and "
                    "_mask_prior_day.subject == _mask_scene.id",
                    "on_true": [
                        _when(
                            "_mask_prior_day.day == _mask_day",
                            [_set("_mask_days", "_mask_prior_day.count")],
                        ),
                        _when(
                            "_mask_prior_day.day + 1 == _mask_day",
                            [_set("_mask_days", "min(30, _mask_prior_day.count + 1)")],
                        ),
                    ],
                    "on_false": [_push("_mask_history_kept", "_mask_prior_day")],
                }
            ],
        ),
        _push(
            "_mask_history_kept",
            {
                "caster": "_caster_state_id",
                "subject": "_mask_scene.id",
                "day": "_mask_day",
                "count": "_mask_days",
            },
        ),
        _set("state.magic_aura_cast_days", "_mask_history_kept"),
        _when("_mask_days == 30", [_set("_mask_expiry", None)]),
    ]


def _install_tool(runtime):
    timed_params = {
        "target_state": "{_mask_subject_actor}",
        "caster_state": "{_caster_state_id}",
        "lasting_state": "state",
        "spell": "sequester",
        "condition": "unconscious",
        "save_dc": "{save_dc}",
        "duration_rounds": 0,
        "repeat_save_timing": "none",
        "save_ability": "wisdom",
        "control_profile": "none",
        "actual_spell_level": "{slot_level}",
    }
    timed_names = {
        p["name"] for p in runtime["tools"]["install_srd_timed_condition_source"]["parameters"]
    }
    for name in ("spell_cast_id", "spell_cast_owner"):
        if name in timed_names:
            timed_params[name] = (
                "{" + ("_mask_source_owner" if name.endswith("owner") else name) + "}"
            )
    effects = [
        {
            "table_lookup": {"table": "definitions.spells", "key": "spell", "required": True},
            "store_as": "_mask_profile",
        },
        _require(
            "_mask_profile.family == 'detection_mask_spell' and "
            "slot_level >= _mask_profile.base_level and slot_level <= 9",
            "Use the actual supported detection spell and source slot.",
        ),
        _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
        _set("_mask_subject", "mask_subject"),
        _when("spell == 'pass_without_trace'", [_set("_mask_subject", "_caster_state_id")]),
        _call(
            "resolve_srd_detection_mask_subject",
            {"subject": "{_mask_subject}"},
            [
                "_mask_scene",
                "_mask_scene_kind",
                "_mask_subject_actor",
                "_mask_subject_object",
                "_mask_origin",
            ],
        ),
        _set("_mask_plane", "_mask_scene.metadata.plane"),
        _when(
            "_mask_subject_object == true",
            [
                _call(
                    "check_srd_illumination_object_visibility",
                    {"object_id": "{_mask_scene.id}"},
                    ["_illumination_object_origin", "_illumination_object_plane"],
                ),
                _set("_mask_origin", "_illumination_object_origin"),
                _set("_mask_plane", "_illumination_object_plane"),
            ],
        ),
        _call("srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS),
        _set("_mask_caster_scene", "_info_scene_entity"),
        _call(
            "measure_srd_information_distance",
            {"start": "{_info_position}", "end": "{_mask_origin}"},
            ["_scene_distance"],
        ),
        _require(
            "_info_scene_entity.metadata.plane == _mask_plane and _scene_distance <= 5",
            "Touch spells need actual same-plane contact within five feet.",
        ),
        _call(
            "srd_information_barriers",
            {
                "start": "{_info_position}",
                "end": "{_mask_origin}",
                "plane": "{_mask_plane}",
                "rule": "solid",
            },
            ["_information_blocked"],
        ),
        _require("_information_blocked != true", "The actual subject is behind solid total cover."),
        _when(
            "_mask_subject_actor != null and spell != 'pass_without_trace'",
            [
                _require("mask_willing == true", "This spell requires a willing creature."),
            ],
        ),
        _when(
            "_mask_scene_kind == 'zone'",
            [
                _require("spell == 'nondetection'", "Only Nondetection supports a place target."),
                *[
                    _require(
                        f"_mask_scene.volume.max[{i}] - _mask_scene.volume.min[{i}] <= 10",
                        "Nondetection's place cannot exceed ten feet in any dimension.",
                    )
                    for i in range(3)
                ],
            ],
        ),
        _set("_mask_type", None),
        _set("_mask_aura", None),
        _set("_mask_school", None),
        _when(
            "spell == 'arcanists_magic_aura'",
            [
                {
                    "condition": "_mask_subject_object == true",
                    "on_true": [
                        _require(
                            "(_mask_scene.metadata.carried_by == null or "
                            "_mask_scene.metadata.carried_by == '') and "
                            "(_mask_scene.metadata.worn_by == null or "
                            "_mask_scene.metadata.worn_by == '')",
                            "Magic Aura's object cannot be worn or carried.",
                        ),
                        _set("_mask_aura", "mask_object_aura"),
                        _set("_mask_school", "mask_school"),
                    ],
                    "on_false": [
                        _call(
                            "resolve_srd_magical_creature_type",
                            {
                                "target_state": "{_mask_subject_actor}",
                            },
                            ["_physical_creature_type"],
                        ),
                        _require(
                            "_mask_scene.metadata.creature_type == null or "
                            "_mask_scene.metadata.creature_type == _physical_creature_type",
                            "The admitted scene type must match the prepared creature's type.",
                        ),
                        _require(
                            "mask_creature_type != _physical_creature_type",
                            "Choose a different creature type from the creature's actual type.",
                        ),
                        _set("_mask_type", "mask_creature_type"),
                    ],
                },
            ],
        ),
        _when(
            "spell == 'nondetection' and _mask_subject_object == true",
            [
                _require(
                    "_mask_scene.footprint.kind == 'box' or _mask_scene.footprint.kind == 'circle'",
                    "Nondetection needs admitted object dimensions.",
                ),
                _when(
                    "_mask_scene.footprint.kind == 'box'",
                    [
                        *[
                            _require(
                                f"_mask_scene.footprint.half_extents[{i}] * 2 <= 10",
                                "Nondetection's object cannot exceed ten feet in any dimension.",
                            )
                            for i in range(2)
                        ],
                    ],
                ),
                _when(
                    "_mask_scene.footprint.kind == 'circle'",
                    [
                        _require(
                            "_mask_scene.footprint.radius * 2 <= 10",
                            "Nondetection's object cannot exceed ten feet in any dimension.",
                        ),
                    ],
                ),
                _require("_mask_scene.height <= 10", "The protected object exceeds ten feet high."),
            ],
        ),
        _set("_mask_expiry", None),
        _when(
            "_mask_profile.effect_rounds > 0",
            [
                _set("_mask_expiry", "state.lasting_tick + _mask_profile.effect_rounds * 2"),
            ],
        ),
        _when("spell == 'arcanists_magic_aura'", _aura_history()),
        _set("_mask_end_tick", None),
        _when(
            "spell == 'sequester' and mask_end_after_rounds > 0",
            [
                _set("_mask_end_tick", "state.lasting_tick + mask_end_after_rounds * 2"),
            ],
        ),
        _when("state.turn_effects == null", [_set("state.turn_effects", [])]),
        _when("state.detection_mask_sequence == null", [_set("state.detection_mask_sequence", 0)]),
        _set("state.detection_mask_sequence", "state.detection_mask_sequence + 1"),
        _set("_mask_instance", "state.detection_mask_sequence"),
        _set("_mask_condition_source_id", None),
        _set("_mask_target", "_mask_scene.id"),
        _when("_mask_subject_actor != null", [_set("_mask_target", "_mask_subject_actor")]),
        _set("_mask_chosen_actors", []),
        _when(
            "spell == 'pass_without_trace'",
            [
                _call(
                    "normalize_srd_trace_creatures",
                    {"creatures": "{mask_chosen_creatures}"},
                    ["_mask_chosen_actors"],
                ),
            ],
        ),
        _set("_mask_source_owner", None),
        _when("spell_cast_owner != null", [_set("_mask_source_owner", "_spell_cast_owner_id")]),
        _when(
            "spell == 'sequester' and _mask_subject_actor != null",
            [
                _call("capture_srd_sequester_invisible", {"target_state": "{_mask_subject_actor}"}),
                _call(
                    "install_srd_timed_condition_source",
                    timed_params,
                    ["_timed_condition_source_id"],
                ),
                _set("_mask_condition_source_id", "_timed_condition_source_id"),
            ],
        ),
        _set(
            "_mask_new",
            {
                "kind": "'detection_mask'",
                "spell": "spell",
                "instance": "_mask_instance",
                "caster": "_caster_state_id",
                "target": "_caster_state_id",
                "masked_subject": "_mask_target",
                "source": "_mask_scene.id",
                "target_actor": None,
                "scene_kind": "_mask_scene_kind",
                "slot_level": "slot_level",
                "spell_level": "slot_level",
                "started_tick": "state.lasting_tick",
                "expires_tick": None,
                "concentration": "_mask_profile.concentration",
                "mask_creature_type": None,
                "object_aura": None,
                "school": None,
                "chosen_creatures": "_mask_chosen_actors",
                "condition_source_id": None,
                "end_description": "mask_end_description",
                "end_tick": None,
                "cast_id": "spell_cast_id",
                "source_caster_id": None,
                "dispel_ongoing": True,
                "ongoing_duration": "spell_cast_id != '' and _mask_expiry != null",
                "owner": "'__time_only__'",
                "phase": "'time'",
                "turn": 1000000000,
                "magnitude": 0,
                "dice_count": 0,
                "damage_die": "''",
                "damage_type": "''",
                "next_attack_disadvantage": False,
                "d20_modifier": 0,
            },
        ),
        *[
            _when(
                value + " != null",
                [
                    _set("_mask_record_patch", {field: value}),
                    _set("_mask_new", "merge(_mask_new, _mask_record_patch)"),
                ],
            )
            for field, value in [
                ("target_actor", "_mask_subject_actor"),
                ("expires_tick", "_mask_expiry"),
                ("mask_creature_type", "_mask_type"),
                ("object_aura", "_mask_aura"),
                ("school", "_mask_school"),
                ("condition_source_id", "_mask_condition_source_id"),
                ("source_caster_id", "_mask_source_owner"),
                ("end_tick", "_mask_end_tick"),
            ]
        ],
        _set("_mask_install_records", "state.turn_effects"),
        _require(
            "len(_mask_install_records) < 128", "Settle existing sources before another cast."
        ),
        _push("_mask_install_records", "_mask_new"),
        _set("state.turn_effects", "_mask_install_records"),
        _set("state.has_detection_masks", True),
        _when(
            "spell == 'pass_without_trace'",
            [
                _set("caster_state.concentration_spell_id", "spell"),
                _set("caster_state.concentration_effect_family", "'detection_mask'"),
                _set("caster_state.concentration_started_tick", "state.lasting_tick"),
                _set("caster_state.concentration_expires_tick", "_mask_expiry"),
                _set("caster_state.concentration_save_modifier", "concentration_modifier"),
                _set("caster_state.concentration_target_state_id", "_caster_state_id"),
                _set("caster_state.concentration_target_state_ids", ["_caster_state_id"]),
                _set("caster_state.mask_concentration_instance", "_mask_instance"),
            ],
        ),
        _when(
            "spell == 'sequester' and _mask_subject_actor != null",
            [
                _call("sync_srd_illumination_invisible", {"actor_state": "{_mask_subject_actor}"}),
                _call(
                    "refresh_srd_timed_condition_target",
                    {"target_state": "{_mask_subject_actor}", "lasting_state": "state"},
                ),
            ],
        ),
        _set("_spell_summary_rows", [{"label": "'Protection'", "value": "spell"}]),
        _set("_spell_detail_rows", [{"label": "'Actual subject'", "value": "_mask_target"}]),
    ]
    return _tool_scene(
        "Retain the source-defined mask only after its actual successful paid casting.",
        [
            _entity("caster_state"),
            _parameter("spell"),
            _parameter("slot_level", "integer"),
            _parameter("save_dc", "integer"),
            _optional("concentration_modifier", "integer", 0),
            _optional("spell_cast_id", default=""),
            {**_entity("spell_cast_owner"), "required": False, "default": None},
            *deepcopy(CHOICES),
        ],
        effects,
    )


def _capture_tool():
    return _tool(
        "Preserve independent native Invisible before the first managed Sequester projection.",
        [_entity("target_state")],
        [
            _when(
                "target_state.illumination_invisible_managed != true",
                [
                    _set(
                        "target_state.illumination_invisible_native",
                        "'invisible' in target_state.conditions",
                    ),
                    _set("target_state.illumination_invisible_managed", True),
                ],
            ),
            _set("target_state.detection_mask_managed", True),
        ],
    )


def _retire_tool():
    return _tool(
        "Retire one exact mask and only its own timed condition and Invisible contribution.",
        [_parameter("instance", "integer")],
        [
            _set("_mask_removed", None),
            _set("_mask_kept", []),
            _set("_mask_retire_records", "state.turn_effects"),
            _loop(
                "_mask_retire_records",
                "_mask_retire_record",
                [
                    {
                        "condition": "_mask_retire_record.kind == 'detection_mask' and "
                        "_mask_retire_record.instance == instance",
                        "on_true": [_set("_mask_removed", "_mask_retire_record")],
                        "on_false": [_push("_mask_kept", "_mask_retire_record")],
                    }
                ],
            ),
            _set("state.turn_effects", "_mask_kept"),
            _when(
                "_mask_removed != null and _mask_removed.spell == 'pass_without_trace'",
                [
                    _call(
                        "finish_srd_detection_mask_concentration",
                        {
                            "caster_state": "{_mask_removed.caster}",
                            "instance": "{_mask_removed.instance}",
                        },
                    )
                ],
            ),
            _when(
                "_mask_removed != null and _mask_removed.condition_source_id != null",
                [
                    _call(
                        "retire_srd_timed_condition_source",
                        {
                            "lasting_state": "state",
                            "source_id": "{_mask_removed.condition_source_id}",
                        },
                    ),
                    _call(
                        "refresh_srd_timed_condition_target",
                        {"target_state": "{_mask_removed.target_actor}", "lasting_state": "state"},
                    ),
                    _call(
                        "sync_srd_illumination_invisible",
                        {"actor_state": "{_mask_removed.target_actor}"},
                    ),
                ],
            ),
            _set("state.has_detection_masks", False),
            _loop(
                "_mask_kept",
                "_mask_remaining",
                [
                    _when(
                        "_mask_remaining.kind == 'detection_mask'",
                        [_set("state.has_detection_masks", True)],
                    )
                ],
            ),
        ],
    )


def _finish_concentration_tool():
    return _tool(
        "Clear only the concentration metadata of the exact removed mask instance.",
        [_entity("caster_state"), _parameter("instance", "integer")],
        [
            _when(
                "caster_state.concentration_effect_family == 'detection_mask' and "
                "caster_state.mask_concentration_instance == instance",
                [
                    *[
                        _set("caster_state." + field, None)
                        for field in (
                            "concentration_spell_id",
                            "concentration_effect_family",
                            "concentration_target_state_id",
                            "concentration_started_tick",
                            "concentration_expires_tick",
                            "mask_concentration_instance",
                        )
                    ],
                    _set("caster_state.concentration_target_state_ids", []),
                ],
            )
        ],
    )


def _condition_projection_tool():
    return _tool(
        "Project only active Sequester's exact installed Unconscious source contribution.",
        [_entity("target_state")],
        [
            _set("_sequester_unconscious", False),
            _call(
                "check_srd_detection_mask_subject",
                {"subject": "{_target_state_id}"},
                ["_mask_sequester_conditions"],
            ),
            _loop(
                "_mask_sequester_conditions",
                "_sequester_condition_id",
                [
                    _set("_sequester_condition", None),
                    _when(
                        "state.timed_condition_sources[_sequester_condition_id] != null",
                        [
                            _set(
                                "_sequester_condition",
                                "state.timed_condition_sources[_sequester_condition_id]",
                            ),
                        ],
                    ),
                    _when(
                        "_sequester_condition != null and "
                        "_sequester_condition.installed == true and "
                        "_sequester_condition.target_state_id == _target_state_id and "
                        "_sequester_condition.spell_id == 'sequester' and "
                        "_sequester_condition.condition == 'unconscious' and "
                        "_sequester_condition.suppressed != true",
                        [
                            _call(
                                "check_srd_target_condition_immunity",
                                {
                                    "target_state": "{_target_state_id}",
                                    "condition": "unconscious",
                                    "source_state_id": "{_sequester_condition.caster_state_id}",
                                    "source_is_magical": True,
                                },
                                ["_source_condition_immune"],
                            ),
                            _when(
                                "_source_condition_immune != true",
                                [
                                    _set("_sequester_unconscious", True),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _damage_subject_tool():
    return _tool(
        "End Sequester for an exact saved creature or object subject after actual damage.",
        [_parameter("subject")],
        [
            _when(
                "state.has_detection_masks == true",
                [
                    _set("_sequester_subject_records", "state.turn_effects"),
                    _loop(
                        "_sequester_subject_records",
                        "_sequester_subject_record",
                        [
                            _when(
                                "_sequester_subject_record.kind == 'detection_mask' and "
                                "_sequester_subject_record.spell == 'sequester' and "
                                "(_sequester_subject_record.source == subject or "
                                "_sequester_subject_record.masked_subject == subject)",
                                [
                                    _call(
                                        "retire_srd_detection_mask",
                                        {
                                            "instance": "{_sequester_subject_record.instance}",
                                        },
                                    )
                                ],
                            ),
                        ],
                    ),
                ],
            )
        ],
    )


def _end_condition_tool():
    """Classify only the stored fictional condition; its one-mile boundary is actual geometry."""
    return _tool_scene(
        "Resolve a saved Sequester ending condition at an actual admitted event location.",
        [
            _parameter("instance", "integer"),
            _parameter("event_subject"),
            _optional("fictional_match", "boolean", False),
        ],
        [
            _set("_sequester_event_ended", False),
            _when(
                "state.has_detection_masks == true and fictional_match == true",
                [
                    _set("_sequester_event_records", "state.turn_effects"),
                    _loop(
                        "_sequester_event_records",
                        "_sequester_event_record",
                        [
                            _when(
                                "_sequester_event_record.kind == 'detection_mask' and "
                                "_sequester_event_record.spell == 'sequester' and "
                                "_sequester_event_record.instance == instance and "
                                "_sequester_event_record.end_description != ''",
                                [
                                    _call(
                                        "resolve_srd_detection_mask_subject",
                                        {
                                            "subject": "{event_subject}",
                                        },
                                        ["_mask_origin", "_mask_scene"],
                                    ),
                                    _set("_sequester_event_origin", "_mask_origin"),
                                    _set("_sequester_event_plane", "_mask_scene.metadata.plane"),
                                    _call(
                                        "resolve_srd_detection_mask_subject",
                                        {
                                            "subject": "{_sequester_event_record.source}",
                                        },
                                        ["_mask_origin", "_mask_scene", "_mask_subject_object"],
                                    ),
                                    _set("_sequester_target_plane", "_mask_scene.metadata.plane"),
                                    _when(
                                        "_mask_subject_object == true",
                                        [
                                            _call(
                                                "check_srd_illumination_object_visibility",
                                                {
                                                    "object_id": "{_mask_scene.id}",
                                                },
                                                [
                                                    "_illumination_object_origin",
                                                    "_illumination_object_plane",
                                                ],
                                            ),
                                            _set("_mask_origin", "_illumination_object_origin"),
                                            _set(
                                                "_sequester_target_plane",
                                                "_illumination_object_plane",
                                            ),
                                        ],
                                    ),
                                    _call(
                                        "measure_srd_information_distance",
                                        {
                                            "start": "{_mask_origin}",
                                            "end": "{_sequester_event_origin}",
                                        },
                                        ["_scene_distance"],
                                    ),
                                    _when(
                                        "_sequester_target_plane == _sequester_event_plane and "
                                        "_scene_distance <= 5280",
                                        [
                                            _call(
                                                "retire_srd_detection_mask",
                                                {
                                                    "instance": (
                                                        "{_sequester_event_record.instance}"
                                                    ),
                                                },
                                            ),
                                            _set("_sequester_event_ended", True),
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


def _damage_tool():
    return _tool(
        "End Sequester after this exact target takes real nonzero damage.",
        [_entity("target_state")],
        [
            _when(
                "target_state.detection_mask_managed == true and state.has_detection_masks == true",
                [
                    _call("end_srd_sequester_for_subject", {"subject": "{_target_state_id}"}),
                ],
            )
        ],
    )


def _refresh_tool():
    return _tool(
        "Settle expired concentration masks and recompute the actual source index.",
        [],
        [
            _when(
                "state.has_detection_masks == true",
                [
                    _set("_mask_refresh_records", []),
                    _when(
                        "state.turn_effects != null",
                        [_set("_mask_refresh_records", "state.turn_effects")],
                    ),
                    _set("_mask_refresh_live", False),
                    _loop(
                        "_mask_refresh_records",
                        "_mask_refresh_record",
                        [
                            _when(
                                "_mask_refresh_record.kind == 'detection_mask'",
                                [
                                    _call(
                                        "check_srd_detection_mask_record",
                                        {"record": "{_mask_refresh_record}"},
                                        ["_mask_active"],
                                    ),
                                    {
                                        "condition": "_mask_active == true",
                                        "on_true": [_set("_mask_refresh_live", True)],
                                        "on_false": [
                                            _call(
                                                "retire_srd_detection_mask",
                                                {
                                                    "instance": "{_mask_refresh_record.instance}",
                                                },
                                            )
                                        ],
                                    },
                                ],
                            )
                        ],
                    ),
                    _set("state.has_detection_masks", "_mask_refresh_live"),
                ],
            )
        ],
    )


def _bind_mask_state(tool, private_tools):
    """Bind appended mask guards and child queries to the consumer's resolved source state."""
    if not any(p["name"] == "lasting_state" for p in tool["parameters"]):
        return tool

    def bind(value):
        if isinstance(value, str):
            return re.sub(
                r"\bstate\.has_detection_masks\b", "lasting_state.has_detection_masks", value
            )
        if isinstance(value, list):
            return [bind(child) for child in value]
        if isinstance(value, dict):
            result = {key: bind(child) for key, child in value.items()}
            child = private_tools.get(result.get("call"))
            if child and any(p["name"] == "lasting_state" for p in child["parameters"]):
                result["params"]["lasting_state"] = "{_lasting_state_id}"
            return result
        return value

    tool["effects"] = bind(tool["effects"])
    return tool


def _consumers(runtime, private_tools):
    changed = {}
    object_sight = deepcopy(runtime["tools"]["check_srd_illumination_object_visibility"])
    assignments = [
        e["set"]
        for e in object_sight["effects"]
        if e.get("set", {}).get("field") == "_illumination_object_invisible"
    ]
    expected = "_info_scene_entity.metadata.invisible == true or _illumination_invisible == true"
    if len(assignments) != 1:
        raise SystemExit("Sequester needs the actual object's Invisible source assignment.")
    if assignments[0]["value"] != expected:
        assignments[0]["value"] = expected
        changed["check_srd_illumination_object_visibility"] = object_sight
    conditions = deepcopy(runtime["tools"]["resolve_srd_control_behavior_conditions"])
    if "resolve_srd_sequester_condition_projection" not in str(conditions["effects"]):
        branches = [
            e
            for e in conditions["effects"]
            if e.get("condition") == "target_state.timed_condition_unowned_unconscious != null"
        ]
        if len(branches) != 1:
            raise SystemExit("Sequester needs the actual owning Unconscious projection.")
        branches[0]["on_true"].insert(
            0,
            _when(
                "target_state.detection_mask_managed == true",
                [
                    _call(
                        "resolve_srd_sequester_condition_projection",
                        {"target_state": "{_target_state_id}"},
                        ["_sequester_unconscious"],
                    ),
                    _set(
                        "_control_unconscious",
                        "_control_unconscious == true or _sequester_unconscious == true",
                    ),
                ],
            ),
        )
        changed["resolve_srd_control_behavior_conditions"] = conditions
    subject = deepcopy(runtime["tools"]["check_srd_illumination_subject"])
    if "check_srd_detection_mask_subject" not in str(subject["effects"]):
        subject["effects"].append(
            _when(
                "state.has_detection_masks == true",
                [
                    _call(
                        "check_srd_detection_mask_subject",
                        {"subject": "{subject}"},
                        ["_mask_sequestered"],
                    ),
                    _set(
                        "_illumination_invisible",
                        "_illumination_invisible == true or _mask_sequestered == true",
                    ),
                ],
            )
        )
        changed["check_srd_illumination_subject"] = subject
    bearer = deepcopy(runtime["tools"]["resolve_srd_information_bearer_magic"])
    if "check_srd_detection_mask_subject" not in str(bearer["effects"]):
        bearer["effects"].append(
            _when(
                "state.has_detection_masks == true",
                [
                    _call(
                        "check_srd_detection_mask_subject",
                        {"subject": "{candidate.id}"},
                        SUBJECT_EXPORTS,
                    ),
                    _when(
                        "_mask_magic_aura_active == true",
                        [
                            _set("_bearer_magic", True),
                            _when(
                                "'illusion' not in _bearer_schools",
                                [
                                    _push("_bearer_schools", "'illusion'"),
                                ],
                            ),
                        ],
                    ),
                    _when(
                        "_mask_object_aura != null",
                        [
                            _set("_bearer_magic", "_mask_object_aura != 'nonmagical'"),
                            _when("_mask_object_aura != 'magical'", [_set("_bearer_schools", [])]),
                            _when(
                                "_mask_object_aura == 'school'",
                                [_push("_bearer_schools", "_mask_school")],
                            ),
                        ],
                    ),
                    _when(
                        "_mask_detect_magic_blocked == true",
                        [
                            _set("_bearer_magic", False),
                            _set("_bearer_schools", []),
                        ],
                    ),
                ],
            )
        )
        changed["resolve_srd_information_bearer_magic"] = bearer
    candidate = deepcopy(runtime["tools"]["qualify_srd_information_candidate"])
    if "check_srd_detection_mask_subject" not in str(candidate["effects"]):
        candidate["effects"].append(
            _when(
                "state.has_detection_masks == true",
                [
                    _call(
                        "check_srd_detection_mask_subject",
                        {"subject": "{candidate.id}"},
                        SUBJECT_EXPORTS,
                    ),
                    _when(
                        "_mask_creature_type != null and spell == 'detect_evil_and_good'",
                        [
                            _when(
                                "candidate.metadata.hallow != true",
                                [
                                    _set("_candidate_kind", "_mask_creature_type"),
                                ],
                            ),
                            _set(
                                "_candidate_eligible",
                                "_mask_creature_type == 'aberration' or "
                                "_mask_creature_type == 'celestial' or "
                                "_mask_creature_type == 'elemental' or "
                                "_mask_creature_type == 'fey' or "
                                "_mask_creature_type == 'fiend' or "
                                "_mask_creature_type == 'undead' or "
                                "candidate.metadata.hallow == true",
                            ),
                        ],
                    ),
                    _when("_mask_divination_blocked == true", [_set("_candidate_eligible", False)]),
                ],
            )
        )
        changed["qualify_srd_information_candidate"] = candidate
    for name in (
        "perceive_srd_information_proxy",
        "perceive_srd_mislead_proxy",
        "perceive_srd_project_image_proxy",
    ):
        if name not in runtime["tools"]:
            continue
        proxy = deepcopy(runtime["tools"][name])
        if "check_srd_detection_mask_subject" in str(proxy["effects"]):
            continue

        def bind(value):
            if isinstance(value, dict):
                for child in value.values():
                    if bind(child):
                        return True
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    if (
                        isinstance(child, dict)
                        and child.get("condition") == "_remote_visible == true"
                        and any(
                            e.get("call") == "format_srd_information_projection"
                            for e in child.get("on_true", [])
                        )
                    ):
                        value.insert(
                            index,
                            _when(
                                "state.has_detection_masks == true",
                                [
                                    _call(
                                        "check_srd_detection_mask_subject",
                                        {"subject": "{_remote_entity.id}"},
                                        ["_mask_sensor_blocked"],
                                    )
                                ],
                            ),
                        )
                        child["condition"] += " and _mask_sensor_blocked != true"
                        return True
                    if bind(child):
                        return True
            return False

        if not bind(proxy["effects"]):
            raise SystemExit(f"{name} no longer has the expected actual subject delivery branch.")
        changed[name] = proxy
    roll = deepcopy(runtime["tools"]["d20_test"])
    if "check_srd_pass_without_trace" not in str(roll["effects"]):
        branches = [
            effect
            for effect in roll["effects"]
            if effect.get("condition", "").startswith(
                "actor_state != null and kind == 'ability_check'"
            )
            and "concentration_d20_modifier_source_ids" in effect["condition"]
        ]
        if len(branches) != 1:
            raise SystemExit("Stealth needs the existing actual ability-check modifier boundary.")
        # This existing branch is widened only to qualify an active source's exact Stealth check.
        branch = branches[0]
        original = deepcopy(branch)
        branch["condition"] = "actor_state != null and kind == 'ability_check' and ability != null"
        branch["on_true"] = [
            original,
            _when(
                "ability == 'dexterity' and skill == 'stealth' and "
                "state.has_detection_masks == true",
                [
                    _call(
                        "check_srd_pass_without_trace",
                        {"target_state": "{_actor_state_id}"},
                        ["_mask_stealth_bonus", "_mask_trackless"],
                    ),
                    _set("_modifier", "_modifier + _mask_stealth_bonus"),
                ],
            ),
        ]
        changed["d20_test"] = roll
    for name in (
        "check_srd_illumination_subject",
        "resolve_srd_information_bearer_magic",
        "qualify_srd_information_candidate",
        "perceive_srd_information_proxy",
        "perceive_srd_mislead_proxy",
        "perceive_srd_project_image_proxy",
        "resolve_srd_control_behavior_conditions",
        "d20_test",
    ):
        if name not in runtime["tools"]:
            continue
        tool = _bind_mask_state(deepcopy(changed.get(name, runtime["tools"][name])), private_tools)
        if tool != runtime["tools"][name]:
            changed[name] = tool
    return changed


def _tools(runtime):
    private_tools = {
        "resolve_srd_detection_mask_subject": _subject_tool(),
        "check_srd_detection_mask_record": _record_tool(),
        "qualify_srd_detection_mask_concentration": _concentration_tool(),
        "normalize_srd_trace_creatures": _chosen_tool(),
        "qualify_srd_detection_mask_target": _target_tool(),
        "resolve_srd_magical_creature_type": _type_tool(),
        "check_srd_sequester_suspension": _suspension_tool(),
        "check_srd_detection_mask_subject": _query_tool(),
        "apply_srd_detection_mask": _install_tool(runtime),
        "capture_srd_sequester_invisible": _capture_tool(),
        "retire_srd_detection_mask": _retire_tool(),
        "finish_srd_detection_mask_concentration": _finish_concentration_tool(),
        "resolve_srd_sequester_condition_projection": _condition_projection_tool(),
        "end_srd_sequester_for_subject": _damage_subject_tool(),
        "resolve_srd_sequester_condition": _end_condition_tool(),
        "end_srd_sequester_for_target": _damage_tool(),
        "check_srd_pass_without_trace": _trace_tool(),
        "refresh_srd_detection_masks": _refresh_tool(),
    }
    for tool in private_tools.values():
        _bind_mask_state(tool, private_tools)
    return {**private_tools, **_consumers(runtime, private_tools)}


def extend_runtime(text):
    """Compose saved mask leaves and source delivery; the real paid cast binder remains separate."""
    runtime = _load_runtime(text)
    return _replace_tools(text, _tools(runtime))
