"""Paid Insect Plague: actual saves, timing, movement, perception and saved retries."""

import json
from tests.lite_imported_fixture import ImportedCase
from tests.lite_fixture_archive import archive_closed_case
from tests.test_srd_lite_zones import retain
from tests.test_srd_lite_foundation import VARIANT_ID
from sylvia_gm.domain.authority import thaw_json_value


def test_insects_upcast_start_first_entry_terrain_perception_expiry_and_replay(tmp_path):
    case = ImportedCase(tmp_path/'insects.db', 'insect_plague',
        participant_changes={'classes':[{'id':'druid','level':17}], 'wizard_spellbook':[],
            'spellcasting_grants':[{'source':'druid','ability':'wisdom','spells':['insect_plague']}],
            'abilities':{'strength':16,'dexterity':14,'constitution':14,'intelligence':18,'wisdom':18,'charisma':10}},
        entity_changes={'hero':{'damage_immunities':['piercing'],
                               'saving_throw_modifiers':{'constitution':2}},
            'guard':{'saving_throw_modifiers':{'constitution':100}, 'speed':100},
            'other':{'saving_throw_modifiers':{'constitution':-100}}},
        positions={'hero':[0,0,0],'guard':[5,0,0],'other':[10,5,0],'extra':[60,0,0]})
    try:
        for changes in ({'environment_origin':[65,0,3]}, {'verbal_component_available':False},
                        {'somatic_component_available':False}):
            case.reject('cast_spell_outside_combat', case.parameters | changes)
        before = dict(case.entities()['hero']['resources'])
        health = {name:case.entities()[name]['hp'] for name in ('hero','guard','other','extra')}
        cast, command = case.cast(origin=[0,0,3], slot_resource='spell_slot_6',
                                  material_component_available=False)
        area = case.lasting()['persistent_spell_areas']['hero']
        assert area['spell']=='insect_plague' and area['spell_level']==6 and area['radius']==20
        assert area['expires_tick']-case.lasting()['lasting_tick']==20
        assert case.entities()['hero']['resources']==before|{'spell_slot_6':before['spell_slot_6']-1}
        for name, outcome, divisor in (('guard','success',2),('other','failure',1)):
            save=case.entities()[name]['last_environment_sphere_save']
            assert save['outcome']==outcome and len(save['raw_damage']['results'])==5
            assert health[name]-case.entities()[name]['hp']==save['raw_damage']['total']//divisor
        assert case.entities()['hero']['hp']==health['hero']
        assert case.entities()['extra']['hp']==health['extra']
        assert any('piercing applied' in row['value'] for row in cast.result['result_card']['details'])
        retain(case,cast,command)
        perception, pc = case.act('ability_check',actor='hero',actor_state='hero',
            ability='wisdom',skill='perception',circumstantial_modifier=0,target_number=10,
            advantage=False,disadvantage=False,requires_sight=True)
        assert len(perception.result['context']['_d20_test']['results'])==2
        retain(case,perception,pc)
        case.start()
        combat_cast, cc=case.act('cast_spell', **(case.parameters|{
            'environment_origin':[0,0,3], 'slot_resource':'spell_slot_6',
            'material_component_available':False}))
        assert case.entities()['hero']['resources']==before|{'spell_slot_6':before['spell_slot_6']-2}
        assert case.snapshot().state[VARIANT_ID+'.combat']['action_available'] is False
        assert case.lasting()['persistent_spell_areas']['hero']['sequence'] > area['sequence']
        retain(case,combat_cast,cc)
        guard_hp=case.entities()['guard']['hp']
        case.next_turn()
        assert case.entities()['guard']['hp'] < guard_hp, 'Actual target turn start must pulse.'
        guard_hp=case.entities()['guard']['hp']
        moved, mc=case.act('move_srd_scene_creature',actor_state='guard',destination=[10,0,0],mode='walk')
        assert moved.result['context']['_scene_movement_cost']==10
        assert case.entities()['guard']['hp']==guard_hp
        retain(case,moved,mc)
        case.act('move_srd_scene_creature',actor_state='guard',destination=[25,0,0],mode='walk')
        case.act('move_srd_scene_creature',actor_state='guard',destination=[15,0,0],mode='walk')
        assert case.entities()['guard']['hp']==guard_hp, 'A turn-start pulse blocks additional entry that turn.'
        case.act('move_srd_scene_creature',actor_state='guard',destination=[25,0,0],mode='walk')
        case.next_turn()
        assert case.entities()['guard']['hp']==guard_hp, 'Ending turn must not pulse.'
        case.next_turn()
        assert case.entities()['guard']['hp']==guard_hp, 'Outside start must not pulse.'
        entry, ec=case.act('move_srd_scene_creature',actor_state='guard',destination=[15,0,0],mode='walk')
        assert case.entities()['guard']['hp'] < guard_hp
        guard_hp=case.entities()['guard']['hp']
        retain(case,entry,ec)
        case.act('move_srd_scene_creature',actor_state='guard',destination=[25,0,0],mode='walk')
        case.act('move_srd_scene_creature',actor_state='guard',destination=[15,0,0],mode='walk')
        assert case.entities()['guard']['hp']==guard_hp
        case.act('end_combat_encounter',end_reason='fighting_stopped')
        case.act('advance_srd_lasting_time',elapsed_rounds=10)
        assert case.entities()['hero'].get('concentration_spell_id') is None
        assert case.lasting()['persistent_spell_areas']['hero']['active'] is False
        after, ac=case.act('ability_check',actor='hero',actor_state='hero',ability='wisdom',
            skill='perception',circumstantial_modifier=0,target_number=10,
            advantage=False,disadvantage=False,requires_sight=True)
        assert len(after.result['context']['_d20_test']['results'])==1
        retain(case,after,ac)
        (tmp_path/'insects-observed.json').write_text(json.dumps(thaw_json_value({
            'cast_card':cast.result['result_card'], 'terrain_card':moved.result['result_card'],
            'perception_context':perception.result['context'],
            'final_area':case.lasting()['persistent_spell_areas']['hero']}),indent=2))
    finally:
        archive_closed_case(case)
