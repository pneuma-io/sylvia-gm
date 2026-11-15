"""Validate Mass Cure Wounds' chosen Sphere from the admitted scene before casting."""
from copy import deepcopy

from .srd_launch_restorative import MASS_CURE_AREA_GUARD
from .srd_scene_relations import POSITION_EXPORTS, _array, _tool_scene
from .srd_timed_actor_defenses import _call, _entity, _require, _set
from .srd_timed_senses import _loop
from .srd_spell_magic_zones import _when

QUALIFY = 'qualify_srd_lite_mass_cure_sphere'
FAMILY = 'cast_spell_instantaneous_multi_target_healing'
SPELL = 'mass_cure_wounds'


def _nearest_body(frame, point):
    """Find the nearest occupied point on an admitted Box or circular creature body."""
    return [
        _require(frame + ".footprint.kind == 'box' or " + frame + ".footprint.kind == 'circle'",
                 'Mass Cure Wounds needs a supported admitted creature footprint.'),
        _set('_mass_cure_nearest_z',
             f'max({frame}.position[2], min({point}[2], {frame}.position[2] + {frame}.height))'),
        _when(frame + ".footprint.kind == 'box'", [
            _set('_mass_cure_nearest_x',
                 f'max({frame}.position[0] - {frame}.footprint.half_extents[0], '
                 f'min({point}[0], {frame}.position[0] + {frame}.footprint.half_extents[0]))'),
            _set('_mass_cure_nearest_y',
                 f'max({frame}.position[1] - {frame}.footprint.half_extents[1], '
                 f'min({point}[1], {frame}.position[1] + {frame}.footprint.half_extents[1]))'),
        ], [
            _set('_mass_cure_dx', f'{point}[0] - {frame}.position[0]'),
            _set('_mass_cure_dy', f'{point}[1] - {frame}.position[1]'),
            _set('_mass_cure_radial', '(_mass_cure_dx ** 2 + _mass_cure_dy ** 2) ** 0.5'),
            _set('_mass_cure_fraction', 1),
            _when('_mass_cure_radial > ' + frame + '.footprint.radius', [
                _set('_mass_cure_fraction', frame + '.footprint.radius * _mass_cure_radial ** -1'),
            ]),
            _set('_mass_cure_nearest_x', frame + '.position[0] + _mass_cure_dx * _mass_cure_fraction'),
            _set('_mass_cure_nearest_y', frame + '.position[1] + _mass_cure_dy * _mass_cure_fraction'),
        ]),
        _set('_mass_cure_nearest',
             ['_mass_cure_nearest_x', '_mass_cure_nearest_y', '_mass_cure_nearest_z']),
        _set('_mass_cure_distance_squared',
             f'({point}[0] - _mass_cure_nearest_x) ** 2 + '
             f'({point}[1] - _mass_cure_nearest_y) ** 2 + '
             f'({point}[2] - _mass_cure_nearest_z) ** 2'),
    ]


def _qualifier(targets_parameter, lasting_parameter):
    """Read actual range, sight, plane, Sphere membership and solid cover without rolling."""
    return _tool_scene('Validate the chosen Mass Cure Wounds point and up to six actual recipients.',
        [_entity('caster_state'), deepcopy(targets_parameter), _array('origin'),
         {'type': 'string', **deepcopy(lasting_parameter)}], [
            _require('targets != null and len(targets) >= 1 and len(targets) <= 6',
                     'Mass Cure Wounds needs one through six selected creatures.'),
            _require('origin[0] >= state.spatial_scene.map.bounds.min[0] and '
                     'origin[0] <= state.spatial_scene.map.bounds.max[0] and '
                     'origin[1] >= state.spatial_scene.map.bounds.min[1] and '
                     'origin[1] <= state.spatial_scene.map.bounds.max[1] and '
                     'origin[2] >= state.spatial_scene.map.bounds.min[2] and '
                     'origin[2] <= state.spatial_scene.map.bounds.max[2]',
                     'Mass Cure Wounds needs a chosen point inside the admitted scene.'),
            _call('srd_information_scene_entity', {'subject': '{_caster_state_id}'}, POSITION_EXPORTS),
            _require('_info_scene_entity.metadata.plane != null',
                     "Mass Cure Wounds needs the caster's actual plane."),
            _set('_mass_cure_plane', '_info_scene_entity.metadata.plane'),
            *_nearest_body('_info_scene_entity', 'origin'),
            _require('_mass_cure_distance_squared <= 60 ** 2 + 0.000000001',
                     "Mass Cure Wounds' actual chosen point exceeds 60 feet."),
            _set('_mass_cure_distance', '_mass_cure_distance_squared ** 0.5'),
            _call('srd_information_barriers', {'start': '{_mass_cure_nearest}', 'end': '{origin}',
                  'plane': '{_mass_cure_plane}', 'rule': 'solid'}, ('_information_blocked',)),
            _require('_information_blocked != true',
                     "A solid barrier blocks Mass Cure Wounds' chosen point."),
            _call('resolve_srd_illumination_point_sight', {'observer_state': '{_caster_state_id}',
                  'point': '{origin}', 'lasting_state': '{_lasting_state_id}'},
                  ('_illumination_point_can_see', '_illumination_point_plane')),
            _require('_illumination_point_can_see == true and _illumination_point_plane == _mass_cure_plane',
                     "The caster cannot actually see Mass Cure Wounds' chosen point on the casting plane."),
            _loop('targets', '_mass_cure_target', [
                _call('validate_spell_healing_target', {'target': '{_mass_cure_target}'}),
                _call('srd_information_scene_entity', {'subject': '{_mass_cure_target}'}, POSITION_EXPORTS),
                _require('_info_scene_entity.metadata.participant_ref == _mass_cure_target and '
                         '_info_scene_entity.metadata.is_object != true and '
                         '_info_scene_entity.metadata.plane == _mass_cure_plane',
                         "A selected Mass Cure Wounds recipient must be an actual creature on the casting plane."),
                _set('_mass_cure_scene_target', '_info_scene_entity.id'),
                *_nearest_body('_info_scene_entity', 'origin'),
                _require('_mass_cure_distance_squared <= 30 ** 2 + 0.000000001',
                         "A selected Mass Cure Wounds recipient is outside its actual 30-foot Sphere."),
                {'spatial_query': {'scene': 'state.spatial_scene', 'operation': 'exposure',
                    'snapshot': '{_info_snapshot}', 'query': {
                        'map_id': '{state.spatial_scene.map.id}', 'observer': {'point': '{origin}'},
                        'target': {'entity_id': '{_mass_cure_scene_target}'},
                        'samples': {'horizontal': 4, 'vertical': 4}, 'include': ['geometry'],
                        'metadata_filter': {'key': 'plane', 'value': '{_mass_cure_plane}',
                                            'missing_matches': False}}},
                    'store_as': '_mass_cure_exposure'},
                _require('len(_mass_cure_exposure.samples) == 16',
                         'Mass Cure Wounds requires the actual bounded physical-cover result.'),
                _set('_mass_cure_clear_samples', 0),
                _set('_mass_cure_samples', '_mass_cure_exposure.samples'),
                _loop('_mass_cure_samples', '_mass_cure_sample', [
                    _call('srd_information_barriers', {'start': '{origin}',
                          'end': '{_mass_cure_sample.point}', 'plane': '{_mass_cure_plane}', 'rule': 'solid'},
                          ('_information_blocked',)),
                    _when('_information_blocked != true', [
                        _set('_mass_cure_clear_samples', '_mass_cure_clear_samples + 1'),
                    ]),
                ]),
                _require('_mass_cure_clear_samples > 0',
                         'Total solid cover blocks a selected Mass Cure Wounds recipient.'),
            ]),
        ])


def _effect_lists(effects):
    yield effects
    for effect in effects:
        for key in ('on_true', 'on_false'):
            if key in effect:
                yield from _effect_lists(effect[key])
        if 'for_each' in effect:
            yield from _effect_lists(effect['for_each']['do'])


def bind_mass_cure_scene_geometry(runtime):
    """Replace only the reported area guard; preserve original healing and payment owners."""
    tools = runtime['tools']
    if QUALIFY in tools:
        raise ValueError('Mass Cure Wounds geometry is already bound')
    profile = runtime['definitions']['spells'][SPELL]
    expected = {'family': 'instantaneous_multi_target_healing', 'targeting_mode': 'visible_point_sphere',
                'range_feet': 60, 'area_size_feet': 30, 'max_targets': 6,
                'requires_origin_sight': True, 'requires_each_target_sight': False}
    if any(profile.get(key) != value for key, value in expected.items()):
        raise ValueError('Mass Cure Wounds differs from its preserved targeting profile')
    parameters = {p['name']: p for p in tools['cast_spell']['parameters']}
    tools[QUALIFY] = _qualifier(parameters['targets'], parameters['lasting_state'])
    guards = tools['cast_spell'].get('pre_conditions', [])
    if guards.count(MASS_CURE_AREA_GUARD) != 1:
        raise ValueError('Expected the sole reported Mass Cure Wounds area guard')
    guards.remove(MASS_CURE_AREA_GUARD)
    gate = ("spell == 'mass_cure_wounds' or (spell in definitions.srd_lite_aliases and "
            "definitions.srd_lite_aliases[spell] == 'mass_cure_wounds')")
    preflight = _when(gate, [
        _call(QUALIFY, {'caster_state': '{_caster_state_id}', 'targets': '{targets}',
              'origin': '{environment_origin}', 'lasting_state': '{_lasting_state_id}'},
              ('_mass_cure_distance',)),
    ])
    for name in ('cast_spell', 'begin_srd_spell_action'):
        tools[name]['effects'].insert(0, deepcopy(preflight))
    # The existing family call still rolls and restores HP. Keep one truthful
    # geometry receipt beside its saved healing calculation in the cast card.
    family_calls = 0
    for rows in _effect_lists(tools['cast_spell']['effects']):
        for index, effect in enumerate(tuple(rows)):
            if effect.get('call') == FAMILY:
                family_calls += 1
                dispatch = []
                for key in ('origin_distance', 'caster_can_see_origin', 'origin_has_clear_path'):
                    original = effect['params'][key]
                    if not (isinstance(original, str) and original.startswith('{') and original.endswith('}')):
                        raise ValueError('The original healing origin must retain its context reference: ' + key)
                    reference = original[1:-1]
                    field = '_mass_cure_dispatch_' + key
                    dispatch.extend([_set(field, None),
                                     _when(reference + ' != null', [_set(field, reference)])])
                    effect['params'][key] = '{' + field + '}'
                dispatch.append(_when("_ms == 'mass_cure_wounds'", [
                    _set('_mass_cure_dispatch_origin_distance', 'ceil(_mass_cure_distance)'),
                    _set('_mass_cure_dispatch_caster_can_see_origin', True),
                    _set('_mass_cure_dispatch_origin_has_clear_path', True),
                ]))
                rows.insert(index, _when('true', dispatch))
                rows.insert(index + 2, _when("_ms == 'mass_cure_wounds' and _spell_healing != null", [
                    {'list_push': {'list': '_spell_detail_rows', 'value': {
                        'label': "'Healing sphere center (30 ft radius)'", 'value': 'environment_origin'}}},
                ]))
    if family_calls != 1:
        raise ValueError('Mass Cure Wounds must retain one original healing family call')
    return [QUALIFY, 'cast_spell', 'begin_srd_spell_action']
