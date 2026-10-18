"""Declared map references and measured previews cannot spoof mechanical identities."""
from copy import deepcopy
from math import dist
import pytest
from tests.test_tactical_grid_ui import _scene
from sylvia_gm.application.tactical_grid import project_coordinate_map, resolve_tactical_action_parameters, TacticalGridError
from sylvia_gm.application.map_segment_preview import segment_preview

def fixture():
    scene=_scene()
    scene['entities'][0]['id']='hero-body'
    scene['entities'][0]['metadata']['participant_ref']='pc'
    target=deepcopy(scene['entities'][0]);target.update(id='target-body',position=[3.5,2.5,0])
    target['metadata']['participant_ref']='npc';target['metadata']['player']['label']='Visible target'
    scene['entities'].append(target)
    action={'id':'attack','label':'Attack','intent':'actual_attack','params':{},
            'bindings':{'actor':'actor','actor_state':'actor','target':'target'},
            'actor_reference':'participant_ref','target_reference':'participant_ref','spatial_preview':'segment'}
    return scene,action,{'map_revision':7,'actor':'hero-body','target':'target-body'}

def test_distinct_body_references_bind_to_saved_actor_and_target():
    scene,action,selection=fixture()
    projection=project_coordinate_map({'scene':scene},actor_reference='participant_ref')
    assert resolve_tactical_action_parameters(action,selection,projection)=={'actor':'pc','actor_state':'pc','target':'npc'}
    with pytest.raises(TacticalGridError): resolve_tactical_action_parameters(action,{**selection,'target':'npc'},projection)

def test_ambiguous_target_reference_rejects_instead_of_selecting_one():
    scene,action,selection=fixture()
    duplicate=deepcopy(scene['entities'][-1]);duplicate.update(id='duplicate-body',position=[4.5,2.5,0])
    scene['entities'].append(duplicate)
    projection=project_coordinate_map({'scene':scene},actor_reference='participant_ref')
    with pytest.raises(TacticalGridError): resolve_tactical_action_parameters(action,selection,projection)

def test_segment_preview_is_exact_safe_and_read_only():
    scene,action,selection=fixture();before=deepcopy(scene)
    preview=segment_preview({'renderer':'coordinate_map','action':action},{'scene':scene},selection)
    assert preview['map_preview']['ray']['origin']==[1.5,2.5,0]
    assert preview['map_preview']['ray']['destination']==[3.5,2.5,0]
    assert preview['preview']['summary'][0]['value']=='2.0 m'
    assert scene==before
    assert 'gm_secret' not in str(preview)
