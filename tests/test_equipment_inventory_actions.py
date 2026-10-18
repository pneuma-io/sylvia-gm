"""Declared inventory controls retain the ordinary owner and actor checks."""
import pytest
from sylvia_gm.application.player_actions import resolve_inventory_action_parameters,validate_player_control
from sylvia_gm.application.tactical_grid import resolve_surface_source
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import CommandRejected

def action():
    return owned_payload({'id':'equip','intent':'prepare_srd_weapon_set','params':{},
        'bindings':{'actor_state':'actor','loadout':'declared_action'}})

def test_form_parameters_bind_only_the_visible_controlled_inventory_owner():
    form=[{'set_name':'melee','main_hand':'dagger','off_hand':''}]
    params=resolve_inventory_action_parameters(action(),owned_payload({'actor':'hero','declared_action':form}),
        {'participants':[{'id':'hero'}]})
    assert params['actor_state']=='hero' and list(params['loadout'])[0]['main_hand']=='dagger'
    validate_player_control(owned_payload({'available':True,'actors':[{'id':'hero'}]}),params,'actor_state')
    with pytest.raises(CommandRejected,match='not currently controlled'):
        validate_player_control(owned_payload({'available':True,'actors':[{'id':'other'}]}),params,'actor_state')

@pytest.mark.parametrize('selection',[
    {'actor':'foreign','declared_action':[]}, {'actor':'hero'},
    {'actor':'hero','declared_action':[],'unbound':'override'},
])
def test_unknown_owner_missing_input_and_unbound_overrides_are_rejected(selection):
    with pytest.raises(CommandRejected):
        resolve_inventory_action_parameters(action(),owned_payload(selection),{'participants':[{'id':'hero'}]})

def test_literal_presentation_does_not_query_campaign_or_participant_records():
    value={'weapons':[{'id':'dagger','label':'Dagger'}]}
    source=owned_payload({'kind':'literal','value':value})
    result=resolve_surface_source(source,state={'private.plan':{'secret':'hidden'}},participants=[])
    assert result==value


def test_declared_equipment_literal_survives_every_player_projection_filter():
    from sylvia_gm.application.package_library import project_surfaces
    from sylvia_gm.domain.package_library import SurfaceDeclaration
    policy={'weapons':[{'id':'dagger','label':'Dagger'}]}
    source=owned_payload({'kind':'object','fields':{
        'entities':{'kind':'state','namespace':'rules','key':'entities'},
        'equipment':{'kind':'literal','value':policy},
    }})
    surface=SurfaceDeclaration(identity='equipment',capability='equipment',renderer='inventory',source=source)
    projection=project_surfaces((),(surface,),active_capabilities=frozenset({'equipment'}))
    assert projection.ui_surface_ids==('equipment',)
    resolved=resolve_surface_source(projection.ui_surfaces[0]['source'],state={'rules.entities':{}},participants=[])
    assert resolved['equipment']==policy
    private=SurfaceDeclaration(identity='private',capability='equipment',source=owned_payload({'kind':'campaign_plan'}))
    assert project_surfaces((),(private,),active_capabilities=frozenset({'equipment'})).ui_surfaces==()


@pytest.mark.parametrize('changed',[None,'files','metadata','version','capabilities','id'])
def test_dependency_rebind_preserves_every_other_admitted_manifest_field(changed):
    import json
    from copy import deepcopy
    from sylvia_gm.application.package_library import dependency_only_manifest_rebind
    original={'id':'setting','version':'1','metadata':{'publisher':'Owner'},'capabilities':['lore'],
              'files':[{'path':'story.md','sha256':'original'}],'dependencies':[{'sha256':'old'}],'content_sha256':'old'}
    current=deepcopy(original);current['dependencies']=[{'sha256':'new'}];current['content_sha256']='new'
    if changed=='files':current['files'][0]['sha256']='different'
    elif changed=='metadata':current['metadata']['publisher']='Other Owner'
    elif changed=='capabilities':current['capabilities']=['different']
    elif changed:current[changed]='different'
    assert dependency_only_manifest_rebind(json.dumps(original),json.dumps(current)) is (changed is None)
