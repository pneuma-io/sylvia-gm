"""Narrow and sixteen-creature detours through actual Lite admission and movement."""
from math import cos, dist, pi, sin

import pytest

from sylvia_gm import dice
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import CommandRejected
from tests.lite_imported_fixture import ImportedCase
from tests.test_srd_creature_space_owner import MAP, NS, request
from tests.test_srd_creature_space_terrain_routes import preview_cost


def passage_case(path, kind='narrow', *, budget=None):
    if kind == 'narrow':
        normal, tangent = (cos(7 * pi / 16), sin(7 * pi / 16)), (sin(7 * pi / 16), -cos(7 * pi / 16))
        def point(t, n=0):
            return [t * tangent[i] + n * normal[i] for i in range(2)] + [0]
        positions = {'hero': point(-10, .25), 'guard': point(0, 5.005), 'other': point(0, -5.005)}
        destination = point(10, .25)
        witness = [positions['hero'], point(-7), point(0), point(7), destination]
        walls = [{'id': identity, 'map_id': 'lite', 'active': True, 'movement': 'blocking',
                  'visibility': 'opaque', 'volume': {'kind': 'box', 'min': lo, 'max': hi},
                  'destroyed': {'active': False, 'movement': 'passable', 'visibility': 'clear'},
                  'metadata': {'plane': 'material', 'player': {'label': identity, 'kind': 'wall'}}}
                 for identity, lo, hi in [('upper', [-100, 7.5, -100], [100, 100, 100]),
                                         ('lower', [-100, -100, -100], [100, -7.5, 100])]]
        budget = 30 if budget is None else budget
    else:
        # Translate the primitive's exact 105-foot scene into this fixture's map bounds.
        positions = {'hero': [-20, 0, 0], 'guard': [-12.5, 0, 0],
                     **{f'guard{i:02}': [-12.5 + 6 * i, 0, 0] for i in range(1, 16)}}
        destination, witness, walls = [85, 0, 0], [[-20, 0, 0], [-20, -5.01, 0], [85, -5.01, 0], [85, 0, 0]], []
        budget = 150 if budget is None else budget
    value = ImportedCase(path, 'fire_bolt', positions=positions, scene_geometry=walls,
        participant_changes={'party_role': 'player_character', 'size_rank': 2, 'speed': budget},
        entity_changes={key: {'size_rank': 2, 'conditions': [], 'ally_state_ids': [],
                              **({'speed': budget} if key == 'hero' else {})} for key in positions},
        scene_metadata_changes={key: {'player': {'label': key, 'kind': 'unit'}} for key in positions})
    value.owner.set_admin_full_audit(True)
    value.start()
    return value, destination, sum(dist(a, b) for a, b in zip(witness, witness[1:]))


@pytest.mark.parametrize('kind', ['narrow', 'sixteen'])
def test_passage_preview_action_command_budget_retry_and_reopen(tmp_path, kind):
    panel, destination, witness = passage_case(tmp_path / 'panel.sqlite3', kind)
    direct, _, _ = passage_case(tmp_path / 'command.sqlite3', kind)
    try:
        before = panel.snapshot()
        budget = before.state[NS + '.combat']['movement_remaining']
        if kind == 'narrow':
            assert witness == pytest.approx(20.02079728939615)
        proposed = request(panel, destination)
        rng = dice._rng.getstate()
        availability = panel.owner.player_action_availability(campaign_id='lite', expected_revision=before.revision,
            requests=[{'source': {'kind': 'panel', 'surface_id': MAP, 'action_id': 'move-walk'},
                       'selection': proposed['selection']}])
        assert availability['results'][0]['available'], availability
        preview = panel.owner.preview_panel_action(**proposed)
        cost = preview_cost(preview)
        assert dist(before.state[NS + '.state']['spatial_scene']['entities'][0]['position'], destination) < cost < witness
        assert preview['confirmation_required']
        for confirmation in (None, 'forged'):
            with pytest.raises(CommandRejected):
                panel.owner.execute_panel_action(**proposed, confirmation=confirmation)
        assert panel.snapshot() == before and dice._rng.getstate() == rng
        receipt = panel.owner.execute_panel_action(**proposed, confirmation=preview['confirmation'])
        assert receipt.status is ActionStatus.COMMITTED
        after = panel.snapshot()
        combat = after.state[NS + '.combat']
        assert combat['movement_spent'] == pytest.approx(cost)
        assert combat['movement_remaining'] == pytest.approx(budget - cost)
        assert next(row for row in after.state[NS + '.state']['spatial_scene']['entities']
                    if row['id'] == 'hero')['position'] == destination
        assert panel.owner.execute_panel_action(**proposed) == receipt
        direct.act('move_srd_scene_creature', actor_state='hero', destination=destination)
        assert direct.snapshot().state == after.state
        panel.reopen()
        assert panel.snapshot().state == after.state
        assert panel.owner.execute_panel_action(**proposed) == receipt
        assert panel.snapshot().state == after.state
    finally:
        panel.close()
        direct.close()


def test_narrow_passage_insufficient_budget_refuses_without_mutation(tmp_path):
    value, destination, _ = passage_case(tmp_path / 'short.sqlite3', budget=20)
    try:
        before = value.snapshot()
        proposed = request(value, destination)
        available = value.owner.player_action_availability(campaign_id='lite', expected_revision=before.revision,
            requests=[{'source': {'kind': 'panel', 'surface_id': MAP, 'action_id': 'move-walk'},
                       'selection': proposed['selection']}])
        assert not available['results'][0]['available']
        with pytest.raises(CommandRejected):
            value.owner.preview_panel_action(**proposed)
        value.reject('move_srd_scene_creature', {'actor_state': 'hero', 'destination': destination}, match='enough movement')
        assert value.snapshot() == before
        value.reopen()
        assert value.snapshot().state == before.state
    finally:
        value.close()
