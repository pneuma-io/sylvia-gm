"""Mount Otherworldly Steed through native movement costs and authenticated body relocation."""
from copy import deepcopy
from .build_srd_5_2_1_mechanics import _tool_schema
from .srd_scene_relations import POSITION_EXPORTS, _tool_scene
from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
from .srd_lite_steed_healing_touch import OWNER
from .srd_lite_steed_fey_step import RIDER_FIELD, MOUNT_FIELD
from .srd_lite_steed_life_bond import _body_distance
from .srd_phantom_steed import _mount_budget_tool
from .srd_mirage_support import _ground_without_landing_requirement
from .srd_lite_mass_cure_geometry import _effect_lists

MOUNT = 'mount_srd_otherworldly_steed'
DISMOUNT = 'dismount_srd_otherworldly_steed'
BUDGET = 'spend_srd_otherworldly_mount_movement'
QUALIFY = 'qualify_srd_otherworldly_rider_move'
RECEIPT = 'state.otherworldly_steed_movement_receipt'
RELOCATE = 'move_srd_phantom_steed_rider'


def _body(subject, identity, field):
    """Read the actual active creature body used by the physical movement receiver."""
    return [_call('srd_information_scene_entity', {'subject':subject}, POSITION_EXPORTS),
        _require('_info_scene_entity.active == true and _info_scene_entity.metadata.participant_ref == '+identity+
            ' and _info_scene_entity.metadata.is_object != true and _info_scene_entity.metadata.plane != null '
            "and _info_scene_entity.footprint.kind == 'box' and _info_scene_entity.height != null",
            'Mounting needs the actual active creature boxes and their planes.'),
        _set(field,'_info_scene_entity')]


def _owned_bodies():
    """Qualify the saved living source, both active bodies and the pending-action boundary."""
    return [_require('state.pending_srd_action_stack == null or len(state.pending_srd_action_stack) == 0',
            'Complete the actual pending action before mounting or dismounting.'),
        _call(OWNER, {'steed_state':'{_steed_state_id}','caster_state':'{steed_state.created_caster_state_id}'}),
        _require('rider_state.is_alive == true and rider_state.hp >= 1 and rider_state.size_rank < steed_state.size_rank',
            'The actual rider must be alive and smaller than this steed.'),
        *_body('{_steed_state_id}','_steed_state_id','_steed_mount_body'),
        *_body('{_rider_state_id}','_rider_state_id','_steed_rider_body'),
        _require('_steed_mount_body.metadata.plane == _steed_rider_body.metadata.plane',
            'The actual rider and steed must share a plane.')]


def _distance(first, second):
    """Use the proven closest-body distance for a mount or a chosen dismount endpoint."""
    return [_set('_bond_caster_frame',first),_set('_bond_steed_frame',second),*_body_distance(),
        _require('_bond_distance_squared <= 25 + 0.000000001','The actual mount or dismount space must be within five feet.')]


def _placement(*, mounted=False):
    """Require the exact endpoint; only mounting can exclude the actual carrier body."""
    return [_set('_steed_mount_excluded', ['_steed_mount_body.id'] if mounted else []),
        _call('resolve_srd_travel_placement',{'subject':'{_rider_state_id}','destination':'{_steed_mount_destination}',
            'plane':'{_steed_mount_body.metadata.plane}','excluded':'{_steed_mount_excluded}'},('_travel_placement',)),
        _require('_travel_placement.distance == 0','Choose the actual unoccupied rider destination.')]


def _movement_receipt(operation):
    """Authorize one exact physical move; callers cannot supply the saved body proof."""
    return [_set(RECEIPT,{'kind':"'otherworldly_steed_rider'",'operation':"'"+operation+"'",'consumed':False,
        'steed':'_steed_state_id','caster':'steed_state.created_caster_state_id','rider':'_rider_state_id',
        'body_id':'_steed_rider_body.id','carrier_id':'_steed_mount_body.id',
        'origin':'_steed_rider_body.position','destination':'_steed_mount_destination',
        'plane':'_steed_mount_body.metadata.plane','footprint':'_steed_rider_body.footprint','height':'_steed_rider_body.height'})]


def _card(tool, title):
    """Show the actual rider, steed, movement cost and endpoint without inventing a roll."""
    tool['metadata'] = {'category':'movement','costs_action':False,'result_card':{
        'version':1,'footprint':'compact','title':title,'summary':[
            {'label':'Rider','value':{'path':'context.rider_state.name'}},
            {'label':'Steed','value':{'path':'context.steed_state.name'}},
            {'label':'Mounted','value':{'path':'context._steed_mounted'}},
            {'label':'Movement spent (ft)','value':{'path':'context._ps_mount_cost'}},
            {'label':'Destination','value':{'path':'context._steed_mount_destination'}}]}}
    return tool


def _mount():
    """Qualify both bodies before native half-Speed payment and paired source links."""
    return _card(_tool_scene('Mount the actual willing Otherworldly Steed, spending half the rider\'s current Speed.',
        [_entity('rider_state'),_entity('steed_state')],_owned_bodies()+[
        _require('rider_state.'+MOUNT_FIELD+" == null or rider_state."+MOUNT_FIELD+" == ''",
            'Dismount the actual current steed before mounting another.'),
        _require('rider_state.phantom_steed_mount == null and (steed_state.'+RIDER_FIELD+
            " == null or steed_state."+RIDER_FIELD+" == '')",'This rider and steed must both be unmounted.'),
        _require('_rider_state_id == steed_state.created_caster_state_id or '
            "rider_state.attitudes[steed_state.created_caster_state_id] == 'friendly'",
            'This loyal steed accepts its actual caster or an actual friendly ally.'),
        *_distance('_steed_rider_body','_steed_mount_body'),
        _set('_steed_mount_destination',['_steed_mount_body.position[0]','_steed_mount_body.position[1]',
            '_steed_mount_body.position[2] + _steed_mount_body.height']),
        *_placement(mounted=True),
        _call(BUDGET,{'actor_state':'{_rider_state_id}'},('_ps_mount_cost',)),
        *_movement_receipt('mount'),
        _call(RELOCATE,{'subject':'{_rider_state_id}','destination':'{_steed_mount_destination}',
            'plane':'{_steed_mount_body.metadata.plane}'}),
        _set('rider_state.'+MOUNT_FIELD,'_steed_state_id'),_set('steed_state.'+RIDER_FIELD,'_rider_state_id'),
        _set(RECEIPT,None),_set('_steed_mounted',True)]),'Mount Otherworldly Steed')


def _dismount():
    """Validate a clear supported endpoint before paying and clearing the reciprocal links."""
    return _card(_tool_scene('Dismount the actual Otherworldly Steed to an unoccupied supported space within five feet.',
        [_entity('rider_state'),_entity('steed_state'),
         _parameter('destination','array',items={'type':'number'},min_items=3,max_items=3)],_owned_bodies()+[
        _require('rider_state.'+MOUNT_FIELD+' == _steed_state_id and steed_state.'+RIDER_FIELD+' == _rider_state_id',
            'Only the actual reciprocal saved rider can dismount this steed.'),
        _require('abs(_steed_rider_body.position[0] - _steed_mount_body.position[0]) <= 0.000000001 and '
            'abs(_steed_rider_body.position[1] - _steed_mount_body.position[1]) <= 0.000000001 and '
            'abs(_steed_rider_body.position[2] - _steed_mount_body.position[2] - _steed_mount_body.height) <= 0.000000001',
            'The actual rider must still occupy this steed.'),
        _set('_steed_mount_destination','destination'),
        _set('_steed_dismount_body',{'position':'destination','footprint':'_steed_rider_body.footprint',
            'height':'_steed_rider_body.height'}),
        *_distance('_steed_dismount_body','_steed_mount_body'),*_placement(),
        _set('_info_scene_entity','_steed_rider_body'),_set('_info_position','_steed_mount_destination'),
        *_ground_without_landing_requirement(),
        _require('_fall_ground_height != null and abs(destination[2] - _fall_ground_height) <= 0.000001',
            'This dismount needs actual same-plane supporting ground; airborne dismounts are not supported by this operation.'),
        _call(BUDGET,{'actor_state':'{_rider_state_id}'},('_ps_mount_cost',)),
        *_movement_receipt('dismount'),
        _call(RELOCATE,{'subject':'{_rider_state_id}','destination':'{_steed_mount_destination}',
            'plane':'{_steed_mount_body.metadata.plane}'}),
        _set('rider_state.'+MOUNT_FIELD,None),_set('steed_state.'+RIDER_FIELD,None),
        _set(RECEIPT,None),_set('_steed_mounted',False)]),'Dismount Otherworldly Steed')


def _qualifier():
    """Authenticate the saved exact rider body and endpoint at the native physical receiver."""
    return _tool_scene('Authenticate the exact Otherworldly Steed rider relocation before its native physical receiver.',
        [_parameter('subject'),_parameter('destination','array',items={'type':'number'},min_items=3,max_items=3),
         _parameter('plane')],[
        _set('_steed_move_receipt',None),
        _when(RECEIPT+' != null',[_set('_steed_move_receipt',RECEIPT)]),
        _require("_steed_move_receipt != null and _steed_move_receipt.kind == 'otherworldly_steed_rider' and "
            '_steed_move_receipt.consumed != true and _steed_move_receipt.rider == subject and '
            '_steed_move_receipt.plane == plane','Choose the exact unused source rider movement receipt.'),
        _require("_steed_move_receipt.operation == 'mount' or _steed_move_receipt.operation == 'dismount'",
            'Use the exact owned mount or dismount operation.'),
        _call(OWNER,{'steed_state':'{_steed_move_receipt.steed}','caster_state':'{_steed_move_receipt.caster}'}),
        *_body('{_steed_move_receipt.steed}','_steed_move_receipt.steed','_steed_move_carrier'),
        _require('_steed_move_carrier.id == _steed_move_receipt.carrier_id and '
            '_steed_move_carrier.metadata.plane == plane','Preserve the actual source carrier body and plane.'),
        *_body('{subject}','subject','_steed_move_body'),
        _require('_steed_move_body.id == _steed_move_receipt.body_id and '
            '_steed_move_body.metadata.plane == plane and _steed_move_body.height == _steed_move_receipt.height and '
            '_steed_move_body.footprint.half_extents[0] == _steed_move_receipt.footprint.half_extents[0] and '
            '_steed_move_body.footprint.half_extents[1] == _steed_move_receipt.footprint.half_extents[1]',
            'Preserve the same actual rider body, plane and dimensions.'),
        *[_require(f'_steed_move_body.position[{a}] == _steed_move_receipt.origin[{a}] and '
            f'destination[{a}] == _steed_move_receipt.destination[{a}]','Keep the checked original rider body and endpoint.')
            for a in range(3)],
        _set('_ps_carrier_id','_steed_move_receipt.carrier_id')])


def bind_steed_mount(runtime, record):
    """Reuse native half-Speed payment and the existing final physical rider receiver."""
    tools = runtime['tools']
    if any(name in tools for name in (MOUNT,DISMOUNT,BUDGET,QUALIFY)):
        raise ValueError('Otherworldly Steed mounting must be composed exactly once')
    tools.update({MOUNT:_mount(),DISMOUNT:_dismount(),BUDGET:_mount_budget_tool(),QUALIFY:_qualifier()})
    original = tools['qualify_srd_phantom_steed_rider_move']['effects']
    tools['qualify_srd_phantom_steed_rider_move']['effects'] = [{'condition':RECEIPT+' != null','on_true':[
        _call(QUALIFY,{'subject':'{subject}','destination':'{destination}','plane':'{plane}'},('_ps_carrier_id',))],
        'on_false':original}]
    consumed = 0
    for rows in list(_effect_lists(tools['move_srd_phantom_steed_rider_node']['effects'])):
        for i,e in enumerate(rows):
            if e.get('set',{}).get('field') == 'state.phantom_steed_movement_receipt.consumed':
                rows[i] = {'condition':RECEIPT+' != null','on_true':[_set(RECEIPT+'.consumed',True)],'on_false':[deepcopy(e)]}
                consumed += 1
    if consumed != 1:
        raise ValueError('Keep the sole original final rider movement receipt consumer')
    tools['move_srd_scene_creature']['effects'].insert(0,
        _require('actor_state.'+MOUNT_FIELD+' == null',
            'Dismount the actual Otherworldly Steed before spending the rider\'s own movement.'))
    for name in (MOUNT,DISMOUNT):
        if any(row['name'] == name for row in record['tools']):
            raise ValueError('Otherworldly mount declaration already exists: '+name)
        record['tools'].append({'name':name,'capability':'dnd.srd.5.2.1.spells','writers':['dm'],
            'turn_statuses':['open'],'state_predicates':[], 'input_schema':_tool_schema(tools[name],tool_name=name)})
    return [MOUNT,DISMOUNT,BUDGET,QUALIFY]
