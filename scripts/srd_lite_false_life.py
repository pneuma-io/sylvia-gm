"""Bind fixed temporary HP and preparation ownership within the isolated variant."""

from copy import deepcopy

from .srd_control_behaviors import _param
from .srd_lite_profiles import lists
from .srd_timed_actor_defenses import _call, _set, _when


def bind_false_life(runtime):
    """Keep the existing neutral damage/rest owners and track only this spell's pool."""
    definitions = runtime['definitions']
    definitions['spells']['false_life'].update(base_dice=0, flat_bonus=7)
    definitions['spell_casting']['false_life'].update(
        components=['verbal', 'somatic'], material='', material_parts=[],
        material_consumed=False, material_has_cost=False, focus_substitution=False,
        duration='Until long rest or normal preparation is removed',
    )
    schema = runtime['definition_row_schemas']['definitions.spells']['variants']['self_temporary_hit_points']
    schema['properties']['base_dice'] = {'type': 'integer', 'enum': [0]}
    schema['properties']['flat_bonus'] = {'type': 'integer', 'enum': [7]}
    tools = runtime['tools']
    tool = tools['cast_spell_self_temporary_hit_points']
    for index, parameter in enumerate(tool['parameters']):
        if parameter['name'] == 'base_dice':
            tool['parameters'][index] = deepcopy(parameter)
            tool['parameters'][index]['minimum'] = 0
    tool['parameters'].append(_param('depends_on_preparation', 'boolean', required=False, default=False))
    tool['parameters'].append(_param('lite_prepared_source', required=False, default='', model_hidden=True))
    for effects in lists(tools['cast_spell']['effects']):
        for effect in effects:
            if effect.get('call') == 'cast_spell_self_temporary_hit_points':
                effect['params']['depends_on_preparation'] = '{_casting_pc_actor}'
                # bind_defenses joins the actual admitted grant at this root.
                effect['params']['lite_prepared_source'] = '{_lite_mage_cast_source}'
                for unused in ('_spell_temporary_hit_point_roll', '_spell_temporary_hit_point_choice'):
                    effect.get('export_context', {}).pop(unused, None)
    for condition in tool['pre_conditions']:
        condition['condition'] = condition['condition'].replace('base_dice == 2', 'base_dice == 0').replace('flat_bonus == 4', 'flat_bonus == 7')
    tool['description'] = 'Offer fixed temporary HP from the Lite False Life profile; retain an equal or stronger pool.'
    tool['narrative']['on_success'] = 'The caster receives the fixed temporary HP pool or retains the stronger existing pool.'
    tool['effects'] = [
        _set('_spell_summary_rows', []), _set('_spell_detail_rows', []),
        _set('_card_hp_before', 'caster_state.hp'),
        _set('_card_temp_before', 'caster_state.temp_hp'),
        _set('_spell_temporary_hit_point_amount', 'flat_bonus + (slot_level - base_level) * temporary_hit_points_per_slot_level'),
        _when('caster_state.temp_hp < _spell_temporary_hit_point_amount', [
            _call('grant_temporary_hit_points', {'target': '{_caster_state_id}', 'amount': '{_spell_temporary_hit_point_amount}', 'accept_new': True}),
            _set('caster_state.lite_false_life', True),
            _set('caster_state.lite_false_life_prepared', 'depends_on_preparation'),
            _set('caster_state.lite_false_life_prepared_source', 'lite_prepared_source'),
        ]),
        _call('record_srd_spell_target_result', {
            'target': '{_caster_state_id}', 'brief': 'Temporary HP {caster_state.temp_hp}',
            'detail': 'False Life offers {_spell_temporary_hit_point_amount} temporary HP without a roll. Existing pool {_card_temp_before}; resulting pool {caster_state.temp_hp}. Pools do not stack.',
        }, ('_spell_target_summary', '_spell_target_detail')),
        {'list_push': {'list': '_spell_summary_rows', 'value': '_spell_target_summary'}},
        {'list_push': {'list': '_spell_detail_rows', 'value': '_spell_target_detail'}},
    ]
    tools['grant_temporary_hit_points']['effects'].insert(0, _when('target.temp_hp == 0 or accept_new == true', _clear_pool_owner('target')))
    tools['prepare_srd_spells']['effects'].append(_when("caster_state.lite_false_life == true and caster_state.lite_false_life_prepared == true and caster_state.lite_false_life_prepared_source == source and 'false_life' not in spells", [_set('caster_state.temp_hp', 0)] + _clear_pool_owner('caster_state')))
    tools['complete_srd_rest']['effects'].append(_when("target_state.rest_kind == 'long'", _clear_pool_owner('target_state')))

    # Saved packets resolve target as an entity; only nested calls use _target_id.
    # Clones already exist before Lite binding, so patch their actual debits.
    for name in ('apply_srd_damage', 'apply_srd_saved_hit_qualifying_damage',
                 'apply_srd_saved_hit_qualifying_composite_damage'):
        changed = 0
        for effects in lists(tools[name]['effects']):
            for effect in list(effects):
                if effect.get('decrement', {}).get('field') == 'target.temp_hp':
                    effects.insert(effects.index(effect) + 1, _when(
                        'target.temp_hp == 0', _clear_pool_owner('target')))
                    changed += 1
        if changed != 1:
            raise ValueError('False Life temporary HP debit seam changed: ' + name)


def _clear_pool_owner(actor):
    """Clear only provenance; the existing HP owner controls the pool amount."""
    return [
        _set(actor + '.lite_false_life', False),
        _set(actor + '.lite_false_life_prepared', False),
        _set(actor + '.lite_false_life_prepared_source', "''"),
    ]
