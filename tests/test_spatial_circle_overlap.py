"""Declared occupied circles share continuous path/endpoint geometry, separate from bodies."""
from copy import deepcopy
import math

import pytest

from sylvia_gm.spatial import SpatialEngine, SpatialValidationError
from tests.test_spatial_engine import _entity, _scene


def query(start, end, *, fixed=(40, 40, 0), moving_radius=2.5, fixed_radius=2.5, **patch):
    scene = _scene(width=100, depth=100, entities=[
        _entity('moving', start, height=6), _entity('fixed', list(fixed), height=6)])
    scene['map']['bounds']['max'][2] = 100
    scene['map']['layers'][0]['max_elevation'] = 100
    engine = SpatialEngine(scene)
    before = deepcopy(engine.snapshot.as_payload())
    result = engine.query({'kind': 'circle_overlap', 'map_id': 'spatial-fixture',
        'moving_entity_id': 'moving', 'fixed_entity_id': 'fixed', 'destination': end,
        'moving_radius': moving_radius, 'fixed_radius': fixed_radius, **patch}).as_payload()
    assert engine.snapshot.as_payload() == before
    return result


def test_crossing_uses_declared_diameter_and_not_physical_box():
    result = query([20, 40, 0], [60, 40, 0])
    assert result['hit']
    assert result['entry'] == pytest.approx(0.375)
    assert result['exit'] == pytest.approx(0.625)
    assert not result['starts_inside'] and not result['ends_inside']


def test_tangent_and_boundary_contact_are_not_occupied():
    assert not query([20, 45, 0], [60, 45, 0])['hit']
    assert not query([35, 40, 0], [30, 40, 0])['hit']
    assert not query([30, 40, 0], [35, 40, 0])['ends_inside']
    assert query([30, 40, 0], [35.001, 40, 0])['ends_inside']


def test_source_overlap_can_be_left_and_is_distinct_from_endpoint():
    result = query([40, 40, 0], [60, 40, 0])
    assert result['starts_inside'] and not result['ends_inside']
    assert result['entry'] == 0 and result['exit'] == pytest.approx(0.25)


def test_very_short_translation_inside_space_is_still_overlap():
    result = query([40, 40, 0], [40.000000001, 40, 0])
    assert result['hit'] and result['starts_inside'] and result['ends_inside']
    assert result['entry'] == 0 and result['exit'] == 1


def test_vertical_contact_is_clear_but_descent_crosses_occupied_height():
    assert not query([20, 40, 6], [60, 40, 6])['hit']
    result = query([40, 40, 12], [40, 40, 0])
    assert result['entry'] == pytest.approx(0.5)
    assert result['ends_inside']


def test_fractional_coordinates_and_reversal():
    result = query([20.125, 41.25, 0], [60.125, 41.25, 0])
    reverse = query([60.125, 41.25, 0], [20.125, 41.25, 0])
    assert result['entry'] == pytest.approx(1 - reverse['exit'])
    assert result['exit'] == pytest.approx(1 - reverse['entry'])


@pytest.mark.parametrize('patch', [{'moving_radius': 0}, {'fixed_radius': True},
    {'moving_radius': math.inf}, {'fixed_entity_id': 'missing'}, {'destination': [101, 0, 0]}])
def test_invalid_query_is_rejected(patch):
    with pytest.raises(SpatialValidationError):
        query([20, 40, 0], [60, 40, 0], **patch)
