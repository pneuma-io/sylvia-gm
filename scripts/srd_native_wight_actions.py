"""Printed Life Drain receives actual Necrotic damage and retains its native maximum loss."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

if __package__:
    from .srd_native_mummy_actions import _native_body_tool
    from .srd_native_mummy_fist import _actual_composite_body
    from .srd_native_receiving_boundary import native_receiving_boundary
    from .srd_native_undead_curses import MUMMY_BLOCK
    from .srd_spell_summoning import (
        _created_actor_attack_geometry_tool,
        _created_undead_additional_profiles,
        _ghoul_actor_tools,
        _walk,
    )
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _require,
        _set,
        _tool,
        _when,
    )
    from .srd_timed_senses import _optional, _push, _replace_tools
else:
    from srd_native_mummy_actions import _native_body_tool
    from srd_native_mummy_fist import _actual_composite_body
    from srd_native_receiving_boundary import native_receiving_boundary
    from srd_native_undead_curses import MUMMY_BLOCK
    from srd_spell_summoning import (
        _created_actor_attack_geometry_tool,
        _created_undead_additional_profiles,
        _ghoul_actor_tools,
        _walk,
    )
    from srd_timed_actor_defenses import _call, _entity, _load_runtime, _require, _set, _tool, _when
    from srd_timed_senses import _optional, _push, _replace_tools

WIGHT_BLOCK = "d65414f9f850be8380a004e3eafaae25f8f4788687d1cebcb7596fb6cb7dd568"
MAXIMUM_SOURCE_PAGE = 17
MAXIMUM_SOURCE_HASH = "10feb4106ac94111f30a6cbe9fbcc2bfeaa1441dcd3bc1fdbbe12b3dda7f0fd4"


def _validate_source():
    """Bind the complete printed native body and the actual maximum-zero death rule."""
    row = _created_undead_additional_profiles()["wight"]
    assert row["source_stat_block_sha256"] == WIGHT_BLOCK
    assert "Constitution Saving Throw: DC 13" in row["source_stat_block"]
    assert "6 (1d8 + 2) Necrotic" in row["source_stat_block"]
    pages = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "rulesets/srd-lite/content/corpus/playing_the_game.json"
        ).read_text()
    )["page_records"]
    page = next(p for p in pages if p["page"] == MAXIMUM_SOURCE_PAGE)
    assert page["text_sha256"] == MAXIMUM_SOURCE_HASH
    assert hashlib.sha256(page["text"].encode()).hexdigest() == MAXIMUM_SOURCE_HASH
    assert "A creature dies if its Hit" in page["text"]


def _wight_body_tool(require_command=False):
    """Use the exact existing body/control qualifier with Wight's retained printed identity."""

    def bind(value):
        if isinstance(value, str):
            return (
                value.replace(MUMMY_BLOCK, WIGHT_BLOCK)
                .replace("mummy", "wight")
                .replace("Mummy", "Wight")
            )
        if isinstance(value, list):
            return [bind(v) for v in value]
        if isinstance(value, dict):
            return {k: bind(v) for k, v in value.items()}
        return value

    tool = bind(_native_body_tool(require_command))
    tool["effects"].append(_set("_wight_native_actor_id", "_actor_state_id"))
    return tool


def _wight_actor_tools(tools):
    """Keep printed armor provenance without inventing a native equipment-training profile."""
    owned = _ghoul_actor_tools(tools, "wight")
    setters = [
        effect["set"]
        for effect in _walk(owned["prepare_srd_source_wight"]["effects"])
        if effect.get("set", {}).get("field") == "_zombie_equipment"
    ]
    if len(setters) != 1 or setters[0]["value"] != ["'Studded Leather Armor'"]:
        raise ValueError("Preserve the exact printed Wight constructor equipment boundary.")
    setters[0]["value"] = []
    return owned


def _maximum_loss_effects(tools):
    """Receive only the actual packet's fresh typed damage before final presentation snapshots."""
    packet = _actual_composite_body(tools, composite=False)
    boundary = native_receiving_boundary(packet["effects"])
    death = deepcopy(boundary.still_zero)
    maximum_zero = "target.max_hp == 0 and target.is_alive == true"
    if boundary.monster_zero["condition"].endswith("target.astral_projection_role != 'form'"):
        maximum_zero += " and target.astral_projection_role != 'form'"
    death_index = next(i for i, e in enumerate(death) if e.get("call") == "record_srd_actual_death")
    death.insert(
        death_index + 1,
        _when(
            "_wight_concentration_target != null",
            [
                _call(
                    "end_srd_concentration_node",
                    {"source_state": "{target}", "target_state": "{_wight_concentration_target}"},
                ),
            ],
        ),
    )
    return [
        _require(
            "target.current_body_source_id == null and target.magic_jar_role == null",
            "Join actual changed physical bodies before Life Drain maximum reduction.",
        ),
        _set("_wight_damage_taken", "_source_observed_type_damage"),
        _set("_wight_packet_hp_damage", "_hp_damage"),
        _set("_wight_temporary_absorbed", "_temp_hp_absorbed"),
        _set("_wight_maximum_prevented", False),
        _set("_wight_maximum_reduction", 0),
        _set("_wight_maximum_zero_death", False),
        _set("_wight_maximum_receipt_id", None),
        _when(
            "_wight_damage_taken > 0",
            [
                _when(
                    "target.is_alive == true and _wight_damage_taken >= target.max_hp",
                    [
                        _call(
                            "consume_srd_death_prevention",
                            {"target_state": "{target}", "lasting_state": "{_lasting_state_id}"},
                            ("_death_prevented",),
                        ),
                        _set("_wight_maximum_prevented", "_death_prevented"),
                    ],
                ),
                _when(
                    "_wight_maximum_prevented != true",
                    [
                        _set("_wight_maximum_reduction", "min(target.max_hp, _wight_damage_taken)"),
                        _when(
                            "lasting_state.native_undead_maximum_sequence == null",
                            [
                                _set("lasting_state.native_undead_maximum_sequence", 0),
                            ],
                        ),
                        _set(
                            "lasting_state.native_undead_maximum_sequence",
                            "lasting_state.native_undead_maximum_sequence + 1",
                        ),
                        _set(
                            "_wight_maximum_receipt_id",
                            "'wight_life_drain_' + "
                            "str(lasting_state.native_undead_maximum_sequence)",
                        ),
                        _set("_wight_maximum_receipts", []),
                        _when(
                            "target.native_undead_max_hp_reduction_receipts != null",
                            [
                                _set(
                                    "_wight_maximum_receipts",
                                    "target.native_undead_max_hp_reduction_receipts",
                                ),
                            ],
                        ),
                        _require(
                            "len(_wight_maximum_receipts) < 128",
                            "Resolve retained native maximum receipts before adding more.",
                        ),
                        _push(
                            "_wight_maximum_receipts",
                            {
                                "source": "_wight_maximum_receipt_id",
                                "sequence": "lasting_state.native_undead_maximum_sequence",
                                "target": "_wight_target_id",
                                "native_body_source": "_wight_native_actor_id",
                                "native_attack": "'Life Drain'",
                                "native_body_block_sha256": repr(WIGHT_BLOCK),
                                "tick": "lasting_state.lasting_tick",
                                "amount": "_wight_maximum_reduction",
                                "damage_taken": "_wight_damage_taken",
                            },
                        ),
                        _set(
                            "target.native_undead_max_hp_reduction_receipts",
                            "_wight_maximum_receipts",
                        ),
                        _when(
                            "target.recovery_max_hp_reduction == null",
                            [
                                _set("target.recovery_max_hp_reduction", 0),
                            ],
                        ),
                        _set(
                            "target.recovery_max_hp_reduction",
                            "target.recovery_max_hp_reduction + _wight_maximum_reduction",
                        ),
                        _set("target.max_hp", "max(0, target.max_hp - _wight_damage_taken)"),
                        _set("target.hp", "min(target.hp, target.max_hp)"),
                        _set("target.bloodied", "target.hp <= floor(target.max_hp * 0.5)"),
                        _when(
                            maximum_zero,
                            [
                                _set("_wight_maximum_zero_death", True),
                                _set("_wight_concentration_target", None),
                                _when(
                                    "target.concentration_target != null",
                                    [
                                        _set(
                                            "_wight_concentration_target",
                                            "target.concentration_target",
                                        )
                                    ],
                                ),
                                *death,
                            ],
                        ),
                        _require(
                            "target.current_body_source_id == null and "
                            "target.magic_jar_role == null",
                            "Join actual changed physical bodies after native maximum-zero death.",
                        ),
                    ],
                ),
            ],
        ),
    ]


def _life_drain_packet(tools):
    """Clone the passed actual scalar receiver and add only native maximum loss."""
    packet = _actual_composite_body(tools, composite=False)
    observers = [p for p in packet["parameters"] if p["name"] == "observe_damage_type"]
    if len(observers) != 1 or "necrotic" not in observers[0]["enum_values"]:
        raise ValueError("Life Drain requires the actual final matching-type damage receipt.")
    boundary = native_receiving_boundary(packet["effects"])
    boundary.ordinary[boundary.completion_index : boundary.completion_index] = (
        _maximum_loss_effects(tools)
    )
    packet["effects"][0:0] = [
        _call(
            "qualify_srd_native_wight_body",
            {"actor_state": "{source_state_id}"},
            ("_wight_native_actor_id",),
        ),
        _call(
            "qualify_srd_native_wight_receiver", {"target_state": "{target}"}, ("_wight_target_id",)
        ),
    ]
    packet["pre_conditions"].append(
        {
            "condition": "damage_type == 'necrotic' and game_damage_roll == true and "
            "observe_damage_type == 'necrotic' and actual_attack_hit != true and "
            "source_state_id != null and "
            "len(source_state_id) > 0",
            "error": "Use only the actual printed Life Drain save-failure producer.",
        }
    )
    packet.pop("narrative", None)
    packet["metadata"] = {"category": "internal", "visible_to_player": False, "costs_action": False}
    return packet


def _life_drain_tools(tools):
    """Resolve actual native command/action, range, CON save and printed raw damage."""
    geometry = _created_actor_attack_geometry_tool()
    cutoff = next(
        i
        for i, e in enumerate(geometry["effects"])
        if e.get("set", {}).get("field") == "_created_attack_targets"
    )
    geometry["effects"] = geometry["effects"][:cutoff] + [
        _require(
            "_created_attack_distance <= 5", "Life Drain requires actual creature range5 feet."
        ),
    ]
    receiver = _tool(
        "Qualify and retain this actual Life Drain receiving body ID.",
        [_entity("target_state")],
        [
            _require(
                "target_state.creature_type != null and target_state.is_object != true and "
                "target_state.is_alive == true and "
                "target_state.form_statistics_replaced != true and "
                "target_state.form_cloud != true and target_state.magic_jar_role == null and "
                "target_state.current_body_source_id == null",
                "Select the actual native living body; changed physical bodies remain unsupported.",
            ),
            _set("_wight_target_id", "_target_state_id"),
        ],
    )
    save = _tool(
        "Receive Life Drain's actual DC13 CON saving throw and printed failure damage.",
        [
            _entity("actor_state"),
            _entity("target_state"),
            _optional("lasting_state", resolve="entity", default="state"),
        ],
        [
            _call("qualify_srd_native_wight_body", {"actor_state": "{_actor_state_id}"}),
            _call("qualify_srd_native_wight_receiver", {"target_state": "{_target_state_id}"}),
            _call(
                "qualify_srd_native_wight_life_drain_geometry",
                {"actor_state": "{_actor_state_id}", "target_state": "{_target_state_id}"},
            ),
            _set("_wight_save_character", None),
            _when(
                "target_state.creature_kind == 'character'",
                [
                    _set("_wight_save_character", "_target_state_id"),
                ],
            ),
            _call(
                "resolve_srd_divination_save",
                {
                    "target_state": "{_target_state_id}",
                    "actor": "{_wight_save_character}",
                    "ability": "constitution",
                    "dc": 13,
                },
                ("_divination_save_success", "_d20_test", "_adjusted_total"),
            ),
            _set("_save_roll", "_d20_test"),
            _set("_save_total", "_adjusted_total"),
            _set("_wight_life_drain_outcome", "'success'"),
            _set("_wight_life_drain_roll", None),
            _set("_wight_damage_taken", 0),
            _set("_wight_packet_hp_damage", 0),
            _set("_wight_temporary_absorbed", 0),
            _set("_wight_maximum_reduction", 0),
            _set("_wight_maximum_prevented", False),
            _set("_wight_maximum_zero_death", False),
            _set("_wight_maximum_receipt_id", None),
            _when(
                "_divination_save_success != true",
                [
                    _set("_wight_life_drain_outcome", "'failure'"),
                    {
                        "dice": {"expression": "d8", "critical": False},
                        "store_as": "_wight_life_drain_roll",
                    },
                    _set("_wight_life_drain_amount", "_wight_life_drain_roll.total + 2"),
                    _set("_wight_unallocated_roll_penalty", 0),
                    _when(
                        "actor_state.has_actor_spell_modifiers == true",
                        [
                            _call(
                                "modify_srd_actor_damage_roll",
                                {
                                    "actor_state": "{_actor_state_id}",
                                    "lasting_state": "{_lasting_state_id}",
                                    "amount": "{_wight_life_drain_amount}",
                                },
                                ("_actor_modified_damage", "_actor_unallocated_roll_penalty"),
                            ),
                            _set("_wight_life_drain_amount", "_actor_modified_damage"),
                            _set(
                                "_wight_unallocated_roll_penalty", "_actor_unallocated_roll_penalty"
                            ),
                        ],
                    ),
                    _call(
                        "apply_srd_native_wight_life_drain_packet",
                        {
                            "target": "{_target_state_id}",
                            "amount": "{_wight_life_drain_amount}",
                            "adjustment": 0,
                            "damage_type": "necrotic",
                            "source_state_id": "{_actor_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                            "critical_hit": False,
                            "game_damage_roll": True,
                            "observe_base_damage": True,
                            "observe_damage_type": "necrotic",
                            "actual_attack_hit": False,
                            "unallocated_roll_penalty": "{_wight_unallocated_roll_penalty}",
                        },
                        (
                            "_wight_damage_taken",
                            "_wight_packet_hp_damage",
                            "_wight_temporary_absorbed",
                            "_wight_maximum_reduction",
                            "_wight_maximum_prevented",
                            "_wight_maximum_zero_death",
                            "_wight_maximum_receipt_id",
                        ),
                    ),
                ],
            ),
        ],
    )
    can_act = next(
        p["condition"]
        for p in tools["resolve_adjudicated_creature_attack"]["pre_conditions"]
        if "actor.conditions" in p["condition"] and "incapacitated" in p["condition"]
    ).replace("actor.", "actor_state.")
    action = _tool(
        "Use only this commanded Wight's actual standalone Life Drain action.",
        [_entity("actor_state"), _entity("target_state")],
        [
            _require(
                "combat.turn_active == true and combat.active_actor == _actor_state_id",
                "Use this Wight's actual active turn.",
            ),
            _require(can_act, "The actual Wight cannot act while incapacitated."),
            _call(
                "qualify_srd_native_wight_life_drain_action", {"actor_state": "{_actor_state_id}"}
            ),
            _call("spend_srd_action", {"magic_action": False}),
            _call(
                "resolve_srd_native_wight_life_drain",
                {"actor_state": "{_actor_state_id}", "target_state": "{_target_state_id}"},
                (
                    "_save_total",
                    "_save_roll",
                    "_wight_life_drain_outcome",
                    "_wight_life_drain_roll",
                    "_wight_damage_taken",
                    "_wight_packet_hp_damage",
                    "_wight_temporary_absorbed",
                    "_wight_maximum_reduction",
                    "_wight_maximum_prevented",
                    "_wight_maximum_zero_death",
                    "_wight_maximum_receipt_id",
                ),
            ),
        ],
    )
    action["metadata"]["result_card"] = {
        "version": 1,
        "footprint": "compact",
        "title": "Life Drain",
        "subtitle": "Constitution saving throw",
        "summary": [
            {"label": "Save", "value": {"path": "context._wight_life_drain_outcome"}},
            {"label": "Necrotic damage taken", "value": {"path": "context._wight_damage_taken"}},
            {"label": "Maximum HP lost", "value": {"path": "context._wight_maximum_reduction"}},
        ],
        "details": [
            {"label": "Constitution dice", "value": {"path": "context._save_roll.results"}},
            {"label": "Constitution total", "value": {"path": "context._save_total"}},
            {"label": "DC", "value": 13},
            {
                "label": "Necrotic dice",
                "value": {"path": "context._wight_life_drain_roll.results"},
                "optional": True,
            },
            {
                "label": "Temporary HP absorbed",
                "value": {"path": "context._wight_temporary_absorbed"},
            },
            {"label": "Maximum zero death", "value": {"path": "context._wight_maximum_zero_death"}},
        ],
    }
    return {
        "qualify_srd_native_wight_body": _wight_body_tool(),
        "qualify_srd_native_wight_receiver": receiver,
        "qualify_srd_native_wight_life_drain_action": _wight_body_tool(True),
        "qualify_srd_native_wight_life_drain_geometry": geometry,
        "apply_srd_native_wight_life_drain_packet": _life_drain_packet(tools),
        "resolve_srd_native_wight_life_drain": save,
        "use_srd_native_wight_life_drain": action,
    }


def extend_runtime(text):
    """Compose source-private Life Drain while public Wight routines remain closed."""
    _validate_source()
    tools = _load_runtime(text)["tools"]
    changed = {
        name: tool
        for name, tool in (_wight_actor_tools(tools) | _life_drain_tools(tools)).items()
        if tools.get(name) != tool
    }
    return _replace_tools(text, changed) if changed else text


def bind_native_wight_life_drain_consumers(text):
    """Refresh only the native receiver from final packet/body callbacks before reaction capture."""
    tools = _load_runtime(text)["tools"]
    packet = _life_drain_packet(tools)
    name = "apply_srd_native_wight_life_drain_packet"
    return text if tools.get(name) == packet else _replace_tools(text, {name: packet})
