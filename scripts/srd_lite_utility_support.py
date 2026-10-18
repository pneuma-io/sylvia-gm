"""Variant utility spells over the existing paid cast, saved senses and rest owners."""

from .srd_control_behaviors import _each, _entity, _lasting, _param, _push
from .srd_lite_profiles import lists
from .srd_timed_actor_defenses import _call, _require, _set, _tool, _when

UTILITY_SPELLS = {"darkvision", "speak_with_animals"}


def bind_utility_profiles(runtime):
    """Bind only independently authored, supported legacy profiles before Lite admission."""
    runtime["definitions"]["srd_lite_free_rituals"] = sorted(set(runtime["definitions"].get("srd_lite_free_rituals", [])) | {"longstrider", "jump", "enhance_leap", "speak_with_animals"})
    profile = runtime["definitions"]["spells"]["darkvision"]
    profile.update(darkvision_feet=40, duration_rounds=0)
    runtime["definition_row_schemas"]["definitions.spells"]["variants"]["timed_senses"]["properties"]["duration_rounds"]["minimum"] = 0
    runtime["definitions"]["spell_casting"]["darkvision"].update(
        components=["verbal", "somatic"], material="", material_parts=[],
        material_consumed=False, material_has_cost=False, focus_substitution=False,
        duration="Until the recipient completes a Long Rest; prepared Wizard/Druid sources require continued preparation",
    )
    runtime["definitions"]["spells"]["speak_with_animals"].update(duration_rounds=0, effect_rounds=0)
    schema = runtime["definition_row_schemas"]["definitions.spells"]["variants"]["information_spell"]
    for name in ("duration_rounds", "effect_rounds"):
        schema["properties"][name]["minimum"] = 0
    runtime["definitions"]["spell_casting"]["speak_with_animals"].update(
        components=["verbal", "somatic"], material="", material_parts=[],
        material_consumed=False, material_has_cost=False, focus_substitution=False,
        ritual=True, duration="Until the caster completes a Long Rest; Druid sources require continued preparation",
    )


def _validate_darkvision():
    """Resolve one real touched creature before accepting or completing a paid cast."""
    return _tool(
        "Validate Darkvision's single actual touched recipient.",
        [_entity("caster_state"), _entity("target_state"), _lasting()],
        [
            _require("target_state.is_alive == true and (target_state.creature_kind == 'character' or target_state.creature_kind == 'monster')", "Darkvision requires a living creature."),
            _call("resolve_srd_scene_geometry_relation", {
                "actor_state": "{_caster_state_id}", "target_state": "{_target_state_id}",
                "lasting_state": "{_lasting_state_id}",
            }, ("_scene_distance", "_scene_same_plane", "_scene_line_of_sight")),
            _require("_scene_distance <= 5 and _scene_same_plane == true and _scene_line_of_sight == true", "Darkvision requires actual touch range and a clear shared plane."),
        ],
    )


def _retire_utility():
    """Remove only this utility's source-owned rows at their real lifecycle boundary."""
    return _tool(
        "Retire matching utility sources after actual rest, death or unpreparation.",
        [_lasting(), _param("owner_id"), _param("reason"), _param("utility_spell", required=False, default=""), _param("preparation_source", required=False, default="")],
        [
            _set("_utility_kept", []), _set("_utility_rows", []),
            _when("lasting_state.lite_utility_effects != null", [_set("_utility_rows", "lasting_state.lite_utility_effects")]),
            _each("_utility_rows", "_utility_row", [
                _when("not ((_utility_row.spell == 'darkvision' or _utility_row.spell == 'speak_with_animals') and _utility_row.kind == 'lite_utility' and ((reason == 'unprepare' and _utility_row.spell == utility_spell and _utility_row.caster == owner_id and _utility_row.preparation_source == preparation_source) or ((reason == 'rest' or reason == 'death') and _utility_row.target == owner_id)))", [_push("_utility_kept", "_utility_row")]),
            ]),
            _set("lasting_state.lite_utility_effects", "_utility_kept"),
        ],
    )


def _install_utility():
    """Save a genuine paid cast instance without replacing native creature senses."""
    return _tool(
        "Install one source-owned utility grant until its real lifecycle boundary.",
        [_entity("caster_state"), _entity("target_state"), _lasting(), _param("cast_id"), _param("utility_spell"),
         _param("slot_level", "integer"), _param("preparation_source", required=False, default="")],
        [
            _require("cast_id != ''", "Darkvision requires the shared casting owner's actual instance identity."),
            _require("utility_spell == 'darkvision' or utility_spell == 'speak_with_animals'", "The utility installer requires an implemented profile."),
            _set("_utility_kept", []), _set("_utility_rows", []),
            _when("lasting_state.lite_utility_effects != null", [_set("_utility_rows", "lasting_state.lite_utility_effects")]),
            _each("_utility_rows", "_utility_row", [
                _when("not (_utility_row.spell == utility_spell and _utility_row.kind == 'lite_utility' and _utility_row.caster == _caster_state_id and _utility_row.target == _target_state_id)", [_push("_utility_kept", "_utility_row")]),
            ]),
            _require("len(_utility_kept) < 128", "Resolve existing utility sources before adding more."),
            _push("_utility_kept", {
                "kind": "'lite_utility'", "spell": "utility_spell", "caster": "_caster_state_id",
                "target": "_target_state_id", "cast_id": "cast_id", "spell_level": "slot_level",
                "preparation_source": "preparation_source", "until_long_rest": True,
            }),
            _set("lasting_state.lite_utility_effects", "_utility_kept"),
            _when("utility_spell == 'darkvision'", [
                _set("_spell_summary_rows", [{"label": "'Darkvision'", "value": "'40 feet'"}]),
                _set("_spell_detail_rows", [{"label": "'Duration'", "value": "'Until the recipient completes a Long Rest. Wizard and Druid preparation maintains their own grants.'"}, {"label": "'Vision'", "value": "'Ordinary darkness becomes dim light within 40 feet; magical darkness remains opaque. Native longer-range senses remain.'"}]),
            ]),
            _when("utility_spell == 'speak_with_animals'", [
                _set("_spell_summary_rows", [{"label": "'Beast speech'", "value": "'Comprehend and reply'"}]),
                _set("_spell_detail_rows", [{"label": "'Duration'", "value": "'Until the caster completes a Long Rest. A Druid must keep its source prepared.'"}, {"label": "'Communication'", "value": "'Perceived Beast speech permits conversation and normal social checks. Shapechanged creatures do not gain Beast dialogue. The spell does not create information the creature does not know.'"}]),
            ]),
        ],
    )


def bind_utility_support(runtime, record=None):
    """Join the isolated utility module to real callers after mobility normalizes spell IDs."""
    tools = runtime["tools"]
    _bind_utility_unpreparation(tools, record)
    tools["validate_srd_lite_darkvision"] = _validate_darkvision()
    tools["install_srd_lite_utility"] = _install_utility()
    tools["retire_srd_lite_utility"] = _retire_utility()
    _bind_animals_ritual(tools)
    for name in ("cast_spell", "begin_srd_spell_action"):
        tool = tools[name]
        # Begin admission must reject bad geometry before saving a paid pending action.
        index = next(i for i, e in enumerate(tool["effects"]) if e.get("set", {}).get("field") == "_spell_definition" or e.get("store_as") == "_spell_definition")
        tool["effects"][index+1:index+1] = [
            _when("_ms == 'darkvision'", [
                _require("targets == null or len(targets) == 0 or (len(targets) == 1 and targets[0] == _target_id)", "Darkvision affects exactly one selected creature at every slot level."),
                _require("target_willing == true", "The Darkvision recipient must be willing."),
                _call("validate_srd_lite_darkvision", {"caster_state": "{_caster_state_id}", "target_state": "{_target_id}", "lasting_state": "{_lasting_state_id}"}),
            ]),
            _when("_ms == 'speak_with_animals'", [
                _require("_target_id == _caster_state_id and (targets == null or len(targets) == 0 or (len(targets) == 1 and targets[0] == _caster_state_id))", "Speak with Animals affects only its living caster."),
                _require("caster_state.is_alive == true", "Speak with Animals requires a living caster."),
            ]),
        ]
    cast = tools["cast_spell"]
    index = next(i for i, e in enumerate(cast["effects"]) if e.get("set", {}).get("field") == "_spell_source_cast_id")
    # Shared casting has already established slot level and the instance before effect delivery.
    index = next(i for i in range(index+1, len(cast["effects"])) if cast["effects"][i].get("call") == "begin_srd_spell_source_instance")
    cast["effects"].insert(index+1, _when("(_ms == 'darkvision' or _ms == 'speak_with_animals') and _spell_cast_failed != true and _pending_spell_operation_lost != true", [
        _when("_spell_source_cast_id == ''", [_call("begin_srd_spell_source_instance", {
            "caster_state": "{_caster_state_id}", "lasting_state": "{_lasting_state_id}", "spell": "{_ms}", "slot_level": "{_slot_level}",
        }, ("_spell_source_cast_id",))]),
        _call("install_srd_lite_utility", {
            "caster_state": "{_caster_state_id}", "target_state": "{_target_id}",
            "lasting_state": "{_lasting_state_id}", "cast_id": "{_spell_source_cast_id}",
            "slot_level": "{_slot_level}", "preparation_source": "{_grant_source}", "utility_spell": "{_ms}",
        }, ("_spell_summary_rows", "_spell_detail_rows")),
    ]))
    tools["resolve_srd_timed_senses"]["effects"].extend([
        _set("_utility_rows", []),
        _when("lasting_state.lite_utility_effects != null", [_set("_utility_rows", "lasting_state.lite_utility_effects")]),
        _each("_utility_rows", "_utility_row", [
            _when("_utility_row.kind == 'lite_utility' and _utility_row.spell == 'darkvision' and _utility_row.target == _target_state_id and _utility_row.cast_id != '' and _utility_row.until_long_rest == true and target_state.is_alive == true", [_set("_sense_darkvision", "max(_sense_darkvision, 40)")]),
        ]),
    ])
    tools["complete_srd_rest"]["effects"].append(_when("target_state.rest_kind == 'long' and target_state.rest_phase == 'complete'", [
        _call("retire_srd_lite_utility", {"owner_id": "{_target_state_id}", "reason": "rest", "lasting_state": "{_lasting_state_id}"}),
    ]))
    tools["prepare_srd_spells"]["effects"].append(_when("(source == 'wizard' or source == 'druid') and 'darkvision' not in spells", [
        _call("retire_srd_lite_utility", {"owner_id": "{_caster_state_id}", "reason": "unprepare", "preparation_source": "{source}", "utility_spell": "darkvision"}),
    ]))
    tools["prepare_srd_spells"]["effects"].append(_when("source == 'druid' and 'speak_with_animals' not in spells", [
        _call("retire_srd_lite_utility", {"owner_id": "{_caster_state_id}", "reason": "unprepare", "preparation_source": "{source}", "utility_spell": "speak_with_animals"}),
    ]))
    death_branches = 0
    for effects in lists(tools["record_srd_actual_death"]["effects"]):
        for effect in effects:
            if effect.get("condition") == "target_state.is_alive == false":
                effect["on_true"].insert(0, _call("retire_srd_lite_utility", {"owner_id": "{_target_state_id}", "reason": "death", "lasting_state": "{_lasting_state_id}"}))
                death_branches += 1
    assert death_branches == 1, "Preserve the existing actual-death owner."
    _bind_animals_language(tools)
    # The accepted source factors these delivery branches into a retained cast tail.
    # Keep its other families intact; our saved grants replace only these two routes.
    counts = {"timed_senses": 0, "information_spell": 0}
    for name in ("cast_spell", "finish_srd_retained_cast_tail"):
        for effects in lists(tools[name]["effects"]):
            for effect in effects:
                condition = effect.get("condition", "")
                for family, spell_id in (("timed_senses", "darkvision"), ("information_spell", "speak_with_animals")):
                    if (f"_spell_definition.family == '{family}'" in condition
                            and f"cast_spell_{family}" in str(effect)):
                        identity = "_ms" if name == "cast_spell" else "_retained_cast_tail_choices.spell"
                        effect["condition"] += f" and {identity} != '{spell_id}'"
                        counts[family] += 1
    assert counts == {"timed_senses": 1, "information_spell": 1}, counts


def _bind_animals_ritual(tools):
    """legacy free-exploration rituals use the ordinary action and saved grant owners."""
    scoped = "_ms == 'speak_with_animals'"
    prelude = [
        _set("_lite_animals_free", False), _set("_lite_animals_level", 1),
        _when(scoped, [
            _set("_lite_animals_free", "caster != null and combat.encounter_active != true and combat.turn_active != true"),
            _require("slot_resource != 'ritual' or _lite_animals_free == true", "Animal speech Ritual casting requires saved free exploration; select a slot during combat."),
            _require("slot_resource != 'cantrip'", "Speak with Animals requires a levelled resource selection."),
            *[_when(f"slot_resource == 'spell_slot_{level}'", [_set("_lite_animals_level", level)]) for level in range(1, 10)],
            _require("caster == null or _lite_animals_level == 1 or (caster_state.resources != null and slot_resource in caster_state.resources)", "An upcast requires the actual saved slot selection."),
            _require("caster == null or _lite_animals_level == 1 or ((caster_state.resource_maxima == null and caster_state.resources[slot_resource] > 0) or (caster_state.resource_maxima != null and slot_resource in caster_state.resource_maxima and caster_state.resource_maxima[slot_resource] > 0))", "An upcast requires positive saved slot capacity."),
        ]),
    ]
    for name in ("cast_spell", "cast_spell_outside_combat", "begin_srd_spell_action"):
        tool = tools[name]
        for condition in tool["pre_conditions"]:
            if "(casting_mode == 'ritual') == (slot_resource == 'ritual')" in condition["condition"]:
                condition["condition"] = f"({condition['condition']}) or (spell == 'speak_with_animals')"
            if "wizard_spellbook" in condition["condition"]:
                condition["condition"] = condition["condition"].replace("casting_mode == 'ritual'", "casting_mode == 'ritual' and spell != 'speak_with_animals'")
                condition["condition"] = condition["condition"].replace("spell != 'speak_with_animals' and not ((spell == 'longstrider' or spell == 'jump' or spell == 'enhance_leap'))", "spell not in definitions.srd_lite_free_rituals")
        if name == "cast_spell_outside_combat":
            continue
        # Identity normalization must precede this scoped admission.
        index = next(i for i,e in enumerate(tool["effects"]) if e.get("set",{}).get("field") == "_ms")
        tool["effects"][index+1:index+1] = prelude
        for effects in list(lists(tool["effects"])):
            for index in range(len(effects)-1, -1, -1):
                effect = effects[index]
                setter = effect.get("set", {})
                if setter.get("field") == "_ritual_cast":
                    setter["value"] = f"({setter['value']}) and not ({scoped})"
                if setter.get("field") == "_cast_resource" and setter.get("value") == "slot_resource":
                    effects.insert(index+1, _when("_lite_animals_free == true", [_set("_cast_resource", "'no_slot'")]))
                if "condition" in effect and "casting_mode == 'ritual'" in effect["condition"]:
                    effect["condition"] = effect["condition"].replace("casting_mode == 'ritual'", f"(casting_mode == 'ritual' and not ({scoped}))")
                required = effect.get("require", {})
                if "casting_mode == 'ordinary'" in required.get("condition", "") and "_spell_definition.casting_time" in required["condition"]:
                    required["condition"] = required["condition"].replace("casting_mode == 'ordinary'", f"(casting_mode == 'ordinary' or (({scoped}) and _lite_animals_free == true))")
        indices = [i for i,e in enumerate(tool["effects"]) if e.get("set",{}).get("field") == "_slot_level"]
        tool["effects"].insert(indices[-1]+1, _when("_lite_animals_free == true", [_set("_slot_level", "_lite_animals_level")]))
        if name == "cast_spell":
            tool["effects"].append(_when("_lite_animals_free == true", [
                _push("_spell_card_summary", {"label": "'Casting mode'", "value": "'Ritual in free exploration; no spell slot spent'"}),
            ]))


def _bind_animals_language(tools):
    """Project source-owned speech into the existing real conversation/check consumer."""
    collect = tools["collect_srd_information_language"]
    if not any(p["name"] == "lasting_state" for p in collect["parameters"]):
        collect["parameters"].append(_lasting())
    collect["effects"].extend([
        _set("_utility_rows", []),
        _when("lasting_state.lite_utility_effects != null", [_set("_utility_rows", "lasting_state.lite_utility_effects")]),
        _each("_utility_rows", "_utility_row", [
            _when("_utility_row.kind == 'lite_utility' and _utility_row.spell == 'speak_with_animals' and _utility_row.target == _target_state_id and _utility_row.caster == _target_state_id and _utility_row.cast_id != '' and _utility_row.until_long_rest == true and target_state.is_alive == true", [_set("_language_animals", True)]),
        ]),
    ])
    for effects in lists(tools["resolve_srd_spell_language"]["effects"]):
        for effect in effects:
            condition = effect.get("condition", "")
            if "_language_animals == true and speaker_state.creature_type == 'beast'" in condition:
                effect["condition"] += " and speaker_state.form_current_source_id == null and (speaker_state.creature_tags == null or 'shapechanger' not in speaker_state.creature_tags)"
    tools["communicate_srd_spell"]["effects"][:0] = [
        _require("purpose == 'conversation' or purpose == 'influence'", "Speak with Animals permits dialogue and normal social checks; it cannot guarantee new information."),
        _require("listener_state.is_alive == true and speaker_state.is_alive == true", "Living communication requires living participants."),
        _require("listener_state.form_can_speak != false", "This form cannot speak."),
    ]


def expose_utility_operations(runtime, denied):
    """Expose only the completed public language consumer after the pending guard binds."""
    name = "communicate_srd_spell"
    tool = runtime["tools"][name]
    guard = tool["effects"].pop(0)
    assert guard == {"require": {"condition": "false", "error": "This spell operation is pending or excluded in the SRD Lite foundation."}}
    tool["metadata"].pop("visible_to_player", None)
    denied.remove(name)


def _bind_utility_unpreparation(tools, record):
    """Remove only owned maintained utility choices outside combat; keep other source policy."""
    tool = tools['prepare_srd_spells']
    tool['parameters'].append(_param('unprepare_only', 'boolean', required=False, default=False,
        description='Remove maintained Darkvision from a Wizard/Druid source or animal speech from a Druid source. No new spells, replacements or other removals.'))
    next(p for p in tool['parameters'] if p['name'] == 'spells')['min_items'] = 0
    if record is not None:
        declaration = next(t for t in record['tools'] if t['name'] == 'prepare_srd_spells')
        declaration['input_schema']['properties']['spells']['minItems'] = 0
        declaration['input_schema']['properties']['unprepare_only'] = {'type': 'boolean', 'default': False}
    tool['effects'][:0] = [_when('unprepare_only == true', [
        _require("source == 'wizard' or source == 'druid'", 'Only an actual Wizard or Druid utility source can be unprepared here.'),
        _set('_lite_prep_old', []), _set('_lite_prep_found', False), _set('_lite_prep_removed', 0),
        _set('_lite_prep_grants', 'caster.data.spellcasting_grants'),
        _each('_lite_prep_grants', '_lite_prep_grant', [
            _when('_lite_prep_grant.source == source', [
                _set('_lite_prep_old', '_lite_prep_grant.spells'), _set('_lite_prep_found', True),
            ]),
        ]),
        _require('_lite_prep_found == true', 'Unprepare only an existing saved class source.'),
        _each('spells', '_lite_prep_spell', [
            _require('_lite_prep_spell in _lite_prep_old', 'Unpreparing cannot learn or replace a spell.'),
        ]),
        _each('_lite_prep_old', '_lite_prep_spell', [
            _when('_lite_prep_spell not in spells', [
                _require("_lite_prep_spell == 'darkvision' or (source == 'druid' and _lite_prep_spell == 'speak_with_animals')", 'This unprepare request may remove only this maintained utility; preserve other spells and cantrips.'),
                _set('_lite_prep_removed', '_lite_prep_removed + 1'),
            ]),
        ]),
        _require('_lite_prep_removed > 0', 'Select a maintained utility spell to remove.'),
        _when('caster_state.spell_preparation_used == null', [_set('caster_state.spell_preparation_used', {})]),
    ])]
    replacements = 0
    for effects in lists(tool['effects']):
        for effect in effects:
            requirement = effect.get('require', {})
            condition = requirement.get('condition', '')
            if (condition.startswith('caster_state.spell_preparation_open == true')
                    or condition.startswith('_prep_old_cantrips == _prep_new_cantrips')
                    or condition.startswith('_prep_changed_cantrips + _prep_used_cantrips')
                    or condition == '_prep_changed_spells + _prep_changed_cantrips > 0'):
                requirement['condition'] = '(' + condition + ') or unprepare_only == true'
                replacements += 1
    assert replacements == 4, 'Preserve the real source selection owner and its other class policies.'
