"""Show actual damage-triggered concentration saves beside each area recipient."""
from copy import deepcopy


def bind_spell_card_names(runtime, profiles):
    """Use the approved roster's display names without editing casting requirements."""
    names={row['identity']:row['name'] for row in profiles}
    if len(names)!=96:
        raise ValueError('Spell card names require the exact approved roster')
    runtime['definitions']['srd_lite_spell_display_names']=names
    for operation in ('cast_spell','cast_spell_outside_combat'):
        owner=runtime['tools'][operation]
        card=owner['metadata']['result_card']
        if card['title']!={'path':'context._casting_requirements.name'}:
            raise ValueError('The spell card title binding changed')
        owner['effects'].append({'set':{'field':'_spell_display_name',
            'value':'definitions.srd_lite_spell_display_names[spell]'}})
        card['title']={'path':'context._spell_display_name'}


def _effects(rows):
    for effect in rows:
        yield effect
        for branch in ('on_true', 'on_false'):
            yield from _effects(effect.get(branch, []))
        if 'for_each' in effect:
            yield from _effects(effect['for_each']['do'])


def bind_area_concentration_cards(runtime):
    """Expose existing native dice without changing damage or concentration rules."""
    changed = []
    for name in ('resolve_spell_area_save_damage_target', 'resolve_srd_dragon_breath_target'):
        owner = deepcopy(runtime['tools'][name])
        calls = [effect for effect in _effects(owner['effects'])
                 if effect.get('call') == 'apply_srd_qualifying_damage']
        if len(calls) != 1 or 'condition' in calls[0]:
            raise ValueError('Area concentration card requires the unchanged damage receiver')
        call = calls[0]
        for part in ('dc', 'outcome', 'roll', 'modifier', 'total'):
            call['export_context']['_area_target_concentration_save_' + part] = '_concentration_save_' + part
        row = {'condition': '_area_target_concentration_save_roll != null', 'on_true': [
            {'set': {'field': '_area_concentration_total', 'value':
                '_area_target_concentration_save_total'}},
            {'call': 'format_srd_information_projection', 'params': {'message':
                'Concentration saving throw against DC {_area_target_concentration_save_dc}: '
                '{_area_target_concentration_save_outcome}. D20 {_area_target_concentration_save_roll.results}; '
                'selected {_area_target_concentration_save_roll.total} + '
                '({_area_target_concentration_save_modifier}) = {_area_concentration_total}.'},
             'export_context': {'_area_concentration_text': '_info_projection_message'}},
            {'call': 'format_srd_information_projection',
             'params': {'message': '{target.name} concentration'},
             'export_context': {'_area_concentration_label': '_info_projection_message'}},
            {'list_push': {'list': '_spell_detail_rows', 'value': {
                'label': '_area_concentration_label', 'value': '_area_concentration_text'}}},
        ]}
        existing = [index for index, effect in enumerate(owner['effects'])
                    if effect.get('condition') == '_area_target_concentration_save_roll != null']
        if len(existing) > 1:
            raise ValueError('Concentration display has more than one receipt owner')
        if existing:
            previous = owner['effects'][existing[0]]
            if not any(effect.get('set', {}).get('field') == '_area_concentration_total'
                       for effect in previous.get('on_true', [])):
                raise ValueError('Inspect changed concentration display before replacing it')
            owner['effects'][existing[0]] = row
        else:
            owner['effects'].append(row)
        runtime['tools'][name] = owner
        changed.append(name)
    return changed
