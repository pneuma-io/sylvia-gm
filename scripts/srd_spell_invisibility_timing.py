"""Observe ordinary Invisibility after a real cast completes, preserving live pending sight."""

from copy import deepcopy

from scripts.srd_timed_actor_defenses import _call, _load_runtime, _when
from scripts.srd_timed_senses import _replace_tools

ENDING = "end_srd_invisibility_spell_source"


def _without_ending(effects):
    """Remove only the existing Light callback, retaining every other owning effect."""
    result = []
    for original in effects:
        if original.get("call") == ENDING:
            continue
        effect = deepcopy(original)
        for branch in ("on_true", "on_false"):
            if branch in effect:
                effect[branch] = _without_ending(effect[branch])
        if "for_each" in effect:
            effect["for_each"]["do"] = _without_ending(effect["for_each"].get("do", []))
        owned_guard = original.get("on_true") and all(
            item.get("call") == ENDING for item in original["on_true"]
        )
        if owned_guard and not effect.get("on_true") and not effect.get("on_false"):
            continue
        result.append(effect)
    return result


def _ending(actor="{_caster_state_id}"):
    return _call(ENDING, {"caster_state": actor})


def _bind_scalar_sight(tool):
    """Query the live defender's sight only where the owning scalar actually rolls."""
    result = deepcopy(tool)
    query = _call(
        "resolve_srd_illumination_target_sight",
        {"observer_state": "{_target_id}", "target_state": "{_caster_state_id}"},
    )
    query["export_context"] = {
        "_invisibility_attack_target_can_see": "_illumination_target_can_see"
    }
    found = 0

    def bind(effects):
        nonlocal found
        bound = []
        for effect in effects:
            for branch in ("on_true", "on_false"):
                if branch in effect:
                    effect[branch] = bind(effect[branch])
            if "for_each" in effect:
                effect["for_each"]["do"] = bind(effect["for_each"].get("do", []))
            if effect.get("call") == "d20_test" and effect["params"].get("kind") == "attack_roll":
                found += 1
                if not bound or bound[-1] != query:
                    bound.append(deepcopy(query))
                effect["params"]["target_can_see_actor"] = "{_invisibility_attack_target_can_see}"
            bound.append(effect)
        return bound

    result["effects"] = bind(result["effects"])
    if found != 1:
        raise ValueError("Live Invisible attack timing requires the exact scalar D20 owner.")
    return result


def _bind_weapon_roll_ending(tool):
    """End the living ordinary source immediately after the real retained weapon roll."""
    result = deepcopy(tool)
    ending = _when(
        "actor_state.illumination_has_invisibility == true",
        [
            _call(
                "end_srd_invisibility_for_actor",
                {"actor_state": "{_actor_state_id}", "event": "attack_roll"},
            )
        ],
    )
    found = 0

    def bind(effects):
        nonlocal found
        bound = []
        for index, effect in enumerate(effects):
            for branch in ("on_true", "on_false"):
                if branch in effect:
                    effect[branch] = bind(effect[branch])
            if "for_each" in effect:
                effect["for_each"]["do"] = bind(effect["for_each"].get("do", []))
            bound.append(effect)
            if effect.get("dice", {}).get("expression") == "1d20":
                found += 1
                if index + 1 == len(effects) or effects[index + 1] != ending:
                    bound.append(deepcopy(ending))
        return bound

    result["effects"] = bind(result["effects"])
    if found != 1:
        raise ValueError("Weapon Invisibility timing requires the exact owning retained Dice.")
    return result


def bind_runtime_consumers(text):
    """Bind after final Light and accepted-begin owners; costs, sources and Dice stay theirs."""
    runtime = _load_runtime(text)
    tools = runtime["tools"]
    if ENDING not in tools:
        raise ValueError("Completed casting timing requires the actual Light ending helper.")
    if "resolve_srd_illumination_target_sight" not in tools:
        raise ValueError("Live Invisible attacks require the actual Light sight owner.")
    changed = {}
    scalar = _bind_scalar_sight(tools["cast_spell_attack_damage"])
    if scalar != tools["cast_spell_attack_damage"]:
        changed["cast_spell_attack_damage"] = scalar
    weapon = _bind_weapon_roll_ending(tools["continue_srd_character_attack_targeting"])
    if weapon != tools["continue_srd_character_attack_targeting"]:
        changed["continue_srd_character_attack_targeting"] = weapon
    for name in ("cast_spell", "cast_spell_outside_combat", "cast_srd_reaction_spell"):
        original = tools[name]
        cast = deepcopy(original)
        cast["effects"] = _without_ending(original["effects"])
        # Final delegates call the actual cast owner; they need no second callback.
        if cast["effects"] != original["effects"] or name == "cast_spell":
            cast["effects"].append(
                _when(
                    "caster_state.illumination_has_invisibility == true and "
                    "_pending_spell_operation_lost != true and "
                    "caster_state.pending_srd_completion_id == null and "
                    "(spell != 'invisibility' or _spell_cast_failed == true)",
                    [_ending()],
                )
            )
            changed[name] = cast
    begin = deepcopy(tools["begin_srd_spell_action"])
    begin["effects"] = _without_ending(begin["effects"])
    if begin != tools["begin_srd_spell_action"]:
        changed["begin_srd_spell_action"] = begin
    extended = deepcopy(tools["begin_srd_spell_cast_internal"])
    extended["effects"] = _without_ending(extended["effects"])
    # A real failed start is already a committed failed casting. Successful longer
    # casting reaches the ordinary paid cast owner only when it actually completes.
    extended["effects"].append(
        _when(
            "_slow_cast_failed == true and caster_state.illumination_has_invisibility == true",
            [_ending()],
        )
    )
    changed["begin_srd_spell_cast_internal"] = extended
    completion = deepcopy(tools["complete_srd_pending_action"])
    completion["effects"] = _without_ending(completion["effects"])
    resolved = [
        effect
        for effect in completion["effects"]
        if effect.get("condition")
        == "_pending_action.kind == 'spell_cast' and _pending_action.status == 'open' and "
        "_pending_action.owner_kind != 'extended_casting'"
    ]
    if len(resolved) != 1:
        raise ValueError("Completed casting timing needs the exact final saved-cast owner.")
    resolved[0]["on_true"].append(
        _when(
            "_pending_action.spell != 'invisibility' or "
            "(_pending_action.slow_cast != null and _pending_action.slow_cast.failed == true)",
            [_ending("{_pending_action.actor_state_id}")],
        )
    )
    lost = [
        effect
        for effect in completion["effects"]
        if effect.get("condition")
        == "_pending_action.kind == 'spell_cast' and _pending_action.status == 'lost'"
    ]
    if len(lost) != 1:
        raise ValueError("Counterspelled timing requires the actual saved lost-cast completion.")
    lost[0]["on_true"].append(_ending("{_pending_action.actor_state_id}"))
    changed["complete_srd_pending_action"] = completion
    illumination = deepcopy(tools["cast_spell_illumination_spell"])
    illumination["effects"] = _without_ending(illumination["effects"])
    invisible_install = [
        effect
        for effect in illumination["effects"]
        if effect.get("condition") == "_light_profile.illumination_mode == 'invisible'"
    ]
    if len(invisible_install) != 1:
        raise ValueError("Completed self-Invisibility needs its actual pre-install owner.")
    # End the living previous ordinary source as this non-attack effect takes hold.
    # The current cast's new source is not installed yet and survives this event.
    invisible_install[0]["on_true"].insert(
        0,
        _when(
            "spell == 'invisibility' and caster_state.illumination_has_invisibility == true",
            [_ending()],
        ),
    )
    changed["cast_spell_illumination_spell"] = illumination
    return _replace_tools(text, changed)
