"""Prismatic Wall owns ordered layers and their actual saved body and consequences."""

import re
from copy import deepcopy

import yaml

if __package__:
    from .srd_actor_spell_modifiers import _call, _when
    from .srd_control_behaviors import _each, _push
    from .srd_scene_relations import _array
    from .srd_spell_barriers import _effect_lists, _scene, _tool
    from .srd_timed_actor_defenses import _entity, _load_runtime, _parameter, _require, _set
    from .srd_timed_senses import _optional, _replace_tools
else:
    from srd_actor_spell_modifiers import _call, _when
    from srd_control_behaviors import _each, _push
    from srd_scene_relations import _array
    from srd_spell_barriers import _effect_lists, _scene, _tool
    from srd_timed_actor_defenses import _entity, _load_runtime, _parameter, _require, _set
    from srd_timed_senses import _optional, _replace_tools


PROFILES = {
    "prismatic_wall": {
        "family": "prismatic_wall",
        "base_level": 9,
        "casting_time": "action",
        "range_kind": "distance",
        "range_feet": 60,
        "duration_rounds": 100,
        "concentration": False,
    }
}
PUBLIC_TOOLS = {"reach_into_srd_prismatic_wall"}
COLORS = ("red", "orange", "yellow", "green", "blue", "indigo", "violet")
POOL = "spell_prismatic_wall_sources"


def validate_profiles(definitions):
    """Admit only the pinned level9 non-concentration wall header."""
    for spell, profile in definitions.items():
        if profile.get("family") == "prismatic_wall" and profile != PROFILES.get(spell):
            raise SystemExit(f"Prismatic Wall profile differs from its source: {spell}")


def _source_current(record):
    # Antimagic cannot suppress a genuine installed Prismatic Wall.
    return (
        f"{record}.spell_id == 'prismatic_wall' and {record}.installed == true and "
        f"state.lasting_tick < {record}.expires_tick and {record}.layers_remaining > 0"
    )


def _read_source():
    return [
        _require(
            f"state.{POOL} != null and source_id in state.{POOL}",
            "Select an actual saved Prismatic Wall source.",
        ),
        _set("_wall_record", f"state.{POOL}[source_id]"),
    ]


def _sources(effects, *, condition=None):
    current = _source_current("_wall_source")
    if condition:
        current += f" and ({condition})"
    return [
        _when(
            "state.has_prismatic_walls == true",
            [
                _set("_wall_source_ids", "state.spell_prismatic_wall_source_ids"),
                _each(
                    "_wall_source_ids",
                    "_wall_id",
                    [
                        _set("_wall_source", f"state.{POOL}[_wall_id]"),
                        _when(current, effects),
                    ],
                ),
            ],
        )
    ]


def _distance(point, volume, output):
    """Measure exact Euclidean distance to the supported saved vertical Box surface."""
    return [
        _set(
            "_wall_nearest",
            [f"min(max({point}[{a}], {volume}.min[{a}]), {volume}.max[{a}])" for a in range(3)],
        ),
        _call(
            "measure_srd_information_distance",
            {"start": "{" + point + "}", "end": "{_wall_nearest}"},
            {output: "_scene_distance"},
        ),
    ]


def _body_bounds(body, point, output):
    return _set(
        output,
        {
            "min": [
                f"{point}[0] - {body}.footprint.half_extents[0]",
                f"{point}[1] - {body}.footprint.half_extents[1]",
                f"{point}[2]",
            ],
            "max": [
                f"{point}[0] + {body}.footprint.half_extents[0]",
                f"{point}[1] + {body}.footprint.half_extents[1]",
                f"{point}[2] + {body}.height",
            ],
        },
    )


def _overlap(a, b):
    return " and ".join(
        f"{a}.min[{i}] < {b}.max[{i}] and {a}.max[{i}] > {b}.min[{i}]" for i in range(3)
    )


def _same_points(a, b):
    return " and ".join(f"{a}[{axis}] == {b}[{axis}]" for axis in range(3))


def _strict_segment(volume, output):
    """A positive interior interval excludes constant-axis tangency to a saved Box face."""
    return [
        _set(output, "_segment_hit == true and _segment_exit > _segment_entry"),
        *[
            _when(
                f"start[{axis}] == end[{axis}] and (start[{axis}] <= {volume}.min[{axis}] "
                f"or start[{axis}] >= {volume}.max[{axis}])",
                [_set(output, False)],
            )
            for axis in range(3)
        ],
    ]


def _source_tools():
    current = _tool(
        "Read the genuine installed Wall lifetime and its ordered outer layer.",
        [_parameter("source_id")],
        [
            *_read_source(),
            _set("_prismatic_wall_current", _source_current("_wall_record")),
            _set("_prismatic_wall_outer_layer", "_wall_record.outer_layer"),
            _set("_prismatic_wall_antimagic_exempt", "_prismatic_wall_current"),
        ],
    )
    retire = _tool(
        "End one exact Wall, preserving instantaneous Indigo and Violet target consequences.",
        [_parameter("source_id"), _optional("reason", default="removed")],
        [
            *_read_source(),
            _when(
                "_wall_record.installed == true",
                [
                    _set(f"state.{POOL}.{{source_id}}.installed", False),
                    _set(f"state.{POOL}.{{source_id}}.ended_tick", "state.lasting_tick"),
                    _set(f"state.{POOL}.{{source_id}}.ended_reason", "reason"),
                ],
            ),
        ],
    )
    layer = _tool(
        "Retire one actual outer layer after its owning source-specific destruction event.",
        [
            _parameter("source_id"),
            _parameter("expected_layer", "integer"),
            _parameter("reason"),
        ],
        [
            *_read_source(),
            _require(_source_current("_wall_record"), "The selected Wall must be current."),
            _require(
                "expected_layer == _wall_record.outer_layer and expected_layer >= 1 and "
                "expected_layer <= 7",
                "Destroy Wall layers in their actual red-to-violet order.",
            ),
            _set(
                f"state.{POOL}.{{source_id}}.layers_remaining", "_wall_record.layers_remaining - 1"
            ),
            _set(f"state.{POOL}.{{source_id}}.outer_layer", "expected_layer + 1"),
            _set(f"state.{POOL}.{{source_id}}.last_layer_end_reason", "reason"),
            _when(
                "expected_layer == 7",
                [
                    _call(
                        "end_srd_prismatic_wall_source",
                        {"source_id": "{source_id}", "reason": "all layers ended"},
                    )
                ],
            ),
        ],
    )
    refresh = _tool(
        "Expire registered Walls only; their caster's death and Antimagic do not end them.",
        [],
        [
            _set("_walls_live", False),
            _when(
                "state.has_prismatic_walls == true",
                [
                    _set("_refresh_wall_ids", "state.spell_prismatic_wall_source_ids"),
                    _each(
                        "_refresh_wall_ids",
                        "_refresh_wall_id",
                        [
                            _set("_refresh_wall", f"state.{POOL}[_refresh_wall_id]"),
                            _when(
                                _source_current("_refresh_wall"),
                                [_set("_walls_live", True)],
                                [
                                    _when(
                                        "_refresh_wall.installed == true",
                                        [
                                            _call(
                                                "end_srd_prismatic_wall_source",
                                                {
                                                    "source_id": "{_refresh_wall_id}",
                                                    "reason": "duration",
                                                },
                                            )
                                        ],
                                    )
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _set("state.has_prismatic_walls", "_walls_live"),
        ],
    )
    return {
        "qualify_srd_prismatic_wall_source": current,
        "end_srd_prismatic_wall_source": retire,
        "retire_srd_prismatic_wall_layer": layer,
        "refresh_srd_prismatic_walls": refresh,
    }


def _cast_tool():
    install = [
        _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
        _when("state.lasting_serial == null", [_set("state.lasting_serial", 0)]),
        {"increment": {"field": "state.lasting_serial", "amount": 1}},
        _call(
            "record_srd_result_text",
            {"text": "prismatic-wall:{_caster_state_id}:{state.lasting_serial}"},
            {"_new_prismatic_wall_source_id": "_recorded_result_text"},
        ),
        _when(
            f"state.{POOL} == null",
            [_set(f"state.{POOL}", {}), _set("state.spell_prismatic_wall_source_ids", [])],
        ),
        _set(
            f"state.{POOL}.{{_new_prismatic_wall_source_id}}",
            {
                "installed": True,
                "spell_id": "'prismatic_wall'",
                "spell_level": "slot_level",
                "caster_state_id": "_caster_state_id",
                "source_caster_id": "_caster_state_id",
                "cast_id": "spell_cast_id",
                "serial": "state.lasting_serial",
                "started_tick": "state.lasting_tick",
                "expires_tick": "state.lasting_tick + 200",
                "volume": "_cast_wall_volume",
                "origin": "origin",
                "plane": "_cast_wall_caster.metadata.plane",
                "shape": "'plane'",
                "axis": "axis",
                "save_dc": "save_dc",
                "designated_creatures": "designated_creatures",
                "layers_remaining": 7,
                "outer_layer": 1,
                "armor_class": 10,
                "antimagic_exempt": True,
                "dispel_ongoing": False,
            },
        ),
        _set("_retained_wall_ids", "state.spell_prismatic_wall_source_ids"),
        _push("_retained_wall_ids", "_new_prismatic_wall_source_id"),
        _set("state.spell_prismatic_wall_source_ids", "_retained_wall_ids"),
        _set("state.has_prismatic_walls", True),
        _set("caster_state.last_prismatic_wall_source_id", "_new_prismatic_wall_source_id"),
    ]
    return _tool(
        "Install the genuine cardinal Wall, or retain its occupied-space no-effect result.",
        [
            _entity("caster_state"),
            _parameter("slot_level", "integer"),
            _parameter("save_dc", "integer"),
            _array("origin"),
            _optional("axis", default="east_west"),
            _optional("length", "number", 90),
            _optional("height", "number", 30),
            _optional("designated_creatures", "array", [], items={"type": "string"}),
            _optional("spell_cast_id", default="", model_hidden=True),
        ],
        [
            _require(
                "slot_level == 9 and save_dc >= 1", "Use the actual level9 cast and derived DC."
            ),
            _require(
                "length > 0 and length <= 90 and height > 0 and height <= 30",
                "The source limits the Wall to90feet by30feet.",
            ),
            _require(
                "axis == 'east_west' or axis == 'north_south'",
                "Other Wall orientations and a Globe need supported geometry.",
            ),
            *_scene("_caster_state_id", "_cast_wall_caster"),
            _call(
                "measure_srd_information_distance",
                {"start": "{_cast_wall_caster.position}", "end": "{origin}"},
                ("_scene_distance",),
            ),
            _require("_scene_distance <= 60", "Place the Wall's actual center within60feet."),
            _set("_wall_half_x", "length * 0.5"),
            _set("_wall_half_y", 1 / 24),
            _when(
                "axis == 'north_south'",
                [_set("_wall_half_x", 1 / 24), _set("_wall_half_y", "length * 0.5")],
            ),
            _set(
                "_cast_wall_volume",
                {
                    "kind": "'box'",
                    "min": [
                        "origin[0] - _wall_half_x",
                        "origin[1] - _wall_half_y",
                        "origin[2] - height * 0.5",
                    ],
                    "max": [
                        "origin[0] + _wall_half_x",
                        "origin[1] + _wall_half_y",
                        "origin[2] + height * 0.5",
                    ],
                },
            ),
            *[
                _require(
                    f"_cast_wall_volume.min[{a}] >= state.spatial_scene.map.bounds.min[{a}] and "
                    f"_cast_wall_volume.max[{a}] <= state.spatial_scene.map.bounds.max[{a}]",
                    "The actual Wall must remain inside the admitted map.",
                )
                for a in range(3)
            ],
            _each(
                "designated_creatures",
                "_wall_designee",
                [
                    _call(
                        "validate_srd_object_lock_creature", {"creature_state": "{_wall_designee}"}
                    )
                ],
            ),
            _set("_prismatic_wall_occupied", False),
            _set("_wall_placement_entities", "state.spatial_scene.entities"),
            _each(
                "_wall_placement_entities",
                "_wall_placement_body",
                [
                    _when(
                        "_wall_placement_body.active != false and "
                        "_wall_placement_body.metadata.participant_ref != null and "
                        "_wall_placement_body.metadata.plane == _cast_wall_caster.metadata.plane",
                        [
                            _require(
                                "_wall_placement_body.footprint.kind == 'box'",
                                "Occupied-space placement needs actual supported creature bodies.",
                            ),
                            _body_bounds(
                                "_wall_placement_body",
                                "_wall_placement_body.position",
                                "_placement_body_bounds",
                            ),
                            _when(
                                _overlap("_placement_body_bounds", "_cast_wall_volume"),
                                [_set("_prismatic_wall_occupied", True)],
                            ),
                        ],
                    ),
                ],
            ),
            _set("_new_prismatic_wall_source_id", None),
            _when("_prismatic_wall_occupied != true", install),
            _set(
                "caster_state.last_prismatic_wall_cast",
                {
                    "occupied": "_prismatic_wall_occupied",
                    "source_id": "_new_prismatic_wall_source_id",
                    "volume": "_cast_wall_volume",
                },
            ),
            _set("_spell_summary_rows", [["'Prismatic Wall'", "_new_prismatic_wall_source_id"]]),
            _set(
                "_spell_detail_rows", [["'Occupied-space no effect'", "_prismatic_wall_occupied"]]
            ),
        ],
    )


def _query_tools():
    ray = _tool(
        "Qualify a genuine positive interior sight or spell segment through the saved Wall.",
        [_array("start"), _array("end"), _parameter("plane"), _parameter("source_id")],
        [
            *_read_source(),
            _set("_prismatic_wall_segment_hit", False),
            _when(
                _source_current("_wall_record") + " and plane == _wall_record.plane",
                [
                    _call(
                        "srd_information_segment_interval",
                        {"start": "{start}", "end": "{end}", "volume": "{_wall_record.volume}"},
                        ("_segment_hit", "_segment_entry", "_segment_exit"),
                    ),
                    *_strict_segment("_wall_record.volume", "_prismatic_wall_segment_hit"),
                ],
            ),
        ],
    )
    light = _tool(
        "Read source-level Wall light at the actual point; this light is never sunlight.",
        [_array("point"), _parameter("plane")],
        [
            _set("_prismatic_wall_light", None),
            _set("_prismatic_wall_light_spell_level", -1),
            _set("_prismatic_wall_light_source_id", None),
            *_sources(
                [
                    _when(
                        "_wall_source.plane == plane",
                        [
                            *_distance("point", "_wall_source.volume", "_wall_light_distance"),
                            _when(
                                "_wall_light_distance <= 200 and _prismatic_wall_light != 'bright'",
                                [
                                    _set("_prismatic_wall_light", "'dim'"),
                                    _when(
                                        "_wall_light_distance <= 100",
                                        [_set("_prismatic_wall_light", "'bright'")],
                                    ),
                                    _set(
                                        "_prismatic_wall_light_spell_level",
                                        "_wall_source.spell_level",
                                    ),
                                    _set("_prismatic_wall_light_source_id", "_wall_id"),
                                ],
                            ),
                        ],
                    ),
                ]
            ),
        ],
    )
    obscuration = _tool(
        "The genuine Wall remains opaque while any ordered layer remains, including to its caster.",
        [_array("start"), _array("end"), _parameter("plane")],
        [
            _set("_prismatic_wall_observation_blocked", False),
            *_sources(
                [
                    _call(
                        "resolve_srd_prismatic_wall_segment",
                        {
                            "start": "{start}",
                            "end": "{end}",
                            "plane": "{plane}",
                            "source_id": "{_wall_id}",
                        },
                        ("_prismatic_wall_segment_hit",),
                    ),
                    _when(
                        "_prismatic_wall_segment_hit == true",
                        [_set("_prismatic_wall_observation_blocked", True)],
                    ),
                ]
            ),
        ],
    )
    spell = deepcopy(obscuration)
    spell["description"] = "Indigo alone prevents an actual spell segment through the genuine Wall."
    for effect in _effect_lists(spell["effects"]):
        for entry in effect:
            if entry.get("set", {}).get("field") == "_prismatic_wall_observation_blocked":
                entry["set"]["field"] = "_prismatic_wall_spell_blocked"
            if entry.get("condition") == "_prismatic_wall_segment_hit == true":
                entry["condition"] += " and _wall_source.outer_layer <= 6"
    attack = deepcopy(obscuration)
    attack["description"] = "Red and Orange qualify an actual source-derived ranged attack segment."
    attack["parameters"].append(_optional("magical", "boolean", False, model_hidden=True))
    for effects in _effect_lists(attack["effects"]):
        for entry in effects:
            if entry.get("set", {}).get("field") == "_prismatic_wall_observation_blocked":
                entry["set"]["field"] = "_prismatic_wall_attack_blocked"
            if entry.get("condition") == "_prismatic_wall_segment_hit == true":
                entry["condition"] += (
                    " and ((_wall_source.outer_layer <= 1 and magical != true) or "
                    "(_wall_source.outer_layer <= 2 and magical == true))"
                )
    return {
        "resolve_srd_prismatic_wall_segment": ray,
        "check_srd_prismatic_wall_light": light,
        "check_srd_prismatic_wall_observation": obscuration,
        "check_srd_prismatic_wall_spell_segment": spell,
        "check_srd_prismatic_wall_attack_segment": attack,
    }


def _body_segment_tool():
    return _tool(
        "Qualify physical passage of the actual admitted Box body through the saved Wall.",
        [_parameter("subject"), _array("start"), _array("end"), _parameter("source_id")],
        [
            *_read_source(),
            *_scene("subject", "_wall_segment_body"),
            _require(
                "_wall_segment_body.footprint.kind == 'box'",
                "Wall passage needs supported actual creature body geometry.",
            ),
            _set("_prismatic_wall_body_hit", False),
            _when(
                _source_current("_wall_record")
                + " and _wall_segment_body.metadata.plane == _wall_record.plane",
                [
                    _set(
                        "_expanded_wall_volume",
                        {
                            "kind": "'box'",
                            "min": [
                                "_wall_record.volume.min[0] - "
                                "_wall_segment_body.footprint.half_extents[0]",
                                "_wall_record.volume.min[1] - "
                                "_wall_segment_body.footprint.half_extents[1]",
                                "_wall_record.volume.min[2] - _wall_segment_body.height",
                            ],
                            "max": [
                                "_wall_record.volume.max[0] + "
                                "_wall_segment_body.footprint.half_extents[0]",
                                "_wall_record.volume.max[1] + "
                                "_wall_segment_body.footprint.half_extents[1]",
                                "_wall_record.volume.max[2]",
                            ],
                        },
                    ),
                    _call(
                        "srd_information_segment_interval",
                        {
                            "start": "{start}",
                            "end": "{end}",
                            "volume": "{_expanded_wall_volume}",
                        },
                        ("_segment_hit", "_segment_entry", "_segment_exit"),
                    ),
                    *_strict_segment("_expanded_wall_volume", "_prismatic_wall_body_hit"),
                ],
            ),
        ],
    )


def _movement_observer_tool():
    return _tool(
        "Observe a committed same-plane physical move without a second position authority.",
        [
            _parameter("subject"),
            _array("start"),
            _array("end"),
            _parameter("origin_plane"),
            _optional("teleport", "boolean", False, model_hidden=True),
            _optional(
                "wall_source_ids", "array", None, items={"type": "string"}, model_hidden=True
            ),
        ],
        [
            *_scene("subject", "_wall_moving_body"),
            _call(
                "measure_srd_information_distance",
                {"start": "{start}", "end": "{end}"},
                ("_scene_distance",),
            ),
            _when(
                "_wall_moving_body.metadata.participant_ref != null and _scene_distance > 0 and "
                + _same_points("_wall_moving_body.position", "end"),
                _sources(
                    [
                        _when(
                            "teleport != true and origin_plane == _wall_source.plane and "
                            "_wall_moving_body.metadata.plane == _wall_source.plane",
                            [
                                _call(
                                    "resolve_srd_prismatic_wall_body_segment",
                                    {
                                        "subject": "{subject}",
                                        "start": "{start}",
                                        "end": "{end}",
                                        "source_id": "{_wall_id}",
                                    },
                                    ("_prismatic_wall_body_hit",),
                                ),
                                _when(
                                    "_prismatic_wall_body_hit == true",
                                    [
                                        _call(
                                            "resolve_srd_prismatic_wall_contact",
                                            {
                                                "target_state": "{subject}",
                                                "caster_state": "{_wall_source.caster_state_id}",
                                                "source_id": "{_wall_id}",
                                            },
                                        )
                                    ],
                                ),
                            ],
                        ),
                        _call(
                            "resolve_srd_prismatic_wall_near",
                            {"target_state": "{subject}", "source_id": "{_wall_id}"},
                        ),
                    ],
                    condition="wall_source_ids == null or _wall_id in wall_source_ids",
                ),
            ),
        ],
    )


def _cast_dispatch(tools):
    """Forward only world-layout choices; the actual cast owns its DC, slot and costs."""
    cast = deepcopy(tools["cast_spell"])
    for parameter in [
        _array("prismatic_wall_origin", default=[0, 0, 0]),
        _optional("prismatic_wall_axis", default="east_west"),
        _optional("prismatic_wall_length", "number", 90),
        _optional("prismatic_wall_height", "number", 30),
        _optional("prismatic_wall_designations", "array", [], items={"type": "string"}),
    ]:
        if parameter["name"] not in {p["name"] for p in cast["parameters"]}:
            cast["parameters"].append(parameter)
    for effects in _effect_lists(cast["effects"]):
        effects[:] = [
            effect
            for effect in effects
            if "_spell_definition.family == 'prismatic_wall'" not in effect.get("condition", "")
        ]
    index = max(
        i
        for i, effect in enumerate(cast["effects"])
        if "_spell_definition.family ==" in effect.get("condition", "")
    )
    cast["effects"].insert(
        index + 1,
        _when(
            "_spell_definition.family == 'prismatic_wall'",
            [
                _call(
                    "cast_srd_prismatic_wall",
                    {
                        "caster_state": "{_caster_state_id}",
                        "slot_level": "{_slot_level}",
                        "save_dc": "{_casting_profile.save_dc}",
                        "origin": "{prismatic_wall_origin}",
                        "axis": "{prismatic_wall_axis}",
                        "length": "{prismatic_wall_length}",
                        "height": "{prismatic_wall_height}",
                        "designated_creatures": "{prismatic_wall_designations}",
                        "spell_cast_id": "{_spell_source_cast_id}",
                    },
                    ("_spell_summary_rows", "_spell_detail_rows"),
                )
            ],
        ),
    )
    return cast


def _movement_queue_tools():
    """Retain actual relocation evidence without recursively invoking damage lifecycle observers."""
    queue = _tool(
        "Retain committed movement evidence for the outer operation's Wall consequences.",
        [
            p
            for p in deepcopy(_movement_observer_tool()["parameters"])
            if p["name"] != "wall_source_ids"
        ],
        [
            *_scene("subject", "_wall_queue_body"),
            _when(
                "_wall_queue_body.metadata.participant_ref != null and "
                + _same_points("_wall_queue_body.position", "end")
                + " and (start[0] != end[0] or start[1] != end[1] or start[2] != end[2])",
                [
                    _when(
                        "state.pending_prismatic_wall_movements == null",
                        [_set("state.pending_prismatic_wall_movements", [])],
                    ),
                    _set("_wall_queued_movements", "state.pending_prismatic_wall_movements"),
                    _set("_wall_movement_sources", []),
                    *_sources([_push("_wall_movement_sources", "_wall_id")]),
                    _push(
                        "_wall_queued_movements",
                        {
                            "subject": "subject",
                            "start": "start",
                            "end": "end",
                            "origin_plane": "origin_plane",
                            "teleport": "teleport",
                            "scene_revision": "state.spatial_scene.map.revision",
                            "wall_source_ids": "_wall_movement_sources",
                        },
                    ),
                    _set("state.pending_prismatic_wall_movements", "_wall_queued_movements"),
                ],
            ),
        ],
    )
    drain = _tool(
        "Settle saved genuine movement receipts once at an outer operation boundary.",
        [],
        [
            _when(
                "state.pending_prismatic_wall_movements != null and "
                "len(state.pending_prismatic_wall_movements) > 0",
                [
                    _set("_wall_pending_movements", "state.pending_prismatic_wall_movements"),
                    _set("state.pending_prismatic_wall_movements", []),
                    _each(
                        "_wall_pending_movements",
                        "_wall_movement_receipt",
                        [
                            _call(
                                "observe_srd_prismatic_wall_relocation",
                                {
                                    name: "{_wall_movement_receipt." + name + "}"
                                    for name in (
                                        "subject",
                                        "start",
                                        "end",
                                        "origin_plane",
                                        "teleport",
                                        "wall_source_ids",
                                    )
                                },
                            )
                        ],
                    ),
                ],
            ),
        ],
    )
    return {
        "queue_srd_prismatic_wall_relocation": queue,
        "settle_srd_prismatic_wall_movements": drain,
    }


def _consequence_tools(tools):
    """Reuse settled ray counters without relabeling a Wall consequence as Spray."""
    consequence = deepcopy(tools["install_srd_prismatic_consequence"])
    consequence["parameters"].append(_parameter("wall_source_id"))
    consequence["effects"][:0] = [
        _set("_consequence_wall", f"state.{POOL}[wall_source_id]"),
        _require(
            _source_current("_consequence_wall"),
            "The consequence must arise from its actual current Wall.",
        ),
        _require(
            "_consequence_wall.caster_state_id == _caster_state_id and "
            "ray >= _consequence_wall.outer_layer",
            "Bind the actual caster and remaining layer.",
        ),
    ]
    for effects in _effect_lists(consequence["effects"]):
        for effect in effects:
            if effect.get("call") == "install_srd_timed_condition_source":
                effect["params"]["spell"] = "prismatic_wall"
    consequence["effects"].append(
        _set(
            "state.timed_condition_sources.{_prismatic_source_id}.prismatic_wall_source_id",
            "wall_source_id",
        )
    )
    consequence["effects"].append(
        _set(
            "state.timed_condition_sources.{_prismatic_source_id}.prismatic_wall_source_serial",
            "_consequence_wall.serial",
        )
    )
    consequence["effects"].append(
        _set(
            "state.timed_condition_sources.{_prismatic_source_id}.spell_level",
            "_consequence_wall.spell_level",
        )
    )
    target = deepcopy(tools["resolve_srd_prismatic_target"])
    for parameter in target["parameters"]:
        if parameter["name"] == "ray_history":
            # Wall colors are printed ordered layers, rather than random Spray selections.
            parameter["min_items"] = 0
    target["parameters"].append(_parameter("wall_source_id"))
    target["parameters"].append(
        _optional("wall_unallocated_roll_penalty", "integer", 0, model_hidden=True)
    )
    for effects in _effect_lists(target["effects"]):
        for effect in effects:
            if effect.get("call") == "install_srd_prismatic_consequence":
                effect["call"] = "install_srd_prismatic_wall_consequence"
                effect["params"]["wall_source_id"] = "{wall_source_id}"
            if effect.get("call", "").startswith("apply_srd_") and "qualifying" in effect["call"]:
                effect["params"]["unallocated_roll_penalty"] = "{wall_unallocated_roll_penalty}"
    return {
        "install_srd_prismatic_wall_consequence": consequence,
        "resolve_srd_prismatic_wall_layer_target": target,
    }


def _contact_tool():
    """Each remaining layer rolls its own12d6 and actualDEX save in printed order."""
    layer = [
        _set("_wall_selected_rays", ["_wall_layer"]),
        _set("_wall_selected_labels", ["_wall_color"]),
        _set("_wall_layer_amount", 0),
        _set("_wall_layer_debt", 0),
        _set("_wall_layer_roll", None),
        _when(
            "_wall_layer <= 5",
            [
                {"dice": {"expression": "12d6"}, "store_as": "_wall_layer_roll"},
                _set("_wall_layer_amount", "_wall_layer_roll.total"),
                _when(
                    "caster_state.has_actor_spell_modifiers == true",
                    [
                        _call(
                            "resolve_srd_actor_spell_modifiers",
                            {"target_state": "{_caster_state_id}", "lasting_state": "state"},
                        ),
                        _call(
                            "modify_srd_actor_raw_damage_roll",
                            {
                                "actor_state": "{_caster_state_id}",
                                "lasting_state": "state",
                                "amount": "{_wall_layer_amount}",
                            },
                            {"_wall_layer_amount": "_ray_raw_damage"},
                        ),
                        _set("_wall_layer_debt", "max(0, -_wall_layer_amount)"),
                        _set("_wall_layer_amount", "max(0, _wall_layer_amount)"),
                    ],
                ),
            ],
        ),
        _call(
            "resolve_srd_prismatic_wall_layer_target",
            {
                "spell": "prismatic_wall",
                "target": "{_target_state_id}",
                "caster_state": "{_caster_state_id}",
                "source_state_id": "{_caster_state_id}",
                "save_ability": "dexterity",
                "spell_save_dc": "{_wall_record.save_dc}",
                "damage_total": "{_wall_layer_amount}",
                "damage_type": "fire",
                "success_damage": "half",
                "construct_save_disadvantage": False,
                "half_cover_targets": [],
                "three_quarters_cover_targets": [],
                "witness_state_ids": [],
                "rays": "{_wall_selected_rays}",
                "ray_history": [],
                "ray_labels": "{_wall_selected_labels}",
                "wall_source_id": "{source_id}",
                "wall_unallocated_roll_penalty": "{_wall_layer_debt}",
            },
            ("_spell_save", "_spell_save_outcome", "_spell_damage_amount", "_spell_detail_rows"),
        ),
        _push(
            "_wall_contact_receipts",
            {
                "layer": "_wall_layer",
                "color": "_wall_color",
                "raw_damage": "_wall_layer_roll",
                "save": "_spell_save",
                "outcome": "_spell_save_outcome",
                "damage": "_spell_damage_amount",
            },
        ),
    ]
    return _tool(
        "Resolve actual reaching or physical passage through all remaining genuine Wall layers.",
        [_entity("target_state"), _entity("caster_state"), _parameter("source_id")],
        [
            *_read_source(),
            _require(_source_current("_wall_record"), "The contacted Wall must be current."),
            _require(
                "_wall_record.caster_state_id == _caster_state_id",
                "Use the Wall's actual saved caster.",
            ),
            _set("_wall_contact_receipts", []),
            _when(
                "_target_state_id != _caster_state_id and "
                "_target_state_id not in _wall_record.designated_creatures",
                [
                    _set("_wall_layer", 0),
                    _set("_wall_colors", ["'" + color + "'" for color in COLORS]),
                    _each(
                        "_wall_colors",
                        "_wall_color",
                        [
                            _set("_wall_layer", "_wall_layer + 1"),
                            _when("_wall_layer >= _wall_record.outer_layer", layer),
                        ],
                    ),
                ],
            ),
            _set(
                "target_state.last_prismatic_wall_contact",
                {
                    "source_id": "source_id",
                    "source_serial": "_wall_record.serial",
                    "layers": "_wall_contact_receipts",
                },
            ),
        ],
    )


def _near_tool():
    return _tool(
        "Resolve actual seeing within20feet after movement or this creature's real starting turn.",
        [
            _entity("target_state"),
            _parameter("source_id"),
            _optional("starting_turn", "boolean", False, model_hidden=True),
        ],
        [
            *_read_source(),
            *_scene("_target_state_id", "_near_wall_target"),
            _when(
                "starting_turn == true",
                [
                    _require(
                        "combat.turn_active == true and combat.active_actor == _target_state_id",
                        "Use this creature's actual starting turn.",
                    ),
                ],
            ),
            _set("_wall_near_already_resolved", False),
            _when(
                "starting_turn == true and target_state.prismatic_wall_start_receipts != null "
                "and source_id in target_state.prismatic_wall_start_receipts",
                [
                    _set(
                        "_wall_start_receipt",
                        "target_state.prismatic_wall_start_receipts[source_id]",
                    ),
                    _set(
                        "_wall_near_already_resolved",
                        "_wall_start_receipt.serial == _wall_record.serial and "
                        "_wall_start_receipt.turn == target_state.turns_started",
                    ),
                ],
            ),
            _when(
                _source_current("_wall_record")
                + " and _near_wall_target.metadata.plane == _wall_record.plane and "
                "_target_state_id != _wall_record.caster_state_id and "
                "_target_state_id not in _wall_record.designated_creatures and "
                "_wall_near_already_resolved != true",
                [
                    *_distance(
                        "_near_wall_target.position", "_wall_record.volume", "_near_wall_distance"
                    ),
                    _when(
                        "_near_wall_distance <= 20",
                        [
                            _call(
                                "resolve_srd_illumination_point_sight",
                                {
                                    "observer_state": "{_target_state_id}",
                                    "point": "{_wall_nearest}",
                                    "perception_plane": "{_wall_record.plane}",
                                },
                                ("_illumination_point_can_see",),
                            ),
                            _when(
                                "_illumination_point_can_see == true",
                                [
                                    _call(
                                        "d20_test",
                                        {
                                            "actor_state": "{_target_state_id}",
                                            "ability": "constitution",
                                            "kind": "saving_throw",
                                            "ability_modifier": (
                                                "{target_state.saving_throw_modifiers.constitution}"
                                            ),
                                            "proficiency_bonus": 0,
                                            "proficiency_factor": "none",
                                            "circumstantial_modifier": 0,
                                            "target_number": "{_wall_record.save_dc}",
                                            "advantage": False,
                                            "disadvantage": False,
                                            "forego_save": False,
                                        },
                                        {
                                            "_wall_near_save": "_d20_test",
                                            "_wall_near_outcome": "_outcome",
                                        },
                                    ),
                                    _when(
                                        "_wall_near_outcome == 'failure'",
                                        [
                                            _call(
                                                "install_srd_timed_condition_source",
                                                {
                                                    "target_state": "{_target_state_id}",
                                                    "caster_state": (
                                                        "{_wall_record.caster_state_id}"
                                                    ),
                                                    "lasting_state": "state",
                                                    "spell": "prismatic_wall",
                                                    "condition": "blinded",
                                                    "save_dc": "{_wall_record.save_dc}",
                                                    "duration_rounds": 10,
                                                    "repeat_save_timing": "none",
                                                    "save_ability": "constitution",
                                                    "concentration_sequence": 0,
                                                    "actual_spell_level": (
                                                        "{_wall_record.spell_level}"
                                                    ),
                                                },
                                                {
                                                    "_wall_near_condition_id": (
                                                        "_timed_condition_source_id"
                                                    )
                                                },
                                            ),
                                            *[
                                                _set(
                                                    "state.timed_condition_sources."
                                                    "{_wall_near_condition_id}." + field,
                                                    value,
                                                )
                                                for field, value in {
                                                    "dispel_ongoing": False,
                                                    "ongoing_duration": False,
                                                    "prismatic_wall_source_id": "source_id",
                                                    "prismatic_wall_source_serial": (
                                                        "_wall_record.serial"
                                                    ),
                                                }.items()
                                            ],
                                        ],
                                    ),
                                    _set(
                                        "target_state.last_prismatic_wall_near",
                                        {
                                            "source_id": "source_id",
                                            "serial": "_wall_record.serial",
                                            "save": "_wall_near_save",
                                            "outcome": "_wall_near_outcome",
                                        },
                                    ),
                                    _when(
                                        "starting_turn == true",
                                        [
                                            _when(
                                                "target_state.prismatic_wall_start_receipts "
                                                "== null",
                                                [
                                                    _set(
                                                        "target_state.prismatic_wall_start_receipts",
                                                        {},
                                                    )
                                                ],
                                            ),
                                            _set(
                                                "target_state.prismatic_wall_start_receipts.{source_id}",
                                                {
                                                    "serial": "_wall_record.serial",
                                                    "turn": "target_state.turns_started",
                                                },
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


def _reach_tool():
    return _tool(
        "Attempt a real reach into a selected current Wall within5feet of the actual body.",
        [_entity("target_state"), _parameter("source_id")],
        [
            *_read_source(),
            *_scene("_target_state_id", "_reaching_wall_target"),
            _require(
                _source_current("_wall_record")
                + " and _reaching_wall_target.metadata.plane == _wall_record.plane",
                "Reach a current Wall on the creature's actual plane.",
            ),
            *_distance(
                "_reaching_wall_target.position", "_wall_record.volume", "_wall_reach_distance"
            ),
            _require("_wall_reach_distance <= 5", "Reaching requires an actual Wall within5feet."),
            _call(
                "resolve_srd_prismatic_wall_contact",
                {
                    "target_state": "{_target_state_id}",
                    "caster_state": "{_wall_record.caster_state_id}",
                    "source_id": "{source_id}",
                },
            ),
        ],
    )


def _union_prismatic_classifier(value):
    if isinstance(value, str):
        return re.sub(
            r"([A-Za-z_][A-Za-z_0-9]*\.spell_id) == 'prismatic_spray'",
            r"(\1 == 'prismatic_spray' or \1 == 'prismatic_wall')",
            value,
        )
    if isinstance(value, list):
        return [_union_prismatic_classifier(item) for item in value]
    if isinstance(value, dict):
        return {key: _union_prismatic_classifier(item) for key, item in value.items()}
    return value


def extend_runtime(text):
    """Emit exact Wall owners; final shared action, attack and information joins remain separate."""
    original = _load_runtime(text)
    tools = original["tools"]
    changed = {
        **_source_tools(),
        **_query_tools(),
        **_consequence_tools(tools),
        **_movement_queue_tools(),
        "cast_srd_prismatic_wall": _cast_tool(),
        "resolve_srd_prismatic_wall_contact": _contact_tool(),
        "resolve_srd_prismatic_wall_near": _near_tool(),
        "reach_into_srd_prismatic_wall": _reach_tool(),
        "resolve_srd_prismatic_wall_body_segment": _body_segment_tool(),
        "observe_srd_prismatic_wall_relocation": _movement_observer_tool(),
        "cast_spell": _cast_dispatch(tools),
    }
    clock = deepcopy(tools["advance_srd_lasting_clock"])
    if "refresh_srd_prismatic_walls" not in str(clock["effects"]):
        clock["effects"].append(
            _when(
                "lasting_state.has_prismatic_walls == true",
                [_call("refresh_srd_prismatic_walls", {})],
            )
        )
    changed["advance_srd_lasting_clock"] = clock
    for name in (
        "refresh_srd_timed_condition_target",
        "resolve_srd_prismatic_indigo_end_turn",
        "resolve_srd_prismatic_violet_start_turn",
    ):
        tool = deepcopy(tools[name])

        # Rebuild the classifier union, avoiding nested copies on repeat emission.
        def normalize(value):
            if isinstance(value, str):
                return re.sub(
                    r"\(([A-Za-z_][A-Za-z_0-9]*\.spell_id) == 'prismatic_spray' "
                    r"or \1 == 'prismatic_wall'\)",
                    r"\1 == 'prismatic_spray'",
                    value,
                )
            if isinstance(value, dict):
                return {key: normalize(item) for key, item in value.items()}
            if isinstance(value, list):
                return [normalize(item) for item in value]
            return value

        tool["effects"] = _union_prismatic_classifier(normalize(tool["effects"]))
        changed[name] = tool
    text = _replace_tools(text, changed)
    text = _replace_tools(text, {name: tool for name, tool in changed.items() if name not in tools})
    props = {
        key: {
            "type": "boolean"
            if type(value) is bool
            else "integer"
            if type(value) is int
            else "string"
        }
        for key, value in PROFILES["prismatic_wall"].items()
        if key != "family"
    }
    schema = yaml.safe_dump(
        {
            "prismatic_wall": {
                "type": "object",
                "properties": props,
                "required": list(props),
                "additionalProperties": False,
            }
        },
        sort_keys=False,
    )
    schema = "".join("      " + line for line in schema.splitlines(True))
    if "      prismatic_wall:" in text:
        text = re.sub(
            r"(?ms)^      prismatic_wall:\n.*?(?=^      [a-z])",
            lambda _: schema + "\n",
            text,
            count=1,
        )
    else:
        first = next(iter(original["definition_row_schemas"]["definitions.spells"]["variants"]))
        anchor = f"      {first}:"
        if text.count(anchor) != 1:
            raise ValueError("Prismatic Wall requires the actual unique spell variant anchor.")
        text = text.replace(anchor, schema + "\n" + anchor, 1)
    return text


def bind_runtime_consumers(text):
    """Bind genuine committed movement and turn starts after final shared owner composition."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    for name, subject, teleport in (
        (
            "move_srd_travel_subject",
            "{subject}",
            "{teleport}",
        ),
        (
            "move_srd_scene_creature_environment_segment",
            "{_actor_state_id}",
            False,
        ),
    ):
        owner = deepcopy(tools[name])
        if "queue_srd_prismatic_wall_relocation" not in str(owner["effects"]):
            owner["effects"].insert(
                0,
                _when(
                    "state.has_prismatic_walls == true",
                    [
                        *_scene(subject[1:-1], "_wall_relocation_origin_body"),
                        _set(
                            "_wall_relocation_origin",
                            [f"_wall_relocation_origin_body.position[{axis}]" for axis in range(3)],
                        ),
                        _set(
                            "_wall_relocation_plane", "_wall_relocation_origin_body.metadata.plane"
                        ),
                    ],
                ),
            )
            owner["effects"].append(
                _when(
                    "state.has_prismatic_walls == true",
                    [
                        _call(
                            "queue_srd_prismatic_wall_relocation",
                            {
                                "subject": subject,
                                "start": "{_wall_relocation_origin}",
                                "end": "{destination}",
                                "origin_plane": "{_wall_relocation_plane}",
                                "teleport": teleport,
                            },
                        ),
                    ],
                )
            )
        if name == "move_srd_scene_creature_environment_segment" and (
            "settle_srd_prismatic_wall_movements" not in str(owner["effects"])
        ):
            owner["effects"].append(
                _when(
                    "state.has_prismatic_walls == true",
                    [_call("settle_srd_prismatic_wall_movements", {})],
                )
            )
        changed[name] = owner
    begin = deepcopy(tools["begin_combat_turn"])
    if "resolve_srd_prismatic_wall_near" not in str(begin["effects"]):
        begin["effects"].append(
            _when(
                "state.has_prismatic_walls == true",
                _sources(
                    [
                        _call(
                            "resolve_srd_prismatic_wall_near",
                            {
                                "target_state": "{_actor_id}",
                                "source_id": "{_wall_id}",
                                "starting_turn": True,
                            },
                        )
                    ]
                ),
            )
        )
    changed["begin_combat_turn"] = begin
    return _replace_tools(text, changed)
