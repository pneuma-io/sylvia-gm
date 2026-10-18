"""Check live cells and actual burned holes at the shared Web consumer boundaries."""
from copy import deepcopy
from pathlib import Path
import os
import zipfile

import pytest
import yaml

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.domain.authority import thaw_json_value
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _calls

ROOTS = ('resolve_srd_web_body_segment', 'resolve_srd_web_intervals',
         'resolve_srd_environment_point', 'resolve_srd_environment_segment')


@pytest.fixture(scope='module')
def native_holes():
    with AuthorityOwner.open(Path(os.environ['SRD_LITE_WEB_HOLES_DATABASE']), 'web-holes-read-only') as owner:
        snapshot = owner.snapshot('burn-clearance')
    state = {key: thaw_json_value(value) for key, value in snapshot.state.items()}
    cells = state[NAMESPACE + '.state']['persistent_spell_areas']['hero']['web_cells']
    assert sum(cell['status'] == 'destroyed' for cell in cells.values()) == 12
    assert sum(cell['status'] == 'intact' for cell in cells.values()) == 4
    return state


@pytest.fixture(scope='module')
def web_consumers():
    with zipfile.ZipFile(os.environ['SRD_LITE_CANDIDATE']) as archive:
        data = yaml.load(archive.read('packages/' + NAMESPACE + '/runtime/ruleset.yaml'), Loader=yaml.CSafeLoader)
    pool = data.get('parameter_declarations', {})
    closure, pending = {}, list(ROOTS)
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


def query(parsed, state, name, parameters):
    """Exercise the actual generated consumers without rolling or changing entities/cells."""
    bridge = AuthorityMemoryStateBridge(NAMESPACE, state)
    rng = dice._rng.getstate()

    def nested(tool, inputs, parent):
        return execute_tool(tool, inputs, parsed.tools, bridge, parsed.ruleset_config,
            parsed.definitions, tool_executor=nested, budget=parent.budget)

    result = execute_tool(name, parameters, parsed.tools, bridge, parsed.ruleset_config,
        parsed.definitions, tool_executor=nested, budget=ExecutionBudget())
    assert result.success, result.error
    assert dice._rng.getstate() == rng
    for mutation in bridge.mutations():
        if mutation.key == 'entities':
            assert mutation.value == state[NAMESPACE + '.entities']
        if mutation.key == 'state':
            assert mutation.value['persistent_spell_areas']['hero']['web_cells'] == state[NAMESPACE + '.state']['persistent_spell_areas']['hero']['web_cells']
            assert mutation.value['spatial_scene'] == state[NAMESPACE + '.state']['spatial_scene']
    return result.context


@pytest.mark.parametrize(('point', 'live'), [([-8, -8, 0], False), ([0, 0, 0], True)])
def test_body_membership_uses_remaining_cells(web_consumers, native_holes, point, live):
    context = query(web_consumers, native_holes, ROOTS[0],
        {'moving_subject': 'guard', 'area_id': 'hero', 'start': point, 'end': point})
    assert context['_body_hit'] is live


@pytest.mark.parametrize(('point', 'live'), [([-8, -8, 2], False), ([0, 0, 2], True)])
def test_point_obscurity_uses_remaining_cells(web_consumers, native_holes, point, live):
    context = query(web_consumers, native_holes, ROOTS[2], {'point': point, 'plane': 'material'})
    assert context['_environment_lightly_obscured'] is live


@pytest.mark.parametrize(('start', 'end', 'live'), [
    ([-8, -8, 2], [-8, 8, 2], False), ([-8, 0, 2], [8, 0, 2], True)])
def test_sight_segment_uses_remaining_cells(web_consumers, native_holes, start, end, live):
    context = query(web_consumers, native_holes, ROOTS[3], {'start': start, 'end': end, 'plane': 'material'})
    assert context['_environment_lightly_obscured'] is live


def test_body_route_through_burned_column_is_clear(web_consumers, native_holes):
    context = query(web_consumers, native_holes, ROOTS[0],
        {'moving_subject': 'guard', 'area_id': 'hero', 'start': [-8, -8, 0], 'end': [-8, 8, 0]})
    assert context['_body_hit'] is False


def test_body_route_through_intact_centre_still_hits(web_consumers, native_holes):
    context = query(web_consumers, native_holes, ROOTS[0],
        {'moving_subject': 'guard', 'area_id': 'hero', 'start': [-8, 0, 0], 'end': [8, 0, 0]})
    assert context['_body_hit'] is True


def test_burned_column_adds_no_difficult_terrain(web_consumers, native_holes):
    context = query(web_consumers, native_holes, ROOTS[1],
        {'actor_subject': 'guard', 'start': [-8, -8, 0], 'end': [-8, 8, 0]})
    assert context['_web_route_intervals'] == []


def test_terrain_pieces_cover_only_the_intact_centre(web_consumers, native_holes):
    context = query(web_consumers, native_holes, ROOTS[1],
        {'actor_subject': 'guard', 'start': [-8, 0, 0], 'end': [8, 0, 0]})
    assert {(row['entry'], row['exit']) for row in context['_web_route_intervals']} == {
        (0.1875, 0.5), (0.5, 0.8125)}
