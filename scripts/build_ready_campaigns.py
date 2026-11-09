"""Prepare the approved parties through the shipped Character File calculation contract."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from copy import deepcopy
from pathlib import Path

from sylvia_gm.application.bundled_campaign_plans import bundled_plan
from sylvia_gm.application.reusable_participants import (
    SETTING_CHARACTER_FORMAT_ID,
    SETTING_CHARACTER_FORMAT_VERSION,
    validate_reusable_participant_draft,
)
from sylvia_gm.domain.authority import PackageRole
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.infrastructure.package_archive import admit_package_archive

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "src/sylvia_gm/campaign/ready-campaigns.json"
PARTIES = {
    "a-debt-in-iron": (("tavin-bell", 1), ("cleric", 1)),
    "last-cart-through": (("fighter", 2), ("cleric", 2)),
    "the-cartographers-wake": (("rogue", 2), ("fighter", 2), ("cleric", 2)),
    "rift-awakening": (("bard", 1), ("fighter", 1), ("cleric", 1)),
}


def prepare() -> bytes:
    """Resolve complete authored choices once, leaving runtime loading to verification."""
    starters_path = ROOT / "src/sylvia_gm/campaign/srd-starters.json"
    starters = {row["id"]: row for row in json.loads(starters_path.read_text(encoding="utf-8"))}
    campaigns = []
    for plan_id, party in PARTIES.items():
        plan = bundled_plan(plan_id)
        assert plan is not None and plan.package_snapshot is not None
        with admit_package_archive(io.BytesIO(plan.package_snapshot)) as archive:
            primary = next(m for m in archive.manifests if m.role is PackageRole.PRIMARY)
            declaration = next(
                d for d in primary.participant_artifacts if d.kind == "player_character"
            )
        characters = []
        for index, (class_id, level) in enumerate(party):
            role = "player_character" if index == 0 else "companion"
            if class_id == "tavin-bell":
                characters.append({"role": role, "required_character_id": class_id})
                continue
            row = deepcopy(starters[class_id])
            participant, state = row["participant"], row["entity_state"]
            participant["level"] = participant["classes"][0]["level"] = level
            profile = row["authored_profile"]
            if level == 2:
                state["max_hp"] += {"fighter": 8, "cleric": 6, "rogue": 7}[class_id]
                state["resources"]["hit_die"] = state["resource_maxima"]["hit_die"] = 2
                features = {
                    "fighter": [
                        ("action_surge", "Action Surge, one use per Short or Long Rest."),
                        ("tactical_mind", "Tactical Mind uses Second Wind; retain Defense."),
                    ],
                    "cleric": [
                        ("channel_divinity", "Two uses; Divine Spark and Turn Undead."),
                        ("spellcasting", "Five Cleric spells prepared; add Shield of Faith."),
                    ],
                    "rogue": [("cunning_action", "Dash, Disengage or Hide as a Bonus Action.")],
                }[class_id]
                participant["advancements"] = [
                    {
                        "level": 2,
                        "class_id": class_id,
                        "summary": (
                            "Prepared level-two edition; fixed HP increase "
                            "and retained starting choices."
                        ),
                        "features": [
                            {
                                "id": identity,
                                "source": f"SRD 5.2.1 {class_id.title()} level 2",
                                "choice": choice,
                            }
                            for identity, choice in features
                        ],
                    }
                ]
                profile = profile.replace("level 1", "level 2")
                profile += "\n\n## Prepared level-two choices\n\n" + " ".join(
                    choice for _, choice in features
                )
                profile += (
                    " Fixed HP increase, including Constitution: "
                    + str({"fighter": 8, "cleric": 6, "rogue": 7}[class_id])
                    + "."
                )
                if class_id == "cleric":
                    participant["spellcasting_grants"][0]["spells"].append("shield_of_faith")
                    state["prepared_spells"].append("shield_of_faith")
                    for key in ("resources", "resource_maxima"):
                        state[key].update(spell_slot_1=3, channel_divinity=2)
                elif class_id == "rogue":
                    state["available_bonus_actions"] = ["cunning_action"]
            summary = row["summary"].replace("Level 1", f"Level {level}")
            profile, participant, complete, setting = validate_reusable_participant_draft(
                role=role,
                summary=summary,
                authored_profile=profile,
                participant=participant,
                entity_state=state,
                declaration=declaration,
            )
            character = {
                "format_id": SETTING_CHARACTER_FORMAT_ID,
                "format_version": SETTING_CHARACTER_FORMAT_VERSION,
                "id": f"ready-{class_id}-{level}",
                "schema": {
                    "id": declaration.schema_id,
                    "content_sha256": declaration.schema_sha256,
                },
                "summary": summary,
                "authored_profile": profile,
                "participant": participant,
                "entity_state": complete,
                "setting_data": setting,
            }
            characters.append(
                {
                    "role": role,
                    "character": character,
                    "content_sha256": hashlib.sha256(canonical_json_bytes(character)).hexdigest(),
                }
            )
        campaigns.append(
            {
                "id": plan_id,
                "plan_content_sha256": plan.content_sha256,
                "bindings": json.loads(canonical_json_bytes(plan.plan.payload["bindings"])),
                "party": characters,
            }
        )
    payload = {
        "format_version": 1,
        "provenance": {
            "adventures": (
                "Existing bundled Severed Lands Campaign Plans; story preparation unchanged."
            ),
            "characters": (
                "Existing SRD starters and Tavin Bell; independent prepared role/level editions."
            ),
            "starter_source_sha256": hashlib.sha256(starters_path.read_bytes()).hexdigest(),
            "rules": "Contained SRD Lite 0.3.15; retained SRD 5.2.1 class and creation references.",
            "notices": [
                "LICENSING-STATUS.md",
                "campaign-settings/severed-lands/ATTRIBUTION.md",
                "rulesets/srd-lite/ATTRIBUTION.md",
            ],
        },
        "campaigns": campaigns,
    }
    return (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    arguments = parser.parse_args()
    generated = prepare()
    if arguments.write:
        OUTPUT.write_bytes(generated)
    elif OUTPUT.read_bytes() != generated:
        raise SystemExit("Prepared campaign content changed; review and regenerate with --write")
    print("Four prepared campaign parties validated.")
