"""Actual owner admission must try affordable terrain-priced creature detours."""
from math import dist

import pytest

from sylvia_gm import dice
from sylvia_gm.domain.errors import CommandRejected, StaleRevision
from tests.lite_imported_fixture import ImportedCase
from tests.test_srd_creature_space_owner import MAP, NS, request


def terrain_case(path, *, side=-1, hidden=False, budget=30, all_difficult=False):
    lower, upper = ([-100, -100, -1], [100, -.0001, 7]) if side < 0 else (
        [-100, .0001, -1], [100, 100, 7])
    if all_difficult:
        lower, upper = [-100, -100, -1], [100, 100, 7]
    metadata = {'difficult': True, 'plane': 'material'}
    if not hidden:
        metadata['player'] = {'label': 'Rubble', 'kind': 'terrain'}
    value = ImportedCase(path, 'fire_bolt',
        positions={'hero': [budget - 30, 0, 0], 'guard': [7.5, 0, 0]},
        scene_zones=[{'id': 'rubble', 'map_id': 'lite',
                      'shape': {'kind': 'box', 'min': lower, 'max': upper}, 'metadata': metadata}],
        participant_changes={'party_role': 'player_character', 'size_rank': 2},
        entity_changes={'hero': {'size_rank': 2, 'ally_state_ids': []},
                        'guard': {'size_rank': 2, 'conditions': []}},
        scene_metadata_changes={key: {'player': {'label': key, 'kind': 'unit'}}
                                for key in ('hero', 'guard')})
    value.owner.set_admin_full_audit(True)
    value.start()
    if budget != 30:
        value.act('move_srd_scene_creature', actor_state='hero', destination=[0, 0, 0])
        assert value.snapshot().state[NS + '.combat']['movement_remaining'] == pytest.approx(budget, abs=1e-10)
    return value


def preview_cost(preview):
    return next(row['value'] for row in preview['preview']['summary'] if row['label'] == 'Movement spent')


@pytest.mark.parametrize('side', [-1, 1])
def test_terrain_priced_detour_availability_confirmation_commit_and_retry(tmp_path, side):
    value = terrain_case(tmp_path / 'terrain.sqlite3', side=side)
    direct = terrain_case(tmp_path / 'command.sqlite3', side=side)
    try:
        witness = [[0, 0, 0], [2.5, -side * 5, 0], [12.5, -side * 5, 0], [15, 0, 0]]
        witness_cost = sum(dist(a, b) for a, b in zip(witness, witness[1:]))
        assert witness_cost == pytest.approx(21.18033988749895)
        before = value.snapshot()
        assert before.state[NS + '.combat']['movement_remaining'] == 30
        proposed = request(value, [15, 0, 0])
        rng = dice._rng.getstate()
        available = value.owner.player_action_availability(campaign_id='lite', expected_revision=before.revision,
            requests=[{'source': {'kind': 'panel', 'surface_id': MAP, 'action_id': 'move-walk'},
                       'selection': proposed['selection']}])
        assert available['results'][0]['available'], available
        preview = value.owner.preview_panel_action(**proposed)
        cost = preview_cost(preview)
        assert 15 < cost <= witness_cost
        assert preview['confirmation_required']
        for change in ({'confirmation': None}, {'confirmation': 'invalid'},
                       {'operation_id': 'forged', 'confirmation': preview['confirmation']}):
            with pytest.raises(CommandRejected):
                value.owner.execute_panel_action(**{**proposed, **change})
        assert value.snapshot() == before and dice._rng.getstate() == rng
        receipt = value.owner.execute_panel_action(**proposed, confirmation=preview['confirmation'])
        after = value.snapshot()
        combat = after.state[NS + '.combat']
        assert combat['movement_spent'] == pytest.approx(cost)
        assert combat['movement_remaining'] == pytest.approx(30 - cost)
        assert next(row for row in after.state[NS + '.state']['spatial_scene']['entities']
                    if row['id'] == 'hero')['position'] == [15, 0, 0]
        direct.act('move_srd_scene_creature', actor_state='hero', destination=[15, 0, 0])
        assert direct.snapshot().state == after.state
        assert value.owner.execute_panel_action(**proposed) == receipt and value.snapshot() == after
        stale = request(value, [20, 0, 0], 'stale')
        stale['expected_revision'] -= 1
        with pytest.raises((CommandRejected, StaleRevision)):
            value.owner.preview_panel_action(**stale)
        assert value.snapshot() == after
        value.reopen()
        assert value.snapshot().state == after.state
        assert value.owner.execute_panel_action(**proposed) == receipt
    finally:
        value.close()
        direct.close()


@pytest.mark.parametrize('delta,affordable', [(0, True), (-.000001, False)])
def test_terrain_detour_exact_budget_and_atomic_refusal(tmp_path, delta, affordable):
    probe = terrain_case(tmp_path / 'probe.sqlite3')
    try:
        exact = preview_cost(probe.owner.preview_panel_action(**request(probe, [15, 0, 0])))
    finally:
        probe.close()
    value = terrain_case(tmp_path / 'boundary.sqlite3', budget=exact + delta)
    try:
        before = value.snapshot()
        proposed = request(value, [15, 0, 0])
        if affordable:
            preview = value.owner.preview_panel_action(**proposed)
            value.owner.execute_panel_action(**proposed, confirmation=preview['confirmation'])
            assert value.snapshot().state[NS + '.combat']['movement_remaining'] == pytest.approx(0, abs=1e-9)
            assert value.snapshot().state[NS + '.combat']['movement_spent'] == pytest.approx(
                before.state[NS + '.combat']['movement_spent'] + exact)
        else:
            with pytest.raises(CommandRejected):
                value.owner.preview_panel_action(**proposed)
            value.reject('move_srd_scene_creature', {'actor_state': 'hero', 'destination': [15, 0, 0]})
            assert value.snapshot() == before
    finally:
        value.close()


def test_all_terrain_options_unaffordable_refuse_without_spending(tmp_path):
    value = terrain_case(tmp_path / 'all.sqlite3', all_difficult=True)
    try:
        before = value.snapshot()
        proposed = request(value, [15, 0, 0])
        with pytest.raises(CommandRejected):
            value.owner.preview_panel_action(**proposed)
        value.reject('move_srd_scene_creature', {'actor_state': 'hero', 'destination': [15, 0, 0]},
                     match='enough movement')
        assert value.snapshot() == before
    finally:
        value.close()


def test_hidden_asymmetric_terrain_has_equal_public_preview_and_authoritative_cost(tmp_path):
    values = [terrain_case(tmp_path / f'hidden-{side}.sqlite3', side=side, hidden=True) for side in (-1, 1)]
    try:
        previews = [value.owner.preview_panel_action(**request(value, [15, 0, 0])) for value in values]
        assert previews[0] == previews[1]
        for value, preview in zip(values, previews):
            proposed = request(value, [15, 0, 0])
            value.owner.execute_panel_action(**proposed, confirmation=preview['confirmation'])
            assert value.snapshot().state[NS + '.combat']['movement_spent'] <= 21.1803398875
    finally:
        for value in values:
            value.close()


def test_terrain_detour_rejects_stale_confirmation_and_retries_fresh_receipt(tmp_path):
    value = terrain_case(tmp_path / 'stale.sqlite3')
    try:
        old_request = request(value, [15, 0, 0])
        old_preview = value.owner.preview_panel_action(**old_request)
        value.act('move_srd_scene_creature', actor_state='hero', destination=[-1, 0, 0])
        changed = value.snapshot()
        with pytest.raises((CommandRejected, StaleRevision)):
            value.owner.execute_panel_action(**old_request, confirmation=old_preview['confirmation'])
        assert value.snapshot() == changed
        proposed = request(value, [15, 0, 0], 'fresh-terrain')
        preview = value.owner.preview_panel_action(**proposed)
        receipt = value.owner.execute_panel_action(**proposed, confirmation=preview['confirmation'])
        after = value.snapshot()
        assert after.state[NS + '.combat']['movement_spent'] == pytest.approx(1 + preview_cost(preview))
        value.reopen()
        assert value.owner.execute_panel_action(**proposed) == receipt
        assert value.snapshot().state == after.state
    finally:
        value.close()
