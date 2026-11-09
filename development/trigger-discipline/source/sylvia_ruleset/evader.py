"""Owned quiet-metal STEALTH reference; private contacts and conservative physical proof."""

ENTRY = "specialty.evader.entry.break_contact"
ROOT = "state.break_contact"
RECORD = ROOT + ".actors[_actor_id]"
ACTIVITY = RECORD + ".activities[activity_id]"


def put(field, value, condition=None):
    result = {"set": {"field": field, "value": value}}
    if condition is not None:
        result["condition"] = condition
    return result


def branch(condition, effects):
    return {"condition": condition, "on_true": effects, "on_false": []}


def bounds(body, prefix):
    """Use the shared engine's exact box bounds, or a conservative cylinder enclosure."""
    return [
        put(prefix + "_hx", f"{body}.footprint.radius"),
        put(prefix + "_hy", f"{body}.footprint.radius"),
        put(prefix + "_hx", f"{body}.footprint.half_extents[0]", f"{body}.footprint.kind == 'box'"),
        put(prefix + "_hy", f"{body}.footprint.half_extents[1]", f"{body}.footprint.kind == 'box'"),
        put(
            prefix + "_lo",
            [
                f"{body}.position[0] - {prefix}_hx",
                f"{body}.position[1] - {prefix}_hy",
                f"{body}.position[2]",
            ],
        ),
        put(
            prefix + "_hi",
            [
                f"{body}.position[0] + {prefix}_hx",
                f"{body}.position[1] + {prefix}_hy",
                f"{body}.position[2] + {body}.height",
            ],
        ),
    ]


def intersects():
    """Conservative segment/enclosure intersection; mere surface contact is not blocking."""
    return [
        put("_bc_enter", 0),
        put("_bc_exit", 1),
        put("_bc_interval", True),
        {
            "for_each": {
                "collection": "_bc_axes",
                "as": "_bc_axis",
                "do": [
                    branch(
                        "_bc_delta[_bc_axis] == 0",
                        [
                            put(
                                "_bc_interval",
                                False,
                                "_bc_origin[_bc_axis] <= _bc_box_lo[_bc_axis] or "
                                "_bc_origin[_bc_axis] >= _bc_box_hi[_bc_axis]",
                            ),
                        ],
                    ),
                    branch(
                        "_bc_delta[_bc_axis] != 0",
                        [
                            put(
                                "_bc_t1",
                                "(_bc_box_lo[_bc_axis] - _bc_origin[_bc_axis]) * "
                                "_bc_delta[_bc_axis] ** -1",
                            ),
                            put(
                                "_bc_t2",
                                "(_bc_box_hi[_bc_axis] - _bc_origin[_bc_axis]) * "
                                "_bc_delta[_bc_axis] ** -1",
                            ),
                            put("_bc_enter", "max(_bc_enter, min(_bc_t1, _bc_t2))"),
                            put("_bc_exit", "min(_bc_exit, max(_bc_t1, _bc_t2))"),
                        ],
                    ),
                ],
            }
        },
        put(
            "_bc_clear",
            False,
            "_bc_interval == true and _bc_exit > _bc_enter and _bc_exit > 0 and _bc_enter < 1",
        ),
    ]


def wall_proof():
    """A real opaque box plane must intercept every ray to the entire target enclosure.

    Perspective projection onto a separating plane is convex. Strict containment
    of all eight enclosure corners inside one actual opaque rectangle proves
    whole-body occlusion. A slit or a union of unrelated blockers cannot pass.
    No finite exposure sampling, observer-body hit or invented sight origin is used.
    """
    crossing = branch(
        "_bc_cross_axis != _bc_wall_axis",
        [
            put(
                "_bc_crossing",
                "_bc_origin[_bc_cross_axis] + _bc_fraction * "
                "(_bc_corner[_bc_cross_axis] - _bc_origin[_bc_cross_axis])",
            ),
            put(
                "_bc_plane_covers",
                False,
                "_bc_crossing <= _bc_geometry.volume.min[_bc_cross_axis] or "
                "_bc_crossing >= _bc_geometry.volume.max[_bc_cross_axis]",
            ),
        ],
    )
    corner = [
        put(
            "_bc_fraction",
            "(_bc_plane - _bc_origin[_bc_wall_axis]) * "
            "(_bc_corner[_bc_wall_axis] - _bc_origin[_bc_wall_axis]) ** -1",
        ),
        {"for_each": {"collection": "_bc_axes", "as": "_bc_cross_axis", "do": [crossing]}},
    ]
    plane = [
        put(
            "_bc_plane",
            "(_bc_geometry.volume.min[_bc_wall_axis] + "
            "_bc_geometry.volume.max[_bc_wall_axis]) * 0.5",
        ),
        put("_bc_plane_covers", True),
        {"for_each": {"collection": "_bc_corners", "as": "_bc_corner", "do": corner}},
        put("_bc_masked", True, "_bc_plane_covers == true"),
    ]
    separated = (
        "(_bc_origin[_bc_wall_axis] < _bc_geometry.volume.min[_bc_wall_axis] and "
        "_bc_target_lo[_bc_wall_axis] > _bc_geometry.volume.max[_bc_wall_axis]) or "
        "(_bc_origin[_bc_wall_axis] > _bc_geometry.volume.max[_bc_wall_axis] and "
        "_bc_target_hi[_bc_wall_axis] < _bc_geometry.volume.min[_bc_wall_axis])"
    )
    return [
        {
            "for_each": {
                "collection": "_bc_wall_axes",
                "as": "_bc_wall_axis",
                "do": [branch(separated, plane)],
            }
        }
    ]


def observer_helper(param):
    contact = "_bc_source"
    supported = (
        "'.' not in _observer_id and '{' not in _observer_id and '}' "
        "not in _observer_id and "
        f"{contact}.source_kind == 'DIRECT_VISUAL' and "
        "observer.active == true and target.active == true "
        "and observer.side != target.side and _bc_observer_body != null "
        "and _bc_target_body != null "
        f"and {contact}.basis_stance == observer.stance and "
        + " and ".join(f"{contact}.basis_center[{i}] == observer.center[{i}]" for i in range(3))
    )
    actual_origin = " and ".join(
        f"_bc_origin[{i}] >= _bc_observer_lo[{i}] and _bc_origin[{i}] <= _bc_observer_hi[{i}]"
        for i in range(3)
    )
    return {
        "description": "Private conservative contact proof for one owned visual source.",
        "parameters": [
            param("observer", "string", resolve="entity"),
            param("target", "string", resolve="entity"),
        ],
        "effects": [
            put(contact, ROOT + ".actors[_target_id].contacts[_observer_id]"),
            put("_bc_supported", False),
            put("_bc_masked", False),
            put("_bc_clear", False),
            put("_bc_observer_body", None),
            put("_bc_target_body", None),
            put("_bc_bodies", "state.spatial_scene.entities"),
            {
                "for_each": {
                    "collection": "_bc_bodies",
                    "as": "_bc_body",
                    "do": [
                        put(
                            "_bc_observer_body",
                            "_bc_body",
                            "_bc_body.id == _observer_id and _bc_body.active == true",
                        ),
                        put(
                            "_bc_target_body",
                            "_bc_body",
                            "_bc_body.id == _target_id and _bc_body.active == true",
                        ),
                    ],
                }
            },
            branch(
                supported,
                [
                    put("_bc_origin", f"{contact}.origin"),
                    *bounds("_bc_observer_body", "_bc_observer"),
                    *bounds("_bc_target_body", "_bc_target"),
                    branch(
                        actual_origin
                        + " and "
                        + " and ".join(
                            f"target.center[{i}] >= _bc_target_lo[{i}] and "
                            f"target.center[{i}] <= _bc_target_hi[{i}]"
                            for i in range(3)
                        ),
                        [
                            put("_bc_supported", True),
                            put("_bc_clear", True),
                            put("_bc_axes", [0, 1, 2]),
                            put("_bc_wall_axes", [0, 1]),
                            put(
                                "_bc_delta",
                                [f"target.center[{i}] - _bc_origin[{i}]" for i in range(3)],
                            ),
                            put(
                                "_bc_corners",
                                [
                                    [
                                        f"_bc_target_{x}[0]",
                                        f"_bc_target_{y}[1]",
                                        f"_bc_target_{z}[2]",
                                    ]
                                    for x in ("lo", "hi")
                                    for y in ("lo", "hi")
                                    for z in ("lo", "hi")
                                ],
                            ),
                            put("_bc_geometry_list", "state.spatial_scene.geometry"),
                            {
                                "for_each": {
                                    "collection": "_bc_geometry_list",
                                    "as": "_bc_geometry",
                                    "do": [
                                        branch(
                                            "_bc_geometry.active == true and "
                                            "_bc_geometry.visibility == 'opaque'",
                                            [
                                                branch(
                                                    "_bc_geometry.volume.kind == 'box'",
                                                    [
                                                        *wall_proof(),
                                                        put(
                                                            "_bc_box_lo", "_bc_geometry.volume.min"
                                                        ),
                                                        put(
                                                            "_bc_box_hi", "_bc_geometry.volume.max"
                                                        ),
                                                        *intersects(),
                                                    ],
                                                ),
                                                # Unknown volumes never prove full occlusion.
                                                put(
                                                    "_bc_clear",
                                                    False,
                                                    "_bc_geometry.volume.kind != 'box'",
                                                ),
                                            ],
                                        ),
                                    ],
                                }
                            },
                            {
                                "for_each": {
                                    "collection": "_bc_bodies",
                                    "as": "_bc_body",
                                    "do": [
                                        branch(
                                            "_bc_body.active == true and "
                                            "_bc_body.visibility == 'opaque' and "
                                            "_bc_body.id != _observer_id and "
                                            "_bc_body.id != _target_id",
                                            [
                                                *bounds("_bc_body", "_bc_other"),
                                                put("_bc_box_lo", "_bc_other_lo"),
                                                put("_bc_box_hi", "_bc_other_hi"),
                                                *intersects(),
                                            ],
                                        ),
                                    ],
                                }
                            },
                            put("_bc_clear", False, "_bc_masked == true"),
                            put(
                                "_bc_supported", False, "_bc_clear == false and _bc_masked == false"
                            ),
                        ],
                    ),
                ],
            ),
            {"publish": {"fields": {}}},
        ],
    }


def actor_helper(param):
    record = ROOT + ".actors[_actor_id]"
    path = ROOT + ".actors.{_actor_id}"
    contact_path = path + ".contacts.{_bc_observer_id}"
    return {
        "description": "Refresh one actor's private visual contacts without rolling or spending.",
        "parameters": [param("actor", "string", resolve="entity")],
        "effects": [
            put("_bc_all_blocked", True),
            put("_bc_all_supported", True),
            put("_bc_any_clear", False),
            put("_bc_had_exact", f"{record}.had_exact"),
            put("_bc_bodies", "state.spatial_scene.entities"),
            {
                "for_each": {
                    "collection": "_bc_bodies",
                    "as": "_bc_body",
                    "do": [
                        branch(
                            "_bc_body.id == _actor_id and _bc_body.active == true",
                            [
                                *bounds("_bc_body", "_bc_current"),
                                put(path + ".body_min", "_bc_current_lo"),
                                put(path + ".body_max", "_bc_current_hi"),
                            ],
                        ),
                    ],
                }
            },
            put("_bc_observer_ids", f"{record}.observer_ids"),
            {
                "for_each": {
                    "collection": "_bc_observer_ids",
                    "as": "_bc_observer_id",
                    "do": [
                        put(
                            "_bc_had_exact",
                            True,
                            f"{record}.contacts[_bc_observer_id].knowledge == 'CONFIRMED'",
                        ),
                        {
                            "call": "td_break_observer",
                            "params": {"observer": "{_bc_observer_id}", "target": "{_actor_id}"},
                            "export_context": {
                                "_bc_supported": "_bc_supported",
                                "_bc_masked": "_bc_masked",
                                "_bc_clear": "_bc_clear",
                            },
                        },
                        put("_bc_all_supported", False, "_bc_supported == false"),
                        put(
                            "_bc_all_blocked",
                            False,
                            "_bc_masked == false or _bc_supported == false",
                        ),
                        branch(
                            "_bc_supported == true and _bc_masked == true",
                            [
                                put(
                                    contact_path + ".knowledge",
                                    "'LAST_KNOWN'",
                                    f"{record}.contacts[_bc_observer_id].knowledge == 'CONFIRMED'",
                                ),
                            ],
                        ),
                        branch(
                            "_bc_supported == true and _bc_clear == true",
                            [
                                put("_bc_any_clear", True),
                                put("_bc_had_exact", True),
                                put(contact_path + ".knowledge", "'CONFIRMED'"),
                                put(contact_path + ".last_exact", "actor.center"),
                                put(
                                    contact_path + ".last_exact_revision",
                                    "state.spatial_scene.map.revision",
                                ),
                                put(contact_path + ".region", None),
                            ],
                        ),
                    ],
                }
            },
            branch(
                "_bc_all_blocked == true and _bc_had_exact == true and "
                f"{record}.fully_lost == false",
                [
                    {"increment": {"field": path + ".epoch", "amount": 1}},
                    put(path + ".fully_lost", True),
                ],
            ),
            put(path + ".fully_lost", False, "_bc_any_clear == true"),
            put(path + ".had_exact", "_bc_had_exact"),
            put(path + ".all_blocked", "_bc_all_blocked"),
            put(path + ".proof_supported", "_bc_all_supported"),
            put(path + ".checked_revision", "state.spatial_scene.map.revision"),
            {"publish": {"fields": {}}},
        ],
    }


def refresh_helper(param):
    return {
        "description": "Reconcile owned visual evidence after a committed map change; "
        "no public sensory queries.",
        "parameters": [],
        "effects": [
            {
                "spatial_query": {"scene": "state.spatial_scene", "operation": "snapshot"},
                "store_as": "_bc_scene",
            },
            put("_bc_actor_ids", ROOT + ".actor_ids"),
            {
                "for_each": {
                    "collection": "_bc_actor_ids",
                    "as": "_bc_target_id",
                    "do": [
                        {
                            "call": "td_break_actor",
                            "params": {"actor": "{_bc_target_id}"},
                            "condition": "'.' not in _bc_target_id and "
                            "'{' not in _bc_target_id and '}' "
                            "not in _bc_target_id",
                        },
                    ],
                }
            },
            {"publish": {"fields": {}}},
        ],
    }


def price_calculations():
    eligible = (
        f"{RECORD} != null and actor.class_id == 'GHOST' and '{ENTRY}' in actor.specialties "
        f"and {RECORD}.epoch > {RECORD}.used_epoch"
    )
    return [
        put("_bc_discount", eligible),
        put(
            "_bc_ap",
            "max(1, definitions.parameters.stealth.break_contact_ap - "
            "occurrences(one(_bc_discount), true) * "
            "definitions.parameters.specialty.evader.break_contact.ap_discount)",
        ),
        put(
            "_bc_difficulty",
            f"{ACTIVITY}.base_difficulty + occurrences(one(_bc_discount), true) * "
            "definitions.parameters.specialty.evader.break_contact.difficulty_offset",
        ),
    ]


def checks():
    equipment = ROOT + f".equipment[{ACTIVITY}.equipment_id]"
    conditions = [
        (
            "actor.active == true and actor.downed == false and actor.side == active_side",
            "This actor cannot perform the activity now.",
        ),
        (
            "'.' not in _actor_id and '{' not in _actor_id and '}' not in _actor_id",
            "This identity is unsupported for the reference activity.",
        ),
        (
            "'.' not in activity_id and '{' not in activity_id and '}' not in activity_id",
            "This activity identity is unsupported.",
        ),
        (
            f"'{ENTRY}' not in actor.specialties or actor.class_id == 'GHOST'",
            "This Entry is not legal for this class.",
        ),
        (f"{RECORD} != null and {ACTIVITY} != null", "No owned reference activity is available."),
        (
            f"{ACTIVITY}.kind == 'QUIET_HELD_METAL_SEARCH' and "
            f"{ACTIVITY}.uncertainty == 'UNCERTAIN' and {ACTIVITY}.inevitable_evidence == 'NONE'",
            "This is not the supported uncertain quiet-metal activity.",
        ),
        (
            f"{ACTIVITY}.actor_id == _actor_id and {ACTIVITY}.id == activity_id "
            f"and {ACTIVITY}.consumed == false",
            "This owned activity is unavailable or already performed.",
        ),
        (
            f"{ACTIVITY}.map_id == state.spatial_scene.map.id and "
            f"{ACTIVITY}.map_revision == state.spatial_scene.map.revision and "
            f"{RECORD}.checked_revision == state.spatial_scene.map.revision",
            "The activity's physical scene has changed.",
        ),
        (
            " and ".join(f"{ACTIVITY}.actor_center[{i}] == actor.center[{i}]" for i in range(3)),
            "This stationary activity was authored for another position.",
        ),
        (
            f"{RECORD}.proof_supported == true and {RECORD}.all_blocked == true "
            f"and {RECORD}.fully_lost == true and {RECORD}.had_exact == true",
            "Loss of every exact observer's supported sight has not been established.",
        ),
        (
            f"{equipment} != null and {equipment}.kind == 'LOOSE_METAL_EQUIPMENT'",
            "The authored loose metal equipment is unavailable.",
        ),
        (
            f"{equipment}.active == true and {equipment}.held == true and "
            f"{equipment}.held_by == _actor_id",
            "The authored loose metal equipment is not already held by this actor.",
        ),
        (
            f"{ACTIVITY}.failure.kind == 'LOCALIZABLE_NOISE' and "
            f"{ACTIVITY}.failure.listener in {RECORD}.observer_ids",
            "The reference listening evidence is unavailable.",
        ),
        ("actor.ap >= _bc_ap", "This actor cannot afford the activity."),
        (
            f"len({RECORD}.contacts[{ACTIVITY}.failure.listener].evidence) < 128",
            "The bounded reference evidence record is full; no activity was performed.",
        ),
    ]
    # The whole body enclosure, not a fabricated exact point, must fit the authored noise region.
    for i in range(3):
        conditions.extend(
            [
                (
                    f"{ACTIVITY}.failure.region.min[{i}] <= {RECORD}.body_min[{i}] and "
                    f"{ACTIVITY}.failure.region.max[{i}] >= {RECORD}.body_max[{i}]",
                    "The actual actor does not fit the authored noise region.",
                ),
                (
                    f"{ACTIVITY}.failure.region.min[{i}] < {ACTIVITY}.failure.region.max[{i}]",
                    "The authored noise region is invalid.",
                ),
                (
                    f"{ACTIVITY}.failure.region.min[{i}] >= "
                    f"state.spatial_scene.map.bounds.min[{i}] and "
                    f"{ACTIVITY}.failure.region.max[{i}] <= "
                    f"state.spatial_scene.map.bounds.max[{i}]",
                    "The authored noise region lies outside this map.",
                ),
            ]
        )
    return [{"condition": c, "reason": reason} for c, reason in conditions]


def noise_effects():
    old = "_bc_old"
    path = ROOT + ".actors.{_actor_id}.contacts.{_bc_listener}"
    smaller = " and ".join(
        f"_bc_region.min[{i}] >= {old}.region.min[{i}] and "
        f"_bc_region.max[{i}] <= {old}.region.max[{i}]"
        for i in range(3)
    )
    return [
        put("_bc_listener", f"{ACTIVITY}.failure.listener"),
        put(old, RECORD + ".contacts[_bc_listener]"),
        put("_bc_region", f"{ACTIVITY}.failure.region"),
        branch(
            f"{old}.knowledge != 'CONFIRMED'",
            [
                branch(
                    f"{old}.knowledge != 'APPROXIMATE' or ({old}.region != null and {smaller})",
                    [
                        put(path + ".region", "_bc_region"),
                        put(path + ".knowledge", "'APPROXIMATE'"),
                    ],
                ),
                put("_bc_evidence", f"{old}.evidence"),
                {
                    "list_push": {
                        "list": "_bc_evidence",
                        "value": {
                            "id": "activity_id",
                            "kind": "'LOCALIZABLE_NOISE'",
                            "region": "_bc_region",
                            "map_revision": "state.spatial_scene.map.revision",
                        },
                    }
                },
                put(path + ".evidence", "_bc_evidence"),
            ],
        ),
    ]


def specification(actor, param):
    path = ROOT + ".actors.{_actor_id}"
    return {
        "capability": "trigger-discipline.stealth",
        "description": "Perform the already-owned stationary quiet-held-metal activity "
        "while guards search/listen. Facts, difficulty, observers and "
        "bounded noise consequences come only from admitted private "
        "state. Ordinary STEALTH pays 2 AP; qualified BREAK CONTACT "
        "pays 1 AP and -2 difficulty for the next attempt after all "
        "actual exact observers lose supported LOS. No movement, "
        "equipment manipulation, automatic hiding, staleness timer or "
        "acoustics solver is added.",
        "parameters": [
            actor,
            param("activity_id", "string"),
            param("active_side", "string", from_state="state.active_side"),
        ],
        "effects": [
            {
                "require": {
                    "condition": f"{ROOT} != null and _actor_id in {ROOT}.actors "
                    f"and activity_id in {RECORD}.activities",
                    "error": "No owned reference activity is available.",
                }
            },
            {"call": "td_refresh_break_contacts", "params": {}},
            *price_calculations(),
            *[{"require": {"condition": c["condition"], "error": c["reason"]}} for c in checks()],
            # Revalidate the real body, without exposing private query origins.
            put("_bc_activity_body", None),
            put("_bc_bodies", "state.spatial_scene.entities"),
            {
                "for_each": {
                    "collection": "_bc_bodies",
                    "as": "_bc_body",
                    "do": [
                        put(
                            "_bc_activity_body",
                            "_bc_body",
                            "_bc_body.id == _actor_id and _bc_body.active == true",
                        ),
                    ],
                }
            },
            {
                "require": {
                    "condition": "_bc_activity_body != null",
                    "error": "The actor has no current physical body.",
                }
            },
            *bounds("_bc_activity_body", "_bc_activity"),
            *[
                {
                    "require": {
                        "condition": f"{ACTIVITY}.failure.region.min[{i}] <= _bc_activity_lo[{i}] "
                        f"and {ACTIVITY}.failure.region.max[{i}] >= _bc_activity_hi[{i}]",
                        "error": "The actual actor does not fit the authored noise region.",
                    }
                }
                for i in range(3)
            ],
            {"dice": {"expression": "d20"}, "store_as": "_bc_roll"},
            put(
                "_bc_total",
                f"_bc_roll.total + actor.skills.stealth + actor.aptitudes[{ACTIVITY}.aptitude]",
            ),
            put("_bc_success", "_bc_total >= _bc_difficulty"),
            {"decrement": {"field": "{_actor_id}.ap", "amount": "_bc_ap"}},
            put(path + ".activities.{activity_id}.consumed", True),
            put(path + ".used_epoch", f"{RECORD}.epoch"),
            branch("_bc_success == false", noise_effects()),
            put(
                path + ".last_attempt",
                {
                    "activity_id": "activity_id",
                    "success": "_bc_success",
                    "roll": "_bc_roll.total",
                    "difficulty": "_bc_difficulty",
                    "ap_spent": "_bc_ap",
                    "map_revision": "state.spatial_scene.map.revision",
                },
            ),
            {
                "publish": {
                    "fields": {
                        "status": "'PERFORMED'",
                        "success": "_bc_success",
                        "roll": "_bc_roll.total",
                        "total": "_bc_total",
                        "difficulty": "_bc_difficulty",
                        "ap_spent": "_bc_ap",
                    }
                }
            },
        ],
        "costs_action": True,
    }


def controls():
    return {
        "confirmation": "none",
        "response": "on_request",
        "actor_parameter": "actor",
        "calculations": price_calculations(),
        "checks": checks(),
        "preview": {
            "version": 1,
            "title": "Keep held metal quiet",
            "summary": [
                {"label": "AP cost", "value": {"path": "context._bc_ap"}},
                {"label": "Difficulty", "value": {"path": "context._bc_difficulty"}},
            ],
        },
    }


def state_schemas(existing):
    """Optional admitted facts, never a production opportunity-creation or inventory editor."""
    from .network_state_schema import _array, _enum, _object, _records

    identity = {"type": "string", "minLength": 1, "maxLength": 128}
    text = {"type": "string", "minLength": 1, "maxLength": 128}
    flag = {"type": "boolean"}
    count = {"type": "integer", "minimum": 0}
    vector = _array({"type": "number"}, 3, 3)
    region = _object({"min": vector, "max": vector})
    nullable_region = {**region, "type": ["object", "null"]}
    knowledge = _enum("UNAWARE", "SUSPICIOUS", "APPROXIMATE", "LAST_KNOWN", "CONFIRMED")
    contact = _object(
        {
            "source_id": identity,
            "source_kind": text,
            "origin": vector,
            "basis_center": vector,
            "basis_stance": text,
            "knowledge": knowledge,
            "last_exact": vector,
            "last_exact_revision": count,
            "region": nullable_region,
            "evidence": _array(
                _object(
                    {"id": identity, "kind": text, "region": nullable_region, "map_revision": count}
                ),
                128,
            ),
        }
    )
    activity = _object(
        {
            "id": identity,
            "actor_id": identity,
            "kind": text,
            "uncertainty": text,
            "inevitable_evidence": text,
            "map_id": identity,
            "map_revision": count,
            "actor_center": vector,
            "equipment_id": identity,
            "consumed": flag,
            "base_difficulty": {"type": "integer", "minimum": 1, "maximum": 60},
            "aptitude": _enum("acuity", "force", "nerve", "precision", "presence", "reason"),
            "failure": _object({"kind": text, "listener": identity, "region": region}),
        }
    )
    observers = _array(identity, 16, 1)
    observers["uniqueItems"] = True
    actors = _array(identity, 8, 1)
    actors["uniqueItems"] = True
    record = _object(
        {
            "observer_ids": observers,
            "contacts": _records(contact),
            "activities": _records(activity),
            "had_exact": flag,
            "fully_lost": flag,
            "epoch": count,
            "used_epoch": {"type": "integer", "minimum": -1},
            "checked_revision": count,
            "proof_supported": flag,
            "all_blocked": flag,
            "body_min": vector,
            "body_max": vector,
            "last_attempt": _object(
                {
                    "activity_id": identity,
                    "success": flag,
                    "roll": count,
                    "difficulty": {"type": "integer"},
                    "ap_spent": count,
                    "map_revision": count,
                }
            ),
        },
        (
            "observer_ids",
            "contacts",
            "activities",
            "had_exact",
            "fully_lost",
            "epoch",
            "used_epoch",
            "checked_revision",
            "proof_supported",
            "all_blocked",
        ),
    )
    facts = _object(
        {
            "actor_ids": actors,
            "actors": _records(record),
            "equipment": _records(
                _object(
                    {
                        "id": identity,
                        "kind": text,
                        "held_by": identity,
                        "held": flag,
                        "active": flag,
                    }
                )
            ),
        }
    )
    references = []

    def ref(source, target, target_key="state"):
        references.append(
            {"source": ["break_contact", *source], "target_key": target_key, "target": target}
        )

    entities = ["*", "$key"]
    ref(["actor_ids", "*"], entities, "entities")
    ref(["actors", "*", "$key"], ["break_contact", "actor_ids", "*"])
    ref(["actor_ids", "*"], ["break_contact", "actors", "*", "$key"])
    ref(["actors", "*", "observer_ids", "*"], entities, "entities")
    ref(
        ["actors", "$a", "contacts", "*", "$key"],
        ["break_contact", "actors", "$a", "observer_ids", "*"],
    )
    ref(
        ["actors", "$a", "observer_ids", "*"],
        ["break_contact", "actors", "$a", "contacts", "*", "$key"],
    )
    ref(["actors", "*", "activities", "*", "actor_id"], entities, "entities")
    ref(
        ["actors", "*", "activities", "*", "equipment_id"],
        ["break_contact", "equipment", "*", "$key"],
    )
    ref(
        ["actors", "$a", "activities", "*", "failure", "listener"],
        ["break_contact", "actors", "$a", "observer_ids", "*"],
    )
    ref(["actors", "*", "activities", "*", "map_id"], ["spatial_scene", "map", "id"])
    ref(["equipment", "*", "held_by"], entities, "entities")
    state = next(row for row in existing if row["key"] == "state")
    state["schema"]["properties"]["break_contact"] = facts
    state["references"].extend(references)
    # The existing runtime mutates its state/entities/combat roots. Make the
    # world-state root private through the host's existing projection contract;
    # public panels continue to select their explicit, filtered fields.
    state["visibility"] = "engine"
    return existing


def attach_refresh(runtime_tools, public_names):
    """Refresh only after a real scene change, including nested prepared-fire mutations."""

    def visits(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from visits(child)
        elif isinstance(value, list):
            for child in value:
                yield from visits(child)

    changing = {
        name
        for name, tool in runtime_tools.items()
        if any("spatial_mutation" in item for item in visits(tool["effects"]))
    }
    while True:
        more = {
            name
            for name, tool in runtime_tools.items()
            if any(item.get("call") in changing for item in visits(tool["effects"]))
        }
        if more <= changing:
            break
        changing |= more
    for name in changing & set(public_names):
        effects = runtime_tools[name]["effects"]
        effects.insert(
            0, put("_bc_prior_revision", "state.spatial_scene.map.revision", ROOT + " != null")
        )
        effects.append(
            {
                "call": "td_refresh_break_contacts",
                "params": {},
                "condition": ROOT
                + " != null and state.spatial_scene.map.revision != _bc_prior_revision",
            }
        )
