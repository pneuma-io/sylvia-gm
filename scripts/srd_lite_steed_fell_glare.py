"""The Fiend Steed's native save, Frightened consumers and summoner-turn expiry."""
from copy import deepcopy
from .build_srd_5_2_1_mechanics import _tool_schema
from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _tool, _when
from .srd_timed_senses import _loop, _push
from .srd_lite_steed_healing_touch import _action as _healing_action, _touch_range, CHARGE
from .srd_lite_steed_casting_statistics import CURRENT, EXPORTS, _walk

ACTION = 'use_srd_steed_fell_glare'
RANGE = 'qualify_srd_lite_steed_glare_range'
SAVE = 'resolve_srd_lite_steed_glare_save'
DEADLINE = 'read_srd_lite_steed_glare_deadline'
EXPIRE = 'expire_srd_lite_steed_glare_sources'
PROFILE = 'steed_fell_glare'


def _range():
    """Keep the actual scene/body range owner and add the printed sight requirement."""
    tool = deepcopy(_touch_range())
    tool['description'] = 'Qualify the actual Fiend Steed creature within 60 feet that it can see.'
    guard = tool['effects'][-1]['require']
    guard['condition'] = guard['condition'].replace('<= 25 +', '<= 3600 +')
    guard['error'] = 'Fell Glare needs an actual creature within 60 feet on the same plane.'
    tool['effects'] += [
        _call('resolve_srd_scene_relation', {'actor_state': '{_steed_state_id}',
            'target_state': '{_target_state_id}'}, ('_scene_can_see',)),
        _require('_scene_can_see == true', 'The actual Fiend Steed must see its Fell Glare target.')]
    return tool


def _deadline():
    """The summoner's next completed turn owns expiry, independently of the steed's turn."""
    return _tool('Read the genuine summoner turn counter for Fell Glare expiry.',
        [_entity('caster_state')], [
            _require('combat.encounter_active != true or caster_state.turns_started != null',
                     'Fell Glare needs the summoner\'s actual turn counter during combat.'),
            _set('_steed_glare_caster_id', '_caster_state_id'),
            _set('_steed_glare_end_turn', 1),
            _when('caster_state.turns_started != null', [
                _set('_steed_glare_end_turn', 'caster_state.turns_started + 1')])])


def _action():
    """Reuse the same Steed identity, condition and charge preflight as Healing Touch."""
    tool = deepcopy(_healing_action())
    cutoff = next(i for i, effect in enumerate(tool['effects'])
        if effect.get('require', {}).get('condition', '').startswith('target_state.is_alive'))
    tool['effects'] = tool['effects'][:cutoff]
    fiend = next(effect['require'] for effect in tool['effects']
        if effect.get('require', {}).get('condition') == "steed_state.creature_type == 'celestial'")
    fiend.update(condition="steed_state.creature_type == 'fiend'", error='Only a Fiend steed has Fell Glare.')
    tool['description'] = 'Use the Fiend Otherworldly Steed\'s Fell Glare, recharging after its Long Rest.'
    tool['effects'] += [
        _require('target_state.is_alive == true and target_state.is_object != true and '
                 'target_state.creature_type != null and target_state.conditions != null and '
                 'target_state.saving_throw_modifiers.wisdom != null',
                 'Fell Glare needs an authoritative living creature and its Wisdom save modifier.'),
        _require('state.timed_condition_free_source_ids == null or '
                 'len(state.timed_condition_free_source_ids) > 0',
                 'Resolve existing bounded condition sources before Fell Glare.'),
        _call(RANGE, {'steed_state': '{_steed_state_id}', 'target_state': '{_target_state_id}'}),
        _call(CURRENT, {'steed_state': '{_steed_state_id}',
            'caster_state': '{steed_state.created_caster_state_id}'}, EXPORTS),
        _call(DEADLINE, {'caster_state': '{steed_state.created_caster_state_id}'},
              ('_steed_glare_caster_id', '_steed_glare_end_turn')),
        _when('combat.encounter_active == true', [
            _call('consume_srd_bonus_action', {'actor_state': '{_steed_state_id}',
                                              'bonus_action': 'Fell Glare'})]),
        _call(SAVE, {'target_state': '{_target_state_id}', 'save_ability': 'wisdom',
            'save_dc': '{_steed_save_dc}', 'selected_condition': 'frightened',
            'source_state_id': '{_steed_state_id}'}, ('_timed_condition_initial_save_outcome',
                '_card_d20_roll', '_card_d20_modifier', '_card_d20_total')),
        _set('_steed_glare_outcome', '_timed_condition_initial_save_outcome'),
        _set('_steed_glare_condition', "'None'"),
        _when("_steed_glare_outcome == 'failure'", [
            _set('_steed_glare_condition', "'Frightened'"),
            _set('_steed_glare_duration_rounds', 1),
            _when('combat.encounter_active == true', [_set('_steed_glare_duration_rounds', 0)]),
            _call('install_srd_timed_condition_source', {'target_state': '{_target_state_id}',
                'caster_state': '{_steed_state_id}', 'lasting_state': 'state', 'spell': 'find_steed',
                'condition': 'frightened', 'save_dc': '{_steed_save_dc}',
                'duration_rounds': '{_steed_glare_duration_rounds}',
                'repeat_save_timing': 'none', 'save_ability': 'wisdom', 'control_profile': PROFILE},
                ('_timed_condition_source_id',)),
            _set('_steed_glare_stamp', {'native_profile': repr(PROFILE),
                'native_body_source': '_steed_state_id',
                'native_body_source_sequence': 'state.timed_condition_sources[_timed_condition_source_id].sequence',
                'steed_glare_caster_id': '_steed_glare_caster_id',
                'steed_glare_end_turn': '_steed_glare_end_turn', 'dispel_ongoing': False,
                'source_is_magical': False, 'effect_kind': "'physical'", 'ongoing_duration': False}),
            _set('state.timed_condition_sources.{_timed_condition_source_id}',
                 'merge(state.timed_condition_sources[_timed_condition_source_id], _steed_glare_stamp)'),
            _set('_steed_glare_index', []),
            _when('state.steed_glare_source_ids != null', [
                _set('_steed_glare_index', 'state.steed_glare_source_ids')]),
            _when('_timed_condition_source_id not in _steed_glare_index', [
                _push('_steed_glare_index', '_timed_condition_source_id')]),
            _set('state.steed_glare_source_ids', '_steed_glare_index'),
            _set('state.has_steed_glare_sources', True),
            _call('refresh_srd_timed_condition_target', {'target_state': '{_target_state_id}',
                                                        'lasting_state': 'state'})]),
        _set('steed_state.' + CHARGE, False),
        _set('_steed_glare_detail', "'Immune to Frightened; no save rolled.'"),
        _when('_card_d20_roll != null', [
            _call('format_srd_information_projection', {'message': 'Wisdom save: dice {_card_d20_roll.results}; '
                '{_card_d20_roll.total} + ({_card_d20_modifier}) = {_card_d20_total}; '
                'DC {_steed_save_dc}; {_steed_glare_outcome}. Failure lasts until the end of '
                'the summoner\'s next turn.'}, ('_info_projection_message',)),
            _set('_steed_glare_detail', '_info_projection_message')]),
        _set('_steed_glare_details', [{'label': 'target_state.name', 'value': '_steed_glare_detail'}])]
    # New effects use the same canonical lasting-state binding as the source tool.
    def lasting(value):
        if isinstance(value, str):
            import re
            return re.sub(r'\bstate\.', 'lasting_state.', value)
        if isinstance(value, list):
            return [lasting(child) for child in value]
        if isinstance(value, dict):
            return {key: lasting(child) for key, child in value.items()}
        return value
    tool['effects'] = lasting(tool['effects'])
    tool['metadata']['result_card'] = {'version': 1, 'footprint': 'compact', 'title': 'Fell Glare',
        'summary': [{'label': 'Steed', 'value': {'path': 'context.steed_state.name'}},
            {'label': 'Creature', 'value': {'path': 'context.target_state.name'}},
            {'label': 'Save DC', 'value': {'path': 'context._steed_save_dc'}},
            {'label': 'Condition', 'value': {'path': 'context._steed_glare_condition'}},
            {'label': 'Wisdom save', 'value': {'path': 'context._steed_glare_outcome'}, 'equation': {
                'optional': True, 'die_sides': 20, 'roll': {'path': 'context._card_d20_roll.total'},
                'modifier': {'path': 'context._card_d20_modifier'},
                'total': {'path': 'context._card_d20_total'}}}],
        'details_label': 'Saving throw', 'details': {'path': 'context._steed_glare_details'}}
    return tool


def _expire():
    """Retire the selected source through the native pool and reproject independent conditions."""
    return _tool('Expire only actual Fell Glare sources at the summoner\'s next turn end.',
        [_parameter('boundary_actor', required=False, default=''),
         _parameter('boundary_phase', required=False, default='time'),
         _parameter('boundary_turn', 'integer', required=False, default=0)], [
            _set('_steed_glare_kept', []),
            _set('_steed_glare_due_ids', 'state.steed_glare_source_ids'),
            _loop('_steed_glare_due_ids', '_steed_glare_due_id', [
                _set('_steed_glare_due', 'state.timed_condition_sources[_steed_glare_due_id]'),
                _when('_steed_glare_due.installed == true and '
                      "_steed_glare_due.native_profile == 'steed_fell_glare' and "
                      '_steed_glare_due.native_body_source_sequence == _steed_glare_due.sequence', [
                    {'condition': "boundary_phase == 'end' and "
                        'boundary_actor == _steed_glare_due.steed_glare_caster_id and '
                        'boundary_turn >= _steed_glare_due.steed_glare_end_turn', 'on_true': [
                            _call('retire_srd_timed_condition_source', {'lasting_state': 'state',
                                'source_id': '{_steed_glare_due_id}'}),
                            _call('refresh_srd_timed_condition_target', {'lasting_state': 'state',
                                'target_state': '{_steed_glare_due.target_state_id}'})],
                        'on_false': [_push('_steed_glare_kept', '_steed_glare_due_id')]}])]),
            _set('state.steed_glare_source_ids', '_steed_glare_kept'),
            _set('state.has_steed_glare_sources', 'len(_steed_glare_kept) > 0')])


def bind_steed_fell_glare(runtime):
    """Use the established control-source path without the Fear spell's forced retreat or repeats."""
    tools = runtime['tools']
    if ACTION in tools:
        raise ValueError('Fell Glare must be composed exactly once')
    save = deepcopy(tools['resolve_srd_timed_condition_initial_save'])
    next(p for p in save['parameters'] if p['name'] == 'selected_condition')['enum_values'] = ['frightened']
    next(effect for effect in save['effects'] if effect.get('call') ==
         'check_srd_target_condition_immunity')['params']['source_is_magical'] = False
    installer = next(p for p in tools['install_srd_timed_condition_source']['parameters']
                     if p['name'] == 'control_profile')
    if PROFILE not in installer['enum_values']:
        installer['enum_values'].append(PROFILE)
    for name, field in (('resolve_srd_control_behavior_sources', '_control_spell_profiles'),
                        ('resolve_srd_affliction_projection', '_affliction_profiles')):
        owners = [effect['set'] for effect in _walk(tools[name]['effects'])
                  if effect.get('set', {}).get('field') == field]
        if len(owners) != 1:
            raise ValueError('Preserve the native selected control-source projection: ' + name)
        owners[0]['value'].append(repr(PROFILE))
    tools.update({ACTION: _action(), RANGE: _range(), SAVE: save,
                  DEADLINE: _deadline(), EXPIRE: _expire()})
    tools['process_srd_turn_effects']['effects'].append(_when('lasting_state.has_steed_glare_sources == true', [
        _call(EXPIRE, {'boundary_actor': '{boundary_actor}', 'boundary_phase': '{boundary_phase}',
                       'boundary_turn': '{boundary_turn}'})]))
    return [ACTION, RANGE, SAVE, DEADLINE, EXPIRE, 'install_srd_timed_condition_source',
            'resolve_srd_control_behavior_sources', 'resolve_srd_affliction_projection',
            'process_srd_turn_effects']


def expose_steed_fell_glare(runtime, record):
    """Expose just the source-owned Bonus Action through the builder's existing owned-operation path."""
    if any(row['name'] == ACTION for row in record['tools']):
        raise ValueError('Fell Glare must have one public declaration')
    record['tools'].append({'name': ACTION, 'capability': 'dnd.srd.5.2.1.spells', 'writers': ['dm'],
        'turn_statuses': ['open'], 'state_predicates': [],
        'input_schema': _tool_schema(runtime['tools'][ACTION], tool_name=ACTION)})
    return ACTION
