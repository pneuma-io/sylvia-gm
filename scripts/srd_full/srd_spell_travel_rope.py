"""Rope Trick measures its admitted rope and actual ceiling before creating an entrance."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import _array, _snapshot, _tool_scene
    from .srd_spell_object_content import _admission_guard, _metadata_patch
    from .srd_spell_travel_dwelling import _lookup, dwelling_intent, dwelling_tools
    from .srd_spell_travel_room_geometry import _body_clearance
    from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from .srd_timed_senses import _loop, _optional, _public
else:
    from srd_scene_relations import _array, _snapshot, _tool_scene
    from srd_spell_object_content import _admission_guard, _metadata_patch
    from srd_spell_travel_dwelling import _lookup, dwelling_intent, dwelling_tools
    from srd_spell_travel_room_geometry import _body_clearance
    from srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from srd_timed_senses import _loop, _optional, _public

PUBLIC_TOOLS = {"record_srd_rope_length", "traverse_srd_rope_trick", "climb_srd_rope_trick"}


def rope_intent():
    """A real rope identity and unprinted interior design are intent, never outcomes."""
    return [_parameter("rope_id"), *[p for p in dwelling_intent() if p["name"] != "point"]]


def _replace(value, before, after):
    """Specialize the existing construction body at compile time, retaining its one creator."""
    if isinstance(value, str):
        return value.replace(before, after)
    if isinstance(value, list):
        return [_replace(item, before, after) for item in value]
    if isinstance(value, dict):
        return {key: _replace(item, before, after) for key, item in value.items()}
    return value


def rope_tools(tools):
    """Bind physical rope facts and source-specific layout to existing admitted geometry."""
    if __package__:
        from .srd_spell_travel import (
            ACTIVE,
            _measure,
            _move,
            _movement_cost,
            _placement,
            _scene_subject,
        )
    else:
        from srd_spell_travel import (
            ACTIVE,
            _measure,
            _move,
            _movement_cost,
            _placement,
            _scene_subject,
        )

    facts = _public(
        _tool_scene(
            "Admit this actual rope object's physical length once, in feet.",
            [
                _parameter("object_id"),
                _parameter("length_feet", "number", minimum=0.001, maximum=100000),
            ],
            [
                *_admission_guard(),
                _require(
                    "_object_scene.metadata.is_object == true",
                    "Select an actual admitted rope object.",
                ),
                _require(
                    "_object_scene.metadata.srd_rope == null",
                    "This rope's actual physical length is already admitted.",
                ),
                *_metadata_patch({"srd_rope": {"length_feet": "length_feet"}}),
                _set("_rope_length", "length_feet"),
            ],
        ),
        "information",
        "Rope length",
        [("Feet", "_rope_length")],
    )
    physical = _tool_scene(
        "Resolve the actual touched rope and its first same-plane blocking ceiling.",
        [_entity("caster_state"), _parameter("rope_id")],
        [
            *_snapshot(),
            *_scene_subject("{_caster_state_id}", "_rt_caster"),
            *_scene_subject("{rope_id}", "_rt_rope"),
            _require(
                "_rt_rope.metadata.is_object == true and "
                "_rt_rope.metadata.srd_rope.length_feet != null and "
                "_rt_rope.metadata.srd_rope.length_feet > 0",
                "Choose an actual rope with its admitted physical length.",
            ),
            _require(
                "_rt_caster.metadata.plane != null and "
                "_rt_rope.metadata.plane == _rt_caster.metadata.plane",
                "Touch the actual rope on the caster's current plane.",
            ),
            _require(
                "_rt_rope.metadata.carried_by == null and "
                "_rt_rope.metadata.worn_by == null and "
                "_rt_rope.metadata.rope_trick_source_id == null",
                "Held, worn or independently enchanted ropes need their actual owner.",
            ),
            _require(
                "_rt_rope.footprint.kind == 'box'",
                "The initial physical rope mode needs an admitted box body.",
            ),
            *_measure("{_rt_caster.position}", "{_rt_rope.position}", "_rt_touch_distance"),
            _require("_rt_touch_distance <= 5", "Reach the actual rope before casting."),
            _set("_rt_ceiling_id", "''"),
            _set("_rt_top_height", "_rt_rope.position[2] + _rt_rope.metadata.srd_rope.length_feet"),
            _set(
                "_rt_ray_end",
                [
                    "_rt_rope.position[0]",
                    "_rt_rope.position[1]",
                    "state.spatial_scene.map.bounds.max[2]",
                ],
            ),
            {
                "spatial_query": {
                    "scene": "state.spatial_scene",
                    "operation": "ray",
                    "query": {
                        "map_id": "{state.spatial_scene.map.id}",
                        "origin": "{_rt_rope.position}",
                        "destination": "{_rt_ray_end}",
                        "include": ["geometry"],
                        "max_intersections": 1000,
                    },
                },
                "store_as": "_rt_upward_ray",
            },
            _require(
                "_rt_upward_ray.truncated != true",
                "The real ceiling query exceeds its supported intersection bound.",
            ),
            _set("_rt_ceiling_hits", "_rt_upward_ray.intersections"),
            _set("_rt_ceiling_geometry", "state.spatial_scene.geometry"),
            _loop(
                "_rt_ceiling_hits",
                "_rt_ceiling_hit",
                [
                    _when(
                        "_rt_ceiling_hit.normal[2] < 0 and "
                        "(_rt_ceiling_hit.metadata.plane == _rt_rope.metadata.plane or "
                        "(_rt_ceiling_hit.metadata.plane == null and "
                        "_rt_rope.metadata.plane == 'material'))",
                        [
                            _loop(
                                "_rt_ceiling_geometry",
                                "_rt_ceiling",
                                [
                                    _when(
                                        "_rt_ceiling.id == _rt_ceiling_hit.id and "
                                        "_rt_ceiling.active == true and "
                                        "_rt_ceiling.movement == 'blocking' and "
                                        "_rt_ceiling_hit.point[2] < _rt_top_height",
                                        [
                                            _require(
                                                "_rt_ceiling.volume.kind == 'box'",
                                                "Curved ceilings need their actual aperture owner.",
                                            ),
                                            _set("_rt_top_height", "_rt_ceiling_hit.point[2]"),
                                            _set("_rt_ceiling_id", "_rt_ceiling.id"),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _require(
                "_rt_top_height > _rt_rope.position[2]",
                "The actual rope needs positive clear space overhead.",
            ),
            _set("_rt_hung_height", "_rt_top_height - _rt_rope.position[2]"),
            _set(
                "_rt_portal_point",
                ["_rt_rope.position[0]", "_rt_rope.position[1]", "_rt_top_height"],
            ),
        ],
    )
    mansion = dwelling_tools(tools)
    layout = deepcopy(mansion["validate_srd_magnificent_mansion"])
    layout["description"] = "Validate the Rope Trick's physical horizontal aperture and room."
    layout["parameters"].append(_optional("ceiling_id", default=""))
    effects = []
    for effect in layout["effects"]:
        if effect.get("call") == "measure_srd_information_distance":
            continue
        if "_xd_range <= 300" in str(effect):
            continue
        field = effect.get("set", {}).get("field")
        if field == "_dp_physical_extent":
            effect["set"]["value"] = [3, 5, "door_depth"]
        if field == "surface_id":
            continue
        effect = _replace(effect, "surface_id", "ceiling_id")
        effects.append(effect)
    layout["effects"] = effects
    intent = rope_intent()
    preflight = _tool_scene(
        "Validate the actual Rope Trick rope, ceiling, source identity and room before payment.",
        [_entity("caster_state"), *intent],
        [
            _require("len(designated) == 0", "Rope Trick has no designated-guest restriction."),
            _call(
                "resolve_srd_rope_trick_geometry",
                {"caster_state": "{_caster_state_id}", "rope_id": "{rope_id}"},
                ["_rt_rope", "_rt_caster", "_rt_portal_point", "_rt_hung_height", "_rt_ceiling_id"],
            ),
            _call(
                "validate_srd_rope_trick_layout",
                {
                    "caster_state": "{_caster_state_id}",
                    "point": "{_rt_portal_point}",
                    "ceiling_id": "{_rt_ceiling_id}",
                    **{p["name"]: "{" + p["name"] + "}" for p in intent if p["name"] != "rope_id"},
                },
                [
                    "_xd_caster",
                    "_xd_room_center",
                    "_xd_room_extent",
                    "_xd_guests",
                    "_xd_plane",
                    "_xd_outer_id",
                    "_xd_inner_id",
                ],
            ),
        ],
    )
    install = _replace(
        deepcopy(mansion["install_srd_magnificent_mansion"]), "magnificent_mansion", "rope_trick"
    )
    install["description"] = "Suspend the paid spell's actual rope and create its retained portals."
    install["parameters"] = [p for p in install["parameters"] if p["name"] != "point"]
    install["parameters"].insert(2, _parameter("rope_id"))
    for parameter in install["parameters"]:
        if parameter["name"] == "slot_level":
            parameter["minimum"] = 2
    install["effects"][0] = _call(
        "validate_srd_rope_trick",
        {
            "caster_state": "{_caster_state_id}",
            **{p["name"]: "{" + p["name"] + "}" for p in intent},
        },
        [
            "_rt_rope",
            "_rt_caster",
            "_rt_portal_point",
            "_rt_hung_height",
            "_rt_ceiling_id",
            "_xd_caster",
            "_xd_room_center",
            "_xd_room_extent",
            "_xd_guests",
            "_xd_plane",
            "_xd_outer_id",
            "_xd_inner_id",
        ],
    )
    for effect in install["effects"]:
        field = effect.get("set", {}).get("field")
        if field == "_xd_source":
            record = effect["set"]["value"]
            if isinstance(record, dict):
                record.update(
                    {
                        "origin": "_rt_portal_point",
                        "expires_tick": "state.lasting_tick + 1200",
                        "rope_id": "rope_id",
                        "rope_lower": "_rt_rope.position",
                        "rope_initial_height": "_rt_rope.height",
                        "rope_initial_footprint": "_rt_rope.footprint",
                        "hung_height": "_rt_hung_height",
                        "ceiling_id": "_rt_ceiling_id",
                        "rope_pulled": False,
                    }
                )
        if field == "_xd_inner_point":
            effect["set"]["value"] = [
                "_xd_room_center[0]",
                "_xd_room_center[1]",
                "_xd_room.shape.min[2]",
            ]
        if field == "_xd_room":
            effect["set"]["value"]["metadata"].pop("atmosphere", None)
        if field == "_world_origin" and effect["set"]["value"] == "point":
            effect["set"]["value"] = "_rt_portal_point"
        if field == "_xd_panel_extent":
            effect["set"]["value"] = [3, 5, "door_depth"]
        if field == "_world_created_metadata":
            metadata = effect["set"]["value"]
            if isinstance(metadata, dict):
                metadata.pop("atmosphere", None)
                metadata.update(
                    {
                        "kind": "'portal'",
                        "aperture_width": 3,
                        "aperture_height": 5,
                        "spell_school": "'transmutation'",
                        "invisible": True,
                        "aperture_face": "'down'"
                        if metadata.get("plane") == "_xd_plane"
                        else "'up'",
                    }
                )
    project = _tool_scene(
        "Project the exact source's derived hanging rope shape through canonical scenery.",
        [_parameter("object_id"), _parameter("source_id")],
        [
            *_lookup(),
            _require(
                "_xd_source.spell == 'rope_trick' and _xd_source.active == true and "
                "_xd_source.rope_id == object_id",
                "Use this actual source's rope.",
            ),
            *_scene_subject("{object_id}", "_object_scene"),
            *_metadata_patch({"rope_trick_source_id": "source_id"}),
            *_snapshot(),
            {
                "spatial_mutation": {
                    "scene": "state.spatial_scene",
                    "map_id": "{state.spatial_scene.map.id}",
                    "expected_revision": "{_info_snapshot.geometry_revision}",
                    "target": {"kind": "entity", "id": "{object_id}"},
                    "patch": {"height": "{_xd_source.hung_height}"},
                },
                "store_as": "_rt_hanging_mutation",
            },
        ],
    )
    install["effects"].append(
        _call(
            "project_srd_rope_trick_shape", {"object_id": "{rope_id}", "source_id": "{dwelling_id}"}
        )
    )
    magic = deepcopy(mansion["check_srd_dwelling_magic"])
    for parameter in magic["parameters"]:
        if parameter["name"] == "slot_level":
            parameter["minimum"] = 2
    climbing = _public(
        _tool_scene(
            "Climb the actual hanging rope with existing movement costs, clearance and position.",
            [
                _entity("actor_state"),
                _parameter("source_id"),
                _parameter("destination_height", "number"),
            ],
            [
                _require(ACTIVE, "A capable living creature must climb the actual rope."),
                _call("refresh_srd_extradimensional_sources", {}),
                *_lookup(),
                _require(
                    "_xd_source.active == true and _xd_source.spell == 'rope_trick' and "
                    "_xd_source.rope_pulled != true",
                    "The actual hanging rope must be available.",
                ),
                _when(
                    "actor_state.has_transformations == true",
                    [
                        _call(
                            "project_srd_transformation",
                            {"actor_state": "{_actor_state_id}", "lasting_state": "state"},
                        ),
                    ],
                ),
                _require(
                    "actor_state.form_cloud != true", "A cloud cannot manipulate the physical rope."
                ),
                *_scene_subject("{_actor_state_id}", "_rt_climber"),
                *_scene_subject("{_xd_source.rope_id}", "_rt_hanging_rope"),
                _require(
                    "_rt_climber.metadata.plane == _rt_hanging_rope.metadata.plane and "
                    "_rt_hanging_rope.metadata.plane == _xd_source.origin_plane and "
                    "_rt_hanging_rope.metadata.rope_trick_source_id == source_id and "
                    "_rt_hanging_rope.position[0] == _xd_source.rope_lower[0] and "
                    "_rt_hanging_rope.position[1] == _xd_source.rope_lower[1] and "
                    "_rt_hanging_rope.position[2] == _xd_source.rope_lower[2] and "
                    "_rt_hanging_rope.height == _xd_source.hung_height",
                    "Climb this source's actual unchanged hanging rope on its current plane.",
                ),
                _require(
                    "_rt_climber.footprint.kind == 'box'", "Actual rope contact needs a box body."
                ),
                *[
                    _require(
                        f"abs(_rt_climber.position[{axis}] - _rt_hanging_rope.position[{axis}]) "
                        f"<= _rt_climber.footprint.half_extents[{axis}]",
                        "The actual body must touch the hanging rope before climbing.",
                    )
                    for axis in range(2)
                ],
                _require(
                    "_rt_climber.position[2] >= _xd_source.rope_lower[2] and "
                    "_rt_climber.position[2] <= _xd_source.origin[2] and "
                    "destination_height >= _xd_source.rope_lower[2] and "
                    "destination_height + _rt_climber.height <= _xd_source.origin[2] + 0.0000001",
                    "Choose an actual vertical climbing destination on the hanging rope.",
                ),
                _call(
                    "derive_srd_scene_movement_budget",
                    {"actor_state": "{_actor_state_id}", "mode": "walk"},
                ),
                _set("_rt_climb_mode", "'walk'"),
                _when(
                    "(actor_state.climb_speed != null and actor_state.climb_speed > 0) or "
                    "actor_state.spell_spider_climb == true",
                    [_set("_rt_climb_mode", "'climb'")],
                ),
                _set("_rt_climb_distance", "abs(destination_height - _rt_climber.position[2])"),
                _require("_rt_climb_distance > 0", "Choose positive actual rope movement."),
                _when("_rt_climb_mode == 'walk'", _movement_cost("{_rt_climb_distance}")),
                _set(
                    "_rt_climb_destination",
                    ["_rt_climber.position[0]", "_rt_climber.position[1]", "destination_height"],
                ),
                _call(
                    "move_srd_scene_creature",
                    {
                        "actor_state": "{_actor_state_id}",
                        "destination": "{_rt_climb_destination}",
                        "mode": "{_rt_climb_mode}",
                    },
                ),
            ],
        ),
        "movement",
        "Climb Rope Trick",
        [("Feet", "_rt_climb_distance")],
    )
    crossing = _public(
        _tool_scene(
            "Traverse the actual horizontal Rope Trick opening after reaching it by real movement.",
            [
                _entity("actor_state"),
                _parameter("source_id"),
                _parameter("door_subject"),
                _array("destination"),
            ],
            [
                _require(ACTIVE, "A capable living creature must traverse this actual opening."),
                _call("refresh_srd_extradimensional_sources", {}),
                *_lookup(),
                _require(
                    "_xd_source.active == true and _xd_source.spell == 'rope_trick'",
                    "Select an actual active Rope Trick source.",
                ),
                _require(
                    "door_subject == _xd_source.outer_id or door_subject == _xd_source.inner_id",
                    "Choose this source's actual portal.",
                ),
                _when(
                    "actor_state.has_transformations == true",
                    [
                        _call(
                            "project_srd_transformation",
                            {"actor_state": "{_actor_state_id}", "lasting_state": "state"},
                        ),
                    ],
                ),
                _require(
                    "actor_state.size_rank != null and actor_state.size_rank >= 0 and "
                    "actor_state.size_rank <= 2",
                    "Rope Trick admits Medium or smaller creatures.",
                ),
                *_scene_subject("{_actor_state_id}", "_rt_actor"),
                *_scene_subject("{door_subject}", "_rt_entry"),
                _require(
                    "_rt_actor.metadata.plane == _rt_entry.metadata.plane",
                    "Reach the actual opening on the creature's current plane.",
                ),
                *_body_clearance("_rt_actor", "'up'", "3", "5"),
                *[
                    _require(
                        f"abs(_rt_actor.position[{axis}] - _rt_entry.position[{axis}]) + "
                        f"_rt_actor.footprint.half_extents[{axis}] <= {limit}",
                        "The actual body must reach and fit the horizontal opening.",
                    )
                    for axis, limit in ((0, 1.5), (1, 2.5))
                ],
                _require(
                    "_rt_actor.position[2] <= _rt_entry.position[2] and "
                    "_rt_actor.position[2] + _rt_actor.height >= _rt_entry.position[2] - 0.0000001",
                    "Climb the actual rope until the body reaches its opening.",
                ),
                _set(
                    "_rt_approach_point",
                    ["_rt_actor.position[0]", "_rt_actor.position[1]", "_rt_entry.position[2]"],
                ),
                *_measure("{_rt_approach_point}", "{_rt_entry.position}", "_rt_approach"),
                *_scene_subject("{_rt_entry.metadata.dwelling_peer}", "_rt_exit"),
                *[
                    _call(
                        "check_srd_rope_trick_magic",
                        {"subject": subject, "slot_level": "{_xd_source.slot_level}"},
                    )
                    for subject in ("{_rt_actor.id}", "{_rt_entry.id}", "{_rt_exit.id}")
                ],
                _when(
                    "door_subject == _xd_source.outer_id",
                    [
                        _set("_rt_occupants", 0),
                        _set("_rt_bodies", "state.spatial_scene.entities"),
                        _loop(
                            "_rt_bodies",
                            "_rt_body",
                            [
                                _when(
                                    "_rt_body.active == true and "
                                    "_rt_body.metadata.participant_ref != null "
                                    "and _rt_body.metadata.plane == _xd_source.room_id",
                                    [_set("_rt_occupants", "_rt_occupants + 1")],
                                ),
                            ],
                        ),
                        _require(
                            "_rt_occupants < 8",
                            "The actual Rope Trick space already holds eight creatures.",
                        ),
                        _call(
                            "check_srd_dwelling_room_bounds",
                            {
                                "subject": "{_actor_state_id}",
                                "destination": "{destination}",
                                "plane": "{_xd_source.room_id}",
                            },
                        ),
                    ],
                ),
                *[
                    _require(
                        f"abs(destination[{axis}] - _rt_exit.position[{axis}]) + "
                        f"_rt_actor.footprint.half_extents[{axis}] <= {limit}",
                        "Choose actual body clearance through the counterpart opening.",
                    )
                    for axis, limit in ((0, 1.5), (1, 2.5))
                ],
                _require(
                    "destination[2] <= _rt_exit.position[2] and destination[2] + "
                    "_rt_actor.height >= _rt_exit.position[2] - 0.0000001",
                    "The actual destination body must touch the counterpart opening.",
                ),
                _set(
                    "_rt_departure_point",
                    ["destination[0]", "destination[1]", "_rt_exit.position[2]"],
                ),
                *_measure("{_rt_exit.position}", "{_rt_departure_point}", "_rt_departure"),
                _set("_travel_excluded", []),
                *_placement("{_actor_state_id}", "{destination}", "{_rt_exit.metadata.plane}"),
                _require(
                    "_travel_placement.distance == 0", "The actual arrival space is occupied."
                ),
                _set("_rt_movement", "ceil(_rt_approach + _rt_departure)"),
                _when("_rt_movement > 0", _movement_cost("{_rt_movement}")),
                *_move("{_actor_state_id}", "{destination}", "{_rt_exit.metadata.plane}", False),
                _set("_rope_trick_entered", "source_id"),
            ],
        ),
        "exploration",
        "Rope Trick opening",
        [("Source", "_rope_trick_entered")],
    )
    return {
        "record_srd_rope_length": facts,
        "resolve_srd_rope_trick_geometry": physical,
        "validate_srd_rope_trick_layout": layout,
        "validate_srd_rope_trick": preflight,
        "install_srd_rope_trick": install,
        "project_srd_rope_trick_shape": project,
        "check_srd_rope_trick_magic": magic,
        "traverse_srd_rope_trick": crossing,
        "climb_srd_rope_trick": climbing,
    }


def bind_rope_consumers(tools, changed):
    """Keep generic Mansion traversal from bypassing a different source's printed admission."""
    name = "traverse_srd_dwelling_door_node"
    if name not in tools:
        name = "traverse_srd_dwelling_door"
    tool = changed.get(name, tools.get(name))
    if tool is None or "Mansion-specific admission" in str(tool["effects"]):
        return
    tool = deepcopy(tool)
    boundary = next(
        (
            index
            for index, effect in enumerate(tool["effects"])
            if "_xd_source.active == true" in str(effect)
        ),
        None,
    )
    if boundary is None:
        return
    tool["effects"].insert(
        boundary,
        _require(
            "_xd_source.spell == 'magnificent_mansion'", "Use the Mansion-specific admission owner."
        ),
    )
    changed[name] = tool
