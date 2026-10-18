"""Resolve mounted Prone and involuntary movement through the actual rider's saved Dexterity save."""
from .build_srd_5_2_1_mechanics import _tool_schema
from .srd_timed_actor_defenses import _call,_entity,_parameter,_require,_set,_tool,_when
from .srd_scene_relations import _tool_scene
from .srd_lite_steed_fey_step import RIDER_FIELD,MOUNT_FIELD
from .srd_lite_steed_healing_touch import OWNER
from .srd_lite_steed_mount import _body,_distance,_placement
from .srd_lite_steed_rider_movement import _seated,PREPARE,FOLLOW
from .srd_lite_steed_disappearance import QUEUE as RELEASED,SETTLE
from .srd_lite_steed_teleport_links import FEY_MOVE

ACTOR='queue_srd_otherworldly_prone_actor'
PAIR='queue_srd_otherworldly_prone_pair'
OPEN='open_srd_otherworldly_mount_balance'
ONE='open_srd_otherworldly_mount_balance_pair'
RESOLVE='resolve_srd_otherworldly_mount_balance'
FORCE='begin_srd_otherworldly_forced_movement'
QUEUE='state.otherworldly_mount_balance_queue'
KIND='otherworldly_mount_balance'


def _pair_bodies():
    """Authenticate the saved source pair and both actual same-plane riding boxes."""
    return [
        _call(OWNER,{'steed_state':'{_steed_state_id}','caster_state':'{steed_state.created_caster_state_id}'}),
        _require('rider_state.'+MOUNT_FIELD+' == _steed_state_id and steed_state.'+RIDER_FIELD+' == _rider_state_id',
            'Use the actual reciprocal mounted pair.'),
        *_body('{_steed_state_id}','_steed_state_id','_steed_mount_body'),
        *_body('{_rider_state_id}','_rider_state_id','_steed_rider_body'),
        *_seated('_steed_mount_body.position'),
        _require('_steed_mount_body.metadata.plane == _steed_rider_body.metadata.plane',
            'Preserve the actual mounted plane.')]


def _queue_pair():
    """Record a real transition once; refreshing an existing Prone condition does not reroll it."""
    return _tool_scene('Queue this actual mounted Prone incident.',
        [_entity('steed_state'),_entity('rider_state'),_entity('trigger_actor')],_pair_bodies()+[
        _require('_trigger_actor_id == _steed_state_id or _trigger_actor_id == _rider_state_id',
            'Preserve the actual newly Prone creature in this mounted pair.'),
        _when(QUEUE+' == null',[_set(QUEUE,[])]),
        _set('_mount_balance_rows',QUEUE),
        _require('len(_mount_balance_rows) < 64','Resolve an existing mounted balance incident first.'),
        {'list_push':{'list':'_mount_balance_rows','value':{
            'steed':'_steed_state_id','rider':'_rider_state_id','trigger_actor':'_trigger_actor_id'}}},
        _set(QUEUE,'_mount_balance_rows')])


def _queue_actor():
    """Find only the changed creature's actual Otherworldly Steed relation."""
    return _tool('Queue an actual newly Prone mounted creature.',[_entity('actor_state')],[
        _when("actor_state.created_spell_id == 'find_steed' and actor_state.created_active == true and "
            'actor_state.'+RIDER_FIELD+" != null and actor_state."+RIDER_FIELD+" != ''",[
            _call(PAIR,{'steed_state':'{_actor_state_id}','rider_state':'{actor_state.'+RIDER_FIELD+'}',
                'trigger_actor':'{_actor_state_id}'})]),
        _when('actor_state.'+MOUNT_FIELD+" != null and actor_state."+MOUNT_FIELD+" != ''",[
            _call(PAIR,{'steed_state':'{actor_state.'+MOUNT_FIELD+'}','rider_state':'{_actor_state_id}',
                'trigger_actor':'{_actor_state_id}'})]),
        _call(OPEN,{})])


def _open_pair():
    """Discard a superseded pair; allocate only an incident still owned by the living mount."""
    return _tool_scene('Open a saved mounted balance choice for this actual pair.',
        [_entity('steed_state'),_entity('rider_state'),_entity('trigger_actor'),
         _parameter('forced_destination','array',required=False,default=[],items={'type':'number'},max_items=3)],[
        _when("steed_state.created_active == true and steed_state.is_alive == true and "
            'rider_state.is_alive == true and rider_state.'+MOUNT_FIELD+' == _steed_state_id and '
            'steed_state.'+RIDER_FIELD+' == _rider_state_id',[
            *_pair_bodies(),
            _when('state.pending_srd_actions == null',[_set('state.pending_srd_actions',{})]),
            _when('state.pending_srd_action_sequence == null',[_set('state.pending_srd_action_sequence',0)]),
            _set('state.pending_srd_action_sequence','state.pending_srd_action_sequence + 1'),
            _call('record_srd_result_text',{'text':'action-{state.pending_srd_action_sequence}'},('_recorded_result_text',)),
            _set('_mount_balance_action_id','_recorded_result_text'),
            _set('state.pending_srd_actions.{_mount_balance_action_id}',{
                'kind':"'"+KIND+"'",'owner_kind':"'"+KIND+"'",'status':"'open'",'stage':"'balance'",
                'actor_state_id':'_rider_state_id','target_state_id':'_steed_state_id','turn_actor':None,
                'parameters':{'rider':'_rider_state_id','steed':'_steed_state_id','trigger_actor':'_trigger_actor_id',
                    'rider_body':'_steed_rider_body.id','steed_body':'_steed_mount_body.id',
                    'rider_origin':'_steed_rider_body.position','steed_origin':'_steed_mount_body.position',
                     'plane':'_steed_mount_body.metadata.plane','forced_destination':'forced_destination'},
                 'context':{},'sanctuary_saves':[]}),
            _set('state.pending_srd_action_stack',['_mount_balance_action_id'])])])


def _open():
    """Open one actual save choice after the owning action's existing windows have closed."""
    return _tool('Open the next actual mounted balance choice.',[],[
        _when(QUEUE+' != null and len('+QUEUE+') > 0 and '
            '(state.pending_srd_action_stack == null or len(state.pending_srd_action_stack) == 0)',[
            _set('_mount_balance_pending_rows',QUEUE),_set('_mount_balance_kept_rows',[]),
            {'for_each':{'collection':'_mount_balance_pending_rows','as':'_mount_balance_row','do':[
                {'condition':'state.pending_srd_action_stack == null or len(state.pending_srd_action_stack) == 0',
                 'on_true':[_call(ONE,{'steed_state':'{_mount_balance_row.steed}',
                    'rider_state':'{_mount_balance_row.rider}','trigger_actor':'{_mount_balance_row.trigger_actor}'})],
                 'on_false':[{'list_push':{'list':'_mount_balance_kept_rows','value':'_mount_balance_row'}}]}]}},
            _set(QUEUE,'_mount_balance_kept_rows')])])


def _save():
    """Use native saving throws and actual PC or NPC authority, including automatic failures."""
    exported={'_mount_balance_roll':'_d20_test','_mount_balance_total':'_adjusted_total',
        '_mount_balance_modifier':'_combined_modifier','_mount_balance_outcome':'_outcome',
        '_mount_balance_automatic_reason':'_automatic_failure_reason'}
    common={'actor_state':'{_rider_state_id}','ability':'dexterity','kind':'saving_throw',
        'target_number':10,'circumstantial_modifier':0,'advantage':False,'disadvantage':False,
        'forego_save':False}
    pc={'call':'d20_test','params':{**common,'actor':'{_rider_state_id}',
        'proficiency_factor':'none'},'export_context':exported}
    npc={'call':'d20_test','params':{**common,'ability_modifier':'{rider_state.saving_throw_modifiers.dexterity}',
        'proficiency_bonus':0,'proficiency_factor':'none'},'export_context':exported}
    return {'condition':"rider_state.creature_kind == 'character'",'on_true':[pc],
        'on_false':[_require('rider_state.saving_throw_modifiers.dexterity != null',
            'Record the actual creature Dexterity save modifier before this mounted save.'),npc]}


def _forced_movement():
    """Record an involuntary carrier endpoint and require the save before either body moves."""
    tool=_tool_scene('Begin a declared involuntary movement of the actual mounted Otherworldly Steed. '
        'The DC 10 rider Dexterity save must resolve before motion; this spends no voluntary movement.',
        [_entity('rider_state'),_entity('steed_state'),
         _parameter('destination','array',items={'type':'number'},min_items=3,max_items=3)],[
        _require('state.pending_srd_action_stack == null or len(state.pending_srd_action_stack) == 0',
            'Resolve the existing actual pending action before another forced movement.'),
        *_pair_bodies(),
        _require('rider_state.is_alive == true','Use the actual living mounted rider.'),
        _require('destination[0] != _steed_mount_body.position[0] or '
            'destination[1] != _steed_mount_body.position[1] or destination[2] != _steed_mount_body.position[2]',
            'Declare an actual involuntary movement endpoint.'),
        _call(PREPARE,{'steed_state':'{_steed_state_id}','rider_state':'{_rider_state_id}',
            'destination':'{destination}'}),
        _call(ONE,{'steed_state':'{_steed_state_id}','rider_state':'{_rider_state_id}',
            'trigger_actor':'{_steed_state_id}','forced_destination':'{destination}'},('_mount_balance_action_id',)),
        _set('_mount_balance_before_position','_steed_mount_body.position'),
        _set('_mount_balance_rider_name','rider_state.name'),_set('_mount_balance_steed_name','steed_state.name')])
    tool['metadata']={'category':'movement','costs_action':False,'result_card':{
        'version':1,'footprint':'compact','title':'Forced movement pending','summary':[
            {'label':'Rider','value':{'path':'context._mount_balance_rider_name'}},
            {'label':'Steed','value':{'path':'context._mount_balance_steed_name'}},
            {'label':'Before','value':{'path':'context._mount_balance_before_position'}},
            {'label':'Intended destination','value':{'path':'parameters.destination'}},
            {'label':'Required','value':'DC 10 Dexterity save before motion'}]}}
    return tool


def _resolve():
    """Preflight a nearby clear fall-off space before the one actual save and physical consequence."""
    tool=_tool_scene('Resolve the saved DC 10 Dexterity save after mounted Prone or before involuntary motion. Choose a clear '
        'off-mount position at the rider\'s actual height within five feet; a failed save opens the real fall.',
        [_entity('rider_state'),_entity('steed_state'),_parameter('pending_action_id'),
         _parameter('destination','array',items={'type':'number'},min_items=3,max_items=3)],[
        _set('_mount_balance_event','state.pending_srd_actions[pending_action_id]'),
        _require("_mount_balance_event.kind == '"+KIND+"' and _mount_balance_event.status == 'open' and "
            "_mount_balance_event.stage == 'balance' and state.pending_srd_action_stack[-1] == pending_action_id",
            'Use the actual innermost unresolved mounted balance choice.'),
        _require('_mount_balance_event.parameters.rider == _rider_state_id and '
            '_mount_balance_event.parameters.steed == _steed_state_id','Preserve the saved incident\'s actual pair.'),
        *_pair_bodies(),
        _require('_steed_mount_body.id == _mount_balance_event.parameters.steed_body and '
            '_steed_rider_body.id == _mount_balance_event.parameters.rider_body and '
            '_steed_mount_body.metadata.plane == _mount_balance_event.parameters.plane',
            'Preserve the saved actual bodies and plane.'),
        *[_require(f'_steed_mount_body.position[{axis}] == _mount_balance_event.parameters.steed_origin[{axis}] and '
            f'_steed_rider_body.position[{axis}] == _mount_balance_event.parameters.rider_origin[{axis}]',
            'Resolve this incident before moving either actual body.') for axis in range(3)],
        _set('_mount_balance_motion',False),
        _when('_mount_balance_event.parameters.forced_destination != null',[
            _set('_mount_balance_motion','len(_mount_balance_event.parameters.forced_destination) == 3')]),
        _when('_mount_balance_motion == true',[
            _call(PREPARE,{'steed_state':'{_steed_state_id}','rider_state':'{_rider_state_id}',
                'destination':'{_mount_balance_event.parameters.forced_destination}'}),
            _require('abs(destination[0] - _mount_balance_event.parameters.forced_destination[0]) >= '
                '_steed_rider_body.footprint.half_extents[0] + _steed_mount_body.footprint.half_extents[0] or '
                'abs(destination[1] - _mount_balance_event.parameters.forced_destination[1]) >= '
                '_steed_rider_body.footprint.half_extents[1] + _steed_mount_body.footprint.half_extents[1]',
                'Keep the failed-save falling column clear of the actual moved mount.')]),
        _set('_steed_mount_destination','destination'),
        _require('abs(destination[2] - _steed_rider_body.position[2]) <= 0.000001',
            'Preserve the real fall height until the existing fall owner resolves the descent.'),
        _set('_mount_balance_destination_body',{'position':'destination','footprint':'_steed_rider_body.footprint',
            'height':'_steed_rider_body.height'}),
        *_distance('_mount_balance_destination_body','_steed_mount_body'),*_placement(),
        _require('abs(destination[0] - _steed_mount_body.position[0]) >= '
            '_steed_rider_body.footprint.half_extents[0] + _steed_mount_body.footprint.half_extents[0] or '
            'abs(destination[1] - _steed_mount_body.position[1]) >= '
            '_steed_rider_body.footprint.half_extents[1] + _steed_mount_body.footprint.half_extents[1]',
            'Choose an actual space off the mount, with a clear falling column.'),
        _set('_mount_balance_rider_name','rider_state.name'),_set('_mount_balance_steed_name','steed_state.name'),
        _save(),_set('_mount_balance_status',"'Stayed mounted'"),
        _when("_mount_balance_automatic_reason == 'none'",[_set('_mount_balance_automatic_reason',None)]),
        _set('_mount_balance_automatic_display',None),
        _when('_mount_balance_automatic_reason != null',[
            _set('_mount_balance_automatic_display',"'This saving throw fails automatically'")]),
        _when("_mount_balance_automatic_reason == 'condition_strength_dexterity_save'",[
            _set('_mount_balance_automatic_display',"'Current condition forces Dexterity save failure'")]),
        _when("_mount_balance_outcome != 'success'",[
            _set('rider_state.'+MOUNT_FIELD,None),_set('steed_state.'+RIDER_FIELD,None),
            _call(FEY_MOVE,{'subject':'{_rider_state_id}','destination':'{destination}',
                'plane':'{_steed_rider_body.metadata.plane}','teleport':False}),
            _when("rider_state.condition_immunities == null or 'prone' not in rider_state.condition_immunities",[
                _call('apply_srd_condition',{'target':'{_rider_state_id}','condition':'prone'})]),
            _when(RELEASED+' == null',[_set(RELEASED,[])]),_set('_mount_balance_released',RELEASED),
            {'list_push':{'list':'_mount_balance_released','value':{'rider':'_rider_state_id','steed':'_steed_state_id'}}},
            _set(RELEASED,'_mount_balance_released'),_set('_mount_balance_status',"'Fell off; Prone; descent pending'"),
            _when("rider_state.condition_immunities != null and 'prone' in rider_state.condition_immunities",[
                _set('_mount_balance_status',"'Fell off; immune to Prone; descent pending'")])]),
        _when('_mount_balance_motion == true',[
            _call(FEY_MOVE,{'subject':'{_steed_state_id}',
                'destination':'{_mount_balance_event.parameters.forced_destination}',
                'plane':'{_mount_balance_event.parameters.plane}','teleport':False}),
            _when("_mount_balance_outcome == 'success'",[
                _call(FOLLOW,{'steed_state':'{_steed_state_id}','rider_state':'{_rider_state_id}',
                    'previous_origin':'{_mount_balance_event.parameters.steed_origin}'}),
                _set('_mount_balance_status',"'Stayed mounted; moved with the Steed'")])]),
        _set('state.pending_srd_actions.{pending_action_id}.stage',"'balance_resolved'"),
        _call('complete_srd_pending_action',{'pending_action_id':'{pending_action_id}'}),
        _call(SETTLE,{}),
        _set('_mount_balance_equations',[]),
        _when('_mount_balance_roll != null',[
            {'list_push':{'list':'_mount_balance_equations','value':{
                'label':'_mount_balance_rider_name','value':"'Dexterity saving throw; DC 10'",
                'equation':{'die_sides':20,'roll':'_mount_balance_roll.total',
                    'modifier':'_mount_balance_modifier','total':'_mount_balance_total'}}}}])])
    # An effect can retire the mount after recording Prone but before this choice resolves.
    effects=tool['effects']
    body_start=next(i for i,effect in enumerate(effects) if effect.get('call')==OWNER)
    completion=next(i for i,effect in enumerate(effects) if effect.get('set',{}).get('field')==
        'lasting_state.pending_srd_actions.{pending_action_id}.stage')
    body=effects[body_start:completion]
    effects[body_start:completion]=[
        _set('_mount_balance_rider_name','rider_state.name'),_set('_mount_balance_steed_name','steed_state.name'),
        _set('_mount_balance_automatic_display',None),
        _set('_mount_balance_roll',None),_set('_mount_balance_status',"'Incident cleared; pair no longer mounted'"),
        _when("steed_state.created_active == true and steed_state.is_alive == true and "
            'rider_state.is_alive == true and rider_state.'+MOUNT_FIELD+' == _steed_state_id and '
            'steed_state.'+RIDER_FIELD+' == _rider_state_id',body)]
    tool['metadata']={'category':'movement','costs_action':False,'result_card':{
        'version':1,'footprint':'compact','title':'Mounted balance','summary':[
            {'label':'Rider','value':{'path':'context._mount_balance_rider_name'}},
            {'label':'Steed','value':{'path':'context._mount_balance_steed_name'}},
            {'label':'Result','value':{'path':'context._mount_balance_status'}},
            {'label':'Automatic failure','value':{'path':'context._mount_balance_automatic_display'},'optional':True}],
        'equations':{'path':'context._mount_balance_equations'}}}
    return tool


def _pause_pending_mounted_movement(tools):
    """Stop the existing route at a real mounted save before another segment changes its bodies."""
    pending=[tools['move_srd_scene_creature_celestial_segment']['effects']]
    matches=[]
    while pending:
        rows=pending.pop()
        for index,effect in enumerate(rows):
            for branch in ('on_true','on_false'):
                pending.append(effect.get(branch,[]))
            if 'for_each' in effect:
                pending.append(effect['for_each']['do'])
            if effect.get('condition','').startswith('actor_state.is_alive != true or actor_state.hp <= 0'):
                if (effect.get('on_true')==[_set('_ground_route_continue',False)] or
                        effect.get('set')==_set('_ground_route_continue',False)['set']):
                    matches.append((rows,index))
    if len(matches)!=1:
        raise ValueError('Keep the sole committed route interruption owner')
    rows,index=matches[0]
    condition='lasting_state.pending_srd_action_stack != null and len(lasting_state.pending_srd_action_stack) > 0 and '
    condition+="lasting_state.pending_srd_actions[lasting_state.pending_srd_action_stack[-1]].kind == '"+KIND+"'"
    # Keep each predicate within the existing 500-character expression limit.
    rows.insert(index+1,_when(condition,[_set('_ground_route_continue',False)]))


def _preserve_unavailable_movement_speed(tools):
    """Return zero when the existing pace owner skips movement for an incapacitated body."""
    owner=tools['project_srd_animal_messenger_pace']
    if any(effect.get('set',{}).get('field')=='_movement_speed' for effect in owner['effects']):
        raise ValueError('Keep the single unavailable-movement default before projection')
    owner['effects'].insert(0,_set('_movement_speed',0))


def bind_steed_balance(runtime,record):
    """Observe actual Prone transitions and retain all original condition and fall owners."""
    tools=runtime['tools']
    names=(ACTOR,PAIR,OPEN,ONE,RESOLVE,FORCE)
    if any(name in tools for name in names):
        raise ValueError('Mounted balance must be composed exactly once')
    tools.update({ACTOR:_queue_actor(),PAIR:_queue_pair(),OPEN:_open(),ONE:_open_pair(),
        RESOLVE:_resolve(),FORCE:_forced_movement()})
    _pause_pending_mounted_movement(tools)
    _preserve_unavailable_movement_speed(tools)
    for name,subject,identity in (('apply_srd_condition','target','target'),
        ('refresh_srd_timed_condition_target','target_state','_target_state_id'),
        ('start_srd_control_order_turn','target_state','_target_state_id')):
        owner=tools[name]
        owner['effects'].insert(0,_set('_mount_balance_was_prone',"'prone' in "+subject+'.conditions'))
        owner['effects'].append(_when("_mount_balance_was_prone != true and 'prone' in "+subject+'.conditions',[
            _call(ACTOR,{'actor_state':'{'+identity+'}'})]))
    tools['settle_srd_phantom_steed_fades']['effects'].append(_call(OPEN,{}))
    complete=tools['complete_srd_pending_action']['effects']
    index=next(i for i,effect in enumerate(complete) if effect.get('set',{}).get('field')=='_pending_action')
    complete.insert(index+1,_require("_pending_action.kind != '"+KIND+"' or _pending_action.stage == 'balance_resolved'",
        'Resolve the saved mounted Dexterity save and fall-off destination before completing this incident.'))
    for name in (RESOLVE,FORCE):
        record['tools'].append({'name':name,'capability':'dnd.srd.5.2.1.spells','writers':['dm'],
            'turn_statuses':['open'],'state_predicates':[],'input_schema':_tool_schema(tools[name],tool_name=name)})
    return (RESOLVE,FORCE)
