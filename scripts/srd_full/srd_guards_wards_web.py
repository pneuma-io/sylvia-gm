"""Ward stair sections reuse Web's actual body/save/escape owners, without Web concentration."""

from copy import deepcopy

if __package__:
    from .srd_control_behaviors import _call, _each, _push
    from .srd_guards_wards import _active, _exempt, _read
    from .srd_scene_relations import _array
    from .srd_spell_barriers import _effect_lists, _scene, _tool
    from .srd_spell_environment import _web_tools
    from .srd_spell_magic_zones import _when
    from .srd_timed_actor_defenses import _entity, _load_runtime, _parameter, _require, _set
    from .srd_timed_senses import _optional, _replace_tools
else:
    from srd_control_behaviors import _call, _each, _push
    from srd_guards_wards import _active, _exempt, _read
    from srd_scene_relations import _array
    from srd_spell_barriers import _effect_lists, _scene, _tool
    from srd_spell_environment import _web_tools
    from srd_spell_magic_zones import _when
    from srd_timed_actor_defenses import _entity, _load_runtime, _parameter, _require, _set
    from srd_timed_senses import _optional, _replace_tools

PUBLIC_TOOLS = {"escape_srd_guards_wards_web"}
POOL = "spell_guarded_web_sections"


def _bind(value):
    """Rebind only the child source namespace and private owner names, retaining geometry."""
    if isinstance(value, str):
        return (
            value.replace("persistent_spell_areas", POOL)
            .replace("srd_web", "srd_guards_wards_web")
            .replace("'web'", "'guards_and_wards'")
            .replace("last_web", "last_guards_wards_web")
        )
    if isinstance(value, list):
        return [_bind(item) for item in value]
    if isinstance(value, dict):
        return {key: _bind(item) for key, item in value.items()}
    return value


def _sections(effects):
    return [
        _when(
            "state.has_guarded_web_sections == true",
            [
                _set("_ward_web_ids", "state.guarded_web_section_ids"),
                _each(
                    "_ward_web_ids",
                    "_ward_web_id",
                    [
                        _set("_ward_web_section", f"state.{POOL}[_ward_web_id]"),
                        _when("_ward_web_section.active == true", effects),
                    ],
                ),
            ],
        )
    ]


def _winning_tool():
    return _tool(
        "Select the strongest active overlapping ward stairs effect, then the latest tie.",
        [_entity("target_state"), _parameter("area_id")],
        [
            _set("_gw_selected", f"state.{POOL}[area_id]"),
            _set("_sphere_effect_applies", True),
            *_sections(
                [
                    _when(
                        "_ward_web_section.parent_source_id != _gw_selected.parent_source_id and "
                        "(_ward_web_section.spell_level > _gw_selected.spell_level or "
                        "(_ward_web_section.spell_level == _gw_selected.spell_level and "
                        "_ward_web_section.parent_serial > _gw_selected.parent_serial))",
                        [
                            _call(
                                "qualify_srd_guards_wards_web_target",
                                {"target_state": "{_target_state_id}", "area_id": "{_ward_web_id}"},
                                ("_sphere_target_inside",),
                            ),
                            _when(
                                "_sphere_target_inside == true",
                                [_set("_sphere_effect_applies", False)],
                            ),
                        ],
                    ),
                ]
            ),
        ],
    )


def _source_tools():
    qualify = _tool(
        "Qualify the exact ward stairs group, independent of caster life or concentration.",
        [_parameter("area_id")],
        [
            _set("_sphere_source_current", False),
            _require(
                f"state.{POOL} != null and area_id in state.{POOL}",
                "Select an actual ward stair section.",
            ),
            _set("_ward_web_section", f"state.{POOL}[area_id]"),
            _set(
                "_guards_source",
                "state.spell_guards_wards_sources[_ward_web_section.parent_source_id]",
            ),
            _set(
                "_sphere_source_current",
                "_ward_web_section.profile == 'guarded_web' and "
                "_ward_web_section.spell == 'guards_and_wards' and "
                "_ward_web_section.active == true "
                "and _guards_source.serial == _ward_web_section.parent_serial",
            ),
            _when(
                "_sphere_source_current == true",
                [
                    _set(
                        "_sphere_source_current",
                        _active() + " and _guards_source.groups.stairs == true",
                    ),
                ],
            ),
        ],
    )
    exempt = _tool(
        "Read actual designated creatures and password speakers from this section's parent.",
        [_entity("target_state"), _parameter("area_id")],
        [
            _call(
                "qualify_srd_guards_wards_web_source",
                {"area_id": "{area_id}"},
                ("_sphere_source_current",),
            ),
            _set("_ward_web_section", f"state.{POOL}[area_id]"),
            _set(
                "_guards_source",
                "state.spell_guards_wards_sources[_ward_web_section.parent_source_id]",
            ),
            _set("_ward_web_exempt", _exempt("_target_state_id")),
        ],
    )
    install = _tool(
        "Save bounded stair Boxes with the authentic ward parent DC and lifetime.",
        [_parameter("source_id"), _optional("sections", "array", [], items={"type": "object"})],
        [
            *_read(),
            _require(
                _active() + " and _guards_source.groups.stairs == true",
                "The ward stairs group must be active.",
            ),
            _require("len(sections) <= 64", "Select at most64 admitted stair sections."),
            _require(
                "len(sections) == 0 or (_guards_source.save_dc != null and "
                "_guards_source.save_dc >= 1)",
                "Stair saves require the actual parent caster's derived save DC.",
            ),
            _when(
                f"state.{POOL} == null",
                [_set(f"state.{POOL}", {}), _set("state.guarded_web_section_ids", [])],
            ),
            _set("_ward_web_retained", "state.guarded_web_section_ids"),
            _set("_ward_web_parent_sections", []),
            _set("_ward_web_section_number", 0),
            _each(
                "sections",
                "_ward_web_box",
                [
                    *[
                        _require(
                            f"_ward_web_box.min[{a}] >= _guards_source.box.min[{a}] and "
                            f"_ward_web_box.max[{a}] <= _guards_source.box.max[{a}] and "
                            f"_ward_web_box.min[{a}] < _ward_web_box.max[{a}]",
                            "Each selected stair Box must fit entirely within its actual ward.",
                        )
                        for a in range(3)
                    ],
                    _call(
                        "format_srd_information_projection",
                        {"message": "{source_id}:stairs:{_ward_web_section_number}"},
                        ("_info_projection_message",),
                    ),
                    _set("_ward_web_new_id", "_info_projection_message"),
                    _set(
                        f"state.{POOL}.{{_ward_web_new_id}}",
                        {
                            "profile": "'guarded_web'",
                            "spell": "'guards_and_wards'",
                            "mode": "'guards_and_wards'",
                            "active": True,
                            "parent_source_id": "source_id",
                            "parent_serial": "_guards_source.serial",
                            "caster": "_guards_source.caster_state_id",
                            "plane": "_guards_source.plane",
                            "sequence": "_guards_source.serial",
                            "save_dc": "_guards_source.save_dc",
                            "spell_level": "_guards_source.spell_level",
                            "permanent": "_guards_source.permanent",
                            "expires_tick": "_guards_source.expires_tick",
                            "condition_source_ids": [],
                            "volume": {
                                "kind": "'box'",
                                "min": "_ward_web_box.min",
                                "max": "_ward_web_box.max",
                            },
                            "origin": [
                                "(_ward_web_box.min[0] + _ward_web_box.max[0]) * 0.5",
                                "(_ward_web_box.min[1] + _ward_web_box.max[1]) * 0.5",
                                "(_ward_web_box.min[2] + _ward_web_box.max[2]) * 0.5",
                            ],
                            "event_clock": "'none'",
                            "event_turn": -1,
                            "event_target_ids": [],
                            "start_event_turn": -1,
                            "start_event_targets": [],
                        },
                    ),
                    _push("_ward_web_retained", "_ward_web_new_id"),
                    _push("_ward_web_parent_sections", "_ward_web_new_id"),
                    _set("_ward_web_section_number", "_ward_web_section_number + 1"),
                ],
            ),
            _set("state.guarded_web_section_ids", "_ward_web_retained"),
            _set("state.has_guarded_web_sections", "len(_ward_web_retained) > 0"),
            _set(
                "state.spell_guards_wards_sources.{source_id}.web_section_ids",
                "_ward_web_parent_sections",
            ),
        ],
    )
    end = _tool(
        "Retire only restraints stamped with this exact parent stair source and serial.",
        [_parameter("source_id")],
        [
            *_read(),
            _set("_ward_web_ending_sections", []),
            _when(
                "_guards_source.web_section_ids != null",
                [_set("_ward_web_ending_sections", "_guards_source.web_section_ids")],
            ),
            _each(
                "_ward_web_ending_sections",
                "_ward_web_ending_id",
                [
                    _set("_ward_web_ending", f"state.{POOL}[_ward_web_ending_id]"),
                    _require(
                        "_ward_web_ending.parent_source_id == source_id and "
                        "_ward_web_ending.parent_serial == _guards_source.serial",
                        "The section must retain its original ward parent.",
                    ),
                    _set(f"state.{POOL}.{{_ward_web_ending_id}}.active", False),
                    _set("_ward_web_condition_ids", "_ward_web_ending.condition_source_ids"),
                    _each(
                        "_ward_web_condition_ids",
                        "_ward_web_condition_id",
                        [
                            _set(
                                "_ward_web_condition",
                                "state.timed_condition_sources[_ward_web_condition_id]",
                            ),
                            _when(
                                "_ward_web_condition.installed == true and "
                                "_ward_web_condition.spell_id == 'guards_and_wards' and "
                                "_ward_web_condition.guarded_web_section_id == "
                                "_ward_web_ending_id and "
                                "_ward_web_condition.guarded_web_parent_serial == "
                                "_guards_source.serial",
                                [
                                    _call(
                                        "retire_srd_timed_condition_source",
                                        {
                                            "source_id": "{_ward_web_condition_id}",
                                            "lasting_state": "state",
                                        },
                                    ),
                                    _call(
                                        "refresh_srd_timed_condition_target",
                                        {
                                            "target_state": "{_ward_web_condition.target_state_id}",
                                            "lasting_state": "state",
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
    return {
        "qualify_srd_guards_wards_web_source": qualify,
        "qualify_srd_guards_wards_web_exemption": exempt,
        "install_srd_guards_wards_web_sections": install,
        "retire_srd_guards_wards_web_sections": end,
    }


def _child_tools(tools):
    """Clone the actual Web Body/DEX/Action mechanics, changing only their source provider."""
    originals = _web_tools()
    selected = {
        name.replace("srd_web", "srd_guards_wards_web"): _bind(deepcopy(originals[name]))
        for name in (
            "resolve_srd_web_body_segment",
            "qualify_srd_web_target",
            "resolve_srd_web_event",
            "escape_srd_web",
        )
    }
    target = selected["qualify_srd_guards_wards_web_target"]
    for effects in _effect_lists(target["effects"]):
        for effect in effects:
            if effect.get("call") == "qualify_srd_guards_wards_web_source":
                effect["call"] = "qualify_srd_guards_wards_web_exemption"
                effect["params"] = {"target_state": "{_target_state_id}", "area_id": "{area_id}"}
                effect["export_context"] = {
                    key: key for key in ("_sphere_source_current", "_ward_web_exempt")
                }
            if (
                effect.get("condition")
                == "_sphere_source_current == true and target_state.is_alive == true"
            ):
                effect["condition"] += " and _ward_web_exempt != true"
    event = selected["resolve_srd_guards_wards_web_event"]
    for effects in list(_effect_lists(event["effects"])):
        for i, effect in list(enumerate(effects)):
            if effect.get("call") == "install_srd_environment_restrained":
                effect["call"] = "install_srd_guards_wards_web_restrained"
                effect["params"]["spell"] = "guards_and_wards"
            if effect.get("set", {}).get("field", "").endswith(".expires_tick"):
                effects[i] = _when(
                    "_web_source.permanent != true", [effect], [_set(effect["set"]["field"], None)]
                )
            if effect.get("set", {}).get("field", "").endswith(".environment_sequence"):
                effects[i + 1 : i + 1] = [
                    _set(
                        effect["set"]["field"].replace(
                            ".environment_sequence", ".guarded_web_section_id"
                        ),
                        "area_id",
                    ),
                    _set(
                        effect["set"]["field"].replace(
                            ".environment_sequence", ".guarded_web_parent_serial"
                        ),
                        "_web_source.parent_serial",
                    ),
                ]
    # Reuse the genuine shared installer with the ordinary unowned-restraint snapshot.
    selected["install_srd_guards_wards_web_restrained"] = deepcopy(
        tools["install_srd_environment_restrained"]
    )
    escape = selected["escape_srd_guards_wards_web"]
    for effects in list(_effect_lists(escape["effects"])):
        for i, effect in list(enumerate(effects)):
            if "still-active Web restraint source" in effect.get("require", {}).get("error", ""):
                effect["require"]["condition"] = (
                    "_escape_source.installed == true and "
                    "_escape_source.spell_id == 'guards_and_wards' and "
                    "_escape_source.target_state_id == _actor_state_id and "
                    "_escape_source.condition == 'restrained' "
                    "and _escape_source.environment_sequence == _escape_area.sequence "
                    "and _escape_source.guarded_web_parent_serial == _escape_area.parent_serial"
                )
                effects[i + 1 : i + 1] = [
                    _call(
                        "qualify_srd_guards_wards_web_target",
                        {
                            "target_state": "{_actor_state_id}",
                            "area_id": "{_escape_source.guarded_web_section_id}",
                        },
                        ("_sphere_target_inside",),
                    ),
                    _require(
                        "_sphere_target_inside == true and _escape_source.suppressed != true",
                        "Escape the actual unsuppressed parent-qualified stair restraint.",
                    ),
                    _call(
                        "qualify_srd_guards_wards_web_effect",
                        {
                            "target_state": "{_actor_state_id}",
                            "area_id": "{_escape_source.guarded_web_section_id}",
                        },
                        ("_sphere_effect_applies",),
                    ),
                    _require(
                        "_sphere_effect_applies == true",
                        "Escape the actual winning ward restraint source.",
                    ),
                ]
    # First entry is once per turn for this one parent effect, across all its stair sections.
    receipts = (
        "event_clock",
        "event_turn",
        "event_target_ids",
        "start_event_turn",
        "start_event_targets",
    )

    def parent_receipts(value):
        if isinstance(value, str):
            for field in receipts:
                value = value.replace("_web_source." + field, "_gw_parent." + field)
            return value
        if isinstance(value, list):
            return [parent_receipts(item) for item in value]
        if isinstance(value, dict):
            return {key: parent_receipts(item) for key, item in value.items()}
        return value

    event["effects"] = parent_receipts(event["effects"])
    index = next(
        i for i, e in enumerate(event["effects"]) if e.get("set", {}).get("field") == "_web_source"
    )
    event["effects"].insert(
        index + 1,
        _set(
            "_gw_parent", "lasting_state.spell_guards_wards_sources[_web_source.parent_source_id]"
        ),
    )
    for effects in _effect_lists(event["effects"]):
        for effect in effects:
            field = effect.get("set", {}).get("field", "")
            if field.rsplit(".", 1)[-1] in receipts:
                effect["set"]["field"] = (
                    "lasting_state.spell_guards_wards_sources.{_web_source.parent_source_id}."
                    + field.rsplit(".", 1)[-1]
                )
    return selected


def _projection_tools():
    """Retain suppressed exact sources while projecting only active, nonexempt restraints."""
    return {
        "refresh_srd_guards_wards_web_target": _tool(
            "Retire actual exited sections and project retained stair immunity/suppression "
            "without erasing independent restraints.",
            [_entity("target_state")],
            [
                _set("_ward_web_target_ids", []),
                _when(
                    "target_state.timed_condition_source_ids != null",
                    [_set("_ward_web_target_ids", "target_state.timed_condition_source_ids")],
                ),
                _set("_ward_web_has_sources", False),
                _each(
                    "_ward_web_target_ids",
                    "_ward_web_target_id",
                    [
                        _set(
                            "_ward_web_target_source",
                            "state.timed_condition_sources[_ward_web_target_id]",
                        ),
                        _when(
                            "_ward_web_target_source.installed == true and "
                            "_ward_web_target_source.guarded_web_section_id != null",
                            [
                                _set("_ward_web_has_sources", True),
                                _call(
                                    "qualify_srd_guards_wards_web_exemption",
                                    {
                                        "target_state": "{_target_state_id}",
                                        "area_id": (
                                            "{_ward_web_target_source.guarded_web_section_id}"
                                        ),
                                    },
                                    ("_sphere_source_current", "_ward_web_exempt"),
                                ),
                                _call(
                                    "resolve_srd_guards_wards_web_body_segment",
                                    {
                                        "moving_subject": "{_target_state_id}",
                                        "area_id": (
                                            "{_ward_web_target_source.guarded_web_section_id}"
                                        ),
                                        "start": "{_ward_web_target_position}",
                                        "end": "{_ward_web_target_position}",
                                    },
                                    ("_body_hit",),
                                ),
                                _when(
                                    "_body_hit != true",
                                    [
                                        _call(
                                            "retire_srd_timed_condition_source",
                                            {
                                                "source_id": "{_ward_web_target_id}",
                                                "lasting_state": "state",
                                            },
                                        ),
                                    ],
                                    [
                                        _call(
                                            "check_srd_target_condition_immunity",
                                            {
                                                "target_state": "{_target_state_id}",
                                                "condition": "restrained",
                                                "source_state_id": (
                                                    "{_ward_web_target_source.caster_state_id}"
                                                ),
                                                "source_is_magical": True,
                                            },
                                        ),
                                        _call(
                                            "qualify_srd_guards_wards_web_effect",
                                            {
                                                "target_state": "{_target_state_id}",
                                                "area_id": (
                                                    "{_ward_web_target_source.guarded_web_section_id}"
                                                ),
                                            },
                                            ("_sphere_effect_applies",),
                                        ),
                                        _set(
                                            "state.timed_condition_sources."
                                            "{_ward_web_target_id}.suppressed",
                                            "_sphere_source_current != true or "
                                            "_ward_web_exempt == true or "
                                            "_source_condition_immune == true or "
                                            "_sphere_effect_applies != true",
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
                _when(
                    "_ward_web_has_sources == true",
                    [
                        _set(
                            "_ward_web_unowned_magical",
                            "target_state.unowned_restrained_is_magical == true",
                        ),
                        _call(
                            "check_srd_target_condition_immunity",
                            {
                                "target_state": "{_target_state_id}",
                                "condition": "restrained",
                                "source_is_magical": "{_ward_web_unowned_magical}",
                            },
                        ),
                        _set(
                            "_ward_web_restrained",
                            "target_state.timed_condition_unowned_restrained == true and "
                            "_source_condition_immune != true",
                        ),
                        _set("_ward_web_owned_restrained", False),
                        _each(
                            "_ward_web_target_ids",
                            "_ward_web_target_id",
                            [
                                _set(
                                    "_ward_web_target_source",
                                    "state.timed_condition_sources[_ward_web_target_id]",
                                ),
                                _when(
                                    "_ward_web_target_source.installed == true and "
                                    "_ward_web_target_source.condition == 'restrained' "
                                    "and _ward_web_target_source.suppressed != true",
                                    [
                                        _set("_ward_web_restrained", True),
                                        _set("_ward_web_owned_restrained", True),
                                    ],
                                ),
                            ],
                        ),
                        {"remove_condition": {"target": "target_state", "condition": "restrained"}},
                        _when(
                            "_ward_web_restrained == true",
                            [
                                {
                                    "apply_condition": {
                                        "target": "target_state",
                                        "condition": "restrained",
                                    }
                                }
                            ],
                        ),
                        _set(
                            "target_state.timed_condition_restrained", "_ward_web_owned_restrained"
                        ),
                    ],
                ),
            ],
        )
    }


def _bind_parent(tools, changed):
    parent = deepcopy(tools["cast_srd_guards_wards"])
    parent["parameters"].extend(
        [
            _optional("save_dc", "integer", None, model_hidden=True),
            _optional("stairs", "array", [], items={"type": "object"}),
        ]
    )
    parent["effects"].extend(
        [
            _set(
                "state.spell_guards_wards_sources.{_new_guards_wards_source_id}.save_dc", "save_dc"
            ),
            _call(
                "install_srd_guards_wards_web_sections",
                {"source_id": "{_new_guards_wards_source_id}", "sections": "{stairs}"},
            ),
        ]
    )
    changed["cast_srd_guards_wards"] = parent
    cast = deepcopy(tools["cast_spell"])
    if not any(p["name"] == "guards_wards_stairs" for p in cast["parameters"]):
        cast["parameters"].append(
            _optional("guards_wards_stairs", "array", [], items={"type": "object"})
        )
    for effects in _effect_lists(cast["effects"]):
        for effect in effects:
            if effect.get("call") == "cast_srd_guards_wards":
                effect["params"].update(
                    save_dc="{_casting_profile.save_dc}", stairs="{guards_wards_stairs}"
                )
    changed["cast_spell"] = cast
    ending = deepcopy(tools["end_srd_guards_wards_effect"])
    ending["effects"].insert(
        2,
        _when(
            "group == 'stairs' or group == 'all'",
            [
                _call("retire_srd_guards_wards_web_sections", {"source_id": "{source_id}"}),
            ],
        ),
    )
    changed["end_srd_guards_wards_effect"] = ending
    password = deepcopy(tools["speak_srd_guards_wards_password"])
    password["effects"].append(
        _when(
            "actor_state.has_guarded_web_restraints == true",
            [
                _call(
                    "refresh_srd_timed_condition_target",
                    {"target_state": "{_actor_state_id}", "lasting_state": "state"},
                ),
            ],
        )
    )
    changed["speak_srd_guards_wards_password"] = password


def extend_runtime(text):
    """Emit only the ward's child provider; bind final movement consumers separately."""
    original = _load_runtime(text)
    tools = original["tools"]
    # The parent emitter regenerates its original tools before this adapter on each build.
    changed = {
        **_source_tools(),
        **_child_tools(tools),
        **_projection_tools(),
        "qualify_srd_guards_wards_web_effect": _winning_tool(),
    }
    if not any(p["name"] == "stairs" for p in tools["cast_srd_guards_wards"]["parameters"]):
        _bind_parent(tools, changed)
    projection = changed["refresh_srd_guards_wards_web_target"]
    projection["effects"][0:0] = _scene("_target_state_id")
    projection["effects"].insert(1, _set("_ward_web_target_position", "_info_position"))
    refresh = deepcopy(tools["refresh_srd_timed_condition_target"])
    if "refresh_srd_guards_wards_web_target" not in str(refresh["effects"]):
        refresh["effects"].append(
            _when(
                "target_state.has_guarded_web_restraints == true",
                [
                    _call(
                        "refresh_srd_guards_wards_web_target",
                        {"target_state": "{_target_state_id}"},
                    ),
                ],
            )
        )
    changed["refresh_srd_timed_condition_target"] = refresh
    event = changed["resolve_srd_guards_wards_web_event"]
    for effects in _effect_lists(event["effects"]):
        for i, effect in list(enumerate(effects)):
            if effect.get("set", {}).get("field", "").endswith(".guarded_web_parent_serial"):
                effects.insert(i + 1, _set("target_state.has_guarded_web_restraints", True))
    text = _replace_tools(text, changed)
    return _replace_tools(text, {k: v for k, v in changed.items() if k not in tools})


def _movement_tools():
    entry = _tool(
        "Find the next actual nonexempt ward stair entry on the committed body segment.",
        [_entity("actor_state"), _array("destination")],
        [
            *_scene("_actor_state_id"),
            _set("_ward_web_start", "_info_position"),
            _set("_ward_web_entry_t", 2),
            _set("_ward_web_entry_events", []),
            _set("_ward_web_clock", "'exploration'"),
            _set("_ward_web_turn", "state.lasting_tick"),
            _when(
                "combat.turn_active == true",
                [
                    _set("_ward_web_clock", "'combat'"),
                    _set("_ward_web_turn", "combat.turn_sequence"),
                ],
            ),
            *_sections(
                [
                    _set(
                        "_gw_entry_parent",
                        "state.spell_guards_wards_sources[_ward_web_section.parent_source_id]",
                    ),
                    _set("_ward_web_done", []),
                    _when(
                        "_gw_entry_parent.event_clock == _ward_web_clock and "
                        "_gw_entry_parent.event_turn == _ward_web_turn",
                        [_set("_ward_web_done", "_gw_entry_parent.event_target_ids")],
                    ),
                    _call(
                        "qualify_srd_guards_wards_web_exemption",
                        {"target_state": "{_actor_state_id}", "area_id": "{_ward_web_id}"},
                        ("_sphere_source_current", "_ward_web_exempt"),
                    ),
                    _when(
                        "_sphere_source_current == true and _ward_web_exempt != true and "
                        "_actor_state_id not in _ward_web_done",
                        [
                            _call(
                                "resolve_srd_guards_wards_web_body_segment",
                                {
                                    "moving_subject": "{_actor_state_id}",
                                    "area_id": "{_ward_web_id}",
                                    "start": "{_ward_web_start}",
                                    "end": "{destination}",
                                },
                                ("_body_hit", "_body_entry", "_body_exit"),
                            ),
                            _when(
                                "_body_hit == true and _body_entry > 0.000000000001 and "
                                "_body_entry <= 1",
                                [
                                    _call(
                                        "measure_srd_information_distance",
                                        {"start": "{_ward_web_start}", "end": "{destination}"},
                                        ("_scene_distance",),
                                    ),
                                    _set(
                                        "_body_entry",
                                        "min(1, _body_entry + min((_body_exit - "
                                        "_body_entry) * 0.5, 0.000001 * _scene_distance ** "
                                        "-1))",
                                    ),
                                    _when(
                                        "_body_entry < _ward_web_entry_t - 0.000000000001",
                                        [
                                            _set("_ward_web_entry_t", "_body_entry"),
                                            _set("_ward_web_entry_events", []),
                                        ],
                                    ),
                                    _when(
                                        "abs(_body_entry - _ward_web_entry_t) < 0.000000000001",
                                        [
                                            _push(
                                                "_ward_web_entry_events",
                                                {
                                                    "area_id": "_ward_web_id",
                                                    "target_id": "_actor_state_id",
                                                },
                                            ),
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
    terrain = _tool(
        "Resolve real ward stair Difficult Terrain through the existing interval union.",
        [_entity("actor_state"), _array("start"), _array("end")],
        [
            _set("_ward_web_route_intervals", []),
            *_sections(
                [
                    _call(
                        "qualify_srd_guards_wards_web_exemption",
                        {"target_state": "{_actor_state_id}", "area_id": "{_ward_web_id}"},
                        ("_sphere_source_current", "_ward_web_exempt"),
                    ),
                    _when(
                        "_sphere_source_current == true and _ward_web_exempt != true",
                        [
                            _call(
                                "resolve_srd_guards_wards_web_body_segment",
                                {
                                    "moving_subject": "{_actor_state_id}",
                                    "area_id": "{_ward_web_id}",
                                    "start": "{start}",
                                    "end": "{end}",
                                },
                                ("_body_hit", "_body_entry", "_body_exit"),
                            ),
                            _when(
                                "_body_hit == true",
                                [
                                    _push(
                                        "_ward_web_route_intervals",
                                        {
                                            "entry": "_body_entry",
                                            "exit": "_body_exit",
                                            "ward_source": True,
                                        },
                                    )
                                ],
                            ),
                        ],
                    ),
                ]
            ),
        ],
    )
    start = _tool(
        "Resolve only the actual starting creature's parent-qualified stair saves.",
        [_entity("target_state")],
        [
            *_sections(
                [
                    _call(
                        "resolve_srd_guards_wards_web_event",
                        {
                            "target_state": "{_target_state_id}",
                            "area_id": "{_ward_web_id}",
                            "timing": "start",
                        },
                    )
                ]
            ),
        ],
    )
    relocation = _tool(
        "Reproject exact stair restraint ownership after the shared actual relocation.",
        [_parameter("subject")],
        [
            *_scene("subject"),
            _when(
                "_info_scene_entity.metadata.participant_ref != null",
                [
                    _call(
                        "refresh_srd_timed_condition_target",
                        {
                            "target_state": "{_info_scene_entity.metadata.participant_ref}",
                            "lasting_state": "state",
                        },
                    ),
                ],
            ),
        ],
    )
    return {
        "resolve_srd_guards_wards_web_entry": entry,
        "resolve_srd_guards_wards_web_intervals": terrain,
        "resolve_srd_guards_wards_web_start": start,
        "observe_srd_guards_wards_web_relocation": relocation,
    }


def _bind_ground_routes(tools):
    """Extend the actual ground-route leaf, including the delegated environment segment."""
    changed = {}
    for name in ("move_srd_scene_creature_node", "move_srd_scene_creature_environment_segment"):
        if name not in tools:
            continue
        node = deepcopy(tools[name])
        selected = [
            rows
            for rows in _effect_lists(node["effects"])
            if any(
                effect.get("list_push", {}).get("list") == "_ground_route_ids"
                and effect["list_push"].get("value") == "'final_endpoint'"
                for effect in rows
            )
        ]
        if not selected:
            continue
        if len(selected) != 1:
            raise ValueError("Guarded Web needs one actual ground-route boundary: " + name)
        body = selected[0]
        if not any("_ward_web_movement_ids" in str(effect) for effect in body):
            for rows in _effect_lists(node["effects"]):
                for effect in rows:
                    if effect.get("on_true") is body:
                        effect["condition"] += " or lasting_state.has_guarded_web_sections == true"
            index = next(
                i
                for i, effect in enumerate(body)
                if effect.get("list_push", {}).get("value") == "'final_endpoint'"
            )
            body.insert(
                index,
                _when(
                    "lasting_state.has_guarded_web_sections == true",
                    [
                        _set("_ward_web_movement_ids", "lasting_state.guarded_web_section_ids"),
                        _each(
                            "_ward_web_movement_ids",
                            "_ward_web_movement_id",
                            [_push("_ground_route_ids", "_ward_web_movement_id")],
                        ),
                    ],
                ),
            )
        changed[name] = node
    if not changed:
        raise ValueError("Guarded Web lost the actual ground-route movement owner.")
    return changed


def bind_runtime_consumers(text):
    """Join actual segment entry/cost/turn-start after final Environment and Travel owners."""
    tools = _load_runtime(text)["tools"]
    changed = _movement_tools()
    entry = deepcopy(tools["resolve_srd_environment_hazard_entry"])
    if "resolve_srd_guards_wards_web_entry" not in str(entry["effects"]):
        final = next(
            i
            for i, e in enumerate(entry["effects"])
            if e.get("condition") == "len(_hazard_entry_events) > 0"
        )
        entry["effects"].insert(
            final,
            _when(
                "lasting_state.has_guarded_web_sections == true",
                [
                    _call(
                        "resolve_srd_guards_wards_web_entry",
                        {"actor_state": "{_actor_state_id}", "destination": "{destination}"},
                        ("_ward_web_entry_t", "_ward_web_entry_events"),
                    ),
                    _when(
                        "len(_ward_web_entry_events) > 0 and _ward_web_entry_t < "
                        "_hazard_entry_t - 0.000000000001",
                        [
                            _set("_hazard_entry_t", "_ward_web_entry_t"),
                            _set("_hazard_entry_events", "_ward_web_entry_events"),
                        ],
                        [
                            _when(
                                "len(_ward_web_entry_events) > 0 and abs(_ward_web_entry_t "
                                "- _hazard_entry_t) < 0.000000000001",
                                [
                                    _each(
                                        "_ward_web_entry_events",
                                        "_ward_web_event",
                                        [_push("_hazard_entry_events", "_ward_web_event")],
                                    ),
                                ],
                            )
                        ],
                    ),
                ],
            ),
        )
    changed["resolve_srd_environment_hazard_entry"] = entry
    dispatch = deepcopy(tools["resolve_srd_environment_hazard_event"])
    if "resolve_srd_guards_wards_web_event" not in str(dispatch["effects"]):
        # Keep the original complete Environment dispatch behind the actual source join.
        dispatch["effects"] = [
            _when(
                f"lasting_state.{POOL} != null and area_id in lasting_state.{POOL}",
                [
                    _call(
                        "resolve_srd_guards_wards_web_event",
                        {"target_state": "{_target_state_id}", "area_id": "{area_id}"},
                    ),
                ],
                dispatch["effects"],
            )
        ]
    changed["resolve_srd_environment_hazard_event"] = dispatch
    changed.update(_bind_ground_routes(tools))
    terrain = deepcopy(tools["resolve_srd_environment_sphere_terrain_intervals"])
    if "resolve_srd_guards_wards_web_intervals" not in str(terrain["effects"]):
        terrain["effects"].append(
            _when(
                "lasting_state.has_guarded_web_sections == true",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{actor_subject}"},
                        ("_info_scene_entity",),
                    ),
                    _call(
                        "resolve_srd_guards_wards_web_intervals",
                        {
                            "actor_state": "{_info_scene_entity.metadata.participant_ref}",
                            "start": "{start}",
                            "end": "{end}",
                        },
                        ("_ward_web_route_intervals",),
                    ),
                    _each(
                        "_ward_web_route_intervals",
                        "_ward_web_route",
                        [_push("_sphere_route_intervals", "_ward_web_route")],
                    ),
                ],
            )
        )
    changed["resolve_srd_environment_sphere_terrain_intervals"] = terrain
    cost = deepcopy(tools["srd_information_movement_cost"])
    for effects in _effect_lists(cost["effects"]):
        for effect in effects:
            if (
                any(
                    e.get("call") == "resolve_srd_environment_sphere_terrain_intervals"
                    for e in effect.get("on_true", [])
                )
                and "has_guarded_web_sections" not in effect["condition"]
            ):
                effect["condition"] = (
                    "((lasting_state.has_spell_environments == true and "
                    "ignore_ground_environments != true) or "
                    "lasting_state.has_guarded_web_sections == true) and len(actor_subject) > 0"
                )
    changed["srd_information_movement_cost"] = cost
    starting = deepcopy(tools["resolve_srd_environment_hazard_start_turn"])
    if "resolve_srd_guards_wards_web_start" not in str(starting["effects"]):
        starting["effects"].append(
            _when(
                "lasting_state.has_guarded_web_sections == true",
                [
                    _call(
                        "resolve_srd_guards_wards_web_start", {"target_state": "{_target_state_id}"}
                    ),
                ],
            )
        )
    changed["resolve_srd_environment_hazard_start_turn"] = starting
    movement = deepcopy(tools["move_srd_scene_creature_environment_segment"])
    if "refresh_srd_guards_wards_web_target" not in str(movement["effects"]):
        movement["effects"].append(
            _when(
                "actor_state.has_guarded_web_restraints == true",
                [
                    _call(
                        "refresh_srd_guards_wards_web_target", {"target_state": "{_actor_state_id}"}
                    ),
                ],
            )
        )
    changed["move_srd_scene_creature_environment_segment"] = movement
    relocate = deepcopy(tools["move_srd_travel_subject"])
    if "observe_srd_guards_wards_web_relocation" not in str(relocate["effects"]):
        relocate["effects"].append(
            _when(
                "lasting_state.has_guarded_web_sections == true",
                [
                    _call("observe_srd_guards_wards_web_relocation", {"subject": "{subject}"}),
                ],
            )
        )
    changed["move_srd_travel_subject"] = relocate
    text = _replace_tools(text, changed)
    return _replace_tools(text, {k: v for k, v in changed.items() if k not in tools})
