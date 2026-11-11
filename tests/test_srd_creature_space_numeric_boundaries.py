"""Actual-owner regressions for tangent supports and equivalent coordinate encodings."""
from math import cos, dist, sin
import json

import pytest

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.spatial import SpatialEngine, SpatialValidationError
from tests.lite_imported_fixture import ImportedCase
from tests.srd_creature_space_fixtures import state
from tests.test_srd_creature_space_owner import NS, MAP, request
from tests.test_srd_creature_space_terrain_routes import preview_cost


def owner_case(path, kind, encoding='positive'):
    if kind == 'tangent':
        normal, tangent = (cos(2.55), sin(2.55)), (sin(2.55), -cos(2.55))
        def point(t, n=0):
            return [t * tangent[i] + n * normal[i] for i in range(2)] + [0]
        positions = {'hero':point(-10, .25), 'guard':point(0, 5), 'other':point(0, -5)}
        destination = point(10, .25)
        witness = [positions['hero'], point(-7), point(0), point(7), destination]
        budget = 25
    else:
        positions = {'hero':[-20, 0, 0], 'guard':[-12.5, 0, 0],
                     **{f'guard{i:02}':[-12.5 + 6 * i, 0, 0] for i in range(1, 16)}}
        if encoding == 'negative_zero_bodies':
            positions = {key:[-0.0 if value == 0 else float(value) for value in point]
                         for key,point in positions.items()}
        destination = [85.0, -0.0, -0.0] if encoding == 'negative_zero_destination' else [85.0, 0.0, 0.0]
        witness, budget = [], 150
    value = ImportedCase(path, 'fire_bolt', positions=positions,
        participant_changes={'party_role':'player_character', 'size_rank':2, 'speed':budget},
        entity_changes={key:{'size_rank':2, 'conditions':[], 'ally_state_ids':[],
                             **({'speed':budget} if key == 'hero' else {})} for key in positions},
        scene_metadata_changes={key:{'player':{'label':key, 'kind':'unit'}} for key in positions})
    value.owner.set_admin_full_audit(True)
    value.start()
    return value, destination, witness


def commit_parity(panel, direct, destination, *, criterion, budget, maximum_cost):
    before, direct_before = panel.snapshot(), direct.snapshot()
    proposed = request(panel, destination)
    rng = dice._rng.getstate()
    available = panel.owner.player_action_availability(campaign_id='lite', expected_revision=before.revision,
        requests=[{'source':{'kind':'panel', 'surface_id':MAP, 'action_id':'move-walk'},
                   'selection':proposed['selection']}])
    preview, preview_error = None, None
    try:
        preview = panel.owner.preview_panel_action(**proposed)
    except CommandRejected as error:
        preview_error = str(error)
    assert panel.snapshot() == before and dice._rng.getstate() == rng
    command = direct.command('move_srd_scene_creature', {'actor_state':'hero', 'destination':destination})
    receipt = direct.execute(command)
    print('CRITERION ' + json.dumps({'id':criterion, 'available':available['results'][0]['available'],
        'preview_error':preview_error, 'command_status':receipt.status.value,
        'refusal_preserved_state':direct.snapshot().state == direct_before.state}), flush=True)
    assert receipt.status is ActionStatus.COMMITTED
    assert available['results'][0]['available'] and preview is not None
    cost = preview_cost(preview)
    assert 0 < cost < maximum_cost
    for confirmation in (None, 'forged'):
        with pytest.raises(CommandRejected):
            panel.owner.execute_panel_action(**proposed, confirmation=confirmation)
    assert panel.snapshot() == before
    panel_receipt = panel.owner.execute_panel_action(**proposed, confirmation=preview['confirmation'])
    assert panel_receipt.status is ActionStatus.COMMITTED
    after = panel.snapshot()
    assert direct.snapshot().state == after.state
    assert after.state[NS + '.combat']['movement_spent'] == pytest.approx(cost)
    assert after.state[NS + '.combat']['movement_remaining'] == pytest.approx(budget - cost)
    assert panel.owner.execute_panel_action(**proposed) == panel_receipt
    assert direct.execute(command) == receipt
    for value in (panel, direct):
        value.reopen()
        assert value.snapshot().state == after.state
    assert panel.owner.execute_panel_action(**proposed) == panel_receipt
    assert direct.execute(command) == receipt
    print('CRITERION ' + json.dumps({'id':criterion, 'passed':True, 'spent':cost,
        'remaining':after.state[NS + '.combat']['movement_remaining'], 'budget':budget,
        'one_debit_retry_reopen':True}), flush=True)


def test_rounded_tangent_passage_qualifies_and_commits_with_25_feet(tmp_path):
    witness_case, destination, witness = owner_case(tmp_path / 'witness.sqlite3', 'tangent')
    panel, _, _ = owner_case(tmp_path / 'panel.sqlite3', 'tangent')
    direct, _, _ = owner_case(tmp_path / 'command.sqlite3', 'tangent')
    try:
        scene = witness_case.snapshot().state[NS + '.state']['spatial_scene']
        engine = SpatialEngine(scene)
        bodies = {row['id']:row for row in scene['entities']}
        separation = dist(bodies['guard']['position'], bodies['other']['position'])
        assert separation == 9.999999999999998
        for first, second in zip(witness, witness[1:]):
            for fixed in ('guard', 'other'):
                assert not engine.query({'kind':'circle_overlap', 'map_id':'lite', 'moving_entity_id':'hero',
                    'fixed_entity_id':fixed, 'origin':first, 'destination':second,
                    'moving_radius':2.5, 'fixed_radius':2.5}).as_payload()['hit']
            witness_case.act('move_srd_scene_creature', actor_state='hero', destination=second)
        explicit = witness_case.snapshot().state[NS + '.combat']['movement_spent']
        assert explicit == pytest.approx(20.020797289396146)
        print('CRITERION ' + json.dumps({'id':'F1-R1-witness', 'separation':separation, 'explicit_spent':explicit}), flush=True)
        commit_parity(panel, direct, destination, criterion='F1-R1', budget=25, maximum_cost=explicit + 1e-9)
    finally:
        witness_case.close()
        panel.close()
        direct.close()


@pytest.mark.parametrize('encoding', ['negative_zero_destination', 'negative_zero_bodies'])
def test_signed_zero_preview_and_command_use_the_same_proofs(tmp_path, encoding):
    panel, destination, _ = owner_case(tmp_path / 'panel.sqlite3', 'sixteen', encoding)
    direct, _, _ = owner_case(tmp_path / 'command.sqlite3', 'sixteen', encoding)
    try:
        commit_parity(panel, direct, destination, criterion='F1-R2-' + encoding,
                      budget=150, maximum_cost=108.577694721)
    finally:
        panel.close()
        direct.close()


@pytest.mark.parametrize('query_zero,body_zero', [(0, 0), (0.0, 0.0), (-0.0, 0.0), (0.0, -0.0), (-0.0, -0.0)])
def test_equivalent_numeric_overlap_identity_keeps_the_first_real_proof(query_zero, body_zero):
    values = state(position=(7.5, 0, 0))
    bridge = AuthorityMemoryStateBridge(NS, values)
    query = {'map_id':'space', 'moving_entity_id':'hero', 'fixed_entity_id':'guard',
             'origin':[0, 0, 0], 'destination':[15, 0, 0], 'moving_radius':2.5, 'fixed_radius':2.5}
    first = bridge.spatial_query('state.spatial_scene', operation='circle_overlap', query=query, snapshot=None)
    scene = values[NS + '.state']['spatial_scene']
    scene['map']['revision'] += 1
    scene['entities'][0]['position'] = [2, body_zero, body_zero]
    scene['entities'][1]['position'] = [7.5, body_zero, body_zero]
    bridge.write_field('state', 'spatial_scene', scene)
    query.update(origin=[query_zero, query_zero, query_zero], destination=[15.0, query_zero, query_zero])
    reused = bridge.spatial_query('state.spatial_scene', operation='circle_overlap', query=query, snapshot=None)
    assert reused == first
    assert len(bridge.spatial_evidence) == 1


@pytest.mark.parametrize('invalid', [False, True, float('nan'), float('inf'), -float('inf'), 1_000_001])
def test_proof_key_normalization_cannot_admit_invalid_coordinates(invalid):
    bridge = AuthorityMemoryStateBridge(NS, state(position=(7.5, 0, 0)))
    query = {'map_id':'space', 'moving_entity_id':'hero', 'fixed_entity_id':'guard',
             'origin':[0, 0, 0], 'destination':[15, 0, 0], 'moving_radius':2.5, 'fixed_radius':2.5}
    bridge.spatial_query('state.spatial_scene', operation='circle_overlap', query=query, snapshot=None)
    query['origin'] = [0, invalid, 0]
    with pytest.raises(SpatialValidationError):
        bridge.spatial_query('state.spatial_scene', operation='circle_overlap', query=query, snapshot=None)
    assert len(bridge.spatial_evidence) == 1


def test_distinct_nonzero_coordinates_keep_distinct_proof_keys():
    bridge = AuthorityMemoryStateBridge(NS, state(position=(7.5, 0, 0)))
    query = {'map_id':'space', 'moving_entity_id':'hero', 'fixed_entity_id':'guard',
             'destination':[15, 0, 0], 'moving_radius':2.5, 'fixed_radius':2.5}
    for count, coordinate in enumerate([0.0, 5e-324, 1e-13], 1):
        bridge.spatial_query('state.spatial_scene', operation='circle_overlap',
                             query=query | {'origin':[coordinate, 0, 0]}, snapshot=None)
        assert len(bridge.spatial_evidence) == count
