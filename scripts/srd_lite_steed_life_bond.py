"""Apply Find Steed's Life Bond to actual HP regained from selected spell owners.

The original healing owner still enforces HP caps and healing prohibitions.
Life Bond does not roll again, repeat the original spell, or spend another slot.
"""
from copy import deepcopy

from .srd_scene_relations import POSITION_EXPORTS, _tool_scene
from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _tool, _when
from .srd_lite_mass_cure_geometry import _effect_lists

QUALIFY = 'qualify_srd_lite_steed_life_bond'
APPLY = 'restore_srd_lite_steed_life_bond'
LEVEL = 'life_bond_spell_level'


def _body_distance():
    """Measure the nearest separation of admitted box/circle bodies and heights."""
    first, second = '_bond_caster_frame', '_bond_steed_frame'
    box_axis = [f'max(0, abs({first}.position[{i}] - {second}.position[{i}]) - '
                f'{first}.footprint.half_extents[{i}] - {second}.footprint.half_extents[{i}])'
                for i in (0, 1)]
    circle = (f'max(0, (({first}.position[0] - {second}.position[0]) ** 2 + '
              f'({first}.position[1] - {second}.position[1]) ** 2) ** 0.5 - '
              f'{first}.footprint.radius - {second}.footprint.radius)')
    mixed = [_set('_bond_box', first), _set('_bond_circle', second),
             _when(first + ".footprint.kind == 'circle'", [
                 _set('_bond_box', second), _set('_bond_circle', first)]),
             _set('_bond_dx', 'max(0, abs(_bond_box.position[0] - _bond_circle.position[0]) - '
                  '_bond_box.footprint.half_extents[0])'),
             _set('_bond_dy', 'max(0, abs(_bond_box.position[1] - _bond_circle.position[1]) - '
                  '_bond_box.footprint.half_extents[1])'),
             _set('_bond_horizontal', 'max(0, (_bond_dx ** 2 + _bond_dy ** 2) ** 0.5 - '
                  '_bond_circle.footprint.radius)')]
    cases = _when(first + ".footprint.kind == 'box' and " + second + ".footprint.kind == 'box'",
                  [_set('_bond_horizontal', f'(({box_axis[0]}) ** 2 + ({box_axis[1]}) ** 2) ** 0.5')])
    circles = _when(first + ".footprint.kind == 'circle' and " + second + ".footprint.kind == 'circle'",
                    [_set('_bond_horizontal', circle)])
    circles['on_false'] = mixed
    cases['on_false'] = [circles]
    return [cases,
        _set('_bond_vertical', f'max(0, {first}.position[2] - '
             f'{second}.position[2] - {second}.height, '
             f'{second}.position[2] - {first}.position[2] - {first}.height)'),
        _set('_bond_distance_squared', '_bond_horizontal ** 2 + _bond_vertical ** 2')]


def _qualifier():
    """Read an active owned steed's real bodies before the original healing mutates HP."""
    frames = []
    for subject, field, identity in (('{_caster_state_id}', '_bond_caster_frame', '_caster_state_id'),
                                    ('{_steed_state_id}', '_bond_steed_frame', '_steed_state_id')):
        frames.extend([
            _call('srd_information_scene_entity', {'subject': subject}, POSITION_EXPORTS),
            _require('_info_scene_entity.metadata.participant_ref == ' + identity,
                     'Life Bond needs the scene body bound to this actual creature.'),
            _require("_info_scene_entity.metadata.plane != null and "
                     "(_info_scene_entity.footprint.kind == 'box' or "
                     "_info_scene_entity.footprint.kind == 'circle') and "
                     "_info_scene_entity.height != null",
                     'Life Bond needs the actual plane, footprint and height of each body.'),
            _set(field, '_info_scene_entity')])
    return _tool_scene('Qualify this caster and its active Otherworldly Steed using actual body distance.',
        [_entity('caster_state'), _entity('steed_state')], [
            _require("caster_state.created_steed_id == _steed_state_id and "
                     "steed_state.created_spell_id == 'find_steed' and "
                     "steed_state.created_caster_state_id == _caster_state_id and "
                     "steed_state.created_active == true and steed_state.is_alive == true",
                     'Life Bond requires this caster\'s actual active Otherworldly Steed.'),
            _set('_life_bond_steed_id', "''"), *frames, *_body_distance(),
            _when('_bond_caster_frame.metadata.plane == _bond_steed_frame.metadata.plane and '
                  '_bond_distance_squared <= 25 + 0.000000001', [
                _set('_life_bond_steed_id', '_steed_state_id')])])


def _receiver():
    """Use the unchanged HP owner once and report the steed's actual restored HP."""
    return _tool('Restore the qualified steed by the caster\'s actual spell healing gain.',
        [_entity('caster_state'), _entity('steed_state'),
         _parameter('amount', 'integer', minimum=1)], [
            _require("caster_state.created_steed_id == _steed_state_id and "
                     "steed_state.created_spell_id == 'find_steed' and "
                     "steed_state.created_caster_state_id == _caster_state_id and "
                     "steed_state.created_active == true and steed_state.is_alive == true",
                     'The qualified Life Bond steed must remain active and owned by this caster.'),
            _set('_bond_steed_hp_before', 'steed_state.hp'),
            _call('restore_hit_points', {'target': '{_steed_state_id}', 'amount': '{amount}'}),
            _set('_life_bond_restored', 'max(0, steed_state.hp - _bond_steed_hp_before)'),
            _set('_life_bond_steed_name', 'steed_state.name'),
            _call('record_srd_spell_target_result', {
                'target': '{_steed_state_id}', 'brief': 'Life Bond: HP restored {_life_bond_restored}',
                'detail': 'Life Bond: caster regained {amount} HP from a spell; '
                          'steed restored {_life_bond_restored} HP.'},
                ('_spell_target_summary', '_spell_target_detail'))])


def _wrap_restore(rows, index, source_level):
    original = rows[index]
    target = original['params']['target']
    if not target.startswith('{_') or not target.endswith('}'):
        raise ValueError('Life Bond must preserve the original resolved healing target identity')
    identity = target[1:-1]
    # Native entity parameters expose the bound object under the corresponding name.
    actor = identity[1:-3] if identity.endswith('_id') else None
    if actor not in ('target', 'target_state', 'caster_state'):
        raise ValueError('Unexpected native healing target for Life Bond: ' + target)
    before = [
        _set('_life_bond_steed_id', "''"),
        _set('_life_bond_hp_before', actor + '.hp'),
        _when(source_level + ' >= 1 and ' + actor + '.created_steed_id != null', [
            _call(QUALIFY, {'caster_state': target, 'steed_state':
                           '{' + actor + '.created_steed_id}'}, ('_life_bond_steed_id',))])]
    after = [_when("_life_bond_steed_id != '' and " + actor + '.hp > _life_bond_hp_before', [
        _set('_life_bond_gain', actor + '.hp - _life_bond_hp_before'),
        _call(APPLY, {'caster_state': target, 'steed_state': '{_life_bond_steed_id}',
                     'amount': '{_life_bond_gain}'},
              ('_spell_target_summary', '_spell_target_detail',
               '_life_bond_restored', '_life_bond_steed_name')),
        _when('_spell_summary_rows != null', [
            {'list_push': {'list': '_spell_summary_rows', 'value': '_spell_target_summary'}}]),
        _when('_spell_detail_rows != null', [
            {'list_push': {'list': '_spell_detail_rows', 'value': '_spell_target_detail'}}])])]
    # Keep the original conditional gate around qualification, healing and its consequence.
    body = before + [{key: deepcopy(value) for key, value in original.items() if key != 'condition'}] + after
    rows[index:index + 1] = [_when(original.get('condition', 'true'), body)]


def bind_steed_life_bond(runtime):
    """Connect the selected healing families and native Aura/Vampiric follow-ups."""
    tools = runtime['tools']
    if QUALIFY in tools or APPLY in tools:
        raise ValueError('Life Bond source binding must be composed exactly once')
    tools[QUALIFY], tools[APPLY] = _qualifier(), _receiver()
    levels = {'cast_spell_healing': 'slot_level',
              'cast_spell_instantaneous_restoration': 'slot_level',
              'restore_shared_spell_healing_target': LEVEL,
              'restore_srd_healing_pool_target': LEVEL,
              'start_srd_aura_of_life_turn': '4',
              'resolve_srd_vampiric_touch_attack': '3',
              'complete_srd_vampiric_touch_attack': '3'}
    sites = {}
    for name, level in levels.items():
        tool = tools[name]
        if level == LEVEL:
            tool['parameters'].append(_parameter(LEVEL, 'integer', required=False,
                                                default=0, minimum=0, maximum=9))
        count = 0
        for rows in list(_effect_lists(tool['effects'])):
            for index in range(len(rows) - 1, -1, -1):
                if rows[index].get('call') == 'restore_hit_points':
                    _wrap_restore(rows, index, level)
                    count += 1
        if count != 1:
            raise ValueError('Keep the one native healing application in ' + name)
        sites[name] = count
    for name, receiver in (
        ('cast_spell_instantaneous_multi_target_healing', 'restore_shared_spell_healing_target'),
        ('cast_spell_instantaneous_healing_pool', 'restore_srd_healing_pool_target')):
        calls = [effect for rows in _effect_lists(tools[name]['effects']) for effect in rows
                 if effect.get('call') == receiver]
        if len(calls) != 1:
            raise ValueError('Keep the actual healing recipient loop in ' + name)
        calls[0]['params'][LEVEL] = '{slot_level}'
        sites[name] = 1
    bind_steed_life_bond_feedback(runtime)
    return sites


def _transport_feedback(tools, roots, fields, signal):
    """Follow the native result exports that already carry this same healing result."""
    owned = set(roots)
    while True:
        before = set(owned)
        for name, tool in tools.items():
            for rows in _effect_lists(tool['effects']):
                for effect in rows:
                    exports = effect.get('export_context', {})
                    if effect.get('call') not in owned or (exports != '*' and signal not in exports):
                        continue
                    if exports != '*':
                        exports.update({field: field for field in fields})
                    owned.add(name)
        if owned == before:
            break
    for name in owned:
        tools[name]['effects'][:0] = [_set(field, None) for field in fields]
    return owned


def bind_steed_life_bond_feedback(runtime):
    """Carry Aura of Life's actual HP gains to the existing public turn cards."""
    tools = runtime['tools']
    life_fields = ('_life_bond_restored', '_life_bond_steed_name')
    aura_owners = _transport_feedback(tools, {'start_srd_aura_of_life_turn'},
                                      life_fields, '_aura_life_healed')
    tools['process_srd_turn_effects']['effects'].insert(0, _set('_aura_life_healed', 0))
    # The turn processor exports its ordinary effect rows, rather than the aura's private result.
    for name, tool in tools.items():
        calls = [e for rows in _effect_lists(tool['effects']) for e in rows
                 if e.get('call') == 'process_srd_turn_effects' and
                 '_turn_effect_rows' in e.get('export_context', {})]
        if not calls:
            continue
        for call in calls:
            call['export_context'].update({field: field for field in (*life_fields, '_aura_life_healed')})
        tool['effects'][:0] = [_set('_aura_life_healed', 0), *[_set(field, None) for field in life_fields]]
        aura_owners.add(name)
        card = tool.get('metadata', {}).get('result_card')
        if card and isinstance(card.get('summary'), list):
            card['summary'].append({'label': 'Aura of Life · HP restored',
                'value': {'path': 'context._aura_life_healed'}, 'optional': True, 'omit_zero': True})
    life_row = {'label': 'Life Bond · steed HP restored',
        'value': {'path': 'context._life_bond_restored'}, 'optional': True, 'omit_zero': True}
    for name in aura_owners:
        card = tools[name].get('metadata', {}).get('result_card')
        if not card or not isinstance(card.get('summary'), list):
            continue
        card = deepcopy(card)
        card['summary'].append(deepcopy(life_row))
        if len(card['summary']) > 16:
            raise ValueError('Keep follow-up cards within the unchanged summary-row limit')
        tools[name]['metadata']['result_card'] = card
    begin = tools['begin_combat_turn']
    begin['effects'][:0] = [_set('_aura_life_creature_name', None), _set('_aura_life_hp_after', None)]
    begin['effects'].append(_when('_aura_life_healed > 0', [
        _set('_aura_life_creature_name', 'actor.name'), _set('_aura_life_hp_after', 'actor.hp')]))
    card = begin['metadata']['result_card']
    if 'title' not in card:
        card['title'] = 'Turn started'
    card['summary'].extend([
        {'label': 'Creature healed', 'value': {'path': 'context._aura_life_creature_name'}, 'optional': True},
        {'label': 'HP after', 'value': {'path': 'context._aura_life_hp_after'}, 'optional': True},
        {'label': 'Steed', 'value': {'path': 'context._life_bond_steed_name'}, 'optional': True},
    ])
    if len(card['summary']) > 16:
        raise ValueError('Keep the turn-start healing card within the unchanged summary-row limit')
