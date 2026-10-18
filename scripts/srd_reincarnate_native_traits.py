"""Reincarnate projects printed passive traits through current native receiving owners."""

import hashlib
import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

if __package__:
    from .srd_control_behaviors import (
        _call,
        _each,
        _entity,
        _param,
        _require,
        _set,
        _tool,
        _walk,
        _when,
    )
    from .srd_divination_spells import _normalize
    from .srd_timed_actor_defenses import _load_runtime
    from .srd_timed_senses import _replace_tools
else:
    from srd_control_behaviors import (
        _call,
        _each,
        _entity,
        _param,
        _require,
        _set,
        _tool,
        _walk,
        _when,
    )
    from srd_divination_spells import _normalize
    from srd_timed_actor_defenses import _load_runtime
    from srd_timed_senses import _replace_tools

ROOT = Path(__file__).resolve().parents[1]
PAGES = {
    84: "c92ce702937331d244297c2da8864b83022bf5b662250e1b7b78b8f0a823a21e",
    85: "031565251f4a849e622b400901c9d4c62063842f5f94cffcda933d0b59021c7f",
    86: "4c48f03bbc21a7ed21254f4368718a92b9d4ca51ce86471713ad735aab3da85f",
}
TRAITS = {
    "dragonborn": {"resistance": "", "condition_saves": [], "save_abilities": [], "trance": False},
    "dwarf": {
        "resistance": "poison",
        "condition_saves": ["poisoned"],
        "save_abilities": [],
        "trance": False,
    },
    "elf": {"resistance": "", "condition_saves": ["charmed"], "save_abilities": [], "trance": True},
    "gnome": {
        "resistance": "",
        "condition_saves": [],
        "save_abilities": ["intelligence", "wisdom", "charisma"],
        "trance": False,
    },
    "goliath": {"resistance": "", "condition_saves": [], "save_abilities": [], "trance": False},
    "halfling": {
        "resistance": "",
        "condition_saves": ["frightened"],
        "save_abilities": [],
        "trance": False,
    },
    "human": {"resistance": "", "condition_saves": [], "save_abilities": [], "trance": False},
    "orc": {"resistance": "", "condition_saves": [], "save_abilities": [], "trance": False},
    "tiefling": {"resistance": "", "condition_saves": [], "save_abilities": [], "trance": False},
}
ANCESTRY = {
    "black": "acid",
    "blue": "lightning",
    "brass": "fire",
    "bronze": "lightning",
    "copper": "acid",
    "gold": "fire",
    "green": "poison",
    "red": "fire",
    "silver": "cold",
    "white": "cold",
}
LEGACIES = {
    "tiefling_abyssal": "poison",
    "tiefling_chthonic": "necrotic",
    "tiefling_infernal": "fire",
}
SOURCE = hashlib.sha256(json.dumps(PAGES, sort_keys=True).encode()).hexdigest()
NATIVE = "target_state.reincarnate_native_traits != null"


@lru_cache
def _validate_source():
    """Bind the small trait table to the already pinned public species pages."""
    corpus = json.loads(
        (ROOT / "rulesets/srd-lite/content/corpus/character_origins.json").read_text()
    )
    pages = {row["page"]: row for row in corpus["page_records"]}
    for page, expected in PAGES.items():
        row = pages[page]
        if (
            row["text_sha256"] != expected
            or hashlib.sha256(row["text"].encode()).hexdigest() != expected
        ):
            raise ValueError("Use the pinned 5.2.1 species source, not an inferred trait profile.")
    return True


def _body_tools():
    qualify = _tool(
        "Read only this genuine completed ordinary Character's new-body traits.",
        [_entity("target_state")],
        [
            _require(
                "target_state.creature_kind == 'character' and "
                "target_state.form_statistics_replaced != true and target_state.form_cloud != t"
                "rue and "
                "target_state.current_body_source_id == null and target_state.magic_jar_role =="
                " null",
                "Reincarnated traits in a replacement, transferred or occupied body remain unsu"
                "pported.",
            ),
            _require(
                NATIVE
                + " and target_state.reincarnate_native_traits.owner == _target_state_id and "
                "target_state.reincarnate_native_traits.species == target_state.species and "
                "target_state.reincarnate_native_traits.source_sha256 == '" + SOURCE + "'",
                "Retain the actual installed source and new-body species identity.",
            ),
            _set(
                "_reincarnate_traits",
                "definitions.srd_reincarnate_native_traits[target_state.species]",
            ),
        ],
    )
    return {"qualify_srd_reincarnate_native_body": qualify}


def _complete(tool):
    """Record new contributions; never erase or retain unknown former species benefits silently."""
    tool = deepcopy(tool)
    tool["parameters"].append(_param("species_origin_feat", required=False, default=""))
    effects = tool["effects"]
    artifact = [
        i
        for i, e in enumerate(effects)
        if e.get("require", {}).get("error")
        == "The reincarnated body requires this actor's actual validated Character File."
    ]
    if len(artifact) != 1:
        raise ValueError("Keep the actual Character File qualification before new-body mutation.")
    effects[artifact[0] + 1 : artifact[0] + 1] = [
        _require(
            "target_state.form_statistics_replaced != true and target_state.form_cloud != true and "
            "target_state.current_body_source_id == null and target_state.magic_jar_role == null",
            "Complete only an ordinary native Character body; transferred/occupied bodies are u"
            "nsupported.",
        ),
        _require(
            "target_state.reincarnate_native_traits != null or "
            "(target_character.data.species != 'human' and target_character.data.species != 'el"
            "f' and "
            "target_character.data.species != 'dwarf' and target_character.data.species != 'dra"
            "gonborn' "
            "and target_character.data.species != 'tiefling')",
            "Record former species skill/feat/resistance/HP provenance before replacing those l"
            "egacy contributions.",
        ),
        _when(
            NATIVE,
            [
                _call(
                    "qualify_srd_reincarnate_native_body", {"target_state": "{_target_state_id}"}
                ),
                _require(
                    "target_character.data.skill_proficiencies == "
                    "target_state.reincarnate_native_traits.installed_skills and "
                    "target_character.data.origin_feats == "
                    "target_state.reincarnate_native_traits.installed_feats and "
                    "target_state.damage_resistances == "
                    "target_state.reincarnate_native_traits.installed_resistances and "
                    "target_character.data.level == target_state.reincarnate_native_traits.level",
                    "Resolve later skill, feat, defense or level contributions before another "
                    "new-body replacement.",
                ),
            ],
        ),
    ]
    for e in _walk(effects):
        field = e.get("set", {}).get("field")
        if field == "_body_size_rank":
            if e["set"]["value"] == 3:
                e["set"]["value"] = 2
            elif e["set"]["value"] == 2:
                e["set"]["value"] = 1
    for field, base in (("_former_skills", "base_skills"), ("_former_feats", "base_feats")):
        index = next(i for i, e in enumerate(effects) if e.get("set", {}).get("field") == field)
        effects.insert(
            index + 1,
            _when(NATIVE, [_set(field, "target_state.reincarnate_native_traits." + base)]),
        )
    mutate = next(
        i for i, e in enumerate(effects) if e.get("set_participant", {}).get("field") == "species"
    )
    effects[mutate:mutate] = [
        _require(
            "len(skill_proficiencies) == len(_former_skills) or "
            "((_body_species == 'human' or _body_species == 'elf') and "
            "len(skill_proficiencies) == len(_former_skills) + 1)",
            "Keep the known non-species skills and only the new body's selected skill.",
        ),
        _when(
            "_body_species == 'human' or _body_species == 'elf'",
            [
                _require(
                    "species_skill not in _former_skills and len(skill_proficiencies) == len(_f"
                    "ormer_skills) + 1",
                    "Choose a distinct new-body skill; duplicate or unknown skill provenance is"
                    " unsupported.",
                ),
            ],
        ),
        _require(
            "(_body_species == 'human' and species_origin_feat != '' and "
            "species_origin_feat not in _former_feats and species_origin_feat in origin_feats "
            "and len(origin_feats) == len(_former_feats) + "
            "1) or "
            "(_body_species != 'human' and len(origin_feats) == len(_former_feats))",
            "Keep the known non-species feats and select the new Human's additional Origin feat.",
        ),
        _set("_reincarnate_base_resistances", []),
        _when(
            "target_state.damage_resistances != null",
            [
                _set("_reincarnate_base_resistances", "target_state.damage_resistances"),
            ],
        ),
        _set("_reincarnate_base_max_hp", "target_state.max_hp"),
        _when(
            NATIVE,
            [
                _set(
                    "_reincarnate_base_resistances",
                    "target_state.reincarnate_native_traits.base_resistances",
                ),
                _set(
                    "_reincarnate_base_max_hp",
                    "target_state.max_hp - target_state.reincarnate_native_traits.hp_bonus",
                ),
            ],
        ),
        _require(
            "_reincarnate_base_resistances != null and _reincarnate_base_max_hp >= 1",
            "Retain the real non-species defenses and HP contributions.",
        ),
        _set("_reincarnate_resistances", []),
        _each(
            "_reincarnate_base_resistances",
            "_body_resistance",
            [
                {"list_push": {"list": "_reincarnate_resistances", "value": "_body_resistance"}},
            ],
        ),
        _set(
            "_reincarnate_resistance",
            "definitions.srd_reincarnate_native_traits[_body_species].resistance",
        ),
        _when(
            "_body_species == 'dragonborn'",
            [
                _require(
                    "lineage in definitions.srd_reincarnate_draconic_ancestry",
                    "Choose the printed Draconic Ancestry.",
                ),
                _set(
                    "_reincarnate_resistance",
                    "definitions.srd_reincarnate_draconic_ancestry[lineage]",
                ),
            ],
        ),
        _when(
            "_body_species == 'tiefling'",
            [
                _set("_reincarnate_legacy_count", 0),
                _each(
                    "spellcasting_grants",
                    "_body_grant",
                    [
                        _when(
                            "_body_grant.source in definitions.srd_reincarnate_fiendish_resista"
                            "nces",
                            [
                                _set("_reincarnate_legacy_count", "_reincarnate_legacy_count + 1"),
                                _set(
                                    "_reincarnate_resistance",
                                    "definitions.srd_reincarnate_fiendish_resistances[_body_gra"
                                    "nt.source]",
                                ),
                            ],
                        ),
                    ],
                ),
                _require(
                    "_reincarnate_legacy_count == 1",
                    "Use the actual validated Tiefling grant's single legacy.",
                ),
            ],
        ),
        _when(
            "_reincarnate_resistance != '' and _reincarnate_resistance not in _reincarnate_resi"
            "stances",
            [
                {
                    "list_push": {
                        "list": "_reincarnate_resistances",
                        "value": "_reincarnate_resistance",
                    }
                },
            ],
        ),
        _set("_reincarnate_hp_bonus", 0),
        _when(
            "_body_species == 'dwarf'",
            [_set("_reincarnate_hp_bonus", "target_character.data.level")],
        ),
        _set("target_state.damage_resistances", "_reincarnate_resistances"),
        _set("target_state.max_hp", "_reincarnate_base_max_hp + _reincarnate_hp_bonus"),
        _set(
            "target_state.reincarnate_native_traits",
            {
                "owner": "_target_state_id",
                "species": "_body_species",
                "source_sha256": repr(SOURCE),
                "base_skills": "_former_skills",
                "base_feats": "_former_feats",
                "base_resistances": "_reincarnate_base_resistances",
                "hp_bonus": "_reincarnate_hp_bonus",
                "level": "target_character.data.level",
                "installed_skills": "skill_proficiencies",
                "installed_feats": "origin_feats",
                "installed_resistances": "_reincarnate_resistances",
            },
        ),
    ]
    return tool


def _save_consumers(tools):
    """Add native Advantage before the existing D20 calculation, retaining cancellation."""
    d20 = deepcopy(tools["d20_test"])
    advantages = [
        e
        for e in _walk(d20["effects"])
        if e.get("set", {}).get("field") == "_effective_advantage"
        and "_condition_advantage" in str(e["set"]["value"])
    ]
    if len(advantages) != 1:
        raise ValueError("Use the actual D20 Advantage calculation once.")
    native_advantage = _when(
        "actor_state.reincarnate_native_traits != null and kind == 'saving_throw'",
        [
            _call(
                "qualify_srd_reincarnate_native_body",
                {"target_state": "{_actor_state_id}"},
                ["_reincarnate_traits"],
            ),
            _set(
                "_effective_advantage",
                "_effective_advantage == true or ability in _reincarnate_traits.save_abilities "
                "or save_avoids_condition in _reincarnate_traits.condition_saves",
            ),
        ],
    )
    pending = [d20["effects"]]
    while pending:
        sequence = pending.pop()
        for index, effect in enumerate(sequence):
            if effect is advantages[0]:
                sequence.insert(index + 1, native_advantage)
                pending.clear()
                break
            pending.extend(effect[key] for key in ("on_true", "on_false") if key in effect)
            if "for_each" in effect:
                pending.append(effect["for_each"]["do"])
    immunity = deepcopy(tools["check_srd_target_condition_immunity"])
    immunity["effects"].append(
        _when(
            NATIVE,
            [
                _call(
                    "qualify_srd_reincarnate_native_body",
                    {"target_state": "{_target_state_id}"},
                    ["_reincarnate_traits"],
                ),
                _set(
                    "_source_condition_save_advantage",
                    "_source_condition_save_advantage == true or condition in _reincarnate_trai"
                    "ts.condition_saves",
                ),
            ],
        )
    )
    return {"d20_test": d20, "check_srd_target_condition_immunity": immunity}


def _rest_consumers(tools):
    """Track genuine conscious Trance in the same rest clock and retain ordinary rest benefits."""
    changed = {
        name: deepcopy(tools[name])
        for name in (
            "begin_srd_rest",
            "set_srd_rest_activity",
            "track_srd_rest_elapsed",
            "complete_srd_rest",
        )
    }
    for name in ("begin_srd_rest", "set_srd_rest_activity"):
        activity = next(p for p in changed[name]["parameters"] if p["name"] == "activity")
        activity["enum_values"].append("trance")
    changed["begin_srd_rest"]["effects"].insert(0, _set("target_state.rest_trance_ticks", 0))
    changed["set_srd_rest_activity"]["effects"].insert(
        0,
        _when(
            "activity == 'trance'",
            [
                _call(
                    "qualify_srd_reincarnate_native_body",
                    {"target_state": "{_target_state_id}"},
                    ["_reincarnate_traits"],
                ),
                _require(
                    "_reincarnate_traits.trance == true and target_state.rest_kind == 'long' and "
                    "not ('unconscious' in target_state.conditions or 'incapacitated' in target"
                    "_state.conditions or "
                    "'paralyzed' in target_state.conditions or 'stunned' in target_state.condit"
                    "ions or 'petrified' in target_state.conditions)",
                    "Only this actual new Elf body can consciously Trance for a Long Rest.",
                ),
            ],
        ),
    )
    track = changed["track_srd_rest_elapsed"]
    sleep = [
        e
        for e in _walk(track["effects"])
        if e.get("condition") == "target_state.rest_activity == 'sleep'"
    ]
    if len(sleep) != 1:
        raise ValueError("Keep the actual elapsed rest interval and native segment accumulation.")
    original = deepcopy(sleep[0])
    sleep[0].clear()
    sleep[0].update(
        _when(
            "true",
            [
                original,
                _when(
                    "target_state.rest_activity == 'trance'",
                    [
                        _set(
                            "target_state.rest_trance_ticks",
                            "target_state.rest_trance_ticks + _elapsed_rest_ticks",
                        ),
                        _set(
                            "target_state.rest_segment_ticks",
                            "target_state.rest_segment_ticks + _elapsed_rest_ticks",
                        ),
                    ],
                ),
            ],
        )
    )
    complete = changed["complete_srd_rest"]
    total = next(
        i
        for i, e in enumerate(complete["effects"])
        if e.get("set", {}).get("field") == "_rest_total_ticks"
    )
    complete["effects"][total + 1 : total + 1] = [
        _set("_reincarnate_trance_rest", False),
        _when(
            "target_state.rest_kind == 'long' and target_state.rest_trance_ticks != null and "
            "target_state.rest_trance_ticks > 0",
            [
                _call(
                    "qualify_srd_reincarnate_native_body",
                    {"target_state": "{_target_state_id}"},
                    ["_reincarnate_traits"],
                ),
                _require(
                    "_reincarnate_traits.trance == true and target_state.rest_trance_ticks + 0."
                    "000000001 >= "
                    "4800 + target_state.rest_interruptions * 1200",
                    "Complete four real hours of Trance, plus actual rest interruptions.",
                ),
                _set("_rest_total_ticks", "_rest_total_ticks + target_state.rest_trance_ticks"),
                _set("_reincarnate_trance_rest", True),
            ],
        ),
    ]
    guards = [
        e["require"]
        for e in complete["effects"]
        if "require" in e
        and e["require"]["error"]
        in {
            "A Long Rest requires at least eight hours of rest.",
            "A resumed Long Rest requires one additional hour of rest per interruption.",
            "A Long Rest requires at least six hours asleep.",
        }
    ]
    if len(guards) != 3:
        raise ValueError("Retain the actual ordinary Long Rest time and sleep guards.")
    for guard in guards:
        guard["condition"] += " or _reincarnate_trance_rest == true"
    return changed


def extend_runtime(text):
    """Bind after final native D20/rest owners; this does not create replacement-body support."""
    _validate_source()
    tools = _load_runtime(text)["tools"]
    if "qualify_srd_reincarnate_native_body" in tools:
        return text
    changed = _body_tools() | _save_consumers(tools) | _rest_consumers(tools)
    completion = tools["complete_srd_reincarnation"]
    if any("require" in e for e in completion["effects"]):
        changed["complete_srd_reincarnation"] = _complete(completion)
    else:
        delegates = [
            e
            for e in completion["effects"]
            if e.get("call") == "complete_srd_reincarnation_internal"
        ]
        if len(delegates) != 1:
            raise ValueError("Follow only the actual saved public Reincarnate internal delegate.")
        completion = deepcopy(completion)
        completion["parameters"].append(_param("species_origin_feat", required=False, default=""))
        delegate = next(
            e
            for e in completion["effects"]
            if e.get("call") == "complete_srd_reincarnation_internal"
        )
        delegate["params"]["species_origin_feat"] = "{species_origin_feat}"
        changed["complete_srd_reincarnation"] = completion
        changed["complete_srd_reincarnation_internal"] = _complete(
            tools["complete_srd_reincarnation_internal"]
        )
    _normalize(changed)
    definitions = {
        "srd_reincarnate_native_traits": TRAITS,
        "srd_reincarnate_draconic_ancestry": ANCESTRY,
        "srd_reincarnate_fiendish_resistances": LEGACIES,
    }
    text = text.replace(
        "definitions:\n",
        "definitions:\n"
        + "".join("  " + name + ": " + json.dumps(row) + "\n" for name, row in definitions.items()),
        1,
    )
    return _replace_tools(text, changed)
