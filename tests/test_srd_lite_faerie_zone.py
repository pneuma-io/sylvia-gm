"""Initial Faerie Fire sphere membership and attached source persistence."""

import json
from tests.lite_imported_fixture import ImportedCase
from tests.lite_fixture_archive import archive_closed_case
from tests.test_srd_lite_zones import retain
from sylvia_gm.domain.authority import thaw_json_value


def marked(case):
    return [row for row in case.lasting()['turn_effects']
            if row.get('kind')=='illumination_spell' and row.get('spell')=='faerie_fire']


def test_faerie_initial_sphere_save_attached_movement_expiry_and_replay(tmp_path):
    case = ImportedCase(tmp_path/'faerie.db','faerie_fire',
        participant_changes={
            'classes':[{'id':'druid','level':17}], 'wizard_spellbook':[],
            'spellcasting_grants':[{'source':'druid','ability':'wisdom','spells':['faerie_fire']}],
            'abilities':{'strength':16,'dexterity':14,'constitution':14,
                         'intelligence':18,'wisdom':18,'charisma':10}},
        entity_changes={
            'hero':{'saving_throw_modifiers':{'dexterity':100}},
            'guard':{'saving_throw_modifiers':{'dexterity':100}},
            'other':{'saving_throw_modifiers':{'dexterity':-100}}},
        positions={'hero':[0,0,0],'guard':[0,10,0],'other':[-10,0,0],'extra':[30,0,0]})
    try:
        for change in ({'illumination_position':[65,0,3]}, {'verbal_component_available':False}):
            case.reject('cast_spell_outside_combat',case.parameters | change)
        before=dict(case.entities()['hero']['resources'])
        receipt,command=case.cast(illumination_position=[0,0,3],
                                 somatic_component_available=False,
                                 material_component_available=False)
        assert case.entities()['hero']['resources']==before|{'spell_slot_1':before['spell_slot_1']-1}
        rows=marked(case)
        assert any(row['source']=='other' for row in rows), 'The genuine negative-modifier save must fail.'
        assert not any(row['source']=='guard' for row in rows), 'The genuine positive-modifier save must succeed.'
        assert not any(row['source']=='extra' for row in rows)
        assert case.entities()['hero']['concentration_spell_id']=='faerie_fire'
        assert receipt.result['result_card']['title']=='Faerie Fire'
        retain(case,receipt,command)
        move,moved=case.act('move_srd_scene_creature',actor_state='other',destination=[-30,0,0],mode='walk')
        assert marked(case)==rows
        retain(case,move,moved)
        case.act('move_srd_scene_creature',actor_state='extra',destination=[10,0,0],mode='walk')
        assert not any(row['source']=='extra' for row in marked(case))
        case.start(second_actor='other')
        case.act('move_srd_scene_creature',actor_state='hero',destination=[-25,0,0],mode='walk')
        attack,attack_command=case.attack(target='other')
        assert len(attack.result['context']['_attack_roll']['results']) == 2
        retain(case,attack,attack_command)
        case.act('end_combat_encounter',end_reason='fighting_stopped')
        case.act('advance_srd_lasting_time',elapsed_rounds=10)
        assert marked(case)==[]
        assert case.entities()['hero'].get('concentration_spell_id') is None
        (tmp_path/'faerie-observed.json').write_text(json.dumps(thaw_json_value({
            'card':receipt.result['result_card'],'initial_attached_rows':rows}),indent=2))
    finally:
        archive_closed_case(case)
