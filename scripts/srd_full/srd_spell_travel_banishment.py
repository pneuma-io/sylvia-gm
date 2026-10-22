"""Banishment's full-minute destination consumes admitted plane associations and anchors."""

if __package__:
    from .srd_scene_relations import _tool_scene
    from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from .srd_timed_senses import _loop, _public, _push
else:
    from srd_scene_relations import _tool_scene
    from srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from srd_timed_senses import _loop, _public, _push


ASSOCIATED_TYPES = ["aberration", "celestial", "elemental", "fey", "fiend"]


def associated_type_condition(value):
    """Keep the printed five-type qualification in one source-owned predicate."""
    return "(" + " or ".join(f"{value} == '{kind}'" for kind in ASSOCIATED_TYPES) + ")"


def banishment_tools(tools):
    """Uniformly select an actual registered location; never fabricate coordinates or clearance."""
    if __package__:
        from .srd_spell_travel import _move, _placement, _scene_subject
    else:
        from srd_spell_travel import _move, _placement, _scene_subject

    record = _public(
        _tool_scene(
            "Record the GM's creature-type plane association using distinct admitted locations.",
            [
                _parameter("creature_type", "enum", enum_values=ASSOCIATED_TYPES),
                _parameter(
                    "destination_subjects", "array", items={"type": "string"},
                    min_items=2, max_items=64,
                ),
            ],
            [
                _require(
                    "state.pending_srd_action_stack == null or "
                    "len(state.pending_srd_action_stack) == 0",
                    "Complete the pending action before recording a plane association.",
                ),
                _set("_banishment_plane", None),
                _set("_banishment_anchors", []),
                _set("_banishment_anchor_ids", []),
                _set("_banishment_anchor_positions", []),
                _loop(
                    "destination_subjects", "_banishment_subject",
                    [
                        *_scene_subject("{_banishment_subject}", "_banishment_location"),
                        _require(
                            "_banishment_location.metadata.plane != null and "
                            "_banishment_location.metadata.plane != '' and "
                            "_banishment_location.active != false",
                            "A Banishment destination needs an active admitted location and plane.",
                        ),
                        _when(
                            "_banishment_plane == null",
                            [_set("_banishment_plane", "_banishment_location.metadata.plane")],
                        ),
                        _require(
                            "_banishment_location.metadata.plane == _banishment_plane and "
                            "_banishment_location.id not in _banishment_anchor_ids and "
                            "_banishment_location.position not in _banishment_anchor_positions",
                            "Register distinct actual locations on the one GM-associated plane.",
                        ),
                        _push("_banishment_anchor_ids", "_banishment_location.id"),
                        _push("_banishment_anchor_positions", "_banishment_location.position"),
                        _push("_banishment_anchors", {
                            "subject": "_banishment_location.id",
                            "plane": "_banishment_location.metadata.plane",
                            "position": "_banishment_location.position",
                        }),
                    ],
                ),
                _when(
                    "state.banishment_plane_associations == null",
                    [_set("state.banishment_plane_associations", dict.fromkeys(ASSOCIATED_TYPES))],
                ),
                _set("_banishment_record", {
                    "creature_type": "creature_type", "plane": "_banishment_plane",
                    "anchors": "_banishment_anchors",
                }),
                *[
                    _when(f"creature_type == '{kind}'", [
                        _set(f"state.banishment_plane_associations.{kind}", "_banishment_record"),
                    ])
                    for kind in ASSOCIATED_TYPES
                ],
            ],
        ),
        "exploration", "Banishment Plane", [("Plane", "_banishment_plane")],
    )
    association = _tool_scene(
        "Resolve and validate the actual GM-associated plane before paid planar exile.",
        [
            _entity("target_state"),
            _parameter("creature_type", "enum", enum_values=ASSOCIATED_TYPES),
        ],
        [
            _require(
                "state.banishment_plane_associations != null and "
                "state.banishment_plane_associations[creature_type] != null",
                "Record actual associated-plane destinations for this creature type first.",
            ),
            _set("_banishment_association", "state.banishment_plane_associations[creature_type]"),
            _require(
                "_banishment_association.creature_type == creature_type and "
                "len(_banishment_association.anchors) >= 2 and "
                "len(_banishment_association.anchors) <= 64",
                "Banishment needs a genuine admitted pool of distinct random destinations.",
            ),
            _set("_banishment_check_anchors", "_banishment_association.anchors"),
            _loop("_banishment_check_anchors", "_banishment_check_anchor", [
                _call("validate_srd_banishment_destination", {
                    "target_state": "{_target_state_id}",
                    "anchor": "{_banishment_check_anchor}",
                    "plane": "{_banishment_association.plane}",
                }),
            ]),
        ],
    )
    destination_effects = [
        *_scene_subject("{anchor.subject}", "_banishment_arrival_location"),
        _require(
            "_banishment_arrival_location.active != false and "
            "_banishment_arrival_location.metadata.plane == plane and anchor.plane == plane "
            "and " + " and ".join(
                f"_banishment_arrival_location.position[{axis}] == anchor.position[{axis}]"
                for axis in range(3)
            ),
            "The registered Banishment destination changed its actual identity, plane or position.",
        ),
        *_placement("{_target_state_id}", "{anchor.position}", "{plane}", []),
        _require(
            "_travel_placement.distance == 0",
            "A registered random Banishment arrival is occupied; no replacement is guessed.",
        ),
    ]
    if "check_srd_progressive_travel" in tools:
        destination_effects.append(_call("check_srd_progressive_travel", {
            "target_state": "{_target_state_id}", "destination_plane": "{plane}",
            "teleport": False,
        }))
    destination = _tool_scene(
        "Validate the actual unchanged anchor and body clearance before random selection.",
        [_entity("target_state"), _parameter("anchor", "object"), _parameter("plane")],
        destination_effects,
    )
    transport = _tool_scene(
        "Resolve the retained full-minute Banishment using one actual uniform location roll.",
        [_entity("target_state")],
        [
            _require(
                "target_state.travel_exile != null and "
                "target_state.travel_exile.spell == 'banishment' and "
                "target_state.travel_exile.permanent_pending == true and "
                "target_state.travel_exile.association != null",
                "Permanent Banishment requires its exact full-minute saved source.",
            ),
            _set("_banishment_saved_association", "target_state.travel_exile.association"),
            _set("_banishment_saved_anchors", "_banishment_saved_association.anchors"),
            _loop("_banishment_saved_anchors", "_banishment_saved_anchor", [
                _call("validate_srd_banishment_destination", {
                    "target_state": "{_target_state_id}", "anchor": "{_banishment_saved_anchor}",
                    "plane": "{_banishment_saved_association.plane}",
                }),
            ]),
            _set("_banishment_location_count", "len(_banishment_saved_association.anchors)"),
            {"dice": {"expression": "1d{_banishment_location_count}"},
             "store_as": "_banishment_location_roll"},
            _set("_banishment_selected_anchor",
                 "_banishment_saved_association.anchors[_banishment_location_roll.total - 1]"),
            *_move("{_target_state_id}", "{_banishment_selected_anchor.position}",
                   "{_banishment_saved_association.plane}", False),
            _set("target_state.last_banishment_arrival", {
                "creature_type": "_banishment_saved_association.creature_type",
                "plane": "_banishment_saved_association.plane",
                "anchor": "_banishment_selected_anchor",
                "roll": "_banishment_location_roll",
                "cast_id": "target_state.travel_exile.cast_id",
                "sequence": "target_state.travel_exile.sequence",
            }),
        ],
    )
    return {
        "record_srd_banishment_plane": record,
        "resolve_srd_banishment_association": association,
        "validate_srd_banishment_destination": destination,
        "transport_srd_permanent_banishment": transport,
    }
