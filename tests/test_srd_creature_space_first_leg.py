"""An expensive geometric first leg must not veto an affordable candidate route."""
import pytest

from sylvia_gm import dice
from sylvia_gm.domain.errors import CommandRejected
from tests.lite_imported_fixture import ImportedCase
from tests.test_srd_creature_space_owner import MAP, NS, request
from tests.test_srd_creature_space_terrain_routes import preview_cost


def first_leg_case(path, *, all_difficult=False, conditions=()):
    value = ImportedCase(path, 'fire_bolt',
        positions={'hero': [0, 0, 0], 'guard': [20, 0, 0]},
        scene_zones=[{'id': 'upper-rubble', 'map_id': 'lite',
            'shape': {'kind': 'box', 'min': [-100, -100 if all_difficult else .0001, -1],
                      'max': [100, 100, 7]},
            'metadata': {'difficult': True, 'plane': 'material',
                         'player': {'label': 'Rubble', 'kind': 'terrain'}}}],
        participant_changes={'party_role': 'player_character', 'size_rank': 2},
        entity_changes={'hero': {'size_rank': 2, 'ally_state_ids': [], 'conditions': list(conditions)},
                        'guard': {'size_rank': 2, 'conditions': []}},
        scene_metadata_changes={key: {'player': {'label': key, 'kind': 'unit'}}
                                for key in ('hero', 'guard')})
    value.owner.set_admin_full_audit(True)
    value.start()
    return value


def test_unaffordable_first_geometric_leg_does_not_veto_affordable_route(tmp_path):
    value = first_leg_case(tmp_path / 'first-leg.sqlite3')
    try:
        before = value.snapshot()
        assert before.state[NS + '.combat']['movement_remaining'] == 30
        proposed = request(value, [27.5, 0, 0])
        rng = dice._rng.getstate()
        available = value.owner.player_action_availability(campaign_id='lite', expected_revision=before.revision,
            requests=[{'source': {'kind': 'panel', 'surface_id': MAP, 'action_id': 'move-walk'},
                       'selection': proposed['selection']}])
        assert available['results'][0]['available'], available
        preview = value.owner.preview_panel_action(**proposed)
        cost = preview_cost(preview)
        assert cost == pytest.approx(29.9355510591, abs=1e-9)
        for confirmation in (None, 'invalid'):
            with pytest.raises(CommandRejected):
                value.owner.execute_panel_action(**proposed, confirmation=confirmation)
        assert value.snapshot() == before and dice._rng.getstate() == rng
        receipt = value.owner.execute_panel_action(**proposed, confirmation=preview['confirmation'])
        after = value.snapshot()
        assert after.state[NS + '.combat']['movement_spent'] == pytest.approx(cost)
        assert after.state[NS + '.combat']['movement_remaining'] == pytest.approx(30 - cost)
        assert next(row for row in after.state[NS + '.state']['spatial_scene']['entities']
                    if row['id'] == 'hero')['position'] == [27.5, 0, 0]
        assert value.owner.execute_panel_action(**proposed) == receipt
        value.reopen()
        assert value.snapshot().state == after.state
        assert value.owner.execute_panel_action(**proposed) == receipt
    finally:
        value.close()


def test_candidate_repricing_still_refuses_unaffordable_selected_routes_atomically(tmp_path):
    value = first_leg_case(tmp_path / 'unaffordable.sqlite3', all_difficult=True)
    try:
        before = value.snapshot()
        proposed = request(value, [27.5, 0, 0])
        with pytest.raises(CommandRejected):
            value.owner.preview_panel_action(**proposed)
        value.reject('move_srd_scene_creature', {'actor_state': 'hero', 'destination': [27.5, 0, 0]},
                     match='enough movement')
        assert value.snapshot() == before
    finally:
        value.close()


def test_candidate_budget_deferral_preserves_native_movement_eligibility(tmp_path):
    value = first_leg_case(tmp_path / 'grappled.sqlite3', conditions=['grappled'])
    try:
        before = value.snapshot()
        with pytest.raises(CommandRejected):
            value.owner.preview_panel_action(**request(value, [27.5, 0, 0]))
        value.reject('move_srd_scene_creature', {'actor_state': 'hero', 'destination': [27.5, 0, 0]},
                     match='Speed is 0')
        assert value.snapshot() == before
    finally:
        value.close()
