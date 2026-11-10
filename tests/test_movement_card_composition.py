"""Cloud of Daggers retains its actual rows when Guardian shares the movement card."""
from tests.test_native_followups import make_case,save_card
from tests.test_srd_lite_foundation import CLOUD_POSITIONS

def test_daggers_movement_card_survives_guardian_rows(make_case,tmp_path):
    case=make_case('cloud_of_daggers',positions=CLOUD_POSITIONS | {'guard':[20,0,0]},entity_changes={'guard':{'speed':100}})
    case.cast(environment_origin=[0,0,3])
    case.start()
    case.next_turn()
    before=case.entities()['guard']['hp']
    receipt,_=case.act('move_srd_scene_creature',actor_state='guard',destination=[0,0,0],mode='walk')
    damage=before-case.entities()['guard']['hp']
    card=save_card(tmp_path,'daggers-movement',receipt)
    assert damage>0
    assert any(row['label']=='guard' and row['value']==f'Cloud of Daggers: {damage} slashing' for row in card['summary'])
    assert any(row['label']=='guard' and '4d4' in row['value'] for row in card['details'])
    assert not any(row['label']=='Guardian radiant damage' for row in card['summary'])
