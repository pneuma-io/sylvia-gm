"""Check actual Sunburst origin paths while preserving payment and random state."""
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import zipfile

import pytest
import yaml
from sylvia_gm import dice
from sylvia_gm.domain.authority import thaw_json_value
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget
from tests.test_native_followups import make_case
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _scene_entity, _wall, _calls

TOOL = 'collect_srd_sunburst_creature_targets'
DBF = 'qualify_srd_delayed_blast_point'

@pytest.fixture(scope='module')
def bound():
    with zipfile.ZipFile(Path(os.environ['SRD_LITE_CANDIDATE'])) as archive:
        data = yaml.load(archive.read('packages/' + NAMESPACE + '/runtime/ruleset.yaml'), Loader=yaml.CSafeLoader)
    pool = data.get('parameter_declarations', {})
    for tool in data['tools'].values():
        tool['parameters'] = [deepcopy(pool[row]) if isinstance(row, str) else row for row in tool['parameters']]
    if os.environ.get('SUNBURST_PATCH_ORIGIN') == '1':
        from scripts.srd_sunburst_geometry import _sphere_tool, AREA_ORIGIN, _area_origin, SOLID_HIT, _solid_hit
        from scripts.srd_delayed_blast_fireball import _point_validator
        data['tools'][TOOL] = _sphere_tool()
        data['tools'][DBF] = _point_validator()
        data['tools'][AREA_ORIGIN] = _area_origin()
        data['tools'][SOLID_HIT] = _solid_hit()
    closure, pending = {}, [TOOL, DBF]
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data['tools'][name]
            pending.extend(_calls(closure[name]['effects']))
    subset = {key: value for key, value in data.items() if key not in {'tools', 'procedures'}}
    subset['tools'] = closure
    return RulesetRuntime(ruleset_dict=subset)

def solid(identity, lower, upper, **changes):
    return _wall() | {'id': identity,
            'volume': {'kind': 'box', 'min': lower, 'max': upper},
            'active': True, 'movement': 'blocking', 'visibility': 'opaque',
            'metadata': {'plane': 'material'}, **changes}

@pytest.mark.parametrize('start, end, blocked, inside, formed', [
    ([4, 0, 0], [10, 0, 0], True, False, [4, 0, 0]),
    ([4.1, 0, 0], [10, 0, 0], True, True, [4.1, 0, 0]),
    ([3.9, 0, 0], [10, 0, 0], True, False, [4, 0, 0]),
    ([4, 0, 0], [4, 4, 0], False, False, [4, 4, 0]),
    ([4, 0, 0], [0, 0, 0], False, False, [0, 0, 0]),
])
def test_area_origin_distinguishes_strict_interior_from_boundary_contact(bound, start, end, blocked, inside, formed):
    scene = {'format': 1, 'map': {'id': 'mass-cure-fixture', 'revision': 0,
        'bounds': {'min': [-20, -20, -20], 'max': [20, 20, 20]},
        'grid': {'cell_size': 5, 'unit': 'ft', 'origin': [-20, -20]},
        'layers': [{'id': 'world', 'min_elevation': -20, 'max_elevation': 20}]},
        'entities': [], 'geometry': [solid('wall', [4, -5, -5], [5, 5, 5])],
        'terrain': [], 'zones': []}
    state = {NAMESPACE + '.state': {'spatial_scene': scene},
             NAMESPACE + '.entities': {}, NAMESPACE + '.combat': {}}
    before, rng = json.dumps(state, sort_keys=True), dice._rng.getstate()
    bridge = AuthorityMemoryStateBridge(NAMESPACE, state)
    def nested(name, parameters, parent):
        return execute_tool(name, parameters, bound.tools, bridge, bound.ruleset_config,
            bound.definitions, tool_executor=nested, budget=parent.budget)
    result = execute_tool('project_srd_area_origin', {'start': start, 'origin': end, 'plane': 'material'},
        bound.tools, bridge, bound.ruleset_config, bound.definitions,
        tool_executor=nested, budget=ExecutionBudget())
    assert result.success, result.error
    assert json.dumps(state, sort_keys=True) == before and dice._rng.getstate() == rng
    assert result.context['_area_origin'] == pytest.approx(formed)
    assert result.context['_area_origin_blocked'] is blocked
    assert result.context['_area_origin_start_inside'] is inside


def run(bound, geometry, origin=(10, 0, 0), tool=TOOL, unseen=False, positions=None):
    scene = {'format': 1, 'map': {'id': 'mass-cure-fixture', 'revision': 0,
        'bounds': {'min': [-200, -200, -200], 'max': [200, 200, 200]},
        'grid': {'cell_size': 5, 'unit': 'ft', 'origin': [-200, -200]},
        'layers': [{'id': 'world', 'min_elevation': -200, 'max_elevation': 200}]},
        'entities': [_scene_entity('hero', [-10, 0, 0]), *[
            _scene_entity(name, list(point)) for name, point in
            (positions or {'recipient': [10, 5, 0]}).items()]],
        'geometry': geometry, 'terrain': [], 'zones': []}
    state = {NAMESPACE + '.state': {'lasting_tick': 0, 'turn_effects': [], 'spatial_scene': scene},
             NAMESPACE + '.entities': {name: {'name': name, 'kind': 'character', 'creature_type': 'humanoid', 'is_alive': True,
                 'conditions': [], 'resources': {'spell_slot_8': 2}} for name in
                 ['hero', *(positions or {'recipient': [10, 5, 0]})]},
             NAMESPACE + '.combat': {}}
    before, rng = json.dumps(state, sort_keys=True), dice._rng.getstate()
    bridge = AuthorityMemoryStateBridge(NAMESPACE, state)
    def nested(name, parameters, parent):
        return execute_tool(name, parameters, bound.tools, bridge, bound.ruleset_config,
            bound.definitions, tool_executor=nested, budget=parent.budget)
    origin_parameter = 'delayed_blast_origin' if tool == DBF else 'sunburst_origin'
    result = execute_tool(tool, {'caster_state': 'hero', origin_parameter: list(origin), 'origin_unseen': unseen},
        bound.tools, bridge, bound.ruleset_config, bound.definitions,
        tool_executor=nested, budget=ExecutionBudget())
    assert json.dumps(state, sort_keys=True) == before and dice._rng.getstate() == rng
    return result

def test_sunburst_origin_can_graze_the_top_of_an_actual_floor(bound):
    result = run(bound, [solid('floor', [-100, -100, -2], [100, 100, 0])])
    assert result.success, result.error
    assert result.context['_sunburst_origin'] == [10, 0, 0]
    assert 'recipient' in result.context['_sunburst_sphere_targets']

def test_sunburst_origin_still_rejects_crossing_solid_interior(bound):
    result = run(bound, [solid('wall', [4, -20, -20], [5, 20, 20])])
    assert not result.success and 'solid barrier blocks' in result.error

@pytest.mark.parametrize('changes', [
    {'active': False}, {'movement': 'passable'}, {'metadata': {'plane': 'ethereal'}}])
def test_sunburst_origin_ignores_nonblocking_actual_geometry(bound, changes):
    result = run(bound, [solid('wall', [4, -20, -20], [5, 20, 20], **changes)])
    assert result.success, result.error

def test_sunburst_origin_may_end_on_the_near_solid_face(bound):
    result = run(bound, [solid('wall', [4, -20, -20], [5, 20, 20])], origin=(4, 0, 0))
    assert result.success, result.error

def test_shared_delayed_blast_bead_origin_can_graze_the_same_floor(bound):
    result = run(bound, [solid('floor', [-100, -100, -2], [100, 100, 0])], tool=DBF)
    assert result.success, result.error
    assert result.context['_dbf_origin'] == [10, 0, 0]

def test_unseen_sunburst_uses_formed_sphere_for_actual_recipients(bound):
    result = run(bound, [solid('wall', [4, -20, -20], [5, 20, 20])], unseen=True,
                 positions={'near': [-55, 0, 0], 'far': [70, 0, 0]})
    assert result.success, result.error
    assert result.context['_sunburst_origin'] == [4, 0, 0]
    assert result.context['_sunburst_origin_relocated'] is True
    assert 'near' in result.context['_sunburst_sphere_targets']
    assert 'far' not in result.context['_sunburst_sphere_targets']

def test_unseen_sunburst_uses_first_same_plane_wall(bound):
    result = run(bound, [
        solid('far', [7, -20, -20], [8, 20, 20]),
        solid('near', [4, -20, -20], [5, 20, 20])], unseen=True)
    assert result.success, result.error
    assert result.context['_sunburst_origin'] == [4, 0, 0]

def test_unseen_clear_sunburst_keeps_requested_point(bound):
    result = run(bound, [], unseen=True)
    assert result.success, result.error
    assert result.context['_sunburst_origin'] == [10, 0, 0]
    assert result.context['_sunburst_origin_relocated'] is False

def test_sunburst_cannot_form_from_inside_the_actual_solid(bound):
    result = run(bound, [solid('wall', [-11, -20, -20], [-9, 20, 20])], unseen=True)
    assert not result.success and 'inside a blocking solid' in result.error

def test_shared_delayed_blast_projection_keeps_actual_formed_point(bound):
    result = run(bound, [solid('wall', [4, -20, -20], [5, 20, 20])], unseen=True, tool=DBF)
    assert result.success, result.error
    assert result.context['_dbf_origin'] == [4, 0, 0]


@pytest.mark.parametrize('tool', [TOOL, DBF])
def test_unseen_origin_can_start_on_the_actual_solid_face(bound, tool):
    result = run(bound, [solid('wall', [-9.6, -20, -20], [-8.6, 20, 20])],
                 unseen=True, tool=tool)
    assert result.success, result.error
    origin = '_dbf_origin' if tool == DBF else '_sunburst_origin'
    assert result.context[origin] == [-9.6, 0, 0]


def test_staged_unseen_sunburst_uses_actual_sphere_and_receipt(make_case, tmp_path):
    geometry = [
        {'id': name, 'map_id': 'lite', 'volume': {'kind': 'box', 'min': lower, 'max': upper},
         'active': True, 'movement': 'blocking', 'visibility': 'opaque',
         'metadata': {'plane': 'material', 'ground': ground},
         'destroyed': {'active': False, 'movement': 'passable', 'visibility': 'clear'}}
        for name, lower, upper, ground in [
            ('floor', [-90, -90, -2], [90, 90, 0], True),
            ('wall', [4, -20, -20], [5, 20, 20], False)]]
    case = make_case('sunburst', casting_source='wizard', geometry=geometry, positions={
        'hero': [-50, -50, 0], 'guard': [0, 5, 0], 'other': [80, 40, 0], 'extra': [80, 60, 0]})
    parameters = case.parameters | {'sunburst_origin': [60, 20, 0],
                                    'caster_can_see_origin': False, 'origin_has_clear_path': False}
    case.start()
    hp_before = {name: case.entities()[name]['hp'] for name in ('hero', 'guard', 'other', 'extra')}
    before_rng = dice._rng.getstate()
    started, _ = case.act('begin_srd_spell_action', **parameters)
    assert dice._rng.getstate() == before_rng
    identity = started.result['context']['_pending_action_id']
    pending = thaw_json_value(case.lasting()['pending_srd_actions'][identity])
    assert pending['parameters']['sunburst_origin'] == parameters['sunburst_origin']
    assert pending['parameters']['caster_can_see_origin'] is False
    assert pending['parameters']['origin_has_clear_path'] is False
    assert case.entities()['hero']['resources']['spell_slot_8'] == 3
    assert all(case.entities()[name]['hp'] == hp_before[name] for name in ('hero', 'guard', 'other', 'extra'))
    assert not case.snapshot().state[case.record['id'] + '.combat']['action_available']
    completed, _ = case.act('complete_srd_pending_action', pending_action_id=identity)
    assert identity not in case.lasting()['pending_srd_action_stack']
    assert case.entities()['hero']['resources']['spell_slot_8'] == 2
    assert case.entities()['guard']['hp'] < 1000
    assert all(case.entities()[name]['hp'] == hp_before[name] for name in ('hero', 'other', 'extra'))
    card = thaw_json_value(completed.result['result_card'])
    summary = {row['label']: row['value'] for row in card['summary']}
    expected_origin = [4, -49.6 + 53.6 * 69.6 / 109.6, 0]
    formed = [float(value.strip()) for value in summary['Origin'].removeprefix('Point ').split(',')]
    assert all(math.isclose(actual, expected, abs_tol=1e-8)
               for actual, expected in zip(formed, expected_origin))
    assert summary['Radius (feet)'] == 60
    assert summary['Origin relocated'] == 'Near side of obstruction'
    native = next(row['equation'] for row in thaw_json_value(completed.result['context'])['_pending_card_summary']
                  if row.get('equation', {}).get('die_sides') == 6)
    projected = next(row['equation'] for row in card['summary']
                     if row.get('equation', {}).get('die_sides') == 6)
    assert projected == native and len(native['rolls']) == 12 and native['roll'] == sum(native['rolls'])
    saves = [row['equation'] for row in card['equations'] if row['equation']['die_sides'] == 20]
    assert len(saves) == 1
    saved = saves[0]['total'] >= 18
    assert hp_before['guard'] - case.entities()['guard']['hp'] == (native['total'] // 2 if saved else native['total'])
    assert ('blinded' in case.entities()['guard']['conditions']) == (not saved)
    (tmp_path / 'staged-sunburst-unseen-card.json').write_text(json.dumps(card, indent=2))
