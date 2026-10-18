"""The reviewed normal BG3 names and honest SRD Lite development states."""

import csv
import hashlib
import io
import json
import re
from collections import Counter
from pathlib import Path

VARIANT_ID = "sylvia.srd-lite.bg3"
VARIANT_VERSION = "0.2.0"
ROOT = Path(__file__).resolve().parents[1]
LITE = ROOT / "rulesets/srd-lite-bg3"
ROSTER = LITE / "content/normal-roster.txt"
CATALOGUE_URL = "https://bg3.wiki/wiki/List_of_all_spells"
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


def _build_legacy_catalogue():
    """Name/alias matches describe identity only; selected profiles remain unverified."""
    declaration = (ROOT / "rulesets/srd-lite-bg3/content/spell-mechanics.yaml").read_text()
    existing = set(re.findall(r"^  ([a-z][a-z0-9_]+):\s*$", declaration, re.M))
    crosswalk_bytes = (LITE / "content/roster-crosswalk.csv").read_bytes()
    crosswalk = list(csv.DictReader(io.StringIO(crosswalk_bytes.decode("utf-8"))))
    assert len(crosswalk) == 210
    by_name = {row["name"]: row for row in crosswalk}
    rows = []
    for line in ROSTER.read_text().splitlines():
        level, name = line.split(" | ", 1)
        evidence = by_name[name]
        assert int(evidence["level"]) == int(level)
        identity = evidence["sylvia_id"] or spell_id(name)
        rows.append(
            {
                "name": name,
                "level": int(level),
                "identity": identity,
                "aliases": [spell_id(name)] if identity != spell_id(name) else [],
                "population": "normal_class_subclass",
                "source_url": evidence["source_url"],
                "catalogue_source_url": CATALOGUE_URL,
                "srd_identity_present": evidence["sylvia_identity_status"] == "existing_identity",
                "baseline_profile_present": identity in existing,
                "school": evidence["school"],
                "base_class_evidence": evidence["base_class_evidence"].split("; "),
                "acquisition_evidence_urls": evidence["acquisition_evidence_urls"].split("; "),
                "srd_name_candidate": evidence["srd_name_candidate"],
                "srd_alias_warning": evidence["srd_alias_warning"],
                "patch_8_added_or_newly_class_learnable": evidence[
                    "patch_8_added_or_newly_class_learnable"
                ]
                == "True",
                "profile_state": "selected_unverified" if identity in HISTORICAL_SELECTED else "pending",
                "behavior_accepted": False,
            }
        )
    assert len(rows) == 210 and len({r["name"] for r in rows}) == 210
    assert dict(Counter(r["level"] for r in rows)) == EXPECTED_LEVELS
    assert len({r["identity"] for r in rows}) == 210
    assert sum(r["srd_identity_present"] for r in rows) == 181
    return {
        "format": "sylvia-srd-lite-catalogue",
        "revision": 1,
        "variant_id": VARIANT_ID,
        "variant_version": VARIANT_VERSION,
        "source_checked": "2026-10-07",
        ("input_route"): (
            "Retained source roster and checked spell-identity crosswalk."
            ""
        ),
        "crosswalk_sha256": hashlib.sha256(crosswalk_bytes).hexdigest(),
        "roster_sha256": hashlib.sha256(ROSTER.read_bytes()).hexdigest(),
        "population": "normal_class_subclass",
        "count": 210,
        "level_counts": EXPECTED_LEVELS,
        "profiles": rows,
        "unresolved_extra_content": [
            {
                "population": "quest_scroll_exclusive",
                "expected_count": 3,
                "state": "excluded_unresolved",
                "names": ["Artistry of War", "Dethrone", "Summon Deva"],
                "identities": ["artistry_of_war", "dethrone", "summon_deva"],
                ("reason"): (
                    "Scope report identifies these extras outside the assessed normal roste"
                    "r; their mechanics and permission remain unresolved"
                ),
            },
            {"population": "item_npc_recast_variants", "state": "excluded_unresolved"},
        ],
        "acceptance": "Foundation only; identity matches are not mechanical or rights acceptance",
    }



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
    legacy = _build_legacy_catalogue()
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
    deferred_by_id = {row['identity']:row for row in legacy['profiles'] if row['identity'] not in SELECTED}
    for row in launch.get('deferred_launch_profiles', []):
        if row['identity'] in SELECTED:
            continue
        deferred_by_id[row['identity']] = {**row, 'launch_priority':'deferred',
            'deferral_approved_utc':'2026-10-09T05:39:04Z'}
    deferred = list(deferred_by_id.values())
    return {'format':'sylvia-srd-lite-catalogue', 'revision':4, 'variant_id':VARIANT_ID,
        'variant_version':VARIANT_VERSION, 'source_checked':'2026-10-09',
        'authority':launch['authority'],
        'final_roster_authority': '117 launch spells: the original 96 Lite profiles and exactly 21 preserved implementations.', 'launch_roster_sha256':hashlib.sha256(launch_bytes).hexdigest(),
        'population':'final_117_launch_roster', 'count':117,
        'level_counts':dict(Counter(row['level'] for row in rows)), 'profiles':rows,
        'strict_bg3_matching_required':False,
        'deferred_profiles':deferred, 'deferred_count':len(deferred),
        'historical_catalogue':legacy,
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
