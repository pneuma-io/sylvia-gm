"""Complete divination consequences at the existing scene and active-source boundaries."""

import re
from copy import deepcopy

if __package__:
    from .srd_observer_visibility import observer_visibility_tool
    from .srd_scene_relations import _tool_scene
    from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _tool, _when
    from .srd_timed_senses import _loop, _optional, _push
else:
    from srd_observer_visibility import observer_visibility_tool
    from srd_scene_relations import _tool_scene
    from srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _tool, _when
    from srd_timed_senses import _loop, _optional, _push


def _touch_tools():
    """Movement breaks continuous touch only when the actual contact becomes impossible."""
    check = _tool_scene(
        "Calculate whether the actual same-plane subject remains physically touchable.",
        [_entity("caster_state"), _parameter("subject"), _parameter("touch_confirmed", "boolean")],
        [
            _set("_identify_touch_valid", "touch_confirmed == true"),
            _set("_identify_caster_scene_id", "''"),
            _set("_identify_subject_scene_id", "''"),
            _when(
                "_identify_touch_valid == true",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_caster_state_id}"},
                        ["_info_scene_entity", "_info_position"],
                    ),
                    _set("_identify_origin", "_info_position"),
                    _set("_identify_plane", "_info_scene_entity.metadata.plane"),
                    _set("_identify_caster_scene_id", "_info_scene_entity.id"),
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{subject}"},
                        ["_info_scene_entity", "_info_position"],
                    ),
                    _set("_identify_subject_scene_id", "_info_scene_entity.id"),
                    _call(
                        "measure_srd_information_distance",
                        {"start": "{_identify_origin}", "end": "{_info_position}"},
                        ["_scene_distance"],
                    ),
                    _set("_identify_touch_reach", 5),
                    _when(
                        "caster_state.melee_reach != null",
                        [_set("_identify_touch_reach", "caster_state.melee_reach")],
                    ),
                    _call(
                        "srd_information_barriers",
                        {"start": "{_identify_origin}", "end": "{_info_position}", "rule": "solid"},
                        ["_information_blocked"],
                    ),
                    _set(
                        "_identify_touch_valid",
                        "_identify_plane != null and "
                        "_info_scene_entity.metadata.plane == _identify_plane and "
                        "_scene_distance <= _identify_touch_reach and _information_blocked != true",
                    ),
                ],
            ),
        ],
    )
    validate = _tool(
        "Require actual physical reach and the continuous touch fact throughout Identify.",
        [_entity("caster_state"), _parameter("subject"), _parameter("touch_confirmed", "boolean")],
        [
            _call(
                "check_srd_identify_touch",
                {
                    "caster_state": "{_caster_state_id}",
                    "subject": "{subject}",
                    "touch_confirmed": "{touch_confirmed}",
                },
                [
                    "_identify_touch_valid",
                    "_identify_caster_scene_id",
                    "_identify_subject_scene_id",
                ],
            ),
            _require(
                "_identify_touch_valid == true", "Identify requires continuous physical touch."
            ),
        ],
    )
    movement = _tool(
        "Recheck continuous contact after the caster or touched subject actually moves.",
        [_entity("caster_state"), _parameter("scene_entity_id")],
        [
            _call(
                "format_srd_information_projection",
                {"message": "scene:{scene_entity_id}"},
                ["_info_projection_message"],
            ),
            _set("_identify_moved_scene_token", "_info_projection_message"),
            _call(
                "format_srd_information_projection",
                {"message": "scene:{caster_state.extended_casting.identify_caster_scene_id}"},
                ["_info_projection_message"],
            ),
            _set("_identify_caster_scene_token", "_info_projection_message"),
            _call(
                "format_srd_information_projection",
                {"message": "scene:{caster_state.extended_casting.identify_subject_scene_id}"},
                ["_info_projection_message"],
            ),
            _set("_identify_subject_scene_token", "_info_projection_message"),
            _when(
                "caster_state.concentration_effect_family == 'extended_casting' and "
                "caster_state.extended_casting != null and "
                "caster_state.extended_casting.spell == 'identify' and "
                "(_identify_moved_scene_token == _identify_caster_scene_token or "
                "_identify_moved_scene_token == _identify_subject_scene_token)",
                [
                    _call(
                        "check_srd_identify_touch",
                        {
                            "caster_state": "{_caster_state_id}",
                            "subject": "{caster_state.extended_casting.identify_subject}",
                            "touch_confirmed": True,
                        },
                        ["_identify_touch_valid"],
                    ),
                    _when(
                        "_identify_touch_valid != true",
                        [
                            _call(
                                "interrupt_srd_extended_casting",
                                {"caster_state": "{_caster_state_id}"},
                            )
                        ],
                    ),
                ],
            ),
        ],
    )
    return {
        "check_srd_identify_touch": check,
        "validate_srd_identify_touch": validate,
        "interrupt_srd_identify_source_movement": movement,
    }


def _legacy_source_tools():
    """Read existing target indexes and linked Guidance sources without a second spell registry."""
    source = _tool(
        "Read one actual legacy concentration instance and its existing Guidance predecessor.",
        [_entity("source_state"), _entity("target_state")],
        [
            _set("_identify_legacy_spell", None),
            _set("_identify_legacy_older", None),
            _set(
                "_identify_legacy_targeted",
                "source_state.concentration_target_state_id == _target_state_id or "
                "(source_state.concentration_target_state_ids != null and "
                "_target_state_id in source_state.concentration_target_state_ids) or "
                "(source_state.concentration_effect_family == 'summoned_creature' and "
                "target_state.summon_caster_state_id == _source_state_id)",
            ),
            _when(
                "_identify_legacy_targeted == true and "
                "source_state.concentration_spell_id != null and "
                "source_state.concentration_expires_tick != null and "
                "state.lasting_tick + 0.000000001 < source_state.concentration_expires_tick and "
                "source_state.is_alive == true and "
                "not ('incapacitated' in source_state.conditions or 'paralyzed' in "
                "source_state.conditions or 'petrified' in source_state.conditions or "
                "'stunned' in source_state.conditions or 'unconscious' in source_state.conditions)",
                [_set("_identify_legacy_spell", "source_state.concentration_spell_id")],
            ),
            _when(
                "_identify_legacy_targeted == true and "
                "source_state.concentration_effect_family == 'lasting_reactive_concentration' and "
                "source_state.concentration_spell_id == 'guidance' and "
                "source_state.concentration_older_caster_state_id != null",
                [
                    _set(
                        "_identify_legacy_older", "source_state.concentration_older_caster_state_id"
                    )
                ],
            ),
        ],
    )
    collect_effects = [_set("_identify_legacy_spells", []), _set("_identify_legacy_queue", [])]
    for field in ["guidance_head_caster_state_id", "summon_caster_state_id"]:
        collect_effects.append(
            _when(
                f"target_state.{field} != null",
                [_push("_identify_legacy_queue", f"target_state.{field}")],
            )
        )
    for field in [
        "concentration_d20_modifier_source_ids",
        "concentration_armor_class_source_ids",
        "concentration_resistance_source_ids",
    ]:
        collect_effects.append(
            _when(
                f"target_state.{field} != null",
                [
                    _set("_identify_legacy_index", f"target_state.{field}"),
                    _loop(
                        "_identify_legacy_index",
                        "_identify_legacy_indexed_source",
                        [
                            _when(
                                "_identify_legacy_indexed_source not in _identify_legacy_queue",
                                [
                                    _push(
                                        "_identify_legacy_queue", "_identify_legacy_indexed_source"
                                    )
                                ],
                            )
                        ],
                    ),
                ],
            )
        )
    collect_effects += [
        _set("_identify_legacy_seen", []),
        # The runtime's local list queue grows as linked predecessors are discovered.
        # Its ordinary operation cap and cycle check bound malformed saved links.
        _loop(
            "_identify_legacy_queue",
            "_identify_legacy_source_id",
            [
                _require(
                    "_identify_legacy_source_id not in _identify_legacy_seen",
                    "The saved Guidance source chain contains a cycle.",
                ),
                _push("_identify_legacy_seen", "_identify_legacy_source_id"),
                _call(
                    "identify_srd_legacy_spell_source",
                    {
                        "source_state": "{_identify_legacy_source_id}",
                        "target_state": "{_target_state_id}",
                    },
                    ["_identify_legacy_spell", "_identify_legacy_older"],
                ),
                _when(
                    "_identify_legacy_spell != null and "
                    "_identify_legacy_spell not in _identify_legacy_spells",
                    [_push("_identify_legacy_spells", "_identify_legacy_spell")],
                ),
                _when(
                    "_identify_legacy_older != null and "
                    "_identify_legacy_older not in _identify_legacy_seen and "
                    "_identify_legacy_older not in _identify_legacy_queue",
                    [_push("_identify_legacy_queue", "_identify_legacy_older")],
                ),
            ],
        ),
    ]
    collect = _tool(
        "Enumerate actual active legacy spells affecting this creature.",
        [_entity("target_state")],
        collect_effects,
    )
    return {
        "identify_srd_legacy_spell_source": source,
        "identify_srd_creature_legacy_spells": collect,
    }


def _nature_tools():
    return {
        "qualify_srd_nature_powerful_creature": _tool(
            "Qualify a powerful creature using its actual type and canonical Challenge Rating.",
            [_entity("target_state")],
            [
                _require(
                    "target_state.creature_kind != 'monster' or "
                    "(target_state.creature_type != null and "
                    "target_state.challenge_rating_eighths != null)",
                    "Commune with Nature requires the actual creature type and Challenge Rating.",
                ),
                _set(
                    "_nature_powerful_qualified",
                    "target_state.is_alive == true and "
                    "target_state.challenge_rating_eighths != null and "
                    "target_state.challenge_rating_eighths >= 80 and "
                    "(target_state.creature_type == 'celestial' or "
                    "target_state.creature_type == 'elemental' or "
                    "target_state.creature_type == 'fey' or "
                    "target_state.creature_type == 'fiend' or "
                    "target_state.creature_type == 'undead')",
                ),
            ],
        )
    }


def _observer_tools():
    """Knowledge of location and actual sight are separate observer-specific consequences."""
    visibility = observer_visibility_tool()
    hide = _tool(
        "Derive this actual enemy's sight and five-foot proximity before the Hide roll.",
        [_entity("actor_state"), _entity("observer_state")],
        [
            _set("_hide_observer_sees", False),
            _set("_hide_observer_near", False),
            _when(
                "observer_state.is_alive == true",
                [
                    _call(
                        "resolve_srd_observer_visibility",
                        {
                            "observer_state": "{_observer_state_id}",
                            "subject_state": "{_actor_state_id}",
                        },
                        ["_observer_can_see_subject", "_scene_distance", "_scene_same_plane"],
                    ),
                    _set("_hide_observer_sees", "_observer_can_see_subject"),
                    _set(
                        "_hide_observer_near", "_scene_same_plane == true and _scene_distance <= 5"
                    ),
                ],
            ),
        ],
    )
    collect = _tool(
        "Select only actual active same-plane Mind Spike casters tracking this subject.",
        [_entity("subject_state")],
        [
            _set("_mind_spike_observers", []),
            _when(
                "state.has_divination_spells == true",
                [
                    _set("_mind_spike_observer_records", "state.turn_effects"),
                    _loop(
                        "_mind_spike_observer_records",
                        "_mind_spike_observer_record",
                        [
                            _when(
                                "_mind_spike_observer_record.kind == 'divination_spell' and "
                                "_mind_spike_observer_record.spell == 'mind_spike' and "
                                "_mind_spike_observer_record.target == _subject_state_id",
                                [
                                    _call(
                                        "check_srd_mind_spike_tracking",
                                        {
                                            "caster_state": "{_mind_spike_observer_record.caster}",
                                            "target_state": "{_subject_state_id}",
                                        },
                                        ["_mind_spike_tracking"],
                                    ),
                                    _when(
                                        "_mind_spike_tracking == true and "
                                        "_mind_spike_observer_record.caster not in "
                                        "_mind_spike_observers",
                                        [
                                            _push(
                                                "_mind_spike_observers",
                                                "_mind_spike_observer_record.caster",
                                            )
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
    return {
        "resolve_srd_observer_visibility": visibility,
        "qualify_srd_hide_observer": hide,
        "collect_srd_mind_spike_observers": collect,
    }


def _observer_consumers(changed, tools):
    """Bind Hide and targeted Search to actual enemies and saved target checks."""
    hide = deepcopy(tools["hide_srd_action"])
    hide["effects"] = [
        e
        for e in hide["effects"]
        if "_hide_divination_enemies" not in str(e) and "Mind Spike Hide" not in str(e)
    ]
    if not any(p["name"] == "enemy_states" for p in hide["parameters"]):
        hide["parameters"].append(
            _optional("enemy_states", "entity_id_list", None, resolve="entity_list", max_items=64)
        )
    for parameter in hide["parameters"]:
        if parameter["name"] == "enemy_states":
            parameter["type"] = "entity_id_list"
            parameter.pop("items", None)
    hide["pre_conditions"] = [
        p
        for p in hide["pre_conditions"]
        if p["condition"]
        not in ["outside_enemy_line_of_sight == true", "enemy_within_5_feet == false"]
    ]
    validation = [
        _set("_hide_divination_enemies", None),
        _when("enemy_states != null", [_set("_hide_divination_enemies", "enemy_states")]),
        _when(
            "_hide_divination_enemies == null and actor_state.fighting_actor_ids != null",
            [_set("_hide_divination_enemies", "actor_state.fighting_actor_ids")],
        ),
        _require(
            "_hide_divination_enemies != null",
            "Mind Spike Hide needs the actual enemy actors, not supplied visibility facts.",
        ),
        _when(
            "actor_state.fighting_actor_ids != null",
            [
                _set("_hide_divination_fighting", "actor_state.fighting_actor_ids"),
                _loop(
                    "_hide_divination_fighting",
                    "_hide_divination_fighter",
                    [
                        _require(
                            "_hide_divination_fighter in _hide_divination_enemies",
                            "Include every saved current enemy in the Hide sight checks.",
                        )
                    ],
                ),
            ],
        ),
        _set("_hide_actual_outside", True),
        _set("_hide_actual_near", False),
        _loop(
            "_hide_divination_enemies",
            "_hide_divination_enemy",
            [
                _require(
                    "_hide_divination_enemy != _actor_state_id", "A creature is not its own enemy."
                ),
                _call(
                    "qualify_srd_hide_observer",
                    {
                        "actor_state": "{_actor_state_id}",
                        "observer_state": "{_hide_divination_enemy}",
                    },
                    ["_hide_observer_sees", "_hide_observer_near"],
                ),
                _when("_hide_observer_sees == true", [_set("_hide_actual_outside", False)]),
                _when("_hide_observer_near == true", [_set("_hide_actual_near", True)]),
            ],
        ),
    ]
    hide["effects"] = [
        e for e in hide["effects"] if e.get("call") != "validate_srd_hide_divination_enemies"
    ]
    changed["validate_srd_hide_divination_enemies"] = _tool(
        "Check actual enemies before Hide; retain ordinary admission when nobody tracks the actor.",
        [
            _entity("actor_state"),
            _optional("enemy_states", "entity_id_list", None, resolve="entity_list", max_items=64),
            _parameter("outside_enemy_line_of_sight", "boolean"),
            _parameter("enemy_within_5_feet", "boolean"),
        ],
        [
            _set("_hide_actual_outside", "outside_enemy_line_of_sight"),
            _set("_hide_actual_near", "enemy_within_5_feet"),
            _when(
                "state.has_divination_spells == true",
                [
                    _call(
                        "collect_srd_mind_spike_observers",
                        {"subject_state": "{_actor_state_id}"},
                        ["_mind_spike_observers"],
                    ),
                    _when("len(_mind_spike_observers) > 0", validation),
                ],
            ),
            _require(
                "_hide_actual_outside == true and _hide_actual_near == false",
                "Mind Spike Hide requires actual enemy sight and proximity checks.",
            ),
        ],
    )
    hide["effects"].insert(
        0,
        _call(
            "validate_srd_hide_divination_enemies",
            {
                "actor_state": "{_actor_state_id}",
                "enemy_states": "{enemy_states}",
                "outside_enemy_line_of_sight": "{outside_enemy_line_of_sight}",
                "enemy_within_5_feet": "{enemy_within_5_feet}",
            },
        ),
    )
    changed["hide_srd_action"] = hide
    search = deepcopy(tools["search_srd_action"])
    search["effects"] = [
        e
        for e in search["effects"]
        if e.get("call") == "d20_test"
        or ("_search_divination" not in str(e) and "record_srd_search_observation" not in str(e))
    ]
    # Reuse the sibling action's roll contract to repair already generated Search tools.
    if not any(effect.get("call") == "d20_test" for effect in search["effects"]):
        roll = deepcopy(
            next(e for e in tools["study_srd_action"]["effects"] if e.get("call") == "d20_test")
        )
        roll["params"]["ability"] = "wisdom"
        index = next(
            i
            for i, e in enumerate(search["effects"])
            if e.get("set", {}).get("field") == "_success"
        )
        search["effects"].insert(index, roll)
    if not any(p["name"] == "target_state" for p in search["parameters"]):
        search["parameters"].append(_optional("target_state", resolve="entity"))
    search["effects"].insert(0, _set("_search_divination_dc", "target_number"))
    search["effects"].insert(
        1,
        _when(
            "target_state != null",
            [
                _set("_search_divination_target", "_target_state_id"),
                _require(
                    "skill == 'perception'", "Searching for a Hidden creature uses Perception."
                ),
                _require(
                    "target_state.hidden_check_total != null",
                    "The Hidden target needs its actual Hide check.",
                ),
                _set("_search_divination_dc", "target_state.hidden_check_total"),
            ],
        ),
    )
    for effect in search["effects"]:
        if effect.get("call") == "d20_test":
            effect["params"]["target_number"] = "{_search_divination_dc}"
    changed["record_srd_search_observation"] = _tool(
        "Save this actual Search result and disclose a position only when its observer knows it.",
        [_entity("actor_state"), _entity("target_state"), _parameter("search_success", "boolean")],
        [
            _call(
                "resolve_srd_observer_visibility",
                {"observer_state": "{_actor_state_id}", "subject_state": "{_target_state_id}"},
                ["_observer_knows_location", "_observer_subject_hidden", "_scene_target_position"],
            ),
            _set(
                "_search_divination_known",
                "_observer_knows_location == true or search_success == true",
            ),
            _set("_search_divination_position", None),
            _when(
                "_search_divination_known == true",
                [_set("_search_divination_position", "_scene_target_position")],
            ),
            _set(
                "actor_state.last_search_target",
                {
                    "target": "_target_state_id",
                    "location_known": "_search_divination_known",
                    "hidden_from_observer": "_observer_subject_hidden == true and "
                    "search_success != true",
                    "position": None,
                    "tick": "state.lasting_tick",
                },
            ),
            _when(
                "_search_divination_known == true",
                [_set("actor_state.last_search_target.position", "_scene_target_position")],
            ),
        ],
    )
    search["effects"].append(
        _when(
            "target_state != null",
            [
                _call(
                    "record_srd_search_observation",
                    {
                        "actor_state": "{_actor_state_id}",
                        "target_state": "{_target_state_id}",
                        "search_success": "{_success}",
                    },
                ),
            ],
        )
    )
    changed["search_srd_action"] = search


def _identify_identity(effects):
    """Keep admitted subject IDs as strings when they also name an actual creature entity."""

    def bind(value):
        if isinstance(value, str):
            return re.sub(r"\bdivination_subject\b", "_identify_subject_ref", value)
        if isinstance(value, list):
            return [bind(item) for item in value]
        if isinstance(value, dict):
            return {key: bind(item) for key, item in value.items()}
        return value

    return [
        _call(
            "format_srd_information_projection",
            {"message": "{divination_subject}"},
            ["_info_projection_message"],
        ),
        _set("_identify_subject_ref", "_info_projection_message"),
        *bind(effects),
    ]


def deepen_divination_cast(tool):
    """Normalize only Identify's saved subject comparisons in the shared final casting owner."""

    def visit(effects):
        for effect in effects:
            if "spell == 'identify'" in effect.get("condition", "") and "divination_subject" in str(
                effect.get("on_true", [])
            ):
                if "_identify_subject_ref" in str(effect["on_true"]):
                    continue
                effect["on_true"] = _identify_identity(effect["on_true"])
                continue
            for branch in ["on_true", "on_false"]:
                if branch in effect:
                    visit(effect[branch])

    visit(tool["effects"])


def deepen_divination_tools(changed, tools):
    """Replace owned consequences while preserving shared casting and pending-action wrappers."""
    changed.update(_touch_tools() | _legacy_source_tools() | _nature_tools() | _observer_tools())
    _observer_consumers(changed, tools)
    changed["cast_srd_identify"]["effects"] = _identify_identity(
        changed["cast_srd_identify"]["effects"]
    )
    if "begin_srd_spell_cast" in changed:
        deepen_divination_cast(changed["begin_srd_spell_cast"])
    conditions = changed["identify_srd_creature_condition_spells"]
    conditions["effects"] += [
        _call(
            "identify_srd_creature_legacy_spells",
            {"target_state": "{_target_state_id}"},
            ["_identify_legacy_spells"],
        ),
        _loop(
            "_identify_legacy_spells",
            "_identify_legacy_spell_name",
            [
                _when(
                    "_identify_legacy_spell_name not in _identify_condition_spells",
                    [_push("_identify_condition_spells", "_identify_legacy_spell_name")],
                )
            ],
        ),
    ]

    # Source-backed creature qualification is inserted immediately before each selected candidate.
    def qualify(effects):
        for effect in effects:
            if (
                effect.get("set", {}).get("field") == "_nature_selected"
                and effect["set"].get("value") is False
            ):
                continue
            if "challenge_ratin" in str(effect.get("condition", "")):
                effect["condition"] = (
                    "_nature_choice == 'powerful_creature' and "
                    "_nature_powerful_qualified == true and _nature_powerful_found != true"
                )
                effect["on_true"] = [
                    _call(
                        "format_srd_information_projection",
                        {"message": "scene:{divination_subject}"},
                        ["_info_projection_message"],
                    ),
                    _set("_nature_requested_subject_token", "_info_projection_message"),
                    _call(
                        "format_srd_information_projection",
                        {"message": "scene:{_nature_candidate.id}"},
                        ["_info_projection_message"],
                    ),
                    _when(
                        "_nature_requested_subject_token == 'scene:' or "
                        "_nature_requested_subject_token == _info_projection_message",
                        [_set("_nature_selected", True)],
                    ),
                ]
            for branch in ["on_true", "on_false"]:
                if branch in effect:
                    qualify(effect[branch])
            if "for_each" in effect:
                qualify(effect["for_each"]["do"])
        for index, effect in enumerate(effects):
            if (
                effect.get("set", {}).get("field") == "_nature_selected"
                and effect["set"].get("value") is False
            ):
                effects[index:index] = [
                    _set("_nature_powerful_qualified", False),
                    _when(
                        "_nature_choice == 'powerful_creature' and "
                        "_nature_candidate.metadata.participant_ref != null",
                        [
                            _call(
                                "qualify_srd_nature_powerful_creature",
                                {"target_state": "{_nature_candidate.metadata.participant_ref}"},
                                ["_nature_powerful_qualified"],
                            )
                        ],
                    ),
                ]
                break

    qualify(changed["cast_srd_nature_divination"]["effects"])
