"""A paid water wall owns actual turn motion, saves, swimming and queued exit falls."""

import json
import re
from copy import deepcopy
from pathlib import Path

if __package__:
    from .srd_courier_chronology import _emit
    from .srd_divination_spells import _normalize
    from .srd_feather_fall import _ground
    from .srd_illusion_appearance import HIDDEN_SOURCE
    from .srd_reverse_gravity import _receiver as _gravity_receiver
    from .srd_scene_relations import _array, _tool_scene
    from .srd_spell_environment import _blade_barrier_tools
    from .srd_spell_travel import _scene_subject
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from .srd_timed_senses import _loop, _optional, _public, _push
else:
    from srd_courier_chronology import _emit
    from srd_divination_spells import _normalize
    from srd_feather_fall import _ground
    from srd_illusion_appearance import HIDDEN_SOURCE
    from srd_reverse_gravity import _receiver as _gravity_receiver
    from srd_scene_relations import _array, _tool_scene
    from srd_spell_environment import _blade_barrier_tools
    from srd_spell_travel import _scene_subject
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from srd_timed_senses import _loop, _optional, _public, _push

SOURCE = {"tsunami": (171, "Tsunami", "Unseen Servant")}
SOURCE_SHA256 = "4e9318dfc49c5ff9c5481efc467936c72c42614e69ee88e8a84ae12503de949b"
SWIMMING_SHA256 = "09b06feeb97ea2800c23025831e4047d34ab8d1bbf9b137c72fb1441f6f2a123"
PROFILES = {
    "tsunami": {
        "family": "tsunami",
        "base_level": 8,
        "casting_time": "1_minute",
        "range_kind": "distance",
        "range_feet": 5280,
        "concentration": True,
        "duration_rounds": 6,
        "max_length_feet": 300,
        "max_height_feet": 300,
        "max_thickness_feet": 50,
    }
}
CAST_CHOICES = [
    _array("tsunami_origin", default=[0, 0, 0]),
    _optional("tsunami_axis", "enum", "north_south", enum_values=["north_south", "east_west"]),
    _optional("tsunami_length", "number", 300, minimum=0.000001, maximum=300),
    _optional("tsunami_height", "number", 300, minimum=0.000001, maximum=300),
    _optional("tsunami_thickness", "number", 50, minimum=0.000001, maximum=50),
]
PUBLIC_TOOLS = {"swim_srd_tsunami", "settle_srd_tsunami_exits"}
PENDING = "state.pending_srd_action_stack == null or len(state.pending_srd_action_stack) == 0"


def validate_profiles():
    """Pin every printed fixed clause, including the carry/damage size distinction."""
    root = Path(__file__).resolve().parents[2]
    corpus = json.loads((root / "rulesets/dnd-srd-5.2.1/content/corpus/spells.json").read_text())
    page = next(p for p in corpus["page_records"] if p["page"] == 171)
    assert page["text_sha256"] == SOURCE_SHA256
    text = " ".join(page["text"].split())
    for phrase in (
        "Level 8 Conjuration",
        "Casting Time: 1 minute",
        "Range: 1 mile",
        "Concentration, up to 6 rounds",
        "300 feet long",
        "300 feet high",
        "50 feet thick",
        "6d10",
        "5d10",
        "Huge or smaller",
        "once per round",
        "At the end of the turn",
        "Strength (Athletics)",
        "falls to the ground",
    ):
        if phrase not in text:
            raise ValueError("Missing Tsunami source clause: " + phrase)
    glossary = json.loads(
        (root / "rulesets/dnd-srd-5.2.1/content/corpus/rules_glossary.json").read_text()
    )
    swimming = next(p for p in glossary["page_records"] if p["page"] == 189)
    assert swimming["text_sha256"] == SWIMMING_SHA256
    assert "costs 1 extra foot (2 extra feet in Difficult Terrain)" in " ".join(
        swimming["text"].split()
    )
    return SOURCE_SHA256


def _source(identity="source_id"):
    return [
        _set("_tsunami_source", None),
        _set("_tsunami_lookup", []),
        _when("state.tsunami_sources != null", [_set("_tsunami_lookup", "state.tsunami_sources")]),
        _loop(
            "_tsunami_lookup",
            "_tsunami_candidate",
            [
                _when(
                    f"_tsunami_candidate.id == {identity}",
                    [
                        _require(
                            "_tsunami_source == null", "Tsunami source identity is ambiguous."
                        ),
                        _set("_tsunami_source", "_tsunami_candidate"),
                    ],
                ),
            ],
        ),
        _require("_tsunami_source != null", "Select an authentic retained Tsunami source."),
    ]


def _save():
    return [
        _set("_tsunami_saved", []),
        _set("_tsunami_save_rows", "state.tsunami_sources"),
        _loop(
            "_tsunami_save_rows",
            "_tsunami_save_row",
            [
                {
                    "condition": "_tsunami_save_row.id == _tsunami_source.id",
                    "on_true": [_push("_tsunami_saved", "_tsunami_source")],
                    "on_false": [_push("_tsunami_saved", "_tsunami_save_row")],
                }
            ],
        ),
        _set("state.tsunami_sources", "_tsunami_saved"),
    ]


def _patch(values):
    return [
        _set("_tsunami_patch", values),
        _set("_tsunami_source", "merge(_tsunami_source, _tsunami_patch)"),
        *_save(),
    ]


def _volume(origin="_tsunami_source.origin", height="_tsunami_source.height"):
    return [
        _set("_tsunami_x", "_tsunami_source.thickness * 0.5"),
        _set("_tsunami_y", "_tsunami_source.length * 0.5"),
        _when(
            "_tsunami_source.axis == 'east_west'",
            [
                _set("_tsunami_x", "_tsunami_source.length * 0.5"),
                _set("_tsunami_y", "_tsunami_source.thickness * 0.5"),
            ],
        ),
        _set(
            "_tsunami_volume",
            {
                "kind": "'box'",
                "min": [
                    f"{origin}[0] - _tsunami_x",
                    f"{origin}[1] - _tsunami_y",
                    f"{origin}[2]",
                ],
                "max": [
                    f"{origin}[0] + _tsunami_x",
                    f"{origin}[1] + _tsunami_y",
                    f"{origin}[2] + {height}",
                ],
            },
        ),
    ]


def _body_tool():
    """Reuse the owning Box/body swept interval math, with this saved source volume."""
    original = _blade_barrier_tools()["resolve_srd_blade_barrier_body_segment"]
    tool = json.loads(
        json.dumps(original)
        .replace("_blade_body_source", "_tsunami_source")
        .replace("area_id", "source_id")
        .replace("'blade'", "'tsunami'")
    )
    # Only the registry lookup is replaced; the actual-body interval math is unchanged.
    index = next(
        i
        for i, effect in enumerate(tool["effects"])
        if effect.get("set", {}).get("field") == "_tsunami_source"
    )
    tool["effects"][index : index + 1] = _source()
    tool["description"] = "Intersect the actual admitted box body/path with this water wall."
    tool["effects"].append(
        _when(
            "_body_hit == true and start[0] == end[0] and start[1] == end[1] and start[2] == "
            "end[2]",
            [
                _set(
                    "_body_hit",
                    " and ".join(
                        f"start[{i}] > _body_bounds.min[{i}] and start[{i}] < _body_bounds.max[{i}]"
                        for i in range(3)
                    ),
                )
            ],
        )
    )
    return tool


def _collect(source="source_id", start="_tsunami_source.origin", end="_tsunami_source.origin"):
    """Collect each actual creature once using its body and the wall's swept displacement."""
    return [
        _set("_tsunami_members", []),
        _set("_tsunami_member_bodies", []),
        _set("_tsunami_scene_bodies", "state.spatial_scene.entities"),
        _set("_tsunami_wall_delta", [f"{end}[{i}] - {start}[{i}]" for i in range(3)]),
        _loop(
            "_tsunami_scene_bodies",
            "_tsunami_body",
            [
                _when(
                    "_tsunami_body.active == true and _tsunami_body.metadata.participant_ref != "
                    "null",
                    [
                        _set(
                            "_tsunami_relative_end",
                            [
                                f"_tsunami_body.position[{i}] - _tsunami_wall_delta[{i}]"
                                for i in range(3)
                            ],
                        ),
                        _call(
                            "resolve_srd_tsunami_body_segment",
                            {
                                "source_id": "{" + source + "}",
                                "moving_subject": "{_tsunami_body.id}",
                                "start": "{_tsunami_body.position}",
                                "end": "{_tsunami_relative_end}",
                            },
                            ["_body_hit"],
                        ),
                        _when(
                            "_body_hit == true",
                            [
                                _require(
                                    "_tsunami_body.metadata.participant_ref not in "
                                    "_tsunami_members",
                                    "Tsunami needs one actual active body per creature.",
                                ),
                                _push("_tsunami_members", "_tsunami_body.metadata.participant_ref"),
                                _push("_tsunami_member_bodies", "_tsunami_body.id"),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        _require(
            "len(_tsunami_members) <= 64", "This water wall supports at most64 actual creatures."
        ),
    ]


def _roll_tool():
    return _tool_scene(
        "Roll the actual wall event once, preserving native dice and caster modifiers.",
        [_entity("caster_state"), _parameter("source_id"), _parameter("count", "integer")],
        [
            *_source(),
            _require("count >= 0 and count <= 6", "Use the source event's printed dice."),
            _set("_tsunami_faces", []),
            _set("_tsunami_total", 0),
            _when(
                "count > 0",
                [
                    {"dice": {"expression": "{count}d10"}, "store_as": "_tsunami_damage"},
                    _set("_tsunami_faces", "_tsunami_damage.results"),
                    _set("_tsunami_total", "_tsunami_damage.total"),
                ],
            ),
            _set("_tsunami_raw", "_tsunami_total"),
            _when(
                "count > 0 and caster_state.has_actor_spell_modifiers == true",
                [
                    _call(
                        "modify_srd_actor_raw_damage_roll",
                        {"actor_state": "{_caster_state_id}", "amount": "{_tsunami_raw}"},
                        ["_ray_raw_damage"],
                    ),
                    _set("_tsunami_raw", "_ray_raw_damage"),
                ],
            ),
            _set("_tsunami_amount", "max(0, _tsunami_raw)"),
            _set("_tsunami_penalty", "max(0, -_tsunami_raw)"),
            *_patch(
                {
                    "event_faces": "_tsunami_faces",
                    "event_total": "_tsunami_total",
                    "event_amount": "_tsunami_amount",
                    "event_penalty": "_tsunami_penalty",
                }
            ),
        ],
    )


def _damage_tool():
    return _tool_scene(
        "Resolve this creature's actual Strength save and one final typed packet.",
        [_entity("target_state"), _parameter("source_id"), _parameter("initial", "boolean")],
        [
            *_source(),
            _require(
                "_tsunami_source.active == true", "The actual water source ended during its event."
            ),
            _set("_tsunami_damage_allowed", True),
            _when(
                "initial != true",
                [
                    _require(
                        "target_state.size_rank != null", "Use the creature's admitted actual size."
                    ),
                    _set("_tsunami_damage_allowed", "target_state.size_rank <= 4"),
                ],
            ),
            _set("_tsunami_seen", False),
            _set("_tsunami_old_events", "_tsunami_source.events"),
            _loop(
                "_tsunami_old_events",
                "_tsunami_old_event",
                [
                    _when(
                        "_tsunami_old_event.target == _target_state_id and "
                        "_tsunami_old_event.initial == initial and (initial == true or "
                        "_tsunami_old_event.round == combat.round)",
                        [_set("_tsunami_seen", True)],
                    ),
                ],
            ),
            _when(
                "_tsunami_damage_allowed == true and _tsunami_seen != true",
                [
                    _set("_tsunami_save_actor", None),
                    _when(
                        "target_state.creature_kind == 'character'",
                        [_set("_tsunami_save_actor", "_target_state_id")],
                    ),
                    _call(
                        "resolve_srd_divination_save",
                        {
                            "actor": "{_tsunami_save_actor}",
                            "target_state": "{_target_state_id}",
                            "ability": "strength",
                            "dc": "{_tsunami_source.save_dc}",
                        },
                        ["_d20_test", "_adjusted_total", "_outcome"],
                    ),
                    _set("_tsunami_save_faces", "_d20_test.results"),
                    _set("_tsunami_save_raw_total", "_d20_test.total"),
                    _set("_tsunami_save_total", "_adjusted_total"),
                    _set("_tsunami_save_modifier", "_adjusted_total - _d20_test.total"),
                    _set("_tsunami_save_outcome", "_outcome"),
                    _set("_tsunami_divisor", 1),
                    _set("_tsunami_deliver", "_outcome == 'failure'"),
                    _when(
                        "initial == true",
                        [
                            _set("_tsunami_deliver", True),
                            _when("_outcome == 'success'", [_set("_tsunami_divisor", 2)]),
                        ],
                    ),
                    _set("_tsunami_event_round", 0),
                    _when(
                        "combat.encounter_active == true",
                        [_set("_tsunami_event_round", "combat.round")],
                    ),
                    # Stamp before any damage observer can retire or otherwise mutate the source.
                    _set("_tsunami_events", "_tsunami_source.events"),
                    _push(
                        "_tsunami_events",
                        {
                            "target": "_target_state_id",
                            "initial": "initial",
                            "round": "_tsunami_event_round",
                            "tick": "state.lasting_tick",
                            "save_faces": "_tsunami_save_faces",
                            "save_total": "_tsunami_save_total",
                            "save_raw_total": "_tsunami_save_raw_total",
                            "save_modifier": "_tsunami_save_modifier",
                            "outcome": "_tsunami_save_outcome",
                            "dice": "_tsunami_source.event_faces",
                            "amount": "_tsunami_source.event_amount",
                            "divisor": "_tsunami_divisor",
                        },
                    ),
                    *_patch({"events": "_tsunami_events"}),
                    _set("_tsunami_before_hp", "target_state.hp"),
                    _set("_tsunami_before_temp", 0),
                    _when(
                        "target_state.temp_hp != null",
                        [_set("_tsunami_before_temp", "target_state.temp_hp")],
                    ),
                    _when(
                        "_tsunami_deliver == true",
                        [
                            _set(
                                "_tsunami_packet_amount",
                                "_tsunami_source.event_amount // _tsunami_divisor",
                            ),
                            _set(
                                "_tsunami_packet_remainder",
                                "_tsunami_source.event_amount % _tsunami_divisor",
                            ),
                            _call(
                                "apply_srd_qualifying_damage",
                                {
                                    "target": "{_target_state_id}",
                                    "source_state_id": "{_tsunami_source.caster}",
                                    "amount": "{_tsunami_packet_amount}",
                                    "adjustment": 0,
                                    "damage_type": "bludgeoning",
                                    "damage_roll_divisor": "{_tsunami_divisor}",
                                    "game_damage_roll": True,
                                    "damage_roll_base_remainder": "{_tsunami_packet_remainder}",
                                    "unallocated_roll_penalty": "{_tsunami_source.event_penalty}",
                                },
                            ),
                        ],
                    ),
                    _call(
                        "read_srd_tsunami_damage_loss",
                        {
                            "target_state": "{_target_state_id}",
                            "before_hp": "{_tsunami_before_hp}",
                            "before_temp": "{_tsunami_before_temp}",
                        },
                        ["_tsunami_actual_loss"],
                    ),
                    _set(
                        "target_state.last_tsunami_save",
                        {
                            "source_id": "source_id",
                            "initial": "initial",
                            "round": "_tsunami_event_round",
                            "faces": "_tsunami_save_faces",
                            "raw_total": "_tsunami_save_raw_total",
                            "modifier": "_tsunami_save_modifier",
                            "total": "_tsunami_save_total",
                            "outcome": "_tsunami_save_outcome",
                            "actual_loss": "_tsunami_actual_loss",
                        },
                    ),
                ],
            ),
        ],
    )


def _receipt_tool():
    return _tool_scene(
        "Authenticate only the current source-derived unconsumed carried-body receipt.",
        [_parameter("subject"), _array("destination"), _parameter("plane")],
        [
            _set("_tsunami_receipt", "state.tsunami_pending_motion"),
            _require(
                "_tsunami_receipt != null and _tsunami_receipt.kind == 'tsunami_carry' and "
                "_tsunami_receipt.consumed == false and _tsunami_receipt.subject == subject and "
                "_tsunami_receipt.plane == plane and _tsunami_receipt.tick == state.lasting_tick",
                "Only the authentic paid water wall can force this carried movement.",
            ),
            *_source("_tsunami_receipt.source_id"),
            _require(
                "_tsunami_source.active == true and "
                "_tsunami_source.sequence == _tsunami_receipt.sequence and "
                "_tsunami_source.caster == _tsunami_receipt.caster and "
                "combat.active_actor == _tsunami_source.caster",
                "The exact advancing water source or actual caster turn changed.",
            ),
            *_scene_subject("{subject}", "_tsunami_moving_body"),
            _require(
                "_tsunami_moving_body.id == _tsunami_receipt.body_id and "
                "_tsunami_moving_body.height == _tsunami_receipt.height and "
                "_tsunami_moving_body.footprint.half_extents[0] == _tsunami_receipt.extents[0] and "
                "_tsunami_moving_body.footprint.half_extents[1] == _tsunami_receipt.extents[1]",
                "The actual carried body changed before movement.",
            ),
            *[
                _require(
                    f"_tsunami_moving_body.position[{i}] == _tsunami_receipt.origin[{i}] and "
                    f"destination[{i}] == _tsunami_receipt.destination[{i}] and "
                    f"destination[{i}] == _tsunami_receipt.origin[{i}] + "
                    f"_tsunami_source.motion_delta[{i}]",
                    "Use the exact fifty-foot source endpoint.",
                )
                for i in range(3)
            ],
            _set("_tsunami_motion_allowed", True),
        ],
    )


def _carry_tool():
    return _tool_scene(
        "Carry every actual living creature already inside, regardless of size.",
        [_entity("target_state"), _parameter("source_id")],
        [
            *_source(),
            *_scene_subject("{_target_state_id}", "_tsunami_carried_body"),
            _require(
                "target_state.is_alive == true",
                "Dead-body water transport needs its actual receiver.",
            ),
            _set("_tsunami_carry_origin", "_tsunami_carried_body.position"),
            _set(
                "_tsunami_carry_destination",
                [
                    f"_tsunami_carry_origin[{i}] + _tsunami_source.motion_delta[{i}]"
                    for i in range(3)
                ],
            ),
            _set(
                "state.tsunami_pending_motion",
                {
                    "kind": "'tsunami_carry'",
                    "source_id": "source_id",
                    "sequence": "_tsunami_source.sequence",
                    "caster": "_tsunami_source.caster",
                    "tick": "state.lasting_tick",
                    "subject": "_target_state_id",
                    "body_id": "_tsunami_carried_body.id",
                    "origin": "_tsunami_carry_origin",
                    "destination": "_tsunami_carry_destination",
                    "height": "_tsunami_carried_body.height",
                    "extents": "_tsunami_carried_body.footprint.half_extents",
                    "plane": "_tsunami_source.plane",
                    "consumed": False,
                },
            ),
            _call(
                "move_srd_tsunami_subject",
                {
                    "subject": "{_target_state_id}",
                    "destination": "{_tsunami_carry_destination}",
                    "plane": "{_tsunami_source.plane}",
                },
            ),
            _set("state.tsunami_pending_motion", None),
        ],
    )


def _turn_tools():
    start = _tool_scene(
        "Advance this caster's wall once at an authentic later caster-start turn.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            *_source(),
            _require(
                "combat.encounter_active == true and combat.active_actor == _caster_state_id",
                "Water wall advancement requires the actual caster's starting turn.",
            ),
            _call(
                "refresh_srd_tsunami_source",
                {"caster_state": "{_caster_state_id}", "source_id": "{source_id}"},
            ),
            *_source(),
            _when(
                "_tsunami_source.active == true and caster_state.turns_started > "
                "_tsunami_source.cast_turn and "
                "caster_state.turns_started > _tsunami_source.last_motion_turn",
                [
                    *_scene_subject("{_caster_state_id}", "_tsunami_caster_body"),
                    _require(
                        "_tsunami_caster_body.metadata.plane == _tsunami_source.plane",
                        "Cross-plane away direction needs an actual qualifying source relation.",
                    ),
                    _set(
                        "_tsunami_dx",
                        "_tsunami_source.origin[0] - _tsunami_caster_body.position[0]",
                    ),
                    _set(
                        "_tsunami_dy",
                        "_tsunami_source.origin[1] - _tsunami_caster_body.position[1]",
                    ),
                    _set(
                        "_tsunami_away_length",
                        "(_tsunami_dx * _tsunami_dx + _tsunami_dy * _tsunami_dy) ** 0.5",
                    ),
                    _require(
                        "_tsunami_away_length > 0",
                        "An exactly coincident wall/caster needs its admitted away direction.",
                    ),
                    _set(
                        "_tsunami_delta",
                        [
                            "50 * _tsunami_dx * _tsunami_away_length ** -1",
                            "50 * _tsunami_dy * _tsunami_away_length ** -1",
                            0,
                        ],
                    ),
                    _set("_tsunami_old_origin", "_tsunami_source.origin"),
                    _set(
                        "_tsunami_new_origin",
                        [f"_tsunami_old_origin[{i}] + _tsunami_delta[{i}]" for i in range(3)],
                    ),
                    *_collect(),
                    _set("_tsunami_carry_ids", "_tsunami_members"),
                    *_collect(start="_tsunami_old_origin", end="_tsunami_new_origin"),
                    _set("_tsunami_later_ids", "_tsunami_members"),
                    *_patch(
                        {
                            "motion_delta": "_tsunami_delta",
                            "last_motion_turn": "caster_state.turns_started",
                        }
                    ),
                    _loop(
                        "_tsunami_carry_ids",
                        "_tsunami_carry_id",
                        [
                            _call(
                                "carry_srd_tsunami_creature",
                                {"target_state": "{_tsunami_carry_id}", "source_id": "{source_id}"},
                            ),
                        ],
                    ),
                    *_source(),
                    _require(
                        "_tsunami_source.active == true",
                        "Source ending during carried receiving effects is pending.",
                    ),
                    *_volume("_tsunami_new_origin"),
                    *_patch({"origin": "_tsunami_new_origin", "volume": "_tsunami_volume"}),
                    _call(
                        "roll_srd_tsunami_event",
                        {
                            "caster_state": "{_caster_state_id}",
                            "source_id": "{source_id}",
                            "count": "{_tsunami_source.damage_dice}",
                        },
                    ),
                    _loop(
                        "_tsunami_later_ids",
                        "_tsunami_later_id",
                        [
                            _call(
                                "damage_srd_tsunami_creature",
                                {
                                    "target_state": "{_tsunami_later_id}",
                                    "source_id": "{source_id}",
                                    "initial": False,
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    end = _tool_scene(
        "Shrink only at the end of a caster turn that actually advanced this wall.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            *_source(),
            _when(
                "_tsunami_source.active == true and _tsunami_source.last_motion_turn == "
                "caster_state.turns_started and "
                "_tsunami_source.last_shrink_turn < caster_state.turns_started",
                [
                    _require(
                        "combat.active_actor == _caster_state_id",
                        "Shrink only on the actual advancing caster turn.",
                    ),
                    *_collect(),
                    _set("_tsunami_before_shrink", "_tsunami_members"),
                    *_patch(
                        {
                            "height": "max(0, _tsunami_source.height - 50)",
                            "damage_dice": "max(0, _tsunami_source.damage_dice - 1)",
                            "last_shrink_turn": "caster_state.turns_started",
                        }
                    ),
                    {
                        "condition": "_tsunami_source.height <= 0",
                        "on_true": [
                            _call("retire_srd_tsunami_source", {"source_id": "{source_id}"}),
                            _call(
                                "end_srd_concentration_node",
                                {
                                    "source_state": "{_caster_state_id}",
                                    "target_state": "{_caster_state_id}",
                                },
                            ),
                        ],
                        "on_false": [
                            *_volume(),
                            *_patch({"volume": "_tsunami_volume"}),
                            *_collect(),
                            _set("_tsunami_after_shrink", "_tsunami_members"),
                            _loop(
                                "_tsunami_before_shrink",
                                "_tsunami_shrink_exit",
                                [
                                    _when(
                                        "_tsunami_shrink_exit not in _tsunami_after_shrink",
                                        [
                                            _call(
                                                "queue_srd_tsunami_exit",
                                                {
                                                    "source_id": "{source_id}",
                                                    "subject": "{_tsunami_shrink_exit}",
                                                },
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    },
                ],
            ),
        ],
    )
    boundary = _tool_scene(
        "Visit only authentic sources owned by the actual beginning/ending caster.",
        [_entity("caster_state"), _parameter("boundary", "enum", enum_values=["start", "end"])],
        [
            _when(
                "state.has_tsunami_sources == true",
                [
                    _set("_tsunami_turn_sources", "state.tsunami_sources"),
                    _loop(
                        "_tsunami_turn_sources",
                        "_tsunami_turn_source",
                        [
                            _when(
                                "_tsunami_turn_source.active == true and "
                                "_tsunami_turn_source.caster == _caster_state_id",
                                [
                                    {
                                        "condition": "boundary == 'start'",
                                        "on_true": [
                                            _call(
                                                "advance_srd_tsunami_turn",
                                                {
                                                    "caster_state": "{_caster_state_id}",
                                                    "source_id": "{_tsunami_turn_source.id}",
                                                },
                                            )
                                        ],
                                        "on_false": [
                                            _call(
                                                "shrink_srd_tsunami_turn",
                                                {
                                                    "caster_state": "{_caster_state_id}",
                                                    "source_id": "{_tsunami_turn_source.id}",
                                                },
                                            )
                                        ],
                                    }
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    return {
        "advance_srd_tsunami_turn": start,
        "shrink_srd_tsunami_turn": end,
        "observe_srd_tsunami_turn": boundary,
    }


def _exit_tool():
    return _tool_scene(
        "Queue an exact source/body exit without allocating a nested fall window.",
        [_parameter("source_id"), _parameter("subject")],
        [
            *_source(),
            *_scene_subject("{subject}", "_tsunami_exit_body"),
            _require(
                "_tsunami_exit_body.metadata.participant_ref != null",
                "Water exit falls currently require an actual creature.",
            ),
            _set("_tsunami_exit_rows", []),
            _when(
                "state.tsunami_exits != null", [_set("_tsunami_exit_rows", "state.tsunami_exits")]
            ),
            _set("_tsunami_exit_seen", False),
            _loop(
                "_tsunami_exit_rows",
                "_tsunami_exit_old",
                [
                    _when(
                        "_tsunami_exit_old.source_id == source_id and _tsunami_exit_old.body_id "
                        "== _tsunami_exit_body.id and "
                        "_tsunami_exit_old.pending == true",
                        [_set("_tsunami_exit_seen", True)],
                    ),
                ],
            ),
            _when(
                "_tsunami_exit_seen != true",
                [
                    _push(
                        "_tsunami_exit_rows",
                        {
                            "source_id": "source_id",
                            "sequence": "_tsunami_source.sequence",
                            "subject": "_tsunami_exit_body.metadata.participant_ref",
                            "body_id": "_tsunami_exit_body.id",
                            "position": "_tsunami_exit_body.position",
                            "plane": "_tsunami_exit_body.metadata.plane",
                            "height": "_tsunami_exit_body.height",
                            "tick": "state.lasting_tick",
                            "pending": True,
                        },
                    ),
                    _set("state.tsunami_exits", "_tsunami_exit_rows"),
                    _set("state.has_tsunami_pending_exits", True),
                ],
            ),
        ],
    )


def _lifetime_tools():
    retire = _tool_scene(
        "Retire only the exact water source and queue its actual contained creature exits.",
        [_parameter("source_id")],
        [
            *_source(),
            _when(
                "_tsunami_source.active == true",
                [
                    *_collect(),
                    _set("_tsunami_end_ids", "_tsunami_members"),
                    *_patch({"active": False, "ended_tick": "state.lasting_tick"}),
                    _loop(
                        "_tsunami_end_ids",
                        "_tsunami_end_id",
                        [
                            _call(
                                "queue_srd_tsunami_exit",
                                {"source_id": "{source_id}", "subject": "{_tsunami_end_id}"},
                            ),
                        ],
                    ),
                    _set("state.has_tsunami_sources", False),
                ],
            ),
        ],
    )
    refresh_one = _tool_scene(
        "Validate the exact concentration sequence and six-round elapsed boundary.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            *_source(),
            _when(
                "_tsunami_source.active == true and (state.lasting_tick >= "
                "_tsunami_source.expires_tick or "
                "caster_state.concentration_spell_id != 'tsunami' or "
                "caster_state.concentration_effect_family != 'tsunami' or "
                "caster_state.concentration_sequence != _tsunami_source.sequence)",
                [
                    _call("retire_srd_tsunami_source", {"source_id": "{source_id}"}),
                    _when(
                        "caster_state.concentration_spell_id == 'tsunami' and "
                        "caster_state.concentration_sequence == _tsunami_source.sequence",
                        [
                            _call(
                                "end_srd_concentration_node",
                                {
                                    "source_state": "{_caster_state_id}",
                                    "target_state": "{_caster_state_id}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    refresh = _tool_scene(
        "Refresh only an actually active water source, preserving the one shared clock.",
        [],
        [
            _when(
                "state.has_tsunami_sources == true",
                [
                    _set("_tsunami_refresh_rows", "state.tsunami_sources"),
                    _loop(
                        "_tsunami_refresh_rows",
                        "_tsunami_refresh_row",
                        [
                            _when(
                                "_tsunami_refresh_row.active == true",
                                [
                                    _call(
                                        "refresh_srd_tsunami_source",
                                        {
                                            "caster_state": "{_tsunami_refresh_row.caster}",
                                            "source_id": "{_tsunami_refresh_row.id}",
                                        },
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    caster_end = _tool_scene(
        "Capture the exact water source before its caster's concentration fields are cleared.",
        [_entity("caster_state")],
        [
            _when(
                "caster_state.concentration_effect_family == 'tsunami' and "
                "state.has_tsunami_sources == true",
                [
                    _set("_tsunami_retire_rows", "state.tsunami_sources"),
                    _loop(
                        "_tsunami_retire_rows",
                        "_tsunami_retire_row",
                        [
                            _when(
                                "_tsunami_retire_row.active == true and "
                                "_tsunami_retire_row.caster == _caster_state_id and "
                                "_tsunami_retire_row.sequence == "
                                "caster_state.concentration_sequence",
                                [
                                    _call(
                                        "retire_srd_tsunami_source",
                                        {"source_id": "{_tsunami_retire_row.id}"},
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    return {
        "retire_srd_tsunami_source": retire,
        "refresh_srd_tsunami_source": refresh_one,
        "refresh_srd_tsunami_sources": refresh,
        "retire_srd_tsunami_caster": caster_end,
    }


def _swim_tools():
    permission = _tool_scene(
        "Reject ordinary movement from a caught body without its real successful swim check.",
        [_entity("actor_state"), _array("destination"), _parameter("mode")],
        [
            _when(
                "state.has_tsunami_pending_exits == true",
                [
                    _set("_tsunami_unsettled_exits", "state.tsunami_exits"),
                    _loop(
                        "_tsunami_unsettled_exits",
                        "_tsunami_unsettled_exit",
                        [
                            _require(
                                "_tsunami_unsettled_exit.pending != true or "
                                "_tsunami_unsettled_exit.subject != _actor_state_id",
                                "Settle the actual queued water exit before further movement.",
                            ),
                        ],
                    ),
                ],
            ),
            _when(
                "state.has_tsunami_sources == true",
                [
                    *_scene_subject("{_actor_state_id}", "_tsunami_swim_body"),
                    _set("_tsunami_swim_sources", "state.tsunami_sources"),
                    _loop(
                        "_tsunami_swim_sources",
                        "_tsunami_swim_source",
                        [
                            _when(
                                "_tsunami_swim_source.active == true",
                                [
                                    _call(
                                        "resolve_srd_tsunami_body_segment",
                                        {
                                            "source_id": "{_tsunami_swim_source.id}",
                                            "moving_subject": "{_actor_state_id}",
                                            "start": "{_tsunami_swim_body.position}",
                                            "end": "{_tsunami_swim_body.position}",
                                        },
                                        ["_body_hit"],
                                    ),
                                    _when(
                                        "_body_hit == true",
                                        [
                                            _set(
                                                "_tsunami_swim_receipt",
                                                "actor_state.tsunami_swim_receipt",
                                            ),
                                            _require(
                                                "mode == 'swim' and _tsunami_swim_receipt != "
                                                "null and "
                                                "_tsunami_swim_receipt.consumed == false and "
                                                "_tsunami_swim_receipt.source_id == "
                                                "_tsunami_swim_source.id and "
                                                "_tsunami_swim_receipt.tick == "
                                                "state.lasting_tick and "
                                                "_tsunami_swim_receipt.body_id == "
                                                "_tsunami_swim_body.id",
                                                "A creature caught in the water must actually "
                                                "succeed at the source Athletics check.",
                                            ),
                                            *[
                                                _require(
                                                    f"destination[{i}] == "
                                                    f"_tsunami_swim_receipt.destination[{i}] and "
                                                    f"_tsunami_swim_body.position[{i}] == "
                                                    f"_tsunami_swim_receipt.origin[{i}]",
                                                    "Use only the actual checked swimming path.",
                                                )
                                                for i in range(3)
                                            ],
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    swim = _public(
        _tool_scene(
            "Make the real Strength (Athletics) check, then spend genuine swimming movement.",
            [
                _entity("actor_state"),
                _optional("actor", "string", None, resolve="participant"),
                _parameter("source_id"),
                _array("destination"),
            ],
            [
                _require(PENDING, "Finish the pending action before trying to swim."),
                _require(
                    "actor_state.hp > 0 and (actor_state.conditions == null or 'incapacitated' "
                    "not in actor_state.conditions)",
                    "An incapacitated creature cannot choose swimming movement.",
                ),
                *_source(),
                _require("_tsunami_source.active == true", "Select the actual active water wall."),
                *_scene_subject("{_actor_state_id}", "_tsunami_swim_body"),
                _call(
                    "resolve_srd_tsunami_body_segment",
                    {
                        "source_id": "{source_id}",
                        "moving_subject": "{_actor_state_id}",
                        "start": "{_tsunami_swim_body.position}",
                        "end": "{_tsunami_swim_body.position}",
                    },
                    ["_body_hit"],
                ),
                _require(
                    "_body_hit == true",
                    "Only a creature actually caught in this wall needs its swim check.",
                ),
                _set("_tsunami_athletics_modifier", 0),
                _set("_tsunami_athletics_proficiency", "'none'"),
                {
                    "condition": "actor != null",
                    "on_true": [
                        _require(
                            "_actor_id == _actor_state_id",
                            "Use the actual caught player's character.",
                        ),
                        _when(
                            "actor.data.skill_proficiencies != null and "
                            "'athletics' in actor.data.skill_proficiencies",
                            [_set("_tsunami_athletics_proficiency", "'normal'")],
                        ),
                        _when(
                            "actor.data.skill_expertise != null and 'athletics' in "
                            "actor.data.skill_expertise",
                            [_set("_tsunami_athletics_proficiency", "'double'")],
                        ),
                    ],
                    "on_false": [
                        _require(
                            "actor_state.skill_check_modifiers.athletics != null",
                            "Use the NPC's actual admitted Athletics modifier.",
                        ),
                        _set(
                            "_tsunami_athletics_modifier",
                            "actor_state.skill_check_modifiers.athletics",
                        ),
                    ],
                },
                _call(
                    "d20_test",
                    {
                        "actor": "{_actor_id}",
                        "actor_state": "{_actor_state_id}",
                        "ability": "strength",
                        "kind": "ability_check",
                        "skill": "athletics",
                        "ability_modifier": "{_tsunami_athletics_modifier}",
                        "proficiency_bonus": 0,
                        "proficiency_factor": "{_tsunami_athletics_proficiency}",
                        "circumstantial_modifier": 0,
                        "advantage": False,
                        "disadvantage": False,
                        "target_number": "{_tsunami_source.save_dc}",
                    },
                    ["_d20_test", "_adjusted_total", "_combined_modifier", "_outcome"],
                ),
                _set("_tsunami_swim_success", "_outcome == 'success'"),
                _set(
                    "actor_state.last_tsunami_swim",
                    {
                        "source_id": "source_id",
                        "tick": "state.lasting_tick",
                        "faces": "_d20_test.results",
                        "raw_total": "_d20_test.total",
                        "modifier": "_combined_modifier",
                        "total": "_adjusted_total",
                        "outcome": "_outcome",
                    },
                ),
                _when(
                    "_tsunami_swim_success == true",
                    [
                        _set(
                            "actor_state.tsunami_swim_receipt",
                            {
                                "source_id": "source_id",
                                "tick": "state.lasting_tick",
                                "body_id": "_tsunami_swim_body.id",
                                "origin": "_tsunami_swim_body.position",
                                "destination": "destination",
                                "consumed": False,
                                "sequence": "_tsunami_source.sequence",
                                "plane": "_tsunami_source.plane",
                                "height": "_tsunami_swim_body.height",
                                "extents": "_tsunami_swim_body.footprint.half_extents",
                            },
                        ),
                        _call(
                            "move_srd_scene_creature",
                            {
                                "actor_state": "{_actor_state_id}",
                                "destination": "{destination}",
                                "mode": "swim",
                            },
                        ),
                        _set("actor_state.tsunami_swim_receipt.consumed", True),
                    ],
                ),
            ],
        ),
        "information",
        "Tsunami swimming",
        [("Check succeeded", "_tsunami_swim_success")],
    )
    # Resolve a derived character ID in its own ordinary participant boundary. This
    # also supports omitted public actor choices without manufacturing NPC bonuses.
    effects = swim["effects"]
    first = next(
        i
        for i, e in enumerate(effects)
        if e.get("set", {}).get("field") == "_tsunami_athletics_modifier"
    )
    last = next(i for i, e in enumerate(effects) if e.get("call") == "d20_test")
    athletics = _tool_scene(
        "Resolve genuine participant Athletics or the actual NPC's saved modifier.",
        [
            _entity("actor_state"),
            _optional("actor", "string", None, resolve="participant"),
            _parameter("source_id"),
        ],
        [*_source(), *deepcopy(effects[first : last + 1])],
    )
    effects[first : last + 1] = [
        _set("_tsunami_swim_actor", None),
        _when("actor != null", [_set("_tsunami_swim_actor", "_actor_id")]),
        _when(
            "actor == null and actor_state.creature_kind == 'character'",
            [_set("_tsunami_swim_actor", "_actor_state_id")],
        ),
        _call(
            "resolve_srd_tsunami_athletics",
            {
                "actor_state": "{_actor_state_id}",
                "actor": "{_tsunami_swim_actor}",
                "source_id": "{source_id}",
            },
            ["_d20_test", "_adjusted_total", "_combined_modifier", "_outcome"],
        ),
    ]
    observe = _tool_scene(
        "Observe genuine committed movement; carried receipts suppress premature exit "
        "observations.",
        [_entity("actor_state"), _array("start")],
        [
            _when(
                "state.has_tsunami_sources == true and (state.tsunami_pending_motion == null or "
                "state.tsunami_pending_motion.subject != _actor_state_id)",
                [
                    *_scene_subject("{_actor_state_id}", "_tsunami_observed_body"),
                    _set("_tsunami_observer_sources", "state.tsunami_sources"),
                    _loop(
                        "_tsunami_observer_sources",
                        "_tsunami_observer_source",
                        [
                            _when(
                                "_tsunami_observer_source.active == true",
                                [
                                    _call(
                                        "resolve_srd_tsunami_body_segment",
                                        {
                                            "source_id": "{_tsunami_observer_source.id}",
                                            "moving_subject": "{_actor_state_id}",
                                            "start": "{start}",
                                            "end": "{start}",
                                        },
                                        ["_body_hit"],
                                    ),
                                    _set("_tsunami_was_inside", "_body_hit"),
                                    _call(
                                        "resolve_srd_tsunami_body_segment",
                                        {
                                            "source_id": "{_tsunami_observer_source.id}",
                                            "moving_subject": "{_actor_state_id}",
                                            "start": "{_tsunami_observed_body.position}",
                                            "end": "{_tsunami_observed_body.position}",
                                        },
                                        ["_body_hit"],
                                    ),
                                    _when(
                                        "_tsunami_was_inside == true and _body_hit != true",
                                        [
                                            _call(
                                                "queue_srd_tsunami_exit",
                                                {
                                                    "source_id": "{_tsunami_observer_source.id}",
                                                    "subject": "{_actor_state_id}",
                                                },
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    segment = _tool_scene(
        "Qualify the actual successful source swim path for one final movement segment.",
        [_entity("actor_state"), _array("destination"), _parameter("mode")],
        [
            _set("_tsunami_ordinary_swim", False),
            _when(
                "mode == 'swim' and state.has_tsunami_sources == true and "
                "actor_state.tsunami_swim_receipt != null",
                [
                    _set("_tsunami_segment_receipt", "actor_state.tsunami_swim_receipt"),
                    _require(
                        "_tsunami_segment_receipt.consumed == false and "
                        "_tsunami_segment_receipt.tick == state.lasting_tick",
                        "Only the current genuine successful Athletics receipt authorizes this "
                        "source swim.",
                    ),
                    *_source("_tsunami_segment_receipt.source_id"),
                    *_scene_subject("{_actor_state_id}", "_tsunami_segment_body"),
                    _require(
                        "_tsunami_source.active == true and _tsunami_source.sequence == "
                        "_tsunami_segment_receipt.sequence and "
                        "_tsunami_segment_body.id == _tsunami_segment_receipt.body_id and "
                        "_tsunami_segment_body.metadata.plane == _tsunami_segment_receipt.plane "
                        "and "
                        "_tsunami_segment_body.height == _tsunami_segment_receipt.height and "
                        "_tsunami_segment_body.footprint.half_extents[0] == "
                        "_tsunami_segment_receipt.extents[0] and "
                        "_tsunami_segment_body.footprint.half_extents[1] == "
                        "_tsunami_segment_receipt.extents[1]",
                        "The actual water source, body or plane changed before this swim segment.",
                    ),
                    _set(
                        "_tsunami_checked_delta",
                        [
                            f"_tsunami_segment_receipt.destination[{i}] - "
                            f"_tsunami_segment_receipt.origin[{i}]"
                            for i in range(3)
                        ],
                    ),
                    _set(
                        "_tsunami_checked_square",
                        "_tsunami_checked_delta[0] ** 2 + _tsunami_checked_delta[1] ** 2 + "
                        "_tsunami_checked_delta[2] ** 2",
                    ),
                    _require(
                        "_tsunami_checked_square > 0", "A swim needs a genuine positive path."
                    ),
                    _set(
                        "_tsunami_segment_start_t",
                        "("
                        + " + ".join(
                            f"(_tsunami_segment_body.position[{i}] - "
                            f"_tsunami_segment_receipt.origin[{i}]) * _tsunami_checked_delta[{i}]"
                            for i in range(3)
                        )
                        + ") * _tsunami_checked_square ** -1",
                    ),
                    _set(
                        "_tsunami_segment_end_t",
                        "("
                        + " + ".join(
                            f"(destination[{i}] - _tsunami_segment_receipt.origin[{i}]) * "
                            f"_tsunami_checked_delta[{i}]"
                            for i in range(3)
                        )
                        + ") * _tsunami_checked_square ** -1",
                    ),
                    _require(
                        "_tsunami_segment_start_t >= -0.000001 and _tsunami_segment_end_t <= "
                        "1.000001 and "
                        "_tsunami_segment_end_t > _tsunami_segment_start_t",
                        "Use only the actually checked forward swim path.",
                    ),
                    *[
                        _require(
                            f"abs(_tsunami_segment_body.position[{i}] - "
                            f"_tsunami_segment_receipt.origin[{i}] - "
                            f"_tsunami_segment_start_t * _tsunami_checked_delta[{i}]) < 0.000001 "
                            f"and "
                            f"abs(destination[{i}] - _tsunami_segment_receipt.origin[{i}] - "
                            f"_tsunami_segment_end_t * _tsunami_checked_delta[{i}]) < 0.000001",
                            "The actual movement segment must lie on the saved checked swim path.",
                        )
                        for i in range(3)
                    ],
                    _call(
                        "resolve_srd_tsunami_body_segment",
                        {
                            "source_id": "{_tsunami_source.id}",
                            "moving_subject": "{_actor_state_id}",
                            "start": "{_tsunami_segment_body.position}",
                            "end": "{destination}",
                        },
                        ["_body_hit", "_body_exit"],
                    ),
                    _require(
                        "_body_hit == true and _body_exit >= 0.999999",
                        "Stop at the actual water exit face; swimming beyond it needs its fall "
                        "consequence first.",
                    ),
                    _set(
                        "_tsunami_ordinary_swim",
                        "(actor_state.swim_speed == null or actor_state.swim_speed <= 0) and "
                        "actor_state.spell_freedom_of_movement != true",
                    ),
                ],
            ),
        ],
    )
    return {
        "check_srd_tsunami_swim": permission,
        "swim_srd_tsunami": swim,
        "resolve_srd_tsunami_athletics": athletics,
        "observe_srd_tsunami_movement": observe,
        "qualify_srd_tsunami_swim_segment": segment,
    }


def _settle_tool():
    return _public(
        _tool_scene(
            "At a completed outer boundary, open genuine falls for actual queued water exits.",
            [],
            [
                _require(
                    PENDING, "Finish the current pending action before settling water exit falls."
                ),
                _set("_pending_action_id", None),
                _set("_tsunami_pending_exits", []),
                _set("_tsunami_settled_exits", []),
                _set("_tsunami_fall_targets", []),
                _when(
                    "state.tsunami_exits != null",
                    [_set("_tsunami_pending_exits", "state.tsunami_exits")],
                ),
                _loop(
                    "_tsunami_pending_exits",
                    "_tsunami_exit",
                    [
                        _when(
                            "_tsunami_exit.pending == true",
                            [
                                *_scene_subject("{_tsunami_exit.subject}", "_tsunami_exit_current"),
                                _require(
                                    "_tsunami_exit_current.id == _tsunami_exit.body_id and "
                                    "_tsunami_exit_current.metadata.plane == _tsunami_exit.plane "
                                    "and "
                                    "_tsunami_exit_current.height == _tsunami_exit.height",
                                    "The actual queued exit body/plane changed; derive the new "
                                    "consequence.",
                                ),
                                *[
                                    _require(
                                        f"_tsunami_exit_current.position[{i}] == "
                                        f"_tsunami_exit.position[{i}]",
                                        "Settle the real queued exit before further movement.",
                                    )
                                    for i in range(3)
                                ],
                                _set("_info_scene_entity", "_tsunami_exit_current"),
                                _set("_info_position", "_tsunami_exit_current.position"),
                                *_ground(),
                                _when(
                                    "_info_position[2] > _fall_ground_height",
                                    [
                                        _require(
                                            "_tsunami_exit.subject not in _tsunami_fall_targets",
                                            "Independent concurrent water falls need source "
                                            "composition.",
                                        ),
                                        _push("_tsunami_fall_targets", "_tsunami_exit.subject"),
                                    ],
                                ),
                                _set(
                                    "_tsunami_exit_patch",
                                    {"pending": False, "settled_tick": "state.lasting_tick"},
                                ),
                                _set("_tsunami_exit", "merge(_tsunami_exit, _tsunami_exit_patch)"),
                            ],
                        ),
                        _push("_tsunami_settled_exits", "_tsunami_exit"),
                    ],
                ),
                _require(
                    "len(_tsunami_fall_targets) <= 64",
                    "At most64 actual water exits settle at this boundary.",
                ),
                _set("state.tsunami_exits", "_tsunami_settled_exits"),
                _set("state.has_tsunami_pending_exits", False),
                _when(
                    "len(_tsunami_fall_targets) > 0",
                    [
                        _call(
                            "begin_srd_scene_fall",
                            {
                                "target_states": "{_tsunami_fall_targets}",
                                "fall_cause": "support_lost",
                            },
                            ["_pending_action_id"],
                        ),
                    ],
                ),
            ],
        ),
        "information",
        "Tsunami exits",
        [("Fall event", "_pending_action_id")],
    )


def _cast_tool():
    return _tool_scene(
        "Install the paid minute-cast source and resolve each actual initial creature.",
        [
            _entity("caster_state"),
            _parameter("slot_level", "integer"),
            _parameter("save_dc", "integer"),
            _parameter("concentration_modifier", "integer"),
            *deepcopy(CAST_CHOICES),
            *deepcopy(HIDDEN_SOURCE),
        ],
        [
            _require(
                "state.has_tsunami_sources != true and state.has_tsunami_pending_exits != true",
                "Concurrent or unsettled water walls need their actual overlap owner.",
            ),
            _require(
                "tsunami_length > 0 and tsunami_length <= 300 and tsunami_height > 0 and "
                "tsunami_height <= 300 and "
                "tsunami_thickness > 0 and tsunami_thickness <= 50",
                "Use the source's actual bounded wall dimensions.",
            ),
            *_scene_subject("{_caster_state_id}", "_tsunami_cast_body"),
            _require("_tsunami_cast_body.metadata.plane != null", "Use the actual casting plane."),
            _call(
                "measure_srd_information_distance",
                {"start": "{_tsunami_cast_body.position}", "end": "{tsunami_origin}"},
                ["_scene_distance"],
            ),
            _require("_scene_distance <= 5280", "The actual chosen wall point exceeds one mile."),
            _call(
                "srd_information_barriers",
                {
                    "start": "{_tsunami_cast_body.position}",
                    "end": "{tsunami_origin}",
                    "rule": "solid",
                },
                ["_information_blocked"],
            ),
            _require(
                "_information_blocked != true",
                "A solid barrier blocks this actual spell placement.",
            ),
            _when("state.tsunami_sources == null", [_set("state.tsunami_sources", [])]),
            _require(
                "len(state.tsunami_sources) < 128", "Retire obsolete water wall history first."
            ),
            _set("_tsunami_focus", ["_caster_state_id"]),
            _call(
                "start_srd_condition_concentration",
                {
                    "caster_state": "{_caster_state_id}",
                    "lasting_state": "state",
                    "targets": "{_tsunami_focus}",
                    "spell": "tsunami",
                    "duration_rounds": 6,
                    "save_modifier": "{concentration_modifier}",
                },
            ),
            _set("caster_state.concentration_effect_family", "'tsunami'"),
            _call(
                "format_srd_information_projection",
                {"message": "tsunami:{_caster_state_id}:{caster_state.concentration_sequence}"},
                ["_info_projection_message"],
            ),
            _set("_tsunami_cast_id", "_info_projection_message"),
            _set("_tsunami_cast_turn", 0),
            _when(
                "caster_state.turns_started != null",
                [_set("_tsunami_cast_turn", "caster_state.turns_started")],
            ),
            _set(
                "_tsunami_source",
                {
                    "id": "_tsunami_cast_id",
                    "mode": "'tsunami'",
                    "spell": "'tsunami'",
                    "caster": "_caster_state_id",
                    "sequence": "caster_state.concentration_sequence",
                    "active": True,
                    "started_tick": "state.lasting_tick",
                    "expires_tick": "state.lasting_tick + 12",
                    "origin": "tsunami_origin",
                    "plane": "_tsunami_cast_body.metadata.plane",
                    "axis": "tsunami_axis",
                    "length": "tsunami_length",
                    "height": "tsunami_height",
                    "thickness": "tsunami_thickness",
                    "damage_dice": 5,
                    "save_dc": "save_dc",
                    "cast_turn": "_tsunami_cast_turn",
                    "last_motion_turn": "_tsunami_cast_turn",
                    "last_shrink_turn": "_tsunami_cast_turn",
                    "events": [],
                    "motion_delta": [0, 0, 0],
                    "spell_level": "slot_level",
                    "cast_id": "spell_cast_id",
                    "source_caster_id": "_caster_state_id",
                    "dispel_ongoing": True,
                    "ongoing_duration": True,
                },
            ),
            *_volume(),
            _set("_tsunami_patch", {"volume": "_tsunami_volume"}),
            _set("_tsunami_source", "merge(_tsunami_source, _tsunami_patch)"),
            *[
                _require(
                    f"_tsunami_volume.min[{i}] >= state.spatial_scene.map.bounds.min[{i}] and "
                    f"_tsunami_volume.max[{i}] <= state.spatial_scene.map.bounds.max[{i}]",
                    "The actual wall must fit within the admitted scene.",
                )
                for i in range(3)
            ],
            _set("_tsunami_cast_rows", "state.tsunami_sources"),
            _push("_tsunami_cast_rows", "_tsunami_source"),
            _set("state.tsunami_sources", "_tsunami_cast_rows"),
            _set("state.has_tsunami_sources", True),
            *_collect("_tsunami_cast_id"),
            _set("_tsunami_initial_ids", "_tsunami_members"),
            _call(
                "roll_srd_tsunami_event",
                {
                    "caster_state": "{_caster_state_id}",
                    "source_id": "{_tsunami_cast_id}",
                    "count": 6,
                },
            ),
            _loop(
                "_tsunami_initial_ids",
                "_tsunami_initial_id",
                [
                    _call(
                        "damage_srd_tsunami_creature",
                        {
                            "target_state": "{_tsunami_initial_id}",
                            "source_id": "{_tsunami_cast_id}",
                            "initial": True,
                        },
                    ),
                ],
            ),
            _set("_spell_summary_rows", [{"label": "'Water wall'", "value": "_tsunami_cast_id"}]),
            _set("_spell_detail_rows", []),
            *_source("_tsunami_cast_id"),
            _set("_tsunami_card_rows", "_tsunami_source.events"),
            _loop(
                "_tsunami_card_rows",
                "_tsunami_card_row",
                [
                    _call(
                        "read_srd_tsunami_save_loss",
                        {"target_state": "{_tsunami_card_row.target}"},
                        ["_tsunami_save_loss"],
                    ),
                    _call(
                        "format_srd_information_projection",
                        {
                            "message": "Strength {_tsunami_card_row.save_total}; "
                            "{_tsunami_card_row.outcome}; dice {_tsunami_card_row.dice}; divisor "
                            "{_tsunami_card_row.divisor}; actual HP and TempHP lost "
                            "{_tsunami_save_loss}"
                        },
                        ["_info_projection_message"],
                    ),
                    _push(
                        "_spell_detail_rows",
                        {"label": "_tsunami_card_row.target", "value": "_info_projection_message"},
                    ),
                ],
            ),
        ],
    )


def _cast_binding(tools, changed):
    """Replace only this family's branch, retaining final Slow/lost-action guards."""
    branch = _when(
        "_spell_definition.family == 'tsunami'",
        [
            _call(
                "cast_srd_tsunami",
                {
                    "caster_state": "{_caster_state_id}",
                    "slot_level": "{_slot_level}",
                    "save_dc": "{_casting_profile.save_dc}",
                    "concentration_modifier": "{_casting_profile.concentration_save_modifier}",
                    **{p["name"]: "{" + p["name"] + "}" for p in CAST_CHOICES},
                },
                ["_spell_summary_rows", "_spell_detail_rows"],
            ),
        ],
    )

    def replace(effects):
        found = False
        for i, e in enumerate(effects):
            if "_spell_definition.family == 'tsunami'" in e.get("condition", ""):
                fresh = deepcopy(branch)
                fresh["condition"] = e["condition"]
                effects[i] = fresh
                found = True
            else:
                for key in ("on_true", "on_false"):
                    if key in e:
                        found = replace(e[key]) or found
        return found

    def insert(effects):
        indices = [
            i
            for i, e in enumerate(effects)
            if "_spell_definition.family ==" in e.get("condition", "")
        ]
        if indices:
            effects.insert(indices[-1] + 1, deepcopy(branch))
            return True
        return any(insert(e[k]) for e in effects for k in ("on_true", "on_false") if k in e)

    for name in ("cast_spell", "cast_spell_node", "cast_spell_outside_combat"):
        if name in tools:
            owner = deepcopy(tools[name])
            existing = {p["name"] for p in owner["parameters"]}
            owner["parameters"] += [deepcopy(p) for p in CAST_CHOICES if p["name"] not in existing]
            if not replace(owner["effects"]) and not insert(owner["effects"]):
                raise ValueError("Tsunami needs the genuine paid dispatch.")
            changed[name] = owner


def extend_runtime(text):
    """Emit paid source/clock/turn leaves; final movement capture runs separately."""
    tools = _load_runtime(text)["tools"]
    changed = {
        "cast_srd_tsunami": _cast_tool(),
        "resolve_srd_tsunami_body_segment": _body_tool(),
        "roll_srd_tsunami_event": _roll_tool(),
        "damage_srd_tsunami_creature": _damage_tool(),
        "qualify_srd_tsunami_motion": _receipt_tool(),
        "carry_srd_tsunami_creature": _carry_tool(),
        "queue_srd_tsunami_exit": _exit_tool(),
        "settle_srd_tsunami_exits": _settle_tool(),
        **_turn_tools(),
        **_lifetime_tools(),
        **_swim_tools(),
        "read_srd_tsunami_damage_loss": _tool_scene(
            "Read fresh HP and TempHP after all actual receiving callbacks.",
            [
                _entity("target_state"),
                _parameter("before_hp", "number"),
                _parameter("before_temp", "number"),
            ],
            [
                _set("_tsunami_after_temp", 0),
                _when(
                    "target_state.temp_hp != null",
                    [_set("_tsunami_after_temp", "target_state.temp_hp")],
                ),
                _set(
                    "_tsunami_actual_loss",
                    "max(0, before_hp - target_state.hp) + max(0, before_temp - "
                    "_tsunami_after_temp)",
                ),
            ],
        ),
        "read_srd_tsunami_save_loss": _tool_scene(
            "Read the last genuine source save's completed HP and TempHP loss.",
            [_entity("target_state")],
            [_set("_tsunami_save_loss", "target_state.last_tsunami_save.actual_loss")],
        ),
    }
    _cast_binding(tools, changed)
    for name, call, params, before in (
        ("advance_srd_lasting_clock", "refresh_srd_tsunami_sources", {}, False),
        (
            "end_srd_concentration_node",
            "retire_srd_tsunami_caster",
            {"caster_state": "{_source_state_id}"},
            True,
        ),
        (
            "begin_combat_turn",
            "observe_srd_tsunami_turn",
            {"caster_state": "{_actor_id}", "boundary": "start"},
            False,
        ),
        (
            "end_combat_turn",
            "observe_srd_tsunami_turn",
            {"caster_state": "{_actor_id}", "boundary": "end"},
            True,
        ),
    ):
        owner = deepcopy(tools[name])
        if call not in str(owner["effects"]):
            insertion = 0 if before else len(owner["effects"])
            if name == "end_combat_turn":
                insertion = next(
                    i
                    for i, e in enumerate(owner["effects"])
                    if e.get("call") == "process_srd_turn_effects"
                )
            owner["effects"].insert(insertion, _call(call, params))
        changed[name] = owner
    for name in PUBLIC_TOOLS:
        owner = changed[name]
        node = deepcopy(owner)
        node["metadata"] = {
            "category": "internal",
            "visible_to_player": False,
            "costs_action": False,
        }
        changed[name + "_node"] = node
        owner["parameters"] = [p for p in owner["parameters"] if p["name"] != "lasting_state"]
        params = {
            p["name"]: "{"
            + (
                "_" + p["name"] + "_id"
                if p.get("resolve") in {"entity", "participant"}
                else p["name"]
            )
            + "}"
            for p in owner["parameters"]
        }
        params["lasting_state"] = "state"
        exports = (
            ["_pending_action_id"]
            if name == "settle_srd_tsunami_exits"
            else ["_tsunami_swim_success"]
        )
        owner["effects"] = [_call(name + "_node", params, exports)]
    _normalize(changed)
    text = _emit(text, changed)
    properties = {
        k: {"type": "boolean" if type(v) is bool else "integer" if type(v) is int else "string"}
        for k, v in PROFILES["tsunami"].items()
        if k != "family"
    }
    row = "      tsunami: " + json.dumps(
        {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
        separators=(",", ":"),
    )
    if re.search(r"(?m)^      tsunami: .*", text):
        return re.sub(r"(?m)^      tsunami: .*", lambda _: row, text)
    variants = _load_runtime(text)["definition_row_schemas"]["definitions.spells"]["variants"]
    anchor = "      " + next(iter(variants)) + ":"
    return text.replace(anchor, row + "\n" + anchor, 1)


def bind_runtime_consumers(text):
    """Capture final source-caused movement and retain all final receiving callbacks."""
    tools = _load_runtime(text)["tools"]
    changed = json.loads(
        json.dumps(_gravity_receiver(tools))
        .replace("reverse_gravity", "tsunami")
        .replace("_gravity_", "_tsunami_")
        .replace("upward fall", "water carry")
        .replace("upward ceiling/anchored collision", "carried water/solid collision")
    )
    for owner in changed.values():
        bound = _tool_scene(owner["description"], owner["parameters"], owner["effects"])
        owner["parameters"] = bound["parameters"]
        owner["effects"] = bound["effects"]
    name = (
        "move_srd_scene_creature_node"
        if "move_srd_scene_creature_node" in tools
        else "move_srd_scene_creature"
    )
    owner = deepcopy(tools[name])
    if "check_srd_tsunami_swim" not in str(owner["effects"]):
        owner["effects"].insert(
            0,
            _call(
                "check_srd_tsunami_swim",
                {
                    "actor_state": "{_actor_state_id}",
                    "destination": "{destination}",
                    "mode": "{mode}",
                },
            ),
        )
    # The canonical scene movement node delegates real per-segment mutation; the old
    # origin is retained here across that actual call and read only after completion.
    if "observe_srd_tsunami_movement" not in str(owner["effects"]):
        owner["effects"].insert(
            0,
            _call(
                "srd_information_scene_entity", {"subject": "{_actor_state_id}"}, ["_info_position"]
            ),
        )
        owner["effects"].insert(1, _set("_tsunami_move_start", "_info_position"))
        owner["effects"].append(
            _call(
                "observe_srd_tsunami_movement",
                {"actor_state": "{_actor_state_id}", "start": "{_tsunami_move_start}"},
            )
        )
    changed[name] = owner
    # Ordinary swimming spends the same normal-Speed pool. The actual route cost
    # already includes Difficult Terrain; adding distance gives its printed2/3 cost.
    segment_name = (
        "move_srd_scene_creature_environment_segment"
        if "move_srd_scene_creature_environment_segment" in tools
        else name
    )
    segment = deepcopy(changed.get(segment_name, tools[segment_name]))
    if "qualify_srd_tsunami_swim_segment" not in str(segment["effects"]):

        def swimming(effects):
            result = []
            for effect in effects:
                for key in ("on_true", "on_false"):
                    if key in effect:
                        effect[key] = swimming(effect[key])
                if effect.get("call") in {
                    "spend_combat_movement",
                    "derive_srd_scene_movement_budget",
                }:
                    key = "movement_kind" if effect["call"] == "spend_combat_movement" else "mode"
                    previous = effect["params"][key]
                    previous = (
                        previous[1:-1] if re.fullmatch(r"\{[^{}]+\}", previous) else repr(previous)
                    )
                    result.extend(
                        [
                            _call(
                                "qualify_srd_tsunami_swim_segment",
                                {
                                    "actor_state": "{_actor_state_id}",
                                    "destination": "{destination}",
                                    "mode": "{mode}",
                                },
                                ["_tsunami_ordinary_swim"],
                            ),
                            _set("_tsunami_segment_budget_mode", previous),
                            _when(
                                "_tsunami_ordinary_swim == true",
                                [
                                    _set("_tsunami_segment_budget_mode", "'walk'"),
                                    _set("_route_cost", "_route_cost + _route_distance"),
                                ],
                            ),
                        ]
                    )
                    effect["params"][key] = "{_tsunami_segment_budget_mode}"
                result.append(effect)
            return result

        segment["effects"] = swimming(segment["effects"])
    changed[segment_name] = segment
    _normalize(changed)
    return _emit(text, changed)
