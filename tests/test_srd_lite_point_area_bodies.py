"""Check circular caster range, Sphere membership and actual origin containment."""
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
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _scene_entity, _calls

SPHERE = 'collect_srd_sunburst_creature_targets'
FIRE = 'qualify_srd_lite_fire_storm_cubes'
COVER = 'collect_srd_sunburst_cover_targets'


@pytest.fixture(scope='module')
def bodies_runtime():
    with zipfile.ZipFile(os.environ['SRD_LITE_CANDIDATE']) as archive:
        data = yaml.load(archive.read('packages/' + NAMESPACE + '/runtime/ruleset.yaml'), Loader=yaml.CSafeLoader)
    pool = data.get('parameter_declarations', {})
    for tool in data['tools'].values():
        tool['parameters'] = [deepcopy(pool[item]) if isinstance(item, str) else item for item in tool['parameters']]
    if os.environ.get('POINT_BODIES_PATCH') == '1':
        from scripts.srd_spell_summoning import _area_cover_tools
        from scripts.srd_sunburst_geometry import _sphere_tool, _point_cover
        from scripts.srd_lite_fire_storm_geometry import _qualifier, _placement, PLACE
        data['tools'].update(_area_cover_tools())
        data['tools'].update(_point_cover(data['tools']))
        data['tools'][SPHERE] = _sphere_tool()
        data['tools'][FIRE] = _qualifier()
        data['tools'][PLACE] = _placement()
    closure, pending = {}, [SPHERE, FIRE, COVER]
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data['tools'][name]
            pending.extend(_calls(closure[name]['effects']))
    subset = {key: value for key, value in data.items() if key not in {'tools', 'procedures'}}
    subset['tools'] = closure
    return RulesetRuntime(ruleset_dict=subset)


def circle(identity, position, radius=0.4):
    frame = _scene_entity(identity, position)
    frame['footprint'] = {'kind': 'circle', 'radius': radius}
    return frame


def run(parsed, tool, frames, parameters):
    scene = {'format': 1, 'map': {'id': 'mass-cure-fixture', 'revision': 0,
        'bounds': {'min': [-400, -400, -400], 'max': [400, 400, 400]},
        'grid': {'cell_size': 5, 'unit': 'ft', 'origin': [-400, -400]},
        'layers': [{'id': 'world', 'min_elevation': -400, 'max_elevation': 400}]},
        'entities': frames, 'geometry': [], 'terrain': [], 'zones': []}
    entities = {row['metadata']['participant_ref']: {'name': row['id'], 'kind': 'character',
        'creature_type': 'humanoid', 'is_alive': True, 'hp': 50, 'max_hp': 50,
        'conditions': [], 'resources': {'spell_slot_7': 2, 'spell_slot_8': 2}}
        for row in frames}
    state = {NAMESPACE + '.state': {'spatial_scene': scene, 'lasting_tick': 0, 'turn_effects': []},
             NAMESPACE + '.entities': entities, NAMESPACE + '.combat': {}}
    before, rng = json.dumps(state, sort_keys=True), dice._rng.getstate()
    bridge = AuthorityMemoryStateBridge(NAMESPACE, state)
    def nested(name, values, parent):
        return execute_tool(name, values, parsed.tools, bridge, parsed.ruleset_config,
            parsed.definitions, tool_executor=nested, budget=parent.budget)
    result = execute_tool(tool, parameters, parsed.tools, bridge, parsed.ruleset_config,
        parsed.definitions, tool_executor=nested, budget=ExecutionBudget())
    assert json.dumps(state, sort_keys=True) == before and dice._rng.getstate() == rng
    return result


@pytest.mark.parametrize(('position', 'radius', 'included'), (
    ([60.4, 0, 0], 0.4, True), ([60.41, 0, 0], 0.4, False),
    ([42.8, 42.8, 0], 0.5, False), ([42.8, 42.8, 0], 0.6, True),
    ([0, 0, 60], 0.4, True), ([0, 0, 60.01], 0.4, False),
))
def test_sphere_uses_actual_circle_and_vertical_body(bodies_runtime, position, radius, included):
    result = run(bodies_runtime, SPHERE,
        [_scene_entity('hero', [-100, 0, 0]), circle('recipient', position, radius)],
        {'caster_state': 'hero', 'sunburst_origin': [0, 0, 0]})
    assert result.success, result.error
    assert ('recipient' in result.context['_sunburst_sphere_targets']) is included


@pytest.mark.parametrize('tool', (SPHERE, FIRE))
@pytest.mark.parametrize(('position', 'radius', 'accepted'), (
    ([-150.4, 0, 0], 0.4, True), ([-150.41, 0, 0], 0.4, False),
    ([-106.5, -106.5, 0], 0.5, False), ([0, 0, 0], 0.4, True),
))
def test_circle_caster_range_uses_true_body_and_handles_zero_radial_distance(
        bodies_runtime, tool, position, radius, accepted):
    parameters = {'caster_state': 'hero', 'sunburst_origin': [0, 0, 0]} if tool == SPHERE else {
        'caster_state': 'hero', 'fire_storm_cubes': [
            {'min': [0, 0, 0], 'origin': [0, 0, 0], 'include_origin': True},
            {'min': [0, 0, -10], 'origin': [0, 0, 0], 'include_origin': True}]}
    result = run(bodies_runtime, tool, [circle('hero', position, radius), circle('recipient', [5, 5, 0])], parameters)
    assert result.success is accepted, result.error
    if not accepted:
        assert '150' in result.error


@pytest.mark.parametrize(('position', 'radius', 'recipient', 'cover', 'expected'), (
    ([-3, -4, 0], 5, [5, 5, 0], 'none', ['recipient']),
    ([-1, -1, 0], 1, [-3, -3, 0], 'total', []),
))
def test_cover_excludes_caster_only_when_origin_is_in_its_true_circle(
        bodies_runtime, position, radius, recipient, cover, expected):
    target = circle('recipient', recipient)
    target['metadata']['cover_from'] = {'hero': cover}
    result = run(bodies_runtime, COVER, [circle('hero', position, radius), target],
        {'source_state': 'hero', 'targets': ['recipient'], 'origin': [0, 0, 0]})
    assert result.success, result.error
    assert result.context['_srd_area_targets'] == expected
