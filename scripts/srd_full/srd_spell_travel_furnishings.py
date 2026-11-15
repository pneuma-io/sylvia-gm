"""Mansion furnishings are source-created scenery and smoke on actual departure."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import _array, _snapshot, _tool_scene
    from .srd_spell_object_content import _new_scene_object
    from .srd_timed_actor_defenses import _call, _parameter, _require, _set, _when
    from .srd_timed_senses import _loop, _optional, _push
else:
    from srd_scene_relations import _array, _snapshot, _tool_scene
    from srd_spell_object_content import _new_scene_object
    from srd_timed_actor_defenses import _call, _parameter, _require, _set, _when
    from srd_timed_senses import _loop, _optional, _push


def furnishing_intent(name="furnishings"):
    """Unprinted furnishing dimensions and appearance are creation fiction, not fit verdicts."""
    return _optional(
        name,
        "array",
        [],
        max_items=20,
        items={
            "type": "object",
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "offset": {
                    "type": "array",
                    "items": {"type": "number"},
                },
                "extent": {
                    "type": "array",
                    "items": {"type": "number"},
                },
                "material": {"type": "string", "minLength": 1},
                "appearance": {"type": "string", "minLength": 1},
            },
            "required": ["id", "offset", "extent", "material", "appearance"],
            "additionalProperties": False,
        },
    )


def furnishing_tools():
    """Derive actual bounds before payment and create only the saved Mansion intent."""
    if __package__:
        from .srd_spell_travel_dwelling import _lookup
    else:
        from srd_spell_travel_dwelling import _lookup

    all_intent = furnishing_intent()
    all_intent["max_items"] = 22
    validate = _tool_scene(
        "Qualify source-created box furnishings against the actual derived Mansion room.",
        [
            all_intent,
            _array("room_min"),
            _array("room_max"),
            _parameter("dwelling_id"),
            _parameter("reserved_ids", "array", items={"type": "string"}),
        ],
        [
            _set("_mf_prior", []),
            _loop(
                "furnishings",
                "_mf_intent",
                [
                    _require(
                        "_mf_intent.id != '' and _mf_intent.material != '' and "
                        "_mf_intent.appearance != ''",
                        "Give each furnishing actual identity, material and appearance intent.",
                    ),
                    _require(
                        "len(_mf_intent.offset) == 3 and len(_mf_intent.extent) == 3",
                        "Use actual three-dimensional furnishing intent.",
                    ),
                    *[
                        _require(
                            f"_mf_intent.extent[{axis}] > 0",
                            "Furnishings need positive physical dimensions.",
                        )
                        for axis in range(3)
                    ],
                    _require(
                        "_mf_intent.id != dwelling_id",
                        "Furnishings cannot replace the source identity.",
                    ),
                    _require(
                        "_mf_intent.id not in reserved_ids",
                        "Furnishings cannot replace source room, doors or walls.",
                    ),
                    *[
                        effect
                        for collection in ("entities", "geometry", "zones")
                        for effect in [
                            _set("_mf_existing", f"state.spatial_scene.{collection}"),
                            _loop(
                                "_mf_existing",
                                "_mf_existing_row",
                                [
                                    _require(
                                        "_mf_existing_row.id != _mf_intent.id",
                                        "A furnishing cannot replace an admitted scene identity.",
                                    )
                                ],
                            ),
                        ]
                    ],
                    *[
                        _require(
                            f"_mf_intent.offset[{axis}] - _mf_intent.extent[{axis}] * 0.5 >= 0 "
                            f"and _mf_intent.offset[{axis}] + _mf_intent.extent[{axis}] * 0.5 "
                            f"<= room_max[{axis}] - room_min[{axis}]",
                            "The full furnishing body must fit inside the actual room.",
                        )
                        for axis in range(2)
                    ],
                    _require(
                        "_mf_intent.offset[2] >= 0 and _mf_intent.offset[2] + "
                        "_mf_intent.extent[2] <= room_max[2] - room_min[2]",
                        "The actual furnishing must fit the room floor and ceiling.",
                    ),
                    _loop(
                        "_mf_prior",
                        "_mf_previous",
                        [
                            _require(
                                "_mf_previous.id != _mf_intent.id",
                                "Furnishing identities must be unique.",
                            ),
                            *[
                                _set(
                                    f"_mf_separated_{axis}",
                                    f"_mf_intent.offset[{axis}] + _mf_intent.extent[{axis}] * 0.5 "
                                    f"<= _mf_previous.offset[{axis}] - "
                                    f"_mf_previous.extent[{axis}] * 0.5 or "
                                    f"_mf_previous.offset[{axis}] + "
                                    f"_mf_previous.extent[{axis}] * 0.5 <= "
                                    f"_mf_intent.offset[{axis}] - "
                                    f"_mf_intent.extent[{axis}] * 0.5",
                                )
                                for axis in range(2)
                            ],
                            _set(
                                "_mf_separated_2",
                                "_mf_intent.offset[2] + _mf_intent.extent[2] "
                                "<= _mf_previous.offset[2] or _mf_previous.offset[2] + "
                                "_mf_previous.extent[2] <= _mf_intent.offset[2]",
                            ),
                            _require(
                                "_mf_separated_0 == true or _mf_separated_1 == true or "
                                "_mf_separated_2 == true",
                                "Actual furnishing bodies cannot overlap.",
                            ),
                        ],
                    ),
                    _push("_mf_prior", "_mf_intent"),
                ],
            ),
        ],
    )
    create = _tool_scene(
        "Create one exact paid Mansion furnishing through the canonical scenery constructor.",
        [_parameter("source_id"), _parameter("object_id")],
        [
            *_lookup(),
            _require(
                "_xd_source.spell == 'magnificent_mansion' and _xd_source.active == true",
                "Furnish only the exact active Mansion source.",
            ),
            _set("_mf_room", None),
            _set("_mf_zones", "state.spatial_scene.zones"),
            _loop(
                "_mf_zones",
                "_mf_zone",
                [_when("_mf_zone.id == _xd_source.room_id", [_set("_mf_room", "_mf_zone")])],
            ),
            _require(
                "_mf_room != null and _mf_room.metadata.active == true and "
                "_mf_room.metadata.source_id == source_id and _mf_room.shape.kind == 'box'",
                "Furnishing creation needs its actual active source room.",
            ),
            _set("_mf_selected", None),
            _set("_mf_saved_intents", "_xd_source.furnishings"),
            _loop(
                "_mf_saved_intents",
                "_mf_saved_intent",
                [
                    _when(
                        "_mf_saved_intent.id == object_id",
                        [
                            _require(
                                "_mf_selected == null",
                                "The exact furnishing intent must be unique.",
                            ),
                            _set("_mf_selected", "_mf_saved_intent"),
                        ],
                    )
                ],
            ),
            _require(
                "_mf_selected != null",
                "Create only furnishing intent retained by this paid source.",
            ),
            _set(
                "_world_origin",
                [f"_mf_room.shape.min[{axis}] + _mf_selected.offset[{axis}]" for axis in range(3)],
            ),
            _set("_mf_extent", "_mf_selected.extent"),
            *_new_scene_object(
                {
                    "plane": "_xd_source.room_id",
                    "kind": "'furnishing'",
                    "material": "_mf_selected.material",
                    "fiction": "_mf_selected.appearance",
                    "source_ref": "'magnificent_mansion'",
                    "magical_effect": True,
                    "spell_school": "'conjuration'",
                    "mansion_creation": {
                        "kind": "'furnishing'",
                        "source_id": "source_id",
                        "cast_id": "_xd_source.cast_id",
                        "created_tick": "_xd_source.created_tick",
                        "room_id": "_xd_source.room_id",
                    },
                },
                identity="object_id",
                extent="_mf_extent",
                movement="'blocking'",
                visibility="'opaque'",
            ),
            _set("state.has_mansion_furnishings", True),
        ],
    )
    departure = _tool_scene(
        "Retire only an authentic source-created furnishing after actual departure as smoke.",
        [_parameter("subject")],
        [
            _set("_mf_body", None),
            _set("_mf_entities", "state.spatial_scene.entities"),
            _loop(
                "_mf_entities",
                "_mf_entity",
                [_when("_mf_entity.id == subject", [_set("_mf_body", "_mf_entity")])],
            ),
            _require(
                "_mf_body != null", "Smoke retirement needs its actual admitted scene identity."
            ),
            _when(
                "_mf_body.active == true and _mf_body.metadata.mansion_creation != null",
                [
                    _set("_mf_creation", "_mf_body.metadata.mansion_creation"),
                    *_lookup("_mf_creation.source_id"),
                    _require(
                        "(_mf_creation.kind == 'furnishing' or _mf_creation.kind == 'banquet') "
                        "and _xd_source.spell == "
                        "'magnificent_mansion' and _mf_creation.cast_id == _xd_source.cast_id "
                        "and _mf_creation.created_tick == _xd_source.created_tick and "
                        "_mf_creation.room_id == _xd_source.room_id",
                        "Smoke retirement needs the exact actual Mansion creation source.",
                    ),
                    _set("_mf_owned", False),
                    _set("_mf_saved_intents", "_xd_source.furnishings"),
                    _when(
                        "_mf_creation.kind == 'banquet'",
                        [_set("_mf_saved_intents", "_xd_source.banquet")],
                    ),
                    _loop(
                        "_mf_saved_intents",
                        "_mf_intent",
                        [_when("_mf_intent.id == _mf_body.id", [_set("_mf_owned", True)])],
                    ),
                    _require(
                        "_mf_owned == true",
                        "The exact paid source must own this furnishing identity.",
                    ),
                    _when(
                        "_mf_body.metadata.plane != _xd_source.room_id",
                        [
                            _set(
                                "_mf_smoke",
                                {
                                    "mansion_smoke": {
                                        "source_id": "_xd_source.id",
                                        "cast_id": "_xd_source.cast_id",
                                        "tick": "state.lasting_tick",
                                        "position": "_mf_body.position",
                                        "plane": "_mf_body.metadata.plane",
                                    }
                                },
                            ),
                            _set("_mf_metadata", "merge(_mf_body.metadata, _mf_smoke)"),
                            *_snapshot(),
                            {
                                "spatial_mutation": {
                                    "scene": "state.spatial_scene",
                                    "map_id": "{state.spatial_scene.map.id}",
                                    "expected_revision": "{_info_snapshot.geometry_revision}",
                                    "target": {"kind": "entity", "id": "{_mf_body.id}"},
                                    "patch": {
                                        "active": False,
                                        "movement": "passable",
                                        "visibility": "clear",
                                        "metadata": "{_mf_metadata}",
                                    },
                                },
                                "store_as": "_mf_dissipated",
                            },
                        ],
                    ),
                ],
            ),
        ],
    )
    return {
        "bind_srd_mansion_reserved_identity": _tool_scene(
            "Bind one source-derived scalar scene identity without unresolved nested templates.",
            [_parameter("identity")],
            [_set("_mf_reserved_id", "identity")],
        ),
        "validate_srd_mansion_furnishings": validate,
        "create_srd_mansion_furnishing": create,
        "retire_srd_departed_mansion_furnishing": departure,
    }


def bind_furnishing_consumers(tools, changed):
    """Observe committed subject movement without adding another position implementation."""
    name = "move_srd_travel_subject"
    original = changed.get(name, tools.get(name))
    if original is None or "retire_srd_departed_mansion_furnishing" in str(original["effects"]):
        return
    if not any(
        "spatial_mutation" in effect or effect.get("call") == "move_srd_travel_subject_node"
        for effect in original["effects"]
    ):
        return
    move = deepcopy(original)
    move["effects"].append(
        _when(
            "state.has_mansion_furnishings == true",
            [_call("retire_srd_departed_mansion_furnishing", {"subject": "{subject}"})],
        )
    )
    changed[name] = move
