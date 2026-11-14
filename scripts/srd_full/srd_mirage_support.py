"""Pure Mirage support transitions leave deferred fall allocation to the action owner."""

from copy import deepcopy

if __package__:
    from .srd_courier_body_movement import BODY_EXPORTS
    from .srd_divination_spells import _normalize
    from .srd_feather_fall import _ground
    from .srd_scene_relations import POSITION_EXPORTS, _snapshot, _tool_scene
    from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from .srd_timed_senses import _loop, _push, _replace_tools
else:
    from srd_courier_body_movement import BODY_EXPORTS
    from srd_divination_spells import _normalize
    from srd_feather_fall import _ground
    from srd_scene_relations import POSITION_EXPORTS, _snapshot, _tool_scene
    from srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from srd_timed_senses import _loop, _push, _replace_tools


def _optional_proof_fields(destination, fields):
    """Keep missing proof values typed null instead of Set's literal-expression fallback."""
    return [
        _when(
            f"{source} != null",
            [
                _set("_support_optional_patch", {field: source}),
                _set(destination, f"merge({destination}, _support_optional_patch)"),
            ],
        )
        for field, source in fields.items()
    ]


def _ground_without_landing_requirement():
    """Read the existing fall ray without inventing ground when its result is empty."""
    effects = deepcopy(_ground())
    return [
        effect
        for effect in effects
        if effect.get("require", {}).get("condition") != "_fall_ground_height != null"
        and effect.get("set", {}).get("field") != "_fall_ground_position"
    ]


def _source(identity="mirage_id"):
    return [
        _require(
            f"state.mirage_sources != null and {identity} in state.mirage_sources",
            "Select the exact saved Mirage source before changing its support.",
        ),
        _set("_support_source", f"state.mirage_sources[{identity}]"),
        _require(
            f"_support_source.id == {identity} and _support_source.spell == 'mirage_arcane'",
            "Support observation needs the actual Mirage source identity.",
        ),
    ]


def _body_tool():
    return _tool_scene(
        "Read an actual body's centerline support through the existing fall ground ray.",
        [_entity("target_state")],
        [
            _call(
                "srd_information_scene_entity", {"subject": "{_target_state_id}"}, POSITION_EXPORTS
            ),
            *_ground_without_landing_requirement(),
            _set("_support_ground", None),
            _loop(
                "_fall_geometry",
                "_support_geometry",
                [
                    _when(
                        "_support_geometry.id == _fall_ground_id",
                        [
                            _set("_support_ground", "_support_geometry"),
                        ],
                    )
                ],
            ),
            _set(
                "_support_body",
                {
                    "actor_id": "_target_state_id",
                    "scene_id": "_info_scene_entity.id",
                    "origin": "_info_position",
                    "plane": "_info_scene_entity.metadata.plane",
                    "footprint": "_info_scene_entity.footprint",
                    "height": "_info_scene_entity.height",
                },
            ),
            _set("_support_height", None),
            _when("_fall_ground_height != null", [_set("_support_height", "_fall_ground_height")]),
        ],
    )


def _capture_tool():
    return _tool_scene(
        "Capture a body standing on exact currently projected or created Mirage support.",
        [_entity("target_state"), _parameter("mirage_id")],
        _source()
        + [
            _set("_mirage_support_receipt", None),
            _when(
                "_support_source.installed == true and target_state.is_alive == true",
                [
                    _call(
                        "query_srd_mirage_body_support",
                        {"target_state": "{_target_state_id}"},
                        ["_support_ground", "_support_height", "_support_body"],
                    ),
                    _set("_support_owned", False),
                    _when(
                        "_support_ground != null",
                        [
                            _set(
                                "_support_owned",
                                "(_support_ground.metadata.mirage_source_id == mirage_id and "
                                "_support_ground.id in _support_source.piece_ids) or "
                                "(_support_ground.metadata.mirage_projection_id == mirage_id and "
                                "_support_source.terrain_subject_ids != null and "
                                "_support_ground.id in _support_source.terrain_subject_ids)",
                            ),
                        ],
                    ),
                    _when(
                        "_support_owned == true and _support_body.plane == _support_source.plane "
                        "and abs(_support_body.origin[2] - _support_height) <= 0.000001",
                        [
                            _set(
                                "_mirage_support_receipt",
                                {
                                    "mirage_id": "mirage_id",
                                    "caster": "_support_source.caster",
                                    "source_started_tick": "_support_source.started_tick",
                                    "source_expires_tick": "_support_source.expires_tick",
                                    "source_cast_id": "_support_source.cast_id",
                                    "map_id": "state.spatial_scene.map.id",
                                    "captured_revision": "state.spatial_scene.map.revision",
                                    "captured_tick": "state.lasting_tick",
                                    "body": "_support_body",
                                    "ground_height": "_support_height",
                                    "geometry": "_support_ground",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _flight_tool():
    """Flight requires actual prior aloft/cloud evidence and a currently qualified source."""
    return _tool_scene(
        "Qualify current source-owned flight and exact existing descent without settling either.",
        [_entity("target_state")],
        [
            _set("_support_flight", False),
            _set("_support_magic_flight", 0),
            _set("_support_active_flight_sources", []),
            _set("_support_flight_records", []),
            _when(
                "state.turn_effects != null",
                [
                    _set("_support_flight_records", "state.turn_effects"),
                ],
            ),
            _loop(
                "_support_flight_records",
                "_support_flight_record",
                [
                    _when(
                        "_support_flight_record.kind == 'actor_mobility' and "
                        "_support_flight_record.target == _target_state_id and "
                        "_support_flight_record.mobility == 'flight' and "
                        "(_support_flight_record.started_tick == null or "
                        "_support_flight_record.started_tick <= state.lasting_tick) and "
                        "state.lasting_tick < _support_flight_record.expires_tick",
                        [
                            _set("_support_flight_record_active", True),
                            _when(
                                "_support_flight_record.concentration == true",
                                [
                                    _set("_support_flight_cast_id", "''"),
                                    _when(
                                        "_support_flight_record.cast_id != null",
                                        [
                                            _set(
                                                "_support_flight_cast_id",
                                                "_support_flight_record.cast_id",
                                            ),
                                        ],
                                    ),
                                    _call(
                                        "qualify_srd_mirage_magic_flight_source",
                                        {
                                            "caster_state": "{_support_flight_record.caster}",
                                            "spell": "{_support_flight_record.spell}",
                                            "cast_id": "{_support_flight_cast_id}",
                                        },
                                        ["_support_flight_record_active"],
                                    ),
                                ],
                            ),
                            _when(
                                "_support_flight_record_active == true",
                                [
                                    _push(
                                        "_support_active_flight_sources", "_support_flight_record"
                                    ),
                                    _set(
                                        "_support_magic_flight",
                                        "max(_support_magic_flight, "
                                        "_support_flight_record.magnitude)",
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _call(
                "project_srd_body_movement_at_tick",
                {
                    "target_state": "{_target_state_id}",
                    "at_tick": "{state.lasting_tick}",
                    "interval_end": "{state.lasting_tick}",
                },
                BODY_EXPORTS,
            ),
            _set("_support_native_flying_speed", 0),
            _call(
                "project_srd_animal_messenger_pace",
                {
                    "target_state": "{_target_state_id}",
                    "at_tick": "{state.lasting_tick}",
                    "interval_end": "{state.lasting_tick}",
                },
                ["_courier_mode", "_movement_speed"],
            ),
            _when(
                "_courier_mode == 'fly' and _movement_speed != null",
                [
                    _set("_support_native_flying_speed", "_movement_speed"),
                ],
            ),
            _set("_support_native_flight_stopped", "target_state.timed_control_speed_zero == true"),
            _set(
                "_support_flight_stopping_conditions",
                [
                    "'prone'",
                    "'grappled'",
                    "'restrained'",
                    "'paralyzed'",
                    "'petrified'",
                    "'stunned'",
                    "'unconscious'",
                ],
            ),
            _loop(
                "_support_flight_stopping_conditions",
                "_support_flight_condition",
                [
                    _when(
                        "_support_flight_condition in _body_conditions",
                        [
                            _set("_support_native_flight_stopped", True),
                        ],
                    ),
                ],
            ),
            _set(
                "_support_flight",
                "target_state.spell_flight_fall_pending != true and "
                "(target_state.flying_aloft == true or _body_cloud == true) and "
                "(_support_magic_flight > 0 or (_body_fly_speed != null and _body_fly_speed > 0 "
                "and "
                "(_body_can_hover == true or (_support_native_flight_stopped != true "
                "and _support_native_flying_speed > 0))))",
            ),
            _set(
                "_support_scene_descent",
                "target_state.scene_fall != null and (target_state.scene_fall.status == 'pending' "
                "or target_state.scene_fall.status == 'descending')",
            ),
            _set("_support_wind_descent", False),
            _when(
                "target_state.wind_walk_descent != null and "
                "target_state.wind_walk_descent.status == 'pending' and "
                "state.transformation_sources != null and "
                "target_state.wind_walk_descent.source_id in state.transformation_sources",
                [
                    _set(
                        "_support_wind_source",
                        "state.transformation_sources[target_state.wind_walk_descent.source_id]",
                    ),
                    _set(
                        "_support_wind_identity",
                        "_support_wind_source.spell_id == 'wind_walk' and "
                        "_support_wind_source.target_state_id == _target_state_id and "
                        "_support_wind_source.serial == "
                        "target_state.wind_walk_descent.source_serial "
                        "and target_state.wind_walk_descent.end_tick == "
                        "target_state.wind_walk_descent.started_tick + 20",
                    ),
                    _set(
                        "_support_wind_window",
                        "target_state.wind_walk_descent.started_tick <= state.lasting_tick and "
                        "state.lasting_tick < target_state.wind_walk_descent.end_tick and "
                        "(_support_wind_source.installed != true or "
                        "state.lasting_tick >= _support_wind_source.expires_tick)",
                    ),
                    _set(
                        "_support_wind_descent", "_support_wind_identity and _support_wind_window"
                    ),
                ],
            ),
            _set(
                "_support_qualifiers",
                {
                    "flight_supported": "_support_flight",
                    "flying_aloft": "target_state.flying_aloft == true",
                    "body_cloud": "_body_cloud",
                    "body_fly_speed": None,
                    "effective_fly_speed": "_support_native_flying_speed",
                    "body_source_id": None,
                    "body_source_serial": "_body_source_serial",
                    "magic_flight_sources": "_support_active_flight_sources",
                    "scene_descent": "_support_scene_descent",
                    "scene_fall": None,
                    "wind_descent": "_support_wind_descent",
                    "wind_receipt": None,
                },
            ),
            *_optional_proof_fields(
                "_support_qualifiers",
                {
                    "body_fly_speed": "_body_fly_speed",
                    "body_source_id": "_body_source_id",
                    "scene_fall": "target_state.scene_fall",
                    "wind_receipt": "target_state.wind_walk_descent",
                },
            ),
        ],
    )


def _observe_tool():
    receipt = _parameter("receipt", "object")
    return _tool_scene(
        "Recheck a private checked support receipt after actual geometry change; allocate nothing.",
        [_entity("target_state"), receipt],
        [
            _require("receipt.body.actor_id == _target_state_id", "Support receipt actor differs."),
            *_source("receipt.mirage_id"),
            _require(
                "receipt.geometry.active == true and receipt.geometry.movement == 'blocking' "
                "and receipt.geometry.metadata.ground == true and "
                "receipt.geometry.metadata.plane == receipt.body.plane and "
                "abs(receipt.body.origin[2] - receipt.ground_height) <= 0.000001",
                "Support receipt must retain actual standing-body and supporting-geometry "
                "evidence.",
            ),
            _set(
                "_support_receipt_owned",
                "(receipt.geometry.metadata.mirage_source_id == receipt.mirage_id and "
                "receipt.geometry.id in _support_source.piece_ids) or "
                "(receipt.geometry.metadata.mirage_projection_id == receipt.mirage_id and "
                "_support_source.terrain_subject_ids != null and "
                "receipt.geometry.id in _support_source.terrain_subject_ids)",
            ),
            _require(
                "_support_receipt_owned == true", "Support geometry belongs to another source."
            ),
            _require(
                "_support_source.caster == receipt.caster and "
                "_support_source.started_tick == receipt.source_started_tick and "
                "_support_source.expires_tick == receipt.source_expires_tick and "
                "_support_source.cast_id == receipt.source_cast_id",
                "The exact captured support source has been replaced.",
            ),
            _require(
                "receipt.map_id == state.spatial_scene.map.id and "
                "state.spatial_scene.map.revision >= receipt.captured_revision and "
                "state.lasting_tick >= receipt.captured_tick",
                "Support receipts cannot cross maps or rewind their actual scene and clock.",
            ),
            _set("_mirage_support_status", "'not_alive'"),
            _set("_mirage_support_falling_event", None),
            _set("_support_after", None),
            _set("_support_qualifiers", None),
            _set("_support_clearance", None),
            _when(
                "target_state.is_alive == true",
                [
                    _call(
                        "query_srd_mirage_body_support",
                        {"target_state": "{_target_state_id}"},
                        ["_support_ground", "_support_height", "_support_body"],
                    ),
                    _set(
                        "_support_after",
                        {
                            "body": "_support_body",
                            "geometry": None,
                            "ground_height": None,
                        },
                    ),
                    *_optional_proof_fields(
                        "_support_after",
                        {
                            "geometry": "_support_ground",
                            "ground_height": "_support_height",
                        },
                    ),
                    _set("_mirage_support_status", "'body_changed'"),
                    _set(
                        "_support_body_unchanged",
                        "_support_body.actor_id == receipt.body.actor_id and "
                        "_support_body.scene_id == receipt.body.scene_id and "
                        "_support_body.plane == receipt.body.plane and "
                        "_support_body.height == receipt.body.height and "
                        "_support_body.footprint.kind == receipt.body.footprint.kind and "
                        + " and ".join(
                            f"_support_body.origin[{i}] == receipt.body.origin[{i}]"
                            for i in range(3)
                        ),
                    ),
                    _when(
                        "_support_body.footprint.kind == 'box'",
                        [
                            _set(
                                "_support_body_unchanged",
                                "_support_body_unchanged and "
                                "_support_body.footprint.half_extents[0] == "
                                "receipt.body.footprint.half_extents[0] "
                                "and _support_body.footprint.half_extents[1] == "
                                "receipt.body.footprint.half_extents[1]",
                            ),
                        ],
                    ),
                    _when(
                        "_support_body.footprint.kind == 'circle'",
                        [
                            _set(
                                "_support_body_unchanged",
                                "_support_body_unchanged and "
                                "_support_body.footprint.radius == receipt.body.footprint.radius",
                            ),
                        ],
                    ),
                    _when(
                        "_support_body_unchanged == true",
                        [
                            _set("_mirage_support_status", "'supported'"),
                            _when(
                                "_support_height == null or "
                                "_support_height < _support_body.origin[2] - 0.000001",
                                [
                                    _call(
                                        "qualify_srd_mirage_support_flight",
                                        {
                                            "target_state": "{_target_state_id}",
                                        },
                                        [
                                            "_support_flight",
                                            "_support_scene_descent",
                                            "_support_wind_descent",
                                            "_support_qualifiers",
                                        ],
                                    ),
                                    _set("_mirage_support_status", "'missing_landing_ground'"),
                                    _when(
                                        "_support_flight == true",
                                        [
                                            _set("_mirage_support_status", "'flight_supported'"),
                                        ],
                                    ),
                                    _when(
                                        "_support_scene_descent == true",
                                        [
                                            _set(
                                                "_mirage_support_status", "'existing_scene_descent'"
                                            ),
                                        ],
                                    ),
                                    _when(
                                        "_support_wind_descent == true and "
                                        "_support_scene_descent != true",
                                        [
                                            _set(
                                                "_mirage_support_status", "'existing_wind_descent'"
                                            ),
                                        ],
                                    ),
                                    _when(
                                        "_support_height != null and _support_flight != true and "
                                        "_support_scene_descent != true and "
                                        "_support_wind_descent != true",
                                        [
                                            _set(
                                                "_support_material_plane",
                                                "_support_body.plane == 'material'",
                                            ),
                                            _set(
                                                "_support_destination",
                                                [
                                                    "_support_body.origin[0]",
                                                    "_support_body.origin[1]",
                                                    "_support_height",
                                                ],
                                            ),
                                            {
                                                "spatial_query": {
                                                    "scene": "state.spatial_scene",
                                                    "operation": "sweep",
                                                    "query": {
                                                        "map_id": "{state.spatial_scene.map.id}",
                                                        "moving_entity_id": (
                                                            "{_support_body.scene_id}"
                                                        ),
                                                        "destination": "{_support_destination}",
                                                        "include_entities": False,
                                                        "metadata_filter": {
                                                            "key": "plane",
                                                            "value": "{_support_body.plane}",
                                                            "missing_matches": (
                                                                "{_support_material_plane}"
                                                            ),
                                                        },
                                                    },
                                                },
                                                "store_as": "_support_clearance",
                                            },
                                            _set("_mirage_support_status", "'unsupported_descent'"),
                                            _when(
                                                "_support_clearance.supported == true and "
                                                "_support_clearance.clear == true",
                                                [
                                                    _call(
                                                        "qualify_srd_scene_fall_target",
                                                        {
                                                            "target_state": "{_target_state_id}",
                                                            "fall_cause": "support_lost",
                                                        },
                                                        ["_falling_event"],
                                                    ),
                                                    _set(
                                                        "_mirage_support_falling_event",
                                                        "_falling_event",
                                                    ),
                                                    _set("_mirage_support_status", "'falling'"),
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
            _set(
                "_mirage_support_result",
                {
                    "receipt": "receipt",
                    "after": None,
                    "status": "_mirage_support_status",
                    "falling_event": None,
                    "qualifiers": None,
                    "clearance": None,
                },
            ),
            *_optional_proof_fields(
                "_mirage_support_result",
                {
                    "after": "_support_after",
                    "falling_event": "_mirage_support_falling_event",
                    "qualifiers": "_support_qualifiers",
                    "clearance": "_support_clearance",
                },
            ),
        ],
    )


def _collection_tools():
    capture = _tool_scene(
        "Capture actual scene participants supported by this exact Mirage source.",
        [_parameter("mirage_id")],
        _source()
        + _snapshot()
        + [
            _set("_mirage_support_receipts", []),
            _when(
                "_support_source.installed == true",
                [
                    _set("_support_scene_actors", "state.spatial_scene.entities"),
                    _loop(
                        "_support_scene_actors",
                        "_support_actor",
                        [
                            _when(
                                "_support_actor.active == true and "
                                "_support_actor.metadata.participant_ref != null and "
                                "_support_actor.metadata.plane == _support_source.plane and "
                                "_support_actor.position[0] >= _support_source.corner[0] and "
                                "_support_actor.position[0] <= _support_source.corner[0] + "
                                "_support_source.side and "
                                "_support_actor.position[1] >= _support_source.corner[1] and "
                                "_support_actor.position[1] <= _support_source.corner[1] + "
                                "_support_source.side",
                                [
                                    _call(
                                        "capture_srd_mirage_support_target",
                                        {
                                            "target_state": (
                                                "{_support_actor.metadata.participant_ref}"
                                            ),
                                            "mirage_id": "{mirage_id}",
                                        },
                                        ["_mirage_support_receipt"],
                                    ),
                                    _when(
                                        "_mirage_support_receipt != null",
                                        [
                                            _require(
                                                "len(_mirage_support_receipts) < 64",
                                                "Simultaneous support loss exceeds the saved "
                                                "fall target bound.",
                                            ),
                                            _push(
                                                "_mirage_support_receipts",
                                                "_mirage_support_receipt",
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
    observe = _tool_scene(
        "Return checked fall events for deferred completed-action allocation, without state "
        "writes.",
        [_parameter("receipts", "array", items={"type": "object"}, max_items=64)],
        [
            _set("_mirage_support_results", []),
            _set("_falling_events", []),
            _set("_support_seen_actors", []),
            _set("_support_receipt_rows", "receipts"),
            _loop(
                "_support_receipt_rows",
                "_support_receipt",
                [
                    _require(
                        "_support_receipt.body.actor_id not in _support_seen_actors",
                        "A support transition cannot select one actor twice.",
                    ),
                    _push("_support_seen_actors", "_support_receipt.body.actor_id"),
                    _call(
                        "observe_srd_mirage_support_target",
                        {
                            "target_state": "{_support_receipt.body.actor_id}",
                            "receipt": "{_support_receipt}",
                        },
                        ["_mirage_support_result", "_mirage_support_falling_event"],
                    ),
                    _push("_mirage_support_results", "_mirage_support_result"),
                    _when(
                        "_mirage_support_falling_event != null",
                        [
                            _push("_falling_events", "_mirage_support_falling_event"),
                        ],
                    ),
                ],
            ),
        ],
    )
    return {"capture_srd_mirage_support": capture, "observe_srd_mirage_support_change": observe}


def extend_runtime(text):
    """Emit only pure support leaves; Root owns persistence and completed-action allocation."""
    changed = {
        "query_srd_mirage_body_support": _body_tool(),
        "capture_srd_mirage_support_target": _capture_tool(),
        "qualify_srd_mirage_support_flight": _flight_tool(),
        "qualify_srd_mirage_magic_flight_source": _tool_scene(
            "Qualify the exact saved cast when present, through the existing mobility source "
            "owner.",
            [_entity("caster_state"), _parameter("spell"), _parameter("cast_id")],
            [
                _call(
                    "check_srd_timed_actor_mobility_source",
                    {
                        "caster_state": "{_caster_state_id}",
                        "spell": "{spell}",
                    },
                    ["_mobility_source_active"],
                ),
                _set("_support_flight_record_active", "_mobility_source_active"),
                _when(
                    "cast_id != ''",
                    [
                        _set(
                            "_support_flight_record_active",
                            "_support_flight_record_active and "
                            "caster_state.concentration_cast_id == cast_id",
                        ),
                    ],
                ),
            ],
        ),
        "observe_srd_mirage_support_target": _observe_tool(),
        **_collection_tools(),
    }
    _normalize(changed)
    return _replace_tools(text, changed)
