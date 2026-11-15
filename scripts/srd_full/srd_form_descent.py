"Wind Walk's ended cloud flight uses the existing scene, ground and fall owners."

from copy import deepcopy

if __package__:
    from . import srd_actor_spell_modifiers as actor
    from .srd_scene_relations import _tool_scene
else:
    import srd_actor_spell_modifiers as actor
    from srd_scene_relations import _tool_scene

_set, _when, _call, _require = actor._set, actor._when, actor._call, actor._require
_entity, _parameter = actor._entity, actor._parameter


def _loop(collection, item, effects):
    return {"for_each": {"collection": collection, "as": item, "do": effects}}


def _scene_subject():
    return _call(
        "srd_information_scene_entity",
        {"subject": "{_target_state_id}"},
        ["_info_scene_entity", "_info_position"],
    )


def _ground_query():
    "Use only real active blocking same-plane ground; map bounds merely limit the ray."
    return [
        _set("_wind_ground_height", None),
        _set(
            "_wind_ray_origin",
            ["_info_position[0]", "_info_position[1]", "_info_position[2] + 0.000001"],
        ),
        _set(
            "_wind_ray_destination",
            ["_info_position[0]", "_info_position[1]", "state.spatial_scene.map.bounds.min[2]"],
        ),
        {
            "spatial_query": {
                "scene": "state.spatial_scene",
                "operation": "ray",
                "query": {
                    "map_id": "{state.spatial_scene.map.id}",
                    "origin": "{_wind_ray_origin}",
                    "destination": "{_wind_ray_destination}",
                    "include": ["geometry"],
                    "max_intersections": 1000,
                },
            },
            "store_as": "_wind_ground_ray",
        },
        _require(
            "_wind_ground_ray.truncated != true",
            "Wind Walk ground support exceeds the admitted ray bound.",
        ),
        _set("_wind_ground_hits", "_wind_ground_ray.intersections"),
        _set("_wind_ground_geometry", "state.spatial_scene.geometry"),
        _loop(
            "_wind_ground_hits",
            "_wind_ground_hit",
            [
                _when(
                    "_wind_ground_hit.normal[2] > 0 and "
                    "_wind_ground_hit.metadata.ground == true and "
                    "_wind_ground_hit.metadata.plane == _info_scene_entity.metadata.plane",
                    [
                        _loop(
                            "_wind_ground_geometry",
                            "_wind_ground",
                            [
                                _when(
                                    "_wind_ground.id == _wind_ground_hit.id and "
                                    "_wind_ground.active == true and "
                                    "_wind_ground.movement == 'blocking'",
                                    [
                                        _when(
                                            "_wind_ground_height == null or "
                                            "_wind_ground_hit.point[2] > _wind_ground_height",
                                            [
                                                _set(
                                                    "_wind_ground_height",
                                                    "_wind_ground_hit.point[2]",
                                                )
                                            ],
                                        )
                                    ],
                                ),
                            ],
                        )
                    ],
                ),
            ],
        ),
        _require(
            "_wind_ground_height != null",
            "Wind Walk descent needs actual same-plane supporting ground "
            "below its saved scene body.",
        ),
    ]


def _sweep(destination):
    "Automatic descent retains ordinary actual body clearance without paying movement."
    return [
        {
            "spatial_query": {
                "scene": "state.spatial_scene",
                "operation": "sweep",
                "query": {
                    "map_id": "{state.spatial_scene.map.id}",
                    "moving_entity_id": "{_info_scene_entity.id}",
                    "destination": "{" + destination + "}",
                    "include_entities": False,
                    "metadata_filter": {
                        "key": "plane",
                        "value": "{_info_scene_entity.metadata.plane}",
                        "missing_matches": "{_wind_material_plane}",
                    },
                },
            },
            "store_as": "_wind_descent_clearance",
        },
        _require(
            "_wind_descent_clearance.supported == true and _wind_descent_clearance.clear == true",
            "Wind Walk descent requires a supported clear path for its actual normal body.",
        ),
    ]


def _move(destination):
    return _call(
        "move_srd_travel_subject",
        {
            "subject": "{_target_state_id}",
            "destination": "{" + destination + "}",
            "plane": "{_info_scene_entity.metadata.plane}",
            "teleport": False,
        },
    )


def _start_tool():
    return _tool_scene(
        "Retain only an ended effective Wind Walk cloud's actual descent window.",
        [_entity("target_state"), _entity("lasting_state"), _parameter("source_id")],
        [
            _set("_wind_ended_source", "state.transformation_sources[source_id]"),
            _when(
                "_wind_ended_source.spell_id == 'wind_walk' and "
                "target_state.form_cloud == true and (_wind_ended_source.installed != true or "
                "state.lasting_tick >= _wind_ended_source.expires_tick)",
                [
                    _require(
                        "_wind_ended_source.target_state_id == _target_state_id",
                        "The ended cloud belongs to another subject.",
                    ),
                    _when(
                        "state.spatial_scene != null and "
                        "(target_state.wind_walk_descent == null or "
                        "target_state.wind_walk_descent.source_serial != "
                        "_wind_ended_source.serial)",
                        [
                            _scene_subject(),
                            _set(
                                "_wind_end_tick",
                                "min(state.lasting_tick, _wind_ended_source.expires_tick)",
                            ),
                            _set(
                                "target_state.wind_walk_descent",
                                {
                                    "source_id": "source_id",
                                    "source_serial": "_wind_ended_source.serial",
                                    "status": "pending",
                                    "started_tick": "_wind_end_tick",
                                    "settled_tick": "_wind_end_tick",
                                    "end_tick": "_wind_end_tick + 20",
                                },
                            ),
                            _when(
                                "state.wind_walk_descent_target_ids == null",
                                [_set("state.wind_walk_descent_target_ids", [])],
                            ),
                            _set("_wind_pending_targets", "state.wind_walk_descent_target_ids"),
                            _when(
                                "_target_state_id not in _wind_pending_targets",
                                [
                                    {
                                        "list_push": {
                                            "list": "_wind_pending_targets",
                                            "value": "_target_state_id",
                                        }
                                    },
                                    _set(
                                        "state.wind_walk_descent_target_ids",
                                        "_wind_pending_targets",
                                    ),
                                ],
                            ),
                            _set("state.has_wind_walk_descent", True),
                        ],
                    ),
                ],
            ),
        ],
    )


def _settle_tool():
    return _tool_scene(
        "Move an actual ended cloud sixty feet per round and settle only its remaining real fall.",
        [_entity("target_state"), _entity("lasting_state")],
        [
            _set("_wind_descent_pending", False),
            _when(
                "target_state.wind_walk_descent.status == 'pending' and "
                "target_state.form_current_source_id != null",
                [
                    _set(
                        "_wind_new_source",
                        "state.transformation_sources[target_state.form_current_source_id]",
                    ),
                    _when(
                        "_wind_new_source.spell_id == 'wind_walk' and "
                        "_wind_new_source.installed == true and _wind_new_source.serial != "
                        "target_state.wind_walk_descent.source_serial and "
                        "state.lasting_tick < _wind_new_source.expires_tick",
                        [_set("target_state.wind_walk_descent.status", "superseded")],
                    ),
                ],
            ),
            _when(
                "target_state.wind_walk_descent.status == 'pending' and "
                "state.lasting_tick > target_state.wind_walk_descent.settled_tick",
                [
                    _scene_subject(),
                    *_ground_query(),
                    _set("_wind_material_plane", "_info_scene_entity.metadata.plane == 'material'"),
                    _set(
                        "_wind_descent_elapsed",
                        "max(0, min(state.lasting_tick, target_state.wind_walk_descent.end_tick) - "
                        "target_state.wind_walk_descent.settled_tick)",
                    ),
                    _set(
                        "_wind_descent_height",
                        "max(_wind_ground_height, _info_position[2] - _wind_descent_elapsed * 30)",
                    ),
                    _set(
                        "_wind_descent_destination",
                        ["_info_position[0]", "_info_position[1]", "_wind_descent_height"],
                    ),
                    *_sweep("_wind_descent_destination"),
                    _move("_wind_descent_destination"),
                    _set(
                        "target_state.wind_walk_descent.settled_tick",
                        "min(state.lasting_tick, target_state.wind_walk_descent.end_tick)",
                    ),
                    _when(
                        "_wind_descent_height <= _wind_ground_height",
                        [
                            _set("target_state.wind_walk_descent.status", "landed"),
                        ],
                    ),
                    _when(
                        "target_state.wind_walk_descent.status == 'pending' and "
                        "state.lasting_tick >= target_state.wind_walk_descent.end_tick",
                        [
                            _scene_subject(),
                            _set(
                                "_wind_remaining_fall", "_wind_descent_height - _wind_ground_height"
                            ),
                            _set(
                                "_wind_descent_destination",
                                ["_info_position[0]", "_info_position[1]", "_wind_ground_height"],
                            ),
                            *_sweep("_wind_descent_destination"),
                            _set("target_state.wind_walk_descent.status", "fallen"),
                            _when(
                                "target_state.is_alive == true",
                                [
                                    _call(
                                        "resolve_srd_fall",
                                        {
                                            "target_state": "{_target_state_id}",
                                            "distance": "{_wind_remaining_fall}",
                                            "landing": "ground",
                                            "reaction": "none",
                                        },
                                    ),
                                ],
                            ),
                            _move("_wind_descent_destination"),
                        ],
                    ),
                ],
            ),
            _when(
                "target_state.wind_walk_descent.status == 'pending'",
                [
                    _set("_wind_descent_pending", True),
                ],
            ),
        ],
    )


def compose(tools, changed):
    "Keep registration out of damage/movement settlement to preserve an acyclic owning graph."
    changed["start_srd_wind_walk_descent"] = _start_tool()
    changed["settle_srd_wind_walk_descent_target"] = _settle_tool()
    projection = changed["project_srd_transformation"]
    registration = _when(
        "target_state.form_current_source_id != null and target_state.form_cloud == true",
        [
            _call(
                "start_srd_wind_walk_descent",
                {
                    "target_state": "{_target_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                    "source_id": "{target_state.form_current_source_id}",
                },
            )
        ],
    )
    projection["effects"][0]["on_true"] = [
        effect
        for effect in projection["effects"][0]["on_true"]
        if "start_srd_wind_walk_descent" not in str(effect)
    ]
    projection["effects"][0]["on_true"].insert(0, registration)
    clock = deepcopy(changed.get("advance_srd_lasting_clock", tools["advance_srd_lasting_clock"]))
    clock["effects"] = [
        e
        for e in clock["effects"]
        if e.get("condition") != "lasting_state.has_wind_walk_descent == true"
    ]
    clock["effects"].append(
        _when(
            "lasting_state.has_wind_walk_descent == true",
            [
                _set("_wind_descent_targets", "lasting_state.wind_walk_descent_target_ids"),
                _set("_wind_remaining_targets", []),
                _loop(
                    "_wind_descent_targets",
                    "_wind_descent_target",
                    [
                        _call(
                            "settle_srd_wind_walk_descent_target",
                            {
                                "target_state": "{_wind_descent_target}",
                                "lasting_state": "{_lasting_state_id}",
                            },
                            ["_wind_descent_pending"],
                        ),
                        _when(
                            "_wind_descent_pending == true",
                            [
                                {
                                    "list_push": {
                                        "list": "_wind_remaining_targets",
                                        "value": "_wind_descent_target",
                                    }
                                },
                            ],
                        ),
                    ],
                ),
                _set("lasting_state.wind_walk_descent_target_ids", "_wind_remaining_targets"),
                _set("lasting_state.has_wind_walk_descent", "len(_wind_remaining_targets) > 0"),
            ],
        )
    )
    changed["advance_srd_lasting_clock"] = clock
