"""Existing three-dimensional movement must also work through creature detours."""
from math import dist

import pytest

from sylvia_gm import dice
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import CommandRejected, StaleRevision
from sylvia_gm.spatial import SpatialEngine
from tests.lite_imported_fixture import ImportedCase
from tests.test_spatial_circle_routes import assert_route_clear, route, route_scene
from tests.test_srd_creature_space_owner import MAP, NS, make_case, request
from tests.test_srd_creature_space_terrain_routes import preview_cost


DESTINATION = [15, 0, 1]
WITNESS = [[0, 0, 0], [2.5, -5, 0], [12.5, -5, 0], DESTINATION]
WITNESS_COST = 21.269078289549746


def test_elevation_changing_route_uses_existing_clearance_and_3d_distance():
    scene = route_scene()
    engine = SpatialEngine(scene)
    result = route(engine, destination=DESTINATION)
    print('F3 primitive:', result, flush=True)
    assert result == route(engine, destination=DESTINATION)
    assert_route_clear(scene, {'circles': [{'entity_id': 'guard', 'radius': 2.5, 'blocking': True}],
        'moving_radius': 2.5, 'metadata_filter': {'key': 'plane', 'value': 'material',
                                               'missing_matches': True}}, result)
    assert result['points'][0] == WITNESS[0] and result['points'][-1] == DESTINATION
    assert dist(WITNESS[0], DESTINATION) < result['distance'] <= WITNESS_COST
    assert result['distance'] == pytest.approx(sum(
        dist(a, b) for a, b in zip(result['points'], result['points'][1:])), abs=1e-12)


@pytest.mark.parametrize('start_z,end_z', [(1, 0), (6, 0), (0, 6), (12, 0), (0, 12)])
def test_elevation_candidates_keep_blockers_relevant_during_ascent_and_descent(start_z, end_z):
    scene = route_scene()
    scene['entities'][0]['position'][2] = start_z
    query = {'circles': [{'entity_id': 'guard', 'radius': 2.5, 'blocking': True}],
             'moving_radius': 2.5, 'metadata_filter': {'key': 'plane', 'value': 'material',
                                                     'missing_matches': True}}
    result = route(SpatialEngine(scene), destination=[15, 0, end_z])
    assert_route_clear(scene, query, result)
    assert {point[2] for point in result['points']} <= {start_z, end_z}
    assert result['distance'] == pytest.approx(sum(
        dist(a, b) for a, b in zip(result['points'], result['points'][1:])), abs=1e-12)


def test_elevation_detour_stays_below_a_ceiling_and_cannot_cross_a_sealed_wall():
    scene = route_scene()
    wall = {'id': 'ceiling', 'map_id': 'space', 'active': True,
        'movement': 'blocking', 'visibility': 'opaque',
        'volume': {'kind': 'box', 'min': [2, -100, 6.5], 'max': [13, 100, 8]},
        'destroyed': {'active': False, 'movement': 'passable', 'visibility': 'clear'}, 'metadata': {}}
    scene['geometry'] = [wall]
    query = {'circles': [{'entity_id': 'guard', 'radius': 2.5, 'blocking': True}],
             'moving_radius': 2.5, 'metadata_filter': {'key': 'plane', 'value': 'material',
                                                     'missing_matches': True}}
    result = route(SpatialEngine(scene), destination=DESTINATION)
    assert_route_clear(scene, query, result)
    wall['volume'] = {'kind': 'box', 'min': [7, -100, -100], 'max': [8, 100, 100]}
    refused = route(SpatialEngine(scene), destination=DESTINATION)
    assert not refused['found'] and not refused['points'] and not refused['truncated']
    capped = route(SpatialEngine(route_scene()), destination=DESTINATION, max_tests=1)
    assert not capped['found'] and capped['truncated'] and capped['tests'] == 1


def test_elevation_witness_and_preview_panel_command_retry_reopen(tmp_path):
    witness = make_case(tmp_path / 'witness.sqlite3')
    panel = make_case(tmp_path / 'panel.sqlite3')
    direct = make_case(tmp_path / 'command.sqlite3')
    try:
        for point in WITNESS[1:]:
            witness.act('move_srd_scene_creature', actor_state='hero', destination=point)
        explicit = witness.snapshot().state[NS + '.combat']['movement_spent']
        assert explicit == pytest.approx(WITNESS_COST, abs=1e-12)
        before = panel.snapshot()
        proposed = request(panel, DESTINATION)
        rng = dice._rng.getstate()
        available = panel.owner.player_action_availability(campaign_id='lite', expected_revision=before.revision,
            requests=[{'source': {'kind': 'panel', 'surface_id': MAP, 'action_id': 'move-walk'},
                       'selection': proposed['selection']}])
        print('F3 owner:', {'explicit_spent': explicit, 'availability': available}, flush=True)
        assert available['results'][0]['available'], available
        preview = panel.owner.preview_panel_action(**proposed)
        cost = preview_cost(preview)
        assert preview == panel.owner.preview_panel_action(**proposed)
        assert dist(WITNESS[0], DESTINATION) < cost <= explicit
        assert preview['confirmation_required']
        for change in ({'confirmation': None}, {'confirmation': 'forged'},
                       {'operation_id': 'changed', 'confirmation': preview['confirmation']},
                       {'selection': {**proposed['selection'], 'aim_point': [15, 0, 2]},
                        'confirmation': preview['confirmation']}):
            with pytest.raises(CommandRejected):
                panel.owner.execute_panel_action(**{**proposed, **change})
        assert panel.snapshot() == before and dice._rng.getstate() == rng
        receipt = panel.owner.execute_panel_action(**proposed, confirmation=preview['confirmation'])
        assert receipt.status is ActionStatus.COMMITTED
        after = panel.snapshot()
        assert after.state[NS + '.combat']['movement_spent'] == pytest.approx(cost, abs=1e-12)
        assert after.state[NS + '.combat']['movement_remaining'] == pytest.approx(30 - cost, abs=1e-12)
        assert next(body for body in after.state[NS + '.state']['spatial_scene']['entities']
                    if body['id'] == 'hero')['position'] == DESTINATION
        command_receipt, command = direct.act('move_srd_scene_creature', actor_state='hero',
                                               destination=DESTINATION)
        assert direct.snapshot().state == after.state
        assert panel.owner.panel_action_receipt(**proposed) == receipt
        assert panel.owner.execute_panel_action(**proposed) == receipt and panel.snapshot() == after
        assert direct.execute(command) == command_receipt and direct.snapshot().state == after.state
        for value in (panel, direct):
            value.reopen()
            assert value.snapshot().state == after.state
        assert panel.owner.execute_panel_action(**proposed) == receipt
        assert direct.execute(command) == command_receipt
        assert panel.snapshot().state == direct.snapshot().state == after.state
        print('F3 committed:', {'witness': explicit, 'spent': cost, 'remaining': 30 - cost,
                               'panel_command_retry_reopen': True}, flush=True)
    finally:
        witness.close()
        panel.close()
        direct.close()


def test_elevation_stale_confirmation_refuses_atomically(tmp_path):
    value = make_case(tmp_path / 'stale.sqlite3')
    try:
        proposed = request(value, DESTINATION)
        preview = value.owner.preview_panel_action(**proposed)
        value.act('move_srd_scene_creature', actor_state='hero', destination=[-1, 0, 0])
        before = value.snapshot()
        with pytest.raises((CommandRejected, StaleRevision)):
            value.owner.execute_panel_action(**proposed, confirmation=preview['confirmation'])
        assert value.snapshot() == before
        fresh = request(value, DESTINATION, 'fresh-elevation')
        forged = {**fresh, 'selection': {**fresh['selection'],
                                       'map_revision': fresh['selection']['map_revision'] - 1}}
        with pytest.raises((CommandRejected, StaleRevision)):
            value.owner.preview_panel_action(**forged)
        assert value.snapshot() == before
    finally:
        value.close()


def test_elevation_insufficient_budget_refuses_without_partial_movement(tmp_path):
    value = make_case(tmp_path / 'budget.sqlite3', mover_position=(-12, 0, 0))
    try:
        value.act('move_srd_scene_creature', actor_state='hero', destination=[0, 0, 0])
        before = value.snapshot()
        assert before.state[NS + '.combat']['movement_remaining'] == 18
        proposed = request(value, DESTINATION)
        available = value.owner.player_action_availability(campaign_id='lite', expected_revision=before.revision,
            requests=[{'source': {'kind': 'panel', 'surface_id': MAP, 'action_id': 'move-walk'},
                       'selection': proposed['selection']}])
        assert not available['results'][0]['available']
        with pytest.raises(CommandRejected):
            value.owner.preview_panel_action(**proposed)
        value.reject('move_srd_scene_creature', {'actor_state': 'hero', 'destination': DESTINATION},
                     match='enough movement')
        assert value.snapshot() == before
        value.reopen()
        assert value.snapshot().state == before.state
    finally:
        value.close()


def test_elevation_invalid_or_occupied_destination_refuses_atomically(tmp_path):
    value = make_case(tmp_path / 'invalid.sqlite3')
    try:
        for destination in ([7.5, 0, 1], [15, 0, 95]):
            before = value.snapshot()
            proposed = request(value, destination)
            with pytest.raises(CommandRejected):
                value.owner.preview_panel_action(**proposed)
            value.reject('move_srd_scene_creature', {'actor_state': 'hero', 'destination': destination})
            assert value.snapshot() == before
    finally:
        value.close()


def test_elevation_hidden_creature_keeps_public_preview_equal_and_execution_authoritative(tmp_path):
    cases = []
    try:
        for name, guard_x in (('plain', 50), ('hidden', 7.5)):
            value = ImportedCase(tmp_path / (name + '.sqlite3'), 'fire_bolt',
                positions={'hero': [0, 0, 0], 'guard': [guard_x, 0, 0]},
                participant_changes={'party_role': 'player_character', 'size_rank': 2},
                entity_changes={'hero': {'size_rank': 2, 'ally_state_ids': []},
                                'guard': {'size_rank': 2, 'conditions': []}},
                scene_metadata_changes={'hero': {'player': {'label': 'Hero', 'kind': 'unit'}}})
            cases.append(value)
            value.owner.set_admin_full_audit(True)
            value.start()
        plain, hidden = cases
        before = hidden.snapshot()
        proposed = request(hidden, DESTINATION)
        preview = hidden.owner.preview_panel_action(**proposed)
        assert preview == plain.owner.preview_panel_action(**request(plain, DESTINATION))
        assert preview_cost(preview) == pytest.approx(dist(WITNESS[0], DESTINATION))
        assert hidden.snapshot() == before
        receipt = hidden.owner.execute_panel_action(**proposed, confirmation=preview['confirmation'])
        after = hidden.snapshot()
        spent = after.state[NS + '.combat']['movement_spent']
        assert preview_cost(preview) < spent <= WITNESS_COST
        assert spent == pytest.approx(18.656502321304757, abs=1e-12)
        assert hidden.owner.execute_panel_action(**proposed) == receipt
        hidden.reopen()
        assert hidden.snapshot().state == after.state
        assert hidden.owner.execute_panel_action(**proposed) == receipt
    finally:
        for value in cases:
            value.close()
