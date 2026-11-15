"""Physical rope interaction and one-body drop receipts preserve the sole scene mover."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import _array, _snapshot, _tool_scene
    from .srd_spell_object_content import _metadata_patch
    from .srd_spell_travel_dwelling import _lookup, _save, dwelling_tools
    from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from .srd_timed_senses import _loop, _public
else:
    from srd_scene_relations import _array, _snapshot, _tool_scene
    from srd_spell_object_content import _metadata_patch
    from srd_spell_travel_dwelling import _lookup, _save, dwelling_tools
    from srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from srd_timed_senses import _loop, _public

PUBLIC_TOOLS = {"manipulate_srd_rope_trick_rope"}


def _shape(height):
    """Resize the source's real rope body through the canonical geometry mutation."""
    return [
        *_snapshot(),
        {
            "spatial_mutation": {
                "scene": "state.spatial_scene",
                "map_id": "{state.spatial_scene.map.id}",
                "expected_revision": "{_info_snapshot.geometry_revision}",
                "target": {"kind": "entity", "id": "{_xd_source.rope_id}"},
                "patch": {"height": height},
            },
            "store_as": "_rt_rope_shape_mutation",
        },
    ]


def rope_state_tools(tools):
    """Prepare exact physical endpoints; genuine fall windows belong to the outer owner."""
    if __package__:
        from .srd_spell_travel import ACTIVE, _measure, _move, _placement, _scene_subject
    else:
        from srd_spell_travel import ACTIVE, _measure, _move, _placement, _scene_subject

    manipulation = _public(
        _tool_scene(
            "Pull this source's actual rope into its space, or drop it outside again.",
            [
                _entity("actor_state"),
                _parameter("source_id"),
                _parameter("operation", "enum", enum_values=["pull", "drop"]),
            ],
            [
                _require(ACTIVE, "A capable living creature must manipulate this physical rope."),
                _call("refresh_srd_extradimensional_sources", {}),
                *_lookup(),
                _require(
                    "_xd_source.spell == 'rope_trick' and _xd_source.active == true",
                    "Select the actual active Rope Trick source.",
                ),
                _when(
                    "actor_state.has_transformations == true",
                    [
                        _call(
                            "project_srd_transformation",
                            {
                                "actor_state": "{_actor_state_id}",
                                "lasting_state": "state",
                            },
                        ),
                    ],
                ),
                _require("actor_state.form_cloud != true", "A cloud cannot manipulate a rope."),
                *_scene_subject("{_actor_state_id}", "_rt_handler"),
                *_scene_subject("{_xd_source.inner_id}", "_rt_inner"),
                *_scene_subject("{_xd_source.rope_id}", "_rt_rope_body"),
                _require(
                    "_rt_handler.metadata.plane == _xd_source.room_id and "
                    "_rt_inner.metadata.plane == _xd_source.room_id and "
                    "_rt_rope_body.metadata.rope_trick_source_id == source_id",
                    "Reach the actual inner opening and this source's real rope.",
                ),
                *_measure("{_rt_handler.position}", "{_rt_inner.position}", "_rt_rope_reach"),
                _require("_rt_rope_reach <= 5", "Touch the actual rope at the inner opening."),
                *[
                    _call(
                        "check_srd_rope_trick_magic",
                        {
                            "subject": subject,
                            "slot_level": "{_xd_source.slot_level}",
                        },
                    )
                    for subject in (
                        "{_rt_handler.id}",
                        "{_xd_source.inner_id}",
                        "{_xd_source.outer_id}",
                    )
                ],
                _require(
                    "(operation == 'pull' and _xd_source.rope_pulled != true and "
                    "_rt_rope_body.metadata.plane == _xd_source.origin_plane) or "
                    "(operation == 'drop' and _xd_source.rope_pulled == true and "
                    "_rt_rope_body.metadata.plane == _xd_source.room_id)",
                    "Use the actual rope's current inside or outside state.",
                ),
                _require(
                    "_rt_rope_body.footprint.kind == 'box' and "
                    "_rt_rope_body.footprint.half_extents[0] == "
                    "_xd_source.rope_initial_footprint.half_extents[0] and "
                    "_rt_rope_body.footprint.half_extents[1] == "
                    "_xd_source.rope_initial_footprint.half_extents[1]",
                    "An independently changed rope body needs its actual shape owner.",
                ),
                _when(
                    "operation == 'pull'",
                    [
                        _require(
                            "_rt_rope_body.height == _xd_source.hung_height and "
                            + " and ".join(
                                f"_rt_rope_body.position[{i}] == _xd_source.rope_lower[{i}]"
                                for i in range(3)
                            ),
                            "The actual hanging rope has moved or changed independently.",
                        ),
                    ],
                ),
                _when(
                    "operation == 'drop'",
                    [
                        *_measure(
                            "{_rt_handler.position}",
                            "{_rt_rope_body.position}",
                            "_rt_actual_rope_reach",
                        ),
                        _require(
                            "_rt_actual_rope_reach <= 5",
                            "Reach the actual pulled rope before dropping it.",
                        ),
                    ],
                ),
                _set("_travel_excluded", []),
                _when(
                    "operation == 'pull'",
                    [
                        *_shape("{_xd_source.rope_initial_height}"),
                        *_placement(
                            "{_xd_source.rope_id}", "{_rt_inner.position}", "{_xd_source.room_id}"
                        ),
                        *_move(
                            "{_xd_source.rope_id}",
                            "{_travel_placement.position}",
                            "{_xd_source.room_id}",
                            False,
                        ),
                        _set("_rt_rope_patch", {"rope_pulled": True}),
                    ],
                ),
                _when(
                    "operation == 'drop'",
                    [
                        *_shape("{_xd_source.hung_height}"),
                        *_placement(
                            "{_xd_source.rope_id}",
                            "{_xd_source.rope_lower}",
                            "{_xd_source.origin_plane}",
                        ),
                        _require(
                            "_travel_placement.distance == 0",
                            "This actual hanging rope's lower space is occupied.",
                        ),
                        *_move(
                            "{_xd_source.rope_id}",
                            "{_xd_source.rope_lower}",
                            "{_xd_source.origin_plane}",
                            False,
                        ),
                        _set("_rt_rope_patch", {"rope_pulled": False}),
                    ],
                ),
                *_lookup(),
                _set("_xd_source", "merge(_xd_source, _rt_rope_patch)"),
                *_save(),
                _set("_rope_trick_rope_operation", "operation"),
            ],
        ),
        "exploration",
        "Rope Trick rope",
        [("Operation", "_rope_trick_rope_operation")],
    )
    prepare = _tool_scene(
        "Derive one queued living occupant's actual portal-fitting downward endpoint.",
        [_parameter("source_id"), _entity("target_state")],
        [
            *_lookup(),
            _require(
                "_xd_source.spell == 'rope_trick' and _xd_source.active == false and "
                "_xd_source.return_pending == true",
                "Use this exact retired Rope Trick's queued occupant.",
            ),
            _require(
                "target_state.is_alive == true and target_state.hp > 0",
                "Dead-creature downward consequences need their actual fall owner.",
            ),
            *_scene_subject("{_target_state_id}", "_rt_return_body"),
            *_scene_subject("{_xd_source.outer_id}", "_rt_return_portal"),
            _require(
                "_rt_return_body.active == true and "
                "_rt_return_body.metadata.participant_ref == _target_state_id and "
                "_rt_return_body.metadata.plane == _xd_source.room_id and "
                "_rt_return_portal.metadata.plane == _xd_source.origin_plane and "
                "_rt_return_body.footprint.kind == 'box'",
                "Use the actual supported body still inside this source's room.",
            ),
            _set(
                "_rt_drop_origin",
                [
                    "_rt_return_portal.position[0]",
                    "_rt_return_portal.position[1]",
                    "_rt_return_portal.position[2] - _rt_return_body.height",
                ],
            ),
            _set("_travel_excluded", []),
            *_placement("{_rt_return_body.id}", "{_rt_drop_origin}", "{_xd_source.origin_plane}"),
            _require(
                "abs(_travel_placement.position[2] + _rt_return_body.height - "
                "_rt_return_portal.position[2]) <= 0.0000001",
                "The actual blocked drop needs a supported portal endpoint before falling.",
            ),
            *[
                _require(
                    f"abs(_travel_placement.position[{axis}] - "
                    f"_rt_return_portal.position[{axis}]) + "
                    f"_rt_return_body.footprint.half_extents[{axis}] <= {limit}",
                    "The actual drop body must fit the source's horizontal opening.",
                )
                for axis, limit in ((0, 1.5), (1, 2.5))
            ],
            _set(
                "state.rope_trick_return_receipt",
                {
                    "kind": "'rope_trick_return'",
                    "source_id": "_xd_source.id",
                    "created_tick": "_xd_source.created_tick",
                    "cast_id": "_xd_source.cast_id",
                    "subject": "_rt_return_body.id",
                    "body_id": "_rt_return_body.id",
                    "creature_id": "_target_state_id",
                    "origin": "_rt_return_body.position",
                    "origin_plane": "_xd_source.room_id",
                    "footprint": "_rt_return_body.footprint",
                    "height": "_rt_return_body.height",
                    "outer_id": "_xd_source.outer_id",
                    "destination": "_travel_placement.position",
                    "plane": "_xd_source.origin_plane",
                    "consumed": False,
                },
            ),
        ],
    )
    returned = _tool_scene(
        "Commit this actual prepared Rope Trick endpoint through the sole scene mover.",
        [_parameter("subject"), _array("destination"), _parameter("plane")],
        [
            _set("_rt_receipt", "state.rope_trick_return_receipt"),
            _require(
                "_rt_receipt != null and _rt_receipt.kind == 'rope_trick_return' and "
                "_rt_receipt.consumed != true and _rt_receipt.subject == subject and "
                "_rt_receipt.plane == plane",
                "Only this actual queued source endpoint may return the selected creature.",
            ),
            *[
                _require(
                    f"destination[{i}] == _rt_receipt.destination[{i}]",
                    "Keep the actual source-derived drop endpoint.",
                )
                for i in range(3)
            ],
            *_lookup("_rt_receipt.source_id"),
            _require(
                "_xd_source.spell == 'rope_trick' and _xd_source.active == false and "
                "_xd_source.return_pending == true and "
                "_xd_source.created_tick == _rt_receipt.created_tick and "
                "_xd_source.cast_id == _rt_receipt.cast_id",
                "The same exact source must still own its prepared return.",
            ),
            *_scene_subject("{subject}", "_rt_current_return_body"),
            _require(
                "_rt_current_return_body.metadata.plane == _xd_source.room_id",
                "The actual return body must still occupy the retained room.",
            ),
            *[
                _require(
                    f"_rt_current_return_body.position[{i}] == _rt_receipt.origin[{i}]",
                    "The actual body moved after its queued endpoint was prepared.",
                )
                for i in range(3)
            ],
            *_move("{subject}", "{destination}", "{plane}", False),
            _set("_rt_consumed", {"consumed": True}),
            _set("state.rope_trick_return_receipt", "merge(_rt_receipt, _rt_consumed)"),
        ],
    )
    # Physical room retirement is the unchanged canonical tail of the same constructor owner.
    mansion_settle = dwelling_tools(tools)["settle_srd_extradimensional_returns"]
    source_branch = mansion_settle["effects"][1]["on_true"][1]["for_each"]["do"][0]["on_true"]
    cleanup_start = next(
        i for i, effect in enumerate(source_branch) if effect.get("spatial_mutation")
    )
    cleanup = deepcopy(source_branch[cleanup_start - 1 :])
    restoration = _tool_scene(
        "Remove the exact outside rope's source shape before canonical room retirement.",
        [_parameter("source_id"), _parameter("object_id")],
        [
            *_lookup(),
            *_scene_subject("{object_id}", "_object_scene"),
            _require(
                "_xd_source.rope_id == object_id and _xd_source.rope_pulled != true and "
                "_object_scene.metadata.plane == _xd_source.origin_plane and "
                "_object_scene.metadata.rope_trick_source_id == source_id",
                "The actual outside rope must retain this exact source projection.",
            ),
            _require(
                "_object_scene.height == _xd_source.hung_height and "
                + " and ".join(
                    f"_object_scene.position[{i}] == _xd_source.rope_lower[{i}]" for i in range(3)
                ),
                "An independently moved or reshaped rope needs its physical end owner.",
            ),
            *_shape("{_xd_source.rope_initial_height}"),
            *_metadata_patch({"rope_trick_source_id": None}),
        ],
    )
    settle = _tool_scene(
        "Commit at most one actual queued Rope drop before any outer fall opportunity.",
        [],
        [
            _require(
                "state.pending_srd_action_stack == null or "
                "len(state.pending_srd_action_stack) == 0",
                "Complete the actual pending window before another Rope drop.",
            ),
            _set("_rt_one_drop", False),
            _set("_rt_queued_sources", []),
            _when(
                "state.extradimensional_sources != null",
                [
                    _set("_rt_queued_sources", "state.extradimensional_sources"),
                ],
            ),
            _loop(
                "_rt_queued_sources",
                "_rt_queued_source",
                [
                    _when(
                        "_rt_queued_source.spell == 'rope_trick' and "
                        "_rt_queued_source.return_pending == true and _rt_one_drop != true",
                        [
                            *_lookup("_rt_queued_source.id"),
                            _set("_rt_drop_bodies", "state.spatial_scene.entities"),
                            _loop(
                                "_rt_drop_bodies",
                                "_rt_drop_body",
                                [
                                    _when(
                                        "_rt_one_drop != true and _rt_drop_body.active == true and "
                                        "_rt_drop_body.metadata.plane == _xd_source.room_id and "
                                        "_rt_drop_body.metadata.participant_ref != null",
                                        [
                                            _call(
                                                "prepare_srd_rope_trick_return",
                                                {
                                                    "source_id": "{_xd_source.id}",
                                                    "target_state": (
                                                        "{_rt_drop_body."
                                                        "metadata.participant_ref}"
                                                    ),
                                                },
                                            ),
                                            _call(
                                                "return_srd_rope_trick_subject",
                                                {
                                                    "subject": (
                                                        "{state.rope_trick_"
                                                        "return_receipt.subject}"
                                                    ),
                                                    "destination": (
                                                        "{state.rope_trick_"
                                                        "return_receipt.destination}"
                                                    ),
                                                    "plane": (
                                                        "{state.rope_trick_"
                                                        "return_receipt.plane}"
                                                    ),
                                                },
                                            ),
                                            _set("_rt_one_drop", True),
                                            _set("state.rope_trick_return_receipt", None),
                                        ],
                                    ),
                                ],
                            ),
                            *_lookup("_rt_queued_source.id"),
                            _set("_rt_remaining", 0),
                            _set("_rt_remaining_bodies", "state.spatial_scene.entities"),
                            _loop(
                                "_rt_remaining_bodies",
                                "_rt_remaining_body",
                                [
                                    _when(
                                        "_rt_remaining_body.active == true and "
                                            "_rt_remaining_body.metadata.plane == "
                                            "_xd_source.room_id and "
                                        "_rt_remaining_body.id != _xd_source.inner_id",
                                        [
                                            _set("_rt_remaining", "_rt_remaining + 1"),
                                        ],
                                    ),
                                ],
                            ),
                            _when(
                                "_rt_remaining == 0 and _xd_source.rope_pulled != true",
                                [
                                    _call(
                                        "restore_srd_rope_trick_rope",
                                        {
                                            "source_id": "{_xd_source.id}",
                                            "object_id": "{_xd_source.rope_id}",
                                        },
                                    ),
                                    *cleanup,
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    return {
        "manipulate_srd_rope_trick_rope": manipulation,
        "prepare_srd_rope_trick_return": prepare,
        "return_srd_rope_trick_subject": returned,
        "restore_srd_rope_trick_rope": restoration,
        "settle_srd_rope_trick_returns": settle,
    }


def _pending_projection():
    """One family must never clear another retained source's earned return queue."""
    return [
        _set("_rt_any_return_pending", False),
        _set("_rt_all_retained_sources", []),
        _when(
            "state.extradimensional_sources != null",
            [
                _set("_rt_all_retained_sources", "state.extradimensional_sources"),
            ],
        ),
        _loop(
            "_rt_all_retained_sources",
            "_rt_retained_source",
            [
                _when(
                    "_rt_retained_source.return_pending == true",
                    [
                        _set("_rt_any_return_pending", True),
                    ],
                ),
            ],
        ),
        _set("state.has_extradimensional_pending_returns", "_rt_any_return_pending"),
    ]


def bind_rope_return_consumers(tools, changed):
    """Keep Mansion receivers exact and dispatch Rope's distinct one-body source endpoint."""
    name = "settle_srd_extradimensional_returns"
    tool = changed.get(name, tools.get(name))
    if tool is None:
        return
    tool = deepcopy(tool)

    def amend(value):
        if isinstance(value, dict):
            if value.get("condition") == "_xd_return_source.return_pending == true":
                value["condition"] += " and _xd_return_source.spell == 'magnificent_mansion'"
            for child in value.values():
                amend(child)
        elif isinstance(value, list):
            for index in range(len(value) - 1, -1, -1):
                effect = value[index]
                if (
                    isinstance(effect, dict)
                    and effect.get("set", {}).get("field")
                    in {
                        "state.has_extradimensional_pending_returns",
                        "lasting_state.has_extradimensional_pending_returns",
                    }
                    and effect["set"].get("value") is False
                ):
                    value[index : index + 1] = _pending_projection()
                else:
                    amend(effect)

    amend(tool["effects"])
    if "settle_srd_rope_trick_returns" not in str(tool["effects"]):
        tool["effects"].append(
            _when(
                "state.has_extradimensional_pending_returns == true and "
                "(state.pending_srd_action_stack == null or "
                "len(state.pending_srd_action_stack) == 0)",
                [_call("settle_srd_rope_trick_returns", {}), *_pending_projection()],
            )
        )
    normalized = _tool_scene(tool["description"], deepcopy(tool["parameters"]), tool["effects"])
    tool["effects"] = normalized["effects"]
    changed[name] = tool
