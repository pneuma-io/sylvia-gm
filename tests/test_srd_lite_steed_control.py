"""Controlled Steed restrictions and initiative through genuine native owning leaves."""
from copy import deepcopy
from pathlib import Path
import os
import zipfile
import pytest
import yaml
from scripts.srd_lite_steed_control import (
    READ, CONTROLLED, ELIGIBLE_FIELD, bind_steed_control,
)
from scripts.srd_lite_steed_fey_step import RIDER_FIELD, MOUNT_FIELD
from scripts.srd_lite_steed_healing_touch import CHARGE
from scripts.srd_timed_actor_defenses import _tool
from sylvia_gm import dice
from sylvia_gm.runtime import RulesetRuntime
from tests.test_srd_lite_steed_healing_touch import _case, _snapshot
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _calls


@pytest.fixture(scope='module')
def bound():
    with zipfile.ZipFile(Path(os.environ['SRD_LITE_CANDIDATE'])) as archive:
        name = next(name for name in archive.namelist()
                    if name.endswith('/runtime/ruleset.yaml') and NAMESPACE in name)
        data = yaml.load(archive.read(name), Loader=yaml.CSafeLoader)
    pool = data.get('parameter_declarations', {})
    for tool in data['tools'].values():
        tool['parameters'] = [deepcopy(pool[row]) if isinstance(row, str) else row
                              for row in tool['parameters']]
    original = deepcopy(data['tools']['advance_group_combat_encounter_turn']['effects'])
    profiles = deepcopy(data['definitions']['spells'])
    changed = bind_steed_control(data)
    assert data['definitions']['spells'] == profiles
    effects = data['tools']['advance_group_combat_encounter_turn']['effects']
    old_index = next(index for index, row in enumerate(original) if 'advance_initiative' in row)
    assert effects[:old_index] == original[:old_index]
    assert effects[old_index+3:] == original[old_index+1:]
    # Exercise the actual newly composed eligibility and unchanged generic initiative owner.
    data['tools']['test_native_controlled_steed_advance'] = _tool(
        'Exercise the two actual changed initiative leaves.', [],
        deepcopy(effects[old_index:old_index+3]))
    closure, pending = {}, [READ, 'spend_srd_action', 'consume_srd_bonus_action',
                           'test_native_controlled_steed_advance']
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data['tools'][name]
            pending.extend(_calls(closure[name]['effects']))
    subset = {key: value for key, value in data.items() if key not in {'tools', 'procedures'}}
    parsed = RulesetRuntime(ruleset_dict={**subset, 'tools': closure})
    return parsed, data, changed


def _controlled_case(bound, *, conditions=(), rider='caster', mounted='steed'):
    bridge, _, act = _case(bound, combat=True,
        caster_changes={MOUNT_FIELD: mounted, 'conditions': list(conditions)},
        steed_changes={RIDER_FIELD: rider, 'speed': 60})
    bridge.combat_state.update(current_actor='steed', active_actor='steed', action_available=True,
        action_surge_available=False, normal_action_spent=False, haste_action_selected=False,
        active_actor_spell_slow=False, active_actor_form_wind_only_actions=False,
        attacks_remaining=0, attack_available=True, turn_sequence=8)
    return bridge, act


@pytest.mark.parametrize('kind,magic', [('attack', False), ('other', False), ('dash', True)])
def test_controlled_disallowed_action_rejects_before_resources_or_dice(bound, kind, magic):
    bridge, act = _controlled_case(bound)
    before, rng = _snapshot(bridge), dice._rng.getstate()
    result = act('spend_srd_action', magic_action=magic, control_action_kind=kind)
    assert not result.success and 'only Dash, Disengage or Dodge' in result.error
    assert _snapshot(bridge) == before
    assert dice._rng.getstate() == rng


@pytest.mark.parametrize('kind', ['dash', 'disengage', 'dodge'])
def test_controlled_allowed_action_uses_native_budget_without_steed_charge_reset(bound, kind):
    bridge, act = _controlled_case(bound)
    rng = dice._rng.getstate()
    result = act('spend_srd_action', control_action_kind=kind)
    assert result.success, result.error
    assert bridge.combat_state['action_available'] is False
    assert bridge.combat_state['normal_action_spent'] is True
    assert bridge.combat_state['turn_sequence'] == 8
    assert bridge.get_entity('steed')[CHARGE] is True
    assert dice._rng.getstate() == rng


def test_controlled_action_limit_preserves_the_native_bonus_action_budget(bound):
    bridge, act = _controlled_case(bound)
    bridge.get_entity('steed')['available_bonus_actions'] = ['Healing Touch']
    rng = dice._rng.getstate()
    result = act('consume_srd_bonus_action', actor_state='steed', bonus_action='Healing Touch')
    assert result.success, result.error
    assert bridge.get_entity('steed')['bonus_action_available'] is False
    assert bridge.combat_state['action_available'] is True
    assert bridge.get_entity('steed')[CHARGE] is True
    assert dice._rng.getstate() == rng


@pytest.mark.parametrize('conditions,expected', [((), 'target'), (('incapacitated',), 'steed'),
                                               (('unconscious',), 'steed')])
def test_native_advance_skips_controlled_mount_and_retains_incapacitated_caster_handoff(
        bound, conditions, expected):
    bridge, act = _controlled_case(bound, conditions=conditions)
    bridge.combat_state.update(current_actor='caster', turn_active=False, turn_index=0, round=1,
        order=[{'entity_id': name, 'initiative_result': value}
               for name, value in [('caster', 15), ('steed', 15), ('target', 10)]])
    rng = dice._rng.getstate()
    result = act('test_native_controlled_steed_advance')
    assert result.success, result.error
    assert bridge.combat_state['current_actor'] == expected
    assert bridge.combat_state['round'] == 1
    assert bridge.get_entity('steed')['hp'] == 25
    assert bridge.get_entity('steed')['is_alive'] is True
    assert bridge.get_entity('steed')[ELIGIBLE_FIELD] is (expected == 'steed')
    assert dice._rng.getstate() == rng


@pytest.mark.parametrize('rider,mounted', [('target', 'steed'), ('caster', None)])
def test_ally_riding_and_nonreciprocal_mount_do_not_assume_caster_control(bound, rider, mounted):
    bridge, act = _controlled_case(bound, rider=rider, mounted=mounted)
    before = _snapshot(bridge)
    result = act(READ, actor_state='steed')
    assert result.success and result.context[CONTROLLED] is False
    assert _snapshot(bridge) == before
