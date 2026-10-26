"""Meld into Stone keeps the actual body inside its retained stone and owns its exit."""

from copy import deepcopy

import yaml

if __package__:
    from .srd_body_soul_spells import _clock, _public
    from .srd_control_behaviors import _when
    from .srd_magic_jar import _patch_scene
    from .srd_magic_jar_consumers import _walk
    from .srd_scene_relations import POSITION_EXPORTS, _tool_scene
    from .srd_spell_travel import _move, _placement
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
    )
    from .srd_timed_senses import _loop, _optional, _push, _replace_tools
else:
    from srd_body_soul_spells import _clock, _public
    from srd_control_behaviors import _when
    from srd_magic_jar import _patch_scene
    from srd_magic_jar_consumers import _walk
    from srd_scene_relations import POSITION_EXPORTS, _tool_scene
    from srd_spell_travel import _move, _placement
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
    )
    from srd_timed_senses import _loop, _optional, _push, _replace_tools


SOURCE_PAGE = 148
SOURCE_SHA = "0b69f7c42359b5c879201a3384dad83a2e20deb742010787fad1cc7aa9757c71"
PROFILE = {
    "family": "meld_stone_spell",
    "base_level": 3,
    "casting_time": "action",
    "range_kind": "touch",
    "range_feet": 0,
    "concentration": False,
    "duration_rounds": 4800,
}
PUBLIC_TOOLS = {"leave_srd_meld_into_stone", "read_srd_meld_into_stone"}
CAST_CHOICES = [_optional("meld_stone_subject", default="")]
HIDDEN_SOURCE = [
    _optional("spell_cast_id", default="", model_hidden=True),
    _optional("spell_cast_owner", resolve="entity", model_hidden=True),
    _optional("actual_spell_level", "integer", -1, minimum=-1, maximum=9, model_hidden=True),
]


def validate_profiles(definitions):
    """The named source fixes level, eight-hour duration and actual self-only merging."""
    for spell, definition in definitions.items():
        if definition.get("family") == "meld_stone_spell" and (
            spell != "meld_into_stone" or definition != PROFILE
        ):
            raise ValueError("Meld into Stone differs from its pinned profile")


def _stone_lookup(subject="stone_subject"):
    """Resolve one exact scene object or surface; no size or occupancy verdict is accepted."""
    return [
        _call(
            "format_srd_information_projection",
            {"message": "{" + subject + "}"},
            ["_info_projection_message"],
        ),
        _set("_meld_stone_id", "_info_projection_message"),
        _set("_meld_stone", None),
        _set("_meld_stone_kind", None),
        _set("_meld_scene_objects", "state.spatial_scene.entities"),
        _loop(
            "_meld_scene_objects",
            "_meld_item",
            [
                _when(
                    "_meld_item.id == _meld_stone_id",
                    [
                        _require("_meld_stone == null", "The stone scene identity is ambiguous."),
                        _set("_meld_stone", "_meld_item"),
                        _set("_meld_stone_kind", "'entity'"),
                    ],
                ),
            ],
        ),
        _set("_meld_scene_surfaces", "state.spatial_scene.geometry"),
        _loop(
            "_meld_scene_surfaces",
            "_meld_item",
            [
                _when(
                    "_meld_item.id == _meld_stone_id",
                    [
                        _require("_meld_stone == null", "The stone scene identity is ambiguous."),
                        _set("_meld_stone", "_meld_item"),
                        _set("_meld_stone_kind", "'geometry'"),
                    ],
                ),
            ],
        ),
        _require("_meld_stone != null", "Select an actual admitted stone object or surface."),
        _set("_meld_material", None),
        _when(
            "_meld_stone.metadata.srd_object_material != null",
            [
                _set("_meld_material", "_meld_stone.metadata.srd_object_material.material"),
            ],
        ),
        _when(
            "_meld_material == null and _meld_stone.metadata.srd_spell_object != null",
            [
                _set("_meld_material", "_meld_stone.metadata.srd_spell_object.material"),
            ],
        ),
        _when(
            "_meld_material == null and _meld_stone.metadata.material != null",
            [
                _set("_meld_material", "_meld_stone.metadata.material"),
            ],
        ),
        _require("_meld_material != null", "The admitted stone needs its actual material fact."),
        _when(
            "_meld_material == 'stone_or_crystal' and _meld_stone.metadata.material == 'stone'",
            [_set("_meld_material", "'stone'")],
        ),
        _set("_meld_stone_min", [0, 0, 0]),
        _set("_meld_stone_max", [0, 0, 0]),
        _when(
            "_meld_stone_kind == 'geometry'",
            [
                _require(
                    "_meld_stone.volume.kind == 'box'", "Curved stone surfaces are unsupported."
                ),
                _set("_meld_stone_min", "_meld_stone.volume.min"),
                _set("_meld_stone_max", "_meld_stone.volume.max"),
            ],
            [
                _require(
                    "_meld_stone.metadata.participant_ref == null and "
                    "_meld_stone.metadata.is_object == true and "
                    "_meld_stone.footprint.kind == 'box'",
                    "The selected stone must be an actual box object, not a creature or proxy.",
                ),
                _set(
                    "_meld_stone_min",
                    [
                        "_meld_stone.position[0] - _meld_stone.footprint.half_extents[0]",
                        "_meld_stone.position[1] - _meld_stone.footprint.half_extents[1]",
                        "_meld_stone.position[2]",
                    ],
                ),
                _set(
                    "_meld_stone_max",
                    [
                        "_meld_stone.position[0] + _meld_stone.footprint.half_extents[0]",
                        "_meld_stone.position[1] + _meld_stone.footprint.half_extents[1]",
                        "_meld_stone.position[2] + _meld_stone.height",
                    ],
                ),
            ],
        ),
    ]


def _body_extents():
    """The actual admitted footprint and height qualify full physical containment."""
    return [
        _require("_meld_body.height > 0", "The actual body needs an admitted physical height."),
        _set("_meld_half", [0, 0]),
        _when(
            "_meld_body.footprint.kind == 'box'",
            [
                _set("_meld_half", "_meld_body.footprint.half_extents"),
            ],
            [
                _require(
                    "_meld_body.footprint.kind == 'circle'", "Unsupported actual body footprint."
                ),
                _set("_meld_half", ["_meld_body.footprint.radius", "_meld_body.footprint.radius"]),
            ],
        ),
        _set("_meld_body_extent", ["_meld_half[0] * 2", "_meld_half[1] * 2", "_meld_body.height"]),
        _set(
            "_meld_fits",
            "_meld_stone_max[0] - _meld_stone_min[0] >= _meld_body_extent[0] and "
            "_meld_stone_max[1] - _meld_stone_min[1] >= _meld_body_extent[1] and "
            "_meld_stone_max[2] - _meld_stone_min[2] >= _meld_body_extent[2] and "
            "_meld_stone.active == true and _meld_stone.movement == 'blocking'",
        ),
    ]


def _source_lookup(source_id="source_id"):
    return [
        _require("state.meld_stone_sources != null", "No Meld into Stone source is retained."),
        _set("_meld_source", f"state.meld_stone_sources[{source_id}]"),
        _require(
            f"_meld_source != null and _meld_source.id == {source_id} and "
            "_meld_source.spell == 'meld_into_stone' and "
            f"_meld_source.source_sha256 == {SOURCE_SHA!r}",
            "Select the actual pinned Meld into Stone source.",
        ),
    ]


def _validate_tool():
    effects = [
        _clock(),
        _require(
            "caster_state.is_alive == true and caster_state.hp > 0 and "
            "caster_state.meld_stone_source_id == null and "
            "caster_state.magic_jar_source_id == null and "
            "caster_state.magic_jar_role == null and caster_state.form_statistics_replaced != true "
            "and caster_state.form_cloud != true",
            "Melding needs the actual unmerged native body; Jar/form combinations are unsupported.",
        ),
        _call("srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS),
        _set("_meld_body", "_info_scene_entity"),
        *_stone_lookup(),
        *_body_extents(),
        _require(
            "_meld_material == 'stone' and _meld_fits == true and "
            "_meld_body.metadata.plane != null and "
            "_meld_stone.metadata.plane == _meld_body.metadata.plane",
            "The actual same-plane solid stone must fully contain this body.",
        ),
        _require(
            "_meld_stone.metadata.srd_spell_object == null or "
            "_meld_stone.metadata.srd_spell_object.destroyed != true",
            "An actually destroyed stone cannot receive this body.",
        ),
        _set(
            "_meld_body_min",
            [
                "_meld_body.position[0] - _meld_half[0]",
                "_meld_body.position[1] - _meld_half[1]",
                "_meld_body.position[2]",
            ],
        ),
        _set(
            "_meld_body_max",
            [
                "_meld_body.position[0] + _meld_half[0]",
                "_meld_body.position[1] + _meld_half[1]",
                "_meld_body.position[2] + _meld_body.height",
            ],
        ),
        _set(
            "_meld_touch_delta",
            [
                f"max(0, _meld_stone_min[{i}] - _meld_body_max[{i}], "
                f"_meld_body_min[{i}] - _meld_stone_max[{i}])"
                for i in range(3)
            ],
        ),
        _require(
            "_meld_touch_delta[0] == 0 and _meld_touch_delta[1] == 0 and _meld_touch_delta[2] == 0",
            "The actual body must touch the stone before merging.",
        ),
        _set(
            "_meld_inside",
            [
                "min(max(_meld_body.position[0], _meld_stone_min[0] + _meld_half[0]), "
                "_meld_stone_max[0] - _meld_half[0])",
                "min(max(_meld_body.position[1], _meld_stone_min[1] + _meld_half[1]), "
                "_meld_stone_max[1] - _meld_half[1])",
                "min(max(_meld_body.position[2], _meld_stone_min[2]), "
                "_meld_stone_max[2] - _meld_body.height)",
            ],
        ),
    ]
    return _tool_scene(
        "Qualify actual touching body, stone material and full physical containment.",
        [_entity("caster_state"), _parameter("stone_subject")],
        effects,
    )


def _cast_tool():
    effects = [
        _call(
            "validate_srd_meld_stone_cast",
            {
                "caster_state": "{_caster_state_id}",
                "stone_subject": "{stone_subject}",
            },
            ["_meld_body", "_meld_stone_id", "_meld_stone_kind", "_meld_inside"],
        ),
        _set("_meld_cast_owner", None),
        _when(
            "spell_cast_id != ''",
            [
                _require(
                    "spell_cast_owner != null and _spell_cast_owner_id == _caster_state_id and "
                    "caster_state.last_spell_source_cast_id == spell_cast_id and "
                    "caster_state.last_spell_source_spell == 'meld_into_stone' and "
                    "actual_spell_level == slot_level",
                    "Use the actual committed Meld cast identity.",
                ),
                _set("_meld_cast_owner", "_spell_cast_owner_id"),
            ],
        ),
        _when("state.meld_stone_serial == null", [_set("state.meld_stone_serial", 0)]),
        _set("state.meld_stone_serial", "state.meld_stone_serial + 1"),
        _call(
            "format_srd_information_projection",
            {
                "message": "{_caster_state_id}:meld_into_stone:{state.meld_stone_serial}",
            },
            ["_info_projection_message"],
        ),
        _set("_meld_source_id", "_info_projection_message"),
        _set(
            "_meld_source",
            {
                "id": "_meld_source_id",
                "spell": "'meld_into_stone'",
                "spell_id": "'meld_into_stone'",
                "caster": "_caster_state_id",
                "caster_state_id": "_caster_state_id",
                "target_state_id": "_caster_state_id",
                "body": "_meld_body.id",
                "stone": "_meld_stone_id",
                "stone_kind": "_meld_stone_kind",
                "entry": "_meld_body.position",
                "plane": "_meld_body.metadata.plane",
                "body_movement": "_meld_body.movement",
                "body_visibility": "_meld_body.visibility",
                "started_tick": "state.lasting_tick",
                "expires_tick": "state.lasting_tick + 9600",
                "installed": True,
                "concentration": False,
                "dispel_ongoing": True,
                "spell_level": "slot_level",
                "cast_id": "spell_cast_id",
                "source_caster_id": "_meld_cast_owner",
                "ongoing_duration": "spell_cast_id != ''",
                "source_sha256": repr(SOURCE_SHA),
            },
        ),
        _when("spell_cast_id == ''", [_set("_meld_source.source_caster_id", None)]),
        _when("state.meld_stone_sources == null", [_set("state.meld_stone_sources", {})]),
        _when("state.meld_stone_source_ids == null", [_set("state.meld_stone_source_ids", [])]),
        _set("state.meld_stone_sources.{_meld_source_id}", "_meld_source"),
        _set("_meld_retained_ids", "state.meld_stone_source_ids"),
        _push("_meld_retained_ids", "_meld_source_id"),
        _set("state.meld_stone_source_ids", "_meld_retained_ids"),
        _set("state.has_meld_stone_sources", True),
        # The actual sole mover commits this source-qualified physical merge, not a hidden plane.
        *_move("{_meld_body.id}", "{_meld_inside}", "{_meld_body.metadata.plane}", teleport=False),
        _set("caster_state.meld_stone_source_id", "_meld_source_id"),
        _call("srd_information_scene_entity", {"subject": "{_meld_body.id}"}, POSITION_EXPORTS),
        _set("_meld_body", "_info_scene_entity"),
        _set("_meld_body_changes", {"meld_stone_source_id": "_meld_source_id"}),
        _set("_meld_body_metadata", "merge(_meld_body.metadata, _meld_body_changes)"),
        *_patch_scene(
            "_meld_body",
            {"metadata": "{_meld_body_metadata}", "movement": "passable", "visibility": "clear"},
        ),
        _set("_spell_summary_rows", [{"label": "'Merged stone'", "value": "_meld_stone_id"}]),
        _set("_spell_detail_rows", [{"label": "'Source'", "value": "_meld_source_id"}]),
    ]
    return _tool_scene(
        "Merge the actual paid caster and equipment with the qualified stone.",
        [
            _entity("caster_state"),
            _parameter("stone_subject"),
            _parameter("slot_level", "integer", minimum=3, maximum=9),
            *deepcopy(HIDDEN_SOURCE),
        ],
        effects,
    )


def _end_tool():
    effects = [
        *_source_lookup(),
        _require("_meld_source.caster == _caster_state_id", "This caster does not own the source."),
        _set("_meld_ended", False),
        _when(
            "_meld_source.installed == true",
            [
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_meld_source.body}"},
                    POSITION_EXPORTS,
                ),
                _set("_meld_body", "_info_scene_entity"),
                _require(
                    "caster_state.meld_stone_source_id == source_id and "
                    "_meld_body.metadata.meld_stone_source_id == source_id",
                    "The actual body's merge source must match this exact lifetime.",
                ),
                _set("_travel_excluded", []),
                *_placement("{_meld_source.body}", "{_meld_source.entry}", "{_meld_source.plane}"),
                _when(
                    "reason != 'partial_stone' and reason != 'destroyed_stone'",
                    [
                        _require(
                            "_travel_placement.distance == 0",
                            "The ordinary exit is occupied; its ending placement is unsupported.",
                        ),
                    ],
                ),
                _set("_meld_exit_position", "_travel_placement.position"),
                _set("caster_state.meld_stone_source_id", None),
                _set("_meld_body_changes", {"meld_stone_source_id": None}),
                _set("_meld_body_metadata", "merge(_meld_body.metadata, _meld_body_changes)"),
                *_patch_scene(
                    "_meld_body",
                    {
                        "metadata": "{_meld_body_metadata}",
                        "movement": "{_meld_source.body_movement}",
                        "visibility": "{_meld_source.body_visibility}",
                    },
                ),
                _when(
                    "reason == 'voluntary'",
                    [
                        _call(
                            "spend_srd_travel_movement",
                            {"actor_state": "{_caster_state_id}", "amount": 5},
                        ),
                    ],
                ),
                _set("state.meld_stone_sources.{source_id}.installed", False),
                _set("state.meld_stone_sources.{source_id}.ended_reason", "reason"),
                *_move(
                    "{_meld_source.body}",
                    "{_meld_exit_position}",
                    "{_meld_source.plane}",
                    teleport=False,
                ),
                _set("_meld_damage", 0),
                _when(
                    "reason == 'partial_stone'",
                    [
                        {"dice": {"expression": "6d6"}, "store_as": "_meld_damage_roll"},
                        _set("_meld_damage", "_meld_damage_roll.total"),
                    ],
                ),
                _when("reason == 'destroyed_stone'", [_set("_meld_damage", 50)]),
                _when(
                    "_meld_damage > 0",
                    [
                        _call(
                            "apply_srd_qualifying_damage",
                            {
                                "target": "{_caster_state_id}",
                                "amount": "{_meld_damage}",
                                "adjustment": 0,
                                "damage_type": "force",
                                "source_state_id": "{_caster_state_id}",
                                "source_damage_kind": "spell",
                            },
                        ),
                        _call(
                            "apply_srd_condition",
                            {"target": "{_caster_state_id}", "condition": "prone"},
                        ),
                    ],
                ),
                _set("_meld_ended", True),
            ],
        ),
    ]
    return _tool_scene(
        "Retire one merge and resolve its actual exit, movement or stone expulsion.",
        [
            _entity("caster_state"),
            _parameter("source_id"),
            _parameter(
                "reason",
                "enum",
                enum_values=[
                    "voluntary",
                    "expired",
                    "dispel",
                    "dismissed",
                    "partial_stone",
                    "destroyed_stone",
                ],
            ),
        ],
        effects,
    )


def _observe_tool():
    effects = [
        _clock(),
        *_source_lookup(),
        _when(
            "_meld_source.installed == true",
            [
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_meld_source.body}"},
                    POSITION_EXPORTS,
                ),
                _set("_meld_body", "_info_scene_entity"),
                *_stone_lookup("_meld_source.stone"),
                *_body_extents(),
                _set("_meld_reason", "'none'"),
                _when("_meld_fits != true", [_set("_meld_reason", "'partial_stone'")]),
                _when(
                    "_meld_material != 'stone' or (_meld_stone.metadata.srd_spell_object != null "
                    "and _meld_stone.metadata.srd_spell_object.destroyed == true)",
                    [
                        _set("_meld_reason", "'destroyed_stone'"),
                    ],
                ),
                _when(
                    "_meld_reason == 'none' and state.lasting_tick >= _meld_source.expires_tick",
                    [
                        _set("_meld_reason", "'expired'"),
                    ],
                ),
                _when(
                    "_meld_reason != 'none'",
                    [
                        _call(
                            "end_srd_meld_stone_source",
                            {
                                "caster_state": "{_meld_source.caster}",
                                "source_id": "{source_id}",
                                "reason": "{_meld_reason}",
                            },
                            ["_meld_ended"],
                        ),
                    ],
                ),
            ],
        ),
    ]
    return _tool_scene(
        "Observe actual stone material, shape, destruction and the saved eight-hour deadline.",
        [_parameter("source_id")],
        effects,
    )


def _tools():
    leave = _public(
        _tool_scene(
            "Use five feet of actual movement to leave where the body entered.",
            [
                _entity("caster_state"),
            ],
            [
                _require(
                    "caster_state.meld_stone_source_id != null", "The actual body is not merged."
                ),
                _call(
                    "end_srd_meld_stone_source",
                    {
                        "caster_state": "{_caster_state_id}",
                        "source_id": "{caster_state.meld_stone_source_id}",
                        "reason": "voluntary",
                    },
                    ["_meld_ended"],
                ),
            ],
        ),
        [("Left stone", "_meld_ended")],
    )
    read = _public(
        _tool_scene(
            "Read the exact merge source and actual passage of time.",
            [
                _entity("caster_state"),
            ],
            [
                _require(
                    "caster_state.meld_stone_source_id != null", "The actual body is not merged."
                ),
                *_source_lookup("caster_state.meld_stone_source_id"),
                _clock(),
                _set(
                    "_meld_elapsed_rounds", "(state.lasting_tick - _meld_source.started_tick) // 2"
                ),
            ],
        ),
        [("Merged source", "_meld_source"), ("Elapsed rounds", "_meld_elapsed_rounds")],
    )
    refresh = _tool_scene(
        "Settle only actual retained merges after a clock or physical object change.",
        [],
        [
            _when(
                "state.has_meld_stone_sources == true",
                [
                    _set("_meld_ids", "state.meld_stone_source_ids"),
                    _set("_meld_active_ids", []),
                    _loop(
                        "_meld_ids",
                        "_meld_id",
                        [
                            _set("_meld_refresh_source", "state.meld_stone_sources[_meld_id]"),
                            _when(
                                "_meld_refresh_source.installed == true",
                                [
                                    _call(
                                        "observe_srd_meld_stone_source", {"source_id": "{_meld_id}"}
                                    ),
                                    _set(
                                        "_meld_refresh_source", "state.meld_stone_sources[_meld_id]"
                                    ),
                                    _when(
                                        "_meld_refresh_source.installed == true",
                                        [
                                            _push("_meld_active_ids", "_meld_id"),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _set("state.has_meld_stone_sources", "len(_meld_active_ids) > 0"),
                ],
            ),
        ],
    )
    return {
        "validate_srd_meld_stone_cast": _validate_tool(),
        "cast_srd_meld_into_stone": _cast_tool(),
        "end_srd_meld_stone_source": _end_tool(),
        "observe_srd_meld_stone_source": _observe_tool(),
        "refresh_srd_meld_stone_sources": refresh,
        "leave_srd_meld_into_stone": leave,
        "read_srd_meld_into_stone": read,
    }


def extend_runtime(text):
    """Install the family before final source, damage and pending-action consumers compose."""
    tools = _load_runtime(text)["tools"]
    cast = deepcopy(tools["cast_spell"])
    for p in CAST_CHOICES:
        if not any(old["name"] == p["name"] for old in cast["parameters"]):
            cast["parameters"].append(deepcopy(p))
    marker = "_spell_cast_failed != true and _spell_definition.family == 'meld_stone_spell'"
    source_arguments = {}

    def retain_source_arguments(node):
        if node.get("call") == "cast_srd_meld_into_stone":
            source_arguments.update(
                {
                    k: v
                    for k, v in node.get("params", {}).items()
                    if k in {p["name"] for p in HIDDEN_SOURCE}
                }
            )

    _walk(cast["effects"], retain_source_arguments)
    cast["effects"] = [e for e in cast["effects"] if e.get("condition") != marker]
    cast["effects"].append(
        _when(
            marker,
            [
                _call(
                    "cast_srd_meld_into_stone",
                    {
                        "caster_state": "{_caster_state_id}",
                        "stone_subject": "{meld_stone_subject}",
                        "slot_level": "{_slot_level}",
                    }
                    | source_arguments,
                    ["_spell_summary_rows", "_spell_detail_rows"],
                ),
            ],
        )
    )
    guard = {
        "condition": "spell != 'meld_into_stone' or _target_id == _caster_state_id",
        "error": "Meld into Stone merges the actual caster only.",
    }
    if guard not in cast["pre_conditions"]:
        cast["pre_conditions"].append(guard)
    text = _replace_tools(text, _tools() | {"cast_spell": cast})
    if "      meld_stone_spell:" not in text:
        properties = {
            k: {"type": "boolean" if type(v) is bool else "integer" if type(v) is int else "string"}
            for k, v in PROFILE.items()
            if k != "family"
        }
        schema = yaml.safe_dump(
            {
                "meld_stone_spell": {
                    "type": "object",
                    "properties": properties,
                    "required": list(properties),
                    "additionalProperties": False,
                }
            },
            sort_keys=False,
        )
        text = text.replace(
            "      timed_senses:",
            "".join("      " + line for line in schema.splitlines(True)) + "\n      timed_senses:",
            1,
        )
    return text
