"""The reviewed SRD Lite launch catalogue and its retained source identities."""

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

VARIANT_ID = "sylvia.srd-lite.bg3"
VARIANT_VERSION = "0.3.14"
ROOT = Path(__file__).resolve().parents[1]
LITE = ROOT / "rulesets/srd-lite"
HISTORICAL_CATALOGUE = ROOT / "historical/spell-research/spell-catalogue.json"
ROSTER = ROOT / "historical/spell-research/normal-roster.txt"
EXPECTED_LEVELS = {0: 25, 1: 52, 2: 42, 3: 34, 4: 21, 5: 17, 6: 19}
HISTORICAL_SELECTED = {
    'darkvision',
    'speak_with_animals',
    'misty_step',
    'silence',
    'faerie_fire',
    'call_lightning',
    'insect_plague',
    'blade_ward',
    'bless',
    'burning_hands',
    'cloud_of_daggers',
    'cure_wounds',
    'false_life',
    'fireball',
    'guiding_bolt',
    'healing_word',
    'inflict_wounds',
    'jump',
    'longstrider',
    'mage_armor',
    'magic_missile',
    'scorching_ray',
    'shield',
    'true_strike',
}
LAUNCH_ROSTER = LITE / 'content/launch-roster.json'
SELECTED = {'acid_splash', 'aid', 'alter_self', 'arcane_eye', 'aura_of_life', 'beacon_of_hope', 'bless', 'blight', 'blur', 'burning_hands', 'charm_monster', 'chromatic_orb', 'circle_of_death', 'cloud_of_daggers', 'command', 'commune', 'cone_of_cold', 'contact_other_plane', 'counterspell', 'cure_wounds', 'darkvision', 'daylight', 'death_ward', 'dispel_magic', 'divine_smite', 'dragons_breath', 'eldritch_blast', 'etherealness', 'fear', 'find_steed', 'fire_bolt', 'fire_shield', 'fire_storm', 'fireball', 'flame_strike', 'fly', 'fog_cloud', 'foresight', 'freedom_of_movement', 'geas', 'greater_restoration', 'guardian_of_faith', 'guidance', 'guiding_bolt', 'heal', 'healing_word', 'hex', 'hold_monster', 'hold_person', 'hunters_mark', 'ice_storm', 'insect_plague', 'legend_lore', 'lesser_restoration', 'light', 'lightning_bolt', 'longstrider', 'mage_armor', 'mage_hand', 'magic_missile', 'mass_cure_wounds', 'mass_healing_word', 'mending', 'misty_step', 'poison_spray', 'polymorph', 'power_word_heal', 'power_word_kill', 'power_word_stun', 'protection_from_energy', 'protection_from_evil_and_good', 'ray_of_frost', 'ray_of_sickness', 'revivify', 'sacred_flame', 'scorching_ray', 'shield', 'shield_of_faith', 'shillelagh', 'shocking_grasp', 'sleep', 'sleet_storm', 'spare_the_dying', 'speak_with_animals', 'stinking_cloud', 'suggestion', 'summon_dragon', 'sunburst', 'thaumaturgy', 'tree_stride', 'true_strike', 'vicious_mockery', 'wall_of_fire', 'wall_of_stone', 'web', 'zone_of_truth'}


# Existing Full-authored mechanics migrated without replacing the old 96 profiles.
RETAINED_FULL_AUTHORED_SPELLS = {'finger_of_death', 'shatter', 'dissonant_whispers', 'harm', 'false_life', 'chill_touch', 'thunderwave', 'chain_lightning', 'stoneskin', 'disintegrate', 'meteor_swarm', 'bane', 'starry_wisp', 'ice_knife', 'enhance_ability', 'befuddlement', 'freezing_sphere', 'blindness_deafness', 'inflict_wounds', 'vitriolic_sphere', 'acid_arrow'}
SELECTED |= RETAINED_FULL_AUTHORED_SPELLS



def spell_id(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower().replace("'", "")).strip("_")


def build_catalogue():
    """Use the exact approved launch roster; preserve all previous catalogue evidence."""
    launch_bytes = LAUNCH_ROSTER.read_bytes()
    launch = json.loads(launch_bytes)
    assert launch['format'] == 'sylvia-srd-lite-launch-roster' and launch['count'] == 96
    identities = [row['identity'] for row in launch['profiles']]
    assert len(identities) == len(set(identities)) == 96 and 'finger_of_death' not in identities
    partition = [name for names in launch['exclusive_spell_source_owners'].values() for name in names]
    assert len(partition) == len(set(partition)) == 96 and set(partition) == set(identities)
    assert len(RETAINED_FULL_AUTHORED_SPELLS) == 21 and len(SELECTED) == 117
    assert SELECTED - RETAINED_FULL_AUTHORED_SPELLS == set(identities)
    rows = [{**row, 'profile_state':'selected_unverified' if row['identity'] in SELECTED else 'pending',
             'behavior_accepted':False} for row in launch['profiles']]
    inventory = json.loads((LITE / 'content/spell-status-inventory.json').read_text())
    retained = {row['identity']: row for row in inventory['spells']
                if row['identity'] in RETAINED_FULL_AUTHORED_SPELLS}
    assert set(retained) == RETAINED_FULL_AUTHORED_SPELLS
    for identity in sorted(RETAINED_FULL_AUTHORED_SPELLS):
        item = retained[identity]
        header = item['casting_requirements']
        rows.append({'identity': identity, 'aliases': [], 'name': header['name'],
                     'level': header['level'], 'population': 'preserved_authored_mechanics',
                     'source': item['source'], 'casting_requirements': header,
                     'profile_state': 'selected_unverified', 'behavior_accepted': False,
                     'srd_identity_present': True,
                     'migration_provenance': item['migration_provenance']})
    assert len(rows) == 117 and {row['identity'] for row in rows} == SELECTED
    for row in rows:
        if row['identity'] in {'true_strike','spare_the_dying','thaumaturgy','mending'}:
            row.update(implementation_rules_authority='wotc.srd.5.2.1.pdf',
                implementation_source_url='https://media.dndbeyond.com/compendium-images/srd/5.2/SRD_CC_v5.2.1.pdf',
                historical_210_roster_member=row['identity'] not in {'spare_the_dying','mending'})
    return {'format':'sylvia-srd-lite-catalogue', 'revision':5, 'variant_id':VARIANT_ID,
        'variant_version':VARIANT_VERSION, 'source_checked':'2026-10-09',
        'authority':launch['authority'],
        'final_roster_authority': '117 launch spells: the original 96 Lite profiles and exactly 21 preserved implementations.', 'launch_roster_sha256':hashlib.sha256(launch_bytes).hexdigest(),
        'population':'final_117_launch_roster', 'count':117,
        'level_counts':dict(Counter(row['level'] for row in rows)), 'profiles':rows,
        'historical_research': {'source_path':'historical/spell-research/spell-catalogue.json', 'sha256': hashlib.sha256(HISTORICAL_CATALOGUE.read_bytes()).hexdigest(), 'normal_roster_count':210, 'deferred_count':125},
        'deferred_scope':'Previous non-roster sources, tests and evidence are retained for later work; they are not launch requirements.',
        'acceptance':'Exact launch identity and source selection only; final-byte gameplay and complete legal acquisition remain unverified.'}


def write_catalogue():
    catalogue = build_catalogue()
    destination = LITE / "content/spell-catalogue.json"
    destination.write_text(json.dumps(catalogue, indent=2) + "\n", encoding="utf-8")
    return catalogue


if __name__ == "__main__":
    data = write_catalogue()
    print(
        json.dumps(
            {
                "count": data["count"],
                "levels": data["level_counts"],
                "matched_identities": sum(r["srd_identity_present"] for r in data["profiles"]),
                "behavior_accepted": sum(r["behavior_accepted"] for r in data["profiles"]),
            }
        )
    )
