"""A monster cannot auto-finish while its real attack's reaction window is open."""
from random import Random
from sylvia_gm import dice
from sylvia_gm.domain.authority import ModelToolBehavior
from tests.test_srd_lite_foundation import Case


def test_monster_auto_finish_waits_for_pending_attack_and_survives_reopen(tmp_path, monkeypatch):
    case=Case(tmp_path/'pending.db','fire_bolt', participant_changes={'party_role':'player_character'})
    try:
        case.admit_scene_encounter()
        case.activate_guard_turn_for_movement()
        monkeypatch.setattr(dice,'_rng',Random(5))
        monkeypatch.setattr(dice,'set_seed',lambda _seed:None)
        receipt,_=case.act('begin_srd_creature_attack',target='hero',attack_name='Practice strike',
            attack_modifier=3,damage_expression='d4',damage_modifier=1,damage_type='bludgeoning',
            attack_distance=5,attack_mode='melee',normal_range=5,long_range=5,
            actor_can_see_target=True,target_can_see_actor=True,target_is_charmer=False,
            fear_source_in_sight=False,advantage=False,disadvantage=False)
        pending=receipt.result['context']['_pending_action_id']
        assert case.lasting()['pending_srd_action_stack']==[pending]
        case.reopen()
        finish=next(t for t in case.owner.catalog('lite') if t.name=='finish_srd_monster_turn')
        assert finish.model_behavior is not ModelToolBehavior.REQUIRED_ENGINE_TRANSITION
        hp=case.entities()['hero']['hp']
        case.act('complete_srd_pending_action',pending_action_id=pending)
        assert case.entities()['hero']['hp']<hp
        assert case.lasting()['pending_srd_action_stack']==[]
        finish=next(t for t in case.owner.catalog('lite') if t.name=='finish_srd_monster_turn')
        assert finish.model_behavior is ModelToolBehavior.REQUIRED_ENGINE_TRANSITION
        case.act('finish_srd_monster_turn')
        case.reopen()
    finally:
        case.close()
