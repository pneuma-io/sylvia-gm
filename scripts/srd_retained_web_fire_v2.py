"""Ordinary Web cells using actual retained straight or ring Wall of Fire contact.

This source contribution neither selects nor enables a profile. Apply the frozen
support/collapse v1 binder first. Existing saved Web without cells keeps its exact
legacy geometry. Ordinary object flames and solid-occluded contact remain
explicitly unsupported; no invented flame volume or source authentication.
"""
from copy import deepcopy
from .srd_control_behaviors import _call,_each,_push
from .srd_spell_environment import _areas,_effect_lists,_environment_tool
from .srd_spell_magic_zones import _when
from .srd_timed_actor_defenses import _entity,_parameter,_require,_set
from .srd_timed_senses import _optional
from .srd_retained_web_lifecycle_v1 import _lasting

UNSUPPORTED=('ordinary object flame volume/contact',
             'instantaneous Fire spell and other unbound Fire contact producers',
             'contact through or around intersecting/unsupported solids',
             'anchoring other than the frozen v1 opposing full-face Box subset')


def overlap(a,b):
    return ' and '.join(f'{a}.min[{i}] < {b}.max[{i}] and {a}.max[{i}] > {b}.min[{i}]' for i in range(3))


def live_cell(cell='_web_cell'):
    return f"{cell}.status != 'destroyed' and ({cell}.status != 'burning' or state.lasting_tick < {cell}.burn_end_tick)"


def _cells(source,effects,*,live=True):
    body=[_set('_web_cell',source+'.web_cells[_web_cell_id]')]
    condition='_web_cell.source_sequence == '+source+'.sequence'
    if live:condition+=' and '+live_cell()
    body.append(_when(condition,effects))
    return [_set('_web_cell_ids',source+'.web_cell_ids'),_each('_web_cell_ids','_web_cell_id',body)]


def _cell_install():
    source='state.persistent_spell_areas.{_environment_cast_id}'
    volume='state.persistent_spell_areas[_environment_cast_id].volume'
    leaf=[_call('format_srd_information_projection',{'message':'wc_{_web_x}_{_web_y}_{_web_z}'},
                ('_info_projection_message',)),
          _set('_web_new_cell_id','_info_projection_message'),
          _set(source+'.web_cells.{_web_new_cell_id}',{
              'volume':{'kind':"'box'",'min':[f'{volume}.min[{i}] + 5 * _web_{axis}' for i,axis in enumerate('xyz')],
                        'max':[f'{volume}.min[{i}] + 5 * (_web_{axis} + 1)' for i,axis in enumerate('xyz')]},
              'source_sequence':'state.persistent_spell_areas[_environment_cast_id].sequence',
              'status':"'intact'"}),
          _push('_web_new_cell_ids','_web_new_cell_id')]
    return [_set(source+'.web_cell_schema',1),_set(source+'.web_cells',{}),
            _set('_web_new_cell_ids',[]),_set('_web_xy',[0,1,2,3]),_set('_web_zs',[0]),
            _when("environment_web_layer == 'cube'",[_set('_web_zs',[0,1,2,3])]),
            _each('_web_xy','_web_x',[_each('_web_xy','_web_y',[_each('_web_zs','_web_z',leaf)])]),
            _set(source+'.web_cell_ids','_web_new_cell_ids')]


def _body_cells(original):
    body=deepcopy(original)
    body['parameters'].append(_optional('web_burning_only','boolean',False))
    candidates=[e for effects in _effect_lists(body['effects']) for e in effects
                if e.get('condition')=='_info_scene_entity.metadata.plane == _web_body_source.plane']
    if len(candidates)!=1:raise ValueError('Actual ordinary Web body plane owner changed')
    old=candidates[0]['on_true'];query=deepcopy(old)
    # Retain the actual footprint expansion and neutral geometry kernel.
    def bind(v):
        if isinstance(v,str):return v.replace('_web_body_source.volume','_web_cell.volume')
        if isinstance(v,list):return [bind(x) for x in v]
        if isinstance(v,dict):return {k:bind(x) for k,x in v.items()}
        return v
    query=bind(query)
    call=next(e for e in query if e.get('call')=='srd_information_segment_interval')
    call['export_context']={'_web_piece_hit':'_segment_hit','_web_piece_entry':'_segment_entry','_web_piece_exit':'_segment_exit'}
    query.append(_when('_web_piece_hit == true',[
        _when('_body_hit != true',[_set('_body_entry','_web_piece_entry'),_set('_body_exit','_web_piece_exit')],
              [_set('_body_entry','min(_body_entry, _web_piece_entry)'),_set('_body_exit','max(_body_exit, _web_piece_exit)')]),
        _set('_body_hit',True)]))
    candidates[0]['on_true']=[_when('lasting_state.persistent_spell_areas[_environment_area_key].web_cell_schema == 1',
        _lasting(_cells('_web_body_source',[_when("web_burning_only != true or _web_cell.status == 'burning'",query)])),
        [_when('web_burning_only != true',old)])]
    return body


def _interval_cells(original):
    tool=deepcopy(original)
    candidates=[e for effects in _effect_lists(tool['effects']) for e in effects
                if e.get('condition')=='_sphere_source_current == true' and
                any(x.get('call')=='srd_information_segment_interval' for x in e.get('on_true',[]))]
    if len(candidates)!=1:raise ValueError('Actual Web terrain interval owner changed')
    old=candidates[0]['on_true'];pieces=deepcopy(old)
    next(e for e in pieces if e.get('call')=='srd_information_segment_interval')['params']['volume']='{_web_cell.volume}'
    candidates[0]['on_true']=[_when('_environment_area.web_cell_schema == 1',
        _lasting(_cells('_environment_area',pieces)),old)]
    return tool


def _sight_cells(original):
    tool=deepcopy(original)
    candidates=[e for effects in _effect_lists(tool['effects']) for e in effects
                if e.get('condition')=="_environment_area.mode == 'web'" and
                any(x.get('call')=='srd_information_segment_interval' for x in e.get('on_true',[]))]
    if len(candidates)!=1:raise ValueError('Actual Web point/sight Box owner changed')
    old=candidates[0]['on_true'];query=deepcopy(old[0]);query['params']['volume']='{_web_cell.volume}'
    query['export_context']={'_web_sight_piece':'_segment_hit'}
    candidates[0]['on_true']=[_when('_environment_area.web_cell_schema == 1',_lasting([
        _set('_sphere_hit',False),_call('qualify_srd_web_source',{'caster_state':'{_environment_area.caster}',
            'area_id':'{_environment_id}'},('_sphere_source_current',)),
        _when('_sphere_source_current == true',_cells('_environment_area',[query,
            _when('_web_sight_piece == true',[_set('_sphere_hit',True)])]))]),old)]
    return tool


def _settle_tool():
    return _environment_tool('Settle ordinary Web burns before current exact restraint membership refresh.',[],[
        _set('_web_burn_changed',False),*_areas([_when("_environment_area.mode == 'web' and _environment_area.web_cell_schema == 1",[
            _call('qualify_srd_web_source',{'caster_state':'{_environment_area.caster}','area_id':'{_environment_id}'},('_sphere_source_current',)),
            _when('_sphere_source_current == true',_cells('_environment_area',[
                _when("_web_cell.status == 'burning' and state.lasting_tick >= _web_cell.burn_end_tick",[
                    _set('state.persistent_spell_areas.{_environment_id}.web_cells.{_web_cell_id}.status',"'destroyed'"),
                    _set('_web_burn_changed',True)])],live=False))])]),
        _when('_web_burn_changed == true',[
            _set('_web_burn_actors','state.spatial_scene.entities'),_each('_web_burn_actors','_web_burn_actor',[
                _when('_web_burn_actor.active == true and _web_burn_actor.metadata.participant_ref != null',[
                    _call('refresh_srd_web_restraint_target',{'target_state':'{_web_burn_actor.metadata.participant_ref}'})])])])])


def _expose_tool():
    params=[_parameter('fire_area_id'),_optional('ordinary_flame_object_id','string',''),
            _optional('strict_request','boolean',True)]
    contact=[_set('_web_fire_contact_clear',True),_set('_web_fire_solids','state.spatial_scene.geometry'),
        _each('_web_fire_solids','_web_fire_solid',[
            _when("_web_fire_solid.active == true and _web_fire_solid.movement == 'blocking' and _web_fire_solid.metadata.plane == _web_fire_source.plane",[
                _when("_web_fire_solid.volume.kind == 'box'",[
                    _when(overlap('_web_cell.volume','_web_fire_solid.volume'),[_set('_web_fire_contact_clear',False)])],
                    [_set('_web_fire_contact_clear',False)])])]),
        _when('strict_request == true',[_require('_web_fire_contact_clear == true',
            'Web fire contact through or around intersecting/unsupported solids is unsupported.')]),
        _when('_web_fire_contact_clear == true',[
            _set('state.persistent_spell_areas.{_environment_id}.web_cells.{_web_cell_id}.status',"'burning'"),
            _set('state.persistent_spell_areas.{_environment_id}.web_cells.{_web_cell_id}.burn_end_tick','state.lasting_tick + 2'),
            _set('state.persistent_spell_areas.{_environment_id}.web_cells.{_web_cell_id}.fire_area_id','_web_fire_key'),
            _set('state.persistent_spell_areas.{_environment_id}.web_cells.{_web_cell_id}.fire_sequence','_web_fire_source.sequence')])]
    # The real public Fire cast invokes this after actual source installation.
    # Direct strict internal requests reject unsupported geometry; automatic
    # observation skips it so unrelated retained Full-SRD Fire remains usable.
    return _environment_tool('Expose ordinary five-foot Web cells to a genuine straight or ring Wall of Fire source.',params,[
        _require("ordinary_flame_object_id == ''",'Ordinary object fire exposure needs an admitted flame volume; object body geometry is insufficient.'),
        _call('format_srd_information_projection',{'message':'{fire_area_id}'},('_info_projection_message',)),
        _set('_web_fire_key','_info_projection_message'),_set('_web_fire_source','state.persistent_spell_areas[_web_fire_key]'),
        _require("_web_fire_source.mode == 'wall_fire'",'Use an actual retained Wall of Fire source.'),
        _call('qualify_srd_wall_of_fire_source',{'caster_state':'{_web_fire_source.caster}','area_id':'{_web_fire_key}'},('_sphere_source_current',)),
        _when('strict_request == true',[
            _require('_sphere_source_current == true','Fire exposure needs the actual current paid/concentrated Wall of Fire.'),
            _require("_web_fire_source.volume.kind == 'box' or _web_fire_source.volume.kind == 'annulus'",
                'Use the actual saved straight or ring Wall of Fire geometry.')]),
        _when("_sphere_source_current == true and (_web_fire_source.volume.kind == 'box' or _web_fire_source.volume.kind == 'annulus')",_areas([
            _when("_environment_area.mode == 'web' and _environment_area.web_cell_schema == 1 and _environment_area.plane == _web_fire_source.plane",[
                _call('qualify_srd_web_source',{'caster_state':'{_environment_area.caster}','area_id':'{_environment_id}'},('_sphere_source_current',)),
                _when('_sphere_source_current == true',_cells('_environment_area',[
                    _when("_web_cell.status == 'intact'",[
                        *_cell_fire_contact(), _when('_web_fire_cell_contact == true',contact)])]))])]))])


def _cell_fire_contact():
    """Test positive Box/shell overlap without igniting cells in the ring's empty inner cylinder."""
    nearest = ' + '.join(
        f'max(_wc.min[{i}] - _wf.outer.center[{i}], 0, _wf.outer.center[{i}] - _wc.max[{i}]) ** 2'
        for i in range(2))
    farthest = ' + '.join(
        f'max(abs(_wc.min[{i}] - _wf.inner.center[{i}]), abs(_wc.max[{i}] - _wf.inner.center[{i}])) ** 2'
        for i in range(2))
    return [_set('_wf','_web_fire_source.volume'), _set('_wc','_web_cell.volume'),
        _when("_wf.kind == 'box'",[_set('_web_fire_cell_contact',overlap('_wc','_wf'))],[
            _set('_web_fire_cell_outer', nearest + ' < _wf.outer.radius ** 2 and '
                '_wc.min[2] < _wf.outer.center[2] + _wf.outer.height and _wc.max[2] > _wf.outer.center[2]'),
            _set('_web_fire_cell_contact', '_web_fire_cell_outer == true and ' + farthest + ' > _wf.inner.radius ** 2')])]


def _observe_tool():
    return _environment_tool('Observe actual paid retained Fire sources after Fire or cell-aware Web installation.',[],[
        _when('state.has_web == true and state.has_wall_of_fire == true',[
            _set('_web_observed_fire_ids','state.persistent_spell_area_ids'),_each('_web_observed_fire_ids','_web_observed_fire_id',[
                _set('_web_observed_fire','state.persistent_spell_areas[_web_observed_fire_id]'),
                _when("_web_observed_fire.active == true and _web_observed_fire.mode == 'wall_fire' and (_web_observed_fire.volume.kind == 'box' or _web_observed_fire.volume.kind == 'annulus')",[
                    _call('expose_srd_retained_web_fire',{'fire_area_id':'{_web_observed_fire_id}','strict_request':False})])])])])


def _damage_effects():
    return [{'dice':{'expression':'2d4'},'store_as':'_web_fire_roll'},
        _call('resolve_srd_actor_spell_modifiers',{'target_state':'{_web_burning_winner.caster}','lasting_state':'state'}),
        _call('modify_srd_actor_raw_damage_roll',{'actor_state':'{_web_burning_winner.caster}','lasting_state':'state',
            'amount':'{_web_fire_roll.total}'},('_ray_raw_damage',)),
        _set('_web_fire_debt','max(0, -_ray_raw_damage)'),_set('_web_fire_amount','max(0, _ray_raw_damage)'),
        _call('apply_srd_qualifying_damage',{'target':'{_target_state_id}','amount':'{_web_fire_amount}','adjustment':0,
            'damage_type':'fire','source_state_id':'{_web_burning_winner.caster}','game_damage_roll':True,
            'unallocated_roll_penalty':'{_web_fire_debt}'},('_effective_damage',)),
        _set('target_state.last_web_fire',{'area_id':'_web_burning_winner_id','source_sequence':'_web_burning_winner.sequence',
            'turn_sequence':'combat.turn_sequence','raw_damage':'_web_fire_roll','applied_damage':'_effective_damage'})]


def _pulse_tool():
    return _environment_tool('Apply one actual typed2d4Fire start packet for current overlapping burning Web.',[_entity('target_state')],[
        _require('combat.turn_active == true and combat.active_actor == _target_state_id','Burning Web needs this creature\'s actual start turn.'),
        _when('target_state.is_alive == true and target_state.last_web_fire.turn_sequence != combat.turn_sequence',[
            _set('_web_burning_winner',None),_set('_web_burning_winner_id',None),
            _call('srd_information_scene_entity',{'subject':'{_target_state_id}'},('_info_position',)),
            _set('_web_start_position','_info_position'),*_areas([
                _when("_environment_area.mode == 'web' and _environment_area.web_cell_schema == 1",[
                    _call('qualify_srd_web_source',{'caster_state':'{_environment_area.caster}','area_id':'{_environment_id}'},('_sphere_source_current',)),
                    _when('_sphere_source_current == true',[
                        _call('resolve_srd_web_body_segment',{'moving_subject':'{_target_state_id}','area_id':'{_environment_id}',
                            'start':'{_web_start_position}','end':'{_web_start_position}','web_burning_only':True},('_body_hit',)),
                        _when('_body_hit == true',[
                            _when('_web_burning_winner == null or _environment_area.spell_level > _web_burning_winner.spell_level or (_environment_area.spell_level == _web_burning_winner.spell_level and _environment_area.sequence > _web_burning_winner.sequence)',[
                                _set('_web_burning_winner','_environment_area'),_set('_web_burning_winner_id','_environment_id')])])])])]),
            _when('_web_burning_winner != null',_damage_effects())])])


def bind_web_fire_cells(runtime,selected):
    """Bind actual consumers contextually; qualification/native admission stays open."""
    if 'web' not in selected:return False
    tools=runtime['tools']
    required={'cast_spell_environment','resolve_srd_web_body_segment','resolve_srd_web_intervals',
        'resolve_srd_environment_point','resolve_srd_environment_segment','refresh_srd_environment_lifecycle',
        'resolve_srd_environment_hazard_start_turn','qualify_srd_web_source','qualify_srd_wall_of_fire_source',
        'refresh_srd_web_restraint_target','collapse_srd_unsupported_web_start'}
    if missing:=required-tools.keys():raise ValueError('Web fire needs actual support and consumer owners: '+str(sorted(missing)))
    if 'settle_srd_retained_web_burns' in tools:return False
    cast=deepcopy(tools['cast_spell_environment'])
    installers=[e for effects in _effect_lists(cast['effects']) for e in effects
        if e.get('condition')=="_environment_profile.environment_mode == 'web'" and
        any(x.get('set',{}).get('field','').endswith('.layer') for x in e.get('on_true',[]))]
    if len(installers)!=1:raise ValueError('Actual stamped Web source installer changed')
    installers[0]['on_true']+=_lasting(_cell_install())
    refresh=[i for i,e in enumerate(cast['effects']) if e.get('call')=='refresh_srd_environment_scene']
    if len(refresh)!=1:raise ValueError('Actual source installation/refresh ordering changed')
    cast['effects'][refresh[0]:refresh[0]]=_lasting([_when("_environment_profile.environment_mode == 'web' or _environment_profile.environment_mode == 'wall_fire'",[
        _call('observe_srd_retained_web_fire',{})])])
    changed={'cast_spell_environment':cast,
        'resolve_srd_web_body_segment':_body_cells(tools['resolve_srd_web_body_segment']),
        'resolve_srd_web_intervals':_interval_cells(tools['resolve_srd_web_intervals']),
        'settle_srd_retained_web_burns':_settle_tool(),
        'expose_srd_retained_web_fire':_expose_tool(),
        'observe_srd_retained_web_fire':_observe_tool(),
        'resolve_srd_retained_web_burning_start':_pulse_tool()}
    for name in ('resolve_srd_environment_point','resolve_srd_environment_segment'):changed[name]=_sight_cells(tools[name])
    lifecycle=deepcopy(tools['refresh_srd_environment_lifecycle'])
    lifecycle['effects'].insert(0,_lasting(_when('state.has_web == true',[_call('settle_srd_retained_web_burns',{})])))
    changed['refresh_srd_environment_lifecycle']=lifecycle
    starting=deepcopy(tools['resolve_srd_environment_hazard_start_turn'])
    # Settle/retire holes first, apply actual fire before Web's retained save event.
    starting['effects'][:0]=_lasting([_when('state.has_web == true',[
        _call('settle_srd_retained_web_burns',{}),
        _when('combat.turn_active == true and combat.active_actor == _target_state_id',[
            _call('resolve_srd_retained_web_burning_start',{'target_state':'{_target_state_id}'})])])])
    changed['resolve_srd_environment_hazard_start_turn']=starting
    tools.update(changed)
    return True
