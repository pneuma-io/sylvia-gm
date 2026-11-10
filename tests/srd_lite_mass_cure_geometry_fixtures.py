"""Mass Cure geometry through the real DSL and authoritative spatial bridge.

These focused component checks use public synthetic state, not a rebuilt
package, persisted campaign or installed app. Native admission/app checks follow.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import zipfile

import pytest
import yaml

from scripts.srd_lite_mass_cure_geometry import QUALIFY, FAMILY, bind_mass_cure_scene_geometry, _effect_lists
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget
from sylvia_gm.runtime._types import ExecutionContext
from sylvia_gm.runtime._effect_executor import execute_effects
from sylvia_gm.dice import RollResult
from sylvia_gm import dice

NAMESPACE = 'sylvia.srd-lite.bg3'


def _calls(value):
    if isinstance(value, dict):
        if isinstance(value.get('call'), str):
            yield value['call']
        for child in value.values():
            yield from _calls(child)
    elif isinstance(value, list):
        for child in value:
            yield from _calls(child)


@pytest.fixture(scope='module')
def bound():
    package = Path(os.environ['SRD_LITE_CANDIDATE'])
    with zipfile.ZipFile(package) as archive:
        entries = [name for name in archive.namelist()
                   if name.endswith('/runtime/ruleset.yaml') and NAMESPACE in name]
        assert len(entries) == 1
        data = yaml.load(archive.read(entries[0]), Loader=yaml.CSafeLoader)
    pool = data.get('parameter_declarations', {})
    for tool in data['tools'].values():
        tool['parameters'] = [deepcopy(pool[p]) if isinstance(p, str) else p for p in tool['parameters']]
    healing = deepcopy(data['tools'][FAMILY])
    profile = deepcopy(data['definitions']['spells']['mass_cure_wounds'])
    changed = bind_mass_cure_scene_geometry(data)
    assert data['tools'][FAMILY] == healing, 'Keep the actual healing, dice and HP owners unchanged'
    assert data['definitions']['spells']['mass_cure_wounds'] == profile
    assert changed == [QUALIFY, 'cast_spell', 'begin_srd_spell_action']
    # Both public entry points must validate before existing staging/payment effects.
    assert data['tools']['begin_srd_spell_action']['effects'][0] == data['tools']['cast_spell']['effects'][0]
    root = data['tools']['cast_spell']
    dispatch = next(effect for rows in _effect_lists(root['effects']) for effect in rows
                    if effect.get('condition') == 'true' and
                    any(row.get('set', {}).get('field') == '_mass_cure_dispatch_origin_distance'
                        for row in effect.get('on_true', [])))
    original_origin_reads = [effect for effect in root['effects'] if
        effect.get('set', {}).get('field') == '_retained_ice_dispatch_origin_distance' or
        (effect.get('condition') == 'origin_distance != null' and
         any(row.get('set', {}).get('field') == '_retained_ice_dispatch_origin_distance'
             for row in effect.get('on_true', [])))]
    assert len(original_origin_reads) == 2
    preview = {
        'description': 'Exercise only the actual cast preflight in synthetic component state.',
        'parameters': [p for p in data['tools']['cast_spell']['parameters'] if p['name'] in {
            'spell', 'caster_state', 'targets', 'environment_origin', 'lasting_state',
            'origin_distance', 'caster_can_see_origin', 'origin_has_clear_path', 'all_targets_in_area'}],
        'effects': [deepcopy(root['effects'][0]), {'set': {'field': '_ms', 'value': 'spell'}},
                    *deepcopy(original_origin_reads), deepcopy(dispatch)],
    }
    data['tools']['preview_mass_cure_source'] = preview
    pending = ['preview_mass_cure_source']
    closure = {}
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data['tools'][name]
            pending.extend(_calls(closure[name]['effects']))
    subset = {key: value for key, value in data.items() if key not in {'tools', 'procedures'}}
    subset['tools'] = closure
    parsed = RulesetRuntime(ruleset_dict=subset)
    receipt = next(effect for rows in _effect_lists(data['tools']['cast_spell']['effects'])
                   for effect in rows if effect.get('condition') ==
                   "_ms == 'mass_cure_wounds' and _spell_healing != null")
    return parsed, preview, len(closure), receipt


def _scene_entity(identity, position, *, footprint=None, plane='material', lighting='bright'):
    return {'id': identity, 'map_id': 'mass-cure-fixture', 'position': position,
        'footprint': footprint or {'kind': 'box', 'half_extents': [0.4, 0.4]},
        'height': 6, 'facing': [1, 0], 'active': True, 'movement': 'blocking', 'visibility': 'opaque',
        'metadata': {'participant_ref': identity, 'plane': plane, 'lighting': lighting,
                     'creature_type': 'humanoid'}}


def _wall(*, height=8, visibility='opaque'):
    return {'id': 'wall', 'map_id': 'mass-cure-fixture',
        'volume': {'kind': 'box', 'min': [4, -20, -10], 'max': [5, 20, height]},
        'active': True, 'movement': 'blocking', 'visibility': visibility,
        'metadata': {'plane': 'material'},
        'destroyed': {'active': False, 'movement': 'passable', 'visibility': 'clear'}}


def _run(bound, *, hero=None, recipient=None, origin=None, lighting='bright', geometry=None,
         target_plane='material', targets=None, footprint=None, spell='mass_cure_wounds', **facts):
    parsed, _, _, _ = bound
    hero = hero or [0, 0, 0]
    recipient = recipient or [10, 0, 0]
    origin = origin or [0, 0, 0]
    frames = [_scene_entity('hero', hero, lighting=lighting),
              _scene_entity('recipient', recipient, footprint=footprint, plane=target_plane, lighting=lighting)]
    for identity in targets or []:
        if identity not in {'hero', 'recipient'}:
            frames.append(_scene_entity(identity, [5, 5, 0], lighting=lighting))
    scene = {'format': 1, 'map': {'id': 'mass-cure-fixture', 'revision': 0,
        'bounds': {'min': [-200, -200, -200], 'max': [200, 200, 200]},
        'grid': {'cell_size': 5, 'unit': 'ft', 'origin': [-200, -200]},
        'layers': [{'id': 'world', 'min_elevation': -200, 'max_elevation': 200}]},
        'entities': frames, 'geometry': geometry or [], 'terrain': [],
        'zones': [{'id': 'ambient', 'map_id': 'mass-cure-fixture',
            'shape': {'kind': 'box', 'min': [-200, -200, -200], 'max': [200, 200, 200]},
            'metadata': {'active': True, 'plane': 'material', 'lighting': lighting}}]}
    entities = {row['id']: {'name': row['id'], 'kind': 'character', 'creature_type': 'humanoid',
        'hp': 10, 'max_hp': 50, 'is_alive': True, 'conditions': [], 'resources': {'spell_slot_5': 2},
        'stable': False, 'death_successes': 0, 'death_failures': 0, 'stable_recovery_hours_remaining': 0,
        'darkvision_feet': 0, 'blindsight_feet': 0, 'truesight_feet': 0, 'senses': {}} for row in frames}
    state = {NAMESPACE + '.state': {'lasting_tick': 0, 'turn_effects': [], 'spatial_scene': scene},
             NAMESPACE + '.entities': entities, NAMESPACE + '.combat': {}}
    before = json.dumps(state, sort_keys=True)
    bridge = AuthorityMemoryStateBridge(NAMESPACE, state)
    def nested(name, parameters, parent):
        return execute_tool(name, parameters, parsed.tools, bridge, parsed.ruleset_config,
                            parsed.definitions, tool_executor=nested, budget=parent.budget)
    rng_before = dice._rng.getstate()
    result = execute_tool('preview_mass_cure_source', {'spell': spell, 'caster_state': 'hero',
        'targets': ['recipient'] if targets is None else targets,
        'environment_origin': origin, 'lasting_state': 'state', **facts},
        parsed.tools, bridge, parsed.ruleset_config, parsed.definitions,
        tool_executor=nested, budget=ExecutionBudget())
    assert dice._rng.getstate() == rng_before, 'Geometry preflight must not consume a die'
    assert json.dumps(state, sort_keys=True) == before
    assert bridge.get_entity('hero')['resources']['spell_slot_5'] == 2
    assert bridge.get_entity('recipient')['hp'] == 10
    assert bridge.get_entity('state')['turn_effects'] == []
    assert bridge.combat_state == {}, 'Geometry validation must not write combat fields'
    return result


def test_actual_closed_sphere_and_cast_range_override_reported_facts(bound):
    result = _run(bound, hero=[60.4, 0, 0], recipient=[30.4, 0, 0],
                  origin_distance=999, caster_can_see_origin=False, origin_has_clear_path=False,
                  all_targets_in_area=False)
    assert result.success, result.error
    assert result.context['_mass_cure_dispatch_origin_distance'] == 60
    assert result.context['_mass_cure_dispatch_caster_can_see_origin'] is True
    assert result.context['_mass_cure_dispatch_origin_has_clear_path'] is True


@pytest.mark.parametrize('recipient', ([30.41, 0, 0], [0, 0, 30.01], [0, 0, -36.01], [22, 22, 0]))
def test_reported_membership_cannot_include_a_body_outside_the_real_sphere(bound, recipient):
    result = _run(bound, recipient=recipient, all_targets_in_area=True)
    assert not result.success
    assert 'outside its actual 30-foot Sphere' in result.error


@pytest.mark.parametrize('recipient', ([0, 0, 30], [0, 0, -36]))
def test_vertical_tangent_body_is_inside_the_closed_sphere(bound, recipient):
    result = _run(bound, recipient=recipient)
    assert result.success, result.error


def test_circle_footprint_uses_its_occupied_body_and_not_its_bounding_box(bound):
    footprint = {'kind': 'circle', 'radius': 0.4}
    inside = _run(bound, recipient=[30.4, 0, 0], footprint=footprint)
    assert inside.success, inside.error
    outside = _run(bound, recipient=[21.6, 21.6, 0], footprint=footprint)
    assert not outside.success
    assert 'outside its actual 30-foot Sphere' in outside.error


def test_actual_range_rejects_a_forged_nearby_origin(bound):
    result = _run(bound, hero=[60.41, 0, 0], origin_distance=0, caster_can_see_origin=True,
                  origin_has_clear_path=True, all_targets_in_area=True)
    assert not result.success
    assert 'exceeds 60 feet' in result.error


def test_actual_dark_point_rejects_reported_visibility(bound):
    result = _run(bound, origin=[10, 0, 0], lighting='darkness', caster_can_see_origin=True)
    assert not result.success
    assert 'cannot actually see' in result.error


def test_transparent_solid_wall_blocks_the_origin_even_when_it_is_visible(bound):
    result = _run(bound, origin=[10, 0, 0], geometry=[_wall(visibility='clear')],
                  origin_has_clear_path=True)
    assert not result.success
    assert 'solid barrier blocks' in result.error


def test_total_recipient_cover_rejects_and_partial_cover_still_allows_healing(bound):
    blocked = _run(bound, geometry=[_wall()])
    assert not blocked.success
    assert 'Total solid cover blocks' in blocked.error
    partial = _run(bound, geometry=[_wall(height=1.5)])
    assert partial.success, partial.error


def test_actual_plane_rejects_a_nearby_recipient_on_another_plane(bound):
    result = _run(bound, target_plane='ethereal')
    assert not result.success
    assert 'actual creature on the casting plane' in result.error


def test_empty_selection_rejects_before_any_cast_effect(bound):
    result = _run(bound, targets=[])
    assert not result.success
    assert 'one through six selected creatures' in result.error


def test_six_selected_creatures_fit_the_normal_budget_and_a_seventh_rejects(bound):
    chosen = ['hero', 'recipient', 'friend1', 'friend2', 'friend3', 'friend4']
    allowed = _run(bound, targets=chosen)
    assert allowed.success, allowed.error
    rejected = _run(bound, targets=chosen + ['friend5'])
    assert not rejected.success
    assert 'one through six selected creatures' in rejected.error


def test_other_healing_spell_preserves_its_existing_reported_facts(bound):
    result = _run(bound, spell='mass_healing_word', recipient=[100, 100, 100],
                  origin_distance=9, caster_can_see_origin=False, all_targets_in_area=False)
    assert result.success, result.error
    assert result.context['_mass_cure_dispatch_origin_distance'] == 9
    assert result.context['_mass_cure_dispatch_caster_can_see_origin'] is False
    assert '_mass_cure_distance' not in result.context


def test_selected_healing_origin_is_kept_in_the_recorded_cast_detail(bound):
    _, _, _, receipt = bound
    context = ExecutionContext(params={'spell': 'mass_cure_wounds', 'environment_origin': [20, -5, 3]},
        vars={'_ms': 'mass_cure_wounds', '_spell_healing': RollResult(8, 5, (1, 2, 3, 4, 5), 15, 0),
              '_spell_detail_rows': []})
    execute_effects([receipt], context)
    assert context.vars['_spell_detail_rows'] == [
        {'label': 'Healing sphere center (30 ft radius)', 'value': [20, -5, 3]}]
