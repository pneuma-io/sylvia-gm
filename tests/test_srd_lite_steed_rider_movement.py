"""Mounted pairs use native paid movement and the authenticated physical rider receiver."""
from copy import deepcopy
import os
from pathlib import Path
import zipfile

import pytest
import yaml

from scripts.srd_lite_steed_fey_step import RIDER_FIELD, MOUNT_FIELD
from scripts.srd_lite_steed_healing_touch import CHARGE
from scripts.srd_lite_steed_mount import MOUNT
from scripts.srd_lite_steed_rider_movement import bind_steed_rider_movement, PREPARE, FOLLOW, CHECK
from sylvia_gm import dice
from sylvia_gm.application.package_runtime import _json_safe
from sylvia_gm.application.result_cards import project_result_card
from sylvia_gm.runtime import RulesetRuntime
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _calls
from tests.test_srd_lite_steed_healing_touch import _snapshot
from tests.test_srd_lite_steed_mount import _mount_case, _position

MOVE = 'move_srd_scene_creature'


@pytest.fixture(scope='module')
def bound():
    """Extend the admitted package once, retaining all genuine movement dependencies."""
    with zipfile.ZipFile(Path(os.environ['SRD_LITE_CANDIDATE'])) as archive:
        name = next(n for n in archive.namelist() if n.endswith('/runtime/ruleset.yaml') and NAMESPACE in n)
        data = yaml.load(archive.read(name), Loader=yaml.CSafeLoader)
    pool = data.get('parameter_declarations', {})
    for tool in data['tools'].values():
        tool['parameters'] = [deepcopy(pool[p]) if isinstance(p, str) else p for p in tool['parameters']]
    changed = bind_steed_rider_movement(data)
    closure, pending = {}, [MOUNT, MOVE, PREPARE, FOLLOW, CHECK]
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data['tools'][name]
            pending.extend(_calls(closure[name]['effects']))
    parsed = RulesetRuntime(ruleset_dict={**{key: value for key, value in data.items()
        if key not in {'tools', 'procedures'}}, 'tools': closure})
    return parsed, data, changed


def _riding(bound, *, geometry=None, flying=False):
    """Mount the actual rider through the existing paid operation before a movement case."""
    changes = {'created_spell_level': 4, 'hp': 45, 'max_hp': 45, 'flying_speed': 60} if flying else {}
    bridge, act = _mount_case(bound, geometry=geometry, steed_changes=changes)
    mounted = act(MOUNT, rider_state='caster', steed_state='steed')
    assert mounted.success, mounted.error
    return bridge, act


@pytest.mark.parametrize('destination,mode', (([0, 10, 0], 'walk'), ([0, 0, 10], 'fly')))
def test_actual_paid_steed_movement_carries_rider_without_rider_payment(bound, destination, mode):
    bridge, act = _riding(bound, flying=mode == 'fly')
    before_rng = dice._rng.getstate()
    result = act(MOVE, actor_state='steed', destination=destination, mode=mode, lasting_state='state')
    assert result.success, result.error
    assert _position(bridge, 'steed') == destination
    assert _position(bridge, 'caster') == [destination[0], destination[1], destination[2]+6]
    assert bridge.get_entity('steed')['scene_movement_spent'] == 10
    assert bridge.get_entity('caster')['scene_movement_spent'] == 15
    assert bridge.get_entity('caster')[MOUNT_FIELD] == 'steed'
    assert bridge.get_entity('steed')[RIDER_FIELD] == 'caster'
    assert bridge.get_entity('steed')[CHARGE] is True
    assert bridge.get_entity('state').get('otherworldly_steed_movement_receipt') is None
    assert bridge.get_entity('state')['lasting_tick'] == 0
    assert dice._rng.getstate() == before_rng
    card = project_result_card(bound[1]['tools'][MOVE]['metadata']['result_card'],
        parameters={}, public_context=_json_safe(result.context))
    rows = {row['label']: row['value'] for row in card['summary']}
    assert rows['Creature'] == 'Otherworldly Steed'
    assert rows['Movement spent'] == 10
    assert rows['Rider'] == 'Caster'
    assert rows['Rider position'] == [destination[0], destination[1], destination[2]+6]


def test_rider_ceiling_collision_rejects_before_native_carrier_payment(bound):
    floor = {'id': 'floor', 'map_id': 'mass-cure-fixture', 'active': True,
        'volume': {'kind': 'box', 'min': [-200, -200, -1], 'max': [200, 200, 0]},
        'movement': 'blocking', 'visibility': 'clear', 'metadata': {'plane': 'material', 'ground': True},
        'destroyed': {'active': False, 'movement': 'passable', 'visibility': 'clear'}}
    ceiling = {**deepcopy(floor), 'id': 'low-ceiling',
        'volume': {'kind': 'box', 'min': [-10, 4, 8], 'max': [10, 6, 9]},
        'metadata': {'plane': 'material'}}
    bridge, act = _riding(bound, geometry=[floor, ceiling])
    before, rng = _snapshot(bridge), dice._rng.getstate()
    result = act(MOVE, actor_state='steed', destination=[0, 10, 0], mode='walk', lasting_state='state')
    assert not result.success and 'blocks the actual rider' in result.error
    assert _snapshot(bridge) == before
    assert dice._rng.getstate() == rng


def test_carrier_occupied_endpoint_rejects_before_moving_either_body(bound):
    bridge, act = _riding(bound)
    before, rng = _snapshot(bridge), dice._rng.getstate()
    result = act(MOVE, actor_state='steed', destination=[10, 0, 0], mode='walk', lasting_state='state')
    assert not result.success and 'unoccupied Steed destination' in result.error
    assert _snapshot(bridge) == before
    assert dice._rng.getstate() == rng


def test_displaced_rider_cannot_be_snapped_onto_moving_steed(bound):
    bridge, act = _riding(bound)
    scene = deepcopy(bridge.get_entity('state')['spatial_scene'])
    next(row for row in scene['entities'] if row['metadata']['participant_ref'] == 'caster')['position'] = [1, 0, 6]
    scene['map']['revision'] += 1
    bridge.write_field('state', 'spatial_scene', scene)
    before, rng = _snapshot(bridge), dice._rng.getstate()
    result = act(MOVE, actor_state='steed', destination=[0, 10, 0], mode='walk', lasting_state='state')
    assert not result.success and 'pre-move riding position' in result.error
    assert _snapshot(bridge) == before
    assert dice._rng.getstate() == rng


def test_ordinary_unmounted_movement_retains_native_payment_and_card(bound):
    bridge, act = _mount_case(bound)
    before_rng = dice._rng.getstate()
    result = act(MOVE, actor_state='caster', destination=[-10, 10, 0], mode='walk', lasting_state='state')
    assert result.success, result.error
    assert _position(bridge, 'caster') == [-10, 10, 0]
    assert _position(bridge, 'steed') == [0, 0, 0]
    assert bridge.get_entity('caster')['scene_movement_spent'] == 10
    card = project_result_card(bound[1]['tools'][MOVE]['metadata']['result_card'],
        parameters={}, public_context=_json_safe(result.context))
    assert any(row['label'] == 'Creature' and row['value'] == 'Caster' for row in card['summary'])
    assert all(row['label'] not in {'Rider', 'Rider position'} for row in card['summary'])
    assert dice._rng.getstate() == before_rng


def test_independent_steed_can_carry_its_actual_living_unconscious_rider(bound):
    bridge, act = _riding(bound)
    bridge.write_field('caster', 'hp', 0)
    bridge.write_field('caster', 'conditions', ['unconscious', 'incapacitated'])
    rng = dice._rng.getstate()
    result = act(MOVE, actor_state='steed', destination=[0, 10, 0], mode='walk', lasting_state='state')
    assert result.success, result.error
    assert _position(bridge, 'caster') == [0, 10, 6]
    assert bridge.get_entity('caster')['hp'] == 0
    assert bridge.get_entity('caster')['scene_movement_spent'] == 15
    assert bridge.get_entity('steed')['scene_movement_spent'] == 10
    assert bridge.get_entity('caster')[MOUNT_FIELD] == 'steed'
    assert dice._rng.getstate() == rng
