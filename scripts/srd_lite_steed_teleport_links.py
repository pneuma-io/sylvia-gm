"""Clear displaced Otherworldly Steed pairs after ordinary teleports, preserving paired Fey Step."""
from copy import deepcopy
from .srd_timed_actor_defenses import _call,_entity,_set,_when
from .srd_scene_relations import _tool_scene
from .srd_lite_mass_cure_geometry import _effect_lists
from .srd_lite_steed_fey_step import ACTION,RIDER_FIELD,MOUNT_FIELD
from .srd_lite_steed_mount import _body
from .srd_lite_steed_healing_touch import OWNER
from .srd_lite_steed_disappearance import QUEUE

ACTOR = 'detach_srd_teleported_otherworldly_actor'
PAIR = 'detach_srd_displaced_otherworldly_pair'
FEY_MOVE = 'move_srd_otherworldly_fey_pair_subject'


def _pair():
    """Keep links only when the actual bodies still share their physical riding position."""
    return _tool_scene('Clear only the authentic pair separated by this ordinary teleport.',
        [_entity('steed_state'),_entity('rider_state')],[
        _call(OWNER,{'steed_state':'{_steed_state_id}',
            'caster_state':'{steed_state.created_caster_state_id}'}),
        *_body('{_steed_state_id}','_steed_state_id','_steed_teleport_body'),
        *_body('{_rider_state_id}','_rider_state_id','_steed_teleport_rider_body'),
        _set('_steed_teleport_displaced',
            '_steed_teleport_body.metadata.plane != _steed_teleport_rider_body.metadata.plane or '
            'abs(_steed_teleport_rider_body.position[0] - _steed_teleport_body.position[0]) > 0.000001 or '
            'abs(_steed_teleport_rider_body.position[1] - _steed_teleport_body.position[1]) > 0.000001 or '
            'abs(_steed_teleport_rider_body.position[2] - _steed_teleport_body.position[2] - '
            '_steed_teleport_body.height) > 0.000001'),
        _when('steed_state.'+RIDER_FIELD+' == _rider_state_id and rider_state.'+MOUNT_FIELD+
            ' == _steed_state_id and _steed_teleport_displaced == true',[
            _set('rider_state.'+MOUNT_FIELD,None),_set('steed_state.'+RIDER_FIELD,None),
            _when(QUEUE+' == null',[_set(QUEUE,[])]),
            _set('_steed_teleport_released_rows',QUEUE),
            {'list_push':{'list':'_steed_teleport_released_rows','value':{
                'rider':'_rider_state_id','steed':'_steed_state_id'}}},
            _set(QUEUE,'_steed_teleport_released_rows')])])


def _actor():
    """Follow the moved body's saved relation, without changing its accepted endpoint or costs."""
    return _tool_scene('Check the moved creature\'s actual Otherworldly Steed relation.',
        [_entity('moving_state')],[
        _when("moving_state.created_spell_id == 'find_steed' and moving_state.created_active == true and "
            'moving_state.'+RIDER_FIELD+' != null and moving_state.'+RIDER_FIELD+" != ''",[
            _call(PAIR,{'steed_state':'{_moving_state_id}',
                'rider_state':'{moving_state.'+RIDER_FIELD+'}'})]),
        _when('moving_state.'+MOUNT_FIELD+' != null and moving_state.'+MOUNT_FIELD+" != ''",[
            _call(PAIR,{'steed_state':'{moving_state.'+MOUNT_FIELD+'}',
                'rider_state':'{_moving_state_id}'})])])


def bind_steed_teleport_links(runtime):
    """Retain the exact paired Fey transport and append ordinary teleport cleanup after its observers."""
    tools = runtime['tools']
    if any(name in tools for name in (ACTOR,PAIR,FEY_MOVE)):
        raise ValueError('Steed teleport relations must be composed exactly once')
    root = tools['move_srd_travel_subject']
    tools[FEY_MOVE] = deepcopy(root)
    fey_calls = [effect for rows in _effect_lists(tools[ACTION]['effects']) for effect in rows
        if effect.get('call') == 'move_srd_travel_subject']
    if len(fey_calls) != 2:
        raise ValueError('Preserve the actual single Fey Step relocation of each body')
    for effect in fey_calls:
        effect['call'] = FEY_MOVE
    tools.update({ACTOR:_actor(),PAIR:_pair()})
    root['effects'].append(_when('teleport == true and _travel_guard_needed == true',[
        _call(ACTOR,{'moving_state':'{_travel_guard_target}'})]))
    return [ACTOR,PAIR,FEY_MOVE,'move_srd_travel_subject',ACTION]
