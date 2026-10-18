"""Floating Disk retains its printed circle, actual cargo and the sole scene position."""

from copy import deepcopy

if __package__:
    from .srd_form_descent import _ground_query
    from .srd_scene_relations import POSITION_EXPORTS, _array, _snapshot, _spatial, _tool_scene
    from .srd_spell_object_content import _lookup_object, _metadata_patch, _new_scene_object, _now
    from .srd_spell_object_utilities import SOURCE_PAGES, _fact_guard, _read_source, _serial
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from .srd_timed_senses import _loop, _push, _replace_tools
else:
    from srd_form_descent import _ground_query
    from srd_scene_relations import POSITION_EXPORTS, _array, _snapshot, _spatial, _tool_scene
    from srd_spell_object_content import _lookup_object, _metadata_patch, _new_scene_object, _now
    from srd_spell_object_utilities import SOURCE_PAGES, _fact_guard, _read_source, _serial
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from srd_timed_senses import _loop, _push, _replace_tools


PUBLIC_TOOLS = {"load_srd_floating_disk", "unload_srd_floating_disk"}


def _scene(subject, local):
    return [
        _call("srd_information_scene_entity", {"subject": "{" + subject + "}"}, POSITION_EXPORTS),
        _set(local, "_info_scene_entity"),
    ]


def _move(subject, destination, plane):
    return _call(
        "move_srd_travel_subject_node",
        {
            "subject": "{" + subject + "}",
            "destination": "{" + destination + "}",
            "plane": "{" + plane + "}",
            "teleport": False,
        },
    )


def _source():
    """Read only the current actual duration-bearing disk and its admitted circle."""
    return [
        *_read_source("floating_disk"),
        _require(
            f"_utility_record.source_sha256 == {SOURCE_PAGES[133]!r} and "
            "_utility_record.caster == _caster_state_id and _utility_record.spell_level >= 1",
            "Use this actual caster's pinned Floating Disk source.",
        ),
        *_scene("_utility_record.force_id", "_disk_scene"),
        _require(
            "_disk_scene.metadata.srd_object_force.source_id == source_id and "
            "_disk_scene.footprint.kind == 'circle' and _disk_scene.footprint.radius == 1.5 and "
            "abs(_disk_scene.height - 12 ** -1) < 0.000001 and "
            "_disk_scene.metadata.participant_ref == null",
            "Use the actual three-foot scene-only disk with its printed one-inch thickness.",
        ),
    ]


def _save():
    return [
        _set("_disk_kept", []),
        _loop(
            "_utility_records",
            "_disk_candidate",
            [
                _when(
                    "_disk_candidate.kind != 'object_utility_spell' or "
                    "_disk_candidate.source_id != source_id",
                    [_push("_disk_kept", "_disk_candidate")],
                ),
            ],
        ),
        _push("_disk_kept", "_utility_record"),
        _set("state.turn_effects", "_disk_kept"),
    ]


def _ground(point, plane):
    """Reuse the admitted downward ground owner, selecting its actual flat support row."""

    def bind_plane(value):
        if isinstance(value, str):
            return value.replace("_info_scene_entity.metadata.plane", "_disk_ground_plane")
        if isinstance(value, list):
            return [bind_plane(item) for item in value]
        if isinstance(value, dict):
            return {key: bind_plane(item) for key, item in value.items()}
        return value

    return [
        _set("_info_position", point),
        _set("_disk_ground_plane", plane),
        *bind_plane(_ground_query()),
        _set("_disk_floor", None),
        _loop(
            "_wind_ground_hits",
            "_disk_ground_hit",
            [
                _when(
                    "_disk_ground_hit.normal[2] > 0 and "
                    "abs(_disk_ground_hit.point[2] - _wind_ground_height) < 0.000001",
                    [
                        _loop(
                            "_wind_ground_geometry",
                            "_disk_ground",
                            [
                                _when(
                                    "_disk_ground.id == _disk_ground_hit.id and "
                                    "_disk_ground.active == true and "
                                    "_disk_ground.movement == 'blocking' and "
                                    "_disk_ground.metadata.ground == true and "
                                    f"_disk_ground.metadata.plane == {plane}",
                                    [
                                        _set("_disk_floor", "_disk_ground"),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        _require(
            "_disk_floor != null and (_disk_floor.volume.kind == 'box' or "
            "_disk_floor.volume.kind == 'cylinder')",
            "Floating Disk currently needs an actual flat box or cylinder ground surface.",
        ),
    ]


def _enclosure(start, end):
    """Both endpoint disks inside one convex flat floor prove whole straight-route support."""
    return [
        _set(
            "_disk_path_min",
            [f"min({start}[0], {end}[0]) - 1.5", f"min({start}[1], {end}[1]) - 1.5", f"{start}[2]"],
        ),
        _set(
            "_disk_path_max",
            [
                f"max({start}[0], {end}[0]) + 1.5",
                f"max({start}[1], {end}[1]) + 1.5",
                f"{start}[2] + 12 ** -1",
            ],
        ),
        *[
            _require(
                f"_disk_path_min[{i}] >= state.spatial_scene.map.bounds.min[{i}] and "
                f"_disk_path_max[{i}] <= state.spatial_scene.map.bounds.max[{i}]",
                "The actual whole disk must stay inside the admitted map.",
            )
            for i in range(3)
        ],
        _when(
            "_disk_floor.volume.kind == 'box'",
            [
                _require(
                    f"_disk_path_min[{i}] >= _disk_floor.volume.min[{i}] and "
                    f"_disk_path_max[{i}] <= _disk_floor.volume.max[{i}]",
                    "The whole disk route requires actual flat ground support.",
                )
                for i in range(2)
            ],
        ),
        _when(
            "_disk_floor.volume.kind == 'cylinder'",
            [
                _require(
                    "_disk_floor.volume.radius >= 1.5 and "
                    f"({point}[0] - _disk_floor.volume.center[0]) ** 2 + "
                    f"({point}[1] - _disk_floor.volume.center[1]) ** 2 <= "
                    "(_disk_floor.volume.radius - 1.5) ** 2",
                    "The whole disk route requires actual flat ground support.",
                )
                for point in (start, end)
            ],
        ),
    ]


def _cast():
    create = _new_scene_object(
        {
            "plane": "_world_plane",
            "lighting": "_world_lighting",
            "srd_object_force": {
                "spell": "'floating_disk'",
                "source_id": "_utility_source_id",
                "caster": "_caster_state_id",
                "source_sha256": repr(SOURCE_PAGES[133]),
            },
        },
        identity="output_id",
        extent="_disk_extent",
    )
    next(
        e["set"]["value"]
        for e in create
        if e.get("set", {}).get("field") == "_world_created_entity"
    )["footprint"] = {
        "kind": "'circle'",
        "radius": 1.5,
    }
    return _tool_scene(
        "Create the source-owned circular disk after ordinary paid spell casting.",
        [
            _entity("caster_state"),
            _parameter("spell", "enum", enum_values=["floating_disk"]),
            _parameter("slot_level", "integer", minimum=1, maximum=9),
            _parameter("output_id"),
            _array("point"),
        ],
        [
            *_now(),
            _call(
                "resolve_srd_object_content_point",
                {"caster_state": "{_caster_state_id}", "point": "{point}", "range_feet": 30},
                ["_world_origin", "_world_plane", "_world_lighting"],
            ),
            _call(
                "resolve_srd_illumination_point_sight",
                {"observer_state": "{_caster_state_id}", "point": "{point}"},
                ["_illumination_point_can_see"],
            ),
            _require(
                "_illumination_point_can_see == true",
                "Floating Disk needs a point you actually see.",
            ),
            *_ground("point", "_world_plane"),
            _require(
                "abs(point[2] - _wind_ground_height - 3) < 0.000001",
                "The disk floats exactly three feet above actual ground.",
            ),
            *_enclosure("point", "point"),
            *_snapshot(),
            _spatial(
                "area",
                {
                    "map_id": "{state.spatial_scene.map.id}",
                    "shape": {
                        "kind": "cylinder",
                        "center": "{point}",
                        "radius": 1.5,
                        "height": 1 / 12,
                    },
                    "include": ["geometry", "entities"],
                    "max_results": 1000,
                },
                "_disk_placement",
            ),
            _require(
                "_disk_placement.truncated != true and len(_disk_placement.results) == 0",
                "The actual circular disk needs unoccupied space.",
            ),
            *_serial(),
            _set("_disk_extent", [3, 3, 1 / 12]),
            *create,
            _set(
                "_utility_record",
                {
                    "kind": "'object_utility_spell'",
                    "spell": "'floating_disk'",
                    "spell_level": "slot_level",
                    "source_id": "_utility_source_id",
                    "source_sha256": repr(SOURCE_PAGES[133]),
                    "caster": "_caster_state_id",
                    "target": "_caster_state_id",
                    "owner": "_caster_state_id",
                    "phase": "'never'",
                    "turn": 0,
                    "active": True,
                    "started_tick": "_object_now",
                    "expires_tick": "_object_now + 1200",
                    "dispel_ongoing": True,
                    "force_id": "output_id",
                    "cargo_ids": [],
                    "floor_id": "_disk_floor.id",
                },
            ),
            _set("_utility_records", []),
            _when("state.turn_effects != null", [_set("_utility_records", "state.turn_effects")]),
            _push("_utility_records", "_utility_record"),
            _set("state.turn_effects", "_utility_records"),
            _set("state.has_floating_disk_sources", True),
            _set("_utility_summary", [{"label": "'Disk'", "value": "output_id"}]),
            _set(
                "_utility_details",
                [
                    {"label": "'Capacity (lb)'", "value": 500},
                    {"label": "'Ground clearance (ft)'", "value": 3},
                ],
            ),
        ],
    )


def _contact():
    """Loading is an actual object interaction in reach, not a numeric load verdict."""
    return [
        *_read_source("floating_disk"),
        _call(
            "qualify_srd_floating_disk_source",
            {"caster_state": "{_utility_record.caster}", "source_id": "{source_id}"},
            ["_disk_scene", "_utility_record"],
        ),
        _require(
            "combat.pending_action == null",
            "Finish the actual pending action before disk interaction.",
        ),
        _require(
            "(combat.encounter_active == true and interaction_mode == 'utilize') or "
            "(combat.encounter_active != true and interaction_mode == 'exploration')",
            "Select the supported Utilize Action in combat or ordinary exploration interaction.",
        ),
        _when(
            "combat.encounter_active == true",
            [
                _require(
                    "combat.turn_active == true and combat.active_actor == _actor_state_id",
                    "Only the actual active actor may use this turn's object interaction.",
                ),
            ],
        ),
        *_scene("_actor_state_id", "_disk_actor"),
        _require(
            "_disk_actor.metadata.plane == _disk_scene.metadata.plane",
            "Reach the actual disk on the same plane.",
        ),
        _call(
            "measure_srd_information_distance",
            {"start": "{_disk_actor.position}", "end": "{_disk_scene.position}"},
            ["_scene_distance"],
        ),
        _require("_scene_distance <= 5", "The actor must actually reach the disk."),
        _call(
            "srd_information_barriers",
            {"start": "{_disk_actor.position}", "end": "{_disk_scene.position}", "rule": "solid"},
            ["_information_blocked"],
        ),
        _require(
            "_information_blocked != true", "An actual solid barrier blocks disk interaction."
        ),
    ]


def _interaction_parameter():
    return _parameter(
        "interaction_mode",
        "enum",
        required=False,
        default="exploration",
        enum_values=["exploration", "utilize"],
    )


def _spend_interaction():
    """Pay the selected ordinary Utilize Action only after actual cargo admission."""
    return _when(
        "combat.encounter_active == true",
        [_call("spend_srd_action", {"magic_action": False, "control_action_kind": "utilize"})],
    )


def _inventory(remove):
    """Keep native inventory/held identities aligned with the one actual scenery object."""
    effects = []
    for field in ("inventory", "held_items"):
        effects += [
            _set("_disk_items", []),
            _when(f"actor_state.{field} != null", [_set("_disk_items", f"actor_state.{field}")]),
            _set("_disk_kept_items", []),
            _loop(
                "_disk_items",
                "_disk_item",
                [
                    _when(
                        "_disk_item != _utility_facts.inventory_item",
                        [_push("_disk_kept_items", "_disk_item")],
                    ),
                ],
            ),
        ]
        if not remove:
            effects += [
                _when(
                    "_utility_facts.inventory_item != ''",
                    [_push("_disk_kept_items", "_utility_facts.inventory_item")],
                )
            ]
        effects += [_set(f"actor_state.{field}", "_disk_kept_items")]
    return effects


def _cargo_sweep(subject, destination, require_clear=True):
    """Qualify the actual cargo's full body before any supported route mutation."""
    return [
        *_snapshot(),
        _set("_disk_material", "_disk_scene.metadata.plane == 'material'"),
        _spatial(
            "sweep",
            {
                "map_id": "{state.spatial_scene.map.id}",
                "moving_entity_id": "{" + subject + "}",
                "destination": "{" + destination + "}",
                "include_entities": False,
                "metadata_filter": {
                    "key": "plane",
                    "value": "{_disk_scene.metadata.plane}",
                    "missing_matches": "{_disk_material}",
                },
            },
            "_disk_cargo_sweep",
        ),
        _require(
            "_disk_cargo_sweep.supported == true"
            + (" and _disk_cargo_sweep.clear == true" if require_clear else ""),
            "Disk cargo needs an actual supported clear full-body route.",
        ),
    ]


def _cargo_support():
    """Keep whole-footprint support on the cargo's actual admitted body."""
    return [
        _when(
            "_object_scene.footprint.kind == 'box'",
            [
                *[
                    _require(
                        f"(position[0] - _disk_scene.position[0] {x} "
                        "_object_scene.footprint.half_extents[0]) ** 2 + "
                        f"(position[1] - _disk_scene.position[1] {y} "
                        "_object_scene.footprint.half_extents[1]) ** 2 <= 2.25",
                        "The actual cargo requires whole-footprint support on the circle.",
                    )
                    for x in ("+", "-")
                    for y in ("+", "-")
                ],
                _set(
                    "_disk_load_min",
                    [
                        "position[0] - _object_scene.footprint.half_extents[0]",
                        "position[1] - _object_scene.footprint.half_extents[1]",
                        "position[2]",
                    ],
                ),
                _set(
                    "_disk_load_max",
                    [
                        "position[0] + _object_scene.footprint.half_extents[0]",
                        "position[1] + _object_scene.footprint.half_extents[1]",
                        "position[2] + _object_scene.height",
                    ],
                ),
            ],
        ),
        _when(
            "_object_scene.footprint.kind == 'circle'",
            [
                _require(
                    "_object_scene.footprint.radius <= 1.5 and "
                    "(position[0] - _disk_scene.position[0]) ** 2 + "
                    "(position[1] - _disk_scene.position[1]) ** 2 <= "
                    "(1.5 - _object_scene.footprint.radius) ** 2",
                    "The actual cargo requires whole-footprint support on the circle.",
                ),
            ],
        ),
    ]


def _cargo_loading_queries():
    """Use each supported literal shape with dimensions from the actual object."""
    return [
        _when(
            f"_object_scene.footprint.kind == '{kind}'",
            [
                _spatial(
                    "area",
                    {
                        "map_id": "{state.spatial_scene.map.id}",
                        "shape": shape,
                        "include": ["geometry", "entities"],
                        "max_results": 1000,
                    },
                    "_disk_loading",
                )
            ],
        )
        for kind, shape in (
            ("box", {"kind": "box", "min": "{_disk_load_min}", "max": "{_disk_load_max}"}),
            (
                "circle",
                {
                    "kind": "cylinder",
                    "center": "{position}",
                    "radius": "{_object_scene.footprint.radius}",
                    "height": "{_object_scene.height}",
                },
            ),
        )
    ]


def _cargo_tools():
    load = _tool_scene(
        "Place one actual mass-qualified object on the real source disk.",
        [
            _entity("actor_state"),
            _parameter("source_id"),
            _parameter("object_id"),
            _array("position"),
            _interaction_parameter(),
        ],
        [
            *_contact(),
            *_lookup_object(),
            *_fact_guard(),
            _require(
                "object_id not in _utility_record.cargo_ids and "
                "_object_scene.metadata.plane == _disk_scene.metadata.plane and "
                "_object_scene.metadata.worn_by == null and "
                "(_object_scene.metadata.carried_by == null or "
                "_object_scene.metadata.carried_by == _actor_state_id) and "
                "_object_scene.metadata.srd_spell_carrier == null and "
                "_object_scene.metadata.srd_object_force == null and "
                "len(_utility_record.cargo_ids) < 64",
                "Load an actual reachable object once, without stealing another holder's object.",
            ),
            _call(
                "measure_srd_information_distance",
                {"start": "{_disk_actor.position}", "end": "{_object_scene.position}"},
                ["_scene_distance"],
            ),
            _require("_scene_distance <= 5", "Reach the actual object to load it."),
            _call(
                "srd_information_barriers",
                {
                    "start": "{_disk_actor.position}",
                    "end": "{_object_scene.position}",
                    "rule": "solid",
                },
                ["_information_blocked"],
            ),
            _require("_information_blocked != true", "An actual solid barrier blocks the cargo."),
            _require(
                "(_object_scene.footprint.kind == 'box' or "
                "_object_scene.footprint.kind == 'circle') and "
                "abs(position[2] - _disk_scene.position[2] - 12 ** -1) < 0.000001",
                "Loading supports actual box or circular cargo on the actual disk surface.",
            ),
            *_cargo_support(),
            _set("_disk_destination", "position"),
            *_cargo_sweep("object_id", "_disk_destination"),
            *_snapshot(),
            *_cargo_loading_queries(),
            _require(
                "_disk_loading.truncated != true", "Actual loading exceeds the geometry bound."
            ),
            _set("_disk_loading_rows", "_disk_loading.results"),
            _loop(
                "_disk_loading_rows",
                "_disk_loading_row",
                [
                    _require(
                        "_disk_loading_row.metadata.plane != _disk_scene.metadata.plane or "
                        "_disk_loading_row.id == object_id or "
                        "_disk_loading_row.id == _utility_record.force_id",
                        "Actual cargo placement overlaps an occupied space.",
                    ),
                ],
            ),
            _spend_interaction(),
            *_inventory(True),
            *_metadata_patch(
                {
                    "carried_by": None,
                    "worn_by": None,
                    "srd_spell_carrier": {
                        "source_id": "source_id",
                        "force_id": "_utility_record.force_id",
                        "spell": "'floating_disk'",
                    },
                }
            ),
            _set("state.disk_relocating", True),
            _move("object_id", "_disk_destination", "_disk_scene.metadata.plane"),
            _set("state.disk_relocating", False),
            _set("_disk_cargo_ids", "_utility_record.cargo_ids"),
            _push("_disk_cargo_ids", "object_id"),
            _set("_disk_source_change", {"cargo_ids": "_disk_cargo_ids"}),
            _set("_utility_record", "merge(_utility_record, _disk_source_change)"),
            *_save(),
            _call(
                "refresh_srd_floating_disk",
                {"caster_state": "{_utility_record.caster}", "source_id": "{source_id}"},
            ),
        ],
    )
    unload = _tool_scene(
        "Retrieve only this disk's actual cargo into the reaching actor's native inventory.",
        [
            _entity("actor_state"),
            _parameter("source_id"),
            _parameter("object_id"),
            _interaction_parameter(),
        ],
        [
            *_contact(),
            *_lookup_object(),
            *_fact_guard(),
            _require(
                "object_id in _utility_record.cargo_ids and "
                "_object_scene.metadata.srd_spell_carrier.source_id == source_id",
                "Retrieve only this actual source's saved cargo.",
            ),
            *_cargo_sweep("object_id", "_disk_actor.position"),
            _spend_interaction(),
            *_metadata_patch(
                {"srd_spell_carrier": None, "carried_by": "_actor_state_id", "worn_by": None}
            ),
            *_inventory(False),
            _set("state.disk_relocating", True),
            _move("object_id", "_disk_actor.position", "_disk_actor.metadata.plane"),
            _set("state.disk_relocating", False),
            _set("_disk_cargo_ids", []),
            _set("_disk_prior_cargo", "_utility_record.cargo_ids"),
            _loop(
                "_disk_prior_cargo",
                "_disk_cargo_id",
                [
                    _when(
                        "_disk_cargo_id != object_id", [_push("_disk_cargo_ids", "_disk_cargo_id")]
                    ),
                ],
            ),
            _set("_disk_source_change", {"cargo_ids": "_disk_cargo_ids"}),
            _set("_utility_record", "merge(_utility_record, _disk_source_change)"),
            *_save(),
        ],
    )
    return {"load_srd_floating_disk": load, "unload_srd_floating_disk": unload}


def _lifecycle_tools():
    clear = _tool_scene(
        "End the actual disk and drop its exact saved cargo onto qualified ground.",
        [
            _entity("caster_state"),
            _parameter("source_id"),
        ],
        [
            *_source(),
            _set("_disk_cargo", "_utility_record.cargo_ids"),
            _set("state.disk_relocating", True),
            _loop(
                "_disk_cargo",
                "_disk_cargo_id",
                [
                    _call(
                        "drop_srd_floating_disk_cargo",
                        {"object_id": "{_disk_cargo_id}", "source_id": "{source_id}"},
                    ),
                ],
            ),
            _set("state.disk_relocating", False),
            _call(
                "deactivate_srd_floating_disk",
                {"object_id": "{_utility_record.force_id}", "source_id": "{source_id}"},
            ),
        ],
    )
    drop = _tool_scene(
        "Release only this source's actual cargo and use the existing ground height owner.",
        [
            _parameter("object_id"),
            _parameter("source_id"),
        ],
        [
            *_lookup_object(),
            _require(
                "_object_scene.metadata.srd_spell_carrier.source_id == source_id",
                "Drop only this actual disk's cargo.",
            ),
            *_ground("_object_scene.position", "_object_scene.metadata.plane"),
            _set(
                "_disk_destination",
                ["_object_scene.position[0]", "_object_scene.position[1]", "_wind_ground_height"],
            ),
            *_metadata_patch({"srd_spell_carrier": None}),
            _move("object_id", "_disk_destination", "_object_scene.metadata.plane"),
        ],
    )
    deactivate = _tool_scene(
        "Vanish the exact source-owned circular scenery without creature consequences.",
        [
            _parameter("object_id"),
            _parameter("source_id"),
        ],
        [
            *_lookup_object(),
            _require(
                "_object_scene.metadata.srd_object_force.source_id == source_id",
                "Vanish only this actual disk.",
            ),
            *_metadata_patch({}, active=False),
        ],
    )
    refresh = _tool_scene(
        "Refresh printed weight, distance, deadline and supported flat follow from real sources.",
        [
            _entity("caster_state"),
            _parameter("source_id"),
        ],
        [
            *_source(),
            *_scene("_caster_state_id", "_disk_caster"),
            _call(
                "measure_srd_information_distance",
                {"start": "{_disk_scene.position}", "end": "{_disk_caster.position}"},
                ["_scene_distance"],
            ),
            _set("_disk_distance", "_scene_distance"),
            _set("_disk_weight", 0),
            _set("_disk_cargo", "_utility_record.cargo_ids"),
            _loop(
                "_disk_cargo",
                "_disk_cargo_id",
                [
                    _call(
                        "read_srd_floating_disk_cargo_weight",
                        {"object_id": "{_disk_cargo_id}", "source_id": "{source_id}"},
                        ["_disk_cargo_weight"],
                    ),
                    _set("_disk_weight", "_disk_weight + _disk_cargo_weight"),
                ],
            ),
            {
                "condition": "_object_now >= _utility_record.expires_tick or _disk_weight > 500 or "
                "_disk_distance > 100 or _disk_caster.metadata.plane != _disk_scene.metadata.plane",
                "on_true": [
                    _call(
                        "retire_srd_object_utility_source",
                        {"caster_state": "{_caster_state_id}", "source_id": "{source_id}"},
                    )
                ],
                "on_false": [
                    _when(
                        "_disk_distance > 20",
                        [
                            _call(
                                "follow_srd_floating_disk",
                                {"caster_state": "{_caster_state_id}", "source_id": "{source_id}"},
                            ),
                        ],
                    )
                ],
            },
        ],
    )
    all_sources = _tool_scene(
        "Refresh the actual saved disk pool without invented position or payload state.",
        [],
        [
            _set("_disk_pending", []),
            _when("state.turn_effects != null", [_set("_disk_pending", "state.turn_effects")]),
            _loop(
                "_disk_pending",
                "_disk_pending_source",
                [
                    _when(
                        "_disk_pending_source.kind == 'object_utility_spell' and "
                        "_disk_pending_source.spell == 'floating_disk'",
                        [
                            _call(
                                "refresh_srd_floating_disk",
                                {
                                    "caster_state": "{_disk_pending_source.caster}",
                                    "source_id": "{_disk_pending_source.source_id}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    weight = _tool_scene(
        "Read actual immutable object mass from this source's exact current cargo identity.",
        [
            _parameter("object_id"),
            _parameter("source_id"),
        ],
        [
            *_lookup_object(),
            *_fact_guard(),
            _require(
                "_object_scene.metadata.srd_spell_carrier.source_id == source_id",
                "The actual cargo must still belong to this disk.",
            ),
            _set("_disk_cargo_weight", "_utility_facts.weight_pounds"),
        ],
    )
    return {
        "qualify_srd_floating_disk_source": _tool_scene(
            "Qualify this exact admitted source disk.",
            [_entity("caster_state"), _parameter("source_id")],
            [
                *_source(),
                _require(
                    "_object_now < _utility_record.expires_tick", "The actual disk has expired."
                ),
            ],
        ),
        "clear_srd_floating_disk": clear,
        "drop_srd_floating_disk_cargo": drop,
        "deactivate_srd_floating_disk": deactivate,
        "refresh_srd_floating_disk": refresh,
        "refresh_srd_floating_disks": all_sources,
        "read_srd_floating_disk_cargo_weight": weight,
    }


def _follow_tool():
    """The neutral sweep qualifies the actual circular body; a blocked disk stays in place."""
    return _tool_scene(
        "Follow the actual caster on a completely supported clear flat route.",
        [
            _entity("caster_state"),
            _parameter("source_id"),
        ],
        [
            *_source(),
            *_scene("_caster_state_id", "_disk_caster"),
            *_ground("_disk_scene.position", "_disk_scene.metadata.plane"),
            _require(
                "_disk_floor.id == _utility_record.floor_id and "
                "abs(_disk_scene.position[2] - _wind_ground_height - 3) < 0.000001",
                "Flat follow requires the disk's actual unchanged supporting floor.",
            ),
            _set("_disk_dx", "_disk_scene.position[0] - _disk_caster.position[0]"),
            _set("_disk_dy", "_disk_scene.position[1] - _disk_caster.position[1]"),
            _set("_disk_dz", "_disk_scene.position[2] - _disk_caster.position[2]"),
            _set("_disk_xy", "(_disk_dx ** 2 + _disk_dy ** 2) ** 0.5"),
            _require(
                "abs(_disk_dz) < 20 and _disk_xy > 0",
                "The disk needs its remaining elevation/stair/slope route consumer.",
            ),
            _set("_disk_ratio", "(400 - _disk_dz ** 2) ** 0.5 * _disk_xy ** -1"),
            _set(
                "_disk_destination",
                [
                    "_disk_caster.position[0] + _disk_dx * _disk_ratio",
                    "_disk_caster.position[1] + _disk_dy * _disk_ratio",
                    "_disk_scene.position[2]",
                ],
            ),
            *_enclosure("_disk_scene.position", "_disk_destination"),
            _set("_disk_material", "_disk_scene.metadata.plane == 'material'"),
            *_snapshot(),
            _spatial(
                "sweep",
                {
                    "map_id": "{state.spatial_scene.map.id}",
                    "moving_entity_id": "{_utility_record.force_id}",
                    "destination": "{_disk_destination}",
                    "include_entities": True,
                    "exclude_entity_ids": "{_utility_record.cargo_ids}",
                    "metadata_filter": {
                        "key": "plane",
                        "value": "{_disk_scene.metadata.plane}",
                        "missing_matches": "{_disk_material}",
                    },
                },
                "_disk_path",
            ),
            _require(
                "_disk_path.supported == true",
                "The actual circular disk needs supported whole-body sweep geometry.",
            ),
            _set("_disk_can_follow", "_disk_path.clear"),
            _set("_disk_cargo", "_utility_record.cargo_ids"),
            _loop(
                "_disk_cargo",
                "_disk_cargo_id",
                [
                    *_scene("_disk_cargo_id", "_disk_cargo_scene"),
                    _set(
                        "_disk_cargo_destination",
                        [
                            f"_disk_cargo_scene.position[{i}] + _disk_destination[{i}] - "
                            f"_disk_scene.position[{i}]"
                            for i in range(3)
                        ],
                    ),
                    *_cargo_sweep("_disk_cargo_id", "_disk_cargo_destination", require_clear=False),
                    _when("_disk_cargo_sweep.clear != true", [_set("_disk_can_follow", False)]),
                ],
            ),
            _when(
                "_disk_can_follow == true",
                [
                    _set("state.disk_relocating", True),
                    _set("_disk_cargo", "_utility_record.cargo_ids"),
                    _loop(
                        "_disk_cargo",
                        "_disk_cargo_id",
                        [
                            *_scene("_disk_cargo_id", "_disk_cargo_scene"),
                            _set(
                                "_disk_cargo_destination",
                                [
                                    f"_disk_cargo_scene.position[{i}] + _disk_destination[{i}] - "
                                    f"_disk_scene.position[{i}]"
                                    for i in range(3)
                                ],
                            ),
                            _move(
                                "_disk_cargo_id",
                                "_disk_cargo_destination",
                                "_disk_scene.metadata.plane",
                            ),
                        ],
                    ),
                    _move(
                        "_utility_record.force_id",
                        "_disk_destination",
                        "_disk_scene.metadata.plane",
                    ),
                    _set("state.disk_relocating", False),
                ],
            ),
        ],
    )


def extend_runtime(text):
    """Add only the owning Disk branch to the existing paid object utility dispatch."""
    tools = _load_runtime(text)["tools"]
    changed = {
        "cast_srd_floating_disk": _cast(),
        "follow_srd_floating_disk": _follow_tool(),
        **_cargo_tools(),
        **_lifecycle_tools(),
    }
    cast = deepcopy(tools["cast_spell_object_utility_spell"])
    cast["effects"] = [
        e for e in cast["effects"] if e.get("condition") != "spell == 'floating_disk'"
    ]
    for e in cast["effects"]:
        condition = e.get("require", {}).get("condition", "")
        if condition in {
            "spell == 'instant_summons'",
            "spell == 'instant_summons' or spell == 'mage_hand'",
        }:
            e["require"] = {
                "condition": condition + " or spell == 'floating_disk'",
                "error": "Select an actual implemented utility spell.",
            }
    cast["effects"].append(
        _when(
            "spell == 'floating_disk'",
            [
                {
                    "call": "cast_srd_floating_disk",
                    "params": {
                        "caster_state": "{_caster_state_id}",
                        "spell": "floating_disk",
                        "slot_level": "{slot_level}",
                        "output_id": "{utility_output_id}",
                        "point": "{utility_point}",
                    },
                    "export_context": {
                        "_utility_summary": "_utility_summary",
                        "_utility_details": "_utility_details",
                    },
                }
            ],
        )
    )
    changed["cast_spell_object_utility_spell"] = cast
    for name, call, before in [
        ("retire_srd_object_utility_source", "clear_srd_floating_disk", "_utility_kept_records"),
        (
            "read_srd_object_utility_source",
            "qualify_srd_floating_disk_source",
            "_object_utility_source",
        ),
    ]:
        tool = deepcopy(tools[name])
        tool["effects"] = [
            e
            for e in tool["effects"]
            if e.get("condition") != "_utility_record.spell == 'floating_disk'"
        ]
        index = next(
            i for i, e in enumerate(tool["effects"]) if e.get("set", {}).get("field") == before
        )
        effects = []
        if name.startswith("read"):
            effects.append(
                _require("object_id == _utility_record.force_id", "Select the actual source disk.")
            )
        effects.append(
            _call(call, {"caster_state": "{_utility_record.caster}", "source_id": "{source_id}"})
        )
        tool["effects"].insert(index, _when("_utility_record.spell == 'floating_disk'", effects))
        changed[name] = tool
    process = deepcopy(tools["process_srd_turn_effects"])
    process["effects"] = [
        e for e in process["effects"] if e.get("call") != "refresh_srd_floating_disks"
    ]
    process["effects"].insert(0, _call("refresh_srd_floating_disks", {}))
    changed["process_srd_turn_effects"] = process
    return _replace_tools(text, changed)


def bind_runtime_consumers(text):
    """Follow after actual caster relocation, suppressing recursive cargo/disk callbacks."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    for name in ("move_srd_travel_subject", "move_srd_scene_creature_node"):
        move = deepcopy(tools[name])
        move["effects"] = [
            e
            for e in move["effects"]
            if not any(c.get("call") == "refresh_srd_floating_disks" for c in e.get("on_true", []))
        ]
        move["effects"].append(
            _when(
                "state.has_floating_disk_sources == true and state.disk_relocating != true",
                [_call("refresh_srd_floating_disks", {})],
            )
        )
        changed[name] = move
    return _replace_tools(text, changed)
