"""UNEXECUTED draft: real admitted campaigns, public tools and persisted receipts.

Requires a newly composed archive containing the defense adapter. These tests
do not install a patched runtime, fabricate a trigger, or replace dice results.
All synthetic source/relationship/equipment facts enter at campaign creation.
"""

from copy import deepcopy
from dataclasses import replace
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import CampaignCreate
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.domain.authority import StateValue, WriterRole, owned_payload, thaw_json_value
from sylvia_gm.domain.errors import CommandRejected, MechanicsRejected
from sylvia_gm.runtime import _effect_executor
from tests.lite_fixture_archive import archive_closed_case
from tests.test_srd_lite_foundation import Case, VARIANT_ID


PUBLIC_ROUTES = {
    'cast_spell', 'cast_spell_outside_combat', 'begin_srd_spell_action',
    'cast_srd_reaction_spell', 'complete_srd_pending_action',
    'begin_srd_creature_attack', 'change_srd_equipment', 'dismiss_srd_spell',
    'begin_srd_rest', 'complete_srd_rest', 'prepare_srd_spells',
}


def _public_routes(case):
    original = case.execute

    def execute(command):
        if command.tool_name in PUBLIC_ROUTES:
            try:
                return StatelessMcpMechanics(case.owner).execute_action(command)
            except MechanicsRejected as error:
                print('REAL_PUBLIC_REJECTION', command.tool_name, error.code,
                      error.field_paths, error.validation_issues, error.state_changed)
                raise
        return original(command)

    case.execute = execute
    return case


def _mage_params(case, **choices):
    params = case.parameters | {'spell': 'mage_armor', 'target': 'hero', 'target_character': 'hero'}
    for name in ('caster_can_touch_target', 'caster_can_touch_all_targets', 'target_distance'):
        params.pop(name, None)
    return params | choices


def _mage_records(case):
    return [row for row in case.lasting().get('turn_effects', [])
            if row.get('kind') == 'timed_actor_defense' and row.get('spell') == 'mage_armor']


def _replay_reopen(case, receipt, command):
    saved = case.snapshot()
    assert case.execute(command) == receipt and case.snapshot().state == saved.state
    case.reopen()
    assert case.execute(command) == receipt and case.snapshot().state == saved.state
    assert case.snapshot().participants == saved.participants


def _reject(case, tool, parameters, match=None):
    before = case.snapshot()
    with pytest.raises((MechanicsRejected, CommandRejected), match=match):
        case.execute(case.command(tool, parameters))
    after = case.snapshot()
    assert after.revision == before.revision and after.state == before.state
    assert after.participants == before.participants


class DefenseMultiCase(Case):
    """Clone a real validated ordinary Wizard into a second admitted campaign."""

    def __init__(self, path, spell, companion_spell=None):
        self.campaign_id = 'lite'
        super().__init__(path, spell, entity_changes={
            'hero': {'armor_class': 12, 'worn_armor': None, 'worn_shield': None, 'dodging': False,
                     'inventory': ['spear', 'dagger', 'scimitar', 'leather', 'shield']},
            'guard': {'attitudes': {'hero': 'hostile'},
                      'multiattack': [{'attack': 'Draft claw', 'count': 2}]},
        }, participant_changes={'wizard_spellbook': list(dict.fromkeys([spell, 'cloud_of_daggers']))})
        initial = self.snapshot()
        state = thaw_json_value(initial.state)
        original = initial.participants[0]
        data = thaw_json_value(original.data)
        companion_spell = companion_spell or spell
        data.update(name='Independent defense companion',
                    wizard_spellbook=list(dict.fromkeys([companion_spell, 'cloud_of_daggers'])),
                    spellcasting_grants=[{'source': 'wizard', 'ability': 'intelligence', 'spells': [companion_spell]}])
        companion = replace(original, participant_id='companion', data=owned_payload(data))
        actors = state[VARIANT_ID + '.entities']
        actors['companion'] = deepcopy(actors['hero'])
        actors['companion'].update(name='Independent defense companion', prepared_spells=[companion_spell])
        actors['guard']['attitudes']['companion'] = 'hostile'
        scene = state[VARIANT_ID + '.state']['spatial_scene']
        proxy = deepcopy(next(row for row in scene['entities'] if row['id'] == 'hero'))
        proxy.update(id='companion', position=[-1, 0, 0])
        proxy['metadata']['participant_ref'] = 'companion'
        scene['entities'].append(proxy)
        next(row for row in scene['entities'] if row['id'] == 'guard')['metadata']['enemy_of'] = ['hero', 'companion']
        self.owner.create_campaign(CampaignCreate(
            campaign_id='lite-defenses', display_name='Saved defense source proof',
            writer=WriterRole.DM, package_lock=initial.package_lock, turn_id='turn',
            participants=(original, companion),
            initial_state=tuple(StateValue(VARIANT_ID, key.removeprefix(VARIANT_ID + '.'), value)
                                for key, value in state.items()),
        ))
        self.campaign_id = 'lite-defenses'

    def snapshot(self):
        return self.owner.snapshot(self.campaign_id)

    def command(self, tool, parameters, identity=None):
        return replace(super().command(tool, parameters, identity), campaign_id=self.campaign_id)


@pytest.fixture
def defense_case(tmp_path, monkeypatch):
    monkeypatch.setattr(dice, '_rng', Random(1))
    monkeypatch.setattr(dice, 'set_seed', lambda _seed: None)
    opened = []

    def make(spell='mage_armor', *, multi=False, companion_spell=None, **changes):
        path = tmp_path / f'defense-{len(opened)}.db'
        if multi:
            assert not changes
            case = DefenseMultiCase(path, spell, companion_spell)
        else:
            entities = deepcopy(changes.pop('entity_changes', {}))
            entities['hero'] = {
                'armor_class': 12, 'worn_armor': None, 'worn_shield': None, 'dodging': False,
                'inventory': ['spear', 'dagger', 'scimitar', 'leather', 'shield'],
                **entities.get('hero', {}),
            }
            entities['guard'] = {
                'attitudes': {'hero': 'hostile'},
                'multiattack': [{'attack': 'Draft claw', 'count': 2}],
                **entities.get('guard', {}),
            }
            participant = {'wizard_spellbook': [spell, 'cloud_of_daggers'],
                           **changes.pop('participant_changes', {})}
            case = Case(path, spell, entity_changes=entities, participant_changes=participant, **changes)
        opened.append(case)
        return _public_routes(case)

    yield make
    for case in opened:
        archive_closed_case(case)


def _begin_attack(case, monkeypatch, *, seed=8, modifier=6):
    monkeypatch.setattr(dice, '_rng', Random(seed))
    combat = case.snapshot().state[VARIANT_ID + '.combat']
    assert combat['active_actor'] == 'guard' and combat['current_actor'] == 'guard'
    # Actor is from_state=combat.active_actor, deliberately absent from public input.
    receipt, command = case.act('begin_srd_creature_attack', target='hero',
        attack_name='Draft claw', attack_mode='melee', attack_distance=5, normal_range=5, long_range=5,
        attack_modifier=modifier, damage_expression='d4', damage_modifier=0, damage_type='slashing',
        advantage=False, disadvantage=False, actor_can_see_target=True, target_can_see_actor=True,
        target_is_charmer=False, fear_source_in_sight=False, attack_uses_weapon=False, use_multiattack=True)
    event_id = receipt.result['context']['_pending_action_id']
    return receipt, command, event_id, thaw_json_value(case.lasting()['pending_srd_actions'][event_id])


@pytest.mark.parametrize('slot', [1, 3])
def test_mage_actual_formula_paid_cost_no_roll_touch_omission_and_reopen(defense_case, monkeypatch, slot):
    case = defense_case()
    rolls = []
    original = _effect_executor.dice_roll

    def recorded(sides, count=1, modifier=0):
        result = original(sides, count, modifier)
        rolls.append(result)
        return result

    monkeypatch.setattr(_effect_executor, 'dice_roll', recorded)
    receipt, command = case.act('cast_spell_outside_combat', **_mage_params(case, slot_resource=f'spell_slot_{slot}'))
    assert rolls == []
    record, = _mage_records(case)
    assert record['magnitude'] == 15 and record['spell_level'] == slot
    assert record['expires_tick'] is None and record['lifetime_kind'] == 'until_long_rest'
    assert record['source_caster_id'] == 'hero' and record['lite_prepared_source'] == 'wizard'
    assert record['cast_id'] and record['ongoing_duration'] is True
    assert case.entities()['hero']['resources'][f'spell_slot_{slot}'] == 2
    assert not case.entities()['hero'].get('concentration_spell_id')
    assert any('successful recipient long rest' in row['value'] for row in receipt.result['result_card']['details'])
    _replay_reopen(case, receipt, command)
    case.start()
    case.next_turn()
    _, _, event_id, event = _begin_attack(case, monkeypatch)
    assert event['attack_total'] == 14 and event['attack_outcome'] == 'miss'
    complete, complete_command = case.act('complete_srd_pending_action', pending_action_id=event_id)
    assert complete.result['context']['_attack_roll'] == event['attack_roll']
    assert case.entities()['hero']['hp'] == 20
    _replay_reopen(case, complete, complete_command)


@pytest.mark.parametrize('tool', ['cast_spell_outside_combat', 'begin_srd_spell_action'])
def test_mage_real_far_target_rejects_before_spend_even_with_touch_claim(defense_case, tool):
    case = defense_case(positions={'hero': [0, 0, 0], 'guard': [30, 0, 0], 'other': [-5, 0, 0], 'extra': [20, 10, 0]})
    if tool == 'begin_srd_spell_action':
        case.start()
    params = case.parameters | {'spell': 'mage_armor', 'target': 'guard', 'caster_can_touch_target': True, 'target_distance': 0}
    _reject(case, tool, params, 'touch range')
    assert not _mage_records(case)
    _reject(case, tool, _mage_params(case, lite_prepared_source='wizard'))


def test_mage_real_armor_equip_permanently_retires_and_old_retry_cannot_retire_new_cast(defense_case, monkeypatch):
    case = defense_case(participant_changes={'classes': [{'id': 'wizard', 'level': 16}, {'id': 'cleric', 'level': 1}]})
    case.act('cast_spell_outside_combat', **_mage_params(case))
    equip, equip_command = case.act('change_srd_equipment', actor='hero', actor_state='hero', slot='armor',
        item=[{'inventory_item': 'leather', 'armor_type': 'leather_armor', 'magic_bonus': 0}])
    assert not _mage_records(case)
    _replay_reopen(case, equip, equip_command)
    case.act('change_srd_equipment', actor='hero', actor_state='hero', slot='armor', item=[])
    assert not _mage_records(case)
    case.start()
    case.next_turn()
    _, _, probe_id, probe = _begin_attack(case, monkeypatch)
    assert probe['attack_total'] == 14 and probe['attack_outcome'] == 'hit'
    before_hp = case.entities()['hero']['hp']
    case.act('complete_srd_pending_action', pending_action_id=probe_id)
    assert case.entities()['hero']['hp'] < before_hp
    case.act('end_combat_encounter', end_reason='fighting_stopped')
    case.act('cast_spell_outside_combat', **_mage_params(case))
    current = case.snapshot()
    assert case.execute(equip_command) == equip and case.snapshot().state == current.state
    assert len(_mage_records(case)) == 1
    case.act('change_srd_equipment', actor='hero', actor_state='hero', slot='shield',
        item=[{'inventory_item': 'shield', 'armor_type': 'shield', 'magic_bonus': 0}])
    assert len(_mage_records(case)) == 1
    case.reopen()


@pytest.mark.parametrize('stronger_base', [False, True])
def test_mage_trained_shield_and_stronger_real_unarmored_base_feed_attack(defense_case, monkeypatch, stronger_base):
    if stronger_base:
        case = defense_case(
            participant_changes={
                'classes': [{'id': 'wizard', 'level': 7}, {'id': 'monk', 'level': 10}],
                'abilities': {'strength': 16, 'dexterity': 14, 'constitution': 14,
                              'intelligence': 18, 'wisdom': 18, 'charisma': 10},
            },
            entity_changes={'hero': {
                'armor_class': 16,
                'resources': {**{f'spell_slot_{level}': 3 for level in range(1, 10)},
                              'focus_point': 10},
                'resource_maxima': {'focus_point': 10},
                'martial_arts_die': 'd8',
            }},
        )
    else:
        case = defense_case(participant_changes={'classes': [{'id': 'wizard', 'level': 16}, {'id': 'cleric', 'level': 1}]})
    case.act('cast_spell_outside_combat', **_mage_params(case))
    if not stronger_base:
        case.act('change_srd_equipment', actor='hero', actor_state='hero', slot='shield',
            item=[{'inventory_item': 'shield', 'armor_type': 'shield', 'magic_bonus': 0}])
    case.start()
    case.next_turn()
    # Total15 would hit the Mage candidate15 but misses actual Monk base16;
    # total16 would hit candidate15 but misses trained-shield Mage17.
    _, _, event_id, event = _begin_attack(case, monkeypatch,
        seed=9 if stronger_base else 8, modifier=0 if stronger_base else 8)
    assert event['attack_total'] == (15 if stronger_base else 16)
    assert event['attack_outcome'] == 'miss'
    hp = case.entities()['hero']['hp']
    complete, command = case.act('complete_srd_pending_action', pending_action_id=event_id)
    assert case.entities()['hero']['hp'] == hp
    _replay_reopen(case, complete, command)


def test_mage_elapsed_time_short_failed_rest_and_successful_long_rest(defense_case, monkeypatch):
    case = defense_case()
    case.act('cast_spell_outside_combat', **_mage_params(case))
    case.act('advance_srd_lasting_time', elapsed_rounds=4800)
    assert len(_mage_records(case)) == 1
    case.start()
    case.next_turn()
    _, _, probe_id, probe = _begin_attack(case, monkeypatch)
    assert probe['attack_total'] == 14 and probe['attack_outcome'] == 'miss'
    case.act('complete_srd_pending_action', pending_action_id=probe_id)
    case.act('end_combat_encounter', end_reason='fighting_stopped')
    case.act('begin_srd_rest', target_state='hero', kind='short', activity='light')
    case.act('advance_srd_lasting_time', elapsed_rounds=600)
    case.act('complete_srd_rest', target_state='hero')
    assert len(_mage_records(case)) == 1
    case.act('begin_srd_rest', target_state='hero', kind='long', activity='sleep')
    _reject(case, 'complete_srd_rest', {'target_state': 'hero'})
    assert len(_mage_records(case)) == 1
    case.act('advance_srd_lasting_time', elapsed_rounds=4800)
    rest, rest_command = case.act('complete_srd_rest', target_state='hero')
    assert not _mage_records(case)
    assert case.entities()['hero']['rest_phase'] == 'complete'
    _replay_reopen(case, rest, rest_command)


def test_mage_two_casters_exact_unprepare_and_free_dismiss_replay(defense_case):
    case = defense_case(multi=True)
    case.act('cast_spell_outside_combat', **_mage_params(case))
    case.act('cast_spell_outside_combat', **_mage_params(case, caster='companion', caster_state='companion'))
    records = _mage_records(case)
    assert {row['source_caster_id'] for row in records} == {'hero', 'companion'}
    companion_id = next(row['cast_id'] for row in records if row['source_caster_id'] == 'companion')
    case.act('begin_srd_rest', target_state='hero', kind='short', activity='light')
    case.act('advance_srd_lasting_time', elapsed_rounds=600)
    case.act('complete_srd_rest', target_state='hero')
    prepared, prepared_command = case.act('prepare_srd_spells', caster='hero', caster_state='hero',
        source='wizard', spells=['cloud_of_daggers'], spellbook_available=True)
    remaining, = _mage_records(case)
    assert remaining['source_caster_id'] == 'companion' and remaining['cast_id'] == companion_id
    _replay_reopen(case, prepared, prepared_command)
    _reject(case, 'dismiss_srd_spell', {'caster_state': 'hero', 'cast_id': companion_id})
    resources = dict(case.entities()['companion']['resources'])
    dismissed, dismissed_command = case.act('dismiss_srd_spell', caster_state='companion', cast_id=companion_id)
    assert not _mage_records(case) and case.entities()['companion']['resources'] == resources
    _replay_reopen(case, dismissed, dismissed_command)


def test_mage_same_caster_two_real_grants_do_not_lose_independent_ownership(defense_case):
    case = defense_case(participant_changes={
        'classes': [{'id': 'wizard', 'level': 9}, {'id': 'sorcerer', 'level': 8}],
        'spellcasting_grants': [
            {'source': 'wizard', 'ability': 'intelligence', 'spells': ['mage_armor']},
            {'source': 'sorcerer', 'ability': 'charisma', 'spells': ['mage_armor']},
        ],
    })
    case.act('cast_spell_outside_combat', **_mage_params(case, spell_grant='wizard'))
    case.act('cast_spell_outside_combat', **_mage_params(case, spell_grant='sorcerer'))
    assert {row['lite_prepared_source'] for row in _mage_records(case)} == {'wizard', 'sorcerer'}
    assert len({row['cast_id'] for row in _mage_records(case)}) == 2
    case.act('begin_srd_rest', target_state='hero', kind='short', activity='light')
    case.act('advance_srd_lasting_time', elapsed_rounds=600)
    case.act('complete_srd_rest', target_state='hero')
    receipt, command = case.act('prepare_srd_spells', caster='hero', caster_state='hero',
        source='wizard', spells=['cloud_of_daggers'], spellbook_available=True)
    remaining, = _mage_records(case)
    assert remaining['lite_prepared_source'] == 'sorcerer'
    assert 'mage_armor' in case.entities()['hero']['prepared_spells']
    _replay_reopen(case, receipt, command)


@pytest.mark.parametrize('seed,modifier,eligible', [(8, 6, True), (8, 0, False), (8, 30, False), (5, 0, False)])
def test_shield_real_saved_hit_gate_same_roll_payment_and_reopen(defense_case, monkeypatch, seed, modifier, eligible):
    case = defense_case('shield')
    case.start()
    case.next_turn()
    _, _, event_id, event = _begin_attack(case, monkeypatch, seed=seed, modifier=modifier)
    assert event['lite_shield_eligible'] is eligible
    params = {'spell': 'shield', 'caster': 'hero', 'caster_state': 'hero', 'slot_resource': 'spell_slot_1',
              'reaction_trigger_id': event_id, 'verbal_component_available': True, 'somatic_component_available': True}
    if not eligible:
        _reject(case, 'cast_srd_reaction_spell', params)
        assert case.entities()['hero']['resources']['spell_slot_1'] == 3
        assert case.entities()['hero']['reaction_available'] is True
        case.act('complete_srd_pending_action', pending_action_id=event_id)
        return
    assert event['attack_outcome'] == 'hit' and event['attack_total'] == 14
    _replay_reopen(case, *case.act('cast_srd_reaction_spell', **params))
    assert case.entities()['hero']['resources']['spell_slot_1'] == 2
    assert case.entities()['hero']['reaction_available'] is False
    hp = case.entities()['hero']['hp']
    rolls = []
    original = _effect_executor.dice_roll

    def recorded(sides, count=1, modifier=0):
        result = original(sides, count, modifier)
        rolls.append(result)
        return result

    monkeypatch.setattr(_effect_executor, 'dice_roll', recorded)
    complete, command = case.act('complete_srd_pending_action', pending_action_id=event_id)
    assert rolls == [] and case.entities()['hero']['hp'] == hp
    assert complete.result['context']['_attack_roll'] == event['attack_roll']
    assert complete.result['context']['_attack_outcome'] == 'miss'
    _replay_reopen(case, complete, command)
    _, _, later_id, later = _begin_attack(case, monkeypatch, seed=9, modifier=0)
    assert later['attack_total'] == 15 and later['attack_outcome'] == 'miss'
    case.act('complete_srd_pending_action', pending_action_id=later_id)
    assert case.entities()['hero']['resources']['spell_slot_1'] == 2
    case.next_turn()  # Actual next caster turn, rather than a forged turn counter.
    case.next_turn()
    _, _, expired_id, expired = _begin_attack(case, monkeypatch)
    assert expired['attack_outcome'] == 'hit' and case.entities()['hero'].get('reaction_shield') is None
    case.act('complete_srd_pending_action', pending_action_id=expired_id)


def test_shield_actual_split_magic_missile_allocation_keeps_other_recipient_damage(defense_case):
    case = defense_case('shield', multi=True, companion_spell='magic_missile')
    case.start(second_actor='companion')
    case.next_turn()
    parameters = case.parameters | {
        'spell': 'magic_missile', 'caster': 'companion', 'caster_state': 'companion',
        'target': 'hero', 'projectile_targets': ['hero', 'guard', 'hero'],
        'slot_resource': 'spell_slot_1', 'target_distance': 1,
    }
    begun, begun_command = case.act('begin_srd_spell_action', **parameters)
    event_id = begun.result['context']['_pending_action_id']
    event = case.lasting()['pending_srd_actions'][event_id]
    assert event['kind'] == 'spell_cast' and event['spell'] == 'magic_missile'
    _replay_reopen(case, begun, begun_command)
    case.act('cast_srd_reaction_spell', spell='shield', caster='hero', caster_state='hero',
        slot_resource='spell_slot_1', reaction_trigger_id=event_id,
        verbal_component_available=True, somatic_component_available=True)
    hero_hp = case.entities()['hero']['hp']
    guard_hp = case.entities()['guard']['hp']
    complete, command = case.act('complete_srd_pending_action', pending_action_id=event_id)
    assert case.entities()['hero']['hp'] == hero_hp
    assert case.entities()['guard']['hp'] < guard_hp
    assert case.entities()['hero']['resources']['spell_slot_1'] == 2
    assert case.entities()['companion']['resources']['spell_slot_1'] == 2
    _replay_reopen(case, complete, command)


def test_shield_condition_critical_uses_saved_ordinary_roll_but_keeps_caster_legality(defense_case, monkeypatch):
    case = defense_case('shield', entity_changes={'hero': {'conditions': ['paralyzed']}})
    case.start()
    case.next_turn()
    _, _, event_id, event = _begin_attack(case, monkeypatch, seed=8, modifier=2)
    assert event['attack_roll']['total'] == 12 and event['attack_total'] == 14
    assert event['context']['_condition_critical_hit'] is True
    assert event['context']['_critical_hit'] is True and event['attack_outcome'] == 'critical_hit'
    # The saved ordinary roll is AC-convertible even though a surviving hit is
    # condition-critical. Eligibility does not grant an incapacitated caster a
    # Reaction or bypass the canonical condition/component/payment owner.
    assert event['lite_shield_eligible'] is True
    _reject(case, 'cast_srd_reaction_spell', {
        'spell': 'shield', 'caster': 'hero', 'caster_state': 'hero', 'slot_resource': 'spell_slot_1',
        'reaction_trigger_id': event_id,
        'verbal_component_available': True, 'somatic_component_available': True,
    })
    assert case.entities()['hero']['resources']['spell_slot_1'] == 3
    complete, command = case.act('complete_srd_pending_action', pending_action_id=event_id)
    assert complete.result['context']['_attack_roll'] == event['attack_roll']
    assert complete.result['context']['_attack_outcome'] == 'critical_hit'
    _replay_reopen(case, complete, command)


def test_shield_decline_resumes_original_saved_hit_without_payment(defense_case, monkeypatch):
    case = defense_case('shield')
    case.start()
    case.next_turn()
    begun, begun_command, event_id, event = _begin_attack(case, monkeypatch)
    assert event['lite_shield_eligible'] is True
    _replay_reopen(case, begun, begun_command)
    hp = case.entities()['hero']['hp']
    complete, command = case.act('complete_srd_pending_action', pending_action_id=event_id)
    assert complete.result['context']['_attack_roll'] == event['attack_roll']
    assert complete.result['context']['_attack_outcome'] == 'hit'
    assert case.entities()['hero']['hp'] < hp
    assert case.entities()['hero']['resources']['spell_slot_1'] == 3
    assert case.entities()['hero']['reaction_available'] is True
    _replay_reopen(case, complete, command)

