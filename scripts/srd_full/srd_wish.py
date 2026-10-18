"""Paid Wish duplication delegates selected fixed effects to their existing owners."""

from copy import deepcopy

if __package__:
    from .srd_control_behaviors import (
        _call,
        _entity,
        _lasting,
        _param,
        _require,
        _set,
        _tool,
        _walk,
        _when,
    )
    from .srd_timed_actor_defenses import _load_runtime
    from .srd_timed_senses import _replace_tools
else:
    from srd_control_behaviors import (
        _call,
        _entity,
        _lasting,
        _param,
        _require,
        _set,
        _tool,
        _walk,
        _when,
    )
    from srd_timed_actor_defenses import _load_runtime
    from srd_timed_senses import _replace_tools

SOURCE_PAGE = 175
SOURCE_PAGE_SHA256 = "61bb65029264b6248ccff644554cf03ef418bfce087ba775181ed8e6d1f3ae39"
SOURCE_PDF_SHA256 = "8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87"
PROFILE = {
    "family": "wish",
    "base_level": 9,
    "casting_time": "action",
    "range_kind": "self",
    "range_feet": 0,
}
PUBLIC_TOOLS = set()
COPIES = {
    "cure_wounds": (1, "spell_healing"),
    "healing_word": (1, "spell_healing"),
    "fly": (3, "multi_target_timed_actor_mobility"),
    "stoneskin": (4, "single_target_concentration_damage_resistance"),
}
CAST_CHOICES = [
    _param("wish_spell", required=False, default=""),
    {**_entity("wish_target"), "required": False, "default": None},
]


def schema():
    properties = {
        key: {"type": "integer" if type(value) is int else "string", "enum": [value]}
        for key, value in PROFILE.items()
        if key != "family"
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _supported(variable):
    return "(" + " or ".join(variable + " == '" + spell + "'" for spell in COPIES) + ")"


def validate_definition(spell, definition):
    if spell != "wish" or definition != PROFILE:
        raise SystemExit("Wish needs its exact source-owned ninth-level duplication profile.")


def _authorization():
    return {
        "condition": "caster_state.wish_duplicate_authorization != null and "
        "caster_state.wish_duplicate_authorization.cast_id == wish_cast_id and "
        "caster_state.wish_duplicate_authorization.spell == spell and "
        "caster_state.wish_duplicate_authorization.target == _target_id and "
        "caster_state.wish_duplicate_authorization.status == 'open' and "
        "caster_state.wish_duplicate_authorization.paid_level == 9 and "
        "caster_state.last_spell_source_cast_id == wish_cast_id and "
        "caster_state.last_spell_source_spell == 'wish'",
        "error": "Only this actual paid Wish authorizes the selected duplicate effect.",
    }


def _effect_owner(tool, *, removed):
    """Waive only the native preparation/slot checks; retain the exact receiving body."""
    result = deepcopy(tool)
    checks = result.get("pre_conditions", [])
    waived = [
        check
        for check in checks
        if "prepared_spells" in check["condition"]
        or "caster_state.resources[slot_resource]" in check["condition"]
    ]
    if len(waived) != removed:
        raise ValueError("Wish must recognize the actual child preparation and slot boundaries.")
    result["pre_conditions"] = [_authorization()] + [
        check for check in checks if check not in waived
    ]
    result["parameters"].append(_param("wish_cast_id"))
    for effect in _walk(result["effects"]):
        if effect.get("decrement", {}).get("field", "").startswith(
            "caster_state.resources"
        ) or effect.get("call") in {
            "consume_srd_action",
            "consume_srd_bonus_action",
            "consume_srd_spell_slot",
        }:
            raise ValueError("A Wish effect owner must not charge a second casting cost.")
    return result


def _child_call(owner, *, family):
    """Bind source constants and validated outer caster facts, never caller roll verdicts."""
    values = {
        "caster_state": "{_caster_state_id}",
        "target": "{_target_id}",
        "lasting_state": "{_lasting_state_id}",
        "spell": "{spell}",
        "family": family,
        "slot_resource": "no_slot",
        "slot_level": "{_wish_definition.base_level}",
        "actual_spell_level": "{_wish_definition.base_level}",
        "wish_cast_id": "{wish_cast_id}",
        "spell_cast_id": "{wish_cast_id}",
        "spell_cast_owner": "{_caster_state_id}",
        "targets": "{_wish_targets}",
        "target_willing": "{target_willing}",
        "all_targets_willing": "{target_willing}",
        "caster_can_touch_target": "{_wish_can_touch}",
        "caster_can_touch_all_targets": "{_wish_can_touch}",
        "caster_can_see_target": "{_illumination_target_can_see}",
        "target_distance": "{_wish_distance}",
        "targets_max_distance": "{_wish_distance}",
        "all_targets_have_clear_path": "{_wish_clear_path}",
        "cover": "none",
        "selected_damage_type": None,
        "casting_concentration_save_modifier": "{concentration_save_modifier}",
    }
    for name in ("ability", "modifier", "attack_bonus", "save_dc", "scaling_level"):
        values["casting_" + name] = "{casting_" + name + "}"
    values["concentration_save_modifier"] = "{concentration_save_modifier}"
    params = {}
    for parameter in owner["parameters"]:
        name = parameter["name"]
        if name in values:
            params[name] = values[name]
        elif name in {
            "scaling_mode",
            "cantrip_upgrade_dice",
            "upgrade_level_1",
            "upgrade_level_2",
            "upgrade_level_3",
        }:
            if parameter.get("required", True):
                raise ValueError("Wish requires the child owner's actual scaling declaration.")
        else:
            params[name] = "{_wish_definition." + name + "}"
    call = _call("resolve_srd_wish_" + family, params)
    call["export_context"] = {
        "_wish_summary": "_spell_summary_rows",
        "_wish_details": "_spell_detail_rows",
    }
    return call


def _effect_owners(tools):
    owners = {
        "spell_healing": _effect_owner(tools["cast_spell_healing"], removed=3),
        "single_target_concentration_damage_resistance": _effect_owner(
            tools["cast_spell_single_target_concentration_damage_resistance"], removed=2
        ),
        "multi_target_timed_actor_mobility": deepcopy(tools["cast_spell_timed_actor_mobility"]),
    }
    mobility = owners["multi_target_timed_actor_mobility"]
    mobility["parameters"].append(_param("wish_cast_id"))
    mobility["parameters"].append(_entity("target"))
    mobility["pre_conditions"] = [_authorization()] + mobility.get("pre_conditions", [])
    return owners


def bind_runtime_consumers(text):
    """Recapture final child receiving/source owners before reaction cloning."""
    owners = _effect_owners(_load_runtime(text)["tools"])
    return _replace_tools(
        text, {"resolve_srd_wish_" + family: owner for family, owner in owners.items()}
    )


def extend_runtime(text):
    """Emit before casting/reaction clones, after the actual child effect owners exist."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    owners = _effect_owners(tools)
    changed.update({"resolve_srd_wish_" + family: owner for family, owner in owners.items()})
    parameters = [
        _entity("caster_state"),
        _entity("target"),
        _lasting(),
        _param("spell"),
        _param("wish_cast_id"),
        _param("target_willing", "boolean"),
    ]
    parameters += [
        _param("casting_" + name, "string" if name == "ability" else "integer")
        for name in ("ability", "modifier", "attack_bonus", "save_dc", "scaling_level")
    ]
    parameters.append(_param("concentration_save_modifier", "integer"))
    effects = [
        _set("_wish_definition", "definitions.spells[spell]"),
        _require(
            "_wish_definition != null and _wish_definition.base_level <= 8",
            "Wish duplicates only a defined spell of level 8 or lower.",
        ),
        _call(
            "resolve_srd_illumination_target_sight",
            {"observer_state": "{_caster_state_id}", "target_state": "{_target_id}"},
            [
                "_illumination_target_can_see",
                "_illumination_target_same_plane",
                "_illumination_point_distance",
                "_illumination_point_total_cover",
            ],
        ),
        _require(
            "_illumination_target_same_plane == true and _illumination_point_total_cover != true",
            "The selected duplicate requires an actual same-plane target and clear physical path.",
        ),
        _set("_wish_can_touch", "_illumination_point_distance <= 5"),
        _set("_wish_distance", "ceil(_illumination_point_distance)"),
        _set("_wish_clear_path", "_illumination_point_total_cover != true"),
        _set("_wish_targets", ["_target_id"]),
    ]
    for spell, (level, family) in COPIES.items():
        effects.append(
            _when(
                "spell == '" + spell + "'",
                [
                    _require(
                        "_wish_definition.family == '"
                        + family
                        + "' and _wish_definition.base_level == "
                        + str(level),
                        "The duplicate must retain its actual source-defined base-level effect.",
                    ),
                    _child_call(owners[family], family=family),
                ],
            )
        )
    effects += [
        _set("caster_state.wish_duplicate_authorization.status", "'consumed'"),
        _set(
            "caster_state.last_wish_duplicate",
            {
                "spell": "spell",
                "spell_level": "_wish_definition.base_level",
                "cast_id": "wish_cast_id",
                "target": "_target_id",
                "stress": False,
            },
        ),
    ]
    changed["cast_srd_wish_duplicate"] = _tool(
        "Apply one selected base-level duplicate within its already paid Wish.",
        parameters,
        effects,
        [
            _authorization(),
            {
                "condition": _supported("spell"),
                "error": "This Wish duplicate has no admitted effect consumer yet.",
            },
        ],
    )
    cast = deepcopy(tools["cast_spell"])
    names = {p["name"] for p in cast["parameters"]}
    cast["parameters"] += [deepcopy(p) for p in CAST_CHOICES if p["name"] not in names]
    for effect in _walk(cast["effects"]):
        if isinstance(effect.get("on_true"), list):
            effect["on_true"][:] = [
                e
                for e in effect["on_true"]
                if "_spell_definition.family == 'wish'" not in str(e.get("condition", ""))
            ]
    cast["effects"][:] = [
        e
        for e in cast["effects"]
        if "_spell_definition.family == 'wish'" not in str(e.get("condition", ""))
    ]
    resolution = [
        effects
        for effects in _effect_lists(cast["effects"])
        if any(
            isinstance(e.get("condition"), str)
            and "_spell_definition.family == 'spell_healing'" in e["condition"]
            for e in effects
        )
    ]
    if len(resolution) != 1:
        raise ValueError("Wish needs the one genuine paid effect dispatch before the cast card.")
    call = _call(
        "cast_srd_wish_duplicate",
        {
            "caster_state": "{_caster_state_id}",
            "target": "{_wish_target_id}",
            "spell": "{wish_spell}",
            "wish_cast_id": "{_wish_cast_id}",
            "target_willing": "{target_willing}",
            **{
                "casting_" + name: "{_casting_profile." + name + "}"
                for name in ("ability", "modifier", "attack_bonus", "save_dc", "scaling_level")
            },
            "concentration_save_modifier": "{_casting_profile.concentration_save_modifier}",
        },
    )
    call["export_context"] = {
        "_spell_summary_rows": "_wish_summary",
        "_spell_detail_rows": "_wish_details",
    }
    resolution[0].append(
        _when(
            "_spell_cast_failed != true and _spell_definition.family == 'wish'",
            [
                _require(
                    "spell == 'wish' and _slot_level == 9 and "
                    + _supported("wish_spell")
                    + " and wish_target != null",
                    "Select an implemented Wish duplicate and an actual target; "
                    "other Wish outcomes remain unsupported.",
                ),
                _call(
                    "begin_srd_spell_source_instance",
                    {"caster_state": "{_caster_state_id}", "spell": "wish", "slot_level": 9},
                    ["_spell_source_cast_id"],
                ),
                _set("_wish_cast_id", "_spell_source_cast_id"),
                _set(
                    "caster_state.wish_duplicate_authorization",
                    {
                        "cast_id": "_wish_cast_id",
                        "spell": "wish_spell",
                        "target": "_wish_target_id",
                        "status": "'open'",
                        "paid_level": 9,
                    },
                ),
                call,
            ],
        )
    )
    changed["cast_spell"] = cast
    return _replace_tools(text, changed)


def _effect_lists(effects):
    yield effects
    for effect in effects:
        for branch in ("on_true", "on_false"):
            if isinstance(effect.get(branch), list):
                yield from _effect_lists(effect[branch])
