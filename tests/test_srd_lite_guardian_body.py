"""Prove actual Guardian radius for admitted bodies without casts, RNG or state writes."""
from copy import deepcopy
import os
import zipfile

import pytest
import yaml

from sylvia_gm.runtime import RulesetRuntime
from tests.test_srd_lite_vertical_cones import run, state_for
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _calls, _scene_entity

TOOL = 'measure_srd_guardian_distance'


@pytest.fixture(scope='module')
def guardian_body():
    with zipfile.ZipFile(os.environ['SRD_LITE_CANDIDATE']) as archive:
        data = yaml.load(archive.read('packages/' + NAMESPACE + '/runtime/ruleset.yaml'), Loader=yaml.CSafeLoader)
    if os.environ.get('SRD_LITE_PATCH_GUARDIAN_BODY') == '1':
        from scripts.srd_lite_guardian_body import bind_guardian_recipient_body
        bind_guardian_recipient_body(data)
    pool = data.get('parameter_declarations', {})
    closure, pending = {}, [TOOL]
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


def guardian_state(position, footprint):
    state = state_for(position)
    scene = state[NAMESPACE + '.state']['spatial_scene']
    target = next(row for row in scene['entities'] if row['id'] == 'guard')
    target['footprint'] = footprint
    guardian = _scene_entity('faith_guardian', [0, 0, 0])
    guardian.update(height=10, footprint={'kind': 'box', 'half_extents': [5, 5]})
    guardian['metadata'].update(creature_type='spell_force')
    scene['entities'].append(guardian)
    return state


@pytest.mark.parametrize(('position', 'distance_squared'), [
    ([17, 0, 0], 100), ([17.01, 0, 0], 100.2001),
    ([13, 11, 0], 64), ([14, 17, 0], 169),
    ([17, 0, 16], 136), ([0, 0, 16], 36),
    ([4, 4, 0], 0), ([-17, 0, 0], 100),
])
def test_circle_uses_true_radius_and_height(guardian_body, position, distance_squared):
    state = guardian_state(position, {'kind': 'circle', 'radius': 2})
    result = run(guardian_body, state, TOOL, {'force_id': 'faith_guardian', 'target_state': 'guard'})
    assert result.success, result.error
    assert result.context['_guardian_distance_squared'] == pytest.approx(distance_squared, abs=1e-10)
    assert result.context['_guardian_target_plane_matches']
    assert result.context['_guardian_observer'] == [0, 0, 5]
    assert result.context['_guardian_target_scene']['footprint'] == {'kind': 'circle', 'radius': 2}


@pytest.mark.parametrize(('position', 'distance_squared'), [
    ([17, 0, 0], 100), ([13, 11, 0], 52), ([17, 0, 16], 136), ([4, 4, 0], 0),
])
def test_original_box_distance_preserved(guardian_body, position, distance_squared):
    state = guardian_state(position, {'kind': 'box', 'half_extents': [2, 2]})
    result = run(guardian_body, state, TOOL, {'force_id': 'faith_guardian', 'target_state': 'guard'})
    assert result.success, result.error
    assert result.context['_guardian_distance_squared'] == pytest.approx(distance_squared, abs=1e-10)


def test_actual_plane_mismatch_is_preserved(guardian_body):
    state = guardian_state([17, 0, 0], {'kind': 'circle', 'radius': 2})
    state[NAMESPACE + '.state']['spatial_scene']['entities'][1]['metadata']['plane'] = 'ethereal'
    result = run(guardian_body, state, TOOL, {'force_id': 'faith_guardian', 'target_state': 'guard'})
    assert result.success, result.error
    assert not result.context['_guardian_target_plane_matches']


def test_non_box_guardian_is_not_fabricated(guardian_body):
    state = guardian_state([17, 0, 0], {'kind': 'circle', 'radius': 2})
    state[NAMESPACE + '.state']['spatial_scene']['entities'][-1]['footprint'] = {'kind': 'circle', 'radius': 5}
    result = run(guardian_body, state, TOOL, {'force_id': 'faith_guardian', 'target_state': 'guard'})
    assert not result.success and 'Guardian range' in result.error
