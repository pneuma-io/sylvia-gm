"""Foresight's current completion recipient must be physically reachable."""
from .srd_revised_touch_geometry import HELPER,bind_revised_srd_touch_geometry
from .srd_timed_actor_defenses import _call,_require,_set,_when


def foresight_touch_prelude():
    # The original actor-modifier dispatcher uses a non-null explicit list
    # before its scalar fallback. An empty explicit list is invalid, too.
    return [_when("spell == 'foresight'",[
        _when('targets != null',[
            _require('len(targets) == 1','Foresight needs exactly one willing recipient.'),
            _set('_foresight_touch_target','targets[0]'),
        ]),
        _when('targets == null',[
            _require('target != null','Foresight needs a selected recipient.'),
            _set('_foresight_touch_target','_target_id'),
        ]),
        _call(HELPER,{'caster_state':'{_caster_state_id}',
            'target_state':'{_foresight_touch_target}','lasting_state':'{_lasting_state_id}'}),
    ])]


def bind_revised_srd_foresight_touch(runtime):
    """Qualify before canonical completion writes; preserve all original owners.

    Longer-casting start has no target parameters and intentionally chooses its
    recipient at completion. The Action pending owner does not admit a one-minute
    Foresight. Both keep their original timing and capability boundaries.
    """
    definition=runtime['definitions']['spells'].get('foresight')
    assert definition['family']=='timed_actor_modifier' and definition['range_kind']=='touch'
    assert definition['base_targets']==1 and definition['targets_per_slot_level']==0
    added=bind_revised_srd_touch_geometry(runtime)
    effects=runtime['tools']['cast_spell']['effects']
    prelude=foresight_touch_prelude()
    if any(HELPER in str(e) and "spell == 'foresight'" in str(e) for e in effects):
        assert any(e==prelude[0] for e in effects),'Foresight has an incompatible Touch prelude.'
        return added
    effects[:0]=prelude
    return (*added,'cast_spell')
