"""Keep neutral starting mechanics alongside variant spell admission."""
from copy import deepcopy
import hashlib

from sylvia_gm.domain.package_library import canonical_json_bytes
from .srd_spell_grants import character_validation
from .srd_lite_repertoire import explain_repertoire_rejections, repertoire_summary

_NON_SPELL_DEFINITIONS = (
    'srd_armor', 'srd_fighting_style_grants', 'srd_fighting_styles',
    'srd_multiclass_armor_training', 'srd_starting_armor_training',
)


def compose_character_validation(runtime, retained):
    """Retain the exact approved equipment/resource block by semantic boundary."""
    effects = retained['effects']
    starts = [index for index, effect in enumerate(effects)
        if effect.get('set') == {'field': '_style_records', 'value': []}]
    if len(starts) != 1:
        raise ValueError('The retained nonspell starting-validation boundary changed')
    fragment = effects[starts[0]:]
    if hashlib.sha256(canonical_json_bytes(fragment)).hexdigest() != '88b4c412f22a1b0266fb90e7be16b391f82f65211cb710bf8115d2a944798f21':
        raise ValueError('Review the changed retained nonspell starting-validation block')
    definitions = {name: retained['definitions'][name] for name in _NON_SPELL_DEFINITIONS}
    if hashlib.sha256(canonical_json_bytes(definitions)).hexdigest() != 'bc483f4c6dc98ad5f1266b46e22c38e50ab8536e71a0abfc8efc1de978001a40':
        raise ValueError('Review the changed retained starting-equipment definitions')
    program = character_validation(runtime)
    for name, value in definitions.items():
        if name in program['definitions'] and program['definitions'][name] != value:
            raise ValueError('Conflicting starting-equipment definition: ' + name)
        program['definitions'][name] = deepcopy(value)
    program['effects'] += deepcopy(fragment)
    program['description'] += ' Preserve validated starting equipment, Fighting Styles, Focus and Wholeness of Body.'
    program['description'] += '\n' + repertoire_summary(runtime)
    explain_repertoire_rejections(program, runtime)
    return program
