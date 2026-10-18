"""Native route geometry checks for the saved Sleet source; no casting or save/load repetition."""
from copy import deepcopy
import json
import os
from pathlib import Path
import zipfile

import pytest
import yaml

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _calls, _scene_entity

SUPPORT = 'qualify_srd_sleet_storm_support'
TERRAIN = 'resolve_srd_sleet_storm_terrain_intervals'


@pytest.fixture(scope='module')
def bound():
    with zipfile.ZipFile(Path(os.environ['SRD_LITE_CANDIDATE'])) as archive:
        data = yaml.load(archive.read('packages/' + NAMESPACE + '/runtime/ruleset.yaml'), Loader=yaml.CSafeLoader)
    pool = data.get('parameter_declarations', {})
    for tool in data['tools'].values():
        tool['parameters'] = [deepcopy(pool[row]) if isinstance(row, str) else row for row in tool['parameters']]
    if os.environ.get('SRD_LITE_PATCH_SLEET_GROUND') == '1':
        from scripts.srd_lite_sleet_terrain import bind_sleet_ground_terrain
        bind_sleet_ground_terrain(data)
    if os.environ.get('SRD_LITE_REQUIRE_SLEET_GENERATED') == '1':
        owner = json.dumps(data['tools'][SUPPORT])
        assert '_sleet_supported_intervals' in owner
        assert 'ground shadows from solids are currently unsupported' not in owner
    closure = {}
    pending = [SUPPORT, TERRAIN]
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data['tools'][name]
            pending.extend(_calls(closure[name]['effects']))
    subset = {key: value for key, value in data.items() if key not in {'tools', 'procedures'}}
    subset['tools'] = closure
    return RulesetRuntime(ruleset_dict=subset)


def solid(identity, minimum, maximum, *, ground=False, active=True, plane='material', movement='blocking'):
    return {'id': identity, 'map_id': 'mass-cure-fixture',
            'active': active, 'movement': movement, 'visibility': 'opaque',
            'volume': {'kind': 'box', 'min': list(minimum), 'max': list(maximum)},
            'metadata': {'plane': plane, 'ground': ground},
            'destroyed': {'active': False, 'movement': 'passable', 'visibility': 'clear'}}


def run(bound, start, end, *, origin=(0, 0, 0), geometry=None, terrain=False):
    geometry = geometry if geometry is not None else [solid('floor', (-50, -50, -2), (50, 50, 0), ground=True)]
    scene = {'format': 1, 'map': {'id': 'mass-cure-fixture', 'revision': 0,
        'bounds': {'min': [-200, -200, -200], 'max': [200, 200, 200]},
        'grid': {'cell_size': 5, 'unit': 'ft', 'origin': [-200, -200]},
        'layers': [{'id': 'world', 'min_elevation': -200, 'max_elevation': 200}]},
        'entities': [_scene_entity('hero', [0, -30, 0])], 'geometry': geometry, 'terrain': [], 'zones': []}
    source = {'mode': 'sleet', 'plane': 'material', 'origin': list(origin), 'radius': 20,
              'cylinder_min': origin[2], 'cylinder_max': origin[2] + 40, 'profile': 'environment',
              'spell': 'sleet_storm', 'active': True, 'caster': 'hero', 'expires_tick': 10,
              'volume': {'kind': 'cylinder', 'center': list(origin), 'radius': 20, 'height': 40}}
    state = {NAMESPACE + '.state': {'spatial_scene': scene, 'persistent_spell_areas': {'storm': source},
                                  'has_spell_environments': True, 'persistent_spell_area_ids': ['storm'],
                                  'lasting_tick': 0},
             NAMESPACE + '.entities': {'hero': {'name': 'Hero', 'resources': {'spell_slot_3': 2},
                'environment_area_id': 'storm', 'concentration_spell_id': 'sleet_storm',
                'concentration_effect_family': 'spell_environment'}},
             NAMESPACE + '.combat': {}}
    before = json.dumps(state, sort_keys=True)
    rng = dice._rng.getstate()
    bridge = AuthorityMemoryStateBridge(NAMESPACE, state)
    def nested(name, parameters, parent):
        return execute_tool(name, parameters, bound.tools, bridge, bound.ruleset_config,
                            bound.definitions, tool_executor=nested, budget=parent.budget)
    parameters = {'start': list(start), 'end': list(end)}
    parameters.update({'actor_subject': 'hero'} if terrain else {'area_id': 'storm'})
    result = execute_tool(TERRAIN if terrain else SUPPORT, parameters,
        bound.tools, bridge, bound.ruleset_config, bound.definitions,
        tool_executor=nested, budget=ExecutionBudget())
    assert json.dumps(state, sort_keys=True) == before
    assert dice._rng.getstate() == rng
    return result


def assert_ranges(result, expected):
    assert result.success, result.error
    intervals = result.context.get('_sleet_supported_intervals')
    if intervals is None and os.environ.get('SRD_LITE_PATCH_SLEET_GROUND') != '1':
        # The retained caller exports the whole crossing when the old qualifier succeeds.
        intervals = [{'entry': 0, 'exit': 1}]
    assert intervals is not None
    actual = sorted((row['entry'], row['exit']) for row in intervals)
    if len(actual) != len(expected):
        print(json.dumps({key: result.context.get(key) for key in
            ('_sleet_ground_intervals', '_sleet_shadows', '_sleet_supported_intervals')}, sort_keys=True))
    assert len(actual) == len(expected), actual
    for got, wanted in zip(actual, expected):
        assert got == pytest.approx(wanted, abs=1e-9)


def test_clear_actual_ground(bound):
    assert_ranges(run(bound, (5, -5, 0), (5, 5, 0)), [(0, 1)])


def test_unrelated_column_does_not_reject_or_change_cost(bound):
    geometry = [solid('floor', (-50, -50, -2), (50, 50, 0), ground=True),
                solid('column', (-10, -10, 0), (-8, -8, 10))]
    assert_ranges(run(bound, (5, -5, 0), (5, 5, 0), geometry=geometry), [(0, 1)])


def test_column_shadow_splits_exposed_route(bound):
    geometry = [solid('floor', (-50, -50, -2), (50, 50, 0), ground=True),
                solid('column', (4, -1, -1), (6, 1, 10))]
    assert_ranges(run(bound, (10, -5, 0), (10, 5, 0), geometry=geometry), [(0, .25), (.75, 1)])


def test_wall_starting_at_ground_blocks_the_ground_behind_it(bound):
    geometry = [solid('floor', (-50, -50, -2), (50, 50, 0), ground=True),
                solid('column', (4, -1, 0), (6, 1, 10))]
    assert_ranges(run(bound, (10, -5, 0), (10, 5, 0), geometry=geometry), [(0, .25), (.75, 1)])


def test_route_before_column_is_exposed(bound):
    geometry = [solid('floor', (-50, -50, -2), (50, 50, 0), ground=True),
                solid('column', (10, -1, -1), (12, 1, 10))]
    assert_ranges(run(bound, (5, -1, 0), (5, 1, 0), geometry=geometry), [(0, 1)])


def test_ray_entering_through_a_box_corner_is_blocked(bound):
    geometry = [solid('floor', (-50, -50, -2), (50, 50, 0), ground=True),
                solid('column', (4, 4, 0), (6, 6, 10))]
    assert_ranges(run(bound, (10, 8, 0), (10, 12, 0), geometry=geometry), [])


@pytest.mark.parametrize('extra', [
    {'active': False}, {'plane': 'ethereal'}, {'movement': 'passable'}])
def test_inactive_other_plane_or_nonblocking_geometry_does_not_cast_shadow(bound, extra):
    geometry = [solid('floor', (-50, -50, -2), (50, 50, 0), ground=True),
                solid('column', (4, -1, -1), (6, 1, 10), **extra)]
    assert_ranges(run(bound, (10, -5, 0), (10, 5, 0), geometry=geometry), [(0, 1)])


def test_adjacent_ground_faces_contribute_both_halves(bound):
    geometry = [solid('left', (-20, -20, -2), (0, 20, 0), ground=True),
                solid('right', (0, -20, -2), (20, 20, 0), ground=True)]
    assert_ranges(run(bound, (-10, 10, 0), (10, 10, 0), geometry=geometry), [(0, .5), (.5, 1)])


def test_ground_gap_is_excluded_from_difficult_ground(bound):
    geometry = [solid('left', (-20, -20, -2), (-2, 20, 0), ground=True),
                solid('right', (2, -20, -2), (20, 20, 0), ground=True)]
    assert_ranges(run(bound, (-10, 10, 0), (10, 10, 0), geometry=geometry), [(0, .4), (.6, 1)])


def test_overlapping_shadows_are_subtracted_once(bound):
    geometry = [solid('floor', (-50, -50, -2), (50, 50, 0), ground=True),
                solid('column', (4, -1, -1), (6, 1, 10)),
                solid('column-2', (4, 0, -1), (6, 2, 10))]
    assert_ranges(run(bound, (10, -5, 0), (10, 5, 0), geometry=geometry), [(0, .25)])


def test_top_origin_projects_box_shadow_onto_lower_ground(bound):
    geometry = [solid('floor', (-50, -50, -2), (50, 50, 0), ground=True),
                solid('raised-block', (4, -1, 4), (6, 1, 6))]
    assert_ranges(run(bound, (10, -5, 0), (10, 5, 0), origin=(0, 0, 10), geometry=geometry),
                  [(0, .25), (.75, 1)])


def test_route_grazing_solid_top_is_exposed(bound):
    geometry = [solid('floor', (-50, -50, -2), (50, 50, 0), ground=True),
                solid('buried-block', (4, -1, -4), (6, 1, 0))]
    assert_ranges(run(bound, (10, -5, 0), (10, 5, 0), geometry=geometry), [(0, 1)])


def test_elevated_ground_at_actual_route_height_is_counted(bound):
    geometry = [solid('platform', (-20, -20, 2), (20, 20, 4), ground=True)]
    assert_ranges(run(bound, (5, -5, 4), (5, 5, 4), origin=(0, 0, 10), geometry=geometry), [(0, 1)])


def test_origin_on_platform_top_cannot_reach_ground_through_the_platform(bound):
    geometry = [solid('floor', (-50, -50, -2), (50, 50, 0), ground=True),
                solid('platform', (-2, -2, 2), (2, 2, 10), ground=True)]
    assert_ranges(run(bound, (5, -5, 0), (5, 5, 0), origin=(0, 0, 10), geometry=geometry), [])


def test_air_above_ground_is_not_difficult_ground(bound):
    assert_ranges(run(bound, (5, -5, 5), (5, 5, 5)), [])


def test_origin_inside_solid_rejects_without_mutation(bound):
    geometry = [solid('column', (-2, -2, -2), (2, 2, 10))]
    result = run(bound, (10, -5, 0), (10, 5, 0), geometry=geometry)
    assert not result.success
    assert 'origin' in str(result.error)


def test_retained_terrain_owner_remaps_the_clipped_cylinder_crossing(bound):
    geometry = [solid('floor', (-50, -50, -2), (50, 50, 0), ground=True),
                solid('unrelated-column', (-10, -10, 0), (-8, -8, 10))]
    result = run(bound, (-30, 0, 0), (30, 0, 0), geometry=geometry, terrain=True)
    assert result.success, result.error
    intervals = result.context['_sleet_route_intervals']
    assert len(intervals) == 1
    assert intervals[0]['area_id'] == 'storm'
    assert (intervals[0]['entry'], intervals[0]['exit']) == pytest.approx((1 / 6, 5 / 6))


def test_retained_terrain_owner_exports_only_exposed_ground(bound):
    geometry = [solid('floor', (-50, -50, -2), (50, 50, 0), ground=True),
                solid('column', (4, -1, 0), (6, 1, 10))]
    result = run(bound, (10, -5, 0), (10, 5, 0), geometry=geometry, terrain=True)
    assert result.success, result.error
    actual = result.context['_sleet_route_intervals']
    assert len(actual) == 2
    for row, expected in zip(actual, [(0, .25), (.75, 1)]):
        assert row['area_id'] == 'storm'
        assert (row['entry'], row['exit']) == pytest.approx(expected)
