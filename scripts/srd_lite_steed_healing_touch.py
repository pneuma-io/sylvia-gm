"""The Celestial Otherworldly Steed's once-per-Long-Rest Healing Touch."""
from .build_srd_5_2_1_mechanics import _tool_schema
from .srd_scene_relations import POSITION_EXPORTS, _tool_scene
from .srd_timed_actor_defenses import _call, _entity, _require, _set, _tool, _when
from .srd_lite_steed_life_bond import _body_distance

ACTION = 'use_srd_steed_healing_touch'
OWNER = 'qualify_srd_lite_steed_bonus_owner'
RANGE = 'qualify_srd_lite_steed_touch_range'
CHARGE = 'created_steed_bonus_available'


def _owner():
    """Keep a created steed tied to its real living caster, including while incapacitated."""
    return _tool('Qualify the saved active Otherworldly Steed and its living source owner.',
        [_entity('steed_state'), _entity('caster_state')], [
            _require("steed_state.created_spell_id == 'find_steed' and steed_state.created_active == true and "
                     "steed_state.is_alive == true and steed_state.hp >= 1 and "
                     "steed_state.created_caster_state_id == _caster_state_id and "
                     "caster_state.created_steed_id == _steed_state_id and caster_state.is_alive == true",
                     'Use the actual active Otherworldly Steed belonging to a living caster.'),
            _require('steed_state.created_spell_level >= 2 and steed_state.created_spell_level <= 9',
                     'The steed needs its actual saved spell level.')])


def _touch_range():
    """Read the validated body-distance primitive without inventing sight requirements."""
    effects = []
    for subject, identity, frame in (
        ('{_steed_state_id}', '_steed_state_id', '_bond_caster_frame'),
        ('{_target_state_id}', '_target_state_id', '_bond_steed_frame')):
        effects.extend([
            _call('srd_information_scene_entity', {'subject': subject}, POSITION_EXPORTS),
            _require('_info_scene_entity.metadata.participant_ref == ' + identity +
                     ' and _info_scene_entity.metadata.is_object != true and '
                     '_info_scene_entity.metadata.plane != null and '
                     "(_info_scene_entity.footprint.kind == 'box' or "
                     "_info_scene_entity.footprint.kind == 'circle') and _info_scene_entity.height != null",
                     'Healing Touch needs the actual creature bodies and their planes.'),
            _set(frame, '_info_scene_entity')])
    return _tool_scene('Qualify Healing Touch against actual bodies on the same plane within 5 feet.',
        [_entity('steed_state'), _entity('target_state')], effects + _body_distance() + [
            _require('_bond_caster_frame.metadata.plane == _bond_steed_frame.metadata.plane and '
                     '_bond_distance_squared <= 25 + 0.000000001',
                     'Healing Touch needs a creature actually within 5 feet on the same plane.')])


def _action():
    """Resolve one source-scaled native roll and one healing application after preflight."""
    tool = _tool_scene('Use the Celestial Otherworldly Steed\'s Healing Touch, recharging after its Long Rest.',
        [_entity('steed_state'), _entity('target_state')], [
            _require('state.pending_srd_action_stack == null or len(state.pending_srd_action_stack) == 0',
                     'Complete the actual pending action before Healing Touch.'),
            _call(OWNER, {'steed_state': '{_steed_state_id}',
                          'caster_state': '{steed_state.created_caster_state_id}'}),
            _require("steed_state.creature_type == 'celestial'", 'Only a Celestial steed has Healing Touch.'),
            _require('steed_state.' + CHARGE + ' == true',
                     'The steed must complete its own Long Rest before using this bonus action again.'),
            _require("steed_state.conditions != null and not ('incapacitated' in steed_state.conditions or "
                     "'paralyzed' in steed_state.conditions or 'petrified' in steed_state.conditions or "
                     "'stunned' in steed_state.conditions or 'unconscious' in steed_state.conditions) and "
                     "steed_state.control_confusion_active != true and "
                     "steed_state.control_order_blocks_bonus != true and "
                     "(steed_state.magic_jar_role == null or steed_state.magic_jar_role == 'possessed')",
                     'The steed cannot currently take a Bonus Action.'),
            _require('target_state.is_alive == true and target_state.hp != null and target_state.max_hp != null '
                     'and target_state.conditions != null and target_state.death_successes != null and '
                     'target_state.death_failures != null and target_state.stable != null and '
                     'target_state.stable_recovery_hours_remaining != null',
                     'Healing Touch requires authoritative living HP, conditions and death state.'),
            _call(RANGE, {'steed_state': '{_steed_state_id}', 'target_state': '{_target_state_id}'}),
            _when('combat.encounter_active == true', [
                _call('consume_srd_bonus_action', {'actor_state': '{_steed_state_id}',
                                                  'bonus_action': 'Healing Touch'})]),
            {'dice': {'expression': '2d8'}, 'store_as': '_steed_touch_roll'},
            _set('_steed_touch_amount', '_steed_touch_roll.total + steed_state.created_spell_level'),
            _set('_steed_touch_hp_before', 'target_state.hp'),
            _call('restore_hit_points', {'target': '{_target_state_id}', 'amount': '{_steed_touch_amount}'}),
            _set('_steed_touch_restored', 'max(0, target_state.hp - _steed_touch_hp_before)'),
            _set('steed_state.' + CHARGE, False),
            _call('record_srd_spell_target_result', {'target': '{_target_state_id}',
                'brief': 'HP restored {_steed_touch_restored}',
                'detail': 'Healing Touch restored {_steed_touch_restored} HP. '
                          'Dice {_steed_touch_roll.results}; {_steed_touch_roll.total} + '
                          '{steed_state.created_spell_level} = {_steed_touch_amount} before the HP cap.'},
                ('_spell_target_detail',)),
            _set('_steed_touch_details', ['_spell_target_detail'])])
    tool['metadata'] = {'category': 'combat', 'costs_action': False, 'result_card': {
        'version': 1, 'footprint': 'compact', 'title': 'Healing Touch',
        'summary': [
            {'label': 'Steed', 'value': {'path': 'context.steed_state.name'}},
            {'label': 'Creature', 'value': {'path': 'context.target_state.name'}},
            {'label': 'HP restored', 'value': {'path': 'context._steed_touch_restored'}},
            {'label': 'Healing dice', 'value': {'path': 'context._steed_touch_roll.results'}, 'equation': {
                'die_sides': 8, 'rolls': {'path': 'context._steed_touch_roll.results'},
                'roll': {'path': 'context._steed_touch_roll.total'},
                'modifier': {'path': 'context.steed_state.created_spell_level'},
                'total': {'path': 'context._steed_touch_amount'}}}],
        'details_label': 'Healing calculation', 'details': {'path': 'context._steed_touch_details'}}}
    next(p for p in tool['parameters'] if p['name'] == 'lasting_state')['model_hidden'] = True
    return tool


def bind_steed_healing_touch(runtime):
    """Preserve a returning individual's charge and reset it after its own validated Long Rest."""
    tools = runtime['tools']
    if any(name in tools for name in (ACTION, OWNER, RANGE)):
        raise ValueError('The Steed bonus-action source must be composed exactly once')
    tools.update({ACTION: _action(), OWNER: _owner(), RANGE: _touch_range()})
    initialize = _when('actor_state.' + CHARGE + ' == null', [_set('actor_state.' + CHARGE, True)])
    tools['finish_srd_steed_source_actor']['effects'].append(initialize)
    recharge = _when("target_state.created_spell_id == 'find_steed' and target_state.created_active == true "
                     "and target_state.rest_kind == 'long' and target_state.rest_phase == 'complete'",
                     [_set('target_state.' + CHARGE, True)])
    tools['complete_srd_rest']['effects'].append(recharge)
    return [ACTION, OWNER, RANGE, 'finish_srd_steed_source_actor', 'complete_srd_rest']


def expose_steed_healing_touch(runtime, record):
    """Declare the owned public action; actor eligibility and budgets stay in the runtime."""
    if any(row['name'] == ACTION for row in record['tools']):
        raise ValueError('The Healing Touch public declaration must be added exactly once')
    declaration = {'name': ACTION, 'capability': 'dnd.srd.5.2.1.spells',
        'writers': ['dm'], 'turn_statuses': ['open'], 'state_predicates': [],
        'input_schema': _tool_schema(runtime['tools'][ACTION], tool_name=ACTION)}
    record['tools'].append(declaration)
    return ACTION
