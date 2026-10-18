"""Qualify only original SRD Longstrider's actual Touch recipients before costs.

All casting, preparation, slots, upcast counts, timed source rows, speed budgets,
cards, removal and hour expiry retain their existing original owners.
"""
from copy import deepcopy

from .srd_revised_touch_geometry import HELPER, bind_revised_srd_touch_geometry
from .srd_timed_actor_defenses import _call, _require, _set, _when
from .srd_timed_senses import _loop

MARKER = '_revised_longstrider_touch_targets'
OWNED = ('cast_spell', 'begin_srd_spell_action')


def longstrider_touch_prelude():
    """Keep the real speed-family explicit target list; never use its scalar."""
    return [_when("spell == 'longstrider'", [
        _require('targets != null and len(targets) >= 1',
            'Longstrider needs at least one actual selected creature.'),
        _require('caster_can_touch_all_targets == true',
            'The caster must be able to touch every selected creature.'),
        _set(MARKER, 'targets'),
        _loop(MARKER, '_revised_longstrider_touch_target', [
            _call(HELPER, {'caster_state':'{_caster_state_id}',
                'target_state':'{_revised_longstrider_touch_target}',
                'lasting_state':'{_lasting_state_id}'}),
        ]),
    ])]


def _nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _nodes(child)


def bind_revised_longstrider_touch(runtime):
    """Install a shared read-only Touch owner and two Longstrider-only guards."""
    profile = runtime['definitions']['spells']['longstrider']
    assert (profile['family'],profile['range_kind'],profile['duration_rounds'],
        profile['speed_bonus'],profile['base_targets'],profile['targets_per_slot_level']) == (
        'multi_target_timed_speed_bonus','touch',600,10,1,1), 'Retain the original hour-duration Longstrider.'
    tools = runtime['tools']
    prelude = longstrider_touch_prelude()
    needed = []
    # Validate both source seams before installing or mutating either one.
    # Another spell's use of the same neutral owner remains compatible.
    for name in OWNED:
        effects = tools[name]['effects']
        if effects[:len(prelude)] == prelude:
            continue
        assert not any(n.get('set',{}).get('field') == MARKER for n in _nodes(effects)), \
            'Longstrider physical Touch prelude placement drift: ' + name
        needed.append(name)
    helper = bind_revised_srd_touch_geometry(runtime)
    for name in needed:
        tools[name]['effects'][:0] = deepcopy(prelude)
    return tuple(helper) + tuple(needed)
