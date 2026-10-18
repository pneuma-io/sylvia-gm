"""Verify adapted attack spells through admitted casts and persisted real engine receipts."""
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.runtime import _effect_executor
from tests.test_srd_lite_healing import healing_case


@pytest.mark.parametrize('spell,slot,sides,damage_count', [
    ('chill_touch', 0, 10, 4),
    ('guiding_bolt', 1, 6, 4), ('guiding_bolt', 3, 6, 6),
])
def test_single_spell_attack_base_upcast_cards_budget_retry_reopen(healing_case, monkeypatch, spell, slot, sides, damage_count):
    case = healing_case(spell)
    case.start()
    original = _effect_executor.dice_roll
    rolls = []

    def recorded(sides, count=1, modifier=0):
        result = original(sides, count, modifier)
        rolls.append(result)
        return result

    monkeypatch.setattr(_effect_executor, 'dice_roll', recorded)
    receipt, command = case.act('cast_spell', **(case.parameters | {'slot_resource': f'spell_slot_{slot}' if slot else 'cantrip'}))
    assert [roll.sides for roll in rolls] == [20, sides]
    assert len(rolls[1].results) == damage_count
    assert case.entities()['guard']['hp'] == 1000 - rolls[1].total
    if slot:
        assert case.entities()['hero']['resources'][f'spell_slot_{slot}'] == 2
    else:
        assert all(value == 3 for value in case.entities()['hero']['resources'].values())
    assert case.snapshot().state['sylvia.srd-lite.bg3.combat']['action_available'] is False
    assert not case.entities()['hero'].get('concentration_spell_id')
    detail = '\n'.join(row['value'] for row in receipt.result['result_card']['details'])
    assert str(rolls[0].results[0]) in detail
    assert str(rolls[1].total) in detail
    saved = case.snapshot()
    assert case.execute(command) == receipt
    case.reopen()
    assert case.execute(command) == receipt and case.snapshot().state == saved.state
    assert len(rolls) == 2


@pytest.mark.parametrize('spell', ['chill_touch', 'guiding_bolt'])
def test_spell_attack_miss_spends_once_without_damage_or_condition(healing_case, spell):
    case = healing_case(spell, guard_ac=100)
    case.start()
    case.act('cast_spell', **case.parameters)
    assert case.entities()['guard']['hp'] == 1000
    assert case.entities()['hero']['resources']['spell_slot_1'] == (3 if spell == 'chill_touch' else 2)
    assert case.snapshot().state['sylvia.srd-lite.bg3.combat']['action_available'] is False
    assert not case.lasting()['turn_effects']


@pytest.mark.parametrize('spell,base_dice', [('chill_touch', 4), ('guiding_bolt', 4)])
def test_real_natural_twenty_doubles_spell_damage_dice(healing_case, monkeypatch, spell, base_dice):
    case = healing_case(spell)
    monkeypatch.setattr(dice, '_rng', Random(5))
    original = _effect_executor.dice_roll
    rolls = []

    def recorded(sides, count=1, modifier=0):
        result = original(sides, count, modifier)
        rolls.append(result)
        return result

    monkeypatch.setattr(_effect_executor, 'dice_roll', recorded)
    case.cast()
    assert rolls[0].results == (20,)
    assert len(rolls[1].results) == 2 * base_dice
    assert case.entities()['guard']['hp'] == 1000 - rolls[1].total


@pytest.mark.parametrize('slot', [2, 4, 9])
def test_scorching_rays_independently_attack_split_roll_and_mitigate(healing_case, monkeypatch, slot):
    case = healing_case('scorching_ray', entity_changes={'guard': {'damage_resistances': ['fire'], 'temp_hp': 5}})
    case.start()
    class HitRng(Random):
        def randint(self, lower, upper):
            return 10 if (lower, upper) == (1, 20) else super().randint(lower, upper)

    monkeypatch.setattr(dice, '_rng', HitRng(1))
    count = slot + 1
    targets = ['guard', 'other', 'guard'] + ['extra'] * (count - 3)
    original = _effect_executor.dice_roll
    rolls = []

    def recorded(sides, count=1, modifier=0):
        result = original(sides, count, modifier)
        rolls.append(result)
        return result

    monkeypatch.setattr(_effect_executor, 'dice_roll', recorded)
    parameters = {key: value for key, value in case.parameters.items()
        if key not in ('target', 'target_distance', 'caster_can_see_target')}
    receipt, command = case.act('cast_spell', **(parameters | {'slot_resource': f'spell_slot_{slot}', 'attack_targets': targets}))
    assert [roll.sides for roll in rolls] == [20, 6] * count
    losses = dict.fromkeys(('guard', 'other', 'extra'), 0)
    for index, target in enumerate(targets):
        amount = rolls[2 * index + 1].total
        losses[target] += amount // 2 if target == 'guard' else amount
    for target, loss in losses.items():
        before = 1005 if target == 'guard' else 1000
        assert case.entities()[target]['hp'] + case.entities()[target]['temp_hp'] == before - loss
    assert case.entities()['hero']['resources'][f'spell_slot_{slot}'] == 2
    assert case.snapshot().state['sylvia.srd-lite.bg3.combat']['action_available'] is False
    assert len(receipt.result['result_card']['details']) >= count
    saved = case.snapshot()
    assert case.execute(command) == receipt
    case.reopen()
    assert case.execute(command) == receipt and case.snapshot().state == saved.state
    assert len(rolls) == count * 2


def test_scorching_ray_bad_count_rejects_without_spending(healing_case):
    case = healing_case('scorching_ray')
    parameters = {key: value for key, value in case.parameters.items()
        if key not in ('target', 'target_distance', 'caster_can_see_target')}
    case.reject('cast_spell_outside_combat', parameters | {'attack_targets': ['guard', 'other']}, match='ray target count|does not match the selected spell and scaling level')


def test_blinded_melee_attack_is_real_disadvantage_and_ranged_reach_is_capped(healing_case):
    melee = healing_case('chill_touch', entity_changes={'hero': {'conditions': ['blinded']}})
    melee.start()
    receipt, _ = melee.act('cast_spell', **melee.parameters)
    assert len(receipt.result['context']['_spell_attack']['results']) == 2
    ranged = healing_case('guiding_bolt', entity_changes={'hero': {'conditions': ['blinded']}},
        positions={'hero': [0, 0, 0], 'guard': [121, 0, 0], 'other': [-5, 0, 0], 'extra': [20, 10, 0]})
    ranged.start()
    ranged.reject('cast_spell', ranged.parameters | {'target_distance': 1}, match='actual combat recipient|actual spell-attack target')


@pytest.mark.parametrize('spell,limit', [('chill_touch', 5), ('guiding_bolt', 120), ('scorching_ray', 120)])
def test_attack_range_is_saved_and_rejection_is_atomic(healing_case, spell, limit):
    case = healing_case(spell, positions={'hero': [0, 0, 0], 'guard': [limit + 1, 0, 0], 'other': [-5, 0, 0], 'extra': [20, 10, 0]})
    parameters = case.parameters | {'target_distance': 0, 'targets_max_distance': 0, 'caster_can_touch_target': True}
    if spell == 'scorching_ray':
        parameters['attack_targets'] = ['guard', 'guard', 'guard']
    case.start()
    case.reject('cast_spell', parameters, match='actual combat recipient|actual spell-attack target')


def test_guiding_bolt_advantage_is_consumed_and_has_actual_two_turn_expiry(healing_case):
    case = healing_case('guiding_bolt')
    case.start()
    case.act('cast_spell', **case.parameters)
    assert any(row['spell'] == 'guiding_bolt' for row in case.lasting()['turn_effects'])
    case.next_turn()
    case.next_turn()
    attack, _ = case.attack()
    assert len(attack.result['context']['_attack_roll']['results']) == 2
    assert not any(row['spell'] == 'guiding_bolt' for row in case.lasting()['turn_effects'])
    elapsed = healing_case('guiding_bolt')
    elapsed.cast()
    elapsed.act('advance_srd_lasting_time', elapsed_rounds=1)
    assert not any(row['spell'] == 'guiding_bolt' for row in elapsed.lasting()['turn_effects'])
    unconsumed = healing_case('guiding_bolt')
    unconsumed.start()
    unconsumed.act('cast_spell', **unconsumed.parameters)
    unconsumed.next_turn()
    assert any(row['spell'] == 'guiding_bolt' for row in unconsumed.lasting()['turn_effects'])
    unconsumed.next_turn()
    assert any(row['spell'] == 'guiding_bolt' for row in unconsumed.lasting()['turn_effects'])
    unconsumed.next_turn()
    assert not any(row['spell'] == 'guiding_bolt' for row in unconsumed.lasting()['turn_effects'])
    unconsumed.reopen()
    assert not any(row['spell'] == 'guiding_bolt' for row in unconsumed.lasting()['turn_effects'])


@pytest.mark.parametrize('slot,modifier', [(1,-100),(1,100),(3,-100),(3,100)])
def test_inflict_wounds_native_constitution_save_upcast_half_damage_and_retry(healing_case, monkeypatch, slot, modifier):
    case = healing_case('inflict_wounds', guard_ac=100, entity_changes={'guard': {'saving_throw_modifiers': dict.fromkeys(('strength','dexterity','constitution','intelligence','wisdom','charisma'), modifier)}})
    case.start()
    rolls=[]
    original=_effect_executor.dice_roll
    def recorded(sides,count=1,modifier=0):
        roll=original(sides,count,modifier);rolls.append(roll);return roll
    monkeypatch.setattr(_effect_executor,'dice_roll',recorded)
    receipt,command=case.act('cast_spell',**(case.parameters|{'slot_resource':f'spell_slot_{slot}'}))
    assert [roll.sides for roll in rolls]==[20,10]
    assert len(rolls[1].results)==slot+1
    context=receipt.result['context']
    assert context['_spell_save_outcome']==('failure' if modifier<0 else 'success')
    assert 'constitution' in str(receipt.result['result_card']).lower()
    damage=rolls[1].total if modifier<0 else rolls[1].total//2
    assert case.entities()['guard']['hp']==1000-damage
    assert case.entities()['hero']['resources'][f'spell_slot_{slot}']==2
    assert case.snapshot().state['sylvia.srd-lite.bg3.combat']['action_available'] is False
    saved=case.snapshot();assert case.execute(command)==receipt and case.snapshot()==saved
    case.reopen();assert case.execute(command)==receipt and case.snapshot()==saved
    assert len(rolls)==2
