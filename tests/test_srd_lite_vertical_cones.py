"""Check vertical Cone membership, source faces, cap boundaries and physical Cover."""
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
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _calls, _scene_entity, _wall

COLLECT = 'collect_srd_cone_creature_targets'
COVER = 'collect_srd_spell_area_cover_targets'


@pytest.fixture(scope='module')
def cones():
    with zipfile.ZipFile(os.environ['SRD_LITE_CANDIDATE']) as archive:
        data = yaml.load(archive.read('packages/' + NAMESPACE + '/runtime/ruleset.yaml'), Loader=yaml.CSafeLoader)
    pool = data.get('parameter_declarations', {})
    if os.environ.get('SRD_LITE_PATCH_VERTICAL_CONES') == '1':
        from scripts.srd_cone_qualification import cone_tools
        data['tools'].update(cone_tools())
    closure, pending = {}, [COLLECT, COVER]
    while pending:
        name = pending.pop()
        if name not in closure:
            tool = deepcopy(data['tools'][name])
            tool['parameters'] = [deepcopy(pool[row]) if isinstance(row, str) else row for row in tool['parameters']]
            closure[name] = tool
            pending.extend(_calls(tool['effects']))
    subset = {key: value for key, value in data.items() if key not in {'tools', 'procedures'}}
    subset['tools'] = closure
    return RulesetRuntime(ruleset_dict=subset)


def state_for(position, geometry=()):
    frames = [_scene_entity('hero', [0, 0, 0]), _scene_entity('guard', position)]
    scene = {'format': 1, 'map': {'id': 'mass-cure-fixture', 'revision': 0,
        'bounds': {'min': [-100, -100, -100], 'max': [100, 100, 100]},
        'grid': {'cell_size': 5, 'unit': 'ft', 'origin': [-100, -100]},
        'layers': [{'id': 'world', 'min_elevation': -100, 'max_elevation': 100}]},
        'entities': frames, 'geometry': list(geometry), 'terrain': [], 'zones': []}
    actors = {identity: {'name': identity, 'kind': 'character', 'creature_type': 'humanoid',
        'is_alive': True, 'hp': 50, 'max_hp': 50, 'conditions': [], 'resources': {'spell_slot_1': 2}}
        for identity in ('hero', 'guard')}
    return {NAMESPACE + '.state': {'spatial_scene': scene, 'lasting_tick': 0, 'turn_effects': []},
            NAMESPACE + '.entities': actors, NAMESPACE + '.combat': {}}


def run(parsed, state, tool, parameters):
    """Use the real generated qualifier while preserving state and random-number state."""
    before = json.dumps(state, sort_keys=True)
    rng = dice._rng.getstate()
    bridge = AuthorityMemoryStateBridge(NAMESPACE, state)

    def nested(name, values, parent):
        return execute_tool(name, values, parsed.tools, bridge, parsed.ruleset_config,
            parsed.definitions, tool_executor=nested, budget=parent.budget)

    result = execute_tool(tool, parameters, parsed.tools, bridge, parsed.ruleset_config,
        parsed.definitions, tool_executor=nested, budget=ExecutionBudget())
    after = deepcopy(state)
    if result.success:
        for mutation in bridge.mutations():
            after[mutation.namespace + '.' + mutation.key] = deepcopy(mutation.value)
    assert json.dumps(after, sort_keys=True) == before
    assert dice._rng.getstate() == rng
    return result


@pytest.mark.parametrize(('position', 'direction', 'included'), [
    ([0, 0, 10], [0, 0, 2], True),
    ([0, 0, 21], [0, 0, 1], True),
    ([0, 0, 21.01], [0, 0, 1], False),
    ([5.9, 5.9, 21], [0, 0, 1], False),
    ([5.6, 5.6, 21], [0, 0, 1], True),
    ([0, 0, -12], [0, 0, -3], True),
    ([0, 0, -21.01], [0, 0, -1], False),
    ([0, 0, 10], [0, 0, -1], False),
])
def test_vertical_membership_uses_actual_box_body(cones, position, direction, included):
    result = run(cones, state_for(position), COLLECT,
        {'source_state': 'hero', 'length_feet': 15, 'direction': direction})
    assert result.success, result.error
    assert ('guard' in result.context['_srd_cone_targets']) is included
    assert 'hero' not in result.context['_srd_cone_targets']
    assert result.context['_srd_cone_origin'] == [0, 0, 6 if direction[2] > 0 else 0]
    assert result.context['_srd_cone_axis'] == []


@pytest.mark.parametrize(('position', 'direction', 'included'), [
    ([10, 0, 0], [1, 0], True), ([-10, 0, 0], [1, 0], False),
    ([5, 5, 0], [1, 1], True), ([5, -5, 0], [1, 1], False)])
def test_existing_horizontal_directions_are_preserved(cones, position, direction, included):
    result = run(cones, state_for(position), COLLECT,
        {'source_state': 'hero', 'length_feet': 15, 'direction': direction})
    assert result.success, result.error
    assert ('guard' in result.context['_srd_cone_targets']) is included
    assert len(result.context['_srd_cone_axis']) == 2


def test_selected_vertical_apex_stays_in_the_actual_source(cones):
    result = run(cones, state_for([0, 0, 19]), COLLECT,
        {'source_state': 'hero', 'length_feet': 15, 'direction': [0, 0, 1], 'apex': [0.2, 0.2, 4]})
    assert result.success, result.error
    assert result.context['_srd_cone_origin'] == [0.2, 0.2, 4]
    assert 'guard' in result.context['_srd_cone_targets']


@pytest.mark.parametrize(('direction', 'apex'), [
    ([1, 0, 1], []), ([0, 0, 0], []), ([0, 0, 1], [0, 0, 7])])
def test_unsupported_or_invalid_intent_fails_before_mutation(cones, direction, apex):
    result = run(cones, state_for([0, 0, 10]), COLLECT,
        {'source_state': 'hero', 'length_feet': 15, 'direction': direction, 'apex': apex})
    assert not result.success


def test_non_creature_scenery_is_not_a_breath_target(cones):
    state = state_for([0, 0, 10])
    state[NAMESPACE + '.state']['spatial_scene']['entities'][1]['metadata']['is_object'] = True
    result = run(cones, state, COLLECT,
        {'source_state': 'hero', 'length_feet': 15, 'direction': [0, 0, 1]})
    assert result.success, result.error
    assert result.context['_srd_cone_targets'] == []


@pytest.mark.parametrize(('minimum', 'maximum', 'visible'), [
    ([-2, -2, 8], [2, 2, 9], False), ([10, 10, 8], [12, 12, 9], True)])
def test_vertical_cover_uses_actual_overhead_blocker(cones, minimum, maximum, visible):
    solid = _wall()
    solid.update(id='slab', volume={'kind': 'box', 'min': minimum, 'max': maximum})
    state = state_for([0, 0, 10], [solid])
    state[NAMESPACE + '.state']['spatial_scene']['entities'][1]['metadata']['cover_from'] = {
        'hero': 'none' if visible else 'total'}
    selected = run(cones, state, COLLECT, {'source_state': 'hero', 'length_feet': 15, 'direction': [0, 0, 1]})
    assert selected.success, selected.error
    result = run(cones, state, COVER, {'source_state': 'hero', 'targets': selected.context['_srd_cone_targets'],
        'origin': selected.context['_srd_cone_origin'], 'axis': selected.context['_srd_cone_axis'],
        'source_frames': [selected.context['_srd_cone_source']]})
    assert result.success, result.error
    assert ('guard' in result.context['_srd_area_targets']) is visible


def test_overhead_obstruction_without_cover_facts_fails_cleanly(cones):
    solid = _wall()
    solid.update(id='slab', volume={'kind': 'box', 'min': [-2, -2, 8], 'max': [2, 2, 9]})
    state = state_for([0, 0, 10], [solid])
    selected = run(cones, state, COLLECT, {'source_state': 'hero', 'length_feet': 15, 'direction': [0, 0, 1]})
    assert selected.success, selected.error
    result = run(cones, state, COVER, {'source_state': 'hero', 'targets': selected.context['_srd_cone_targets'],
        'origin': selected.context['_srd_cone_origin'], 'axis': selected.context['_srd_cone_axis'],
        'source_frames': [selected.context['_srd_cone_source']]})
    assert not result.success and 'categorical Cover' in result.error
