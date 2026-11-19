"""False Life follows SRD 5.2.1: rolled temporary HP, slot scaling, no stacking and rests.

False Life is one of the 21 preserved Full-only implementations in the 117-spell Lite roster
(product contract 3.2), so it keeps the official 2d4 + 4 pool rather than the cut Lite
fixed-pool profile. Temporary Hit Points never stack and end on a finished Long Rest.
"""
import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import ResolveMechanicsChoiceCommand
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.runtime import _effect_executor
from tests.test_srd_lite_healing import healing_case  # noqa: F401

# healing_case disables per-action seeding; a choice replays its action, so that test
# restores the owner's real seeded stream to reproduce the offered roll.
_SEEDED_ROLLS = dice.set_seed


def _self_parameters(case, **choices):
    return {key: value for key, value in case.parameters.items() if key != 'target'} | choices


def _record_rolls(monkeypatch):
    rolls = []
    original = _effect_executor.dice_roll

    def recorded(sides, count=1, modifier=0):
        result = original(sides, count, modifier)
        rolls.append(result)
        return result

    monkeypatch.setattr(_effect_executor, 'dice_roll', recorded)
    return rolls


def _pool(rolls, slot):
    (roll,) = rolls
    assert roll.sides == 4 and roll.count == 2
    return roll.total + 4 + 5 * (slot - 1)


@pytest.mark.parametrize('slot', [1, 3])
def test_rolled_pool_scales_with_its_slot_and_retries_persist(healing_case, monkeypatch, slot):
    case = healing_case('false_life')
    case.start()
    rolls = _record_rolls(monkeypatch)
    receipt, command = case.act('cast_spell', **_self_parameters(case, slot_resource=f'spell_slot_{slot}'))
    expected = _pool(rolls, slot)
    assert case.entities()['hero']['temp_hp'] == expected
    assert case.entities()['hero']['hp'] == 20
    assert case.entities()['hero']['resources'][f'spell_slot_{slot}'] == 2
    assert case.snapshot().state['sylvia.srd-lite.bg3.combat']['action_available'] is False
    details = [row['value'] for row in receipt.result['result_card']['details']]
    assert any(f'offered {expected}.' in text for text in details)
    saved = case.snapshot()
    assert case.execute(command) == receipt
    case.reopen()
    assert case.execute(command) == receipt and case.snapshot().state == saved.state
    assert len(rolls) == 1


@pytest.mark.parametrize('option', ['keep_existing', 'take_new'])
def test_existing_pool_does_not_stack_and_the_saved_choice_decides(healing_case, monkeypatch, option):
    case = healing_case('false_life', entity_changes={'hero': {'temp_hp': 20}})
    monkeypatch.setattr(dice, 'set_seed', _SEEDED_ROLLS)
    rolls = _record_rolls(monkeypatch)
    command = case.command('cast_spell_outside_combat', _self_parameters(case, slot_resource='spell_slot_1'))
    pending = case.owner.execute_action(command)
    assert pending.status == 'awaiting_choice'
    assert pending.choice.choice_id == 'temporary-hit-point-replacement'
    assert case.entities()['hero']['temp_hp'] == 20
    receipt = case.owner.resolve_mechanics_choice(ResolveMechanicsChoiceCommand(
        campaign_id=command.campaign_id, action_id=command.action_id,
        resolution_id=command.action_id + '-' + option, owner_id=command.owner_id,
        expected_revision=pending.basis_revision, choice_id=pending.choice.choice_id,
        option_id=option, choice_sequence=pending.choice_sequence))
    assert receipt.status is ActionStatus.COMMITTED, receipt.result
    offered = _pool(rolls[-1:], 1)
    assert case.entities()['hero']['temp_hp'] == (20 if option == 'keep_existing' else offered)
    assert case.entities()['hero']['resources']['spell_slot_1'] == 2
    saved = case.snapshot()
    case.reopen()
    assert case.snapshot().state == saved.state
    # The suspended request and its resolution replay the same seeded roll.
    assert len({roll.results for roll in rolls}) == 1


def test_preparation_change_keeps_false_life_temporary_hit_points(healing_case, monkeypatch):
    case = healing_case('false_life', participant_changes={'wizard_spellbook': ['false_life', 'cloud_of_daggers']})
    rolls = _record_rolls(monkeypatch)
    case.act('cast_spell_outside_combat', **_self_parameters(case))
    pool = _pool(rolls, 1)
    case.act('begin_srd_rest', target_state='hero', kind='short', activity='light')
    case.act('advance_srd_lasting_time', elapsed_rounds=600)
    case.act('complete_srd_rest', target_state='hero')
    assert case.entities()['hero']['temp_hp'] == pool
    case.act('prepare_srd_spells', caster='hero', caster_state='hero', source='wizard', spells=['cloud_of_daggers'], spellbook_available=True)
    # Temporary Hit Points last until depleted or a Long Rest; preparation does not end them.
    assert 'false_life' not in case.entities()['hero']['prepared_spells']
    assert case.entities()['hero']['temp_hp'] == pool
    case.reopen()
    assert case.entities()['hero']['temp_hp'] == pool


def test_genuine_long_rest_clears_the_pool(healing_case):
    case = healing_case('false_life')
    case.act('cast_spell_outside_combat', **_self_parameters(case))
    assert case.entities()['hero']['temp_hp'] > 0
    case.act('begin_srd_rest', target_state='hero', kind='long', activity='sleep')
    case.act('advance_srd_lasting_time', elapsed_rounds=4799)
    assert case.entities()['hero']['temp_hp'] > 0
    case.act('advance_srd_lasting_time', elapsed_rounds=1)
    case.act('complete_srd_rest', target_state='hero')
    assert case.entities()['hero']['temp_hp'] == 0
    case.reopen()
    assert case.entities()['hero']['temp_hp'] == 0


def test_real_creature_attack_consumes_false_life_pool_before_hp(healing_case, monkeypatch):
    case = healing_case('false_life')
    case.start()
    rolls = _record_rolls(monkeypatch)
    case.act('cast_spell', **_self_parameters(case))
    pool = _pool(rolls, 1)
    case.next_turn()
    receipt, command = case.act('resolve_adjudicated_creature_attack',
        target='hero', attack_name='Synthetic claw', attack_mode='melee',
        attack_distance=5, normal_range=5, long_range=5, attack_modifier=100,
        damage_expression='d4', damage_modifier=0, damage_type='slashing',
        advantage=False, disadvantage=False, actor_can_see_target=True,
        target_can_see_actor=True, target_is_charmer=False, fear_source_in_sight=False,
        attack_uses_weapon=False, use_multiattack=False)
    damage = [roll for roll in rolls[1:] if roll.sides == 4]
    assert len(damage) == 1
    amount = damage[0].total
    assert case.entities()['hero']['temp_hp'] == max(0, pool - amount)
    assert case.entities()['hero']['hp'] == 20 - max(0, amount - pool)
    saved = case.snapshot()
    assert case.execute(command) == receipt
    case.reopen()
    assert case.snapshot().state == saved.state and case.execute(command) == receipt
