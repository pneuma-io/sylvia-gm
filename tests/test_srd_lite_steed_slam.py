"""Otherworldly Slam composes current owner statistics with the three native attack phases."""
from copy import deepcopy
from random import Random
import json, os
from pathlib import Path
import pytest
from scripts.srd_lite_steed_slam import OWNERS, CACHED, SAVED, bind_steed_slam, bind_steed_slam_widgets
from sylvia_gm import dice
from sylvia_gm.application.package_runtime import _json_safe
from sylvia_gm.application.result_cards import project_result_card
from sylvia_gm.runtime import RulesetRuntime
from tests.test_srd_lite_steed_casting_statistics import bound as statistics_bound, _statistics_case
from tests.test_srd_lite_steed_healing_touch import bound as touch_bound, _snapshot
from tests.test_srd_lite_mass_cure_geometry import _calls


@pytest.fixture(scope='module')
def bound(statistics_bound):
    data = deepcopy(statistics_bound[1])
    before = {name: deepcopy(data['tools'][name]) for name in OWNERS}
    completion_card = deepcopy(data['tools']['complete_srd_pending_action']['metadata']['result_card'])
    profiles = deepcopy(data['definitions']['spells'])
    changed = bind_steed_slam(data)
    assert set(changed) == set(OWNERS) and data['definitions']['spells'] == profiles
    for name in OWNERS:
        assert data['tools'][name]['parameters'] == before[name]['parameters']
        assert data['tools'][name]['effects'][1:] == before[name]['effects']
        assert data['tools'][name]['metadata'] == before[name]['metadata']
        expected = deepcopy(before[name]['pre_conditions'])
        for guard in expected:
            guard['condition'] = guard['condition'].replace(CACHED, '')
        assert data['tools'][name]['pre_conditions'] == expected
    bind_steed_slam_widgets(data)
    assert data['tools']['complete_srd_pending_action']['metadata']['result_card'] == completion_card
    closure, pending = {}, [*OWNERS, 'complete_srd_creature_attack', 'complete_srd_pending_action']
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data['tools'][name]
            pending.extend(_calls(closure[name]['effects']))
    parsed = RulesetRuntime(ruleset_dict={**{key: value for key, value in data.items()
        if key not in {'tools', 'procedures'}}, 'tools': closure})
    return parsed, data, changed


def _slam_case(bound, *, creature_type='celestial', npc=False, caster_changes=None):
    damage_type = {'celestial': 'radiant', 'fey': 'psychic', 'fiend': 'necrotic'}[creature_type]
    bridge, act = _statistics_case(bound, npc=npc,
        steed_changes={'creature_type': creature_type, 'created_attack_damage_type': damage_type},
        caster_changes=caster_changes)
    bridge.combat_state.update(encounter_active=True, turn_active=True, active_actor='steed',
        current_actor='steed', action_available=True, attack_available=True, attacks_remaining=0)
    for name in ('steed', 'target'):
        for field, value in {'damage_immunities': [], 'damage_resistances': [],
            'damage_vulnerabilities': [], 'armor_class': 13, 'resource_maxima': {}}.items():
            bridge.write_field(name, field, value)
    bridge.write_field('target', 'hp', 50)
    parameters = {'actor': 'steed', 'target': 'target', 'attack_name': 'Otherworldly Slam',
        'attack_modifier': 9 if npc else 7, 'damage_expression': 'd8', 'damage_modifier': 2,
        'damage_type': damage_type, 'attack_mode': 'melee', 'attack_distance': 5,
        'normal_range': 5, 'long_range': 5, 'use_multiattack': False,
        'advantage': False, 'disadvantage': False, 'actor_can_see_target': True,
        'target_can_see_actor': True, 'target_is_charmer': False, 'fear_source_in_sight': False,
        'attack_uses_weapon': False}
    return bridge, act, parameters


@pytest.mark.parametrize('owner', OWNERS)
def test_each_phase_rejects_cached_modifier_before_rng_or_spending(bound, owner):
    bridge, act, parameters = _slam_case(bound)
    before, rng_before = _snapshot(bridge), dice._rng.getstate()
    if owner == 'continue_srd_creature_attack_targeting':
        parameters['pending_action_id'] = 'pending-slam'
    result = act(owner, **{**parameters, 'attack_modifier': 4})
    assert not result.success and 'current spell attack bonus' in result.error
    assert _snapshot(bridge) == before and dice._rng.getstate() == rng_before


@pytest.mark.parametrize('seed,creature_type,expected_outcome',
    [(0, 'celestial', 'hit'), (5, 'fiend', 'critical_hit'), (2, 'fey', 'miss')])
def test_native_attack_hit_critical_miss_and_actual_card(bound, monkeypatch, seed,
                                                      creature_type, expected_outcome):
    monkeypatch.setattr(dice, '_rng', Random(seed))
    bridge, act, parameters = _slam_case(bound, creature_type=creature_type)
    result = act(OWNERS[0], **parameters)
    assert result.success, result.error
    assert result.context['_steed_attack_bonus'] == 7
    assert result.context['_attack_outcome'] == expected_outcome
    assert bridge.get_entity('steed')['created_attack_bonus'] == 4
    assert bridge.combat_state['action_available'] is False
    assert bridge.combat_state['attack_available'] is False
    card = project_result_card(bound[1]['tools'][OWNERS[0]]['metadata']['result_card'],
        parameters=parameters, public_context=_json_safe(result.context))
    assert card is not None and card['equation']['die_sides'] == 20
    assert card['equation']['modifier'] == 7
    assert bridge.get_entity('target')['hp'] == 50 - result.context['_damage_applied']
    if expected_outcome == 'miss':
        assert result.context['_damage_applied'] == 0 and result.context['_damage_roll'] is None
        assert not any(row['label'] == 'Slam damage before defenses' for row in card['summary'])
    else:
        assert result.context['_damage_roll'].sides == 8
        assert result.context['_damage_roll'].count == (2 if expected_outcome == 'critical_hit' else 1)
        equation = next(row for row in card['summary']
                        if row['label'] == 'Slam damage before defenses')['equation']
        assert equation['rolls'] == list(result.context[SAVED].results)
        assert equation['roll'] == sum(equation['rolls'])
        assert equation['total'] == result.context['_damage_incoming']
        assert equation['modifier'] == 2
    if os.environ.get('SRD_LITE_STEED_SLAM_CARD') and seed == 0:
        Path(os.environ['SRD_LITE_STEED_SLAM_CARD']).write_text(json.dumps(card, indent=2), encoding='utf-8')


def test_native_npc_owner_uses_current_bonus_without_character_artifact(bound, monkeypatch):
    monkeypatch.setattr(dice, '_rng', Random(0))
    bridge, act, parameters = _slam_case(bound, npc=True)
    result = act(OWNERS[0], **parameters)
    assert result.success, result.error
    assert result.context['_steed_attack_bonus'] == 9


def test_original_damage_profile_rejection_remains_unchanged(bound):
    bridge, act, parameters = _slam_case(bound)
    before, rng_before = _snapshot(bridge), dice._rng.getstate()
    result = act(OWNERS[0], **{**parameters, 'damage_modifier': 100})
    assert not result.success and 'actual saved Otherworldly Slam source profile' in result.error
    assert _snapshot(bridge) == before and dice._rng.getstate() == rng_before


def test_native_begin_completion_keeps_the_original_slam_pool(bound, monkeypatch):
    monkeypatch.setattr(dice, '_rng', Random(0))
    bridge, act, parameters = _slam_case(bound)
    started = act('begin_srd_creature_attack', **parameters)
    assert started.success, started.error
    assert started.context.get('_pending_action_id') is not None
    finished = act('complete_srd_pending_action', pending_action_id=started.context['_pending_action_id'])
    assert finished.success, finished.error
    assert finished.context[SAVED].results == (7,)
    assert finished.context['_damage_applied'] == 9
    assert bridge.get_entity('target')['hp'] == 41
    card = project_result_card(bound[1]['tools']['complete_srd_pending_action']['metadata']['result_card'],
        parameters={'pending_action_id': started.context['_pending_action_id']},
        public_context=_json_safe(finished.context))
    assert card is not None and next(row for row in card['summary']
                                   if row['label'] == 'Attack roll')['equation'] == {
        'die_sides': 20, 'roll': 13, 'modifier': 7, 'total': 20}
    assert next(row for row in card['summary']
                if row['label'] == 'Slam damage before defenses')['equation']['rolls'] == [7]


def test_ordinary_creature_attack_does_not_gain_a_slam_damage_row(bound, monkeypatch):
    monkeypatch.setattr(dice, '_rng', Random(0))
    bridge, act, parameters = _slam_case(bound)
    bridge.write_field('steed', 'created_spell_id', None)
    result = act(OWNERS[0], **{**parameters, 'attack_name': 'Claw', 'damage_expression': 'd6'})
    assert result.success, result.error
    card = project_result_card(bound[1]['tools'][OWNERS[0]]['metadata']['result_card'],
        parameters=parameters, public_context=_json_safe(result.context))
    assert card is not None and card['equation']['modifier'] == 7
    assert not any(row['label'] == 'Slam damage before defenses' for row in card['summary'])
