"""Preserve the accepted SRD Foresight profile and existing consumers unchanged.

No legacy parity adapter, new public declaration, or progression policy is added.
Call before selection pruning so the original source can be checked precisely.
"""
EXPECTED_PROFILE = {
    'base_level': 9, 'base_targets': 1, 'casting_time': '1_minute',
    'concentration': False, 'duration_rounds': 4800,
    'family': 'timed_actor_modifier', 'profile': 'foresight',
    'range_feet': 0, 'range_kind': 'touch', 'required_creature_type': 'any',
    'requires_willing': True, 'save_ability': 'none', 'targets_per_slot_level': 0,
}
EXPECTED_HEADER = {
    'casting_time': '1 minute', 'classes': ['bard', 'druid', 'warlock', 'wizard'],
    'components': ['verbal', 'somatic', 'material'], 'duration': '8 hours',
    'focus_substitution': True, 'level': 9, 'material': 'a hummingbird feather',
    'material_consumed': False, 'material_has_cost': False,
    'material_parts': [{'consumed': False, 'id': 'foresight.1',
        'minimum_value_cp': 0, 'quantity': 1, 'scale': 'fixed'}],
    'name': 'Foresight', 'range': 'Touch', 'ritual': False, 'school': 'divination',
}
REQUIRED_OWNERS = (
    'cast_spell', 'cast_spell_outside_combat', 'begin_srd_spell_cast',
    'continue_srd_spell_cast', 'cast_spell_timed_actor_modifier',
    'install_srd_actor_spell_modifier', 'check_srd_actor_spell_modifier_active',
    'refresh_srd_actor_spell_projections', 'resolve_srd_actor_spell_modifiers',
    'read_srd_actor_foresight', 'd20_test', 'ability_check',
    'death_saving_throw', 'cast_spell_attack_damage',
    'resolve_srd_attack_conditions', 'start_combat_encounter_entities',
    'roll_srd_actor_group_initiative', 'advance_srd_lasting_time',
)


def preserve_foresight(runtime):
    """Assert unchanged source ownership without replacing shared tools or schemas."""
    definitions = runtime['definitions']
    if definitions['spells'].get('foresight') != EXPECTED_PROFILE:
        raise ValueError('The revised launch must reuse the accepted SRD Foresight profile.')
    if definitions['spell_casting'].get('foresight') != EXPECTED_HEADER:
        raise ValueError('The revised launch must preserve SRD Foresight components and casting time.')
    missing = set(REQUIRED_OWNERS) - set(runtime['tools'])
    if missing:
        raise ValueError('Foresight requires its existing source consumers: ' + ', '.join(sorted(missing)))
    return {'spell': 'foresight', 'mechanics': 'original_srd_5_2_1',
        'new_runtime_nodes': 0, 'source_preserved': True}
