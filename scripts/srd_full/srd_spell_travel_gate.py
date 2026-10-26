"""Gate opens authentic paired circular portals; exact circular transit remains unsupported."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import _array, _snapshot, _spatial, _tool_scene
    from .srd_spell_object_content import _lookup_object, _metadata_patch, _new_scene_object
    from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from .srd_timed_senses import _loop, _optional, _public, _push
else:
    from srd_scene_relations import _array, _snapshot, _spatial, _tool_scene
    from srd_spell_object_content import _lookup_object, _metadata_patch, _new_scene_object
    from srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from srd_timed_senses import _loop, _optional, _public, _push

PUBLIC_TOOLS = {"inspect_srd_gate"}


def gate_intent():
    """Choose source identity, actual destination anchor and unprinted depiction depth."""
    return [
        _parameter("source_id"),
        _parameter("destination_subject"),
        _array("point"),
        _parameter("diameter", "number", minimum=5, maximum=20),
        _parameter("front", "enum", enum_values=["up", "down"]),
        _parameter("depiction_depth", "number", minimum=0.000001),
    ]


def _lookup():
    return [
        _set("_gate_sources", []),
        _when("state.gate_sources != null", [_set("_gate_sources", "state.gate_sources")]),
        _set("_gate_source", None),
        _loop(
            "_gate_sources",
            "_gate_candidate",
            [
                _when(
                    "_gate_candidate.id == source_id",
                    [
                        _require(
                            "_gate_source == null", "The actual Gate source identity is ambiguous."
                        ),
                        _set("_gate_source", "_gate_candidate"),
                    ],
                ),
            ],
        ),
        _require(
            "_gate_source != null and _gate_source.spell == 'gate'",
            "Select one actual retained Gate source.",
        ),
    ]


def _save():
    return [
        _set("_gate_saved_sources", []),
        _loop(
            "_gate_sources",
            "_gate_saved_candidate",
            [
                _when(
                    "_gate_saved_candidate.id == source_id",
                    [
                        _set("_gate_saved_candidate", "_gate_source"),
                    ],
                ),
                _push("_gate_saved_sources", "_gate_saved_candidate"),
            ],
        ),
        _set("state.gate_sources", "_gate_saved_sources"),
    ]


def _space(point, plane, excluded):
    """Check the actual circular opening volume; never approximate it by a rectangle."""
    return [
        *_snapshot(),
        *[
            _require(
                f"{point}[{axis}] - diameter * 0.5 >= "
                f"state.spatial_scene.map.bounds.min[{axis}] and "
                f"{point}[{axis}] + diameter * 0.5 <= state.spatial_scene.map.bounds.max[{axis}]",
                "The whole actual circular opening must fit the admitted map.",
            )
            for axis in range(2)
        ],
        _require(
            f"{point}[2] >= state.spatial_scene.map.bounds.min[2] and "
            f"{point}[2] + depiction_depth <= state.spatial_scene.map.bounds.max[2]",
            "The portal depiction must fit the admitted vertical bounds.",
        ),
        _spatial(
            "area",
            {
                "map_id": "{state.spatial_scene.map.id}",
                "shape": {
                    "kind": "cylinder",
                    "center": "{" + point + "}",
                    "radius": "{_gate_radius}",
                    "height": "{depiction_depth}",
                },
                "include": ["geometry", "entities"],
                "max_results": 512,
            },
            "_gate_space",
        ),
        _require("_gate_space.truncated != true", "The circular occupancy query is incomplete."),
        _set("_gate_hits", "_gate_space.results"),
        _loop(
            "_gate_hits",
            "_gate_hit",
            [
                _set("_gate_hit_plane", "'material'"),
                _when(
                    "_gate_hit.metadata.plane != null",
                    [
                        _set("_gate_hit_plane", "_gate_hit.metadata.plane"),
                    ],
                ),
                _require(
                    f"_gate_hit_plane != {plane} or _gate_hit.id == {excluded}",
                    "The actual circular portal space is occupied on its plane.",
                ),
            ],
        ),
    ]


def gate_tools(tools):
    """Use admitted anchors, exact circle occupancy, canonical creation and concentration."""
    if __package__:
        from .srd_spell_travel import _scene_subject
    else:
        from srd_spell_travel import _scene_subject

    validate = _tool_scene(
        "Qualify both actual circular opening spaces and origin sight before paying Gate.",
        [_entity("caster_state"), *gate_intent()],
        [
            _require("source_id != ''", "Choose a unique Gate source identity."),
            *_scene_subject("{_caster_state_id}", "_gate_caster"),
            *_scene_subject("{destination_subject}", "_gate_anchor"),
            _require(
                "_gate_caster.metadata.plane != null and "
                "_gate_anchor.metadata.plane != null and "
                "_gate_caster.metadata.plane != _gate_anchor.metadata.plane",
                "Gate needs an actual precise destination on another plane.",
            ),
            _require(
                "_gate_anchor.metadata.planar_anchor == true and "
                "_gate_anchor.metadata.participant_ref == null and "
                "_gate_anchor.metadata.is_object == false and "
                "_gate_anchor.movement == 'passable' and _gate_anchor.visibility == 'clear'",
                "Use an admitted nonphysical planar-location anchor, not an occupied body.",
            ),
            _set("_gate_radius", "diameter * 0.5"),
            _call(
                "resolve_srd_illumination_point_sight",
                {
                    "observer_state": "{_caster_state_id}",
                    "point": "{point}",
                },
                ["_illumination_point_can_see", "_illumination_point_distance"],
            ),
            _require(
                "_illumination_point_can_see == true and _illumination_point_distance <= 60",
                "The actual origin must be visible and within sixty feet.",
            ),
            _set("_gate_sources", []),
            _when("state.gate_sources != null", [_set("_gate_sources", "state.gate_sources")]),
            _require("len(_gate_sources) < 128", "The retained Gate source history is full."),
            _loop(
                "_gate_sources",
                "_gate_existing_source",
                [
                    _require(
                        "_gate_existing_source.id != source_id", "Gate identities cannot be reused."
                    ),
                ],
            ),
            _call(
                "bind_srd_gate_ids",
                {
                    "near_id": "{source_id}:near",
                    "far_id": "{source_id}:far",
                },
                ["_gate_near_id", "_gate_far_id"],
            ),
            *[
                effect
                for collection in ("entities", "geometry", "zones")
                for effect in [
                    _set("_gate_existing_rows", f"state.spatial_scene.{collection}"),
                    _loop(
                        "_gate_existing_rows",
                        "_gate_existing_row",
                        [
                            _require(
                                "_gate_existing_row.id != _gate_near_id and "
                                "_gate_existing_row.id != _gate_far_id and "
                                "_gate_existing_row.id != source_id",
                                "Gate cannot replace an actual scene identity.",
                            ),
                        ],
                    ),
                ]
            ],
            *_space("point", "_gate_caster.metadata.plane", "''"),
            *_space("_gate_anchor.position", "_gate_anchor.metadata.plane", "_gate_anchor.id"),
            _set("_gate_prevented", False),
            _set("_gate_prevention", None),
            _when(
                "_gate_anchor.metadata.gate_prevention != null",
                [
                    _require(
                        "_gate_anchor.metadata.gate_prevention.prevents == true or "
                        "_gate_anchor.metadata.gate_prevention.prevents == false",
                        "Admit the actual ruler's prevention choice as a typed fiction fact.",
                    ),
                    _require(
                        "_gate_anchor.metadata.gate_prevention.ruler_name != null and "
                        "_gate_anchor.metadata.gate_prevention.domain != null",
                        "Record the actual deity or ruler and its present domain.",
                    ),
                    _set("_gate_prevention", "_gate_anchor.metadata.gate_prevention"),
                    _set(
                        "_gate_prevented", "_gate_anchor.metadata.gate_prevention.prevents == true"
                    ),
                ],
            ),
        ],
    )
    creation = deepcopy(
        _new_scene_object(
            {
                "plane": "plane",
                "is_object": False,
                "creature_type": "'spell_force'",
                "participant_ref": None,
                "gate_portal": {
                    "source_id": "source_id",
                    "peer_id": "peer_id",
                    "front": "front",
                    "diameter": "diameter",
                    "cast_id": "cast_id",
                    "created_tick": "state.lasting_tick",
                },
            },
            identity="portal_id",
            extent="_gate_extent",
        )
    )
    next(
        row["set"] for row in creation if row.get("set", {}).get("field") == "_world_created_marker"
    )["value"] = {"is_object": False}
    next(
        row["set"] for row in creation if row.get("set", {}).get("field") == "_world_created_entity"
    )["value"]["footprint"] = {
        "kind": "'circle'",
        "radius": "diameter * 0.5",
    }
    create = _tool_scene(
        "Create one actual circular Gate body through canonical scene admission.",
        [
            _parameter("source_id"),
            _parameter("portal_id"),
            _parameter("peer_id"),
            _parameter("plane"),
            _array("point"),
            _parameter("diameter", "number"),
            _parameter("depiction_depth", "number"),
            _parameter("front"),
            _optional("cast_id", default="", model_hidden=True),
        ],
        [
            _set("_world_origin", "point"),
            _set("_gate_extent", ["diameter", "diameter", "depiction_depth"]),
            *creation,
        ],
    )
    install = _tool_scene(
        "Retain the paid Gate attempt and create its exact paired circles when permitted.",
        [
            _entity("caster_state"),
            *gate_intent(),
            _parameter("slot_level", "integer", minimum=9, maximum=9),
            _optional("concentration_modifier", "integer", 0),
            _optional("spell_cast_id", default="", model_hidden=True),
            _optional("spell_cast_owner", default=None, resolve="entity", model_hidden=True),
            _optional("actual_spell_level", "integer", -1, model_hidden=True),
        ],
        [
            _call(
                "validate_srd_gate_cast",
                {
                    "caster_state": "{_caster_state_id}",
                    **{p["name"]: "{" + p["name"] + "}" for p in gate_intent()},
                },
                [
                    "_gate_anchor",
                    "_gate_caster",
                    "_gate_prevented",
                    "_gate_near_id",
                    "_gate_far_id",
                    "_gate_prevention",
                ],
            ),
            _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
            _set("_gate_owner", None),
            _when("spell_cast_owner != null", [_set("_gate_owner", "_spell_cast_owner_id")]),
            _when(
                "spell_cast_id != ''",
                [
                    _require(
                        "spell_cast_owner != null and _spell_cast_owner_id == _caster_state_id and "
                        "caster_state.last_spell_source_cast_id == spell_cast_id and "
                        "caster_state.last_spell_source_spell == 'gate'",
                        "Gate must retain its authentic committed cast identity.",
                    ),
                ],
            ),
            _set("_gate_sequence", None),
            _when(
                "_gate_prevented != true",
                [
                    _set("_gate_focus", ["_caster_state_id"]),
                    _call(
                        "start_srd_condition_concentration",
                        {
                            "caster_state": "{_caster_state_id}",
                            "lasting_state": "state",
                            "spell": "gate",
                            "targets": "{_gate_focus}",
                            "duration_rounds": 10,
                            "save_modifier": "{concentration_modifier}",
                            "spell_cast_id": "{spell_cast_id}",
                            "spell_cast_owner": "{_gate_owner}",
                            "actual_spell_level": "{actual_spell_level}",
                        },
                    ),
                    _set("caster_state.concentration_effect_family", "'travel_gate'"),
                    _set("caster_state.gate_source_id", "source_id"),
                    _set("_gate_sequence", "caster_state.concentration_sequence"),
                ],
            ),
            _set(
                "_gate_source",
                {
                    "id": "source_id",
                    "spell": "'gate'",
                    "caster": "_caster_state_id",
                    "source_caster_id": None,
                    "cast_id": "spell_cast_id",
                    "ongoing_duration": "spell_cast_id != ''",
                    "sequence": None,
                    "slot_level": "slot_level",
                    "spell_level": "slot_level",
                    "active": "_gate_prevented != true",
                    "prevented": "_gate_prevented",
                    "prevention": None,
                    "created_tick": "state.lasting_tick",
                    "expires_tick": "state.lasting_tick + 20",
                    "anchor_id": "_gate_anchor.id",
                    "destination": "_gate_anchor.position",
                    "origin": "point",
                    "origin_plane": "_gate_caster.metadata.plane",
                    "destination_plane": "_gate_anchor.metadata.plane",
                    "near_id": "_gate_near_id",
                    "far_id": "_gate_far_id",
                    "diameter": "diameter",
                    "front": "front",
                    "depiction_depth": "depiction_depth",
                },
            ),
            _when(
                "spell_cast_id != ''",
                [
                    _set("_gate_identity_patch", {"source_caster_id": "_spell_cast_owner_id"}),
                    _set("_gate_source", "merge(_gate_source, _gate_identity_patch)"),
                ],
            ),
            _when(
                "_gate_prevented != true",
                [
                    _set(
                        "_gate_sequence_patch", {"sequence": "caster_state.concentration_sequence"}
                    ),
                    _set("_gate_source", "merge(_gate_source, _gate_sequence_patch)"),
                ],
            ),
            _when(
                "_gate_anchor.metadata.gate_prevention != null",
                [
                    _set(
                        "_gate_prevention_patch",
                        {"prevention": "_gate_anchor.metadata.gate_prevention"},
                    ),
                    _set("_gate_source", "merge(_gate_source, _gate_prevention_patch)"),
                ],
            ),
            _when("state.gate_sources == null", [_set("state.gate_sources", [])]),
            _set("_gate_sources", "state.gate_sources"),
            _push("_gate_sources", "_gate_source"),
            _set("state.gate_sources", "_gate_sources"),
            _set("state.has_gate_sources", True),
            _when(
                "_gate_prevented != true",
                [
                    _call(
                        "create_srd_gate_portal",
                        {
                            "source_id": "{source_id}",
                            "portal_id": "{_gate_near_id}",
                            "peer_id": "{_gate_far_id}",
                            "plane": "{_gate_source.origin_plane}",
                            "point": "{point}",
                            "diameter": "{diameter}",
                            "depiction_depth": "{depiction_depth}",
                            "front": "{front}",
                            "cast_id": "{spell_cast_id}",
                        },
                    ),
                    _call(
                        "create_srd_gate_portal",
                        {
                            "source_id": "{source_id}",
                            "portal_id": "{_gate_far_id}",
                            "peer_id": "{_gate_near_id}",
                            "plane": "{_gate_source.destination_plane}",
                            "point": "{_gate_source.destination}",
                            "diameter": "{diameter}",
                            "depiction_depth": "{depiction_depth}",
                            "front": "{front}",
                            "cast_id": "{spell_cast_id}",
                        },
                    ),
                ],
            ),
            _set("_gate_opened", "_gate_prevented != true"),
        ],
    )
    retirement = _metadata_patch(
        {"gate_retired_tick": "state.lasting_tick"},
        active=False,
        movement="passable",
        visibility="clear",
    )
    next(
        row["set"]
        for row in retirement
        if row.get("set", {}).get("field") == "_world_object_marker"
    )["value"] = {"is_object": False}
    retire = _tool_scene(
        "Retire only an authentic opening body; never relocate a traveler during raw source end.",
        [_parameter("source_id"), _parameter("object_id")],
        [
            *_lookup(),
            *_lookup_object(),
            _require(
                "_object_scene.metadata.gate_portal.source_id == source_id and "
                "_object_scene.metadata.gate_portal.cast_id == _gate_source.cast_id and "
                "_object_scene.metadata.gate_portal.created_tick == _gate_source.created_tick and "
                "(_object_scene.id == _gate_source.near_id or "
                "_object_scene.id == _gate_source.far_id)",
                "Retire only this actual Gate's paired body.",
            ),
            *retirement,
        ],
    )
    end = _tool_scene(
        "End one exact Gate source and retire both actual portals once; no transit is claimed.",
        [_parameter("source_id")],
        [
            *_lookup(),
            _when(
                "_gate_source.active == true",
                [
                    _call(
                        "retire_srd_gate_portal",
                        {"source_id": "{source_id}", "object_id": "{_gate_source.near_id}"},
                    ),
                    _call(
                        "retire_srd_gate_portal",
                        {"source_id": "{source_id}", "object_id": "{_gate_source.far_id}"},
                    ),
                    _set("_gate_patch", {"active": False, "ended_tick": "state.lasting_tick"}),
                    _set("_gate_source", "merge(_gate_source, _gate_patch)"),
                    *_save(),
                ],
            ),
        ],
    )
    ending = _tool_scene(
        "Authenticate the raw concentration callback before ending its Gate source.",
        [_parameter("source_id"), _entity("caster_state"), _parameter("sequence", "integer")],
        [
            *_lookup(),
            _require(
                "_gate_source.caster == _caster_state_id and _gate_source.sequence == sequence",
                "End only the actual Gate concentration sequence.",
            ),
            _call("end_srd_gate_source", {"source_id": "{source_id}"}),
        ],
    )
    refresh = _tool_scene(
        "Refresh actual Gate concentration before admitting its remaining lifetime.",
        [_parameter("source_id")],
        [
            *_lookup(),
            _when(
                "_gate_source.active == true",
                [
                    _call(
                        "refresh_srd_concentration_source",
                        {
                            "source_state": "{_gate_source.caster}",
                            "target_state": "{_gate_source.caster}",
                            "lasting_state": "state",
                        },
                    ),
                    _call(
                        "check_srd_condition_concentration",
                        {
                            "caster_state": "{_gate_source.caster}",
                            "sequence": "{_gate_source.sequence}",
                        },
                        ["_condition_concentration_active"],
                    ),
                    _when(
                        "_condition_concentration_active != true or "
                        "state.lasting_tick >= _gate_source.expires_tick",
                        [
                            _call("end_srd_gate_source", {"source_id": "{source_id}"}),
                        ],
                    ),
                ],
            ),
        ],
    )
    refresh_all = _tool_scene(
        "Refresh each retained actual Gate source against its shared lifetime and caster.",
        [],
        [
            _set("_gate_refresh_sources", "state.gate_sources"),
            _loop(
                "_gate_refresh_sources",
                "_gate_refresh_source",
                [
                    _when(
                        "_gate_refresh_source.active == true",
                        [
                            _call(
                                "refresh_srd_gate_source",
                                {"source_id": "{_gate_refresh_source.id}"},
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    inspect = _public(
        _tool_scene(
            "Report this caster's actual Gate attempt and lifetime; crossing remains unavailable.",
            [_entity("caster_state"), _parameter("source_id")],
            [
                *_lookup(),
                _require(
                    "_gate_source.caster == _caster_state_id", "Inspect this caster's actual Gate."
                ),
                _call("refresh_srd_gate_source", {"source_id": "{source_id}"}),
                *_lookup(),
                _set("_gate_inspection", "_gate_source"),
                _set("_gate_transit_supported", False),
            ],
        ),
        "exploration",
        "Gate",
        [
            ("Portal open", "_gate_inspection.active"),
            ("Prevented by planar ruler", "_gate_inspection.prevented"),
            ("Near portal", "_gate_inspection.near_id"),
            ("Far portal", "_gate_inspection.far_id"),
            ("Diameter (feet)", "_gate_inspection.diameter"),
            ("Transit supported", "_gate_transit_supported"),
        ],
    )
    return {
        "bind_srd_gate_ids": _tool_scene(
            "Bind actual paired Gate scene identities.",
            [_parameter("near_id"), _parameter("far_id")],
            [_set("_gate_near_id", "near_id"), _set("_gate_far_id", "far_id")],
        ),
        "validate_srd_gate_cast": validate,
        "create_srd_gate_portal": create,
        "install_srd_gate": install,
        "retire_srd_gate_portal": retire,
        "end_srd_gate_source": end,
        "end_srd_gate_concentration": ending,
        "refresh_srd_gate_source": refresh,
        "refresh_srd_gate_sources": refresh_all,
        "inspect_srd_gate": inspect,
    }


def bind_gate_consumers(tools, changed):
    """Bind only actual concentration retirement and the existing lasting clock."""
    end = deepcopy(changed.get("end_srd_concentration_node", tools["end_srd_concentration_node"]))
    marker = "source_state.concentration_effect_family == 'travel_gate'"
    end["effects"] = [row for row in end["effects"] if row.get("condition") != marker]
    end["effects"].insert(
        0,
        _when(
            marker,
            [
                _when(
                    "source_state.gate_source_id != null",
                    [
                        _call(
                            "end_srd_gate_concentration",
                            {
                                "source_id": "{source_state.gate_source_id}",
                                "caster_state": "{_source_state_id}",
                                "sequence": "{source_state.concentration_sequence}",
                            },
                        ),
                        _set("source_state.gate_source_id", None),
                    ],
                ),
            ],
        ),
    )
    changed["end_srd_concentration_node"] = end
    clock = deepcopy(changed.get("advance_srd_lasting_clock", tools["advance_srd_lasting_clock"]))
    clock["effects"] = [
        row for row in clock["effects"] if "refresh_srd_gate_sources" not in str(row)
    ]
    clock["effects"].append(
        _when("state.has_gate_sources == true", [_call("refresh_srd_gate_sources", {})])
    )
    changed["advance_srd_lasting_clock"] = clock
