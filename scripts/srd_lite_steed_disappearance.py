"""Release an actual Otherworldly Steed rider when its source body disappears."""
from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _tool, _when
from .srd_scene_relations import _tool_scene, POSITION_EXPORTS
from .srd_mirage_support import _ground_without_landing_requirement
from .srd_lite_steed_fey_step import RIDER_FIELD, MOUNT_FIELD
from .srd_lite_steed_mount import _body
from .srd_lite_steed_rider_movement import _seated
from .srd_lite_steed_turns import RIDER, STEED, SAVE, RESTORE, ENDED, CONTINUATION

RELEASE = 'release_srd_disappearing_steed_rider'
SETTLE = 'settle_srd_disappeared_steed_riders'
ONE = 'qualify_srd_released_steed_rider_fall'
QUEUE = 'state.otherworldly_steed_released_riders'
FALLING = '_steed_loss_falling'


def _release():
    """Clear the authentic pair now; defer physical descent until the outer action finishes."""
    return _tool_scene('Release the actual rider before this source Steed disappears.',
        [_entity('steed_state'), _entity('rider_state')], [
        _require("steed_state.created_spell_id == 'find_steed' and steed_state.created_active == true and "
            'state.created_actor_records[_steed_state_id].active == true and '
            "state.created_actor_records[_steed_state_id].spell == 'find_steed' and "
            'state.created_actor_records[_steed_state_id].caster == steed_state.created_caster_state_id',
            'Release only the actual disappearing source Steed.'),
        _require('steed_state.'+RIDER_FIELD+' == _rider_state_id and rider_state.'+MOUNT_FIELD+' == _steed_state_id',
            'Release only the actual reciprocal rider.'),
        *_body('{_steed_state_id}', '_steed_state_id', '_steed_mount_body'),
        *_body('{_rider_state_id}', '_rider_state_id', '_steed_rider_body'),
        *_seated('_steed_mount_body.position'),
        _require('_steed_mount_body.metadata.plane == _steed_rider_body.metadata.plane',
            'Preserve the actual mounted plane before disappearance.'),
        _when(QUEUE+' == null', [_set(QUEUE, [])]),
        _set('_steed_released_rows', QUEUE),
        {'list_push': {'list': '_steed_released_rows', 'value': {
            'rider': '_rider_state_id', 'steed': '_steed_state_id'}}},
        _set(QUEUE, '_steed_released_rows'),
        _set('rider_state.'+MOUNT_FIELD, None), _set('steed_state.'+RIDER_FIELD, None)])


def _one():
    """Restore the rider's spent budget and qualify an actual unsupported living body."""
    return _tool_scene('Qualify the actual released rider at the completed action boundary.',
        [_entity('rider_state'), _parameter('steed_id')], [
        _require('rider_state.'+MOUNT_FIELD+' == null', 'The released rider must remain unmounted.'),
        _when(STEED+' == steed_id', [
            _require(RIDER+' == _rider_state_id', 'Preserve the actual controlled phase rider.'),
            _require('combat.active_actor == steed_id', 'Preserve the actual acting Steed before restoring its rider.'),
            _call(SAVE, {'actor_state': '{steed_id}'}),
            _call(RESTORE, {'actor_state': '{_rider_state_id}'}),
            _set(RIDER, None), _set(STEED, None)]),
        _set(FALLING, False),
        _call('srd_information_scene_entity', {'subject': '{_rider_state_id}'}, POSITION_EXPORTS),
        *_ground_without_landing_requirement(),
        _require('_fall_ground_height != null', 'The released rider needs actual same-plane ground below.'),
        _when('_info_position[2] > _fall_ground_height + 0.000001', [
            {'condition': 'rider_state.is_alive == true', 'on_true': [
                _call('qualify_srd_mirage_support_flight', {'target_state': '{_rider_state_id}'},
                    ('_support_flight', '_support_scene_descent', '_support_wind_descent')),
                _when('_support_flight != true and _support_scene_descent != true and _support_wind_descent != true', [
                    _call('qualify_srd_scene_fall_target', {'target_state': '{_rider_state_id}',
                        'fall_cause': 'support_lost'}, ('_falling_event',)), _set(FALLING, True)])],
             'on_false': [
                _set('_steed_dead_rider_ground', ['_info_position[0]', '_info_position[1]', '_fall_ground_height']),
                _call('move_srd_travel_subject', {'subject': '{_rider_state_id}',
                    'destination': '{_steed_dead_rider_ground}', 'plane': '{_info_scene_entity.metadata.plane}',
                    'teleport': False})]}])])


def _settle():
    """Open one real fall reaction after existing pending actions have completed."""
    return _tool('Settle actual released Steed riders after the owning action.', [], [
        _when(QUEUE+' != null and len('+QUEUE+') > 0 and '
            '(state.pending_srd_action_stack == null or len(state.pending_srd_action_stack) == 0)', [
            _set('_steed_released_rows', QUEUE), _set('_steed_falling_riders', []),
            {'for_each': {'collection': '_steed_released_rows', 'as': '_steed_released_row', 'do': [
                _call(ONE, {'rider_state': '{_steed_released_row.rider}', 'steed_id': '{_steed_released_row.steed}'},
                    (FALLING,)),
                _when(FALLING+' == true', [{'list_push': {'list': '_steed_falling_riders',
                    'value': '_steed_released_row.rider'}}])]}},
            _set(QUEUE, []),
            _when('len(_steed_falling_riders) > 0', [
                _call('begin_srd_scene_fall', {'target_states': '{_steed_falling_riders}',
                    'fall_cause': 'support_lost'}, ('_pending_action_id',))])])])


def bind_steed_disappearance(runtime):
    """Retain existing item drops and death/recast owners; reuse their outer action settlement."""
    tools = runtime['tools']
    if any(name in tools for name in (RELEASE, SETTLE, ONE)):
        raise ValueError('Otherworldly Steed disappearance must be composed exactly once')
    tools.update({RELEASE: _release(), SETTLE: _settle(), ONE: _one()})
    tools['retire_srd_created_actor']['effects'].insert(0,
        _when("actor_state.created_spell_id == 'find_steed' and actor_state.created_active == true and "
            'actor_state.'+RIDER_FIELD+' != null', [
            _call(RELEASE, {'steed_state': '{_actor_state_id}',
                'rider_state': '{actor_state.'+RIDER_FIELD+'}'}),
            _set('actor_state.'+ENDED, True), _set('actor_state.'+CONTINUATION, False)]))
    tools['settle_srd_phantom_steed_fades']['effects'].append(_call(SETTLE, {}))
    return [RELEASE, SETTLE, ONE, 'retire_srd_created_actor', 'settle_srd_phantom_steed_fades']
