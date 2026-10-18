"""Disappearance keeps existing drops and releases the actual mounted body through native falls."""
from copy import deepcopy
import os
from pathlib import Path
import zipfile
import pytest
import yaml

from scripts.srd_lite_steed_disappearance import RELEASE, SETTLE, ONE, bind_steed_disappearance
from scripts.srd_lite_steed_fey_step import RIDER_FIELD, MOUNT_FIELD
from scripts.srd_lite_steed_mount import MOUNT
from scripts.srd_lite_steed_turns import RIDER, STEED, BUDGET, FIELDS
from sylvia_gm import dice
from sylvia_gm.runtime import RulesetRuntime
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _calls
from tests.test_srd_lite_steed_healing_touch import _snapshot
from tests.test_srd_lite_steed_mount import _mount_case, _position


@pytest.fixture(scope='module')
def bound():
    with zipfile.ZipFile(Path(os.environ['SRD_LITE_CANDIDATE'])) as archive:
        name = next(n for n in archive.namelist() if n.endswith('/runtime/ruleset.yaml') and NAMESPACE in n)
        data = yaml.load(archive.read(name), Loader=yaml.CSafeLoader)
    pool = data.get('parameter_declarations', {})
    for tool in data['tools'].values():
        tool['parameters'] = [deepcopy(pool[p]) if isinstance(p, str) else p for p in tool['parameters']]
    prior = deepcopy(data['tools']['retire_srd_created_actor']['effects'])
    changed = bind_steed_disappearance(data)
    assert data['tools']['retire_srd_created_actor']['effects'][1:] == prior
    closure, pending = {}, [MOUNT, RELEASE, SETTLE, ONE, 'retire_srd_created_actor',
        'record_srd_actual_death', 'resolve_srd_scene_fall_event', 'settle_srd_phantom_steed_fades']
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data['tools'][name]
            pending.extend(_calls(closure[name]['effects']))
    parsed = RulesetRuntime(ruleset_dict={**{k:v for k,v in data.items() if k not in {'tools','procedures'}},
        'tools':closure})
    return parsed, data, changed


def _riding(bound):
    bridge, act = _mount_case(bound)
    result = act(MOUNT, rider_state='caster', steed_state='steed')
    assert result.success, result.error
    bridge.write_field('state', 'created_actor_ids', ['steed'])
    bridge.write_field('state', 'created_actor_records', {
        'steed': {'spell':'find_steed', 'caster':'caster', 'active':True}})
    bridge.write_field('state', 'created_actor_dropped_items', [])
    bridge.write_field('steed', 'inventory', ['saddle', 'pack'])
    bridge.write_field('steed', 'held_items', ['pack'])
    return bridge, act


@pytest.mark.parametrize('reason', ('zero_hit_points', 'replaced'))
def test_actual_disappearance_releases_pair_preserves_drops_and_opens_existing_fall(bound, reason):
    bridge, act = _riding(bound)
    before = dice._rng.getstate()
    result = act('retire_srd_created_actor', actor_state='steed', reason=reason)
    assert result.success, result.error
    assert bridge.get_entity('caster')[MOUNT_FIELD] is None
    assert bridge.get_entity('steed')[RIDER_FIELD] is None
    assert bridge.get_entity('caster')['scene_movement_spent'] == 15
    assert bridge.get_entity('steed')['created_active'] is False
    assert bridge.get_entity('caster')['created_steed_id'] is None
    assert bridge.get_entity('state')['created_actor_dropped_items'] == [
        {'item_id':item, 'position':[0,0,0], 'plane':'material', 'former_carrier':'steed'}
        for item in ('saddle','pack')]
    assert bridge.get_entity('steed')['inventory'] == []
    settled = act('settle_srd_phantom_steed_fades')
    assert settled.success, settled.error
    state = bridge.get_entity('state')
    pending = state['pending_srd_action_stack'][-1]
    assert state['pending_srd_actions'][pending]['kind'] == 'scene_fall'
    assert bridge.get_entity('caster')['scene_fall']['status'] == 'pending'
    assert _position(bridge, 'caster') == [0,0,6]
    assert dice._rng.getstate() == before
    resolved = act('resolve_srd_scene_fall_event', pending_action_id=pending)
    assert resolved.success, resolved.error
    assert _position(bridge, 'caster') == [0,0,0]
    assert bridge.get_entity('caster')['scene_fall']['status'] == 'landed'
    assert bridge.get_entity('caster')['hp'] == 10
    assert dice._rng.getstate() == before


def test_existing_caster_death_owner_retires_steed_and_settles_actual_dead_rider(bound):
    bridge, act = _riding(bound)
    bridge.write_field('caster', 'hp', 0)
    bridge.write_field('caster', 'is_alive', False)
    before = dice._rng.getstate()
    died = act('record_srd_actual_death', target_state='caster', lasting_state='state')
    assert died.success, died.error
    assert bridge.get_entity('steed')['created_disappearance'] == 'caster_died'
    assert bridge.get_entity('caster')[MOUNT_FIELD] is None
    result = act('settle_srd_phantom_steed_fades')
    assert result.success, result.error
    assert _position(bridge, 'caster') == [0,0,0]
    assert bridge.get_entity('caster')['hp'] == 0 and bridge.get_entity('caster')['is_alive'] is False
    assert not bridge.get_entity('state').get('pending_srd_action_stack')
    assert dice._rng.getstate() == before


def test_disappearance_defers_fall_until_the_actual_pending_action_completes(bound):
    bridge, act = _riding(bound)
    bridge.write_field('state', 'pending_srd_action_stack', ['original-action'])
    result = act('retire_srd_created_actor', actor_state='steed', reason='zero_hit_points')
    assert result.success, result.error
    result = act('settle_srd_phantom_steed_fades')
    assert result.success, result.error
    assert bridge.get_entity('state')['pending_srd_action_stack'] == ['original-action']
    assert bridge.get_entity('caster').get('scene_fall') is None
    bridge.write_field('state', 'pending_srd_action_stack', [])
    result = act('settle_srd_phantom_steed_fades')
    assert result.success, result.error
    assert bridge.get_entity('caster')['scene_fall']['status'] == 'pending'


def test_retirement_restores_actual_rider_budget_without_resetting_actions(bound):
    bridge, act = _riding(bound)
    # Native Bonus Action availability belongs to the actor, rather than its phase snapshot.
    bridge.write_field('caster', 'bonus_action_available', False)
    saved = {field:None for field in FIELDS}
    saved.update(active_actor='caster', current_actor='caster', turn_active=True,
        movement_remaining=15, movement_spent=15, movement_speed=30,
        action_available=False, bonus_action_used_this_turn=True)
    bridge.write_field('caster', BUDGET, saved)
    bridge.combat_state.update(encounter_active=True, turn_active=True, active_actor='steed',
        current_actor='steed', otherworldly_steed_phase_rider='caster', otherworldly_steed_phase_steed='steed')
    result = act('retire_srd_created_actor', actor_state='steed', reason='zero_hit_points')
    assert result.success, result.error
    result = act('settle_srd_phantom_steed_fades')
    assert result.success, result.error
    assert bridge.combat_state['active_actor'] == 'caster'
    assert bridge.combat_state['movement_remaining'] == 15
    assert bridge.combat_state['action_available'] is False
    assert bridge.get_entity('caster')['bonus_action_available'] is False
    assert bridge.combat_state['bonus_action_used_this_turn'] is True
    assert bridge.combat_state[RIDER.removeprefix('combat.')] is None
    assert bridge.combat_state[STEED.removeprefix('combat.')] is None


def test_wrong_pair_cannot_release_the_actual_rider_or_move_bodies(bound):
    bridge, act = _riding(bound)
    snapshot, rng = _snapshot(bridge), dice._rng.getstate()
    result = act(RELEASE, steed_state='steed', rider_state='target')
    assert not result.success and 'reciprocal rider' in result.error
    assert _snapshot(bridge) == snapshot and dice._rng.getstate() == rng


def test_inactive_retirement_does_not_duplicate_drops_or_release_queue(bound):
    bridge, act = _riding(bound)
    result = act('retire_srd_created_actor', actor_state='steed', reason='replaced')
    assert result.success, result.error
    snapshot, rng = _snapshot(bridge), dice._rng.getstate()
    result = act('retire_srd_created_actor', actor_state='steed', reason='replaced')
    assert result.success, result.error
    assert _snapshot(bridge) == snapshot and dice._rng.getstate() == rng
