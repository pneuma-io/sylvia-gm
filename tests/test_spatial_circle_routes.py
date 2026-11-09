"""Bounded continuous detours reuse exact occupied-space and footprint clearance."""
from copy import deepcopy
from math import cos, dist, pi, sin

import pytest

from sylvia_gm.spatial import SpatialEngine, SpatialValidationError
from tests.test_srd_creature_space import NS, state


def route_scene():
    return state(position=(7.5, 0, 0))[NS + '.state']['spatial_scene']


def route(engine, **patch):
    return engine.query({'kind': 'circle_route', 'map_id': 'space', 'moving_entity_id': 'hero',
        'destination': [15, 0, 0], 'moving_radius': 2.5,
        'circles': [{'entity_id': 'guard', 'radius': 2.5, 'blocking': True}],
        'metadata_filter': {'key': 'plane', 'value': 'material', 'missing_matches': True},
        'max_tests': 16384, **patch}).as_payload()


def test_detour_is_deterministic_clear_and_shorter_than_explicit_affordable_route():
    scene = route_scene()
    engine = SpatialEngine(scene)
    before = engine.snapshot.as_payload()
    result = route(engine)
    assert result == route(engine)
    assert result['found'] and not result['truncated']
    assert result['points'][0] == [0, 0, 0] and result['points'][-1] == [15, 0, 0]
    assert 15 < result['distance'] < 21.181
    assert result['distance'] == pytest.approx(sum(dist(a, b) for a, b in zip(result['points'], result['points'][1:])))
    for start, end in zip(result['points'], result['points'][1:]):
        assert not engine.query({'kind': 'circle_overlap', 'map_id': 'space',
            'moving_entity_id': 'hero', 'fixed_entity_id': 'guard', 'origin': start,
            'destination': end, 'moving_radius': 2.5, 'fixed_radius': 2.5}).as_payload()['hit']
    assert engine.snapshot.as_payload() == before


def test_allowed_passage_is_direct_but_occupied_endpoint_is_refused():
    engine = SpatialEngine(route_scene())
    circles = [{'entity_id': 'guard', 'radius': 2.5, 'blocking': False}]
    assert route(engine, circles=circles)['distance'] == 15
    assert not route(engine, circles=circles, destination=[7.5, 0, 0])['found']


def test_detour_keeps_physical_walls_and_search_bound():
    scene = route_scene()
    scene['geometry'] = [{'id': 'wall', 'map_id': 'space', 'active': True,
        'movement': 'blocking', 'visibility': 'opaque',
        'volume': {'kind': 'box', 'min': [2, -100, -1], 'max': [13, 0, 7]},
        'destroyed': {'active': False, 'movement': 'passable', 'visibility': 'clear'}, 'metadata': {}}]
    result = route(SpatialEngine(scene))
    assert result['found'] and all(point[1] >= 0 for point in result['points'])
    truncated = route(SpatialEngine(route_scene()), max_tests=1)
    assert truncated['truncated'] and not truncated['found'] and not truncated['points']


@pytest.mark.parametrize('patch', [
    {'moving_radius': 0}, {'max_tests': 16385},
    {'circles': [{'entity_id': 'missing', 'radius': 2.5, 'blocking': True}]},
    {'circles': [{'entity_id': 'guard', 'radius': 2.5, 'blocking': 'yes'}]},
])
def test_invalid_route_input_fails_closed(patch):
    with pytest.raises(SpatialValidationError):
        route(SpatialEngine(deepcopy(route_scene())), **patch)


def test_caller_priced_directed_edges_select_an_alternative_without_changing_distance():
    engine = SpatialEngine(route_scene())
    initial = route(engine)
    costs = [{'start': a, 'end': b, 'cost': 2 * dist(a, b)}
             for a, b in zip(initial['points'], initial['points'][1:])]
    alternative = route(engine, edge_costs=costs)
    assert alternative == route(engine, edge_costs=costs)
    assert alternative['found'] and alternative['points'] != initial['points']
    assert alternative['distance'] == pytest.approx(initial['distance'])
    assert all(point[1] >= 0 for point in alternative['points'])
    reverse_costs = [dict(row, start=row['end'], end=row['start']) for row in costs]
    assert route(engine, edge_costs=reverse_costs)['points'] == initial['points']
    assert alternative['distance'] == pytest.approx(sum(
        dist(a, b) for a, b in zip(alternative['points'], alternative['points'][1:])))


@pytest.mark.parametrize('costs', [
    [{'start': [0, 0, 0], 'end': [15, 0, 0], 'cost': 14}],
    [{'start': [0, 0, 0], 'end': [15, 0, 0], 'cost': True}],
    [{'start': [0, 0, 0], 'end': [15, 0, 0], 'cost': float('inf')}],
    [{'start': [0, 0], 'end': [15, 0, 0], 'cost': 15}],
    [{'start': [0, 0, 0], 'end': [15, 0, 0], 'cost': 15, 'terrain': True}],
    [{'start': [0, 0, 0], 'end': [15, 0, 0], 'cost': 15}] * 16385,
])
def test_route_edge_cost_input_remains_closed_and_bounded(costs):
    with pytest.raises(SpatialValidationError):
        route(SpatialEngine(route_scene()), edge_costs=costs)


def assert_route_clear(scene, query, result):
    engine = SpatialEngine(scene)
    assert result['found'] and not result['truncated'], result
    for start, end in zip(result['points'], result['points'][1:]):
        leg = deepcopy(scene)
        next(row for row in leg['entities'] if row['id'] == 'hero')['position'] = start
        assert SpatialEngine(leg).query({'kind': 'sweep', 'map_id': scene['map']['id'],
            'moving_entity_id': 'hero', 'destination': end, 'include_entities': False,
            'exclude_entity_ids': [], 'metadata_filter': query['metadata_filter']}).as_payload()['clear']
        for circle in query['circles']:
            overlap = engine.query({'kind': 'circle_overlap', 'map_id': scene['map']['id'],
                'moving_entity_id': 'hero', 'fixed_entity_id': circle['entity_id'], 'origin': start,
                'destination': end, 'moving_radius': query['moving_radius'],
                'fixed_radius': circle['radius']}).as_payload()
            assert not overlap['ends_inside']
            assert not overlap['hit'] or not circle['blocking']


@pytest.mark.parametrize('angle', [0, 7 * pi / 16, 1.123])
@pytest.mark.parametrize('gap', [.01, .00001])
def test_disjoint_circle_passage_survives_polygon_sampling(angle, gap):
    normal, tangent = (cos(angle), sin(angle)), (sin(angle), -cos(angle))
    def point(t, n):
        return [t * tangent[i] + n * normal[i] for i in range(2)] + [0]
    scene = route_scene()
    scene['entities'][0]['position'] = point(-10, .25)
    scene['entities'][1]['position'] = point(0, 5 + gap / 2)
    other = deepcopy(scene['entities'][1])
    other.update(id='other', position=point(0, -5 - gap / 2))
    scene['entities'].append(other)
    query = {'kind': 'circle_route', 'map_id': 'space', 'moving_entity_id': 'hero',
        'destination': point(10, .25), 'moving_radius': 2.5,
        'circles': [{'entity_id': key, 'radius': 2.5, 'blocking': True} for key in ('guard', 'other')],
        'metadata_filter': {'key': 'plane', 'value': 'material', 'missing_matches': True}, 'max_tests': 16384}
    result = SpatialEngine(scene).query(query).as_payload()
    assert_route_clear(scene, query, result)
    assert result['distance'] < 20.021


def test_sixteen_circle_route_finishes_inside_the_existing_edge_cap():
    scene = route_scene()
    template = deepcopy(scene['entities'][1])
    scene['entities'] = [scene['entities'][0]]
    for index in range(16):
        body = deepcopy(template)
        body.update(id=f'guard{index:02}', position=[7.5 + 6 * index, 0, 0])
        scene['entities'].append(body)
    scene['map']['bounds'] = {'min': [-200, -100, -100], 'max': [200, 100, 100]}
    scene['map']['grid']['origin'] = [-200, -100]
    query = {'kind': 'circle_route', 'map_id': 'space', 'moving_entity_id': 'hero',
        'destination': [105, 0, 0], 'moving_radius': 2.5,
        'circles': [{'entity_id': body['id'], 'radius': 2.5, 'blocking': True}
                    for body in scene['entities'][1:]],
        'metadata_filter': {'key': 'plane', 'value': 'material', 'missing_matches': True}, 'max_tests': 16384}
    result = SpatialEngine(scene).query(query).as_payload()
    assert_route_clear(scene, query, result)
    assert result['distance'] < 115.02
    assert result['tests'] <= 16384
