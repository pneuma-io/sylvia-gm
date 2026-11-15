"""Normalize the completed SRD roll owner without changing its cards or mechanical hooks."""

from copy import deepcopy

if __package__:
    from .srd_timed_actor_defenses import _load_runtime, _set, _when
    from .srd_timed_senses import _replace_tools
else:
    from srd_timed_actor_defenses import _load_runtime, _set, _when
    from srd_timed_senses import _replace_tools

PROFICIENCY_SCALE = {"none": 0, "half": 0.5, "normal": 1, "double": 2}


def _branch(condition, yes, no):
    return {"condition": condition, "on_true": yes, "on_false": no}


def _title(effects):
    """Keep the existing title precedence while assigning its final value only once."""
    initial = next(
        (effect for effect in effects if effect.get("set", {}).get("field") == "_card_title"),
        None,
    )
    if initial is None:
        return
    titles = [
        effect
        for effect in effects
        if effect.get("set", {}).get("field") == "_card_title"
        or (
            "on_false" not in effect
            and len(effect.get("on_true", [])) == 1
            and effect["on_true"][0].get("set", {}).get("field") == "_card_title"
        )
    ]
    if len(titles) != 6:
        raise SystemExit("D20 title normalization requires the six existing title effects")
    title = _branch(
        "skill != null",
        [_set("_card_title", "skill")],
        [
            _branch(
                "tool != null",
                [_set("_card_title", "tool")],
                [
                    _branch(
                        "ability != null",
                        [_set("_card_title", "ability")],
                        [
                            _branch(
                                "kind == 'saving_throw'",
                                [_set("_card_title", "'Saving throw'")],
                                [
                                    _branch(
                                        "kind == 'attack_roll'",
                                        [_set("_card_title", "'Attack roll'")],
                                        [_set("_card_title", "'Ability check'")],
                                    )
                                ],
                            )
                        ],
                    )
                ],
            )
        ],
    )
    index = effects.index(initial)
    effects[:] = [effect for effect in effects if effect not in titles]
    effects.insert(index, title)


def _automatic_failure_defaults(effects, rolling):
    """The rolled path assigns all three outputs; automatic failures still expose exact nulls."""
    fields = {"_d20_test", "_adjusted_total", "_combined_modifier"}
    defaults = [effect for effect in effects if effect.get("set", {}).get("field") in fields]
    if not defaults:
        return
    if len(defaults) != 3 or any(effect["set"]["value"] is not None for effect in defaults):
        raise SystemExit("D20 output defaults differ from the three existing null initializers")
    effects[:] = [effect for effect in effects if effect not in defaults]
    rolling["on_true"][:0] = defaults


def _proficiency(effects):
    """Evaluate the admitted proficiency enum with the same downward rounding for half."""
    if any(effect.get("set", {}).get("field") == "_d20_proficiency_scale" for effect in effects):
        return
    indices = [
        index
        for index, effect in enumerate(effects)
        if effect.get("set", {}).get("field") == "_proficiency_modifier"
        or effect.get("condition", "").startswith("proficiency_factor ==")
    ]
    if len(indices) != 4 or indices != list(range(indices[0], indices[0] + 4)):
        raise SystemExit("D20 proficiency normalization requires the four existing enum effects")
    effects[indices[0] : indices[-1] + 1] = [
        _set("_d20_proficiency_scale", deepcopy(PROFICIENCY_SCALE)),
        _set(
            "_proficiency_modifier",
            "floor(_proficiency_bonus * _d20_proficiency_scale[proficiency_factor])",
        ),
    ]


def _native_conditions(effects):
    """Skip attack/check-only branches on saves and qualify Dodge before computing its speed."""
    native = next(
        (
            effect
            for effect in effects
            if any(
                child.get("set", {}).get("field") == "_dodge_speed_available"
                for child in effect.get("on_true", [])
            )
        ),
        None,
    )
    if native is None:
        return
    body = native["on_true"]
    grouped = [
        effect
        for effect in body
        if "saving_throw" not in effect.get("condition", "")
        and any(
            text in effect.get("condition", "")
            for text in (
                "'invisible' in actor_state.conditions",
                "'blinded' in actor_state.conditions",
                "'frightened' in actor_state.conditions",
                "'deafened' in actor_state.conditions",
            )
        )
        and (
            "kind == 'attack_roll'" in effect["condition"]
            or "kind == 'ability_check'" in effect["condition"]
        )
    ]
    if len(grouped) != 5:
        raise SystemExit("D20 native normalization requires the five attack/check-only branches")
    index = body.index(grouped[0])
    body[:] = [effect for effect in body if effect not in grouped]
    body.insert(index, _when("kind == 'ability_check' or kind == 'attack_roll'", grouped))
    speed = next(
        effect for effect in body if effect.get("set", {}).get("field") == "_dodge_speed_available"
    )
    dodge = next(
        effect
        for effect in body
        if effect.get("condition", "").startswith("actor_state.dodging == true")
    )
    index = body.index(speed)
    body[:] = [effect for effect in body if effect not in (speed, dodge)]
    body.insert(
        index,
        _when(
            "actor_state.dodging == true and kind == 'saving_throw' and ability == 'dexterity'",
            [speed, dodge],
        ),
    )


def _attack_outcomes(rolling):
    """Retain post-Dice source callbacks and evaluate critical outcomes only for an attack."""
    rolled = rolling["on_false"]
    critical = [
        effect
        for effect in rolled
        if effect.get("condition", "").startswith("kind == 'attack_roll' and _d20_test.total")
    ]
    if not critical:
        return
    attack = next(
        effect
        for effect in rolled
        if effect.get("condition") == "kind == 'attack_roll'"
        and any(
            child.get("set", {}).get("field") == "_attack_outcome"
            for child in effect.get("on_true", [])
        )
    )
    index = rolled.index(attack)
    if len(critical) != 2 or rolled[index + 1 : index + 3] != critical:
        raise SystemExit("D20 critical outcomes must follow the actual post-Dice attack branch")
    rolled[:] = [effect for effect in rolled if effect not in critical]
    for effect in critical:
        effect["condition"] = effect["condition"].removeprefix("kind == 'attack_roll' and ")
    attack["on_true"].extend(critical)


def _attack_check_sources(effects):
    """Keep Heat Metal's earned grip check inside the existing attack/check-only path."""

    def lists(rows):
        yield rows
        for effect in rows:
            for key in ("on_true", "on_false"):
                if isinstance(effect.get(key), list):
                    yield from lists(effect[key])

    def heat_branch(effect):
        return any(
            child.get("call") == "check_srd_heat_metal_disadvantage"
            for child in effect.get("on_true", [])
        )

    bodies = list(lists(effects))
    heat = [effect for rows in bodies for effect in rows if heat_branch(effect)]
    if not heat:
        return
    group = next(
        (
            effect
            for rows in bodies
            for effect in rows
            if effect.get("condition") == "kind == 'ability_check' or kind == 'attack_roll'"
            and any(
                "'blinded' in actor_state.conditions" in child.get("condition", "")
                for child in effect.get("on_true", [])
            )
        ),
        None,
    )
    if group is None:
        raise SystemExit("Heat Metal D20 normalization requires the existing attack/check group")
    # A repeated family binder can emit a fresh outer branch beside the previously grouped one.
    owning = next((effect for effect in effects if heat_branch(effect)), heat[0])
    if "kind == 'attack_roll'" not in owning.get(
        "condition", ""
    ) or "kind == 'ability_check'" not in owning.get("condition", ""):
        raise SystemExit("Heat Metal D20 hook must remain limited to attacks and ability checks")
    for rows in bodies:
        rows[:] = [effect for effect in rows if not heat_branch(effect)]
    group["on_true"].append(owning)


def normalize_d20_tool(tool):
    """Reduce inactive work after family composition while retaining one mechanical roll owner."""
    result = deepcopy(tool)
    effects = result["effects"]
    rolling = next(
        effect
        for effect in effects
        if effect.get("condition", "").startswith("forego_save == true")
    )
    _title(effects)
    _automatic_failure_defaults(effects, rolling)
    _proficiency(effects)
    _native_conditions(effects)
    _attack_check_sources(effects)
    _attack_outcomes(rolling)
    return result


def normalize_runtime(text):
    """Run after all owning D20 consumers and before reaction helpers clone the completed owner."""
    runtime = _load_runtime(text)
    result = _replace_tools(text, {"d20_test": normalize_d20_tool(runtime["tools"]["d20_test"])})
    if "*d20_result_card" in result and "&d20_result_card" not in result:
        start = result.index("  d20_test:")
        index = result.index("      result_card:", start)
        result = result[:index] + result[index:].replace(
            "      result_card:", "      result_card: &d20_result_card", 1
        )
    return result
