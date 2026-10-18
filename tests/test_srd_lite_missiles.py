"""Actual admitted force darts retain per-dart defenses and once-only receipts."""
import pytest

from sylvia_gm.runtime import _effect_executor
from tests.test_srd_lite_healing import healing_case


@pytest.mark.parametrize('slot', [1, 3])
def test_split_force_darts_roll_each_face_and_apply_resistance_per_dart(healing_case, monkeypatch, slot):
    case = healing_case('magic_missile', entity_changes={'guard': {'damage_resistances': ['force'], 'temp_hp': 5}})
    case.start()
    original = _effect_executor.dice_roll
    rolls = []

    def recorded(sides, count=1, modifier=0):
        result = original(sides, count, modifier)
        rolls.append(result)
        return result

    monkeypatch.setattr(_effect_executor, 'dice_roll', recorded)
    count = slot + 2
    allocation = ['guard', 'other', 'guard'] + ['extra'] * (count - 3)
    before = {name: dict(case.entities()[name]) for name in ('guard', 'other', 'extra')}
    receipt, command = case.act('cast_spell', **(case.parameters | {'slot_resource': f'spell_slot_{slot}', 'projectile_targets': allocation}))
    assert len(rolls) == 1 and rolls[0].sides == 4 and len(rolls[0].results) == count
    expected = {name: 0 for name in before}
    for target, face in zip(allocation, rolls[0].results, strict=True):
        expected[target] += (face + 1) // 2 if target == 'guard' else face + 1
    for target, amount in expected.items():
        current = case.entities()[target]
        assert before[target]['hp'] + before[target]['temp_hp'] - current['hp'] - current['temp_hp'] == amount
    assert case.entities()['hero']['resources'][f'spell_slot_{slot}'] == 2
    assert case.snapshot().state['sylvia.srd-lite.bg3.combat']['action_available'] is False
    details = [row['value'] for row in receipt.result['result_card']['details']]
    for index, face in enumerate(rolls[0].results, 1):
        assert any(f'Dart {index}.' in text and f'd4: {face}' in text for text in details)
    saved = case.snapshot()
    assert case.execute(command) == receipt
    case.reopen()
    assert case.execute(command) == receipt and case.snapshot().state == saved.state
    assert len(rolls) == 1


def test_later_allocated_darts_do_not_rollback_a_target_killed_by_the_first(healing_case):
    case = healing_case('magic_missile', entity_changes={'guard': {'hp': 1}})
    receipt, _ = case.cast(projectile_targets=['guard', 'guard', 'guard'])
    assert case.entities()['guard']['is_alive'] is False
    assert case.entities()['hero']['resources']['spell_slot_1'] == 2
    assert any('target alive before dart: false' in row['value'].lower() for row in receipt.result['result_card']['details'])


def test_missile_actual_range_and_bad_allocation_are_atomic(healing_case):
    case = healing_case('magic_missile', positions={'hero': [0, 0, 0], 'guard': [61, 0, 0], 'other': [1, 0, 0], 'extra': [2, 0, 0]})
    case.reject('cast_spell_outside_combat', case.parameters | {'target_distance': 0, 'projectile_targets': ['guard', 'other', 'other']}, match='actual dart target')


def test_saved_shield_blocks_each_dart_without_extra_spend(healing_case):
    case = healing_case('magic_missile', entity_changes={'guard': {'reaction_shield': {'expires_turn': 100, 'expires_tick': 100, 'trigger_id': 'synthetic-saved-shield'}}})
    case.cast(projectile_targets=['guard', 'guard', 'guard'])
    assert case.entities()['guard']['hp'] == 1000
    assert case.entities()['hero']['resources']['spell_slot_1'] == 2


def test_wrong_projectile_count_rejects_atomically(healing_case):
    case = healing_case('magic_missile')
    case.reject('cast_spell_outside_combat', case.parameters | {'projectile_targets': ['guard', 'other']}, match='projectile')
