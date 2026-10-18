"""Derive Sleet Storm's actual ground costs, excluding box-solid shadows along the route."""
from copy import deepcopy

from .srd_spell_environment import _array, _call, _each, _environment_tool, _param, _push, _require, _set, _when

SUPPORT = 'qualify_srd_sleet_storm_support'
TERRAIN = 'resolve_srd_sleet_storm_terrain_intervals'
CAST_CARD = 'format_srd_sleet_storm_cast_receipt'


def _cast_feedback():
    """Describe the actual saved storm without spending resources, rolling, or changing state."""
    return _environment_tool(
        'Read the actual saved Sleet Storm area for the paid-cast result card.',
        [_param('area_id')], [
            _set('_sleet_card_source', 'state.persistent_spell_areas[area_id]'),
            _require("_sleet_card_source.mode == 'sleet' and _sleet_card_source.active == true",
                     'Describe only an actual active Sleet Storm source.'),
            _set('_sleet_cast_summary_rows', [
                {'label': "'Origin'", 'value': '_sleet_card_source.origin'},
                {'label': "'Radius (ft)'", 'value': '_sleet_card_source.radius'},
                {'label': "'Height (ft)'", 'value': '_sleet_card_source.cylinder_max - _sleet_card_source.cylinder_min'}]),
            _set('_sleet_cast_detail_rows', [
                {'label': "'Plane'", 'value': '_sleet_card_source.plane'},
                {'label': "'Duration'", 'value': "'Concentration, up to 1 minute'"},
                {'label': "'Area effects'", 'value': "'Heavily obscured; exposed ground is Difficult Terrain; exposed flames extinguished.'"}])
        ])


def _faces():
    """Represent each visible box face with its signed depth and its four edge half-planes."""
    faces = []
    for axis in range(3):
        others = [i for i in range(3) if i != axis]
        for side, sign in (('min', 1), ('max', -1)):
            depth = f'_sleet_solid.volume.{side}[{axis}] - _sleet_ground_source.origin[{axis}]'
            direction = f'start[{axis}] - _sleet_ground_source.origin[{axis}]'
            delta = f'end[{axis}] - start[{axis}]'
            constraints = [{'a': f'{sign} * ({direction} - ({depth}))',
                            'b': f'{sign} * ({delta})', 'strict': True}]
            for other in others:
                for bound, edge_sign in (('min', 1), ('max', -1)):
                    edge = f'_sleet_solid.volume.{bound}[{other}] - _sleet_ground_source.origin[{other}]'
                    entering_edge = ('<' if bound == 'min' else '>')
                    tolerance = ('-' if bound == 'min' else '+')
                    constraints.append({
                        'a': f'{sign * edge_sign} * (({depth}) * (start[{other}] - '
                             f'_sleet_ground_source.origin[{other}]) - ({edge}) * ({direction}))',
                        'b': f'{sign * edge_sign} * (({depth}) * (end[{other}] - start[{other}]) '
                             f'- ({edge}) * ({delta}))',
                        'strict': False if other == 2 and bound == 'min' else
                            f'not (_sleet_ground_source.origin[{other}] {entering_edge} '
                            f'_sleet_solid.volume.{bound}[{other}] {tolerance} 0.000000001)'})
            faces.append({'depth': f'{sign} * ({depth})', 'constraints': constraints})
    return faces


def _clip_face():
    """Clip the route against a face's forward shadow; grazing a solid face has no interior."""
    return [
        _set('_sleet_shadow_lo', 0), _set('_sleet_shadow_hi', 1),
        _set('_sleet_shadow_valid', True),
        _set('_sleet_face_constraints', '_sleet_face.constraints'),
        _each('_sleet_face_constraints', '_sleet_plane', [
            _when('abs(_sleet_plane.b) < 0.000000000001', [
                _when('_sleet_plane.a < -0.000000001 or '
                      '(_sleet_plane.strict == true and _sleet_plane.a <= 0.000000001)',
                      [_set('_sleet_shadow_valid', False)])
            ], [
                _set('_sleet_shadow_cut', '-_sleet_plane.a * _sleet_plane.b ** -1'),
                _when('_sleet_plane.b > 0', [
                    _set('_sleet_shadow_lo', 'max(_sleet_shadow_lo, _sleet_shadow_cut)')
                ], [_set('_sleet_shadow_hi', 'min(_sleet_shadow_hi, _sleet_shadow_cut)')])
            ])
        ]),
        _when('_sleet_shadow_valid == true and _sleet_shadow_hi - _sleet_shadow_lo > 0.000000001', [
            _push('_sleet_shadows', {'entry': '_sleet_shadow_lo', 'exit': '_sleet_shadow_hi'})
        ])
    ]


def _ground_support():
    """Collect real supporting top faces and subtract the union of actual blocking box shadows."""
    same_plane = ('(_sleet_solid.metadata.plane == _sleet_ground_source.plane or '
                  '(_sleet_solid.metadata.plane == null and _sleet_ground_source.plane == \'material\'))')
    inside = ' and '.join(
        f'_sleet_ground_source.origin[{i}] > _sleet_solid.volume.min[{i}] + 0.000000001 and '
        f'_sleet_ground_source.origin[{i}] < _sleet_solid.volume.max[{i}] - 0.000000001'
        for i in range(3))
    closed_box = ' and '.join(
        f'_sleet_ground_source.origin[{i}] >= _sleet_solid.volume.min[{i}] - 0.000000001 and '
        f'_sleet_ground_source.origin[{i}] <= _sleet_solid.volume.max[{i}] + 0.000000001'
        for i in range(3))
    contact_planes = [
        _when(f'abs(_sleet_ground_source.origin[{axis}] - _sleet_solid.volume.{side}[{axis}]) < 0.000000001', [
            _push('_sleet_contact_planes', {
                'a': f'{sign} * (start[{axis}] - _sleet_ground_source.origin[{axis}])',
                'b': f'{sign} * (end[{axis}] - start[{axis}])',
                'strict': not (axis == 2 and side == 'min')})])
        for axis in range(3) for side, sign in (('min', 1), ('max', -1))]
    return _environment_tool(
        'Read actual same-plane ground intervals and exclude Total Cover shadows from box solids.',
        [_param('area_id'), _array('start'), _array('end')], [
            _set('_sleet_ground_source', 'state.persistent_spell_areas[area_id]'),
            _require('_sleet_ground_source.mode == \'sleet\'', 'Select the actual saved Sleet Storm source.'),
            _set('_sleet_supported_intervals', []), _set('_sleet_ground_intervals', []),
            _set('_sleet_shadows', []),
            _set('_sleet_ground_geometry', 'state.spatial_scene.geometry'),
            _each('_sleet_ground_geometry', '_sleet_solid', [
                _when('_sleet_solid.active == true and _sleet_solid.movement == \'blocking\' and ' + same_plane, [
                    _require('_sleet_solid.volume.kind == \'box\'',
                             'Sleet Storm ground shadows currently require admitted box solids.'),
                    _when('_sleet_solid.metadata.ground == true and '
                          'abs(start[2] - end[2]) < 0.000000001 and '
                          'abs(start[2] - _sleet_solid.volume.max[2]) < 0.000000001', [
                        _call('srd_information_segment_interval', {
                            'start': '{start}', 'end': '{end}', 'volume': '{_sleet_solid.volume}'},
                            ('_segment_hit', '_segment_entry', '_segment_exit')),
                        _when('_segment_hit == true and _segment_exit - _segment_entry > 0.000000001', [
                            _push('_sleet_ground_intervals', {'entry': '_segment_entry', 'exit': '_segment_exit'})
                        ])
                    ]),
                    _require('not (' + inside + ')',
                             'The actual Sleet Storm origin cannot be inside a blocking solid.'),
                    _set('_sleet_faces', _faces()),
                    _when(closed_box, [
                        _set('_sleet_contact_planes', []), *contact_planes,
                        _when('len(_sleet_contact_planes) > 0', [
                            _push('_sleet_faces', {'depth': 1, 'constraints': '_sleet_contact_planes'})])
                    ]),
                    _each('_sleet_faces', '_sleet_face', [
                        _when('_sleet_face.depth > 0.000000001', _clip_face())
                    ])
                ])
            ]),
            {'sort': {'collection': '_sleet_shadows', 'by': 'entry', 'direction': 'asc'}},
            _each('_sleet_ground_intervals', '_sleet_ground_interval', [
                _set('_sleet_visible_cursor', '_sleet_ground_interval.entry'),
                _each('_sleet_shadows', '_sleet_shadow', [
                    _when('_sleet_shadow.exit > _sleet_visible_cursor and '
                          '_sleet_shadow.entry < _sleet_ground_interval.exit', [
                        _when('_sleet_shadow.entry > _sleet_visible_cursor', [
                            _push('_sleet_supported_intervals', {
                                'entry': '_sleet_visible_cursor',
                                'exit': 'min(_sleet_shadow.entry, _sleet_ground_interval.exit)'})
                        ]),
                        _set('_sleet_visible_cursor',
                             'max(_sleet_visible_cursor, min(_sleet_shadow.exit, _sleet_ground_interval.exit))')
                    ])
                ]),
                _when('_sleet_ground_interval.exit - _sleet_visible_cursor > 0.000000001', [
                    _push('_sleet_supported_intervals', {
                        'entry': '_sleet_visible_cursor', 'exit': '_sleet_ground_interval.exit'})
                ])
            ])
        ])


def bind_sleet_ground_terrain(runtime):
    """Replace only Sleet's ground qualifier and its interval export; other terrain owners stay intact."""
    if 'sleet_storm' not in runtime['definitions']['spells']:
        return []
    tools = runtime['tools']
    tools[SUPPORT] = _ground_support()
    terrain = deepcopy(tools[TERRAIN])
    matches = []

    def visit(effects):
        for index, effect in enumerate(effects):
            if effect.get('call') == SUPPORT:
                matches.append((effects, index))
            for key in ('on_true', 'on_false'):
                if key in effect:
                    visit(effect[key])
            if 'for_each' in effect:
                visit(effect['for_each']['do'])

    visit(terrain['effects'])
    if len(matches) != 1:
        raise ValueError('Sleet terrain must have exactly one support owner.')
    effects, index = matches[0]
    expected = effects[index + 1].get('list_push', {})
    if expected.get('list') != '_sleet_route_intervals':
        raise ValueError('Sleet support must precede the retained interval export.')
    effects[index]['export_context'] = {'_sleet_supported_intervals': '_sleet_supported_intervals'}
    effects[index + 1] = _each('_sleet_supported_intervals', '_sleet_exposed_ground', [
        _push('_sleet_route_intervals', {
            'entry': '_segment_entry + (_segment_exit - _segment_entry) * _sleet_exposed_ground.entry',
            'exit': '_segment_entry + (_segment_exit - _segment_entry) * _sleet_exposed_ground.exit',
            'area_id': '_environment_id'})
    ])
    tools[TERRAIN] = terrain
    if CAST_CARD in tools:
        raise ValueError('Sleet cast feedback must be composed exactly once.')
    tools[CAST_CARD] = _cast_feedback()
    tools['finish_srd_retained_cast_tail']['effects'].append(
        _when("_retained_cast_tail_choices.spell == 'sleet_storm' and _spell_cast_failed != true and "
              "_pending_spell_operation_lost != true and "
              "state.persistent_spell_areas[caster_state.environment_area_id].active == true", [
            _call(CAST_CARD, {'area_id': '{caster_state.environment_area_id}'},
                  ('_sleet_cast_summary_rows', '_sleet_cast_detail_rows')),
            _each('_sleet_cast_summary_rows', '_sleet_cast_row', [
                _push('_spell_card_summary', '_sleet_cast_row')]),
            _each('_sleet_cast_detail_rows', '_sleet_cast_row', [
                _push('_spell_detail_rows', '_sleet_cast_row')])
        ]))
    return [SUPPORT, TERRAIN, CAST_CARD, 'finish_srd_retained_cast_tail']
