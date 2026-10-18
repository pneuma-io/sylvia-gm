"""Generate the retained Insect Plague zone mechanics.

Historical source URLs and their research date are retained verbatim in
historical/spell-research/srd_lite_insect_zone.py.
"""

from copy import deepcopy
from .srd_lite_profiles import lists


def bind_insect_zone(runtime):
    profile = runtime['definitions']['spells']['insect_plague']
    profile.update(range_feet=60, radius_feet=20, radius_per_slot_level=0,
                   duration_rounds=10)
    runtime['definitions']['spell_casting']['insect_plague'].update(
        components=['verbal', 'somatic'], material='', material_parts=[],
        material_has_cost=False, material_consumed=False, focus_substitution=False,
        range='60 feet', duration='Concentration, up to 10 turns')
    ending = runtime['tools']['resolve_srd_environment_hazard_end_turn']
    matches = [effect for effects in lists(ending['effects']) for effect in effects
               if effect.get('call') == 'resolve_srd_environment_sphere_event'
               and "_environment_area.mode == 'insects'" in effect.get('condition', '')]
    assert len(matches) == 1, 'Expected the actual saved sphere end-turn owner'
    old = matches[0]
    start = deepcopy(old)
    start['condition'] = ("_environment_area.spell == 'insect_plague' and "
                          "_environment_area.mode == 'insects' and "
                          "combat.turn_active == true and combat.active_actor == _target_state_id")
    old['condition'] = "(" + old['condition'] + ") and _environment_area.spell != 'insect_plague'"
    starting = runtime['tools']['resolve_srd_environment_hazard_start_turn']
    loops = [e['for_each'] for e in starting['effects'] if 'for_each' in e
             and e['for_each'].get('as') == '_environment_id']
    assert len(loops) == 1
    branches = [e['on_true'] for e in loops[0]['do'] if 'on_true' in e
                and e.get('condition', '').startswith("_environment_area.profile == 'environment'")]
    assert len(branches) == 1
    branches[0].append(start)
