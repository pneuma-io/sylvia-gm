"""Actual Lite action admission, budgets, confirmation, retry and reopen for ordinary movement."""
from math import dist

import pytest

from sylvia_gm import dice
from tests.lite_imported_fixture import ImportedCase
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import CommandRejected, RulesetPreconditionRejected, StaleRevision

NS = 'sylvia.srd-lite.bg3'
MAP = 'srd-coordinate-map'


def make_case(
    path, *, ally=False, incapacitated=False, guard_position=(7.5, 0, 0),
    mover_position=(0, 0, 0), start=True, archive_bytes=None,
):
    value = ImportedCase(
        path, 'fire_bolt', archive_bytes=archive_bytes,
        positions={'hero': list(mover_position), 'guard': list(guard_position)},
        participant_changes={'party_role': 'player_character', 'size_rank': 2},
        entity_changes={'hero': {'size_rank': 2, 'ally_state_ids': ['guard'] if ally else []},
                        'guard': {'size_rank': 2, 'conditions': ['incapacitated'] if incapacitated else []}},
        scene_metadata_changes={key: {'player': {'label': key.title(), 'kind': 'unit'}} for key in ('hero', 'guard')})
    value.owner.set_admin_full_audit(True)
    if start:
        value.start()
    return value


def request(value, destination, operation='space-move'):
    panels = value.owner.campaign_panels('lite')
    surface = next(row for row in panels['surfaces'] if row['id'] == MAP)
    return dict(campaign_id='lite', surface_id=MAP, panel_action_id='move-walk', operation_id=operation,
                expected_revision=panels['revision'], selection={'actor': 'hero', 'aim_point': list(destination),
                'map_revision': surface['data']['map_revision']})


@pytest.mark.parametrize('destination,reason', [((7.5, 0, 0), 'cannot end')])
def test_ordinary_action_rejects_occupied_endpoint_without_any_mutation(tmp_path, destination, reason):
    value = make_case(tmp_path / 'reject.sqlite3')
    try:
        proposed = request(value, destination)
        before = value.snapshot()
        availability = value.owner.player_action_availability(campaign_id='lite', expected_revision=before.revision,
            requests=[{'source': {'kind': 'panel', 'surface_id': MAP, 'action_id': 'move-walk'},
                       'selection': proposed['selection']}])
        assert availability['results'][0]['available'] is False
        with pytest.raises(CommandRejected):
            value.owner.preview_panel_action(**proposed)
        assert value.snapshot() == before
        value.reject('move_srd_scene_creature', {'actor_state': 'hero', 'destination': list(destination)}, match=reason)
    finally:
        value.close()


@pytest.mark.parametrize('ally,base_difficult,expected', [(True, False, 15), (False, False, 25), (False, True, 30)])
def test_actual_cost_uses_nonstacking_difficult_union(tmp_path, ally, base_difficult, expected):
    value = make_case(tmp_path / 'cost.sqlite3', ally=ally, incapacitated=not ally)
    try:
        receipt, command = value.act('move_srd_scene_creature', actor_state='hero', destination=[15, 0, 0],
                                     difficult_terrain=base_difficult)
        after = value.snapshot()
        combat = after.state[NS + '.combat']
        assert combat['movement_spent'] == pytest.approx(expected)
        assert combat['movement_remaining'] == pytest.approx(30 - expected)
        assert value.execute(command) == receipt and value.snapshot() == after
        value.reopen()
        assert value.execute(command) == receipt and value.snapshot().state == after.state
    finally:
        value.close()


@pytest.mark.parametrize('ally,incapacitated,cost', [(True, False, 15), (False, True, 25)])
def test_panel_preview_confirmation_cancellation_retry_and_engine_command_parity(tmp_path, ally, incapacitated, cost):
    direct = make_case(tmp_path / 'direct.sqlite3', ally=ally, incapacitated=incapacitated)
    command = make_case(tmp_path / 'command.sqlite3', ally=ally, incapacitated=incapacitated)
    try:
        proposed = request(direct, [15, 0, 0])
        before = direct.snapshot()
        rng = dice._rng.getstate()
        with pytest.raises(CommandRejected, match='Preview and confirm'):
            direct.owner.execute_panel_action(**proposed)
        assert direct.snapshot() == before
        availability = direct.owner.player_action_availability(campaign_id='lite', expected_revision=before.revision,
            requests=[{'source': {'kind': 'panel', 'surface_id': MAP, 'action_id': 'move-walk'},
                       'selection': proposed['selection']}])
        assert availability['results'][0]['available'], availability
        preview = direct.owner.preview_panel_action(**proposed)
        assert preview['confirmation_required'] is True
        summary = {row['label']: row['value'] for row in preview['preview']['summary']}
        assert summary['Movement spent'] == pytest.approx(cost)
        drawing = preview['map_preview']
        assert set(drawing) == {'notice', 'map_id', 'map_revision', 'path'}
        path = drawing['path']
        assert set(path) == {'found', 'truncated', 'points', 'cost'}
        assert path['found'] is True and path['truncated'] is False
        points = path['points']
        assert len(points) >= 2 and points[0] == [0, 0, 0] and points[-1] == [15, 0, 0]
        # Permitted passage stays straight; its surcharge belongs in the quoted cost.
        assert sum(dist(a, b) for a, b in zip(points, points[1:])) == pytest.approx(15)
        assert path['cost'] == pytest.approx(cost)
        assert availability['results'][0]['map_preview'] == drawing
        assert dice._rng.getstate() == rng
        assert direct.snapshot() == before
        # Cancelling means no execution. Repeating a read-only preview never reserves/spends an action.
        preview = direct.owner.preview_panel_action(**proposed)
        assert direct.snapshot() == before
        receipt = direct.owner.execute_panel_action(**proposed, confirmation=preview['confirmation'])
        assert receipt.status is ActionStatus.COMMITTED
        after = direct.snapshot()
        assert after.state[NS + '.combat']['movement_spent'] == pytest.approx(path['cost'])
        assert after.state[NS + '.combat']['movement_remaining'] == pytest.approx(30 - cost)
        assert direct.owner.panel_action_receipt(**proposed) == receipt
        assert direct.owner.execute_panel_action(**proposed) == receipt
        assert direct.snapshot() == after
        command.act('move_srd_scene_creature', actor_state='hero', destination=[15, 0, 0])
        assert command.snapshot().state == after.state
        stale = request(direct, [20, 0, 0], 'stale')
        stale['expected_revision'] -= 1
        with pytest.raises((CommandRejected, StaleRevision)):
            direct.owner.preview_panel_action(**stale)
        assert direct.snapshot() == after
        direct.reopen()
        assert direct.owner.panel_action_receipt(**proposed) == receipt
        assert direct.snapshot().state == after.state
    finally:
        direct.close()
        command.close()


def test_actual_turn_end_saves_prone_and_receipt_then_reopens(tmp_path):
    value = make_case(tmp_path / 'turn-overlap.sqlite3', ally=True, guard_position=(3, 0, 0))
    try:
        receipt, command = value.act('end_combat_turn')
        assert 'prone' in value.entities()['hero']['conditions']
        assert 'prone' in str(receipt.result['result_card']).lower()
        after = value.snapshot()
        value.reopen()
        assert value.snapshot().state == after.state
        assert value.execute(command) == receipt
    finally:
        value.close()


def test_creature_cost_leaves_the_exact_budget_for_a_followup_move(tmp_path):
    value = make_case(tmp_path / 'fractional-budget.sqlite3', incapacitated=True,
                      mover_position=(-1.25, 2.125, 0), guard_position=(8.75, 2.125, 0))
    try:
        value.act('move_srd_scene_creature', actor_state='hero', destination=[13.75, 2.125, 0])
        value.reject('move_srd_scene_creature', {'actor_state': 'hero', 'destination': [18.751, 2.125, 0]},
                     match='enough movement')
        proposed = request(value, [18.75, 2.125, 0], 'spend-final-five')
        preview = value.owner.preview_panel_action(**proposed)
        assert preview['preview']['summary'][0]['value'] == pytest.approx(5)
        receipt = value.owner.execute_panel_action(**proposed, confirmation=preview['confirmation'])
        combat = value.snapshot().state[NS + '.combat']
        assert combat['movement_spent'] == 30 and combat['movement_remaining'] == 0
        value.reject('move_srd_scene_creature', {'actor_state': 'hero', 'destination': [19.25, 2.125, 0]},
                     match='enough movement')
        after = value.snapshot()
        assert value.owner.execute_panel_action(**proposed) == receipt
        value.reopen()
        assert value.snapshot().state == after.state
    finally:
        value.close()


def test_exploration_keeps_its_existing_fictional_travel_boundary(tmp_path):
    value = make_case(tmp_path / 'exploration-budget.sqlite3', incapacitated=True, start=False,
                      mover_position=(-1.25, 2.125, 0), guard_position=(8.75, 2.125, 0))
    try:
        before = value.snapshot()
        with pytest.raises(RulesetPreconditionRejected, match='Outside combat, use the established fictional travel'):
            value.act('move_srd_scene_creature', actor_state='hero', destination=[13.75, 2.125, 0])
        assert value.snapshot() == before
    finally:
        value.close()
