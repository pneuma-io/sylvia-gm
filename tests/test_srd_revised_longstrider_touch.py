"""Unmocked public Longstrider Touch regressions; native execution remains Root-owned.

Original complete Wizard acquisition, admitted saved scene and normal public
actions. These cases are newly staged, not counted as native passes.
"""
import pytest

from sylvia_gm.domain.authority import thaw_json_value
from tests.srd_revised_native_fixture import native_utility_case, SRD


def _barrier(identity, *, movement='blocking', visibility='clear'):
    return {'id':identity, 'map_id':'utility',
        'volume':{'kind':'box','min':[2,-20,-20],'max':[3,20,20]},
        'active':True, 'movement':movement, 'occupies_cells':movement=='blocking',
        'visibility':visibility, 'metadata':{},
        'destroyed':{'active':False,'movement':'passable','visibility':'clear'}}


def _rows(case):
    return [row for row in case.lasting().get('turn_effects', [])
        if row.get('kind')=='speed_bonus' and row.get('spell')=='longstrider']


def _relation(case, target):
    receipt, _ = case.act('resolve_srd_scene_relation', actor_state='hero', target_state=target)
    return receipt.result['context']


def _replay_reopen(case, *actions):
    saved = case.snapshot()
    for receipt, command in actions:
        assert case.execute(command)==receipt and case.snapshot()==saved
    case.reopen()
    assert case.snapshot()==saved
    for receipt, command in actions:
        assert case.execute(command)==receipt and case.snapshot()==saved


@pytest.mark.parametrize('kind', ['far','plane','glass'])
def test_longstrider_actual_saved_touch_rejects_spoofed_facts_before_costs(native_utility_case, tmp_path, kind):
    options = {}
    target = 'extra' if kind=='far' else 'guard'
    if kind=='plane':
        options['scene_entity_changes']={'guard':{'metadata':{'participant_ref':'guard',
            'plane':'ethereal','lighting':'bright','creature_type':'humanoid'}}}
    if kind=='glass':
        options['geometry']=[_barrier('longstrider-clear-glass')]
    case = native_utility_case(tmp_path, 'longstrider-touch-'+kind, spell='longstrider', level=5, **options)
    assert case.acquisition['verified_saved_state'] is True
    relation = _relation(case, target)
    if kind=='far':
        assert relation['_scene_distance'] > 5
    else:
        assert relation['_scene_distance']==5
    if kind=='plane':
        assert relation['_scene_same_plane'] is False
    if kind=='glass':
        assert relation['_scene_same_plane'] is True
        assert relation['_scene_can_see'] is True and relation['_scene_line_of_sight'] is True
    parameters = case.parameters | {'target':target,'targets':[target],
        'target_distance':0,'targets_max_distance':0,
        'caster_can_touch_target':True,'caster_can_touch_all_targets':True,
        'caster_can_see_target':True,'caster_can_see_all_targets':True,'all_targets_have_clear_path':True}
    case.reject('cast_spell_outside_combat', parameters, match='Touch|touch|plane|physical|range')
    assert _rows(case)==[] and case.entities()[target]['speed']==30
    case.start()
    for tool in ('begin_srd_spell_action','cast_spell'):
        case.reject(tool, parameters, match='Touch|touch|plane|physical|range')
        assert case.snapshot().state[SRD+'.combat']['action_available'] is True
        assert case.snapshot().state[SRD+'.combat']['movement_remaining']==30
    assert _rows(case)==[]
    saved = case.snapshot()
    case.reopen()
    assert case.snapshot()==saved


@pytest.mark.parametrize('route', ['outside','pending'])
def test_longstrider_passable_opaque_fog_allows_real_touch_and_preserves_shared_costs(native_utility_case, tmp_path, route):
    case = native_utility_case(tmp_path, 'longstrider-touch-fog-'+route, spell='longstrider', level=5,
        geometry=[_barrier('longstrider-fog',movement='passable',visibility='opaque')])
    relation = _relation(case, 'guard')
    assert relation['_scene_distance']==5 and relation['_scene_same_plane'] is True
    assert relation['_scene_line_of_sight'] is False and relation['_scene_can_see'] is False
    assert case.acquisition['verified_saved_state'] is True
    parameters = case.parameters | {'targets':['guard'],'caster_can_see_target':False,
        'caster_can_see_all_targets':False}
    slots = dict(case.entities()['hero']['resources'])
    materials = thaw_json_value(case.entities()['hero']['spell_materials'])
    actions = []
    if route=='pending':
        case.start()
        begun = case.act('begin_srd_spell_action', **parameters)
        actions.append(begun)
        combat = case.snapshot().state[SRD+'.combat']
        assert combat['action_available'] is False and combat['movement_remaining']==30
        assert case.entities()['hero']['bonus_action_available'] is True
        assert case.entities()['hero']['resources']==slots and _rows(case)==[]
        pending = next(iter(case.lasting()['pending_srd_actions']))
        paid = case.act('complete_srd_pending_action',pending_action_id=pending)
    else:
        paid = case.act('cast_spell_outside_combat',**parameters)
    actions.append(paid)
    assert case.entities()['hero']['resources']==slots | {'spell_slot_1':slots['spell_slot_1']-1}
    assert case.entities()['hero']['spell_materials']==materials
    assert case.entities()['hero']['speed']==30 and case.entities()['guard']['speed']==40
    assert len(_rows(case))==1 and _rows(case)[0]['target']=='guard'
    assert _rows(case)[0]['expires_tick']==case.lasting()['lasting_tick']+1200
    assert case.entities()['hero'].get('concentration_spell_id') is None
    _replay_reopen(case, *actions)


def test_longstrider_upcast_qualifies_every_selected_recipient_and_keeps_list_precedence(native_utility_case, tmp_path):
    case = native_utility_case(tmp_path, 'longstrider-touch-upcast-all', spell='longstrider', level=5,
        positions={'hero':[0,0,0],'guard':[30,0,0],'other':[5,0,0],'extra':[-5,0,0]})
    assert case.acquisition['verified_saved_state'] is True
    assert _relation(case,'guard')['_scene_distance']==30
    parameters = case.parameters | {'target':'other','targets':['other','guard'],
        'slot_resource':'spell_slot_3','target_distance':0,'targets_max_distance':0,
        'caster_can_touch_target':True,'caster_can_touch_all_targets':True}
    case.reject('cast_spell_outside_combat',parameters,match='Touch|touch|range')
    case.start()
    for tool in ('begin_srd_spell_action','cast_spell'):
        case.reject(tool,parameters,match='Touch|touch|range')
    assert _rows(case)==[] and case.snapshot().state[SRD+'.combat']['action_available'] is True
    # The explicit list is the genuine family source. An unused far scalar must
    # not override legal self/near recipients or force a false rejection.
    paid_parameters = parameters | {'target':'guard','targets':['hero','other']}
    slots = dict(case.entities()['hero']['resources'])
    paid = case.act('cast_spell',**paid_parameters)
    assert case.entities()['hero']['resources']==slots | {'spell_slot_3':slots['spell_slot_3']-1}
    assert {row['target'] for row in _rows(case)}=={'hero','other'}
    assert len(_rows(case))==2 and {row['spell_level'] for row in _rows(case)}=={3}
    assert {row['magnitude'] for row in _rows(case)}=={10}
    assert len({row['cast_id'] for row in _rows(case)})==1
    assert case.entities()['hero']['speed']==40 and case.entities()['other']['speed']==40
    assert case.entities()['guard']['speed']==30 and case.entities()['extra']['speed']==30
    assert case.snapshot().state[SRD+'.combat']['action_available'] is False
    assert case.snapshot().state[SRD+'.combat']['movement_remaining']==40
    _replay_reopen(case, paid)
