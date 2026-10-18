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
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _scene_entity, _calls


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


@pytest.mark.parametrize('combat', [False, True])
def test_one_native_pool_hp_owner_charge_and_actual_card(bound, monkeypatch, combat):
    from sylvia_gm.runtime import _effect_executor
    native = _effect_executor.dice_roll
    rolls = []
    def recorded(*args, **kwargs):
        result = native(*args, **kwargs)
        rolls.append(result)
        return result
    monkeypatch.setattr(dice, '_rng', Random(0))
    monkeypatch.setattr(_effect_executor, 'dice_roll', recorded)
    bridge, _, act = _case(bound, combat=combat)
    result = _touch(act)
    assert result.success, result.error
    assert len(rolls) == 1 and rolls[0].results == (7, 7) and rolls[0].sides == 8
    assert result.context['_steed_touch_amount'] == 16
    assert bridge.get_entity('target')['hp'] == 26
    assert bridge.get_entity('steed')['hp'] == 25, 'Healing Touch is an ability, so it must not trigger Life Bond'
    assert bridge.get_entity('steed')[CHARGE] is False
    assert bridge.get_entity('steed')['bonus_action_available'] is (not combat)
    before_projection = dice._rng.getstate()
    card = project_result_card(bound[1]['tools'][ACTION]['metadata']['result_card'],
        parameters={}, public_context=_json_safe(result.context))
    assert card is not None
    equation = next(row['equation'] for row in card['summary'] if 'equation' in row)
    assert equation == {'die_sides': 8, 'rolls': [7, 7], 'roll': 14, 'modifier': 2, 'total': 16}
    assert any(row['label'] == 'HP restored' and row['value'] == 16 for row in card['summary'])
    assert any('before the HP cap' in row['value'] for row in card['details'])
    assert dice._rng.getstate() == before_projection
    if combat and os.environ.get('SRD_LITE_STEED_TOUCH_CARD'):
        Path(os.environ['SRD_LITE_STEED_TOUCH_CARD']).write_text(json.dumps(card, indent=2), encoding='utf-8')


def test_native_hp_cap_and_prohibition_keep_original_roll(bound, monkeypatch):
    monkeypatch.setattr(dice, '_rng', Random(0))
    bridge, _, act = _case(bound, target_changes={'hp': 49})
    result = _touch(act)
    assert result.success, result.error
    assert bridge.get_entity('target')['hp'] == 50 and result.context['_steed_touch_restored'] == 1
    bridge, _, act = _case(bound, target_changes={'hit_point_recovery_blocked': True})
    result = _touch(act)
    assert result.success, result.error
    assert bridge.get_entity('target')['hp'] == 10 and result.context['_steed_touch_restored'] == 0
    assert bridge.get_entity('steed')[CHARGE] is False


@pytest.mark.parametrize('changes,expected', [
    ({'steed_changes': {'creature_type': 'fey'}}, 'Only a Celestial'),
    ({'steed_changes': {'conditions': ['incapacitated']}}, 'cannot currently take'),
    ({'steed_changes': {CHARGE: False}}, 'own Long Rest'),
    ({'caster_changes': {'is_alive': False}}, 'living caster'),
    ({'target_changes': {'is_alive': False}}, 'authoritative living'),
    ({'position': (10.401, 0, 0)}, 'actually within 5 feet'),
    ({'plane': 'ethereal'}, 'same plane'),
    ({'combat': True, 'steed_changes': {'bonus_action_available': False}}, 'already been spent')])
def test_rejected_intent_keeps_rng_hp_charge_and_budget_unchanged(bound, changes, expected):
    bridge, state, act = _case(bound, **changes)
    before = _snapshot(bridge)
    rng_before = dice._rng.getstate()
    result = _touch(act)
    assert not result.success and expected in result.error
    assert _snapshot(bridge) == before
    assert dice._rng.getstate() == rng_before


def test_repeated_use_rejects_without_a_second_roll(bound, monkeypatch):
    monkeypatch.setattr(dice, '_rng', Random(0))
    bridge, state, act = _case(bound)
    result = _touch(act)
    assert result.success, result.error
    before, rng_before = _snapshot(bridge), dice._rng.getstate()
    rejected = _touch(act)
    assert not rejected.success and 'own Long Rest' in rejected.error
    assert _snapshot(bridge) == before and dice._rng.getstate() == rng_before


def test_saved_individual_return_preserves_spent_charge_and_new_individual_initializes(bound):
    for initial, expected in ((False, False), (None, True)):
        bridge, _, act = _case(bound, steed_changes={CHARGE: initial})
        result = act('finish_srd_steed_source_actor', caster_state='caster', actor_state='steed',
                     slot_level=4, attack_bonus=8, save_dc=16)
        assert result.success, result.error
        assert bridge.get_entity('steed')[CHARGE] is expected
        assert bridge.get_entity('steed')['created_spell_level'] == 4


def test_native_steed_long_rest_recharges_after_validated_completion(bound):
    bridge, state, act = _case(bound, steed_changes={CHARGE: False})
    started = act('begin_srd_rest', target_state='steed', kind='long', activity='sleep')
    assert started.success, started.error
    too_early = act('complete_srd_rest', target_state='steed')
    assert not too_early.success and 'eight hours' in too_early.error
    assert bridge.get_entity('steed')[CHARGE] is False
    bridge.write_field('state', 'lasting_tick', 9600)
    complete = act('complete_srd_rest', target_state='steed')
    assert complete.success, complete.error
    assert bridge.get_entity('steed')['rest_phase'] == 'complete'
    assert bridge.get_entity('steed')[CHARGE] is True


def test_caster_long_rest_does_not_recharge_the_steed(bound):
    bridge, state, act = _case(bound, steed_changes={CHARGE: False})
    started = act('begin_srd_rest', target_state='caster', kind='long', activity='sleep')
    assert started.success, started.error
    bridge.write_field('state', 'lasting_tick', 9600)
    complete = act('complete_srd_rest', target_state='caster')
    assert complete.success, complete.error
    assert bridge.get_entity('steed')[CHARGE] is False


def test_public_declaration_matches_real_parameters_and_owned_exposure(bound):
    from scripts.srd_lite_policy import bind_exposed_operations
    from sylvia_gm.application.package_runtime import _runtime_parameter_schema, _schemas_agree
    from sylvia_gm.domain.package_library import ToolDeclaration
    runtime = deepcopy(bound[1])
    record = {'tools': []}
    name = expose_steed_healing_touch(runtime, record)
    assert name == ACTION and len(record['tools']) == 1
    declaration = record['tools'][0]
    assert declaration['writers'] == ['dm'] and declaration['turn_statuses'] == ['open']
    assert declaration['state_predicates'] == []
    native_schema = _runtime_parameter_schema(bound[0].tools[ACTION])
    assert _schemas_agree(native_schema, ToolDeclaration(
        name=ACTION, capability=declaration['capability'], input_schema=declaration['input_schema'],
        state_predicates=(), turn_statuses=('open',), writers=('dm',)))
    assert set(declaration['input_schema']['required']) == {'steed_state', 'target_state'}
    assert declaration['input_schema']['properties']['lasting_state']['x-sylvia-gm-model-hidden'] is True
    # The builder's existing owned-operation path removes this one exact denial.
    denied = bind_exposed_operations(runtime, record)
    assert denied == [ACTION]
    expected = {'require': {'condition': 'false',
        'error': 'This spell operation is pending or excluded in the SRD Lite foundation.'}}
    assert runtime['tools'][ACTION]['effects'].pop(0) == expected
    assert runtime['tools'][ACTION]['effects'] == bound[1]['tools'][ACTION]['effects']
