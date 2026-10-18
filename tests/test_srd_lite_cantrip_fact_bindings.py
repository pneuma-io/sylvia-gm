"""Cantrip scene facts through native spatial queries and the actual DSL owners.

These focused source checks exercise the repaired projection and its existing
consumers. A regenerated package and compiled app are separate pending checks.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import zipfile

import pytest
import yaml

from scripts.srd_lite_offensive_cantrips import bind_cantrip_fact_variables
from scripts.srd_lite_profiles import lists
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm import dice
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget
from tests.test_srd_lite_mass_cure_geometry import _scene_entity, _wall

NAMESPACE = 'sylvia.srd-lite.bg3'
ROOTS = ('cast_spell', 'begin_srd_spell_action')
LEAVES = ('cast_spell_attack_damage', 'resolve_spell_multi_attack_target')


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
    with zipfile.ZipFile(Path(os.environ['SRD_LITE_CANDIDATE'])) as archive:
        entries = [name for name in archive.namelist()
                   if name.endswith('/runtime/ruleset.yaml') and NAMESPACE in name]
        assert len(entries) == 1
        data = yaml.load(archive.read(entries[0]), Loader=yaml.CSafeLoader)
    pool = data.get('parameter_declarations', {})
    for tool in data['tools'].values():
        tool['parameters'] = [deepcopy(pool[p]) if isinstance(p, str) else p
                              for p in tool['parameters']]
    originals = {name: deepcopy(data['tools'][name]) for name in ROOTS + LEAVES}
    profiles = deepcopy(data['definitions']['spells'])
    before_dice = [deepcopy(effect) for owner in originals.values()
                   for rows in lists(owner['effects']) for effect in rows
                   if effect.get('call') == 'd20_test' or 'dice' in effect or 'roll' in effect]
    assert bind_cantrip_fact_variables(data) == list(ROOTS + LEAVES)
    assert data['definitions']['spells'] == profiles
    after_dice = [effect for name in ROOTS + LEAVES for rows in lists(data['tools'][name]['effects'])
                  for effect in rows if effect.get('call') == 'd20_test' or 'dice' in effect or 'roll' in effect]
    assert before_dice == after_dice, 'Retain original attack/save dice and damage owners'
    previews = []
    for name in ROOTS + LEAVES:
        owner = data['tools'][name]
        assert owner.get('pre_conditions', []) == originals[name].get('pre_conditions', [])
        defaults = []
        for effect in owner['effects']:
            if not effect.get('set', {}).get('field', '').startswith('_cantrip_fact_'):
                break
            defaults.append(effect)
        projection = next(effect for effect in owner['effects']
            if any(row.get('set', {}).get('field') == '_cantrip_fact_target_distance'
                   for row in effect.get('on_true', [])))
        if name in ROOTS:
            defaults.append({'set': {'field': '_ms', 'value': 'spell'}})
        consumers = []
        fields = {'_retained_caster_sees', '_retained_target_sees',
                  '_retained_environment_enemy_nearby', '_retained_environment_enemy_sees',
                  '_retained_environment_enemy_incapacitated', '_caster_can_see_target'}
        for effect in owner['effects'][len(defaults) + 1:]:
            field = effect.get('set', {}).get('field')
            if field in fields:
                consumers.append(effect)
            elif effect.get('condition') == '_cantrip_fact_caster_can_see_target != null':
                consumers.append(effect)
        if name in LEAVES:
            # Evaluate the existing native attack conditions after scene projection.
            for rows in lists(owner['effects']):
                for effect in rows:
                    if effect.get('set', {}).get('field') not in {
                            '_spell_attack_advantage', '_spell_attack_disadvantage'}:
                        continue
                    if effect.get('set', {}).get('value') is False:
                        continue  # The unrelated magic-zone no-effect cleanup branch.
                    consumers.append(effect)
        preview = 'preview_' + name
        needed = {'spell', 'caster_state', 'target', 'lasting_state', 'object_target_id',
            'targets', 'attack_targets', 'target_distance', 'caster_can_see_target',
            'target_can_see_caster', 'enemy_within_5_feet', 'close_enemy_can_see_caster',
            'close_enemy_incapacitated', 'target_within_5_feet', 'attack_kind',
            'advantage', 'disadvantage', 'initial_advantage', 'initial_disadvantage'}
        data['tools'][preview] = {'description': 'Exercise native source projection and consumers.',
            'parameters': [deepcopy(p) for p in owner['parameters'] if p['name'] in needed or
                           (p.get('resolve') == 'entity' and p.get('default') == 'state')],
            'effects': deepcopy(defaults + [projection] + consumers)}
        previews.append(preview)
    closure = {}
    pending = list(previews)
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data['tools'][name]
            pending.extend(_calls(closure[name]['effects']))
    subset = {key: value for key, value in data.items() if key not in {'tools', 'procedures'}}
    subset['tools'] = closure
    return RulesetRuntime(ruleset_dict=subset), data


def _run(bound, owner, *, spell='ray_of_frost', distance=12.5, lighting='bright',
         geometry=None, alive=True, prone=False, **facts):
    parsed, _ = bound
    frames = [_scene_entity('hero', [0, 0, 0], lighting=lighting),
              _scene_entity('recipient', [distance, 0, 0], lighting=lighting)]
    scene = {'format': 1, 'map': {'id': 'mass-cure-fixture', 'revision': 0,
        'bounds': {'min': [-200, -200, -200], 'max': [200, 200, 200]},
        'grid': {'cell_size': 5, 'unit': 'ft', 'origin': [-200, -200]},
        'layers': [{'id': 'world', 'min_elevation': -200, 'max_elevation': 200}]},
        'entities': frames, 'geometry': geometry or [], 'terrain': [],
        'zones': [{'id': 'ambient', 'map_id': 'mass-cure-fixture',
            'shape': {'kind': 'box', 'min': [-200, -200, -200], 'max': [200, 200, 200]},
            'metadata': {'active': True, 'plane': 'material', 'lighting': lighting}}]}
    entities = {row['id']: {'name': row['id'], 'kind': 'character', 'creature_kind': 'character',
        'creature_type': 'humanoid', 'hp': 20, 'max_hp': 50, 'is_alive': True,
        'conditions': [], 'resources': {'spell_slot_1': 2}, 'senses': {},
        'darkvision_feet': 0, 'blindsight_feet': 0, 'truesight_feet': 0,
        'melee_reach': 5, 'speed': 30, 'exhaustion_level': 0, 'faction': 'heroes'} for row in frames}
    entities['recipient'].update(is_alive=alive, faction='foes', conditions=['prone'] if prone else [])
    state = {NAMESPACE + '.state': {'lasting_tick': 0, 'turn_effects': [], 'spatial_scene': scene},
             NAMESPACE + '.entities': entities, NAMESPACE + '.combat': {}}
    before = json.dumps(state, sort_keys=True)
    bridge = AuthorityMemoryStateBridge(NAMESPACE, state)
    def nested(name, parameters, parent):
        return execute_tool(name, parameters, parsed.tools, bridge, parsed.ruleset_config,
                            parsed.definitions, tool_executor=nested, budget=parent.budget)
    rng = dice._rng.getstate()
    result = execute_tool('preview_' + owner, {'spell': spell, 'caster_state': 'hero',
        'target': 'recipient', 'lasting_state': 'state', 'object_target_id': '',
        'targets': None, 'attack_targets': None, 'target_distance': 999,
        'caster_can_see_target': False, 'target_can_see_caster': False,
        'enemy_within_5_feet': False, 'close_enemy_can_see_caster': False,
        'close_enemy_incapacitated': True, 'target_within_5_feet': True,
        'attack_kind': 'ranged', 'advantage': False, 'disadvantage': False,
        'initial_advantage': False, 'initial_disadvantage': False,
        **facts}, parsed.tools, bridge, parsed.ruleset_config, parsed.definitions,
        tool_executor=nested, budget=ExecutionBudget())
    assert dice._rng.getstate() == rng
    assert json.dumps(state, sort_keys=True) == before
    assert bridge.combat_state == {}, 'Scene fact projection cannot write an empty combat key'
    return result


@pytest.mark.parametrize('owner', ROOTS)
def test_public_preflight_uses_actual_distance_and_sight_without_state_writes(bound, owner):
    result = _run(bound, owner)
    assert result.success, result.error
    assert result.context['_cantrip_fact_target_distance'] == pytest.approx(12.5)
    assert result.context['_cantrip_fact_caster_can_see_target'] is True
    if owner == 'cast_spell':
        assert result.context['_caster_can_see_target'] is True


@pytest.mark.parametrize('owner', ROOTS)
@pytest.mark.parametrize('spell,distance,geometry', [
    ('ray_of_frost', 61, []), ('shocking_grasp', 6, []), ('fire_bolt', 12.5, [_wall()]),
])
def test_claimed_range_and_sight_cannot_bypass_actual_scene_qualification(bound, owner, spell, distance, geometry):
    result = _run(bound, owner, spell=spell, distance=distance, geometry=geometry,
                  target_distance=0, caster_can_see_target=True)
    assert not result.success
    assert 'beyond range or lacks a clear path' in result.error


@pytest.mark.parametrize('owner', ROOTS)
def test_sacred_flame_requires_actual_sight(bound, owner):
    result = _run(bound, owner, spell='sacred_flame', lighting='darkness', caster_can_see_target=True)
    assert not result.success
    assert 'requires actual sight' in result.error


@pytest.mark.parametrize('owner,spell', list(zip(LEAVES, ('ray_of_frost', 'eldritch_blast'))))
def test_attack_conditions_use_real_far_prone_body_and_visibility(bound, owner, spell):
    result = _run(bound, owner, spell=spell, prone=True)
    assert result.success, result.error
    assert result.context['_retained_caster_sees'] is True
    assert result.context['_retained_target_sees'] is True
    assert result.context['_spell_attack_advantage'] is False
    assert result.context['_spell_attack_disadvantage'] is True
    assert result.context['_cantrip_fact_close_enemy_incapacitated'] is False


@pytest.mark.parametrize('owner', ROOTS + LEAVES)
def test_other_spells_retain_their_existing_parameter_facts(bound, owner):
    result = _run(bound, owner, spell='guiding_bolt')
    assert result.success, result.error
    assert result.context['_cantrip_fact_caster_can_see_target'] is False
    if owner != 'resolve_spell_multi_attack_target':
        assert result.context['_cantrip_fact_target_distance'] == 999


def test_dead_later_beam_recipient_skips_projection(bound):
    result = _run(bound, 'resolve_spell_multi_attack_target', spell='eldritch_blast', alive=False)
    assert result.success, result.error
    assert result.context['_cantrip_fact_caster_can_see_target'] is False


def test_root_dispatch_and_saved_argument_keys_keep_their_native_contracts(bound):
    _, data = bound
    calls = [effect for rows in lists(data['tools']['cast_spell']['effects']) for effect in rows
             if effect.get('call') in {'cast_spell_attack_damage', 'cast_spell_single_target_save_damage'}]
    assert calls
    assert all(call['params']['target_distance'] == '{_cantrip_fact_target_distance}' for call in calls)
    assert all(call['params']['caster_can_see_target'] in {
        '{_caster_can_see_target}', '{_cantrip_fact_caster_can_see_target}'} for call in calls)
    staged = [item for rows in lists(data['tools']['begin_srd_spell_action']['effects'])
              for effect in rows if isinstance(effect.get('set', {}).get('value'), list)
              for item in effect['set']['value'] if isinstance(item, dict) and item.get('key') in {
                  "'target_distance'", "'caster_can_see_target'"}]
    assert len(staged) == 2
    assert staged[0]['value'] == '_cantrip_fact_target_distance'
    assert staged[1]['value'] == '_cantrip_fact_caster_can_see_target'
    assert staged[0]['missing'] == '_cantrip_fact_target_distance == null'
    assert staged[1]['missing'] == '_cantrip_fact_caster_can_see_target == null'
