"""Display the native Web burn and saving throw on the existing turn cards."""
from .srd_lite_visual_rolls import _caption, _lists
from .srd_lite_steed_turns import NATIVE_BEGIN
from .srd_spell_environment import _push, _set, _when


def _feedback_effects():
    """Project only this actor's current committed receipts, retaining the original dice faces."""
    burn, save = 'actor.last_web_fire', 'actor.last_web_save'
    return [_when('combat.turn_active == true and combat.active_actor == _actor_id', [
        _when(burn + '.turn_sequence == combat.turn_sequence and ' + burn + '.raw_damage != null and ' +
              burn + '.damage_roll_total != null', [
            _caption('Burning Web · {' + burn + '.applied_damage} fire damage applied', '_web_turn_burn_caption'),
            _push('_turn_effect_rows', {'label': 'actor.name', 'value': '_web_turn_burn_caption',
                'equation': {'die_sides': burn + '.raw_damage.sides', 'roll': burn + '.raw_damage.total',
                    'rolls': burn + '.raw_damage.results',
                    'modifier': burn + '.damage_roll_total - ' + burn + '.raw_damage.total + ' + burn + '.raw_damage.modifier',
                    'total': burn + '.damage_roll_total'}}),
            _set('_turn_effect_damage_applied', '_turn_effect_damage_applied + ' + burn + '.applied_damage'),
            _set('_turn_effect_count', 'len(_turn_effect_rows)')]),
        _when(save + ".clock == 'combat' and " + save + ".timing == 'start' and " +
              save + '.turn == combat.turn_sequence and ' + save + '.roll != null and ' +
              save + '.modifier != null and ' + save + '.total != null and ' + save + '.save_dc != null', [
            _caption('Web · Dexterity save · DC {' + save + '.save_dc} · {' + save + '.outcome}', '_web_turn_save_caption'),
            _push('_turn_effect_rows', {'label': 'actor.name', 'value': '_web_turn_save_caption',
                'equation': {'die_sides': 20, 'roll': save + '.roll.total',
                    'modifier': save + '.modifier', 'total': save + '.total'}}),
            _set('_turn_effect_count', 'len(_turn_effect_rows)')])])]


def bind_web_turn_feedback(runtime):
    """Retain actual adjusted totals and project them before the native turn's existing equation collection."""
    tools = runtime['tools']
    save = tools['resolve_srd_web_event']
    rolls = [effect for rows in _lists(save['effects']) for effect in rows if effect.get('call') == 'd20_test']
    receipts = [effect['set']['value'] for rows in _lists(save['effects']) for effect in rows
                if effect.get('set', {}).get('field') == 'target_state.last_web_save']
    if len(rolls) != 1 or len(receipts) != 1:
        raise ValueError('Keep Web\'s sole native saving throw and receipt.')
    rolls[0]['export_context'].update({'_combined_modifier': '_combined_modifier', '_adjusted_total': '_adjusted_total'})
    receipts[0].update(modifier='_adjusted_total - _d20_test.total', total='_adjusted_total', save_dc='_web_source.save_dc')
    burn = tools['resolve_srd_retained_web_burning_start']
    receipts = [effect['set']['value'] for rows in _lists(burn['effects']) for effect in rows
                if effect.get('set', {}).get('field') == 'target_state.last_web_fire']
    if len(receipts) != 1:
        raise ValueError('Keep Web\'s sole native typed fire receipt.')
    receipts[0]['damage_roll_total'] = '_web_fire_amount'
    owner = tools[NATIVE_BEGIN]
    matches = [index for index, effect in enumerate(owner['effects'])
               if effect.get('set', {}).get('field') == '_spell_featured_equations']
    if len(matches) != 1:
        raise ValueError('Keep the native turn\'s sole existing equation projection.')
    owner['effects'][matches[0]:matches[0]] = _feedback_effects()
    return ['resolve_srd_web_event', 'resolve_srd_retained_web_burning_start', NATIVE_BEGIN]
