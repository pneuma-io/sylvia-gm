"""Fly's real selected recipients must be physically reachable before payment.

Reuse the one neutral admitted-scene reader. Original source, willing/count,
components, Action, slot, upcast, effect and concentration owners remain exact.
"""
from copy import deepcopy

from .srd_revised_touch_geometry import HELPER, bind_revised_srd_touch_geometry
from .srd_timed_actor_defenses import _call, _require, _set, _when
from .srd_timed_senses import _loop


def fly_touch_prelude():
    # This exactly follows the retained mobility dispatcher's list precedence.
    # A non-null explicit list wins; only a null list uses the scalar target.
    return _when("spell == 'fly'", [
        _set('_revised_fly_touch_targets', 'targets'),
        _when('targets == null', [
            _require('target != null', 'Fly needs a selected recipient.'),
            _set('_revised_fly_touch_targets', ['_target_id']),
        ]),
        _require('len(_revised_fly_touch_targets) > 0', 'Fly needs a selected recipient.'),
        _loop('_revised_fly_touch_targets', '_revised_fly_touch_target', [
            _call(HELPER, {
                'caster_state': '{_caster_state_id}',
                'target_state': '{_revised_fly_touch_target}',
                'lasting_state': '{_lasting_state_id}',
            }),
        ]),
    ])


def bind_revised_srd_fly_touch(runtime):
    """Guard initial Action start and every canonical cast completion.

    The existing public pending completion calls cast_spell with its saved real
    target selection, so the same reader checks the current scene again. Read
    before materials and budget payment; never treat supplied range/Touch flags
    as scene authority. Other profiles take the unchanged original path.
    """
    definition = runtime['definitions']['spells']['fly']
    assert definition['family'] == 'multi_target_timed_actor_mobility'
    assert definition['range_kind'] == 'touch' and definition['base_targets'] == 1
    assert definition['targets_per_slot_level'] == 1
    added = bind_revised_srd_touch_geometry(runtime)
    changed = []
    prelude = fly_touch_prelude()
    for name in ('cast_spell', 'begin_srd_spell_action'):
        effects = runtime['tools'][name]['effects']
        existing = [effect for effect in effects if effect.get('condition') == "spell == 'fly'"
                    and HELPER in str(effect)]
        if existing:
            assert existing == [prelude], 'An incompatible Fly Touch prelude is already installed.'
            continue
        anchors = [index for index, effect in enumerate(effects)
                   if effect.get('call') == 'resolve_srd_spell_materials']
        assert len(anchors) == 1, 'Use the actual original prepayment material seam.'
        effects.insert(anchors[0], deepcopy(prelude))
        changed.append(name)
    return (*added, *changed)
