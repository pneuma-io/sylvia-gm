"""Fixed temporary HP use authentic budgets, damage ownership and earned rests."""
import pytest

import json

from sylvia_gm.domain.errors import MechanicsRejected
from sylvia_gm.runtime import _effect_executor
from tests.test_srd_lite_healing import healing_case


def _self_parameters(case, **choices):
    return {key: value for key, value in case.parameters.items() if key != 'target'} | choices


@pytest.mark.parametrize('slot,existing,expected', [(1, 0, 7), (3, 0, 17), (1, 20, 20)])
def test_fixed_pool_does_not_roll_or_stack_and_retries_persist(healing_case, monkeypatch, slot, existing, expected):
    case = healing_case('false_life', entity_changes={'hero': {'temp_hp': existing}})
    case.start()

    def unexpected_roll(*args, **kwargs):
        raise AssertionError('False Life must not roll dice')

    monkeypatch.setattr(_effect_executor, 'dice_roll', unexpected_roll)
    if slot == 1 and existing == 0:
        before_invalid = case.snapshot()
        with pytest.raises(MechanicsRejected) as failure:
            case.execute(case.command('cast_spell', case.parameters | {'target': None}))
        print(json.dumps({'null_target_rejection_code': failure.value.code,
                          'field_paths': failure.value.field_paths,
                          'issues': [issue.as_payload() for issue in failure.value.validation_issues]}), flush=True)
        assert any(path == 'target' or path.endswith('.target') for path in failure.value.field_paths)
        assert failure.value.state_changed is False
        after_invalid = case.snapshot()
        assert after_invalid.revision == before_invalid.revision and after_invalid.state == before_invalid.state
    receipt, command = case.act('cast_spell', **_self_parameters(case, slot_resource=f'spell_slot_{slot}'))
    assert case.entities()['hero']['temp_hp'] == expected
    assert case.entities()['hero']['hp'] == 20
    assert case.entities()['hero']['resources'][f'spell_slot_{slot}'] == 2
    assert case.snapshot().state['sylvia.srd-lite.bg3.combat']['action_available'] is False
    assert any('without a roll' in row['value'] for row in receipt.result['result_card']['details'])
    saved = case.snapshot()
    assert case.execute(command) == receipt
    case.reopen()
    assert case.execute(command) == receipt and case.snapshot().state == saved.state


@pytest.mark.parametrize('replace_with_focus_pool', [False, True])
def test_short_rest_preparation_clears_only_false_life_owned_pool(healing_case, monkeypatch, replace_with_focus_pool):
    changes = {'participant_changes': {'wizard_spellbook': ['false_life', 'cloud_of_daggers']}}
    if replace_with_focus_pool:
        changes['participant_changes']['classes'] = [{'id': 'wizard', 'level': 7}, {'id': 'monk', 'level': 10}]
        changes['entity_changes'] = {'hero': {
            'resources': {**{f'spell_slot_{level}': 3 for level in range(1, 10)}, 'focus_point': 10},
            'resource_maxima': {'focus_point': 10},
        }}
    case = healing_case('false_life', **changes)
    case.act('cast_spell_outside_combat', **_self_parameters(case))
    assert case.entities()['hero']['temp_hp'] == 7
    independent_pool = 0
    if replace_with_focus_pool:
        case.start()
        actual_roll = _effect_executor.dice_roll
        focus_rolls = []

        def recorded(sides, count=1, modifier=0):
            result = actual_roll(sides, count, modifier)
            focus_rolls.append(result)
            return result

        with monkeypatch.context() as observed:
            observed.setattr(_effect_executor, 'dice_roll', recorded)
            focus, focus_command = case.act('use_monk_focus_action', actor='hero', actor_state='hero',
                feature='patient_defense', spend_focus=True, temporary_hp_choice='new')
        assert len(focus_rolls) == 1 and focus_rolls[0].sides == 8 and focus_rolls[0].count == 2
        independent_pool = focus_rolls[0].total
        assert case.entities()['hero']['temp_hp'] == independent_pool
        assert case.entities()['hero']['resources']['focus_point'] == 9
        assert case.entities()['hero']['bonus_action_available'] is False
        assert case.entities()['hero']['lite_false_life'] is False
        focus_saved = case.snapshot()
        assert case.execute(focus_command) == focus and case.snapshot().state == focus_saved.state
        case.reopen()
        assert case.execute(focus_command) == focus and case.snapshot().state == focus_saved.state
        case.act('end_combat_encounter', end_reason='fighting_stopped')
    case.act('begin_srd_rest', target_state='hero', kind='short', activity='light')
    case.act('advance_srd_lasting_time', elapsed_rounds=600)
    case.act('complete_srd_rest', target_state='hero')
    case.act('prepare_srd_spells', caster='hero', caster_state='hero', source='wizard', spells=['cloud_of_daggers'], spellbook_available=True)
    assert case.entities()['hero']['temp_hp'] == independent_pool
    assert case.entities()['hero']['lite_false_life'] is False
    case.reopen()


def test_genuine_long_rest_clears_the_fixed_pool(healing_case):
    case = healing_case('false_life')
    case.act('cast_spell_outside_combat', **_self_parameters(case))
    case.act('begin_srd_rest', target_state='hero', kind='long', activity='sleep')
    case.act('advance_srd_lasting_time', elapsed_rounds=4800)
    case.act('complete_srd_rest', target_state='hero')
    assert case.entities()['hero']['temp_hp'] == 0
    assert case.entities()['hero']['lite_false_life'] is False
    case.reopen()


def test_real_creature_attack_consumes_false_life_pool_before_hp(healing_case, monkeypatch):
    case = healing_case('false_life')
    case.start()
    case.act('cast_spell', **_self_parameters(case))
    case.next_turn()
    original = _effect_executor.dice_roll
    rolls = []

    def recorded(sides, count=1, modifier=0):
        result = original(sides, count, modifier)
        rolls.append(result)
        return result

    monkeypatch.setattr(_effect_executor, 'dice_roll', recorded)
    receipt, command = case.act('resolve_adjudicated_creature_attack',
        target='hero', attack_name='Synthetic claw', attack_mode='melee',
        attack_distance=5, normal_range=5, long_range=5, attack_modifier=100,
        damage_expression='d4', damage_modifier=0, damage_type='slashing',
        advantage=False, disadvantage=False, actor_can_see_target=True,
        target_can_see_actor=True, target_is_charmer=False, fear_source_in_sight=False,
        attack_uses_weapon=False, use_multiattack=False)
    damage = [roll for roll in rolls if roll.sides == 4]
    assert len(damage) == 1
    amount = damage[0].total
    assert case.entities()['hero']['temp_hp'] == max(0, 7 - amount)
    assert case.entities()['hero']['hp'] == 20 - max(0, amount - 7)
    saved = case.snapshot()
    assert case.execute(command) == receipt
    case.reopen()
    assert case.snapshot().state == saved.state and case.execute(command) == receipt
