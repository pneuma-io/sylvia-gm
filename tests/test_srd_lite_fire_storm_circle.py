"""Check admitted circular bodies at Fire Storm's native area and Cover boundaries."""
from copy import deepcopy
import os
import zipfile

import pytest
import yaml

from scripts.srd_lite_fire_storm_geometry import _qualifier, QUALIFY
from scripts.srd_sunburst_geometry import _point_cover
from scripts.srd_spell_summoning import _area_cover_tools
from sylvia_gm.runtime import RulesetRuntime
from tests.test_srd_lite_fire_storm_geometry import _run, cube, _scene_entity, _calls, NAMESPACE, _wall


@pytest.fixture(scope='module')
def circle_runtime():
    with zipfile.ZipFile(os.environ['SRD_LITE_CANDIDATE']) as archive:
        data = yaml.load(archive.read('packages/' + NAMESPACE + '/runtime/ruleset.yaml'),
                         Loader=yaml.CSafeLoader)
    pool = data.get('parameter_declarations', {})
    for tool in data['tools'].values():
        tool['parameters'] = [deepcopy(pool[item]) if isinstance(item, str) else item
                              for item in tool['parameters']]
    if os.environ.get('FIRE_CIRCLE_PATCH') == '1':
        # Rebuild the raw factory boundary before the package compiler factors calls.
        data['tools'].update(_area_cover_tools())
        data['tools'][QUALIFY] = _qualifier()
        data['tools'].update(_point_cover(data['tools']))
    closure, pending = {}, [QUALIFY]
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data['tools'][name]
            pending.extend(_calls(closure[name]['effects']))
    subset = {key: value for key, value in data.items() if key not in {'tools', 'procedures'}}
    subset['tools'] = closure
    return RulesetRuntime(ruleset_dict=subset), data, {}


@pytest.mark.parametrize(('position', 'radius', 'include_origin', 'expected'), (
    ([5, 5, 0], 0.4, False, ['recipient']),
    ([-3, -4, -6], 5, False, []),
    ([-3, -4, -6], 5, True, ['recipient']),
    ([-3, -4, 0], 5, False, ['recipient']),
    ([-1, -1, 0], 1, True, []),
))
def test_circle_recipient_uses_native_intersection_and_origin_policy(
        circle_runtime, position, radius, include_origin, expected):
    recipient = _scene_entity('recipient', position)
    recipient['footprint'] = {'kind': 'circle', 'radius': radius}
    result = _run(circle_runtime, cubes=[cube(include_origin=include_origin), cube((10, 0, 0))],
                  frames=[recipient])
    assert result.success, result.error
    assert result.context['_fire_storm_targets'] == expected


def test_circle_recipient_retains_actual_solid_cover(circle_runtime):
    recipient = _scene_entity('recipient', [7, 5, 0])
    recipient['footprint'] = {'kind': 'circle', 'radius': 0.4}
    recipient['metadata']['cover_from'] = {'hero': 'total'}
    wall = _wall()
    wall['volume'] = {'kind': 'box', 'min': [4, -2, -10], 'max': [5, 12, 12]}
    result = _run(circle_runtime, cubes=[cube(), cube((0, 0, -10), origin=(0, 0, 0))],
                  frames=[recipient], geometry=[wall])
    assert result.success, result.error
    assert result.context['_fire_storm_targets'] == []
