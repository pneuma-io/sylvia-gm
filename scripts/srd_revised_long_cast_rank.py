"""Reuse the real SRD resource-rank rules before a longer casting starts.

The direct cast and pending action retain their own guards. This narrowly
extends that same preflight to begin_srd_spell_cast's actual internal owner.
No spell selection, pool, duration, material, or completion policy changes.
"""
from copy import deepcopy

RANK_CONDITION = '_slot_level >= _spell_definition.base_level'


def _walk(value):
    if isinstance(value,dict):
        yield value
        for child in value.values():yield from _walk(child)
    elif isinstance(value,list):
        for child in value:yield from _walk(child)


def bind_revised_long_cast_rank(runtime):
    tools=runtime['tools']
    effects=tools['begin_srd_spell_cast_internal']['effects']
    source=tools['cast_spell']['effects']
    setters=[e for e in source if e.get('set',{}).get('field')=='_slot_level']
    expected=[{'set':{'field':'_slot_level','value':0}},
        *[{'set':{'field':'_slot_level','value':rank},
            'condition':f"_cast_resource == 'spell_slot_{rank}'"} for rank in range(1,10)],
        {'set':{'field':'_slot_level','value':'_npc_spell.casting_level'},'condition':'_casting_pc_actor != true'},
        {'set':{'field':'_slot_level','value':'_spell_definition.base_level'},'condition':'_feature_cast == true'},
        {'set':{'field':'_slot_level','value':'_spell_definition.base_level'},'condition':'_ritual_cast == true'}]
    if setters!=expected:raise ValueError('Review actual canonical paid/NPC/feature/ritual rank resolution')
    guards=[e for e in tools['begin_srd_spell_action']['effects']
        if e.get('require',{}).get('condition')==RANK_CONDITION]
    if len(guards)!=1:raise ValueError('Review actual pending-begin rank guard')
    lookups=[e for e in effects if e.get('table_lookup',{}).get('table')=='definitions.spells'
        and e.get('table_lookup',{}).get('key')=='spell' and e.get('store_as')=='_spell_definition']
    if len(lookups)!=1:raise ValueError('Review actual selected-spell lookup in long-cast owner')
    anchors=[i for i,e in enumerate(effects) if e.get('set',{}).get('field')=='_card_slots_before']
    if len(anchors)!=2:raise ValueError('Review actual resource-normalized before-material anchor')
    join=anchors[0]
    existing=[e for e in effects if e.get('require',{}).get('condition')==RANK_CONDITION]
    if existing:
        own=[e for e in effects[:join] if e.get('set',{}).get('field')=='_slot_level']
        if len(existing)!=1 or own!=expected:raise ValueError('Review competing longer-casting rank gate')
        return {'already_applied':True}
    if any('_slot_level' in str(e) for e in effects):raise ValueError('Review existing longer-cast rank consumer')
    dependencies={'_cast_resource','_npc_spell','_casting_pc_actor','_feature_cast','_ritual_cast','_spell_definition'}
    if any(row.get('set',{}).get('field') in dependencies
        for effect in effects[join:] for row in _walk(effect)):
        raise ValueError('Review resource/rank input mutation after long-cast anchor')
    if any(row.get('call') in {'resolve_srd_spell_materials','spend_spell_casting_budget'}
        for effect in effects[:join] for row in _walk(effect)):
        raise ValueError('Review long-cast cost before proposed rank gate')
    effects[join:join]=deepcopy(setters+[guards[0]])
    return {'applied':True,'owner':'begin_srd_spell_cast_internal',
        'public_caller':'begin_srd_spell_cast','original_rank_setters':len(setters),
        'guard_source':'begin_srd_spell_action','new_parameters':0,
        'preserved_completion_checks':True,'pre_cost':True}
