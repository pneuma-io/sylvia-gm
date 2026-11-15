"""Complete earned Wizard17 Foresight through real longer casting and Scene.

No injected grant, forced die, mocked spatial query or post-cast state edit.
"""
import pytest
from sylvia_gm.domain.authority import thaw_json_value
from tests.srd_full.srd_revised_native_fixture import native_utility_case,SRD
from tests.srd_full.test_srd_revised_foresight import _begin_parameters,_record,_retry,_evidence


def _barrier(kind):
    row={'id':kind,'map_id':'utility','volume':{'kind':'box',
        'min':[2,-20,-20],'max':[3,20,20]},'active':True,'movement':'blocking',
        'occupies_cells':True,'visibility':'opaque','metadata':{},
        'destroyed':{'active':False,'movement':'passable','visibility':'clear'}}
    if kind=='fog':row['movement']='passable'
    if kind in ('glass','partial_glass','different_plane_glass'):row['visibility']='clear'
    if kind=='partial_glass':row['volume']={'kind':'box','min':[2,-20,2.9],'max':[3,20,3.1]}
    if kind=='different_plane_glass':row['metadata']={'plane':'ethereal'}
    if kind=='floor':row['volume']={'kind':'box','min':[-20,-20,-1],'max':[20,20,0]}
    return row


def _ready(case):
    beginning=_begin_parameters(case)
    begun,command=case.act('begin_srd_spell_cast',**beginning)
    _retry(case,begun,command)
    continued,command=case.act('continue_srd_spell_cast',caster_state='hero',elapsed_rounds=10,
        uninterrupted=True,verbal_component_available=True,somatic_component_available=True,
        material_component_available=True)
    _retry(case,continued,command)
    assert case.entities()['hero']['resources']['spell_slot_9']==1
    assert case.entities()['hero']['extended_casting']['spell']=='foresight'
    return case.parameters|{'target':'guard','targets':['guard'],'target_distance':0,
        'targets_max_distance':0,'caster_can_touch_target':True,'caster_can_touch_all_targets':True,
        'all_targets_have_clear_path':True,'target_willing':True,'all_targets_willing':True}


@pytest.mark.parametrize('kind',['far','vertical_far','plane','solid','glass'])
def test_foresight_real_completion_geometry_rejects_spoofed_flags_without_spend(tmp_path,native_utility_case,kind):
    positions={'hero':[0,0,0],'guard':[5,0,0],'other':[-5,0,0],'extra':[20,10,0]}
    kwargs={}
    if kind=='far':positions['guard']=[10,0,0]
    if kind=='vertical_far':positions['guard']=[0,0,10]
    if kind=='plane':kwargs['scene_entity_changes']={'guard':{'metadata':{
        'participant_ref':'guard','plane':'ethereal','lighting':'bright','creature_type':'humanoid'}}}
    if kind in ('solid','glass'):kwargs['geometry']=[_barrier(kind)]
    case=native_utility_case(tmp_path,'foresight-touch-'+kind,spell='foresight',level=17,
        positions=positions,**kwargs)
    assert case.acquisition['verified_saved_state'] is True
    request=_ready(case)
    resources=thaw_json_value(case.entities()['hero']['resources'])
    materials=thaw_json_value(case.entities()['hero']['spell_materials'])
    pending=thaw_json_value(case.entities()['hero']['extended_casting'])
    case.reject('cast_spell_outside_combat',request)
    assert case.entities()['hero']['resources']==resources
    assert case.entities()['hero']['spell_materials']==materials
    assert case.entities()['hero']['extended_casting']==pending
    assert not case.entities()['guard'].get('spell_foresight',False)
    # Real same-source self-target completion is still legal after the invalid
    # physical recipient; it distinguishes a guard from an always-failed owner.
    finished,command=case.act('cast_spell_outside_combat',**(request|{'target':'guard','targets':['hero']}))
    assert case.entities()['hero']['resources']==resources|{'spell_slot_9':0}
    assert case.entities()['hero']['spell_foresight'] is True
    assert not case.entities()['guard'].get('spell_foresight',False)
    assert case.entities()['hero'].get('extended_casting') is None
    assert _record(case,'hero')['concentration'] is False
    case.reopen();_retry(case,finished,command)
    _evidence(case,'physical-touch-rejection',{'kind':kind,'invalid_request':request,'valid_self_completion':finished.result})


@pytest.mark.parametrize('kind',['clear','fog','partial_glass','floor','different_plane_glass','darkness'])
def test_foresight_physically_clear_touch_accepts_without_visibility_and_preserves_payment(tmp_path,native_utility_case,kind):
    kwargs={}
    if kind not in ('clear','darkness'):kwargs['geometry']=[_barrier(kind)]
    if kind=='darkness':kwargs['lighting']='darkness'
    case=native_utility_case(tmp_path,'foresight-touch-allowed-'+kind,spell='foresight',level=17,**kwargs)
    assert case.acquisition['verified_saved_state'] is True
    request=_ready(case)|{'caster_can_see_target':False,'caster_can_see_all_targets':False}
    resources=thaw_json_value(case.entities()['hero']['resources'])
    materials=thaw_json_value(case.entities()['hero']['spell_materials'])
    finished,command=case.act('cast_spell_outside_combat',**request)
    assert case.entities()['hero']['resources']==resources|{'spell_slot_9':0}
    assert case.entities()['hero']['spell_materials']==materials
    assert case.entities()['guard']['spell_foresight'] is True
    assert case.entities()['hero'].get('extended_casting') is None
    assert case.entities()['hero'].get('concentration_spell_id') is None
    record=_record(case,'guard')
    assert record['spell_level']==9 and record['concentration'] is False
    assert record['expires_tick']-record['started_tick']==9600
    case.reopen();_retry(case,finished,command)
    _evidence(case,'physical-touch-accepted',{'kind':kind,'original_source':record,'actual_paid_completion':finished.result})
