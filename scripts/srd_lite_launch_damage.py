"""The nine retained launch damage spells reuse their existing complete SRD owners.

This module records the exclusive contribution boundary. It changes no spell dice,
components, geometry, acquisition, slot progression, concentration, or expiry rule.
The caller must invoke it on the accepted SRD runtime before historical legacy adapters.
Main alone decides the current ninety-six-name union and its acquisition choices.
"""
from copy import deepcopy
from .srd_lite_launch_chain_range import bind_srd_chain_selected_range
from .srd_lite_profiles import lists

LAUNCH_DAMAGE = frozenset({
    'acid_splash', 'lightning_bolt', 'blight', 'cone_of_cold', 'flame_strike',
    'chromatic_orb', 'circle_of_death', 'ray_of_sickness', 'dragons_breath',
})

# Preserve their source and historical proofs; the historical launch cut removes
# only current launch selection/priority. Do not start deferred-only tests.
DEFERRED_DAMAGE = frozenset({
    'acid_arrow', 'chain_lightning', 'disintegrate', 'harm', 'shatter', 'thunderwave',
})

# Existing provider-selected peers are only isolated qualification dependencies.
# They are not this contribution's assignment or the revised final launch roster.
QUALIFICATION_PEERS = frozenset({
    'bless', 'true_strike', 'cloud_of_daggers', 'cure_wounds', 'healing_word',
    'magic_missile', 'inflict_wounds', 'guiding_bolt', 'scorching_ray',
    'longstrider', 'jump', 'light', 'blade_ward',
    # These unchanged, retained launch identities supply complete low-Wizard
    # test choices. They remain qualification peers, never this worker's batch.
    'burning_hands', 'shield', 'mage_armor', 'misty_step',
})


def bind_launch_damage(runtime):
    """Retain complete original profiles, rejecting an absent or substituted identity."""
    definitions = runtime['definitions']
    captured = {}
    for identity in sorted(LAUNCH_DAMAGE):
        profile = definitions['spells'][identity]
        header = definitions['spell_casting'][identity]
        assert header['level'] >= 0
        assert profile['family'] in runtime['definition_row_schemas']['definitions.spells']['variants']
        captured[identity] = {'profile': deepcopy(profile), 'casting': deepcopy(header)}
    # No synthesized grants or sparse substitute class tables are installed here.
    assert set(captured) == LAUNCH_DAMAGE
    if 'chain_lightning' in LAUNCH_DAMAGE:
        bind_srd_chain_selected_range(runtime)
    # A newly prepared NPC starts without Dodge. The existing DEX consumer needs
    # this Boolean; preserve any newer producer expression/default from Main.
    for group in lists(runtime['tools']['prepare_srd_creature']['effects']):
        for effect in group:
            value = effect.get('create_entity', {}).get('value')
            if isinstance(value, dict) and value.get('creature_kind') == "'monster'":
                value.setdefault('dodging', False)
    return captured
