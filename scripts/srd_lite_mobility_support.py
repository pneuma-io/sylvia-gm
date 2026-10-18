"""Variant-only Longstrider/Leap behavior over the admitted casting and movement owners."""

from copy import deepcopy
from io import StringIO
import tokenize

from .srd_control_behaviors import _each, _entity, _lasting, _param, _push
from .srd_lite_profiles import lists
from .srd_timed_actor_defenses import _call, _require, _set, _tool, _when

SPELLS = ("longstrider", "jump")
SCOPED = "(spell == 'longstrider' or spell == 'jump' or spell == 'enhance_leap')"
PAYMENT_SCOPED = SCOPED


def _spell_expression(expression):
    """Replace only a root parameter token, preserving properties and quoted prose."""
    lines = expression.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    changes = []
    previous = None
    for token in tokenize.generate_tokens(StringIO(expression).readline):
        if token.type == tokenize.NAME and token.string == "spell" and previous != ".":
            start = offsets[token.start[0]-1] + token.start[1]
            end = offsets[token.end[0]-1] + token.end[1]
            changes.append((start, end))
        if token.type not in {tokenize.NL, tokenize.NEWLINE, tokenize.ENDMARKER}:
            previous = token.string
    for start, end in reversed(changes):
        expression = expression[:start] + "_ms" + expression[end:]
    return expression


def _bind_spell_identity(runtime):
    """Canonicalize admitted casting aliases after the unchanged Lite guards bind.

    The real ephemeral binding feeds existing lookups, dispatch and saved choices.
    Pending/excluded policy and canonical acquisition identities remain unchanged.
    """
    def rewrite(value, parent=None):
        if isinstance(value, dict):
            for key, child in value.items():
                if isinstance(child, str) and key in {"condition", "value"}:
                    value[key] = _spell_expression(child)
                elif key == "key" and child == "spell" and "table" in value:
                    value[key] = "_ms"
                elif key == "params" and isinstance(child, dict):
                    for parameter, supplied in child.items():
                        if isinstance(supplied, str):
                            child[parameter] = supplied.replace("{spell}", "{_ms}")
                else:
                    rewrite(child, key)
        elif isinstance(value, list):
            for child in value:
                rewrite(child, parent)

    for name in ("cast_spell", "cast_spell_outside_combat", "begin_srd_spell_action"):
        tool = runtime["tools"][name]
        rewrite(tool["effects"])
        tool["effects"][:0] = [
            _set("_ms", "spell"),
            _when("spell in definitions.srd_lite_aliases",
                  [_set("_ms", "definitions.srd_lite_aliases[spell]")]),
        ]


def _target_validation():
    return _tool(
        "Qualify an actual touched mobility recipient before any casting expenditure.",
        [_entity("caster_state"), _entity("target_state"), _lasting()],
        [
            _require("target_state.is_alive == true and (target_state.creature_kind == 'character' or target_state.creature_kind == 'monster')", "Mobility casting needs a living creature."),
            _call("resolve_srd_scene_geometry_relation", {
                "actor_state": "{_caster_state_id}", "target_state": "{_target_state_id}",
                "lasting_state": "{_lasting_state_id}",
            }, ("_scene_distance", "_scene_same_plane", "_scene_line_of_sight")),
            _require(
                "_scene_distance <= 5 and _scene_same_plane == true and _scene_line_of_sight == true",
                "Every mobility recipient must be within actual touch range on a clear shared plane.",
            ),
        ],
    )


def _cast_prelude():
    """Keep selected shape separate from the saved-mode slot waiver."""
    shape = [_set("_lite_mobility_level", 1)]
    for level in range(1, 10):
        effect = _set("_lite_mobility_level", level)
        effect["condition"] = f"slot_resource == 'spell_slot_{level}'"
        shape.append(effect)
    return [
        _set("_lite_mobility_free", False),
        _set("_grant_source", "''"),
        _when(SCOPED, [
            _set("_lite_mobility_free", "caster != null and combat.encounter_active != true and combat.turn_active != true"),
            *shape,
            _when("caster == null", [
                _require("caster_state.spell_definitions != null and spell in caster_state.spell_definitions", "The creature needs its actual saved mobility spell definition."),
                _set("_lite_mobility_level", "caster_state.spell_definitions[spell].casting_level"),
            ]),
            _require(
                "slot_resource != 'ritual' or _lite_mobility_free == true",
                "A mobility Ritual resource is available only in saved free exploration; select a slot during turns.",
            ),
            _require("slot_resource != 'cantrip'", "A level-one mobility spell cannot use a cantrip resource."),
            _require(
                "caster == null or _lite_mobility_level == 1 or (caster_state.resources != null and slot_resource in caster_state.resources)",
                "The selected mobility level needs an actual saved slot resource.",
            ),
            _require(
                "caster == null or _lite_mobility_level == 1 or ((caster_state.resource_maxima == null and caster_state.resources[slot_resource] > 0) or (caster_state.resource_maxima != null and slot_resource in caster_state.resource_maxima and caster_state.resource_maxima[slot_resource] > 0))",
                "The selected mobility level needs positive saved capacity. A zero maximum is locked; depleted legacy slots without a saved maximum have unknown capacity.",
            ),
            _set("_lite_mobility_targets", []),
            _when("targets != null and len(targets) > 0", [_set("_lite_mobility_targets", "targets")]),
            _when("targets == null or len(targets) == 0", [_push("_lite_mobility_targets", "_target_id")]),
            _require("spell != 'longstrider' or len(_lite_mobility_targets) <= _lite_mobility_level", "The selected Longstrider level cannot affect that many creatures."),
            _require("spell != 'jump' or len(_lite_mobility_targets) == 1", "Enhance Leap affects exactly one creature at every selected level."),
            _set("_lite_mobility_seen", []),
            _each("_lite_mobility_targets", "_lite_mobility_target", [
                _require("_lite_mobility_target not in _lite_mobility_seen", "Choose distinct mobility recipients."),
                _push("_lite_mobility_seen", "_lite_mobility_target"),
                _call("validate_srd_lite_mobility_target", {
                    "caster_state": "{_caster_state_id}", "target_state": "{_lite_mobility_target}",
                    "lasting_state": "{_lasting_state_id}",
                }),
            ]),
        ]),
    ]


def _bind_casting(runtime):
    tools = runtime["tools"]
    tools["validate_srd_lite_mobility_target"] = _target_validation()
    for name in ("cast_spell", "cast_spell_outside_combat", "begin_srd_spell_action"):
        tool = tools[name]
        for condition in tool["pre_conditions"]:
            if "(casting_mode == 'ritual') == (slot_resource == 'ritual')" in condition["condition"]:
                condition["condition"] = f"({condition['condition']}) or ({PAYMENT_SCOPED})"
            if "wizard_spellbook" in condition["condition"]:
                condition["condition"] = condition["condition"].replace("casting_mode == 'ritual'", f"casting_mode == 'ritual' and not ({PAYMENT_SCOPED})")
        if name == "cast_spell_outside_combat":
            continue
        tool["effects"][:0] = _cast_prelude()
        for effects in list(lists(tool["effects"])):
            for index in range(len(effects)-1, -1, -1):
                effect = effects[index]
                setter = effect.get("set", {})
                if setter.get("field") == "_ritual_cast":
                    setter["value"] = f"casting_mode == 'ritual' and not ({PAYMENT_SCOPED})"
                if setter.get("field") == "_cast_resource" and setter.get("value") == "slot_resource":
                    effects.insert(index+1, _when("_lite_mobility_free == true", [_set("_cast_resource", "'no_slot'")]))
                if "condition" in effect and "casting_mode == 'ritual' or" in effect["condition"]:
                    effect["condition"] = effect["condition"].replace("casting_mode == 'ritual'", f"(casting_mode == 'ritual' and not ({PAYMENT_SCOPED}))")
                required = effect.get("require", {})
                if "casting_mode == 'ordinary' and (_spell_definition.casting_time" in required.get("condition", ""):
                    required["condition"] = required["condition"].replace("casting_mode == 'ordinary'", f"(casting_mode == 'ordinary' or (({PAYMENT_SCOPED}) and _lite_mobility_free == true))")
                if effect.get("call") == "cast_spell_multi_target_timed_speed_bonus":
                    effect["call"] = "cast_srd_lite_longstrider"
                    effect["params"]["targets"] = "{_lite_mobility_targets}"
                    effect["params"]["preparation_source"] = "{_grant_source}"
                if effect.get("call") == "validate_srd_spell_grants":
                    effect.setdefault("export_context", {})["_grant_source"] = "_grant_source"
                if effect.get("call") == "cast_spell_timed_actor_mobility" and "jump" in runtime["definitions"]["spells"]:
                    leap = deepcopy(effect)
                    leap["call"] = "cast_srd_lite_enhance_leap"
                    leap["params"]["targets"] = "{_lite_mobility_targets}"
                    effects[index] = {"condition": "spell == 'jump'", "on_true": [leap], "on_false": [effect]}
        indices = [i for i,e in enumerate(tool["effects"]) if e.get("set",{}).get("field") == "_slot_level"]
        if indices:
            tool["effects"].insert(indices[-1]+1, _when("_lite_mobility_free == true", [_set("_slot_level", "_lite_mobility_level")]))
        if name == "cast_spell":
            tool["effects"].append(_when(PAYMENT_SCOPED, [
                _push("_spell_card_summary", {"label": "'Selected spell level'", "value": "_slot_level"}),
                _when("_lite_mobility_free == true", [_push("_spell_card_summary", {
                    "label": "'Casting mode'", "value": "'Ritual in free exploration; no spell slot spent'"})]),
                _when("_lite_mobility_free != true", [_push("_spell_card_summary", {
                    "label": "'Casting mode'", "value": "'Ordinary casting; saved resource costs apply'"})]),
            ]))


def _bind_longstrider(runtime):
    definitions = runtime["definitions"]
    definitions["spells"]["longstrider"]["duration_rounds"] = 0
    definitions["spell_casting"]["longstrider"].update(
        components=["verbal", "somatic"], material="", material_parts=[],
        material_consumed=False, material_has_cost=False, focus_substitution=False,
        ritual=True, duration="Until genuine long rest or recipient death",
    )
    schema = runtime["definition_row_schemas"]["definitions.spells"]["variants"]["multi_target_timed_speed_bonus"]
    schema["properties"]["duration_rounds"] = {"type": "integer", "enum": [0]}
    tools = runtime["tools"]
    installer = deepcopy(tools["install_srd_timed_speed_bonus"])
    tools["install_srd_lite_longstrider"] = installer
    installer["parameters"].append(_param("preparation_source", required=False, default=""))
    for parameter in installer["parameters"]:
        if parameter["name"] == "duration_rounds":
            parameter["minimum"] = 0
        if parameter["name"] == "caster_state_id":
            # Entity parameters expose a separate scalar ID context binding.
            parameter["resolve"] = "entity"
    for effects in lists(installer["effects"]):
        for effect in effects:
            value = effect.get("list_push", {}).get("value")
            if isinstance(value, dict) and value.get("kind") == "'speed_bonus'":
                value.update(caster="_caster_state_id_id", owner="'__long_rest__'", phase="'long_rest'", turn=0,
                    expires_tick=None, lite_until_long_rest=True, preparation_source="preparation_source")
    index = next(i for i,e in enumerate(installer["effects"]) if "require" in e)
    installer["effects"][index:index] = [
        _set("_lite_speed_kept", []),
        _each("_timed_effects", "_lite_speed_existing", [
            {"list_push": {"list": "_lite_speed_kept", "value": "_lite_speed_existing"},
             "condition": "not (_lite_speed_existing.kind == 'speed_bonus' and _lite_speed_existing.spell == 'longstrider' and _lite_speed_existing.lite_until_long_rest == true and _lite_speed_existing.target == _target_state_id and _lite_speed_existing.caster == _caster_state_id_id and _lite_speed_existing.preparation_source == preparation_source)"},
        ]),
        _set("_timed_effects", "_lite_speed_kept"),
    ]
    caster = deepcopy(tools["cast_spell_multi_target_timed_speed_bonus"])
    tools["cast_srd_lite_longstrider"] = caster
    caster["parameters"].append(_param("preparation_source", required=False, default=""))
    for parameter in caster["parameters"]:
        if parameter["name"] == "duration_rounds":
            parameter["minimum"] = 0
    caster["pre_conditions"] = [c for c in caster["pre_conditions"] if "caster_can_touch_all_targets" not in c["condition"]]
    for effects in lists(caster["effects"]):
        for effect in effects:
            if effect.get("call") == "install_srd_timed_speed_bonus":
                effect["call"] = "install_srd_lite_longstrider"
                effect["params"]["preparation_source"] = "{preparation_source}"
            if effect.get("call") == "record_srd_spell_target_result":
                effect["params"]["detail"] = "Speed increases by 10 feet until genuine long rest or recipient death. Identical bonuses do not stack."


def _bind_leap(runtime):
    definitions = runtime["definitions"]
    definitions["spells"]["jump"].update(
        casting_time="action", base_targets=1, targets_per_slot_level=0,
        duration_rounds=10, concentration=False, flying_speed=0,
        jump_distance=0, jump_cost=0,
    )
    definitions["spell_casting"]["jump"].update(
        name="Enhance Leap", casting_time="action", ritual=True,
        components=["verbal", "somatic"], material="", material_parts=[],
        material_consumed=False, material_has_cost=False, focus_substitution=False,
        duration="10 turns; ordinary jump reach tripled",
    )
    tools = runtime["tools"]
    installer = deepcopy(tools["install_srd_timed_actor_mobility"])
    tools["install_srd_lite_enhance_leap"] = installer
    for effects in lists(installer["effects"]):
        for index, effect in enumerate(effects):
            value = effect.get("list_push", {}).get("value")
            if isinstance(value, dict) and value.get("kind") == "'actor_mobility'":
                value.update(mobility="'jump_multiplier'", magnitude=3, jump_cost=0)
    # Recasting the same owned recipient refreshes one source rather than multiplying it.
    index = next(i for i,e in enumerate(installer["effects"]) if "require" in e)
    installer["effects"][index:index] = [
        _set("_lite_leap_kept", []),
        _each("_mobility_effects", "_lite_leap_existing", [
            {"list_push": {"list": "_lite_leap_kept", "value": "_lite_leap_existing"},
             "condition": "not (_lite_leap_existing.kind == 'actor_mobility' and _lite_leap_existing.spell == 'jump' and _lite_leap_existing.mobility == 'jump_multiplier' and _lite_leap_existing.target == _target_state_id and _lite_leap_existing.caster == _caster_state_id)"},
        ]),
        _set("_mobility_effects", "_lite_leap_kept"),
    ]
    caster = deepcopy(tools["cast_spell_timed_actor_mobility"])
    tools["cast_srd_lite_enhance_leap"] = caster
    touch_assertions = 0
    for effects in list(lists(caster["effects"])):
        for effect in list(effects):
            if "caster_can_touch_all_targets" in effect.get("require", {}).get("condition", ""):
                effects.remove(effect)
                touch_assertions += 1
                continue
            if effect.get("call") == "install_srd_timed_actor_mobility":
                effect["call"] = "install_srd_lite_enhance_leap"
            setter = effect.get("set", {})
            if setter.get("field") == "_mobility_brief" and "Jump up to" in str(setter.get("value")):
                setter["value"] = "'Ordinary jump reach tripled'"
            if setter.get("field") == "_mobility_detail" and "Once on each" in str(setter.get("value")):
                setter["value"] = "'Ordinary jump reach is tripled for 10 turns. Each foot moved spends ordinary movement once; run-up and other independent modifiers still apply.'"
            elif setter.get("field") == "_mobility_detail" and " Duration:" in str(setter.get("value")):
                effects.remove(effect)
    assert touch_assertions == 1, "Actual target geometry replaces this private touch claim."
    jump = tools["derive_srd_jump_distance"]
    if not any(p["name"] == "lasting_state" for p in jump["parameters"]):
        jump["parameters"].append(_lasting())
    jump["effects"].extend([
        _set("_lite_jump_factor", 1), _set("_lite_jump_sources", []),
        _when("lasting_state.turn_effects != null", [_set("_lite_jump_sources", "lasting_state.turn_effects")]),
        _each("_lite_jump_sources", "_lite_jump_source", [
            _when("_lite_jump_source.kind == 'actor_mobility' and _lite_jump_source.spell == 'jump' and _lite_jump_source.mobility == 'jump_multiplier' and _lite_jump_source.target == _actor_state_id and _lite_jump_source.expires_tick != null and lasting_state.lasting_tick != null and lasting_state.lasting_tick + 0.000000001 < _lite_jump_source.expires_tick", [
                _set("_lite_jump_factor", "max(_lite_jump_factor, _lite_jump_source.magnitude)"),
            ]),
        ]),
        _set("_jump_maximum", "_jump_maximum * _lite_jump_factor"),
    ])


def _longstrider_retirement():
    return _tool(
        "Retire only the selected actual Longstrider owners and reproject their recipients.",
        [_lasting(), _param("caster_id", resolve="entity", required=False, default=None),
         _param("target_id", resolve="entity", required=False, default=None),
         _param("preparation_source", required=False, default=""), _param("reason")],
        [
            _set("_lite_long_before", []), _set("_lite_long_kept", []), _set("_lite_long_targets", []),
            _when("lasting_state.turn_effects != null", [_set("_lite_long_before", "lasting_state.turn_effects")]),
            _each("_lite_long_before", "_lite_long_source", [
                {"condition": "_lite_long_source.kind == 'speed_bonus' and _lite_long_source.spell == 'longstrider' and _lite_long_source.lite_until_long_rest == true and ((reason == 'unprepare' and _lite_long_source.caster == _caster_id_id and _lite_long_source.preparation_source == preparation_source) or (reason == 'death' and _lite_long_source.target == _target_id_id) or (reason == 'self_rest' and _lite_long_source.target == _target_id_id and _lite_long_source.caster == _target_id_id))",
                 "on_true": [
                     {"list_push": {"list": "_lite_long_targets", "value": "_lite_long_source.target"}, "condition": "_lite_long_source.target not in _lite_long_targets"},
                 ], "on_false": [_push("_lite_long_kept", "_lite_long_source")]},
            ]),
            _when("len(_lite_long_targets) > 0", [
                _set("lasting_state.turn_effects", "_lite_long_kept"),
                _each("_lite_long_targets", "_lite_long_target", [
                    _call("project_srd_lite_speed_bonus", {"target_state": "{_lite_long_target}", "lasting_state": "{_lasting_state_id}"}),
                ]),
            ]),
        ],
    )


def _speed_bonus_projection(runtime):
    """Reuse the core's exact bonus calculation without unrelated damage joins."""
    original = runtime["tools"]["project_srd_turn_modifiers"]
    effects = []
    fields = {"_previous_speed_bonus", "_speed_bonus", "_speed_effects"}
    for effect in original["effects"]:
        if effect.get("set", {}).get("field") in fields:
            effects.append(deepcopy(effect))
        elif effect.get("condition") in {
            "target_state.spell_speed_bonus != null", "lasting_state.turn_effects != null",
            "_previous_speed_bonus != _speed_bonus",
        }:
            effects.append(deepcopy(effect))
        elif effect.get("for_each", {}).get("collection") == "_speed_effects":
            bonus = [branch for branch in effect["for_each"]["do"]
                if branch.get("condition") == "_speed_effect.kind == 'speed_bonus' and _speed_effect.target == _target_state_id"]
            assert len(bonus) == 1
            effects.append(_each("_speed_effects", "_speed_effect", deepcopy(bonus)))
        elif effect.get("condition", "").startswith("combat.turn_active == true") and "_previous_speed_bonus" in effect["condition"]:
            effects.append(_when(
                "combat.turn_active == true and combat.active_actor == _target_state_id and _previous_speed_bonus != _speed_bonus",
                deepcopy(effect["on_true"]),
            ))
    assert len(effects) == 8, "Preserve the admitted core speed-bonus fragment."
    return _tool("Project the retained maximum speed bonus and refresh its actual movement budget.",
        deepcopy(original["parameters"]), effects)


def _bind_longstrider_sources(runtime):
    tools = runtime["tools"]
    validator = tools["validate_srd_spell_grants"]
    # Character creation owns the validator's first two profile-binding effects.
    validator["effects"].insert(2, _set("_grant_source", "''"))
    for effects in lists(validator["effects"]):
        for effect in effects:
            if "spell in _grant_spells" in effect.get("condition", "") and "_ritual_book_match" in effect["condition"]:
                effect["on_true"].append(_set("_grant_source", "_grant.source"))
    tools["project_srd_lite_speed_bonus"] = _speed_bonus_projection(runtime)
    tools["retire_srd_lite_longstrider"] = _longstrider_retirement()
    tools["prepare_srd_spells"]["effects"].append(
        _when("(source == 'wizard' or source == 'druid') and 'longstrider' not in spells", [
            _call("retire_srd_lite_longstrider", {"caster_id": "{_caster_state_id}",
                "preparation_source": "{source}", "reason": "unprepare"}),
        ])
    )


def _bind_longstrider_self_rest(runtime):
    runtime["tools"]["complete_srd_rest"]["effects"].append(
        _when("target_state.rest_kind == 'long'", [
            _call("retire_srd_lite_longstrider", {
                "target_id": "{_target_state_id}", "lasting_state": "{_lasting_state_id}",
                "reason": "self_rest",
            }),
        ])
    )


def _bind_longstrider_lifecycle(runtime):
    tools = runtime["tools"]
    # There is no group-rest completion owner. Do not invent separate-rest expiry.
    tools["complete_srd_rest"]["effects"][:0] = [
        _when("target_state.rest_kind == 'long' and lasting_state.turn_effects != null", [
            _set("_lite_rest_sources", "lasting_state.turn_effects"),
            _each("_lite_rest_sources", "_lite_rest_source", [
                _require(
                    "not (_lite_rest_source.kind == 'speed_bonus' and _lite_rest_source.spell == 'longstrider' and _lite_rest_source.lite_until_long_rest == true and _lite_rest_source.caster != _lite_rest_source.target and (_lite_rest_source.caster == _target_state_id or _lite_rest_source.target == _target_state_id))",
                    "Separate caster or recipient Long Rest completion is unsupported while this Longstrider remains active. Dismiss its exact cast before completing the separate rest.",
                ),
            ]),
        ]),
    ]
    death_branches = 0
    for effects in lists(tools["record_srd_actual_death"]["effects"]):
        for effect in effects:
            if effect.get("condition") == "target_state.is_alive == false":
                effect["on_true"].insert(0, _call("retire_srd_lite_longstrider", {
                    "target_id": "{_target_state_id}", "lasting_state": "{_lasting_state_id}",
                    "reason": "death",
                }))
                death_branches += 1
    assert death_branches == 1, "Preserve the existing ordinary actual-death owner."
    existing_expiry = "_dismiss_effect.expires_tick != null and lasting_state.lasting_tick < _dismiss_effect.expires_tick"
    bounded_expiry = f"(({existing_expiry}) or _lite_dismiss_rest_span == true)"
    dismissal_branches = 0
    for effects in list(lists(tools["dismiss_srd_spell"]["effects"])):
        for index, effect in enumerate(list(effects)):
            lifetime = effect.get("set", {})
            if lifetime.get("field") == "_lite_dismiss_lifetime" and existing_expiry in str(lifetime.get("value")):
                # Mage Armour already factors the unchanged source/cast guard
                # into this per-row lifetime binding. Compose only Longstrider's
                # typed lifetime, retaining that exact ownership predicate.
                lifetime["value"] = "(" + lifetime["value"] + ") or (" + (
                    "_dismiss_effect.kind == 'speed_bonus' and _dismiss_effect.spell == 'longstrider' and "
                    "_dismiss_effect.lite_until_long_rest == true and _dismiss_effect.expires_tick == null"
                ) + ")"
                dismissal_branches += 1
                continue
            condition = effect.get("condition", "")
            if condition.startswith("_dismiss_effect.cast_id == cast_id") and existing_expiry in condition:
                effect["condition"] = condition.replace(existing_expiry, bounded_expiry)
                effects.insert(index, _set("_lite_dismiss_rest_span",
                    "_dismiss_effect.kind == 'speed_bonus' and _dismiss_effect.spell == 'longstrider' and _dismiss_effect.lite_until_long_rest == true and _dismiss_effect.expires_tick == null"))
                dismissal_branches += 1
    assert dismissal_branches == 1, "Retain exact cast and source ownership at dismissal."


def bind_mobility_support(runtime):
    """Compose the enabled mobility profiles; original full-SRD input is unchanged."""
    _bind_casting(runtime)
    _bind_longstrider(runtime)
    if "jump" in runtime["definitions"]["spells"]:
        _bind_leap(runtime)
    _bind_longstrider_sources(runtime)
    _bind_longstrider_self_rest(runtime)
    _bind_longstrider_lifecycle(runtime)
    _bind_spell_identity(runtime)
