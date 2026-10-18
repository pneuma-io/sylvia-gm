"""Read physical Touch from admitted bodies and solid geometry, without sight.

This neutral internal reader adds no spell, effect, cost or public surface. Each
spell owner supplies its actual resolved recipient before its own payment seam.
The frozen Darkvision physical validator remains unchanged.
"""
from .srd_control_behaviors import _entity, _lasting
from .srd_scene_relations import _snapshot, _spatial
from .srd_timed_actor_defenses import _call, _require, _set, _tool, _when
from .srd_timed_senses import _loop

HELPER = 'validate_revised_srd_physical_touch'


def physical_touch_tool():
    return _tool('Qualify actual physical Touch without a target-visibility requirement.',
        [_entity('caster_state'),_entity('target_state'),_lasting()], [
        _call('resolve_srd_scene_geometry_relation',{
            'actor_state':'{_caster_state_id}','target_state':'{_target_state_id}',
            'lasting_state':'{_lasting_state_id}'},
            ('_scene_distance','_scene_same_plane','_scene_actor_entity','_scene_exposure')),
        _require('_scene_distance <= 5 and _scene_same_plane == true',
            'Touch needs actual range within 5 feet on the same plane.'),
        _set('_physical_touch_origin',[
            '_scene_actor_entity.position[0]','_scene_actor_entity.position[1]',
            '_scene_actor_entity.position[2] + _scene_actor_entity.height * 0.5']),
        *_snapshot(),
        _set('_physical_touch_geometry','state.spatial_scene.geometry'),
        _set('_physical_touch_samples','_scene_exposure.samples'),
        _set('_physical_touch_clear',False),
        _loop('_physical_touch_samples','_physical_touch_sample',[
            _when('_physical_touch_clear != true',[
                _spatial('ray',{'map_id':'{state.spatial_scene.map.id}',
                    'origin':'{_physical_touch_origin}','destination':'{_physical_touch_sample.point}',
                    'include':['geometry'],'max_intersections':1024},'_physical_touch_ray'),
                _require('_physical_touch_ray.truncated != true',
                    'Touch requires a complete bounded physical ray.'),
                _set('_physical_touch_hits','_physical_touch_ray.intersections'),
                _set('_physical_touch_sample_clear',True),
                _loop('_physical_touch_hits','_physical_touch_hit',[
                    _loop('_physical_touch_geometry','_physical_touch_blocker',[
                        _when('_physical_touch_hit.id == _physical_touch_blocker.id and '
                            '_physical_touch_blocker.active == true and '
                            "_physical_touch_blocker.movement == 'blocking' and "
                            '(_physical_touch_blocker.metadata.plane == null or '
                            '_physical_touch_blocker.metadata.plane == _scene_actor_entity.metadata.plane)',[
                            _set('_physical_touch_sample_clear',False),
                        ]),
                    ]),
                ]),
                _when('_physical_touch_sample_clear == true',[
                    _set('_physical_touch_clear',True),
                ]),
            ]),
        ]),
        _require('_physical_touch_clear == true','Touch needs an unobstructed physical path.'),
    ])


def bind_revised_srd_touch_geometry(runtime):
    """Install one internal reader; profile-owned binders retain their own gates."""
    tools=runtime['tools']
    assert 'resolve_srd_scene_geometry_relation' in tools
    expected=physical_touch_tool()
    if HELPER in tools:
        assert tools[HELPER]==expected,'An incompatible physical Touch owner is already installed.'
        return ()
    tools[HELPER]=expected
    return (HELPER,)
