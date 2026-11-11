"""Focused Healing Touch checks through original scene, HP, Bonus Action and rest owners."""
from copy import deepcopy
import json, os, zipfile
from pathlib import Path
from random import Random

import pytest
import yaml

from scripts.srd_lite_steed_healing_touch import ACTION, CHARGE, bind_steed_healing_touch, expose_steed_healing_touch
from scripts.srd_lite_steed_life_bond import bind_steed_life_bond
from sylvia_gm import dice
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge, _json_safe
from sylvia_gm.application.result_cards import project_result_card
from sylvia_gm.application.result_card_admission import validate_result_card_declaration
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget
from tests.srd_lite_mass_cure_geometry_fixtures import NAMESPACE, _scene_entity, _calls


@pytest.fixture(scope='module')
def bound():
    with zipfile.ZipFile(Path(os.environ['SRD_LITE_CANDIDATE'])) as archive:
        name = next(n for n in archive.namelist() if n.endswith('/runtime/ruleset.yaml') and NAMESPACE in n)
        data = yaml.load(archive.read(name), Loader=yaml.CSafeLoader)
    pool = data.get('parameter_declarations', {})
    for tool in data['tools'].values():
        tool['parameters'] = [deepcopy(pool[p]) if isinstance(p, str) else p for p in tool['parameters']]
    hp_owner = deepcopy(data['tools']['restore_hit_points'])
    bonus_owner = deepcopy(data['tools']['consume_srd_bonus_action'])
    rest_effects = deepcopy(data['tools']['complete_srd_rest']['effects'])
    finish_effects = deepcopy(data['tools']['finish_srd_steed_source_actor']['effects'])
    profiles = deepcopy(data['definitions']['spells'])
    bind_steed_life_bond(data)
    changed = bind_steed_healing_touch(data)
    assert data['tools']['restore_hit_points'] == hp_owner
    assert data['tools']['consume_srd_bonus_action'] == bonus_owner
    assert data['tools']['complete_srd_rest']['effects'][:-1] == rest_effects
    assert data['tools']['finish_srd_steed_source_actor']['effects'][:-1] == finish_effects
    assert data['definitions']['spells'] == profiles
    closure, pending = {}, [ACTION, 'complete_srd_rest', 'begin_srd_rest', 'finish_srd_steed_source_actor']
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data['tools'][name]
            pending.extend(_calls(closure[name]['effects']))
    subset = {key: value for key, value in data.items() if key not in {'tools', 'procedures'}}
    subset['tools'] = closure
    parsed = RulesetRuntime(ruleset_dict=subset)
    validate_result_card_declaration(data['tools'][ACTION]['metadata']['result_card'])
    return parsed, data, changed


def _case(bound, *, combat=False, steed_changes=None, target_changes=None,
          caster_changes=None, position=(10.4, 0, 0), plane='material'):
    parsed, data, _ = bound
    defaults = {'name': 'Creature', 'kind': 'monster', 'creature_kind': 'monster', 'creature_type': 'humanoid',
        'hp': 10, 'max_hp': 50, 'temp_hp': 0, 'conditions': [], 'is_alive': True, 'bloodied': True,
        'death_successes': 0, 'death_failures': 0, 'stable': False, 'stable_recovery_hours_remaining': 0,
        'resources': {}, 'exhaustion_level': 0, 'available_bonus_actions': [], 'bonus_action_available': True}
    entities = {
        'caster': {**deepcopy(defaults), 'name': 'Caster', 'created_steed_id': 'steed',
                   **(caster_changes or {})},
        'steed': {**deepcopy(defaults), 'name': 'Otherworldly Steed', 'creature_type': 'celestial',
            'hp': 25, 'max_hp': 25, 'created_spell_id': 'find_steed', 'created_active': True,
            'created_caster_state_id': 'caster', 'created_spell_level': 2, CHARGE: True,
            **(steed_changes or {})},
        'target': {**deepcopy(defaults), 'name': 'Recipient', 'created_steed_id': 'steed',
                   **(target_changes or {})}}
    frames = [_scene_entity('caster', [-10, 0, 0]),
              _scene_entity('steed', [0, 0, 0], footprint={'kind': 'box', 'half_extents': [5, 5]}),
              _scene_entity('target', list(position), plane=plane)]
    scene = {'format': 1, 'map': {'id': 'mass-cure-fixture', 'revision': 0,
        'bounds': {'min': [-200, -200, -200], 'max': [200, 200, 200]},
        'grid': {'cell_size': 5, 'unit': 'ft', 'origin': [-200, -200]},
        'layers': [{'id': 'world', 'min_elevation': -200, 'max_elevation': 200}]},
        'entities': frames, 'geometry': [], 'terrain': [], 'zones': []}
    state = {NAMESPACE + '.state': {'lasting_tick': 0, 'turn_effects': [], 'spatial_scene': scene},
             NAMESPACE + '.entities': entities,
             NAMESPACE + '.combat': {'encounter_active': combat, 'turn_active': combat,
                                     'active_actor': 'steed' if combat else None}}
    bridge = AuthorityMemoryStateBridge(NAMESPACE, state)
    def act(name, **parameters):
        def nested(tool, args, parent):
            return execute_tool(tool, args, parsed.tools, bridge, parsed.ruleset_config,
                                parsed.definitions, combat_state=bridge.combat_state,
                                tool_executor=nested, budget=parent.budget)
        return execute_tool(name, parameters, parsed.tools, bridge, parsed.ruleset_config,
                            parsed.definitions, combat_state=bridge.combat_state,
                            tool_executor=nested, budget=ExecutionBudget())
    return bridge, state, act


def _touch(act):
    return act(ACTION, steed_state='steed', target_state='target')


def _snapshot(bridge):
    return json.dumps({'state': bridge.get_entity('state'), 'combat': bridge.combat_state,
        'entities': {name: bridge.get_entity(name) for name in ('caster', 'steed', 'target')}}, sort_keys=True)
