"""Genuine initial/repeated lightning strikes from one retained paid source."""

import json
from tests.lite_imported_fixture import ImportedCase
from tests.lite_fixture_archive import archive_closed_case
from tests.test_srd_lite_zones import retain
from tests.test_srd_lite_foundation import VARIANT_ID
from sylvia_gm.domain.authority import thaw_json_value


def test_lightning_initial_upcast_recast_range_components_no_automatic_pulse_expiry(tmp_path):
    case=ImportedCase(tmp_path/'lightning.db','call_lightning',
        participant_changes={'classes':[{'id':'druid','level':17}], 'wizard_spellbook':[],
            'spellcasting_grants':[{'source':'druid','ability':'wisdom','spells':['call_lightning']}],
            'abilities':{'strength':16,'dexterity':14,'constitution':14,'intelligence':18,'wisdom':18,'charisma':10}},
        entity_changes={'guard':{'saving_throw_modifiers':{'dexterity':100}},
                        'other':{'saving_throw_modifiers':{'dexterity':-100}}},
        positions={'hero':[0,0,0],'guard':[20,0,0],'other':[25,0,0],'extra':[60,0,0]},
        scene_zones=[{'id':'daylight','map_id':'lite',
            'shape':{'kind':'box','min':[-100,-100,-100],'max':[100,100,100]},
            'metadata':{'plane':'material','lighting':'bright'}}])
    try:
        case.reject('cast_spell_outside_combat',case.parameters|{'environment_lightning_point':[65,0,3]})
        case.start()
        resources=dict(case.entities()['hero']['resources'])
        hp={name:case.entities()[name]['hp'] for name in ('guard','other','extra')}
        receipt,command=case.act('cast_spell',**(case.parameters|{
            'environment_lightning_point':[20,0,3],'slot_resource':'spell_slot_4',
            'material_component_available':False}))
        source=case.lasting()['persistent_spell_areas']['hero']
        assert source['spell_level']==4 and source['storm_bonus'] is False
        assert source['radius']==20/3 and source['bolt_sequence']==1
        assert source['expires_tick']-case.lasting()['lasting_tick']==20
        roll=source['bolt_damage_roll']
        assert len(roll['results'])==4 and 4 <= roll['total'] <= 40
        guard=case.entities()['guard']['last_call_lightning_save']
        other=case.entities()['other']['last_call_lightning_save']
        assert guard['outcome']=='success' and other['outcome']=='failure'
        assert guard['raw_damage']==other['raw_damage']==roll
        assert hp['guard']-case.entities()['guard']['hp']==roll['total']//2
        assert hp['other']-case.entities()['other']['hp']==roll['total']
        assert case.entities()['extra']['hp']==hp['extra']
        assert case.entities()['hero']['resources']==resources|{'spell_slot_4':resources['spell_slot_4']-1}
        assert case.snapshot().state[VARIANT_ID+'.combat']['action_available'] is False
        initial_details=receipt.result['result_card']['details']
        assert any('lightning applied' in row['value'] for row in initial_details)
        assert any('DC ' in row['value'] for row in initial_details)
        retain(case,receipt,command)
        paid=dict(case.entities()['hero']['resources'])
        health={name:case.entities()[name]['hp'] for name in hp}
        case.next_turn(); case.next_turn()
        assert {name:case.entities()[name]['hp'] for name in hp}==health
        for changes in ({'point':[65,0,3]},{'verbal_component_available':False},{'somatic_component_available':False}):
            case.reject('call_srd_lightning_source',{'caster_state':'hero','area_id':'hero','point':[60,0,3]}|changes)
        recast,recast_command=case.act('call_srd_lightning_source',caster_state='hero',area_id='hero',point=[60,0,0])
        assert case.entities()['hero']['resources']==paid
        assert case.lasting()['persistent_spell_areas']['hero']['bolt_sequence']==2
        assert case.entities()['extra']['hp'] < hp['extra']
        assert case.snapshot().state[VARIANT_ID+'.combat']['action_available'] is False
        assert recast.result['result_card']['title']=='Activate Call Lightning'
        assert any('lightning applied' in row['value'] for row in recast.result['result_card']['details'])
        retain(case,recast,recast_command)
        case.act('end_combat_encounter',end_reason='fighting_stopped')
        case.act('advance_srd_lasting_time',elapsed_rounds=10)
        assert case.entities()['hero'].get('concentration_spell_id') is None
        assert case.lasting()['persistent_spell_areas']['hero']['active'] is False
        case.reject('call_srd_lightning_source',{'caster_state':'hero','area_id':'hero','point':[20,0,3]})
        (tmp_path/'lightning-observed.json').write_text(json.dumps(thaw_json_value({
            'initial_card':receipt.result['result_card'],'recast_card':recast.result['result_card'],
            'initial_roll':roll,'initial_success_save':guard,'initial_failed_save':other,
            'final_source':case.lasting()['persistent_spell_areas']['hero']}),indent=2))
    finally:
        archive_closed_case(case)
