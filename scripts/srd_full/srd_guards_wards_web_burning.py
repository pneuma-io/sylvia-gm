"""Burn genuine ward stair Cubes without replacing their parent lifetime or identity."""

from copy import deepcopy

if __package__:
    from .srd_control_behaviors import _call, _each, _push
    from .srd_guards_wards_web import POOL, _sections
    from .srd_spell_barriers import _effect_lists, _tool
    from .srd_spell_magic_zones import _when
    from .srd_timed_actor_defenses import _entity, _load_runtime, _parameter, _require, _set
    from .srd_timed_senses import _replace_tools
else:
    from srd_control_behaviors import _call, _each, _push
    from srd_guards_wards_web import POOL, _sections
    from srd_spell_barriers import _effect_lists, _tool
    from srd_spell_magic_zones import _when
    from srd_timed_actor_defenses import _entity, _load_runtime, _parameter, _require, _set
    from srd_timed_senses import _replace_tools


def _overlap(a, b):
    return " and ".join(
        f"{a}.min[{i}] < {b}.max[{i}] and {a}.max[{i}] > {b}.min[{i}]" for i in range(3)
    )


def _retire_tool():
    return _tool(
        "Retire only this burned section's original stamped restraints, preserving other sources.",
        [_parameter("area_id")],
        [
            _set("_burn_section", f"state.{POOL}[area_id]"),
            _set("_burn_condition_ids", "_burn_section.condition_source_ids"),
            _each(
                "_burn_condition_ids",
                "_burn_condition_id",
                [
                    _set("_burn_condition", "state.timed_condition_sources[_burn_condition_id]"),
                    _when(
                        "_burn_condition.installed == true and "
                        "_burn_condition.spell_id == 'guards_and_wards' and "
                        "_burn_condition.guarded_web_section_id == area_id and "
                        "_burn_condition.guarded_web_parent_serial == _burn_section.parent_serial",
                        [
                            _call(
                                "retire_srd_timed_condition_source",
                                {
                                    "source_id": "{_burn_condition_id}",
                                    "lasting_state": "state",
                                },
                            ),
                            _call(
                                "refresh_srd_timed_condition_target",
                                {
                                    "target_state": "{_burn_condition.target_state_id}",
                                    "lasting_state": "state",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _timer_tool():
    # Mark absent strands before reentrant condition refresh can query the section.
    return _tool(
        "Settle the real one-round burn and ten-minute regrowth under the unchanged ward parent.",
        [],
        _sections(
            [
                _when(
                    "_ward_web_section.web_burn != null",
                    [
                        _set(
                            "_burn_parent",
                            "state.spell_guards_wards_sources[_ward_web_section.parent_source_id]",
                        ),
                        _when(
                            "_burn_parent.installed == true and "
                            "_burn_parent.groups.stairs == true and "
                            "_burn_parent.serial == _ward_web_section.parent_serial",
                            [
                                _when(
                                    "_ward_web_section.web_burn.status == 'burning' and "
                                    "state.lasting_tick >= "
                                    "_ward_web_section.web_burn.burn_end_tick",
                                    [
                                        _set(
                                            f"state.{POOL}.{{_ward_web_id}}.web_burn.status",
                                            "'destroyed'",
                                        ),
                                        _set(f"state.{POOL}.{{_ward_web_id}}.web_destroyed", True),
                                        _call(
                                            "retire_srd_guards_wards_web_burned_restraints",
                                            {
                                                "area_id": "{_ward_web_id}",
                                            },
                                        ),
                                    ],
                                ),
                                _when(
                                    "state.lasting_tick >= _ward_web_section.web_burn.regrow_tick",
                                    [
                                        _set(
                                            f"state.{POOL}.{{_ward_web_id}}.web_burn.status",
                                            "'regrown'",
                                        ),
                                        _set(f"state.{POOL}.{{_ward_web_id}}.web_destroyed", False),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ]
        ),
    )


def _supported_contact():
    """Reject ambiguous exposure rather than burning through unsupported solids or Cube layouts."""
    return [
        _require(
            " and ".join(
                f"abs(_ward_web_section.volume.max[{i}] - "
                f"_ward_web_section.volume.min[{i}] - 5) < 0.000000001"
                for i in range(3)
            ),
            "Burning larger stair Boxes needs actual five-foot Cube subdivision.",
        ),
        _set("_burn_geometry", "state.spatial_scene.geometry"),
        _each(
            "_burn_geometry",
            "_burn_solid",
            [
                _when(
                    "_burn_solid.active == true and _burn_solid.movement == 'blocking' "
                    "and _burn_solid.metadata.plane == _burn_fire.plane",
                    [
                        _require(
                            "_burn_solid.volume.kind == 'box'",
                            "Fire contact needs supported actual solid geometry.",
                        ),
                        _require(
                            "not ("
                            + _overlap(
                                "_ward_web_section.volume",
                                "_burn_solid.volume",
                            )
                            + ")",
                            "Fire contact through or around intersecting solids is unsupported.",
                        ),
                    ],
                ),
            ],
        ),
    ]


def _exposure_tool():
    tool = _tool(
        "Expose supported five-foot ward Cubes to an actual live Wall of Fire volume.",
        [_parameter("fire_area_id")],
        [
            _set("_burn_fire", "state.persistent_spell_areas[fire_area_id]"),
            _require("_burn_fire.mode == 'wall_fire'", "Use the actual Wall of Fire source."),
            _call(
                "qualify_srd_wall_of_fire_source",
                {
                    "caster_state": "{_burn_fire.caster}",
                    "area_id": "{fire_area_id}",
                },
                ("_sphere_source_current",),
            ),
            _when(
                "_sphere_source_current == true",
                _sections(
                    [
                        _call(
                            "qualify_srd_guards_wards_web_source",
                            {"area_id": "{_ward_web_id}"},
                            ("_sphere_source_current",),
                        ),
                        _when(
                            "_sphere_source_current == true and "
                            "_ward_web_section.web_destroyed != true "
                            "and _ward_web_section.plane == _burn_fire.plane and "
                            + _overlap("_ward_web_section.volume", "_burn_fire.volume"),
                            [
                                *_supported_contact(),
                                _when(
                                    "_ward_web_section.web_burn == null or "
                                    "_ward_web_section.web_burn.status == 'regrown'",
                                    [
                                        _set(
                                            f"state.{POOL}.{{_ward_web_id}}.web_burn",
                                            {
                                                "fire_area_id": "fire_area_id",
                                                "fire_sequence": "_burn_fire.sequence",
                                                "parent_serial": "_ward_web_section.parent_serial",
                                                "started_tick": "state.lasting_tick",
                                                "burn_end_tick": "state.lasting_tick + 2",
                                                "regrow_tick": "state.lasting_tick + 202",
                                                "status": "'burning'",
                                            },
                                        ),
                                        _set(f"state.{POOL}.{{_ward_web_id}}.web_destroyed", False),
                                        _set("state.has_guarded_web_burns", True),
                                    ],
                                ),
                            ],
                        ),
                    ]
                ),
            ),
        ],
    )
    # Ring shells have no Box min/max; keep their unimplemented section exposure
    # separate from real straight Fire sources and their paid installation.
    capture, source_requirement, *exposure = tool["effects"]
    for effect in exposure:
        if effect.get("condition") == "_sphere_source_current == true":
            effect["on_true"].insert(0, _set("_ward_web_fire_exposure_supported", True))
    tool["effects"] = [
        capture,
        source_requirement,
        _set("_ward_web_fire_exposure_supported", False),
        _when("_burn_fire.volume.kind == 'box'", exposure),
    ]
    return tool


def _fire_observer():
    return _tool(
        "Observe actual retained Wall of Fire sources after a real ward installation or regrowth.",
        [],
        [
            _when(
                "state.has_wall_of_fire == true and state.has_guarded_web_sections == true",
                [
                    _set("_burn_fire_ids", "state.persistent_spell_area_ids"),
                    _each(
                        "_burn_fire_ids",
                        "_burn_fire_id",
                        [
                            _set(
                                "_burn_fire_candidate",
                                "state.persistent_spell_areas[_burn_fire_id]",
                            ),
                            _when(
                                "_burn_fire_candidate.active == true and "
                                "_burn_fire_candidate.mode == 'wall_fire' and "
                                "_burn_fire_candidate.volume.kind == 'box'",
                                [
                                    _call(
                                        "expose_srd_guards_wards_web_fire",
                                        {
                                            "fire_area_id": "{_burn_fire_id}",
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


def _damage_effects():
    """Keep the actual raw roll, Ray allocation and sole typed lifecycle together."""
    return [
        {
            "dice": {"expression": "2d4"},
            "store_as": "_ward_web_fire_roll",
        },
        _call(
            "resolve_srd_actor_spell_modifiers",
            {
                "target_state": "{_ward_web_section.caster}",
                "lasting_state": "state",
            },
        ),
        _call(
            "modify_srd_actor_raw_damage_roll",
            {
                "actor_state": "{_ward_web_section.caster}",
                "lasting_state": "state",
                "amount": "{_ward_web_fire_roll.total}",
            },
            ("_ray_raw_damage",),
        ),
        _set("_ward_web_fire_amount", "_ray_raw_damage"),
        _set(
            "_ward_web_fire_debt",
            "max(0, -_ward_web_fire_amount)",
        ),
        _set(
            "_ward_web_fire_amount",
            "max(0, _ward_web_fire_amount)",
        ),
        _call(
            "apply_srd_qualifying_damage",
            {
                "target": "{_target_state_id}",
                "amount": "{_ward_web_fire_amount}",
                "adjustment": 0,
                "damage_type": "fire",
                "source_state_id": "{_ward_web_section.caster}",
                "game_damage_roll": True,
                "unallocated_roll_penalty": "{_ward_web_fire_debt}",
            },
            ("_effective_damage",),
        ),
        _set(
            "target_state.last_guards_wards_web_fire",
            {
                "section_id": "_ward_web_id",
                "parent_source_id": "_ward_web_section.parent_source_id",
                "parent_serial": "_ward_web_section.parent_serial",
                "turn_sequence": "combat.turn_sequence",
                "raw_damage": "_ward_web_fire_roll",
                "applied_damage": "_effective_damage",
            },
        ),
    ]


def _start_tool():
    return _tool(
        "Roll one real 2d4 Fire packet when this creature starts its turn in burning ward strands.",
        [_entity("target_state")],
        [
            _require(
                "combat.turn_active == true and combat.active_actor == _target_state_id",
                "Burning Web damage requires this creature's actual starting turn.",
            ),
            *_sections(
                [
                    _when(
                        "_ward_web_section.web_burn != null and "
                        "_ward_web_section.web_burn.status == 'burning' and "
                        "_ward_web_section.web_burn.parent_serial == "
                        "_ward_web_section.parent_serial "
                        "and state.lasting_tick < _ward_web_section.web_burn.burn_end_tick",
                        [
                            _call(
                                "qualify_srd_guards_wards_web_target",
                                {
                                    "target_state": "{_target_state_id}",
                                    "area_id": "{_ward_web_id}",
                                },
                                ("_sphere_target_inside",),
                            ),
                            _when(
                                "_sphere_target_inside == true",
                                [
                                    _call(
                                        "qualify_srd_guards_wards_web_effect",
                                        {
                                            "target_state": "{_target_state_id}",
                                            "area_id": "{_ward_web_id}",
                                        },
                                        ("_sphere_effect_applies",),
                                    ),
                                    _set(
                                        "_burn_parent",
                                        "state.spell_guards_wards_sources["
                                        "_ward_web_section.parent_source_id]",
                                    ),
                                    _set("_burn_prior", []),
                                    _when(
                                        "_burn_parent.web_fire_turn == combat.turn_sequence",
                                        [
                                            _set("_burn_prior", "_burn_parent.web_fire_targets"),
                                        ],
                                    ),
                                    _when(
                                        "_sphere_effect_applies == true and "
                                        "_target_state_id not in _burn_prior",
                                        [
                                            _push("_burn_prior", "_target_state_id"),
                                            _set(
                                                "state.spell_guards_wards_sources."
                                                "{_ward_web_section.parent_source_id}.web_fire_turn",
                                                "combat.turn_sequence",
                                            ),
                                            _set(
                                                "state.spell_guards_wards_sources."
                                                "{_ward_web_section.parent_source_id}.web_fire_targets",
                                                "_burn_prior",
                                            ),
                                            *_damage_effects(),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ]
            ),
        ],
    )


def _append(tool, name, effects):
    if name not in str(tool["effects"]):
        tool["effects"].extend(effects)


def bind_runtime_consumers(text):
    """Bind fire, clock, start-turn and strand consumers after their final owners."""
    tools = _load_runtime(text)["tools"]
    required = {
        "qualify_srd_wall_of_fire_source",
        "cast_spell_environment",
        "qualify_srd_guards_wards_web_target",
        "resolve_srd_guards_wards_web_body_segment",
    }
    if missing := required - tools.keys():
        raise SystemExit(f"Ward burning needs actual source owners: {sorted(missing)}")
    changed = {
        "retire_srd_guards_wards_web_burned_restraints": _retire_tool(),
        "refresh_srd_guards_wards_web_burns": _timer_tool(),
        "expose_srd_guards_wards_web_fire": _exposure_tool(),
        "observe_srd_guards_wards_web_fire": _fire_observer(),
        "resolve_srd_guards_wards_web_burning_start": _start_tool(),
    }
    refresh = deepcopy(tools["refresh_srd_guards_wards"])
    _append(
        refresh,
        "refresh_srd_guards_wards_web_burns",
        [
            _when(
                "state.has_guarded_web_burns == true",
                [
                    _call("refresh_srd_guards_wards_web_burns", {}),
                    _call("observe_srd_guards_wards_web_fire", {}),
                ],
            ),
        ],
    )
    changed["refresh_srd_guards_wards"] = refresh
    for name, guard, call in (
        (
            "cast_srd_guards_wards",
            "state.has_wall_of_fire == true",
            _call("observe_srd_guards_wards_web_fire", {}),
        ),
        (
            "cast_spell_environment",
            "_environment_profile.environment_mode == 'wall_fire' and "
            "state.has_guarded_web_sections == true and "
            "state.persistent_spell_areas[_environment_cast_id].volume.kind == 'box'",
            _call("expose_srd_guards_wards_web_fire", {"fire_area_id": "{_environment_cast_id}"}),
        ),
        (
            "resolve_srd_environment_hazard_start_turn",
            "state.has_guarded_web_burns == true",
            _call(
                "resolve_srd_guards_wards_web_burning_start", {"target_state": "{_target_state_id}"}
            ),
        ),
    ):
        owner = deepcopy(tools[name])
        if name == "cast_spell_environment":
            for effects in _effect_lists(owner["effects"]):
                for effect in effects:
                    if "_environment_profile.environment_mode == 'wall_fire'" in effect.get(
                        "condition", ""
                    ) and any(
                        child.get("call") == "expose_srd_guards_wards_web_fire"
                        for child in effect.get("on_true", [])
                    ):
                        effect["condition"] = guard
        _append(owner, call["call"], [_when(guard, [call])])
        changed[name] = owner
    # Parent-current remains valid during burning/destruction for exact group Dispel.
    for name, field in (
        ("qualify_srd_guards_wards_web_target", "_sphere_target_inside"),
        ("resolve_srd_guards_wards_web_body_segment", "_body_hit"),
    ):
        owner = deepcopy(tools[name])
        marker = f"state.{POOL}[area_id].web_destroyed == true"
        if marker not in str(owner["effects"]):
            owner["effects"].append(_when(marker, [_set(field, False)]))
        changed[name] = owner
    if "resolve_srd_guards_wards_obscuration" in tools:
        sight = deepcopy(tools["resolve_srd_guards_wards_obscuration"])
        for effects in _effect_lists(sight["effects"]):
            for effect in effects:
                clause = effect.get("condition", "")
                if (
                    "_guards_sight_section.active == true" in clause
                    and "web_destroyed" not in clause
                ):
                    effect["condition"] += " and _guards_sight_section.web_destroyed != true"
        changed["resolve_srd_guards_wards_obscuration"] = sight
    return _replace_tools(_replace_tools(text, changed), changed)
