"""Fell Glare composes real scene, D20, control sources, Bonus Action and turn owners."""
from copy import deepcopy
import json, os
from pathlib import Path
from random import Random
import pytest
from scripts.srd_lite_steed_fell_glare import ACTION, EXPIRE, PROFILE, bind_steed_fell_glare, expose_steed_fell_glare
from scripts.srd_lite_steed_healing_touch import CHARGE
from sylvia_gm import dice
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge, _json_safe, _runtime_parameter_schema, _schemas_agree
from sylvia_gm.application.result_cards import project_result_card
from sylvia_gm.application.result_card_admission import validate_result_card_declaration
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget
from tests.test_srd_lite_steed_casting_statistics import bound as statistics_bound
from tests.test_srd_lite_steed_healing_touch import bound as touch_bound, _case, _snapshot
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _calls, _wall


@pytest.fixture(scope='module')
def bound(statistics_bound):
    data = deepcopy(statistics_bound[1])
    native_save = deepcopy(data['tools']['resolve_srd_timed_condition_initial_save'])
    native_bonus = deepcopy(data['tools']['consume_srd_bonus_action'])
    native_d20 = deepcopy(data['tools']['d20_test'])
    native_move = deepcopy(data['tools']['check_srd_control_scene_move'])
    profiles = deepcopy(data['definitions']['spells'])
    changed = bind_steed_fell_glare(data)
    assert data['definitions']['spells'] == profiles
    assert data['tools']['resolve_srd_timed_condition_initial_save'] == native_save
    assert data['tools']['consume_srd_bonus_action'] == native_bonus
    assert data['tools']['d20_test'] == native_d20
    assert data['tools']['check_srd_control_scene_move'] == native_move
    closure, pending = {}, [ACTION, EXPIRE, 'd20_test', 'check_srd_control_scene_move',
                           'process_srd_turn_effects', 'refresh_srd_timed_condition_target']
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data['tools'][name]
            pending.extend(_calls(closure[name]['effects']))
    parsed = RulesetRuntime(ruleset_dict={**{key: value for key, value in data.items()
        if key not in {'tools', 'procedures'}}, 'tools': closure})
    validate_result_card_declaration(data['tools'][ACTION]['metadata']['result_card'])
    return parsed, data, changed


def _glare_case(bound, *, combat=True, steed_changes=None, target_changes=None,
                caster_changes=None, position=(25, 0, 0), plane='material', geometry=None):
    _, state, _ = _case(bound, combat=combat, position=position, plane=plane,
        steed_changes={'creature_type': 'fiend', 'turns_started': 20, **(steed_changes or {})},
        caster_changes={'turns_started': 7,
            'spellcasting_profile': {'ability': 'charisma', 'attack_bonus': 9, 'save_dc': 17},
            **(caster_changes or {})}, target_changes={'saving_throw_modifiers': {'wisdom': 2},
            **(target_changes or {})})
    if geometry is not None:
        state[NAMESPACE + '.state']['spatial_scene']['geometry'] = geometry
    bridge = AuthorityMemoryStateBridge(NAMESPACE, state)
    parsed = bound[0]
    def act(name, **parameters):
        def nested(tool, args, parent):
            return execute_tool(tool, args, parsed.tools, bridge, parsed.ruleset_config,
                parsed.definitions, combat_state=bridge.combat_state,
                tool_executor=nested, budget=parent.budget)
        return execute_tool(name, parameters, parsed.tools, bridge, parsed.ruleset_config,
            parsed.definitions, combat_state=bridge.combat_state,
            tool_executor=nested, budget=ExecutionBudget())
    return bridge, act


def _glare(act):
    return act(ACTION, steed_state='steed', target_state='target')


def _card(bound, result):
    return project_result_card(bound[1]['tools'][ACTION]['metadata']['result_card'],
        parameters={'steed_state': 'steed', 'target_state': 'target'}, public_context=_json_safe(result.context))


def test_failed_native_save_installs_source_and_real_d20_card(bound, monkeypatch):
    monkeypatch.setattr(dice, '_rng', Random(2))
    bridge, act = _glare_case(bound)
    result = _glare(act)
    assert result.success, result.error
    assert result.context['_steed_save_dc'] == 17
    assert result.context['_steed_glare_outcome'] == 'failure'
    target, steed, state = bridge.get_entity('target'), bridge.get_entity('steed'), bridge.get_entity('state')
    assert 'frightened' in target['conditions'] and steed[CHARGE] is False
    assert steed['bonus_action_available'] is False
    assert target['control_fear_active'] is False and target['affliction_movement_pending'] is False
    source_id = state['steed_glare_source_ids'][0]
    source = state['timed_condition_sources'][source_id]
    assert source['control_profile'] == PROFILE and source['caster_state_id'] == 'steed'
    assert source['steed_glare_caster_id'] == 'caster' and source['steed_glare_end_turn'] == 8
    assert source['repeat_save_timing'] == 'none' and source['source_is_magical'] is False
    assert source['dispel_ongoing'] is False and source['ongoing_duration'] is False
    assert source_id in target['control_affliction_source_ids']
    card = _card(bound, result)
    assert card is not None and card['title'] == 'Fell Glare'
    assert next(row for row in card['summary'] if row['label'] == 'Condition')['value'] == 'Frightened'
    equation = card['summary'][-1]['equation']
    assert equation == {'die_sides': 20, 'roll': 2, 'modifier': 2, 'total': 4}
    if os.environ.get('SRD_LITE_STEED_GLARE_CARD'):
        Path(os.environ['SRD_LITE_STEED_GLARE_CARD']).write_text(json.dumps(card, indent=2), encoding='utf-8')


def test_native_success_uses_charge_without_fear_source(bound, monkeypatch):
    monkeypatch.setattr(dice, '_rng', Random(5))
    bridge, act = _glare_case(bound, combat=False)
    result = _glare(act)
    assert result.success, result.error
    assert result.context['_steed_glare_outcome'] == 'success'
    assert 'frightened' not in bridge.get_entity('target')['conditions']
    assert bridge.get_entity('steed')[CHARGE] is False
    assert bridge.get_entity('steed')['bonus_action_available'] is True
    assert next(row for row in _card(bound, result)['summary']
                if row['label'] == 'Condition')['value'] == 'None'


def test_native_immunity_consumes_charge_without_rng_or_fake_dice(bound):
    bridge, act = _glare_case(bound, target_changes={'condition_immunities': ['frightened']})
    rng_before = dice._rng.getstate()
    result = _glare(act)
    assert result.success, result.error
    assert result.context['_steed_glare_outcome'] == 'immune'
    assert dice._rng.getstate() == rng_before
    assert 'frightened' not in bridge.get_entity('target')['conditions']
    assert bridge.get_entity('steed')[CHARGE] is False
    card = _card(bound, result)
    assert card is not None and 'equation' not in card['summary'][-1]
    assert next(row for row in card['summary'] if row['label'] == 'Condition')['value'] == 'None'
    assert card['details'][0]['value'] == 'Immune to Frightened; no save rolled.'


def test_source_expires_on_summoner_next_end_not_steed_or_target(bound, monkeypatch):
    monkeypatch.setattr(dice, '_rng', Random(2))
    bridge, act = _glare_case(bound)
    assert _glare(act).success
    for actor, turn in (('steed', 21), ('target', 99), ('caster', 7)):
        result = act('process_srd_turn_effects', lasting_state='state', boundary_actor=actor,
                     boundary_phase='end', boundary_turn=turn)
        assert result.success, result.error
        assert 'frightened' in bridge.get_entity('target')['conditions']
    bridge.write_field('caster', 'turns_started', 8)
    result = act('process_srd_turn_effects', lasting_state='state', boundary_actor='caster',
                 boundary_phase='end', boundary_turn=8)
    assert result.success, result.error
    assert 'frightened' not in bridge.get_entity('target')['conditions']
    assert bridge.get_entity('state')['steed_glare_source_ids'] == []


def test_expiry_preserves_an_independent_unowned_fear(bound, monkeypatch):
    monkeypatch.setattr(dice, '_rng', Random(2))
    bridge, act = _glare_case(bound, target_changes={'conditions': ['frightened'],
        'timed_condition_unowned_frightened': True})
    assert _glare(act).success
    result = act(EXPIRE, boundary_actor='caster', boundary_phase='end', boundary_turn=8)
    assert result.success, result.error
    assert 'frightened' in bridge.get_entity('target')['conditions']


def test_native_frightened_roll_and_movement_consumers_are_active(bound, monkeypatch):
    monkeypatch.setattr(dice, '_rng', Random(2))
    bridge, act = _glare_case(bound)
    assert _glare(act).success
    result = act('d20_test', actor_state='target', ability='wisdom', kind='ability_check',
        ability_modifier=2, proficiency_bonus=0, proficiency_factor='none',
        circumstantial_modifier=0, target_number=15)
    assert result.success, result.error
    assert result.context['_effective_disadvantage'] is True
    result = act('check_srd_control_scene_move', actor_state='target', destination=[20, 0, 0])
    assert not result.success and 'cannot willingly move closer' in result.error
    result = act('check_srd_control_scene_move', actor_state='target', destination=[30, 0, 0])
    assert result.success, result.error


@pytest.mark.parametrize('changes,error', [
    ({'steed_changes': {'creature_type': 'celestial'}}, 'Only a Fiend'),
    ({'steed_changes': {CHARGE: False}}, 'own Long Rest'),
    ({'steed_changes': {'bonus_action_available': False}}, 'already been spent'),
    ({'caster_changes': {'is_alive': False}}, 'living caster'),
    ({'target_changes': {'is_alive': False}}, 'authoritative living'),
    ({'position': (65.401, 0, 0)}, 'within 60 feet'),
    ({'plane': 'ethereal'}, 'same plane')])
def test_rejections_preserve_rng_charge_budget_and_actual_state(bound, changes, error):
    bridge, act = _glare_case(bound, **changes)
    before, rng_before = _snapshot(bridge), dice._rng.getstate()
    result = _glare(act)
    assert not result.success and error in result.error
    assert _snapshot(bridge) == before and dice._rng.getstate() == rng_before


def test_public_declaration_agrees_with_native_parameters(bound):
    record = {'tools': []}
    assert expose_steed_fell_glare(bound[1], record) == ACTION
    declaration = record['tools'][0]
    assert declaration['writers'] == ['dm'] and declaration['turn_statuses'] == ['open']
    assert declaration['input_schema']['required'] == ['steed_state', 'target_state']
    assert declaration['input_schema']['properties']['lasting_state']['x-sylvia-gm-model-hidden'] is True
    assert _schemas_agree(_runtime_parameter_schema(bound[0].tools[ACTION]),
        ToolDeclaration(name=ACTION, capability=declaration['capability'],
            input_schema=declaration['input_schema'], state_predicates=(), turn_statuses=('open',), writers=('dm',)))


def test_blocked_sight_preserves_rng_charge_budget_and_actual_state(bound):
    wall = _wall()
    wall['volume'].update(min=[12, -20, -10], max=[13, 20, 8])
    bridge, act = _glare_case(bound, geometry=[wall])
    before, rng_before = _snapshot(bridge), dice._rng.getstate()
    result = _glare(act)
    assert not result.success and 'must see' in result.error
    assert _snapshot(bridge) == before and dice._rng.getstate() == rng_before


def test_round_clock_cannot_end_glare_before_summoner_next_turn(bound, monkeypatch):
    monkeypatch.setattr(dice, '_rng', Random(2))
    bridge, act = _glare_case(bound)
    assert _glare(act).success
    bridge.write_field('state', 'lasting_tick', 2)
    result = act('refresh_srd_timed_condition_target', target_state='target', lasting_state='state')
    assert result.success, result.error
    assert 'frightened' in bridge.get_entity('target')['conditions']
    result = act(EXPIRE, boundary_actor='caster', boundary_phase='end', boundary_turn=8)
    assert result.success, result.error
    assert 'frightened' not in bridge.get_entity('target')['conditions']


def test_free_time_uses_native_six_second_fallback_expiry(bound, monkeypatch):
    monkeypatch.setattr(dice, '_rng', Random(2))
    bridge, act = _glare_case(bound, combat=False)
    assert _glare(act).success
    for tick, active in ((1.999, True), (2, False)):
        bridge.write_field('state', 'lasting_tick', tick)
        result = act('refresh_srd_timed_condition_target', target_state='target', lasting_state='state')
        assert result.success, result.error
        assert ('frightened' in bridge.get_entity('target')['conditions']) is active
