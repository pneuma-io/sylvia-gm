"""Animate Dead's printed Skeleton uses the existing body, command and NPC attack owners."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

if __package__:
    from .srd_divination_spells import _normalize
    from .srd_scene_relations import POSITION_EXPORTS, _snapshot, _tool_scene
    from .srd_spell_summoning import (
        _animated_attack_tools,
        _scene_position,
        _walk,
        _zombie_actor_tools,
        _zombie_source_profile,
    )
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from .srd_timed_senses import _loop, _optional, _replace_tools
else:
    from srd_divination_spells import _normalize
    from srd_scene_relations import POSITION_EXPORTS, _snapshot, _tool_scene
    from srd_spell_summoning import (
        _animated_attack_tools,
        _scene_position,
        _walk,
        _zombie_actor_tools,
        _zombie_source_profile,
    )
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from srd_timed_senses import _loop, _optional, _replace_tools

NATIVE = (
    "actor.created_spell_id == 'animate_dead' and actor.created_form_id == 'skeleton' "
    "and actor.form_attack_in_progress != true"
)
BONES = _optional(
    "animated_bones",
    "array",
    [],
    max_items=13,
    items={
        "type": "object",
        "additionalProperties": False,
        "required": ["bones_scene_id", "individual_id", "other_language", "appearance_height"],
        "properties": {
            "bones_scene_id": {"type": "string", "minLength": 1},
            "individual_id": {"type": "string", "minLength": 1},
            "other_language": {"type": "string", "minLength": 1},
            "appearance_height": {"type": "number", "minimum": 0.01, "maximum": 1000},
        },
    },
)


def source_profile():
    """Bind the complete printed block, including its next-page native weapon actions."""
    corpus = Path(__file__).resolve().parents[1] / "rulesets/srd-lite/content/corpus"
    pages = {
        p["page"]: p for p in json.loads((corpus / "monsters.json").read_text())["page_records"]
    }
    for number, digest in (
        (325, "e105a53a53d8f9178ca6b374234801af440b699bf09062236a803dea979f0549"),
        (326, "a86e8103201bd74abb26b131b8666c29f2b2b85fb296600ee6ba3c80e0c9ea1c"),
    ):
        assert (
            pages[number]["text_sha256"]
            == hashlib.sha256(pages[number]["text"].encode()).hexdigest()
            == digest
        )
    block = "Skeleton\nMedium Undead" + pages[325]["text"].split("Skeleton\nMedium Undead", 1)[1]
    block += "\n" + pages[326]["text"].split("Actions\n", 1)[1].split("Warhorse Skeleton", 1)[0]
    digest = hashlib.sha256(block.encode()).hexdigest()
    assert digest == "a88cfe2aeabd62c7c9ea3eb8cf9545a0e16ece50f25b9ea7374c630c9d2dbcb6"
    assert "Gear Shortbow, Shortsword" in block and "Vulnerabilities Bludgeoning" in block
    scores = {
        "strength": 10,
        "dexterity": 16,
        "constitution": 15,
        "intelligence": 6,
        "wisdom": 8,
        "charisma": 5,
    }
    return {
        "block_sha256": digest,
        "armor_class": 14,
        "hit_points": 13,
        "speed": 30,
        "size_rank": 2,
        "initiative_modifier": 3,
        "challenge_rating_eighths": 2,
        "ability_scores": scores,
        "saving_throw_modifiers": {a: (v - 10) // 2 for a, v in scores.items()},
        "damage_immunities": ["poison"],
        "damage_vulnerabilities": ["bludgeoning"],
        "condition_immunities": ["exhaustion", "poisoned"],
        "darkvision_feet": 60,
        "passive_perception": 9,
        "attacks": {
            "Shortsword": {
                "attack_modifier": 5,
                "damage_expression": "d6",
                "damage_modifier": 3,
                "damage_type": "piercing",
                "normal_range": 5,
                "long_range": 5,
            },
            "Shortbow": {
                "attack_modifier": 5,
                "damage_expression": "d6",
                "damage_modifier": 3,
                "damage_type": "piercing",
                "normal_range": 80,
                "long_range": 320,
            },
        },
    }


def _bones_tools():
    """Use an actual admitted bones object; consumption does not invent a corpse or death."""
    qualify = _tool_scene(
        "Qualify this real unconsumed Humanoid bones scene object.",
        [
            _parameter("bones_id"),
        ],
        [
            _call("srd_information_scene_entity", {"subject": "{bones_id}"}, POSITION_EXPORTS),
            _require(
                "_info_scene_entity.id == bones_id and "
                "_info_scene_entity.metadata.participant_ref == null "
                "and _info_scene_entity.metadata.is_object == true and "
                "_info_scene_entity.metadata.humanoid_bones == true and "
                "(_info_scene_entity.metadata.bones_origin_size_rank == 1 or "
                "_info_scene_entity.metadata.bones_origin_size_rank == 2)",
                "Admit one actual Small or Medium Humanoid bones object.",
            ),
            _require(
                "_info_scene_entity.metadata.bones_consumed_by == null",
                "These actual bones already belong to an animated body.",
            ),
            _set("_bones_scene", "_info_scene_entity"),
            _set("_bones_origin", "_info_position"),
        ],
    )
    consume = _tool_scene(
        "Retain the original bones scene identity and its one animated individual.",
        [
            _parameter("bones_id"),
            _entity("actor_state"),
        ],
        [
            _call(
                "qualify_srd_animated_bones_source",
                {"bones_id": "{bones_id}"},
                ("_bones_scene", "_bones_origin"),
            ),
            _require(
                "actor_state.created_bone_source_scene_id == bones_id and "
                "actor_state.created_form_id == 'skeleton' and "
                "actor_state.created_individual_id == _actor_state_id",
                "Consume only the exact bones of this genuinely prepared individual.",
            ),
            *_snapshot(),
            _set("_bone_consumption", {"bones_consumed_by": "_actor_state_id"}),
            _set("_consumed_bone_metadata", "merge(_bones_scene.metadata, _bone_consumption)"),
            {
                "spatial_mutation": {
                    "scene": "state.spatial_scene",
                    "map_id": "{state.spatial_scene.map.id}",
                    "expected_revision": "{_info_snapshot.geometry_revision}",
                    "target": {"kind": "entity", "id": "{bones_id}"},
                    "patch": {"metadata": "{_consumed_bone_metadata}", "active": False},
                },
                "store_as": "_consumed_bones_scene",
            },
        ],
    )
    return {
        "qualify_srd_animated_bones_source": qualify,
        "consume_srd_animated_bones_source": consume,
    }


def _actor_tools(tools, row):
    """Project source-fixed Skeleton facts through the existing prepared-NPC body installer."""
    owned = _zombie_actor_tools(tools)
    prepare = owned["prepare_srd_source_zombie"]
    finish = owned["finish_srd_zombie_source_actor"]
    prepare["parameters"] = [p for p in prepare["parameters"] if p["name"] != "corpse_state"]
    prepare["parameters"].append(_parameter("bones_id"))
    prepare["description"] = (
        "Install only the printed Skeleton from one real qualifying bones object."
    )
    fixed = {
        "name": "'Skeleton'",
        "source": "'SRD5.2.1 Skeleton pp325–326'",
        **{
            key: row[key]
            for key in (
                "armor_class",
                "hit_points",
                "speed",
                "size_rank",
                "initiative_modifier",
                "challenge_rating_eighths",
                "damage_immunities",
                "damage_vulnerabilities",
            )
        },
        **{a + "_score": v for a, v in row["ability_scores"].items()},
        **{a + "_save": v for a, v in row["saving_throw_modifiers"].items()},
    }
    prepare["effects"] = [
        e for e in prepare["effects"] if "corpse_state." not in str(e.get("require", {}))
    ]
    for effect in _walk(prepare["effects"]):
        setting = effect.get("set", {})
        field = setting.get("field", "")
        if field.startswith("_zombie_") and field.removeprefix("_zombie_") in fixed:
            setting["value"] = deepcopy(fixed[field.removeprefix("_zombie_")])
        if effect.get("call") == "finish_srd_zombie_source_actor":
            effect["call"] = "finish_srd_skeleton_source_actor"
            effect["params"].pop("corpse_state")
            effect["params"]["bones_id"] = "{bones_id}"
    prepare["effects"].insert(
        0, _call("qualify_srd_animated_bones_source", {"bones_id": "{bones_id}"})
    )
    finish["parameters"] = [p for p in finish["parameters"] if p["name"] != "corpse_state"]
    finish["parameters"].append(_parameter("bones_id"))
    finish["effects"] = [e for e in finish["effects"] if "corpse_state." not in str(e)]
    replacements = {
        "actor_state.created_form_id": "'skeleton'",
        "actor_state.created_source_block_sha256": repr(row["block_sha256"]),
        "actor_state.created_body_source_state_id": None,
        "_zombie_physical_body_source": "bones_id",
        "actor_state.created_source_attack": {
            "name": "'Shortsword'",
            **{
                k: repr(v) if isinstance(v, str) else v
                for k, v in row["attacks"]["Shortsword"].items()
            },
        },
        "actor_state.condition_immunities": row["condition_immunities"],
        "actor_state.darkvision_feet": row["darkvision_feet"],
        "actor_state.passive_perception": row["passive_perception"],
        "actor_state.resources.hit_die": 2,
        "actor_state.resource_maxima": {"hit_die": 2},
        "actor_state.constitution_modifier": 2,
    }
    for effect in _walk(finish["effects"]):
        setting = effect.get("set", {})
        if setting.get("field") in replacements:
            setting["value"] = deepcopy(replacements[setting["field"]])
        if setting.get("field") in {
            "state.created_actor_records.{_actor_state_id}",
            "lasting_state.created_actor_records.{_actor_state_id}",
        }:
            setting["value"].update(
                body_source=None,
                body_source_id="bones_id",
                bone_source_scene_id="bones_id",
                source_block_sha256=repr(row["block_sha256"]),
            )
    finish["effects"] += [
        _set("actor_state.created_bone_source_scene_id", "bones_id"),
        _set(
            "actor_state.created_source_attacks",
            {
                name: {k: repr(v) if isinstance(v, str) else v for k, v in values.items()}
                for name, values in row["attacks"].items()
            },
        ),
        _set("actor_state.inventory", ["'Shortbow'", "'Shortsword'"]),
        _call(
            "consume_srd_animated_bones_source",
            {"bones_id": "{bones_id}", "actor_state": "{_actor_state_id}"},
        ),
    ]
    finish["description"] = (
        "Retain the actual Skeleton body and the consumed original bones identity."
    )
    return {"prepare_srd_source_skeleton": prepare, "finish_srd_skeleton_source_actor": finish}


def _creator():
    """Transform the selected bones at their actual scene position and ordinary spell range."""
    return _tool_scene(
        "Transform the actual unconsumed bones into their printed Skeleton.",
        [
            _parameter("bones_id"),
            _entity("caster_state"),
            _parameter("individual_id"),
            _parameter("other_language"),
            _parameter("appearance_height", "number", minimum=0.01),
        ],
        [
            _call(
                "qualify_srd_animated_bones_source",
                {"bones_id": "{bones_id}"},
                ("_bones_scene", "_bones_origin"),
            ),
            *_scene_position("{_caster_state_id}", "_skeleton_caster_origin"),
            _require(
                "_info_scene_entity.metadata.plane != null and "
                "_info_scene_entity.metadata.plane == _bones_scene.metadata.plane",
                "Animate only genuine bones in the caster's actual plane.",
            ),
            _call(
                "prepare_srd_source_skeleton",
                {
                    "creature_id": "{individual_id}",
                    "bones_id": "{bones_id}",
                    "source_caster": "{_caster_state_id}",
                    "other_language": "{other_language}",
                },
            ),
            _call(
                "create_srd_source_actor_scene",
                {
                    "caster_state": "{_caster_state_id}",
                    "summon_id": "{individual_id}",
                    "spell": "animate_dead",
                    "range_feet": 10,
                    "half_extent": 2.5,
                    "creature_type": "undead",
                    "requires_sight": False,
                    "created_actor_position": "{_bones_origin}",
                    "created_actor_height": "{appearance_height}",
                    "created_actor_lighting": "{_bones_scene.metadata.lighting}",
                },
            ),
        ],
    )


def _attack_tools(tools, row):
    """Retain native NPC D20/damage owners and source-authenticate both printed weapons."""
    zombie = _zombie_source_profile()
    qualify = deepcopy(_animated_attack_tools()["qualify_srd_animated_zombie_attack"])

    def bind(value):
        if isinstance(value, str):
            return (
                value.replace(zombie["source_stat_block_sha256"], row["block_sha256"])
                .replace("'zombie'", "'skeleton'")
                .replace("Zombie", "Skeleton")
            )
        if isinstance(value, list):
            return [bind(child) for child in value]
        if isinstance(value, dict):
            return {key: bind(child) for key, child in value.items()}
        return value

    qualify = bind(qualify)
    qualify["effects"].append(
        _require(
            "_animated_slam_record.bone_source_scene_id == "
            "actor_state.created_bone_source_scene_id "
            "and actor_state.created_bone_source_scene_id != null",
            "Retain the actual consumed bones source, never a fabricated original corpse.",
        )
    )
    attack = deepcopy(tools["resolve_adjudicated_creature_attack"])
    guards = [
        p
        for p in attack["pre_conditions"]
        if p["error"]
        in {
            "Use Zombie's printed single unarmed Slam attack and damage profile.",
            "Only Zombie's native attack is currently source-qualified.",
        }
    ]
    if len(guards) != 2:
        raise ValueError("Extend the existing source-owned animated native attack fence.")
    for guard in guards:
        guard["condition"] += " or actor.created_form_id == 'skeleton'"
    native_branches = [
        e for e in attack["effects"] if "qualify_srd_animated_zombie_attack" in str(e)
    ]
    if len(native_branches) != 1:
        raise ValueError("Keep one actual existing Zombie native-body qualification.")
    native_branches[0]["condition"] += " and actor.created_form_id != 'skeleton'"
    attack["pre_conditions"].append(
        {
            "condition": "not ("
            + NATIVE
            + ") or (attack_modifier == 5 and damage_expression == 'd6' "
            "and damage_modifier == 3 and damage_type == 'piercing' "
            "and use_multiattack != true and ((attack_name == 'Shortsword' "
            "and attack_mode == 'melee' and normal_range == 5 and long_range == 5) or "
            "(attack_name == 'Shortbow' and attack_mode == 'ranged' "
            "and normal_range == 80 and long_range == 320)))",
            "error": "Use only the actual printed Skeleton weapon routine.",
        }
    )
    attack["effects"].insert(
        0,
        _when(
            NATIVE,
            [
                _call("qualify_srd_animated_skeleton_attack", {"actor_state": "{_actor_id}"}),
                _require(
                    "attack_uses_weapon == true",
                    "Skeleton's printed weapons are real weapon attacks.",
                ),
                _require(
                    "attack_name in actor.inventory",
                    "This Skeleton no longer carries that printed weapon.",
                ),
                _call(
                    "qualify_srd_created_actor_attack_geometry",
                    {"actor_state": "{_actor_id}", "target_state": "{_target_id}"},
                    ("_created_attack_distance",),
                ),
                _require(
                    "attack_distance == _created_attack_distance",
                    "Use the actual Skeleton and target distance.",
                ),
            ],
        ),
    )
    return {
        "qualify_srd_animated_skeleton_attack": qualify,
        "resolve_adjudicated_creature_attack": attack,
    }


def extend_runtime(text):
    """Compose after actual animated commands/attacks and before public cast/reaction derivation."""
    tools = _load_runtime(text)["tools"]
    if "create_srd_animated_skeleton_body" in tools:
        return text
    row = source_profile()
    changed = _bones_tools() | _actor_tools(tools, row) | _attack_tools(tools, row)
    changed["create_srd_animated_skeleton_body"] = _creator()
    cast = deepcopy(tools["cast_spell_animated_undead_creature"])
    cast["parameters"].append(deepcopy(BONES))
    create_branch = next(
        e for e in cast["effects"] if e.get("condition") == "animated_mode == 'create'"
    )
    create_branch["on_true"].insert(0, _set("_animated_bone_choices", "animated_bones"))
    for effect in _walk(create_branch["on_true"]):
        for body in (effect.get("require", {}), effect.get("set", {})):
            for key in ("condition", "value"):
                value = body.get(key)
                if isinstance(value, str) and "len(_animated_body_choices)" in value:
                    body[key] = value.replace(
                        "len(_animated_body_choices)",
                        "(len(_animated_body_choices) + len(_animated_bone_choices))",
                    )
    create_branch["on_true"].insert(
        -1,
        _loop(
            "_animated_bone_choices",
            "_animated_bone_choice",
            [
                _require(
                    "_animated_bone_choice.bones_scene_id not in _animated_selected_corpses and "
                    "_animated_bone_choice.individual_id not in _animated_selected_ids",
                    "Each Skeleton must come from a distinct real bones source and individual.",
                ),
                {
                    "list_push": {
                        "list": "_animated_selected_corpses",
                        "value": "_animated_bone_choice.bones_scene_id",
                    }
                },
                {
                    "list_push": {
                        "list": "_animated_selected_ids",
                        "value": "_animated_bone_choice.individual_id",
                    }
                },
                _call(
                    "create_srd_animated_skeleton_body",
                    {
                        "bones_id": "{_animated_bone_choice.bones_scene_id}",
                        "caster_state": "{_caster_state_id}",
                        "individual_id": "{_animated_bone_choice.individual_id}",
                        "other_language": "{_animated_bone_choice.other_language}",
                        "appearance_height": "{_animated_bone_choice.appearance_height}",
                    },
                ),
            ],
        ),
    )
    create_branch["on_false"].insert(
        0,
        _require(
            "len(animated_bones) == 0", "Renew actual individuals rather than consuming bones."
        ),
    )
    changed["cast_spell_animated_undead_creature"] = cast
    for name in ("cast_spell", "cast_spell_node", "cast_spell_outside_combat"):
        if name not in tools:
            continue
        public = deepcopy(tools[name])
        public["parameters"].append(deepcopy(BONES))
        calls = [
            e
            for e in _walk(public["effects"])
            if e.get("call") == "cast_spell_animated_undead_creature"
        ]
        if not calls:
            calls = [e for e in _walk(public["effects"]) if e.get("call") == "cast_spell_node"]
        if len(calls) != 1:
            raise ValueError("Keep the real existing animated body dispatcher.")
        calls[0]["params"]["animated_bones"] = "{animated_bones}"
        changed[name] = public
    for name in ("save_srd_animated_undead_order", "start_srd_animated_undead_turn"):
        turn = deepcopy(tools[name])
        body_guards = [
            e["require"]
            for e in _walk(turn["effects"])
            if "actor_state.created_form_id == 'zombie'"
            in e.get("require", {}).get("condition", "")
        ]
        if len(body_guards) != 1:
            raise ValueError("Keep the existing native form fence and ordinary command owner.")
        body_guards[0]["condition"] = body_guards[0]["condition"].replace(
            "actor_state.created_form_id == 'zombie'",
            "actor_state.created_form_id in ['zombie', 'skeleton']",
        )
        native, remaining = body_guards[0]["condition"].split(
            " and actor_state.form_statistics_replaced", 1
        )
        turn["effects"].insert(0, _set("_animated_native_body", native))
        body_guards[0]["condition"] = (
            "_animated_native_body == true and actor_state.form_statistics_replaced" + remaining
        )
        changed[name] = turn
    begin = deepcopy(tools["begin_combat_turn"])
    branches = [e for e in begin["effects"] if "start_srd_animated_undead_turn" in str(e)]
    if len(branches) != 1:
        raise ValueError("Keep one actual final controlled native-body turn observer.")
    old_condition = branches[0]["condition"]
    if "actor.created_form_id == 'zombie'" not in old_condition:
        raise ValueError("Extend only the existing source-Zombie turn observer.")
    branches[0]["condition"] = old_condition.replace(
        "actor.created_form_id == 'zombie'",
        "(actor.created_form_id == 'zombie' or actor.created_form_id == 'skeleton')",
    )
    changed["begin_combat_turn"] = begin
    language = deepcopy(tools["resolve_srd_spell_language"])
    for effect in _walk(language["effects"]):
        for key in ("condition",):
            if (
                isinstance(effect.get(key), str)
                and "created_spell_id == 'animate_dead'" in effect[key]
            ):
                effect[key] = effect[key].replace(
                    "created_form_id == 'zombie'", "created_form_id in ['zombie', 'skeleton']"
                )
        require = effect.get("require", {})
        if "created_spell_id == 'animate_dead'" in require.get("condition", ""):
            require["condition"] = require["condition"].replace(
                "created_form_id == 'zombie'", "created_form_id in ['zombie', 'skeleton']"
            )
    changed["resolve_srd_spell_language"] = language
    _normalize(changed)
    return _replace_tools(text, changed)
