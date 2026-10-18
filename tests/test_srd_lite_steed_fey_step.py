"""Native Fey Step geometry, saved rider, teleport, budgets and truthful receipt checks."""
from copy import deepcopy
import json, os
from pathlib import Path
import pytest

from scripts.srd_lite_steed_fey_step import (
    ACTION, PREFLIGHT, RIDER_FIELD, MOUNT_FIELD, bind_steed_fey_step, expose_steed_fey_step)
from scripts.srd_lite_steed_healing_touch import CHARGE
from sylvia_gm import dice
from sylvia_gm.application.package_runtime import _json_safe
from sylvia_gm.application.result_cards import project_result_card
from sylvia_gm.application.result_card_admission import validate_result_card_declaration
from sylvia_gm.runtime import RulesetRuntime
from tests.test_srd_lite_steed_healing_touch import bound as touch_bound, _case, _snapshot
from tests.test_srd_lite_mass_cure_geometry import _calls, _scene_entity, _wall


@pytest.fixture(scope='module')
def bound(touch_bound):
    data = deepcopy(touch_bound[1])
    original = {name: deepcopy(data['tools'][name]) for name in (
        'move_srd_travel_subject', 'move_srd_travel_subject_node',
        'resolve_srd_travel_placement', 'consume_srd_bonus_action', 'complete_srd_rest')}
    profiles = deepcopy(data['definitions']['spells'])
    changed = bind_steed_fey_step(data)
    assert data['definitions']['spells'] == profiles
    assert all(data['tools'][name] == tool for name, tool in original.items())
    node = original['move_srd_travel_subject_node']
    owning_index = next(i for i, row in enumerate(node['effects']) if 'spatial_mutation' in row)
    assert data['tools'][PREFLIGHT]['effects'][2:] == node['effects'][:owning_index]
    closure, pending = {}, [ACTION]
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data['tools'][name]
            pending.extend(_calls(closure[name]['effects']))
    parsed = RulesetRuntime(ruleset_dict={**{key: value for key, value in data.items()
        if key not in {'tools', 'procedures'}}, 'tools': closure})
    validate_result_card_declaration(data['tools'][ACTION]['metadata']['result_card'])
    return parsed, data, changed


def _fey_case(bound, *, rider=False, combat=False, geometry=None, steed_changes=None,
              caster_changes=None, rider_changes=None, rider_position=None, rider_plane='material'):
    bridge, state, act = _case(bound, combat=combat,
        steed_changes={'creature_type': 'fey', 'size_rank': 4, RIDER_FIELD: 'caster' if rider else '',
                       **(steed_changes or {})},
        caster_changes={'size_rank': 3, MOUNT_FIELD: 'steed' if rider else '',
                        **(caster_changes or {}), **(rider_changes or {})})
    scene = deepcopy(bridge.get_entity('state')['spatial_scene'])
    scene['geometry'] = geometry or []
    if rider:
        scene['entities'][0] = _scene_entity('caster', rider_position or [0, 0, 6], plane=rider_plane)
    bridge.write_field('state', 'spatial_scene', scene)
    return bridge, state, act


def _step(act, destination=None):
    return act(ACTION, steed_state='steed', destination=destination or [60, 0, 0])


def _frame(bridge, identity):
    return next(row for row in bridge.get_entity('state')['spatial_scene']['entities']
                if row['metadata']['participant_ref'] == identity)


@pytest.mark.parametrize('combat', [False, True])
def test_native_unridden_exact_60_foot_step_cost_and_receipt(bound, combat):
    bridge, _, act = _fey_case(bound, combat=combat)
    rng_before = dice._rng.getstate()
    result = _step(act)
    assert result.success, result.error
    assert _frame(bridge, 'steed')['position'] == [60, 0, 0]
    assert _frame(bridge, 'caster')['position'] == [-10, 0, 0]
    assert _frame(bridge, 'steed')['metadata']['previous_position'] == [0, 0, 0]
    assert bridge.get_entity('steed')[CHARGE] is False
    assert bridge.get_entity('steed')['bonus_action_available'] is (not combat)
    assert dice._rng.getstate() == rng_before
    card = project_result_card(bound[1]['tools'][ACTION]['metadata']['result_card'],
        parameters={}, public_context=_json_safe(result.context))
    assert card is not None
    assert {row['label']: row['value'] for row in card['summary']} == {
        'Steed': 'Otherworldly Steed', 'Rider': 'None', 'Distance (ft)': 60,
        'Destination': [60, 0, 0]}
    assert all('equation' not in row for row in card['summary'])


def test_native_teleport_crosses_an_unseen_solid_wall_without_sight_or_sweep(bound):
    bridge, _, act = _fey_case(bound, geometry=[_wall()])
    scene = deepcopy(bridge.get_entity('state')['spatial_scene'])
    for frame in scene['entities']:
        frame['metadata']['lighting'] = 'darkness'
    bridge.write_field('state', 'spatial_scene', scene)
    result = _step(act, [30, 0, 0])
    assert result.success, result.error
    assert _frame(bridge, 'steed')['position'] == [30, 0, 0]


def test_native_saved_rider_moves_with_saddle_offset_and_receipt(bound):
    bridge, _, act = _fey_case(bound, rider=True, combat=True,
                               caster_changes={'conditions': ['incapacitated']})
    before = dice._rng.getstate()
    result = _step(act, [30, 40, 0])
    assert result.success, result.error
    assert _frame(bridge, 'steed')['position'] == [30, 40, 0]
    assert _frame(bridge, 'caster')['position'] == [30, 40, 6]
    assert result.context['_fey_distance'] == 50
    assert bridge.get_entity('steed')[RIDER_FIELD] == 'caster'
    assert bridge.get_entity('caster')[MOUNT_FIELD] == 'steed'
    assert dice._rng.getstate() == before
    card = project_result_card(bound[1]['tools'][ACTION]['metadata']['result_card'],
        parameters={}, public_context=_json_safe(result.context))
    assert any(row['label'] == 'Rider' and row['value'] == 'Caster' for row in card['summary'])
    assert any(row['label'] == 'Rider' and row['value'] == [30, 40, 6] for row in card['details'])
    if os.environ.get('SRD_LITE_STEED_FEY_CARD'):
        Path(os.environ['SRD_LITE_STEED_FEY_CARD']).write_text(json.dumps(card, indent=2), encoding='utf-8')


@pytest.mark.parametrize('changes,destination,error', [
    ({'steed_changes': {'creature_type': 'celestial'}}, [60, 0, 0], 'Only a Fey'),
    ({'steed_changes': {CHARGE: False}}, [60, 0, 0], 'own Long Rest'),
    ({'steed_changes': {'conditions': ['stunned']}}, [60, 0, 0], 'cannot currently take'),
    ({'caster_changes': {'is_alive': False}}, [60, 0, 0], 'living caster'),
    ({'steed_changes': {'created_active': False}}, [60, 0, 0], 'actual active'),
    ({}, [60.001, 0, 0], 'exceed 60 feet'),
    ({}, [0, 0, 60.001], 'exceed 60 feet'),
    ({}, [10.4, 0, 0], 'unoccupied space'),
    ({'combat': True, 'steed_changes': {'bonus_action_available': False}}, [60, 0, 0], 'already been spent'),
    ({'rider': True, 'rider_changes': {MOUNT_FIELD: 'other'}}, [60, 0, 0], 'actual smaller living rider'),
    ({'rider': True, 'rider_position': [1, 0, 6]}, [60, 0, 0], 'still occupy'),
    ({'rider': True, 'rider_plane': 'ethereal'}, [60, 0, 0], 'same plane'),
    ({'rider': True, 'rider_changes': {'size_rank': 4}}, [60, 0, 0], 'smaller living rider'),
    ({'rider': True, 'geometry': [{**_wall(), 'volume': {'kind': 'box',
        'min': [55, -10, 7], 'max': [65, 10, 9]}}]}, [60, 0, 0], 'unoccupied space'),
])
def test_failed_intent_preserves_both_bodies_rng_charge_and_budget(bound, changes, destination, error):
    bridge, _, act = _fey_case(bound, **changes)
    before, rng_before = _snapshot(bridge), dice._rng.getstate()
    result = _step(act, destination)
    assert not result.success and error in result.error
    assert _snapshot(bridge) == before
    assert dice._rng.getstate() == rng_before


def test_pending_action_and_native_melded_body_guard_precede_spending(bound):
    bridge, _, act = _fey_case(bound)
    bridge.write_field('state', 'pending_srd_action_stack', [{'id': 'actual-pending'}])
    before = _snapshot(bridge)
    result = _step(act)
    assert not result.success and 'pending action' in result.error
    assert _snapshot(bridge) == before
    bridge, _, act = _fey_case(bound)
    scene = deepcopy(bridge.get_entity('state')['spatial_scene'])
    scene['entities'][1]['metadata']['meld_stone_source_id'] = 'real-source'
    bridge.write_field('state', 'spatial_scene', scene)
    before = _snapshot(bridge)
    result = _step(act)
    assert not result.success and 'leave its merged stone' in result.error
    assert _snapshot(bridge) == before


def test_public_schema_uses_only_destination_intent_and_the_owned_hidden_state(bound):
    from sylvia_gm.application.package_runtime import _runtime_parameter_schema, _schemas_agree
    from sylvia_gm.domain.package_library import ToolDeclaration
    from scripts.srd_lite_policy import bind_exposed_operations
    record = {'tools': []}
    assert expose_steed_fey_step(bound[1], record) == ACTION
    declaration = record['tools'][0]
    schema = declaration['input_schema']
    assert set(schema['properties']) == {'steed_state', 'destination', 'lasting_state'}
    assert schema['properties']['lasting_state'].get('x-sylvia-gm-model-hidden') is True
    assert _schemas_agree(_runtime_parameter_schema(bound[0].tools[ACTION]), ToolDeclaration(
        name=ACTION, capability=declaration['capability'], input_schema=schema,
        state_predicates=(), turn_statuses=('open',), writers=('dm',)))
    runtime = deepcopy(bound[1])
    assert bind_exposed_operations(runtime, record) == [ACTION]
    assert runtime['tools'][ACTION]['effects'].pop(0)['require']['condition'] == 'false'
    assert runtime['tools'][ACTION]['effects'] == bound[1]['tools'][ACTION]['effects']


def test_scene_identifiers_can_differ_from_saved_rider_and_mount_entity_ids(bound):
    bridge, _, act = _fey_case(bound, rider=True)
    scene = deepcopy(bridge.get_entity('state')['spatial_scene'])
    scene['entities'][0]['id'] = 'rider-body'
    scene['entities'][1]['id'] = 'steed-body'
    bridge.write_field('state', 'spatial_scene', scene)
    result = _step(act, [30, 40, 0])
    assert result.success, result.error
    assert _frame(bridge, 'steed')['position'] == [30, 40, 0]
    assert _frame(bridge, 'caster')['position'] == [30, 40, 6]
