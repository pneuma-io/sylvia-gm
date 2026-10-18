"""UNRUN native mechanics regressions; prevalidated fixture is not acquisition proof.

Integrate alongside existing tests only after the main owner composes and admits
the repaired candidate. This file must not be executed during the access block.
"""
import pytest

from sylvia_gm.runtime import _effect_executor
from tests.test_srd_lite_healing import healing_case
from tests.test_srd_lite_false_life import _self_parameters
from tests.test_srd_lite_defenses import _public_routes


def _overlap(healing_case):
    return healing_case('false_life', participant_changes={
        'classes': [{'id': 'wizard', 'level': 16}, {'id': 'sorcerer', 'level': 1}],
        'wizard_spellbook': ['false_life', 'cloud_of_daggers'],
        'spellcasting_grants': [
            {'source': 'wizard', 'ability': 'intelligence', 'spells': ['false_life']},
            {'source': 'sorcerer', 'ability': 'charisma', 'spells': ['false_life']},
        ],
    })


def _assert_tracking(case, active, source):
    hero = case.entities()['hero']
    assert hero['lite_false_life'] is active
    assert hero['lite_false_life_prepared'] is active
    assert hero['lite_false_life_prepared_source'] == source


def _cast(case, source, slot='spell_slot_1'):
    return case.act('cast_spell_outside_combat',
        **_self_parameters(case, spell_grant=source, slot_resource=slot))


def _replay(case, receipt, command):
    saved = case.snapshot()
    assert case.execute(command) == receipt and case.snapshot().state == saved.state
    case.reopen()
    assert case.execute(command) == receipt and case.snapshot().state == saved.state


def _unprepare_wizard(case):
    case.act('begin_srd_rest', target_state='hero', kind='short', activity='light')
    case.act('advance_srd_lasting_time', elapsed_rounds=600)
    case.act('complete_srd_rest', target_state='hero')
    receipt, command = case.act('prepare_srd_spells', caster='hero', caster_state='hero',
        source='wizard', spells=['cloud_of_daggers'], spellbook_available=True)
    assert 'false_life' in case.entities()['hero']['prepared_spells']
    _replay(case, receipt, command)


@pytest.mark.parametrize('first,second,slot,owner,remaining', [
    ('wizard', 'sorcerer', 'spell_slot_1', 'wizard', 0),
    ('sorcerer', 'wizard', 'spell_slot_1', 'sorcerer', 7),
    ('sorcerer', 'wizard', 'spell_slot_2', 'wizard', 0),
])
def test_actual_overlap_pool_winner_retirement_and_reopen(
        healing_case, monkeypatch, first, second, slot, owner, remaining):
    case = _overlap(healing_case)

    def forbidden_dice(*args, **kwargs):
        raise AssertionError('False Life cannot roll')

    monkeypatch.setattr(_effect_executor, 'dice_roll', forbidden_dice)
    hp = case.entities()['hero']['hp']
    for source, resource in ((first, 'spell_slot_1'), (second, slot)):
        before = case.entities()['hero']['resources'][resource]
        receipt, command = _cast(case, source, resource)
        assert case.entities()['hero']['resources'][resource] == before - 1
        assert any('without a roll' in row['value'] for row in receipt.result['result_card']['details'])
        _replay(case, receipt, command)
    _assert_tracking(case, True, owner)
    assert case.entities()['hero']['temp_hp'] == (12 if slot == 'spell_slot_2' else 7)
    _unprepare_wizard(case)
    assert case.entities()['hero']['temp_hp'] == remaining
    assert case.entities()['hero']['hp'] == hp
    _assert_tracking(case, bool(remaining), owner if remaining else '')


def test_public_focus_replacement_clears_source_before_wizard_retirement(healing_case):
    case = healing_case('false_life', participant_changes={
        'wizard_spellbook': ['false_life', 'cloud_of_daggers'],
        'classes': [{'id': 'wizard', 'level': 7}, {'id': 'monk', 'level': 10}],
    }, entity_changes={'hero': {
        'resources': {**{f'spell_slot_{level}': 3 for level in range(1, 10)}, 'focus_point': 10},
        'resource_maxima': {'focus_point': 10},
        'martial_arts_die': 'd8',
    }})
    _cast(case, 'wizard')
    _assert_tracking(case, True, 'wizard')
    case.start()
    receipt, command = case.act('use_monk_focus_action', actor='hero', actor_state='hero',
        feature='patient_defense', spend_focus=True, temporary_hp_choice='new')
    independent = case.entities()['hero']['temp_hp']
    hp = case.entities()['hero']['hp']
    _assert_tracking(case, False, '')
    _replay(case, receipt, command)
    case.act('end_combat_encounter', end_reason='fighting_stopped')
    case.act('begin_srd_rest', target_state='hero', kind='short', activity='light')
    case.act('advance_srd_lasting_time', elapsed_rounds=600)
    case.act('complete_srd_rest', target_state='hero')
    receipt, command = case.act('prepare_srd_spells', caster='hero', caster_state='hero',
        source='wizard', spells=['cloud_of_daggers'], spellbook_available=True)
    assert case.entities()['hero']['temp_hp'] == independent
    assert case.entities()['hero']['hp'] == hp
    _assert_tracking(case, False, '')
    _replay(case, receipt, command)


def test_public_attack_exhaustion_clears_source_immediately(healing_case):
    # Recorded synthetic GM stat block, stored before the encounter; no hit,
    # roll, damage receipt or pending action is seeded. The public NPC attack
    # accepts supplied stat-block facts (there is no canonical attacks table).
    case = _public_routes(healing_case('false_life', entity_changes={'guard': {
        'source': 'Recorded GM stat block: Regression claw; melee reach 5 feet; '
                  '+6 to hit; 1d8 + 3 slashing; one attack per action.',
        'multiattack': [{'attack': 'Regression claw', 'count': 1}],
        'attitudes': {'hero': 'hostile'},
    }}))
    _cast(case, 'wizard')
    case.start()
    case.next_turn()
    before_hp = case.entities()['hero']['hp']
    begun, begin_command = case.act('begin_srd_creature_attack',
        target='hero', attack_name='Regression claw', attack_mode='melee',
        attack_distance=5, normal_range=5, long_range=5, attack_modifier=6,
        damage_expression='d8', damage_modifier=3, damage_type='slashing',
        advantage=False, disadvantage=False, actor_can_see_target=True,
        target_can_see_actor=True, target_is_charmer=False, fear_source_in_sight=False,
        attack_uses_weapon=False, use_multiattack=True)
    pending_id = begun.result['context']['_pending_action_id']
    event = case.lasting()['pending_srd_actions'][pending_id]
    assert event['actor_state_id'] == 'guard' and event['target_state_id'] == 'hero'
    assert event['attack_outcome'] in ('hit', 'critical_hit'), 'Real roll did not produce the required hit'
    _replay(case, begun, begin_command)
    receipt, command = case.act('complete_srd_pending_action', pending_action_id=pending_id)
    assert case.entities()['hero']['temp_hp'] == 0
    assert case.entities()['hero']['hp'] < before_hp, 'Actual damage did not exhaust the pool with excess'
    _assert_tracking(case, False, '')
    _replay(case, receipt, command)


def test_public_earned_long_rest_clears_source(healing_case):
    case = healing_case('false_life')
    _cast(case, 'wizard')
    case.act('begin_srd_rest', target_state='hero', kind='long', activity='sleep')
    case.act('advance_srd_lasting_time', elapsed_rounds=4800)
    receipt, command = case.act('complete_srd_rest', target_state='hero')
    assert case.entities()['hero']['temp_hp'] == 0
    _assert_tracking(case, False, '')
    _replay(case, receipt, command)
