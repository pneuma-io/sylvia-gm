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
    pending = ['srd_information_movement_cost']
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
