"""Expose existing last-target area damage concentration receipts in selected builds."""
from copy import deepcopy

PARTS = ('dc', 'outcome', 'roll')
TARGET = '_area_target_concentration_save_'
PUBLIC = '_spell_area_last_concentration_save_'


def _effect_lists(effects):
    yield effects
    for effect in effects:
        for branch in ('on_true', 'on_false'):
            if isinstance(effect.get(branch), list):
                yield from _effect_lists(effect[branch])
        loop = effect.get('for_each')
        if isinstance(loop, dict) and isinstance(loop.get('do'), list):
            yield from _effect_lists(loop['do'])


def _unique_call(tool, name, *, require_map=True):
    calls = [effect for group in _effect_lists(tool['effects']) for effect in group
             if effect.get('call') == name]
    assert len(calls) == 1, f'Inspect changed {name} owner/caller before binding receipts.'
    if require_map:
        assert isinstance(calls[0].get('export_context'), dict)
    return calls[0]


def _add_exports(effect, mapping):
    exports = effect['export_context']
    for destination, source in mapping.items():
        assert destination not in exports or exports[destination] == source
    exports.update(mapping)


def bind_area_damage_concentration_receipts(runtime):
    """Bridge already-produced last-target data through the outside-cast route.

    No targets / held globe / alternate subfamily: defaults are null. Multiple
    targets: this is explicitly the final processed target, not an aggregate or
    proof for earlier targets. Dragon's public Acid control has exactly one.
    Deferred begin/complete receipt exposure is outside this scoped bridge.
    """
    tools = runtime['tools']
    changed_names = ('resolve_spell_area_save_damage_target',
                     'cast_spell_instantaneous_area_save_damage', 'cast_spell')
    before = {name:deepcopy(tools[name]) for name in changed_names}
    protected = {name:deepcopy(tools[name]) for name in
                 ('resolve_srd_concentration_after_damage','execute_srd_qualified_scalar_packet',
                  'apply_srd_qualifying_damage','cast_spell_outside_combat','d20_test')}
    producer = tools['execute_srd_qualified_scalar_packet']['effects']
    first_branch = next(i for i,e in enumerate(producer)
                        if 'on_true' in e or 'on_false' in e or 'for_each' in e)
    for part in PARTS:
        initializers = [i for i,e in enumerate(producer)
                        if e.get('set') == {'field':'_concentration_save_'+part,'value':None}
                        and 'condition' not in e]
        assert len(initializers) == 1 and initializers[0] < first_branch
    wrapper = _unique_call(tools['apply_srd_qualifying_damage'],
                           'execute_srd_qualified_scalar_packet',require_map=False)
    exports = wrapper.get('export_context')
    assert exports == '*' or (isinstance(exports,dict) and all(
        exports.get('_concentration_save_'+part) == '_concentration_save_'+part for part in PARTS))
    leaf = _unique_call(tools['resolve_spell_area_save_damage_target'],
                        'apply_srd_qualifying_damage')
    assert 'condition' not in leaf, 'A newly conditional damage leaf needs explicit receipt defaults.'
    _add_exports(leaf, {TARGET+part:'_concentration_save_'+part for part in PARTS})

    family = tools['cast_spell_instantaneous_area_save_damage']
    defaults = [{'set':{'field':PUBLIC+part,'value':None}} for part in PARTS]
    if family['effects'][:len(defaults)] != defaults:
        assert not any(effect.get('set', {}).get('field', '').startswith(PUBLIC)
                       for group in _effect_lists(family['effects']) for effect in group)
        family['effects'][:0] = deepcopy(defaults)
    target = _unique_call(family, 'resolve_spell_area_save_damage_target')
    _add_exports(target, {PUBLIC+part:TARGET+part for part in PARTS})
    cast = _unique_call(tools['cast_spell'],'cast_spell_instantaneous_area_save_damage')
    _add_exports(cast, {PUBLIC+part:PUBLIC+part for part in PARTS})

    # Compare touched owners after stripping only this module's additions.
    # This detects any accidental semantic/state/metadata/declaration mutation.
    st = {name:deepcopy(tools[name]) for name in changed_names}
    original = _unique_call(before['resolve_spell_area_save_damage_target'],
                            'apply_srd_qualifying_damage')['export_context']
    for destination in (TARGET+part for part in PARTS):
        if destination not in original:
            _unique_call(st['resolve_spell_area_save_damage_target'],
                         'apply_srd_qualifying_damage')['export_context'].pop(destination)
    original_family = before['cast_spell_instantaneous_area_save_damage']
    if original_family['effects'][:len(defaults)] != defaults:
        del st['cast_spell_instantaneous_area_save_damage']['effects'][:len(defaults)]
    for owner, called in (('cast_spell_instantaneous_area_save_damage','resolve_spell_area_save_damage_target'),
                          ('cast_spell','cast_spell_instantaneous_area_save_damage')):
        original = _unique_call(before[owner],called)['export_context']
        changed = _unique_call(st[owner],called)['export_context']
        for destination in (PUBLIC+part for part in PARTS):
            if destination not in original:
                changed.pop(destination)
    assert st == before, 'Receipt bridge changed more than its declared context additions.'
    assert all(tools[name] == old for name,old in protected.items())
    return runtime
