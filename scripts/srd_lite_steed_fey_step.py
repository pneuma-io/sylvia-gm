"""Otherworldly Steed's Fey Step through actual placement and native teleport owners."""
from copy import deepcopy

from .build_srd_5_2_1_mechanics import _tool_schema
from .srd_scene_relations import POSITION_EXPORTS, _tool_scene
from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
from .srd_lite_steed_healing_touch import OWNER, CHARGE

ACTION = 'use_srd_steed_fey_step'
RIDER = 'qualify_srd_lite_steed_fey_rider'
PREFLIGHT = 'qualify_srd_lite_steed_teleport'
RIDER_FIELD = 'created_steed_rider_id'
MOUNT_FIELD = 'otherworldly_steed_mount'


def _body(subject, identity, local):
    """Read the real active creature body; never accept a reported position or plane."""
    return [
        _call('srd_information_scene_entity', {'subject': subject}, POSITION_EXPORTS),
        _require('_info_scene_entity.active == true and '
                 '_info_scene_entity.metadata.participant_ref == ' + identity +
                 ' and _info_scene_entity.metadata.is_object != true and '
                 '_info_scene_entity.metadata.plane != null and _info_scene_entity.height != null',
                 'Fey Step needs the actual active creature body and plane.'),
        _set(local, '_info_scene_entity')]


def _placement(subject, destination, plane, excluded):
    return [_call('resolve_srd_travel_placement', {
        'subject': subject, 'destination': destination, 'plane': plane, 'excluded': excluded},
        ('_travel_placement',)),
        _require('_travel_placement.distance == 0',
                 'Fey Step needs the chosen actual unoccupied space for both bodies.')]


def _rider():
    """Carry only a reciprocal saved rider at this actual saddle, preserving its offset."""
    return _tool_scene('Qualify the actual saved rider and its exact Fey Step destination.',
        [_entity('steed_state'), _entity('rider_state'),
         _parameter('destination', 'array', items={'type': 'number'}, min_items=3, max_items=3)], [
            _require('steed_state.' + RIDER_FIELD + ' == _rider_state_id and rider_state.' +
                     MOUNT_FIELD + ' == _steed_state_id and rider_state.is_alive == true and '
                     'rider_state.size_rank < steed_state.size_rank',
                     'Fey Step carries only the actual smaller living rider of this steed.'),
            *_body('{_steed_state_id}', '_steed_state_id', '_fey_mount_body'),
            *_body('{_rider_state_id}', '_rider_state_id', '_fey_rider_body'),
            _require('_fey_mount_body.metadata.plane == _fey_rider_body.metadata.plane and '
                     'abs(_fey_mount_body.position[0] - _fey_rider_body.position[0]) <= 0.000000001 and '
                     'abs(_fey_mount_body.position[1] - _fey_rider_body.position[1]) <= 0.000000001 and '
                     'abs(_fey_rider_body.position[2] - _fey_mount_body.position[2] - '
                     '_fey_mount_body.height) <= 0.000000001',
                     'The actual rider must still occupy this steed on the same plane.'),
            _set('_fey_rider_destination', ['destination[0]', 'destination[1]',
                 'destination[2] + _fey_mount_body.height']),
            _set('_fey_rider_excluded', ['_fey_mount_body.id']),
            *_placement('{_rider_state_id}', '{_fey_rider_destination}',
                        '{_fey_mount_body.metadata.plane}', '{_fey_rider_excluded}'),
            _call(PREFLIGHT, {'subject': '{_rider_state_id}',
                'destination': '{_fey_rider_destination}',
                'plane': '{_fey_mount_body.metadata.plane}', 'teleport': True}),
            _set('_fey_rider_name', 'rider_state.name'),
            _set('_fey_rider_scene_id', '_fey_rider_body.id')])


def _action():
    """Validate both destinations before one native Bonus Action and paired relocation."""
    tool = _tool_scene('Use the Fey Otherworldly Steed\'s Fey Step, recharging after its Long Rest.',
        [_entity('steed_state'),
         _parameter('destination', 'array', items={'type': 'number'}, min_items=3, max_items=3)], [
            _require('state.pending_srd_action_stack == null or len(state.pending_srd_action_stack) == 0',
                     'Complete the actual pending action before Fey Step.'),
            _call(OWNER, {'steed_state': '{_steed_state_id}',
                          'caster_state': '{steed_state.created_caster_state_id}'}),
            _require("steed_state.creature_type == 'fey'", 'Only a Fey steed has Fey Step.'),
            _require('steed_state.' + CHARGE + ' == true',
                     'The steed must complete its own Long Rest before using this bonus action again.'),
            _require("steed_state.conditions != null and not ('incapacitated' in steed_state.conditions or "
                     "'paralyzed' in steed_state.conditions or 'petrified' in steed_state.conditions or "
                     "'stunned' in steed_state.conditions or 'unconscious' in steed_state.conditions) and "
                     "steed_state.control_confusion_active != true and "
                     "steed_state.control_order_blocks_bonus != true and "
                     "(steed_state.magic_jar_role == null or steed_state.magic_jar_role == 'possessed')",
                     'The steed cannot currently take a Bonus Action.'),
            *_body('{_steed_state_id}', '_steed_state_id', '_fey_body'),
            _call('measure_srd_information_distance', {
                'start': '{_fey_body.position}', 'end': '{destination}'}, ('_scene_distance',)),
            _require('_scene_distance <= 60', 'Fey Step cannot exceed 60 feet from the actual steed.'),
            _set('_fey_distance', '_scene_distance'),
            _set('_fey_rider_id', "''"),
            _set('_fey_rider_name', "'None'"),
            _set('_fey_rider_destination', None),
            _set('_fey_excluded', []),
            _when('steed_state.' + RIDER_FIELD + " != null and steed_state." + RIDER_FIELD + " != ''", [
                _set('_fey_rider_id', 'steed_state.' + RIDER_FIELD),
                _call(RIDER, {'steed_state': '{_steed_state_id}',
                    'rider_state': '{_fey_rider_id}', 'destination': '{destination}'},
                    ('_fey_rider_destination', '_fey_rider_name', '_fey_rider_scene_id')),
                _set('_fey_excluded', ['_fey_rider_scene_id'])]),
            *_placement('{_steed_state_id}', '{destination}',
                        '{_fey_body.metadata.plane}', '{_fey_excluded}'),
            _call(PREFLIGHT, {'subject': '{_steed_state_id}', 'destination': '{destination}',
                             'plane': '{_fey_body.metadata.plane}', 'teleport': True}),
            _set('_fey_destination', 'destination'),
            _when('combat.encounter_active == true', [
                _call('consume_srd_bonus_action', {'actor_state': '{_steed_state_id}',
                                                  'bonus_action': 'Fey Step'})]),
            _set('steed_state.' + CHARGE, False),
            _call('move_srd_travel_subject', {'subject': '{_steed_state_id}',
                'destination': '{destination}', 'plane': '{_fey_body.metadata.plane}', 'teleport': True}),
            _when("_fey_rider_id != ''", [
                _call('move_srd_travel_subject', {'subject': '{_fey_rider_id}',
                    'destination': '{_fey_rider_destination}',
                    'plane': '{_fey_body.metadata.plane}', 'teleport': True})])])
    tool['metadata'] = {'category': 'combat', 'costs_action': False, 'result_card': {
        'version': 1, 'footprint': 'compact', 'title': 'Fey Step', 'summary': [
            {'label': 'Steed', 'value': {'path': 'context.steed_state.name'}},
            {'label': 'Rider', 'value': {'path': 'context._fey_rider_name'}},
            {'label': 'Distance (ft)', 'value': {'path': 'context._fey_distance'}},
            {'label': 'Destination', 'value': {'path': 'context._fey_destination'}}],
        'details_label': 'Actual destinations', 'details': [
            {'label': 'Steed', 'value': {'path': 'context._fey_destination'}},
            {'label': 'Rider', 'value': {'path': 'context._fey_rider_destination'}, 'optional': True}]}}
    next(p for p in tool['parameters'] if p['name'] == 'lasting_state')['model_hidden'] = True
    return tool


def bind_steed_fey_step(runtime):
    """Reuse all final native travel guards, mutation and observers without replacing owners."""
    tools = runtime['tools']
    if any(name in tools for name in (ACTION, RIDER, PREFLIGHT)):
        raise ValueError('Fey Step source must be composed exactly once')
    source = tools['move_srd_travel_subject_node']
    mutations = [i for i, effect in enumerate(source['effects']) if 'spatial_mutation' in effect]
    if len(mutations) != 1:
        raise ValueError('Retain the single actual final Travel mutation before Fey Step')
    preflight = deepcopy(source)
    preflight['description'] = 'Run the retained native teleport guards before spending Fey Step.'
    preflight['effects'] = [
        _call('srd_information_scene_entity', {'subject': '{subject}'}, POSITION_EXPORTS),
        _require("_info_scene_entity.metadata.astral_projection_role != 'form' or plane == 'astral'",
                 'Fey Step cannot change an Astral Projection body through a preflight.')
    ] + deepcopy(source['effects'][:mutations[0]])
    tools.update({ACTION: _action(), RIDER: _rider(), PREFLIGHT: preflight})
    return [ACTION, RIDER, PREFLIGHT]


def expose_steed_fey_step(runtime, record):
    """Expose destination intent only; rider identity comes from the actual mount record."""
    if any(row['name'] == ACTION for row in record['tools']):
        raise ValueError('The Fey Step public declaration must be added exactly once')
    record['tools'].append({'name': ACTION, 'capability': 'dnd.srd.5.2.1.spells',
        'writers': ['dm'], 'turn_statuses': ['open'], 'state_predicates': [],
        'input_schema': _tool_schema(runtime['tools'][ACTION], tool_name=ACTION)})
    return ACTION
