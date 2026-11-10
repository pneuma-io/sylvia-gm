"""Version-bound SRD category/relationship rules over real admitted spatial query effects."""
import os
import zipfile
from copy import deepcopy

import pytest

from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget
from scripts.srd_timed_actor_defenses import _load_runtime
from tests.srd_lite_mass_cure_geometry_fixtures import _calls

NS = 'sylvia.srd-lite.bg3'


@pytest.fixture(scope='module')
def runtime():
    with zipfile.ZipFile(os.environ['SRD_LITE_CANDIDATE']) as archive:
        data = _load_runtime(archive.read('packages/' + NS + '/runtime/ruleset.yaml').decode())
    pending = ['collect_srd_creature_space_intervals',
               'srd_information_movement_cost']
    tools = {}
    while pending:
        name = pending.pop()
        if name not in tools:
            tools[name] = data['tools'][name]
            pending.extend(_calls(tools[name]['effects']))
    return RulesetRuntime(ruleset_dict={**{k: v for k, v in data.items() if k not in {'tools', 'procedures'}},
                                       'tools': tools})


def state(mover=2, other=2, *, conditions=(), ally=False, friendly=False, position=(25, 0, 0)):
    actors = {
        'hero': {'size_rank': mover, 'conditions': [], 'ally_state_ids': ['guard'] if ally else []},
        'guard': {'size_rank': other, 'conditions': list(conditions),
                  'attitudes': {'hero': 'friendly'} if friendly else {}},
    }
    entities = [{
        'id': key, 'map_id': 'space', 'position': list(point),
        'footprint': {'kind': 'box', 'half_extents': [0.4, 0.4]}, 'height': 6, 'facing': [1, 0],
        'active': True, 'movement': 'blocking', 'visibility': 'opaque',
        'metadata': {'participant_ref': key, 'plane': 'material'},
    } for key, point in [('hero', (0, 0, 0)), ('guard', position)]]
    return {NS + '.entities': actors, NS + '.state': {'spatial_scene': {
        'format': 1, 'map': {'id': 'space', 'revision': 0,
            'bounds': {'min': [-100, -100, -100], 'max': [100, 100, 100]},
            'grid': {'cell_size': 5, 'unit': 'ft', 'origin': [-100, -100]},
            'layers': [{'id': 'world', 'min_elevation': -100, 'max_elevation': 100}]},
        'entities': entities, 'geometry': [], 'zones': [], 'terrain': [],
    }}}


def run(runtime, values, *, destination=(50, 0, 0), endpoint=True, tool='collect_srd_creature_space_intervals', player_view=False, parameters=None):
    bridge = AuthorityMemoryStateBridge(NS, values, player_spatial_view=player_view)
    def nested(name, parameters, parent):
        return execute_tool(name, parameters, runtime.tools, bridge, runtime.ruleset_config,
                            runtime.definitions, combat_state=parent.combat_state,
                            tool_executor=nested, budget=parent.budget)
    params = parameters or {'actor_state': 'hero'}
    if tool == 'collect_srd_creature_space_intervals':
        params.update(destination=list(destination), endpoint=endpoint)
    result = execute_tool(tool, params, runtime.tools, bridge, runtime.ruleset_config, runtime.definitions,
                          combat_state=bridge.combat_state, tool_executor=nested, budget=ExecutionBudget())
    return result, bridge


@pytest.mark.parametrize('mover', range(6))
@pytest.mark.parametrize('other', range(6))
def test_all_category_pairs_follow_521_passage_rule(runtime, mover, other):
    result, bridge = run(runtime, state(mover, other))
    allowed = other == 0 or abs(mover - other) >= 2
    assert result.success == allowed, result.error
    if allowed:
        assert bool(result.context['_space_intervals']) == (other != 0)
    assert not bridge.mutations()


@pytest.mark.parametrize('condition', ['incapacitated', 'paralyzed', 'petrified', 'stunned', 'unconscious'])
def test_incapacitated_and_including_conditions_allow_costly_passage(runtime, condition):
    result, _ = run(runtime, state(conditions=[condition]))
    assert result.success, result.error
    assert result.context['_space_intervals'] == [{'entry': .4, 'exit': .6}]


def test_genuine_ally_is_free_but_friendly_attitude_does_not_make_ally(runtime):
    result, _ = run(runtime, state(ally=True))
    assert result.success and not result.context['_space_intervals']
    result, _ = run(runtime, state(friendly=True))
    assert not result.success and 'passage' in result.error


def test_visible_creature_keeps_occupied_interval_in_player_preview(runtime):
    values = state(conditions=['incapacitated'])
    for body in values[NS + '.state']['spatial_scene']['entities']:
        body['metadata']['player'] = {'label': body['id'], 'kind': 'unit'}
    result, _ = run(runtime, values, player_view=True)
    assert result.success, result.error
    assert result.context['_space_intervals'] == [{'entry': .4, 'exit': .6}]


@pytest.mark.parametrize('player_view', [False, True])
def test_cost_owner_counts_visible_creature_in_both_views(runtime, player_view):
    values = state(conditions=['incapacitated'])
    for body in values[NS + '.state']['spatial_scene']['entities']:
        body['metadata']['player'] = {'label': body['id'], 'kind': 'unit'}
    result, _ = run(runtime, values, player_view=player_view, tool='srd_information_movement_cost',
                    parameters={'start': [0, 0, 0], 'end': [50, 0, 0], 'actor_subject': 'hero',
                                'base_difficult': False, 'plane': 'material'})
    assert result.success, result.error
    assert result.context['_route_cost'] == 60


@pytest.mark.parametrize('rank,ally', [(0, False), (2, True), (4, False)])
def test_permitted_passage_never_authorizes_willing_occupied_endpoint(runtime, rank, ally):
    result, bridge = run(runtime, state(other=rank, ally=ally), destination=(25, 0, 0))
    assert not result.success and 'cannot end' in result.error
    assert not bridge.mutations()


def test_intermediate_segment_and_vertical_contact_are_distinct(runtime):
    result, _ = run(runtime, state(ally=True), destination=(25, 0, 0), endpoint=False)
    assert result.success
    result, _ = run(runtime, state(position=(25, 0, 6)))
    assert result.success and not result.context['_space_intervals']


@pytest.mark.parametrize('rank', [None, -1, 6, True, 'Medium'])
def test_absent_or_invalid_category_never_defaults_to_medium(runtime, rank):
    values = state(other=rank)
    before = deepcopy(values)
    result, bridge = run(runtime, values)
    assert not result.success
    assert not bridge.mutations() and values == before
