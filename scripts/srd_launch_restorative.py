"""The approved 96-spell launch's restorative contribution reuses SRD owners.

Do not bind the preserved legacy restorative adapters on this launch path. These
identities, class lists, material components and earned grants already exist in
the accepted full SRD package. Main owns the combined selection and boundary.
"""
OWNED = frozenset({
    'guidance', 'aid', 'lesser_restoration', 'mass_healing_word',
    'protection_from_energy', 'death_ward', 'freedom_of_movement',
    'greater_restoration', 'mass_cure_wounds', 'heal',
    'aura_of_life', 'beacon_of_hope', 'blur', 'power_word_heal',
    'protection_from_evil_and_good', 'revivify', 'shield_of_faith',
})
MASS_CURE_AREA_GUARD = {
    'condition': "spell != 'mass_cure_wounds' or all_targets_in_area == true",
    'error': 'Mass Cure Wounds requires every recipient inside its selected 30-foot Sphere.',
}


def verify_existing_restorative_owners(runtime):
    """Return existing profiles for main's selection; fail on missing SRD owners."""
    profiles = runtime['definitions']['spells']
    headers = runtime['definitions']['spell_casting']
    missing = sorted(OWNED - profiles.keys() | OWNED - headers.keys())
    if missing:
        raise ValueError(f'Missing existing restorative SRD contracts: {missing}')
    return {name: {'profile': profiles[name], 'casting_requirements': headers[name]}
            for name in sorted(OWNED)}


def bind_restorative_launch_repairs(runtime):
    """Keep bound healing IDs and stage the retained source's initial area guard.

Both receivers resolve their ``target`` parameter to an entity. Sending
``{target}`` to another entity parameter therefore sends an object, which the
normal parameter boundary correctly rejects. Preserve the bound ``_target_id``.
    The selected Mass Cure scene binder replaces this initial reported-area
    guard with actual scene validation after these receiving repairs.
    No profile, class list, resource policy, healing value or condition changes.
"""
    verify_existing_restorative_owners(runtime)
    changed=[]
    for name in ('restore_shared_spell_healing_target','restore_srd_healing_pool_target'):
        tool=runtime['tools'][name]
        def visit(value):
            if isinstance(value,dict):
                if value.get('call')=='observe_srd_astral_receiving':
                    params=value['params']
                    if params.get('target')=='{target}':
                        params['target']='{_target_id}'
                        changed.append(name)
                    elif params.get('target')!='{_target_id}':
                        raise ValueError(f'Unexpected receiving target binding in {name}')
                for child in tuple(value.values()): visit(child)
            elif isinstance(value,list):
                for child in value: visit(child)
        visit(tool['effects'])
    cast=runtime['tools']['cast_spell']
    if not any(row.get('name')=='all_targets_in_area' for row in cast['parameters']):
        raise ValueError('The existing public casting contract has no area fact')
    guards=cast.setdefault('pre_conditions',[])
    if MASS_CURE_AREA_GUARD not in guards:
        guards.append(dict(MASS_CURE_AREA_GUARD))
        changed.append('cast_spell')
    changed.extend(bind_launch_duration_cleanup(runtime))
    return sorted(set(changed))


"""Scoped duration cleanup to append after group06 evidence is frozen.

This uses the existing indexed projection lifecycle and normal concentration
unlinker. It never chooses a stale record's caster/target on behalf of a recast.
"""
from copy import deepcopy

LAUNCH_DURATION_NAMES=('aura_of_life','beacon_of_hope','blur','protection_from_evil_and_good','shield_of_faith')
LAUNCH_DURATION_HELPER='refresh_launch_restorative_duration'


def bind_launch_duration_cleanup(runtime):
    tools=runtime['tools']
    caster_names=' or '.join(f"caster_state.concentration_spell_id == '{name}'" for name in LAUNCH_DURATION_NAMES)
    cast_names='('+' or '.join(f"spell == '{name}'" for name in LAUNCH_DURATION_NAMES)+')'
    helper={
        'description':'Remove actually expired retained concentration after a public lasting-time advance.',
        'parameters':deepcopy(tools['refresh_srd_condition_caster']['parameters']),
        'effects':[{'condition':caster_names,'on_true':[{
            'call':'end_srd_concentration_node',
            'params':{'source_state':'{_caster_state_id}',
                      'target_state':'{caster_state.concentration_target_state_id}'},
            'condition':'caster_state.concentration_expires_tick != null and lasting_state.lasting_tick != null and lasting_state.lasting_tick + 0.000000001 >= caster_state.concentration_expires_tick',
        }]}],
        'metadata':{'category':'internal','visible_to_player':False,'costs_action':False},
    }
    changed=[]
    if LAUNCH_DURATION_HELPER in tools:
        if tools[LAUNCH_DURATION_HELPER]!=helper:
            raise ValueError('Unexpected preexisting restorative duration helper')
    else:
        tools[LAUNCH_DURATION_HELPER]=helper
        changed.append(LAUNCH_DURATION_HELPER)

    # Reuse the package's bounded, saved projection target index. Registering
    # the caster also handles a touch/distance spell cast on another creature.
    register={
        'call':'register_srd_actor_spell_projection_target',
        'params':{'target_state':'{_caster_state_id}','lasting_state':'{_lasting_state_id}'},
        'condition':f'_spell_cast_failed != true and _spell_hit_window_deferred != true and {cast_names}',
    }
    effects=tools['cast_spell']['effects']
    if register not in effects:
        effects.append(register)
        changed.append('cast_spell')

    # The concentration unlinker already calls the projection procedure. Hook
    # the public time advance instead, preserving the required static DAG.
    duration_cleanup=[
        {'set':{'field':'_launch_duration_casters','value':[]}},
        {'set':{'field':'_launch_duration_casters',
                'value':'lasting_state.actor_spell_projection_target_ids'},
         'condition':'lasting_state.actor_spell_projection_target_ids != null'},
        {'for_each':{'collection':'_launch_duration_casters','as':'_launch_duration_caster',
          'do':[{'call':LAUNCH_DURATION_HELPER,
                 'params':{'caster_state':'{_launch_duration_caster}',
                           'lasting_state':'{_lasting_state_id}'}}]}},
    ]
    effects=tools['advance_srd_lasting_time']['effects']
    existing=[row for row in effects
              if row.get('for_each',{}).get('collection')=='_launch_duration_casters']
    if existing and existing!=[duration_cleanup[-1]]:
        raise ValueError('Unexpected preexisting restorative duration loop')
    if not existing:
        effects.extend(duration_cleanup)
        changed.append('advance_srd_lasting_time')
    return changed
