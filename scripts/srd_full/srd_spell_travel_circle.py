"""Circle knowledge and daily casting receipts stay with their existing source owners."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import _array, _tool_scene
    from .srd_spell_object_content import _new_scene_object
    from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _tool, _when
    from .srd_timed_senses import _loop, _optional, _public, _push
else:
    from srd_scene_relations import _array, _tool_scene
    from srd_spell_object_content import _new_scene_object
    from srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _tool, _when
    from srd_timed_senses import _loop, _optional, _public, _push


PUBLIC_TOOLS = {"learn_srd_initial_circle_sigils"}
CIRCLE_CAST_CHOICES = [
    _optional("circle_depiction_depth", "number", 0.01, minimum=0.000001),
]


def _subject(subject, local):
    return [
        _call("srd_information_scene_entity", {"subject": subject}, ["_info_scene_entity"]),
        _set(local, "_info_scene_entity"),
    ]


def _rows(effects):
    for effect in effects:
        if isinstance(effect, list):
            yield from _rows(effect)
        else:
            yield effect


def circle_tools():
    """Admit the GM's first two destination choices against a genuine known casting grant."""
    learn = _tool_scene(
        "Learn the initial two Material Plane destination sigils once from a real casting grant.",
        [
            _entity("caster_state"),
            _optional("caster", default=None, resolve="participant"),
            _optional("spell_grant", default=""),
            _parameter(
                "circle_subjects", "array", items={"type": "string"}, min_items=2, max_items=2
            ),
        ],
        [
            _require(
                "caster_state.initial_circle_sigils == null",
                "The first two sigils are learned once, independently of preparation.",
            ),
            _set("_circle_grant_ability", "''"),
            _when(
                "caster != null",
                [
                    _require(
                        "_caster_id == _caster_state_id",
                        "The actual Character File must match this learner.",
                    ),
                    _call(
                        "validate_srd_spell_grants",
                        {
                            "caster": "{_caster_id}",
                            "spell": "teleportation_circle",
                            "selected_source": "{spell_grant}",
                        },
                        ["_grant_ability"],
                    ),
                    _set("_circle_grant_ability", "_grant_ability"),
                ],
            ),
            _when(
                "caster == null",
                [
                    _require(
                        "caster_state.spell_definitions.teleportation_circle != null and "
                        "caster_state.spellcasting_profile != null",
                        "An NPC learner needs its actual admitted Teleportation Circle ability.",
                    ),
                    _set("_circle_grant_ability", "caster_state.spellcasting_profile.ability"),
                ],
            ),
            _require(
                "len(circle_subjects) == 2 and circle_subjects[0] != circle_subjects[1]",
                "The GM selects two different actual destinations.",
            ),
            _set("_circle_initial", []),
            _set("_circle_selected", "circle_subjects"),
            _loop(
                "_circle_selected",
                "_circle_subject",
                [
                    *_subject("{_circle_subject}", "_circle_anchor"),
                    _require(
                        "_circle_anchor.metadata.plane == 'material' and "
                        "_circle_anchor.metadata.teleportation_circle == true and "
                        "_circle_anchor.metadata.sigil != null and "
                        "_circle_anchor.metadata.sigil != '' and "
                        "_circle_anchor.metadata.sigil not in _circle_initial",
                        "Each first destination needs its distinct permanent Material sigil.",
                    ),
                    _push("_circle_initial", "_circle_anchor.metadata.sigil"),
                ],
            ),
            _set("_circle_known", []),
            _when(
                "caster_state.known_circle_sigils != null",
                [
                    _set("_circle_known", "caster_state.known_circle_sigils"),
                ],
            ),
            _loop(
                "_circle_initial",
                "_circle_sigil",
                [
                    _when(
                        "_circle_sigil not in _circle_known",
                        [_push("_circle_known", "_circle_sigil")],
                    ),
                ],
            ),
            _set("caster_state.known_circle_sigils", "_circle_known"),
            _set(
                "caster_state.initial_circle_sigils",
                {
                    "spell": "'teleportation_circle'",
                    "caster": "_caster_state_id",
                    "casting_ability": "_circle_grant_ability",
                    "sigils": "_circle_initial",
                    "anchors": "circle_subjects",
                    "learned_tick": "state.lasting_tick",
                },
            ),
        ],
    )
    creation = deepcopy(
        _new_scene_object(
            {
                "plane": "plane",
                "teleportation_circle": True,
                "sigil": "sigil",
                "lighting": "lighting",
                "source_caster": "_caster_state_id",
                "created_tick": "state.lasting_tick",
                "daily_casts": 365,
                "depiction_only": True,
                "is_object": False,
            },
            identity="circle_id",
            extent="_circle_extent",
        )
    )
    next(e["set"] for e in creation if e.get("set", {}).get("field") == "_world_created_marker")[
        "value"
    ] = {"is_object": False}
    next(e["set"] for e in creation if e.get("set", {}).get("field") == "_world_created_entity")[
        "value"
    ]["footprint"] = {
        "kind": "'circle'",
        "radius": 5,
    }
    create = _tool_scene(
        "Create the actual permanent radius-five circle after its paid daily source receipt.",
        [
            _entity("caster_state"),
            _parameter("circle_id"),
            _parameter("sigil"),
            _parameter("plane"),
            _array("point"),
            _parameter("lighting"),
            _parameter("depiction_depth", "number", minimum=0.000001),
        ],
        [
            _require(
                "_circle_creation_paid == true",
                "Only the completed 365-day casting receipt creates this circle.",
            ),
            _set("_world_origin", "point"),
            _set("_circle_extent", [10, 10, "depiction_depth"]),
            *creation,
        ],
    )
    # Calls have their own local context; authenticate the retained paid row instead.
    create["effects"][0:1] = _tool(
        "",
        create["parameters"],
        [
            _set("_circle_creation_paid", False),
            _require("state.travel_circles != null", "There is no completed circle casting."),
            _set("_circle_creation_records", "state.travel_circles"),
            _loop(
                "_circle_creation_records",
                "_circle_creation_record",
                [
                    _when(
                        "_circle_creation_record.caster == _caster_state_id and "
                        "_circle_creation_record.created_tick == state.lasting_tick and "
                        "_circle_creation_record.consecutive_days >= 365 and "
                        "_circle_creation_record.permanent_circle_id == circle_id and "
                        "_circle_creation_record.plane == plane and "
                        "_circle_creation_record.origin[0] == point[0] and "
                        "_circle_creation_record.origin[1] == point[1] and "
                        "_circle_creation_record.origin[2] == point[2]",
                        [_set("_circle_creation_paid", True)],
                    ),
                ],
            ),
            _require(
                "_circle_creation_paid == true",
                "Only the completed 365-day casting receipt creates this circle.",
            ),
        ],
    )["effects"]
    return {
        "learn_srd_initial_circle_sigils": _public(
            learn, "information", "Initial circle sigils", [("Known sigils", "_circle_known")]
        ),
        "create_srd_permanent_circle": create,
        "validate_srd_circle_cast": _circle_cast_qualification(),
        "interrupt_srd_circle_study": _tool(
            "Mark an actual ongoing minute study when its creature really moves.",
            [_entity("actor_state")],
            [
                _when(
                    "actor_state.circle_study != null",
                    [
                        _set("actor_state.circle_study.interrupted", True),
                    ],
                )
            ],
        ),
    }


def _circle_cast_qualification():
    """Validate the actual known destination and printed local circle before payment."""
    return _tool_scene(
        "Validate the source circle and known same-plane destination before payment.",
        [
            _entity("caster_state"),
            _parameter("destination_subject"),
            _array("point"),
            _parameter("depiction_depth", "number", minimum=0.000001),
        ],
        [
            *_subject("{_caster_state_id}", "_circle_cast_actor"),
            *_subject("{destination_subject}", "_circle_cast_anchor"),
            _require(
                "_circle_cast_actor.metadata.plane != null and "
                "_circle_cast_anchor.metadata.teleportation_circle == true and "
                "_circle_cast_anchor.metadata.plane == _circle_cast_actor.metadata.plane",
                "The destination must be an actual permanent circle on the same plane.",
            ),
            _require(
                "caster_state.known_circle_sigils != null and "
                "_circle_cast_anchor.metadata.sigil in caster_state.known_circle_sigils",
                "The destination's actual sigil sequence must be known.",
            ),
            _require(
                "sqrt((point[0] - _circle_cast_actor.position[0]) ** 2 + "
                "(point[1] - _circle_cast_actor.position[1]) ** 2 + "
                "(point[2] - _circle_cast_actor.position[2]) ** 2) <= 10",
                "Draw the actual circle within ten feet.",
            ),
            *[
                _require(
                    f"point[{axis}] - 5 >= state.spatial_scene.map.bounds.min[{axis}] and "
                    f"point[{axis}] + 5 <= state.spatial_scene.map.bounds.max[{axis}]",
                    "The printed radius-five circle must fit the actual map bounds.",
                )
                for axis in (0, 1)
            ],
            _require(
                "point[2] >= state.spatial_scene.map.bounds.min[2] and "
                "point[2] + depiction_depth <= state.spatial_scene.map.bounds.max[2]",
                "The chosen circle depiction must fit the actual map bounds.",
            ),
        ],
    )


def daily_casting_receipt():
    """Count one completed casting per actual day at the exact same existing portal origin."""
    return [
        _set("_circle_day", "state.lasting_tick // 28800"),
        _set("_circle_prior_day", -2),
        _set("_circle_prior_count", 0),
        _set("_circle_permanent_id", "''"),
        _set("_circle_records", "_travel_saved_circles"),
        _loop(
            "_circle_records",
            "_circle_record",
            [
                _when(
                    "_circle_record.cast_day != null and "
                    "_circle_record.caster == _caster_state_id and "
                    "_circle_record.map_id == state.spatial_scene.map.id and "
                    "_circle_record.plane == _travel_caster.metadata.plane and "
                    "_circle_record.origin[0] == travel_destination[0] and "
                    "_circle_record.origin[1] == travel_destination[1] and "
                    "_circle_record.origin[2] == travel_destination[2] and "
                    "_circle_record.cast_day >= _circle_prior_day",
                    [
                        _set("_circle_prior_day", "_circle_record.cast_day"),
                        _set("_circle_prior_count", "_circle_record.consecutive_days"),
                    ],
                ),
                _when(
                    "_circle_record.permanent == true and "
                    "_circle_record.caster == _caster_state_id and "
                    "_circle_record.map_id == state.spatial_scene.map.id and "
                    "_circle_record.plane == _travel_caster.metadata.plane and "
                    "_circle_record.origin[0] == travel_destination[0] and "
                    "_circle_record.origin[1] == travel_destination[1] and "
                    "_circle_record.origin[2] == travel_destination[2]",
                    [_set("_circle_permanent_id", "_circle_record.permanent_circle_id")],
                ),
            ],
        ),
        _set("_circle_days", 1),
        _when(
            "_circle_prior_day == _circle_day",
            [
                _set("_circle_days", "_circle_prior_count"),
            ],
        ),
        _when(
            "_circle_prior_day == _circle_day - 1",
            [
                _set("_circle_days", "_circle_prior_count + 1"),
            ],
        ),
        _set("_circle_permanent", "_circle_days >= 365"),
        _set("_circle_create", "_circle_permanent == true and _circle_permanent_id == ''"),
        _when(
            "_circle_create == true",
            [
                _call(
                    "record_srd_result_text",
                    {"text": "teleportation_circle:{_caster_state_id}:{state.lasting_tick}"},
                    ["_recorded_result_text"],
                ),
                _set(
                    "_circle_permanent_id",
                    "_recorded_result_text",
                ),
            ],
        ),
        _when("_circle_permanent_id != ''", [_set("_circle_permanent", True)]),
    ]


def bind_circle_cast_receipts(cast):
    """Attach clock-derived history only at the existing paid circle effect dispatch."""
    cast = deepcopy(cast)

    branch = next(
        e for e in _rows(cast["effects"]) if e.get("condition") == "spell == 'teleportation_circle'"
    )
    body = list(_rows(branch["on_true"]))
    branch["on_true"] = body
    index = next(
        i
        for i, e in enumerate(body)
        if e.get("list_push", {}).get("list") == "_travel_saved_circles"
    )
    row = body[index]["list_push"]["value"]
    row.update(
        cast_day="_circle_day",
        consecutive_days="_circle_days",
        permanent="_circle_permanent",
        created_tick="state.lasting_tick",
        map_id="state.spatial_scene.map.id",
        permanent_circle_id="_circle_permanent_id",
    )
    body[index:index] = daily_casting_receipt()
    body.append(
        _when(
            "_circle_create == true",
            [
                _call(
                    "record_srd_result_text",
                    {"text": "sigil:{_circle_permanent_id}"},
                    ["_recorded_result_text"],
                ),
                _set("_circle_new_sigil", "_recorded_result_text"),
                _call(
                    "create_srd_permanent_circle",
                    {
                        "caster_state": "{_caster_state_id}",
                        "circle_id": "{_circle_permanent_id}",
                        "sigil": "{_circle_new_sigil}",
                        "point": "{travel_destination}",
                        "plane": "{_travel_caster.metadata.plane}",
                        "lighting": "{_travel_caster.metadata.lighting}",
                        "depiction_depth": "{circle_depiction_depth}",
                    },
                ),
            ],
        )
    )
    cast["effects"] = _tool("", cast["parameters"], cast["effects"])["effects"]
    return cast


def bind_circle_study(study, active_condition):
    """One atomic minute cannot retain caller chunks across intervening ordinary actions."""
    study = deepcopy(study)
    study["effects"] = list(_rows(study["effects"]))
    for p in study["parameters"]:
        if p["name"] == "elapsed_rounds":
            p.update(required=False, default=10, minimum=10, maximum=10)
        if p["name"] == "uninterrupted":
            p.update(required=False, default=True)
    cut = next(
        i
        for i, e in enumerate(study["effects"])
        if "actor_state.circle_study == null" in e.get("condition", "")
    )
    study["effects"][cut:] = [
        _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
        _set("_circle_study_start", "state.lasting_tick"),
        _set(
            "actor_state.circle_study",
            {
                "subject": "circle_subject",
                "start_tick": "state.lasting_tick",
                "origin": "_travel_study_actor.position",
                "plane": "_travel_study_actor.metadata.plane",
                "interrupted": False,
            },
        ),
        _call("advance_srd_lasting_clock", {"lasting_state": "state", "elapsed_ticks": 20}),
        _set("_circle_study_complete", False),
        _set(
            "_circle_study_actor_ready",
            active_condition + " and actor_state.circle_study.interrupted != true",
        ),
        _set(
            "_circle_study_clock_ready",
            "state.lasting_tick == _circle_study_start + 20 and "
            "(state.pending_srd_action_stack == null or "
            "len(state.pending_srd_action_stack) == 0)",
        ),
        _set("_circle_study_rows", "state.spatial_scene.entities"),
        _loop(
            "_circle_study_rows",
            "_circle_study_current",
            [
                _when(
                    "_circle_study_current.id == _travel_study_actor.id and "
                    "_circle_study_current.active == true and "
                    "_circle_study_current.metadata.plane == "
                    "_travel_study_actor.metadata.plane and "
                    "_circle_study_current.position[0] == _travel_study_actor.position[0] and "
                    "_circle_study_current.position[1] == _travel_study_actor.position[1] and "
                    "_circle_study_current.position[2] == _travel_study_actor.position[2] and "
                    "_circle_study_actor_ready == true and _circle_study_clock_ready == true",
                    [_set("_circle_study_complete", True)],
                ),
            ],
        ),
        _when(
            "_circle_study_complete == true",
            [
                _when(
                    "actor_state.known_circle_sigils == null",
                    [_set("actor_state.known_circle_sigils", [])],
                ),
                _when(
                    "_travel_circle_subject.metadata.sigil not in actor_state.known_circle_sigils",
                    [
                        _set("_travel_saved_sigils", "actor_state.known_circle_sigils"),
                        _push("_travel_saved_sigils", "_travel_circle_subject.metadata.sigil"),
                        _set("actor_state.known_circle_sigils", "_travel_saved_sigils"),
                    ],
                ),
            ],
        ),
        _set("actor_state.circle_study", None),
        _set("_travel_known_sigils", "actor_state.known_circle_sigils"),
    ]
    study["effects"] = _tool("", study["parameters"], study["effects"])["effects"]
    return study


def bind_circle_consumers(tools, changed):
    """Mark study interruption at the existing two actual creature movement owners."""
    move = deepcopy(changed.get("move_srd_travel_subject", tools["move_srd_travel_subject"]))
    if "interrupt_srd_circle_study" not in str(move["effects"]):
        boundary = next(
            i
            for i, e in enumerate(move["effects"])
            if "spatial_mutation" in e or e.get("call") == "move_srd_travel_subject_node"
        )
        move["effects"].insert(
            boundary + 1,
            _when(
                "_travel_guard_needed == true",
                [
                    _call(
                        "interrupt_srd_circle_study",
                        {
                            "actor_state": "{_travel_guard_target}",
                        },
                    ),
                ],
            ),
        )
        changed["move_srd_travel_subject"] = move
    # Source families wrap the scene segment; bind its retained mutation body rather than wrappers.
    for name, tool in (tools | changed).items():
        if not name.startswith("move_srd_scene_creature"):
            continue
        if not any("spatial_mutation" in e for e in tool["effects"]):
            continue
        if not any(e.get("set", {}).get("field") == "_move_scene_entity" for e in tool["effects"]):
            continue
        if "interrupt_srd_circle_study" in str(tool["effects"]):
            continue
        move = deepcopy(tool)
        boundary = next(i for i, e in enumerate(move["effects"]) if "spatial_mutation" in e)
        move["effects"].insert(
            boundary + 1,
            _call(
                "interrupt_srd_circle_study",
                {
                    "actor_state": "{_actor_state_id}",
                },
            ),
        )
        changed[name] = move
