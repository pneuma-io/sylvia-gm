"""Small owned Guardian movement hook using the accepted exact body geometry.

Independently authored from SRD 5.2.1 p137; keep existing start-turn semantics.
"""
def _effect_lists(rows):
    yield rows
    for effect in rows:
        for branch in ('on_true','on_false'):
            yield from _effect_lists(effect.get(branch,[]))
        if 'for_each' in effect:
            yield from _effect_lists(effect['for_each'].get('do',[]))


def _carry_guardian_results(owner,child):
    """Aggregate only committed Guardian rows and damage across an existing movement call."""
    fields={'rows':[],'damage':0}
    owner['effects'][0:0]=[{'set':{'field':'_guardian_movement_'+name,'value':value}}
        for name,value in fields.items()]
    count=0
    for rows in list(_effect_lists(owner['effects'])):
        for index in range(len(rows)-1,-1,-1):
            effect=rows[index]
            if effect.get('call')!=child:
                continue
            if 'condition' in effect or effect.get('export_context')=='*':
                raise ValueError('Guardian result propagation requires a named unconditional call')
            effect.setdefault('export_context',{}).update({
                '_guardian_child_'+name:'_guardian_movement_'+name for name in fields})
            rows[index+1:index+1]=[{'set':{'field':'_guardian_movement_'+name,
                'value':'_guardian_movement_'+name+' + _guardian_child_'+name}} for name in fields]
            count+=1
    if not count:
        raise ValueError('Guardian result propagation lost its actual movement consumer')


def bind_guardian_movement_entry(runtime):
    tools=runtime['tools'];name='resolve_srd_scope96_guardian_move'
    assert name not in tools
    assert runtime['definitions']['spells']['guardian_of_faith']['family']=='created_spell_guardian'
    begin=tools['begin_combat_turn']
    epoch={'condition':'lasting_state.has_guardian_sources == true','on_true':[
        {'set':{'field':'lasting_state.guardian_entry_epoch','value':0},'condition':'lasting_state.guardian_entry_epoch == null'},
        {'set':{'field':'lasting_state.guardian_entry_epoch','value':'lasting_state.guardian_entry_epoch + 1'}}]}
    begin['effects'].insert(0,epoch)
    segment=tools['move_srd_scene_creature_environment_segment']['effects']
    indices=[i for i,e in enumerate(segment) if 'spatial_mutation' in e and e['spatial_mutation'].get('patch',{}).get('position')=='{destination}']
    assert len(indices)==1
    hook={'condition':'lasting_state.has_guardian_sources == true and _route_distance > 0.000000001','on_true':[
        {'call':name,'params':{'actor_state':'{_actor_state_id}','start':'{_move_origin}','end':'{destination}'},
         'export_context':{'_guardian_movement_rows':'_guardian_movement_rows',
                           '_guardian_movement_damage':'_guardian_movement_damage'}}]}
    segment.insert(indices[0],hook)
    segment[0:0]=[{'set':{'field':'_guardian_movement_rows','value':[]}},
                  {'set':{'field':'_guardian_movement_damage','value':0}}]
    tools[name]={
        'description':'Resolve the first actual Guardian entry movement in a recorded turn through the existing body-segment and damage owners.',
        'parameters':['param_actor_state_d3c054b169a46c77','param_lasting_state_f791c68f01bb49c8','param_start_d98e3be2ca5dca70','param_end_2ff639971e9448b4'],
        'effects':[
            {'set':{'field':'_guardian_movement_rows','value':[]}},
            {'set':{'field':'_guardian_movement_damage','value':0}},
            {'set':{'field':'lasting_state.guardian_entry_epoch','value':0},'condition':'lasting_state.guardian_entry_epoch == null'},
            {'set':{'field':'_owned_guardian_ids','value':'lasting_state.spell_force_ids'}},
            {'for_each':{'collection':'_owned_guardian_ids','as':'_owned_guardian_id','do':[
                {'set':{'field':'_owned_guardian','value':'lasting_state.spell_force_records[_owned_guardian_id]'}},
                {'condition':"_owned_guardian.active == true and _owned_guardian.spell == 'guardian_of_faith' and actor_state.is_alive == true and lasting_state.lasting_tick < _owned_guardian.expires_tick",'on_true':[
                    {'call':'qualify_srd_scene_enemy','params':{'caster_id':'{_owned_guardian.caster}','target_id':'{_actor_state_id}'},'export_context':{'_scene_is_enemy':'_scene_is_enemy'}},
                    {'condition':'_scene_is_enemy == true','on_true':[
                        {'call':'resolve_srd_environment_body_segment','params':{'moving_subject':'{_actor_state_id}','fixed_subject':'{_owned_guardian_id}','start':'{start}','end':'{end}','radius':10},'export_context':{'_body_hit':'_body_hit','_body_entry':'_body_entry'}},
                        {'condition':'_body_hit == true and _body_entry > 0.000000001 and (_owned_guardian.entry_epochs == null or _owned_guardian.entry_epochs[_actor_state_id] != lasting_state.guardian_entry_epoch)','on_true':[
                            {'set':{'field':'lasting_state.spell_force_records.{_owned_guardian_id}.entry_epochs','value':{}},'condition':'_owned_guardian.entry_epochs == null'},
                            {'set':{'field':'lasting_state.spell_force_records.{_owned_guardian_id}.entry_epochs.{_actor_state_id}','value':'lasting_state.guardian_entry_epoch'}},
                            {'call':'measure_srd_guardian_distance','params':{'force_id':'{_owned_guardian_id}','target_state':'{_actor_state_id}'},'export_context':{'_guardian_observer':'_guardian_observer','_guardian_target_scene':'_guardian_target_scene'}},
                            {'call':'damage_srd_guardian_target','params':{'force_id':'{_owned_guardian_id}','target_state':'{_actor_state_id}','observer':'{_guardian_observer}','target_scene':'{_guardian_target_scene}'},
                             'export_context':{'_guardian_target_rows':'_spell_detail_rows',
                                               '_guardian_target_damage':'_affliction_trigger_damage'}},
                            {'condition':'_guardian_target_rows != null','on_true':[
                                {'set':{'field':'_guardian_movement_rows','value':'_guardian_movement_rows + _guardian_target_rows'}}]},
                            {'condition':'_guardian_target_damage != null','on_true':[
                                {'set':{'field':'_guardian_movement_damage','value':'_guardian_movement_damage + _guardian_target_damage'}}]}
                        ]}
                    ]}
                ]}
            ]}}
        ],'metadata':{'category':'internal','visible_to_player':False,'costs_action':False}}
    for parent,child in (
        ('move_srd_scene_creature_celestial_segment','move_srd_scene_creature_environment_segment'),
        ('move_srd_scene_creature_node','move_srd_scene_creature_celestial_segment'),
        ('move_srd_scene_creature','move_srd_scene_creature_node')):
        _carry_guardian_results(tools[parent],child)
    return ['begin_combat_turn','move_srd_scene_creature_environment_segment',name]


def bind_guardian_movement_card(runtime):
    """Expose Guardian results after Cloud of Daggers binds the shared movement card."""
    public=runtime['tools']['move_srd_scene_creature']
    card=public['metadata']['result_card']
    if card.get('details')!={'path':'context._lite_daggers_detail_rows'}:
        raise ValueError('Preserve the existing movement calculations')
    public['effects'].append({'set':{'field':'_guardian_movement_card_rows',
        'value':'_lite_daggers_detail_rows + _guardian_movement_rows'}})
    card['details']={'path':'context._guardian_movement_card_rows'}
    card['details_label']='Spell effects and calculations'
    card['summary'].append({'label':'Guardian radiant damage',
        'value':{'path':'context._guardian_movement_damage'},'omit_zero':True})
