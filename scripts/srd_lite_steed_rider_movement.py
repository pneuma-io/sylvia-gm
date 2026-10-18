"""Carry an Otherworldly Steed's actual rider through native paid movement segments."""
from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
from .srd_scene_relations import _tool_scene, POSITION_EXPORTS
from .srd_lite_steed_healing_touch import OWNER
from .srd_lite_steed_fey_step import RIDER_FIELD, MOUNT_FIELD
from .srd_lite_steed_mount import (
    QUALIFY, RECEIPT, RELOCATE, _body, _placement, _movement_receipt,
)

PREPARE = 'qualify_srd_otherworldly_rider_segment'
FOLLOW = 'follow_srd_otherworldly_steed_rider'
CHECK = 'qualify_srd_otherworldly_follow_endpoint'
MOVING_RIDER = ("actor_state.created_spell_id == 'find_steed' and actor_state.created_active == true and "
                'actor_state.'+RIDER_FIELD+' != null')
NAME = '_otherworldly_movement_rider_name'
POSITION = '_otherworldly_movement_rider_position'
ACTOR_NAME = '_scene_movement_actor_name'


def _pair():
    """Use the actual reciprocal rider and both same-plane creature boxes, including a helpless rider."""
    return [_call(OWNER, {'steed_state': '{_steed_state_id}',
                         'caster_state': '{steed_state.created_caster_state_id}'}),
        _require('steed_state.'+RIDER_FIELD+' == _rider_state_id and rider_state.'+MOUNT_FIELD+
                 ' == _steed_state_id and rider_state.is_alive == true',
                 'Preserve the actual saved mounted rider before carrying this body.'),
        *_body('{_steed_state_id}', '_steed_state_id', '_steed_mount_body'),
        *_body('{_rider_state_id}', '_rider_state_id', '_steed_rider_body'),
        _require('_steed_mount_body.metadata.plane == _steed_rider_body.metadata.plane',
                 'Carry only the actual rider on the same plane.')]


def _seated(origin):
    """Authenticate the rider's actual pre-segment seat instead of snapping an unrelated body onto the mount."""
    return [_require(f'abs(_steed_rider_body.position[{axis}] - {origin}[{axis}]'+
        (' - _steed_mount_body.height' if axis == 2 else '')+') <= 0.000001',
        'Preserve the actual pre-move riding position.') for axis in range(3)]


def _destination(origin):
    return _set('_steed_mount_destination', [origin+'[0]', origin+'[1]',
                                            origin+'[2] + _steed_mount_body.height'])


def _prepare():
    """Check both final bodies and rider clearance before the genuine segment spends movement."""
    return _tool_scene('Qualify the actual mounted pair before this native paid movement segment.',
        [_entity('steed_state'), _entity('rider_state'),
         _parameter('destination', 'array', items={'type': 'number'}, min_items=3, max_items=3)], [
        *_pair(), *_seated('_steed_mount_body.position'),
        _set('_steed_carrier_excluded', ['_steed_rider_body.id']),
        _call('resolve_srd_travel_placement', {'subject': '{_steed_state_id}', 'destination': '{destination}',
              'plane': '{_steed_mount_body.metadata.plane}', 'excluded': '{_steed_carrier_excluded}'},
              ('_travel_placement',)),
        _require('_travel_placement.distance == 0', 'Choose an actual unoccupied Steed destination.'),
        _destination('destination'), *_placement(mounted=True),
        _set('_steed_rider_missing_plane', "_steed_mount_body.metadata.plane == 'material'"),
        {'spatial_query': {'scene': 'state.spatial_scene', 'operation': 'sweep', 'query': {
            'map_id': '{state.spatial_scene.map.id}', 'moving_entity_id': '{_steed_rider_body.id}',
            'destination': '{_steed_mount_destination}', 'include_entities': False,
            'exclude_entity_ids': ['{_steed_mount_body.id}'],
            'metadata_filter': {'key': 'plane', 'value': '{_steed_mount_body.metadata.plane}',
                                'missing_matches': '{_steed_rider_missing_plane}'}}},
         'store_as': '_steed_rider_sweep'},
        _require('_steed_rider_sweep.supported == true',
                 'Exact rider clearance is unsupported for the selected geometry.'),
        _require('_steed_rider_sweep.clear == true',
                 'A solid barrier or map boundary blocks the actual rider movement footprint.')])


def _follow():
    """Move the saved rider only after the carrier's real mutation, using its existing final receiver."""
    return _tool_scene('Carry the actual saved rider after this genuine budgeted Steed segment.',
        [_entity('steed_state'), _entity('rider_state'),
         _parameter('previous_origin', 'array', items={'type': 'number'}, min_items=3, max_items=3)], [
        *_pair(), *_seated('previous_origin'), _destination('_steed_mount_body.position'),
        *_movement_receipt('follow'),
        _call(RELOCATE, {'subject': '{_rider_state_id}', 'destination': '{_steed_mount_destination}',
                        'plane': '{_steed_mount_body.metadata.plane}'}),
        _set(RECEIPT, None)])


def _check():
    """Authenticate the live carrier endpoint at each final rider receiver invocation."""
    return _tool_scene('Authenticate this source rider follow against the actual moved carrier.',
        [_entity('steed_state'), _entity('rider_state'),
         _parameter('destination', 'array', items={'type': 'number'}, min_items=3, max_items=3)], [
        *_pair(), *[_require(f'abs(destination[{axis}] - _steed_mount_body.position[{axis}]'+
            (' - _steed_mount_body.height' if axis == 2 else '')+') <= 0.000001',
            'Keep the rider on the actual moved carrier endpoint.') for axis in range(3)]])


def bind_steed_rider_movement(runtime):
    """Bind after movement composition; retain the sole carrier mutation, payment and rider receiver."""
    tools = runtime['tools']
    if any(name in tools for name in (PREPARE, FOLLOW, CHECK)):
        raise ValueError('Otherworldly Steed rider movement must be composed exactly once')
    tools.update({PREPARE: _prepare(), FOLLOW: _follow(), CHECK: _check()})
    qualifier = tools[QUALIFY]
    old = "_steed_move_receipt.operation == 'mount' or _steed_move_receipt.operation == 'dismount'"
    guards = [effect['require'] for effect in qualifier['effects']
              if effect.get('require', {}).get('condition') == old]
    if len(guards) != 1:
        raise ValueError('Keep the exact existing mount receipt operation gate')
    guards[0].update(condition=old+" or _steed_move_receipt.operation == 'follow'",
                     error='Use the exact owned mount, dismount or paid rider follow operation.')
    qualifier['effects'].insert(-1, _when("_steed_move_receipt.operation == 'follow'", [
        _call(CHECK, {'steed_state': '{_steed_move_receipt.steed}',
                     'rider_state': '{_steed_move_receipt.rider}', 'destination': '{destination}'})]))
    segments = []
    for name in ('move_srd_scene_creature_environment_segment', 'move_srd_scene_creature_node'):
        owner = tools[name]
        mutations = [index for index, effect in enumerate(owner['effects']) if 'spatial_mutation' in effect]
        if not mutations:
            continue
        if len(mutations) != 1:
            raise ValueError('Keep the sole native carrier position owner: '+name)
        owner['effects'].insert(mutations[0]+1, _when(MOVING_RIDER, [_call(FOLLOW, {
            'steed_state': '{_actor_state_id}', 'rider_state': '{actor_state.'+RIDER_FIELD+'}',
            'previous_origin': '{_move_scene_entity.position}'})]))
        owner['effects'].insert(0, _when(MOVING_RIDER, [_call(PREPARE, {
            'steed_state': '{_actor_state_id}', 'rider_state': '{actor_state.'+RIDER_FIELD+'}',
            'destination': '{destination}'})]))
        segments.append(name)
    if not segments:
        raise ValueError('Find the actual native carrier movement segment')
    root = tools['move_srd_scene_creature']
    root['effects'][0:0] = [_set(NAME, None), _set(POSITION, None), _set(ACTOR_NAME, None),
        _when('actor_state.name != null', [_set(ACTOR_NAME, 'actor_state.name')])]
    root['effects'].append(_when(MOVING_RIDER, [
        _call('read_srd_summon_initiative_label', {'summon_state': '{actor_state.'+RIDER_FIELD+'}'},
              ('_initiative_summon_name',)),
        _set(NAME, '_initiative_summon_name'),
        _call('srd_information_scene_entity', {'subject': '{actor_state.'+RIDER_FIELD+'}'}, POSITION_EXPORTS),
        _set(POSITION, '_info_position')]))
    summary = root['metadata']['result_card']['summary']
    if not isinstance(summary, list):
        raise ValueError('Keep the native movement receipt summary')
    actors = [row for row in summary if row.get('label') == 'Creature']
    if len(actors) > 1:
        raise ValueError('Keep the single native movement actor label')
    # The compact variant can omit this row, and spatial checks can hide the full actor snapshot.
    if actors:
        actors[0]['value'] = {'path': 'context.'+ACTOR_NAME}
    else:
        summary.insert(0, {'label': 'Creature', 'value': {'path': 'context.'+ACTOR_NAME}, 'optional': True})
    summary.extend([{'label': 'Rider', 'value': {'path': 'context.'+NAME}, 'optional': True},
                    {'label': 'Rider position', 'value': {'path': 'context.'+POSITION}, 'optional': True}])
    return [PREPARE, FOLLOW, CHECK, QUALIFY, *segments, 'move_srd_scene_creature']
