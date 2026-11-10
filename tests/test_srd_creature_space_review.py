"""Review regressions: hidden scene facts and declared Halfling passage."""
from copy import deepcopy

import pytest

from tests.srd_creature_space_fixtures import NS, run, state
from tests.srd_creature_space_fixtures import runtime as runtime
from tests.test_srd_creature_space_owner import request
from tests.lite_imported_fixture import ImportedCase
from sylvia_gm.domain.errors import CommandRejected


def zone(*, visible=False):
    metadata = {'difficult': True, 'plane': 'material', 'gm_secret': 'concealed-rubble'}
    if visible:
        metadata['player'] = {'label': 'Rubble', 'kind': 'terrain'}
    return {'id': 'rubble', 'map_id': 'space', 'shape': {
        'kind': 'box', 'min': [2, -2, -1], 'max': [8, 2, 7]}, 'metadata': metadata}


def visible_mover():
    values = state(position=(50, 0, 0))
    scene = values[NS + '.state']['spatial_scene']
    scene['entities'][0]['metadata']['player'] = {'label': 'Hero', 'kind': 'unit'}
    scene['entities'] = scene['entities'][:1]
    return values


def cost(runtime, values, *, player_view):
    return run(runtime, values, tool='srd_information_movement_cost', player_view=player_view,
               parameters={'start': [0, 0, 0], 'end': [10, 0, 0], 'actor_subject': 'hero',
                           'base_difficult': False, 'plane': 'material'})


def test_hidden_zone_cannot_change_the_player_cost_but_admission_keeps_it(runtime):
    plain = visible_mover()
    concealed = deepcopy(plain)
    concealed[NS + '.state']['spatial_scene']['zones'].append(zone())
    authoritative, _ = cost(runtime, concealed, player_view=False)
    assert authoritative.success and authoritative.context['_route_cost'] == pytest.approx(16)
    clean, _ = cost(runtime, plain, player_view=True)
    hidden, bridge = cost(runtime, concealed, player_view=True)
    assert clean.success and hidden.success, hidden.error
    assert hidden.context['_route_cost'] == clean.context['_route_cost'] == 10
    assert not bridge.mutations()


def test_distant_hidden_creature_does_not_make_the_preview_disappear(runtime):
    values = visible_mover()
    concealed = deepcopy(state()[NS + '.state']['spatial_scene']['entities'][1])
    concealed['position'] = [50, 0, 0]
    values[NS + '.state']['spatial_scene']['entities'].append(concealed)
    result, bridge = cost(runtime, values, player_view=True)
    assert result.success, result.error
    assert result.context['_route_cost'] == 10
    assert not bridge.mutations()


def test_visible_zone_still_changes_the_player_cost(runtime):
    values = visible_mover()
    values[NS + '.state']['spatial_scene']['zones'].append(zone(visible=True))
    result, _ = cost(runtime, values, player_view=True)
    assert result.success, result.error
    assert result.context['_route_cost'] == pytest.approx(16)


@pytest.mark.parametrize('species,mover,other,allowed', [
    ('halfling', 1, 2, True), ('human', 1, 2, False), ('halfling', 1, 1, False),
    ('halfling', 2, 3, True), ('halfling', 2, 2, False),
])
def test_nimbleness_uses_declared_species_and_actual_relative_size(runtime, species, mover, other, allowed):
    values = state(mover, other)
    values[NS + '.entities']['hero']['species'] = species
    result, _ = run(runtime, values)
    assert result.success == allowed, result.error
    if allowed:
        assert result.context['_space_intervals'] == [{'entry': .4 if mover == 1 else .35,
                                                       'exit': .6 if mover == 1 else .65}]
        stopped, _ = run(runtime, values, destination=(25, 0, 0))
        assert not stopped.success and 'cannot end' in stopped.error


def owner_case(path, *, hidden=None, halfling=False):
    positions = {'hero': [0, 0, 0], 'guard': [50, 0, 0]}
    metadata = {key: {'player': {'label': key.title(), 'kind': 'unit'}} for key in positions}
    zones = []
    geometry = []
    if hidden in ('distant', 'blocking'):
        positions['extra'] = [50 if hidden == 'distant' else 5, 0, 0]
    if halfling:
        positions['guard'] = [5, 0, 0]
    if hidden == 'zone':
        zones = [{**zone(), 'map_id': 'lite'}]
    if hidden == 'wall':
        geometry = [{'id': 'secret-wall', 'map_id': 'lite', 'active': True,
                     'movement': 'blocking', 'visibility': 'opaque',
                     'volume': {'kind': 'box', 'min': [4, -2, -1], 'max': [6, 2, 7]},
                     'destroyed': {'active': False, 'movement': 'passable', 'visibility': 'clear'},
                     'metadata': {'gm_secret': 'concealed-wall'}}]
    value = ImportedCase(path, 'fire_bolt', positions=positions, scene_zones=zones, scene_geometry=geometry,
        participant_changes={'party_role': 'player_character', 'species': 'halfling' if halfling else 'human',
                             'size_rank': 1 if halfling else 2},
        entity_changes={'hero': {'size_rank': 1 if halfling else 2, 'ally_state_ids': [],
                                 'species': 'halfling' if halfling else 'human'},
                        'guard': {'size_rank': 2, 'conditions': []},
                        'extra': {'size_rank': 2, 'conditions': []}},
        scene_metadata_changes=metadata)
    value.owner.set_admin_full_audit(True)
    value.start()
    return value


@pytest.mark.parametrize('hidden', ['zone', 'distant', 'blocking', 'wall'])
def test_public_preview_does_not_disclose_hidden_scene_changes(tmp_path, hidden):
    plain = owner_case(tmp_path / 'plain.sqlite3')
    secret = owner_case(tmp_path / 'secret.sqlite3', hidden=hidden)
    try:
        before = secret.snapshot()
        clear_preview = plain.owner.preview_panel_action(**request(plain, [10, 0, 0]))
        proposed = request(secret, [10, 0, 0])
        hidden_preview = secret.owner.preview_panel_action(**proposed)
        assert hidden_preview == clear_preview
        def availability(case):
            selected = request(case, [10, 0, 0])
            return case.owner.player_action_availability(campaign_id='lite', expected_revision=before.revision,
                requests=[{'source': {'kind': 'panel', 'surface_id': 'srd-coordinate-map', 'action_id': 'move-walk'},
                           'selection': selected['selection']}])
        assert availability(secret) == availability(plain)
        assert secret.snapshot() == before
        if hidden == 'wall':
            with pytest.raises(CommandRejected):
                secret.owner.execute_panel_action(**proposed, confirmation=hidden_preview['confirmation'])
            assert secret.snapshot() == before
        else:
            receipt = secret.owner.execute_panel_action(**proposed, confirmation=hidden_preview['confirmation'])
            spent = secret.snapshot().state[NS + '.combat']['movement_spent']
            if hidden == 'blocking':
                assert 10 < spent <= 30
            else:
                assert spent == pytest.approx(16 if hidden == 'zone' else 10)
            after = secret.snapshot()
            assert secret.owner.execute_panel_action(**proposed) == receipt
            secret.reopen()
            assert secret.snapshot().state == after.state
    finally:
        plain.close()
        secret.close()


def test_halfling_public_preview_commit_cost_endpoint_and_reopen(tmp_path):
    value = owner_case(tmp_path / 'halfling.sqlite3', halfling=True)
    try:
        value.reject('move_srd_scene_creature', {'actor_state': 'hero', 'destination': [5, 0, 0]},
                     match='cannot end')
        proposed = request(value, [10, 0, 0])
        preview = value.owner.preview_panel_action(**proposed)
        assert {row['label']: row['value'] for row in preview['preview']['summary']}['Movement spent'] == 20
        receipt = value.owner.execute_panel_action(**proposed, confirmation=preview['confirmation'])
        after = value.snapshot()
        assert after.state[NS + '.combat']['movement_spent'] == 20
        assert value.owner.execute_panel_action(**proposed) == receipt
        value.reopen()
        assert value.snapshot().state == after.state
    finally:
        value.close()
