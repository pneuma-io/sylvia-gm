"""Native controlled-Steed budgets, boundaries, handoff and honest receipt projection."""
from copy import deepcopy
from pathlib import Path
import json
import os
import zipfile
import pytest
import yaml
from scripts.srd_lite_steed_turns import (
    BEGIN, PAUSE, ACT, SAVE, RESTORE, NATIVE_BEGIN, NATIVE_END, BUDGET, TOKEN, ENDED,
    CONTINUATION, bind_steed_turns,
)
from scripts.srd_lite_steed_mount import MOUNT, BUDGET as MOUNT_BUDGET
from scripts.srd_lite_steed_healing_touch import CHARGE
from scripts.srd_lite_steed_fey_step import RIDER_FIELD, MOUNT_FIELD
from sylvia_gm import dice
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.application.package_runtime import _json_safe
from sylvia_gm.application.result_cards import project_result_card
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _calls
from tests.test_srd_lite_steed_healing_touch import _case, _snapshot


@pytest.fixture(scope='module')
def bound():
    from sylvia_gm.runtime import _effect_executor
    assert Path(_effect_executor.__file__).resolve().is_relative_to(Path(os.environ['SRD_LITE_EXPECT_CORE']))
    with zipfile.ZipFile(Path(os.environ['SRD_LITE_CANDIDATE'])) as archive:
        path = next(name for name in archive.namelist()
                    if name.endswith('/runtime/ruleset.yaml') and NAMESPACE in name)
        data = yaml.load(archive.read(path), Loader=yaml.CSafeLoader)
    pool = data.get('parameter_declarations', {})
    for tool in data['tools'].values():
        tool['parameters'] = [deepcopy(pool[row]) if isinstance(row, str) else row for row in tool['parameters']]
    original_begin = deepcopy(data['tools']['begin_combat_turn'])
    original_end = deepcopy(data['tools']['end_combat_turn'])
    profiles = deepcopy(data['definitions']['spells'])
    record = {'tools': []}
    bind_steed_turns(data, record)
    assert data['definitions']['spells'] == profiles
    assert data['tools']['begin_combat_turn']['parameters'] == original_begin['parameters']
    assert data['tools']['end_combat_turn']['parameters'] == original_end['parameters']
    assert data['tools']['begin_combat_turn']['pre_conditions'] == original_begin['pre_conditions']
    assert data['tools']['end_combat_turn']['pre_conditions'] == original_end['pre_conditions']
    assert data['tools'][NATIVE_BEGIN]['effects'] != [] and data['tools'][NATIVE_END]['effects'] != []
    assert {row['name'] for row in record['tools']} == {BEGIN, PAUSE, ACT}
    closure, pending = {}, [BEGIN, PAUSE, ACT, MOUNT, MOUNT_BUDGET, 'begin_combat_turn', 'end_combat_turn',
                           'spend_srd_action', 'consume_srd_bonus_action', 'apply_srd_dash',
                           'advance_group_combat_encounter_turn']
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data['tools'][name]
            pending.extend(_calls(closure[name]['effects']))
    subset = {key: value for key, value in data.items() if key not in {'tools', 'procedures'}}
    parsed = RulesetRuntime(ruleset_dict={**subset, 'tools': closure})
    return parsed, data, record


def _turn_case(bound, *, mounted=True):
    bridge, _, act = _case(bound, combat=True,
        caster_changes={'speed': 30, 'size_rank': 2, 'spell_speed_reduction': 0,
            'dexterity_modifier': 2, MOUNT_FIELD: 'steed' if mounted else None},
        steed_changes={'speed': 60, 'size_rank': 3, 'spell_speed_reduction': 0,
            RIDER_FIELD: 'caster' if mounted else None, 'summon_active': True,
            'available_bonus_actions': ['Healing Touch']})
    bridge.combat_state.update(turn_active=False, active_actor=None, current_actor='caster',
        round=1, turn_index=0, turn_sequence=0, group_order=True,
        group_clock_start_tick=0,
        order=[{'entity_id': name, 'initiative_result': value}
               for name, value in [('caster', 15), ('steed', 15), ('target', 10)]])
    if mounted:
        scene = bridge.get_entity('state')['spatial_scene']
        scene['entities'][0]['position'] = [0, 0, 6]
    return bridge, act


def _success(result):
    assert result.success, result.error
    return result


def _phase(act, name=BEGIN):
    return _success(act(name, rider_state='caster', steed_state='steed'))


def test_native_shared_turn_preserves_spent_actions_bonus_movement_sequence_and_clock(bound):
    bridge, act = _turn_case(bound)
    rng = dice._rng.getstate()
    _success(act('begin_combat_turn', actor='caster'))
    assert bridge.combat_state['active_actor'] == 'caster'
    assert bridge.combat_state['turn_sequence'] == 1
    assert bridge.get_entity('caster')['turns_started'] == 1
    assert bridge.get_entity('steed')['turns_started'] == 1
    assert bridge.get_entity('steed')[BUDGET]['action_available'] is True
    assert bridge.get_entity('steed')['dodging'] is False
    _success(act('spend_srd_action'))
    _phase(act)
    assert bridge.combat_state['active_actor'] == 'steed'
    assert bridge.combat_state['movement_remaining'] == 60
    _success(act(ACT, steed_state='steed', action='dash'))
    _success(act(MOUNT_BUDGET, actor_state='steed'))
    _success(act('consume_srd_bonus_action', actor_state='steed', bonus_action='Healing Touch'))
    _phase(act, PAUSE)
    assert bridge.combat_state['active_actor'] == 'caster'
    assert bridge.combat_state['action_available'] is False
    assert bridge.combat_state['movement_remaining'] == 30
    resumed = _phase(act)
    assert bridge.combat_state['action_available'] is False
    assert bridge.combat_state['movement_remaining'] == 90
    assert bridge.combat_state['movement_spent'] == 30
    assert bridge.combat_state['dashes_taken'] == 1
    assert bridge.get_entity('steed')['bonus_action_available'] is False
    assert bridge.get_entity('steed')[CHARGE] is True
    assert bridge.get_entity('steed')['turns_started'] == 1
    assert bridge.combat_state['turn_sequence'] == 1
    assert bridge.get_entity('state')['lasting_tick'] == 0
    card = project_result_card(bound[1]['tools'][BEGIN]['metadata']['result_card'],
        parameters={}, public_context=_json_safe(resumed.context))
    values = {row['label']: row['value'] for row in card['summary']}
    assert values['Steed movement (ft)'] == 90
    assert values['Steed Action'] == 'Spent' and values['Steed Bonus Action'] == 'Spent'
    if os.environ.get('SRD_LITE_TURN_CARD'):
        Path(os.environ['SRD_LITE_TURN_CARD']).write_text(json.dumps(card, indent=2), encoding='utf-8')
    _phase(act, PAUSE)
    ended = _success(act('end_combat_turn', actor='caster'))
    assert bridge.get_entity('steed')[ENDED] is True
    assert bridge.get_entity('steed')[BUDGET]['turn_active'] is False
    assert bridge.combat_state['turn_active'] is False
    assert bridge.combat_state['current_actor'] == 'caster'
    assert bridge.get_entity('state')['lasting_tick'] == 0
    assert bridge.combat_state['turn_sequence'] == 1
    assert dice._rng.getstate() == rng
    final_card = project_result_card(bound[1]['tools']['end_combat_turn']['metadata']['result_card'],
        parameters={}, public_context=_json_safe(ended.context))
    assert any(row['label'] == 'Steed turn ended' for row in final_card['details'])


def test_actual_mid_turn_mount_preserves_paid_rider_movement_and_initializes_steed_once(bound):
    bridge, act = _turn_case(bound, mounted=False)
    rng = dice._rng.getstate()
    _success(act('begin_combat_turn', actor='caster'))
    assert bridge.get_entity('steed').get('turns_started') is None
    mounted = _success(act(MOUNT, rider_state='caster', steed_state='steed'))
    assert mounted.context['_ps_mount_cost'] == 15
    assert bridge.combat_state['movement_remaining'] == 15
    assert bridge.combat_state['movement_spent'] == 15
    assert bridge.combat_state['active_actor'] == 'caster'
    assert bridge.get_entity('steed')['turns_started'] == 1
    _phase(act)
    _phase(act, PAUSE)
    assert bridge.combat_state['movement_remaining'] == 15
    assert bridge.get_entity('steed')['turns_started'] == 1
    assert dice._rng.getstate() == rng


def test_pending_window_and_wrong_rider_cannot_switch_or_close_actual_steed_phase(bound):
    bridge, act = _turn_case(bound)
    _success(act('begin_combat_turn', actor='caster'))
    before = _snapshot(bridge)
    rejected = act(BEGIN, rider_state='target', steed_state='steed')
    assert not rejected.success and 'actual capable caster' in rejected.error
    assert _snapshot(bridge) == before
    _phase(act)
    before = _snapshot(bridge)
    rejected = act('end_combat_turn', actor='steed')
    assert not rejected.success and 'Pause the actual Steed phase' in rejected.error
    assert _snapshot(bridge) == before
    bridge.write_field('state', 'pending_srd_action_stack', [{'kind': 'test_actual_pending_window'}])
    before = _snapshot(bridge)
    rejected = act(PAUSE, rider_state='caster', steed_state='steed')
    assert not rejected.success and 'pending' in rejected.error.lower()
    assert _snapshot(bridge) == before


def test_incapacitated_caster_handoff_resumes_existing_budget_without_a_second_turn_start(bound):
    bridge, act = _turn_case(bound)
    _success(act('begin_combat_turn', actor='caster'))
    _phase(act)
    _success(act(ACT, steed_state='steed', action='dodge'))
    _success(act('consume_srd_bonus_action', actor_state='steed', bonus_action='Healing Touch'))
    _phase(act, PAUSE)
    bridge.write_field('caster', 'conditions', ['incapacitated'])
    _success(act('end_combat_turn', actor='caster'))
    assert bridge.get_entity('steed')[CONTINUATION] is True
    started = bridge.get_entity('steed')['turns_started']
    sequence = bridge.combat_state['turn_sequence']
    _success(act('advance_group_combat_encounter_turn', completed_actor='caster'))
    assert bridge.combat_state['current_actor'] == 'steed'
    tick = bridge.get_entity('state')['lasting_tick']
    _success(act('begin_combat_turn', actor='steed'))
    assert bridge.combat_state['active_actor'] == 'steed'
    assert bridge.combat_state['action_available'] is False
    assert bridge.get_entity('steed')['bonus_action_available'] is False
    assert bridge.get_entity('steed')['turns_started'] == started
    assert bridge.combat_state['turn_sequence'] == sequence
    assert bridge.get_entity('state')['lasting_tick'] == tick
    _success(act('end_combat_turn', actor='steed'))
    assert bridge.get_entity('steed')[ENDED] is True


@pytest.mark.parametrize('kind', ['disengage', 'dodge'])
def test_actual_generated_steed_native_action_and_second_action_rejection(bound, kind):
    bridge, act = _turn_case(bound)
    _success(act('begin_combat_turn', actor='caster'))
    _phase(act)
    rng = dice._rng.getstate()
    result = _success(act(ACT, steed_state='steed', action=kind))
    assert bridge.combat_state['action_available'] is False
    if kind == 'dodge':
        assert bridge.get_entity('steed')['dodging'] is True
    else:
        assert bridge.combat_state['disengaged_actor'] == 'steed'
    before = _snapshot(bridge)
    failed = act(ACT, steed_state='steed', action=kind)
    assert not failed.success and 'already been spent' in failed.error
    assert _snapshot(bridge) == before
    assert dice._rng.getstate() == rng
    card = project_result_card(bound[1]['tools'][ACT]['metadata']['result_card'],
        parameters={}, public_context=_json_safe(result.context))
    assert {row['label']: row['value'] for row in card['summary']}['Action'] == kind.title()
    if os.environ.get('SRD_LITE_ACTION_CARD'):
        Path(os.environ['SRD_LITE_ACTION_CARD']).write_text(json.dumps(card, indent=2), encoding='utf-8')


def test_initially_incapacitated_caster_gives_steed_a_native_independent_protective_action(bound):
    bridge, act = _turn_case(bound)
    bridge.write_field('caster', 'conditions', ['incapacitated'])
    _success(act('begin_combat_turn', actor='caster'))
    assert bridge.get_entity('steed').get('turns_started') is None
    _success(act('end_combat_turn', actor='caster'))
    _success(act('advance_group_combat_encounter_turn', completed_actor='caster'))
    assert bridge.combat_state['current_actor'] == 'steed'
    _success(act('begin_combat_turn', actor='steed'))
    assert bridge.combat_state['action_available'] is True
    assert bridge.get_entity('steed')['turns_started'] == 1
    _success(act(ACT, steed_state='steed', action='dash'))
    assert bridge.combat_state['movement_remaining'] == 120
    assert bridge.combat_state['action_available'] is False
