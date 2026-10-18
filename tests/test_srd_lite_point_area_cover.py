"""Native Fire Storm cover unions use categorical facts at actual points without changing game state."""
from copy import deepcopy
import json,os,zipfile
from pathlib import Path
import pytest
import yaml
from sylvia_gm.runtime import RulesetRuntime
from tests.test_srd_lite_fire_storm_geometry import _run,cube,_scene_entity,_wall,_calls,NAMESPACE,QUALIFY


@pytest.fixture(scope='module')
def native_point_cover():
    """Parse only this admitted package's actual Fire Storm dependency closure."""
    with zipfile.ZipFile(Path(os.environ['SRD_LITE_CANDIDATE'])) as archive:
        data=yaml.load(archive.read('packages/'+NAMESPACE+'/runtime/ruleset.yaml'),Loader=yaml.CSafeLoader)
    assert 'cover_from_points' in json.dumps(data['tools']['qualify_srd_sunburst_target_cover'])
    pool=data.get('parameter_declarations',{})
    for tool in data['tools'].values():
        tool['parameters']=[deepcopy(pool[item]) if isinstance(item,str) else item for item in tool['parameters']]
    closure={};pending=[QUALIFY]
    while pending:
        name=pending.pop()
        if name not in closure:
            closure[name]=data['tools'][name];pending.extend(_calls(closure[name]['effects']))
    subset={key:value for key,value in data.items() if key not in {'tools','procedures'}}
    subset['tools']=closure
    return RulesetRuntime(ruleset_dict=subset),data,{}


def _facts(*pairs,plane='material'):
    """Record categorical world facts at exact points; sampling does not invent cover percentages."""
    return [{'point':list(point),'plane':plane,'cover':cover} for point,cover in pairs]


def _solid(identity,minimum,maximum):
    row=_wall();row['id']=identity;row['volume']={'kind':'box','min':minimum,'max':maximum};return row


@pytest.mark.parametrize('first,second,legacy,expected_half',[
    ('half','none','half',[]),
    ('half','three_quarters','three_quarters',['recipient']),
    ('total','half','total',['recipient']),
])
def test_least_blocked_cube_origin_controls_each_unique_recipient(native_point_cover,first,second,legacy,expected_half):
    recipient=_scene_entity('recipient',[10,5,0])
    recipient['metadata'].update(cover_from={'hero':legacy},
        cover_from_points=_facts(([0,0,0],first),([20,0,0],second)))
    geometry=[_solid('left',[4,1,0],[5,5,8 if first=='total' else 2])]
    if second!='none':geometry.append(_solid('right',[15,1,0],[16,5,2.8 if second=='three_quarters' else 2]))
    result=_run(native_point_cover,cubes=[cube(),cube((10,0,0),origin=(20,0,0))],frames=[recipient],geometry=geometry)
    assert result.success,result.error
    assert result.context['_fire_storm_targets']==['recipient']
    assert result.context['_fire_storm_half_cover']==expected_half
    assert result.context['_fire_storm_three_quarters']==[]


def test_duplicate_facts_for_the_same_origin_reject_before_payment_or_dice(native_point_cover):
    recipient=_scene_entity('recipient',[10,5,0])
    recipient['metadata'].update(cover_from={'hero':'half'},
        cover_from_points=_facts(([0,0,0],'half'),([0,0,0],'none')))
    result=_run(native_point_cover,frames=[recipient])
    assert not result.success and 'ambiguous' in result.error.lower()


@pytest.mark.parametrize('point_facts',[None,_facts(([0,0,0],'none'),([10,0,0],'none'),plane='ethereal')])
def test_source_only_cover_and_other_plane_facts_preserve_existing_behavior(native_point_cover,point_facts):
    recipient=_scene_entity('recipient',[10,5,0]);recipient['metadata']['cover_from']={'hero':'half'}
    if point_facts is not None:recipient['metadata']['cover_from_points']=point_facts
    result=_run(native_point_cover,frames=[recipient])
    assert result.success,result.error
    assert result.context['_fire_storm_half_cover']==['recipient']


def test_malformed_origin_fact_rejects_clearly_without_state_change(native_point_cover):
    recipient=_scene_entity('recipient',[10,5,0])
    recipient['metadata'].update(cover_from={'hero':'half'},
        cover_from_points=[{'point':[0,0],'plane':'material','cover':'none'}])
    result=_run(native_point_cover,frames=[recipient])
    assert not result.success and 'three-dimensional point' in result.error
