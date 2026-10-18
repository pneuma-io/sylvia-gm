"""Pause an actual final weapon hit for a paid Smite, then settle its original packet once."""

import re
from copy import deepcopy

if __package__:
    from .srd_attack_damage_components import typed_component_mitigation
    from .srd_qualified_packet_implementations import _expanded_core
    from .srd_retained_spell_reactions import _private_inputs, _restore_rows, _rows
    from .srd_smite_casting_bridge import BEGIN, bridge_tool
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
    from .srd_timed_senses import _loop, _replace_tools
else:
    from srd_attack_damage_components import typed_component_mitigation
    from srd_qualified_packet_implementations import _expanded_core
    from srd_retained_spell_reactions import _private_inputs, _restore_rows, _rows
    from srd_smite_casting_bridge import BEGIN, bridge_tool
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
    from srd_timed_senses import _loop, _replace_tools

INTENT = "request_after_hit_smite"
EVENT = "_hit_rider_event_id"
MARKER = "attack_hit_riders_v1"
TYPE_MARKER = "attack_hit_riders_coalesced_types_v2"
CARD_MARKER = "attack_hit_riders_whole_type_cards_v3"
CARD_FORMAT = "format_srd_saved_hit_type_receipts"
CARD_CONDITION = (
    "len(_hit_type_receipts) > 0 and (_component_1_effective_damage == null or "
    "_component_2_effective_damage == null)"
)
COMPONENT_CARD_TEXTS = {
    "After defenses: {_component_1_effective_damage} Piercing and "
    "{_component_2_effective_damage} Poison.",
    "After defenses: {_component_1_effective_damage} Piercing and "
    "{_component_2_effective_damage} Necrotic.",
}
CAPTURE = "capture_srd_final_hit_riders"
PUBLIC_TOOLS = {BEGIN}
PACKETS = {"apply_srd_qualifying_damage", "apply_srd_qualifying_composite_damage"}
WRAPPERS = {
    "apply_srd_divine_favor_weapon_damage",
    "apply_srd_form_attack_damage",
    "apply_srd_insect_poison_jab_damage",
    "apply_srd_insect_web_bolt_damage",
    "apply_srd_created_ghoul_bite_damage",
}


def _lists(effects):
    yield effects
    for effect in effects:
        for key in ("on_true", "on_false"):
            if key in effect:
                yield from _lists(effect[key])
        if "for_each" in effect:
            yield from _lists(effect["for_each"]["do"])


def _lasting(value):
    if isinstance(value, str):
        return re.sub(r"\bstate\.", "lasting_state.", value)
    if isinstance(value, list):
        return [_lasting(v) for v in value]
    if isinstance(value, dict):
        return {k: _lasting(v) for k, v in value.items()}
    return value


def _event(field):
    return "state.pending_srd_actions.{pending_action_id}." + field


def _supported_packet_guard():
    """An unjoined native wrapper must not silently discard an earned Mark or Smite."""
    hit = "lasting_state.pending_srd_actions[pending_action_id]"
    return _when(
        "_hit_saved_components == true",
        [
            _require(
                "("
                + hit
                + ".smite_component == null and len("
                + hit
                + ".rider_mark_components) == 0) or "
                + hit
                + ".rider_packet_settled == true",
                "This native attack's saved Mark/Smite packet producer is not yet supported.",
            )
        ],
    )


def _nullable(field, expression):
    return [_set(field, None), _when(expression + " != null", [_set(field, expression)])]


def _capture_tool():
    """Earn current-source components before the child casting can replace Concentration."""
    return _tool(
        "Capture each earned final-hit source once before any damage or child casting.",
        [
            _entity("attacker_state"),
            _entity("target_state"),
            _parameter("pending_action_id"),
            _parameter(
                "minor_elementals_damage_type",
                "enum",
                required=False,
                default="fire",
                enum_values=["acid", "cold", "fire", "lightning"],
            ),
        ],
        [
            _set("_hit_event", "state.pending_srd_actions[pending_action_id]"),
            _require(
                "_hit_event.status == 'open' and _hit_event.stage == 'attack_hit_riders' and "
                "_hit_event.actor_state_id == _attacker_state_id and "
                "_hit_event.target_state_id == _target_state_id and "
                "(_hit_event.attack_outcome == 'hit' or "
                "_hit_event.attack_outcome == 'critical_hit') and "
                "state.pending_srd_action_stack[-1] == pending_action_id",
                "Capture only the real innermost final hit after defensive settlement.",
            ),
            _require(
                "_hit_event.rider_marks_captured != true", "This hit already earned its dice."
            ),
            _set("_rider_components", []),
            _set("_rider_damage_rolls", []),
            _set("_hit_critical", "_hit_event.attack_outcome == 'critical_hit'"),
            _when(
                "attacker_state.has_attack_rider_owned_sources == true",
                [
                    _call(
                        "roll_srd_attack_mark_components",
                        {
                            "attacker_state": "{_attacker_state_id}",
                            "target_state": "{_target_state_id}",
                            "critical_hit": "{_hit_critical}",
                        },
                        ("_rider_components", "_rider_damage_rolls"),
                    ),
                ],
            ),
            _set(_event("rider_mark_components"), "_rider_components"),
            _set(_event("rider_mark_rolls"), "_rider_damage_rolls"),
            _set("_conjure_minor_qualifies", False),
            _set("_conjure_minor_amount", 0),
            _set("_conjure_minor_type", "minor_elementals_damage_type"),
            _set("_conjure_minor_roll", None),
            _when(
                "state.has_spell_environments == true",
                [
                    _call(
                        "roll_srd_conjure_minor_damage_component",
                        {
                            "caster_state": "{_attacker_state_id}",
                            "target_state": "{_target_state_id}",
                            "critical_hit": "{_hit_critical}",
                            "damage_type": "{minor_elementals_damage_type}",
                        },
                        (
                            "_conjure_minor_qualifies",
                            "_conjure_minor_amount",
                            "_conjure_minor_type",
                            "_conjure_minor_roll",
                        ),
                    ),
                ],
            ),
            *[
                _set(_event("rider_minor_" + field), "_conjure_minor_" + field)
                for field in ("qualifies", "amount", "type")
            ],
            *_nullable(_event("rider_minor_roll"), "_conjure_minor_roll"),
            _set("_curse_extra_amount", 0),
            _set("_curse_extra_roll", None),
            _when(
                "state.has_affliction_damage_curses == true",
                [
                    _call(
                        "roll_srd_curse_damage_component",
                        {
                            "caster_state": "{_attacker_state_id}",
                            "target_state": "{_target_state_id}",
                            "critical_hit": "{_hit_critical}",
                        },
                        ("_curse_extra_amount", "_curse_extra_roll"),
                    ),
                ],
            ),
            _set(_event("rider_curse_amount"), "_curse_extra_amount"),
            *_nullable(_event("rider_curse_roll"), "_curse_extra_roll"),
            _set(_event("rider_marks_captured"), True),
        ],
    )


def _expanded_packet(tools, name):
    """Read the actual final body through only the approved named qualified delegate."""
    tool = deepcopy(tools[name])
    first = tool["effects"][0]
    if first.get("call") in {
        "execute_srd_qualified_scalar_packet",
        "execute_srd_qualified_composite_packet",
    }:
        tool["effects"] = _expanded_core(tools[first["call"]], True) + tool["effects"][1:]
    return tool


def _component(role, kind, amount, roll=None, source="''", spell="''"):
    return {
        "role": "'" + role + "'",
        "damage_type": kind,
        "amount": amount,
        "roll": roll,
        "source_id": source,
        "spell": spell,
    }


def _push_component(component):
    return {"list_push": {"list": "_hit_raw_components", "value": component}}


def _receipt_defaults():
    """Publish fresh receipts even when a real completion defers or misses damage."""
    return [
        _set("_hit_rider_deferred", False),
        _set("_hit_rider_receipts", []),
        _set("_hit_type_receipts", []),
        _set("_hit_raw_components", []),
    ]


def _type_card_tool():
    """Format only actual whole-type receipts when rounded damage has no individual split."""
    tool = _tool(
        "Present the saved attack's actual whole-type receiving amounts.",
        [_parameter("receipts", "array", items={"type": "object"})],
        [
            _set("_hit_type_card_text", "'After defenses (whole types): '"),
            _set("_hit_type_card_separator", "''"),
            _loop(
                "receipts",
                "_hit_type_card_receipt",
                [
                    {
                        "call": "record_srd_result_text",
                        "params": {
                            "text": "{_hit_type_card_text}{_hit_type_card_separator}"
                            "{_hit_type_card_receipt.effective} "
                            "{_hit_type_card_receipt.damage_type}",
                        },
                        "export_context": {"_hit_type_card_text": "_recorded_result_text"},
                    },
                    _set("_hit_type_card_separator", "'; '"),
                ],
            ),
            _call(
                "record_srd_result_text",
                {"text": "{_hit_type_card_text}."},
                ("_recorded_result_text",),
            ),
        ],
    )
    tool["metadata"] = {"category": "internal", "visible_to_player": False, MARKER: True}
    return tool


def _bind_component_cards(effects):
    """Adapt the exact native Ghoul/Insect receiving text, retaining numeric ordinary cards."""
    for rows in list(_lists(effects)):
        for index, effect in enumerate(rows):
            if effect.get("call") != "record_srd_result_text":
                continue
            text = effect["params"].get("text")
            if text not in COMPONENT_CARD_TEXTS:
                continue
            rows[index] = {
                "condition": CARD_CONDITION,
                "on_true": [
                    _call(
                        CARD_FORMAT,
                        {"receipts": "{_hit_type_receipts}"},
                        ("_recorded_result_text",),
                    )
                ],
                "on_false": [effect],
            }


def _bond_receipt(prefix):
    """Keep the actual final Bond qualifier, replacing only its old component accounting."""
    candidates = [
        effects
        for effects in _lists(prefix)
        if any(e.get("call") == "check_srd_warding_bond_damage" for e in effects)
    ]
    if not candidates:
        return []
    if len(candidates) != 1:
        raise ValueError("Saved hit needs exactly one genuine Bond receipt owner.")
    effects = deepcopy(candidates[0])
    active = next(e for e in effects if e.get("condition") == "_ward_bond_effective == true")
    body = active["on_true"]
    first = next(
        i for i, e in enumerate(body) if e.get("set", {}).get("field") == "_bond_damage_components"
    )
    last = next(
        i for i, e in enumerate(body) if e.get("set", {}).get("field") == "_bond_pending_reflection"
    )
    if body[last] != _set("_bond_pending_reflection", True):
        raise ValueError("Saved hit cannot replace a changed Bond commitment.")
    body[first:last] = [
        _require(
            "len(_hit_received_types) <= 5",
            "This Bond receiver supports at most five actual damage types in one packet.",
        ),
        _set("_bond_damage_components", "_hit_received_types"),
    ]
    return [_when("lasting_state.has_spell_wards == true and _effective_damage > 0", effects)]


def _fortitude_receipt(suffix):
    """Feed the inherited lethal callback the fresh whole-packet Radiant type receipt."""
    for effects in _lists(suffix):
        for index, effect in enumerate(effects):
            if effect.get("call") != "resolve_srd_created_undead_fortitude":
                continue
            argument = effect["params"]["radiant_damage"].strip("{}")
            for prior in effects[:index]:
                if prior.get("set", {}).get("field") == argument:
                    prior["set"]["value"] = "_hit_radiant_effective_damage > 0"
                    break
            else:
                effect["params"]["radiant_damage"] = "{_hit_radiant_effective_damage > 0}"


def _coalesced_pipeline(tools, tool, composite, prefix):
    """Allocate the existing raw penalty, then mitigate each genuine game-roll type once."""
    initial = "_component_1_effective_damage" if composite else "_incoming_damage"
    first = next(i for i, e in enumerate(prefix) if e.get("set", {}).get("field") == initial)
    prelude = deepcopy(prefix[:first])
    if not composite:
        ordinary = tools["apply_srd_composite_damage"]["effects"]
        defenses = [
            e for e in ordinary if e.get("condition") == "target.has_timed_actor_defenses == true"
        ]
        if len(defenses) != 1:
            raise ValueError("Saved scalar needs the actual composite defense projection.")
        prelude += deepcopy(defenses)
    base = (
        [
            _push_component(_component("base", "component_1_damage_type", "component_1_amount")),
            _push_component(
                _component("secondary", "component_2_damage_type", "component_2_amount")
            ),
        ]
        if composite
        else [_push_component(_component("base", "damage_type", "max(0, amount + adjustment)"))]
    )
    extra = []
    for role, raw, kind, roll, active in (
        (
            "minor",
            "_hit_packet.rider_minor_amount",
            "_hit_packet.rider_minor_type",
            "_hit_packet.rider_minor_roll",
            "_hit_packet.rider_minor_qualifies == true",
        ),
        (
            "curse",
            "_hit_packet.rider_curse_amount",
            "'necrotic'",
            "_hit_packet.rider_curse_roll",
            "_hit_packet.rider_curse_amount > 0",
        ),
    ):
        amount = "_conjure_minor_amount" if role == "minor" else "_curse_extra_amount"
        extra += [
            _when(
                active,
                [
                    _set(amount, "max(0, " + raw + " - _hit_remaining_penalty)"),
                    _set("_hit_remaining_penalty", "max(0, _hit_remaining_penalty - " + raw + ")"),
                    _push_component(_component(role, kind, amount, roll)),
                ],
            )
        ]
    observation = []
    if any(p["name"] == "observe_damage_type" for p in tool["parameters"]):
        observation = [
            _when(
                "_hit_damage_type == observe_damage_type",
                [
                    _set("_source_observed_type_damage", "_hit_type_effective_damage"),
                ],
            )
        ]
    witness = [deepcopy(effect) for effect in prefix if "_received_temp_hp_before" in str(effect)]
    if len(witness) > 1:
        raise ValueError("Saved hit needs one genuine pre-transition received-damage witness.")
    return [
        *prelude,
        _set("_hit_raw_components", []),
        *base,
        _set("_conjure_minor_qualifies", "_hit_packet.rider_minor_qualifies == true"),
        _set("_conjure_minor_amount", 0),
        _set("_conjure_minor_type", "_hit_packet.rider_minor_type"),
        *_nullable("_conjure_minor_roll", "_hit_packet.rider_minor_roll"),
        _set("_curse_extra_amount", 0),
        *_nullable("_curse_extra_roll", "_hit_packet.rider_curse_roll"),
        _set("_hit_remaining_penalty", "unallocated_roll_penalty"),
        *extra,
        _set("_hit_components", "_hit_packet.rider_mark_components"),
        _when(
            "_hit_packet.smite_component != null",
            [
                {"list_push": {"list": "_hit_components", "value": "_hit_packet.smite_component"}},
            ],
        ),
        _loop(
            "_hit_components",
            "_hit_component",
            [
                _set(
                    "_hit_component_amount",
                    "max(0, _hit_component.amount - _hit_remaining_penalty)",
                ),
                _set(
                    "_hit_remaining_penalty",
                    "max(0, _hit_remaining_penalty - _hit_component.amount)",
                ),
                _push_component(
                    _component(
                        "rider",
                        "_hit_component.damage_type",
                        "_hit_component_amount",
                        "_hit_component.roll",
                        "_hit_component.source_id",
                        "_hit_component.spell",
                    )
                ),
            ],
        ),
        _set("_hit_damage_types", []),
        _loop(
            "_hit_raw_components",
            "_hit_raw_component",
            [
                _when(
                    "_hit_raw_component.damage_type not in _hit_damage_types",
                    [
                        {
                            "list_push": {
                                "list": "_hit_damage_types",
                                "value": "_hit_raw_component.damage_type",
                            }
                        },
                    ],
                ),
            ],
        ),
        _set("_incoming_damage", 0),
        _set("_effective_damage", 0),
        _set("_source_base_effective_damage", 0),
        _set("_source_observed_type_damage", 0),
        _set("_component_1_effective_damage", 0),
        _set("_component_2_effective_damage", 0),
        _set("_hit_radiant_effective_damage", 0),
        _set("_hit_type_receipts", []),
        _set("_hit_received_types", []),
        _set("_hit_rider_receipts", []),
        _loop(
            "_hit_damage_types",
            "_hit_damage_type",
            [
                _set("_hit_type_amount", 0),
                _set("_hit_type_count", 0),
                _loop(
                    "_hit_raw_components",
                    "_hit_raw_component",
                    [
                        _when(
                            "_hit_raw_component.damage_type == _hit_damage_type",
                            [
                                _set(
                                    "_hit_type_amount",
                                    "_hit_type_amount + _hit_raw_component.amount",
                                ),
                                _when(
                                    "_hit_raw_component.amount > 0",
                                    [
                                        _set("_hit_type_count", "_hit_type_count + 1"),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
                *typed_component_mitigation(
                    tools, "_hit_type_", "_hit_type_amount", "_hit_damage_type"
                ),
                _set("_incoming_damage", "_incoming_damage + _hit_type_amount"),
                _set("_effective_damage", "_effective_damage + _hit_type_effective_damage"),
                *observation,
                _when(
                    "_hit_damage_type == 'radiant'",
                    [
                        _set("_hit_radiant_effective_damage", "_hit_type_effective_damage"),
                    ],
                ),
                {
                    "list_push": {
                        "list": "_hit_type_receipts",
                        "value": {
                            "damage_type": "_hit_damage_type",
                            "amount": "_hit_type_amount",
                            "effective": "_hit_type_effective_damage",
                            "component_count": "_hit_type_count",
                        },
                    }
                },
                _when(
                    "_hit_type_effective_damage > 0",
                    [
                        {
                            "list_push": {
                                "list": "_hit_received_types",
                                "value": {
                                    "damage_type": "_hit_damage_type",
                                    "amount": "_hit_type_effective_damage",
                                },
                            }
                        },
                    ],
                ),
                _loop(
                    "_hit_raw_components",
                    "_hit_raw_component",
                    [
                        *[
                            _when(
                                "_hit_raw_component.damage_type == _hit_damage_type and "
                                "_hit_raw_component.role == '" + role + "' and "
                                "_hit_raw_component.amount > 0",
                                [
                                    _set(field, None),
                                    _when(
                                        "_hit_type_count <= 1",
                                        [_set(field, "_hit_type_effective_damage")],
                                    ),
                                ],
                            )
                            for role, field in (
                                ("base", "_source_base_effective_damage"),
                                ("base", "_component_1_effective_damage"),
                                ("secondary", "_component_2_effective_damage"),
                            )
                        ],
                        _when(
                            "_hit_raw_component.damage_type == _hit_damage_type and "
                            "_hit_raw_component.role == 'rider'",
                            [
                                _set(
                                    "_hit_rider_receipt",
                                    {
                                        "source_id": "_hit_raw_component.source_id",
                                        "spell": "_hit_raw_component.spell",
                                        "damage_type": "_hit_damage_type",
                                        "roll": "_hit_raw_component.roll",
                                        "amount": "_hit_raw_component.amount",
                                        "effective": None,
                                        "effective_type_amount": "_hit_type_effective_damage",
                                        "type_coalesced": "_hit_raw_component.amount > 0 and "
                                        "_hit_type_count > 1",
                                    },
                                ),
                                _when(
                                    "_hit_raw_component.amount > 0 and _hit_type_count <= 1",
                                    [
                                        _set(
                                            "_hit_individual_receipt",
                                            {"effective": "_hit_type_effective_damage"},
                                        ),
                                        _set(
                                            "_hit_rider_receipt",
                                            "merge(_hit_rider_receipt, _hit_individual_receipt)",
                                        ),
                                    ],
                                ),
                                _when(
                                    "_hit_raw_component.amount == 0",
                                    [
                                        _set("_hit_individual_receipt", {"effective": 0}),
                                        _set(
                                            "_hit_rider_receipt",
                                            "merge(_hit_rider_receipt, _hit_individual_receipt)",
                                        ),
                                    ],
                                ),
                                {
                                    "list_push": {
                                        "list": "_hit_rider_receipts",
                                        "value": "_hit_rider_receipt",
                                    }
                                },
                            ],
                        ),
                    ],
                ),
            ],
        ),
        *_bond_receipt(prefix),
        *witness,
    ]


def _saved_packet(tools, name):
    """Coalesce saved raw components before the unchanged single receiving lifecycle."""
    tool = _expanded_packet(tools, name)
    next(p for p in tool["parameters"] if p["name"] == "target")["resolve"] = "entity"
    tool["parameters"].append(_parameter(EVENT, model_hidden=True))
    if __package__:
        from .srd_warding_bond_damage import _receiving_start
    else:
        from srd_warding_bond_damage import _receiving_start

    hp = _receiving_start(tool["effects"])
    if (
        sum(
            effect.get("set", {}).get("field") == "_hp_before"
            for rows in _lists(tool["effects"])
            for effect in rows
        )
        != 1
    ):
        raise ValueError("Saved hit rejects an extra or missing HP-before initializer.")
    prefix, suffix = tool["effects"][:hp], deepcopy(tool["effects"][hp:])
    if (
        sum(
            e.get("call") == "roll_srd_conjure_minor_damage_component"
            for rows in _lists(prefix)
            for e in rows
        )
        != 1
        or sum(
            e.get("call") == "roll_srd_curse_damage_component"
            for rows in _lists(prefix)
            for e in rows
        )
        != 1
    ):
        raise ValueError(
            "Saved hit needs exactly one final Minor and Curse raw producer per packet."
        )
    _fortitude_receipt(suffix)
    tool["effects"] = _coalesced_pipeline(tools, tool, "composite" in name, prefix) + suffix
    for effects in list(_lists(tool["effects"])):
        for effect in effects:
            if "call" in effect:
                effect["params"] = {
                    key: "{_target_id}" if value == "{target}" else value
                    for key, value in effect["params"].items()
                }
    tool["effects"] = (
        _lasting(
            [
                _set("_hit_packet", "state.pending_srd_actions[" + EVENT + "]"),
                _require(
                    "_hit_packet.stage == 'attack_hit_settling' and "
                    "_hit_packet.rider_marks_captured == true and "
                    "_hit_packet.rider_packet_settled != true and "
                    "_hit_packet.actor_state_id == source_state_id and "
                    "_hit_packet.target_state_id == _target_id",
                    "Settle only this authenticated original attack's unused final packet.",
                ),
            ]
        )
        + tool["effects"]
        + _lasting(
            [
                _set("state.pending_srd_actions.{" + EVENT + "}.rider_packet_settled", True),
                _set(
                    "state.pending_srd_actions.{" + EVENT + "}.rider_damage_receipts",
                    "_hit_rider_receipts",
                ),
                _set(
                    "state.pending_srd_actions.{" + EVENT + "}.rider_raw_components",
                    "_hit_raw_components",
                ),
                _set(
                    "state.pending_srd_actions.{" + EVENT + "}.rider_type_receipts",
                    "_hit_type_receipts",
                ),
            ]
        )
    )
    tool["metadata"] = {
        "category": "internal",
        "visible_to_player": False,
        "costs_action": False,
        MARKER: True,
    }
    return tool


def _packet_clones(tools):
    """Retain native Favor/form/insect/Ghoul producers; route only their final typed packet."""
    changed = {}
    aliases = {n: n.replace("apply_srd_", "apply_srd_saved_hit_", 1) for n in PACKETS | WRAPPERS}
    for name in sorted(PACKETS):
        changed[aliases[name]] = _saved_packet(tools, name)
    for name in sorted(WRAPPERS & tools.keys()):
        tool = deepcopy(tools[name])
        tool["parameters"].append(_parameter(EVENT, model_hidden=True))
        touched = False
        for effects in _lists(tool["effects"]):
            for effect in effects:
                if effect.get("call") in PACKETS:
                    effect["call"] = aliases[effect["call"]]
                    effect["params"][EVENT] = "{" + EVENT + "}"
                    exports = effect.setdefault("export_context", {})
                    if exports != "*":
                        exports["_hit_rider_receipts"] = "_hit_rider_receipts"
                        for field in ("_hit_type_receipts", "_hit_raw_components"):
                            exports[field] = field
                    touched = True
        if not touched:
            raise ValueError("Saved hit wrapper lost its actual qualified packet: " + name)
        tool["metadata"] = {
            "category": "internal",
            "visible_to_player": False,
            "costs_action": False,
            MARKER: True,
        }
        changed[aliases[name]] = tool
    return changed, aliases


def _suffix(tool, kind, actor, aliases):
    """Factor only the final post-Mirror suffix and retain all later callbacks in order."""
    value = deepcopy(tool)
    indices = [
        i
        for i, e in enumerate(value["effects"])
        if e.get("condition") == "_attack_outcome != 'miss'"
    ]
    if len(indices) != 1:
        raise ValueError("Hit riders need the actual weapon completion hit branch.")
    split = indices[0]
    branch = value["effects"][split]
    mirror = branch["on_true"]
    if (
        len(mirror) != 2
        or mirror[0].get("call") != "resolve_srd_mirror_image_hit"
        or mirror[1].get("condition") != "_mirror_redirected != true"
    ):
        raise ValueError("Hit riders require the final settled Mirror weapon boundary.")
    suffix = [deepcopy(branch), *deepcopy(value["effects"][split + 1 :])]
    suffix[0]["on_true"] = [_when("_mirror_redirected != true", mirror[1]["on_true"])]
    # Every actual owning HP call is redirected; raw base/Favor/poison rolls stay in place.
    for effects in list(_lists(suffix)):
        for i, effect in enumerate(effects):
            if effect.get("call") in aliases:
                saved = deepcopy(effect)
                saved["call"] = aliases[effect["call"]]
                saved["params"][EVENT] = "{pending_action_id}"
                saved.setdefault("export_context", {})["_hit_rider_receipts"] = (
                    "_hit_rider_receipts"
                )
                for field in ("_hit_type_receipts", "_hit_raw_components"):
                    saved["export_context"][field] = field
                effects[i] = {
                    "condition": "_hit_saved_components == true",
                    "on_true": [saved],
                    "on_false": [effect],
                }
    context = sorted(
        _private_inputs(suffix)
        | {
            "_attack_roll",
            "_attack_adjusted_total",
            "_attack_card_modifier",
            "_weapon_damage_type",
            "_attack_outcome",
            "_critical_hit",
            "_mirror_redirected",
            "_hit_saved_components",
        }
    )
    helper_name = "finish_srd_" + kind + "_hit_packet"
    helper = deepcopy(value)
    helper["parameters"] += [_parameter("_hit_suffix_context", "array", items={"type": "object"})]
    if actor == "actor_state":
        helper["parameters"].append(
            _parameter("_hit_weapon_rows", "array", items={"type": "object"})
        )
    helper["effects"] = _restore_rows("_hit_suffix_context")
    if actor == "actor_state":
        helper["effects"].append(_set("weapon_info", "_hit_weapon_rows[0]"))
    helper["effects"] += [*suffix, _supported_packet_guard()]
    helper["metadata"] = {
        "category": "internal",
        "visible_to_player": False,
        "costs_action": False,
        MARKER: True,
    }
    helper.pop("narrative", None)
    param = _parameter(INTENT, "boolean", required=False, default=False)
    value["parameters"].append(param)
    # Before suffix capture the helper parameter itself must be forwarded explicitly.
    helper["parameters"].append(deepcopy(param))
    invoke = {
        "call": helper_name,
        "params": {
            p["name"]: "{_" + p["name"] + "_id}" if p.get("resolve") else "{" + p["name"] + "}"
            for p in value["parameters"]
        },
        "export_context": "*",
    }
    invoke["params"]["_hit_suffix_context"] = "{_hit_suffix_context}"
    if actor == "actor_state":
        invoke["params"]["_hit_weapon_rows"] = "{_hit_weapon_rows}"
    capture = [
        _set("_hit_saved_components", True),
        _set(_event("attack_outcome"), "_attack_outcome"),
        _set(_event("stage"), "'attack_hit_riders'"),
        _call(
            CAPTURE,
            {
                "attacker_state": "{_" + actor + "_id}",
                "target_state": "{_target_id}",
                "pending_action_id": "{pending_action_id}",
                "minor_elementals_damage_type": "{minor_elementals_damage_type}",
            },
        ),
    ]
    prefix = value["effects"][:split]
    value["effects"] = [
        _set("_hit_saved_components", False),
        *_receipt_defaults(),
        *prefix,
        _when(
            "_attack_outcome != 'miss'",
            [
                mirror[0],
                _when(
                    "_mirror_redirected != true",
                    [
                        *_lasting(capture),
                        _set("_hit_rider_deferred", INTENT),
                    ],
                ),
            ],
        ),
        _set("_hit_suffix_context", _rows(context)),
    ]
    if actor == "actor_state":
        value["effects"].append(_set("_hit_weapon_rows", ["weapon_info"]))
    value["effects"] += _lasting(
        [
            _set(_event("hit_suffix_context"), "_hit_suffix_context"),
            *(
                [_set(_event("hit_weapon_rows"), "_hit_weapon_rows")]
                if actor == "actor_state"
                else []
            ),
            {
                "condition": "_hit_rider_deferred == true",
                "on_true": [],
                "on_false": [
                    _when(
                        "_hit_saved_components == true",
                        [_set(_event("stage"), "'attack_hit_settling'")],
                    ),
                    invoke,
                    _set(_event("stage"), "'attack_hit_resolved'"),
                ],
            },
        ]
    )
    value.setdefault("metadata", {})[MARKER] = True
    resume = deepcopy(value)
    resume["effects"] = _lasting(
        [
            *_receipt_defaults(),
            _set("_hit_resume", "state.pending_srd_actions[pending_action_id]"),
            _require(
                "_hit_resume.stage == 'attack_hit_riders' and _hit_resume.status == 'open' and "
                "_hit_resume.rider_marks_captured == true and "
                "_hit_resume.actor_state_id == _" + actor + "_id and "
                "state.pending_srd_action_stack[-1] == pending_action_id",
                "Resume the innermost saved hit after the paid child or decline.",
            ),
            _set("_hit_suffix_context", "_hit_resume.hit_suffix_context"),
            *(
                [_set("_hit_weapon_rows", "_hit_resume.hit_weapon_rows")]
                if actor == "actor_state"
                else []
            ),
            _set(_event("stage"), "'attack_hit_settling'"),
            invoke,
            _set(_event("stage"), "'attack_hit_resolved'"),
        ]
    )
    return value, {helper_name: helper, "resume_srd_" + kind + "_hit": resume}


def _completion(original):
    """Keep the deferred parent; actual child completion removes only that child."""
    tool = deepcopy(original)
    added_cast_parameters = 0
    tool["effects"][:0] = _receipt_defaults()
    found = 0
    for effects in list(_lists(tool["effects"])):
        for effect in effects:
            name = effect.get("call", "")
            if name == "cast_spell":
                added_cast_parameters += "rider_pending_action_id" not in effect["params"]
                effect["params"]["rider_pending_action_id"] = (
                    "{_pending_action.parameters.rider_pending_action_id}"
                )
            if name in {"complete_srd_character_attack", "complete_srd_creature_attack"}:
                effect.setdefault("export_context", {})["_hit_rider_deferred"] = (
                    "_hit_rider_deferred"
                )
                effect["export_context"]["_hit_rider_receipts"] = "_hit_rider_receipts"
                for field in ("_hit_type_receipts", "_hit_raw_components"):
                    effect["export_context"][field] = field
                effect["params"][INTENT] = "{_pending_action.parameters." + INTENT + "}"
                found += 1
    if found != 2:
        raise ValueError("Hit riders require both genuine saved weapon completion owners.")
    # Native cleanup is nested by spell and other pending owner binders; add the exact
    # stage test to its existing keep-open predicate, leaving their lifecycle untouched.
    keeps = [
        e
        for seq in _lists(tool["effects"])
        for e in seq
        if e.get("set", {}).get("field") == "_keep_spell_hit_open"
    ]
    if len(keeps) != 1:
        raise ValueError("Hit riders require the final common pending cleanup fence.")
    keeps[0]["set"]["value"] = (
        "("
        + keeps[0]["set"]["value"]
        + ") or "
        + ("lasting_state.pending_srd_actions[pending_action_id].stage == 'attack_hit_riders'")
    )
    cleanup_at = next(i for i, e in enumerate(tool["effects"]) if e is keeps[0])
    before_cleanup = []
    for kind in ("character_attack", "creature_attack"):
        params = next(
            e["params"]
            for seq in _lists(tool["effects"])
            for e in seq
            if e.get("call") == "complete_srd_" + kind
        )
        # Reuse the unchanged native card builder after actual resumed damage.
        branch = next(
            e
            for seq in _lists(tool["effects"])
            for e in seq
            if e.get("condition", "").startswith("_pending_action.owner_kind == '" + kind)
        )
        resumed = deepcopy(branch["on_true"])
        resumed[0]["call"] = "resume_srd_" + kind + "_hit"
        resumed[0]["params"] = deepcopy(params)
        before_cleanup.append(
            _when(
                "_pending_action.owner_kind == '" + kind + "' and "
                "_pending_action.stage == 'attack_hit_riders' and _hit_rider_deferred != true",
                resumed,
            )
        )
    tool["effects"][cleanup_at:cleanup_at] = before_cleanup + [
        _when(
            "_hit_rider_deferred != true and len(_hit_rider_receipts) > 0",
            [
                _loop(
                    "_hit_rider_receipts",
                    "_hit_card_component",
                    [
                        _call(
                            "record_srd_result_text",
                            {
                                "text": "Dice {_hit_card_component.roll.results}; "
                                "incoming {_hit_card_component.amount} "
                                "{_hit_card_component.damage_type}; "
                                "received for this damage type "
                                "{_hit_card_component.effective_type_amount}.",
                            },
                            ("_recorded_result_text",),
                        ),
                        {
                            "list_push": {
                                "list": "_pending_card_details",
                                "value": {
                                    "label": "'Added damage'",
                                    "value": "_recorded_result_text",
                                },
                            }
                        },
                    ],
                )
            ],
        ),
        _when(
            "_hit_rider_deferred == true",
            [
                _set("_pending_card_title", "'Weapon hit awaiting Smite choice'"),
                _set(
                    "_pending_card_summary", [{"label": "'Damage'", "value": "'Not resolved yet'"}]
                ),
                _set("_pending_card_details", []),
            ],
        ),
    ]
    tool.setdefault("metadata", {})[MARKER] = True
    tool["metadata"][TYPE_MARKER] = True
    tool["metadata"][CARD_MARKER] = True
    _bind_component_cards(tool["effects"])
    tool["metadata"][MARKER + "_added_cast_parameters"] = added_cast_parameters
    return tool


def bind_runtime(text):
    """Join after final Reaction/Mirror cloning and before declaration serialization."""
    tools = _load_runtime(text)["tools"]
    marker = tools["complete_srd_pending_action"].get("metadata", {})
    if marker.get(MARKER):
        if marker.get(CARD_MARKER):
            return text
        text = restore_runtime(text)
        tools = _load_runtime(text)["tools"]
    changed, aliases = _packet_clones(tools)
    changed[CAPTURE] = _capture_tool()
    changed[CARD_FORMAT] = _type_card_tool()
    for kind, actor in (("character_attack", "actor_state"), ("creature_attack", "actor")):
        name = "complete_srd_" + kind
        changed[name], helpers = _suffix(tools[name], kind, actor, aliases)
        changed.update(helpers)
        for prefix in ("begin_srd_", "continue_srd_"):
            source = prefix + kind + ("_targeting" if prefix == "continue_srd_" else "")
            if source in tools:
                tool = deepcopy(tools[source])
                tool["parameters"].append(
                    _parameter(INTENT, "boolean", required=False, default=False)
                )
                if prefix == "begin_srd_":
                    # Allocation owns the original parameter object; add this actual choice once.
                    tool["effects"].append(
                        _lasting(
                            _set(
                                "state.pending_srd_actions.{_pending_action_id}.parameters."
                                + INTENT,
                                INTENT,
                            )
                        )
                    )
                changed[source] = tool
    changed["complete_srd_pending_action"] = _completion(tools["complete_srd_pending_action"])
    changed[BEGIN] = bridge_tool(tools["begin_srd_spell_action"])
    return _replace_tools(text, changed)


def _restore_damage_calls(effects):
    """Unwrap only this saved/ordinary packet choice, leaving every source effect unchanged."""
    result = []
    for original in effects:
        if original.get("condition") == CARD_CONDITION:
            if len(original.get("on_false", [])) != 1:
                raise ValueError("Hit restoration requires its exact native component card.")
            result.extend(deepcopy(original["on_false"]))
            continue
        if original.get("condition") == "_hit_saved_components == true":
            if len(original.get("on_false", [])) != 1:
                raise ValueError("Hit restoration needs its exact ordinary packet alternative.")
            result.extend(deepcopy(original["on_false"]))
            continue
        effect = deepcopy(original)
        for key in ("on_true", "on_false"):
            if key in effect:
                effect[key] = _restore_damage_calls(effect[key])
        if "for_each" in effect:
            effect["for_each"]["do"] = _restore_damage_calls(effect["for_each"]["do"])
        result.append(effect)
    return result


def restore_runtime(text):
    """Restore the final original owners before family generation and remove only owned helpers."""
    tools = _load_runtime(text)["tools"]
    pending = tools["complete_srd_pending_action"]
    if not pending.get("metadata", {}).get(MARKER):
        return text
    typed = pending["metadata"].get(TYPE_MARKER) is True
    receipt_prefix = _receipt_defaults() if typed else _receipt_defaults()[:2]
    changed, remove = {}, {CAPTURE, BEGIN}
    for kind in ("character_attack", "creature_attack"):
        name, helper_name = "complete_srd_" + kind, "finish_srd_" + kind + "_hit_packet"
        owner, helper = deepcopy(tools[name]), tools[helper_name]
        effects = owner["effects"]
        boundary = next(
            i for i, e in enumerate(effects) if e.get("condition") == "_attack_outcome != 'miss'"
        )
        start = 1 + int(any(p["name"] == "_hit_weapon_rows" for p in helper["parameters"]))
        if helper["effects"][-1] != _supported_packet_guard():
            raise ValueError("Hit restoration lost its explicit unsupported-producer guard.")
        suffix = _restore_damage_calls(helper["effects"][start:-1])
        mirror = deepcopy(effects[boundary]["on_true"][0])
        suffix[0]["on_true"].insert(0, mirror)
        expected_prefix = [_set("_hit_saved_components", False), *receipt_prefix]
        if effects[: len(expected_prefix)] != expected_prefix:
            raise ValueError("Hit restoration found a changed completion prefix.")
        owner["effects"] = deepcopy(effects[len(expected_prefix) : boundary]) + suffix
        owner["parameters"] = [p for p in owner["parameters"] if p["name"] != INTENT]
        owner["metadata"].pop(MARKER)
        changed[name] = owner
        remove |= {helper_name, "resume_srd_" + kind + "_hit"}
        for prefix in ("begin_srd_", "continue_srd_"):
            source = prefix + kind + ("_targeting" if prefix == "continue_srd_" else "")
            if source not in tools:
                continue
            tool = deepcopy(tools[source])
            tool["parameters"] = [p for p in tool["parameters"] if p["name"] != INTENT]
            if prefix == "begin_srd_":
                expected = _lasting(
                    _set(
                        "state.pending_srd_actions.{_pending_action_id}.parameters." + INTENT,
                        INTENT,
                    )
                )
                if tool["effects"][-1] != expected:
                    raise ValueError("Hit restoration found a changed saved intent boundary.")
                tool["effects"].pop()
            changed[source] = tool
    value = deepcopy(pending)
    value["effects"] = _restore_damage_calls(value["effects"])
    expected_prefix = receipt_prefix
    if value["effects"][: len(expected_prefix)] != expected_prefix:
        raise ValueError("Hit restoration requires its exact common completion marker.")
    del value["effects"][: len(expected_prefix)]
    value["effects"] = [
        e
        for e in value["effects"]
        if not (
            e.get("condition", "").startswith("_pending_action.owner_kind == '")
            and e["condition"].endswith("_hit_rider_deferred != true")
        )
        and e.get("condition")
        not in {
            "_hit_rider_deferred == true",
            "_hit_rider_deferred != true and len(_hit_rider_receipts) > 0",
        }
    ]
    added = value["metadata"].pop(MARKER + "_added_cast_parameters")
    for effects in _lists(value["effects"]):
        for effect in effects:
            if effect.get("call") in {
                "complete_srd_character_attack",
                "complete_srd_creature_attack",
            }:
                effect["params"].pop(INTENT)
                effect["export_context"].pop("_hit_rider_deferred")
                effect["export_context"].pop("_hit_rider_receipts")
                if typed:
                    for field in ("_hit_type_receipts", "_hit_raw_components"):
                        effect["export_context"].pop(field)
            if effect.get("call") == "cast_spell" and added:
                effect["params"].pop("rider_pending_action_id")
                added -= 1
            if effect.get("set", {}).get("field") == "_keep_spell_hit_open":
                clause = effect["set"]["value"]
                ending = (
                    ") or lasting_state.pending_srd_actions[pending_action_id].stage == "
                    "'attack_hit_riders'"
                )
                if not clause.startswith("(") or not clause.endswith(ending):
                    raise ValueError("Hit restoration found a changed cleanup predicate.")
                effect["set"]["value"] = clause[1 : -len(ending)]
    value["metadata"].pop(MARKER)
    value["metadata"].pop(TYPE_MARKER, None)
    if value["metadata"].pop(CARD_MARKER, False):
        remove.add(CARD_FORMAT)
    changed["complete_srd_pending_action"] = value
    remove |= {
        n
        for n in tools
        if n.startswith("apply_srd_saved_hit_") and tools[n].get("metadata", {}).get(MARKER)
    }
    text = _replace_tools(text, changed)
    for name in sorted(remove):
        pattern = (
            rf"(?ms)^  {re.escape(name)}:[^\n]*\r?\n.*?(?=^  [A-Za-z_][A-Za-z0-9_]*:|^procedures:)"
        )
        text, count = re.subn(pattern, "", text, count=1)
        if count != 1:
            raise ValueError("Hit restoration lost its owned helper boundary: " + name)
    remaining = _load_runtime(text)
    if any(
        e.get("call") in remove
        for tool in remaining["tools"].values()
        for seq in _lists(tool["effects"])
        for e in seq
    ):
        raise ValueError("A later owner retained an after-hit helper; restore its binder first.")
    return text
