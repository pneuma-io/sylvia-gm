"""Qualify native Otherworldly Slam attacks against the actual current summoner."""
from copy import deepcopy
from .srd_timed_actor_defenses import _call, _require, _set, _when
from .srd_lite_steed_casting_statistics import CURRENT, EXPORTS
from .srd_lite_damage_roll_receipts import _lists

OWNERS = ('resolve_adjudicated_creature_attack', 'begin_srd_creature_attack',
          'continue_srd_creature_attack_targeting')
CACHED = 'attack_modifier == actor.created_attack_bonus and '
SAVED = '_steed_slam_original_roll'
DISPLAY_MODIFIER = '_steed_slam_display_modifier'
CARD_OWNERS = ('resolve_adjudicated_creature_attack', 'complete_srd_creature_attack',
               'resume_srd_creature_attack_hit')


def bind_steed_slam(runtime):
    """Keep native attack/damage and phase owners, changing only the Steed modifier preflight."""
    tools = runtime['tools']
    selected = tuple(name for name, tool in tools.items()
        if any(CACHED in guard['condition'] for guard in tool.get('pre_conditions', [])))
    if set(selected) != set(OWNERS):
        raise ValueError('Preserve all three native Otherworldly Slam attack owners')
    for name in OWNERS:
        tool = deepcopy(tools[name])
        guards = [guard for guard in tool['pre_conditions'] if CACHED in guard['condition']]
        if len(guards) != 1:
            raise ValueError('Keep the single exact source profile guard: ' + name)
        guards[0]['condition'] = guards[0]['condition'].replace(CACHED, '')
        tool['effects'].insert(0, _when("actor.created_spell_id == 'find_steed' and "
            'actor.form_attack_in_progress != true', [
                _call(CURRENT, {'steed_state': '{_actor_id}',
                    'caster_state': '{actor.created_caster_state_id}'}, EXPORTS),
                _require('attack_modifier == _steed_attack_bonus',
                         'Otherworldly Slam needs the summoner\'s current spell attack bonus.')]))
        tools[name] = tool
    return list(OWNERS)


def bind_steed_slam_widgets(runtime):
    """Carry immutable native Slam dice to existing attack cards through their real phase exports."""
    tools = runtime['tools']
    providers = {'resolve_adjudicated_creature_attack', 'finish_srd_creature_attack_hit_packet'}
    changed = set(providers)
    for name in sorted(providers):
        tool = tools[name]
        sites = 0
        for rows in list(_lists(tool['effects'])):
            for index in range(len(rows) - 1, -1, -1):
                effect = rows[index]
                if 'dice' not in effect or effect.get('store_as') != '_damage_roll':
                    continue
                rows[index + 1:index + 1] = [_when("actor.created_spell_id == 'find_steed' and "
                    'actor.form_attack_in_progress != true and _damage_roll != null',
                    [_set(SAVED, '_damage_roll')])]
                sites += 1
        if sites != 1:
            raise ValueError('Keep the single original Slam damage dice owner: ' + name)
        tool['effects'][0:0] = [_set(SAVED, None), _set(DISPLAY_MODIFIER, None)]
        tool['effects'].append(_when(SAVED + ' != null',
            [_set(DISPLAY_MODIFIER, '_damage_incoming - ' + SAVED + '.total')]))
    while True:
        inherited = set()
        for name, tool in tools.items():
            for rows in _lists(tool['effects']):
                for effect in rows:
                    if effect.get('call') not in providers:
                        continue
                    exports = effect.get('export_context')
                    if exports == '*':
                        inherited.add(name)
                    elif isinstance(exports, dict) and exports.get('_damage_roll') == '_damage_roll':
                        exports[SAVED] = SAVED
                        exports[DISPLAY_MODIFIER] = DISPLAY_MODIFIER
                        inherited.add(name)
        new = inherited - providers
        changed.update(inherited)
        if not new:
            break
        providers.update(new)
    for name in CARD_OWNERS:
        card = deepcopy(tools[name]['metadata']['result_card'])
        if card['title'] != 'Attack':
            raise ValueError('Keep the existing native attack card: ' + name)
        card['summary'].append({'label': 'Slam damage before defenses', 'optional': True,
            'value': {'path': 'context.' + SAVED + '.results'}, 'equation': {
                'die_sides': 8, 'rolls': {'path': 'context.' + SAVED + '.results'},
                'roll': {'path': 'context.' + SAVED + '.total'},
                'modifier': {'path': 'context.' + DISPLAY_MODIFIER},
                'total': {'path': 'context._damage_incoming'}}})
        tools[name]['metadata']['result_card'] = card
    completion = tools['complete_srd_pending_action']
    if completion['metadata']['result_card']['summary'] != {'path': 'context._pending_card_summary'}:
        raise ValueError('Keep the original pending-action card and its native summary owner')
    # Extend the actual completed receipt; retain the original card for other pending action kinds.
    completion['effects'] += [
        _when('_attack_roll != null and _pending_card_summary != null', [
            {'list_push': {'list': '_pending_card_summary', 'value': {
                'label': "'Attack roll'", 'value': '_attack_outcome', 'equation': {
                    'die_sides': 20, 'roll': '_attack_roll.total',
                    'modifier': '_attack_card_modifier', 'total': '_attack_adjusted_total'}}}}]),
        _when(SAVED + ' != null and _pending_card_summary != null', [
            {'list_push': {'list': '_pending_card_summary', 'value': {
                'label': "'Slam damage before defenses'", 'value': SAVED + '.results',
                'equation': {'die_sides': 8, 'rolls': SAVED + '.results', 'roll': SAVED + '.total',
                    'modifier': DISPLAY_MODIFIER, 'total': '_damage_incoming'}}}}])]
    return sorted(changed | set(CARD_OWNERS) | {'complete_srd_pending_action'})
