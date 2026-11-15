"""Persistent spell areas consume admitted scene entry and source-owned release events."""

import re
from copy import deepcopy

import yaml

if __package__:
    from .srd_control_behaviors import (
        _call,
        _each,
        _entity,
        _lasting,
        _param,
        _push,
        _require,
        _set,
        _tool,
        _walk,
        _when,
    )
    from .srd_timed_senses import _public, _replace_tools
else:
    from srd_control_behaviors import (
        _call,
        _each,
        _entity,
        _lasting,
        _param,
        _push,
        _require,
        _set,
        _tool,
        _walk,
        _when,
    )
    from srd_timed_senses import _public, _replace_tools


PUBLIC_TOOLS = {"resolve_srd_affinity_release"}
POSITION_EXPORTS = ("_info_scene_entity", "_info_position")


def _array(name):
    return _param(name, "array", items={"type": "number"}, min_items=3, max_items=3)


def _area_loop(effects):
    return [
        _set("_area_ids", "lasting_state.persistent_spell_area_ids"),
        _each(
            "_area_ids",
            "_area_id",
            [
                _set("_area", "lasting_state.persistent_spell_areas[_area_id]"),
                _set("_area_cast", "lasting_state.affinity_casts[_area.cast_id]"),
                _when(
                    "_area.profile == 'affinity' and _area_cast != null and "
                    "lasting_state.lasting_tick < _area_cast.expires_tick",
                    effects,
                ),
            ],
        ),
    ]


def _refresh(actor="_target_state_id"):
    return _call(
        "refresh_srd_timed_condition_target",
        {
            "target_state": "{" + actor + "}",
            "lasting_state": "{_lasting_state_id}",
        },
    )


def _tools():
    register = _tool(
        "Reference an existing affinity casting without copying its clock or anchor position.",
        [_lasting(), _param("cast_id")],
        [
            _when(
                "lasting_state.persistent_spell_area_ids == null",
                [
                    _set("lasting_state.persistent_spell_area_ids", []),
                    _set("lasting_state.persistent_spell_areas", {}),
                ],
            ),
            _set(
                "lasting_state.persistent_spell_areas.{cast_id}",
                {"profile": "'affinity'", "cast_id": "cast_id"},
            ),
            _set("_area_registered", "lasting_state.persistent_spell_area_ids"),
            _when("cast_id not in _area_registered", [_push("_area_registered", "cast_id")]),
            _set("lasting_state.persistent_spell_area_ids", "_area_registered"),
            _set("lasting_state.has_persistent_spell_areas", True),
        ],
    )
    eligible = _tool(
        "Qualify actual creature kind, existing source and successful-save immunity for one area.",
        [_entity("target_state"), _lasting(), _param("cast_id")],
        [
            _set("_area_cast", "lasting_state.affinity_casts[cast_id]"),
            _set("_area_can_expose", False),
            _when(
                "_area_cast != null and lasting_state.lasting_tick < _area_cast.expires_tick "
                "and target_state.is_alive == true and (target_state.creature_kind == "
                "'character' or target_state.creature_kind == 'monster')",
                [
                    _set("_area_kind_matches", "target_state.creature_type == _area_cast.kind"),
                    _when(
                        "target_state.creature_kind_tags != null",
                        [
                            _set(
                                "_area_kind_matches",
                                "_area_kind_matches == true or "
                                "_area_cast.kind in target_state.creature_kind_tags",
                            ),
                        ],
                    ),
                    _set("_area_already_affected", False),
                    _set("_area_target_sources", []),
                    _when(
                        "target_state.timed_condition_source_ids != null",
                        [
                            _set("_area_target_sources", "target_state.timed_condition_source_ids"),
                        ],
                    ),
                    _each(
                        "_area_target_sources",
                        "_area_target_source_id",
                        [
                            _set(
                                "_area_target_source",
                                "lasting_state.timed_condition_sources[_area_target_source_id]",
                            ),
                            _when(
                                "_area_target_source.installed == true and "
                                "_area_target_source.affliction_cast_id == cast_id",
                                [
                                    _set("_area_already_affected", True),
                                ],
                            ),
                        ],
                    ),
                    _set(
                        "_area_can_expose",
                        "_area_kind_matches == true and _area_already_affected != true",
                    ),
                    _when(
                        "_target_state_id in _area_cast.saved_until",
                        [
                            _set(
                                "_area_can_expose",
                                (
                                    "_area_can_expose == true and lasting_state.lasting_tick >= _"
                                    "area_cast.saved_until[_target_state_id]"
                                ),
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    expose = _tool(
        "Settle one eligible actor's affinity exposure at its actual admitted position.",
        [_entity("target_state"), _lasting(), _param("cast_id")],
        [
            _refresh(),
            _call(
                "qualify_srd_affinity_area_target",
                {
                    "target_state": "{_target_state_id}",
                    "cast_id": "{cast_id}",
                },
                ("_area_can_expose",),
            ),
            _when(
                "_area_can_expose == true",
                [
                    _set("_area_cast", "lasting_state.affinity_casts[cast_id]"),
                    _call(
                        "resolve_srd_scene_geometry_relation",
                        {
                            "actor_state": "{_target_state_id}",
                            "target_state": "{_area_cast.anchor_id}",
                        },
                        ("_scene_same_plane", "_scene_distance"),
                    ),
                    _when(
                        "_scene_same_plane == true and _scene_distance <= 120.000000001",
                        [
                            _call(
                                "expose_srd_affinity_internal",
                                {
                                    "target_state": "{_target_state_id}",
                                    "anchor_state": "{_area_cast.anchor_id}",
                                    "lasting_state": "{_lasting_state_id}",
                                    "cast_id": "{cast_id}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    collect = _tool(
        "Discover active anchored-area exposure for one actual actor.",
        [_entity("target_state"), _lasting()],
        _area_loop(
            [
                _call(
                    "try_srd_affinity_area_exposure",
                    {
                        "target_state": "{_target_state_id}",
                        "cast_id": "{_area.cast_id}",
                    },
                ),
            ]
        ),
    )
    scene = _tool(
        (
            "Discover active anchored areas for actual saved scene actors after casting or clock "
            "changes."
        ),
        [_lasting()],
        [
            {
                "spatial_query": {"scene": "state.spatial_scene", "operation": "snapshot"},
                "store_as": "_area_snapshot",
            },
            _set("_area_scene_entities", "lasting_state.spatial_scene.entities"),
            _each(
                "_area_scene_entities",
                "_area_scene_entity",
                [
                    _when(
                        "_area_scene_entity.active == true and "
                        "_area_scene_entity.metadata.participant_ref != null",
                        [
                            _call(
                                "collect_srd_persistent_area_exposure",
                                {
                                    "target_state": "{_area_scene_entity.metadata.participant_ref}",
                                    "lasting_state": "{_lasting_state_id}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    offer = _tool(
        "Save an optional Sympathy release opportunity owned by actual anchor damage.",
        [_entity("target_state"), _lasting(), _param("source_id")],
        [
            _set("_release_source", "lasting_state.timed_condition_sources[source_id]"),
            _when(
                "_release_source.affinity_release_sequence == null",
                [
                    _set(
                        (
                            "lasting_state.timed_condition_sources.{source_id}.affinity_release_s"
                            "equence"
                        ),
                        0,
                    ),
                    _set(
                        (
                            "lasting_state.timed_condition_sources.{source_id}.affinity_release_o"
                            "pportunities"
                        ),
                        [],
                    ),
                ],
            ),
            {
                "increment": {
                    "field": (
                        "lasting_state.timed_condition_sources.{source_id}.affinity_release_seque"
                        "nce"
                    ),
                    "amount": 1,
                }
            },
            _set(
                "lasting_state.timed_condition_sources.{source_id}.affinity_release_pending", True
            ),
            _set(
                "_release_opportunities",
                "lasting_state.timed_condition_sources[source_id].affinity_release_opportunities",
            ),
            _push(
                "_release_opportunities",
                "lasting_state.timed_condition_sources[source_id].affinity_release_sequence",
            ),
            _set(
                "lasting_state.timed_condition_sources.{source_id}.affinity_release_opportunities",
                "_release_opportunities",
            ),
            _set("target_state.last_affinity_release_source_id", "source_id"),
            _set(
                "target_state.last_affinity_release_opportunity",
                "lasting_state.timed_condition_sources[source_id].affinity_release_sequence",
            ),
        ],
    )
    release = _public(
        _tool(
            "Accept or decline the exact saved optional save offered by Sympathy anchor damage.",
            [
                _entity("target_state"),
                _param("source_id"),
                _param("opportunity", "integer", minimum=1),
                _param("accept", "boolean"),
            ],
            [
                _call(
                    "resolve_srd_affinity_release_node",
                    {
                        "target_state": "{_target_state_id}",
                        "source_id": "{source_id}",
                        "opportunity": "{opportunity}",
                        "accept": "{accept}",
                        "lasting_state": "state",
                    },
                    ("_affinity_release_outcome",),
                ),
            ],
            public=True,
        ),
        "saving_throw",
        "Sympathy release",
        [("Choice", "_affinity_release_outcome")],
    )
    release_node = _tool(
        "Consume one source-qualified anchor-damage opportunity without spending an action.",
        [
            _entity("target_state"),
            _lasting(),
            _param("source_id"),
            _param("opportunity", "integer"),
            _param("accept", "boolean"),
        ],
        [
            _refresh(),
            _require(
                "source_id in target_state.control_affliction_source_ids",
                "Select this actor's currently effective affinity source.",
            ),
            _set("_release_source", "lasting_state.timed_condition_sources[source_id]"),
            _require(
                "_release_source.control_profile == 'affinity_sympathy' and "
                "_release_source.affinity_release_pending == true and "
                "opportunity in _release_source.affinity_release_opportunities",
                "There is no matching unconsumed anchor-damage release opportunity.",
            ),
            _set("_release_kept", []),
            _set("_release_offers", "_release_source.affinity_release_opportunities"),
            _each(
                "_release_offers",
                "_release_offer",
                [
                    _when(
                        ("_release_offer != opportunity"),
                        [_push("_release_kept", "_release_offer")],
                    )
                ],
            ),
            _set(
                "lasting_state.timed_condition_sources.{source_id}.affinity_release_opportunities",
                "_release_kept",
            ),
            _set(
                "lasting_state.timed_condition_sources.{source_id}.affinity_release_pending",
                "len(_release_kept) > 0",
            ),
            _set("_affinity_release_outcome", "'Declined; no save rolled.'"),
            _when(
                "accept == true",
                [
                    _call(
                        "save_srd_affinity_release",
                        {
                            "target_state": "{_target_state_id}",
                            "source_id": "{source_id}",
                        },
                    ),
                    _set("_affinity_release_outcome", "target_state.last_affinity_save_roll"),
                    _refresh(),
                ],
            ),
        ],
    )
    return {
        "settle_srd_persistent_area_move": _tool(
            "Discover stationary creatures when their actual affinity anchor moved.",
            [_entity("actor_state"), _lasting()],
            [
                _set("_area_anchor_moved", False),
                *_area_loop(
                    [
                        _when(
                            "_area_cast.anchor_id == _actor_state_id",
                            [
                                _set("_area_anchor_moved", True),
                            ],
                        ),
                    ]
                ),
                _when(
                    "_area_anchor_moved == true",
                    [
                        _call(
                            ("collect_srd_persistent_area_scene"),
                            {"lasting_state": "{_lasting_state_id}"},
                        ),
                    ],
                    [
                        _call(
                            ("collect_srd_persistent_area_exposure"),
                            {"target_state": "{_actor_state_id}"},
                        )
                    ],
                ),
            ],
        ),
        "check_srd_persistent_areas_active": _tool(
            "Read the area's active flag from its actual saved state binding.",
            [_lasting()],
            [
                _set(
                    "_persistent_areas_active", "lasting_state.has_persistent_spell_areas == true"
                ),
                _set("_persistent_area_ids", "lasting_state.persistent_spell_area_ids"),
            ],
        ),
        "collect_srd_persistent_area_exposure_if_active": _tool(
            "Skip scene work when no saved area is active.",
            [_entity("target_state"), _lasting()],
            [
                _when(
                    "lasting_state.has_persistent_spell_areas == true",
                    [
                        _call(
                            "collect_srd_persistent_area_exposure",
                            {
                                "target_state": "{_target_state_id}",
                                "lasting_state": "{_lasting_state_id}",
                            },
                        ),
                    ],
                ),
            ],
        ),
        "register_srd_affinity_area": register,
        "qualify_srd_affinity_area_target": eligible,
        "try_srd_affinity_area_exposure": expose,
        "collect_srd_persistent_area_exposure": collect,
        "collect_srd_persistent_area_scene": scene,
        "offer_srd_affinity_release": offer,
        "resolve_srd_affinity_release": release,
        "resolve_srd_affinity_release_node": release_node,
    }


def _entry_tool():
    return _tool(
        (
            "Find the earliest eligible radius entry on the actual requested straight movement se"
            "gment."
        ),
        [_entity("actor_state"), _lasting(), _array("destination")],
        [
            _call(
                "srd_information_scene_entity", {"subject": "{_actor_state_id}"}, POSITION_EXPORTS
            ),
            _set("_area_move_start", "_info_position"),
            _set("_area_move_plane", "_info_scene_entity.metadata.plane"),
            _set("_area_entry_fraction", 1),
            _set("_area_entry_found", False),
            *_area_loop(
                [
                    _when(
                        "_area_cast.anchor_id == _actor_state_id",
                        [
                            _set(
                                "_area_moving_anchor_entities",
                                "lasting_state.spatial_scene.entities",
                            ),
                            _each(
                                "_area_moving_anchor_entities",
                                "_area_moving_anchor_entity",
                                [
                                    _when(
                                        (
                                            "_area_moving_anchor_entity.active == true and _area_"
                                            "moving_anchor_entity.metadata.participant_ref != nul"
                                            "l and _area_moving_anchor_entity.metadata.plane == _"
                                            "area_move_plane"
                                        ),
                                        [
                                            _call(
                                                "qualify_srd_affinity_area_target",
                                                {
                                                    "target_state": (
                                                        "{_area_moving_anchor_entity.metadata.par"
                                                        "ticipant_ref}"
                                                    ),
                                                    "cast_id": "{_area.cast_id}",
                                                },
                                                ("_area_can_expose",),
                                            ),
                                            _when(
                                                "_area_can_expose == true",
                                                [
                                                    _call(
                                                        "resolve_srd_persistent_area_segment",
                                                        {
                                                            "start": "{_area_move_start}",
                                                            "end": "{destination}",
                                                            "origin": (
                                                                "{_area_moving_anchor_entity.posi"
                                                                "tion}"
                                                            ),
                                                            "radius": 120,
                                                        },
                                                        ("_sphere_hit", "_sphere_entry"),
                                                    ),
                                                    _when(
                                                        (
                                                            "_sphere_hit == true and _sphere_entr"
                                                            "y > 0 and _sphere_entry <= _area_ent"
                                                            "ry_fraction"
                                                        ),
                                                        [
                                                            _set(
                                                                "_area_entry_fraction",
                                                                "_sphere_entry",
                                                            ),
                                                            _set("_area_entry_found", True),
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
                    _call(
                        "qualify_srd_affinity_area_target",
                        {
                            "target_state": "{_actor_state_id}",
                            "cast_id": "{_area.cast_id}",
                        },
                        ("_area_can_expose",),
                    ),
                    _when(
                        "_area_can_expose == true",
                        [
                            _call(
                                "srd_information_scene_entity",
                                {"subject": "{_area_cast.anchor_id}"},
                                POSITION_EXPORTS,
                            ),
                            _when(
                                "_area_move_plane == _info_scene_entity.metadata.plane",
                                [
                                    _call(
                                        "resolve_srd_persistent_area_segment",
                                        {
                                            "start": "{_area_move_start}",
                                            "end": "{destination}",
                                            "origin": "{_info_position}",
                                            "radius": 120,
                                        },
                                        ("_sphere_hit", "_sphere_entry"),
                                    ),
                                    _when(
                                        "_sphere_hit == true and _sphere_entry > 0 and "
                                        "_sphere_entry <= _area_entry_fraction",
                                        [
                                            _set("_area_entry_fraction", "_sphere_entry"),
                                            _set("_area_entry_found", True),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ]
            ),
            _set("_area_move_destination", "destination"),
            _when(
                "_area_entry_found == true",
                [
                    _set(
                        "_area_move_destination",
                        [
                            f"_area_move_start[{i}] + (destination[{i}] - _area_move_start[{i}]) * "
                            "_area_entry_fraction"
                            for i in range(3)
                        ],
                    ),
                ],
            ),
        ],
    )


def _strip_calls(effects, owned):
    effects[:] = [
        e
        for e in effects
        if e.get("call") not in owned and not any(name in str(e) for name in owned)
    ]
    for effect in effects:
        for branch in ("on_true", "on_false"):
            if branch in effect:
                _strip_calls(effect[branch], owned)


def _bind_move_destination(value):
    """Input parameters remain immutable; movement consumers read the resolved entry endpoint."""
    if isinstance(value, str):
        return re.sub(r"\bdestination\b", "_area_move_destination", value)
    if isinstance(value, list):
        return [_bind_move_destination(item) for item in value]
    if isinstance(value, dict):
        return {key: _bind_move_destination(item) for key, item in value.items()}
    return value


def extend_runtime(text):
    """Compose durable affinity areas after the scene, affliction and condition owners."""
    tools = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))["tools"]
    changed = _tools()
    interval = deepcopy(tools["srd_plants_route_interval"])
    interval["parameters"].append(_param("radius", "number", minimum=0))
    for effect in _walk(interval["effects"]):
        setting = effect.get("set", {})
        if setting.get("field") == "_sphere_c":
            setting["value"] = setting["value"].replace("900", "radius ** 2")
    changed["resolve_srd_persistent_area_segment"] = interval
    changed["resolve_srd_persistent_area_entry"] = _entry_tool()
    install = deepcopy(tools["install_srd_affinity_anchor"])
    _strip_calls(
        install["effects"], {"register_srd_affinity_area", "collect_srd_persistent_area_scene"}
    )
    install["effects"].append(
        _when(
            "_affinity_anchor_valid == true",
            [
                _call(
                    "register_srd_affinity_area",
                    {
                        "lasting_state": "{_lasting_state_id}",
                        "cast_id": "{_affinity_cast_id}",
                    },
                ),
                _require(
                    "lasting_state.spatial_scene != null",
                    "An actual affinity anchor requires the admitted scene.",
                ),
                _call(
                    "collect_srd_persistent_area_scene", {"lasting_state": "{_lasting_state_id}"}
                ),
            ],
        )
    )
    changed["install_srd_affinity_anchor"] = install
    prune = deepcopy(tools["prune_srd_affinity_anchors"])
    prune["effects"] = [
        _when("lasting_state.affinity_cast_ids != null", prune["effects"])
    ]
    prune["effects"].append(
        _when(
            "lasting_state.persistent_spell_area_ids != null",
            [
                _set("_retained_area_ids", []),
                _set("_prior_area_ids", "lasting_state.persistent_spell_area_ids"),
                _each("_prior_area_ids", "_prior_area_id", [
                    _set("_prior_area", "lasting_state.persistent_spell_areas[_prior_area_id]"),
                    _when(
                        "(_prior_area.profile == 'affinity' and _prior_area_id in "
                        "lasting_state.affinity_cast_ids) or (_prior_area.profile != 'affinity' "
                        "and _prior_area.active == true and lasting_state.lasting_tick < "
                        "_prior_area.expires_tick)",
                        [_push("_retained_area_ids", "_prior_area_id")],
                    ),
                ]),
                _set("lasting_state.persistent_spell_area_ids", "_retained_area_ids"),
                _set(
                    "lasting_state.has_persistent_spell_areas",
                    "len(_retained_area_ids) > 0",
                ),
            ],
        )
    )
    changed["prune_srd_affinity_anchors"] = prune
    damage = deepcopy(tools["resolve_srd_control_after_damage"])
    for effect in _walk(damage["effects"]):
        if effect.get("condition", "").find("'affinity_sympathy'") >= 0:
            effect["condition"] += (
                " and _control_damage_source_id in target_state.control_affliction_source_ids"
            )
            effect["on_true"] = [
                _call(
                    "offer_srd_affinity_release",
                    {
                        "target_state": "{_target_state_id}",
                        "source_id": "{_control_damage_source_id}",
                    },
                )
            ]
    changed["resolve_srd_control_after_damage"] = damage
    turn = deepcopy(tools["begin_combat_turn"])
    _strip_calls(turn["effects"], {"collect_srd_persistent_area_exposure"})
    turn["effects"].append(
        _call("collect_srd_persistent_area_exposure_if_active", {"target_state": "{_actor_id}"})
    )
    changed["begin_combat_turn"] = turn
    clock = deepcopy(tools["advance_srd_lasting_clock"])
    _strip_calls(clock["effects"], {"collect_srd_persistent_area_scene"})
    clock["effects"].append(
        _when(
            "lasting_state.has_persistent_spell_areas == true",
            [
                _call("prune_srd_affinity_anchors", {"lasting_state": "{_lasting_state_id}"}),
                _call(
                    "collect_srd_persistent_area_scene", {"lasting_state": "{_lasting_state_id}"}
                ),
            ],
        )
    )
    changed["advance_srd_lasting_clock"] = clock
    movement = deepcopy(tools["move_srd_scene_creature"])
    original = _bind_move_destination(movement["effects"])
    movement["effects"] = [
        _call(
            "check_srd_persistent_areas_active",
            {},
            ("_persistent_areas_active", "_persistent_area_ids"),
        ),
        _set("_scene_area_interrupted", False),
        _set("_area_stop_before_move", False),
        _set("_area_move_destination", "destination"),
        _set("_area_entry_found", False),
        _when(
            "_persistent_areas_active == true",
            [
                _set("_area_previous_pending", "actor_state.affliction_movement_pending == true"),
                _call(
                    "collect_srd_persistent_area_exposure", {"target_state": "{_actor_state_id}"}
                ),
                _set(
                    "_area_stop_before_move",
                    (
                        "actor_state.control_order_moving != true and _area_previous_pending != t"
                        "rue and actor_state.affliction_movement_pending == true"
                    ),
                ),
                _call(
                    "resolve_srd_persistent_area_entry",
                    {
                        "actor_state": "{_actor_state_id}",
                        "destination": "{destination}",
                    },
                    ("_area_entry_found", "_area_move_destination"),
                ),
            ],
        ),
        _when(
            "_area_stop_before_move == true",
            [
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_actor_state_id}"},
                    POSITION_EXPORTS,
                ),
                _set("_scene_movement_distance", 0),
                _set("_scene_movement_cost", 0),
                _set("_scene_actor_position", "_info_position"),
                _set("_scene_actor_entity_id", "_info_scene_entity.id"),
                _set("_scene_area_interrupted", True),
            ],
            original,
        ),
        _when(
            "_persistent_areas_active == true",
            [
                _call("settle_srd_persistent_area_move", {"actor_state": "{_actor_state_id}"}),
                _when(
                    "_area_entry_found == true and actor_state.affliction_movement_pending == true",
                    [
                        _set("_scene_area_interrupted", True),
                    ],
                ),
            ],
        ),
    ]
    # Resume compulsory routes after successful entry saves inside the same action.
    # Each saved area can add one new boundary before the original final endpoint.
    segment = movement["effects"]
    movement["effects"] = [
        _call(
            "check_srd_persistent_areas_active",
            {},
            ("_persistent_areas_active", "_persistent_area_ids"),
        ),
        _when(
            "_persistent_areas_active == true and actor_state.control_order_moving == true",
            [
                _set("_area_total_distance", 0),
                _set("_area_total_cost", 0),
                _set("_area_route_continue", True),
                _set("_area_route_ids", "_persistent_area_ids"),
                _push("_area_route_ids", "'final_endpoint'"),
                _each(
                    "_area_route_ids",
                    "_area_route_id",
                    [
                        _when(
                            "_area_route_continue == true",
                            segment
                            + [
                                _set(
                                    ("_area_total_distance"),
                                    ("_area_total_distance + _scene_movement_distance"),
                                ),
                                _set("_area_total_cost", "_area_total_cost + _scene_movement_cost"),
                                _set(
                                    "_area_route_continue",
                                    (
                                    "_area_entry_found == true and "
                                    "actor_state.affliction_movement_pending != true"
                                    ),
                                ),
                            ],
                        )
                    ],
                ),
                _set("_scene_movement_distance", "_area_total_distance"),
                _set("_scene_movement_cost", "_area_total_cost"),
            ],
            segment,
        ),
    ]
    changed["move_srd_scene_creature"] = movement
    # Information generation copies movement preconditions before this family adds its own guard.
    budget = deepcopy(tools["derive_srd_scene_movement_budget"])
    if not any("affliction_movement_pending" in str(e) for e in budget["effects"]):
        budget["effects"].insert(
            0,
            _require(
                (
                    "actor_state.affliction_movement_pending != true or actor_state.control_order"
                    "_moving == true"
                ),
                "Perform compulsory movement through its saved source owner.",
            ),
        )
    changed["derive_srd_scene_movement_budget"] = budget
    # A new entry effect can interrupt an order's route before its original full endpoint.
    for name, original_tool in tools.items():
        if name == "move_srd_scene_creature":
            continue
        tool = deepcopy(changed.get(name, original_tool))
        moved = False
        for effect in _walk(tool.get("effects", [])):
            if effect.get("call") == "move_srd_scene_creature":
                effect.setdefault("export_context", {})["_scene_area_interrupted"] = (
                    "_scene_area_interrupted"
                )
                moved = True
        if moved:
            for effect in _walk(tool["effects"]):
                condition = effect.get("require", {}).get("condition", "")
                if ("combat.movement_remaining == 0") in condition and (
                    "_scene_area_interrupted"
                ) not in condition:
                    effect["require"]["condition"] = (
                        "_scene_area_interrupted == true or (" + condition + ")"
                    )
            changed[name] = tool
    return _replace_tools(text, changed)
