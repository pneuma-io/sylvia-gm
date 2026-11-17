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
from tests.srd_lite_mass_cure_geometry_fixtures import NAMESPACE, _calls
from tests.srd_lite_steed_healing_touch_fixtures import _case, _snapshot


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




def check_actual_mid_turn_mount_preserves_paid_rider_movement_and_initializes_steed_once(bound):
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
