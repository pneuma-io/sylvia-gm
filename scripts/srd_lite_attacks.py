"""Adapt leveled spell attacks while retaining the existing neutral attack owners."""

from copy import deepcopy
import re

from sylvia_gm.runtime._exceptions import ExpressionError
from sylvia_gm.runtime._expression_parser import Parser, TokenType, tokenize

from .srd_control_behaviors import _each, _entity, _lasting, _param
from .srd_lite_profiles import lists
from .srd_timed_actor_defenses import _call, _require, _set, _tool, _when


def _expression_names(value, replacements):
    """Rewrite recursively evaluated payloads, preserving literals and record keys."""
    if isinstance(value, str):
        try:
            tokens = tokenize(value)
            Parser(tokens).parse()
        except ExpressionError:
            # _resolve_value returns strings that fail parsing as literal prose.
            return value
        for index in range(len(tokens) - 1, -1, -1):
            token = tokens[index]
            if token.type != TokenType.IDENT or token.value not in replacements:
                continue
            if index and tokens[index - 1].type == TokenType.DOT:
                continue  # Persisted property names are not context variables.
            if tokens[index + 1].type == TokenType.LPAREN:
                continue  # Function names are not context variables either.
            value = (
                value[: token.position]
                + replacements[token.value]
                + value[token.position + len(token.value) :]
            )
        return value
    if isinstance(value, list):
        return [_expression_names(child, replacements) for child in value]
    if isinstance(value, dict):
        return {key: _expression_names(child, replacements) for key, child in value.items()}
    return value


_REFERENCE = re.compile(r"\{([A-Za-z_][A-Za-z_0-9]*)(?:\.[A-Za-z_][A-Za-z_0-9]*)*\}")
_DESTINATION = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*")


def _template_names(value, replacements):
    """Rewrite only engine-supported placeholders, including those inside quotes."""
    if not isinstance(value, str):
        return value

    def reference(match):
        name = match.group(1)
        return "{" + replacements.get(name, name) + match.group()[1 + len(name) :]

    return _REFERENCE.sub(reference, value)


def _destination_names(value, replacements):
    """Rename a destination's root binding and placeholders, keeping saved paths."""
    value = _template_names(value, replacements)
    if not isinstance(value, str):
        return value
    name = _DESTINATION.match(value)
    if name:
        return replacements.get(name.group(), name.group()) + value[name.end() :]
    return value


def _call_names(value, replacements):
    """Call values follow resolve_params; keys and exported child names stay fixed."""
    result = deepcopy(value)
    if isinstance(result.get("params"), dict):
        result["params"] = {
            key: _template_names(child, replacements) for key, child in result["params"].items()
        }
    return result


def _copy_optional_fact(private, public):
    """Copy a fact without converting a missing value into literal field-name text."""
    return {
        "condition": public + " != null",
        "on_true": [{"set": {"field": private, "value": public}}],
        "on_false": [{"set": {"field": private, "value": None}}],
    }


def _effect_names(effects, replacements):
    """Rewrite known effect contracts without mistaking evaluated records for effects."""
    result = []
    for original in effects:
        effect = deepcopy(original)
        if isinstance(effect.get("condition"), str):
            effect["condition"] = _expression_names(effect["condition"], replacements)
        elif isinstance(effect.get("condition"), dict):
            branch = effect["condition"]
            branch["condition"] = _expression_names(branch.get("condition", "true"), replacements)
            for arm in ("on_true", "on_false"):
                if arm in branch:
                    branch[arm] = _effect_names(branch[arm], replacements)
        for branch in ("on_true", "on_false"):
            if branch in effect:
                effect[branch] = _effect_names(effect[branch], replacements)
        if "store_as" in effect:
            effect["store_as"] = replacements.get(effect["store_as"], effect["store_as"])
        if isinstance(effect.get("require"), dict):
            requirement = effect["require"]
            requirement["condition"] = _expression_names(requirement["condition"], replacements)
            # _handle_require raises error verbatim, with no expression evaluation.
        if "call" in effect:
            if isinstance(effect["call"], dict):
                effect["call"] = _call_names(effect["call"], replacements)
            else:
                effect = _call_names(effect, replacements)
        if isinstance(effect.get("set"), dict):
            assignment = effect["set"]
            assignment["field"] = _destination_names(assignment["field"], replacements)
            assignment["value"] = _expression_names(assignment.get("value"), replacements)
        if isinstance(effect.get("list_push"), dict):
            push = effect["list_push"]
            push["list"] = replacements.get(push["list"], push["list"])
            push["value"] = _expression_names(push.get("value"), replacements)
        if isinstance(effect.get("for_each"), dict):
            loop = effect["for_each"]
            loop["collection"] = _expression_names(loop["collection"], replacements)
            loop["as"] = replacements.get(loop["as"], loop["as"])
            loop["do"] = _effect_names(loop["do"], replacements)
        if isinstance(effect.get("dice"), dict):
            dice = effect["dice"]
            if "expression" in dice:
                dice["expression"] = _template_names(dice["expression"], replacements)
            for field in (
                "modifier",
                "advantage",
                "disadvantage",
                "critical",
                "explode",
                "explode_on",
                "max_explosions",
            ):
                if field in dice:
                    dice[field] = _expression_names(dice[field], replacements)
        for kind in ("increment", "decrement"):
            if isinstance(effect.get(kind), dict):
                change = effect[kind]
                change["field"] = _destination_names(change["field"], replacements)
                if "amount" in change:
                    change["amount"] = _expression_names(change["amount"], replacements)
        if isinstance(effect.get("table_lookup"), dict):
            lookup = effect["table_lookup"]
            if "key" in lookup:
                lookup["key"] = _expression_names(lookup["key"], replacements)
        result.append(effect)
    return result


def bind_attacks(runtime):
    """Compose only the three selected attack profiles; full SRD remains unchanged."""
    spells = runtime["definitions"]["spells"]
    headers = runtime["definitions"]["spell_casting"]
    inflict = deepcopy(spells["guiding_bolt"])
    inflict.update(
        attack_kind="melee",
        range_kind="touch",
        range_feet=0,
        base_dice=3,
        damage_die="d10",
        allows_radiant=False,
        allows_necrotic=True,
        requires_sight=False,
    )
    inflict.pop("turn_effect")
    spells["inflict_wounds"] = inflict
    spells["guiding_bolt"].update(range_feet=60, requires_sight=False)
    spells["guiding_bolt"]["turn_effect"]["description"] = (
        "The next attack against this creature has advantage; otherwise expire after two source-turn ends."
    )
    spells["scorching_ray"].update(range_feet=60, requires_sight=False)
    headers["guiding_bolt"].update(
        range="60 feet", duration="Two turns; consumed by the next attack"
    )
    headers["scorching_ray"]["range"] = "60 feet"
    tools = runtime["tools"]
    tools["validate_srd_lite_attack_target"] = _tool(
        "Qualify the selected attack target from saved scene geometry before spending.",
        [
            _entity("caster_state"),
            _entity("target_state"),
            _lasting(),
            _param("range_feet", "integer"),
        ],
        [
            _require(
                "target_state.is_alive == true and (target_state.creature_kind == 'character' or target_state.creature_kind == 'monster')",
                "A spell attack needs a living creature when selected.",
            ),
            _call(
                "resolve_srd_scene_relation_node",
                {
                    "actor_state": "{_caster_state_id}",
                    "target_state": "{_target_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
                ("_scene_distance", "_scene_same_plane", "_scene_line_of_sight", "_scene_can_see"),
            ),
            _set("_lite_attack_range", "range_feet"),
            _when(
                "'blinded' in caster_state.conditions",
                [_set("_lite_attack_range", "min(range_feet, 10)")],
            ),
            _require(
                "_scene_distance <= _lite_attack_range and _scene_same_plane == true and _scene_line_of_sight == true",
                "The actual spell-attack target is beyond range or lacks a clear path on this plane.",
            ),
        ],
    )
    single = _call(
        "validate_srd_lite_attack_target",
        {
            "caster_state": "{_caster_state_id}",
            "target_state": "{_target_id}",
            "lasting_state": "{_lasting_state_id}",
            "range_feet": "{_lite_attack_range}",
        },
    )
    ray = _call(
        "validate_srd_lite_attack_target",
        {
            "caster_state": "{_caster_state_id}",
            "target_state": "{_lite_ray_target}",
            "lasting_state": "{_lasting_state_id}",
            "range_feet": 60,
        },
    )
    for name in ("cast_spell", "begin_srd_spell_action"):
        tools[name]["effects"].insert(
            0,
            _when(
                "spell == 'inflict_wounds' or spell == 'guiding_bolt'",
                [
                    _set("_lite_attack_range", 60),
                    _when("spell == 'inflict_wounds'", [_set("_lite_attack_range", 5)]),
                    single,
                ],
            ),
        )
        tools[name]["effects"].insert(
            0,
            _when(
                "spell == 'scorching_ray' and attack_targets != null",
                [_each("attack_targets", "_lite_ray_target", [ray])],
            ),
        )

    tools["resolve_srd_lite_attack_facts"] = _tool(
        "Project actual visibility, distance and nearby threats through existing scene owners.",
        [_entity("caster_state"), _entity("target_state"), _lasting()],
        [
            _call(
                "resolve_srd_scene_relation_node",
                {
                    "actor_state": "{_caster_state_id}",
                    "target_state": "{_target_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
                ("_scene_distance", "_scene_can_see", "_scene_same_plane", "_scene_line_of_sight"),
            ),
            _set("_lite_attack_distance", "_scene_distance"),
            _set("_lite_attack_caster_sees", "_scene_can_see"),
            _set("_lite_attack_same_plane", "_scene_same_plane"),
            _set("_lite_attack_line_of_sight", "_scene_line_of_sight"),
            _call(
                "resolve_srd_scene_relation_node",
                {
                    "actor_state": "{_target_state_id}",
                    "target_state": "{_caster_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
                ("_scene_can_see",),
            ),
            _set("_lite_attack_target_sees", "_scene_can_see"),
            _call(
                "resolve_srd_produce_flame_nearby_enemies",
                {"caster_state": "{_caster_state_id}", "lasting_state": "{_lasting_state_id}"},
                ("_produce_nearby_disadvantage",),
            ),
            _set("_lite_attack_threatened", "_produce_nearby_disadvantage"),
        ],
    )
    project = [
        _call(
            "resolve_srd_lite_attack_facts",
            {
                "caster_state": "{_caster_state_id}",
                "target_state": "{_target_id}",
                "lasting_state": "state",
            },
            (
                "_lite_attack_distance",
                "_lite_attack_caster_sees",
                "_lite_attack_target_sees",
                "_lite_attack_threatened",
                "_lite_attack_same_plane",
                "_lite_attack_line_of_sight",
            ),
        ),
        _set("_lite_caster_can_see_target", "_lite_attack_caster_sees"),
        _set("_lite_target_can_see_caster", "_lite_attack_target_sees"),
        _set("_lite_enemy_within_5_feet", "_lite_attack_threatened"),
        _set("_lite_close_enemy_can_see_caster", True),
        _set("_lite_close_enemy_incapacitated", False),
        _set("_lite_target_distance", "_lite_attack_distance"),
        _set("_lite_target_within_5_feet", "_lite_attack_distance <= 5"),
        _set("_lite_leaf_range", 60),
        _when("spell == 'inflict_wounds'", [_set("_lite_leaf_range", 5)]),
        _when(
            "'blinded' in caster_state.conditions",
            [_set("_lite_leaf_range", "min(_lite_leaf_range, 10)")],
        ),
        _require(
            "_lite_attack_same_plane == true and _lite_attack_line_of_sight == true and _lite_attack_distance <= _lite_leaf_range",
            "The actual spell-attack target is beyond range or lacks a clear path on this plane.",
        ),
    ]
    replacements = {
        name: "_lite_" + name
        for name in (
            "caster_can_see_target",
            "target_can_see_caster",
            "enemy_within_5_feet",
            "close_enemy_can_see_caster",
            "close_enemy_incapacitated",
            "target_distance",
            "target_within_5_feet",
        )
    }
    for name, selected in (
        ("cast_spell_attack_damage", "spell == 'guiding_bolt' or spell == 'inflict_wounds'"),
        (
            "resolve_spell_multi_attack_target",
            "spell == 'scorching_ray' and target.is_alive == true",
        ),
    ):
        owner = tools[name]
        defaults = []
        if name == "resolve_spell_multi_attack_target":
            # An earlier ray can defeat this recipient. The caller still exports
            # the target number, although this branch makes no attack against it.
            defaults.append(_set("_spell_target_armor_class", None))
        owner["effects"] = [
            *defaults,
            *[_set(actual, original) for original, actual in replacements.items()],
            _when(selected, deepcopy(project)),
            *_effect_names(owner["effects"], replacements),
        ]
        for condition in owner.get("pre_conditions", []):
            if "target_distance" in condition["condition"]:
                condition["condition"] = "(" + selected + ") or (" + condition["condition"] + ")"

    # Current and next caster-turn ends are the two source-owned countdown boundaries.
    # Exploration has no turn boundary, so the independent elapsed-time expiry is two rounds.
    for effects in lists(tools["install_srd_turn_effect"]["effects"]):
        for effect in effects:
            pushed = effect.get("list_push")
            if (
                pushed
                and isinstance(pushed.get("value"), dict)
                and pushed["value"].get("kind") == "kind"
            ):
                effects.insert(
                    effects.index(effect),
                    _set("_lite_turn_expiry_tick", "lasting_state.lasting_tick + 2"),
                )
                effects.insert(
                    effects.index(effect),
                    _when(
                        "spell == 'guiding_bolt'",
                        [_set("_lite_turn_expiry_tick", "lasting_state.lasting_tick + 4")],
                    ),
                )
                effects.insert(
                    effects.index(effect),
                    _when(
                        "spell == 'guiding_bolt' and combat.encounter_active != true",
                        [_set("_effect_turn", "caster_state.turns_started + 2")],
                    ),
                )
                pushed["value"]["expires_tick"] = "_lite_turn_expiry_tick"
                break


def bind_attack_facts(runtime):
    """Retain the shared reviewed projection, without historical spell overrides."""
    tools = runtime["tools"]
    previous = tools.get("resolve_srd_lite_attack_facts")
    previous_target = tools.get("validate_srd_lite_attack_target")
    tools["validate_srd_lite_attack_target"] = _tool(
        "Qualify the selected attack target from saved scene geometry before spending.",
        [
            _entity("caster_state"),
            _entity("target_state"),
            _lasting(),
            _param("range_feet", "integer"),
        ],
        [
            _require(
                "target_state.is_alive == true and (target_state.creature_kind == 'character' or target_state.creature_kind == 'monster')",
                "A spell attack needs a living creature when selected.",
            ),
            _call(
                "resolve_srd_scene_relation_node",
                {
                    "actor_state": "{_caster_state_id}",
                    "target_state": "{_target_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
                ("_scene_distance", "_scene_same_plane", "_scene_line_of_sight", "_scene_can_see"),
            ),
            _set("_lite_attack_range", "range_feet"),
            _require(
                "_scene_distance <= _lite_attack_range and _scene_same_plane == true and _scene_line_of_sight == true",
                "The actual spell-attack target is beyond range or lacks a clear path on this plane.",
            ),
        ],
    )
    tools["resolve_srd_lite_attack_facts"] = _tool(
        "Project actual visibility, distance and nearby threats through existing scene owners.",
        [_entity("caster_state"), _entity("target_state"), _lasting()],
        [
            _call(
                "resolve_srd_scene_relation_node",
                {
                    "actor_state": "{_caster_state_id}",
                    "target_state": "{_target_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
                ("_scene_distance", "_scene_can_see", "_scene_same_plane", "_scene_line_of_sight"),
            ),
            _set("_lite_attack_distance", "_scene_distance"),
            _set("_lite_attack_caster_sees", "_scene_can_see"),
            _set("_lite_attack_same_plane", "_scene_same_plane"),
            _set("_lite_attack_line_of_sight", "_scene_line_of_sight"),
            _call(
                "resolve_srd_scene_relation_node",
                {
                    "actor_state": "{_target_state_id}",
                    "target_state": "{_caster_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
                ("_scene_can_see",),
            ),
            _set("_lite_attack_target_sees", "_scene_can_see"),
            _call(
                "resolve_srd_produce_flame_nearby_enemies",
                {"caster_state": "{_caster_state_id}", "lasting_state": "{_lasting_state_id}"},
                ("_produce_nearby_disadvantage",),
            ),
            _set("_lite_attack_threatened", "_produce_nearby_disadvantage"),
        ],
    )
    if previous is not None and previous != tools["resolve_srd_lite_attack_facts"]:
        raise ValueError("Existing shared cantrip projection ownership changed")
    if previous_target is not None and previous_target != tools["validate_srd_lite_attack_target"]:
        raise ValueError("Existing shared cantrip target ownership changed")
    return ([] if previous is not None else ["resolve_srd_lite_attack_facts"]) + (
        [] if previous_target is not None else ["validate_srd_lite_attack_target"]
    )
