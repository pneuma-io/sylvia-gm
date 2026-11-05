"""The map draws the route selected by the actual Lite visible-scene qualifier."""
from copy import deepcopy
from math import dist

import pytest

from sylvia_gm import dice
from sylvia_gm.application.player_actions import parse_player_controls
from sylvia_gm.application.spatial_preview import project_preflight_path
from sylvia_gm.domain.authority import ActionStatus, thaw_json_value
from sylvia_gm.domain.errors import CommandRejected, StaleRevision
from sylvia_gm.spatial import SpatialEngine
from tests.lite_imported_fixture import ImportedCase
from tests.test_srd_creature_space_owner import MAP, NS, make_case, request
from tests.test_srd_creature_space_terrain_routes import preview_cost, terrain_case


def assert_public_route(value, preview, destination):
    drawing = preview['map_preview']
    assert set(drawing) == {'notice', 'map_id', 'map_revision', 'path'}
    path = drawing['path']
    assert set(path) == {'found', 'truncated', 'cost', 'points'}
    assert path['found'] is True and path['truncated'] is False
    points = path['points']
    assert len(points) > 2 and points[0] == [0, 0, 0] and points[-1] == destination
    assert path['cost'] == preview_cost(preview)
    assert path['cost'] == pytest.approx(sum(dist(a, b) for a, b in zip(points, points[1:])), abs=1e-12)
    scene = thaw_json_value(value.snapshot().state[NS + '.state']['spatial_scene'])
    for start, end in zip(points, points[1:]):
        leg = deepcopy(scene)
        next(body for body in leg['entities'] if body['id'] == 'hero')['position'] = start
        engine = SpatialEngine(leg)
        assert engine.query({'kind': 'sweep', 'map_id': 'lite', 'moving_entity_id': 'hero',
            'destination': end, 'include_entities': False}).as_payload()['clear']
        assert not engine.query({'kind': 'circle_overlap', 'map_id': 'lite',
            'moving_entity_id': 'hero', 'fixed_entity_id': 'guard', 'destination': end,
            'moving_radius': 2.5, 'fixed_radius': 2.5}).as_payload()['hit']
    return points


@pytest.mark.parametrize('elevation', [0, 1])
def test_public_preview_draws_selected_route_and_preserves_confirmation_lifecycle(tmp_path, elevation):
    value = make_case(tmp_path / 'route.sqlite3')
    try:
        destination = [15, 0, elevation]
        proposed = request(value, destination)
        before, rng = value.snapshot(), dice._rng.getstate()
        preview = value.owner.preview_panel_action(**proposed)
        points = assert_public_route(value, preview, destination)
        if elevation == 0:
            assert preview_cost(preview) == pytest.approx(18.57769472003053, abs=1e-12)
        available = value.owner.player_action_availability(campaign_id='lite', expected_revision=before.revision,
            requests=[{'source': {'kind': 'panel', 'surface_id': MAP, 'action_id': 'move-walk'},
                       'selection': proposed['selection']}])['results'][0]
        assert available['available'] and available['map_preview'] == preview['map_preview']
        # Cancelling is simply discarding the preview; it owns no mutation or reservation.
        assert value.snapshot() == before and dice._rng.getstate() == rng
        assert value.owner.preview_panel_action(**proposed) == preview
        for change in ({'confirmation': None}, {'confirmation': 'forged'},
                       {'operation_id': 'forged', 'confirmation': preview['confirmation']},
                       {'selection': {**proposed['selection'], 'aim_point': [15, 0, elevation + 1]},
                        'confirmation': preview['confirmation']}):
            with pytest.raises(CommandRejected):
                value.owner.execute_panel_action(**{**proposed, **change})
        assert value.snapshot() == before and dice._rng.getstate() == rng
        receipt = value.owner.execute_panel_action(**proposed, confirmation=preview['confirmation'])
        after = value.snapshot()
        assert receipt.status is ActionStatus.COMMITTED
        assert after.state[NS + '.combat']['movement_spent'] == pytest.approx(preview_cost(preview), abs=1e-12)
        assert next(body for body in after.state[NS + '.state']['spatial_scene']['entities']
                    if body['id'] == 'hero')['position'] == points[-1]
        assert len([a for a in after.actions if a.action_id == receipt.action_id]) == 1
        assert value.owner.execute_panel_action(**proposed) == receipt and value.snapshot() == after
        value.reopen()
        assert value.owner.execute_panel_action(**proposed) == receipt
        assert value.snapshot().state == after.state
        stale = request(value, [16, 0, elevation], 'stale')
        old = value.owner.preview_panel_action(**stale)
        value.act('move_srd_scene_creature', actor_state='hero', destination=[16, 0, elevation])
        changed = value.snapshot()
        with pytest.raises((CommandRejected, StaleRevision)):
            value.owner.execute_panel_action(**stale, confirmation=old['confirmation'])
        assert value.snapshot() == changed
    finally:
        value.close()


def test_preview_draws_final_terrain_priced_selection(tmp_path):
    value = terrain_case(tmp_path / 'terrain.sqlite3')
    try:
        proposed = request(value, [15, 0, 0])
        preview = value.owner.preview_panel_action(**proposed)
        points = assert_public_route(value, preview, [15, 0, 0])
        assert all(point[1] >= 0 for point in points)
        value.owner.execute_panel_action(**proposed, confirmation=preview['confirmation'])
        assert value.snapshot().state[NS + '.combat']['movement_spent'] == pytest.approx(preview_cost(preview))
    finally:
        value.close()


def hidden_case(path, variant):
    positions = {'hero': [0, 0, 0], 'guard': [7.5, 0, 0]}
    zones = []
    if variant in ('creature', 'elevation'):
        positions['extra'] = [7.5, 6, 0 if variant == 'creature' else 12]
    if variant == 'terrain':
        zones = [{'id': 'concealed-rubble', 'map_id': 'lite',
            'shape': {'kind': 'box', 'min': [-100, -100, -1], 'max': [100, 100, 7]},
            'metadata': {'difficult': True, 'plane': 'material'}}]
    value = ImportedCase(path, 'fire_bolt', positions=positions, scene_zones=zones,
        participant_changes={'party_role': 'player_character', 'size_rank': 2},
        entity_changes={key: {'size_rank': 2, 'conditions': [], 'ally_state_ids': []} for key in positions},
        scene_metadata_changes={key: {'player': {'label': key.title(), 'kind': 'unit'}}
                                for key in ('hero', 'guard')})
    value.owner.set_admin_full_audit(True)
    value.start()
    return value


def test_hidden_creature_terrain_and_elevation_changes_have_identical_public_previews(tmp_path):
    expected = None
    for variant in ('plain', 'creature', 'terrain', 'elevation'):
        value = hidden_case(tmp_path / (variant + '.sqlite3'), variant)
        try:
            before = value.snapshot()
            proposed = request(value, [15, 0, 1])
            preview = value.owner.preview_panel_action(**proposed)
            assert len(preview['map_preview']['path']['points']) > 2
            public = thaw_json_value(preview)
            if expected is None:
                expected = public
            assert public == expected
            assert value.snapshot() == before
            if variant == 'terrain':
                with pytest.raises(CommandRejected):
                    value.owner.execute_panel_action(**proposed, confirmation=preview['confirmation'])
                assert value.snapshot() == before
        finally:
            value.close()


@pytest.mark.parametrize('declaration', [
    {}, {'scene': 'private.scene', 'route': '_route', 'cost': '_cost'},
    {'scene': 'state.scene', 'route': 'state.secret', 'cost': '_cost'},
    {'scene': 'state.scene', 'route': '_route', 'cost': '_cost', 'metadata': '_private'},
])
def test_preflight_path_declaration_is_closed(declaration):
    assert not parse_player_controls({'preflight': ['qualify'], 'preflight_path': declaration}).available


@pytest.mark.parametrize('change', [
    {'points': [[0, 0, 0]] * 1025}, {'points': [[0, 0, 0], [float('nan'), 0, 0]]},
    {'points': [[0, 0, 0], [101, 0, 0]]}, {'truncated': True}, {'found': False},
])
def test_preflight_path_refuses_invalid_or_exhausted_drawing(change):
    scene = {'map': {'id': 'map', 'revision': 0, 'bounds': {'min': [-100] * 3, 'max': [100] * 3}}}
    route = {'points': [[0, 0, 0], [15, 0, 0]], 'found': True, 'truncated': False, **change}
    with pytest.raises(ValueError):
        project_preflight_path(route, 15, scene)


def test_preflight_drawing_bound_and_closed_public_fields():
    scene = {'map': {'id': 'map', 'revision': 4, 'bounds': {'min': [-100] * 3, 'max': [100] * 3}}}
    points = [[0, 0, 0]] * 1023 + [[15, 0, 1]]
    route = {'points': points, 'found': True, 'truncated': False,
             'snapshot_id': 'private-hash', 'metadata': {'secret': 'not a drawing'}}
    drawing = project_preflight_path(route, 18.7, scene)
    assert set(drawing) == {'notice', 'map_id', 'map_revision', 'path'}
    assert set(drawing['path']) == {'points', 'found', 'truncated', 'cost'}
    assert drawing['path']['points'] == points and drawing['path']['cost'] == 18.7
    assert drawing['map_id'] == 'map' and drawing['map_revision'] == 4
    for invalid in (True, -1, float('nan'), float('inf')):
        with pytest.raises(ValueError):
            project_preflight_path(route, invalid, scene)
