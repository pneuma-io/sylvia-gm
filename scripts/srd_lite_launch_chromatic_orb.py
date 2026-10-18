"""Draft launch reuse: retain the accepted SRD Orb and its paid leap owners.

Copy to scripts/srd_lite_launch_chromatic_orb.py after parent review. This binder
adds no class grants, damage owners, materials or surfaces.
Call before boundary/policy binding; Main alone selects the approved union.
The scoped private aliases repair actual admitted-scene facts in the retained
choice; public inputs and original fallback GM-fact behavior remain intact.
"""
from copy import deepcopy
import hashlib
import json
import re

IDENTITY = 'chromatic_orb'
EXPECTED_CAPTURE_SHA256 = 'b839b40180681e921603096491ee9488df4005562aaebb197407f1a35ee6f39e'
EXPECTED_CHOICE_SHA256 = 'c860af34c87d6307a96d84cc4abcead388acf8c2a0ef06dbec3068f70c548154'
PUBLIC_OPERATIONS = frozenset({'choose_srd_chromatic_orb_leap'})
RETAINED_OWNERS = frozenset({
    'cast_spell', 'cast_spell_outside_combat', 'cast_spell_attack_damage',
    'retain_srd_chromatic_orb_choice', 'resolve_srd_chromatic_orb_leap',
    'complete_srd_chromatic_orb_attack', 'complete_srd_spell_attack_hit',
    'complete_srd_pending_action', 'choose_srd_chromatic_orb_leap',
    'begin_srd_spell_source_instance', 'resolve_srd_spell_materials',
})
CHOICE_FACTS = (
    'jump_distance', 'target_distance', 'cover', 'caster_can_see_target',
    'target_can_see_caster', 'enemy_within_5_feet', 'close_enemy_can_see_caster',
    'close_enemy_incapacitated',
)


def _bind_choice_facts(runtime):
    """Route actual scene results to private variables accepted by native Set.

Native Set writes only '_' fields into ephemeral context. The retained choice's
unprefixed facts are public immutable parameters, so writing those fields cannot
update the computed hop/range/cover. Keep the existing scene and attack owners.
"""
    choice = deepcopy(runtime['tools']['choose_srd_chromatic_orb_leap'])
    aliases = {name: '_launch_orb_' + name for name in CHOICE_FACTS}
    token = re.compile(r'(?<![\w.])(' + '|'.join(CHOICE_FACTS) + r')(?!\w)')

    def rewrite(value, key=None, *, inverse=False):
        if isinstance(value, dict):
            return {name: rewrite(child, name, inverse=inverse) for name, child in value.items()}
        if isinstance(value, list):
            return [rewrite(child, key, inverse=inverse) for child in value]
        if isinstance(value, str) and key not in {'error', 'description'}:
            if inverse:
                for public, private in aliases.items():
                    value = re.sub(r'(?<![\w.])' + private + r'(?!\w)', public, value)
                return value
            return token.sub(lambda match: aliases[match[0]], value)
        return value

    initialization = [
        {'set': {'field': aliases[name], 'value': name}} for name in CHOICE_FACTS
    ]
    if any('_launch_orb_' in str(effect) for effect in choice['effects']):
        assert choice['effects'][:len(initialization)] == initialization, 'Partial Orb alias correction.'
        restored = deepcopy(choice)
        restored['effects'] = rewrite(choice['effects'][len(initialization):], inverse=True)
        assert _digest(restored) == EXPECTED_CHOICE_SHA256, 'Corrected Orb owner drift; inspect before merging.'
        return aliases
    assert _digest(choice) == EXPECTED_CHOICE_SHA256, 'Original Orb owner drift; inspect before merging.'
    choice['effects'] = initialization + rewrite(choice['effects'])
    runtime['tools']['choose_srd_chromatic_orb_leap'] = choice
    return aliases


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def bind_launch_chromatic_orb(runtime, record):
    """Validate the retained identity and return its original capture/allowance."""
    profile = runtime['definitions']['spells'][IDENTITY]
    casting = runtime['definitions']['spell_casting'][IDENTITY]
    assert _digest({'profile': profile, 'casting': casting}) == EXPECTED_CAPTURE_SHA256
    assert profile['family'] == 'spell_attack_damage'
    assert (profile['base_level'], profile['base_dice'], profile['damage_die'],
            profile['dice_per_slot_level'], profile['range_feet']) == (1, 3, 'd8', 1, 90)
    assert profile['scaling_mode'] == 'slot_level' and profile['miss_damage'] == 'none'
    assert {name.removeprefix('allows_') for name, value in profile.items()
            if name.startswith('allows_') and value} == {
        'acid', 'cold', 'fire', 'lightning', 'poison', 'thunder'}
    assert casting['classes'] == ['sorcerer', 'wizard']
    assert casting['components'] == ['verbal', 'somatic', 'material']
    assert casting['duration'] == 'Instantaneous'
    assert casting['material_has_cost'] is True and casting['material_consumed'] is False
    assert casting['focus_substitution'] is False
    assert casting['material_parts'] == [{
        'consumed': False, 'id': 'chromatic_orb.1', 'minimum_value_cp': 5000,
        'quantity': 1, 'scale': 'fixed'}]
    assert RETAINED_OWNERS <= set(runtime['tools'])
    declarations = {row['name']: row for row in record['tools']}
    assert PUBLIC_OPERATIONS <= set(declarations)
    choice = runtime['tools']['choose_srd_chromatic_orb_leap']
    assert choice['metadata']['costs_action'] is False
    assert not any(row.get('require', {}).get('condition') == 'false'
                   for row in choice['effects']), 'Apply reuse before obsolete exclusion guards.'
    aliases = _bind_choice_facts(runtime)
    # Dice, source receipts, saved hits and receiving consumers remain original.
    capture = {'profile': deepcopy(profile), 'casting': deepcopy(casting)}
    return {'identity': IDENTITY, 'capture': capture,
            'capture_sha256': _digest(capture),
            'allowed_operations': sorted(PUBLIC_OPERATIONS), 'choice_fact_aliases': aliases}
