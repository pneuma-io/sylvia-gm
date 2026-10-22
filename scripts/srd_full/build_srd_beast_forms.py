"""Extract the fixed core of SRD Beast forms for transformation rules."""

from __future__ import annotations

import hashlib
import json
import re
from bisect import bisect_right
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "rulesets" / "dnd-srd-5.2.1" / "content"
CORPUS = ROOT / "corpus" / "animals.json"
OUTPUT = ROOT / "beast-form-index.json"
FORM_HEADER = re.compile(r"(?m)^([^\n]{2,70})\n([^\n]*\bBeasts?\b[^\n]*)\nAC\s+(\d+)")
ABILITIES = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")
SHORT_ABILITIES = ("Str", "Dex", "Con", "Int", "Wis", "Cha")
SIZE_RANKS = {"Tiny": 0, "Small": 1, "Medium": 2, "Large": 3, "Huge": 4, "Gargantuan": 5}
STAT_BLOCK_HEADER = re.compile(
    r"(?m)^([^\n]{2,70})\n((?:Tiny|Small|Medium|Large|Huge|Gargantuan) [^\n]+)\nAC\s+(\d+)"
)
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4}
DAMAGE_TYPES = {
    "Acid",
    "Bludgeoning",
    "Cold",
    "Fire",
    "Force",
    "Lightning",
    "Necrotic",
    "Piercing",
    "Poison",
    "Psychic",
    "Radiant",
    "Slashing",
    "Thunder",
}
CONDITIONS = {
    "Blinded",
    "Charmed",
    "Deafened",
    "Exhaustion",
    "Frightened",
    "Grappled",
    "Incapacitated",
    "Invisible",
    "Paralyzed",
    "Petrified",
    "Poisoned",
    "Prone",
    "Restrained",
    "Stunned",
    "Unconscious",
}


def _source_adjustments(clean: str) -> tuple[dict[str, list[str]], list[str]]:
    """Read only closed damage and condition names, retaining unsupported qualifiers."""
    fields = dict.fromkeys(
        (
            "damage_resistances",
            "damage_immunities",
            "damage_vulnerabilities",
            "condition_immunities",
        )
    )
    fields = {name: [] for name in fields}
    unsupported = []
    next_header = r"Senses |Languages |CR |Traits\n|Actions\n|Skills |Gear |Saving Throws "
    for header, destination in (
        ("Resistances", "damage_resistances"),
        ("Immunities", "damage_immunities"),
        ("Vulnerabilities", "damage_vulnerabilities"),
    ):
        match = re.search(
            rf"(?ms)^{header} (.*?)(?=\n(?:{next_header}"
            r"|Resistances |Immunities |Vulnerabilities )|\Z)",
            clean,
        )
        if not match:
            continue
        text = re.sub(r"\s+", " ", match[1]).strip()
        for value in (part.strip() for part in text.split(",")):
            if value in DAMAGE_TYPES:
                fields[destination].append(value.lower())
            elif header == "Immunities" and value in CONDITIONS:
                fields["condition_immunities"].append(value.lower())
            else:
                unsupported.append(header + ": " + value)
    return fields, unsupported


def _playable_profile(block: str) -> dict[str, object]:
    "Derive ordinary attacks and senses; reject any unimplemented source clause explicitly."
    clean = re.sub(r"(\w)\s*-\s*\n(\w)", r"\1\2", block)
    clean = re.sub(r"(?m)^\d+\s*\nSystem Reference Document 5\.2\.1\s*", "", clean)
    clean = re.sub(r"System Reference Document 5\.2\.1\s*\d+", "", clean)
    skills_text = re.search(r"(?m)^Skills ([^\n]+)", clean)
    skill_names = {
        "Acrobatics": "acrobatics",
        "Athletics": "athletics",
        "Perception": "perception",
        "Stealth": "stealth",
    }
    skills = (
        {}
        if not skills_text
        else {
            skill_names[name]: _signed(score)
            for name, score in re.findall(
                r"(Acrobatics|Athletics|Perception|Stealth)\s+([+−-]\d+)", skills_text[1]
            )
        }
    )
    senses = {}
    for kind in ("Darkvision", "Blindsight", "Tremorsense", "Truesight"):
        match = re.search(rf"\b{kind} (\d+) ft\.", clean)
        senses[kind.lower() + "_feet"] = int(match[1]) if match else 0
    perception = _required(r"Passive Perception (\d+)", clean, "passive Perception")
    languages = _required(r"^Languages ([^\n]+)", clean, "Languages")[1]
    traits = (
        clean.split("\nTraits\n", 1)[1].split("\nActions\n", 1)[0] if "\nTraits\n" in clean else ""
    )
    traits = re.sub(r"\s+", " ", traits).strip()
    respiration = {"breathes_air": True, "breathes_water": False, "hold_breath_ticks": 0}
    amphibious = re.compile(r"Amphibious\. The [A-Za-z ]+ can breathe air and water\.")
    aquatic = re.compile(r"Water Breathing\. The [A-Za-z ]+ can breathe only underwater\.")
    hold_breath = re.compile(
        r"(?:Hold Breath\. The [A-Za-z ]+|It) can hold its breath for "
        r"(\d+) (minute|hour)s?(?: outside water)?\."
    )
    if amphibious.search(traits):
        respiration["breathes_water"] = True
        traits = amphibious.sub("", traits)
    if aquatic.search(traits):
        respiration.update(breathes_air=False, breathes_water=True)
        traits = aquatic.sub("", traits)
    for breath in hold_breath.finditer(traits):
        respiration["hold_breath_ticks"] = int(breath[1]) * (1200 if breath[2] == "hour" else 20)
    traits = hold_breath.sub("", traits)
    pack_tactics = re.compile(
        r"Pack Tactics\. The [A-Za-z ]+ has Advantage on (?:an attack roll|attack rolls) "
        r"against a creature if at least one of the [A-Za-z ]+[’']s allies is within 5 feet "
        r"of the creature and the ally doesn[’']t have the Incapacitated condition\."
    )
    has_pack_tactics = bool(pack_tactics.search(traits))
    traits = pack_tactics.sub("", traits)
    illumination = re.compile(
        r"Illumination\. The [A-Za-z ]+ sheds Bright Light in a (\d+)-foot "
        r"radius and Dim Light for an additional (\d+) feet\."
    )
    bright_light_feet = dim_light_feet = 0
    if light := illumination.search(traits):
        bright_light_feet = int(light[1])
        dim_light_feet = bright_light_feet + int(light[2])
    traits = illumination.sub("", traits)
    actions_text = clean.split("\nActions\n", 1)[1] if "\nActions\n" in clean else ""
    headings = list(
        re.finditer(
            "(?m)^(?!(?:" + "|".join(sorted(DAMAGE_TYPES)) + ") damage)"
            r"([A-Z][^\n]{0,70}?)\. ",
            actions_text,
        )
    )
    attacks = {}
    multiattack = []
    adjustments, unsupported = _source_adjustments(clean)
    if "Burrow " in clean:
        unsupported.append("Burrow movement has no admitted scene consumer")
    if traits.strip():
        unsupported.append("Traits: " + re.sub(r"\s+", " ", traits).strip())
    for i, heading in enumerate(headings):
        name = heading[1]
        tail = headings[i + 1].start() if i + 1 < len(headings) else len(actions_text)
        text = re.sub(r"\s+", " ", actions_text[heading.end() : tail]).strip()
        if name == "Multiattack":
            for number, attack in re.findall(
                r"(one|two|three|four) ([A-Za-z ]+?) attacks?(?: and |\.|,)", text
            ):
                multiattack.append({"attack": attack.strip(), "count": NUMBER_WORDS[number]})
            if not multiattack:
                unsupported.append("Multiattack: " + text)
            continue
        not_full_hp_advantage = (
            "(with Advantage if the target doesn’t have all its Hit Points)" in text
        )
        text = text.replace(" (with Advantage if the target doesn’t have all its Hit Points)", "")
        bloodied_damage_expression = ""
        bloodied_damage_modifier = 0
        bloodied = re.search(
            r" damage, or (\d+)(?: \((\d+d\d+)(?: ([+−–-]) (\d+))?\))? "
            r"(" + "|".join(sorted(DAMAGE_TYPES)) + r") damage if the target is Bloodied\.$",
            text,
        )
        if bloodied:
            average, roll, sign, modifier, alternate_type = bloodied.groups()
            main_type = re.search(r"(" + "|".join(sorted(DAMAGE_TYPES)) + r") damage, or ", text)[1]
            if alternate_type == main_type:
                bloodied_damage_expression = (
                    roll[1:] if roll and roll.startswith("1d") else roll
                ) or "fixed-" + average
                bloodied_damage_modifier = (
                    int(modifier) * (-1 if sign in ("−", "–", "-") else 1) if modifier else 0
                )
                text = text[: bloodied.start()] + " damage."
        extra_damage_expression = ""
        extra_damage_modifier = 0
        extra_damage_type = ""
        extra = re.search(
            r" damage plus (\d+)(?: \((\d+d\d+)(?: ([+−–-]) (\d+))?\))? "
            r"(" + "|".join(sorted(DAMAGE_TYPES)) + r") damage\.(.*)$",
            text,
        )
        if extra:
            average, roll, sign, modifier, extra_damage_type, tail = extra.groups()
            extra_damage_expression = (
                roll[1:] if roll and roll.startswith("1d") else roll
            ) or "fixed-" + average
            extra_damage_modifier = (
                int(modifier) * (-1 if sign in ("−", "–", "-") else 1) if modifier else 0
            )
            text = text[: extra.start()] + " damage." + tail
            extra_damage_type = extra_damage_type.lower()
        match = re.fullmatch(
            "(Melee|Ranged) Attack Roll: ([+−–-]\\d+)\\s*, (?:reach (\\d+) (?:ft|feet)|range "
            "(\\d+)/(\\d+) ft)\\. "
            r"Hit: (\d+)(?: \((\d+d\d+)(?: ([+−–-]) (\d+))?\))? "
            r"(" + "|".join(sorted(DAMAGE_TYPES)) + r") damage\.(.*)",
            text,
        )
        recharge = re.fullmatch(r"(.+) \(Recharge ([1-6])(?:[−–-]6)?\)", name)
        if not match or ("(" in name and not recharge):
            unsupported.append(name + ": " + text)
            continue
        if recharge:
            name = recharge[1]
        kind, bonus, reach, normal, long, average, dice, sign, modifier, damage_type, rider = (
            match.groups()
        )
        rider = rider.strip()
        prone = re.fullmatch(
            (
                "If the target is a (Tiny|Small|Medium|Large|Huge|Gargantuan) or "
                "smaller creature, it has the Prone condition\\."
            ),
            rider,
        )
        if rider and not prone:
            unsupported.append(name + " rider: " + rider)
        attacks[name] = {
            "name": name,
            "kind": kind.lower(),
            "attack_modifier": _signed(bonus),
            "normal_range": int(reach or normal),
            "long_range": int(reach or long),
            "damage_expression": (dice[1:] if dice and dice.startswith("1d") else dice)
            or "fixed-" + average,
            "damage_modifier": (int(modifier) * (-1 if sign in ("−", "–", "-") else 1))
            if modifier
            else 0,
            "damage_type": damage_type.lower(),
            "bloodied_damage_expression": bloodied_damage_expression,
            "bloodied_damage_modifier": bloodied_damage_modifier,
            "target_not_full_hp_advantage": not_full_hp_advantage,
            "extra_damage_expression": extra_damage_expression,
            "extra_damage_modifier": extra_damage_modifier,
            "extra_damage_type": extra_damage_type,
            "prone_large_or_smaller": bool(prone) and prone[1] == "Large",
            "prone_max_size_rank": SIZE_RANKS[prone[1]] if prone else -1,
            "supported": not rider or bool(prone),
        }
        if recharge:
            attacks[name]["recharge_min"] = int(recharge[2])
    if not attacks:
        unsupported.append("No admitted ordinary attack")
    if any(row["attack"] not in attacks for row in multiattack):
        unsupported.append("Multiattack references an unimplemented attack")
    if " or " in _required(r"^Speed\s+([^\n]+)", clean, "Speed")[1]:
        unsupported.append("Conditional movement modes")
    profile = {
        "source_stat_block": block.rstrip(),
        "source_stat_block_sha256": hashlib.sha256(block.rstrip().encode()).hexdigest(),
        "initiative_modifier": _signed(
            _required(r"Initiative ([+−–-]\d+)", clean, "Initiative")[1]
        ),
        "skills": skills,
        "senses": senses,
        "passive_perception": int(perception[1]),
        "languages": []
        if languages.strip() == "None"
        else [v.strip() for v in languages.split(",")],
        **adjustments,
        **respiration,
        "pack_tactics": has_pack_tactics,
        "bright_light_feet": bright_light_feet,
        "dim_light_feet": dim_light_feet,
        "attacks": attacks,
        "multiattack": multiattack,
        "unsupported_clauses": unsupported,
        "execution_supported": not unsupported,
    }
    recharge_names = [name for name, attack in attacks.items() if "recharge_min" in attack]
    if recharge_names:
        profile["recharge_attack_names"] = recharge_names
    return profile


def _signed(value: str) -> int:
    """Read the SRD's Unicode minus sign as an ordinary signed integer."""

    return int(value.replace("−", "-").replace("–", "-"))


def _required(pattern: str, block: str, name: str) -> re.Match[str]:
    "Reject a form whose fixed statistic cannot be recovered from its admitted source."

    match = re.search(pattern, block, re.MULTILINE | re.IGNORECASE)
    if match is None:
        raise SystemExit(f"Beast form is missing a fixed {name}: {block.splitlines()[0]}")
    return match


def source_form_profile(name, type_line, armor_class, block, page):
    "Derive fixed body facts once for source-bound Beast and non-Beast catalogues."
    hit_points = int(_required(r"\bHP\s+(\d+)", block, "Hit Points")[1])
    challenge_rating = Fraction(_required(r"\bCR\s+(\d+(?:/\d+)?)\b", block, "CR")[1])
    rating_eighths = challenge_rating * 8
    if rating_eighths.denominator != 1 or rating_eighths < 0:
        raise SystemExit(f"Beast form CR cannot use eighths: {name}")
    size = type_line.split(" ", 1)[0]
    if size not in SIZE_RANKS:
        raise SystemExit(f"Beast form has unknown size: {name}")
    speed_text = _required(r"^Speed\s+([^\n]+)", block, "Speed")[1]
    walk_speed = int(_required(r"^Speed\s+(\d+) ft\.", block, "walking Speed")[1])
    conditional_speed = " or " in speed_text
    modes = (
        {
            mode.lower(): int(value)
            for mode, value in re.findall(r"\b(Climb|Swim|Fly|Burrow) (\d+) ft\.", speed_text)
        }
        if not conditional_speed
        else {}
    )
    scores: dict[str, int] = {}
    saves: dict[str, int] = {}
    for ability, short in zip(ABILITIES, SHORT_ABILITIES, strict=True):
        score, _modifier, save = _required(
            rf"\b{short}\s+(\d+)\s+([+−-]\d+)\s+([+−-]\d+)",
            block,
            f"{ability} score and save",
        ).groups()
        scores[ability] = int(score)
        saves[ability] = _signed(save)
    return {
        "name": name,
        "source": {"page": page["page"], "page_text_sha256": page["text_sha256"]},
        "type_line": type_line,
        "challenge_rating_eighths": int(rating_eighths),
        "size_rank": SIZE_RANKS[size],
        "armor_class": int(armor_class),
        "hit_points": hit_points,
        "walk_speed": walk_speed,
        "other_speeds": modes,
        "speed_text": speed_text,
        "ability_scores": scores,
        "saving_throw_modifiers": saves,
        **_playable_profile(block),
    }


def build_index() -> dict[str, object]:
    "Bind each Beast's fixed statistics and admitted actions to its exact corpus page."

    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    pages = corpus["page_records"]
    starts: list[int] = []
    chunks: list[str] = []
    position = 0
    for page in pages:
        starts.append(position)
        chunk = page["text"] + "\n"
        chunks.append(chunk)
        position += len(chunk)
    text = "".join(chunks)
    all_headings = list(STAT_BLOCK_HEADER.finditer(text))
    headings = [heading for heading in all_headings if re.search(r"\bBeasts?\b", heading[2])]
    block_ends = {
        heading.start(): all_headings[i + 1].start() if i + 1 < len(all_headings) else len(text)
        for i, heading in enumerate(all_headings)
    }
    if len(headings) != 90:
        raise SystemExit(f"Expected 90 admitted SRD Beast forms; found {len(headings)}")

    forms: dict[str, object] = {}
    for heading in headings:
        name, type_line, armor_class = heading.groups()
        form_id = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        if form_id in forms:
            raise SystemExit(f"Duplicate Beast form ID: {form_id}")
        page = pages[bisect_right(starts, heading.start()) - 1]
        block_end = block_ends[heading.start()]
        block = text[heading.start() : block_end]
        forms[form_id] = source_form_profile(name, type_line, armor_class, block, page)
    return {
        "format_id": "sylvia-gm-srd-beast-form-core-index",
        "format_version": 1,
        "source_sha256": corpus["source_sha256"],
        "form_count": len(forms),
        "forms": dict(sorted(forms.items())),
    }


if __name__ == "__main__":
    index = build_index()
    OUTPUT.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Indexed {index['form_count']} SRD Beast core profiles from admitted corpus")
