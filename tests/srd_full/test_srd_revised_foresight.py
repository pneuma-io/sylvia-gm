"""Original admitted SRD Foresight through complete creation and public actions.

No roll forcing, engine monkeypatch, sparse grants or post-creation source writes.
Selected65 acquisition shortages are retained separately from original proof.
"""
import json
from pathlib import Path

import pytest
from sylvia_gm.domain.authority import thaw_json_value
from tests.srd_full.srd_revised_native_fixture import native_utility_case, SRD
from tests.srd_full.srd_revised_foresight_classes import bard_factory, druid_factory, warlock_factory


def _record(case, target):
    rows = [r for r in case.lasting()['turn_effects'] if r.get('kind')=='actor_spell_modifier'
        and r.get('spell')=='foresight' and r.get('caster')=='hero' and r.get('target')==target]
    assert len(rows)==1, rows
    return rows[0]


def _retry(case, receipt, command):
    before = case.snapshot()
    retried = case.execute(command)
    assert retried.result == receipt.result
    assert case.snapshot().revision == before.revision
    assert case.snapshot().state == before.state
    assert case.snapshot().participants == before.participants


def _begin_parameters(case, **changes):
    tool = next(t for t in case.record['tools'] if t['name']=='begin_srd_spell_cast')
    permitted = set(tool['input_schema']['properties'])
    return {key:value for key,value in (case.parameters|changes).items() if key in permitted}


def _finish_foresight(case, target='hero', *, resource=None):
    resource = resource or case.parameters['slot_resource']
    beginning = _begin_parameters(case, target=target, slot_resource=resource)
    started, start_command = case.act('begin_srd_spell_cast', **beginning)
    assert not case.entities()[target].get('spell_foresight',False)
    assert case.entities()['hero']['extended_casting']['spell']=='foresight'
    _retry(case, started, start_command)
    continued, continue_command = case.act('continue_srd_spell_cast', caster_state='hero',
        elapsed_rounds=10, uninterrupted=True, verbal_component_available=True,
        somatic_component_available=True, material_component_available=True)
    _retry(case, continued, continue_command)
    assert not case.entities()[target].get('spell_foresight',False)
    finished, finish_command = case.cast(target=target, targets=[target],
        slot_resource=resource, target_distance=0 if target=='hero' else 5,
        targets_max_distance=0 if target=='hero' else 5,
        all_targets_willing=True, caster_can_touch_all_targets=True)
    assert case.entities()[target]['spell_foresight'] is True
    assert case.entities()['hero'].get('extended_casting') is None
    assert case.entities()['hero'].get('concentration_spell_id') is None
    record = _record(case,target)
    assert record['concentration'] is False and record['spell_level']==9
    assert record['expires_tick']-record['started_tick']==9600
    _retry(case,finished,finish_command)
    return finished, finish_command


def _faces(roll, mode):
    assert roll['sides']==20
    faces = list(roll['results'])
    assert len(faces)==(1 if mode=='normal' else 2)
    assert all(isinstance(face,int) and 1<=face<=20 for face in faces)
    assert roll['total']==(faces[0] if mode=='normal' else max(faces) if mode=='advantage' else min(faces))
    return faces


def _evidence(case, name, value):
    path = Path(case.path).with_suffix('.'+name+'.json')
    path.write_text(json.dumps(thaw_json_value(value),indent=2),encoding='utf-8')


@pytest.mark.parametrize('class_id,factory,grant,resource',[
    ('wizard',None,'wizard','spell_slot_9'),
    ('bard',bard_factory,'bard','spell_slot_9'),
    ('druid',druid_factory,'druid','spell_slot_9'),
    ('warlock',warlock_factory,'mystic_arcanum_9','feature'),
])
def test_complete_original_acquisition_cost_receipts_and_reopen(tmp_path,native_utility_case,class_id,factory,grant,resource):
    kwargs = {'character_factory':factory} if factory is not None else {}
    case = native_utility_case(tmp_path,'foresight-'+class_id,spell='foresight',level=17,**kwargs)
    assert case.record['id']==SRD
    assert case.acquisition['verified_saved_state'] is True
    assert case.parameters['spell_grant']==grant
    profile = thaw_json_value(case.snapshot().participants[0].data)
    assert profile['classes']==[{'id':class_id,'level':17}]
    union = {s for g in profile['spellcasting_grants'] for s in g['spells']}
    assert set(case.entities()['hero']['prepared_spells'])==union
    selected = next(g for g in profile['spellcasting_grants'] if g['source']==grant)
    assert 'foresight' in selected['spells']
    before_resources = thaw_json_value(case.entities()['hero']['resources'])
    before_materials = thaw_json_value(case.entities()['hero']['spell_materials'])
    receipt, command = _finish_foresight(case,resource=resource)
    if class_id=='warlock':
        assert case.entities()['hero']['resources']==before_resources
        assert case.entities()['hero']['spell_grant_uses']['mystic_arcanum_9']==1
    else:
        assert before_resources['spell_slot_9']==1
        assert case.entities()['hero']['resources']==before_resources|{'spell_slot_9':0}
    assert case.entities()['hero']['spell_materials']==before_materials
    # A spent ninth-level slot or actual Arcanum free-use pool cannot begin a
    # second casting. Rejection preserves the first saved source and receipt.
    case.reject('begin_srd_spell_cast',_begin_parameters(case,slot_resource=resource))
    assert case.entities()['hero']['spell_foresight'] is True
    _evidence(case,'acquisition',case.acquisition)
    _evidence(case,'cast',{'receipt':receipt.result,'record':_record(case,'hero')})
    case.reopen()
    _retry(case,receipt,command)
    assert case.entities()['hero']['spell_foresight'] is True


def test_invalid_component_source_slot_ritual_and_early_completion_do_not_spend(tmp_path,native_utility_case):
    case = native_utility_case(tmp_path,'foresight-invalid',spell='foresight',level=17)
    case.reject('cast_spell_outside_combat',case.parameters|{'targets':['guard']})
    base = _begin_parameters(case)
    for changes in (
        {'verbal_component_available':False}, {'somatic_component_available':False},
        {'material_component_available':False,'material_item_ids':[]},
        {'spell_grant':'cleric'}, {'slot_resource':'spell_slot_8'},
        {'slot_resource':'ritual','casting_mode':'ritual'},
    ):
        case.reject('begin_srd_spell_cast',base|changes)
    started, command = case.act('begin_srd_spell_cast',**base)
    _retry(case,started,command)
    case.reject('cast_spell_outside_combat',case.parameters|{'targets':['guard']})
    assert case.entities()['hero']['resources']['spell_slot_9']==1
    assert not case.entities()['guard'].get('spell_foresight',False)
    case.act('continue_srd_spell_cast',caster_state='hero',elapsed_rounds=10,
        uninterrupted=True,verbal_component_available=True,somatic_component_available=True,
        material_component_available=True)
    finishing = case.parameters|{'targets':['guard']}
    for changes in (
        {'targets':['guard','other']}, {'targets':['guard','guard']},
        {'targets':['unknown']}, {'all_targets_willing':False},
        {'caster_can_touch_all_targets':False},
    ):
        case.reject('cast_spell_outside_combat',finishing|changes)
    assert case.entities()['hero']['resources']['spell_slot_9']==1
    assert case.entities()['hero']['extended_casting']['spell']=='foresight'
    assert not case.entities()['guard'].get('spell_foresight',False)
    # A valid completion through this same pending owner must still work after
    # the deep-invalid requests. A generally broken engine cannot pass here.
    finished, finish_command = case.act('cast_spell_outside_combat',**finishing)
    assert case.entities()['hero']['resources']['spell_slot_9']==0
    assert case.entities()['guard']['spell_foresight'] is True
    assert case.entities()['hero'].get('extended_casting') is None
    record = _record(case,'guard')
    assert record['spell_level']==9 and record['concentration'] is False
    assert record['expires_tick']-record['started_tick']==9600
    _retry(case,finished,finish_command)
    case.reopen()
    _retry(case,finished,finish_command)
    assert case.entities()['guard']['spell_foresight'] is True
    _evidence(case,'invalids-then-valid-completion',finished.result)


def test_actual_checks_save_attack_and_incoming_attack_consumers(tmp_path,native_utility_case):
    case = native_utility_case(tmp_path,'foresight-consumers',spell='foresight',level=17,extra_spells=('magic_missile',))
    _finish_foresight(case)
    check, check_command = case.act('ability_check',actor='hero',actor_state='hero',
        ability='intelligence',skill='arcana',circumstantial_modifier=0,target_number=13)
    check_faces = _faces(check.result['context']['_d20_test'],'advantage')
    _retry(case,check,check_command)
    save, _ = case.act('d20_test',actor='hero',actor_state='hero',kind='saving_throw',
        ability='wisdom',proficiency_factor='normal',circumstantial_modifier=0,target_number=13)
    save_faces = _faces(save.result['context']['_d20_test'],'advantage')
    cancelled, _ = case.act('ability_check',actor='hero',actor_state='hero',
        ability='intelligence',skill='arcana',circumstantial_modifier=0,target_number=13,disadvantage=True)
    cancelled_faces = _faces(cancelled.result['context']['_d20_test'],'normal')
    # A real original-SRD spell/control remains runnable; Foresight does not
    # convert automatic force projectiles into an attack roll.
    guard_hp = case.entities()['guard']['hp']
    slots = case.entities()['hero']['resources']['spell_slot_1']
    missile, missile_command = case.cast(spell='magic_missile',spell_grant='wizard',
        slot_resource='spell_slot_1',target='guard',targets=['guard'])
    assert case.entities()['guard']['hp'] < guard_hp
    assert case.entities()['hero']['resources']['spell_slot_1']==slots-1
    assert case.entities()['hero']['spell_foresight'] is True
    _retry(case,missile,missile_command)
    case.start()
    own, _ = case.attack()
    own_faces = _faces(own.result['context']['_attack_roll'],'advantage')
    case.next_turn()
    assert case.snapshot().state[SRD+'.combat']['active_actor']=='guard'
    incoming, incoming_command = case.act('resolve_adjudicated_creature_attack',
        target='hero',attack_name='spear',attack_modifier=3,
        damage_expression='fixed-1',damage_modifier=0,damage_type='piercing',
        advantage=False,disadvantage=False,use_multiattack=False,attack_mode='melee',
        attack_distance=5,normal_range=5,long_range=5,actor_can_see_target=True,
        target_can_see_actor=True,target_is_charmer=False,fear_source_in_sight=False)
    incoming_faces = _faces(incoming.result['context']['_attack_roll'],'disadvantage')
    _retry(case,incoming,incoming_command)
    _evidence(case,'unforced-consumer-rolls',{'ability':check_faces,'save':save_faces,
        'cancellation':cancelled_faces,'own_attack':own_faces,'incoming_attack':incoming_faces})


@pytest.mark.parametrize('surprised,mode',[(False,'advantage'),(True,'normal')])
def test_2024_surprise_initiative_cancels_foresight_advantage(tmp_path,native_utility_case,surprised,mode):
    case = native_utility_case(tmp_path,'foresight-surprise-'+str(surprised),spell='foresight',level=17)
    _finish_foresight(case)
    receipt, command = case.act('start_combat_encounter_entities',first_actor='hero',
        second_actor='guard',first_surprised=surprised,second_surprised=False,tie_winner='first')
    faces = _faces(receipt.result['context']['_initiative_first_roll'],mode)
    _retry(case,receipt,command)
    _evidence(case,'initiative',{'surprised':surprised,'mode':mode,'actual_faces':faces})


def test_same_caster_recast_retires_old_target_after_public_resource_restoration(tmp_path,native_utility_case):
    case = native_utility_case(tmp_path,'foresight-recast',spell='foresight',level=17)
    _finish_foresight(case,target='guard')
    old = thaw_json_value(_record(case,'guard'))
    assert case.entities()['hero']['resources']['spell_slot_9']==0
    # Explicit GM resource-restoration setup; capacity stays the real class1.
    restored, restored_command = case.act('restore_srd_resource',target='hero',
        resource='spell_slot_9',amount=1)
    _retry(case,restored,restored_command)
    assert case.entities()['hero']['resource_maxima']['spell_slot_9']==1
    assert case.entities()['hero']['resources']['spell_slot_9']==1
    _finish_foresight(case,target='other')
    assert not case.entities()['guard'].get('spell_foresight',False)
    assert case.entities()['other']['spell_foresight'] is True
    assert not any(r.get('kind')=='actor_spell_modifier' and r.get('spell')=='foresight'
        and r.get('caster')=='hero' and r.get('target')=='guard' for r in case.lasting()['turn_effects'])
    assert _record(case,'other')['started_tick']>old['started_tick']
    assert case.entities()['hero']['resources']['spell_slot_9']==0
    case.reopen()


def test_eight_hour_expiry_removes_source_and_actual_check_advantage(tmp_path,native_utility_case):
    case = native_utility_case(tmp_path,'foresight-expiry',spell='foresight',level=17)
    _finish_foresight(case)
    record = thaw_json_value(_record(case,'hero'))
    case.act('advance_srd_lasting_time',elapsed_rounds=4799)
    assert case.entities()['hero']['spell_foresight'] is True
    assert case.lasting()['lasting_tick'] < record['expires_tick']
    case.reopen()
    expired, command = case.act('advance_srd_lasting_time',elapsed_rounds=1)
    _retry(case,expired,command)
    assert case.lasting()['lasting_tick']==record['expires_tick']
    assert not case.entities()['hero'].get('spell_foresight',False)
    assert not any(r.get('kind')=='actor_spell_modifier' and r.get('spell')=='foresight'
        for r in case.lasting()['turn_effects'])
    check, _ = case.act('ability_check',actor='hero',actor_state='hero',
        ability='intelligence',skill='arcana',circumstantial_modifier=0,target_number=13)
    _faces(check.result['context']['_d20_test'],'normal')
    assert case.entities()['hero']['resources']['spell_slot_9']==0
    case.reopen()


@pytest.mark.parametrize('recipient,mode',[('hero','advantage'),('guard','disadvantage')])
def test_actual_spell_attack_uses_caster_or_recipient_foresight(tmp_path,native_utility_case,recipient,mode):
    case = native_utility_case(tmp_path,'foresight-spell-attack-'+recipient,
        spell='foresight',level=17,extra_spells=('fire_bolt',))
    _finish_foresight(case,target=recipient)
    before_slots = thaw_json_value(case.entities()['hero']['resources'])
    receipt, command = case.cast(spell='fire_bolt',spell_grant='wizard',
        slot_resource='cantrip',target='guard',targets=['guard'],
        target_distance=5,target_can_see_caster=True,
        enemy_within_5_feet=False,close_enemy_can_see_caster=True,
        close_enemy_incapacitated=False,advantage=False,disadvantage=False)
    faces = _faces(receipt.result['context']['_spell_attack'],mode)
    _retry(case,receipt,command)
    assert case.entities()['hero']['resources']==before_slots
    assert case.entities()[recipient]['spell_foresight'] is True
    _evidence(case,'spell-attack',{'recipient':recipient,'mode':mode,'actual_faces':faces})
    case.reopen()


def test_actual_death_save_keeps_foresight_advantage_after_unconsciousness(tmp_path,native_utility_case):
    # Genuine GM-admitted injury at creation, then public spell damage to zero;
    # the complete participant/source profile remains unchanged.
    case = native_utility_case(tmp_path,'foresight-death-save',spell='foresight',
        level=17,extra_spells=('magic_missile',),entity_changes={'hero':{'hp':6}})
    _finish_foresight(case)
    hurt, hurt_command = case.cast(spell='magic_missile',spell_grant='wizard',
        slot_resource='spell_slot_1',target='hero',targets=['hero'],
        target_distance=0,targets_max_distance=0)
    _retry(case,hurt,hurt_command)
    assert case.entities()['hero']['hp']==0
    assert case.entities()['hero']['is_alive'] is True
    assert case.entities()['hero']['stable'] is False
    assert 'unconscious' in case.entities()['hero']['conditions']
    assert case.entities()['hero']['spell_foresight'] is True
    case.start()
    before = thaw_json_value(case.entities()['hero'])
    saved, command = case.act('death_saving_throw')
    faces = _faces(saved.result['context']['_death_save'],'advantage')
    _retry(case,saved,command)
    _evidence(case,'death-save',{'actual_faces':faces,'before':before,
        'after':case.entities()['hero'],'receipt':saved.result})
    case.reopen()
