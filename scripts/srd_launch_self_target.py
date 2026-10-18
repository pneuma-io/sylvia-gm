"""Reject explicit non-caster targets for the two owned SRD Self spells."""


def bind_owned_self_targets(runtime):
    """Validate before pending reservation, casting budget, or slot spending."""
    condition = (
        "(spell != 'shillelagh' and spell != 'divine_favor') or "
        "(_target_id == _caster_state_id and "
        "(targets == null or len(targets) == 0 or "
        "(len(targets) == 1 and targets[0] == _caster_state_id)))"
    )
    changed = []
    for name in ('cast_spell', 'cast_spell_outside_combat', 'begin_srd_spell_action'):
        tool = runtime['tools'][name]
        pre_conditions = tool.setdefault('pre_conditions', [])
        if any(row.get('condition') == condition for row in pre_conditions):
            continue
        pre_conditions.append({
            'condition': condition,
            'error': 'Shillelagh and Divine Favor affect the actual caster only.',
        })
        changed.append(name)
    return changed
