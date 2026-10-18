"""Select Fire Storm's admitted aligned cubes before the retained paid area owner.

Each cube supplies a minimum corner and an explicit point on one face. Unseen
blocked origins form on the near side of their first actual solid obstruction.
Rotated cubes are not represented by this bounded scene adapter.
"""
from copy import deepcopy

from .srd_lite_attacks import _effect_names
from .srd_scene_relations import POSITION_EXPORTS, _tool_scene
from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set
from .srd_timed_senses import _loop, _push
from .srd_spell_magic_zones import _when
from .srd_lite_mass_cure_geometry import _effect_lists
from .build_srd_5_2_1_mechanics import _tool_schema
from .srd_sunburst_geometry import _point_cover, AREA_ORIGIN, _area_origin, _body_nearest_call

QUALIFY = 'qualify_srd_lite_fire_storm_cubes'
PLACE = 'place_srd_lite_fire_storm_cubes'
PARAMETER = 'fire_storm_cubes'
SPELL = 'fire_storm'
FAMILY = 'cast_spell_instantaneous_area_save_damage'
EXPORTS = ('_fire_storm_targets', '_fire_storm_half_cover', '_fire_storm_three_quarters',
           '_fire_storm_objects', '_fire_storm_distance', '_fire_storm_cube_count',
           '_fire_origin_rows', '_fire_origins_relocated')
GATE = ("spell == 'fire_storm' or (spell in definitions.srd_lite_aliases and "
        "definitions.srd_lite_aliases[spell] == 'fire_storm')")


def _intent():
    """Expose ten bounded boxes with actual face origins, not reported membership."""
    point = {'type': 'array', 'items': {'type': 'number'}}
    return _parameter(PARAMETER, 'array', required=False, default=[], max_items=10,
        items={'type': 'object', 'additionalProperties': False,
               'required': ['min', 'origin', 'include_origin'],
               'properties': {'min': deepcopy(point), 'origin': deepcopy(point),
                               'include_origin': {'type': 'boolean'},
                               'origin_unseen': {'type': 'boolean'}}},
        description='Fire Storm only: each aligned 10-foot cube has min [x,y,z], '
                     'origin [x,y,z] on a face, and include_origin. '
                     'Set origin_unseen true for an unseen point; a blocked origin forms '
                     'on the near side of the first solid obstruction, translating that cube. '
                    'Actual scene recipients and ignition are derived before payment. '
                    'For Cover that varies by cube origin, admit target scene metadata.cover_from_points '
                    'rows with point [x,y,z], plane, and cover (none, half, three_quarters, or total).')


def _face_contact():
    """Permit face rounding while requiring positive overlap beyond the same tolerance."""
    pairs = []
    for axis in range(3):
        rest = [i for i in range(3) if i != axis]
        touch = (f'(abs(_fire_cube.min[{axis}] + 10 - _fire_other.min[{axis}]) <= 0.000000001 or '
                 f'abs(_fire_other.min[{axis}] + 10 - _fire_cube.min[{axis}]) <= 0.000000001)')
        overlaps = ' and '.join(
            f'max(_fire_cube.min[{i}], _fire_other.min[{i}]) + 0.000000001 < '
            f'min(_fire_cube.min[{i}] + 10, _fire_other.min[{i}] + 10)' for i in rest)
        pairs.append('(' + touch + ' and ' + overlaps + ')')
    return pairs


def _origin_on_face(cube):
    """Keep face comparisons within the expression bound and tolerate native arithmetic."""
    distances = [f'abs({cube}.origin[{i}] - {cube}.min[{i}]{offset})'
                 for i in range(3) for offset in ('', ' - 10')]
    return 'min(' + ', '.join(distances) + ') <= 0.000000001'


def _query_cube():
    return {'spatial_query': {'scene': 'state.spatial_scene', 'operation': 'area',
        'snapshot': '{_info_snapshot}', 'query': {'map_id': '{state.spatial_scene.map.id}',
        'shape': {'kind': 'box', 'min': '{_fire_cube.min}', 'max': '{_fire_cube_max}'},
        'include': ['entities'], 'max_results': 4096}}, 'store_as': '_fire_cube_area'}


def _origin_only_touch():
    """Exclude only a single origin point after native box/circle intersection."""
    return [
        _when("_fire_frame.footprint.kind == 'box'", [
            _set('_fire_cube_touch_min', [
                'max(_fire_cube.min[0], _fire_frame.position[0] - _fire_frame.footprint.half_extents[0])',
                'max(_fire_cube.min[1], _fire_frame.position[1] - _fire_frame.footprint.half_extents[1])',
                'max(_fire_cube.min[2], _fire_frame.position[2])']),
            _set('_fire_cube_touch_max', [
                'min(_fire_cube.min[0] + 10, _fire_frame.position[0] + _fire_frame.footprint.half_extents[0])',
                'min(_fire_cube.min[1] + 10, _fire_frame.position[1] + _fire_frame.footprint.half_extents[1])',
                'min(_fire_cube.min[2] + 10, _fire_frame.position[2] + _fire_frame.height)']),
            _set('_fire_origin_only_touch', ' and '.join(
                f'_fire_cube_touch_min[{i}] == _fire_cube.origin[{i}] and '
                f'_fire_cube_touch_max[{i}] == _fire_cube.origin[{i}]' for i in range(3))),
        ]),
        _when("_fire_frame.footprint.kind == 'circle'", [
            _set('_fire_circle_nearest', [
                f'max(_fire_cube.min[{i}], min(_fire_frame.position[{i}], _fire_cube.min[{i}] + 10))'
                for i in range(2)]),
            _set('_fire_circle_distance_squared',
                 '(_fire_circle_nearest[0] - _fire_frame.position[0]) ** 2 + '
                 '(_fire_circle_nearest[1] - _fire_frame.position[1]) ** 2'),
            _set('_fire_origin_only_touch',
                 '_fire_circle_nearest[0] == _fire_cube.origin[0] and '
                 '_fire_circle_nearest[1] == _fire_cube.origin[1] and '
                 'abs(_fire_circle_distance_squared - _fire_frame.footprint.radius ** 2) <= 0.000000001 and '
                 'max(_fire_cube.min[2], _fire_frame.position[2]) == _fire_cube.origin[2] and '
                 'min(_fire_cube.min[2] + 10, _fire_frame.position[2] + _fire_frame.height) == _fire_cube.origin[2]'),
        ]),
    ]


def _object_cover():
    """Use bounded native body samples and existing solid-barrier semantics for fuel."""
    return [
        {'spatial_query': {'scene': 'state.spatial_scene', 'operation': 'exposure',
            'snapshot': '{_info_snapshot}', 'query': {
                'map_id': '{state.spatial_scene.map.id}', 'observer': {'point': '{_fire_cube.origin}'},
                'target': {'entity_id': '{_fire_frame.id}'},
                'samples': {'horizontal': 3, 'vertical': 3}, 'include': ['geometry'],
                'metadata_filter': {'key': 'plane', 'value': '{_fire_storm_plane}',
                                    'missing_matches': False}}}, 'store_as': '_fire_object_exposure'},
        _require('len(_fire_object_exposure.samples) == 9',
                 'Fire Storm fuel requires the actual bounded solid-cover result.'),
        _set('_fire_object_clear', False),
        _set('_fire_object_samples', '_fire_object_exposure.samples'),
        _loop('_fire_object_samples', '_fire_object_sample', [
            _when('_fire_object_clear != true', [
                _call('srd_information_barriers', {'start': '{_fire_cube.origin}',
                    'end': '{_fire_object_sample.point}', 'plane': '{_fire_storm_plane}',
                    'rule': 'solid'}, ('_information_blocked',)),
                _when('_information_blocked != true', [_set('_fire_object_clear', True)]),
            ]),
        ]),
    ]


def _placement():
    """Translate an unseen blocked cube from the native ray's first real solid hit."""
    return _tool_scene('Place actual Fire Storm cubes, including unseen blocked origins.',
        [_entity('caster_state'), _intent()], [
            _call('srd_information_scene_entity', {'subject': '{_caster_state_id}'}, POSITION_EXPORTS),
            _set('_fire_placement_source', '_info_scene_entity'),
            _set('_fire_actual_cubes', []), _set('_fire_origin_rows', []),
            _set('_fire_origins_relocated', 0), _set('_fire_cube_number', 0),
            _set('_fire_requested_cubes', 'fire_storm_cubes'),
            _loop('_fire_requested_cubes', '_fire_requested_cube', [
                _set('_fire_cube_number', '_fire_cube_number + 1'),
                _require('len(_fire_requested_cube.min) == 3 and len(_fire_requested_cube.origin) == 3',
                         'Each Fire Storm cube minimum and origin must have exactly three coordinates.'),
                *[_require(
                    f'_fire_requested_cube.min[{i}] >= state.spatial_scene.map.bounds.min[{i}] and '
                    f'_fire_requested_cube.min[{i}] + 10 <= state.spatial_scene.map.bounds.max[{i}] and '
                    f'_fire_requested_cube.origin[{i}] >= _fire_requested_cube.min[{i}] - 0.000000001 and '
                    f'_fire_requested_cube.origin[{i}] <= _fire_requested_cube.min[{i}] + 10 + 0.000000001',
                    'Each requested Fire Storm cube and origin must fit the admitted scene.')
                    for i in range(3)],
                _require(_origin_on_face('_fire_requested_cube'),
                         'Each Fire Storm origin must lie on an actual cube face.'),
                _body_nearest_call('_fire_placement_source', '_fire_requested_cube.origin'),
                _require('_mass_cure_distance_squared <= 150 ** 2 + 0.000000001',
                         'An actual Fire Storm cube origin exceeds 150 feet.'),
                _set('_fire_placement_start', '_mass_cure_nearest'),
                {'call': AREA_ORIGIN, 'params': {
                    'start': '{_fire_placement_start}', 'origin': '{_fire_requested_cube.origin}',
                    'plane': '{_fire_placement_source.metadata.plane}'},
                 'export_context': {'_fire_placed_origin': '_area_origin',
                                    '_fire_placed_blocked': '_area_origin_blocked',
                                    '_fire_start_inside': '_area_origin_start_inside'}},
                _require('_fire_placed_blocked != true or _fire_requested_cube.origin_unseen == true',
                         'A solid barrier blocks a seen Fire Storm origin; declare the actual unseen-point intent.'),
                _require('_fire_start_inside != true',
                         'Fire Storm cannot place an area from inside a blocking solid.'),
                _push('_fire_actual_cubes', {
                    'min': [f'_fire_requested_cube.min[{i}] + _fire_placed_origin[{i}] - _fire_requested_cube.origin[{i}]'
                            for i in range(3)],
                    'origin': '_fire_placed_origin', 'include_origin': '_fire_requested_cube.include_origin'}),
                _when('_fire_placed_blocked == true', [
                    _set('_fire_origins_relocated', '_fire_origins_relocated + 1'),
                    _call('format_srd_information_projection', {'message':
                        'Cube {_fire_cube_number}: requested {_fire_requested_cube.origin}; '
                        'formed {_fire_placed_origin} on the near side of the obstruction.'},
                        ('_info_projection_message',)),
                    _set('_fire_placement_text', '_info_projection_message'),
                    _push('_fire_origin_rows', {'label': "'Origin relocated'", 'value': '_fire_placement_text'})
                ])
            ])
        ])


def _qualifier():
    """Derive actual unique creature/cover/fuel lists without rolling or state writes."""
    cube_valid = [
        f'_fire_cube.min[{i}] >= state.spatial_scene.map.bounds.min[{i}] and '
        f'_fire_cube.min[{i}] + 10 <= state.spatial_scene.map.bounds.max[{i}] and '
        f'_fire_cube.origin[{i}] >= _fire_cube.min[{i}] - 0.000000001 and '
        f'_fire_cube.origin[{i}] <= _fire_cube.min[{i}] + 10 + 0.000000001' for i in range(3)]
    on_face = _origin_on_face('_fire_cube')
    return _tool_scene('Qualify actual Fire Storm cubes, unique creatures, Cover and eligible fuel.',
        [_entity('caster_state'), _intent()], [
        _require('len(fire_storm_cubes) >= 2 and len(fire_storm_cubes) <= 10',
                 'Choose two through ten Fire Storm cubes; each needs contact with another cube.'),
        _call('srd_information_scene_entity', {'subject': '{_caster_state_id}'}, POSITION_EXPORTS),
        _set('_fire_source', '_info_scene_entity'),
        _require('_fire_source.metadata.plane != null',
                 'Fire Storm needs the actual caster body and admitted plane.'),
        _set('_fire_storm_plane', '_fire_source.metadata.plane'),
        _set('_fire_storm_targets', []), _set('_fire_storm_half_cover', []),
        _set('_fire_storm_three_quarters', []), _set('_fire_storm_objects', []),
        _set('_fire_half_candidates', []), _set('_fire_quarter_candidates', []),
        _set('_fire_clear_candidates', []),
        _set('_fire_storm_distance', 0), _set('_fire_storm_cube_count', 'len(fire_storm_cubes)'),
        _call(PLACE, {'caster_state': '{_caster_state_id}', PARAMETER: '{fire_storm_cubes}'},
              ('_fire_actual_cubes', '_fire_origin_rows', '_fire_origins_relocated')),
        _set('_fire_cubes', '_fire_actual_cubes'),
        _loop('_fire_cubes', '_fire_cube', [
            _require('len(_fire_cube.min) == 3 and len(_fire_cube.origin) == 3',
                     'Each Fire Storm cube minimum and origin must have exactly three coordinates.'),
            *[_require(condition, 'Each Fire Storm cube and origin must fit the admitted scene.')
              for condition in cube_valid],
            _require(on_face, 'Each Fire Storm origin must lie on an actual cube face.'),
            _set('_fire_cube_contact', False),
            _loop('_fire_cubes', '_fire_other', [
                *[_when(condition, [_set('_fire_cube_contact', True)]) for condition in _face_contact()],
            ]),
            _require('_fire_cube_contact == true',
                     'Each supported Fire Storm cube must share some face area with another cube.'),
            _body_nearest_call('_fire_source', '_fire_cube.origin'),
            _require('_mass_cure_distance_squared <= 150 ** 2 + 0.000000001',
                     'An actual Fire Storm cube origin exceeds 150 feet.'),
            _set('_fire_storm_distance', 'max(_fire_storm_distance, _mass_cure_distance_squared ** 0.5)'),
            _set('_fire_cube_max', [f'_fire_cube.min[{i}] + 10' for i in range(3)]),
            _query_cube(),
            _require('_fire_cube_area.truncated != true', 'Fire Storm exceeded the actual scene query bound.'),
            _set('_fire_cube_hits', '_fire_cube_area.results'),
            _set('_fire_cube_candidates', []),
            _loop('_fire_cube_hits', '_fire_hit', [
                _call('srd_information_scene_entity', {'subject': '{_fire_hit.id}'}, POSITION_EXPORTS),
                _set('_fire_frame', '_info_scene_entity'),
                _require('_fire_frame.metadata.plane != null', 'Fire Storm needs each intersecting body\'s plane.'),
                _when('_fire_frame.metadata.plane == _fire_storm_plane', [
                    _require("_fire_frame.footprint.kind == 'box' or _fire_frame.footprint.kind == 'circle'",
                             'Fire Storm needs actual admitted box or circle bodies.'),
                    *_origin_only_touch(),
                    _when('_fire_cube.include_origin == true or _fire_origin_only_touch != true', [
                        _when('_fire_frame.metadata.participant_ref != null and '
                              '_fire_frame.metadata.is_object != true and '
                              '_fire_frame.metadata.creature_type != \'spell_force\'', [
                            _require('_fire_frame.metadata.participant_ref not in _fire_cube_candidates',
                                     'A Fire Storm creature has ambiguous active scene proxies.'),
                            _push('_fire_cube_candidates', '_fire_frame.metadata.participant_ref'),
                        ]),
                        _when('_fire_frame.metadata.participant_ref == null and '
                              '_fire_frame.metadata.is_object == true and '
                              '_fire_frame.metadata.worn_by == null and _fire_frame.metadata.carried_by == null', [
                            *_object_cover(),
                            _when('_fire_object_clear == true', [
                                _require('_fire_frame.metadata.flammable != null',
                                         'Record actual Fire Storm scenery combustibility before casting.'),
                                _when('_fire_frame.metadata.flammable == true', [
                                    _require('_fire_frame.metadata.srd_ordinary_flame.kind == \'ordinary_flame\'',
                                             'Record actual Fire Storm ordinary fuel facts before casting.'),
                                    _when('_fire_frame.id not in _fire_storm_objects', [
                                        _require('len(_fire_storm_objects) < 64', 'Fire Storm exceeds 64 supported fuel recipients.'),
                                        _push('_fire_storm_objects', '_fire_frame.id'),
                                    ]),
                                ]),
                            ]),
                        ]),
                    ]),
                ]),
            ]),
            _call('collect_srd_sunburst_cover_targets', {'source_state': '{_caster_state_id}',
                'targets': '{_fire_cube_candidates}', 'origin': '{_fire_cube.origin}'},
                ('_srd_area_targets', '_srd_area_half_cover', '_srd_area_three_quarters')),
            _set('_fire_cube_clear_targets', '_srd_area_targets'),
            _loop('_fire_cube_clear_targets', '_fire_recipient', [
                _require('_fire_recipient in _fire_storm_targets or len(_fire_storm_targets) < 64',
                         'Fire Storm exceeds 64 supported creature recipients.'),
                _when('_fire_recipient not in _fire_storm_targets', [
                    _push('_fire_storm_targets', '_fire_recipient'),
                ]),
                _when('_fire_recipient in _srd_area_three_quarters', [
                    _when('_fire_recipient not in _fire_quarter_candidates', [_push('_fire_quarter_candidates', '_fire_recipient')]),
                ], [
                    _when('_fire_recipient in _srd_area_half_cover', [
                        _when('_fire_recipient not in _fire_half_candidates', [_push('_fire_half_candidates', '_fire_recipient')]),
                    ], [
                        _when('_fire_recipient not in _fire_clear_candidates', [_push('_fire_clear_candidates', '_fire_recipient')]),
                    ]),
                ]),
            ]),
        ]),
        _loop('_fire_storm_targets', '_fire_recipient', [
            _when('_fire_recipient not in _fire_clear_candidates', [
                _when('_fire_recipient in _fire_half_candidates', [
                    _push('_fire_storm_half_cover', '_fire_recipient'),
                ], [
                    _when('_fire_recipient in _fire_quarter_candidates', [_push('_fire_storm_three_quarters', '_fire_recipient')]),
                ]),
            ]),
        ]),
    ])


def _transport(runtime):
    """Carry new cube intent through the established Sunburst staging templates."""
    changed = set()
    def clone_intent(value):
        if isinstance(value, str):
            return value.replace('sunburst_origin', PARAMETER)
        if isinstance(value, list):
            return [clone_intent(child) for child in value]
        if isinstance(value, dict):
            return {key: clone_intent(child) for key, child in value.items()}
        return value
    for name, tool in runtime['tools'].items():
        if any(p.get('name') == 'sunburst_origin' for p in tool['parameters']):
            tool['parameters'].append(_intent())
            changed.add(name)

        def visit(value):
            if isinstance(value, dict):
                params = value.get('params', {})
                if value.get('call') in {'cast_spell', 'cast_spell_outside_combat', 'begin_srd_spell_action'}:
                    original = params.get('sunburst_origin')
                    fixed_spell = params.get('spell')
                    if original is None and fixed_spell in runtime['definitions']['spells'] and fixed_spell != SPELL:
                        # Fixed casts of another spell do not carry Fire Storm's cube intent.
                        pass
                    elif not isinstance(original, str):
                        raise ValueError('Actual Fire Storm cast transport lacks the retained origin intent: ' + name)
                    else:
                        params[PARAMETER] = original.replace('sunburst_origin', PARAMETER)
                        changed.add(name)
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                additions = []
                for index, child in enumerate(value):
                    if isinstance(child, dict):
                        # Pending action capture rows and delayed source choices.
                        if child.get('key') == "'sunburst_origin'":
                            additions.append((index + 1, clone_intent(child)))
                        field = child.get('set', {}).get('field', '')
                        if field.endswith('.sunburst_origin'):
                            clone = clone_intent(child)
                            additions.append((index + 1, clone))
                    visit(child)
                for index, child in reversed(additions):
                    value.insert(index, child)
                    changed.add(name)
        visit(tool['effects'])
    return sorted(changed)


def _normalize_staged_fire_lists(runtime):
    """Represent absent derived Cover as null at the public cast callback."""
    parameters = {p['name']:p for p in runtime['tools']['cast_spell']['parameters']}
    owners = [(rows, index, effect) for rows in _effect_lists(runtime['tools']['complete_srd_pending_action']['effects'])
              for index, effect in enumerate(rows) if effect.get('call') == 'cast_spell']
    if len(owners) != 1:
        raise ValueError('Keep the sole original pending cast callback')
    rows, index, call = owners[0]
    normalizers = []
    for name in ('half_cover_targets', 'three_quarters_cover_targets'):
        parameter = parameters[name]
        if parameter.get('min_items') != 1 or parameter.get('required', True) or parameter.get('default') is not None:
            raise ValueError('Keep the optional nonempty public cast list contract: ' + name)
        source = '_pending_action.parameters.' + name
        if call['params'].get(name) != '{' + source + '}':
            raise ValueError('Keep the original pending cast list choice: ' + name)
        field = '_fire_pending_public_' + name
        normalizers.extend([
            _set(field, None),
            _when(source + ' != null', [_set(field, source)]),
            _when("_pending_action.spell == 'fire_storm' and " + field + ' != null and len(' + field + ') == 0',
                  [_set(field, None)])])
        call['params'][name] = '{' + field + '}'
    rows[index:index] = normalizers
    return ('half_cover_targets', 'three_quarters_cover_targets')


def bind_fire_storm_scene_geometry(runtime):
    """Derive Fire Storm facts while preserving profiles, paid dice and ignition owners."""
    tools = runtime['tools']
    if QUALIFY in tools:
        raise ValueError('Fire Storm geometry is already bound')
    profile = runtime['definitions']['spells'][SPELL]
    expected = {'family': 'instantaneous_area_save_damage', 'range_feet': 150,
                'area_shape': 'cube', 'area_size_feet': 10, 'base_dice': 7,
                'damage_die': 'd10', 'damage_type': 'fire', 'success_damage': 'half'}
    if any(profile.get(key) != value for key, value in expected.items()):
        raise ValueError('Fire Storm differs from its preserved fixed SRD profile')
    tools.update(_point_cover(tools))
    transport = _transport(runtime)
    _normalize_staged_fire_lists(runtime)
    tools[QUALIFY] = _qualifier()
    tools[PLACE] = _placement()
    tools[AREA_ORIGIN] = _area_origin()
    facts = {
        '_retained_ice_dispatch_targets': '_fire_storm_targets',
        '_retained_ice_dispatch_half_cover_targets': '_fire_storm_half_cover',
        '_retained_ice_dispatch_three_quarters_cover_targets': '_fire_storm_three_quarters',
        '_retained_ice_dispatch_origin_distance': 'ceil(_fire_storm_distance)',
        'area_object_targets': '_fire_storm_objects',
        'all_targets_in_area': True,
        'origin_has_clear_path': True,
    }
    for name in ('cast_spell', 'begin_srd_spell_action'):
        tool = tools[name]
        declared = {p['name'] for p in tool['parameters']}
        originals = {key: '_fire_dispatch_' + key.lstrip('_') for key in facts}
        tool['effects'] = _effect_names(tool['effects'], originals)
        defaults = [_set(originals[key], key if key in declared else None)
                    for key in facts if not key.startswith('_')]
        # These retained dispatch defaults exist before Lite aliases bind and
        # must run before Fire Storm replaces their reported values with facts.
        initializers = []
        for key in facts:
            if not key.startswith('_'):
                continue
            rows = [(i, effect) for i, effect in enumerate(tool['effects'])
                    if effect.get('set', {}).get('field') == originals[key]]
            if len(rows) != 2 or rows[0][1]['set'].get('value') is not None or not rows[1][1].get('condition'):
                raise ValueError('Expected retained dispatch initialization: ' + name + ':' + key)
            initializers.extend(i for i, _ in rows)
        start = max(initializers) + 1
        preflight = _when(GATE, [
            _call(QUALIFY, {'caster_state': '{_caster_state_id}', PARAMETER: '{' + PARAMETER + '}'}, EXPORTS),
            *[_set(originals[key], value) for key, value in facts.items()],
        ])
        moved_guards = [guard for guard in tool.get('pre_conditions', []) if any(
            key in guard['condition'] for key in ('all_targets_in_area', 'caster_can_see_origin', 'origin_has_clear_path'))]
        tool['pre_conditions'] = [guard for guard in tool.get('pre_conditions', []) if guard not in moved_guards]
        tool['effects'][start:start] = [preflight, *_effect_names([
            _require(guard['condition'], guard['error']) for guard in moved_guards], originals)]
        tool['effects'][:0] = defaults
    # The actual family call already consumes the projected root variables;
    # origin and recipient arrays reach the paid owner without a second roll.
    root = tools['cast_spell']
    calls = [effect for rows in _effect_lists(root['effects']) for effect in rows if effect.get('call') == FAMILY]
    if len(calls) != 1:
        raise ValueError('Expected one original Fire Storm area family dispatcher')
    captures = [effect['set']['value'] for rows in _effect_lists(root['effects']) for effect in rows
                if effect.get('set', {}).get('field') == '_retained_cast_tail_context']
    if len(captures) != 1 or not isinstance(captures[0], list):
        raise ValueError('Keep the sole native cast context capture for Fire Storm receipts')
    for field in EXPORTS:
        captures[0].append({'key': "'" + field + "'", 'missing': field + ' == null', 'value': field})
    root['effects'].append(_when("_ms == 'fire_storm' and _spell_cast_failed != true and "
                               "_pending_spell_operation_lost != true", [
        {'call': 'format_srd_information_projection', 'params': {'message':
            '{_fire_storm_cube_count} admitted cubes; creatures: {_fire_storm_targets}; '
            'fuel objects: {_fire_storm_objects}.'},
            'export_context': {'_fire_storm_area_caption': '_info_projection_message'}},
        _push('_spell_detail_rows', {'label': "'Fire Storm area'", 'value': '_fire_storm_area_caption'}),
        _push('_spell_card_summary', {'label': "'Cubes'", 'value': '_fire_storm_cube_count'}),
        _loop('_fire_origin_rows', '_fire_origin_row', [
            _push('_spell_detail_rows', '_fire_origin_row')]),
        _when('_fire_origins_relocated > 0', [
            _push('_spell_card_summary', {'label': "'Origins relocated'", 'value': '_fire_origins_relocated'})
        ]),
    ]))
    return {'qualifier': QUALIFY, 'transport_owners': transport,
            'preflight_owners': ['cast_spell', 'begin_srd_spell_action'],
            'range_policy': 'each_explicit_cube_origin', 'rotated_cubes_supported': False}


def expose_fire_storm_cube_intent(runtime, record):
    """Keep each public transport declaration aligned with its new optional cube field."""
    exposed = []
    for declaration in record['tools']:
        name = declaration['name']
        tool = runtime['tools'][name]
        if not any(parameter.get('name') == PARAMETER for parameter in tool['parameters']):
            continue
        schema = _tool_schema(tool, tool_name=name)
        if PARAMETER in schema.get('required', []):
            raise ValueError('Fire Storm cube intent must remain optional outside its actual cast')
        declaration['input_schema']['properties'][PARAMETER] = deepcopy(schema['properties'][PARAMETER])
        exposed.append(name)
    return exposed
