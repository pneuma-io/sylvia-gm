"""Native mount/dismount composition through real geometry, current Speed and body receivers."""
from copy import deepcopy
import os, json
from pathlib import Path
import pytest
from scripts.srd_lite_steed_mount import MOUNT, DISMOUNT, QUALIFY, RECEIPT, bind_steed_mount
from scripts.srd_scene_relations import _tool_scene
from scripts.srd_timed_actor_defenses import _entity
from scripts.srd_lite_steed_fey_step import RIDER_FIELD, MOUNT_FIELD
from scripts.srd_lite_steed_healing_touch import CHARGE
from sylvia_gm import dice
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.application.package_runtime import _json_safe
from sylvia_gm.application.result_cards import project_result_card
from tests.srd_lite_steed_healing_touch_fixtures import bound as touch_bound, _case, _snapshot
from tests.srd_lite_mass_cure_geometry_fixtures import _calls


@pytest.fixture(scope='module')
def bound(touch_bound):
    data = deepcopy(touch_bound[1])
    original = {name:deepcopy(data['tools'][name]) for name in
        ('refresh_srd_movement_budget','spend_combat_movement','derive_srd_scene_movement_budget',
         'move_srd_travel_subject','move_srd_travel_subject_node')}
    definitions = deepcopy(data['definitions'])
    record = {'tools':[]}
    changed = bind_steed_mount(data,record)
    assert data['definitions'] == definitions
    assert all(data['tools'][name] == value for name,value in original.items())
    assert [row['name'] for row in record['tools']] == [MOUNT,DISMOUNT]
    closure, pending = {}, [MOUNT,DISMOUNT,QUALIFY]
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data['tools'][name]
            pending.extend(_calls(closure[name]['effects']))
    # Exercise the added native movement admission leaf before the unchanged route machinery.
    data['tools']['test_steed_rider_walk_guard'] = _tool_scene('Test the real mounted rider movement admission.',
        [_entity('actor_state')],[deepcopy(data['tools']['move_srd_scene_creature']['effects'][0])])
    parsed = RulesetRuntime(ruleset_dict={**{key:value for key,value in data.items()
        if key not in {'tools','procedures'}},'tools':{**closure,
        'test_steed_rider_walk_guard':data['tools']['test_steed_rider_walk_guard']}})
    return parsed,data,changed


def _mount_case(bound,*,combat=False, caster_changes=None,steed_changes=None,geometry=None):
    bridge,_,act = _case(bound,combat=combat,
        caster_changes={'size_rank':2,'speed':30,'scene_movement_spent':0,'scene_movement_tick':0,MOUNT_FIELD:None,
            **(caster_changes or {})},
        steed_changes={'size_rank':3,'speed':60,'scene_movement_spent':0,RIDER_FIELD:None,
            **(steed_changes or {})})
    bridge.combat_state.update(active_actor='caster' if combat else None,movement_remaining=30,
        movement_spent=0,movement_speed=30,action_available=True,bonus_action_available=True)
    scene = deepcopy(bridge.get_entity('state')['spatial_scene'])
    scene['geometry'] = geometry if geometry is not None else [{
        'id':'floor','map_id':scene['map']['id'],'active':True,
        'volume':{'kind':'box','min':[-200,-200,-1],'max':[200,200,0]},
        'movement':'blocking','visibility':'clear','metadata':{'plane':'material','ground':True},
        'destroyed':{'active':False,'movement':'passable','visibility':'clear'}}]
    bridge.write_field('state','spatial_scene',scene)
    return bridge,act


def _position(bridge,identity):
    return next(row['position'] for row in bridge.get_entity('state')['spatial_scene']['entities']
        if row['metadata']['participant_ref'] == identity)
