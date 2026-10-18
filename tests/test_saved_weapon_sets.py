"""Prepared equipment uses the same admitted package operations as gameplay."""
from copy import deepcopy
import pytest
from tests.test_srd_action_budget import SRD,_run
from tests.test_srd_attack_budget import _combat_case
from sylvia_gm.domain.errors import RulesetPreconditionRejected

def fixture():
    state,data=_combat_case()
    state[f'{SRD}.combat'].update(encounter_active=False,turn_active=False)
    state[f'{SRD}.entities']['hero'].update(inventory=['dagger','scimitar','shortbow','greatsword'],worn_shield=None)
    return state,data

def prepare(state,set_name,main,off=''):
    return _run('prepare_srd_weapon_set',state,actor_state='hero',loadout=[{'set_name':set_name,'main_hand':main,'off_hand':off}])

def test_prepare_and_switch_preserve_slots_and_all_action_resources():
    state,data=fixture();prepare(state,'melee','dagger','scimitar');prepare(state,'ranged','shortbow')
    hero=state[f'{SRD}.entities']['hero'];assert hero['weapon_sets']['active_set']=='melee'
    assert hero['weapon_sets']['ranged']=={'main_hand':'shortbow','off_hand':'shortbow'}
    assert hero['held_items']==['dagger','scimitar']
    state[f'{SRD}.combat'].update(encounter_active=True,turn_active=True,active_actor='hero',action_available=False,attacks_remaining=0,light_attack_followup_available=False)
    before=deepcopy(state[f'{SRD}.combat']);hero['light_extra_attack_used']=True;resources=deepcopy(hero['resources'])
    _run('activate_srd_weapon_set',state,actor_state='hero',set_name='ranged')
    assert state[f'{SRD}.entities']['hero']['held_items']==['shortbow']
    assert state[f'{SRD}.combat']==before
    assert state[f'{SRD}.entities']['hero']['resources']==resources
    assert state[f'{SRD}.entities']['hero']['light_extra_attack_used'] is True

@pytest.mark.parametrize('main,off',[('longsword',''),('greatsword','scimitar'),('dagger','dagger')])
def test_invalid_pair_preserves_every_saved_value(main,off):
    state,data=fixture();before=deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):prepare(state,'melee',main,off)
    assert state==before

def test_two_carried_one_handed_copies_require_and_use_real_quantity():
    state,data=fixture();state[f'{SRD}.entities']['hero']['inventory_quantities']={'dagger':2}
    prepare(state,'melee','dagger','dagger')
    assert state[f'{SRD}.entities']['hero']['weapon_sets']['melee']=={'main_hand':'dagger','off_hand':'dagger'}
    assert state[f'{SRD}.entities']['hero']['inventory_quantities']=={'dagger':2}

def test_combat_inventory_replacement_spends_one_action_and_cannot_repeat():
    state,data=fixture();prepare(state,'melee','dagger','scimitar')
    state[f'{SRD}.combat'].update(encounter_active=True,turn_active=True,active_actor='hero',action_available=True,normal_action_spent=False)
    prepare(state,'melee','greatsword')
    assert state[f'{SRD}.combat']['action_available'] is False
    assert state[f'{SRD}.combat']['normal_action_spent'] is True
    before=deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):prepare(state,'melee','dagger','scimitar')
    assert state==before

def test_unknown_inventory_does_not_silently_select_a_weapon():
    state,data=fixture();_run('initialize_srd_weapon_sets',state,actor_state='hero')
    assert state[f'{SRD}.entities']['hero']['weapon_sets']['active_set'] is None
    assert state[f'{SRD}.entities']['hero']['weapon_sets']['melee']['main_hand'] is None

def test_known_actual_single_held_weapon_survives_initialization():
    state,data=fixture();state[f'{SRD}.entities']['hero']['held_items']=['greatsword']
    _run('initialize_srd_weapon_sets',state,actor_state='hero')
    assert state[f'{SRD}.entities']['hero']['weapon_sets']['melee']=={'main_hand':'greatsword','off_hand':'greatsword'}
    assert state[f'{SRD}.entities']['hero']['weapon_sets']['active_set']=='melee'


def ready_attack(main='dagger',off='scimitar',set_name='melee'):
    state,data=fixture();prepare(state,set_name,main,off)
    state[f'{SRD}.combat'].update(encounter_active=True,turn_active=True,active_actor='hero')
    return state,data

def test_plain_attack_defaults_to_saved_main_without_model_grip_flags():
    from tests.test_srd_attack_budget import _attack
    state,data=ready_attack('greatsword','')
    result=_attack(state,data,weapon=None,wielding_two_handed=False)
    assert result.result['status']=='committed'
    assert result.result['context']['_equipped_weapon']=='greatsword'
    assert result.result['context']['_equipped_two_handed'] is True
    assert state[f'{SRD}.combat']['attacks_remaining']==1

def test_requested_carried_weapon_outside_active_set_is_atomic_rejection():
    from tests.test_srd_attack_budget import _attack
    state,data=ready_attack();before=deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):_attack(state,data,weapon='greatsword')
    assert state==before

def test_unselected_loadout_cannot_be_silently_supplied_by_gm_weapon():
    from tests.test_srd_attack_budget import _attack
    state,data=fixture();_run('initialize_srd_weapon_sets',state,actor_state='hero')
    state[f'{SRD}.combat'].update(encounter_active=True,turn_active=True,active_actor='hero')
    before=deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):_attack(state,data,weapon='dagger')
    assert state==before

def test_saved_one_hand_ignores_false_model_two_hand_assertion():
    from tests.test_srd_attack_budget import _attack
    state,data=ready_attack()
    result=_attack(state,data,weapon='dagger',wielding_two_handed=True)
    assert result.result['status']=='committed'
    assert result.result['context']['_equipped_two_handed'] is False

def test_saved_ranged_two_hands_preserves_worn_shield_ac_and_spends_ammunition():
    from tests.test_srd_attack_budget import _attack
    state,data=fixture();hero=state[f'{SRD}.entities']['hero']
    hero.update(worn_shield={'inventory_item':'shield','armor_type':'shield','magic_bonus':0},armor_class=19,ammunition={'arrow':2})
    hero['inventory'].append('shield');prepare(state,'ranged','shortbow')
    state[f'{SRD}.combat'].update(encounter_active=True,turn_active=True,active_actor='hero')
    result=_attack(state,data,weapon='shortbow',attack_mode='ranged',attack_ability='dexterity',wielding_two_handed=False)
    assert result.result['status']=='committed'
    hero=state[f'{SRD}.entities']['hero'];assert hero['armor_class']==19
    assert hero['worn_shield']['inventory_item']=='shield'
    assert hero['ammunition']['arrow']==1


def test_same_profile_second_copy_supports_one_nick_without_reset_on_switch():
    from tests.test_srd_attack_budget import _attack
    state,data=fixture();state[f'{SRD}.entities']['hero']['inventory_quantities']={'dagger':2}
    prepare(state,'melee','dagger','dagger');prepare(state,'ranged','shortbow')
    state[f'{SRD}.combat'].update(encounter_active=True,turn_active=True,active_actor='hero')
    assert _attack(state,data,weapon='dagger').result['status']=='committed'
    assert state[f'{SRD}.entities']['hero']['light_attack_source_items']==['dagger:1']
    assert _attack(state,data,weapon='dagger',attack_sequence='light_nick').result['status']=='committed'
    hero=state[f'{SRD}.entities']['hero'];assert hero['bonus_action_available'] is True
    assert hero['light_extra_attack_used'] is True
    _run('activate_srd_weapon_set',state,actor_state='hero',set_name='ranged')
    _run('activate_srd_weapon_set',state,actor_state='hero',set_name='melee')
    before=deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):_attack(state,data,weapon='dagger',attack_sequence='light_nick')
    assert state==before

def test_light_followup_uses_actual_prepared_off_hand_and_rejects_other_inventory():
    from tests.test_srd_attack_budget import _attack
    state,data=ready_attack()
    assert _attack(state,data,weapon='dagger').result['status']=='committed'
    before=deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):_attack(state,data,weapon='dagger',attack_sequence='light_nick')
    assert state==before
    assert _attack(state,data,weapon='scimitar',attack_sequence='light_nick').result['status']=='committed'

def test_pending_attack_defaults_to_saved_two_hand_weapon_before_roll():
    state,data=ready_attack('greatsword','')
    result=_run('begin_srd_character_attack',state,participant_data=data,actor='hero',actor_state='hero',target='guard',weapon=None,
        attack_sequence='primary',use_graze_on_miss=False,attack_mode='melee',attack_ability='strength',wielding_two_handed=False,
        mounted=False,circumstantial_modifier=0,advantage=False,disadvantage=False,actor_can_see_target=True,
        target_can_see_actor=True,target_is_charmer=False,fear_source_in_sight=False,attack_distance=5)
    assert result.result['status']=='committed'
    assert result.result['context']['_equipped_weapon']=='greatsword'
    assert result.result['context']['_equipped_two_handed'] is True
    assert state[f'{SRD}.state']['pending_srd_action_stack']



def shield(state,data,item):
    return _run('change_srd_equipment',state,participant_data=data,actor='hero',actor_state='hero',slot='shield',
        item=[] if item is None else [{'inventory_item':item,'armor_type':'shield','magic_bonus':0}])

def test_shield_operations_share_melee_slot_and_existing_ac_authority():
    state,data=fixture();state[f'{SRD}.entities']['hero']['inventory'].append('shield')
    prepare(state,'melee','dagger');shield(state,data,'shield')
    hero=state[f'{SRD}.entities']['hero'];assert hero['weapon_sets']['melee']['off_hand']=='shield'
    assert hero['worn_shield']['inventory_item']=='shield'
    assert hero['armor_class']==14
    prepare(state,'ranged','shortbow');_run('activate_srd_weapon_set',state,actor_state='hero',set_name='ranged')
    assert state[f'{SRD}.entities']['hero']['armor_class']==14
    shield(state,data,None);hero=state[f'{SRD}.entities']['hero']
    assert hero['weapon_sets']['melee']['off_hand'] is None
    assert hero['worn_shield'] is None and hero['armor_class']==12

@pytest.mark.parametrize('main,off',[('greatsword',''),('dagger','scimitar')])
def test_equipping_shield_cannot_replace_prepared_weapon_or_two_hand_set(main,off):
    state,data=fixture();state[f'{SRD}.entities']['hero']['inventory'].append('shield')
    prepare(state,'melee',main,off);before=deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):shield(state,data,'shield')
    assert state==before

def test_actual_inventory_loss_preserves_known_off_hand_and_other_saved_set():
    state,data=fixture();prepare(state,'melee','dagger','scimitar');prepare(state,'ranged','shortbow')
    before=deepcopy(state[f'{SRD}.combat'])
    _run('set_srd_carried_inventory',state,actor_state='hero',inventory=['scimitar','shortbow','greatsword'])
    hero=state[f'{SRD}.entities']['hero'];assert hero['weapon_sets']['active_set']=='melee'
    assert hero['held_items']==['scimitar']
    assert hero['weapon_sets']['melee']=={'main_hand':None,'off_hand':'scimitar'}
    assert hero['weapon_sets']['ranged']=={'main_hand':'shortbow','off_hand':'shortbow'}
    assert state[f'{SRD}.combat']==before
    unchanged=deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run('derive_srd_equipped_weapon',state,actor_state='hero',requested_weapon=None,attack_sequence='primary')
    assert state==unchanged

def test_forced_held_weapon_drop_invalidates_both_prepared_references():
    state,data=fixture();prepare(state,'melee','dagger','scimitar')
    _run('drop_srd_control_held_items',state,target_state='hero')
    hero=state[f'{SRD}.entities']['hero'];assert hero['weapon_sets']['active_set'] is None
    assert hero['weapon_sets']['melee']=={'main_hand':None,'off_hand':None}
    assert 'dagger' not in hero['inventory'] and 'scimitar' not in hero['inventory']


def test_shield_ui_one_actor_binding_targets_that_actor_without_model_derivation():
    state,data=fixture();state[f'{SRD}.entities']['hero']['inventory'].append('shield');prepare(state,'melee','dagger')
    out=_run('change_srd_character_shield',state,participant_data=data,actor='hero',
        item=[{'inventory_item':'shield','armor_type':'shield','magic_bonus':0}])
    assert out.result['status']=='committed'
    assert state[f'{SRD}.entities']['hero']['weapon_sets']['melee']['off_hand']=='shield'

def test_bookkeeping_can_record_actual_second_copy_and_cannot_invent_uncarried_count():
    state,data=fixture()
    _run('update_srd_carried_inventory',state,updates=[{'participant_id':'hero','fields':{'inventory':state[f'{SRD}.entities']['hero']['inventory'],
        'inventory_quantities':[{'inventory_item':'dagger','quantity':2}]}}])
    assert state[f'{SRD}.entities']['hero']['inventory_quantities']=={'dagger':2}
    prepare(state,'melee','dagger','dagger');before=deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run('update_srd_carried_inventory',state,updates=[{'participant_id':'hero','fields':{'inventory':['dagger'],
            'inventory_quantities':[{'inventory_item':'longsword','quantity':2}]}}])
    assert state==before


def test_thrown_single_weapon_leaves_inventory_and_clears_saved_slots():
    from tests.test_srd_attack_budget import _attack
    state,data=ready_attack('dagger','')
    out=_attack(state,data,weapon='dagger',attack_mode='thrown')
    assert out.result['status']=='committed'
    hero=state[f'{SRD}.entities']['hero'];assert 'dagger' not in hero['inventory']
    assert hero['weapon_sets']['melee']['main_hand'] is None
    assert hero['weapon_sets']['active_set'] is None
    assert hero['dropped_quantities']['dagger']==1

def test_thrown_main_copy_preserves_second_copy_and_does_not_restore_budgets():
    from tests.test_srd_attack_budget import _attack
    state,data=fixture();state[f'{SRD}.entities']['hero']['inventory_quantities']={'dagger':2};prepare(state,'melee','dagger','dagger')
    state[f'{SRD}.combat'].update(encounter_active=True,turn_active=True,active_actor='hero')
    out=_attack(state,data,weapon='dagger',attack_mode='thrown');assert out.result['status']=='committed'
    hero=state[f'{SRD}.entities']['hero'];assert hero['inventory_quantities']['dagger']==1
    assert 'dagger' in hero['inventory']
    assert hero['weapon_sets']['melee']=={'main_hand':None,'off_hand':'dagger'}
    assert state[f'{SRD}.combat']['attacks_remaining']==1


def test_pending_throw_reserves_one_projectile_and_completion_does_not_repeat_loss():
    from tests.test_srd_reaction_windows import _parameters
    state,data=ready_attack('dagger','')
    out=_run('begin_srd_character_attack',state,participant_data=data,**(_parameters()|{'weapon':'dagger','attack_mode':'thrown'}))
    assert out.result['status']=='committed'
    event_id=out.result['context']['_pending_action_id']
    hero=state[f'{SRD}.entities']['hero'];assert 'dagger' not in hero['inventory']
    assert hero['dropped_quantities']['dagger']==1
    before_hp=state[f'{SRD}.entities']['guard']['hp']
    done=_run('complete_srd_pending_action',state,participant_data=data,pending_action_id=event_id)
    assert done.result['status']=='committed'
    assert state[f'{SRD}.entities']['hero']['dropped_quantities']['dagger']==1
    assert state[f'{SRD}.entities']['guard']['hp'] <= before_hp
    assert state[f'{SRD}.state']['pending_srd_action_stack']==[]


def actual_hands(state,capacity=2):
    _run('record_srd_control_held_items',state,target_state='hero',held_items=[],held_attack_names=[])
    _run('record_srd_scene_hand_occupancy',state,actor_state='hero',hand_capacity=capacity,holdings=[])

def test_known_anatomical_hands_follow_prepared_grips_without_new_model_facts():
    state,data=fixture();actual_hands(state);prepare(state,'melee','greatsword')
    body=next(x for x in state[f'{SRD}.state']['spatial_scene']['entities'] if x['id']=='hero')
    hands=body['metadata']['srd_hand_occupancy'];assert hands['capacity']==2
    assert hands['holdings']==[{'inventory_item':'greatsword','hand_indices':[0,1]}]

def test_insufficient_actual_hand_capacity_rejects_pair_atomically():
    state,data=fixture();actual_hands(state,1);before=deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):prepare(state,'melee','greatsword')
    assert state==before

def test_passive_ranged_shield_preserves_ac_without_inventing_third_hand():
    state,data=fixture();actual_hands(state);hero=state[f'{SRD}.entities']['hero'];hero['inventory'].append('shield')
    prepare(state,'melee','dagger');shield(state,data,'shield');prepare(state,'ranged','shortbow')
    _run('activate_srd_weapon_set',state,actor_state='hero',set_name='ranged')
    body=next(x for x in state[f'{SRD}.state']['spatial_scene']['entities'] if x['id']=='hero');hands=body['metadata']['srd_hand_occupancy']
    assert hands['capacity']==2 and hands['worn_shield_inventory_item'] is None
    assert hands['holdings']==[{'inventory_item':'shortbow','hand_indices':[0,1]}]
    assert state[f'{SRD}.entities']['hero']['armor_class']==14


def test_reaction_weapon_uses_saved_main_grip_and_preserves_all_turn_resources():
    state,data=ready_attack('greatsword','');before=deepcopy(state)
    out=_run('derive_srd_reaction_weapon',state,participant_data=data,actor='hero',actor_state='hero',weapon='',
        attack_distance=5,wielding_two_handed=False)
    assert out.result['status']=='committed' and state==before
    assert out.result['context']['_equipped_weapon']=='greatsword'
    assert out.result['context']['_reaction_damage_dice_count']==2
    assert out.result['context']['_reaction_damage_die_size']=='6'

def test_reaction_cannot_substitute_carried_melee_weapon_while_ranged_set_is_active():
    state,data=ready_attack('shortbow','',set_name='ranged');before=deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run('derive_srd_reaction_weapon',state,participant_data=data,actor='hero',actor_state='hero',weapon='dagger',attack_distance=5)
    assert state==before


def weapon_spell_case(spell):
    from tests.test_srd_spell_result_cards import _spell_case
    state,data,params=_spell_case(spell);hero=state[f'{SRD}.entities']['hero']
    hero['inventory'] += ['quarterstaff','shortbow'];hero['worn_shield']=None
    state[f'{SRD}.combat'].update(encounter_active=False,turn_active=False,active_actor=None)
    prepare(state,'melee','quarterstaff');prepare(state,'ranged','shortbow')
    for supply in hero.get('spell_materials',[]):
        supply['inventory_item']='quarterstaff'
    # Prepare committed mutations produce new authority values; use the live
    # owner value when establishing this synthetic actual material supply.
    state[f'{SRD}.entities']['hero']['spell_materials']=hero.get('spell_materials',[])
    params.update(target='hero',target_distance=0,actor_spell_weapon='quarterstaff')
    return state,data,params

def test_switching_away_retires_actual_held_shillelagh_without_resource_reset():
    state,data,params=weapon_spell_case('shillelagh')
    out=_run('cast_spell_outside_combat',state,participant_data=data,**params);assert out.result['status']=='committed'
    before=_run('read_srd_actor_weapon_spell',state,actor_state='hero',lasting_state='state',weapon='quarterstaff',weapon_copy_serial=1).result['context']
    assert before['_weapon_spell_die']=='2d6'
    assert before['_weapon_spell_ability']=='wisdom' and before['_weapon_spell_modifier']==4
    from tests.test_srd_attack_budget import _attack
    state[f'{SRD}.combat'].update(encounter_active=True,turn_active=True,active_actor='hero',action_available=True)
    hit=_attack(state,data,weapon='quarterstaff')
    assert hit.result['context']['_damage_roll']['count']==2
    assert hit.result['context']['_damage_roll']['sides']==6
    assert hit.result['context']['_ability_modifier']==4
    resources=deepcopy(state[f'{SRD}.entities']['hero']['resources'])
    _run('activate_srd_weapon_set',state,actor_state='hero',set_name='ranged')
    after=_run('read_srd_actor_weapon_spell',state,actor_state='hero',lasting_state='state',weapon='quarterstaff',weapon_copy_serial=1).result['context']
    assert after['_weapon_spell_die'] is None and after['_weapon_spell_ability'] is None
    assert state[f'{SRD}.entities']['hero']['resources']==resources


def test_shillelagh_on_main_copy_does_not_enhance_second_club(monkeypatch):
    from tests.test_srd_spell_result_cards import _spell_case
    from tests.test_srd_attack_budget import _attack
    state,data,params=_spell_case('shillelagh');hero=state[f'{SRD}.entities']['hero']
    hero['inventory'].append('club');hero['inventory_quantities']={'club':2};hero['worn_shield']=None
    state[f'{SRD}.combat'].update(encounter_active=False,turn_active=False,active_actor=None)
    prepare(state,'melee','club','club');params.update(target='hero',target_distance=0,actor_spell_weapon='club',actor_spell_weapon_hand='main')
    assert _run('cast_spell_outside_combat',state,participant_data=data,**params).result['status']=='committed'
    state[f'{SRD}.combat'].update(encounter_active=True,turn_active=True,active_actor='hero',action_available=True)
    # Check damage isolation on ordinary hits; a miss has no damage dice.
    from random import Random
    from sylvia_gm import dice
    monkeypatch.setattr(dice, '_rng', Random(0))
    monkeypatch.setattr(dice, 'set_seed', lambda _seed: None)
    main=_attack(state,data,weapon='club');assert main.result['context']['_damage_roll']['count']==2
    off=_attack(state,data,weapon='club',attack_sequence='light_bonus')
    assert off.result['context']['_damage_roll']['count']==1
    assert off.result['context']['_damage_roll']['sides']==4
    assert off.result['context']['_ability_modifier']==3


def test_two_identical_actual_weapons_occupy_two_anatomical_hands():
    state,data=fixture();actual_hands(state);state[f'{SRD}.entities']['hero']['inventory_quantities']={'dagger':2}
    prepare(state,'melee','dagger','dagger')
    body=next(x for x in state[f'{SRD}.state']['spatial_scene']['entities'] if x['id']=='hero')
    assert body['metadata']['srd_hand_occupancy']['holdings']==[{'inventory_item':'dagger','hand_indices':[0,1]}]
    assert state[f'{SRD}.entities']['hero']['weapon_copy_refs']['melee']=={'main_hand':1,'off_hand':2}


def test_partial_inventory_loss_needs_actual_serial_and_preserves_other_copy():
    state,data=fixture();state[f'{SRD}.entities']['hero']['inventory_quantities']={'dagger':3};prepare(state,'melee','dagger','dagger')
    before=deepcopy(state)
    update={'participant_id':'hero','fields':{'inventory':['dagger'],'inventory_quantities':[{'inventory_item':'dagger','quantity':2}]}}
    with pytest.raises(RulesetPreconditionRejected):_run('update_srd_carried_inventory',state,updates=[update])
    assert state==before
    update['fields']['inventory_quantities'][0]['removed_copies']=[1]
    _run('update_srd_carried_inventory',state,updates=[update])
    hero=state[f'{SRD}.entities']['hero']
    assert hero['inventory_quantities']['dagger']==2
    assert hero['weapon_sets']['melee']=={'main_hand':None,'off_hand':'dagger'}
    assert hero['weapon_copy_refs']['melee']=={'main_hand':None,'off_hand':2}
    assert hero['inventory_weapon_copies']['dagger']=={'minimum':2,'maximum':3,'removed':[]}


def test_forced_drop_keeps_unheld_copy_and_reacquisition_never_reuses_serial():
    state,data=fixture();state[f'{SRD}.entities']['hero']['inventory_quantities']={'dagger':3};prepare(state,'melee','dagger','dagger')
    _run('drop_srd_control_held_items',state,participant_data=data,target_state='hero')
    hero=state[f'{SRD}.entities']['hero']
    assert hero['inventory_quantities']['dagger']==1 and 'dagger' in hero['inventory']
    assert hero['dropped_quantities']['dagger']==2
    assert hero['inventory_weapon_copies']['dagger']=={'minimum':3,'maximum':3,'removed':[]}
    assert hero['weapon_sets']['active_set'] is None
    _run('set_srd_carried_inventory',state,actor_state='hero',inventory=[])
    _run('set_srd_carried_inventory',state,actor_state='hero',inventory=['dagger'])
    prepare(state,'melee','dagger')
    assert state[f'{SRD}.entities']['hero']['weapon_copy_refs']['melee']['main_hand']==4


def test_shillelagh_ambiguous_identical_copies_reject_before_any_spend():
    from tests.test_srd_spell_result_cards import _spell_case
    state,data,params=_spell_case('shillelagh');hero=state[f'{SRD}.entities']['hero']
    hero['inventory'].append('club');hero['inventory_quantities']={'club':2};hero['worn_shield']=None
    state[f'{SRD}.combat'].update(encounter_active=False,turn_active=False,active_actor=None)
    prepare(state,'melee','club','club');params.update(target='hero',target_distance=0,actor_spell_weapon='club')
    before=deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected,match='main or off-hand'):
        _run('cast_spell_outside_combat',state,participant_data=data,**params)
    assert state==before


def test_authored_active_starting_set_needs_no_extra_equip_for_shillelagh():
    state,data,params=weapon_spell_case('shillelagh')
    # New Character Builder state declares selected slots, not a separate
    # redundant held-items cache. The authoritative selection is sufficient.
    hero=state[f'{SRD}.entities']['hero'];hero.pop('held_items',None)
    out=_run('cast_spell_outside_combat',state,participant_data=data,**params)
    assert out.result['status']=='committed'
    assert state[f'{SRD}.entities']['hero']['held_items']==['quarterstaff']


def test_unequipping_active_pair_marks_unselected_and_spends_only_one_action():
    state,data=ready_attack('dagger','scimitar')
    resources=deepcopy(state[f'{SRD}.entities']['hero']['resources'])
    prepare(state,'melee','','')
    hero=state[f'{SRD}.entities']['hero']
    assert hero['weapon_sets']['melee']=={'main_hand':None,'off_hand':None}
    assert hero['weapon_sets']['active_set'] is None
    assert hero['held_items']==[]
    assert hero['resources']==resources
    assert state[f'{SRD}.combat']['action_available'] is False


def test_removing_main_copy_keeps_known_enhanced_off_copy_in_its_actual_hand():
    from tests.test_srd_spell_result_cards import _spell_case
    state,data,params=_spell_case('shillelagh');hero=state[f'{SRD}.entities']['hero']
    hero['inventory'].append('club');hero['inventory_quantities']={'club':2};hero['worn_shield']=None
    state[f'{SRD}.combat'].update(encounter_active=False,turn_active=False,active_actor=None)
    prepare(state,'melee','club','club');params.update(target='hero',target_distance=0,actor_spell_weapon='club',actor_spell_weapon_hand='off')
    _run('cast_spell_outside_combat',state,participant_data=data,**params)
    inventory=state[f'{SRD}.entities']['hero']['inventory']
    _run('update_srd_carried_inventory',state,updates=[{'participant_id':'hero','fields':{'inventory':inventory,'inventory_quantities':[{'inventory_item':'club','quantity':1,'removed_copies':[1]}]}}])
    hero=state[f'{SRD}.entities']['hero']
    assert hero['weapon_sets']['melee']=={'main_hand':None,'off_hand':'club'}
    assert hero['weapon_copy_refs']['melee']=={'main_hand':None,'off_hand':2}
    assert hero['held_items']==['club']
    read=_run('read_srd_actor_weapon_spell',state,actor_state='hero',lasting_state='state',weapon='club',weapon_copy_serial=2).result['context']
    assert read['_weapon_spell_die']=='2d6' and read['_weapon_spell_modifier']==4
