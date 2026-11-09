"""Detours share authoritative command admission and saved native Reaction continuation."""
import pytest

from tests.lite_imported_fixture import ImportedCase
from tests.test_action_sequences import GUARD_SPEAR_ATTACK
from tests.test_srd_creature_space_owner import NS, make_case, request


def test_detour_panel_and_engine_command_commit_the_same_state(tmp_path):
    panel = make_case(tmp_path / 'panel.sqlite3')
    command = make_case(tmp_path / 'command.sqlite3')
    try:
        proposed = request(panel, [15, 0, 0])
        preview = panel.owner.preview_panel_action(**proposed)
        panel.owner.execute_panel_action(**proposed, confirmation=preview['confirmation'])
        command.act('move_srd_scene_creature', actor_state='hero', destination=[15, 0, 0])
        assert command.snapshot().state == panel.snapshot().state
    finally:
        panel.close()
        command.close()


def test_detour_reaction_keeps_final_destination_and_reopens_before_resuming(tmp_path):
    value = ImportedCase(tmp_path / 'reaction.sqlite3', 'fire_bolt',
        positions={'hero': [0, 0, 0], 'guard': [7.5, 0, 0]},
        participant_changes={'party_role': 'player_character', 'size_rank': 2},
        entity_changes={
            'hero': {'size_rank': 2, 'ally_state_ids': [], 'fighting_actor_ids': ['guard'],
                     'attitudes': {'guard': 'hostile'}},
            'guard': {'size_rank': 2, 'conditions': [], 'fighting_actor_ids': ['hero'],
                      'attitudes': {'hero': 'hostile'}, 'melee_reach': 6,
                      'reaction_available': True, 'available_reactions': ['opportunity_attack']}},
        scene_metadata_changes={key: {'player': {'label': key.title(), 'kind': 'unit'}}
                                for key in ('hero', 'guard')})
    value.owner.set_admin_full_audit(True)
    value.start()
    try:
        proposed = request(value, [15, 0, 0])
        preview = value.owner.preview_panel_action(**proposed)
        quoted = {row['label']: row['value'] for row in preview['preview']['summary']}['Movement spent']
        value.act('move_srd_scene_creature', actor_state='hero', destination=[15, 0, 0])
        paused = value.snapshot()
        pending = paused.state[NS + '.state']['pending_srd_movement']
        assert pending['stage'] == 'selection'
        assert pending['destination'] == pending['parameters']['destination'] == [15, 0, 0]
        assert 0 < paused.state[NS + '.combat']['movement_spent'] < quoted
        value.reopen()
        assert value.snapshot().state == paused.state
        decision = {key: val for key, val in GUARD_SPEAR_ATTACK.items() if key != 'attack_distance'}
        value.act('prepare_srd_coordinate_opportunity', **decision, take_reaction=False)
        receipt, command = value.act('complete_srd_coordinate_movement', continue_movement=True)
        after = value.snapshot()
        assert after.state[NS + '.state']['pending_srd_movement'] is None
        assert next(body for body in after.state[NS + '.state']['spatial_scene']['entities']
                    if body['id'] == 'hero')['position'] == [15, 0, 0]
        assert after.state[NS + '.combat']['movement_spent'] == pytest.approx(quoted)
        assert value.execute(command) == receipt
        value.reopen()
        assert value.snapshot().state == after.state
    finally:
        value.close()
