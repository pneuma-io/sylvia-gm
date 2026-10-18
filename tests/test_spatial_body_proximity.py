"""Continuous admitted body distance, with independent geometric expectations."""
import copy
import math

import pytest

from sylvia_gm.spatial import SpatialEngine, SpatialValidationError
from sylvia_gm.runtime._schema_validator import _validate_spatial_query_effect
from tests.test_spatial_engine import _entity, _scene


def _engine(start, *, mover_circle=False, fixed_circle=False):
    body = {'kind': 'circle', 'radius': 2} if mover_circle else {'kind': 'box', 'half_extents': [2, 2]}
    fixed = {'kind': 'circle', 'radius': 5} if fixed_circle else {'kind': 'box', 'half_extents': [5, 5]}
    scene = _scene(width=100, depth=100, entities=[
        _entity('moving', start, footprint=body, height=3),
        _entity('fixed', [40, 40, 0], footprint=fixed, height=4)])
    scene['map']['bounds']['max'][2] = 100
    scene['map']['layers'][0]['max_elevation'] = 100
    return SpatialEngine(scene)


def _query(engine, end, radius=10, **changes):
    return engine.query({'kind': 'body_proximity', 'map_id': 'spatial-fixture',
        'moving_entity_id': 'moving', 'fixed_entity_id': 'fixed',
        'destination': end, 'radius': radius, **changes}).as_payload()


@pytest.mark.parametrize('moving_circle,fixed_circle', [(False, False), (True, False), (False, True), (True, True)])
def test_crossing_interval_all_admitted_body_pairs(moving_circle, fixed_circle):
    engine = _engine([10, 40, 0], mover_circle=moving_circle, fixed_circle=fixed_circle)
    result = _query(engine, [70, 40, 0])
    assert result['hit'] is True
    assert result['entry'] == pytest.approx(13 / 60, abs=1e-10)
    assert result['exit'] == pytest.approx(47 / 60, abs=1e-10)
    assert result['minimum_distance'] == 0


def test_circle_corner_does_not_use_bounding_square():
    engine = _engine([55, 55, 0], mover_circle=True)
    result = _query(engine, [55, 55, 0])
    assert result['hit'] is False
    assert result['minimum_distance'] == pytest.approx(math.sqrt(200) - 2, abs=1e-10)
    assert result['entry'] is None and result['exit'] is None


def test_vertical_gap_and_rounded_corner_combine_in_3d():
    engine = _engine([55, 55, 12], mover_circle=True)
    result = _query(engine, [55, 55, 12], radius=15)
    assert result['minimum_distance'] == pytest.approx(math.hypot(math.sqrt(200) - 2, 8), abs=1e-10)
    assert result['hit'] is True and result['entry'] == 0 and result['exit'] == 1


def test_vertical_entry_uses_actual_body_height():
    engine = _engine([40, 40, 30], mover_circle=True)
    result = _query(engine, [40, 40, 0])
    assert result['entry'] == pytest.approx(16 / 30, abs=1e-10)
    assert result['exit'] == 1


def test_endpoint_contact_and_outside_start_remain_distinct():
    assert _query(_engine([57, 40, 0], mover_circle=True), [60, 40, 0])['entry'] == 0
    result = _query(_engine([60, 40, 0], mover_circle=True), [57, 40, 0])
    assert result['hit'] is True and result['entry'] == 1 and result['exit'] == 1


def test_tangent_contact_on_circle_is_not_sampled_away():
    result = _query(_engine([10, 57, 0], mover_circle=True, fixed_circle=True), [70, 57, 0])
    assert result['hit'] is True
    assert result['entry'] == pytest.approx(0.5, abs=1e-7)
    assert result['exit'] == pytest.approx(0.5, abs=1e-7)
    assert result['minimum_distance'] == 10


def test_narrow_interval_and_reverse_segment():
    engine = _engine([10, 56.999999, 0], mover_circle=True, fixed_circle=True)
    result = _query(engine, [70, 56.999999, 0])
    half = math.sqrt(17 ** 2 - 16.999999 ** 2) / 60
    assert result['entry'] == pytest.approx(0.5 - half, abs=1e-9)
    assert result['exit'] == pytest.approx(0.5 + half, abs=1e-9)
    reverse = _query(_engine([70, 56.999999, 0], mover_circle=True, fixed_circle=True), [10, 56.999999, 0])
    assert reverse['entry'] == pytest.approx(1 - result['exit'], abs=1e-10)


def test_query_reuses_snapshot_and_does_not_mutate():
    engine = _engine([10, 40, 0], mover_circle=True)
    before = copy.deepcopy(engine.snapshot.as_payload())
    result = _query(engine, [70, 40, 0])
    assert _query(engine, [70, 40, 0]) == result
    assert engine.snapshot.as_payload() == before
    assert result['snapshot_id'] == engine.snapshot.snapshot_id


@pytest.mark.parametrize('changes', [
    {'radius': -1}, {'radius': True}, {'radius': math.inf},
    {'fixed_entity_id': 'missing'}, {'moving_entity_id': 'missing'},
    {'destination': [101, 40, 0]}, {'origin': [10, 40, 0]},
])
def test_invalid_physical_query_fails_clearly(changes):
    with pytest.raises(SpatialValidationError):
        _query(_engine([10, 40, 0]), [70, 40, 0], **changes)


def test_runtime_admits_closed_query_with_actual_context_ids():
    effect = {'spatial_query': {'scene': 'state.spatial_scene', 'operation': 'body_proximity',
        'query': {'map_id': '{state.spatial_scene.map.id}', 'moving_entity_id': '{_moving.id}',
            'fixed_entity_id': '{_fixed.id}', 'destination': '{end}', 'radius': 10}},
        'store_as': '_proximity'}
    assert not _validate_spatial_query_effect('move', effect, effect['spatial_query'], 'effects[0]')
    effect['spatial_query']['query']['radius'] = -1
    assert _validate_spatial_query_effect('move', effect, effect['spatial_query'], 'effects[0]')
