"""Prove Web contact with an actual current ring source, including its empty centre."""
from copy import deepcopy
import json
import os
import zipfile

import pytest
import yaml

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _calls, _scene_entity

EXPOSE = 'expose_srd_retained_web_fire'
OBSERVE = 'observe_srd_retained_web_fire'


@pytest.fixture(scope='module')
def web_runtime():
    with zipfile.ZipFile(os.environ['SRD_LITE_CANDIDATE']) as archive:
        data = yaml.load(archive.read('packages/' + NAMESPACE + '/runtime/ruleset.yaml'), Loader=yaml.CSafeLoader)
    pool = data.get('parameter_declarations', {})
    for tool in data['tools'].values():
        tool['parameters'] = [deepcopy(pool[row]) if isinstance(row, str) else row for row in tool['parameters']]
    if os.environ.get('SRD_LITE_PATCH_WEB_RING') == '1':
        from scripts.srd_retained_web_fire_v2 import _expose_tool, _observe_tool
        from scripts.srd_spell_magic_zones import _when
        exposed = _expose_tool()
        original_flame = data['tools'][EXPOSE]['effects'][0]
        assert original_flame['condition'] == "ordinary_flame_object_id != ''"
        exposed['effects'] = [_when(original_flame['condition'],
            deepcopy(original_flame['on_true']), exposed['effects'][1:])]
        observed = _observe_tool()
        observed['effects'] += deepcopy(data['tools'][OBSERVE]['effects'][1:])
        data['tools'][EXPOSE], data['tools'][OBSERVE] = exposed, observed
    closure, pending = {}, [EXPOSE, OBSERVE]
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data['tools'][name]
            pending.extend(_calls(closure[name]['effects']))
    subset = {key: value for key, value in data.items() if key not in {'tools', 'procedures'}}
    subset['tools'] = closure
    return RulesetRuntime(ruleset_dict=subset)


def fixture_state(minimum, maximum, *, origin=(0, 0, 0), plane='material', active=True, shape='ring'):
    outer = {'kind': 'cylinder', 'center': list(origin), 'radius': 10, 'height': 20}
    inner = outer | {'radius': 9}
    fire_volume = {'kind': 'annulus', 'outer': outer, 'inner': inner} if shape == 'ring' else {
        'kind': 'box', 'min': [9, -20, 0], 'max': [10, 20, 20]}
    web = {'profile': 'environment', 'mode': 'web', 'spell': 'web', 'caster': 'hero',
        'active': True, 'expires_tick': 100, 'plane': plane, 'sequence': 1, 'web_cell_schema': 1,
        'web_cell_ids': ['cell'], 'web_cells': {'cell': {'source_sequence': 1, 'status': 'intact',
        'volume': {'kind': 'box', 'min': list(minimum), 'max': list(maximum)}}}}
    fire = {'profile': 'environment', 'mode': 'wall_fire', 'spell': 'wall_of_fire', 'caster': 'other',
        'active': active, 'expires_tick': 100, 'plane': 'material', 'sequence': 2, 'volume': fire_volume,
        'origin': list(origin), 'wall_shape': shape,
        'source_sha256': 'f47aa77fef781cc935dd1f3983da93a43c07d667bd51dfd8b4d874425c50c489'}
    scene = {'format': 1, 'map': {'id': 'mass-cure-fixture', 'revision': 0,
        'bounds': {'min': [-100, -100, -100], 'max': [100, 100, 100]},
        'grid': {'cell_size': 5, 'unit': 'ft', 'origin': [-100, -100]},
        'layers': [{'id': 'world', 'min_elevation': -100, 'max_elevation': 100}]},
        'entities': [_scene_entity('hero', [-50, 0, 0]), _scene_entity('other', [-50, 10, 0])],
        'geometry': [], 'terrain': [], 'zones': []}
    return {NAMESPACE + '.state': {'lasting_tick': 10, 'has_web': True, 'has_wall_of_fire': True,
        'has_spell_environments': True, 'persistent_spell_area_ids': ['web-source', 'fire-source'],
        'persistent_spell_areas': {'web-source': web, 'fire-source': fire}, 'spatial_scene': scene},
        NAMESPACE + '.entities': {identity: {'environment_area_id': area,
        'concentration_spell_id': spell, 'concentration_effect_family': 'spell_environment',
        'resources': {'spell_slot_2': 2, 'spell_slot_4': 2}}
        for identity, area, spell in [('hero', 'web-source', 'web'), ('other', 'fire-source', 'wall_of_fire')]},
        NAMESPACE + '.combat': {}}


def run(parsed, state, automatic):
    before_resources = json.dumps(state[NAMESPACE + '.entities'], sort_keys=True)
    before_scene = json.dumps(state[NAMESPACE + '.state']['spatial_scene'], sort_keys=True)
    rng = dice._rng.getstate()
    bridge = AuthorityMemoryStateBridge(NAMESPACE, state)
    def nested(name, params, parent):
        return execute_tool(name, params, parsed.tools, bridge, parsed.ruleset_config,
            parsed.definitions, tool_executor=nested, budget=parent.budget)
    result = execute_tool(OBSERVE if automatic else EXPOSE, {} if automatic else {'fire_area_id': 'fire-source'},
        parsed.tools, bridge, parsed.ruleset_config, parsed.definitions, tool_executor=nested, budget=ExecutionBudget())
    if result.success:
        for mutation in bridge.mutations():
            state[mutation.namespace + '.' + mutation.key] = deepcopy(mutation.value)
    assert json.dumps(state[NAMESPACE + '.entities'], sort_keys=True) == before_resources
    assert json.dumps(state[NAMESPACE + '.state']['spatial_scene'], sort_keys=True) == before_scene
    assert dice._rng.getstate() == rng
    return result


@pytest.mark.parametrize('automatic', [False, True])
@pytest.mark.parametrize(('minimum', 'maximum', 'burns'), [
    ([9, -2.5, 0], [14, 2.5, 5], True),
    ([-2.5, -2.5, 0], [2.5, 2.5, 5], False),
    ([8, 8, 0], [13, 13, 5], False),
    ([4, 4, 0], [9, 9, 5], True),
    ([10, 0, 0], [15, 5, 5], False),
    ([9, -2.5, 20], [14, 2.5, 25], False),
    ([9, -2.5, -5], [14, 2.5, 0], False),
])
def test_ring_burns_only_cells_with_actual_positive_contact(web_runtime, minimum, maximum, burns, automatic):
    state = fixture_state(minimum, maximum)
    result = run(web_runtime, state, automatic)
    assert result.success, result.error
    cell = state[NAMESPACE + '.state']['persistent_spell_areas']['web-source']['web_cells']['cell']
    assert (cell['status'] == 'burning') is burns
    if burns:
        assert cell['burn_end_tick'] == 12 and cell['fire_area_id'] == 'fire-source' and cell['fire_sequence'] == 2
        before = deepcopy(cell)
        again = run(web_runtime, state, automatic)
        assert again.success
        assert state[NAMESPACE + '.state']['persistent_spell_areas']['web-source']['web_cells']['cell'] == before


def test_translated_ring_contact(web_runtime):
    state = fixture_state([29, -32.5, 5], [34, -27.5, 10], origin=(20, -30, 5))
    result = run(web_runtime, state, True)
    assert result.success, result.error
    assert state[NAMESPACE + '.state']['persistent_spell_areas']['web-source']['web_cells']['cell']['status'] == 'burning'


@pytest.mark.parametrize('changes', [{'plane': 'ethereal'}, {'active': False}])
def test_other_plane_or_stale_fire_does_not_burn(web_runtime, changes):
    state = fixture_state([9, -2.5, 0], [14, 2.5, 5], **changes)
    before = deepcopy(state)
    result = run(web_runtime, state, True)
    assert result.success, result.error
    assert state == before


def test_straight_wall_contact_is_preserved(web_runtime):
    state = fixture_state([9, -2.5, 0], [14, 2.5, 5], shape='straight')
    result = run(web_runtime, state, True)
    assert result.success, result.error
    assert state[NAMESPACE + '.state']['persistent_spell_areas']['web-source']['web_cells']['cell']['status'] == 'burning'
