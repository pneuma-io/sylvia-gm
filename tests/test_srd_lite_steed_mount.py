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
from tests.test_srd_lite_steed_healing_touch import bound as touch_bound, _case, _snapshot
from tests.test_srd_lite_mass_cure_geometry import _calls


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


@pytest.mark.parametrize('combat',(False,True))
def test_actual_mount_and_dismount_spend_current_half_speed_and_move_only_rider(bound,combat):
    bridge,act = _mount_case(bound,combat=combat)
    before = dice._rng.getstate()
    result = act(MOUNT,rider_state='caster',steed_state='steed')
    assert result.success,result.error
    assert _position(bridge,'caster') == [0,0,6]
    assert _position(bridge,'steed') == [0,0,0]
    assert bridge.get_entity('caster')[MOUNT_FIELD] == 'steed'
    assert bridge.get_entity('steed')[RIDER_FIELD] == 'caster'
    assert result.context['_ps_mount_cost'] == 15
    assert bridge.get_entity('steed')[CHARGE] is True
    assert bridge.get_entity('state').get('otherworldly_steed_movement_receipt') is None
    card = project_result_card(bound[1]['tools'][MOUNT]['metadata']['result_card'],
        parameters={},public_context=_json_safe(result.context))
    assert card is not None
    assert {row['label']:row['value'] for row in card['summary']} == {
        'Rider':'Caster','Steed':'Otherworldly Steed','Mounted':True,'Movement spent (ft)':15,'Destination':[0,0,6]}
    if os.environ.get('SRD_LITE_STEED_MOUNT_CARD'):
        Path(os.environ['SRD_LITE_STEED_MOUNT_CARD']).write_text(json.dumps(card,indent=2),encoding='utf-8')
    dismounted = act(DISMOUNT,rider_state='caster',steed_state='steed',destination=[-10,0,0])
    assert dismounted.success,dismounted.error
    assert _position(bridge,'caster') == [-10,0,0]
    assert bridge.get_entity('caster')[MOUNT_FIELD] is None
    assert bridge.get_entity('steed')[RIDER_FIELD] is None
    assert dismounted.context['_ps_mount_cost'] == 15
    assert dice._rng.getstate() == before


@pytest.mark.parametrize('changes,error',(
    ({'caster_changes':{MOUNT_FIELD:'other'}},'Dismount the actual'),
    ({'caster_changes':{'size_rank':3}},'smaller'),
    ({'steed_changes':{RIDER_FIELD:'target'}},'both be unmounted'),
    ({'caster_changes':{'conditions':['incapacitated']}},'Incapacitated'),
    ({'caster_changes':{'scene_movement_spent':30}},'enough movement')))
def test_failed_mount_intent_preserves_bodies_budget_links_and_rng(bound,changes,error):
    bridge,act = _mount_case(bound,**changes)
    if changes.get('caster_changes',{}).get('scene_movement_spent') == 30:
        # This direct runtime bridge has no action transaction; initialize native projections first.
        initialized = act('derive_srd_scene_movement_budget',actor_state='caster',mode='walk')
        assert initialized.success,initialized.error
    before,rng = _snapshot(bridge),dice._rng.getstate()
    result = act(MOUNT,rider_state='caster',steed_state='steed')
    assert not result.success and error in result.error
    assert _snapshot(bridge) == before
    assert dice._rng.getstate() == rng


def test_dismount_without_actual_support_preserves_mount_and_movement(bound):
    bridge,act = _mount_case(bound,geometry=[])
    mounted = act(MOUNT,rider_state='caster',steed_state='steed')
    assert mounted.success,mounted.error
    before,rng = _snapshot(bridge),dice._rng.getstate()
    failed = act(DISMOUNT,rider_state='caster',steed_state='steed',destination=[-10,0,0])
    assert not failed.success and 'supporting ground' in failed.error
    assert _snapshot(bridge) == before
    assert dice._rng.getstate() == rng


def test_mounted_rider_cannot_spend_own_walking_movement_until_dismounted(bound):
    bridge,act = _mount_case(bound)
    rng = dice._rng.getstate()
    mounted = act(MOUNT,rider_state='caster',steed_state='steed')
    assert mounted.success,mounted.error
    before = _snapshot(bridge)
    walking = act('test_steed_rider_walk_guard',actor_state='caster')
    assert not walking.success and 'Dismount the actual Otherworldly' in walking.error
    assert _snapshot(bridge) == before
    dismounted = act(DISMOUNT,rider_state='caster',steed_state='steed',destination=[-10,0,0])
    assert dismounted.success,dismounted.error
    assert act('test_steed_rider_walk_guard',actor_state='caster').success
    assert dice._rng.getstate() == rng


def test_unmounted_body_cannot_dismount_or_invoke_private_receiver_without_owning_receipt(bound):
    bridge,act = _mount_case(bound)
    before = _snapshot(bridge)
    failed = act(DISMOUNT,rider_state='caster',steed_state='steed',destination=[-10,0,0])
    assert not failed.success and 'reciprocal' in failed.error
    forged = act(QUALIFY,subject='caster',destination=[0,0,6],plane='material')
    assert not forged.success and 'unused source rider' in forged.error
    assert _snapshot(bridge) == before


@pytest.mark.parametrize('destination', ([0, 0, 0], [-4, 0, 0]))
def test_ground_dismount_rejects_actual_steed_body_before_payment(bound, destination):
    bridge, act = _mount_case(bound)
    mounted = act(MOUNT, rider_state='caster', steed_state='steed')
    assert mounted.success, mounted.error
    before, rng = _snapshot(bridge), dice._rng.getstate()
    result = act(DISMOUNT, rider_state='caster', steed_state='steed', destination=destination)
    assert not result.success and 'unoccupied rider destination' in result.error
    assert _snapshot(bridge) == before
    assert dice._rng.getstate() == rng
