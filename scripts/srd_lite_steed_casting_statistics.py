"""Read the summoner's current casting statistics for native Steed abilities."""
from copy import deepcopy
from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _tool, _when
from .srd_lite_steed_healing_touch import OWNER

CURRENT = 'read_srd_lite_steed_casting_statistics'
CHARACTER = 'read_srd_lite_steed_character_statistics'
ABILITY = 'created_casting_ability'
EXPORTS = ('_steed_attack_bonus', '_steed_save_dc')


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _character_statistics(tools):
    """Reuse the paid cast owner's exact ability, recovery and proficiency calculations."""
    original = [deepcopy(effect) for effect in _walk(tools['cast_spell']['effects'])
        if effect.get('set', {}).get('field') in {'_casting_modifier', '_casting_proficiency'}
        and (effect['set'].get('value') in (
            '(_casting_character_data.abilities[_casting_ability] - 10) // 2',
            '(caster_state.ability_scores[_casting_ability] - 10) // 2',
            '2 + ((_casting_character_data.level - 1) // 4)'))]
    # YAML aliases can repeat a body's references; select the same three source effects once.
    unique = []
    for effect in original:
        if effect not in unique:
            unique.append(effect)
    if len(unique) != 3:
        raise ValueError('Review the actual paid character casting-statistics owner')

    def bind(value):
        if isinstance(value, str):
            return (value.replace('_casting_character_data', 'caster_character.data')
                .replace('_casting_ability', 'steed_state.' + ABILITY)
                .replace('_casting_modifier', '_steed_casting_modifier')
                .replace('_casting_proficiency', '_steed_casting_proficiency'))
        if isinstance(value, list):
            return [bind(child) for child in value]
        if isinstance(value, dict):
            return {key: bind(child) for key, child in value.items()}
        return value

    return _tool('Read current statistics from the actual validated summoner character.',
        [_entity('steed_state'), _entity('caster_state'),
         _parameter('caster_character', resolve='participant')], [
            _require('caster_character.participant_id == _caster_state_id and '
                     "caster_character.validation_status == 'validated' and "
                     "caster_character.package_id == 'sylvia.srd-lite.bg3'",
                     'Use the actual validated summoner character belonging to this Ruleset.'),
            _require(' or '.join('steed_state.' + ABILITY + " == '" + ability + "'" for ability in
                     ('strength', 'dexterity', 'constitution', 'intelligence', 'wisdom', 'charisma')),
                     'The character steed needs its actual original casting ability; recast an older steed.'),
            _require('caster_character.data.level >= 1 and caster_character.data.level <= 20 and '
                     'caster_character.data.abilities[steed_state.' + ABILITY + '] >= 3 and '
                     'caster_character.data.abilities[steed_state.' + ABILITY + '] <= 20',
                     'The summoner needs its authoritative current level and casting ability score.'),
            *bind(unique),
            _set('_steed_attack_bonus', '_steed_casting_modifier + _steed_casting_proficiency'),
            _set('_steed_save_dc', '8 + _steed_casting_modifier + _steed_casting_proficiency')])


def _current_statistics():
    """Resolve a character artifact by its saved owner, or read a live NPC casting profile."""
    return _tool('Read the genuine summoner\'s current spell attack bonus and save DC.',
        [_entity('steed_state'), _entity('caster_state')], [
            _call(OWNER, {'steed_state': '{_steed_state_id}', 'caster_state': '{_caster_state_id}'}),
            {'condition': "caster_state.creature_kind == 'character'", 'on_true': [
                _call(CHARACTER, {'steed_state': '{_steed_state_id}',
                    'caster_state': '{_caster_state_id}', 'caster_character': '{_caster_state_id}'}, EXPORTS)],
                'on_false': [
                    _require("caster_state.creature_kind == 'monster' and "
                             'caster_state.spellcasting_profile.ability != null and '
                             'caster_state.spellcasting_profile.attack_bonus != null and '
                             'caster_state.spellcasting_profile.save_dc >= 1 and '
                             'caster_state.spellcasting_profile.save_dc <= 40',
                             'Use the actual NPC summoner\'s current saved casting profile.'),
                    _set('_steed_attack_bonus', 'caster_state.spellcasting_profile.attack_bonus'),
                    _set('_steed_save_dc', 'caster_state.spellcasting_profile.save_dc')]}])


def bind_steed_casting_statistics(runtime):
    """Carry the engine-derived casting ability through the original paid creation path."""
    tools = runtime['tools']
    if CURRENT in tools or CHARACTER in tools:
        raise ValueError('Steed casting statistics must be composed exactly once')
    tools[CHARACTER] = _character_statistics(tools)
    tools[CURRENT] = _current_statistics()
    creation = tools['cast_spell_independent_created_creature']
    creation['parameters'].append(_parameter('casting_ability', required=False,
                                              default=None, model_hidden=True))
    finish = tools['finish_srd_steed_source_actor']
    finish['parameters'].append(_parameter('casting_ability', required=False,
                                            default=None, model_hidden=True))
    finish['effects'].append(_when('casting_ability != null',
                                   [_set('actor_state.' + ABILITY, 'casting_ability')]))
    finish_calls = [effect for effect in _walk(creation['effects'])
                    if effect.get('call') == 'finish_srd_steed_source_actor']
    if len(finish_calls) != 1:
        raise ValueError('Preserve the original single Steed finishing owner')
    finish_calls[0]['params']['casting_ability'] = '{casting_ability}'
    callers = []
    for name, tool in tools.items():
        for effect in _walk(tool.get('effects', [])):
            if effect.get('call') == 'cast_spell_independent_created_creature':
                effect['params']['casting_ability'] = '{_casting_profile.ability}'
                callers.append(name)
    if not callers:
        raise ValueError('The original paid Steed creation consumer is missing')
    return [CURRENT, CHARACTER, 'finish_srd_steed_source_actor',
            'cast_spell_independent_created_creature', *sorted(set(callers))]
