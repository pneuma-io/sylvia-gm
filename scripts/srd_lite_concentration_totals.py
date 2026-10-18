"""Carry the final saving-throw total into concentration display receipts."""


def _effects(rows):
    for effect in rows:
        yield effect
        for branch in ('on_true', 'on_false'):
            yield from _effects(effect.get(branch, []))
        if 'for_each' in effect:
            yield from _effects(effect['for_each']['do'])


def _export(tools, owner, child, mapping):
    calls = [effect for effect in _effects(tools[owner]['effects']) if effect.get('call') == child]
    if len(calls) != 1:
        raise ValueError('Keep the concentration receipt owner: ' + owner)
    exports = calls[0].get('export_context')
    if exports == '*':
        return
    if not isinstance(exports, dict):
        raise ValueError('Keep named concentration exports: ' + owner)
    for name, source in mapping.items():
        if name in exports and exports[name] != source:
            raise ValueError('Concentration export changed: ' + name)
        exports[name] = source


def bind_concentration_roll_totals(runtime):
    """Expose the existing final D20 values without changing rolls, outcomes or state writes."""
    tools = runtime['tools']
    names = ('modifier', 'total')
    for owner in ('resolve_srd_concentration_after_damage', 'execute_srd_qualified_scalar_packet'):
        effects = tools[owner]['effects']
        for name in names:
            field = '_concentration_save_' + name
            existing = [effect for effect in effects if effect.get('set', {}).get('field') == field]
            if existing and existing != [{'set': {'field': field, 'value': None}}]:
                raise ValueError('Concentration receipt initialization changed: ' + owner)
            if not existing:
                effects.insert(0, {'set': {'field': field, 'value': None}})
    _export(tools, 'resolve_srd_concentration_after_damage', 'd20_test', {
        '_concentration_save_modifier': '_combined_modifier',
        '_concentration_save_total': '_adjusted_total',
    })
    for owner, child, destination, source in (
        ('execute_srd_qualified_scalar_packet', 'resolve_srd_concentration_after_damage',
         '_concentration_save_', '_concentration_save_'),
        ('apply_srd_qualifying_damage', 'execute_srd_qualified_scalar_packet',
         '_concentration_save_', '_concentration_save_'),
        ('resolve_spell_area_save_damage_target', 'apply_srd_qualifying_damage',
         '_area_target_concentration_save_', '_concentration_save_'),
        ('resolve_srd_dragon_breath_target', 'apply_srd_qualifying_damage',
         '_area_target_concentration_save_', '_concentration_save_'),
        ('cast_spell_instantaneous_area_save_damage', 'resolve_spell_area_save_damage_target',
         '_spell_area_last_concentration_save_', '_area_target_concentration_save_'),
        ('cast_spell', 'cast_spell_instantaneous_area_save_damage',
         '_spell_area_last_concentration_save_', '_spell_area_last_concentration_save_'),
    ):
        _export(tools, owner, child, {destination+name: source+name for name in names})
    family = tools['cast_spell_instantaneous_area_save_damage']['effects']
    for name in names:
        field = '_spell_area_last_concentration_save_' + name
        if not any(effect.get('set', {}).get('field') == field for effect in family):
            family.insert(0, {'set': {'field': field, 'value': None}})
    return ['resolve_srd_concentration_after_damage', 'execute_srd_qualified_scalar_packet',
            'resolve_spell_area_save_damage_target', 'resolve_srd_dragon_breath_target',
            'cast_spell_instantaneous_area_save_damage', 'cast_spell']
