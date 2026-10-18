"""Migration changes only equipment additions and preserves the old save."""
from copy import deepcopy
import pytest
from scripts.migrate_srd_lite_weapon_sets import migrate_state,PACKAGE_ID

WEAPONS={'dagger':{'kind':'melee','properties':['light','thrown']},
         'greatsword':{'kind':'melee','properties':['two_handed']},
         'shortbow':{'kind':'ranged','properties':['two_handed']}}

@pytest.mark.parametrize('held,expected',[([],None),(['dagger'],'melee'),(['greatsword'],'melee'),(['shortbow'],'ranged'),(['dagger','greatsword'],None)])
def test_known_actual_loadout_or_unselected_without_inventory_guess(held,expected):
    actor={'creature_kind':'character','inventory':['dagger','greatsword','shortbow'],'held_items':held,
           'resources':{'spell_slot_1':2},'ammunition':{'arrow':17},'hp':12,'armor_class':16,
           'light_extra_attack_used':True,'light_attack_source_weapons':['dagger']}
    state={PACKAGE_ID+'.entities':{'hero':actor},PACKAGE_ID+'.state':{'quest_flags':{'gate_open':True},'act':2},'campaign.story':{'events':['saved']}}
    before=deepcopy(state);after=migrate_state(state,WEAPONS)
    assert state==before
    migrated=after[PACKAGE_ID+'.entities']['hero'];assert migrated['weapon_sets']['active_set']==expected
    assert {k:v for k,v in migrated.items() if k not in ['weapon_sets','light_attack_source_items']}==actor
    assert after[PACKAGE_ID+'.state']==before[PACKAGE_ID+'.state'] and after['campaign.story']==before['campaign.story']
    assert migrate_state(after,WEAPONS)==after

def test_existing_selected_sets_are_never_rewritten():
    actor={'creature_kind':'character','weapon_sets':{'format':1,'active_set':'ranged','melee':{'main_hand':'dagger','off_hand':None},'ranged':{'main_hand':'shortbow','off_hand':'shortbow'}}}
    state={PACKAGE_ID+'.entities':{'hero':actor}}
    assert migrate_state(state,WEAPONS)==state

def test_pending_action_blocks_migration_without_mutation():
    state={PACKAGE_ID+'.state':{'pending_srd_action_stack':['pending']},PACKAGE_ID+'.entities':{}}
    before=deepcopy(state)
    with pytest.raises(ValueError,match='pending'):migrate_state(state,WEAPONS)
    assert state==before
