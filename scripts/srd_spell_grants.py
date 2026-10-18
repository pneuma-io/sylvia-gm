"""SRD casting grants and class limits, compiled into ruleset-owned data."""

import json
import re
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "rulesets/srd-lite"
ABILITIES = ["intelligence", "wisdom", "charisma"]
CLASSES = {
    "bard": (31, "charisma", "musical_instrument"),
    "cleric": (36, "wisdom", "holy_symbol"),
    "druid": (41, "wisdom", "druidic_focus"),
    "paladin": (53, "charisma", "holy_symbol"),
    "ranger": (58, "wisdom", "druidic_focus"),
    "sorcerer": (65, "charisma", "arcane_focus"),
    "warlock": (71, "charisma", "arcane_focus"),
    "wizard": (77, "intelligence", "arcane_focus"),
}
SUBCLASSES = {
    "barbarian": "berserker", "fighter": "champion", "monk": "warrior_of_the_open_hand",
    "rogue": "thief", "ranger": "hunter",
    "bard": "college_of_lore", "cleric": "life_domain", "druid": "circle_of_land",
    "paladin": "oath_of_devotion", "sorcerer": "draconic_sorcery", "warlock": "fiend_patron",
    "wizard": "evoker",
}


def _class_limits(class_id, page):
    """Read the numeric tail of all twenty rows in the official class feature table."""
    text = page[page.index("Features\n"):]
    matches = list(re.finditer(r"(?m)^(\d{1,2}) \+[2-6] ", text))
    if [int(m[1]) for m in matches] != list(range(1, 21)):
        raise ValueError(f"Incomplete SRD class table: {class_id}")
    result = {"cantrips": [], "prepared": [], "max_level": []}
    for index, match in enumerate(matches):
        row = text[match.end():matches[index + 1].start() if index < 19 else len(text)].strip()
        tail = re.search(r"(?:\d+|—)(?:\s+(?:\d+|—))+$", row)
        if tail is None:
            raise ValueError(f"Invalid SRD class row: {class_id} {index + 1}")
        numbers = [0 if token == "—" else int(token) for token in tail[0].split()]
        if class_id == "warlock":
            _, cantrips, prepared, _, maximum = numbers[-5:]
        else:
            slot_count = 5 if class_id in {"paladin", "ranger"} else 9
            slots = numbers[-slot_count:]
            prepared = numbers[-slot_count - 1]
            cantrips = 0 if slot_count == 5 else numbers[-slot_count - 2]
            maximum = max(rank for rank, count in enumerate(slots, 1) if count)
        result["cantrips"].append(cantrips)
        result["prepared"].append(prepared)
        result["max_level"].append(maximum)
    return result


def grant_policies():
    """Describe legal sources; choices remain saved on the character, never invented per cast."""
    corpus = json.loads((ROOT / "content/corpus/classes.json").read_text(encoding="utf-8"))
    pages = {row["page"]: row["text"] for row in corpus["page_records"]}
    policies = {}

    def add(source, *, class_id="", species="", feat="", subclass="", minimum=1,
            ability="", focus=None, lists=None, fixed=None, cantrips=0, prepared=0,
            maximum=9, page=0, book=False):
        policies[source] = {
            "class_id": class_id, "species": species, "feat": feat, "subclass": subclass,
            "minimum": minimum, "ability": ability, "focus": focus or [],
            "lists": lists or [], "fixed": fixed or {}, "book": book, "page": page,
            "cantrips": [cantrips] * 20, "prepared": [prepared] * 20,
            "max_level": [maximum] * 20,
            "min_spell_level": 0, "slots_allowed": True,
            "free_uses": [0] * 20, "free_proficiency": False,
            "free_pool": source, "free_per_spell": False,
            "free_label": source.replace("_", " ").title(),
            "long_rest_replacements": 0, "long_rest_cantrip_replacements": 0,
            "short_rest_replacements": 0, "short_rest_minimum": 1,
            "level_spell_replacements": 0, "level_cantrip_replacements": 0,
            "level_total_replacements": -1, "level_flexible_spells": False,
        }

    for name, (page, ability, focus) in CLASSES.items():
        add(name, class_id=name, ability=ability, focus=[focus], lists=[name], page=page,
            book=name == "wizard")
        policies[name].update(_class_limits(name, pages[page]))
    for name in ("cleric", "druid", "wizard"):
        add(f"magic_initiate_{name}", feat="magic_initiate", lists=[name],
            cantrips=2, prepared=1, maximum=1, page=87)

    # These grants are additional to the base class preparation limit.
    for source, class_id, minimum, cantrips, prepared, lists in [
        ("thaumaturge", "cleric", 1, 1, 0, ["cleric"]),
        ("magician", "druid", 1, 1, 0, ["druid"]),
        ("blessed_warrior", "paladin", 2, 2, 0, ["cleric"]),
        ("druidic_warrior", "ranger", 2, 2, 0, ["druid"]),
        ("magical_discoveries", "bard", 6, 2, 2, ["cleric", "druid", "wizard"]),
    ]:
        _, ability, focus = CLASSES[class_id]
        add(source, class_id=class_id, minimum=minimum, ability=ability, focus=[focus],
            lists=lists, cantrips=cantrips, prepared=prepared,
            subclass="college_of_lore" if source == "magical_discoveries" else "",
            page={"cleric": 37, "druid": 42, "paladin": 54, "ranger": 59, "bard": 35}[class_id])
        policies[source]["max_level"] = policies[class_id]["max_level"]

    fixed_sources = {
        "druidic": ("druid", "", {1: "speak_with_animals"}, 42),
        "favored_enemy": ("ranger", "", {1: "hunters_mark"}, 58),
        "paladins_smite": ("paladin", "", {2: "divine_smite"}, 54),
        "faithful_steed": ("paladin", "", {5: "find_steed"}, 55),
        "contact_patron": ("warlock", "", {9: "contact_other_plane"}, 72),
        "words_of_creation": ("bard", "", {20: "power_word_heal power_word_kill"}, 33),
        "life_domain": ("cleric", "life_domain", {
            3: "aid bless cure_wounds lesser_restoration", 5: "mass_healing_word revivify",
            7: "aura_of_life death_ward", 9: "greater_restoration mass_cure_wounds"}, 40),
        "draconic_spells": ("sorcerer", "draconic_sorcery", {
            3: "alter_self chromatic_orb command dragons_breath", 5: "fear fly",
            7: "arcane_eye charm_monster", 9: "legend_lore summon_dragon"}, 70),
        "devotion_spells": ("paladin", "oath_of_devotion", {
            3: "protection_from_evil_and_good shield_of_faith", 5: "aid zone_of_truth",
            9: "beacon_of_hope dispel_magic", 13: "freedom_of_movement guardian_of_faith",
            17: "commune flame_strike"}, 56),
        "fiend_spells": ("warlock", "fiend_patron", {
            3: "burning_hands command scorching_ray suggestion", 5: "fireball stinking_cloud",
            7: "fire_shield wall_of_fire", 9: "geas insect_plague"}, 76),
    }
    for land, levels in {
        "arid": {3: "blur burning_hands fire_bolt", 5: "fireball", 7: "blight", 9: "wall_of_stone"},
        "polar": {3: "fog_cloud hold_person ray_of_frost", 5: "sleet_storm", 7: "ice_storm",
                  9: "cone_of_cold"},
        "temperate": {3: "misty_step shocking_grasp sleep", 5: "lightning_bolt",
                      7: "freedom_of_movement", 9: "tree_stride"},
        "tropical": {3: "acid_splash ray_of_sickness web", 5: "stinking_cloud",
                     7: "polymorph", 9: "insect_plague"},
    }.items():
        fixed_sources[f"land_{land}"] = ("druid", "circle_of_land", levels, 46)
    for source, (class_id, subclass, levels, page) in fixed_sources.items():
        _, ability, focus = CLASSES[class_id]
        add(source, class_id=class_id, subclass=subclass, minimum=min(levels),
            ability=ability, focus=[focus], prepared=20, cantrips=3, page=page,
            fixed={spell: level for level, names in levels.items() for spell in names.split()})

    for source, species, levels, page in [
        ("elf_drow", "elf", {1: "dancing_lights", 3: "faerie_fire", 5: "darkness"}, 85),
        ("elf_high", "elf", {3: "detect_magic", 5: "misty_step"}, 85),
        ("elf_wood", "elf", {1: "druidcraft", 3: "longstrider", 5: "pass_without_trace"}, 85),
        ("gnome_forest", "gnome", {1: "minor_illusion speak_with_animals"}, 85),
        ("gnome_rock", "gnome", {1: "mending prestidigitation"}, 85),
        ("tiefling_abyssal", "tiefling", {1: "poison_spray thaumaturgy", 3: "ray_of_sickness",
                                          5: "hold_person"}, 86),
        ("tiefling_chthonic", "tiefling", {1: "chill_touch thaumaturgy", 3: "false_life",
                                            5: "ray_of_enfeeblement"}, 86),
        ("tiefling_infernal", "tiefling", {1: "fire_bolt thaumaturgy", 3: "hellish_rebuke",
                                           5: "darkness"}, 86),
    ]:
        add(source, species=species, page=page, cantrips=2, prepared=2,
            fixed={spell: level for level, names in levels.items() for spell in names.split()})
    policies["elf_high"].update(lists=["wizard"], cantrips=[1] * 20, max_level=[0] * 20)
    for source in ("cleric", "druid", "wizard"):
        # A negative limit permits replacing the whole list without a cumulative swap cap.
        policies[source]["long_rest_replacements"] = -1
    for source in ("paladin", "ranger"):
        policies[source]["long_rest_replacements"] = 1
    policies["wizard"].update(long_rest_cantrip_replacements=1,
                              short_rest_replacements=1, short_rest_minimum=5)
    policies["elf_high"]["long_rest_cantrip_replacements"] = 1
    for source in ("bard", "cleric", "druid", "sorcerer", "warlock", "thaumaturge", "magician",
                   "blessed_warrior", "druidic_warrior"):
        policies[source]["level_cantrip_replacements"] = 1
    for source in ("bard", "sorcerer", "warlock"):
        policies[source]["level_spell_replacements"] = 1
    policies["magical_discoveries"].update(level_spell_replacements=1,
                                          level_cantrip_replacements=1,
                                          level_total_replacements=1, level_flexible_spells=True)

    for name in ("cleric", "druid", "wizard"):
        policies[f"magic_initiate_{name}"].update(
            free_uses=[1] * 20, free_label=f"Magic Initiate ({name.title()})",
            level_spell_replacements=1, level_cantrip_replacements=1, level_total_replacements=1,
        )
    for source, policy in policies.items():
        if policy["species"] in {"elf", "tiefling"}:
            policy.update(free_uses=[1] * 20, free_per_spell=True)
            lineage = source.split("_", 1)[1].title()
            policy["free_label"] = (
                f"Elven Lineage ({lineage})" if policy["species"] == "elf"
                else f"Fiendish Legacy ({lineage})"
            )
        if source.startswith("land_"):
            policy.update(free_uses=[0] * 5 + [1] * 15, free_pool="natural_recovery",
                          free_label="Natural Recovery")
    policies["gnome_forest"].update(free_proficiency=True, free_label="Forest Gnome magic")
    policies["favored_enemy"].update(free_uses=[2 + (level - 1) // 4 for level in range(1, 21)])
    for source in ("paladins_smite", "faithful_steed"):
        policies[source]["free_uses"] = [1] * 20
    policies["paladins_smite"]["free_label"] = "Paladin's Smite"
    for rank, level in [(6, 11), (7, 13), (8, 15), (9, 17)]:
        source = f"mystic_arcanum_{rank}"
        add(source, class_id="warlock", minimum=level, ability="charisma",
            focus=["arcane_focus"], lists=["warlock"], prepared=1, maximum=rank, page=72)
        policies[source].update(min_spell_level=rank, slots_allowed=False,
                                free_uses=[1] * 20, free_label=f"Mystic Arcanum (level {rank})",
                                level_spell_replacements=1)
    # Product SRD Lite ancestry adaptations approved on 2026-10-12.
    lite_replacements = {'elf_drow': {'dancing_lights': 'light', 'faerie_fire': 'bane', 'darkness': 'blur'}, 'elf_high': {'detect_magic': 'longstrider'}, 'elf_wood': {'druidcraft': 'guidance', 'pass_without_trace': 'enhance_ability'}, 'gnome_forest': {'minor_illusion': 'mage_hand'}, 'gnome_rock': {'prestidigitation': 'light'}, 'tiefling_chthonic': {'ray_of_enfeeblement': 'blindness_deafness'}, 'tiefling_infernal': {'hellish_rebuke': 'burning_hands', 'darkness': 'blindness_deafness'}}
    for source, replacements in lite_replacements.items():
        for old, new in replacements.items():
            policies[source]["fixed"][new] = policies[source]["fixed"].pop(old)
        policies[source]["free_label"] += " (Lite adaptation)"
    return policies


def grants_schema():
    """Store each grant once so multiclass and feat abilities cannot be mixed per spell."""
    return {
        "type": "array", "maxItems": 32,
        "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "source": {"type": "string", "enum": sorted(grant_policies())},
                "ability": {"type": "string", "enum": ABILITIES},
                "spells": {"type": "array", "items": {"type": "string", "minLength": 1},
                           "uniqueItems": True, "maxItems": 200},
            },
            "required": ["source", "ability", "spells"],
        },
    }


def complete_grant_policies(policies, headers):
    """Compile complete repertoire counts and automatic sources from the same casting data."""
    automatic = set(CLASSES) | {
        "druidic", "favored_enemy", "paladins_smite", "faithful_steed", "contact_patron",
        "words_of_creation", "life_domain", "draconic_spells", "devotion_spells", "fiend_spells",
        "magical_discoveries", *[f"mystic_arcanum_{rank}" for rank in range(6, 10)],
    }
    for source, policy in policies.items():
        policy["complete_cantrips"] = []
        policy["complete_spells"] = []
        for level in range(1, 21):
            fixed = [headers[spell] for spell, earned in policy["fixed"].items()
                     if earned <= level]
            policy["complete_cantrips"].append(
                sum(row["level"] == 0 for row in fixed)
                + (policy["cantrips"][level - 1] if policy["lists"] else 0)
            )
            policy["complete_spells"].append(
                sum(row["level"] > 0 for row in fixed)
                + (policy["prepared"][level - 1] if policy["lists"] and source != "elf_high" else 0)
            )
        policy["automatic_sources"] = sorted(
            name for name in automatic if policies[name]["class_id"] == source
        ) if source in CLASSES else []
    return policies


def character_validation(runtime):
    """Reuse the casting validator for Character Files, adding initial profile/state agreement."""
    shared = deepcopy(runtime["tools"]["validate_srd_spell_grants"]["effects"])
    if (shared[0] != {"set": {"field": "_grant_profile", "value": "caster.data"}}
            or shared[1].get("condition") != "proposal != null and len(proposal) == 1"):
        raise ValueError("The casting validator's profile binding changed; "
                         "update its creation binding")
    effects = [
        {"set": {"field": "_defaults", "value": {"spellcasting_grants": []}}},
        {"set": {"field": "_grant_profile", "value": "merge(_defaults, participant)"}},
        {"set": {"field": "_complete", "value": True}},
        {"set": {"field": "_spell", "value": "''"}},
        {"set": {"field": "_selected_source", "value": "''"}},
    ]

    def rebind_effect(value):
        """Rebind private inputs without changing fixed diagnostic text."""
        if isinstance(value, dict):
            return {key: (item if key == "error" else rebind_effect(item))
                    for key, item in value.items()}
        if isinstance(value, list):
            return [rebind_effect(item) for item in value]
        if isinstance(value, str):
            return re.sub(r"\b(complete|spell|selected_source)\b",
                          lambda m: "_" + m[0], value)
        return value

    effects += [rebind_effect(effect) for effect in shared[2:]]
    effects += [
        {"set": {"field": "_subclasses", "value": {}}},
        {"condition": "participant.subclasses != null", "on_true": [
            {"set": {"field": "_subclasses", "value": "participant.subclasses"}},
        ]},
        {"set": {"field": "_subclass_count", "value": 0}},
        {"for_each": {"collection": "_class_records", "as": "_creation_class", "do": [
            {"require": {
                "condition": "_creation_class.level < 3 or _creation_class.id in _subclasses",
                "error": "Record the selected subclass for each class of level 3 or higher.",
            }},
            {"condition": "_creation_class.id in _subclasses", "on_true": [
                {"require": {"condition": "_creation_class.level >= 3",
                             "error": "A subclass requires class level 3."}},
                {"set": {"field": "_subclass_count", "value": "_subclass_count + 1"}},
            ]},
            {"condition": "_creation_class.id == 'wizard'", "on_true": [
                {"set": {"field": "_book", "value": "participant.wizard_spellbook"}},
                {"for_each": {"collection": "_book", "as": "_book_spell", "do": [
                    {"require": {
                        "condition": "_book_spell in definitions.spell_casting",
                        "error": "The spellbook contains an unknown spell.",
                    }},
                    {"table_lookup": {"table": "definitions.spell_casting", "key": "_book_spell",
                                      "required": True}, "store_as": "_book_header"},
                    {"require": {
                        "condition": "'wizard' in _book_header.classes and _book_header.level > 0 "
                        "and _book_header.level <= "
                        "definitions.spell_grants.wizard.max_level[_creation_class.level - 1]",
                        "error": "Starting spellbook entries must be eligible Wizard spells.",
                    }},
                ]}},
            ]},
        ]}},
        {"require": {"condition": "len(_subclasses) == _subclass_count",
                     "error": "Each selected subclass must belong to an actual class."}},
        {"set": {"field": "_prepared", "value": []}},
        {"for_each": {"collection": "_grant_records", "as": "_creation_grant", "do": [
            {"set": {"field": "_creation_spells", "value": "_creation_grant.spells"}},
            {"for_each": {"collection": "_creation_spells", "as": "_creation_spell", "do": [
                {"condition": "_creation_spell not in _prepared", "on_true": [
                    {"list_push": {"list": "_prepared", "value": "_creation_spell"}},
                ]},
            ]}},
        ]}},
        {"require": {"condition": "len(state.prepared_spells) == len(_prepared)",
                     "error": "Prepared spells must match the complete saved casting grants."}},
        {"set": {"field": "_saved_prepared", "value": "state.prepared_spells"}},
        {"for_each": {"collection": "_saved_prepared", "as": "_creation_spell", "do": [
            {"require": {"condition": "_creation_spell in _prepared",
                         "error": "A prepared spell has no saved casting grant."}},
        ]}},
    ]
    return {
        "description": (
            "Supply complete spellcasting_grants for actual classes, feats and lineage, including "
            "automatic spells. Use each source's ability, spell list and own class level. Save "
            "Wizard spellbook entries and required subclasses; prepared_spells must equal the "
            "grant union. The draft checker returns missing or invalid choices before publication."
        ),
        "definitions": {
            "spell_grants": runtime["definitions"]["spell_grants"],
            "spell_casting": {name: {key: row[key] for key in ("level", "classes", "school")}
                              for name, row in runtime["definitions"]["spell_casting"].items()},
        },
        "effects": effects,
    }


def definition_schema():
    """Admit generated grant policies through the ordinary typed-definition boundary."""
    properties = {key: {"type": "string"} for key in
                  ("class_id", "species", "feat", "subclass", "ability", "free_pool", "free_label")}
    properties.update({key: {"type": "array", "items": {"type": "string"}}
                       for key in ("focus", "lists", "automatic_sources")})
    properties.update({key: {"type": "array", "items": {"type": "integer"}}
                       for key in ("cantrips", "prepared", "max_level", "free_uses",
                                   "complete_cantrips", "complete_spells")})
    properties.update({
        "minimum": {"type": "integer"}, "page": {"type": "integer"},
        "book": {"type": "boolean"},
        "min_spell_level": {"type": "integer"}, "slots_allowed": {"type": "boolean"},
        "long_rest_replacements": {"type": "integer"},
        "long_rest_cantrip_replacements": {"type": "integer"},
        "short_rest_replacements": {"type": "integer"},
        "short_rest_minimum": {"type": "integer"},
        "level_spell_replacements": {"type": "integer"},
        "level_cantrip_replacements": {"type": "integer"},
        "level_total_replacements": {"type": "integer"},
        "level_flexible_spells": {"type": "boolean"},
        "free_proficiency": {"type": "boolean"}, "free_per_spell": {"type": "boolean"},
        "fixed": {"type": "object", "additionalProperties": False,
                  "properties": {spell: {"type": "integer"}
                                 for policy in grant_policies().values()
                                 for spell in policy["fixed"]}},
    })
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}
