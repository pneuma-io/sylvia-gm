"""Four assigned SRD owners for the approved launch pivot.

Use the existing accepted implementations. Call before the main selection
boundary, from the accepted full runtime; do not invoke the deferred legacy
Produce Flame binder. This module never changes another spell or class grant.
"""
from copy import deepcopy
import hashlib,json

OWNED=('produce_flame','fire_storm','sunburst','meteor_swarm')
FAMILIES={'produce_flame':'illumination_spell','fire_storm':'instantaneous_area_save_damage',
          'sunburst':'instantaneous_area_save_damage','meteor_swarm':'instantaneous_area_save_composite_damage'}
REQUIRED_TOOLS={'prepare_srd_produce_flame_source','hurl_srd_produce_flame',
    'hurl_srd_produce_flame_node','begin_srd_produce_flame_attack','complete_srd_produce_flame_attack',
    'cast_srd_sunburst','collect_srd_sunburst_creature_targets',
    'cast_spell_instantaneous_area_save_damage','cast_spell_instantaneous_area_save_composite_damage'}

def owned_srd_contribution(runtime):
    """Return exact existing rows plus owner requirements; mutate nothing."""
    missing=REQUIRED_TOOLS-set(runtime['tools'])
    if missing:raise ValueError('Retain the accepted spell owners: '+', '.join(sorted(missing)))
    result={}
    for identity in OWNED:
        row=runtime['definitions']['spells'][identity]
        if row['family']!=FAMILIES[identity]:raise ValueError('Spell owner drift: '+identity)
        result[identity]={'spell':deepcopy(row),
            'casting':deepcopy(runtime['definitions']['spell_casting'][identity])}
    flame=result['produce_flame']
    if (flame['spell']['casting_time']!='bonus_action' or flame['spell']['bright_radius']!=20
            or flame['spell']['duration_rounds']!=100 or flame['casting']['classes']!=['druid']):
        raise ValueError('Use accepted SRD Produce Flame; omit the deferred legacy binder.')
    for name in ('cast_spell_illumination_spell','begin_combat_turn','complete_srd_rest'):
        if 'lite_flame_' in json.dumps(runtime['tools'][name]):
            raise ValueError('Omit deferred legacy Produce Flame shared hooks before composing the SRD pivot.')
    return {'owned_rows':result,'required_tools':sorted(REQUIRED_TOOLS),
        'mechanics_sha256':hashlib.sha256(json.dumps(result,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        'reuses_existing_srd':True,'selection_boundary_owned_by_main':True,
        'fire_storm_and_meteor_targeting':'retained SRD declared area recipients',
        'native_acceptance_requires_separate_evidence':True}

def bind_owned_srd_call_guard(runtime):
    """Reject an invalid Meteor origin before the bounded private call validates it.

    The public input permits distances over one mile. Its private composite
    owner caps this argument at 5280; a private parameter failure becomes a
    generic failed action. A source-owned root requirement gives the normal
    atomic mechanics rejection without changing any other spell or geometry.
    """
    guard={'require':{'condition':"spell != 'meteor_swarm' or origin_distance <= 5280",
        'error':'Meteor Swarm origins must be within one mile (5280 feet).'}}
    changed=[]
    for name in ('cast_spell','cast_spell_outside_combat'):
        effects=runtime['tools'][name]['effects']
        if guard not in effects:effects.insert(0,deepcopy(guard));changed.append(name)
    return changed


def bind_owned_srd_sunburst_cover_ids(runtime):
    """Keep scene identities scalar when a scene ID also names an actor.

    Expression parameters matching an entity ID auto-resolve to its body.
    Carry this private caller's scene ID in a one-element array so its original
    source-frame equality, path exclusion and categorical cover checks retain
    their meaning. No evaluator, physical predicate or budget is relaxed.
    """
    declaration='param_scope65_sunburst_source_identity'
    runtime['parameter_declarations'][declaration]={'name':'source_identity','type':'array',
        'items':{'type':'string'},'min_items':1,'max_items':1}
    qualifier=runtime['tools']['qualify_srd_sunburst_target_cover']
    if declaration in qualifier['parameters']:return []
    qualifier['parameters'].append(declaration)
    def replace(value):
        if isinstance(value,dict):return {k:replace(v) for k,v in value.items()}
        if isinstance(value,list):return [replace(v) for v in value]
        if isinstance(value,str):
            import re
            return re.sub(r'\bsource_id\b','source_identity[0]',value)
        return value
    qualifier['pre_conditions']=replace(qualifier['pre_conditions'])
    qualifier['effects']=replace(qualifier['effects'])
    count=0
    def callers(value):
        nonlocal count
        if isinstance(value,dict):
            for child in value.values():callers(child)
        elif isinstance(value,list):
            index=0
            while index<len(value):
                child=value[index]
                if isinstance(child,dict) and child.get('call')=='qualify_srd_sunburst_target_cover':
                    # Nested-call templates resolve at the top level only.
                    # Build the scalar array through ordinary recursive DSL
                    # expressions, then forward that complete private value.
                    child['params']['source_identity']='{_sunburst_cover_identity}'
                    value.insert(index,{'set':{'field':'_sunburst_cover_identity',
                        'value':['_breath_source_scene_id']}})
                    count+=1;index+=1
                callers(child);index+=1
    callers(runtime['tools']['collect_srd_sunburst_cover_targets']['effects'])
    assert count==2
    return ['qualify_srd_sunburst_target_cover','collect_srd_sunburst_cover_targets',declaration]
