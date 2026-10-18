"""Consume actual admitted whole Box flame sections for ordinary Web burns.

The existing public flame recorder and Sleet consumer establish footprint plus
height as the admitted flame-section bounds, including current holder origin.
This adapter adds no geometry primitive, fact flag, profile or selected name.
Apply frozen support v1 and straight-Fire/cell v2 before this binder.
"""
from copy import deepcopy
from .srd_control_behaviors import _call,_each
from .srd_spell_environment import _areas,_environment_tool
from .srd_spell_magic_zones import _when
from .srd_timed_actor_defenses import _parameter,_require,_set
from .srd_timed_senses import _optional
from .srd_retained_web_lifecycle_v1 import _lasting
from .srd_retained_web_fire_v2 import _cells,overlap

REMAINING=('contact through/around intersecting or unsupported solids',
           'direct instantaneous Fire exposure without an admitted flame section',
           'non-Box flame section geometry','general anchoring and wall/ceiling layers',
           'normal package admission and native/public/UI acceptance')


def flame_volume():
    return {'kind':"'box'",'min':[
        '_illumination_object_origin[0] - _web_flame_scene.footprint.half_extents[0]',
        '_illumination_object_origin[1] - _web_flame_scene.footprint.half_extents[1]',
        '_illumination_object_origin[2]'],
        'max':['_illumination_object_origin[0] + _web_flame_scene.footprint.half_extents[0]',
               '_illumination_object_origin[1] + _web_flame_scene.footprint.half_extents[1]',
               '_illumination_object_origin[2] + _web_flame_scene.height']}


def _contact_effects():
    return [_set('_web_flame_contact_clear',True),_set('_web_flame_solids','state.spatial_scene.geometry'),
        _each('_web_flame_solids','_web_flame_solid',[
            _when("_web_flame_solid.active == true and _web_flame_solid.movement == 'blocking' and _web_flame_solid.metadata.plane == _illumination_object_plane",[
                _when("_web_flame_solid.volume.kind == 'box'",[
                    _when(overlap('_web_cell.volume','_web_flame_solid.volume'),[_set('_web_flame_contact_clear',False)])],
                    [_set('_web_flame_contact_clear',False)])])]),
        _when('strict_request == true',[_require('_web_flame_contact_clear == true',
            'Web flame contact through/around intersecting or unsupported solids requires a separate supported flame section.')]),
        _when('_web_flame_contact_clear == true',[
            _set('state.persistent_spell_areas.{_environment_id}.web_cells.{_web_cell_id}.status',"'burning'"),
            _set('state.persistent_spell_areas.{_environment_id}.web_cells.{_web_cell_id}.burn_end_tick','state.lasting_tick + 2'),
            _set('state.persistent_spell_areas.{_environment_id}.web_cells.{_web_cell_id}.flame_object_id','_web_flame_key'),
            _set('state.persistent_spell_areas.{_environment_id}.web_cells.{_web_cell_id}.flame_volume_at_exposure','_web_flame_volume')])]


def _expose_tool():
    return _environment_tool('Expose Web to actual current burning/exposed whole admitted Box flame sections.',
        [_parameter('object_id'),_optional('strict_request','boolean',True)],[
        _call('format_srd_information_projection',{'message':'{object_id}'},('_info_projection_message',)),
        _set('_web_flame_key','_info_projection_message'),
        _call('srd_information_scene_entity',{'subject':'{_web_flame_key}'},('_info_scene_entity','_info_position')),
        _set('_web_flame_scene','_info_scene_entity'),
        _set('_web_flame_admitted',"_web_flame_scene.active == true and _web_flame_scene.metadata.is_object == true and _web_flame_scene.metadata.srd_ordinary_flame.kind == 'ordinary_flame'"),
        _set('_web_flame_exposed',"_web_flame_admitted == true and _web_flame_scene.metadata.srd_ordinary_flame.burning == true and _web_flame_scene.metadata.srd_ordinary_flame.exposed == true"),
        _set('_web_flame_geometry',"_web_flame_scene.footprint.kind == 'box' and _web_flame_scene.height > 0"),
        _when('strict_request == true',[
            _require('_web_flame_admitted == true','Use an actual active scene object admitted by record_srd_ordinary_flame.'),
            _require('_web_flame_exposed == true','Use an actual currently exposed burning flame section; unlit or covered fuel cannot expose Web.'),
            _require('_web_flame_geometry == true','Web flame contact needs actual whole Box footprint and positive height; admit separate flame sections.')]),
        _when('_web_flame_exposed == true and _web_flame_geometry == true',[
            _call('check_srd_illumination_object_visibility',{'object_id':'{_web_flame_key}'},
                ('_illumination_object_origin','_illumination_object_plane')),
            _set('_web_flame_volume',flame_volume()),*_areas([
                _when("_environment_area.mode == 'web' and _environment_area.web_cell_schema == 1 and _environment_area.plane == _illumination_object_plane",[
                    _call('qualify_srd_web_source',{'caster_state':'{_environment_area.caster}','area_id':'{_environment_id}'},('_sphere_source_current',)),
                    _when('_sphere_source_current == true',_cells('_environment_area',[
                        _when("_web_cell.status == 'intact' and "+overlap('_web_cell.volume','_web_flame_volume'),_contact_effects())]))])])])])


def _observe_tool():
    return _environment_tool('Observe actual whole flame sections after admission, ignition, Web cast or holder movement.',[],[
        _when('state.has_web == true',[
            _set('_web_observed_flames','state.spatial_scene.entities'),
            _each('_web_observed_flames','_web_observed_flame',[
                _when("_web_observed_flame.active == true and _web_observed_flame.metadata.is_object == true and _web_observed_flame.metadata.srd_ordinary_flame.kind == 'ordinary_flame' and _web_observed_flame.metadata.srd_ordinary_flame.burning == true and _web_observed_flame.metadata.srd_ordinary_flame.exposed == true",[
                    _call('expose_srd_retained_web_flame',{'object_id':'{_web_observed_flame.id}','strict_request':False})])])])])


def bind_web_flame_sections(runtime,selected):
    """Contextual source-only binding; preserve frozen v2 and existing flame facts."""
    if 'web' not in selected:return False
    tools=runtime['tools']
    required={'expose_srd_retained_web_fire','observe_srd_retained_web_fire','settle_srd_retained_web_burns',
        'record_srd_ordinary_flame','check_srd_illumination_object_visibility','qualify_srd_web_source',
        'refresh_srd_environment_lifecycle','resolve_srd_environment_hazard_start_turn'}
    if missing:=required-tools.keys():raise ValueError('Web flame sections need actual owners: '+str(sorted(missing)))
    if 'expose_srd_retained_web_flame' in tools:return False
    expose=deepcopy(tools['expose_srd_retained_web_fire'])
    first=expose['effects'][0]
    if first.get('require',{}).get('condition')!="ordinary_flame_object_id == ''":
        raise ValueError('Preserve the frozen v2 explicit ordinary flame branch')
    expose['effects']=[_when("ordinary_flame_object_id != ''",[
        _call('expose_srd_retained_web_flame',{'object_id':'{ordinary_flame_object_id}','strict_request':'{strict_request}'})],
        expose['effects'][1:])]
    observe=deepcopy(tools['observe_srd_retained_web_fire'])
    observe['effects'].append(_lasting(_call('observe_srd_retained_web_flames',{})))
    changed={'expose_srd_retained_web_fire':expose,'observe_srd_retained_web_fire':observe,
             'expose_srd_retained_web_flame':_expose_tool(),'observe_srd_retained_web_flames':_observe_tool()}
    # Actual final producer leaves, not their public wrappers, own the mutation.
    recorder='record_srd_ordinary_flame_node' if 'record_srd_ordinary_flame_node' in tools else 'record_srd_ordinary_flame'
    for name in (recorder,'ignite_srd_area_object','move_srd_scene_creature_node'):
        if name not in tools:
            if name==recorder:raise ValueError('Actual ordinary flame fact writer missing')
            continue
        owner=deepcopy(tools[name])
        if name==recorder and 'srd_ordinary_flame' not in str(owner['effects']):
            raise ValueError('Actual ordinary flame producer contract changed')
        owner['effects'].append(_lasting(_when('state.has_web == true',[
            _call('observe_srd_retained_web_flames',{})])))
        changed[name]=owner
    # Settled clock observations preserve already burning cells and catch current
    # admitted section changes. Start observations precede the actual v2 pulse.
    for name in ('refresh_srd_environment_lifecycle','resolve_srd_environment_hazard_start_turn'):
        owner=deepcopy(tools[name]);owner['effects'].insert(0,_lasting(_when('state.has_web == true',[
            _call('observe_srd_retained_web_flames',{})])))
        changed[name]=owner
    tools.update(changed)
    return True
