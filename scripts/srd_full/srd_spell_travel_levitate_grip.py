"""Levitate surface contact authorizes climbing through the existing scene movement owner."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import _array, _tool_scene
    from .srd_spell_travel_demiplane import FACE_AXES, FACES
    from .srd_spell_travel_levitate import _body, _save_source, _source
    from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from .srd_timed_senses import _loop, _optional, _public
else:
    from srd_scene_relations import _array, _tool_scene
    from srd_spell_travel_demiplane import FACE_AXES, FACES
    from srd_spell_travel_levitate import _body, _save_source, _source
    from srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from srd_timed_senses import _loop, _optional, _public


PUBLIC_TOOLS = {'record_srd_levitate_surface_grip', 'move_srd_levitate_grip'}


def _surface(identity):
    """The contact surface is one real same-plane blocking box geometry record."""
    return [
        _set('_grip_surface', None),
        _set('_grip_geometry', 'state.spatial_scene.geometry'),
        _loop('_grip_geometry', '_grip_geometry_row', [
            _when(f'_grip_geometry_row.id == {identity}', [
                _require('_grip_surface == null', 'The contact geometry identity is ambiguous.'),
                _set('_grip_surface', '_grip_geometry_row'),
            ]),
        ]),
        _require('_grip_surface != null and _grip_surface.active == true and '
                 "_grip_surface.movement == 'blocking' and "
                 "_grip_surface.volume.kind == 'box' and "
                 '_grip_surface.metadata.plane == _levitate_source.plane',
                 'Grip needs an actual active fixed box surface on the suspended body plane.'),
    ]


def _on_face(point, face):
    """Exact endpoints on the same rectangle preserve translated contact along its convex face."""
    return [
        _when(f"{face} == '{name}'", [
            _require(f'{point}[{axis}] == _grip_surface.volume.{edge}[{axis}] and ' +
                     ' and '.join(
                         f'{point}[{other}] >= _grip_surface.volume.min[{other}] and '
                         f'{point}[{other}] <= _grip_surface.volume.max[{other}]'
                         for other in range(3) if other != axis),
                     'The actual contact must remain on the selected surface rectangle.'),
        ])
        for name, axis, edge in FACE_AXES
    ]


def _active_actor(tools):
    """Source liveness, actual body identity and manipulation come from their existing owners."""
    if __package__:
        from .srd_spell_travel import ACTIVE
    else:
        from srd_spell_travel import ACTIVE
    effects = [
        *_source(),
        _require('_levitate_source.active == true and '
                 'state.lasting_tick < _levitate_source.expires_tick and '
                 '_levitate_source.creature_id == _actor_state_id',
                 'Only this actual suspended creature can use its live Levitate grip.'),
        _require(ACTIVE, 'An Incapacitated creature cannot push or pull itself along a surface.'),
        _require('combat.pending_action == null and '
                 '(state.pending_srd_action_stack == null or '
                 'len(state.pending_srd_action_stack) == 0)',
                 'Finish the actual pending action before using this Levitate grip.'),
        _call('check_srd_condition_concentration', {
            'caster_state': '{_levitate_source.caster}',
            'sequence': '{_levitate_source.sequence}',
        }, ['_condition_concentration_active']),
        _require('_condition_concentration_active == true',
                 'Surface movement needs the actual active Levitate concentration.'),
        *_body('_levitate_source.subject'),
        _require('_info_scene_entity.metadata.levitate_source_id == _levitate_source.id and '
                 '_info_scene_entity.metadata.plane == _levitate_source.plane',
                 'Select the actual source-marked suspended body on its recorded plane.'),
    ]
    if 'project_srd_transformation' in tools:
        effects.append(_when('actor_state.has_transformations == true', [
            _call('project_srd_transformation', {
                'target_state': '{_actor_state_id}', 'lasting_state': 'state',
            }),
        ]))
    effects.append(_require('actor_state.form_cloud != true',
                            'A cloud body cannot manipulate a fixed surface for this grip.'))
    return effects


def grip_tools(tools):
    """Save only open contact-reach fiction, then derive and spend actual surface movement."""
    record = _public(_tool_scene(
        'Record arm/contact reach fiction against an actual suspended body and fixed surface.',
        [_entity('actor_state'), _parameter('source_id'), _parameter('surface_id'),
         _parameter('face', 'enum', enum_values=FACES), _array('contact_point'),
         _parameter('contact_within_reach', 'boolean')],
        [
            *_active_actor(tools), *_surface('surface_id'),
            _require('contact_within_reach == true',
                     'The selected actual arm/contact reach must be established in the fiction.'),
            *_on_face('contact_point', 'face'),
            _set('_levitate_source_patch', {'grip': {
                'surface_id': '_grip_surface.id', 'volume': '_grip_surface.volume',
                'face': 'face', 'contact': 'contact_point', 'origin': '_info_position',
                'footprint': '_info_scene_entity.footprint', 'height': '_info_scene_entity.height',
            }}),
            _set('_levitate_source', 'merge(_levitate_source, _levitate_source_patch)'),
            *_save_source(), _set('_grip_contact', 'contact_point'),
        ]), 'exploration', 'Levitate Surface Contact', [('Contact', '_grip_contact')])

    move = _public(_tool_scene(
        'Push or pull the actual suspended creature along its established fixed surface contact.',
        [_entity('actor_state'), _parameter('source_id'), _array('destination')],
        [
            *_active_actor(tools),
            _require('_levitate_source.grip != null',
                     'Establish actual fixed-surface contact reach before moving by grip.'),
            _set('_grip', '_levitate_source.grip'),
            *_surface('_grip.surface_id'),
            _set('_grip_saved_volume', ['_grip.volume']),
            _set('_grip_saved_footprint', ['_grip.footprint']),
            _require('_grip_surface.volume in _grip_saved_volume and '
                     '_info_scene_entity.footprint in _grip_saved_footprint and '
                     '_info_scene_entity.height == _grip.height',
                     'Re-establish contact after actual surface or body shape changes.'),
            _set('_grip_origin_member', ['_grip.origin']),
            _require('_info_position in _grip_origin_member',
                     'Re-establish contact reach after the body changes its actual position.'),
            _set('_grip_delta', [f'destination[{i}] - _info_position[{i}]' for i in range(3)]),
            _set('_grip_axis', -1),
            *[_when(f'_grip_delta[{i}] != 0', [
                _require('_grip_axis == -1',
                         'Exact grip movement currently selects one tangent axis at a time.'),
                _set('_grip_axis', i),
            ]) for i in range(3)],
            _require('_grip_axis >= 0', 'Choose an actual positive surface movement distance.'),
            _set('_grip_end_contact', [f'_grip.contact[{i}] + _grip_delta[{i}]' for i in range(3)]),
            *_on_face('_grip.contact', '_grip.face'),
            *_on_face('_grip_end_contact', '_grip.face'),
            _set('_levitate_source_patch', {'movement_destination': 'destination',
                'grip_movement': {'start': '_info_position', 'end': 'destination',
                                 'axis': '_grip_axis', 'contact': '_grip.contact',
                                 'surface_id': '_grip.surface_id', 'volume': '_grip.volume',
                                 'face': '_grip.face', 'footprint': '_grip.footprint',
                                 'height': '_grip.height'}}),
            _set('_levitate_source', 'merge(_levitate_source, _levitate_source_patch)'),
            *_save_source(),
            _call('move_srd_scene_creature', {
                'actor_state': '{_actor_state_id}', 'destination': '{destination}',
                'mode': 'climb', 'levitate_source_id': '{_levitate_source.id}',
            }, ['_scene_movement_distance', '_scene_movement_cost', '_scene_actor_position']),
            # Entry consequences can retire the source; reload it before clearing authorization.
            *_source(), *_body('_levitate_source.subject'),
            _set('_grip_actual_contact', [
                f'_grip.contact[{i}] + _info_position[{i}] - _grip.origin[{i}]'
                for i in range(3)]),
            _set('_grip_patch', {'origin': '_info_position', 'contact': '_grip_actual_contact'}),
            _set('_grip', 'merge(_grip, _grip_patch)'),
            _set('_levitate_source_patch', {'movement_destination': None,
                                           'grip_movement': None, 'grip': '_grip'}),
            _set('_levitate_source', 'merge(_levitate_source, _levitate_source_patch)'),
            *_save_source(), _set('_grip_final_position', '_info_position'),
        ]), 'movement', 'Levitate Surface Movement',
        [('Position', '_grip_final_position'), ('Movement spent', '_scene_movement_cost')])

    segment = _tool_scene(
        'Qualify only an actual intermediate point on the saved axis-aligned grip route.',
        [_parameter('source_id'), _parameter('subject'), _array('destination'),
         _parameter('plane')],
        [
            *_source(), *_body('subject'),
            _set('_levitate_grip_segment_allowed', False),
            _when('_levitate_source.active == true and _levitate_source.grip_movement != null', [
                _set('_grip_route', '_levitate_source.grip_movement'),
                *_surface('_grip_route.surface_id'),
                _set('_grip_saved_volume', ['_grip_route.volume']),
                _set('_grip_saved_footprint', ['_grip_route.footprint']),
                _require('_grip_surface.volume in _grip_saved_volume and '
                         '_info_scene_entity.footprint in _grip_saved_footprint and '
                         '_info_scene_entity.height == _grip_route.height and '
                         '_levitate_source.plane == plane',
                         'The actual surface, body and plane must still qualify the grip segment.'),
                *[_when(f'_grip_route.axis == {i}', [
                    _require(' and '.join([
                        f'destination[{i}] >= min(_grip_route.start[{i}], _grip_route.end[{i}])',
                        f'destination[{i}] <= max(_grip_route.start[{i}], _grip_route.end[{i}])',
                        f'_info_position[{i}] >= min(_grip_route.start[{i}], _grip_route.end[{i}])',
                        f'_info_position[{i}] <= max(_grip_route.start[{i}], _grip_route.end[{i}])',
                    ] + [f'destination[{j}] == _grip_route.start[{j}] and '
                         f'_info_position[{j}] == _grip_route.start[{j}]'
                         for j in range(3) if j != i]),
                        'Every actual grip segment must remain inside its saved tangent route.'),
                ]) for i in range(3)],
                _set('_grip_segment_contact', [
                    f'_grip_route.contact[{i}] + destination[{i}] - _grip_route.start[{i}]'
                    for i in range(3)]),
                *_on_face('_grip_segment_contact', '_grip_route.face'),
                _set('_levitate_grip_segment_allowed', True),
            ]),
        ])
    budget = _tool_scene(
        'Choose walking Speed only for an actual grip route without a real Climb Speed.',
        [_entity('actor_state'), _parameter('source_id'), _array('destination'),
         _parameter('plane')],
        [
            _call('qualify_srd_levitate_grip_segment', {
                'source_id': '{source_id}', 'subject': '{_actor_state_id}',
                'destination': '{destination}', 'plane': '{plane}',
            }, ['_levitate_grip_segment_allowed']),
            _require('_levitate_grip_segment_allowed == true',
                     'Climbing fallback needs the actual source-owned surface-contact route.'),
            _call('project_srd_timed_actor_mobility', {
                'target_state': '{_actor_state_id}', 'lasting_state': 'state',
            }),
            _set('_levitate_grip_walk_budget', False),
            _when('(actor_state.climb_speed == null or actor_state.climb_speed <= 0) and '
                  'actor_state.spell_spider_climb != true', [
                _set('_levitate_grip_walk_budget', True),
            ]),
        ])
    return {'record_srd_levitate_surface_grip': record, 'move_srd_levitate_grip': move,
            'qualify_srd_levitate_grip_segment': segment,
            'resolve_srd_levitate_grip_climbing_budget': budget}


def bind_grip_consumers(tools, changed):
    """Forward private source authorization through the final environment movement route."""
    movement_names = {'move_srd_scene_creature', 'move_srd_scene_creature_node',
                      'move_srd_scene_creature_environment_segment'}

    def rows(effects):
        yield effects
        for effect in effects:
            for name in ('on_true', 'on_false'):
                if name in effect:
                    yield from rows(effect[name])
            if 'for_each' in effect:
                yield from rows(effect['for_each']['do'])

    for name in movement_names:
        if name not in tools and name not in changed:
            continue
        tool = deepcopy(changed.get(name, tools.get(name)))
        if not any(p['name'] == 'levitate_source_id' for p in tool['parameters']):
            tool['parameters'].append(
                _optional('levitate_source_id', default='', model_hidden=True))
        for effects in rows(tool['effects']):
            for effect in effects:
                if effect.get('call') in movement_names | {'check_srd_levitate_movement'}:
                    effect['params']['levitate_source_id'] = '{levitate_source_id}'
        changed[name] = tool
    qualifier = deepcopy(changed.get('qualify_srd_levitate_source_movement',
                                    tools.get('qualify_srd_levitate_source_movement')))
    qualifier['effects'] = [e for e in qualifier['effects']
                            if e.get('call') != 'qualify_srd_levitate_grip_segment']
    boundary = next(i for i, e in enumerate(qualifier['effects']) if 'require' in e)
    qualifier['effects'].insert(boundary, _call('qualify_srd_levitate_grip_segment', {
        'source_id': '{source_id}', 'subject': '{subject}',
        'destination': '{destination}', 'plane': '{plane}',
    }, ['_levitate_grip_segment_allowed']))
    for effect in qualifier['effects']:
        if 'require' in effect:
            condition = effect['require']['condition']
            if 'destination in _levitate_allowed_destination and ' in condition:
                effect['require']['condition'] = condition.replace(
                    'destination in _levitate_allowed_destination and ',
                    '(destination in _levitate_allowed_destination or '
                    '_levitate_grip_segment_allowed == true) and ')
    changed['qualify_srd_levitate_source_movement'] = qualifier
    segment_name = ('move_srd_scene_creature_environment_segment'
                    if 'move_srd_scene_creature_environment_segment' in changed
                    else 'move_srd_scene_creature_node')
    segment = changed[segment_name]
    segment['effects'] = [e for e in segment['effects']
                          if e.get('set', {}).get('field') not in {
                              '_levitate_grip_budget_mode', '_levitate_grip_walk_budget'}
                          and e.get('condition') not in {
                              "mode == 'climb' and len(levitate_source_id) > 0",
                              '_levitate_grip_walk_budget == true'}]
    boundary = next(i for i, e in enumerate(segment['effects'])
                    if e.get('call') == 'srd_information_movement_cost')
    segment['effects'][boundary:boundary] = [
        _set('_levitate_grip_budget_mode', 'mode'),
        _set('_levitate_grip_walk_budget', False),
        _when("mode == 'climb' and len(levitate_source_id) > 0", [
            _call('resolve_srd_levitate_grip_climbing_budget', {
                'actor_state': '{_actor_state_id}', 'source_id': '{levitate_source_id}',
                'destination': '{destination}', 'plane': '{_travel_movement_plane}',
            }, ['_levitate_grip_walk_budget']),
            _when('_levitate_grip_walk_budget == true', [
                _set('_levitate_grip_budget_mode', "'walk'"),
            ]),
        ]),
    ]
    boundary = next(i for i, e in enumerate(segment['effects'])
                    if e.get('require', {}).get('condition') == '_route_distance > 0')
    segment['effects'].insert(boundary, _when('_levitate_grip_walk_budget == true', [
        _set('_route_cost', '_route_cost + _route_distance'),
    ]))
    for effects in rows(segment['effects']):
        for effect in effects:
            if effect.get('call') == 'spend_combat_movement':
                effect['params']['movement_kind'] = '{_levitate_grip_budget_mode}'
            if effect.get('call') == 'derive_srd_scene_movement_budget':
                effect['params']['mode'] = '{_levitate_grip_budget_mode}'
