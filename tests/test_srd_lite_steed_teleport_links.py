"""Actual Travel unlinks separated bodies; actual Fey Step retains the paired rider."""
from copy import deepcopy
from pathlib import Path
import os
import zipfile
import pytest
import yaml
from sylvia_gm import dice
from sylvia_gm.runtime import RulesetRuntime
from tests.test_srd_lite_steed_mount import _mount_case,_position
from scripts.srd_lite_steed_mount import MOUNT
from scripts.srd_lite_steed_fey_step import ACTION,RIDER_FIELD,MOUNT_FIELD
from scripts.srd_lite_steed_healing_touch import CHARGE
from scripts.srd_lite_steed_disappearance import SETTLE
from scripts.srd_lite_steed_teleport_links import bind_steed_teleport_links,FEY_MOVE


@pytest.fixture(scope='module')
def bound():
    with zipfile.ZipFile(Path(os.environ['SRD_LITE_CANDIDATE'])) as package:
        data = yaml.load(package.read('packages/sylvia.srd-lite.bg3/runtime/ruleset.yaml'),Loader=yaml.CSafeLoader)
    for tool in data['tools'].values():
        tool['parameters'] = [deepcopy(data['parameter_declarations'][p]) if isinstance(p,str) else p for p in tool['parameters']]
    original = deepcopy(data['tools']['move_srd_travel_subject'])
    changed = bind_steed_teleport_links(data)
    assert data['tools'][FEY_MOVE] == original
    assert data['tools']['move_srd_travel_subject']['effects'][:-1] == original['effects']
    return RulesetRuntime(ruleset_dict=data),data,changed


def _riding(bound,**changes):
    bridge,act = _mount_case(bound,**changes)
    result = act(MOUNT,rider_state='caster',steed_state='steed')
    assert result.success,result.error
    return bridge,act


def test_rider_teleport_clears_pair_and_preserves_native_endpoint_and_spent_movement(bound):
    bridge,act = _riding(bound)
    rng = dice._rng.getstate()
    result = act('move_srd_travel_subject',subject='caster',destination=[20,0,0],plane='material',teleport=True)
    assert result.success,result.error
    assert _position(bridge,'caster') == [20,0,0] and _position(bridge,'steed') == [0,0,0]
    assert bridge.get_entity('caster')[MOUNT_FIELD] is None and bridge.get_entity('steed')[RIDER_FIELD] is None
    assert bridge.get_entity('caster')['scene_movement_spent'] == 15
    result = act(SETTLE)
    assert result.success,result.error
    assert bridge.get_entity('state')['otherworldly_steed_released_riders'] == []
    assert not bridge.get_entity('state').get('pending_srd_action_stack')
    assert dice._rng.getstate() == rng


def test_steed_teleport_releases_actual_unsupported_rider_into_existing_fall(bound):
    bridge,act = _riding(bound)
    rng = dice._rng.getstate()
    result = act('move_srd_travel_subject',subject='steed',destination=[20,0,0],plane='material',teleport=True)
    assert result.success,result.error
    assert _position(bridge,'caster') == [0,0,6] and _position(bridge,'steed') == [20,0,0]
    assert bridge.get_entity('caster')[MOUNT_FIELD] is None and bridge.get_entity('steed')[RIDER_FIELD] is None
    result = act(SETTLE)
    assert result.success,result.error
    pending = bridge.get_entity('state')['pending_srd_action_stack'][-1]
    assert bridge.get_entity('state')['pending_srd_actions'][pending]['kind'] == 'scene_fall'
    assert bridge.get_entity('caster')['scene_fall']['status'] == 'pending'
    assert bridge.get_entity('caster')['scene_movement_spent'] == 15
    assert dice._rng.getstate() == rng


def test_same_seat_teleport_preserves_pair_without_queuing_a_fall(bound):
    bridge,act = _riding(bound)
    result = act('move_srd_travel_subject',subject='caster',destination=[0,0,6],plane='material',teleport=True)
    assert result.success,result.error
    assert bridge.get_entity('caster')[MOUNT_FIELD] == 'steed' and bridge.get_entity('steed')[RIDER_FIELD] == 'caster'
    assert not bridge.get_entity('state').get('otherworldly_steed_released_riders')


def test_actual_paired_fey_step_retains_relations_and_original_charge(bound):
    bridge,act = _riding(bound,steed_changes={'creature_type':'fey'})
    rng = dice._rng.getstate()
    result = act(ACTION,steed_state='steed',destination=[20,0,0])
    assert result.success,result.error
    assert _position(bridge,'steed') == [20,0,0] and _position(bridge,'caster') == [20,0,6]
    assert bridge.get_entity('caster')[MOUNT_FIELD] == 'steed' and bridge.get_entity('steed')[RIDER_FIELD] == 'caster'
    assert bridge.get_entity('steed')[CHARGE] is False
    assert not bridge.get_entity('state').get('otherworldly_steed_released_riders')
    assert bridge.get_entity('caster')['scene_movement_spent'] == 15
    assert dice._rng.getstate() == rng
