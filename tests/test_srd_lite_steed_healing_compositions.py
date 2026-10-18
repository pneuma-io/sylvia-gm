"""Aura of Life feedback and Life Bond composition at the original HP owners."""
from copy import deepcopy
from random import Random
from pathlib import Path
import json, os
import pytest

from sylvia_gm import dice
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.application.package_runtime import _json_safe
from sylvia_gm.application.result_cards import project_result_card
from sylvia_gm.application.result_card_admission import validate_result_card_declaration
from scripts.srd_lite_catalogue import SELECTED
from tests.test_srd_lite_steed_healing_touch import bound as touch_bound, _case
from tests.test_srd_lite_mass_cure_geometry import _calls


@pytest.fixture(scope='module')
def bound(touch_bound):
    data = deepcopy(touch_bound[1])
    closure, pending = {}, ['cast_srd_spell_ward', 'start_srd_aura_of_life_turn',
                            'resolve_srd_vampiric_touch_attack', 'begin_combat_turn', 'end_combat_turn']
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data['tools'][name]
            pending.extend(_calls(closure[name]['effects']))
    parsed = RulesetRuntime(ruleset_dict={**{key: value for key, value in data.items()
        if key not in {'tools', 'procedures'}}, 'tools': closure})
    return parsed, data, None


@pytest.mark.parametrize('blocked', [False, True])
def test_actual_aura_source_heals_zero_hp_owner_and_shares_only_actual_gain(bound, blocked):
    bridge, _, act = _case(bound, caster_changes={
        'hp': 0, 'conditions': ['unconscious'], 'hit_point_recovery_blocked': blocked},
        steed_changes={'hp': 5}, target_changes={'ally_state_ids': ['caster', 'steed']})
    before = dice._rng.getstate()
    source = act('cast_srd_spell_ward', caster_state='target',
                 spell='aura_of_life', slot_level=4, concentration_save_modifier=2)
    assert source.success, source.error
    result = act('start_srd_aura_of_life_turn', target_state='caster')
    assert result.success, result.error
    assert bridge.get_entity('caster')['hp'] == (0 if blocked else 1)
    assert bridge.get_entity('steed')['hp'] == (5 if blocked else 6)
    assert dice._rng.getstate() == before
    if not blocked:
        assert 'unconscious' not in bridge.get_entity('caster')['conditions']
        assert result.context['_life_bond_restored'] == 1


@pytest.mark.parametrize('caster_hp,resistances,immunities,damage,gain', [
    (49, [], [], 8, 1), (10, ['necrotic'], [], 4, 2), (10, [], ['necrotic'], 0, 0)])
def test_native_healing_composition_shares_actual_gain_after_cap_and_damage_defenses(
        bound, monkeypatch, caster_hp, resistances, immunities, damage, gain):
    # This is evidence for the shared healing boundary, not launch qualification
    # or exposure of the deferred Vampiric Touch public action.
    from sylvia_gm.runtime import _effect_executor
    original_roll = _effect_executor.dice_roll
    rolls = []
    def recorded(*args, **kwargs):
        result = original_roll(*args, **kwargs)
        rolls.append(result)
        return result
    monkeypatch.setattr(dice, '_rng', Random(0))
    monkeypatch.setattr(_effect_executor, 'dice_roll', recorded)
    bridge, _, act = _case(bound, position=(-6, 0, 0),
        caster_changes={'hp': caster_hp}, steed_changes={'hp': 5}, target_changes={
            'hp': 50, 'armor_class': 10, 'damage_immunities': immunities,
            'damage_resistances': resistances, 'damage_vulnerabilities': [],
            'created_steed_id': None})
    result = act('resolve_srd_vampiric_touch_attack', caster_state='caster', target='target',
                 _retained_attack_bonus=7, _retained_dice_count=3, _retained_max_range=5,
                 _retained_casting_ability='wisdom', source_id='synthetic-retained-source')
    assert result.success, result.error
    assert result.context['_retained_attack_kind'] == 'hit'
    assert result.context['_retained_attack_total'] == 20
    assert result.context['_retained_necrotic_damage'] == damage
    assert bridge.get_entity('target')['hp'] == 50 - damage
    assert bridge.get_entity('caster')['hp'] == caster_hp + gain
    assert bridge.get_entity('steed')['hp'] == 5 + gain
    assert len(rolls) == 2 and rolls[0].results == (13,) and rolls[0].sides == 20
    assert rolls[1].results == (4, 1, 3) and rolls[1].sides == 6


@pytest.mark.parametrize('source,blocked,linked,healed,bond', [
    (True, False, True, 1, 1), (True, True, True, 0, 0),
    (True, False, False, 1, 0), (False, False, True, 0, 0)])
def test_actual_begin_turn_transports_only_real_aura_and_life_bond_into_public_card(
        bound, source, blocked, linked, healed, bond):
    bridge, _, act = _case(bound, caster_changes={
        'hp': 0, 'conditions': ['unconscious'], 'speed': 30, 'turns_started': 0,
        'hit_point_recovery_blocked': blocked, 'created_steed_id': 'steed' if linked else None},
        steed_changes={'hp': 5}, target_changes={'ally_state_ids': ['caster', 'steed']})
    if source:
        result = act('cast_srd_spell_ward', caster_state='target', spell='aura_of_life',
                     slot_level=4, concentration_save_modifier=2)
        assert result.success, result.error
    bridge.combat_state.update(encounter_active=True, current_actor='caster', turn_active=False)
    before = dice._rng.getstate()
    result = act('begin_combat_turn', actor='caster')
    assert result.success, result.error
    assert bridge.get_entity('caster')['hp'] == healed and bridge.get_entity('steed')['hp'] == 5 + bond
    assert dice._rng.getstate() == before
    declaration = bound[1]['tools']['begin_combat_turn']['metadata']['result_card']
    validate_result_card_declaration(declaration)
    card = project_result_card(declaration, parameters={'actor': 'caster'},
                               public_context=_json_safe(result.context))
    assert dice._rng.getstate() == before
    if healed:
        assert card is not None and card['title'] == 'Turn started'
        assert next(row['value'] for row in card['summary'] if row['label'] == 'Creature healed') == 'Caster'
        assert next(row['value'] for row in card['summary'] if row['label'] == 'HP after') == healed
    summary = card['summary'] if card else []
    aura = [row['value'] for row in summary if row['label'] == 'Aura of Life · HP restored']
    life = [row['value'] for row in summary if row['label'] == 'Life Bond · steed HP restored']
    assert aura == ([healed] if healed else [])
    assert life == ([bond] if bond else [])
    assert not any('equation' in row for row in summary)
    if bond and os.environ.get('SRD_LITE_AURA_LIFE_CARD'):
        Path(os.environ['SRD_LITE_AURA_LIFE_CARD']).write_text(json.dumps(card, indent=2), encoding='utf-8')


def test_end_and_next_healthy_begin_turn_do_not_repeat_the_aura_healing(bound):
    bridge, _, act = _case(bound, caster_changes={
        'hp': 0, 'conditions': ['unconscious'], 'speed': 30, 'turns_started': 0},
        steed_changes={'hp': 5}, target_changes={'ally_state_ids': ['caster', 'steed']})
    assert act('cast_srd_spell_ward', caster_state='target', spell='aura_of_life',
               slot_level=4, concentration_save_modifier=2).success
    bridge.combat_state.update(encounter_active=True, current_actor='caster', turn_active=False)
    assert act('begin_combat_turn', actor='caster').success
    before = dice._rng.getstate()
    for name in ('end_combat_turn', 'begin_combat_turn'):
        result = act(name, actor='caster')
        assert result.success, result.error
        assert bridge.get_entity('caster')['hp'] == 1 and bridge.get_entity('steed')['hp'] == 6
        card = project_result_card(bound[1]['tools'][name]['metadata']['result_card'],
            parameters={'actor': 'caster'}, public_context=_json_safe(result.context))
        assert not any(row['label'] in ('Aura of Life · HP restored', 'Life Bond · steed HP restored')
                       for row in (card['summary'] if card else []))
    assert dice._rng.getstate() == before


def test_deferred_retained_action_stays_disabled_and_launch_roster_stays_exact(bound):
    assert len(SELECTED) == 96 and 'aura_of_life' in SELECTED and 'find_steed' in SELECTED
    assert 'vampiric_touch' not in SELECTED
    assert bound[1]['tools']['use_srd_vampiric_touch']['effects'] == [{'require': {
        'condition': 'false', 'error': 'This spell operation is pending or excluded in the SRD Lite foundation.'}}]
