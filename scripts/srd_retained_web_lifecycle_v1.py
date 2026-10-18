"""Narrow ordinary Web support/collapse repair; burn-cell closure remains pending.

The default floor owner remains byte-for-byte equivalent. Supported anchored
cubes use two actual opposing full-face Box solids; other anchoring layouts are
explicitly unsupported. An unanchored cube uses the existing real concentration
expiry and next-caster-start removal owners. This does not select/enable Web.
"""
from copy import deepcopy
import re

if __package__:
    from .srd_spell_environment import _areas,_effect_lists,_environment_tool
    from .srd_control_behaviors import _call,_each
    from .srd_timed_actor_defenses import _entity,_require,_set
    from .srd_spell_magic_zones import _when
    from .srd_timed_senses import _optional
else:
    from srd_spell_environment import _areas,_effect_lists,_environment_tool
    from srd_control_behaviors import _call,_each
    from srd_timed_actor_defenses import _entity,_require,_set
    from srd_spell_magic_zones import _when
    from srd_timed_senses import _optional

PENDING_CRITERIA = ('ordinary five-foot burn-cell exposure and one-round lifecycle',
                    'real2d4 Fire start pulses',
                    'burned-hole sight/body/terrain union and exact restraint retirement')


def _lasting(value):
    """Keep inserted effects on the actual existing private lasting-state binding."""
    if isinstance(value,str):return re.sub(r'\bstate\.', 'lasting_state.',value)
    if isinstance(value,list):return [_lasting(item) for item in value]
    if isinstance(value,dict):return {key:_lasting(item) for key,item in value.items()}
    return value


def anchor_conditions(a='_wa',b='_wb',v='_wv'):
    """Require actual contact on opposing faces and complete transverse support.

Small tree/branch anchors, sloped/ring supports and disconnected contact need a
separate source contract. A wholly supported opposing Box pair is unambiguous.
"""
    alternatives=[]
    for axis in range(3):
        for left,right in ((a,b),(b,a)):
            conditions=[f'abs({left}.max[{axis}] - {v}.min[{axis}]) < 0.000000001',
                        f'abs({right}.min[{axis}] - {v}.max[{axis}]) < 0.000000001']
            for other in range(3):
                if other != axis:
                    conditions.extend([f'{left}.min[{other}] <= {v}.min[{other}]',
                        f'{left}.max[{other}] >= {v}.max[{other}]',
                        f'{right}.min[{other}] <= {v}.min[{other}]',
                        f'{right}.max[{other}] >= {v}.max[{other}]'])
            alternatives.append('('+' and '.join(conditions)+')')
    return alternatives


def _parameters():
    return [_optional('environment_web_layer','enum','floor',enum_values=['floor','cube']),
            _optional('environment_web_anchor_ids','array',[],items={'type':'string','minLength':1},max_items=2)]


def _cube_effects(bounds_guard):
    return [
        _set('_environment_ground_id',None),
        _set('_web_cast_volume',{'kind':"'box'",'min':['_environment_cast_origin[0] - 10',
            '_environment_cast_origin[1] - 10','_environment_cast_origin[2]'],
            'max':['_environment_cast_origin[0] + 10','_environment_cast_origin[1] + 10',
                   '_environment_cast_origin[2] + 20']}),
        deepcopy(bounds_guard),
        _require('len(environment_web_anchor_ids) == 0 or len(environment_web_anchor_ids) == 2',
                 'Select no supports for a temporary cube, or two actual opposing solid supports.'),
        _when('len(environment_web_anchor_ids) == 2',[
            _require('environment_web_anchor_ids[0] != environment_web_anchor_ids[1]',
                     'A Web anchor pair needs two distinct real solids.'),
            _set('_web_supports',[]),_set('_web_support_rows','state.spatial_scene.geometry'),
            _each('_web_support_rows','_web_support',[
                _when('_web_support.id in environment_web_anchor_ids',[
                    _require('_web_support.active == true and _web_support.movement == \'blocking\' and '
                             '_web_support.metadata.plane == _environment_plane and '
                             '_web_support.volume.kind == \'box\'',
                             'Use actual active same-plane blocking Box geometry as Web support.'),
                    {'list_push':{'list':'_web_supports','value':'_web_support.volume'}},
                ]),
            ]),
            _require('len(_web_supports) == 2','Both chosen Web solids must exist in the admitted scene.'),
            _set('_wa','_web_supports[0]'),_set('_wb','_web_supports[1]'),_set('_wv','_web_cast_volume'),
            _set('_web_supported',False),
            *[_when(condition,[_set('_web_supported',True)]) for condition in anchor_conditions()],
            _require('_web_supported == true','These supports must touch opposing complete faces of the20-foot cube; other anchoring is unsupported.'),
        ]),
    ]


def _collapse_tool():
    return _environment_tool('Remove only a current unsupported Web at its actual next caster turn start.',
        [_entity('target_state')],_areas([
            _when("_environment_area.mode == 'web' and _environment_area.web_unsupported_cube == true and "
                  '_environment_area.caster == _target_state_id and '
                  'target_state.turns_started >= _environment_area.web_collapse_turn',[
                _call('qualify_srd_web_source',{'caster_state':'{_target_state_id}','area_id':'{_environment_id}'},
                      ('_sphere_source_current',)),
                _when('_sphere_source_current == true',[
                    _call('end_srd_concentration_node',{'source_state':'{_target_state_id}',
                        'target_state':'{_target_state_id}','lasting_state':'state'}),
                ]),
            ]),
        ]))


def _staged_web_updates(tools,cast):
    """Carry the same actual geometry through paid Action intent and completion."""
    begin_name='begin_srd_spell_action';complete_name='complete_srd_pending_action'
    qualifier='validate_srd_retained_web_action'
    present={begin_name,complete_name}&tools.keys()
    if not present:return {}
    if len(present)!=2:raise ValueError('Staged Web needs both actual Action and completion owners')
    begin=deepcopy(tools[begin_name]);complete=deepcopy(tools[complete_name])
    calls=[e for effects in _effect_lists(complete['effects']) for e in effects
           if e.get('call')=='cast_spell' and e.get('params',{}).get('spell')=='{_pending_action.parameters.spell}']
    if len(calls)!=1:raise ValueError('Preserve the actual saved spell-cast completion dispatcher')
    names={p['name'] for p in _parameters()}
    if qualifier in tools:
        if (names<={p['name'] for p in begin['parameters']} and
            all(calls[0]['params'].get(n)=='{_pending_action.parameters.'+n+'}' for n in names) and
            any(e.get('call')==qualifier for effects in _effect_lists(begin['effects']) for e in effects)):
            return {}
        raise ValueError('Partial staged Web binding requires reconciliation')
    if names&{p['name'] for p in begin['parameters']}:
        raise ValueError('Preserve the actual unbound staged Web declaration')
    # Reuse the actual read-only cast qualifier statements, including range,
    # solid path, whole-map bounds and the exact floor/cube branch. No installer,
    # source instance, material consumption or Action spend belongs in this tool.
    effects=cast['effects']
    def one(predicate,label):
        found=[e for e in effects if predicate(e)]
        if len(found)!=1:raise ValueError('Preserve actual Web '+label)
        return deepcopy(found[0])
    lookup=one(lambda e:e.get('store_as')=='_environment_profile','profile lookup')
    slot=one(lambda e:e.get('require',{}).get('error')=="Use this environment family's actual valid source slot.",'source slot guard')
    plane=one(lambda e:e.get('set',{}).get('field')=='_environment_plane','actual plane')
    origin=one(lambda e:e.get('set',{}).get('field')=='_environment_cast_origin','actual chosen origin')
    scene=next(deepcopy(e) for e in effects if e.get('call')=='srd_information_scene_entity' and
               e.get('params',{}).get('subject')=='{_caster_state_id}')
    distance=one(lambda e:e.get('call')=='measure_srd_information_distance','actual distance helper')
    range_guard=one(lambda e:e.get('require',{}).get('error')=="The chosen environment origin is outside the spell's range.",'source range guard')
    barriers=one(lambda e:e.get('call')=='srd_information_barriers','actual solid-path helper')
    path_guard=one(lambda e:e.get('require',{}).get('error')=='A solid barrier prevents placing this environment.','actual solid-path guard')
    geometry=one(lambda e:e.get('condition')=="_environment_profile.environment_mode == 'web'" and
                 any(x.get('condition')=="environment_web_layer == 'floor'" for x in e.get('on_true',[])),
                 'floor/cube pre-spend geometry')
    wanted={'caster_state','spell','slot_level','save_dc','origin','lasting_state'}|names
    parameters=[deepcopy(p) for p in cast['parameters'] if p['name'] in wanted]
    if {p['name'] for p in parameters}!=wanted:raise ValueError('Actual Web qualifier inputs changed')
    validate=_environment_tool('Validate actual Web geometry before the existing casting Action is spent.',parameters,
        [lookup,slot,_require("_environment_profile.environment_mode == 'web'",'Use this qualifier only for the actual Web profile.'),
         scene,plane,origin,distance,range_guard,barriers,path_guard,geometry])
    spend=[i for i,e in enumerate(begin['effects']) if e.get('call')=='spend_spell_casting_budget']
    if len(spend)!=1:raise ValueError('Preserve the actual single staged casting Action spend')
    begin['parameters']+=_parameters()
    begin['effects'].insert(spend[0],_when("spell == 'web'",[_call(qualifier,{
        'caster_state':'{_caster_state_id}','spell':'{spell}','slot_level':'{_slot_level}',
        'save_dc':'{_casting_profile.save_dc}','origin':'{environment_origin}',
        **{p['name']:'{'+p['name']+'}' for p in _parameters()},'lasting_state':'{_lasting_state_id}'})]))
    stamped=[i for i,e in enumerate(begin['effects']) if e.get('set',{}).get('field')==
             'lasting_state.pending_srd_actions.{_pending_action_id}.spell']
    if len(stamped)!=1:raise ValueError('Preserve actual saved-parameter allocation before spell stamping')
    begin['effects'][stamped[0]:stamped[0]]=[_set(
        'lasting_state.pending_srd_actions.{_pending_action_id}.parameters.'+p['name'],p['name']) for p in _parameters()]
    calls[0]['params'].update({p['name']:'{_pending_action.parameters.'+p['name']+'}' for p in _parameters()})
    return {qualifier:validate,begin_name:begin,complete_name:complete}


def bind_web_support_and_collapse(runtime,selected):
    """Bind only supported source geometry and honest temporary unsupported cubes.

This function does not bind fire exposure or claim complete Web admission. Main
must retain visible partial status until PENDING_CRITERIA receive real closure.
"""
    if 'web' not in selected:return False
    tools=runtime['tools']
    needed={'cast_spell_environment','resolve_srd_environment_hazard_start_turn','qualify_srd_web_source'}
    if missing:=needed-tools.keys():raise ValueError('Actual ordinary Web owners missing: '+str(sorted(missing)))
    original=tools['cast_spell_environment']
    if any(p.get('name')=='environment_web_layer' for p in original['parameters']):
        if 'collapse_srd_unsupported_web_start' not in tools:
            raise ValueError('Partial previous support binding requires reconciliation')
        staged=_staged_web_updates(tools,original)
        tools.update(staged)
        return bool(staged)
    cast=deepcopy(original)
    matches=[e for effects in _effect_lists(cast['effects']) for e in effects
        if e.get('condition')=="_environment_profile.environment_mode == 'web'" and
        any(x.get('call')=='qualify_srd_environment_ground' for x in e.get('on_true',[]))]
    if len(matches)!=1:raise ValueError('Preserve the actual floor-layer Web geometry guard')
    floor=deepcopy(matches[0]['on_true'])
    bounds=[e for e in floor if e.get('require',{}).get('error')=='The whole actual Web must fit the admitted map bounds.']
    if len(bounds)!=1:raise ValueError('Preserve Web whole-map bounds before any source or cost')
    # Existing floor statements retain their exact order and contents.
    matches[0]['on_true']=[_require('save_dc >= 1','Use the actual caster spell save DC for Web.'),
        _when("environment_web_layer == 'floor'",[
            _require('len(environment_web_anchor_ids) == 0','Floor Web uses its actual ground support; omit cube anchors.'),
            *floor,
        ],_lasting(_cube_effects(bounds[0])))]
    cast['parameters']+=_parameters()
    installs=[e for effects in _effect_lists(cast['effects']) for e in effects
        if e.get('condition')=="_environment_profile.environment_mode == 'web'" and
        any(x.get('set',{}).get('field','').endswith('.layer') for x in e.get('on_true',[]))]
    if len(installs)!=1:raise ValueError('Preserve the actual stamped Web source installer')
    body=installs[0]['on_true']
    layer=next(x for x in body if x.get('set',{}).get('field','').endswith('.layer'))
    layer['set']['value']='environment_web_layer'
    body += _lasting([
        _set('state.persistent_spell_areas.{_environment_cast_id}.web_anchor_ids','environment_web_anchor_ids'),
        _set('state.persistent_spell_areas.{_environment_cast_id}.web_unsupported_cube',False),
        _when("environment_web_layer == 'cube' and len(environment_web_anchor_ids) == 0",[
            _set('_web_next_caster_turn',1),
            _when('caster_state.turns_started != null',[_set('_web_next_caster_turn','caster_state.turns_started + 1')]),
            _set('state.persistent_spell_areas.{_environment_cast_id}.web_unsupported_cube',True),
            _set('state.persistent_spell_areas.{_environment_cast_id}.web_collapse_turn','_web_next_caster_turn'),
            _set('state.persistent_spell_areas.{_environment_cast_id}.expires_tick','state.lasting_tick + 2'),
            _set('caster_state.concentration_expires_tick','state.lasting_tick + 2'),
        ]),
    ])
    starting=deepcopy(tools['resolve_srd_environment_hazard_start_turn'])
    starting['effects'].insert(0,_when('lasting_state.has_web == true',[
        _call('collapse_srd_unsupported_web_start',{'target_state':'{_target_state_id}'}),
    ]))
    changed={'cast_spell_environment':cast,'resolve_srd_environment_hazard_start_turn':starting,
             'collapse_srd_unsupported_web_start':_collapse_tool()}
    tail=None
    if 'finish_srd_retained_cast_tail' in tools:
        tail=deepcopy(tools['finish_srd_retained_cast_tail'])
        delegates=[e for effects in _effect_lists(tail['effects']) for e in effects if e.get('call')=='cast_spell_environment']
        if len(delegates)!=1:raise ValueError('Actual retained environment tail dispatch changed')
        delegates[0]['params'].update({p['name']:'{_retained_cast_tail_choices.'+p['name']+'}' for p in _parameters()})
        names=tail.get('metadata',{}).get('retained_spell_reactions_v1_choices')
        if not isinstance(names,list):raise ValueError('Preserve the actual retained choice declaration')
        names.extend(p['name'] for p in _parameters())
        changed['finish_srd_retained_cast_tail']=tail
    # Change existing final aliases/capsules contextually without replacing Main's union.
    for name in ('cast_spell','cast_spell_node','cast_spell_outside_combat'):
        if name not in tools:continue
        caller=deepcopy(tools[name]);caller['parameters']+=_parameters()
        delegates=[e for effects in _effect_lists(caller['effects']) for e in effects
                   if e.get('call') in ('cast_spell_environment','cast_spell')]
        if len(delegates)==1:
            delegates[0]['params'].update({p['name']:'{'+p['name']+'}' for p in _parameters()})
        elif tail is not None and name=='cast_spell':
            captures=[e['set'] for effects in _effect_lists(caller['effects']) for e in effects
                      if isinstance(e.get('set',{}).get('value'),list)
                      and len(e['set']['value'])==1 and isinstance(e['set']['value'][0],dict)
                      and (e['set']['field']=='_retained_cast_tail_choices' or e['set']['field'].endswith('.retained_cast_choices'))]
            if not captures:raise ValueError('Preserve actual paid-cast retained choices before dispatch/retry')
            for capture in captures:capture['value'][0].update({p['name']:p['name'] for p in _parameters()})
        else:raise ValueError('Actual '+name+' environment dispatch changed')
        changed[name]=caller
    changed.update(_staged_web_updates(tools,cast))
    tools.update(changed)
    return True
