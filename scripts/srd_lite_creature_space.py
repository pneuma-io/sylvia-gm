"""SRD 5.2.1 ordinary passage using explicitly declared, centred gridless control circles."""
from copy import deepcopy

from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _tool, _when

CONVENTION = 'srd.5.2.1.control-circle.v1'
WIDTHS = [2.5, 5, 5, 10, 15, 20]
WIDTH = 'read_srd_creature_space_width'
PAIR = 'query_srd_creature_space_pair'
COLLECT = 'collect_srd_creature_space_intervals'
ENDPOINT = 'qualify_srd_creature_space_endpoint'
TURN = 'resolve_srd_occupied_space_turn_end'


def _each(collection, item, effects):
    return {'for_each': {'collection': collection, 'as': item, 'do': effects}}


def _push(collection, value):
    return {'list_push': {'list': collection, 'value': value}}


def _width():
    return _tool('Map an actual SRD size category to its declared control-space diameter in feet.',
        [_parameter('rank', 'integer', minimum=0, maximum=5)], [
            _set('_space_widths', WIDTHS), _set('_space_width', '_space_widths[rank]')])


def _pair():
    """Reuse the collector's admitted mover; resolve the fixed subject through its native owner."""
    return _tool('Query one actual creature pair; attitude alone does not establish an ally.',
        [_entity('actor_state'), _entity('other_state'),
         _parameter('moving_entity'),
         _parameter('moving_position', 'array', items={'type': 'number'}, min_items=3, max_items=3),
         _parameter('moving_radius', 'number'),
         _parameter('destination', 'array', items={'type': 'number'}, min_items=3, max_items=3),
         _parameter('start', 'array', items={'type': 'number'}, required=False, default=[], max_items=3)], [
        _require('actor_state.size_rank != null and other_state.size_rank != null',
                 'Creature space needs an explicit supported SRD size_rank for both creatures.'),
        _call(WIDTH, {'rank': '{other_state.size_rank}'}, ('_space_width',)),
        _call('srd_information_scene_entity', {'subject': '{_other_state_id}'}, ('_info_scene_entity',)),
        _set('_space_fixed_body', '_info_scene_entity'),
        _set('_space_moving_radius', 'moving_radius'),
        _set('_space_fixed_radius', '_space_width * 0.5'),
        _set('_space_origin', 'moving_position'),
        _set('_space_origin', 'start') | {'condition': 'len(start) > 0'},
        {'spatial_query': {'scene': 'state.spatial_scene', 'operation': 'circle_overlap', 'query': {
            'map_id': '{state.spatial_scene.map.id}', 'moving_entity_id': '{moving_entity}',
            'fixed_entity_id': '{_space_fixed_body.id}', 'destination': '{destination}',
            'origin': '{_space_origin}',
            'moving_radius': '{_space_moving_radius}', 'fixed_radius': '{_space_fixed_radius}'}},
         'store_as': '_space_overlap'},
        _set('_space_ally', "(actor_state.ally_state_ids != null and _other_state_id in actor_state.ally_state_ids)"),
        _set('_space_incapacitated', "'incapacitated' in other_state.conditions or "
             "'paralyzed' in other_state.conditions or 'petrified' in other_state.conditions or "
             "'stunned' in other_state.conditions or 'unconscious' in other_state.conditions"),
        _set('_space_allowed', '_space_ally or _space_incapacitated or other_state.size_rank == 0 or '
             'abs(actor_state.size_rank - other_state.size_rank) >= 2 or '
             "(actor_state.species == 'halfling' and other_state.size_rank > actor_state.size_rank)"),
        _set('_space_difficult', '_space_ally != true and other_state.size_rank != 0'),
        _set('_space_other_rank', 'other_state.size_rank'),
    ])


def _collect():
    return _tool('Collect continuous creature-space intervals from actual scene and relationship records.',
        [_entity('actor_state'), _parameter('destination', 'array', items={'type': 'number'}, min_items=3, max_items=3),
         _parameter('endpoint', 'boolean', required=False, default=False),
         _parameter('start', 'array', items={'type': 'number'}, required=False, default=[], max_items=3),
         _parameter('passage', 'boolean', required=False, default=True)], [
        _require('actor_state.size_rank != null', 'Creature space needs an explicit supported SRD size_rank.'),
        _call(WIDTH, {'rank': '{actor_state.size_rank}'}, ('_space_width',)),
        _set('_space_route_radius', '_space_width * 0.5'),
        _call('check_srd_form_movement_permissions', {'actor_state': '{_actor_state_id}'},
              ('_form_share_creature_space',)),
        _call('srd_information_scene_entity', {'subject': '{_actor_state_id}'}, ('_info_scene_entity',)),
        _set('_space_mover', '_info_scene_entity'),
        _set('_space_intervals', []), _set('_space_turn_prone', False),
        _set('_space_route_circles', []), _set('_space_route_blocked', False),
        _set('_space_scene_entities', 'state.spatial_scene.entities'),
        _each('_space_scene_entities', '_space_body', [
            _when('_space_body.active == true and _space_body.id != _space_mover.id and '
                  '_space_body.metadata.participant_ref != null and '
                  '(_space_body.metadata.plane == _space_mover.metadata.plane or '
                  '((_space_body.metadata.plane == null or _space_body.metadata.plane == \'material\') and '
                  '(_space_mover.metadata.plane == null or _space_mover.metadata.plane == \'material\')))', [
                _call(PAIR, {'actor_state': '{_actor_state_id}',
                            'other_state': '{_space_body.metadata.participant_ref}', 'destination': '{destination}',
                            'moving_entity': '{_space_mover.id}', 'moving_position': '{_space_mover.position}',
                            'moving_radius': '{_space_route_radius}',
                            'start': '{start}'},
                      ('_space_overlap', '_space_allowed', '_space_difficult', '_space_other_rank', '_space_fixed_radius')),
                _when('_form_share_creature_space != true', [
                    _set('_space_route_blocked', True) | {'condition': '_space_overlap.hit == true and '
                        '_space_allowed != true and _space_overlap.starts_inside != true'},
                    _push('_space_route_circles', {'entity_id': '_space_body.id', 'radius': '_space_fixed_radius',
                                                 'blocking': '_space_allowed != true'}),
                    _require('endpoint != true or _space_overlap.ends_inside != true',
                             'A willing move cannot end in another creature\'s occupied space.'),
                    _require('passage != true or _space_overlap.hit != true or _space_allowed == true or '
                             '_space_overlap.starts_inside == true',
                             'This creature does not permit passage through its occupied space.'),
                ]),
                _when('_space_overlap.hit == true and _space_difficult == true', [
                    _push('_space_intervals', {'entry': '_space_overlap.entry', 'exit': '_space_overlap.exit'}),
                ]),
                _when('_space_overlap.ends_inside == true and actor_state.size_rank != 0 and '
                      'actor_state.size_rank <= _space_other_rank and _form_share_creature_space != true', [
                    _set('_space_turn_prone', True),
                ]),
            ]),
        ]),
    ])


def bind_creature_space(runtime, record):
    """Bind ordinary routes and their existing cost union; teleports and forced moves retain their owners."""
    tools = runtime['tools']
    if COLLECT in tools:
        raise ValueError('Creature-space movement must be composed once')
    tools.update({WIDTH: _width(), PAIR: _pair(), COLLECT: _collect()})
    tools[ENDPOINT] = _tool('Qualify the requested endpoint before opportunity or environment splitting.',
        [_entity('actor_state'), _parameter('destination', 'array', items={'type': 'number'}, min_items=3, max_items=3)], [
            _call(COLLECT, {'actor_state': '{_actor_state_id}', 'destination': '{destination}',
                            'endpoint': True, 'passage': False}),
        ])
    tools['move_srd_scene_creature']['effects'].insert(2, _call(ENDPOINT, {
        'actor_state': '{_actor_state_id}', 'destination': '{destination}'}))
    cost = tools['srd_information_movement_cost']['effects']
    insertion = next(i for i, row in enumerate(cost) if 'sort' in row and row['sort']['collection'] == '_route_cuts')
    cost[insertion:insertion] = [
        _set('_space_cost_intervals', []),
        _when('len(actor_subject) > 0', [
            _call(COLLECT, {'actor_state': '{actor_subject}', 'destination': '{end}'}, ('_space_intervals',)),
            _set('_space_cost_intervals', '_space_intervals'),
            _each('_space_cost_intervals', '_space_cost_interval', [
                _push('_route_cuts', {'t': '_space_cost_interval.entry'}),
                _push('_route_cuts', {'t': '_space_cost_interval.exit'}),
            ]),
        ]),
    ]
    loop = next(row['for_each'] for row in cost if row.get('for_each', {}).get('collection') == '_route_cuts')
    piece = loop['do'][0]['on_true']
    insertion = next(i for i, row in enumerate(piece) if row.get('set', {}).get('field') == '_route_piece_cost')
    piece.insert(insertion, _each('_space_cost_intervals', '_space_cost_interval', [
        _set('_route_piece_difficult', True) | {'condition':
            '_route_mid > _space_cost_interval.entry and _route_mid < _space_cost_interval.exit'},
    ]))
    # Fractional interval arithmetic can leave a near-equal final budget a few ulps short.
    budget = tools['qualify_srd_coordinate_departure_budget']['effects']
    insertion = next(i for i, row in enumerate(budget)
                     if row.get('require', {}).get('condition') == '_movement_cost <= combat.movement_remaining')
    budget[insertion]['require']['condition'] = '_movement_cost <= combat.movement_remaining + 0.000000001'
    budget.insert(insertion + 1, _set('_movement_cost', 'max(0, min(_movement_cost, combat.movement_remaining))'))
    # The zero-prefix opportunity branch previously omitted the actual actor from this cost call.
    def visit(value):
        if isinstance(value, dict):
            if value.get('call') == 'srd_information_movement_cost':
                value['params'].update(actor_subject='{_actor_state_id}', movement_mode='{mode}')
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(tools['move_srd_scene_creature']['effects'])
    tools[TURN] = _tool('Apply the SRD turn-end Prone consequence to actual unresolved overlap.',
        [_entity('actor_state')], [
        _call('srd_information_scene_entity', {'subject': '{_actor_state_id}'}, ('_info_position',)),
        _call(COLLECT, {'actor_state': '{_actor_state_id}', 'destination': '{_info_position}', 'passage': False},
              ('_space_turn_prone',)),
        _set('_space_end_conditions', 'actor_state.conditions'),
        _set('_space_prone_applied', False),
        _when("_space_turn_prone == true and 'prone' not in actor_state.conditions and "
              "(actor_state.condition_immunities == null or 'prone' not in actor_state.condition_immunities)", [
            _push('_space_end_conditions', "'prone'"),
            _set('actor_state.conditions', '_space_end_conditions'),
            _set('_space_prone_applied', True),
        ]),
    ])
    end = tools['end_srd_otherworldly_native_turn_body']['effects']
    # Run after turn-end relocations, before the receipt captures the final conditions.
    insertion = next(i for i, row in enumerate(end) if row.get('set', {}).get('field') == '_end_turn_display_conditions')
    end.insert(insertion, _set('_space_end_conditions', None))
    end.insert(insertion + 1, _when('state.spatial_scene != null', [
        _call(TURN, {'actor_state': '{_actor_id}'}, ('_space_end_conditions', '_space_prone_applied')),
        _when('_space_prone_applied == true', [
            _push('_end_turn_rows', {'label': "'Occupied space'", 'value': "'Prone'"}),
            _set('_end_turn_card_title', "'End of turn'"),
        ]),
    ]))
    last_display = max(i for i, row in enumerate(end)
                       if row.get('set', {}).get('field') == '_end_turn_display_conditions')
    end.insert(last_display + 1, _set('_end_turn_display_conditions', '_space_end_conditions') |
               {'condition': '_space_end_conditions != null'})
    record['compatibility']['requires'].append('spatial_circle_overlap.v1')
    record['compatibility']['requires'].append('player_action_preflight.v1')
    tools['move_srd_scene_creature']['metadata']['player_controls'] = {
        'confirmation': 'required',
        'preflight': [ENDPOINT, 'qualify_srd_celestial_scene_route'],
        'preview': {'version': 1, 'footprint': 'compact', 'title': 'Movement', 'summary': [
            {'label': 'Movement spent', 'value': {'path': 'context._route_cost'}},
        ]},
    }
    surface = next(row for row in record['ui_surfaces'] if row['id'] == 'srd-coordinate-map')
    surface['source']['fields']['occupied_space'] = {'kind': 'object', 'fields': {
        'declaration': {'kind': 'literal', 'value': {
            'convention': CONVENTION, 'widths': WIDTHS, 'category_field': 'size_rank'}},
        'actors': {'kind': 'state', 'namespace': record['id'], 'key': 'entities'},
    }}
    return [WIDTH, PAIR, COLLECT, ENDPOINT, TURN, 'move_srd_scene_creature',
            'srd_information_movement_cost', 'end_srd_otherworldly_native_turn_body',
            'qualify_srd_coordinate_departure_budget'] + bind_creature_space_routes(runtime, record)


def _bind_route_eligibility(tools):
    """Prepare native eligibility and movement mode without vetoing an unpriced candidate."""
    budget = 'qualify_srd_coordinate_departure_budget'
    prepare_budget = 'prepare_srd_creature_space_departure'
    prepare_route = 'prepare_srd_creature_space_route'
    checked = tools[budget]
    split = next(i for i, row in enumerate(checked['effects']) if row.get('require', {}).get('condition') ==
                 '_movement_cost <= combat.movement_remaining + 0.000000001')
    prepared = deepcopy(checked)
    prepared['description'] = 'Prepare native movement eligibility and mode before candidate affordability.'
    prepared['effects'] = deepcopy(checked['effects'][:split])
    prepared.pop('narrative', None)
    tools[prepare_budget] = prepared
    params = {row['name']: '{' + ('_' + row['name'] + '_id' if row.get('resolve') == 'entity'
                                 else row['name']) + '}' for row in checked['parameters']}
    checked['effects'] = [_call(prepare_budget, params) | {'export_context': '*'},
                          *checked['effects'][split:]]
    eligibility = deepcopy(tools['qualify_srd_celestial_scene_route'])
    eligibility['description'] = 'Prepare a physical candidate without requiring its initial leg to be affordable.'
    replaced = []

    def replace_budget(value):
        if isinstance(value, dict):
            if value.get('call') in {budget, 'qualify_srd_celestial_movement_budget'}:
                replaced.append(value['call'])
                value['call'] = prepare_budget
            for child in value.values():
                replace_budget(child)
        elif isinstance(value, list):
            for child in value:
                replace_budget(child)

    replace_budget(eligibility['effects'])
    assert sorted(replaced) == sorted([budget, 'qualify_srd_celestial_movement_budget'])
    tools[prepare_route] = eligibility
    return [budget, prepare_budget, prepare_route]


def bind_creature_space_routes(runtime, record):
    """Plan forbidden-space detours, then reuse ordinary movement for every admitted leg."""
    tools = runtime['tools']
    plan = 'plan_srd_creature_space_route'
    qualify = 'qualify_srd_creature_space_route'
    segment = 'move_srd_creature_space_segment'
    root = tools['move_srd_scene_creature']
    if segment in tools:
        raise ValueError('Creature-space routes must be composed once')
    eligibility_tools = _bind_route_eligibility(tools)
    tools[PAIR], tools[COLLECT] = _pair(), _collect()
    tools[ENDPOINT]['effects'][0].update(export_context={name: name for name in (
        '_space_route_circles', '_space_route_radius', '_space_route_blocked', '_space_mover')})
    tools[ENDPOINT]['effects'][0]['params']['passage'] = False

    def origin_cost(value):
        if isinstance(value, dict):
            if value.get('call') == COLLECT:
                value['params']['start'] = '{start}'
            for child in value.values():
                origin_cost(child)
        elif isinstance(value, list):
            for child in value:
                origin_cost(child)
    origin_cost(tools['srd_information_movement_cost']['effects'])
    tools[plan] = _tool('Select a bounded physical route around forbidden creature spaces.',
        [_entity('actor_state'), _parameter('destination', 'array', items={'type': 'number'}, min_items=3, max_items=3)], [
        _call(ENDPOINT, {'actor_state': '{_actor_state_id}', 'destination': '{destination}'},
              ('_space_route_circles', '_space_route_radius', '_space_route_blocked', '_space_mover')),
        _set('_detour_mover', '_space_mover'),
        _set('_detour_plane', '_detour_mover.metadata.plane'),
        _set('_detour_plane', "'material'") | {'condition': '_detour_plane == null'},
        _set('_detour_missing_plane', "_detour_plane == 'material'"),
        _set('_detour_route', {'points': ['_detour_mover.position', 'destination'],
                               'found': True, 'truncated': False}),
        {'spatial_query': {'scene': 'state.spatial_scene', 'operation': 'circle_route', 'query': {
            'map_id': '{state.spatial_scene.map.id}', 'moving_entity_id': '{_detour_mover.id}',
            'destination': '{destination}', 'moving_radius': '{_space_route_radius}', 'circles': '{_space_route_circles}',
            'metadata_filter': {'key': 'plane', 'value': '{_detour_plane}',
                                'missing_matches': '{_detour_missing_plane}'},
            'max_tests': 16384}}, 'store_as': '_detour_route', 'condition': '_space_route_blocked == true'},
        _require('_detour_route.truncated != true', 'Creature-space route search exceeded its bounded search.'),
        _require('_detour_route.found == true', 'No legal creature-space route was found for this destination.'),
    ])
    route_params = {'actor_state': '{_actor_state_id}', 'destination': '{_detour_first}',
        'mode': '{mode}', 'difficult_terrain': '{difficult_terrain}', 'lasting_state': '{_lasting_state_id}',
        'levitate_source_id': '{levitate_source_id}', 'witness_state_ids': '{witness_state_ids}'}
    # Reprice only candidate paths through the real SRD cost owner. The neutral
    # graph receives directed numeric costs, with Euclidean bounds for unknown edges.
    pricing = [
        _set('_detour_total_cost', 0), _set('_detour_total_distance', 0),
        _set('_detour_previous', '_detour_route.points[0]'),
        _set('_detour_pricing_points', '_detour_route.points'),
        _each('_detour_pricing_points', '_detour_point', [
            _when(' or '.join(f'_detour_point[{axis}] != _detour_previous[{axis}]' for axis in range(3)), [
                _call('srd_information_movement_cost', {'start': '{_detour_previous}', 'end': '{_detour_point}',
                    'actor_subject': '{_actor_state_id}', 'movement_mode': '{mode}',
                    'base_difficult': '{difficult_terrain}', 'plane': '{_detour_plane}',
                    'ignore_ground_environments': '{_move_ignore_ground_environments}'}, ('_route_cost', '_route_distance')),
                _set('_route_cost', '_route_distance') | {'condition': 'actor_state.spell_freedom_of_movement == true'},
                _set('_route_cost', '_route_cost + _route_distance') | {'condition': "'prone' in actor_state.conditions"},
                _set('_route_cost', '_route_cost + _route_distance') | {'condition': '_levitate_grip_walk_budget == true'},
                _set('_route_cost', '_route_cost + _route_distance') | {'condition':
                    "mode == 'swim' and (_ordinary_water_swim == true or _tsunami_ordinary_swim == true) and "
                    '(actor_state.swim_speed == null or actor_state.swim_speed <= 0) and actor_state.spell_freedom_of_movement != true'},
                _push('_detour_edge_costs', {'start': '_detour_previous', 'end': '_detour_point', 'cost': '_route_cost'}),
                _set('_detour_total_cost', '_detour_total_cost + _route_cost'),
                _set('_detour_total_distance', '_detour_total_distance + _route_distance'),
                _set('_detour_previous', '_detour_point'),
            ]),
        ]),
    ]
    retry = deepcopy(next(row for row in tools[plan]['effects'] if 'spatial_query' in row))
    retry.pop('condition')
    retry['spatial_query']['query'].update(edge_costs='{_detour_edge_costs}', max_tests='{_detour_tests_left}')
    tools[qualify] = _tool('Select an affordable route using actual movement prices without spending or revealing hidden facts.',
        [deepcopy(row) for row in root['parameters'] if row['name'] not in
         {'time_stop_state', 'celestial_entry_choices'}], [
        _call(plan, {'actor_state': '{_actor_state_id}', 'destination': '{destination}'}) | {'export_context': '*'},
        _set('_detour_first', '_detour_route.points[1]'),
        _call('prepare_srd_creature_space_route', route_params) |
            {'export_context': '*', 'condition': 'len(_detour_route.points) > 2'},
        _call('qualify_srd_celestial_scene_route', route_params) |
            {'export_context': '*', 'condition': 'len(_detour_route.points) <= 2'},
        _when('len(_detour_route.points) > 2', [
        _set('_detour_tests_left', '16384 - _detour_route.tests'),
        _set('_detour_edge_costs', []), _set('_detour_affordable', False),
        _set('_detour_attempts', list(range(8))),
        _each('_detour_attempts', '_detour_attempt', [
            _when('_detour_affordable != true', pricing + [
                _set('_detour_affordable', '_detour_total_cost <= combat.movement_remaining + 0.000000001'),
                _when('_detour_affordable != true and _detour_attempt < 7', [
                    _require('_detour_tests_left > 0', 'Creature-space route search exceeded its bounded search.'),
                    retry,
                    _set('_detour_tests_left', '_detour_tests_left - _detour_route.tests'),
                    _require('_detour_route.truncated != true', 'Creature-space route search exceeded its bounded search.'),
                    _require('_detour_route.found == true', 'No legal creature-space route was found for this destination.'),
                ]),
            ]),
        ]),
        _require('_detour_affordable == true', 'Not enough movement for a route within the bounded priced search.'),
        _call('qualify_srd_coordinate_departure_budget', {'actor': '{_actor_state_id}',
            'actor_state': '{_actor_state_id}', 'distance': '{_detour_total_cost}', 'difficult_terrain': False,
            'movement_kind': '{_ordinary_swim_budget_mode}', 'known_spell_effect': '{known_spell_effect}'}, ('_movement_cost',)),
        _set('_route_cost', '_movement_cost'), _set('_route_distance', '_detour_total_distance'),
        ]),
    ])
    tools[segment] = deepcopy(root)
    tools[segment]['metadata'] = {'category': 'internal', 'visible_to_player': False, 'costs_action': False}
    params = {row['name']: '{' + ('_' + row['name'] + '_id' if row.get('resolve') == 'entity'
                                 else row['name']) + '}' for row in root['parameters']}
    segment_params = {**params, 'destination': '{_detour_waypoint}'}
    qualify_params = {key: value for key, value in params.items()
                      if key not in {'time_stop_state', 'celestial_entry_choices'}}
    root['effects'] = [
        _call(qualify, qualify_params) | {'export_context': '*'},
        _set('_detour_walk_points', '_detour_route.points'),
        _set('_detour_final_destination', 'destination'),
        _set('_detour_walk_origin', '_detour_route.points[0]'),
        _set('_detour_spent', 0), _set('_detour_distance', 0), _set('_detour_continue', True),
        _each('_detour_walk_points', '_detour_waypoint', [
            _when('_detour_continue == true and (' + ' or '.join(
                f'_detour_waypoint[{axis}] != _detour_walk_origin[{axis}]' for axis in range(3)) + ')', [
                _call(segment, segment_params) | {'export_context': '*'},
                _set('_detour_spent', '_detour_spent + _scene_movement_cost'),
                _set('_detour_distance', '_detour_distance + _scene_movement_distance'),
                _set('_detour_continue', 'lasting_state.pending_srd_movement == null and ' + ' and '.join(
                    f'abs(_scene_actor_position[{axis}] - _detour_waypoint[{axis}]) <= 0.000000001' for axis in range(3))),
                _when('lasting_state.pending_srd_movement != null', [
                    _set('lasting_state.pending_srd_movement.destination', '_detour_final_destination'),
                    _set('lasting_state.pending_srd_movement.parameters.destination', '_detour_final_destination'),
                ]),
            ]),
        ]),
        _set('_scene_movement_cost', '_detour_spent'), _set('_scene_movement_distance', '_detour_distance'),
    ]
    root['metadata']['player_controls']['preflight'] = [qualify]
    root['metadata']['player_controls']['preflight_path'] = {
        'scene': 'state.spatial_scene', 'route': '_detour_route', 'cost': '_route_cost'}
    root['description'] = 'Combat only. Follow a legal creature-space route and consume its actual movement cost.'
    record['compatibility']['requires'].append('spatial_circle_route.v1')
    record['compatibility']['requires'].append('player_action_preflight_path.v1')
    return [PAIR, COLLECT, ENDPOINT, 'srd_information_movement_cost', plan, qualify, segment,
            'move_srd_scene_creature', *eligibility_tools]
