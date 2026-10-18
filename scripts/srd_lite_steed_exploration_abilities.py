"""Use the saved Steed abilities in fiction; admit authoritative positions at combat entry."""

from copy import deepcopy

from .build_srd_5_2_1_mechanics import _tool_schema
from .srd_control_behaviors import _each, _push
from .srd_scene_relations import _snapshot
from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _tool, _when

TOUCH = "use_srd_steed_healing_touch"
GLARE = "use_srd_steed_fell_glare"
FEY = "use_srd_steed_fey_step"
OWNER = "qualify_srd_lite_steed_bonus_owner"
RIDER = "qualify_srd_lite_exploration_fey_rider"
RECONCILE = "reconcile_srd_lite_exploration_steed_step"
PREPARE = "prepare_srd_lite_retired_steed_proxies"
RETIRE_PROXY = "deactivate_srd_lite_retired_steed_proxy"
ONE = "reconcile_srd_lite_exploration_steed_placement"
ADMIT_RIDER = "qualify_srd_lite_exploration_steed_placement_rider"
TRAVEL = "qualify_srd_lite_exploration_steed_teleport"
ENVIRONMENT = "qualify_srd_lite_exploration_fey_environment_source"
IDS = "exploration_steed_placement_ids"
RECORDS = "exploration_steed_placements"
RIDER_FIELD = "created_steed_rider_id"
MOUNT_FIELD = "otherworldly_steed_mount"
CHARGE = "created_steed_bonus_available"

TARGET_FACTS = (
    ("established_target_distance", "number", -1, {"minimum": -1}),
    ("established_same_plane", "boolean", False, {}),
)
FEY_FACTS = (
    ("established_destination", "string", "", {}),
    ("established_distance", "number", -1, {"minimum": -1}),
    ("established_same_plane", "boolean", False, {}),
    ("established_plane", "string", "", {}),
    ("established_space_unoccupied", "boolean", False, {}),
    (
        "established_environment_source_ids",
        "array",
        ["__undeclared__"],
        {"items": {"type": "string"}, "max_items": 32},
    ),
)


def _choose(condition, on_true, on_false=()):
    return {"condition": condition, "on_true": list(on_true), "on_false": list(on_false)}


def _bound(rows):
    return _tool("Bind the saved package state.", [], rows)["effects"]


def _parameters(facts):
    return [
        _parameter(name, kind, required=False, default=default, **bounds)
        for name, kind, default, bounds in facts
    ]


def _target_branch(owner, range_owner, distance, sight=False):
    """Replace only range qualification, leaving genuine source, roll and charge effects intact."""
    matches = [i for i, row in enumerate(owner["effects"]) if row.get("call") == range_owner]
    if len(matches) != 1:
        raise ValueError("Preserve the Steed ability's single native range qualification")
    facts = [
        _require(
            "established_same_plane == true and established_target_distance >= 0 and "
            f"established_target_distance <= {distance}",
            f"Establish this creature's current same-plane separation within {distance} feet.",
        ),
        _set("_steed_exploration_distance", "established_target_distance"),
    ]
    owner["parameters"] += _parameters(TARGET_FACTS)
    if sight:
        owner["parameters"] += _parameters((("established_target_visible", "boolean", False, {}),))
        facts.append(
            _require(
                "established_target_visible == true",
                "The actual Fiend Steed must currently see its established target.",
            )
        )
    index = matches[0]
    original = owner["effects"][index]
    owner["effects"][index : index + 1] = [
        _set("_steed_exploration_distance", None),
        _choose("combat.encounter_active == true", [original], facts),
    ]
    owner["description"] += (
        " Outside combat, supply current established_target_distance in feet and "
        "established_same_plane=true; these are current fictional facts, never birth placement "
        "or a cached map position. Combat still qualifies the actual admitted bodies."
    )
    if sight:
        owner["description"] += " Fell Glare also requires established_target_visible=true."
    owner["metadata"]["result_card"]["summary"].append(
        {
            "label": "Established separation (ft)",
            "value": {"path": "context._steed_exploration_distance"},
            "optional": True,
        }
    )
    if sight:
        for row in owner["effects"]:
            if row.get("condition") == "_card_d20_roll != null":
                message = next(
                    effect
                    for effect in row["on_true"]
                    if effect.get("call") == "format_srd_information_projection"
                )
                native_message = deepcopy(message)
                message["params"]["message"] = message["params"]["message"].replace(
                    "Failure lasts until the end of the summoner's next turn.",
                    "Failure lasts for 1 round outside combat.",
                )
                row["on_true"][row["on_true"].index(message)] = _choose(
                    "combat.encounter_active == true", [native_message], [message]
                )


def _native_plane(actor, plane):
    return [
        _require(
            "("
            + actor
            + ".travel_location == null or "
            + actor
            + ".travel_location.plane == null or "
            + actor
            + ".travel_location.plane == "
            + plane
            + ") and ("
            + actor
            + ".find_path_current_plane == null or "
            + actor
            + ".find_path_current_plane == "
            + plane
            + ")",
            "The current declared plane must match this exact actor's saved native plane facts.",
        )
    ]


def _pending_plane(actor, plane):
    return [
        _require(
            "("
            + actor
            + ".travel_location != null and "
            + actor
            + ".travel_location.plane == "
            + plane
            + ") or "
            + actor
            + ".find_path_current_plane == "
            + plane,
            "A pending placement changes plane only after "
            "this exact actor's genuine native arrival.",
        )
    ]


def _rider():
    return _tool(
        "Carry the actual reciprocal smaller living rider during exploration Fey Step.",
        [_entity("steed_state"), _entity("rider_state"), _parameter("plane")],
        [
            _require(
                "steed_state."
                + RIDER_FIELD
                + " == _rider_state_id and rider_state."
                + MOUNT_FIELD
                + " == _steed_state_id and rider_state.is_alive == true and "
                "rider_state.hp >= 1 and rider_state.size_rank < steed_state.size_rank",
                "Fey Step carries only this Steed's actual smaller living rider.",
            ),
            _set("_fey_rider_name", "rider_state.name"),
            *_native_plane("rider_state", "plane"),
            _when(
                "state." + RECORDS + " != null and _steed_state_id in state." + RECORDS,
                [
                    _set("_step_previous", "state." + RECORDS + "[_steed_state_id]"),
                    _when(
                        "_step_previous.pending == true and "
                        "_step_previous.rider == _rider_state_id "
                        "and _step_previous.plane != plane",
                        _pending_plane("rider_state", "plane"),
                    ),
                ],
            ),
        ],
    )


def _record_placement():
    """Keep fictional relocation pending; the next combat must supply these same actual bodies."""
    return [
        _when("state." + IDS + " == null", [_set("state." + IDS, [])]),
        _when("state." + RECORDS + " == null", [_set("state." + RECORDS, {})]),
        *_native_plane("steed_state", "established_plane"),
        _when(
            "_steed_state_id in state." + RECORDS,
            [
                _set("_step_previous", "state." + RECORDS + "[_steed_state_id]"),
                _when(
                    "_step_previous.pending == true and _step_previous.plane != established_plane",
                    _pending_plane("steed_state", "established_plane"),
                ),
            ],
        ),
        _set("_step_ids", "state." + IDS),
        _when(
            "_steed_state_id not in _step_ids",
            [
                _require(
                    "len(_step_ids) < 128", "The saved exploration Steed placement bound is full."
                ),
                _push("_step_ids", "_steed_state_id"),
                _set("state." + IDS, "_step_ids"),
            ],
        ),
        _set(
            "state." + RECORDS + ".{_steed_state_id}",
            {
                "steed": "_steed_state_id",
                "caster": "steed_state.created_caster_state_id",
                "rider": "_fey_rider_id",
                "steed_placement": "established_destination",
                "rider_placement": "_fey_rider_destination",
                "plane": "established_plane",
                "mounted": "_fey_rider_id != ''",
                "pending": True,
                "tick": "state.lasting_tick",
            },
        ),
    ]


def _fey_branch(owner):
    """Retain the full coordinate arm and place the scene-free arm after identical native guards."""
    index = next(
        i
        for i, row in enumerate(owner["effects"])
        if row.get("call") == "srd_information_scene_entity"
    )
    native = deepcopy(owner["effects"][index:])
    owner["effects"] = owner["effects"][:index] + [
        _choose(
            "combat.encounter_active == true",
            [
                _require(
                    "len(destination) == 3",
                    "Combat Fey Step needs its actual coordinate destination.",
                ),
                *native,
            ],
            _bound(
                [
                    _require(
                        "len(established_destination) > 0 and "
                        "len(established_destination) <= 240 and "
                        "len(established_plane) > 0 and len(established_plane) <= 80 and "
                        "established_distance >= 0 and established_distance <= 60 and "
                        "established_same_plane == true and established_space_unoccupied == true",
                        "Establish an unoccupied same-plane destination within 60 feet "
                        "for the full Steed and rider.",
                    ),
                    _require(
                        "'__undeclared__' not in established_environment_source_ids",
                        "Explicitly list relevant current environmental source IDs; "
                        "supply [] only when none apply.",
                    ),
                    _set("_step_environment_ids", "established_environment_source_ids"),
                    _each(
                        "_step_environment_ids",
                        "_step_environment_id",
                        [
                            _call(ENVIRONMENT, {"source_id": "{_step_environment_id}"}),
                        ],
                    ),
                    _require(
                        "len(_step_environment_ids) == 0",
                        "Exploration Fey Step cannot yet resolve this relevant "
                        "environmental source interaction.",
                    ),
                    _call(
                        TRAVEL,
                        {
                            "target_state": "{_steed_state_id}",
                            "destination_plane": "{established_plane}",
                            "teleport": True,
                        },
                    ),
                    _set("_fey_distance", "established_distance"),
                    _set("_fey_destination", "established_destination"),
                    _set("_fey_rider_id", "''"),
                    _set("_fey_rider_name", "'None'"),
                    _set("_fey_rider_destination", None),
                    _when(
                        "steed_state."
                        + RIDER_FIELD
                        + " != null and steed_state."
                        + RIDER_FIELD
                        + " != ''",
                        [
                            _set("_fey_rider_id", "steed_state." + RIDER_FIELD),
                            _call(
                                RIDER,
                                {
                                    "steed_state": "{_steed_state_id}",
                                    "rider_state": "{_fey_rider_id}",
                                    "plane": "{established_plane}",
                                },
                                ("_fey_rider_name",),
                            ),
                            _call(
                                TRAVEL,
                                {
                                    "target_state": "{_fey_rider_id}",
                                    "destination_plane": "{established_plane}",
                                    "teleport": True,
                                },
                            ),
                            _set("_fey_rider_destination", "established_destination"),
                        ],
                    ),
                    *_record_placement(),
                    _set("steed_state." + CHARGE, False),
                ]
            ),
        )
    ]
    destination = next(p for p in owner["parameters"] if p["name"] == "destination")
    destination.update(required=False, default=[], min_items=0)
    owner["parameters"] += _parameters(FEY_FACTS)
    owner["description"] += (
        " Outside combat, supply the current established_destination description, "
        "established_distance in feet, established_plane, established_same_plane=true "
        "and established_space_unoccupied=true "
        "for the full Steed and its genuine saved rider. This records fiction, never coordinates. "
        "Explicitly supply established_environment_source_ids=[] when no environmental source "
        "currently interacts with this teleport; relevant unresolved source interactions reject. "
        "At combat entry explicitly admit these same Steed/rider IDs "
        "at their actual current placements."
    )
    owner["metadata"]["result_card"]["details_label"] = "Destinations"


def _find(rows, identity, local):
    return [
        _set(local, None),
        _each(
            rows,
            "_step_row",
            [
                _when(
                    "_step_row.id == " + identity,
                    [
                        _require(
                            local + " == null",
                            "The pending Steed placement has duplicate body IDs.",
                        ),
                        _set(local, "_step_row"),
                    ],
                ),
            ],
        ),
        _require(
            local
            + " != null and "
            + local
            + ".active == true and "
            + local
            + ".metadata.participant_ref == "
            + identity
            + " and "
            + local
            + ".metadata.is_object != true",
            "Explicitly supply this pending Steed or rider's exact active creature body.",
        ),
    ]


def _environment_source():
    """Validate declared identities without treating distant world sources as current geometry."""
    rows = [_set("_step_environment_found", False)]
    for pool in (
        "spell_barrier_sources",
        "spell_magic_zone_sources",
        "spell_secure_area_sources",
        "spell_prismatic_wall_sources",
        "solid_spell_wall_sources",
    ):
        rows += [
            _when(
                "state." + pool + " != null and source_id in state." + pool,
                [
                    _set("_step_environment_record", "state." + pool + "[source_id]"),
                    _when(
                        "_step_environment_record.installed == true and "
                        "(_step_environment_record.expires_tick == null or state.lasting_tick < "
                        "_step_environment_record.expires_tick)",
                        [_set("_step_environment_found", True)],
                    ),
                ],
            )
        ]
    rows += [
        _set("_step_environment_records", []),
        _when(
            "state.turn_effects != null", [_set("_step_environment_records", "state.turn_effects")]
        ),
        _each(
            "_step_environment_records",
            "_step_environment_record",
            [
                _when(
                    "(_step_environment_record.instance == source_id or "
                    "_step_environment_record.source_id == source_id or "
                    "_step_environment_record.id == source_id) and "
                    "_step_environment_record.active != false and "
                    "_step_environment_record.installed != false",
                    [
                        _when(
                            "(_step_environment_record.expires_tick == null or "
                            "state.lasting_tick < _step_environment_record.expires_tick) and "
                            "(_step_environment_record.expires_seconds == null or "
                            "state.lasting_tick * 3 < "
                            "_step_environment_record.expires_seconds)",
                            [_set("_step_environment_found", True)],
                        )
                    ],
                ),
            ],
        ),
        _require(
            "_step_environment_found == true",
            "Use the exact current active source ID from its saved package registry.",
        ),
    ]
    return _tool(
        "Validate a declared relevant saved environmental source "
        "before clear unsupported rejection.",
        [_parameter("source_id")],
        rows,
    )


def _apply_body(identity, prefix):
    """Allow only pending exact IDs to take supplied placements; retain all mechanic metadata."""
    return [
        *_find("_step_incoming_rows", identity, prefix + "_incoming"),
        *_find("_step_candidate_rows", identity, prefix + "_saved"),
        _require(
            prefix
            + "_incoming.footprint.kind == "
            + prefix
            + "_saved.footprint.kind and "
            + prefix
            + "_incoming.height == "
            + prefix
            + "_saved.height and "
            + prefix
            + "_incoming.metadata.plane == _step_record.plane",
            "Preserve this body's actual dimensions and established plane.",
        ),
        _choose(
            prefix + "_saved.footprint.kind == 'box'",
            [
                _require(
                    prefix
                    + "_incoming.footprint.half_extents[0] == "
                    + prefix
                    + "_saved.footprint.half_extents[0] and "
                    + prefix
                    + "_incoming.footprint.half_extents[1] == "
                    + prefix
                    + "_saved.footprint.half_extents[1]",
                    "Preserve the actual box body's half extents.",
                ),
            ],
            [
                _require(
                    prefix
                    + "_saved.footprint.kind == 'circle' and "
                    + prefix
                    + "_incoming.footprint.radius == "
                    + prefix
                    + "_saved.footprint.radius",
                    "Preserve the actual circular body's radius.",
                ),
            ],
        ),
        _set(
            prefix + "_frame_patch",
            {
                "position": prefix + "_incoming.position",
                "facing": prefix + "_incoming.facing",
                "metadata": "merge(" + prefix + "_saved.metadata, " + prefix + "_incoming_plane)",
            },
        ),
    ]


def _admitted_rider():
    return _tool(
        "Qualify the exact saved rider links before admitting its pending placement.",
        [_entity("steed_state"), _entity("rider_state")],
        [
            _set("_step_record", "state." + RECORDS + "[_steed_state_id]"),
            _require(
                "rider_state.is_alive == true and rider_state.hp >= 1 and "
                "rider_state.size_rank < steed_state.size_rank",
                "Admit the actual smaller living Steed rider.",
            ),
            _choose(
                "_step_record.mounted == true and steed_state.created_active == true",
                [
                    _require(
                        "steed_state."
                        + RIDER_FIELD
                        + " == _rider_state_id and rider_state."
                        + MOUNT_FIELD
                        + " == _steed_state_id",
                        "Preserve the exact reciprocal mounted pair.",
                    ),
                ],
                [
                    _require(
                        "(steed_state."
                        + RIDER_FIELD
                        + " == null or steed_state."
                        + RIDER_FIELD
                        + " == '') and (rider_state."
                        + MOUNT_FIELD
                        + " == null or rider_state."
                        + MOUNT_FIELD
                        + " == '')",
                        "Preserve the actual cleared dismount links.",
                    ),
                ],
            ),
        ],
    )


def _retired_proxy():
    """Deactivate only a genuine retired Find Steed source's exact cached body."""
    return _tool(
        "Retire exact cached Steed geometry without consuming pending rider placement.",
        [
            _entity("steed_state"),
            _entity("caster_state"),
            _parameter("refresh_snapshot", "boolean", required=False, default=True),
        ],
        [
            _require(
                "steed_state.created_active == false and "
                "steed_state.created_spell_id == 'find_steed' and "
                "steed_state.created_caster_state_id == _caster_state_id and "
                "caster_state.created_steed_id != _steed_state_id and "
                "(steed_state." + RIDER_FIELD + " == null or "
                "steed_state." + RIDER_FIELD + " == '')",
                "Retire only the actual inactive Steed with cleared native owner/rider links.",
            ),
            _require(
                "state.created_actor_ids != null and "
                "_steed_state_id in state.created_actor_ids and "
                "state.created_actor_records != null and "
                "_steed_state_id in state.created_actor_records",
                "Retirement requires the Steed's actual saved native source record.",
            ),
            _set("_step_retired_source", "state.created_actor_records[_steed_state_id]"),
            _require(
                "_step_retired_source.individual == _steed_state_id and "
                "_step_retired_source.spell == 'find_steed' and "
                "_step_retired_source.caster == _caster_state_id and "
                "_step_retired_source.active == false",
                "Preserve the exact inactive Find Steed source identity and caster.",
            ),
            _when(
                "state.spatial_scene != null",
                [
                    _set("_step_retired_rows", "state.spatial_scene.entities"),
                    _set("_step_retired_entities", []),
                    _set("_step_retired_changed", False),
                    _each(
                        "_step_retired_rows",
                        "_step_retired_row",
                        [
                            _when(
                                "_step_retired_row.id == _steed_state_id",
                                [
                                    _require(
                                        "_step_retired_row.metadata.participant_ref == "
                                        "_steed_state_id and "
                                        "_step_retired_row.metadata.source_ref == 'find_steed' "
                                        "and _step_retired_row.metadata.source_caster == "
                                        "_caster_state_id and "
                                        "_step_retired_row.metadata.is_object != true",
                                        "Retire only the exact Steed's genuine cached body.",
                                    ),
                                    _when(
                                        "_step_retired_row.active == true",
                                        [
                                            _set("_step_retired_patch", {"active": False}),
                                            _set(
                                                "_step_retired_row",
                                                "merge(_step_retired_row, _step_retired_patch)",
                                            ),
                                            _set("_step_retired_changed", True),
                                        ],
                                    ),
                                ],
                            ),
                            _push("_step_retired_entities", "_step_retired_row"),
                        ],
                    ),
                    _when(
                        "_step_retired_changed == true",
                        [
                            _set("state.spatial_scene.entities", "_step_retired_entities"),
                            _set(
                                "state.spatial_scene.map.revision",
                                "state.spatial_scene.map.revision + 1",
                            ),
                            _when("refresh_snapshot == true", _snapshot()),
                        ],
                    ),
                ],
            ),
        ],
    )


def _prepare_retired_proxies():
    """Clean cached retired sources before arena reconciliation, retaining every pending fact."""
    return _tool(
        "Clean genuine retired Steed proxies before saved scene capture or arena replacement.",
        [],
        [
            _require(
                "combat.encounter_active != true and combat.turn_active != true and "
                "combat.pending_action == null",
                "Retire cached Steed geometry before initiative and outside pending actions.",
            ),
            _when(
                "state.spatial_scene != null and state.created_actor_records != null",
                [
                    _set("_step_proxy_rows", "state.spatial_scene.entities"),
                    _each(
                        "_step_proxy_rows",
                        "_step_proxy_row",
                        [
                            _when(
                                "_step_proxy_row.id in state.created_actor_records",
                                [
                                    _set(
                                        "_step_proxy_source",
                                        "state.created_actor_records[_step_proxy_row.id]",
                                    ),
                                    _when(
                                        "_step_proxy_source.spell == 'find_steed' and "
                                        "_step_proxy_source.active != true",
                                        [
                                            _call(
                                                RETIRE_PROXY,
                                                {
                                                    "steed_state": "{_step_proxy_row.id}",
                                                    "caster_state": "{_step_proxy_source.caster}",
                                                    "lasting_state": "{_lasting_state_id}",
                                                    "refresh_snapshot": False,
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


def _reconcile_one():
    header = [
        _set("_step_record", "state." + RECORDS + "[_steed_state_id]"),
        _require(
            "_step_record.steed == _steed_state_id and _step_record.caster == _caster_state_id and "
            "steed_state.created_spell_id == 'find_steed' and "
            "steed_state.created_caster_state_id == _caster_state_id",
            "Preserve the pending Steed's genuine saved source and caster.",
        ),
        _set(
            "_step_live_steed",
            "steed_state.created_active == true and "
            "steed_state.is_alive == true and steed_state.hp >= 1",
        ),
        _when(
            "_step_live_steed == true",
            [
                _call(
                    OWNER,
                    {"steed_state": "{_steed_state_id}", "caster_state": "{_caster_state_id}"},
                ),
            ],
        ),
        _when(
            "_step_live_steed != true",
            [
                _call(
                    RETIRE_PROXY,
                    {
                        "steed_state": "{_steed_state_id}",
                        "caster_state": "{_caster_state_id}",
                        "lasting_state": "{_lasting_state_id}",
                    },
                ),
            ],
        ),
    ]
    effects = [
        _require(
            "len(scenes) == 1", "Supply actual body placements after exploration Steed movement."
        ),
        _set("_step_incoming_rows", "scenes[0].entities"),
        _set("_step_candidate_rows", "state.spatial_scene.entities"),
    ]
    for identity, prefix, condition in (
        ("_steed_state_id", "_step_steed", "_step_live_steed == true"),
        ("_step_record.rider", "_step_rider", "_step_record.rider != ''"),
    ):
        rows = _apply_body(identity, prefix)
        # Read only admitted environmental fields from the incoming row, never caller HP or magic.
        insertion = next(
            i
            for i, row in enumerate(rows)
            if row.get("set", {}).get("field") == prefix + "_frame_patch"
        )
        rows[insertion:insertion] = [
            _set(
                prefix + "_incoming_plane",
                {
                    "plane": prefix + "_incoming.metadata.plane",
                    "lighting": prefix + "_incoming.metadata.lighting",
                },
            )
        ]
        rows += [
            _set("_step_replaced_rows", []),
            _each(
                "_step_candidate_rows",
                "_step_row",
                [
                    _choose(
                        "_step_row.id == " + identity,
                        [
                            _push(
                                "_step_replaced_rows",
                                "merge(_step_row, " + prefix + "_frame_patch)",
                            )
                        ],
                        [_push("_step_replaced_rows", "_step_row")],
                    ),
                ],
            ),
            _set("_step_candidate_rows", "_step_replaced_rows"),
        ]
        if prefix == "_step_rider":
            rows[:0] = [
                _call(
                    ADMIT_RIDER,
                    {"steed_state": "{_steed_state_id}", "rider_state": "{_step_record.rider}"},
                )
            ]
        effects.append(_when(condition, rows))
    effects += [
        _when(
            "_step_live_steed == true",
            [
                _require(
                    "_step_steed_incoming.metadata.source_ref == 'find_steed' and "
                    "_step_steed_incoming.metadata.source_caster == _caster_state_id and "
                    "_step_steed_incoming.metadata.creature_type == steed_state.creature_type and "
                    "_step_steed_saved.metadata.source_ref == 'find_steed' and "
                    "_step_steed_saved.metadata.source_caster == _caster_state_id",
                    "Preserve the supplied Steed's native body source metadata.",
                ),
                _require(
                    "_step_steed_saved.footprint.kind == 'box' and "
                    "_step_steed_saved.footprint.half_extents[0] == 5 and "
                    "_step_steed_saved.footprint.half_extents[1] == 5 and "
                    "_step_steed_saved.height > 0",
                    "Preserve the Steed's native Large body and actual physical height.",
                ),
            ],
        ),
        _when(
            "_step_record.mounted == true and _step_live_steed == true",
            [
                _require(
                    "_step_record.rider != '' and "
                    "abs(_step_rider_incoming.position[0] - _step_steed_incoming.position[0]) "
                    "<= 0.000000001 and "
                    "abs(_step_rider_incoming.position[1] - _step_steed_incoming.position[1]) "
                    "<= 0.000000001",
                    "Admit the actual rider at its Steed's saddle.",
                ),
                _require(
                    "abs(_step_rider_incoming.position[2] - _step_steed_incoming.position[2] - "
                    "_step_steed_incoming.height) <= 0.000000001",
                    "Admit the actual rider at its Steed's saddle height.",
                ),
            ],
        ),
        _set("state.spatial_scene.entities", "_step_candidate_rows"),
        _set("state.spatial_scene.map.revision", "state.spatial_scene.map.revision + 1"),
        *_snapshot(),
    ]
    for identity, prefix, condition in (
        ("_steed_state_id", "_step_steed", "_step_live_steed == true"),
        ("_step_record.rider", "_step_rider", "_step_record.rider != ''"),
    ):
        rows = [
            _call(
                "resolve_srd_travel_placement",
                {
                    "subject": "{" + identity + "}",
                    "destination": "{" + prefix + "_incoming.position}",
                    "plane": "{_step_record.plane}",
                    "excluded": [],
                },
                ("_travel_placement",),
            ),
            _require(
                "_travel_placement.distance == 0",
                "Admit the full body in its actual unoccupied space.",
            ),
        ]
        effects.append(_when(condition, rows)) if condition else effects.extend(rows)
    return _tool(
        "Admit only exact pending Steed/rider body placements after generic scene merge.",
        [
            _entity("steed_state"),
            _entity("caster_state"),
            _parameter("scenes", "array", items={"type": "object"}, max_items=1),
        ],
        header
        + [
            _when("_step_live_steed == true or _step_record.rider != ''", effects),
            _set("state." + RECORDS + ".{_steed_state_id}.pending", False),
        ],
    )


def _reconcile():
    return _tool(
        "Reconcile explicit pending Steed placements before combat actor admission.",
        [_parameter("scenes", "array", items={"type": "object"}, max_items=1)],
        [
            _require(
                "combat.encounter_active != true and combat.pending_action == null",
                "Admit exploration Steed placements before initiative.",
            ),
            _when(
                "state." + IDS + " != null",
                [
                    _set("_step_ids", "state." + IDS),
                    _each(
                        "_step_ids",
                        "_step_id",
                        [
                            _set("_step_record", "state." + RECORDS + "[_step_id]"),
                            _when(
                                "_step_record.pending == true",
                                [
                                    _call(
                                        ONE,
                                        {
                                            "steed_state": "{_step_record.steed}",
                                            "caster_state": "{_step_record.caster}",
                                            "scenes": "{scenes}",
                                            "lasting_state": "{_lasting_state_id}",
                                        },
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def bind_steed_exploration_abilities(runtime, record):
    """Bind after final Steed owners; expose existing callbacks with current fictional facts."""
    tools = runtime["tools"]
    if RECONCILE in tools:
        raise ValueError("Exploration Steed abilities must be composed exactly once")
    _target_branch(tools[TOUCH], "qualify_srd_lite_steed_touch_range", 5)
    _target_branch(tools[GLARE], "qualify_srd_lite_steed_glare_range", 60, sight=True)
    _fey_branch(tools[FEY])
    travel = deepcopy(tools["check_srd_progressive_travel"])
    travel["description"] = (
        "Preserve native saved teleport restrictions for same-plane fictional Fey Step."
    )

    def scene_free(rows):
        kept = []
        for row in rows:
            if row.get("call") == "srd_information_scene_entity":
                continue
            # Same-plane Fey Step cannot trigger the native planar-change guard.
            if "_info_scene_entity.metadata.plane" in row.get("require", {}).get("condition", ""):
                continue
            for branch in ("on_true", "on_false"):
                if branch in row:
                    row[branch] = scene_free(row[branch])
            kept.append(row)
        return kept

    travel["effects"] = scene_free(travel["effects"])
    tools.update(
        {
            RIDER: _rider(),
            ADMIT_RIDER: _admitted_rider(),
            ONE: _reconcile_one(),
            RECONCILE: _reconcile(),
            TRAVEL: travel,
            ENVIRONMENT: _environment_source(),
            RETIRE_PROXY: _retired_proxy(),
            PREPARE: _prepare_retired_proxies(),
        }
    )
    for name in (TOUCH, GLARE, FEY):
        declarations = [row for row in record["tools"] if row["name"] == name]
        if len(declarations) != 1:
            raise ValueError("Preserve the single public Steed ability declaration: " + name)
        declarations[0]["input_schema"] = _tool_schema(tools[name], tool_name=name)
    return [
        TOUCH,
        GLARE,
        FEY,
        RIDER,
        ADMIT_RIDER,
        ONE,
        RECONCILE,
        TRAVEL,
        ENVIRONMENT,
        RETIRE_PROXY,
        PREPARE,
    ]
