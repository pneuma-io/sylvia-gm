"""Verify real admitted healing casts, budgets, receipts and persisted transactions."""
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.runtime import _effect_executor
from tests.lite_fixture_archive import archive_closed_case
from tests.test_srd_lite_foundation import Case, VARIANT_ID


@pytest.fixture
def healing_case(tmp_path, monkeypatch):
    monkeypatch.setattr(dice, '_rng', Random(1))
    monkeypatch.setattr(dice, 'set_seed', lambda _seed: None)
    opened = []

    def make(spell, **changes):
        case = Case(tmp_path / f'{len(opened)}.db', spell, **changes)
        opened.append(case)
        return case

    yield make
    for case in opened:
        archive_closed_case(case)


@pytest.mark.parametrize('spell,sides,slot', [
    ('cure_wounds', 8, 1), ('cure_wounds', 8, 3),
    ('healing_word', 4, 1), ('healing_word', 4, 3),
])
def test_admitted_healing_cast_actual_dice_budget_card_retry_reopen(healing_case, monkeypatch, spell, sides, slot):
    case = healing_case(spell, entity_changes={'guard': {'hp': 0, 'max_hp': 100, 'conditions': ['unconscious'], 'death_successes': 1, 'death_failures': 2}})
    case.start()
    rolls = []
    original = _effect_executor.dice_roll

    def recorded(sides, count=1, modifier=0):
        result = original(sides, count, modifier)
        rolls.append(result)
        return result

    monkeypatch.setattr(_effect_executor, 'dice_roll', recorded)
    before_resources = dict(case.entities()['hero']['resources'])
    receipt, command = case.act('cast_spell', **(case.parameters | {'slot_resource': f'spell_slot_{slot}'}))
    # SRD 5.2 Cure Wounds/Healing Word use two base dice and two per higher slot.
    assert len(rolls) == 1 and rolls[0].sides == sides and len(rolls[0].results) == 2 * slot
    target = case.entities()['guard']
    assert target['hp'] == rolls[0].total + 4
    assert 'unconscious' not in target['conditions']
    assert target['death_successes'] == target['death_failures'] == 0
    before_resources[f'spell_slot_{slot}'] -= 1
    assert case.entities()['hero']['resources'] == before_resources
    combat = case.snapshot().state[VARIANT_ID + '.combat']
    assert combat['action_available'] is (spell == 'healing_word')
    assert case.entities()['hero']['bonus_action_available'] is (spell == 'cure_wounds')
    assert not case.entities()['hero'].get('concentration_spell_id')
    detail = '\n'.join(row['value'] for row in receipt.result['result_card']['details'])
    assert f'HP restored {target["hp"]}' in detail
    faces = ', '.join(str(face) for face in rolls[0].results)
    assert f'Healing dice {faces}; {rolls[0].total} + (4)' in detail
    saved = case.snapshot()
    assert case.execute(command) == receipt and case.snapshot().state == saved.state
    case.reopen()
    assert case.execute(command) == receipt and case.snapshot().state == saved.state
    assert len(rolls) == 1


@pytest.mark.parametrize('spell', ['cure_wounds', 'healing_word'])
def test_healing_hp_cap_and_final_dice_header(healing_case, spell):
    case = healing_case(spell, entity_changes={'guard': {'hp': 999}})
    receipt, _ = case.cast()
    assert case.entities()['guard']['hp'] == 1000
    assert case.entities()['hero']['resources']['spell_slot_1'] == 2
    assert any('HP restored 1' in row['value'] for row in receipt.result['result_card']['details'])


def test_caster_can_heal_self_using_saved_scene_classification(healing_case):
    case = healing_case('cure_wounds', entity_changes={'hero': {'hp': 10}})
    case.cast(target='hero')
    assert 10 < case.entities()['hero']['hp'] <= 20


@pytest.mark.parametrize('spell', ['cure_wounds', 'healing_word'])
def test_actual_blindness_blocks_word_but_not_touch(healing_case, spell):
    case = healing_case(spell, entity_changes={'hero': {'conditions': ['blinded']}, 'guard': {'hp': 900}})
    case.start()
    if spell == 'healing_word':
        case.reject('cast_spell', case.parameters | {'caster_can_see_target': True}, match='see|sight')
    else:
        case.act('cast_spell', **(case.parameters | {'caster_can_see_target': False}))
        assert case.entities()['guard']['hp'] > 900


@pytest.mark.parametrize('creature_type', ['undead', 'construct'])
def test_srd_52_healing_affects_creatures_without_the_old_type_exclusion(healing_case, creature_type):
    # SRD 5.2.1 Cure Wounds has no Undead/Construct exclusion. Keep the old
    # pre-migration draft assertions in the archive; verify the actual final Lite rule.
    case = healing_case('cure_wounds', entity_changes={'guard': {'creature_type': creature_type, 'hp': 900}})
    before = case.entities()['hero']['resources']['spell_slot_1']
    receipt, command = case.cast()
    saved = case.snapshot()
    assert case.entities()['guard']['hp'] > 900
    assert case.entities()['hero']['resources']['spell_slot_1'] == before - 1
    assert case.execute(command) == receipt and case.snapshot() == saved
    case.reopen()
    assert case.snapshot().state == saved.state


def test_dead_healing_target_rejects_without_spend(healing_case):
    case = healing_case('cure_wounds', entity_changes={'guard': {'is_alive': False, 'hp': 0}})
    case.reject('cast_spell_outside_combat', case.parameters, match='dead creature')


@pytest.mark.parametrize('spell,limit', [('cure_wounds', 5), ('healing_word', 60)])
def test_forged_caller_range_cannot_bypass_scene(healing_case, spell, limit):
    case = healing_case(spell, positions={'hero': [0, 0, 0], 'guard': [limit + 1, 0, 0], 'other': [-5, 0, 0], 'extra': [20, 10, 0]})
    case.start()
    case.reject('cast_spell', case.parameters | {'target_distance': 0, 'caster_can_touch_target': True, 'caster_can_see_target': True}, match='range|actual|touch')


def test_earned_long_rest_replaces_a_real_cleric_spell_and_persists(healing_case):
    case = healing_case('cure_wounds', entity_changes={'guard': {'hp': 900}})
    preparation = {'caster': 'hero', 'caster_state': 'hero', 'source': 'cleric', 'spells': ['healing_word'], 'spellbook_available': True}
    case.reject('prepare_srd_spells', preparation, match='rest')
    case.act('begin_srd_rest', target_state='hero', kind='long', activity='sleep')
    case.act('advance_srd_lasting_time', elapsed_rounds=4800)
    case.act('complete_srd_rest', target_state='hero')
    before = case.snapshot()
    before_resources = dict(case.entities()['hero']['resources'])
    case.reject('prepare_srd_spells', preparation | {'spells': ['lightning_bolt']}, match='not granted')
    receipt, command = case.act('prepare_srd_spells', **preparation)
    saved = case.snapshot()
    assert saved.participants[0].data['spellcasting_grants'] == [{'source': 'cleric', 'ability': 'wisdom', 'spells': ['healing_word']}]
    assert case.entities()['hero']['prepared_spells'] == ['healing_word']
    assert case.entities()['hero']['resources'] == before_resources
    assert saved.package_lock == before.package_lock
    assert case.execute(command) == receipt
    case.reopen()
    assert case.snapshot().participants == saved.participants
    case.cast(spell='healing_word', slot_resource='spell_slot_1')
    assert case.entities()['guard']['hp'] > 900
    assert case.entities()['hero']['resources']['spell_slot_1'] == before_resources['spell_slot_1'] - 1
    case.reject('prepare_srd_spells', preparation | {'spells': ['cure_wounds']}, match='rest')


def test_public_npc_preparation_casts_its_saved_modifier_and_daily_budget(healing_case, monkeypatch):
    case = healing_case('cure_wounds', positions={'hero': [0, 0, 0], 'guard': [5, 0, 0], 'other': [-5, 0, 0], 'extra': [20, 10, 0], 'mage': [1, 0, 0]}, entity_changes={'guard': {'hp': 900}})
    case.act('prepare_srd_creature', creature_id='mage', name='Synthetic healer', source='Synthetic stat block: Wisdom +3, one third-level Cure Wounds daily.', armor_class=12, hit_points=25, speed=30, initiative_modifier=0, size_rank=2, constitution_save=0, spellcasting_ability='wisdom', spellcasting_modifier=3, spell_attack_bonus=7, spell_save_dc=15, spells=[{'spell': 'cure_wounds', 'uses_per_day': 1, 'casting_level': 3}])
    parameters = {key: value for key, value in case.parameters.items() if key not in ('caster', 'slot_resource')}
    parameters['caster_state'] = 'mage'
    original = _effect_executor.dice_roll
    rolls = []

    def recorded(sides, count=1, modifier=0):
        result = original(sides, count, modifier)
        rolls.append(result)
        return result

    monkeypatch.setattr(_effect_executor, 'dice_roll', recorded)
    receipt, command = case.act('cast_spell_outside_combat', **parameters)
    assert len(rolls) == 1 and rolls[0].sides == 8 and len(rolls[0].results) == 6
    assert case.entities()['guard']['hp'] == 900 + rolls[0].total + 3
    assert case.entities()['mage']['spell_uses_remaining']['cure_wounds'] == 0
    assert case.entities()['mage']['resources'] == {}
    saved = case.snapshot()
    assert case.execute(command) == receipt
    case.reopen()
    assert case.snapshot().state == saved.state
    case.reject('cast_spell_outside_combat', parameters, match='no uses')
    assert len(rolls) == 1
