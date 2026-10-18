"""Paid Call Lightning source and repeatable BG3 strikes, without an SRD storm cloud.

Original contract: https://bg3.wiki/wiki/Call_Lightning and
https://bg3.wiki/wiki/Activate_Call_Lightning (research snapshot 2026-10-08).
Caster/granted-source ability selection follows the admitted casting owner.
"""

import hashlib
from .srd_lite_profiles import lists
from .srd_timed_actor_defenses import _call, _require, _set, _when

SOURCE_SHA = hashlib.sha256(b'BG3 Call Lightning: level3; range60ft; radius20/3ft; 10turns concentration; slot-level d10 lightning; dex half; recast action VS no slot').hexdigest()


def point_checks(point):
    return [
        _call('srd_information_scene_entity', {'subject':'{_caster_state_id}'},
              ('_info_position', '_info_scene_entity')),
        _call('measure_srd_information_distance', {'start':'{_info_position}', 'end':'{'+point+'}'},
              ('_scene_distance',)),
        _require('_scene_distance <= 60', 'Choose a lightning point within sixty feet of the actual caster.'),
        _require(' and '.join(f'{point}[{i}] >= state.spatial_scene.map.bounds.min[{i}] and {point}[{i}] <= state.spatial_scene.map.bounds.max[{i}]' for i in range(3)),
                 'The lightning point must be inside the admitted map.'),
        _call('srd_information_barriers', {'start':'{_info_position}', 'end':'{'+point+'}', 'rule':'solid'},
              ('_information_blocked',)),
        _require('_information_blocked != true', 'A solid barrier blocks the chosen lightning point.'),
    ]


def bind_lightning_zone(runtime, record):
    profile = runtime['definitions']['spells']['call_lightning']
    profile.update(range_feet=60, radius_feet=20/3, duration_rounds=10)
    runtime['definitions']['spell_casting']['call_lightning'].update(
        components=['verbal','somatic'], material='', material_parts=[],
        material_has_cost=False, material_consumed=False, focus_substitution=False,
        range='60 feet', duration='Concentration, up to 10 turns')
    tools = runtime['tools']
    cast = tools['cast_spell_environment']
    origin = next(i for i,e in enumerate(cast['effects']) if e.get('set',{}).get('field')=='_environment_cast_origin')
    cast['effects'][origin+1:origin+1] = [_when("_environment_profile.environment_mode == 'call_lightning'", [
        _require('lightning_point != null', 'Choose the actual initial lightning point.'),
        _set('_environment_cast_origin','lightning_point')])]
    changed = 0
    for effects in lists(cast['effects']):
        for effect in effects:
            if effect.get('condition') == "_environment_profile.environment_mode == 'call_lightning'" and 'Admit the caster' in str(effect):
                effect['on_true'] = [_require('save_dc >= 1 and lightning_point != null', 'Use the actual casting owner DC and selected lightning point.'),
                                    _set('_lightning_cast_storm',False), *point_checks('lightning_point')]
                changed += 1
            setter = effect.get('set',{})
            field = setter.get('field','')
            if field.endswith('.source_sha256') and setter.get('value') == "'9abd042be323813dfa45923df963dfff9a6a25a032f5daec712ec85c5955c134'":
                setter['value'] = repr(SOURCE_SHA)
            if field.endswith('.volume') and isinstance(setter.get('value'),dict) and setter['value'].get('radius')==60:
                setter['value']={'kind':"'strike_entitlement'", 'last_point':'_environment_cast_origin'}
            if field.endswith(('.weather_subject','.setting','.weather')) and '_lightning_cast_scene' in str(setter.get('value')):
                setter['value']=None
    assert changed == 1, 'Expected one SRD cloud requirement owner'
    source = tools['qualify_srd_call_lightning_source']
    for effects in lists(source['effects']):
        for effect in effects:
            setter=effect.get('set',{})
            if isinstance(setter.get('value'),str):
                setter['value']=setter['value'].replace('9abd042be323813dfa45923df963dfff9a6a25a032f5daec712ec85c5955c134',SOURCE_SHA)
    target=tools['qualify_srd_call_lightning_target']
    for effects in lists(target['effects']):
        for effect in effects:
            setter=effect.get('set',{})
            if setter.get('field')=='_sphere_target_source' and isinstance(setter.get('value'),dict):
                setter['value']['radius']=20/3
    bolt=tools['resolve_srd_call_lightning_bolt']
    for effects in lists(bolt['effects']):
        for i,effect in list(enumerate(effects)):
            req=effect.get('require',{})
            if 'under this cloud' in req.get('error',''):
                effects[i:i+1]=point_checks('point')
    bolt['description']='Resolve one selected BG3 lightning strike from its genuine paid concentration source.'
    recast=tools['call_srd_lightning_source']
    for name in ('verbal_component_available','somatic_component_available'):
        recast['parameters'].append({'name':name,'type':'boolean','required':False,'default':True})
    recast['effects'][:0]=[
        _require('verbal_component_available == true and somatic_component_available == true', 'Activate Call Lightning requires Verbal and Somatic components.'),
        _call('srd_information_scene_entity', {'subject':'{_caster_state_id}'}, ('_info_position','_info_scene_entity')),
        _call('resolve_srd_environment_point', {'point':'{_info_position}','plane':'{_info_scene_entity.metadata.plane}'}, ('_environment_silence',)),
        _require('_environment_silence != true', 'Silence prevents the Verbal recast component.'),
        *point_checks('point'),
    ]
    declaration=next(row for row in record['tools'] if row['name']=='call_srd_lightning_source')
    for name in ('verbal_component_available','somatic_component_available'):
        declaration['input_schema']['properties'][name]={'type':'boolean','default':True}


def expose_lightning_recast(runtime, denied):
    name='call_srd_lightning_source'
    guard=runtime['tools'][name]['effects'].pop(0)
    assert guard == {'require':{'condition':'false','error':'This spell operation is pending or excluded in the SRD Lite foundation.'}}
    runtime['tools'][name]['metadata'].pop('visible_to_player',None)
    denied.remove(name)
