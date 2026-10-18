"""Exercise actual saved scene facts through the retained spell attack consumers."""
from dataclasses import replace
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import CampaignCreate
from sylvia_gm.domain.authority import StateValue, WriterRole, thaw_json_value
from sylvia_gm.runtime import _effect_executor
from tests.lite_fixture_archive import archive_closed_case
from tests.test_srd_lite_foundation import Case, VARIANT_ID
from tests.test_srd_lite_healing import healing_case


class AttackSceneCase(Case):
    def __init__(self, path, spell, *, scene_metadata=None, **changes):
        self.campaign_id = 'lite'
        super().__init__(path, spell, **changes)
        initial = self.snapshot()
        state = thaw_json_value(initial.state)
        for row in state[VARIANT_ID + '.state']['spatial_scene']['entities']:
            row['metadata'].update((scene_metadata or {}).get(row['id'], {}))
        self.owner.create_campaign(CampaignCreate(
            campaign_id='lite-attack-scene', display_name='Actual spell attack geometry',
            writer=WriterRole.DM, package_lock=initial.package_lock, turn_id='turn',
            participants=initial.participants,
            initial_state=tuple(StateValue(VARIANT_ID, key.removeprefix(VARIANT_ID + '.'), value) for key, value in state.items()),
        ))
        self.campaign_id = 'lite-attack-scene'

    def snapshot(self):
        return self.owner.snapshot(self.campaign_id)

    def command(self, tool, parameters, identity=None):
        return replace(super().command(tool, parameters, identity), campaign_id=self.campaign_id)


@pytest.fixture
def attack_scene(tmp_path, monkeypatch):
    monkeypatch.setattr(dice, '_rng', Random(1))
    monkeypatch.setattr(dice, 'set_seed', lambda _seed: None)
    opened = []

    def make(spell, **changes):
        case = AttackSceneCase(tmp_path / f'{len(opened)}.db', spell, **changes)
        opened.append(case)
        return case

    yield make
    for case in opened:
        archive_closed_case(case)


@pytest.mark.parametrize('capable', [True, False])
def test_actual_nearby_enemy_causes_ranged_disadvantage_despite_forged_intent(attack_scene, capable):
    case = attack_scene('guiding_bolt',
        positions={'hero': [0, 0, 0], 'guard': [40, 0, 0], 'other': [0, 2, 0], 'extra': [80, 40, 0]},
        scene_metadata={'other': {'enemy_of': ['hero']}},
        entity_changes={'other': {'attitudes': {'hero': 'hostile'}, 'conditions': [] if capable else ['incapacitated']}})
    case.start()
    receipt, command = case.act('cast_spell', **(case.parameters | {
        'target_distance': 999, 'enemy_within_5_feet': False,
        'close_enemy_can_see_caster': False, 'close_enemy_incapacitated': True}))
    assert len(receipt.result['context']['_spell_attack']['results']) == (2 if capable else 1)
    assert case.entities()['guard']['hp'] < 1000
    assert case.entities()['hero']['resources']['spell_slot_1'] == 2
    saved = case.snapshot()
    assert '' not in saved.state[VARIANT_ID + '.combat']
    case.reopen()
    assert case.execute(command) == receipt and case.snapshot().state == saved.state


@pytest.mark.parametrize('position,critical', [(4.8, True), (20.8, False)])
def test_condition_critical_uses_actual_distance_and_persists(healing_case, monkeypatch, position, critical):
    case = healing_case('guiding_bolt', entity_changes={'guard': {'conditions': ['paralyzed']}},
        positions={'hero': [0, 0, 0], 'guard': [position, 0, 0], 'other': [-50, 0, 0], 'extra': [80, 40, 0]})
    original = _effect_executor.dice_roll
    damage = []

    def recorded(sides, count=1, modifier=0):
        result = original(sides, count, modifier)
        if sides == 6:
            damage.append(result)
        return result

    monkeypatch.setattr(_effect_executor, 'dice_roll', recorded)
    case.start()
    receipt, command = case.act('cast_spell', **(case.parameters | {
        'target_distance': 999 if critical else 0,
        'target_can_see_caster': False, 'caster_can_see_target': False}))
    assert len(damage) == 1 and damage[0].count == (8 if critical else 4)
    assert case.entities()['guard']['hp'] == 1000 - damage[0].total
    assert '' not in case.snapshot().state[VARIANT_ID + '.combat']
    saved = case.snapshot()
    case.reopen()
    assert case.execute(command) == receipt and case.snapshot().state == saved.state
    assert len(damage) == 1


def test_real_ray_miss_critical_and_later_defeated_target_skip(healing_case, monkeypatch):
    case = healing_case('scorching_ray', entity_changes={
        'guard': {'armor_class': 100}, 'other': {'hp': 1, 'creature_kind': 'monster'},
    })
    case.start()

    class RayRng(Random):
        def __init__(self):
            super().__init__(1)
            self.attacks = iter((1, 20))

        def randint(self, lower, upper):
            return next(self.attacks) if (lower, upper) == (1, 20) else super().randint(lower, upper)

    monkeypatch.setattr(dice, '_rng', RayRng())
    original = _effect_executor.dice_roll
    rolls = []

    def recorded(sides, count=1, modifier=0):
        result = original(sides, count, modifier)
        rolls.append(result)
        return result

    monkeypatch.setattr(_effect_executor, 'dice_roll', recorded)
    parameters = {key: value for key, value in case.parameters.items()
        if key not in ('target', 'target_distance', 'caster_can_see_target')}
    receipt, command = case.act('cast_spell', **(parameters | {'attack_targets': ['guard', 'other', 'other']}))
    assert [roll.sides for roll in rolls] == [20, 20, 6]
    assert rolls[0].results == (1,) and rolls[1].results == (20,)
    assert rolls[2].count == 4
    assert case.entities()['guard']['hp'] == 1000
    assert case.entities()['other']['hp'] == 0 and case.entities()['other']['is_alive'] is False
    assert case.entities()['hero']['resources']['spell_slot_2'] == 2
    assert case.snapshot().state[VARIANT_ID + '.combat']['action_available'] is False
    assert '' not in case.snapshot().state[VARIANT_ID + '.combat']
    saved = case.snapshot()
    case.reopen()
    assert case.execute(command) == receipt and case.snapshot().state == saved.state
    assert len(rolls) == 3
