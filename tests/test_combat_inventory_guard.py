"""Reject provider-selected catalogue weapons absent from explicit carried inventory."""
import pytest
from tests.test_srd_lite_foundation import Case
from sylvia_gm.domain.errors import MechanicsRejected
from sylvia_gm.domain.authority import ActionStatus

def attack(weapon):
    return dict(actor='hero', target='guard', attack_sequence='primary', use_graze_on_miss=False,
        weapon=weapon, attack_mode='melee', attack_ability='strength', wielding_two_handed=True,
        mounted=False, circumstantial_modifier=0, advantage=False, disadvantage=False,
        actor_can_see_target=True, target_can_see_actor=True, target_is_charmer=False,
        fear_source_in_sight=False, attack_distance=5)

@pytest.mark.parametrize('attack_stage', ['primary', 'extra_attack'])
def test_uncarried_weapon_rejection_preserves_action_damage_and_reopen(tmp_path, attack_stage):
    case=Case(tmp_path/'inventory.db', 'fire_bolt', entity_changes={'hero': {'inventory':['greatsword'], 'fighter_level':5}}, participant_changes={'classes':[{'id':'fighter','level':5}], 'level':5})
    try:
        case.start()
        if attack_stage == 'extra_attack':
            case.execute(case.command('resolve_combat_attack', attack('greatsword')))
            assert case.snapshot().state[case.record['id']+'.combat']['attacks_remaining'] == 1
        before=case.snapshot()
        with pytest.raises(MechanicsRejected, match='actual carried inventory'):
            case.execute(case.command('resolve_combat_attack', attack('longsword')))
        after=case.snapshot()
        assert (after.revision,after.state,after.participants)==(before.revision,before.state,before.participants)
        case.reopen()
        assert case.snapshot().state==before.state
        receipt=case.execute(case.command('resolve_combat_attack', attack('greatsword')))
        assert receipt.status is ActionStatus.COMMITTED
        assert case.snapshot().state[case.record['id']+'.combat']['action_available'] is False
        case.reopen()
    finally:
        case.close()




def test_light_weapon_followup_rejects_uncarried_then_commits_carried_weapon(tmp_path):
    case = Case(tmp_path/'light-followup.db', 'fire_bolt',
        entity_changes={'hero': {'inventory':['dagger','scimitar'], 'fighter_level':5}},
        participant_changes={'classes':[{'id':'fighter','level':5}], 'level':5})
    try:
        case.act('record_srd_control_held_items', target_state='hero', held_items=['dagger','scimitar'], held_attack_names=[])
        case.act('record_srd_scene_hand_occupancy', actor_state='hero', hand_capacity=2,
                 holdings=[{'inventory_item':'dagger','hand_indices':[0]}, {'inventory_item':'scimitar','hand_indices':[1]}])
        case.start()
        first = attack('dagger') | {'wielding_two_handed':False, 'other_wielded_weapons':['scimitar']}
        case.execute(case.command('resolve_combat_attack',first))
        assert case.snapshot().state[case.record['id']+'.combat']['light_attack_followup_available'] is True
        before=case.snapshot()
        bad=attack('handaxe') | {'attack_sequence':'light_bonus', 'wielding_two_handed':False}
        with pytest.raises(MechanicsRejected, match='actual carried inventory'):
            case.execute(case.command('resolve_combat_attack_followup',bad))
        after=case.snapshot()
        assert (after.revision,after.state,after.participants)==(before.revision,before.state,before.participants)
        case.reopen()
        valid=attack('scimitar') | {'attack_sequence':'light_bonus','wielding_two_handed':False}
        receipt=case.execute(case.command('resolve_combat_attack_followup',valid))
        assert receipt.status is ActionStatus.COMMITTED
        assert case.snapshot().state[case.record['id']+'.entities']['hero']['bonus_action_available'] is False
        case.reopen()
    finally:
        case.close()

