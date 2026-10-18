"""Finger of Death's actual Humanoid kill rises on its caster's next turn."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

if __package__:
    from .srd_divination_spells import _normalize
    from .srd_native_wight_rise import _scene_constructor
    from .srd_qualified_packet_implementations import _delegate
    from .srd_spell_summoning import _undead_fortitude_tools, _walk, _zombie_actor_tools
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
        _when,
    )
    from .srd_timed_senses import _loop, _optional, _public, _push, _replace_tools
else:
    from srd_divination_spells import _normalize
    from srd_native_wight_rise import _scene_constructor
    from srd_qualified_packet_implementations import _delegate
    from srd_spell_summoning import _undead_fortitude_tools, _walk, _zombie_actor_tools
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
        _when,
    )
    from srd_timed_senses import _loop, _optional, _public, _push, _replace_tools

SPELL = "finger_of_death"
PUBLIC_TOOLS = {"admit_srd_finger_death_zombie_language", "command_srd_finger_death_zombie"}


def _validate_source():
    """Keep the delayed body consequence bound to the pinned instantaneous spell."""
    path = Path(__file__).resolve().parents[2] / "rulesets/dnd-srd-5.2.1/content/corpus/spells.json"
    page = next(p for p in json.loads(path.read_text())["page_records"] if p["page"] == 131)
    assert hashlib.sha256(page["text"].encode()).hexdigest() == (
        "e30140a7e2c635c648a0cb5b58f7b10282fae36f725304a62c49d6b55b7c4510"
    )
    block = " ".join(
        page["text"].split("Finger of Death\n", 1)[1].split("Fireball\n", 1)[0].split()
    )
    for clause in (
        "7d8 + 30 Necrotic damage",
        "A Humanoid killed by this spell rises",
        "start of your next turn as a Zombie",
        "follows your verbal orders",
    ):
        assert clause in block


def _record_tools():
    """Retain a death witnessed by the actual spell damage leaf, never a caller kill verdict."""
    record = _tool(
        "Retain this actual Finger of Death Humanoid kill.",
        [
            _entity("caster_state"),
            _entity("corpse_state"),
        ],
        [
            _require(
                "corpse_state.is_alive == false and corpse_state.death_tick != null and "
                "corpse_state.death_tick == lasting_state.lasting_tick and "
                "corpse_state.death_creature_type == 'humanoid' and "
                "corpse_state.death_was_undead != true",
                "Record this spell's actual Humanoid death.",
            ),
            _require(
                "corpse_state.current_body_source_id == null and "
                "corpse_state.magic_jar_role == null and "
                "corpse_state.form_statistics_replaced != true",
                "Transferred, occupied and replaced death bodies remain unsupported.",
            ),
            _when(
                "state.finger_death_rise_ids == null",
                [
                    _set("state.finger_death_rise_ids", []),
                    _set("state.finger_death_rise_records", {}),
                    _set("state.finger_death_rise_sequence", 0),
                ],
            ),
            _require(
                "len(state.finger_death_rise_ids) < 128",
                "Resolve retained body consequences first.",
            ),
            _set("state.finger_death_rise_sequence", "state.finger_death_rise_sequence + 1"),
            _call(
                "record_srd_result_text",
                {"text": "finger_rise_{state.finger_death_rise_sequence}"},
                ("_recorded_result_text",),
            ),
            _set("_finger_rise_id", "_recorded_result_text"),
            _set("_finger_pending", "state.finger_death_rise_ids"),
            _push("_finger_pending", "_finger_rise_id"),
            _set("state.finger_death_rise_ids", "_finger_pending"),
            _set("corpse_state.finger_death_rise_id", "_finger_rise_id"),
            _set("_finger_caster_turn", 0),
            _when(
                "caster_state.turns_started != null",
                [
                    _set("_finger_caster_turn", "caster_state.turns_started"),
                ],
            ),
            _set(
                "state.finger_death_rise_records.{_finger_rise_id}",
                {
                    "active": True,
                    "status": "'pending'",
                    "caster": "_caster_state_id",
                    "body": "_corpse_state_id",
                    "death_tick": "corpse_state.death_tick",
                    "caster_turn": "_finger_caster_turn + 1",
                    "other_language": None,
                    "sequence": "state.finger_death_rise_sequence",
                    "zombie": None,
                    "spell": repr(SPELL),
                },
            ),
            _set("caster_state.has_finger_death_rises", True),
        ],
    )
    language = _public(
        _tool(
            "Admit the printed Zombie's other understood language before it rises.",
            [
                _entity("caster_state"),
                _entity("corpse_state"),
                _parameter("other_language"),
            ],
            [
                _require(
                    "len(other_language) > 0 and len(other_language) <= 80 and "
                    "other_language != 'common'",
                    "Admit the printed other language in 1 to 80 characters.",
                ),
                _set("_finger_rise_id", "corpse_state.finger_death_rise_id"),
                _set("_finger_rise", "state.finger_death_rise_records[_finger_rise_id]"),
                _require(
                    "_finger_rise.active == true and _finger_rise.caster == _caster_state_id and "
                    "_finger_rise.body == _corpse_state_id",
                    "Use this caster's actual pending spell kill.",
                ),
                _set(
                    "state.finger_death_rise_records.{_finger_rise_id}.other_language",
                    "other_language",
                ),
            ],
        ),
        "spells",
        "Zombie source language",
        [("Other understood language", "other_language")],
    )
    cancel = _tool(
        "Cancel only the old pending consequence after genuine original revival.",
        [
            _entity("target_state"),
        ],
        [
            _require(
                "target_state.is_alive == true and target_state.death_tick == null",
                "Cancel only after genuine original revival.",
            ),
            _set("_finger_rise_id", "target_state.finger_death_rise_id"),
            _set("state.finger_death_rise_records.{_finger_rise_id}.active", False),
            _set("state.finger_death_rise_records.{_finger_rise_id}.status", "'cancelled_revival'"),
        ],
    )
    return {
        "record_srd_finger_death_rise": record,
        "admit_srd_finger_death_zombie_language": language,
        "cancel_srd_finger_death_rise_after_revival": cancel,
    }


def _constructors(tools):
    """Reuse complete Zombie stats and actual corpse consumption without Animate Dead control."""
    original = _zombie_actor_tools(tools)
    prepare, finish = (
        original["prepare_srd_source_zombie"],
        original["finish_srd_zombie_source_actor"],
    )
    prepare["pre_conditions"] = [
        p
        for p in prepare["pre_conditions"]
        if p["condition"] != "combat.encounter_active != true and combat.turn_active != true"
    ]
    prepare["parameters"].append(_parameter("rise_id"))
    for effect in _walk(prepare["effects"]):
        if effect.get("call") == "finish_srd_zombie_source_actor":
            effect["call"] = "finish_srd_finger_death_zombie"
            effect["params"]["rise_id"] = "{rise_id}"
        require = effect.get("require", {})
        if "Animate Dead requires a Small or Medium" in require.get("error", ""):
            require["error"] = (
                "Only Small or Medium original Humanoid bodies are supported for this rise."
            )
    finish["parameters"].append(_parameter("rise_id"))
    finish["effects"] = [
        e
        for e in finish["effects"]
        if not any(
            key in str(e)
            for key in (
                "actor_state.animated_",
                "state.animated_actor_ids",
                "_zombie_animated_ids",
                "state.has_animated_undead",
            )
        )
    ]
    finish["effects"].insert(
        0,
        _require(
            "state.finger_death_rise_records[rise_id].active == true and "
            "state.finger_death_rise_records[rise_id].body == _corpse_state_id and "
            "state.finger_death_rise_records[rise_id].caster == _caster_state_id",
            "Create only from this real pending spell kill and retained caster.",
        ),
    )
    for e in _walk(finish["effects"]):
        setting = e.get("set", {})
        if setting.get("field") == "actor_state.created_spell_id":
            setting["value"] = repr(SPELL)
        if setting.get("field") in {
            "state.created_actor_records.{_actor_state_id}",
            "lasting_state.created_actor_records.{_actor_state_id}",
        }:
            setting["value"].update(
                spell=repr(SPELL), source_rise_id="rise_id", ongoing_duration=False
            )
    finish["effects"] += [
        _set("actor_state.finger_death_rise_id", "rise_id"),
        _set("actor_state.finger_verbal_order", None),
        _set("actor_state.summon_active", False),
        _set("actor_state.summon_caster_state_id", None),
        _set("state.finger_death_rise_records.{rise_id}.active", False),
        _set("state.finger_death_rise_records.{rise_id}.status", "'risen'"),
        _set("state.finger_death_rise_records.{rise_id}.zombie", "_actor_state_id"),
    ]
    scene = _scene_constructor(tools)
    for e in _walk(scene["effects"]):
        setting = e.get("set", {})
        if setting.get("field") == "_native_spell":
            setting["value"] = repr(SPELL)
    return {
        "prepare_srd_finger_death_zombie": prepare,
        "finish_srd_finger_death_zombie": finish,
        "create_srd_finger_death_zombie_scene": scene,
    }


def _initiative_tool():
    """Insert a genuinely rolled ordinary entrant while preserving this active group turn."""
    tool = _tool(
        "Join this new ordinary Zombie to the actual active group order.",
        [
            _entity("caster_state"),
            _entity("zombie_state"),
        ],
        [
            _require(
                "combat.group_order == true and combat.encounter_active == true and "
                "combat.current_actor == _caster_state_id and combat.active_actor == "
                "_caster_state_id "
                "and combat.turn_active == true",
                "An active complete group caster-start turn is required.",
            ),
            _set("_finger_order", "combat.order"),
            _require(
                "len(_finger_order) < 16",
                "This ordinary entrant uses the existing 16-creature group bound.",
            ),
            _loop(
                "_finger_order",
                "_finger_entry",
                [
                    _require(
                        "_finger_entry.entity_id != _zombie_state_id and "
                        "_finger_entry.initiative_result != null",
                        "Retain a complete order without duplicate entrant.",
                    ),
                ],
            ),
            _call(
                "roll_srd_actor_group_initiative",
                {"actor_state": "{_zombie_state_id}"},
                ("_actor_initiative_entry",),
            ),
            _push("_finger_order", "_actor_initiative_entry"),
            {
                "sort": {
                    "collection": "_finger_order",
                    "by": "initiative_result",
                    "direction": "desc",
                }
            },
            _set("_finger_index", 0),
            _set("_finger_caster_found", False),
            _loop(
                "_finger_order",
                "_finger_entry",
                [
                    _when(
                        "_finger_entry.entity_id == _caster_state_id",
                        [
                            _set("combat.turn_index", "_finger_index"),
                            _set("_finger_caster_found", True),
                        ],
                    ),
                    _set("_finger_index", "_finger_index + 1"),
                ],
            ),
            _require(
                "_finger_caster_found == true", "Retain the actual current caster in this order."
            ),
            _set("combat.order", "_finger_order"),
            _set("combat.lasting_epoch_round", "combat.round"),
            _set(
                "combat.lasting_epoch_tick",
                "state.lasting_tick - 2 * combat.turn_index * (len(combat.order) ** -1)",
            ),
        ],
    )
    return {"register_srd_finger_death_zombie_initiative": tool}


def _turn_tool():
    """Resolve exactly the retained caster-next-turn deadline using the original corpse."""
    return {
        "start_srd_finger_death_rises": _tool(
            "Rise on this actual caster's next begin turn.",
            [
                _entity("caster_state"),
            ],
            [
                _set("_finger_pending", "state.finger_death_rise_ids"),
                _set("_finger_has_pending", False),
                _loop(
                    "_finger_pending",
                    "_finger_rise_id",
                    [
                        _set("_finger_rise", "state.finger_death_rise_records[_finger_rise_id]"),
                        _when(
                            "_finger_rise.active == true and _finger_rise.caster == "
                            "_caster_state_id",
                            [
                                _require(
                                    "caster_state.turns_started == _finger_rise.caster_turn",
                                    "Resolve the actual next caster turn; overdue turn "
                                    "chronology remains unsupported.",
                                ),
                                _call(
                                    "settle_srd_finger_death_rise",
                                    {
                                        "caster_state": "{_caster_state_id}",
                                        "corpse_state": "{_finger_rise.body}",
                                        "rise_id": "{_finger_rise_id}",
                                    },
                                ),
                            ],
                        ),
                        _when(
                            "state.finger_death_rise_records[_finger_rise_id].active == true and "
                            "_finger_rise.caster == _caster_state_id",
                            [_set("_finger_has_pending", True)],
                        ),
                    ],
                ),
                _set("caster_state.has_finger_death_rises", "_finger_has_pending"),
            ],
        )
    }


def _settle_tool():
    return {
        "settle_srd_finger_death_rise": _tool(
            "Consume the exact original killed Humanoid body.",
            [
                _entity("caster_state"),
                _entity("corpse_state"),
                _parameter("rise_id"),
            ],
            [
                _set("_finger_rise", "state.finger_death_rise_records[rise_id]"),
                _require(
                    "_finger_rise.active == true and _finger_rise.caster == _caster_state_id and "
                    "_finger_rise.body == _corpse_state_id",
                    "Retain the exact pending caster/body witness.",
                ),
                _when(
                    "corpse_state.is_alive == true or corpse_state.death_tick != "
                    "_finger_rise.death_tick",
                    [
                        _set("state.finger_death_rise_records.{rise_id}.active", False),
                        _set(
                            "state.finger_death_rise_records.{rise_id}.status",
                            "'cancelled_original_body'",
                        ),
                    ],
                ),
                _when(
                    "state.finger_death_rise_records[rise_id].active == true",
                    [
                        _require(
                            "_finger_rise.other_language != null",
                            "Admit the printed other Zombie language first.",
                        ),
                        _require(
                            "corpse_state.body_available == true and "
                            "corpse_state.body_animated_actor_id == null",
                            "Resolve the real original body; destruction or prior "
                            "consumption cannot create a second body.",
                        ),
                        _call(
                            "record_srd_result_text",
                            {"text": "finger_zombie_{_finger_rise.sequence}"},
                            ("_recorded_result_text",),
                        ),
                        _set("_finger_zombie_id", "_recorded_result_text"),
                        _call(
                            "create_srd_finger_death_zombie_scene",
                            {
                                "corpse_state": "{_corpse_state_id}",
                                "controller_state": "{_caster_state_id}",
                                "summon_id": "{_finger_zombie_id}",
                            },
                        ),
                        _call(
                            "prepare_srd_finger_death_zombie",
                            {
                                "creature_id": "{_finger_zombie_id}",
                                "corpse_state": "{_corpse_state_id}",
                                "source_caster": "{_caster_state_id}",
                                "other_language": "{_finger_rise.other_language}",
                                "rise_id": "{rise_id}",
                            },
                        ),
                        _call(
                            "register_srd_finger_death_zombie_initiative",
                            {
                                "caster_state": "{_caster_state_id}",
                                "zombie_state": "{_finger_zombie_id}",
                            },
                        ),
                    ],
                ),
            ],
        )
    }


def _control_tools():
    """Keep source control and real verbal intent separate from mental commands."""
    qualify = deepcopy(_undead_fortitude_tools()["resolve_srd_created_undead_fortitude"])
    body = deepcopy(
        [
            e
            for e in qualify["effects"]
            if e.get("require") or e.get("set", {}).get("field") == "_undead_record"
        ]
    )
    body[0]["require"]["condition"] = body[0]["require"]["condition"].replace(
        "target_state.hp == 0 and", "(target_state.hp > 0 or allow_zero == true) and"
    )
    body = _bind_spell(body)
    for e in _walk(body):
        req = e.get("require", {})
        if req:
            req["condition"] = (
                req["condition"]
                .replace("target_state.", "actor_state.")
                .replace("_target_state_id", "_actor_state_id")
            )
        setting = e.get("set", {})
        if setting:
            setting["value"] = setting["value"].replace("_target_state_id", "_actor_state_id")
    body += [
        _set(
            "_finger_body_witness",
            "state.finger_death_rise_records[actor_state.finger_death_rise_id]",
        ),
        _require(
            "_finger_body_witness.status == 'risen' and _finger_body_witness.zombie == "
            "_actor_state_id "
            "and _finger_body_witness.caster == actor_state.created_caster_state_id and "
            "_finger_body_witness.body == actor_state.created_body_source_state_id and "
            "_undead_record.source_rise_id == actor_state.finger_death_rise_id",
            "Retain this exact spell kill, body and verbal controller.",
        ),
    ]
    native = _tool(
        "Qualify the complete actual Finger of Death Zombie body.",
        [
            _entity("actor_state"),
            _optional("allow_zero", "boolean", False),
        ],
        body,
    )
    command = _public(
        _tool(
            "Save an actual verbal order without an invented Bonus Action cost.",
            [
                _entity("caster_state"),
                _entity("zombie_state"),
                _optional(
                    "action", "enum", "attack", enum_values=["attack", "dodge", "move", "other"]
                ),
                _optional("task", default=""),
                _optional("language", default="common"),
                _optional("perceived", "boolean", True),
            ],
            [
                _call(
                    "qualify_srd_finger_death_zombie_body", {"actor_state": "{_zombie_state_id}"}
                ),
                _require(
                    "zombie_state.created_caster_state_id == _caster_state_id and "
                    "caster_state.is_alive == true "
                    "and 'unconscious' not in caster_state.conditions",
                    "This living caster issues its actual verbal order.",
                ),
                _require(
                    "len(task) <= 500",
                    "Save bounded intent; general task execution remains GM-owned.",
                ),
                _call(
                    "resolve_srd_spell_language",
                    {
                        "speaker_state": "{_caster_state_id}",
                        "listener_state": "{_zombie_state_id}",
                        "channel": "speech",
                        "language": "{language}",
                        "perceived": "{perceived}",
                    },
                    ("_language_understands",),
                ),
                _require(
                    "_language_understands == true",
                    "The actual Zombie must perceive and understand this verbal order.",
                ),
                _set(
                    "zombie_state.finger_verbal_order",
                    {
                        "caster": "_caster_state_id",
                        "action": "action",
                        "task": "task",
                        "language": "language",
                        "tick": "state.lasting_tick",
                    },
                ),
            ],
        ),
        "spells",
        "Finger of Death verbal order",
        [("Order", "action")],
    )
    fortitude = _bind_spell(deepcopy(qualify))
    fortitude["effects"].insert(
        0,
        _call(
            "qualify_srd_finger_death_zombie_body",
            {
                "actor_state": "{_target_state_id}",
                "allow_zero": True,
            },
        ),
    )
    return {
        "qualify_srd_finger_death_zombie_body": native,
        "command_srd_finger_death_zombie": command,
        "resolve_srd_finger_death_zombie_fortitude": fortitude,
    }


def _bind_spell(value):
    if isinstance(value, str):
        return value.replace("'animate_dead'", repr(SPELL))
    if isinstance(value, list):
        return [_bind_spell(v) for v in value]
    if isinstance(value, dict):
        return {k: _bind_spell(v) for k, v in value.items()}
    return value


def _fortitude_packet(tool, composite, qualified):
    """Use the existing still-zero lethal suffix; no work is added to surviving targets."""
    tool = deepcopy(tool)
    lethal = [
        e
        for e in _walk(tool["effects"])
        if e.get("condition")
        in (
            "target.hp == 0 and target.creature_kind == 'monster'",
            "(target.hp == 0 and target.creature_kind == 'monster') "
            "and target.astral_projection_role != 'form'",
        )
    ]
    if len(lethal) != 1:
        raise ValueError("Keep the actual nested zero-HP monster boundary.")
    suffix = [
        i for i, e in enumerate(lethal[0]["on_true"]) if e.get("condition") == "target.hp == 0"
    ]
    if len(suffix) != 1:
        raise ValueError("Preserve the existing still-zero real death suffix.")
    prefix = []
    radiant = "damage_type == 'radiant' and _effective_damage > 0"
    if composite:
        radiant = (
            "(component_1_damage_type == 'radiant' and _component_1_effective_damage > 0) or "
            "(component_2_damage_type == 'radiant' and _component_2_effective_damage > 0)"
        )
    elif qualified:
        receipts = [
            e
            for e in _walk(tool["effects"])
            if e.get("condition")
            and any(
                r.get("set", {}).get("field") == "_source_base_effective_damage"
                for r in _walk(e.get("on_true", []))
            )
        ]
        # Select the actual outer typed-receipt branch, not an occupied-body forwarding guard.
        receipts = [
            e
            for e in receipts
            if not any(r is not e and e in list(_walk(r.get("on_true", []))) for r in receipts)
        ]
        if len(receipts) != 1:
            raise ValueError("Retain the real scalar post-mitigation base typed receipt.")
        prefix = [
            _set("_finger_base_received", "_effective_damage"),
            _when(
                receipts[0]["condition"],
                [_set("_finger_base_received", "_source_base_effective_damage")],
            ),
        ]
        radiant = "damage_type == 'radiant' and _finger_base_received > 0"
    lethal[0]["on_true"].insert(
        suffix[0],
        _when(
            "target.created_spell_id == 'finger_of_death' and target.created_form_id == "
            "'zombie' and "
            "target.form_statistics_replaced != true and target.is_alive == true and "
            "_effective_damage > 0",
            [
                *prefix,
                _call(
                    "resolve_srd_finger_death_zombie_fortitude",
                    {
                        "target_state": "{target}",
                        "damage_taken": "{_effective_damage}",
                        "radiant_damage": "{" + radiant + "}",
                        "critical_hit": "{critical_hit}",
                    },
                ),
            ],
        ),
    )
    return tool


def _revival(effects):
    for index, effect in enumerate(tuple(effects)):
        setting = effect.get("set", {})
        if setting.get("field") == "target_state.death_tick" and setting.get("value") is None:
            effects.insert(
                index + 1,
                _when(
                    "target_state.finger_death_rise_id != null and "
                    "lasting_state.finger_death_rise_records != null and "
                    "lasting_state.finger_death_rise_records"
                    "[target_state.finger_death_rise_id].active == true",
                    [
                        _call(
                            "cancel_srd_finger_death_rise_after_revival",
                            {
                                "target_state": "{_target_state_id}",
                                "lasting_state": "{_lasting_state_id}",
                            },
                        ),
                    ],
                ),
            )
        for key in ("on_true", "on_false"):
            _revival(effect.get(key, []))
        _revival(effect.get("for_each", {}).get("do", []))


def extend_runtime(text):
    """Compose after native packet/turn owners and before Reaction derivation."""
    _validate_source()
    tools = _load_runtime(text)["tools"]
    if "record_srd_finger_death_rise" in tools:
        return text
    changed = (
        _record_tools()
        | _constructors(tools)
        | _initiative_tool()
        | _turn_tool()
        | _settle_tool()
        | _control_tools()
    )
    cast = deepcopy(tools["cast_spell_single_target_save_damage"])
    calls = [e for e in _walk(cast["effects"]) if e.get("call") == "apply_srd_qualifying_damage"]
    if len(calls) != 1:
        raise ValueError("Retain the actual single-target save/damage owner.")
    cast["effects"].append(
        _when(
            "spell == 'finger_of_death' and _card_damage > 0 and target.is_alive == false and "
            "target.death_creature_type == 'humanoid'",
            [
                _call(
                    "record_srd_finger_death_rise",
                    {"caster_state": "{_caster_state_id}", "corpse_state": "{_target_id}"},
                ),
            ],
        )
    )
    changed["cast_spell_single_target_save_damage"] = cast
    begin = deepcopy(tools["begin_combat_turn"])
    begin["effects"].append(
        _when(
            "actor.has_finger_death_rises == true",
            [
                _call("start_srd_finger_death_rises", {"caster_state": "{_actor_id}"}),
            ],
        )
    )
    changed["begin_combat_turn"] = begin
    attack = deepcopy(tools["resolve_adjudicated_creature_attack"])
    native = "actor.created_spell_id == 'finger_of_death' and actor.form_attack_in_progress != true"
    attack["effects"].insert(
        0,
        _when(
            native,
            [
                _call("qualify_srd_finger_death_zombie_body", {"actor_state": "{_actor_id}"}),
                _require(
                    "actor.finger_verbal_order != null and "
                    "actor.finger_verbal_order.action == 'attack' "
                    "and actor.finger_verbal_order.caster == actor.created_caster_state_id",
                    "This controlled Zombie needs its actual saved verbal attack order.",
                ),
                _require(
                    "attack_name == 'Slam' and attack_mode == 'melee' and attack_modifier == 3 and "
                    "damage_expression == 'd8' and damage_modifier == 1 and damage_type == "
                    "'bludgeoning' "
                    "and normal_range == 5 and long_range == 5 and use_multiattack != true "
                    "and attack_uses_weapon != true",
                    "Use Zombie's real native Slam.",
                ),
                _call(
                    "qualify_srd_created_actor_attack_geometry",
                    {"actor_state": "{_actor_id}", "target_state": "{_target_id}"},
                    ("_created_attack_distance",),
                ),
                _require(
                    "attack_distance == _created_attack_distance",
                    "Use the actual native body attack distance.",
                ),
            ],
        ),
    )
    changed["resolve_adjudicated_creature_attack"] = attack
    for name, composite, qualified in (
        ("apply_srd_damage", False, False),
        ("apply_srd_composite_damage", True, False),
        ("apply_srd_qualifying_damage", False, True),
        ("apply_srd_qualifying_composite_damage", True, True),
    ):
        core = (
            "execute_srd_qualified_composite_packet"
            if composite
            else "execute_srd_qualified_scalar_packet"
        )
        selected = name
        if qualified and tools[name]["effects"][0].get("call") == core:
            if tools[name]["effects"][0] != _delegate(tools[name], core, True, [])["effects"][0]:
                raise ValueError("Keep the actual complete named qualified packet delegate.")
            selected = core
        changed[selected] = _fortitude_packet(tools[selected], composite, qualified)
    for name in ("resolve_srd_revival", "complete_srd_reincarnation_internal"):
        if name in tools:
            revive = deepcopy(tools[name])
            _revival(revive["effects"])
            changed[name] = revive
    language = deepcopy(tools["resolve_srd_spell_language"])
    for e in _walk(language["effects"]):
        req = e.get("require", {})
        if "created_spell_id == 'animate_dead'" in req.get("condition", ""):
            req["condition"] = req["condition"].replace(
                "speaker_state.created_spell_id == 'animate_dead'",
                "speaker_state.created_spell_id in ['animate_dead', 'finger_of_death']",
            )
        if "listener_state.created_spell_id == 'animate_dead'" in e.get("condition", ""):
            e["condition"] = e["condition"].replace(
                "listener_state.created_spell_id == 'animate_dead'",
                "listener_state.created_spell_id in ['animate_dead', 'finger_of_death']",
            )
    changed["resolve_srd_spell_language"] = language
    _normalize(changed)
    return _replace_tools(text, changed)
