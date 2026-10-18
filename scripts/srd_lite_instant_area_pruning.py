"""Omit immediate-area branches whose spell is absent from Lite's closed catalogue."""
from copy import deepcopy
import re


def omit_unselected_instant_area_branches(runtime):
    """Keep the exact selected damage path while dropping false excluded-spell arms."""
    selected = set(runtime['definitions']['spells']) | set(runtime['definitions']['srd_lite_aliases'])
    excluded = {'freezing_sphere', 'prismatic_spray', 'shatter', 'thunderwave'} - selected
    owner = runtime['tools']['cast_spell_instantaneous_area_save_damage']
    before = deepcopy(owner)
    omitted = []

    def prune(effects):
        retained = []
        for original in effects:
            effect = deepcopy(original)
            condition = effect.get('condition', '')
            match = re.fullmatch(r"spell == '([^']+)'(?: and .*)?", condition) if isinstance(condition, str) else None
            if match and match.group(1) in excluded and 'on_true' in effect:
                omitted.append({'spell': match.group(1), 'condition': condition})
                retained.extend(prune(effect.get('on_false', [])))
                continue
            for branch in ('on_true', 'on_false'):
                if branch in effect:
                    effect[branch] = prune(effect[branch])
            if 'for_each' in effect:
                effect['for_each']['do'] = prune(effect['for_each']['do'])
            retained.append(effect)
        return retained

    owner['effects'] = prune(owner['effects'])
    if {key: value for key, value in owner.items() if key != 'effects'} != {
            key: value for key, value in before.items() if key != 'effects'}:
        raise ValueError('Area pruning may only remove unreachable effect arms.')
    if not omitted or any(row['spell'] in selected for row in omitted):
        raise ValueError('Only excluded spell branches may be omitted.')
    return omitted
