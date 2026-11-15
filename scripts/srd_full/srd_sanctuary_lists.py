"""Sanctuary list decisions use the existing saved spell, cache and ray owners."""

from copy import deepcopy

if __package__:
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _require,
        _set,
        _tool,
    )
    from .srd_timed_actor_defenses import (
        _when as _clause,
    )
    from .srd_timed_senses import _loop, _replace_tools
else:
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _require,
        _set,
        _tool,
    )
    from srd_timed_actor_defenses import (
        _when as _clause,
    )
    from srd_timed_senses import _loop, _replace_tools


EVENT = "lasting_state.pending_srd_actions.{pending_action_id}"
LISTS = (
    "caster_unseen_attack_targets",
    "attack_targets_unable_to_see_caster",
    "attack_targets_within_5_feet",
    "half_cover_targets",
    "three_quarters_cover_targets",
)


def _when(condition, yes, no=None):
    result = _clause(condition, yes)
    if no is not None:
        result["on_false"] = no
    return result


def _walk(nodes):
    for node in nodes:
        yield node
        for branch in ("on_true", "on_false"):
            yield from _walk(node.get(branch, []))
        if "for_each" in node:
            yield from _walk(node["for_each"]["do"])


def _push(array, value):
    return {"list_push": {"list": array, "value": value}}


def _pending(field, value):
    return _set(f"{EVENT}.{field}", value)


def _list_targeting():
    """A damaging spell saves per distinct recipient; attack rolls save per ordinal.

    Missile allocations to one creature share its saved spell qualification (p159
    interpretation). Each ray has its own trigger; a lost ray does not lose others.
    """
    return _tool(
        "Continue the saved spell's original target list until a Sanctuary decision is needed.",
        [
            _entity("caster_state"),
            {"name": "pending_action_id", "type": "string", "required": True},
        ],
        [
            _set("_list_cast", "state.pending_srd_actions[pending_action_id]"),
            _require(
                "_list_cast.kind == 'spell_cast' and _list_cast.status == 'open' and "
                "_list_cast.actor_state_id == _caster_state_id and "
                "state.pending_srd_action_stack[-1] == pending_action_id",
                "Targeting must use the actual current saved spell.",
            ),
            _require(
                "_list_cast.stage != 'awaiting_sanctuary_choice' and "
                "_list_cast.stage != 'sanctuary_retarget_validation'",
                "Resolve the existing Sanctuary choice first.",
            ),
            _set("_list_targets", "_list_cast.parameters.projectile_targets"),
            _when(
                "_list_cast.sanctuary_list_kind == 'attack'",
                [_set("_list_targets", "_list_cast.parameters.attack_targets")],
            ),
            _set("_list_cursor", "_list_cast.sanctuary_cursor"),
            _set("_list_approved", "_list_cast.sanctuary_approved"),
            _set("_list_index", 0),
            _set("_list_blocked", False),
            _loop(
                "_list_targets",
                "_list_target",
                [
                    _when(
                        "_list_blocked != true and _list_index >= _list_cursor",
                        [
                            _pending("sanctuary_cursor", "_list_index"),
                            _pending("target_state_id", "_list_target"),
                            _call(
                                "continue_srd_spell_targeting",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "target_state": "{_list_target}",
                                    "pending_action_id": "{pending_action_id}",
                                },
                                ["_sanctuary_target_allowed", "_sanctuary_choice_required"],
                            ),
                            _when(
                                "_sanctuary_target_allowed == true",
                                [
                                    _push(
                                        "_list_approved",
                                        {"target": "_list_target", "ordinal": "_list_index"},
                                    ),
                                    _pending("sanctuary_approved", "_list_approved"),
                                    _pending("sanctuary_cursor", "_list_index + 1"),
                                    _set("_list_cursor", "_list_index + 1"),
                                ],
                                [_set("_list_blocked", True)],
                            ),
                        ],
                    ),
                    _set("_list_index", "_list_index + 1"),
                ],
            ),
            _when(
                "_list_blocked != true",
                [_pending("spell_targeting_checked", True), _pending("stage", "'reaction'")],
            ),
        ],
    )


def _validation():
    """Reject malformed list intent before the begin owner reserves an Action."""
    effects = [
        _set("_sanctuary_list_kind", "''"),
        _when(
            "_spell_definition.family == 'automatic_force_projectiles' and "
            "projectile_targets != null",
            [
                _set("_sanctuary_list_kind", "'projectile'"),
                _require(
                    "len(projectile_targets) == _spell_definition.base_projectiles + "
                    "(_slot_level - _spell_definition.base_level) * "
                    "_spell_definition.projectiles_per_slot_level and "
                    "_target_id in projectile_targets",
                    "Declare every projectile and include its primary target.",
                ),
                _require(
                    "targets_max_distance != null and "
                    "targets_max_distance <= _spell_definition.range_feet and "
                    "all_targets_have_clear_path == true and "
                    "(_spell_definition.requires_sight != true or "
                    "caster_can_see_all_targets == true)",
                    "Every projectile target needs its actual range, sight and clear path.",
                ),
            ],
        ),
        _when(
            "_spell_definition.family == 'multi_spell_attack_damage'",
            [
                _set("_sanctuary_list_kind", "'attack'"),
                _require(
                    "attack_targets != null and target == null and target_distance == null and "
                    "caster_can_see_target == null and target_can_see_caster != false and "
                    "(cover == null or cover == 'none') and advantage != true "
                    "and disadvantage != true",
                    "Independent spell attacks require list facts, not scalar targeting facts.",
                ),
                _require(
                    "targets_max_distance != null and "
                    "targets_max_distance <= _spell_definition.range_feet and "
                    "all_attack_targets_have_clear_path == true",
                    "Every ray needs its actual range and clear path.",
                ),
                _set("_sanctuary_expected_rays", "_spell_definition.base_attacks"),
                _when(
                    "_spell_definition.scaling_mode == 'slot_level'",
                    [
                        _set(
                            "_sanctuary_expected_rays",
                            "_spell_definition.base_attacks + "
                            "(_slot_level - _spell_definition.base_level) * "
                            "_spell_definition.attacks_per_slot_level",
                        )
                    ],
                ),
            ],
        ),
    ]
    # The established multi-attack family also admits caster-level cantrips.
    for level in range(1, 4):
        effects.append(
            _when(
                "_sanctuary_list_kind == 'attack' and "
                "_spell_definition.scaling_mode == 'caster_level' and "
                f"_casting_profile.scaling_level >= _spell_definition.upgrade_level_{level}",
                [
                    _set(
                        "_sanctuary_expected_rays",
                        "_sanctuary_expected_rays + _spell_definition.cantrip_upgrade_attacks",
                    )
                ],
            )
        )
    effects.append(
        _when(
            "_sanctuary_list_kind == 'attack'",
            [
                _require(
                    "len(attack_targets) == _sanctuary_expected_rays",
                    "Declare the actual source-derived number of rays.",
                ),
                *[
                    _loop(
                        field,
                        "_sanctuary_subset",
                        [
                            _call(
                                "validate_spell_multi_attack_subset_entry",
                                {
                                    "entry_id": "{_sanctuary_subset}",
                                    "attack_targets": "{attack_targets}",
                                },
                            )
                        ],
                    )
                    for field in LISTS
                ],
                _loop(
                    "half_cover_targets",
                    "_sanctuary_subset",
                    [
                        _require(
                            "_sanctuary_subset not in three_quarters_cover_targets",
                            "Choose one actual cover category per target.",
                        )
                    ],
                ),
            ],
        )
    )
    return effects


def _begin(tool):
    if any(n.get("set", {}).get("field") == "_sanctuary_list_kind" for n in tool["effects"]):
        return
    budget = next(
        i for i, n in enumerate(tool["effects"]) if n.get("call") == "spend_spell_casting_budget"
    )
    # Normalize optional subsets exactly as the existing actual casting owner does.
    normalized = []
    for field in LISTS:
        normalized.extend(
            [
                _set(f"_sanctuary_{field}", []),
                _when(f"{field} != null", [_set(f"_sanctuary_{field}", field)]),
            ]
        )
    checks = _validation()
    for n in _walk(checks):
        if "for_each" in n and n["for_each"]["collection"] in LISTS:
            n["for_each"]["collection"] = "_sanctuary_" + n["for_each"]["collection"]
        if (
            n.get("require", {}).get("condition")
            == "_sanctuary_subset not in three_quarters_cover_targets"
        ):
            n["require"]["condition"] = (
                "_sanctuary_subset not in _sanctuary_three_quarters_cover_targets"
            )
    tool["effects"][budget:budget] = [*normalized, *checks]
    scalar = next(
        n for n in tool["effects"] if "projectile_targets == null" in n.get("condition", "")
    )
    wisdom = next(n for n in scalar["on_true"] if "caster != null" in n.get("condition", ""))
    list_wisdom = deepcopy(wisdom)
    list_wisdom["condition"] = "caster != null"
    tool["effects"].append(
        _when(
            "_sanctuary_list_kind != ''",
            [
                _set(
                    "lasting_state.pending_srd_actions.{_pending_action_id}.sanctuary_list_kind",
                    "_sanctuary_list_kind",
                ),
                _set("lasting_state.pending_srd_actions.{_pending_action_id}.sanctuary_cursor", 0),
                _set(
                    "lasting_state.pending_srd_actions.{_pending_action_id}.sanctuary_approved", []
                ),
                _set(
                    "lasting_state.pending_srd_actions.{_pending_action_id}.sanctuary_lost_rays", []
                ),
                list_wisdom,
                _call(
                    "continue_srd_spell_list_targeting",
                    {
                        "caster_state": "{_caster_state_id}",
                        "pending_action_id": "{_pending_action_id}",
                    },
                ),
            ],
        )
    )


def _cache(tool):
    if any(p["name"] == "operation_index" for p in tool["parameters"]):
        return
    tool["parameters"].append(
        {
            "name": "operation_index",
            "type": "integer",
            "required": False,
            "default": -1,
            "model_hidden": True,
        }
    )
    tool["effects"].insert(
        2,
        _require(
            "operation_index == -1 or "
            "(_sanctuary_pending.sanctuary_list_kind == 'attack' and "
            "operation_index == _sanctuary_pending.sanctuary_cursor and "
            "_sanctuary_pending.parameters.attack_targets[operation_index] == _target_state_id)",
            "A ray targeting save requires its actual saved ordinal and target.",
        ),
    )
    cache = next(
        n
        for n in _walk(tool["effects"])
        if "_sanctuary_cached_save.target_state_id" in n.get("condition", "")
    )
    cache["condition"] += " and _sanctuary_cached_save.cast_id == _sanctuary_source_cast_id"
    cache["condition"] += " and (_sanctuary_cached_save.operation_index == operation_index or "
    cache["condition"] += (
        "(_sanctuary_cached_save.operation_index == null and operation_index == -1))"
    )
    source = next(
        n
        for n in _walk(tool["effects"])
        if n.get("condition") == "_actor_modifier_sanctuary_dc > 0"
    )
    source["on_true"][:0] = [
        _set("_sanctuary_source_cast_id", None),
        _when(
            "_actor_modifier_sanctuary_source.cast_id != null and "
            "_actor_modifier_sanctuary_source.cast_id != ''",
            [_set("_sanctuary_source_cast_id", "_actor_modifier_sanctuary_source.cast_id")],
        ),
    ]
    saved = next(
        n["set"]["value"]
        for n in _walk(tool["effects"])
        if n.get("set", {}).get("field") == "_sanctuary_save"
        and isinstance(n["set"].get("value"), dict)
    )
    saved.update(cast_id="_sanctuary_source_cast_id", operation_index="operation_index")


def _continue(tool):
    if any(n.get("set", {}).get("field") == "_sanctuary_operation_index" for n in tool["effects"]):
        return
    call = next(n for n in tool["effects"] if n.get("call") == "check_srd_sanctuary_targeting")
    i = tool["effects"].index(call)
    tool["effects"][i:i] = [
        _set("_sanctuary_operation_index", -1),
        _when(
            "_targeting_cast.sanctuary_list_kind == 'attack'",
            [_set("_sanctuary_operation_index", "_targeting_cast.sanctuary_cursor")],
        ),
    ]
    call["params"]["operation_index"] = "{_sanctuary_operation_index}"
    success = tool["effects"][-1]
    success["condition"] += " and _targeting_cast.sanctuary_list_kind == null"


def _decision(tool):
    if any(
        "_sanctuary_decision_pending.sanctuary_list_kind" in n.get("condition", "")
        for n in _walk(tool["effects"])
    ):
        return
    choice = next(
        n for n in tool["effects"] if n.get("condition") == "decision == 'lose_operation'"
    )
    ordinary = deepcopy(choice["on_true"])
    choice["on_true"] = [
        _when(
            "_sanctuary_decision_pending.sanctuary_list_kind == 'attack'",
            [
                _set(
                    "_sanctuary_decision_lost_rays",
                    "_sanctuary_decision_pending.sanctuary_lost_rays",
                ),
                _push(
                    "_sanctuary_decision_lost_rays",
                    "_sanctuary_decision_pending.sanctuary_cursor",
                ),
                _pending("sanctuary_lost_rays", "_sanctuary_decision_lost_rays"),
                _pending("sanctuary_cursor", "_sanctuary_decision_pending.sanctuary_cursor + 1"),
                _pending("stage", "'targeting'"),
                _call(
                    "continue_srd_spell_list_targeting",
                    {
                        "caster_state": "{_sanctuary_decision_pending.actor_state_id}",
                        "pending_action_id": "{pending_action_id}",
                    },
                ),
            ],
            ordinary,
        )
    ]


def _resume(tool, cast):
    """Replacement facts use existing GM projections; an admitted scene takes precedence."""
    if any(p["name"] == "replacement_target_distance" for p in tool["parameters"]):
        return
    tool["description"] = (
        "Resume the saved spell with validated replacement facts and retained saves."
    )
    params = {p["name"]: p for p in cast["parameters"]}
    for name in ("target_distance", "caster_can_see_target", "target_can_see_caster", "cover"):
        p = deepcopy(params[name])
        p.update(name="replacement_" + name, required=False, default=None)
        tool["parameters"].append(p)
    tool["parameters"].append(
        {
            "name": "replacement_has_clear_path",
            "type": "boolean",
            "required": False,
            "default": None,
        }
    )
    nodes = tool["effects"]
    relation = next(n for n in nodes if n.get("call") == "resolve_srd_scene_relation")
    at = nodes.index(relation)
    nodes[at : at + 1] = [
        _set("_scene_distance", "replacement_target_distance"),
        _set("_scene_can_see", "replacement_caster_can_see_target"),
        _set("_scene_line_of_sight", "replacement_has_clear_path"),
        _set("_scene_same_plane", True),
        _when("lasting_state.spatial_scene != null", [relation]),
        _require(
            "_scene_distance != null and _scene_line_of_sight != null",
            "Supply the replacement's actual distance and clear-path facts.",
        ),
        _when(
            "_retarget_cast.sanctuary_list_kind == 'attack'",
            [
                _require(
                    "replacement_target_can_see_caster != null and _scene_can_see != null and "
                    "replacement_cover != null and replacement_cover != 'total'",
                    "Supply the replacement ray target's actual sight and cover facts.",
                )
            ],
        ),
    ]
    at = next(
        i
        for i, n in enumerate(nodes)
        if n.get("set", {}).get("field") == f"{EVENT}.parameters.target"
    )
    ordinary = nodes[at:]
    # Reuse the original scalar branch when this is not a list spell.
    nodes[at:] = [
        _when(
            "_retarget_cast.sanctuary_list_kind != null",
            [
                _set("_retarget_array", "_retarget_cast.parameters.projectile_targets"),
                _when(
                    "_retarget_cast.sanctuary_list_kind == 'attack'",
                    [_set("_retarget_array", "_retarget_cast.parameters.attack_targets")],
                ),
                _set("_retarget_changed", []),
                _set("_retarget_index", 0),
                _loop(
                    "_retarget_array",
                    "_retarget_item",
                    [
                        _set("_retarget_value", "_retarget_item"),
                        _when(
                            "(_retarget_cast.sanctuary_list_kind == 'projectile' and "
                            "_retarget_item == "
                            "_retarget_cast.sanctuary_rejected_target_state_id) or "
                            "(_retarget_cast.sanctuary_list_kind == 'attack' and "
                            "_retarget_index == _retarget_cast.sanctuary_cursor)",
                            [_set("_retarget_value", "_retarget_id")],
                        ),
                        _push("_retarget_changed", "_retarget_value"),
                        _set("_retarget_index", "_retarget_index + 1"),
                    ],
                ),
                _when(
                    "_retarget_cast.sanctuary_list_kind == 'projectile'",
                    [
                        _pending("parameters.projectile_targets", "_retarget_changed"),
                        _when(
                            "_retarget_cast.parameters.target == "
                            "_retarget_cast.sanctuary_rejected_target_state_id",
                            [
                                _pending("parameters.target", "_retarget_id"),
                                _pending("parameters.target_distance", "_scene_distance"),
                                _pending("parameters.caster_can_see_target", "_scene_can_see"),
                            ],
                        ),
                    ],
                    [
                        _pending("parameters.attack_targets", "_retarget_changed"),
                        *_retarget_subsets(),
                    ],
                ),
                _pending(
                    "parameters.targets_max_distance",
                    "max(_retarget_cast.parameters.targets_max_distance, _scene_distance)",
                ),
                _pending("stage", "'targeting'"),
                _call(
                    "continue_srd_spell_list_targeting",
                    {
                        "caster_state": "{_retarget_cast.actor_state_id}",
                        "pending_action_id": "{pending_action_id}",
                    },
                ),
            ],
            ordinary,
        )
    ]


def _retarget_subsets():
    effects = []
    facts = (
        "_scene_can_see == false",
        "replacement_target_can_see_caster == false",
        "_scene_distance <= 5",
        "replacement_cover == 'half'",
        "replacement_cover == 'three_quarters'",
    )
    for field, fact in zip(LISTS, facts, strict=True):
        effects.extend(
            [
                _set("_retarget_subset", []),
                _set("_retarget_old_subset", []),
                _when(
                    f"_retarget_cast.parameters.{field} != null",
                    [_set("_retarget_old_subset", f"_retarget_cast.parameters.{field}")],
                ),
                _loop(
                    "_retarget_old_subset",
                    "_retarget_member",
                    [
                        _when(
                            "_retarget_member != _retarget_id and "
                            "_retarget_member in _retarget_changed",
                            [_push("_retarget_subset", "_retarget_member")],
                        )
                    ],
                ),
                _when(fact, [_push("_retarget_subset", "_retarget_id")]),
                _pending("parameters." + field, None),
                _when(
                    "len(_retarget_subset) > 0",
                    [_pending("parameters." + field, "_retarget_subset")],
                ),
            ]
        )
    return effects


def _damage(tools):
    """Lost ordinals never enter the actual attack owner; other ray effects remain intact."""
    ray = tools["cast_spell_multi_attack_damage"]
    if not any(p["name"] == "lasting_state" for p in ray["parameters"]):
        ray["parameters"].append(
            dict(_entity("lasting_state"), required=False, default="state", model_hidden=True)
        )
    if not any(n.get("set", {}).get("field") == "_sanctuary_ray_index" for n in ray["effects"]):
        loop = next(
            n["for_each"]
            for n in ray["effects"]
            if "for_each" in n
            and any(
                c.get("call") == "resolve_spell_multi_attack_target" for c in n["for_each"]["do"]
            )
        )
        at = next(i for i, n in enumerate(ray["effects"]) if n.get("for_each") is loop)
        ray["effects"][at:at] = [
            _set("_sanctuary_ray_index", 0),
            _set("_sanctuary_lost_rays", []),
            _when(
                "caster_state.pending_srd_completion_id != null",
                [
                    _set(
                        "_sanctuary_ray_cast",
                        "lasting_state.pending_srd_actions[caster_state.pending_srd_completion_id]",
                    ),
                    _when(
                        "_sanctuary_ray_cast.sanctuary_list_kind == 'attack'",
                        [_set("_sanctuary_lost_rays", "_sanctuary_ray_cast.sanctuary_lost_rays")],
                    ),
                ],
            ),
        ]
        original = loop["do"]
        loop["do"] = [
            _set("caster_state.pending_srd_sanctuary_ray_index", "_sanctuary_ray_index"),
            _when(
                "_sanctuary_ray_index not in _sanctuary_lost_rays",
                original,
                [
                    _push(
                        "_spell_summary_rows",
                        {
                            "label": "'Sanctuary'",
                            "value": "'Ray lost after failed Sanctuary save.'",
                        },
                    )
                ],
            ),
            _set("caster_state.pending_srd_sanctuary_ray_index", None),
            _set("_sanctuary_ray_index", "_sanctuary_ray_index + 1"),
        ]
    target = tools["resolve_spell_multi_attack_target"]
    if not any(n.get("call") == "check_srd_sanctuary_target" for n in _walk(target["effects"])):
        target["effects"].insert(
            0,
            _call(
                "check_srd_sanctuary_target",
                {
                    "actor_state": "{_caster_state_id}",
                    "target_state": "{_target_id}",
                    "lasting_state": "state",
                },
            ),
        )
    atomic = tools["check_srd_sanctuary_target"]
    owner = next(
        n
        for n in atomic["effects"]
        if n.get("condition") == "actor_state.pending_srd_completion_id != null"
    )
    if not any(
        n.get("condition") == "_sanctuary_bypass_event.sanctuary_list_kind != null and "
        "_sanctuary_bypass_event.spell_targeting_checked == true"
        for n in owner["on_true"]
    ):
        owner["on_true"].append(
            _when(
                "_sanctuary_bypass_event.sanctuary_list_kind != null and "
                "_sanctuary_bypass_event.spell_targeting_checked == true",
                [
                    _set("_sanctuary_bypass", False),
                    _set("_sanctuary_approved", "_sanctuary_bypass_event.sanctuary_approved"),
                    _loop(
                        "_sanctuary_approved",
                        "_sanctuary_approval",
                        [
                            _when(
                                "_sanctuary_approval.target == _target_state_id and "
                                "(_sanctuary_bypass_event.sanctuary_list_kind == 'projectile' or "
                                "_sanctuary_approval.ordinal == "
                                "actor_state.pending_srd_sanctuary_ray_index)",
                                [_set("_sanctuary_bypass", True)],
                            )
                        ],
                    ),
                ],
            )
        )


def bind_runtime_consumers(text):
    """Run after the final reaction targeting owners; preserve completion and unrelated effects."""
    runtime = _load_runtime(text)
    tools = runtime["tools"]
    names = (
        "begin_srd_spell_action",
        "check_srd_sanctuary_targeting",
        "continue_srd_spell_targeting",
        "decide_srd_sanctuary_target_node",
        "resume_srd_sanctuary_spell",
        "cast_spell_multi_attack_damage",
        "resolve_spell_multi_attack_target",
        "check_srd_sanctuary_target",
    )
    changed = {name: deepcopy(tools[name]) for name in names}
    _begin(changed["begin_srd_spell_action"])
    _cache(changed["check_srd_sanctuary_targeting"])
    _continue(changed["continue_srd_spell_targeting"])
    _decision(changed["decide_srd_sanctuary_target_node"])
    _resume(changed["resume_srd_sanctuary_spell"], tools["cast_spell"])
    _damage(changed)
    changed["continue_srd_spell_list_targeting"] = _list_targeting()
    result = _replace_tools(text, changed)
    # Canonicalize the new leaf's insertion whitespace on the first emission too.
    return _replace_tools(result, {"continue_srd_spell_list_targeting": _list_targeting()})
