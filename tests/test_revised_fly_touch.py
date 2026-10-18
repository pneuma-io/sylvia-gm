"""Actual public Fly Touch geometry, parent-run against normally admitted SRD.

All geometry, planes and positions are explicit initial GM facts admitted before
CampaignCreate. No dice injection, private calls, post-cast state edits or sparse
class grants. The existing native fixture materializes the complete legal Wizard.
"""
import json
import pytest
from tests.srd_revised_native_fixture import native_utility_case, SRD, package_inputs
from sylvia_gm.domain.authority import ParticipantValidationStatus, thaw_json_value


GLASS = {'id': 'glass', 'map_id': 'utility', 'active': True,
         'movement': 'blocking', 'visibility': 'clear',
         'volume': {'kind': 'box', 'min': [2,-4,-1], 'max': [3,4,8]},
         'metadata': {'solid': True, 'plane': 'material'},
         'destroyed': {'active': False, 'movement': 'passable', 'visibility': 'clear'}}
FOG = {'id': 'fog', 'map_id': 'utility', 'active': True,
       'movement': 'passable', 'visibility': 'opaque',
       'volume': {'kind': 'box', 'min': [1,-4,-1], 'max': [4,4,8]},
       'metadata': {'plane': 'material'},
       'destroyed': {'active': False, 'movement': 'passable', 'visibility': 'clear'}}
LOW_WALL = GLASS | {'id': 'low_wall', 'visibility': 'opaque',
                    'volume': {'kind': 'box', 'min': [2,-4,-1], 'max': [3,4,2]}}


def fly_rows(case):
    return [r for r in case.lasting()['turn_effects']
            if r.get('kind') == 'actor_mobility' and r.get('spell') == 'fly']


def evidence(case, name, **facts):
    (case.path.parent/(name+'-observations.json')).write_text(
        json.dumps(thaw_json_value(facts), indent=2), encoding='utf-8')


def acquired(case, spell='fly'):
    participant = next(p for p in case.snapshot().participants if p.participant_id == 'hero')
    assert participant.validation_status is ParticipantValidationStatus.VALIDATED
    assert spell in participant.data['wizard_spellbook']
    assert spell in next(g for g in participant.data['spellcasting_grants']
                         if g['source'] == 'wizard')['spells']
    _,artifact,_ = package_inputs()
    policy = artifact['initial_state_validation']['definitions']['spell_grants']['wizard']
    grant = next(g for g in participant.data['spellcasting_grants'] if g['source'] == 'wizard')
    level = participant.data['level']
    assert len(grant['spells']) == policy['complete_cantrips'][level-1] + policy['complete_spells'][level-1]
    assert case.entities()['hero']['prepared_spells'] == grant['spells']
    assert case.acquisition['verified_saved_state'] is True


def retry_reopen(case, receipt, command):
    before = case.snapshot()
    assert case.execute(command) == receipt and case.snapshot() == before
    case.reopen()
    assert case.execute(command) == receipt and case.snapshot() == before


@pytest.mark.parametrize('obstacle', ['horizontal_distance', 'vertical_distance', 'plane', 'glass'])
def test_fly_actual_touch_rejects_forged_flags_before_cast_or_paid_action(native_utility_case,tmp_path,obstacle):
    choices = {}
    if obstacle == 'horizontal_distance':
        choices['positions'] = {'hero':[0,0,0],'guard':[20,0,0],'other':[-5,0,0],'extra':[20,10,0]}
    elif obstacle == 'vertical_distance':
        choices['positions'] = {'hero':[0,0,0],'guard':[5,0,10],'other':[-5,0,0],'extra':[20,10,0]}
    elif obstacle == 'plane':
        choices['scene_entity_changes'] = {'guard': {'metadata': {'participant_ref':'guard',
            'plane':'ethereal','lighting':'bright','creature_type':'humanoid'}}}
    else:
        choices['geometry'] = [GLASS]
    case = native_utility_case(tmp_path, 'fly-touch-'+obstacle, spell='fly', level=5, **choices)
    acquired(case)
    # Both literal claims lie about the saved scene. The scalar fallback and
    # explicit one-recipient list must each reject through the public caller.
    for selection in ({'target':'guard'}, {'target':'hero','targets':['guard']}):
        parameters = case.parameters | selection | {'target_distance':0,'targets_max_distance':0,
            'caster_can_touch_target':True,'caster_can_touch_all_targets':True,
            'caster_can_see_target':True,'all_targets_have_clear_path':True}
        case.reject('cast_spell_outside_combat', parameters)
    assert not fly_rows(case)
    case.start()
    before = case.snapshot()
    for selection in ({'target':'guard'}, {'target':'hero','targets':['guard']}):
        case.reject('begin_srd_spell_action', case.parameters | selection | {
            'target_distance':0,'targets_max_distance':0,
            'caster_can_touch_target':True,'caster_can_touch_all_targets':True})
        assert case.snapshot() == before
    assert case.snapshot().state[SRD+'.combat']['action_available'] is True
    assert not case.lasting().get('pending_srd_action_stack') and not fly_rows(case)
    # A real self recipient is reachable even when another creature is blocked.
    slots = dict(case.entities()['hero']['resources'])
    cast,command = case.act('cast_spell', **(case.parameters | {
        'target':'hero','targets':['hero'],'target_distance':0,'targets_max_distance':0}))
    assert case.entities()['hero']['resources'] == slots | {'spell_slot_3':slots['spell_slot_3']-1}
    assert case.snapshot().state[SRD+'.combat']['action_available'] is False
    assert case.entities()['hero']['bonus_action_available'] is True
    assert case.entities()['hero']['spell_flying_speed'] == 60
    retry_reopen(case,cast,command)
    evidence(case,'fly-touch-'+obstacle,acquisition=case.acquisition,source=fly_rows(case),
             initial_obstacle=obstacle,legal_self_card=cast.result['result_card'])


def test_fly_upcast_checks_every_real_recipient_atomically(native_utility_case,tmp_path):
    case = native_utility_case(tmp_path,'fly-touch-upcast',spell='fly',level=7,
        positions={'hero':[0,0,0],'guard':[5,0,0],'other':[-20,0,0],'extra':[20,10,0]})
    acquired(case)
    parameters = case.parameters | {'target':'guard','targets':['guard','other'],
        'slot_resource':'spell_slot_4','target_distance':0,'targets_max_distance':0}
    case.reject('cast_spell_outside_combat',parameters)
    assert not fly_rows(case) and case.entities()['guard'].get('spell_flying_speed',0) == 0
    case.start()
    case.reject('begin_srd_spell_action',parameters)
    # The actual explicit list is authoritative; a harmless scalar cannot hide
    # its unreachable second member. Rejecting one does not grant the other Fly.
    case.reject('begin_srd_spell_action',parameters | {'target':'hero'})
    slots = dict(case.entities()['hero']['resources'])
    begin,begin_command = case.act('begin_srd_spell_action',**(parameters | {'targets':['guard','hero']}))
    assert case.entities()['hero']['resources'] == slots and not fly_rows(case)
    assert case.snapshot().state[SRD+'.combat']['action_available'] is False
    retry_reopen(case,begin,begin_command)
    pending = case.lasting()['pending_srd_action_stack'][-1]
    cast,command = case.act('complete_srd_pending_action',pending_action_id=pending)
    assert case.entities()['hero']['resources'] == slots | {'spell_slot_4':slots['spell_slot_4']-1}
    rows = fly_rows(case)
    assert {r['target'] for r in rows} == {'guard','hero'} and len({r['cast_id'] for r in rows}) == 1
    assert all(r['spell_level'] == 4 for r in rows)
    assert case.entities()['guard']['spell_flying_speed'] == case.entities()['hero']['spell_flying_speed'] == 60
    retry_reopen(case,cast,command)
    evidence(case,'fly-touch-upcast',acquisition=case.acquisition,source=rows,card=cast.result['result_card'])


@pytest.mark.parametrize('obstacle', ['fog', 'low_wall', 'inactive_glass'])
def test_fly_physical_touch_allows_unseen_or_partly_accessible_body(native_utility_case,tmp_path,obstacle):
    geometry = {'fog':FOG,'low_wall':LOW_WALL,'inactive_glass':GLASS | {'active':False}}[obstacle]
    case = native_utility_case(tmp_path,'fly-touch-open-'+obstacle,spell='fly',level=5,geometry=[geometry])
    acquired(case)
    # No sight requirement belongs to the original Touch profile. The actual
    # solid join permits opaque passable fog and a reachable upper-body sample.
    slots = dict(case.entities()['hero']['resources'])
    cast,command = case.cast(target='guard',caster_can_see_target=False,
        caster_can_see_all_targets=False,all_targets_have_clear_path=False)
    assert case.entities()['hero']['resources'] == slots | {'spell_slot_3':slots['spell_slot_3']-1}
    assert case.entities()['guard']['spell_flying_speed'] == 60
    assert case.entities()['hero']['concentration_target_state_ids'] == ['guard']
    retry_reopen(case,cast,command)
    evidence(case,'fly-touch-open-'+obstacle,acquisition=case.acquisition,
             geometry=geometry,source=fly_rows(case),card=cast.result['result_card'])


def test_fly_touch_guard_is_noop_for_original_alter_self_owner(native_utility_case,tmp_path):
    case = native_utility_case(tmp_path,'fly-touch-alter-control',spell='alter_self',level=5,
        geometry=[GLASS],positions={'hero':[0,0,0],'guard':[20,0,0],'other':[-5,0,0],'extra':[20,10,0]})
    acquired(case,'alter_self')
    slots = dict(case.entities()['hero']['resources'])
    cast,command = case.cast(target='hero',transformation_option='aquatic',
        caster_can_touch_target=False,caster_can_touch_all_targets=False,
        caster_can_see_target=False,caster_can_see_all_targets=False)
    hero = case.entities()['hero']
    assert hero['resources'] == slots | {'spell_slot_2':slots['spell_slot_2']-1}
    assert hero['concentration_spell_id'] == 'alter_self' and hero['form_current_source_id']
    assert hero['swim_speed'] == 30 and hero['form_can_breathe_water'] is True
    assert not fly_rows(case)
    retry_reopen(case,cast,command)
    evidence(case,'fly-touch-alter-control',acquisition=case.acquisition,card=cast.result['result_card'])
