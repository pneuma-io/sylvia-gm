"""Repair the real attack visibility owner for retained SRD environments.

This module changes no spell selection, saved source, profile, cost or lifetime.
Main applies it to its current selected union before normal boundary admission.
"""
from copy import deepcopy

OBSCURING_ENVIRONMENTS = frozenset({'fog_cloud','sleet_storm','stinking_cloud'})
OLD = 'lasting_state.has_illumination_spells == true'
NEW = OLD + ' or lasting_state.has_spell_environments == true'

def bind_environment_attack_visibility(runtime, selected):
    """Refresh both saved scene sight directions without requiring an unrelated light."""
    if not OBSCURING_ENVIRONMENTS.intersection(selected):
        return False
    owner=runtime['tools']['resolve_srd_attack_conditions']
    matches=[e for e in owner['effects'] if e.get('condition') in (OLD,NEW)
             and len([x for x in e.get('on_true',[]) if x.get('call')=='resolve_srd_scene_relation'])==2]
    if len(matches)!=1:
        raise ValueError('Actual bidirectional attack scene-visibility owner changed')
    effect=matches[0]
    expected=[{'call':'resolve_srd_scene_relation','params':{'actor_state':'{_actor_state_id}','target_state':'{_target_state_id}'},'export_context':{'_scene_can_see':'_scene_can_see'}},
      {'set':{'field':'_illumination_actor_can_see_target','value':'_scene_can_see'}},
      {'call':'resolve_srd_scene_relation','params':{'actor_state':'{_target_state_id}','target_state':'{_actor_state_id}'},'export_context':{'_scene_can_see':'_scene_can_see'}},
      {'set':{'field':'_illumination_target_can_see_actor','value':'_scene_can_see'}}]
    if effect.get('on_true')!=expected:
        raise ValueError('Attack sight must retain the actual two scene relations and consumers')
    if effect['condition']==NEW:
        return False
    effect['condition']=NEW
    return True
