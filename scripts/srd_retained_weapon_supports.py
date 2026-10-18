"""Compose scoped raw/resolved owners without replacing the current builder.

Copy this and the two final support modules to the builder's equivalent module
directory. Public schema additions must be emitted by its normal declaration
builder after composition. This function changes no catalogue or Core limits.
"""
from sylvia_gm.runtime._parameter_declarations import resolve_parameter_declarations
if __package__:
    from .srd_fire_shield_support import bind_srd_fire_shield_support
    from .srd_original_unarmed_smite import bind_original_unarmed_smite
else:
    from srd_fire_shield_support import bind_srd_fire_shield_support
    from srd_original_unarmed_smite import bind_original_unarmed_smite


def bind_retained_weapon_supports(runtime, selected):
    selected=set(selected)
    changed=set()
    if 'fire_shield' in selected:
        assert 'parameter_declarations' in runtime, 'Fire Shield binds at the original raw parameter-pool stage.'
        changed.update(bind_srd_fire_shield_support(runtime))
    if 'divine_smite' in selected:
        # The Smite binder consumes resolved formal parameter descriptors. Copy
        # only its returned owners back; keep unrelated raw fragments and pool.
        view=resolve_parameter_declarations(runtime)
        smite=bind_original_unarmed_smite(view,selected=selected)
        for name in smite:runtime['tools'][name]=view['tools'][name]
        changed.update(smite)
    return sorted(changed)
