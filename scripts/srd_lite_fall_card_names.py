"""Use actual creature names in the retained fall receipt without changing fall mechanics."""
from .srd_timed_actor_defenses import _set, _when
from .srd_lite_visual_rolls import _lists


def _bind_public_fall_dice(tools):
    """Show the original roll on the public fall receipt; an unrolled fall has no dice row."""
    card=tools['resolve_srd_fall']['metadata']['result_card']
    if 'equation' in card or 'equations' in card:
        raise ValueError('Keep exactly one original public fall dice declaration')
    card['equation']={'die_sides':{'path':'context._fall_roll.sides'},
        'roll':{'path':'context._fall_roll.total'},'rolls':{'path':'context._fall_roll.results'},
        'modifier':0,'total':{'path':'context._fall_roll.total'},'optional':True}


def _bind_fall_damage_dice(tools):
    """Carry the resolved fall's original faces into its existing recipient rows."""
    settle=tools['settle_srd_scene_fall_target']['effects']
    fields=('_scene_fall_roll','_fall_hp_lost','_fall_temp_hp_absorbed')
    settle[:0]=[_set('_scene_fall_roll',None),_set('_fall_hp_lost',0),_set('_fall_temp_hp_absorbed',0)]
    calls=0
    for rows in _lists(settle):
        for effect in rows:
            if effect.get('call')=='resolve_srd_fall':
                exports=effect['export_context']
                if exports.get('_fall_effective_damage')!='_fall_effective_damage':
                    raise ValueError('Preserve the actual fall damage owner')
                exports['_scene_fall_roll']='_fall_roll'
                calls+=1
    if calls!=2:
        raise ValueError('Preserve ordinary and expired-descent fall damage')
    rows=tools['resolve_srd_scene_fall_event']['effects'][-1]['for_each']['do']
    for field in fields:
        rows[0]['export_context'][field]=field
    rows.append(_when('_scene_fall_roll != null and len(_scene_fall_roll.results) > 0',[
        {'call':'record_srd_result_text','params':{'text':
            'Bludgeoning damage dice; applied {_fall_effective_damage} (HP {_fall_hp_lost}; temporary HP {_fall_temp_hp_absorbed}).'},
         'export_context':{'_scene_fall_damage_caption':'_recorded_result_text'}},
        {'list_push':{'list':'_scene_fall_detail_rows','value':{
            'label':'_scene_fall_actor_name','value':'_scene_fall_damage_caption',
            'equation':{'die_sides':'_scene_fall_roll.sides','rolls':'_scene_fall_roll.results',
                'roll':'_scene_fall_roll.total','modifier':0,'total':'_scene_fall_roll.total'}}}}
    ]))


def bind_fall_card_names(runtime):
    """Capture the resolved body name and reuse the original row formatter."""
    tools=runtime['tools']
    settle=tools['settle_srd_scene_fall_target']['effects']
    index=next(i for i,effect in enumerate(settle) if effect.get('set',{}).get('field')=='_fall_effective_damage')
    settle[index+1:index+1]=[
        _set('_scene_fall_actor_name','_target_state_id'),
        _when("target_state.name != null and target_state.name != ''",[_set('_scene_fall_actor_name','target_state.name')])]
    rows=tools['resolve_srd_scene_fall_event']['effects'][-1]['for_each']['do']
    if rows[0]['call']!='settle_srd_scene_fall_target' or rows[1]['call']!='record_srd_result_text':
        raise ValueError('Preserve the actual fall settlement and text formatter')
    rows[0]['export_context']['_scene_fall_actor_name']='_scene_fall_actor_name'
    rows[1]['params']['text']=rows[1]['params']['text'].replace('{_resolve_fall_row.actor_id}','{_scene_fall_actor_name}')
    for effect in rows[2:]:
        row=effect['list_push']['value']
        if row['label']!='_resolve_fall_row.actor_id':
            raise ValueError('Preserve the original fall row label')
        row['label']='_scene_fall_actor_name'
    _bind_fall_damage_dice(tools)
    _bind_public_fall_dice(tools)
