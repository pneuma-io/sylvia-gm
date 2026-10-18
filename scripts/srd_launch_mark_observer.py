"""Preserve SRD marks when a genuine coalesced saved hit drops their target."""


def _effect_lists(effects):
    yield effects
    for node in effects:
        for field in ('on_true', 'on_false'):
            if isinstance(node.get(field), list):
                yield from _effect_lists(node[field])
        if 'for_each' in node:
            yield from _effect_lists(node['for_each']['do'])


def bind_srd_saved_hit_mark_observer(runtime):
    """Observe final zero HP after Fortitude and before Clone replaces the body."""
    changed = []
    for name in ('apply_srd_saved_hit_qualifying_damage',
                 'apply_srd_saved_hit_qualifying_composite_damage'):
        tool = runtime['tools'][name]
        sites = []
        for effects in list(_effect_lists(tool['effects'])):
            for index, node in enumerate(effects):
                if node.get('condition') == 'lasting_state.has_clone_bodies == true':
                    sites.append((effects, index))
        assert len(sites) == 4, (name, len(sites))
        modified = False
        for effects, index in reversed(sites):
            if index and effects[index-1].get('call') == 'observe_srd_attack_mark_zero':
                continue
            effects.insert(index, {
                'call': 'observe_srd_attack_mark_zero',
                'params': {'target_state': '{_target_id}'},
                'condition': 'target.hp == 0 and target.has_attack_rider_sources == true',
            })
            modified = True
        if modified:
            changed.append(name)
    return changed
