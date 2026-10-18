"""Reuse original SRD Light; expose its existing cover and scoped dismissal owners."""
LIGHT_OPERATIONS = {'set_srd_illumination_cover', 'dismiss_srd_illumination_spell'}


def expose_srd65_light(runtime, denied):
    """Remove only the ordinary pending wrapper; retain original effects and consumers."""
    for name in LIGHT_OPERATIONS:
        if name not in denied:
            # Preserve a broader exposure already reviewed by the integration owner.
            continue
        tool = runtime['tools'][name]
        guard = tool['effects'].pop(0)
        assert guard == {'require': {'condition': 'false', 'error': 'This spell operation is pending or excluded in the SRD Lite foundation.'}}
        if name == 'dismiss_srd_illumination_spell':
            tool['pre_conditions'].insert(0, {
                'condition': "spell == 'light'",
                'error': 'This revised spell operation currently supports Light.'})
        denied.remove(name)
        tool['metadata'].pop('visible_to_player', None)
