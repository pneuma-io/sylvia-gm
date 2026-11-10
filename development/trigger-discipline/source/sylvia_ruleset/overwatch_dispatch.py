"""TD ordering for shooter-owned watches, using shared geometry and normal attacks.

Legacy authored preparation stays in its original slot. New lane preparations live
by shooter; no selected actor or preview is allowed to replace another shooter's watch.
"""

from . import overwatch_lanes, sentinel


def set_(field, value, condition=None):
    effect = {"set": {"field": field, "value": value}}
    if condition:
        effect["condition"] = condition
    return effect


def when(condition, effects):
    return {"condition": condition, "on_true": effects, "on_false": []}


def each(collection, name, effects, condition=None):
    return {
        "for_each": {
            "collection": collection,
            "as": name,
            "do": [when(condition, effects)] if condition else effects,
        }
    }


def push(collection, value):
    return {"list_push": {"list": collection, "value": value}, "store_as": collection}


def call(tool, params, exports=None):
    effect = {"call": tool, "params": params}
    if exports:
        effect["export_context"] = exports
    return effect


def query(operation, args, store):
    body = {"scene": "state.spatial_scene", "operation": operation}
    if operation != "snapshot":
        body["query"] = args
    return {"spatial_query": body, "store_as": store}


def common_params(param):
    return [
        param("actor", "entity_id", resolve="entity"),
        param("prepared", "object", from_state="state.prepared"),
        param("watch_bodies", "array", items="object", from_state="state.spatial_scene.entities"),
        param("watch_zones", "array", items="object", from_state="state.spatial_scene.zones"),
        param("map_id", "string", from_state="state.spatial_scene.map.id"),
    ]


def select_watch():
    return [
        set_("_watch", "prepared.overwatch"),
        set_("_watch", "prepared.overwatchers[_watcher_id]", "kind == 'LANE'"),
        set_("_watch", "prepared.suppression", "kind == 'SUPPRESSION'"),
    ]


def watch_field():
    """Only these internal helpers choose a storage path; clients never supply one."""
    return [
        set_("_watch_root", "'state.prepared.overwatch'"),
        set_("_watch_root", "'state.prepared.overwatchers.' + _watcher_id", "kind == 'LANE'"),
        set_("_watch_root", "'state.prepared.suppression'", "kind == 'SUPPRESSION'"),
    ]


def eligibility():
    effects = (
        select_watch()
        + watch_field()
        + [
            set_("_watch_stationary", True),
            set_(
                "_watch_stationary",
                False,
                "_watch.stationary_origin != null and "
                "(_watch.stationary_origin[0] != watcher.position_cell[0] or "
                "_watch.stationary_origin[1] != watcher.position_cell[1])",
            ),
            set_("_watch_stationary", False, "_watch.owner != _watcher_id"),
            *overwatch_lanes.validity("_watch", "watcher", "_watcher_id", root="{_watch_root}"),
            set_(
                "_eligible",
                "actor.active == true and actor.downed == false and "
                'watcher.active == true and watcher.downed == '
                'false and _actor_id != _watcher_id and '
                "_watch.active == true and _watch.owner == _watcher_id and "
                "watcher.weapon.location == 'READY' and watcher.weapon.controlled_by == null",
            ),
            set_(
                "_eligible",
                False,
                "_eligible == true and kind != 'SUPPRESSION' and (watcher.side == actor.side or "
                '_watch.shots <= 0 or watcher.weapon.chamber !='
                " 'LIVE' or _watch_stationary != true)",
            ),
            set_(
                "_eligible",
                False,
                "_eligible == true and kind == 'SUPPRESSION' and _watch.capacity <= 0",
            ),
            *sentinel.counter_prep_calculations("watcher"),
            set_(
                "_eligible",
                False,
                "event != 'MOVEMENT' and kind != 'SUPPRESSION' and "
                "(_watch.counter_prep != true or _cp_eligible != true)",
            ),
        ]
    )
    return effects


def probe(param):
    """Find the first real visible point on the caller's authoritative physical path."""
    return {
        "description": "Inspect one live prepared response on a shared-map path.",
        "parameters": common_params(param)
        + [
            param("watcher", "entity_id", resolve="entity"),
            param("kind", "string"),
            param("event", "string"),
            {
                **param("path", "array", items="array"),
                "items": {"type": "array", "items": {"type": "number"}},
            },
        ],
        "effects": eligibility()
        + [
            set_("_found", False),
            set_("_supported", True),
            set_("_point", None),
            set_("_distance", None),
            when(
                "_eligible == true and event == 'MOVEMENT'",
                [
                    query(
                        "exposure_path",
                        {
                            "map_id": "{map_id}",
                            "observer": {"point": "{watcher.muzzle}"},
                            "target": {"entity_id": "{_actor_id}"},
                            "samples": {"horizontal": 3, "vertical": 3},
                            "include": ["geometry", "entities"],
                            "path": "{path}",
                            "zone_id": "{_watch.zone_id}",
                            "max_tests": 4096,
                        },
                        "_probe",
                    ),
                    set_("_found", "_probe.found"),
                    set_("_supported", "_probe.supported"),
                    set_("_point", "_probe.point", "_probe.found == true"),
                    set_("_distance", "_probe.distance", "_probe.found == true"),
                ],
            ),
            when(
                "_eligible == true and event != 'MOVEMENT'",
                [
                    query(
                        "crossed_zones",
                        {
                            "map_id": "{map_id}",
                            "path": ["{actor.center}", "{actor.center}"],
                            "max_crossings": 4096,
                        },
                        "_at_point",
                    ),
                    set_("_point_hits", "_at_point.crossings"),
                    set_("_inside", False),
                    each(
                        "_point_hits",
                        "_point_hit",
                        [set_("_inside", True, "_point_hit.zone_id == _watch.zone_id")],
                    ),
                    when(
                        "_inside == true",
                        [
                            query(
                                "exposure",
                                {
                                    "map_id": "{map_id}",
                                    "observer": {"point": "{watcher.muzzle}"},
                                    "target": {"entity_id": "{_actor_id}"},
                                    "samples": {"horizontal": 3, "vertical": 3},
                                    "include": ["geometry", "entities"],
                                },
                                "_point_exposure",
                            ),
                            set_("_found", "_point_exposure.exposure > 0"),
                            set_("_point", "actor.center"),
                            set_("_distance", 0),
                        ],
                    ),
                ],
            ),
            set_("_side", "watcher.side"),
        ],
        "metadata": {"costs_action": False},
    }


def resolve(param, attack):
    """Recheck the actual current world at a paused event before spending one response."""
    effects = eligibility() + [
        set_("_ready", False),
        when(
            "_eligible == true",
            [
                query(
                    "crossed_zones",
                    {
                        "map_id": "{map_id}",
                        "path": ["{actor.center}", "{actor.center}"],
                        "max_crossings": 4096,
                    },
                    "_here",
                ),
                set_("_inside", False),
                set_("_hits", "_here.crossings"),
                each("_hits", "_hit", [set_("_inside", True, "_hit.zone_id == _watch.zone_id")]),
                set_("_ready", "_inside"),
            ],
        ),
        set_(
            "_aim", ["actor.center[0] + 0.000001", "actor.center[1] + 0.000001", "actor.center[2]"]
        ),
        set_("_shot_ready", "_ready"),
        set_("_reaction_record", "'state.last_trigger.overwatch'"),
        set_("_reaction_record", "'state.last_counter_prep'", "event != 'MOVEMENT'"),
        *attack(
            prefix="_shot",
            shooter="watcher",
            shooter_id="_watcher_id",
            target="actor",
            target_id="_actor_id",
            target_point="_aim",
            record="{_reaction_record}",
            condition="_shot_ready == true and kind != 'SUPPRESSION'",
            difficulty_modifier="0",
            critical_effect="_critical",
            consume_round=True,
            choose_critical=True,
        ),
        {
            "decrement": {"field": "{_watch_root}.shots", "amount": 1},
            "condition": "_shot_ready == true and kind != 'SUPPRESSION'",
        },
        set_(
            "{_watch_root}.active",
            False,
            "_shot_ready == true and kind != 'SUPPRESSION' and _watch.shots <= 1",
        ),
        set_(
            "state.last_counter_prep.event",
            "event",
            "event != 'MOVEMENT' and kind != 'SUPPRESSION' and _shot_ready == true",
        ),
        set_("_suppshot_ready", "_ready and event == 'MOVEMENT'"),
        *attack(
            prefix="_suppshot",
            shooter="watcher",
            shooter_id="_watcher_id",
            target="actor",
            target_id="_actor_id",
            target_point="_aim",
            record="state.last_trigger.suppression",
            condition="_suppshot_ready == true and kind == 'SUPPRESSION'",
            difficulty_modifier="definitions.parameters.suppression.attack_difficulty_modifier",
            critical_effect="_suppcritical",
            consume_round=False,
            apply_aim=False,
            choose_critical=True,
        ),
        {
            "decrement": {"field": "{_watch_root}.capacity", "amount": 1},
            "condition": "_suppshot_ready == true and kind == 'SUPPRESSION'",
        },
        set_(
            "{_watch_root}.active",
            False,
            "_suppshot_ready == true and kind == 'SUPPRESSION' and _watch.capacity <= 1",
        ),
        when(
            "kind == 'SUPPRESSION' and event != 'MOVEMENT'",
            [
                call(
                    "td_suppression_exposure",
                    {
                        "actor": "{_actor_id}",
                        "suppressor": "{_watcher_id}",
                        "event": "{event}",
                        "inspect_only": False,
                    },
                    {"_suppshot_ready": "_incoming_suppression_ready"},
                ),
            ],
        ),
        set_(
            "_fired",
            "(_shot_ready == true and kind != 'SUPPRESSION') or "
            "(_suppshot_ready == true and kind == 'SUPPRESSION')",
        ),
    ]
    return {
        "description": "Resolve one prepared response against the current paused world.",
        "parameters": common_params(param)
        + [
            param("watcher", "entity_id", resolve="entity"),
            param("kind", "string"),
            param("event", "string"),
        ],
        "effects": effects,
        "metadata": {"costs_action": False},
    }


def candidates():
    """Scene order is only enumeration; same-moment resolution asks the controller."""
    return [
        set_("_candidates", []),
        each(
            "watch_bodies",
            "_body",
            [
                set_(
                    "_entry",
                    {
                        "id": "prepared.overwatchers[_body.id].zone_id",
                        "watcher": "_body.id",
                        "kind": "'LANE'",
                    },
                ),
                push("_candidates", "_entry"),
            ],
            'prepared.overwatchers != null and _body.id in '
            'prepared.overwatchers and '
            'prepared.overwatchers[_body.id].active == true',
        ),
        when(
            "prepared.overwatch.active == true and prepared.overwatch.owner != null",
            [
                set_(
                    "_entry",
                    {
                        "id": "'legacy-overwatch'",
                        "watcher": "prepared.overwatch.owner",
                        "kind": "'LEGACY'",
                    },
                ),
                push("_candidates", "_entry"),
            ],
        ),
        when(
            "prepared.suppression.active == true and prepared.suppression.owner != null",
            [
                set_(
                    "_entry",
                    {
                        "id": "'suppression'",
                        "watcher": "prepared.suppression.owner",
                        "kind": "'SUPPRESSION'",
                    },
                ),
                push("_candidates", "_entry"),
            ],
        ),
    ]


def gather_events():
    return candidates() + [
        set_("_events", []),
        set_("_exposure_path_supported", True),
        each(
            "_candidates",
            "_candidate",
            [
                call(
                    "td_watch_probe",
                    {
                        "actor": "{_actor_id}",
                        "watcher": "{_candidate.watcher}",
                        "kind": "{_candidate.kind}",
                        "event": "{_event}",
                        "path": "{_route}",
                    },
                    {
                        "_probe_found": "_found",
                        "_probe_supported": "_supported",
                        "_probe_point": "_point",
                        "_probe_distance": "_distance",
                        "_probe_side": "_side",
                    },
                ),
                set_("_exposure_path_supported", False, "_probe_supported != true"),
                when(
                    "_probe_found == true",
                    [
                        set_(
                            "_event_detail",
                            {
                                "distance": "_probe_distance",
                                "point": "_probe_point",
                                "side": "_probe_side",
                            },
                        ),
                        set_("_entry", "merge(_candidate, _event_detail)"),
                        push("_events", "_entry"),
                    ],
                ),
            ],
            "_candidate.id not in _responded",
        ),
        {"sort": {"collection": "_events", "by": "distance", "direction": "asc"}},
    ]


def window():
    """Choose each next effect within a side's tier; update the world after each."""
    return [
        set_("_done", []),
        each(
            "_events",
            "_tick",
            [
                set_("_pool", []),
                set_("_has_nonactive", False),
                each(
                    "_events",
                    "_pending",
                    [
                        set_("_has_nonactive", True, "_pending.side != active_side"),
                    ],
                    'abs(_pending.distance - _window.distance) <= '
                    '0.000000001 and _pending.id not in _done',
                ),
                each(
                    "_events",
                    "_pending",
                    [
                        push("_pool", "_pending"),
                    ],
                    'abs(_pending.distance - _window.distance) <= '
                    '0.000000001 and _pending.id not in _done and '
                    "(_has_nonactive != true or _pending.side != active_side)",
                ),
                when(
                    "len(_pool) > 0",
                    [
                        set_("_chosen", "_pool[0]"),
                        # Pairwise selection supports any number without exceeding the owner's
                        # bounded choice-option API. Every candidate can be selected next.
                        each(
                            "_pool",
                            "_alternative",
                            [
                                set_("_choices", []),
                                set_(
                                    "_option",
                                    {
                                        "id": "_chosen.id",
                                        "label": "_chosen.watcher + ' (' + _chosen.kind + ')'",
                                    },
                                ),
                                push("_choices", "_option"),
                                set_(
                                    "_option",
                                    {
                                        "id": "_alternative.id",
                                        "label": ("_alternative.watcher + ' (' + "
                                                  "_alternative.kind + ')'"),
                                    },
                                ),
                                push("_choices", "_option"),
                                {
                                    "request_choice": {
                                        "choice_id": "overwatch-window-order",
                                        "prompt": 'Choose the next same-side prepared response at'
                                        ' this point.',
                                        "options": "_choices",
                                    },
                                    "store_as": "_choice",
                                },
                                set_("_chosen", "_alternative", "_choice == _alternative.id"),
                            ],
                            "_alternative.id != _chosen.id",
                        ),
                        push("_done", "_chosen.id"),
                        call(
                            "td_watch_resolve",
                            {
                                "actor": "{_actor_id}",
                                "watcher": "{_chosen.watcher}",
                                "kind": "{_chosen.kind}",
                                "event": "{_event}",
                            },
                            {"_response_fired": "_fired"},
                        ),
                        when(
                            "_response_fired == true",
                            [
                                set_(
                                    "_record",
                                    {
                                        "owner": "_chosen.watcher",
                                        "kind": "_chosen.kind",
                                        "point": "actor.center",
                                    },
                                ),
                                {
                                    "list_push": {"list": "_reactions", "value": "_record"},
                                    "store_as": "_reactions",
                                },
                                set_(
                                    "_prep_interruption",
                                    "'COUNTER_PREP'",
                                    "_event != 'MOVEMENT' and actor.downed == true "
                                    "and _chosen.kind != 'SUPPRESSION'",
                                ),
                                set_(
                                    "_prep_interruption",
                                    "'SUPPRESSION'",
                                    "_event != 'MOVEMENT' and actor.downed == true "
                                    "and _chosen.kind == 'SUPPRESSION'",
                                ),
                            ],
                        ),
                    ],
                ),
            ],
            "actor.active == true and actor.downed == false",
        ),
    ]


def pose(point):
    return [
        set_("_pose_position", [f"{point}[0]", f"{point}[1]", "_feet"]),
        query("snapshot", {}, "_pose_snapshot"),
        {
            "spatial_mutation": {
                "scene": "state.spatial_scene",
                "map_id": "{map_id}",
                "expected_revision": "{_pose_snapshot.geometry_revision}",
                "target": {"kind": "entity", "id": "{_actor_id}"},
                "patch": {"position": "{_pose_position}"},
            },
            "store_as": "_pose_mutation",
        },
        set_("{_actor_id}.position_cell", [f"floor({point}[0])", f"floor({point}[1])"]),
        set_("{_actor_id}.center", [f"{point}[0]", f"{point}[1]", "_origin_center[2]"]),
        set_(
            "{_actor_id}.muzzle",
            [
                f"{point}[0] + _origin_muzzle[0] - _origin_center[0]",
                f"{point}[1] + _origin_muzzle[1] - _origin_center[1]",
                "_origin_muzzle[2]",
            ],
        ),
    ]


def movement(param, weighted_prefix, movement_break):
    """Price one route, pause at each physical window, resume with one total AP charge."""
    effects = [
        set_("_event", "'MOVEMENT'"),
        set_("_trigger_paused", False),
        set_("_ordinary_distance", None),
        set_("_ordinary_geometry_revision", None),
        set_("_exposure_path_supported", True),
        set_("_card_trigger_status", "'NO_ACTIVE_TRIGGER'"),
        set_("_prep_interruption", "'NONE'"),
        set_("_origin_center", "actor.center"),
        set_("_origin_muzzle", "actor.muzzle"),
        set_("_feet", 0),
        each(
            "watch_bodies", "_body", [set_("_feet", "_body.position[2]", "_body.id == _actor_id")]
        ),
        query(
            "path",
            {
                "map_id": "{map_id}",
                "start": "{actor.position_cell}",
                "goal": "{destination}",
                "allow_diagonal": True,
                "corner_policy": "forbid_cutting",
                "include_entities": True,
                "moving_entity_id": "{_actor_id}",
                "max_steps": 256,
                "cost_profile": True,
            },
            "_trigger_path",
        ),
        set_("_route", []),
        set_("_cells", "_trigger_path.cells"),
        each(
            "_cells",
            "_cell",
            [
                set_("_point", ["_cell[0] + 0.5", "_cell[1] + 0.5", "actor.center[2]"]),
                push("_route", "_point"),
            ],
        ),
        set_("_reactions", []),
        set_("_responded", []),
        when(
            "_trigger_path.found == true and len(_route) > 1",
            gather_events()
            + [
                {
                    "require": {
                        "condition": "_exposure_path_supported == true",
                        "error": 'A prepared response has an unsupported '
                        'exposure path; no movement was committed.',
                    }
                },
                when(
                    "len(_events) > 0",
                    [
                        set_(
                            "_full_ap",
                            "max(0, ceil(_trigger_path.cost * actor.movement_m_per_ap ** -1))",
                        ),
                        {
                            "require": {
                                "condition": "actor.ap >= _full_ap",
                                "error": "Insufficient AP for the declared movement.",
                            }
                        },
                        set_("_paid", 0),
                        set_("_motion_started", False),
                        set_("_travelled", 0),
                        set_("_trigger_found", True),
                        set_("_route_points", []),
                        set_("_distance_so_far", 0),
                        set_("_previous_point", "_route[0]"),
                        each(
                            "_route",
                            "_route_point",
                            [
                                set_(
                                    "_distance_so_far",
                                    '_distance_so_far + ((_route_point[0] - '
                                    '_previous_point[0]) ** 2 + (_route_point[1] - '
                                    '_previous_point[1]) ** 2) ** 0.5',
                                ),
                                set_(
                                    "_route_row",
                                    {"point": "_route_point", "distance": "_distance_so_far"},
                                ),
                                push("_route_points", "_route_row"),
                                set_("_previous_point", "_route_point"),
                            ],
                        ),
                        # Validate all initial cost prefixes before moving or rolling.
                        each(
                            "_events",
                            "_window",
                            [set_("_trigger_distance", "_window.distance"), *weighted_prefix()],
                        ),
                        set_("_trigger_paused", True),
                        set_("state.last_trigger", {}),
                        set_("_finished", False),
                        # At least one response is considered per iteration. Re-query the
                        # remaining physical route after every window so changed cover can
                        # expose another response at the current point or farther ahead.
                        each(
                            "_candidates",
                            "_iteration",
                            [
                                *gather_events(),
                                {
                                    "require": {
                                        "condition": "_exposure_path_supported == true",
                                        "error": "The updated exposure path is unsupported.",
                                    }
                                },
                                set_("_finished", True, "len(_events) == 0"),
                                when(
                                    "len(_events) > 0",
                                    [
                                        set_("_window", "_events[0]"),
                                        set_("_trigger_distance", "_travelled + _window.distance"),
                                        *weighted_prefix(),
                                        when(
                                            "_motion_started != true and _trigger_distance > 0",
                                            [*movement_break(), set_("_motion_started", True)],
                                        ),
                                        *pose("_window.point"),
                                        set_(
                                            "_due",
                                            'max(0, ceil((_trigger_prefix_cost - '
                                            '0.000001) * actor.movement_m_per_ap ** '
                                            '-1))',
                                        ),
                                        {
                                            "decrement": {
                                                "field": "{_actor_id}.ap",
                                                "amount": "max(0, _due - _paid)",
                                            }
                                        },
                                        set_("_paid", "max(_paid, _due)"),
                                        *window(),
                                        each(
                                            "_done",
                                            "_resolved_id",
                                            [push("_responded", "_resolved_id")],
                                            "_resolved_id not in _responded",
                                        ),
                                        set_("_travelled", "_trigger_distance"),
                                        set_("_route", []),
                                        push("_route", "actor.center"),
                                        each(
                                            "_route_points",
                                            "_route_row",
                                            [push("_route", "_route_row.point")],
                                            "_route_row.distance > _travelled + 0.000000001",
                                        ),
                                    ],
                                ),
                            ],
                            '_finished != true and actor.active == true and'
                            ' actor.downed == false and len(_route) > 1',
                        ),
                        when(
                            "actor.active == true and actor.downed == false",
                            [
                                when(
                                    "_motion_started != true and _trigger_path.cost > 0",
                                    [*movement_break(), set_("_motion_started", True)],
                                ),
                                set_(
                                    "_destination_point",
                                    ["destination[0] + 0.5", "destination[1] + 0.5"],
                                ),
                                *pose("_destination_point"),
                                {
                                    "decrement": {
                                        "field": "{_actor_id}.ap",
                                        "amount": "max(0, _full_ap - _paid)",
                                    }
                                },
                            ],
                        ),
                        set_("_card_trigger_status", "'INTERRUPTED'"),
                        set_(
                            "_card_trigger_status",
                            "'RESUMED'",
                            "actor.active == true and actor.downed == false",
                        ),
                        set_("_ordinary_distance", "_trigger_prefix_cost"),
                        set_(
                            "_ordinary_distance",
                            "_trigger_path.cost",
                            "_card_trigger_status == 'RESUMED'",
                        ),
                        set_("_ordinary_geometry_revision", "_pose_mutation.geometry_revision"),
                        set_("state.last_trigger.actor", "_actor_id"),
                        set_("state.last_trigger.status", "_card_trigger_status"),
                        set_("state.last_trigger.reactions", "_reactions"),
                        set_("state.last_trigger.travelled_cost", "_ordinary_distance"),
                    ],
                ),
            ],
        ),
    ]
    return {
        "description": "Resolve all live shooter lanes on one ordinary movement route.",
        "parameters": common_params(param)
        + [
            param("destination", "array", items="integer"),
            param("active_side", "string", from_state="state.active_side"),
        ],
        "effects": effects,
        "metadata": {"costs_action": False},
    }


def preparation(param):
    return {
        "description": "Resolve all configured responses to one preparation event.",
        "parameters": common_params(param)
        + [
            param("event", "string"),
            param("active_side", "string", from_state="state.active_side"),
        ],
        "effects": [
            set_("_event", "event"),
            set_("_prep_interruption", "'NONE'"),
            set_("_reactions", []),
            set_("_responded", []),
            set_("_route", ["actor.center", "actor.center"]),
            *gather_events(),
            when("len(_events) > 0", [set_("_window", "_events[0]"), *window()]),
        ],
        "metadata": {"costs_action": False},
    }
