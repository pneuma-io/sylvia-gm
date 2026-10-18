"""Native Undead curses retain their actual body, attack and receiving-source identity."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

if __package__:
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
    from .srd_timed_senses import _loop, _optional, _push, _replace_tools
else:
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
    from srd_timed_senses import _loop, _optional, _push, _replace_tools


MUMMY_BLOCK = "f0308dcb8736fc921617097ec97d466c7b2183ec039272f305f8d8f93fa2ec26"
CLASSIFICATION_SOURCE = {
    "curses_page": 179,
    "curses_page_sha256": "e7de657d8bc0a1f559e5a170457ae9fb2fc25b106f990ba91e8f622e47ebf1de",
    "magic_page": 185,
    "magic_page_sha256": "5e09e91d348112034afa223d81f6e63cad1c8058a83a09fcc23ed5e493394b6a",
}
DEATH_PREVENTION_SOURCE = {
    "page": 122,
    "page_text_sha256": "c1a01ec6390595776ee138387a3e6da910c8232f96426b123ebb11e384900bae",
}


def _validate_sources():
    """Bind native classification to the pinned definition, rather than the curse label."""
    if __package__:
        from .srd_spell_summoning import _created_undead_additional_profiles
    else:
        from srd_spell_summoning import _created_undead_additional_profiles

    root = Path(__file__).resolve().parents[1]
    pages = json.loads(
        (root / "rulesets/srd-lite/content/corpus/rules_glossary.json").read_text()
    )["page_records"]
    for key in ("curses", "magic"):
        page = next(p for p in pages if p["page"] == CLASSIFICATION_SOURCE[key + "_page"])
        assert page["text_sha256"] == CLASSIFICATION_SOURCE[key + "_page_sha256"]
        assert hashlib.sha256(page["text"].encode()).hexdigest() == page["text_sha256"]
    assert _created_undead_additional_profiles()["mummy"]["source_stat_block_sha256"] == MUMMY_BLOCK
    spell_pages = json.loads(
        (root / "rulesets/srd-lite/content/corpus/spells.json").read_text()
    )["page_records"]
    prevention = next(p for p in spell_pages if p["page"] == DEATH_PREVENTION_SOURCE["page"])
    assert prevention["text_sha256"] == DEATH_PREVENTION_SOURCE["page_text_sha256"]
    assert hashlib.sha256(prevention["text"].encode()).hexdigest() == prevention["text_sha256"]


def _native_source_tools():
    """Authenticate original installed body provenance even after that attacker dies."""
    read = _tool(
        "Authenticate the retained Mummy Rotting Fist curse and allocated source sequence.",
        [_parameter("source_id"), _entity("lasting_state")],
        [
            _set("_native_mummy_rot", False),
            _set("_native_mummy_rot_source", None),
            _when(
                "source_id in lasting_state.timed_condition_sources",
                [
                    _set(
                        "_native_mummy_rot_source",
                        "lasting_state.timed_condition_sources[source_id]",
                    ),
                    _when(
                        "_native_mummy_rot_source.installed == true and "
                        "_native_mummy_rot_source.native_profile == 'mummy_rot' and "
                        "_native_mummy_rot_source.native_attack == 'Rotting Fist' and "
                        "_native_mummy_rot_source.native_body_source != null and "
                        "_native_mummy_rot_source.native_body_source_sequence == "
                        "_native_mummy_rot_source.sequence and "
                        "_native_mummy_rot_source.spell_id == 'create_undead' and "
                        "_native_mummy_rot_source.condition == 'none'",
                        [
                            _when(
                                "_native_mummy_rot_source.native_body_source in "
                                "lasting_state.created_actor_records",
                                [
                                    _set(
                                        "_native_mummy_rot_body",
                                        "lasting_state.created_actor_records["
                                        "_native_mummy_rot_source.native_body_source]",
                                    ),
                                    _set(
                                        "_native_mummy_rot",
                                        "_native_mummy_rot_body.spell == "
                                        "'create_undead' and _native_mummy_rot_body.individual == "
                                        "_native_mummy_rot_source.native_body_source and "
                                        "_native_mummy_rot_source.caster_state_id == "
                                        "_native_mummy_rot_source.native_body_source and "
                                        "_native_mummy_rot_body.body_source != null and "
                                        "_native_mummy_rot_body.body_source_id != null",
                                    ),
                                    _when(
                                        "_native_mummy_rot_body.source_block_sha256 != '"
                                        + MUMMY_BLOCK
                                        + (
                                            "' or _native_mummy_rot_source."
                                            "native_body_block_sha256 != '"
                                        )
                                        + MUMMY_BLOCK
                                        + "'",
                                        [_set("_native_mummy_rot", False)],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    active = _tool(
        "Qualify the actual continuous native curse and its whole-rest exception.",
        [_entity("target_state"), _entity("lasting_state")],
        [
            _set("_native_mummy_rot_active", False),
            _set("_native_mummy_rot_ids", []),
            _when(
                "target_state.mummy_rot_source_ids != null",
                [
                    _set("_native_mummy_rot_ids", "target_state.mummy_rot_source_ids"),
                ],
            ),
            _loop(
                "_native_mummy_rot_ids",
                "_native_mummy_rot_id",
                [
                    _call(
                        "read_srd_native_mummy_rot",
                        {
                            "source_id": "{_native_mummy_rot_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                        ("_native_mummy_rot", "_native_mummy_rot_source"),
                    ),
                    _when(
                        "_native_mummy_rot == true and "
                        "_native_mummy_rot_source.target_state_id == _target_state_id",
                        [
                            _set("_native_mummy_rot_active", True),
                        ],
                    ),
                ],
            ),
        ],
    )
    return {"read_srd_native_mummy_rot": read, "qualify_srd_native_mummy_rot_target": active}


def _no_save_installer(tools):
    """Reuse final pool allocation without inventing a saving throw for the native curse."""
    installer = deepcopy(tools["install_srd_timed_condition_source"])
    installer["description"] = "Allocate the native Mummy curse with no saving throw or DC."
    installer["parameters"] = [p for p in installer["parameters"] if p["name"] != "save_dc"]

    def without_dc(value):
        if isinstance(value, list):
            return [without_dc(child) for child in value]
        if isinstance(value, dict):
            return {key: without_dc(child) for key, child in value.items()}
        return None if value == "save_dc" else value

    installer["effects"] = without_dc(installer["effects"])
    return installer


def _mummy_rot_tools():
    """A real Fist hit creates one continuous curse, not extra daily damage per repeated hit."""
    project_healing = _tool(
        "Reproject retained healing restrictions without entering source lifecycle cleanup.",
        [_entity("target_state"), _entity("lasting_state")],
        [
            _set("target_state.hit_point_recovery_blocked", False),
            _set("_native_rot_healing_records", []),
            _when(
                "lasting_state.turn_effects != null",
                [
                    _set("_native_rot_healing_records", "lasting_state.turn_effects"),
                ],
            ),
            _loop(
                "_native_rot_healing_records",
                "_native_rot_healing_record",
                [
                    _when(
                        "_native_rot_healing_record.kind == 'healing_block' and "
                        "_native_rot_healing_record.target == _target_state_id",
                        [
                            _set("target_state.hit_point_recovery_blocked", True),
                        ],
                    ),
                ],
            ),
        ],
    )
    install = _tool(
        "Install the printed curse only from the authenticated native Rotting Fist hit branch.",
        [
            _entity("actor_state"),
            _entity("target_state"),
            _optional("lasting_state", resolve="entity", default="state"),
        ],
        [
            _require(
                "actor_state.created_spell_id == 'create_undead' and "
                "actor_state.created_form_id == 'mummy' and "
                "actor_state.created_source_block_sha256 == '" + MUMMY_BLOCK + "' and "
                "actor_state.form_statistics_replaced != true and "
                "actor_state.is_alive == true",
                "Use the actual installed native Mummy.",
            ),
            _set("_native_rot_body", "lasting_state.created_actor_records[_actor_state_id]"),
            _require(
                "_native_rot_body.spell == 'create_undead' and "
                "_native_rot_body.individual == _actor_state_id and "
                "_native_rot_body.caster == actor_state.created_caster_state_id and "
                "_native_rot_body.source_block_sha256 == '" + MUMMY_BLOCK + "' and "
                "_native_rot_body.body_source == actor_state.created_body_source_state_id and "
                "_native_rot_body.body_source_id == actor_state.created_body_source_id",
                "Retain the authentic installed body and original consumed corpse.",
            ),
            _require(
                "target_state.creature_type != null and target_state.is_object != true",
                "Rotting Fist curses an actual creature, not an object proxy.",
            ),
            _set("_mummy_rot_source_id", None),
            _set("_mummy_rot_target_ids", []),
            _when(
                "target_state.mummy_rot_source_ids != null",
                [
                    _set("_mummy_rot_target_ids", "target_state.mummy_rot_source_ids"),
                ],
            ),
            _loop(
                "_mummy_rot_target_ids",
                "_native_rot_id",
                [
                    _call(
                        "read_srd_native_mummy_rot",
                        {
                            "source_id": "{_native_rot_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                        ("_native_mummy_rot", "_native_mummy_rot_source"),
                    ),
                    _when(
                        "_native_mummy_rot == true and "
                        "_native_mummy_rot_source.target_state_id == _target_state_id",
                        [
                            _set("_mummy_rot_source_id", "_native_rot_id"),
                        ],
                    ),
                ],
            ),
            _when(
                "_mummy_rot_source_id == null",
                [
                    _call(
                        "install_srd_native_no_save_curse",
                        {
                            "target_state": "{_target_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                            "caster_state": "{_actor_state_id}",
                            "spell": "create_undead",
                            "condition": "none",
                            "duration_rounds": 0,
                            "repeat_save_timing": "none",
                        },
                        ("_timed_condition_source_id",),
                    ),
                    _set("_mummy_rot_source_id", "_timed_condition_source_id"),
                    _set(
                        "_native_rot_stamp",
                        {
                            "native_profile": "'mummy_rot'",
                            "native_attack": "'Rotting Fist'",
                            "native_body_source": "_actor_state_id",
                            "native_body_block_sha256": repr(MUMMY_BLOCK),
                            "native_body_source_sequence": "lasting_state.timed_condition_sources["
                            "_mummy_rot_source_id].sequence",
                            "native_next_reduction_tick": "lasting_state.lasting_tick + 28800",
                            "dispel_ongoing": False,
                            "source_is_magical": False,
                            "effect_kind": "'physical'",
                            "ongoing_duration": False,
                            "cast_id": "''",
                        },
                    ),
                    _set(
                        "lasting_state.timed_condition_sources.{_mummy_rot_source_id}",
                        "merge(lasting_state.timed_condition_sources[_mummy_rot_source_id], "
                        "_native_rot_stamp)",
                    ),
                    _push("_mummy_rot_target_ids", "_mummy_rot_source_id"),
                    _set("target_state.mummy_rot_source_ids", "_mummy_rot_target_ids"),
                    _set("target_state.has_native_mummy_rot", True),
                    _set("_native_rot_effects", []),
                    _when(
                        "lasting_state.turn_effects != null",
                        [
                            _set("_native_rot_effects", "lasting_state.turn_effects"),
                        ],
                    ),
                    _require(
                        "len(_native_rot_effects) < 128",
                        "Resolve existing source records before adding the native curse.",
                    ),
                    _push(
                        "_native_rot_effects",
                        {
                            "kind": "'healing_block'",
                            "label": "'Mummy curse'",
                            "spell": "'create_undead'",
                            "target": "_target_state_id",
                            "caster": "_actor_state_id",
                            "owner": "_actor_state_id",
                            "phase": "'native_curse'",
                            "turn": 0,
                            "expires_tick": None,
                            "native_curse_source": "_mummy_rot_source_id",
                            "native_curse_sequence": "lasting_state.timed_condition_sources["
                            "_mummy_rot_source_id].sequence",
                            "native_body_source": "_actor_state_id",
                            "native_body_block_sha256": repr(MUMMY_BLOCK),
                            "native_attack": "'Rotting Fist'",
                            "cast_id": "''",
                            "ongoing_duration": False,
                        },
                    ),
                    _set("lasting_state.turn_effects", "_native_rot_effects"),
                    _call(
                        "project_srd_turn_modifiers",
                        {
                            "target_state": "{_target_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                    ),
                ],
            ),
        ],
    )
    cleanup = _tool(
        "Remove only this authenticated curse's healing contribution before source retirement.",
        [_parameter("source_id"), _entity("lasting_state")],
        [
            _call(
                "read_srd_native_mummy_rot",
                {
                    "source_id": "{source_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
                ("_native_mummy_rot", "_native_mummy_rot_source"),
            ),
            _when(
                "_native_mummy_rot == true",
                [
                    _set("_native_rot_kept", []),
                    _set("_native_rot_records", []),
                    _when(
                        "lasting_state.turn_effects != null",
                        [
                            _set("_native_rot_records", "lasting_state.turn_effects"),
                        ],
                    ),
                    _loop(
                        "_native_rot_records",
                        "_native_rot_record",
                        [
                            _set(
                                "_native_rot_own_record",
                                "_native_rot_record.kind == 'healing_block' and "
                                "_native_rot_record.native_curse_source == source_id and "
                                "_native_rot_record.native_curse_sequence == "
                                "_native_mummy_rot_source.sequence and "
                                "_native_rot_record.native_body_source == "
                                "_native_mummy_rot_source.native_body_source and "
                                "_native_rot_record.native_body_block_sha256 == '"
                                + MUMMY_BLOCK
                                + "'",
                            ),
                            _when(
                                "_native_rot_own_record != true",
                                [
                                    _push("_native_rot_kept", "_native_rot_record"),
                                ],
                            ),
                        ],
                    ),
                    _set("lasting_state.turn_effects", "_native_rot_kept"),
                    _call(
                        "project_srd_native_mummy_rot_healing",
                        {
                            "target_state": "{_native_mummy_rot_source.target_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                    ),
                ],
            ),
        ],
    )
    return {
        "project_srd_native_mummy_rot_healing": project_healing,
        "install_srd_native_mummy_rot": install,
        "clear_srd_native_mummy_rot_healing": cleanup,
    }


def _maximum_zero_death_tool(tools):
    """Derive native instant death from the actual final received-packet death suffix."""
    packet = deepcopy(tools["apply_srd_qualifying_composite_damage"])
    if packet["effects"][0].get("call") == "execute_srd_qualified_composite_packet":
        if __package__:
            from .srd_qualified_packet_implementations import _expanded_core
        else:
            from srd_qualified_packet_implementations import _expanded_core

        packet["effects"] = _expanded_core(tools["execute_srd_qualified_composite_packet"], True)
    # True Polymorph owns the object branch; native curses use its genuine ordinary branch.
    effects = packet["effects"]
    if any(e.get("set", {}).get("field") == "_tp_received_object" for e in effects):
        if __package__:
            from .srd_warding_bond_damage import _receiving_start
        else:
            from srd_warding_bond_damage import _receiving_start

        start = _receiving_start(effects)
        effects = effects[start + 1]["on_false"]
    ordinary_zero = "target.hp == 0 and target.creature_kind == 'monster'"
    guarded_zero = "(" + ordinary_zero + ") and target.astral_projection_role != 'form'"
    zero = [
        effect for effect in effects if effect.get("condition") in (ordinary_zero, guarded_zero)
    ]
    if len(zero) != 1:
        raise ValueError("Native maximum-zero death needs the actual final monster-zero boundary.")
    suffixes = [
        effect["on_true"]
        for effect in zero[0]["on_true"]
        if effect.get("condition") == "target.hp == 0"
    ]
    if len(suffixes) != 1:
        raise ValueError("Preserve the actual still-zero death suffix after Fortitude.")
    suffix = suffixes[0]
    if not (
        {"set": {"field": "target.is_alive", "value": "false"}} in suffix
        and {"apply_condition": {"target": "target", "condition": "dead"}} in suffix
        and any(effect.get("call") == "record_srd_actual_death" for effect in suffix)
    ):
        raise ValueError(
            "Maximum-zero death must retain actual life, condition and death stamping."
        )

    def rebind(value):
        if isinstance(value, str):
            return (
                value.replace("{target}", "{_target_state_id}").replace("target.", "target_state.")
                if value != "target"
                else "target_state"
            )
        if isinstance(value, list):
            return [rebind(child) for child in value]
        if isinstance(value, dict):
            return {key: rebind(child) for key, child in value.items()}
        return value

    death = rebind(suffix)
    stamped = [
        i for i, effect in enumerate(death) if effect.get("call") == "record_srd_actual_death"
    ]
    if len(stamped) != 1:
        raise ValueError("One native instant death must own one actual death stamp.")
    death.insert(
        stamped[0] + 1,
        _when(
            "_native_maximum_concentration_target != null",
            [
                _call(
                    "end_srd_concentration_node",
                    {
                        "source_state": "{_target_state_id}",
                        "target_state": "{_native_maximum_concentration_target}",
                    },
                ),
            ],
        ),
    )

    death.append(_set("_native_mummy_maximum_zero_death", True))
    if zero[0]["condition"] == guarded_zero:
        # Keep the final receiver's Astral form protection with its unchanged death suffix.
        death = [
            _set("_native_mummy_maximum_zero_death", False),
            _when("target_state.astral_projection_role != 'form'", death),
        ]

    return _tool(
        "Apply this authenticated native maximum-zero instant death through final death owners.",
        [
            _entity("target_state"),
            _parameter("source_id"),
            _parameter("hp_before", "integer", minimum=0, model_hidden=True),
            _optional("lasting_state", resolve="entity", default="state"),
        ],
        [
            _call(
                "read_srd_native_mummy_rot",
                {
                    "source_id": "{source_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
                ("_native_mummy_rot", "_native_mummy_rot_source"),
            ),
            _require(
                "_native_mummy_rot == true and "
                "_native_mummy_rot_source.target_state_id == _target_state_id and "
                "target_state.max_hp == 0 and target_state.hp == 0 and "
                "target_state.is_alive == true",
                "Use only this actual native curse's real maximum-zero body.",
            ),
            _set("_hp_before", "hp_before"),
            _set("_native_maximum_concentration_target", None),
            _when(
                "target_state.concentration_target_state_id != null",
                [
                    _set(
                        "_native_maximum_concentration_target",
                        "target_state.concentration_target_state_id",
                    ),
                ],
            ),
            *death,
        ],
    )


def _mummy_maximum_tools(tools):
    """Keep real reduction receipts and the printed whole-maximum Long Rest exception."""
    body = _require(
        "target_state.form_statistics_replaced != true and target_state.form_cloud != true and "
        "target_state.magic_jar_role == null and target_state.current_body_source_id == null",
        "Join the actual replacement, occupied or transferred body before restoring its maximum.",
    )
    boundary = _tool(
        "Resolve one actual due24-hour native curse boundary with its real3d6 reduction.",
        [
            _entity("target_state"),
            _parameter("source_id"),
            _optional("lasting_state", resolve="entity", default="state"),
        ],
        [
            _call(
                "read_srd_native_mummy_rot",
                {
                    "source_id": "{source_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
                ("_native_mummy_rot", "_native_mummy_rot_source"),
            ),
            _require(
                "_native_mummy_rot == true and "
                "_native_mummy_rot_source.target_state_id == _target_state_id",
                "Use this actual creature's continuous authenticated native curse.",
            ),
            _set("_native_mummy_daily_roll", None),
            _set("_native_mummy_daily_reduction", 0),
            _set("_native_mummy_daily_prevented", False),
            _set("_native_mummy_daily_applied", 0),
            _set("_native_mummy_maximum_zero_death", False),
            _when(
                "lasting_state.lasting_tick >= _native_mummy_rot_source.native_next_reduction_tick",
                [
                    deepcopy(body),
                    _require(
                        "target_state.is_alive == true and target_state.max_hp > 0",
                        "A daily reduction requires the actual living cursed body.",
                    ),
                    _require(
                        "lasting_state.lasting_tick < "
                        "_native_mummy_rot_source.native_next_reduction_tick + 28800",
                        "Join catch-up before resolving multiple elapsed native curse boundaries.",
                    ),
                    {"dice": {"expression": "3d6"}, "store_as": "_native_mummy_daily_roll"},
                    _set("_native_mummy_daily_reduction", "_native_mummy_daily_roll.total"),
                    _when(
                        "target_state.max_hp <= _native_mummy_daily_reduction",
                        [
                            _call(
                                "consume_srd_death_prevention",
                                {
                                    "target_state": "{_target_state_id}",
                                    "lasting_state": "{_lasting_state_id}",
                                },
                                ("_death_prevented",),
                            ),
                            _set("_native_mummy_daily_prevented", "_death_prevented"),
                        ],
                    ),
                    _when(
                        "_native_mummy_daily_prevented != true",
                        [
                            _set("_native_mummy_daily_hp_before", "target_state.hp"),
                            _set(
                                "_native_mummy_daily_applied",
                                "min(target_state.max_hp, _native_mummy_daily_reduction)",
                            ),
                            _set("_native_mummy_reduction_receipts", []),
                            _when(
                                "target_state.native_undead_max_hp_reduction_receipts != null",
                                [
                                    _set(
                                        "_native_mummy_reduction_receipts",
                                        "target_state.native_undead_max_hp_reduction_receipts",
                                    ),
                                ],
                            ),
                            _require(
                                "len(_native_mummy_reduction_receipts) < 128",
                                "Resolve existing native maximum-reduction receipts "
                                "before adding more.",
                            ),
                            _push(
                                "_native_mummy_reduction_receipts",
                                {
                                    "source": "source_id",
                                    "sequence": "_native_mummy_rot_source.sequence",
                                    "native_body_source": (
                                        "_native_mummy_rot_source.native_body_source"
                                    ),
                                    "native_attack": "'Rotting Fist'",
                                    "native_body_block_sha256": repr(MUMMY_BLOCK),
                                    "tick": "_native_mummy_rot_source.native_next_reduction_tick",
                                    "amount": "_native_mummy_daily_applied",
                                    "rolled_amount": "_native_mummy_daily_reduction",
                                },
                            ),
                            _set(
                                "target_state.native_undead_max_hp_reduction_receipts",
                                "_native_mummy_reduction_receipts",
                            ),
                            _when(
                                "target_state.recovery_max_hp_reduction == null",
                                [
                                    _set("target_state.recovery_max_hp_reduction", 0),
                                ],
                            ),
                            _set(
                                "target_state.max_hp",
                                "max(0, target_state.max_hp - _native_mummy_daily_reduction)",
                            ),
                            _set("target_state.hp", "min(target_state.hp, target_state.max_hp)"),
                            _set(
                                "target_state.bloodied",
                                "target_state.hp <= floor(target_state.max_hp * 0.5)",
                            ),
                            _set(
                                "target_state.recovery_max_hp_reduction",
                                "target_state.recovery_max_hp_reduction + "
                                "_native_mummy_daily_applied",
                            ),
                            _when(
                                "target_state.max_hp == 0",
                                [
                                    _call(
                                        "resolve_srd_native_mummy_maximum_zero",
                                        {
                                            "target_state": "{_target_state_id}",
                                            "source_id": "{source_id}",
                                            "hp_before": "{_native_mummy_daily_hp_before}",
                                            "lasting_state": "{_lasting_state_id}",
                                        },
                                        ("_native_mummy_maximum_zero_death",),
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _set(
                        "lasting_state.timed_condition_sources.{source_id}.native_next_reduction_tick",
                        "_native_mummy_rot_source.native_next_reduction_tick + 28800",
                    ),
                ],
            ),
        ],
    )
    restore = _tool(
        "Restore the actual aggregate at Long Rest unless the continuous Mummy curse forbids it.",
        [_entity("target_state"), _optional("lasting_state", resolve="entity", default="state")],
        [
            _set("_native_rest_maximum_restored", 0),
            _when(
                "target_state.recovery_max_hp_reduction != null and "
                "target_state.recovery_max_hp_reduction > 0",
                [
                    deepcopy(body),
                    _call(
                        "qualify_srd_native_mummy_rot_target",
                        {
                            "target_state": "{_target_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                        ("_native_mummy_rot_active",),
                    ),
                    _when(
                        "_native_mummy_rot_active != true",
                        [
                            _set(
                                "_native_rest_maximum_restored",
                                "target_state.recovery_max_hp_reduction",
                            ),
                            _set(
                                "target_state.max_hp",
                                "target_state.max_hp + _native_rest_maximum_restored",
                            ),
                            _set("target_state.recovery_max_hp_reduction", 0),
                            _set("target_state.native_undead_max_hp_reduction_receipts", []),
                        ],
                    ),
                ],
            ),
        ],
    )
    return {
        "resolve_srd_native_mummy_maximum_zero": _maximum_zero_death_tool(tools),
        "resolve_srd_native_mummy_rot_boundary": boundary,
        "restore_srd_native_undead_rest_maximum": restore,
    }


def bind_native_undead_rest_consumers(text):
    """Add only the selected maximum receiver before the final rest owner's ordinary HP test."""
    tools = _load_runtime(text)["tools"]
    rest = deepcopy(tools["complete_srd_rest"])
    if "restore_srd_native_undead_rest_maximum" in str(rest["effects"]):
        return text
    branch = next(
        e["on_true"]
        for e in rest["effects"]
        if e.get("condition") == "target_state.rest_kind == 'long'"
        and "restore_hit_points" in str(e["on_true"])
    )
    assert not any(e.get("set", {}).get("field") == "target_state.max_hp" for e in branch)
    branch.insert(
        0,
        _call(
            "restore_srd_native_undead_rest_maximum",
            {
                "target_state": "{_target_state_id}",
                "lasting_state": "state",
            },
            ("_native_rest_maximum_restored",),
        ),
    )
    return _replace_tools(text, {"complete_srd_rest": rest})


def bind_native_mummy_maximum_zero_consumers(text):
    """Refresh only native instant death after the final packet/body callbacks compose."""
    tools = _load_runtime(text)["tools"]
    receiver = _maximum_zero_death_tool(tools)
    name = "resolve_srd_native_mummy_maximum_zero"
    return text if tools.get(name) == receiver else _replace_tools(text, {name: receiver})


def extend_runtime(text):
    """Derive the private no-save receiver from final admitted source and recovery owners."""
    _validate_sources()
    tools = _load_runtime(text)["tools"]
    changed = _native_source_tools() | _mummy_rot_tools() | _mummy_maximum_tools(tools)
    changed["install_srd_native_no_save_curse"] = _no_save_installer(tools)
    retire = deepcopy(tools["retire_srd_timed_condition_source"])
    if "clear_srd_native_mummy_rot_healing" not in str(retire["effects"]):
        branch = next(
            e["on_true"]
            for e in retire["effects"]
            if ".installed == true" in e.get("condition", "")
        )
        branch.insert(
            0,
            _when(
                "_retiring_condition_source.native_profile == 'mummy_rot'",
                [
                    _call(
                        "clear_srd_native_mummy_rot_healing",
                        {
                            "source_id": "{source_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                    ),
                ],
            ),
        )
    changed["retire_srd_timed_condition_source"] = retire
    for name in ("remove_srd_recovery_curses", "remove_srd_one_recovery_curse"):
        owner = deepcopy(tools[name])
        if "read_srd_native_mummy_rot" not in str(owner["effects"]):
            loop = next(e["for_each"]["do"] for e in owner["effects"] if "for_each" in e)
            loop.insert(
                1,
                _call(
                    "read_srd_native_mummy_rot",
                    {
                        "source_id": "{_recovery_id}",
                        "lasting_state": "{_lasting_state_id}",
                    },
                    ("_native_mummy_rot",),
                ),
            )
            candidate = next(
                e for e in loop if "_recovery_source.control_profile" in e.get("condition", "")
            )
            candidate["condition"] = candidate["condition"][:-1] + " or _native_mummy_rot == true)"
        changed[name] = owner
    recovery = deepcopy(tools["cast_srd_recovery"])
    if "target_state.native_undead_max_hp_reduction_receipts" not in str(recovery["effects"]):

        def clear_receipts(effects):
            for index, effect in reversed(list(enumerate(effects))):
                setting = effect.get("set", {})
                if (
                    setting.get("field") == "target_state.recovery_max_hp_reduction"
                    and setting.get("value") == 0
                ):
                    effects.insert(
                        index + 1, _set("target_state.native_undead_max_hp_reduction_receipts", [])
                    )
                for key in ("on_true", "on_false"):
                    clear_receipts(effect.get(key, []))
                clear_receipts(effect.get("for_each", {}).get("do", []))

        clear_receipts(recovery["effects"])
    changed["cast_srd_recovery"] = recovery
    changed = {name: value for name, value in changed.items() if tools.get(name) != value}
    return _replace_tools(text, changed) if changed else text
