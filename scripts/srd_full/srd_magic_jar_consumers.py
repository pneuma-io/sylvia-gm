"""Magic Jar's physical aliases join existing scene, roll, action and damage owners."""

from copy import deepcopy

import yaml

if __package__:
    from .srd_control_behaviors import (
        _call,
        _entity,
        _lasting,
        _param,
        _require,
        _set,
        _tool,
        _when,
    )
    from .srd_timed_senses import _replace_tools
else:
    from srd_control_behaviors import _call, _entity, _lasting, _param, _require, _set, _tool, _when
    from srd_timed_senses import _replace_tools


def _walk(value, visit):
    if isinstance(value, dict):
        visit(value)
        for child in value.values():
            _walk(child, visit)
    elif isinstance(value, list):
        for child in value:
            _walk(child, visit)


def _strip_conditions(effects, conditions):
    effects[:] = [n for n in effects if n.get("condition") not in conditions]
    for n in effects:
        for key in ("on_true", "on_false", "do"):
            if isinstance(n.get(key), list):
                _strip_conditions(n[key], conditions)


def _precondition(tool, condition, error):
    conditions = tool.setdefault("pre_conditions", [])
    if not any(p["condition"] == condition for p in conditions):
        conditions.append({"condition": condition, "error": error})


def _damage_exports(tools):
    """Forward the actual damage owner's context, including existing fall/ward consumers."""
    names = {
        "_effective_damage",
        "_hp_lost",
        "_is_alive_after",
        "_temp_hp_absorbed",
        "_hp_after",
        "_temp_hp_after",
        "_conditions_after",
        "_death_failures_after",
        "_stable_after",
        "_stable_recovery_hours_remaining_after",
        "_concentration_save_dc",
        "_concentration_save_outcome",
        "_concentration_save_roll",
    }

    def collect(node):
        if node.get("call") == "apply_srd_damage":
            names.update(node.get("export_context", {}).values())

    _walk(tools, collect)
    return tuple(sorted(names))


def _forward_params(parameters):
    return {
        p["name"]: "{"
        + ("_" + p["name"] + "_id" if p.get("resolve") in {"entity", "participant"} else p["name"])
        + "}"
        for p in parameters
    }


def _sync_host_tool():
    return _tool(
        "Project only the actual occupied body's current HP and Hit Dice onto its host record.",
        [_entity("caster_state"), _entity("host_state"), _lasting()],
        [
            _require(
                "caster_state.magic_jar_role == 'possessed' and "
                "host_state.magic_jar_role == 'host_soul' and "
                "host_state.magic_jar_caster_id == _caster_state_id and "
                "host_state.magic_jar_source_id == caster_state.magic_jar_source_id",
                "The body projection requires the exact occupied host source.",
            ),
            _set("host_state.hp", "caster_state.hp"),
            _set("host_state.max_hp", "caster_state.max_hp"),
            _set("host_state.resources.hit_die", "caster_state.resources.hit_die"),
            _set("host_state.resource_maxima.hit_die", "caster_state.resource_maxima.hit_die"),
            _set("host_state.hit_die_size", "caster_state.hit_die_size"),
            _set("host_state.hit_dice_pools", None),
            _when(
                "caster_state.hit_dice_pools != null",
                [_set("host_state.hit_dice_pools", "caster_state.hit_dice_pools")],
            ),
        ],
    )


def _native_hit_dice_creation(tools, changed):
    """Admit the original printed Hit Dice only at genuine NPC creation, never from HP or CR."""
    tool = deepcopy(tools["prepare_srd_creature"])
    if any(p["name"] == "native_hit_dice_count" for p in tool["parameters"]):
        return
    tool["parameters"].extend(
        [
            _param(
                "native_hit_dice_count",
                "integer",
                required=False,
                default=None,
                minimum=1,
                maximum=1000,
            ),
            _param(
                "native_hit_die_size",
                "integer",
                required=False,
                default=None,
                minimum=4,
                maximum=20,
            ),
        ]
    )
    tool["effects"].insert(
        0,
        _require(
            "(native_hit_dice_count == null and native_hit_die_size == null) or "
            "(native_hit_dice_count != null and (native_hit_die_size == 4 or "
            "native_hit_die_size == 6 or native_hit_die_size == 8 or "
            "native_hit_die_size == 10 or native_hit_die_size == 12 or native_hit_die_size == 20))",
            "Supply both original printed native Hit Dice facts, or leave both unknown.",
        ),
    )
    tool["effects"].append(
        _when(
            "native_hit_dice_count != null",
            [
                _set(
                    "creature_id.native_hit_dice",
                    {
                        "count": "native_hit_dice_count",
                        "size": "native_hit_die_size",
                        "source": "source",
                    },
                ),
                _set("creature_id.hit_die_size", "native_hit_die_size"),
                _set("creature_id.hit_dice_pools", None),
                _set("creature_id.resources.hit_die", "native_hit_dice_count"),
                _when(
                    "creature_id.resource_maxima == null", [_set("creature_id.resource_maxima", {})]
                ),
                _set("creature_id.resource_maxima.hit_die", "native_hit_dice_count"),
            ],
        )
    )
    changed["prepare_srd_creature"] = tool


def bind_consumers(text):
    """Keep ordinary paths inline; only an authenticated active Jar uses physical forwarding."""
    runtime = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    tools = runtime["tools"]
    changed = {"sync_srd_magic_jar_host_body": _sync_host_tool()}
    _native_hit_dice_creation(tools, changed)
    # These actual recovery owners change the occupied body's HP or Hit Dice. Keep the retained
    # host record synchronized without changing the abandoned body's saved physical resources.
    for name in (
        "take_srd_short_rest_hit_die",
        "restore_srd_resource",
        "spend_srd_resource",
        "complete_srd_rest",
    ):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        entity = (
            "target" if name in {"restore_srd_resource", "spend_srd_resource"} else "target_state"
        )
        condition = entity + ".magic_jar_role == 'possessed'"
        if name in {"restore_srd_resource", "spend_srd_resource"}:
            condition += " and resource == 'hit_die'"
        _strip_conditions(tool["effects"], {condition})
        if name == "take_srd_short_rest_hit_die":
            tool["effects"].insert(
                0,
                _when(
                    condition,
                    [
                        _call(
                            "read_srd_magic_jar_source",
                            {"caster_state": "{_target_state_id}"},
                            ("_jar_source",),
                        ),
                        _require(
                            "_jar_source.mode == 'possessed'",
                            "Hit Dice require this exact currently occupied body.",
                        ),
                    ],
                ),
            )

            def native_die(node):
                require = node.get("require", {})
                if require.get("condition", "").startswith("_rest_die == 4"):
                    require["condition"] = (
                        "_rest_die == 4 or _rest_die == 6 or _rest_die == 8 or "
                        "_rest_die == 10 or _rest_die == 12 or (_rest_die == 20 and "
                        "target_state.magic_jar_role == 'possessed' and "
                        "_jar_source.host_profile.native_hit_dice != null and "
                        "_jar_source.host_profile.native_hit_dice.size == 20)"
                    )

            _walk(tool["effects"], native_die)
        tool["effects"].append(
            _when(
                condition,
                [
                    _call(
                        "read_srd_magic_jar_source",
                        {"caster_state": "{_" + entity + "_id}"},
                        ("_jar_source",),
                    ),
                    _call(
                        "sync_srd_magic_jar_host_body",
                        {
                            "caster_state": "{_" + entity + "_id}",
                            "host_state": "{_jar_source.host}",
                        },
                    ),
                ],
            )
        )
        changed[name] = tool
    physical_modifier = (
        "actor_state.magic_jar_role == 'possessed' and "
        "(ability == 'strength' or ability == 'dexterity' or ability == 'constitution')"
    )
    scene = deepcopy(tools["srd_information_scene_entity"])
    original_subject = "_info_scene_item.active == true and (_info_scene_item.id == subject or "
    _strip_conditions(scene["effects"], {"lasting_state.has_magic_jar_sources == true"})

    def restore_scene(node):
        if "_magic_jar_scene_id" in node.get("condition", ""):
            node["condition"] = (
                original_subject + "_info_scene_item.metadata.participant_ref == subject)"
            )

    _walk(scene["effects"], restore_scene)
    if original_subject not in str(scene):
        raise ValueError("Magic Jar scene join requires the actual ordinary scene lookup owner")
    scene["effects"].insert(
        0,
        _when(
            "lasting_state.has_magic_jar_sources == true",
            [
                _call(
                    "resolve_srd_magic_jar_scene_subject",
                    {"subject": "{subject}"},
                    ("_magic_jar_scene_id",),
                ),
            ],
        ),
    )

    def scene_match(node):
        condition = node.get("condition", "")
        if condition.startswith(original_subject):
            node["condition"] = (
                "_info_scene_item.active == true and "
                "((lasting_state.has_magic_jar_sources != true and "
                "(_info_scene_item.id == subject or "
                "_info_scene_item.metadata.participant_ref == subject)) "
                "or (lasting_state.has_magic_jar_sources == true and "
                "(_info_scene_item.id == _magic_jar_scene_id or "
                "_info_scene_item.metadata.participant_ref == _magic_jar_scene_id)))"
            )

    _walk(scene["effects"], scene_match)
    changed["srd_information_scene_entity"] = scene

    d20 = deepcopy(tools["d20_test"])
    _strip_conditions(d20["effects"], {physical_modifier})
    for effect in d20["effects"]:
        if effect.get("condition") == "actor != null":
            body = effect["on_true"]
            at = next(
                i
                for i, n in enumerate(body)
                if n.get("set", {}).get("field") == "_proficiency_bonus"
            )
            body.insert(
                at,
                _when(
                    physical_modifier,
                    [
                        _set(
                            "_ability_modifier",
                            "(actor_state.magic_jar_physical_scores[ability] - 10) // 2",
                        )
                    ],
                ),
            )
            break
    else:
        raise ValueError("Magic Jar needs the actual participant physical-score D20 owner")
    changed["d20_test"] = d20
    equipment = deepcopy(tools["resolve_srd_equipment"])
    _strip_conditions(equipment["effects"], {"actor_state.magic_jar_role == 'possessed'"})
    at = next(
        i
        for i, n in enumerate(equipment["effects"])
        if n.get("condition") == "_equipment_form_statistics == true"
    )
    equipment["effects"].insert(
        at + 1,
        _when(
            "actor_state.magic_jar_role == 'possessed'",
            [
                _set("_equipment_ability_scores", "actor_state.ability_scores"),
            ],
        ),
    )
    changed["resolve_srd_equipment"] = equipment
    facts = deepcopy(tools["derive_srd_control_character_facts"])
    passive_condition = (
        "target_state.magic_jar_role == 'possessed' and target_state.magic_jar_host_passive != null"
    )
    _strip_conditions(facts["effects"], {passive_condition})
    facts["effects"].append(
        _when(
            passive_condition,
            [
                _set("target_state.base_passive_perception", "target_state.magic_jar_host_passive"),
            ],
        )
    )
    changed["derive_srd_control_character_facts"] = facts

    action = deepcopy(tools["check_srd_active_control_action"])
    _precondition(
        action,
        "actor_state.magic_jar_role != 'body' and "
        "actor_state.magic_jar_role != 'retired_body' and "
        "actor_state.magic_jar_role != 'host_soul' and "
        "(actor_state.magic_jar_role != 'container' or "
        "(action_kind == 'magic_jar' and magic_action == true))",
        "Only the caster soul's own projection or return Magic action is available in the Jar.",
    )
    changed["check_srd_active_control_action"] = action
    for name in (
        "spend_combat_movement",
        "derive_srd_scene_movement_budget",
        "consume_srd_reaction",
        "spend_srd_bonus_action",
    ):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        field = (
            "actor_state"
            if any(p["name"] == "actor_state" for p in tool["parameters"])
            else "actor"
        )
        _precondition(
            tool,
            f"{field}.magic_jar_role == null or {field}.magic_jar_role == 'possessed'",
            "This actual soul or catatonic body cannot move, react or take a Bonus Action.",
        )
        changed[name] = tool
    for name in ("begin_srd_spell_cast", "cast_spell", "cast_spell_outside_combat"):
        tool = deepcopy(changed.get(name, tools[name]))
        _precondition(
            tool,
            "caster_state.magic_jar_role == null or caster_state.magic_jar_role == 'possessed'",
            "The container's soul and the abandoned body cannot cast another spell.",
        )
        changed[name] = tool

    death = deepcopy(tools["record_srd_actual_death"])
    restored = []
    for n in death["effects"]:
        if n.get("condition") == "target_state.magic_jar_role == 'possessed'":
            restored.extend(n["on_false"][0]["on_true"])
        else:
            restored.append(n)
    death["effects"] = restored
    death["effects"] = [
        _when(
            "target_state.magic_jar_role == 'possessed'",
            [
                _set("target_state.magic_jar_host_death_pending", True),
            ],
            [_when("target_state.magic_jar_role != 'body'", death["effects"])],
        )
    ]
    changed["record_srd_actual_death"] = death

    damage = deepcopy(tools["apply_srd_damage"])
    damage_condition = (
        "lasting_state.has_magic_jar_sources == true and "
        "(target.magic_jar_role == 'host_soul' or "
        "target.magic_jar_role == 'possessed' or target.magic_jar_role == 'container')"
    )
    restored = []
    for n in damage["effects"]:
        restored.extend(n["on_false"] if n.get("condition") == damage_condition else [n])
    damage["effects"] = restored
    inner = deepcopy(damage)

    # The abandoned body's death is physical, while an occupied body's death first resolves souls.
    def clone_guard(node):
        if node.get("condition") == "lasting_state.has_clone_bodies == true":
            node["condition"] += (
                " and target.magic_jar_role != 'possessed' and target.magic_jar_role != 'body'"
            )

    _walk(inner["effects"], clone_guard)
    changed["apply_srd_magic_jar_body_damage"] = inner
    params = _forward_params(damage["parameters"])
    params["target"] = "{_magic_jar_target_id}"
    exports = _damage_exports(tools)
    damage["effects"] = [
        _when(
            damage_condition,
            [
                _call(
                    "resolve_srd_magic_jar_target",
                    {"target_state": "{target}"},
                    ("_magic_jar_target_id",),
                ),
                _call("apply_srd_magic_jar_occupied_damage", params, exports),
            ],
            damage["effects"],
        )
    ]
    changed["apply_srd_damage"] = damage
    occupied = deepcopy(inner)
    occupied["effects"] = [
        _call("read_srd_magic_jar_source", {"caster_state": "{target}"}, ("_jar_source",)),
        _require(
            "_jar_source.mode == 'possessed'",
            "Only the actual occupied physical body takes damage here.",
        ),
        _set("_jar_damage_original_kind", "target.creature_kind"),
        _set("target.creature_kind", "_jar_source.host_profile.creature_kind"),
        _call("apply_srd_magic_jar_body_damage", _forward_params(damage["parameters"]), exports),
        _set("target.creature_kind", "_jar_damage_original_kind"),
        _call(
            "sync_srd_magic_jar_host_body",
            {
                "caster_state": "{target}",
                "host_state": "{_jar_source.host}",
            },
        ),
        _when(
            "target.magic_jar_host_death_pending == true",
            [
                _set("target.magic_jar_host_death_pending", False),
                _call("settle_srd_magic_jar_host_death", {"caster_state": "{target}"}),
            ],
        ),
    ]
    changed["apply_srd_magic_jar_occupied_damage"] = occupied
    # Body records are not autonomous actors and cannot be independently revived.
    for name in ("resolve_srd_revival", "record_srd_deceased_body", "settle_srd_clone_death"):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        if name == "settle_srd_clone_death":
            condition = (
                "target_state.magic_jar_role != 'body' and "
                "target_state.magic_jar_role != 'possessed' and "
                "target_state.magic_jar_role != 'host_soul'"
            )
            if len(tool["effects"]) == 1 and tool["effects"][0].get("condition") == condition:
                tool["effects"] = tool["effects"][0]["on_true"]
            tool["effects"] = [_when(condition, tool["effects"])]
        else:
            _precondition(
                tool,
                "target_state.magic_jar_role != 'body' and "
                "target_state.magic_jar_role != 'retired_body' and "
                "target_state.magic_jar_role != 'host_soul'",
                "This physical alias has no independent dead soul to revive or declare.",
            )
        changed[name] = tool
    return _replace_tools(text, changed)
