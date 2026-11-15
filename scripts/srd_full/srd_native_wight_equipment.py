"""Printed Wight armor uses the existing NPC equipment and elapsed-time owners."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

if __package__:
    from .srd_native_wight_actions import WIGHT_BLOCK, _validate_source
    from .srd_spell_summoning import _created_undead_additional_profiles
    from .srd_timed_actor_defenses import _call, _load_runtime, _set
    from .srd_timed_senses import _push, _replace_tools
else:
    from srd_native_wight_actions import WIGHT_BLOCK, _validate_source
    from srd_spell_summoning import _created_undead_additional_profiles
    from srd_timed_actor_defenses import _call, _load_runtime, _set
    from srd_timed_senses import _push, _replace_tools


def _source_equipment_profile():
    """Derive native body/armor facts without granting character classes or caller statistics."""
    _validate_source()
    corpus = Path(__file__).resolve().parents[2] / "rulesets/dnd-srd-5.2.1/content/corpus"
    for name, number, digest, clauses in (
        (
            "equipment",
            92,
            "3b4eb89e6e8e254c9ffa071d142414d7d8c3091fbe64a07da98015bfadd71d24",
            (
                "A monster has training with any armor in its stat",
                "Light Armor (1 Minute to Don or Doff)",
                "Studded Leather Armor 12 + Dex modifier",
            ),
        ),
        (
            "rules_glossary",
            177,
            "2157ea5b29cdb71f3d186c48c87305184ec6c32dba1035e275cb921a5e35d5ab",
            ("Your base AC calculation is 10 plus your Dexterity",),
        ),
    ):
        page = next(
            p
            for p in json.loads((corpus / (name + ".json")).read_text())["page_records"]
            if p["page"] == number
        )
        assert page["text_sha256"] == hashlib.sha256(page["text"].encode()).hexdigest() == digest
        assert all(clause in page["text"] for clause in clauses)
    row = _created_undead_additional_profiles()["wight"]
    assert row["source_stat_block_sha256"] == WIGHT_BLOCK
    assert "Studded Leather Armor" in row["source_stat_block"]
    abilities = row["ability_scores"]
    return {
        "strength": abilities["strength"],
        "dexterity": abilities["dexterity"],
        "unarmored_ac": 10 + (abilities["dexterity"] - 10) // 2,
        "speed": row["walk_speed"],
        "climb_speed": 0,
        "swim_speed": 0,
        "armor_training": ["'light'"],
        "armor": [
            {
                "inventory_item": "'Studded Leather Armor'",
                "armor_type": "'studded_leather_armor'",
                "magic_bonus": 0,
            }
        ],
        "shield": [],
    }


def extend_runtime(text):
    """Admit only printed Wight gear through the same validated NPC initializer."""
    profile = _source_equipment_profile()
    tools = _load_runtime(text)["tools"]
    name = "finish_srd_wight_source_actor"
    finish = deepcopy(tools[name])
    effects = [
        _set("_native_wight_equipment_choices", []),
        _push("_native_wight_equipment_choices", profile),
        _call(
            "initialize_srd_npc_equipment",
            {
                "actor_state": "{_actor_state_id}",
                "equipment": "{_native_wight_equipment_choices}",
            },
        ),
    ]
    marker = "_native_wight_equipment_choices"
    if marker in str(finish["effects"]):
        if finish["effects"][-len(effects) :] != effects:
            raise ValueError("Preserve Wight's exact source-derived equipment admission.")
        return text
    armor = [
        e["set"]["value"]
        for e in finish["effects"]
        if e.get("set", {}).get("field") == "actor_state.created_source_armor"
    ]
    if (
        armor
        != [
            {
                "block_sha256": repr(WIGHT_BLOCK),
                "inventory_item": "'Studded Leather Armor'",
                "armor_type": "'studded_leather_armor'",
            }
        ]
        or "initialize_srd_npc_equipment" not in tools
    ):
        raise ValueError("Use the real printed Wight armor and existing NPC equipment owner.")
    finish["effects"].extend(effects)
    return _replace_tools(text, {name: finish})
