"""Actual admitted casts and consumers, with persisted public-command replay."""

import json
from copy import deepcopy
from tests.lite_imported_fixture import ImportedCase
from tests.lite_fixture_archive import archive_closed_case
from tests.test_srd_lite_foundation import VARIANT_ID
from sylvia_gm.domain.authority import thaw_json_value


def zone_case(path, spell, initial_spell=None, **changes):
    source = "cleric" if spell == "silence" else "druid"
    spells = [initial_spell or spell]
    return ImportedCase(path, spell,
        participant_changes={
            "classes": [{"id": source, "level": 17}],
            "wizard_spellbook": [],
            "abilities": {"strength":16,"dexterity":14,"constitution":14,
                          "intelligence":18,"wisdom":18,"charisma":10},
            "spellcasting_grants":[{"source":source,"ability":"wisdom","spells":spells},
                                  {"source":"magic_initiate_wizard","ability":"intelligence",
                                   "spells":["blade_ward","true_strike"]}],
        }, entity_changes={"hero":{"prepared_spells":spells+['blade_ward','true_strike']}}, **changes)


def retain(case, receipt, command):
    saved = case.snapshot()
    assert case.execute(command) == receipt and case.snapshot() == saved
    case.reopen()
    assert case.snapshot() == saved
    assert case.execute(command) == receipt and case.snapshot() == saved


def test_silence_public_cost_components_occupancy_expiry_and_replays(tmp_path):
    case = zone_case(tmp_path/'silence.db', 'silence', initial_spell='bless', positions={
        'hero':[0,0,0], 'guard':[30,0,0], 'other':[-30,0,0], 'extra':[60,0,0]})
    try:
        case.act('begin_srd_rest',target_state='hero',kind='long',activity='sleep')
        case.act('advance_srd_lasting_time',elapsed_rounds=4800)
        case.act('complete_srd_rest',target_state='hero')
        case.act('prepare_srd_spells', caster='hero', caster_state='hero', source='cleric',
                 spells=['silence'], spellbook_available=True)
        before = dict(case.entities()['hero']['resources'])
        for changes in ({'environment_origin':[65,0,3]}, {'verbal_component_available':False},
                        {'somatic_component_available':False}):
            case.reject('cast_spell_outside_combat', case.parameters | changes)
        case.start()
        receipt, command = case.act('cast_spell', **(case.parameters | {
            'environment_origin':[30,0,3], 'slot_resource':'spell_slot_3'}))
        assert case.entities()['hero']['resources'] == before | {'spell_slot_3':before['spell_slot_3']-1}
        combat = case.snapshot().state[VARIANT_ID+'.combat']
        assert combat['action_available'] is False
        area = case.lasting()['persistent_spell_areas']['hero']
        assert area['radius'] == 20 and area['spell_level'] == 3
        assert area['expires_tick']-case.lasting()['lasting_tick'] == 200
        assert case.entities()['hero']['concentration_spell_id'] == 'silence'
        assert 'deafened' in case.entities()['guard']['conditions']
        assert 'deafened' not in case.entities()['other']['conditions']
        assert receipt.result['result_card']['title'] == 'Silence'
        retain(case, receipt, command)
        case.next_turn()
        movement, moved = case.act('move_srd_scene_creature', actor_state='guard',
                                  destination=[55,0,0], mode='walk')
        assert 'deafened' not in case.entities()['guard']['conditions']
        retain(case, movement, moved)
        case.act('end_combat_encounter',end_reason='fighting_stopped')
        case.act('advance_srd_lasting_time', elapsed_rounds=100)
        assert case.entities()['hero'].get('concentration_spell_id') is None
        assert case.lasting()['persistent_spell_areas']['hero']['active'] is False
        assert 'deafened' not in case.entities()['guard']['conditions']
        (tmp_path/'silence-observed.json').write_text(json.dumps(thaw_json_value({
            'card':receipt.result['result_card'], 'saved_area':dict(area),
            'final_lasting':dict(case.lasting())}),indent=2))
    finally:
        archive_closed_case(case)


def test_silence_real_saved_thunder_receiver_and_nonverbal_cast(tmp_path):
    case = zone_case(tmp_path/'silence-receiver.db', 'silence', positions={
        'hero':[0,0,0], 'guard':[30,0,0], 'other':[-30,0,0], 'extra':[60,0,0]})
    try:
        receipt, command = case.cast(origin=[0,0,3])
        retain(case,receipt,command)
        assert 'deafened' in case.entities()['hero']['conditions']
        case.start()
        case.reject('cast_spell',case.parameters | {'spell':'blade_ward','slot_resource':'cantrip',
                                                  'target':'hero','targets':['hero']},match='Silence')
        assert case.snapshot().state[VARIANT_ID+'.combat']['action_available'] is True
        case.next_turn()
        hits=[]
        for attempt in range(3):
            hp=case.entities()['hero']['hp']
            begin,begin_command=case.act('begin_srd_creature_attack',target='hero',
                attack_name='Declared thunder test attack',attack_mode='ranged',attack_distance=30,
                normal_range=60,long_range=60,attack_modifier=100,damage_expression='d4',
                damage_modifier=8,damage_type='thunder',advantage=False,disadvantage=False,
                actor_can_see_target=True,target_can_see_actor=True,target_is_charmer=False,
                fear_source_in_sight=False,attack_uses_weapon=False,use_multiattack=False,
                request_after_hit_smite=False)
            pending=case.lasting()['pending_srd_action_stack'][-1]
            retain(case,begin,begin_command)
            done,done_command=case.act('complete_srd_pending_action',pending_action_id=pending)
            assert case.entities()['hero']['hp']==hp
            retain(case,done,done_command)
            hits.append(dict(done.result['context']))
            if done.result['context'].get('_hit_raw_components'):
                assert done.result['context']['_damage_applied']==0
                break
            case.next_turn(); case.next_turn()
        assert hits[-1].get('_hit_raw_components'), 'No genuine hit observed; misses are retained.'
        case.next_turn()
        # Actual Somatic-only True Strike is permitted and replaces concentration normally.
        cast,cast_command=case.act('cast_spell',**(case.parameters | {
            'spell':'true_strike','slot_resource':'cantrip','target':'guard','target_distance':30}))
        assert case.entities()['hero']['concentration_spell_id']=='true_strike'
        assert 'deafened' not in case.entities()['hero']['conditions']
        retain(case,cast,cast_command)
        (tmp_path/'silence-receiver-observed.json').write_text(json.dumps(thaw_json_value(hits),indent=2))
    finally:
        archive_closed_case(case)
