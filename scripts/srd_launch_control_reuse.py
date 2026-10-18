"""Approved exclusive-five identities reuse the existing SRD producers.

The launch builder owns the complete selected union and its roster boundary.
These aliases are catalogue metadata; native spell/result-card names remain
unchanged. No legacy status adapter or duplicate producer is installed here.
"""
DISPLAY_TO_CANONICAL = {
    "Blindness": "blindness_deafness",
    "Hold Monster": "hold_monster",
    "Otto’s Irresistible Dance": "irresistible_dance",
    "Power Word Stun": "power_word_stun",
    "Power Word Kill": "power_word_kill",
}
PROFILE_IDS = frozenset(DISPLAY_TO_CANONICAL.values())
EXPECTED_FAMILIES = {
    "blindness_deafness": "multi_target_timed_repeating_save_condition",
    "hold_monster": "multi_target_timed_repeating_save_condition",
    "irresistible_dance": "timed_control_behavior",
    "power_word_stun": "threshold_timed_condition",
    "power_word_kill": "instantaneous_threshold_death_damage",
}


def validate_reused_profiles(runtime):
    """Fail before build if an assigned identity or its real producer is missing."""
    profiles = runtime["definitions"]["spells"]
    headers = runtime["definitions"]["spell_casting"]
    for identity, family in EXPECTED_FAMILIES.items():
        if identity not in headers or profiles.get(identity, {}).get("family") != family:
            raise ValueError(f"Existing SRD profile is missing or incompatible: {identity}")
    return PROFILE_IDS
