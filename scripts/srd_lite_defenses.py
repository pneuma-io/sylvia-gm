"""Scoped Lite defense draft; compose only into the isolated variant runtime.

No full-SRD generator is changed. Rest ownership is an engine adaptation:
successful recipient rest retires its record; separate off-party/NPC rest
fidelity (contract M1) remains unproved. The main owner must join per-ray
saved continuation before claiming complete Shield support for Scorching Ray.
"""

from copy import deepcopy

from .srd_control_behaviors import _lasting, _param
from .srd_lite_profiles import lists
from .srd_timed_actor_defenses import _call, _entity, _require, _set, _tool, _when


MAGE = "mage_armor"
REST_MAGE = (
    "{record}.kind == 'timed_actor_defense' and {record}.spell == 'mage_armor' "
    "and {record}.lifetime_kind == 'until_long_rest'"
)


def _each(collection, item, effects):
    return {"for_each": {"collection": collection, "as": item, "do": effects}}


def _push(collection, value):
    return {"list_push": {"list": collection, "value": value}}


def _named_parameter(owner, parameter):
    if any(isinstance(p, dict) and p.get("name") == parameter["name"] for p in owner["parameters"]):
        raise ValueError("Defense draft already bound: " + parameter["name"])
    owner["parameters"].append(parameter)


def _private_source_parameter():
    return _param("lite_prepared_source", required=False, default="", model_hidden=True)


def _walk_effects(owner):
    # Snapshot lists before wrapping a call, so its new fallback is not wrapped twice.
    for effects in list(lists(owner["effects"])):
        for effect in list(effects):
            yield effects, effect


def _mage_admission_tool():
    return _tool(
        "Qualify the complete Mage Armour selection from saved bodies before spending.",
        [
            _entity("caster_state"),
            _lasting(),
            {"name": "caster", "type": "string", "resolve": "participant", "required": False},
            {"name": "target_state", "type": "string", "resolve": "entity", "required": False},
            {"name": "target_character", "type": "string", "resolve": "participant", "required": False},
            _param("targets", "array", items={"type": "string"}, required=False, default=[]),
            _param("target_willing", "boolean", required=False, default=False),
            _param("all_targets_willing", "boolean", required=False, default=False),
        ],
        [
            _set("_lite_mage_targets", []),
            _set("_lite_mage_selected_targets", []),
            _when("targets != null", [_set("_lite_mage_selected_targets", "targets")]),
            _set("_lite_mage_caster_profile", None),
            _when("caster != null", [_set("_lite_mage_caster_profile", "_caster_id")]),
            _when("caster == null and caster_state.creature_kind == 'character'", [
                _set("_lite_mage_caster_profile", "_caster_state_id"),
            ]),
            _when("len(_lite_mage_selected_targets) == 0", [
                _require("target_state != null", "Mage Armour needs one actual recipient."),
                _require("target_willing == true", "The Mage Armour recipient must be willing."),
                _push("_lite_mage_targets", "_target_state_id"),
            ]),
            _when("len(_lite_mage_selected_targets) > 0", [
                _require("all_targets_willing == true", "The Mage Armour recipient must be willing."),
                _set("_lite_mage_targets", "_lite_mage_selected_targets"),
            ]),
            _require("len(_lite_mage_targets) == 1", "Mage Armour has exactly one recipient at every slot level."),
            _each("_lite_mage_targets", "_lite_mage_target", [
                _call("resolve_srd_scene_geometry_relation", {
                    "actor_state": "{_caster_state_id}", "target_state": "{_lite_mage_target}",
                    "lasting_state": "{_lasting_state_id}",
                }, ("_scene_distance", "_scene_same_plane", "_scene_line_of_sight")),
                _require(
                    "_scene_distance <= 5 and _scene_same_plane == true and _scene_line_of_sight == true",
                    "Mage Armour needs actual touch range, shared plane and a clear path.",
                ),
                _call("validate_srd_actor_defense_target", {
                    "spell": MAGE, "target_state": "{_lite_mage_target}",
                    "caster_state": "{_caster_state_id}", "caster": "{_lite_mage_caster_profile}",
                    "target_character": "{_target_character_id}",
                }),
            ]),
        ],
    )


def _bind_mage_admission(tools):
    tools["validate_srd_lite_mage_armor_targets"] = _mage_admission_tool()
    for name in ("cast_spell", "begin_srd_spell_action"):
        tools[name]["effects"].insert(0, _when("spell == 'mage_armor'", [
            _call("validate_srd_lite_mage_armor_targets", {
                "caster_state": "{_caster_state_id}", "caster": "{_caster_id}",
                "target_state": "{_target_id}", "target_character": "{_target_character_id}",
                "targets": "{targets}", "target_willing": "{target_willing}",
                "all_targets_willing": "{all_targets_willing}",
                "lasting_state": "{_lasting_state_id}",
            }),
        ]))
    # The root's optional public claims can be absent/None, whereas this family
    # has required boolean parameters. Forward the already-qualified facts for
    # Mage alone. Preserve the entire original call for every other profile.
    found = 0
    for effects, effect in _walk_effects(tools["cast_spell"]):
        if effect.get("call") == "cast_spell_timed_actor_defense":
            qualified = deepcopy(effect)
            qualified["params"].update(caster_can_touch_target=True,
                                       caster_can_touch_all_targets=True,
                                       target_willing=True, all_targets_willing=True)
            effects[effects.index(effect)] = {
                "condition": "spell == 'mage_armor'",
                "on_true": [qualified], "on_false": [deepcopy(effect)],
            }
            found += 1
    if found != 1:
        raise ValueError("Actor-defense public dispatch seam changed")


def _bind_mage_source(tools):
    validator = tools["validate_srd_spell_grants"]
    # Character-file composition owns the first two profile/proposal effects.
    validator["effects"].insert(2, _set("_lite_admitted_grant_source", "''"))
    matches = 0
    for effects, effect in _walk_effects(validator):
        if effect.get("set", {}).get("field") == "_grant_matches" and effect["set"]["value"] == "_grant_matches + 1":
            effects.insert(effects.index(effect) + 1, _set("_lite_admitted_grant_source", "_grant.source"))
            matches += 1
    if matches != 1:
        raise ValueError("Exact saved grant-selection seam changed")
    cast = tools["cast_spell"]
    cast["effects"].insert(0, _set("_lite_mage_cast_source", "''"))
    joined = 0
    for _, effect in _walk_effects(cast):
        if effect.get("call") == "validate_srd_spell_grants" and effect.get("params", {}).get("spell") == "{spell}":
            effect.setdefault("export_context", {})["_lite_mage_cast_source"] = "_lite_admitted_grant_source"
            joined += 1
        if effect.get("call") == "cast_spell_timed_actor_defense":
            effect["params"]["lite_prepared_source"] = "{_lite_mage_cast_source}"
    if joined != 1:
        raise ValueError("Ordinary PC spell-grant export seam changed")
    family = tools["cast_spell_timed_actor_defense"]
    installer = tools["install_srd_timed_actor_defense"]
    _named_parameter(family, _private_source_parameter())
    _named_parameter(installer, _private_source_parameter())
    for _, effect in _walk_effects(family):
        if effect.get("call") == "install_srd_timed_actor_defense":
            effect["params"]["lite_prepared_source"] = "{lite_prepared_source}"
    found_record = found_replacement = 0
    for effects, effect in _walk_effects(installer):
        if effect.get("set", {}).get("field") == "_actor_defense_record" and isinstance(effect["set"]["value"], dict):
            # Extend the real record, retaining cast/source/level stamps and
            # every downstream registration and projection callback.
            effects.insert(effects.index(effect) + 1, _when("spell == 'mage_armor'", [
                _set("_lite_mage_lifetime", {
                    "owner": "'__long_rest__'", "phase": "'long_rest'", "turn": 0,
                    "lifetime_kind": "'until_long_rest'", "expires_tick": None,
                    "rest_owner_id": "_target_state_id", "lite_prepared_source": "lite_prepared_source",
                }),
                _set("_actor_defense_record", "merge(_actor_defense_record, _lite_mage_lifetime)"),
            ]))
            found_record += 1
        predicate = effect.get("condition", "")
        if "_prior_actor_defense.kind == 'timed_actor_defense'" in predicate and "_prior_actor_defense.target == _target_state_id" in predicate:
            effect["condition"] = predicate.replace(
                "_prior_actor_defense.target == _target_state_id",
                "_prior_actor_defense.target == _target_state_id and (spell != 'mage_armor' or _prior_actor_defense.lite_prepared_source == lite_prepared_source)",
            )
            found_replacement += 1
    if (found_record, found_replacement) != (1, 1):
        raise ValueError("Stamped actor-defense installer seam changed")


def _mage_retirement_tool():
    return _tool(
        "Retire only exact Mage Armour source dependencies after an accepted lifecycle operation.",
        [
            _param("recipient", required=False, default=""),
            _param("source_caster", required=False, default=""),
            _param("prepared_source", required=False, default=""),
            _param("by_rest", "boolean", required=False, default=False),
        ],
        [
            _set("_lite_mage_prior", []), _set("_lite_mage_remaining", []), _set("_lite_mage_affected", []),
            _when("state.turn_effects != null", [_set("_lite_mage_prior", "state.turn_effects")]),
            _each("_lite_mage_prior", "_lite_mage_record", [
                _set("_lite_mage_retire", False),
                _when(REST_MAGE.format(record="_lite_mage_record"), [
                    _set("_lite_mage_retire",
                         "(by_rest == true and recipient != '' and _lite_mage_record.rest_owner_id == recipient) or "
                         "(by_rest != true and source_caster != '' and prepared_source == 'wizard' and "
                         "_lite_mage_record.source_caster_id == source_caster and _lite_mage_record.lite_prepared_source == prepared_source)"),
                ]),
                _when("_lite_mage_retire == true and _lite_mage_record.target not in _lite_mage_affected", [
                    _push("_lite_mage_affected", "_lite_mage_record.target"),
                ]),
                _when("_lite_mage_retire != true", [_push("_lite_mage_remaining", "_lite_mage_record")]),
            ]),
            _when("len(_lite_mage_affected) > 0", [_set("state.turn_effects", "_lite_mage_remaining")]),
            _each("_lite_mage_affected", "_lite_mage_refresh", [
                _call("refresh_srd_timed_condition_target", {"target_state": "{_lite_mage_refresh}", "lasting_state": "{_lasting_state_id}"}),
            ]),
        ],
    )


def _bind_mage_lifecycle(tools):
    tools["retire_srd_lite_mage_armor_sources"] = _mage_retirement_tool()
    for reader, record in (("resolve_srd_timed_actor_defenses", "_defense_effect"),
                           ("resolve_srd_timed_actor_attack_defenses", "_attack_defense")):
        wrapped = 0
        for effects, effect in _walk_effects(tools[reader]):
            if effect.get("call") == "check_srd_timed_actor_defense_active":
                effects[effects.index(effect)] = {
                    "condition": REST_MAGE.format(record=record),
                    "on_true": [_set("_actor_defense_active", True)],
                    "on_false": [deepcopy(effect)],
                }
                wrapped += 1
        if wrapped != 1:
            raise ValueError("Actor-defense active-reader seam changed: " + reader)
    tools["complete_srd_rest"]["effects"].append(_when(
        "target_state.rest_kind == 'long' and target_state.rest_phase == 'complete'",
        [_call("retire_srd_lite_mage_armor_sources", {"recipient": "{_target_state_id}", "by_rest": True, "lasting_state": "{_lasting_state_id}"})],
    ))
    tools["prepare_srd_spells"]["effects"].append(_when(
        "source == 'wizard' and 'mage_armor' not in spells",
        [_call("retire_srd_lite_mage_armor_sources", {
            "source_caster": "{_caster_state_id}", "prepared_source": "{source}",
            "lasting_state": "{_lasting_state_id}",
        })],
    ))
    expiry = "_dismiss_effect.expires_tick != null and lasting_state.lasting_tick < _dismiss_effect.expires_tick"
    changed = 0
    for effects, effect in _walk_effects(tools["dismiss_srd_spell"]):
        condition = effect.get("condition", "")
        if "_dismiss_effect.cast_id == cast_id" in condition and expiry in condition:
            # Parenthesize the entire lifetime alternative; exact source guards
            # and other family exclusions must still apply to both branches.
            effects.insert(effects.index(effect), _set(
                "_lite_dismiss_lifetime", "(" + expiry + ") or (" + REST_MAGE.format(record="_dismiss_effect") + ")"))
            effect["condition"] = condition.replace(expiry, "_lite_dismiss_lifetime == true")
            changed += 1
    if changed != 1:
        raise ValueError("Exact-source dismissal lifetime seam changed")
    family = tools["cast_spell_timed_actor_defense"]
    for _, effect in _walk_effects(family):
        if effect.get("call") == "record_srd_spell_target_result" and "Defense lasts" in effect.get("params", {}).get("detail", ""):
            original = deepcopy(effect)
            effect.clear()
            effect.update({
                "condition": "spell == 'mage_armor'",
                "on_true": [_call("record_srd_spell_target_result", {
                    **original["params"],
                    "detail": "Mage Armour uses the strongest actual base AC, including its 13 + Dexterity formula. "
                              "It lasts until an actual successful recipient long rest, armor donning, exact dismissal, "
                              "or removal from this cast's normal Wizard prepared source. Shields remain compatible.",
                }, tuple(original.get("export_context", {})))],
                "on_false": [original],
            })


def _shield_eligibility_tool():
    return _tool(
        "Read whether Shield can change the exact saved hostile hit or missile allocation.",
        [_entity("caster_state"), _param("reaction_trigger_id")],
        [
            _require("state.pending_srd_actions != null and reaction_trigger_id in state.pending_srd_actions",
                     "Shield needs an actual saved reaction event."),
            _set("_lite_shield_event", "state.pending_srd_actions[reaction_trigger_id]"),
            _set("_lite_shield_eligible", False),
            _call("resolve_srd_reaction_shield", {"target_state": "{_caster_state_id}"}, ("_shield_active",)),
            _when(
                "_lite_shield_event.actor_state_id != _caster_state_id and _lite_shield_event.kind == 'spell_cast' and "
                "_lite_shield_event.spell == 'magic_missile' and (_lite_shield_event.parameters.target == _caster_state_id or "
                "(_lite_shield_event.parameters.projectile_targets != null and _caster_state_id in _lite_shield_event.parameters.projectile_targets))",
                [_set("_lite_shield_eligible", "_shield_active != true")],
            ),
            _when(
                "_lite_shield_event.actor_state_id != _caster_state_id and _lite_shield_event.target_state_id == _caster_state_id and "
                "(_lite_shield_event.kind == 'weapon_attack' or _lite_shield_event.kind == 'spell_attack') and "
                "(_lite_shield_event.attack_outcome == 'hit' or _lite_shield_event.attack_outcome == 'critical_hit')",
                [
                    _set("_lite_shield_total", None), _set("_lite_shield_roll", None),
                    _set("_lite_shield_critical", True), _set("_lite_shield_condition_critical", False),
                    _set("_lite_shield_cover", 0),
                    _set("_lite_shield_hand_origin", "_lite_shield_event.actor_state_id"),
                    _when("_lite_shield_event.kind == 'weapon_attack'", [
                        _set("_lite_shield_total", "_lite_shield_event.attack_total"),
                        _set("_lite_shield_roll", "_lite_shield_event.attack_roll"),
                        _when("_lite_shield_event.context._critical_hit != null", [
                            _set("_lite_shield_critical", "_lite_shield_event.context._critical_hit"),
                        ]),
                        _when("_lite_shield_event.context._condition_critical_hit != null", [
                            _set("_lite_shield_condition_critical", "_lite_shield_event.context._condition_critical_hit"),
                        ]),
                    ]),
                    _when("_lite_shield_event.kind == 'spell_attack'", [
                        _set("_lite_shield_total", "_lite_shield_event.spell_attack_context._card_d20_total"),
                        _set("_lite_shield_roll", "_lite_shield_event.spell_attack_context._card_d20_roll"),
                        _when("_lite_shield_event.spell_attack_parameters.cover == 'half'", [
                            _set("_lite_shield_cover", 2),
                        ]),
                        _when("_lite_shield_event.spell_attack_context._spell_condition_critical_hit != null", [
                            _set("_lite_shield_condition_critical", "_lite_shield_event.spell_attack_context._spell_condition_critical_hit"),
                        ]),
                        _when("_lite_shield_event.spell_attack_parameters.cover == 'three_quarters'", [
                            _set("_lite_shield_cover", 5),
                        ]),
                        _when("_lite_shield_event.spell_attack_context._spell_critical_hit != null", [
                            _set("_lite_shield_critical", "_lite_shield_event.spell_attack_context._spell_critical_hit"),
                        ]),
                        _when("_lite_shield_event.spell_attack_parameters.force_source_id != null and "
                              "_lite_shield_event.spell_attack_parameters.force_source_id != ''", [
                            _set("_lite_shield_hand_origin", "_lite_shield_event.spell_attack_parameters.force_source_id"),
                        ]),
                    ]),
                    # Mirror the real completion's hand-cover input separately
                    # from Blade Barrier's final additive cover classification.
                    _set("_lite_shield_hand_existing_cover", "_lite_shield_cover"),
                    _when("_lite_shield_event.kind == 'spell_attack' and state.has_blade_barrier == true", [
                        _require("_lite_shield_event.spell_attack_parameters.force_source_id == null or "
                                 "_lite_shield_event.spell_attack_parameters.force_source_id == ''",
                                 "A created force attack needs its actual source-frame Blade Cover adapter."),
                        _call("resolve_srd_environment_blade_cover", {
                            "observer_subject": "{_lite_shield_event.actor_state_id}",
                            "target_subject": "{_caster_state_id}",
                        }, ("_environment_blade_cover", "_environment_blade_cover_supported")),
                        _require("_environment_blade_cover_supported == true",
                                 "Partly covered body rays need continuous Blade Cover qualification."),
                        _when("_environment_blade_cover == 'three_quarters'", [_set("_lite_shield_cover", 5)]),
                    ]),
                    _call("read_srd_creature_attitude", {
                        "actor_state": "{_lite_shield_event.actor_state_id}", "toward_state": "{_caster_state_id}",
                    }, ("_creature_attitude",)),
                    _set("_lite_shield_hostile", "_creature_attitude == 'hostile'"),
                    _when("caster_state.fighting_actor_ids != null and _lite_shield_event.actor_state_id in caster_state.fighting_actor_ids", [
                        _set("_lite_shield_hostile", True),
                    ]),
                    # Shield requires no sight/range test against its attacker.
                    # Read the established enemy_of scene fact without adding a
                    # new geometry admission failure to ordinary attack owners.
                    _when("state.spatial_scene != null", [
                        _each("state.spatial_scene.entities", "_lite_shield_body", [
                            _when("_lite_shield_body.active == true and "
                                  "_lite_shield_body.metadata.participant_ref == _lite_shield_event.actor_state_id and "
                                  "_lite_shield_body.metadata.enemy_of != null and _caster_state_id in _lite_shield_body.metadata.enemy_of", [
                                _set("_lite_shield_hostile", True),
                            ]),
                        ]),
                    ]),
                    _call("resolve_srd_effective_armor_class", {
                        "target_state": "{_caster_state_id}", "base_armor_class": "{caster_state.armor_class}",
                        "lasting_state": "{_lasting_state_id}",
                        "hand_cover_source": "{_lite_shield_hand_origin}",
                        "hand_existing_cover_bonus": "{_lite_shield_hand_existing_cover}",
                    }, ("_effective_armor_class",)),
                    _set("_lite_shield_effective_ac", "_effective_armor_class + _lite_shield_cover"),
                    _when("_lite_shield_total != null and _lite_shield_roll != null", [
                        _set("_lite_shield_eligible", "_lite_shield_hostile == true and _shield_active != true and "
                             "_lite_shield_roll.total != 1 and _lite_shield_roll.total != 20 and "
                             "(_lite_shield_critical == false or _lite_shield_condition_critical == true) and "
                             "_lite_shield_total >= _lite_shield_effective_ac and _lite_shield_total < _lite_shield_effective_ac + 5"),
                    ]),
                ],
            ),
        ],
    )


def _bind_shield(tools):
    tools["read_srd_lite_shield_eligibility"] = _shield_eligibility_tool()
    tools["validate_srd_reaction_spell_trigger"]["effects"].append(_when("spell == 'shield'", [
        _call("read_srd_lite_shield_eligibility", {
            "caster_state": "{_caster_state_id}", "reaction_trigger_id": "{reaction_trigger_id}",
            "lasting_state": "{_lasting_state_id}",
        }, ("_lite_shield_eligible",)),
        _require("_lite_shield_eligible == true", "Shield needs a saved hostile hit convertible by +5 AC, or actual Magic Missile allocation."),
    ]))
    # Offer consumers can inspect this saved field; affordability/grants/budget
    # still belong to the canonical reaction cast. It is never an authority token.
    tools["annotate_srd_lite_shield_window"] = _tool(
        "Annotate a genuine saved attack window with current Shield trigger eligibility.",
        [_param("pending_action_id")],
        [
            _set("_lite_offer_event", "state.pending_srd_actions[pending_action_id]"),
            _when("_lite_offer_event.status == 'open' and _lite_offer_event.target_state_id != null and "
                  "(_lite_offer_event.kind == 'weapon_attack' or _lite_offer_event.kind == 'spell_attack')", [
                _call("read_srd_lite_shield_eligibility", {
                    "caster_state": "{_lite_offer_event.target_state_id}", "reaction_trigger_id": "{pending_action_id}",
                    "lasting_state": "{_lasting_state_id}",
                }, ("_lite_shield_eligible",)),
                _set("state.pending_srd_actions.{pending_action_id}.lite_shield_eligible", "_lite_shield_eligible"),
            ]),
        ],
    )
    for name in ("begin_srd_character_attack", "begin_srd_creature_attack",
                 "continue_srd_character_attack_targeting", "continue_srd_creature_attack_targeting"):
        tools[name]["effects"].append(_when(
            "state.pending_srd_actions != null and _pending_action_id != null and _pending_action_id in state.pending_srd_actions",
            [_call("annotate_srd_lite_shield_window", {"pending_action_id": "{_pending_action_id}", "lasting_state": "{_lasting_state_id}"})],
        ))
    tools["cast_spell_attack_damage"]["effects"].append(_when("_spell_hit_window_deferred == true", [
        _call("annotate_srd_lite_shield_window", {"pending_action_id": "{_pending_action_id}", "lasting_state": "{_lasting_state_id}"}),
    ]))


def bind_defenses(runtime):
    """Compose after mobility drafts and before boundary/schema/exposure binding."""
    definitions = runtime["definitions"]
    definitions["spells"][MAGE]["duration_rounds"] = 0
    definitions["spell_casting"][MAGE].update(
        name="Mage Armour", components=["verbal", "somatic"], material="", material_parts=[],
        material_consumed=False, material_has_cost=False, focus_substitution=False,
        ritual=False, duration="Until successful long rest or owned source retirement",
    )
    # Variant-only schema admits the typed sentinel; other profiles retain all
    # existing positive finite durations and their original active-reader branch.
    duration = runtime["definition_row_schemas"]["definitions.spells"]["variants"]["timed_actor_defense"]["properties"]["duration_rounds"]
    duration["minimum"] = 0
    tools = runtime["tools"]
    _bind_mage_admission(tools)
    _bind_mage_source(tools)
    _bind_mage_lifecycle(tools)
    _bind_shield(tools)


def bind_defense_public_tools(runtime, record, tool_schema):
    """Run after Lite exposed-operation policy; enable the one owned Shield route."""
    name = "cast_srd_reaction_spell"
    owner = runtime["tools"][name]
    owner["effects"] = [effect for effect in owner["effects"] if effect.get("require", {}).get("error") !=
                        "This spell operation is pending or excluded in the SRD Lite foundation."]
    owner["effects"].insert(0, _require("spell == 'shield'", "Only the implemented Shield reaction uses this Lite route."))
    spell_param = next(p for p in owner["parameters"] if isinstance(p, dict) and p.get("name") == "spell")
    spell_param["type"] = "enum"
    spell_param["enum_values"] = ["shield"]
    owner.setdefault("metadata", {})["visible_to_player"] = True
    declaration = next(row for row in record["tools"] if row["name"] == name)
    declaration["input_schema"] = tool_schema(owner)

