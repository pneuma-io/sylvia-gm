"""Render actual material rows at their shared producer without changing stock.

List values are expressions/literals, not templates. The existing formatting
owner resolves the template while the actual supply/unit locals are in scope.
"""
from copy import deepcopy

OWNERS=('resolve_srd_spell_materials','resolve_srd_magic_jar_body_materials')
TEMPLATES={
 '{_supply.inventory_item} (retained)',
 '{_supply.inventory_item}: {_required_units}; {_remaining_units} remaining',
}

def _lists(value):
    if isinstance(value,list):
        yield value
        for child in value:yield from _lists(child)
    elif isinstance(value,dict):
        for child in value.values():yield from _lists(child)

def bind_material_card_formatting(runtime):
    """Repair both actual shared and generated body material-row producers."""
    changed=[]
    for name in OWNERS:
        if name not in runtime['tools']:
            if name==OWNERS[0]:raise ValueError('Actual shared material producer unavailable')
            continue
        original=runtime['tools'][name]
        owner=deepcopy(original);count=0
        for effects in list(_lists(owner['effects'])):
            for index in range(len(effects)-1,-1,-1):
                effect=effects[index]
                if not isinstance(effect,dict):continue
                push=effect.get('list_push',{})
                if push.get('list')!='_material_rows' or not isinstance(push.get('value'),dict):continue
                template=push['value'].get('value')
                if template not in TEMPLATES:continue
                effects.insert(index,{'call':'format_srd_information_projection',
                    'params':{'message':template},
                    'export_context':{'_srd_material_card_value':'_info_projection_message'}})
                push['value']['value']='_srd_material_card_value';count+=1
        if count not in (0,2):raise ValueError(name+': shared retained/consumed row shape changed')
        if count==0:
            formatted=[e for rows in _lists(owner['effects']) for e in rows
                if isinstance(e,dict) and e.get('call')=='format_srd_information_projection'
                and e.get('params',{}).get('message') in TEMPLATES
                and e.get('export_context')=={'_srd_material_card_value':'_info_projection_message'}]
            if len(formatted)!=2:raise ValueError(name+': exact material card producers unavailable')
        if count:
            runtime['tools'][name]=owner;changed.append(name)
    return changed
