"""Project actual committed zone saves and HP changes into existing result cards."""
from .srd_lite_profiles import lists
from .srd_control_behaviors import _each, _push
from .srd_timed_actor_defenses import _call, _set, _when


FIELDS=('summary_rows','detail_rows','applied')
PREFIX='_lite_zone_'


def collect_rows(tool, child):
    tool['effects'][:0]=[_set(PREFIX+field,0 if field=='applied' else []) for field in FIELDS]
    matches=0
    for effects in list(lists(tool['effects'])):
        for index in range(len(effects)-1,-1,-1):
            e=effects[index]
            if e.get('call')!=child:
                continue
            assert e.get('export_context')!='*', 'Use the actual named zone result boundary'
            exports=e.setdefault('export_context',{})
            exports.update({PREFIX+'child_'+f:PREFIX+f for f in FIELDS})
            effects[index:index]=[_set(PREFIX+'child_'+f,0 if f=='applied' else []) for f in FIELDS]
            offset=index+len(FIELDS)+1
            effects[offset:offset]=[_set(PREFIX+f,PREFIX+f+' + '+PREFIX+'child_'+f) for f in FIELDS]
            matches+=1
    assert matches, 'Missing actual zone result caller: '+child


def bind_zone_cards(runtime):
    tools=runtime['tools']
    for name,receipt in (('resolve_srd_environment_sphere_event','target_state.last_environment_sphere_save'),
                         ('settle_srd_call_lightning_target','target_state.last_call_lightning_save')):
        tool=tools[name]
        tool['effects'][:0]=[_set(PREFIX+f,0 if f=='applied' else []) for f in FIELDS]
        owners=0
        for effects in list(lists(tool['effects'])):
            for index in range(len(effects)-1,-1,-1):
                e=effects[index]
                if e.get('call')=='apply_srd_qualifying_damage':
                    effects[index:index]=[_set('_lite_zone_hp_before','target_state.hp'),
                                          _set('_lite_zone_temp_before','target_state.temp_hp')]
                if e.get('set',{}).get('field')!=receipt:
                    continue
                effects[index+1:index+1]=[_when("_sphere_event_source.spell == 'call_lightning' or _sphere_event_source.spell == 'insect_plague'",[
                    _set('_lite_zone_name',"'Insect Plague'"),
                    _when("_sphere_event_source.spell == 'call_lightning'",[_set('_lite_zone_name',"'Call Lightning'")]),
                    _call('record_srd_spell_target_result',{
                        'target':'{_target_state_id}',
                        'brief':'{_lite_zone_name}: {_effective_damage} {_sphere_profile.damage_type}; {_outcome}',
                        'detail':'{_sphere_profile.save_ability} save {_d20_test.results} with modifier {_sphere_save_modifier}, DC {_sphere_event_source.save_dc}: {_outcome}. Damage {_sphere_damage_roll.count}d{_sphere_damage_roll.sides} {_sphere_damage_roll.results} = {_sphere_damage_roll.total}; {_effective_damage} {_sphere_profile.damage_type} applied. HP {_lite_zone_hp_before} to {target_state.hp}; temporary HP {_lite_zone_temp_before} to {target_state.temp_hp}.'},
                        ('_spell_target_summary','_spell_target_detail')),
                    _push(PREFIX+'summary_rows','_spell_target_summary'),
                    _push(PREFIX+'detail_rows','_spell_target_detail'),
                    _set(PREFIX+'applied','_effective_damage'),
                ])]
                owners+=1
        assert owners==1, 'Project only the actual committed target receipt'
    for parent,child in (
        ('resolve_srd_environment_sphere_scene','resolve_srd_environment_sphere_event'),
        ('resolve_srd_call_lightning_bolt','settle_srd_call_lightning_target'),
        ('cast_spell_environment','resolve_srd_environment_sphere_scene'),
        ('cast_spell_environment','resolve_srd_call_lightning_bolt'),
        ('call_srd_lightning_source','resolve_srd_call_lightning_bolt'),
        ('resolve_srd_environment_hazard_event','resolve_srd_environment_sphere_event'),
        ('resolve_srd_environment_hazard_start_turn','resolve_srd_environment_sphere_event'),
        ('move_srd_scene_creature_celestial_segment','resolve_srd_environment_hazard_event'),
        ('move_srd_scene_creature_node','move_srd_scene_creature_celestial_segment'),
        ('move_srd_scene_creature','move_srd_scene_creature_node'),
        ('begin_combat_turn','resolve_srd_environment_hazard_start_turn'),
    ):
        collect_rows(tools[parent],child)
    tools['cast_spell_environment']['effects'].extend([
        _set('_spell_summary_rows','_spell_summary_rows + '+PREFIX+'summary_rows'),
        _set('_spell_detail_rows','_spell_detail_rows + '+PREFIX+'detail_rows')])
    tail=tools['finish_srd_retained_cast_tail']
    count=0
    for effects in list(lists(tail['effects'])):
        for index in range(len(effects)-1,-1,-1):
            e=effects[index]
            if e.get('call')!='cast_spell_environment':
                continue
            e['export_context'][PREFIX+'summary_rows']=PREFIX+'summary_rows'
            effects[index+1:index+1]=[_when('('+e['condition']+") and (_retained_cast_tail_choices.spell == 'call_lightning' or _retained_cast_tail_choices.spell == 'insect_plague')",[
                _set(PREFIX+'shown',0),
                _each(PREFIX+'summary_rows',PREFIX+'row',[_when(PREFIX+'shown < 3',[
                    _push('_spell_card_summary',PREFIX+'row'),_set(PREFIX+'shown',PREFIX+'shown + 1')])])])]
            count+=1
    assert count==1
    recast=tools['call_srd_lightning_source']
    recast['description']='Take one Action for a selected lightning strike from the current paid concentration source.'
    recast['effects'].append(_set(PREFIX+'event_count','len('+PREFIX+'summary_rows)'))
    recast['metadata']['result_card']={
        'version':1,'footprint':'compact','title':'Activate Call Lightning',
        'summary':[{'label':'Caster','value':{'path':'context._caster_state_id'}},
                   {'label':'Spell slots spent','value':0},
                   {'label':'Targets','value':{'path':'context.'+PREFIX+'event_count'}},
                   {'label':'Lightning damage applied','value':{'path':'context.'+PREFIX+'applied'}}],
        'details_label':'Targets and calculations','details':{'path':'context.'+PREFIX+'detail_rows'}}
    movement=tools['move_srd_scene_creature']
    movement['metadata']['result_card']['details_label']='Movement effects and calculations'
    movement['effects'].append(_set(PREFIX+'event_count','len('+PREFIX+'summary_rows)'))
    movement['metadata']['result_card']['summary'].append(
        {'label':'Zone damage events','value':{'path':'context.'+PREFIX+'event_count'},'omit_zero':True})
    # Existing Cloud of Daggers details remain first, followed by these actual rows.
    movement['effects'].append(_set('_lite_daggers_detail_rows','_lite_daggers_detail_rows + '+PREFIX+'detail_rows'))
    turn=tools['begin_combat_turn']
    turn['effects'].extend([_when('_turn_effect_rows == null',[_set('_turn_effect_rows',[])]),
                           _set('_turn_effect_rows','_turn_effect_rows + '+PREFIX+'detail_rows')])
