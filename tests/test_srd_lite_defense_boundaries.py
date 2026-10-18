"""UNEXECUTED boundary draft; compose the defense archive before running.

All relationships, actor profiles, planes and geometry enter through actual
campaign creation. Spells use real admitted grants, public commands, genuine
saved windows and real RNG. No pending event, grant, dice result or installed
package is patched. This file does not change the reviewed defense tests.
"""

from copy import deepcopy
from dataclasses import replace
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import CampaignCreate
from sylvia_gm.domain.authority import StateValue, WriterRole, owned_payload, thaw_json_value
from sylvia_gm.domain.errors import CommandRejected, MechanicsRejected
from sylvia_gm.runtime import _effect_executor
from tests.lite_fixture_archive import archive_closed_case
from tests.test_spatial_engine import _geometry
from tests.test_srd_lite_foundation import Case, VARIANT_ID


PUBLIC = {
    'cast_spell_outside_combat', 'begin_srd_spell_action',
    'complete_srd_pending_action', 'cast_srd_reaction_spell',
    'end_combat_encounter', 'advance_srd_lasting_time',
}
DISTANT_OTHERS = {
    'hero': [0, 0, 0], 'guard': [50, 0, 0],
    'other': [-50, 0, 0], 'extra': [60, 10, 0],
}


def _public(case):
    original = case.execute

    def execute(command):
        if command.tool_name in PUBLIC:
            return StatelessMcpMechanics(case.owner).execute_action(command)
        return original(command)

    case.execute = execute
    return case


class BoundaryCampaignCase(Case):
    def snapshot(self):
        return self.owner.snapshot(self.campaign_id)

    def command(self, tool, parameters, identity=None):
        return replace(super().command(tool, parameters, identity), campaign_id=self.campaign_id)

    def admit(self, initial, state, participants, identity):
        self.owner.create_campaign(CampaignCreate(
            campaign_id=identity, display_name='Actual saved defense boundary proof',
            writer=WriterRole.DM, package_lock=initial.package_lock, turn_id='turn',
            participants=tuple(participants),
            initial_state=tuple(StateValue(VARIANT_ID, key.removeprefix(VARIANT_ID + '.'), value)
                                for key, value in state.items()),
        ))
        self.campaign_id = identity


class ScalarShieldCase(BoundaryCampaignCase):
    """The Guiding Bolt caster is a Cleric; the defender comes from a real Wizard."""

    def __init__(self, path, wizard_donor):
        self.campaign_id = 'lite'
        super().__init__(path, 'guiding_bolt', positions=DISTANT_OTHERS)
        initial = self.snapshot()
        donor = wizard_donor.snapshot()
        def exact(lock):
            return sorted((row.package_id, row.version, row.sha256) for row in lock.entries)
        assert exact(initial.package_lock) == exact(donor.package_lock)
        caster = initial.participants[0]
        wizard = donor.participants[0]
        assert thaw_json_value(caster.data)['classes'] == [{'id': 'cleric', 'level': 17}]
        assert thaw_json_value(wizard.data)['classes'] == [{'id': 'wizard', 'level': 17}]
        data = thaw_json_value(wizard.data)
        data.update(name='Actual Wizard Shield defender', wizard_spellbook=['shield'],
                    spellcasting_grants=[{'source': 'wizard', 'ability': 'intelligence', 'spells': ['shield']}])
        defender = replace(wizard, participant_id='defender', data=owned_payload(data))
        state = thaw_json_value(initial.state)
        actors = state[VARIANT_ID + '.entities']
        actors['defender'] = deepcopy(thaw_json_value(donor.state)[VARIANT_ID + '.entities']['hero'])
        actors['defender'].update(name='Actual Wizard Shield defender', prepared_spells=['shield'],
                                  armor_class=16, hp=1000, max_hp=1000, temp_hp=0,
                                  dodging=False, reaction_available=True, attitudes={'hero': 'hostile'})
        scene = state[VARIANT_ID + '.state']['spatial_scene']
        caster_proxy = next(row for row in scene['entities'] if row['id'] == 'hero')
        caster_proxy['metadata']['enemy_of'] = ['defender']
        proxy = deepcopy(caster_proxy)
        proxy.update(id='defender', position=[30, 0, 0])
        proxy['metadata'].update(participant_ref='defender', enemy_of=['hero'])
        scene['entities'].append(proxy)
        self.admit(initial, state, (caster, defender), 'lite-scalar-shield-boundary')


class MageSceneCase(BoundaryCampaignCase):
    """Create an actual second Mage recipient with an admitted plane/wall fact."""

    def __init__(self, path, obstruction):
        self.campaign_id = 'lite'
        super().__init__(path, 'mage_armor', positions=DISTANT_OTHERS,
                         entity_changes={'hero': {'armor_class': 12, 'worn_armor': None,
                                                  'worn_shield': None, 'dodging': False}})
        initial = self.snapshot()
        caster = initial.participants[0]
        data = thaw_json_value(caster.data)
        data['name'] = 'Actual willing Mage recipient'
        recipient = replace(caster, participant_id='recipient', data=owned_payload(data))
        state = thaw_json_value(initial.state)
        actors = state[VARIANT_ID + '.entities']
        actors['recipient'] = deepcopy(actors['hero'])
        actors['recipient'].update(name='Actual willing Mage recipient', attitudes={'hero': 'friendly'})
        scene = state[VARIANT_ID + '.state']['spatial_scene']
        proxy = deepcopy(next(row for row in scene['entities'] if row['id'] == 'hero'))
        proxy.update(id='recipient', position=[5, 0, 0])
        proxy['metadata']['participant_ref'] = 'recipient'
        if obstruction == 'other_plane':
            proxy['metadata']['plane'] = 'ethereal'
        elif obstruction == 'wall':
            wall = _geometry('mage_touch_wall', [2, -100, -100], [3, 100, 100])
            wall['map_id'] = scene['map']['id']
            scene['geometry'].append(wall)
        else:
            assert obstruction == 'clear'
        scene['entities'].append(proxy)
        self.admit(initial, state, (caster, recipient), 'lite-mage-scene-boundary')


@pytest.fixture
def boundary_case(tmp_path, monkeypatch):
    monkeypatch.setattr(dice, '_rng', Random(1))
    monkeypatch.setattr(dice, 'set_seed', lambda _seed: None)
    opened = []

    def make(*, mage_obstruction=None):
        path = tmp_path / f'boundary-{len(opened)}.db'
        if mage_obstruction is None:
            # The donor is an ordinary admitted Wizard, never a relabelled Cleric.
            donor = Case(path.with_name(path.stem + '-wizard.db'), 'scorching_ray')
            opened.append(donor)
            case = ScalarShieldCase(path, donor)
        else:
            case = MageSceneCase(path, mage_obstruction)
        opened.append(case)
        return _public(case)

    yield make
    for case in opened:
        archive_closed_case(case)


def _record_rolls(monkeypatch):
    rolls = []
    original = _effect_executor.dice_roll

    def recorded(sides, count=1, modifier=0):
        result = original(sides, count, modifier)
        rolls.append(result)
        return result

    monkeypatch.setattr(_effect_executor, 'dice_roll', recorded)
    return rolls


def _replay_reopen(case, receipt, command, rolls):
    saved = case.snapshot()
    count = len(rolls)
    assert case.execute(command) == receipt and case.snapshot().state == saved.state
    case.reopen()
    assert case.execute(command) == receipt and case.snapshot().state == saved.state
    assert case.snapshot().participants == saved.participants and len(rolls) == count


def _reject(case, tool, parameters, rolls, match=None):
    saved = case.snapshot()
    count = len(rolls)
    with pytest.raises((CommandRejected, MechanicsRejected), match=match):
        case.act(tool, **parameters)
    after = case.snapshot()
    assert after.revision == saved.revision and after.state == saved.state
    assert after.participants == saved.participants and len(rolls) == count


def _guiding(case, cover='none'):
    return case.parameters | {'target': 'defender', 'target_distance': 30, 'cover': cover}


def _shield(event_id):
    return {'spell': 'shield', 'caster': 'defender', 'caster_state': 'defender',
            'slot_resource': 'spell_slot_1', 'reaction_trigger_id': event_id,
            'verbal_component_available': True, 'somatic_component_available': True}


def _hold_scalar(case, monkeypatch, rolls, *, seed=8, cover='none'):
    case.start()
    monkeypatch.setattr(dice, '_rng', Random(seed))
    begun, begin_command = case.act('begin_srd_spell_action', **_guiding(case, cover))
    event_id = begun.result['context']['_pending_action_id']
    assert rolls == []
    _replay_reopen(case, begun, begin_command, rolls)
    held, held_command = case.act('complete_srd_pending_action', pending_action_id=event_id)
    event = thaw_json_value(case.lasting()['pending_srd_actions'][event_id])
    assert event['kind'] == 'spell_attack' and event['owner_kind'] == 'spell_cast'
    assert event['stage'] == 'spell_attack_reaction' and event['status'] == 'open'
    assert event['spell'] == 'guiding_bolt' and event['target_state_id'] == 'defender'
    assert 'lite_scorching_ray_cursor' not in event
    assert event['spell_attack_parameters']['cover'] == cover
    assert [roll.sides for roll in rolls] == [20]
    assert case.entities()['defender']['hp'] == 1000
    assert case.entities()['hero']['resources']['spell_slot_1'] == 2
    assert case.snapshot().state[VARIANT_ID + '.combat']['action_available'] is False
    _replay_reopen(case, held, held_command, rolls)
    return event_id, event


@pytest.mark.parametrize('intervene', [True, False])
def test_actual_guiding_bolt_shield_or_decline_reuses_scalar_roll_and_reopens(boundary_case, monkeypatch, intervene):
    case = boundary_case()
    rolls = _record_rolls(monkeypatch)
    event_id, event = _hold_scalar(case, monkeypatch, rolls)
    captured = event['spell_attack_context']
    assert captured['_card_d20_roll']['results'] == [8]
    assert captured['_card_d20_total'] == 18 and captured['_spell_target_armor_class'] == 16
    assert captured['_spell_critical_hit'] is False and event['lite_shield_eligible'] is True
    if intervene:
        _replay_reopen(case, *case.act('cast_srd_reaction_spell', **_shield(event_id)), rolls)
        assert case.entities()['defender']['resources']['spell_slot_1'] == 2
        assert case.entities()['defender']['reaction_available'] is False
    finished, finish_command = case.act('complete_srd_pending_action', pending_action_id=event_id)
    assert finished.result['context']['_spell_attack'] == captured['_spell_attack']
    assert finished.result['context']['_spell_attack_outcome'] == ('failure' if intervene else 'success')
    assert event_id not in case.lasting().get('pending_srd_action_stack', [])
    assert sum(roll.sides == 20 for roll in rolls) == 1
    effects = [row for row in case.lasting()['turn_effects']
               if row.get('spell') == 'guiding_bolt' and row.get('target') == 'defender']
    if intervene:
        assert [roll.sides for roll in rolls] == [20]
        assert case.entities()['defender']['hp'] == 1000 and effects == []
    else:
        assert [roll.sides for roll in rolls] == [20, 6] and len(rolls[-1].results) == 4
        assert case.entities()['defender']['hp'] == 1000 - rolls[-1].total and effects
        assert case.entities()['defender']['resources']['spell_slot_1'] == 3
        assert case.entities()['defender']['reaction_available'] is True
    assert case.entities()['hero']['resources']['spell_slot_1'] == 2
    _replay_reopen(case, finished, finish_command, rolls)


@pytest.mark.parametrize('cover,ac,eligible', [('none', 16, False), ('half', 18, False), ('three_quarters', 21, True)])
def test_scalar_cover_classification_survives_to_actual_ac_and_shield_boundary(boundary_case, monkeypatch, cover, ac, eligible):
    case = boundary_case()
    rolls = _record_rolls(monkeypatch)
    event_id, event = _hold_scalar(case, monkeypatch, rolls, seed=9, cover=cover)
    captured = event['spell_attack_context']
    assert captured['_card_d20_roll']['results'] == [15] and captured['_card_d20_total'] == 25
    assert captured['_spell_target_armor_class'] == ac
    assert event['attack_outcome'] == 'hit' and event['lite_shield_eligible'] is eligible
    if eligible:
        _replay_reopen(case, *case.act('cast_srd_reaction_spell', **_shield(event_id)), rolls)
    else:
        _reject(case, 'cast_srd_reaction_spell', _shield(event_id), rolls)
        assert case.entities()['defender']['resources']['spell_slot_1'] == 3
        assert case.entities()['defender']['reaction_available'] is True
    finished, command = case.act('complete_srd_pending_action', pending_action_id=event_id)
    assert finished.result['context']['_spell_attack'] == captured['_spell_attack']
    assert finished.result['context']['_spell_attack_outcome'] == ('failure' if eligible else 'success')
    assert sum(roll.sides == 20 for roll in rolls) == 1
    if eligible:
        assert rolls[-1].sides == 20 and case.entities()['defender']['hp'] == 1000
    else:
        assert rolls[-1].sides == 6 and case.entities()['defender']['hp'] < 1000
    _replay_reopen(case, finished, command, rolls)


def test_real_shield_survives_encounter_exit_then_expires_on_free_time_clock(boundary_case, monkeypatch):
    case = boundary_case()
    rolls = _record_rolls(monkeypatch)
    event_id, event = _hold_scalar(case, monkeypatch, rolls)
    _replay_reopen(case, *case.act('cast_srd_reaction_spell', **_shield(event_id)), rolls)
    actual_shield = thaw_json_value(case.entities()['defender']['reaction_shield'])
    cast_tick = case.lasting()['lasting_tick']
    assert actual_shield['expires_tick'] == cast_tick + 2 and actual_shield['trigger_id'] == event_id
    case.act('complete_srd_pending_action', pending_action_id=event_id)
    turns = case.entities()['defender'].get('turns_started')
    ended, ended_command = case.act('end_combat_encounter', end_reason='fighting_stopped')
    assert case.snapshot().state[VARIANT_ID + '.combat']['encounter_active'] is False
    assert case.lasting()['lasting_tick'] == cast_tick
    assert case.entities()['defender']['reaction_shield'] == actual_shield
    _replay_reopen(case, ended, ended_command, rolls)
    monkeypatch.setattr(dice, '_rng', Random(8))
    before = len(rolls)
    active, active_command = case.act('cast_spell_outside_combat', **_guiding(case))
    assert [roll.sides for roll in rolls[before:]] == [20]
    assert active.result['context']['_spell_attack']['results'] == [8]
    assert case.entities()['defender']['hp'] == 1000
    assert case.entities()['defender']['reaction_shield'] == actual_shield
    assert case.entities()['hero']['resources']['spell_slot_1'] == 1
    _replay_reopen(case, active, active_command, rolls)
    advanced, advanced_command = case.act('advance_srd_lasting_time', elapsed_rounds=1)
    assert case.lasting()['lasting_tick'] == actual_shield['expires_tick']
    assert case.entities()['defender'].get('turns_started') == turns
    _replay_reopen(case, advanced, advanced_command, rolls)
    monkeypatch.setattr(dice, '_rng', Random(8))
    before = len(rolls)
    expired, expired_command = case.act('cast_spell_outside_combat', **_guiding(case))
    assert [roll.sides for roll in rolls[before:]] == [20, 6]
    assert expired.result['context']['_spell_attack']['results'] == [8]
    assert case.entities()['defender'].get('reaction_shield') is None
    assert case.entities()['defender']['hp'] == 1000 - rolls[-1].total
    assert case.entities()['hero']['resources']['spell_slot_1'] == 0
    assert case.entities()['defender']['resources']['spell_slot_1'] == 2
    _replay_reopen(case, expired, expired_command, rolls)


@pytest.mark.parametrize('tool', ['cast_spell_outside_combat', 'begin_srd_spell_action'])
@pytest.mark.parametrize('obstruction', ['clear', 'other_plane', 'wall'])
def test_mage_actual_plane_and_blocked_touch_path_before_payment(boundary_case, monkeypatch, tool, obstruction):
    case = boundary_case(mage_obstruction=obstruction)
    rolls = _record_rolls(monkeypatch)
    if tool == 'begin_srd_spell_action':
        case.start()
    params = case.parameters | {'target': 'recipient', 'target_character': 'recipient',
                                'target_distance': 0, 'caster_can_touch_target': True,
                                'all_targets_have_clear_path': True, 'target_willing': True}
    if obstruction != 'clear':
        _reject(case, tool, params, rolls, 'touch range, shared plane and a clear path')
        assert case.entities()['hero']['resources']['spell_slot_1'] == 3
        assert case.lasting()['turn_effects'] == []
        assert not case.lasting().get('pending_srd_action_stack')
        if tool == 'begin_srd_spell_action':
            assert case.snapshot().state[VARIANT_ID + '.combat']['action_available'] is True
        return
    receipt, command = case.act(tool, **params)
    _replay_reopen(case, receipt, command, rolls)
    if tool == 'begin_srd_spell_action':
        event_id = receipt.result['context']['_pending_action_id']
        receipt, command = case.act('complete_srd_pending_action', pending_action_id=event_id)
        _replay_reopen(case, receipt, command, rolls)
    record, = [row for row in case.lasting()['turn_effects'] if row.get('spell') == 'mage_armor']
    assert record['target'] == 'recipient' and record['source_caster_id'] == 'hero'
    assert record['magnitude'] == 15 and record['lifetime_kind'] == 'until_long_rest'
    assert case.entities()['hero']['resources']['spell_slot_1'] == 2 and rolls == []
