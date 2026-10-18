"""Restore the existing Counterspell leaf at the actual shared cast owner.

This conditional composition changes no grants, resource owners, trigger guards,
save resolution, spell headers, or other reaction profiles.
"""
from copy import deepcopy


def _nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _nodes(child)


def bind_original_counterspell_dispatch(runtime, *, selected=('counterspell',)):
    """Return the exact changed tool names; fail closed on an unexpected owner."""
    if 'counterspell' not in selected:
        return []
    tools = runtime['tools']
    owner = tools['cast_spell']
    has_dispatch = any(node.get('call') == 'cast_spell_reaction' for node in _nodes(owner['effects']))
    # These are genuine original producers/consumers, never fabricated contexts.
    for name in ('cast_spell_reaction', 'resolve_srd_counterspell',
                 'validate_srd_reaction_spell_trigger', 'spend_spell_casting_budget'):
        if name not in tools:
            raise ValueError('Missing original Counterspell owner: ' + name)
    required_owners = ('validate_srd_reaction_spell_trigger', 'spend_spell_casting_budget')
    if not all(any(node.get('call') == name for node in _nodes(owner['effects']))
               for name in required_owners):
        raise ValueError('Counterspell requires the existing trigger and paid budget owners.')
    effects = deepcopy(owner['effects'])
    source_defaults = [index for index, effect in enumerate(effects)
                       if effect.get('set', {}).get('field') == '_spell_source_cast_id'
                       and effect['set']['value'] == "''"]
    debits = [index for index, effect in enumerate(effects)
              if effect.get('decrement', {}).get('field') == 'caster_state.resources.{_cast_resource}']
    if len(source_defaults) != 1 or len(debits) != 1 or source_defaults[0] >= debits[0]:
        raise ValueError('The original resolved-cast source and slot-debit anchors must be unique.')
    # Insert immediately before the existing terminal debit. Every validated
    # casting profile, selected slot, budget, and cast-source default exists here.
    # This also serves the original direct reaction and pending completion callers,
    # which both delegate to cast_spell instead of inheriting private contexts.
    effects.insert(debits[0], {
        'condition': "spell == 'counterspell' and _spell_cast_failed != true and "
                     "_pending_spell_operation_lost != true",
        'on_true': [{
            'call': 'cast_spell_reaction',
            'params': {'caster_state': '{_caster_state_id}', 'spell': '{spell}',
                       'reaction_trigger_id': '{reaction_trigger_id}',
                       'save_dc': '{_casting_profile.save_dc}', 'targets': '{targets}',
                       'actual_spell_level': '{_slot_level}',
                       'spell_cast_id': '{_spell_source_cast_id}'},
            'export_context': {'_spell_summary_rows': '_spell_summary_rows',
                               '_spell_detail_rows': '_spell_detail_rows'},
        }, {
            # Reuse the original leaf's factual saved-save feedback. The public
            # reaction wrapper explicitly exports this existing card field.
            'set': {'field': '_spell_card_summary', 'value': '_spell_summary_rows'},
        }],
    })
    changed = []
    if not has_dispatch:
        tools['cast_spell'] = {**owner, 'effects': effects}
        changed.append('cast_spell')
    wrapper = tools['cast_srd_reaction_spell']
    title = wrapper.get('metadata', {}).get('result_card', {}).get('title')
    if title != {'path': 'context._casting_requirements.name'}:
        raise ValueError('Unexpected original public reaction card-title consumer.')
    wrapper_effects = deepcopy(wrapper['effects'])
    forwards = [node for node in _nodes(wrapper_effects) if node.get('call') == 'cast_spell']
    if len(forwards) != 1 or not isinstance(forwards[0].get('export_context'), dict):
        raise ValueError('The public reaction owner must explicitly forward the actual cast result.')
    if '_casting_requirements' not in forwards[0]['export_context']:
        if not any(node.get('table_lookup', {}).get('table') == 'definitions.spell_casting'
                   and node.get('store_as') == '_casting_requirements'
                   for node in _nodes(owner['effects'])):
            raise ValueError('The genuine original card-title source is absent.')
        forwards[0]['export_context']['_casting_requirements'] = '_casting_requirements'
        tools['cast_srd_reaction_spell'] = {**wrapper, 'effects': wrapper_effects}
        changed.append('cast_srd_reaction_spell')
    return changed
