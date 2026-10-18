"""Check actual near-side placement and unchanged payment/RNG before the paid family."""
from copy import deepcopy
import json
import os
from pathlib import Path
import zipfile

import pytest
import yaml

from tests.test_native_followups import make_case

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _scene_entity, _wall, _calls

QUALIFY = 'qualify_srd_lite_fire_storm_cubes'
PLACE = 'place_srd_lite_fire_storm_cubes'

@pytest.fixture(scope='module')
def bound():
    with zipfile.ZipFile(Path(os.environ['SRD_LITE_CANDIDATE'])) as archive:
        data = yaml.load(archive.read('packages/' + NAMESPACE + '/runtime/ruleset.yaml'), Loader=yaml.CSafeLoader)
    pool = data.get('parameter_declarations', {})
    for tool in data['tools'].values():
        tool['parameters'] = [deepcopy(pool[row]) if isinstance(row, str) else row for row in tool['parameters']]
    if os.environ.get('FIRE_STORM_PATCH_PLACEMENT') == '1':
        from scripts.srd_lite_fire_storm_geometry import _qualifier, _placement
        from scripts.srd_sunburst_geometry import _point_cover
        data['tools'][QUALIFY], data['tools'][PLACE] = _qualifier(), _placement()
        data['tools'].update(_point_cover(data['tools']))
        from scripts.srd_sunburst_geometry import AREA_ORIGIN, _area_origin
        data['tools'][AREA_ORIGIN] = _area_origin()
    if os.environ.get('FIRE_STORM_REQUIRE_GENERATED_PLACEMENT') == '1':
        assert PLACE in data['tools']
    closure, pending = {}, [QUALIFY]
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data['tools'][name]
            pending.extend(_calls(closure[name]['effects']))
    subset = {key: value for key, value in data.items() if key not in {'tools', 'procedures'}}
    subset['tools'] = closure
    return RulesetRuntime(ruleset_dict=subset)

def cube(minimum, origin, *, unseen=None):
    value = {'min': list(minimum), 'origin': list(origin), 'include_origin': True}
    if unseen is not None:
        value['origin_unseen'] = unseen
    return value

def wall(identity='wall', minimum=(4, -20, -20), maximum=(5, 20, 20), **metadata):
    result = _wall()
    result['id'] = identity
    result['volume'] = {'kind': 'box', 'min': list(minimum), 'max': list(maximum)}
    result['metadata'].update(metadata)
    return result

def run(bound, cubes, *, geometry=None, positions=None):
    rows = [_scene_entity('hero', [-10, 0, 0]),
            *[_scene_entity(name, list(point)) for name, point in
              (positions or {'near': [0, 5, 0], 'far': [8, 5, 0]}).items()]]
    for frame in rows[1:]:
        frame['metadata']['cover_from'] = {'hero': 'none'}
    scene = {'format': 1, 'map': {'id': 'mass-cure-fixture', 'revision': 0,
        'bounds': {'min': [-200, -200, -200], 'max': [200, 200, 200]},
        'grid': {'cell_size': 5, 'unit': 'ft', 'origin': [-200, -200]},
        'layers': [{'id': 'world', 'min_elevation': -200, 'max_elevation': 200}]},
        'entities': rows, 'geometry': [] if geometry is None else geometry,
        'terrain': [], 'zones': []}
    state = {NAMESPACE + '.state': {'lasting_tick': 0, 'turn_effects': [], 'spatial_scene': scene},
             NAMESPACE + '.entities': {row['id']: {'name': row['id'], 'kind': 'character',
                'is_alive': True, 'conditions': [], 'resources': {'spell_slot_7': 2}}
                for row in rows}, NAMESPACE + '.combat': {}}
    before = json.dumps(state, sort_keys=True)
    original_intent = deepcopy(cubes)
    rng = dice._rng.getstate()
    bridge = AuthorityMemoryStateBridge(NAMESPACE, state)
    def nested(name, parameters, parent):
        return execute_tool(name, parameters, bound.tools, bridge, bound.ruleset_config,
            bound.definitions, tool_executor=nested, budget=parent.budget)
    result = execute_tool(QUALIFY, {'caster_state': 'hero', 'fire_storm_cubes': cubes},
        bound.tools, bridge, bound.ruleset_config, bound.definitions,
        tool_executor=nested, budget=ExecutionBudget())
    assert json.dumps(state, sort_keys=True) == before
    assert cubes == original_intent
    assert dice._rng.getstate() == rng
    return result

def shared_origin(unseen=True):
    return [cube((0, 0, 0), (10, 0, 0), unseen=unseen),
            cube((0, 0, -10), (10, 0, 0), unseen=unseen)]

def test_unseen_origin_forms_on_near_side_and_targets_actual_cubes(bound):
    result = run(bound, shared_origin(), geometry=[wall()])
    assert result.success, result.error
    assert result.context['_fire_origins_relocated'] == 2
    assert result.context['_fire_actual_cubes'][0]['origin'] == [4, 0, 0]
    assert result.context['_fire_actual_cubes'][0]['min'] == [-6, 0, 0]
    assert result.context['_fire_storm_targets'] == ['near']
    assert len(result.context['_fire_origin_rows']) == 2
    assert 'formed' in result.context['_fire_origin_rows'][0]['value']

@pytest.mark.parametrize('unseen', (False, None))
def test_seen_or_unspecified_blocked_origin_rejects_without_payment(bound, unseen):
    result = run(bound, shared_origin(unseen), geometry=[wall()])
    assert not result.success
    assert 'solid barrier blocks' in result.error

def test_first_same_plane_solid_wins_even_when_geometry_is_reversed(bound):
    result = run(bound, shared_origin(), geometry=[
        wall('far-wall', (7, -20, -20), (8, 20, 20)), wall()])
    assert result.success, result.error
    assert result.context['_fire_actual_cubes'][0]['origin'] == [4, 0, 0]

def test_passable_and_other_plane_geometry_do_not_relocate(bound):
    passable = wall('passable', (1, -20, -20), (2, 20, 20))
    passable['movement'] = 'passable'
    result = run(bound, shared_origin(), geometry=[
        passable, wall('ethereal', (3, -20, -20), (4, 20, 20), plane='ethereal')])
    assert result.success, result.error
    assert result.context['_fire_origins_relocated'] == 0
    assert result.context['_fire_actual_cubes'][0]['origin'] == [10, 0, 0]

def test_floor_grazing_does_not_block_the_origin(bound):
    floor = wall('floor', (-100, -100, -2), (100, 100, 0), ground=True)
    result = run(bound, shared_origin(False), geometry=[floor])
    assert result.success, result.error
    assert result.context['_fire_origins_relocated'] == 0

def test_unadmitted_solid_shape_rejects_clearly_without_payment(bound):
    column = wall('column')
    column['volume'] = {'kind': 'cylinder', 'center': [4, 0, -20], 'radius': 1, 'height': 40}
    result = run(bound, shared_origin(), geometry=[column])
    assert not result.success
    assert 'closed schema' in result.error

def test_inactive_geometry_does_not_relocate(bound):
    inactive = wall()
    inactive['active'] = False
    result = run(bound, shared_origin(False), geometry=[inactive])
    assert result.success, result.error
    assert result.context['_fire_origins_relocated'] == 0

def test_casting_from_inside_a_solid_rejects_without_payment(bound):
    result = run(bound, shared_origin(), geometry=[
        wall('overlapping-wall', (-9.9, -20, -20), (-9.5, 20, 20))])
    assert not result.success
    assert 'inside a blocking solid' in result.error

def test_relocated_cubes_must_still_be_contiguous(bound):
    cubes = [cube((0, 0, 0), (10, 0, 0), unseen=True),
             cube((10, 0, 0), (20, 0, 0), unseen=True)]
    result = run(bound, cubes, geometry=[wall()])
    assert not result.success
    assert 'face area' in result.error


def test_unseen_fire_origin_can_start_on_the_actual_solid_face(bound):
    result = run(bound, shared_origin(), geometry=[
        wall('wall', (-9.6, -20, -20), (-8.6, 20, 20))])
    assert result.success, result.error
    assert result.context['_fire_origins_relocated'] == 2
    assert all(cube['origin'] == [-9.6, 0, 0] for cube in result.context['_fire_actual_cubes'])


def test_fractional_adjacent_faces_and_caster_cover_use_actual_identity(bound):
    cubes = [cube((-19.6, 0, 0), (-9.6, 0, 0)),
             cube((-9.6, 0, 0), (0.4, 0, 0))]
    result = run(bound, cubes)
    assert result.success, result.error
    assert result.context['_fire_actual_cubes'] == cubes
    assert result.context['_fire_origins_relocated'] == 0
    assert result.context['_fire_storm_targets'].count('hero') == 1


@pytest.mark.parametrize('cubes, error', [
    ([cube((0, 0, 0), (10.000001, 0, 0)), cube((10, 0, 0), (10, 0, 0))], 'fit the admitted scene'),
    ([cube((0, 0, 0), (5, 5, 5)), cube((10, 0, 0), (10, 0, 0))], 'actual cube face'),
    ([cube((0, 0, 0), (10, 0, 0)), cube((10.000001, 0, 0), (10.000001, 0, 0))], 'face area'),
    ([cube((0, 0, 0), (10, 0, 0)), cube((10, 10, 0), (10, 10, 0))], 'face area'),
    ([cube((-200.000001, 0, 0), (-200.000001, 0, 0)), cube((-190.000001, 0, 0), (-190.000001, 0, 0))], 'fit the admitted scene'),
])
def test_rounding_tolerance_preserves_invalid_placement_rejection(bound, cubes, error):
    result = run(bound, cubes)
    assert not result.success
    assert error in result.error


@pytest.mark.parametrize('origin, included', [(-9.6, True), (-9.6001, True), (-9.59, False)])
def test_caster_surface_cover_distinguishes_rounding_from_an_outside_origin(bound, origin, included):
    cubes = [cube((origin - 10, -5, z), (origin, 0, 0)) for z in (0, -10)]
    result = run(bound, cubes, positions={'behind': [-15, 0, 0]})
    if included:
        assert result.success, result.error
        assert set(result.context['_fire_storm_targets']) == {'hero', 'behind'}
    else:
        assert not result.success
        assert 'Physical obstruction needs admitted categorical Cover' in result.error


def test_staged_unseen_fire_origin_preserves_intent_and_actual_receipt(make_case, tmp_path):
    geometry = [
        {'id': name, 'map_id': 'lite', 'volume': {'kind': 'box', 'min': lower, 'max': upper},
         'active': True, 'movement': 'blocking', 'visibility': 'opaque',
         'metadata': {'plane': 'material', 'ground': ground},
         'destroyed': {'active': False, 'movement': 'passable', 'visibility': 'clear'}}
        for name, lower, upper, ground in [
            ('floor', [-90, -90, -2], [90, 90, 0], True),
            ('wall', [4, -20, -20], [5, 20, 20], False)]]
    case = make_case('fire_storm', casting_source='sorcerer', geometry=geometry, positions={
        'hero': [-10, 0, 0], 'guard': [0, 5, 0], 'other': [8, 5, 0], 'extra': [80, 50, 0]})
    cubes = [
        {'min': [0, 0, z], 'origin': [10, 0, 0], 'include_origin': True, 'origin_unseen': True}
        for z in (0, -10)]
    case.start()
    before_rng = dice._rng.getstate()
    started, _ = case.act('begin_srd_spell_action', **(case.parameters | {
        'caster_can_see_origin': False, 'fire_storm_cubes': cubes}))
    assert dice._rng.getstate() == before_rng
    identity = started.result['context']['_pending_action_id']
    pending = thaw_json_value(case.lasting()['pending_srd_actions'][identity])
    assert pending['parameters']['fire_storm_cubes'] == cubes
    assert case.entities()['hero']['resources']['spell_slot_7'] == 3
    assert case.entities()['guard']['hp'] == 1000
    assert not case.snapshot().state[case.record['id'] + '.combat']['action_available']
    completed, _ = case.act('complete_srd_pending_action', pending_action_id=identity)
    assert identity not in case.lasting()['pending_srd_action_stack']
    assert case.entities()['hero']['resources']['spell_slot_7'] == 2
    assert case.entities()['guard']['hp'] < 1000
    assert all(case.entities()[name]['hp'] == 1000 for name in ('other', 'extra'))
    card = thaw_json_value(completed.result['result_card'])
    origin_rows = [row for row in card['details'] if row['label'] == 'Origin relocated']
    assert len(origin_rows) == 2
    formed = [[float(value.strip()) for value in row['value'].split('formed ', 1)[1]
               .split(' on the near', 1)[0].split(',')] for row in origin_rows]
    assert formed == [[4, 0, 0], [4, 0, 0]]
    summary = {row['label']: row['value'] for row in card['summary']}
    assert summary['Cubes'] == summary['Origins relocated'] == 2
    native = next(row['equation'] for row in thaw_json_value(completed.result['context'])['_pending_card_summary']
                  if row.get('equation', {}).get('die_sides') == 10)
    projected = next(row['equation'] for row in card['summary']
                     if row.get('equation', {}).get('die_sides') == 10)
    assert projected == native and len(native['rolls']) == 7
    assert native['roll'] == sum(native['rolls'])
    destination = tmp_path / 'staged-fire-origin-card.json'
    destination.write_text(json.dumps(card, indent=2))
