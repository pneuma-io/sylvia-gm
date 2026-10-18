"""Real scene cover composes with native Slam dice, armor and pending reactions."""
from copy import deepcopy
from pathlib import Path
from random import Random
import json, os
import pytest
from scripts.srd_lite_steed_slam_cover import COVER, ARMOR, bind_steed_slam_cover
from scripts.srd_lite_damage_roll_receipts import _lists as _effect_lists
from sylvia_gm import dice
from sylvia_gm.application.package_runtime import _json_safe
from sylvia_gm.application.result_cards import project_result_card
from sylvia_gm.runtime import RulesetRuntime
from tests.test_srd_lite_steed_slam import bound as slam_bound, _slam_case
from tests.test_srd_lite_steed_casting_statistics import bound as statistics_bound
from tests.test_srd_lite_steed_healing_touch import bound as touch_bound, _snapshot
from tests.test_srd_lite_mass_cure_geometry import _calls, _wall


@pytest.fixture(scope='module')
def bound(slam_bound):
    data = deepcopy(slam_bound[1])
    profiles = deepcopy(data['definitions']['spells'])
    dice_before = {name: [deepcopy(e) for rows in _effect_lists(tool['effects'])
        for e in rows if 'dice' in e] for name, tool in data['tools'].items()}
    changed = bind_steed_slam_cover(data)
    assert profiles == data['definitions']['spells']
    assert dice_before == {name: [e for rows in _effect_lists(tool['effects'])
        for e in rows if 'dice' in e] for name, tool in data['tools'].items()}
    closure, pending = {}, ['resolve_adjudicated_creature_attack', 'begin_srd_creature_attack',
        'complete_srd_pending_action', 'continue_srd_creature_attack_targeting']
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data['tools'][name]
            pending.extend(_calls(closure[name]['effects']))
    parsed = RulesetRuntime(ruleset_dict={**{k: v for k, v in data.items()
        if k not in {'tools', 'procedures'}}, 'tools': closure})
    return parsed, data, changed


def _case(bound, cover='none', *, armor=19, admitted=True):
    bridge, act, parameters = _slam_case(bound)
    bridge.write_field('target', 'armor_class', armor)
    low = _wall(height=3)
    low['volume'] = {'kind': 'box', 'min': [8, -20, -10], 'max': [8.1, 20, 3]}
    post = _wall()
    post.update(id='post', volume={'kind': 'box', 'min': [8, -.05, -10], 'max': [8.1, .05, 8]})
    full = _wall()
    full['volume'] = {'kind': 'box', 'min': [8, -20, -10], 'max': [8.1, 20, 8]}
    geometry = {'none': [], 'half': [low], 'three_quarters': [low, post], 'total': [full]}[cover]
    scene = deepcopy(bridge.get_entity('state')['spatial_scene'])
    scene['geometry'] = geometry
    if admitted and cover != 'none':
        target = next(row for row in scene['entities'] if row['id'] == 'target')
        target['metadata']['cover_from'] = {'steed': cover}
    bridge.write_field('state', 'spatial_scene', scene)
    return bridge, act, parameters


def _card(bound, name, result, parameters):
    return project_result_card(bound[1]['tools'][name]['metadata']['result_card'],
        parameters=parameters, public_context=_json_safe(result.context))


@pytest.mark.parametrize('cover,bonus,outcome', [('none', 0, 'hit'),
    ('half', 2, 'miss'), ('three_quarters', 5, 'miss')])
def test_real_partial_cover_changes_target_ac_without_changing_attack_d20(bound,
        monkeypatch, cover, bonus, outcome):
    monkeypatch.setattr(dice, '_rng', Random(0))
    bridge, act, parameters = _case(bound, cover)
    result = act('resolve_adjudicated_creature_attack', **parameters)
    assert result.success, result.error
    assert result.context[COVER] == bonus
    assert result.context[ARMOR] == 19 + bonus
    assert result.context['_attack_roll'].results == (13,)
    assert result.context['_attack_adjusted_total'] == 20
    assert result.context['_attack_outcome'] == outcome
    assert bridge.get_entity('target')['armor_class'] == 19
    assert bridge.get_entity('target')['hp'] == (41 if outcome == 'hit' else 50)
    card = _card(bound, 'resolve_adjudicated_creature_attack', result, parameters)
    assert card['equation'] == {'die_sides': 20, 'roll': 13, 'modifier': 7, 'total': 20}
    assert next(r['value'] for r in card['summary'] if r['label'] == 'Target AC') == 19 + bonus
    cover_rows = [r for r in card['summary'] if r['label'] == 'Cover · AC bonus']
    assert ([r['value'] for r in cover_rows]) == ([bonus] if bonus else [])
    if bonus:
        assert result.context['_damage_roll'] is None
        assert not any('equation' in r for r in card['summary'])
    if os.environ.get('SRD_LITE_STEED_COVER_CARD') and cover == 'half':
        Path(os.environ['SRD_LITE_STEED_COVER_CARD']).write_text(json.dumps(card, indent=2), encoding='utf-8')


@pytest.mark.parametrize('owner', ('resolve_adjudicated_creature_attack', 'begin_srd_creature_attack'))
def test_total_cover_rejects_before_dice_or_action_payment(bound, owner):
    bridge, act, parameters = _case(bound, 'total')
    before, rng_before = _snapshot(bridge), dice._rng.getstate()
    result = act(owner, **parameters)
    assert not result.success and 'Total Cover' in result.error
    assert _snapshot(bridge) == before and dice._rng.getstate() == rng_before


def test_cover_is_added_after_native_armor_floor(bound, monkeypatch):
    monkeypatch.setattr(dice, '_rng', Random(0))
    bridge, act, parameters = _case(bound, 'half', armor=13)
    bridge.write_field('state', 'turn_effects', [{'id': 'armor-floor', 'kind': 'armor_class_floor',
        'target': 'target', 'magnitude': 19, 'expires_tick': 100}])
    result = act('resolve_adjudicated_creature_attack', **parameters)
    assert result.success, result.error
    assert result.context[ARMOR] == 21 and result.context['_attack_outcome'] == 'miss'


def test_unadmitted_obstruction_rejects_before_dice_or_payment(bound):
    bridge, act, parameters = _case(bound, 'half', admitted=False)
    before, rng_before = _snapshot(bridge), dice._rng.getstate()
    result = act('resolve_adjudicated_creature_attack', **parameters)
    assert not result.success and 'admitted categorical Cover' in result.error
    assert _snapshot(bridge) == before and dice._rng.getstate() == rng_before


@pytest.mark.parametrize('shield', (False, True))
def test_native_pending_completion_keeps_cover_and_original_roll(bound, monkeypatch, shield):
    monkeypatch.setattr(dice, '_rng', Random(0))
    bridge, act, parameters = _case(bound, 'half', armor=17 if shield else 19)
    started = act('begin_srd_creature_attack', **parameters)
    assert started.success, started.error
    pending_id = started.context['_pending_action_id']
    pending = bridge.get_entity('state')['pending_srd_actions'][pending_id]
    assert pending['context'][COVER] == 2
    assert pending['attack_roll'].results == (13,)
    if shield:
        # This composition uses the native shield resolver's actual active source state.
        bridge.write_field('target', 'reaction_shield', {'expires_turn': 1, 'expires_tick': 100})
        bridge.write_field('target', 'turns_started', 0)
    rng_before = dice._rng.getstate()
    finished = act('complete_srd_pending_action', pending_action_id=pending_id)
    assert finished.success, finished.error
    assert finished.context[COVER] == 2
    assert finished.context[ARMOR] == (24 if shield else 21)
    assert finished.context['_attack_roll'].results == (13,)
    assert finished.context['_attack_outcome'] == 'miss'
    assert finished.context['_damage_roll'] is None
    assert dice._rng.getstate() == rng_before
    assert bridge.get_entity('target')['hp'] == 50
    card = _card(bound, 'complete_srd_pending_action', finished, {'pending_action_id': pending_id})
    assert next(r['value'] for r in card['summary'] if r['label'] == 'Cover · AC bonus') == 2
    assert next(r['value'] for r in card['summary'] if r['label'] == 'Target AC') == (24 if shield else 21)
    assert next(r['equation'] for r in card['summary'] if r['label'] == 'Attack roll') == {
        'die_sides': 20, 'roll': 13, 'modifier': 7, 'total': 20}


def test_other_creature_attack_keeps_existing_behavior(bound, monkeypatch):
    monkeypatch.setattr(dice, '_rng', Random(0))
    bridge, act, parameters = _case(bound, 'half', armor=13)
    bridge.write_field('steed', 'created_spell_id', None)
    result = act('resolve_adjudicated_creature_attack', **{**parameters,
        'attack_name': 'Claw', 'damage_expression': 'd6'})
    assert result.success, result.error
    assert result.context['_attack_outcome'] == 'hit'
    card = _card(bound, 'resolve_adjudicated_creature_attack', result, parameters)
    assert not any(r['label'] in {'Target AC', 'Cover · AC bonus'} for r in card['summary'])


def test_pending_ordinary_miss_has_no_damage_roll_or_steed_cover_rows(bound, monkeypatch):
    monkeypatch.setattr(dice, '_rng', Random(0))
    bridge, act, parameters = _case(bound, 'none', armor=30)
    bridge.write_field('steed', 'created_spell_id', None)
    started = act('begin_srd_creature_attack', **{**parameters,
        'attack_name': 'Claw', 'damage_expression': 'd6'})
    assert started.success, started.error
    pending_id = started.context['_pending_action_id']
    rng_before = dice._rng.getstate()
    finished = act('complete_srd_pending_action', pending_action_id=pending_id)
    assert finished.success, finished.error
    assert finished.context['_attack_outcome'] == 'miss'
    assert finished.context['_damage_roll'] is None and dice._rng.getstate() == rng_before
    card = _card(bound, 'complete_srd_pending_action', finished, {'pending_action_id': pending_id})
    assert not any(r['label'] in {'Target AC', 'Cover · AC bonus', 'Slam damage before defenses'}
                   for r in card['summary'])
