"""Real public SRD-reuse effects; sparse starting fixtures do not prove acquisition.

Complete acquisition is exercised separately through the retained materializer.
Area membership here is the existing GM-projected SRD contract, not UI evidence.
"""
import json
import pytest
from sylvia_gm.domain.authority import thaw_json_value
from tests.test_srd_lite_foundation import Case, VARIANT_ID
from tests.lite_fixture_archive import archive_closed_case

ABILITIES = ('strength', 'dexterity', 'constitution', 'intelligence', 'wisdom', 'charisma')
def _mods(value): return dict.fromkeys(ABILITIES, value)

def _finish(case, receipts, output):
    saved = case.snapshot()
    for receipt, command in receipts:
        assert case.owner.execute_action(command) == receipt and case.snapshot() == saved
    case.reopen()
    for receipt, command in receipts:
        assert case.owner.execute_action(command) == receipt and case.snapshot() == saved
    output.write_text(json.dumps({'scope':'native_effects_only', 'complete_acquisition':False,
        'contexts':[r.result['context'] for r,c in receipts], 'entities':case.entities()},
        indent=2, default=thaw_json_value))

def _roll(roll, count, sides):
    assert roll['count'] == count and roll['sides'] == sides
    assert len(roll['results']) == count and all(1 <= x <= sides for x in roll['results'])
    assert roll['total'] == sum(roll['results'])

@pytest.mark.parametrize('spell,slot,count', [('acid_splash',0,4), ('lightning_bolt',5,10)])
def test_launch_area_native_scaling_save_branches_action_slot_retry(tmp_path, spell, slot, count):
    case = Case(tmp_path/(spell+'.db'), spell, positions={"hero":[0,0,0],"guard":[5,0,0],"other":[10,1.5,0],"extra":[20,10,0]}, entity_changes={
        'hero':{'saving_throw_modifiers':_mods(100), 'stable':False, 'death_successes':0, 'death_failures':0, 'stable_recovery_hours_remaining':0},
        'guard':{'saving_throw_modifiers':_mods(100)},
        'other':{'saving_throw_modifiers':_mods(-100), 'temp_hp':7}})
    receipts=[]
    try:
        p=case.parameters|{'targets':['guard','other'], 'all_targets_in_area':True,
                         'slot_resource':f'spell_slot_{slot}' if slot else 'cantrip'}
        if spell == "lightning_bolt":
            p["instant_area_height"] = 10
        else:
            p["environment_origin"] = [7.5, 0, 3]
        # Generic SRD area membership is the GM's chosen list. The root's
        # all_targets_* flags belong to other families and are not its contract.
        # Exercise genuine native component, origin and list-schema invalidity.
        for invalid in ({'verbal_component_available':False}, {'somatic_component_available':False},
                        {'targets':['guard','guard']}):
            case.reject('cast_spell_outside_combat',p|invalid)
        case.reject('cast_spell_outside_combat',p|{'origin_distance':1 if slot else 61})
        if slot:
            case.reject('cast_spell_outside_combat',p|{'slot_resource':'spell_slot_2'})
        else:
            case.reject('cast_spell_outside_combat',p|{'origin_distance':61})
        before=thaw_json_value(case.entities())
        case.start()
        receipt,command=case.act('cast_spell',**p); receipts.append((receipt,command))
        context=receipt.result['context']; _roll(context['_spell_damage'],count,6)
        total=context['_spell_damage']['total']
        assert case.entities()['guard']['hp']==before['guard']['hp']-(0 if spell=='acid_splash' else total//2)
        lost=before['other']['hp']-case.entities()['other']['hp']+7-case.entities()['other']['temp_hp']
        assert lost==total
        assert case.entities()['extra']==before['extra']
        assert case.entities()['hero']['hp']==before['hero']['hp']
        expected=before['hero']['resources'] if not slot else before['hero']['resources']|{f'spell_slot_{slot}':2}
        assert case.entities()['hero']['resources']==expected
        assert case.snapshot().state[VARIANT_ID+'.combat']['action_available'] is False
        assert case.entities()['hero'].get('concentration_spell_id')==before['hero'].get('concentration_spell_id')
        _finish(case,receipts,tmp_path/(spell+'-observed.json'))
    finally: archive_closed_case(case)

@pytest.mark.parametrize('spell,slot,count,sides,flat', [
    ('blight',5,9,8,0), ('disintegrate',7,13,6,40), ('harm',7,14,6,0)])
def test_launch_single_native_srd_save_upcast_and_harm_damage_maximum(tmp_path,spell,slot,count,sides,flat):
    participant = ({'classes':[{'id':'cleric','level':17}], 'wizard_spellbook':[],
        'spellcasting_grants':[{'source':'cleric','ability':'wisdom','spells':['harm']}]}
        if spell=='harm' else {})
    case=Case(tmp_path/(spell+'.db'),spell,participant_changes=participant,
        entity_changes={'guard':{'creature_type':'plant' if spell=='blight' else 'humanoid',
            'saving_throw_modifiers':_mods(-100)}, 'other':{'saving_throw_modifiers':_mods(100)}})
    receipts=[]
    try:
        p=case.parameters|{'slot_resource':f'spell_slot_{slot}'}
        for invalid in ({'verbal_component_available':False},{'somatic_component_available':False},
            {'target_distance':1000},{'caster_can_see_target':False},{'cover':'total'},
            {'slot_resource':'spell_slot_1'}):
            case.reject('cast_spell_outside_combat',p|invalid)
        before=thaw_json_value(case.entities())
        receipt,command=case.cast(slot_resource=f'spell_slot_{slot}'); receipts.append((receipt,command))
        c=receipt.result['context']; _roll(c['_spell_damage'],count,sides)
        assert c['_spell_save_outcome']=='failure'
        actual=c['_spell_damage']['total']+flat
        assert before['guard']['hp']-case.entities()['guard']['hp']==actual
        if spell=='blight':
            # The native expression engine exports true as numeric one.
            assert c['_spell_save_foregone'] == 1
        if spell=='harm':
            assert case.entities()['guard']['max_hp']==1000-actual
            assert case.entities()['guard']['recovery_max_hp_reduction']==actual
        else: assert case.entities()['guard']['max_hp']==1000
        case.start()
        receipt,command=case.act('cast_spell',**(p|{'target':'other'})); receipts.append((receipt,command))
        c=receipt.result['context']; assert c['_spell_save_outcome']=='success'
        if spell=='disintegrate': assert case.entities()['other']['hp']==1000
        else:
            _roll(c['_spell_damage'],count,sides)
            assert 1000-case.entities()['other']['hp']==c['_spell_damage']['total']//2
        assert case.entities()['other']['max_hp']==1000
        assert case.entities()['hero']['resources'][f'spell_slot_{slot}']==1
        assert case.snapshot().state[VARIANT_ID+'.combat']['action_available'] is False
        _finish(case,receipts,tmp_path/(spell+'-observed.json'))
        if spell=='harm':
            receipts.append(case.act('end_combat_encounter',end_reason='fighting_stopped'))
            receipts.append(case.act('begin_srd_rest',target_state='guard',kind='long',activity='sleep'))
            receipts.append(case.act('advance_srd_lasting_time',elapsed_rounds=4800))
            receipts.append(case.act('complete_srd_rest',target_state='guard'))
            assert case.entities()['guard']['max_hp']==1000 and case.entities()['guard']['recovery_max_hp_reduction']==0
            _finish(case,receipts,tmp_path/(spell+'-rest-observed.json'))
    finally: archive_closed_case(case)
