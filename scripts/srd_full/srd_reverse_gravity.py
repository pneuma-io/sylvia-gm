"""A saved gravity Cylinder owns real upward motion and queued downward falls."""

import json
import re
from copy import deepcopy
from pathlib import Path

if __package__:
    from .srd_courier_chronology import _emit
    from .srd_divination_spells import _normalize
    from .srd_illusion_appearance import HIDDEN_SOURCE
    from .srd_scene_relations import _array, _snapshot, _tool_scene
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
    from .srd_wall_ice_relocation import _receiver_guards, _source_semantics
else:
    from srd_courier_chronology import _emit
    from srd_divination_spells import _normalize
    from srd_illusion_appearance import HIDDEN_SOURCE
    from srd_scene_relations import _array, _snapshot, _tool_scene
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
    from srd_wall_ice_relocation import _receiver_guards, _source_semantics

SOURCE = {"reverse_gravity": (159, "Reverse Gravity", "Revivify")}
SOURCE_SHA256 = "33c2d0e1e36ce690a586192f3c86118461c4e17d7ff444586f3bb52ecf656bc3"
PROFILES = {
    "reverse_gravity": {
        "family": "reverse_gravity",
        "base_level": 7,
        "casting_time": "action",
        "range_kind": "distance",
        "range_feet": 100,
        "concentration": True,
        "duration_rounds": 10,
        "radius_feet": 50,
        "height_feet": 100,
    }
}
PUBLIC_TOOLS = {"record_srd_reverse_gravity_anchoring", "settle_srd_reverse_gravity_returns"}
CAST_CHOICES = [
    _array("gravity_origin", default=[0, 0, 0]),
    _optional("gravity_grab_actors", "array", [], items={"type": "string"}, max_items=64),
    _optional("gravity_grab_objects", "array", [], items={"type": "string"}, max_items=64),
]
PENDING = "state.pending_srd_action_stack == null or len(state.pending_srd_action_stack) == 0"


def validate_profiles():
    """Keep each fixed clause of the actual 2024 source selected."""
    root = Path(__file__).resolve().parents[2]
    corpus = json.loads((root / "rulesets/dnd-srd-5.2.1/content/corpus/spells.json").read_text())
    page = next(p for p in corpus["page_records"] if p["page"] == 159)
    assert page["text_sha256"] == SOURCE_SHA256
    text = " ".join(page["text"].split())
    for phrase in (
        "Level 7 Transmutation",
        "Range: 100 feet",
        "lodestone and iron filings",
        "Concentration, up to 1 minute",
        "50-foot-radius, 100-",
        "anchored to the ground",
        "Dexterity",
        "fixed object it can reach",
        "ceiling or an anchored object",
        "downward fall",
        "hovers there",
        "When the spell ends",
    ):
        if phrase not in text:
            raise ValueError("Missing Reverse Gravity source clause: " + phrase)
    return SOURCE_SHA256


def _source(identity="source_id"):
    return [
        _set("_gravity_source", None),
        _set("_gravity_sources", []),
        _when(
            "state.reverse_gravity_sources != null",
            [_set("_gravity_sources", "state.reverse_gravity_sources")],
        ),
        _loop(
            "_gravity_sources",
            "_gravity_candidate",
            [
                _when(
                    f"_gravity_candidate.id == {identity}",
                    [
                        _require(
                            "_gravity_source == null", "Gravity source identity is ambiguous."
                        ),
                        _set("_gravity_source", "_gravity_candidate"),
                    ],
                )
            ],
        ),
        _require("_gravity_source != null", "Select an actual retained gravity source."),
    ]


def _save_source():
    return [
        _set("_gravity_saved", []),
        _set("_gravity_save_rows", "state.reverse_gravity_sources"),
        _loop(
            "_gravity_save_rows",
            "_gravity_save_row",
            [
                {
                    "condition": "_gravity_save_row.id == _gravity_source.id",
                    "on_true": [_push("_gravity_saved", "_gravity_source")],
                    "on_false": [_push("_gravity_saved", "_gravity_save_row")],
                }
            ],
        ),
        _set("state.reverse_gravity_sources", "_gravity_saved"),
    ]


def _source_flags():
    """Retain independent active and deferred-return sources after exact retirement."""
    return [
        _set("_gravity_any_active", False),
        _set("_gravity_any_pending", False),
        _set("_gravity_flag_rows", "state.reverse_gravity_sources"),
        _loop(
            "_gravity_flag_rows",
            "_gravity_flag_row",
            [
                _when("_gravity_flag_row.active == true", [_set("_gravity_any_active", True)]),
                _when(
                    "_gravity_flag_row.return_pending == true",
                    [_set("_gravity_any_pending", True)],
                ),
            ],
        ),
        _set("state.has_reverse_gravity_sources", "_gravity_any_active"),
        _set("state.has_reverse_gravity_pending_returns", "_gravity_any_pending"),
    ]


def _membership(body, origin="_gravity_source.origin", plane="_gravity_source.plane"):
    """Exact box/Cylinder overlap uses actual horizontal extents and vertical interval."""
    return [
        _require(
            f"{body}.footprint.kind == 'box'",
            "Gravity currently requires actual box bodies; curved membership is pending.",
        ),
        _set(
            "_gravity_dx",
            f"max(0, abs({body}.position[0] - {origin}[0]) - {body}.footprint.half_extents[0])",
        ),
        _set(
            "_gravity_dy",
            f"max(0, abs({body}.position[1] - {origin}[1]) - {body}.footprint.half_extents[1])",
        ),
        _set(
            "_gravity_inside",
            f"{body}.metadata.plane == {plane} and "
            "_gravity_dx * _gravity_dx + _gravity_dy * _gravity_dy <= 2500 and "
            f"{body}.position[2] <= {origin}[2] + 100 and "
            f"{body}.position[2] + {body}.height >= {origin}[2]",
        ),
    ]


def _fall_fact_effects(body, fact):
    """Qualify an admitted vertical impact/landing fact against one actual Box face."""
    return [
        _require(
            f"{body}.footprint.kind == 'box' and {fact}.distance >= 0",
            "Gravity fall facts need an actual Box body and nonnegative distance.",
        ),
        _require(
            f"{fact}.plane == {body}.metadata.plane", "The fall fact belongs to another plane."
        ),
        *[
            _require(
                f"{fact}.origin[{i}] == {body}.position[{i}]",
                "The fall fact must describe this body's current origin.",
            )
            for i in range(3)
        ],
        _set("_gravity_fact_obstacle", None),
        _set("_gravity_fact_geometry", "state.spatial_scene.geometry"),
        _loop(
            "_gravity_fact_geometry",
            "_gravity_fact_candidate",
            [
                _when(
                    f"_gravity_fact_candidate.id == {fact}.obstacle_id",
                    [
                        _require(
                            "_gravity_fact_obstacle == null", "Obstacle identity is ambiguous."
                        ),
                        _set("_gravity_fact_obstacle", "_gravity_fact_candidate"),
                    ],
                ),
            ],
        ),
        _require(
            "_gravity_fact_obstacle != null and _gravity_fact_obstacle.active == true and "
            "_gravity_fact_obstacle.movement == 'blocking' and "
            "_gravity_fact_obstacle.volume.kind == 'box' and "
            f"_gravity_fact_obstacle.metadata.plane == {body}.metadata.plane",
            "Admit an actual active same-plane Box ceiling or supporting surface.",
        ),
        *[
            _require(
                f"{body}.position[{i}] - {body}.footprint.half_extents[{i}] >= "
                f"_gravity_fact_obstacle.volume.min[{i}] and "
                f"{body}.position[{i}] + {body}.footprint.half_extents[{i}] <= "
                f"_gravity_fact_obstacle.volume.max[{i}]",
                "The admitted face must cover the actual body's footprint.",
            )
            for i in range(2)
        ],
        _set("_gravity_fact_height", f"{body}.position[2] + {fact}.distance"),
        _when(
            f"{fact}.direction == 'down'",
            [
                _set("_gravity_fact_height", f"{body}.position[2] - {fact}.distance"),
                _require(
                    "_gravity_fact_obstacle.metadata.ground == true and "
                    "_gravity_fact_height == _gravity_fact_obstacle.volume.max[2]",
                    "The observed landing must be the actual supporting face.",
                ),
            ],
        ),
        _when(
            f"{fact}.direction == 'up'",
            [
                _require(
                    f"_gravity_fact_height + {body}.height == _gravity_fact_obstacle.volume.min[2]",
                    "The observed impact must be the actual ceiling face.",
                ),
            ],
        ),
        _set(
            "_gravity_fact_destination",
            [f"{body}.position[0]", f"{body}.position[1]", "_gravity_fact_height"],
        ),
    ]


def _anchoring_tool():
    return _public(
        _tool_scene(
            "Admit anchoring/grabbability fiction against actual creature or object scenery.",
            [
                _parameter("subject"),
                _parameter("anchored", "boolean"),
                _parameter("grabbable", "boolean"),
                _optional("fall_direction", "enum", "none", enum_values=["none", "up", "down"]),
                _optional("fall_distance", "number", 0, minimum=0),
                _optional("fall_obstacle_id", default=""),
            ],
            [
                _require(PENDING, "Finish the pending action before admitting anchor facts."),
                _require("combat.encounter_active != true", "Admit anchor facts outside combat."),
                *_scene_subject("{subject}", "_gravity_anchor_body"),
                _require(
                    "_gravity_anchor_body.metadata.participant_ref != null or "
                    "_gravity_anchor_body.metadata.is_object == true",
                    "Select an actual creature or object body.",
                ),
                _require(
                    "grabbable != true or (anchored == true and "
                    "_gravity_anchor_body.metadata.is_object == true)",
                    "A grab needs an actual fixed object.",
                ),
                *_snapshot(),
                _set(
                    "_gravity_anchor_patch",
                    {
                        "reverse_gravity_ground_anchor": "anchored",
                        "reverse_gravity_grabbable": "grabbable",
                    },
                ),
                _when(
                    "fall_direction != 'none'",
                    [
                        _set(
                            "_gravity_admitted_fall",
                            {
                                "direction": "fall_direction",
                                "distance": "fall_distance",
                                "obstacle_id": "fall_obstacle_id",
                                "origin": "_gravity_anchor_body.position",
                                "plane": "_gravity_anchor_body.metadata.plane",
                            },
                        ),
                        *_fall_fact_effects("_gravity_anchor_body", "_gravity_admitted_fall"),
                        _set(
                            "_gravity_fall_patch",
                            {"reverse_gravity_fall_fact": "_gravity_admitted_fall"},
                        ),
                        _set(
                            "_gravity_anchor_patch",
                            "merge(_gravity_anchor_patch, _gravity_fall_patch)",
                        ),
                    ],
                ),
                _set(
                    "_gravity_anchor_metadata",
                    "merge(_gravity_anchor_body.metadata, _gravity_anchor_patch)",
                ),
                {
                    "spatial_mutation": {
                        "scene": "state.spatial_scene",
                        "map_id": "{state.spatial_scene.map.id}",
                        "expected_revision": "{_info_snapshot.geometry_revision}",
                        "target": {"kind": "entity", "id": "{_gravity_anchor_body.id}"},
                        "patch": {"metadata": "{_gravity_anchor_metadata}"},
                    },
                    "store_as": "_gravity_anchor_mutation",
                },
            ],
        ),
        "information",
        "Gravity anchoring",
        [("Subject", "subject")],
    )


def _qualify_motion_tool():
    return _tool_scene(
        "Authenticate only this exact source's current unconsumed upward fall receipt.",
        [_parameter("subject"), _array("destination"), _parameter("plane")],
        [
            _set("_gravity_receipt", "state.reverse_gravity_pending_motion"),
            _require(
                "_gravity_receipt != null and (_gravity_receipt.kind == 'reverse_gravity_up' or "
                "_gravity_receipt.kind == 'reverse_gravity_down') "
                "and _gravity_receipt.consumed == false and "
                "_gravity_receipt.subject == subject and _gravity_receipt.plane == plane and "
                "_gravity_receipt.tick == state.lasting_tick",
                "Only the authentic paid gravity consequence can force upward movement.",
            ),
            _set("_gravity_lookup_source_id", "_gravity_receipt.source_id"),
            *_source("_gravity_lookup_source_id"),
            _require(
                "((_gravity_receipt.kind == 'reverse_gravity_up' and "
                "_gravity_source.active == true) or "
                "(_gravity_receipt.kind == 'reverse_gravity_down' and "
                "_gravity_source.active == false "
                "and _gravity_source.return_pending == true)) and "
                "_gravity_source.sequence == _gravity_receipt.sequence and "
                "_gravity_source.caster == _gravity_receipt.caster",
                "The actual source instance changed before its upward consequence.",
            ),
            *_scene_subject("{subject}", "_gravity_moving_body"),
            _require(
                "_gravity_moving_body.id == _gravity_receipt.body_id and "
                "_gravity_moving_body.height == _gravity_receipt.height and "
                "_gravity_moving_body.footprint.half_extents[0] == _gravity_receipt.extents[0] "
                "and _gravity_moving_body.footprint.half_extents[1] == _gravity_receipt.extents[1]",
                "The actual falling body changed before movement.",
            ),
            *[
                _require(
                    f"_gravity_moving_body.position[{i}] == _gravity_receipt.origin[{i}] and "
                    f"destination[{i}] == _gravity_receipt.destination[{i}]",
                    "Use the source-derived current origin and endpoint.",
                )
                for i in range(3)
            ],
            _require(
                "destination[0] == _gravity_receipt.origin[0] and "
                "destination[1] == _gravity_receipt.origin[1]",
                "Gravity changes only the actual body's vertical position.",
            ),
            {
                "condition": "_gravity_receipt.fall_fact != null",
                "on_true": [
                    *_fall_fact_effects("_gravity_moving_body", "_gravity_receipt.fall_fact"),
                    _require(
                        "destination[2] == _gravity_fact_height and "
                        "((_gravity_receipt.kind == 'reverse_gravity_up' and "
                        "_gravity_receipt.fall_fact.direction == 'up') or "
                        "(_gravity_receipt.kind == 'reverse_gravity_down' and "
                        "_gravity_receipt.fall_fact.direction == 'down'))",
                        "The motion must match its admitted impact or landing.",
                    ),
                ],
                "on_false": [
                    _require(
                        "_gravity_receipt.kind == 'reverse_gravity_up' and "
                        "destination[2] == _gravity_source.origin[2] + 100",
                        "An unobstructed body falls upward to the Cylinder top.",
                    )
                ],
            },
            _set("_gravity_motion_allowed", True),
        ],
    )


def _grab_tool():
    return _tool_scene(
        "Compute fixed-object reach before one optional actual Dexterity save.",
        [_entity("target_state"), _parameter("source_id"), _parameter("grab_object")],
        [
            *_source(),
            *_scene_subject("{_target_state_id}", "_gravity_grabber"),
            *_scene_subject("{grab_object}", "_gravity_fixed"),
            _require(
                "_gravity_fixed.metadata.is_object == true and "
                "_gravity_fixed.metadata.reverse_gravity_ground_anchor == true and "
                "_gravity_fixed.metadata.reverse_gravity_grabbable == true and "
                "_gravity_fixed.metadata.plane == _gravity_grabber.metadata.plane",
                "Choose an actual fixed grabbable object on this body's plane.",
            ),
            _require("_gravity_fixed.footprint.kind == 'box'", "Use a supported fixed box body."),
            _set(
                "_gravity_nearest_fixed",
                [
                    f"min(max(_gravity_grabber.position[{i}], _gravity_fixed.position[{i}] - "
                    f"_gravity_fixed.footprint.half_extents[{i}]), _gravity_fixed.position[{i}] + "
                    f"_gravity_fixed.footprint.half_extents[{i}])"
                    for i in range(2)
                ]
                + [
                    "min(max(_gravity_grabber.position[2], _gravity_fixed.position[2]), "
                    "_gravity_fixed.position[2] + _gravity_fixed.height)"
                ],
            ),
            _call(
                "measure_srd_information_distance",
                {"start": "{_gravity_grabber.position}", "end": "{_gravity_nearest_fixed}"},
                ["_scene_distance"],
            ),
            _set("_gravity_grab_reach", 5),
            _when(
                "target_state.melee_reach != null",
                [_set("_gravity_grab_reach", "target_state.melee_reach")],
            ),
            _require(
                "_scene_distance <= _gravity_grab_reach",
                "The chosen actual fixed object is outside this creature's reach.",
            ),
            _call(
                "resolve_srd_divination_save",
                {
                    "target_state": "{_target_state_id}",
                    "ability": "dexterity",
                    "dc": "{_gravity_source.save_dc}",
                },
                ["_divination_save_success", "_d20_test"],
            ),
            _set("_gravity_grabbed", "_divination_save_success"),
            _set("_gravity_grab_roll", "_d20_test"),
        ],
    )


def _subject_tool():
    return _tool_scene(
        "Resolve only an actual affected body and its chosen optional grab.",
        [_parameter("source_id"), _parameter("subject"), _optional("grab_object", default="")],
        [
            *_source(),
            *_scene_subject("{subject}", "_gravity_body"),
            _set(
                "_gravity_subject_row",
                {
                    "subject": "subject",
                    "body_id": "_gravity_body.id",
                    "origin": "_gravity_body.position",
                    "plane": "_gravity_body.metadata.plane",
                    "creature": None,
                    "phase": "'anchored'",
                },
            ),
            _when(
                "_gravity_body.metadata.participant_ref != null",
                [
                    _set(
                        "_gravity_creature_patch",
                        {"creature": "_gravity_body.metadata.participant_ref"},
                    ),
                    _set(
                        "_gravity_subject_row",
                        "merge(_gravity_subject_row, _gravity_creature_patch)",
                    ),
                ],
            ),
            _when(
                "_gravity_body.metadata.reverse_gravity_ground_anchor != true",
                [
                    _set("_gravity_grabbed", False),
                    _when(
                        "grab_object != ''",
                        [
                            _require(
                                "_gravity_body.metadata.participant_ref != null",
                                "Only creatures make the optional grab saving throw.",
                            ),
                            _call(
                                "resolve_srd_reverse_gravity_grab",
                                {
                                    "target_state": "{_gravity_body.metadata.participant_ref}",
                                    "source_id": "{source_id}",
                                    "grab_object": "{grab_object}",
                                },
                                ["_gravity_grabbed", "_gravity_grab_roll"],
                            ),
                            _set(
                                "_gravity_save_patch",
                                {
                                    "save_results": "_gravity_grab_roll.results",
                                    "save_total": "_gravity_grab_roll.total",
                                    "grabbed": "_gravity_grabbed",
                                },
                            ),
                            _set(
                                "_gravity_subject_row",
                                "merge(_gravity_subject_row, _gravity_save_patch)",
                            ),
                        ],
                    ),
                    _set("_gravity_phase", "'grabbed'"),
                    _when(
                        "_gravity_grabbed != true",
                        [
                            _set(
                                "_gravity_destination",
                                [
                                    "_gravity_body.position[0]",
                                    "_gravity_body.position[1]",
                                    "_gravity_source.origin[2] + 100",
                                ],
                            ),
                            _set("_gravity_impact_fact", None),
                            _when(
                                "_gravity_body.metadata.reverse_gravity_fall_fact != null and "
                                "_gravity_body.metadata.reverse_gravity_fall_fact.direction "
                                "== 'up'",
                                [
                                    _set(
                                        "_gravity_impact_fact",
                                        "_gravity_body.metadata.reverse_gravity_fall_fact",
                                    ),
                                    *_fall_fact_effects("_gravity_body", "_gravity_impact_fact"),
                                    _require(
                                        "_gravity_fact_height <= _gravity_source.origin[2] + 100",
                                        "The impact lies above the actual Cylinder.",
                                    ),
                                    _set("_gravity_destination", "_gravity_fact_destination"),
                                ],
                            ),
                            _set(
                                "state.reverse_gravity_pending_motion",
                                {
                                    "kind": "'reverse_gravity_up'",
                                    "source_id": "source_id",
                                    "sequence": "_gravity_source.sequence",
                                    "caster": "_gravity_source.caster",
                                    "subject": "subject",
                                    "body_id": "_gravity_body.id",
                                    "origin": "_gravity_body.position",
                                    "destination": "_gravity_destination",
                                    "plane": "_gravity_source.plane",
                                    "height": "_gravity_body.height",
                                    "extents": "_gravity_body.footprint.half_extents",
                                    "tick": "state.lasting_tick",
                                    "consumed": False,
                                    "fall_fact": "_gravity_impact_fact",
                                },
                            ),
                            _when(
                                "_gravity_impact_fact == null",
                                [
                                    _set("state.reverse_gravity_pending_motion.fall_fact", None),
                                ],
                            ),
                            _call(
                                "move_srd_reverse_gravity_subject",
                                {
                                    "subject": "{subject}",
                                    "destination": "{_gravity_destination}",
                                    "plane": "{_gravity_source.plane}",
                                },
                            ),
                            _when(
                                "_gravity_impact_fact != null and "
                                "_gravity_subject_row.creature != null",
                                [
                                    _call(
                                        "resolve_srd_fall",
                                        {
                                            "target_state": "{_gravity_subject_row.creature}",
                                            "distance": "{_gravity_impact_fact.distance}",
                                            "landing": "ground",
                                            "reaction": "none",
                                        },
                                        ["_fall_effective_damage"],
                                    ),
                                    _set(
                                        "_gravity_impact_patch",
                                        {
                                            "impact_distance": "_gravity_impact_fact.distance",
                                            "impact_obstacle": "_gravity_impact_fact.obstacle_id",
                                            "impact_damage": "_fall_effective_damage",
                                        },
                                    ),
                                    _set(
                                        "_gravity_subject_row",
                                        "merge(_gravity_subject_row, _gravity_impact_patch)",
                                    ),
                                ],
                            ),
                            _set("_gravity_phase", "'hovering'"),
                        ],
                    ),
                    _set("_gravity_subject_patch", {"phase": "_gravity_phase"}),
                    _set(
                        "_gravity_subject_row",
                        "merge(_gravity_subject_row, _gravity_subject_patch)",
                    ),
                ],
            ),
        ],
    )


def _cast_tool():
    return _tool_scene(
        "Install the paid actual Cylinder and process its authentic bodies exactly once.",
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
                "state.has_reverse_gravity_sources != true and "
                "state.has_reverse_gravity_pending_returns != true",
                "Concurrent or unsettled gravity areas need their actual "
                "overlap/return owner; pending.",
            ),
            _require(
                "len(gravity_grab_actors) == len(gravity_grab_objects)",
                "Each chosen grab names one actual creature and fixed object.",
            ),
            *_scene_subject("{_caster_state_id}", "_gravity_caster"),
            _require("_gravity_caster.metadata.plane != null", "Use the actual casting plane."),
            _call(
                "measure_srd_information_distance",
                {"start": "{_gravity_caster.position}", "end": "{gravity_origin}"},
                ["_scene_distance"],
            ),
            _require("_scene_distance <= 100", "The actual Cylinder point exceeds100 feet."),
            _set("_gravity_affected", []),
            _set("_gravity_actor_ids", []),
            _set("_gravity_bodies", "state.spatial_scene.entities"),
            _loop(
                "_gravity_bodies",
                "_gravity_member",
                [
                    _when(
                        "_gravity_member.active == true and "
                        "(_gravity_member.metadata.participant_ref "
                        "!= null or _gravity_member.metadata.is_object == true)",
                        [
                            *_membership(
                                "_gravity_member",
                                "gravity_origin",
                                "_gravity_caster.metadata.plane",
                            ),
                            _when(
                                "_gravity_inside == true",
                                [
                                    _require(
                                        "_gravity_member.metadata."
                                        "reverse_gravity_ground_anchor != null",
                                        "Admit every affected body's ground anchoring.",
                                    ),
                                    _require(
                                        "_gravity_member.metadata.held_by == null and "
                                        "_gravity_member.metadata.worn_by == null",
                                        "Held/worn body composition is pending.",
                                    ),
                                    _push("_gravity_affected", "_gravity_member.id"),
                                    _when(
                                        "_gravity_member.metadata.participant_ref != null",
                                        [
                                            _push(
                                                "_gravity_actor_ids",
                                                "_gravity_member.metadata.participant_ref",
                                            )
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    )
                ],
            ),
            _require(
                "len(_gravity_affected) <= 64", "The supported gravity area has at most64 bodies."
            ),
            _set("_gravity_grab_index", 0),
            _set("_gravity_grab_seen", []),
            _loop(
                "gravity_grab_actors",
                "_gravity_grab_actor",
                [
                    _require(
                        "_gravity_grab_actor in _gravity_actor_ids and "
                        "_gravity_grab_actor not in _gravity_grab_seen",
                        "Choose each actual affected creature's grab once.",
                    ),
                    _push("_gravity_grab_seen", "_gravity_grab_actor"),
                ],
            ),
            _when(
                "state.reverse_gravity_sources == null", [_set("state.reverse_gravity_sources", [])]
            ),
            _require(
                "len(state.reverse_gravity_sources) < 128", "Retire obsolete gravity history first."
            ),
            _set("_gravity_focus", ["_caster_state_id"]),
            _call(
                "start_srd_condition_concentration",
                {
                    "caster_state": "{_caster_state_id}",
                    "lasting_state": "state",
                    "targets": "{_gravity_focus}",
                    "spell": "reverse_gravity",
                    "duration_rounds": 10,
                    "save_modifier": "{concentration_modifier}",
                },
            ),
            _set("caster_state.concentration_effect_family", "'reverse_gravity'"),
            _call(
                "format_srd_information_projection",
                {"message": "gravity:{_caster_state_id}:{caster_state.concentration_sequence}"},
                ["_info_projection_message"],
            ),
            _set("_gravity_source_id", "_info_projection_message"),
            _set(
                "_gravity_source",
                {
                    "id": "_gravity_source_id",
                    "spell": "'reverse_gravity'",
                    "caster": "_caster_state_id",
                    "sequence": "caster_state.concentration_sequence",
                    "started_tick": "state.lasting_tick",
                    "expires_tick": "state.lasting_tick + 20",
                    "origin": "gravity_origin",
                    "plane": "_gravity_caster.metadata.plane",
                    "radius": 50,
                    "height": 100,
                    "save_dc": "save_dc",
                    "active": True,
                    "spell_level": "slot_level",
                    "cast_id": "spell_cast_id",
                    "subjects": [],
                    "source_caster_id": "_caster_state_id",
                    "dispel_ongoing": True,
                    "ongoing_duration": True,
                    "return_pending": False,
                },
            ),
            _set("_gravity_initial", "state.reverse_gravity_sources"),
            _push("_gravity_initial", "_gravity_source"),
            _set("state.reverse_gravity_sources", "_gravity_initial"),
            _set("state.has_reverse_gravity_sources", True),
            _set("_gravity_resolved_subjects", []),
            _loop(
                "_gravity_affected",
                "_gravity_affected_subject",
                [
                    *_scene_subject("{_gravity_affected_subject}", "_gravity_choosing_body"),
                    _set("_gravity_chosen_grab", "''"),
                    _set("_gravity_grab_index", 0),
                    _loop(
                        "gravity_grab_actors",
                        "_gravity_grab_actor",
                        [
                            _when(
                                "_gravity_grab_actor == "
                                "_gravity_choosing_body.metadata.participant_ref",
                                [
                                    _set(
                                        "_gravity_chosen_grab",
                                        "gravity_grab_objects[_gravity_grab_index]",
                                    )
                                ],
                            ),
                            _set("_gravity_grab_index", "_gravity_grab_index + 1"),
                        ],
                    ),
                    _call(
                        "resolve_srd_reverse_gravity_subject",
                        {
                            "source_id": "{_gravity_source_id}",
                            "subject": "{_gravity_affected_subject}",
                            "grab_object": "{_gravity_chosen_grab}",
                        },
                        ["_gravity_subject_row"],
                    ),
                    _push("_gravity_resolved_subjects", "_gravity_subject_row"),
                ],
            ),
            *_source("_gravity_source_id"),
            _require(
                "_gravity_source.active == true",
                "Source ending during upward receiving effects is pending; no partial success.",
            ),
            _set("_gravity_source_patch", {"subjects": "_gravity_resolved_subjects"}),
            _set("_gravity_source", "merge(_gravity_source, _gravity_source_patch)"),
            *_save_source(),
            _set(
                "_spell_summary_rows",
                [{"label": "'Gravity Cylinder'", "value": "_gravity_source_id"}],
            ),
            _set("_spell_detail_rows", []),
            _loop(
                "_gravity_resolved_subjects",
                "_gravity_card_body",
                [
                    *_scene_subject("{_gravity_card_body.subject}", "_gravity_card_current"),
                    _call(
                        "format_srd_information_projection",
                        {
                            "message": "{_gravity_card_body.phase}; actual position "
                            "{_gravity_card_current.position}"
                        },
                        ["_info_projection_message"],
                    ),
                    _when(
                        "_gravity_card_body.save_total != null",
                        [
                            _call(
                                "format_srd_information_projection",
                                {
                                    "message": "{_info_projection_message}; Dexterity save "
                                    "{_gravity_card_body.save_total} against {save_dc}; "
                                    "faces {_gravity_card_body.save_results}"
                                },
                                ["_info_projection_message"],
                            )
                        ],
                    ),
                    _push(
                        "_spell_detail_rows",
                        {
                            "label": "_gravity_card_body.subject",
                            "value": "_info_projection_message",
                        },
                    ),
                ],
            ),
        ],
    )


def _lifetime_tools():
    retire = _tool_scene(
        "End one exact source; actual return motion is deferred until an outer safe boundary.",
        [_parameter("source_id")],
        [
            *_source(),
            _when(
                "_gravity_source.active == true",
                [
                    _set("_gravity_return_subjects", []),
                    _set("_gravity_retire_rows", "_gravity_source.subjects"),
                    _loop(
                        "_gravity_retire_rows",
                        "_gravity_retire_row",
                        [
                            _when(
                                "_gravity_retire_row.phase == 'hovering'",
                                [_push("_gravity_return_subjects", "_gravity_retire_row")],
                            )
                        ],
                    ),
                    _set(
                        "_gravity_end_patch",
                        {
                            "active": False,
                            "ended_tick": "state.lasting_tick",
                            "return_pending": "len(_gravity_return_subjects) > 0",
                            "returns": "_gravity_return_subjects",
                        },
                    ),
                    _set("_gravity_source", "merge(_gravity_source, _gravity_end_patch)"),
                    *_save_source(),
                    *_source_flags(),
                ],
            ),
        ],
    )
    expire = _tool_scene(
        "Qualify actual concentration and minute boundaries without rewinding shared time.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            *_source(),
            _when(
                "_gravity_source.active == true",
                [
                    _when(
                        "state.lasting_tick >= _gravity_source.expires_tick or "
                        "caster_state.concentration_spell_id != 'reverse_gravity' or "
                        "caster_state.concentration_effect_family != 'reverse_gravity' or "
                        "caster_state.concentration_sequence != _gravity_source.sequence",
                        [
                            _call(
                                "retire_srd_reverse_gravity_source", {"source_id": "{source_id}"}
                            ),
                            _when(
                                "caster_state.concentration_spell_id == 'reverse_gravity' and "
                                "caster_state.concentration_sequence == _gravity_source.sequence",
                                [
                                    _call(
                                        "end_srd_concentration_node",
                                        {
                                            "source_state": "{_caster_state_id}",
                                            "target_state": "{_caster_state_id}",
                                        },
                                    )
                                ],
                            ),
                        ],
                    )
                ],
            ),
        ],
    )
    refresh = _tool_scene(
        "Refresh only when actual gravity sources exist.",
        [],
        [
            _when(
                "state.has_reverse_gravity_sources == true",
                [
                    _set("_gravity_refresh_rows", "state.reverse_gravity_sources"),
                    _loop(
                        "_gravity_refresh_rows",
                        "_gravity_refresh_row",
                        [
                            _when(
                                "_gravity_refresh_row.active == true",
                                [
                                    _call(
                                        "refresh_srd_reverse_gravity_source",
                                        {
                                            "caster_state": "{_gravity_refresh_row.caster}",
                                            "source_id": "{_gravity_refresh_row.id}",
                                        },
                                    )
                                ],
                            )
                        ],
                    ),
                    _set("_gravity_live", False),
                    _set("_gravity_after_refresh", "state.reverse_gravity_sources"),
                    _loop(
                        "_gravity_after_refresh",
                        "_gravity_after_row",
                        [_when("_gravity_after_row.active == true", [_set("_gravity_live", True)])],
                    ),
                    _set("state.has_reverse_gravity_sources", "_gravity_live"),
                ],
            )
        ],
    )
    returns = _public(
        _tool_scene(
            "Land admitted objects and open real living-body fall opportunities "
            "for the ended source.",
            [_parameter("source_id")],
            [
                _require(
                    PENDING, "Finish the actual pending action before gravity return settlement."
                ),
                *_source(),
                _require(
                    "_gravity_source.active == false and _gravity_source.return_pending == true",
                    "Select an actual ended source with pending returns.",
                ),
                _set("_gravity_fall_targets", []),
                _set("_gravity_unresolved_objects", []),
                _set("_gravity_return_rows", "_gravity_source.returns"),
                _loop(
                    "_gravity_return_rows",
                    "_gravity_return_body",
                    [
                        *_scene_subject(
                            "{_gravity_return_body.subject}", "_gravity_return_current"
                        ),
                        _require(
                            "_gravity_return_current.id == _gravity_return_body.body_id and "
                            "_gravity_return_current.metadata.plane == _gravity_return_body.plane",
                            "The return body or plane changed; derive its actual new consequence.",
                        ),
                        {
                            "condition": "_gravity_return_body.creature != null",
                            "on_true": [
                                _push("_gravity_fall_targets", "_gravity_return_body.creature")
                            ],
                            "on_false": [
                                _call(
                                    "land_srd_reverse_gravity_object",
                                    {
                                        "source_id": "{source_id}",
                                        "subject": "{_gravity_return_body.subject}",
                                    },
                                ),
                                _push(
                                    "_gravity_unresolved_objects", "_gravity_return_body.subject"
                                ),
                            ],
                        },
                    ],
                ),
                _set("_pending_action_id", None),
                _when(
                    "len(_gravity_fall_targets) > 0",
                    [
                        _call(
                            "begin_srd_scene_fall",
                            {
                                "target_states": "{_gravity_fall_targets}",
                                "fall_cause": "support_lost",
                            },
                            ["_pending_action_id"],
                        ),
                    ],
                ),
                _set(
                    "_gravity_return_patch",
                    {"return_pending": False, "fall_event_id": "_pending_action_id"},
                ),
                _set("_gravity_source", "merge(_gravity_source, _gravity_return_patch)"),
                _when(
                    "_pending_action_id == null",
                    [
                        _set("_gravity_null_event", {"fall_event_id": None}),
                        _set("_gravity_source", "merge(_gravity_source, _gravity_null_event)"),
                    ],
                ),
                *_save_source(),
                *_source_flags(),
            ],
        ),
        "information",
        "Gravity returns",
        [("Actual fall", "_pending_action_id")],
    )
    return {
        "retire_srd_reverse_gravity_source": retire,
        "refresh_srd_reverse_gravity_source": expire,
        "refresh_srd_reverse_gravity_sources": refresh,
        "settle_srd_reverse_gravity_returns": returns,
        "retire_srd_reverse_gravity_caster": _tool_scene(
            "Queue only the actual ending caster's exact current gravity instance.",
            [_entity("source_state")],
            [
                _when(
                    "state.has_reverse_gravity_sources == true and "
                    "source_state.concentration_spell_id == 'reverse_gravity' and "
                    "source_state.concentration_effect_family == 'reverse_gravity'",
                    [
                        _set("_gravity_ending_rows", "state.reverse_gravity_sources"),
                        _loop(
                            "_gravity_ending_rows",
                            "_gravity_ending_row",
                            [
                                _when(
                                    "_gravity_ending_row.active == true and "
                                    "_gravity_ending_row.caster == _source_state_id and "
                                    "_gravity_ending_row.sequence == "
                                    "source_state.concentration_sequence",
                                    [
                                        _call(
                                            "retire_srd_reverse_gravity_source",
                                            {"source_id": "{_gravity_ending_row.id}"},
                                        )
                                    ],
                                )
                            ],
                        ),
                    ],
                )
            ],
        ),
    }


def _object_landing_tool():
    """Land the exact ended source's object using admitted support facts; damage remains fiction."""
    return _tool_scene(
        "Settle one authentic object's normal downward landing, without invented damage.",
        [_parameter("source_id"), _parameter("subject")],
        [
            *_source(),
            _require(
                "_gravity_source.active == false and _gravity_source.return_pending == true",
                "Only this ended gravity source can settle its objects.",
            ),
            *_scene_subject("{subject}", "_gravity_object"),
            _require(
                "_gravity_object.metadata.participant_ref == null and "
                "_gravity_object.metadata.is_object == true",
                "Select an actual scene object, not creature HP.",
            ),
            _set("_gravity_object_return_matches", 0),
            _set("_gravity_object_returns", "_gravity_source.returns"),
            _loop(
                "_gravity_object_returns",
                "_gravity_object_return",
                [
                    _when(
                        "_gravity_object_return.subject == subject and "
                        "_gravity_object_return.body_id == _gravity_object.id and "
                        "_gravity_object_return.plane == _gravity_object.metadata.plane and "
                        "_gravity_object_return.creature == null",
                        [
                            _set(
                                "_gravity_object_return_matches",
                                "_gravity_object_return_matches + 1",
                            ),
                        ],
                    ),
                ],
            ),
            _require(
                "_gravity_object_return_matches == 1",
                "Only this exact source's retained object can fall on its end.",
            ),
            _set("_gravity_object_fact", "_gravity_object.metadata.reverse_gravity_fall_fact"),
            _require(
                "_gravity_object_fact != null and _gravity_object_fact.direction == 'down'",
                "Object downward falling requires its actual distance and supporting surface.",
            ),
            *_fall_fact_effects("_gravity_object", "_gravity_object_fact"),
            _set(
                "state.reverse_gravity_pending_motion",
                {
                    "kind": "'reverse_gravity_down'",
                    "source_id": "source_id",
                    "sequence": "_gravity_source.sequence",
                    "caster": "_gravity_source.caster",
                    "subject": "subject",
                    "body_id": "_gravity_object.id",
                    "origin": "_gravity_object.position",
                    "destination": "_gravity_fact_destination",
                    "plane": "_gravity_source.plane",
                    "height": "_gravity_object.height",
                    "extents": "_gravity_object.footprint.half_extents",
                    "tick": "state.lasting_tick",
                    "consumed": False,
                    "fall_fact": "_gravity_object_fact",
                },
            ),
            _call(
                "move_srd_reverse_gravity_subject",
                {
                    "subject": "{subject}",
                    "destination": "{_gravity_fact_destination}",
                    "plane": "{_gravity_source.plane}",
                },
            ),
        ],
    )


def _receiver(tools):
    """Specialize the final sole position mutation, retaining authentic ward and receiving hooks."""
    source_name = "move_srd_travel_subject_node"
    destination_name = "move_srd_reverse_gravity_subject_node"
    original = tools.get(source_name, tools["move_srd_travel_subject"])
    node = deepcopy(original)
    node["effects"] = _source_semantics(node["effects"])
    fixed = {
        "teleport": False,
        "levitate_source_id": "",
        "telekinesis_source_id": "",
        "sphere_source_id": "",
    }

    def specialize(tool):
        removed = [p for p in tool["parameters"] if p["name"] in fixed]
        tool["parameters"] = [p for p in tool["parameters"] if p["name"] not in fixed]
        aliases = {p["name"]: "_gravity_receiver_" + p["name"] for p in removed}

        def constants(value):
            if isinstance(value, str):
                for original, local in aliases.items():
                    value = re.sub(r"\b" + re.escape(original) + r"\b", local, value)
                return value
            if isinstance(value, list):
                return [constants(child) for child in value]
            if isinstance(value, dict):
                return {
                    key: child if key in {"error", "description"} else constants(child)
                    for key, child in value.items()
                }
            return value

        tool["effects"] = constants(tool["effects"])
        tool["effects"][:0] = [_set(aliases[p["name"]], fixed[p["name"]]) for p in removed]
        qualify = [
            _call(
                "qualify_srd_reverse_gravity_motion",
                {"subject": "{subject}", "destination": "{destination}", "plane": "{plane}"},
                ["_gravity_motion_allowed"],
            ),
            _require("_gravity_motion_allowed == true", "Use the actual gravity receipt."),
        ]
        tool["effects"][:0] = deepcopy(qualify)
        return qualify

    qualify = specialize(node)
    mutations = [i for i, e in enumerate(node["effects"]) if "spatial_mutation" in e]
    if len(mutations) != 1:
        raise ValueError("Gravity must capture the final sole position mutation.")
    guards = json.loads(
        json.dumps(_receiver_guards(tools, original))
        .replace("paid Ice consequence", "paid gravity consequence")
        .replace("Ice push", "upward fall")
    )
    node["effects"][mutations[0] : mutations[0]] = [
        *guards,
        *deepcopy(qualify),
        {
            "spatial_query": {
                "scene": "state.spatial_scene",
                "operation": "sweep",
                "query": {
                    "map_id": "{state.spatial_scene.map.id}",
                    "moving_entity_id": "{_travel_mover.id}",
                    "destination": "{destination}",
                    "include_entities": True,
                    "metadata_filter": {
                        "key": "plane",
                        "value": "{plane}",
                        "missing_matches": False,
                    },
                },
            },
            "store_as": "_gravity_up_sweep",
        },
        _require(
            "_gravity_up_sweep.supported == true and _gravity_up_sweep.clear == true",
            "The upward impact receiver requires an admitted contact/landing fact "
            "and clear body path.",
        ),
        _set("state.reverse_gravity_pending_motion.consumed", True),
    ]
    if source_name not in tools:
        return {"move_srd_reverse_gravity_subject": node}
    wrapper = deepcopy(tools["move_srd_travel_subject"])
    wrapper["effects"] = _source_semantics(wrapper["effects"])
    specialize(wrapper)

    def substitute(value):
        if isinstance(value, dict):
            if value.get("call") == source_name:
                value["call"] = destination_name
                for key in fixed:
                    value["params"].pop(key, None)
            for child in value.values():
                substitute(child)
        elif isinstance(value, list):
            for child in value:
                substitute(child)

    substitute(wrapper["effects"])
    return {"move_srd_reverse_gravity_subject": wrapper, destination_name: node}


def _cast_binding(tools, changed):
    for name in ("cast_spell", "cast_spell_node", "cast_spell_outside_combat"):
        if name not in tools:
            continue
        cast = deepcopy(tools[name])
        present = {p["name"] for p in cast["parameters"]}
        cast["parameters"] += [deepcopy(p) for p in CAST_CHOICES if p["name"] not in present]
        branch = _when(
            "_spell_definition.family == 'reverse_gravity'",
            [
                _call(
                    "cast_srd_reverse_gravity",
                    {
                        "caster_state": "{_caster_state_id}",
                        "slot_level": "{_slot_level}",
                        "save_dc": "{_casting_profile.save_dc}",
                        "concentration_modifier": "{_casting_profile.concentration_save_modifier}",
                        **{p["name"]: "{" + p["name"] + "}" for p in CAST_CHOICES},
                    },
                    ["_spell_summary_rows", "_spell_detail_rows"],
                )
            ],
        )

        def replace(effects, branch=branch):
            found = False
            for i, e in enumerate(effects):
                if "_spell_definition.family == 'reverse_gravity'" in e.get("condition", ""):
                    fresh = deepcopy(branch)
                    fresh["condition"] = e["condition"]
                    effects[i] = fresh
                    found = True
                else:
                    for key in ("on_true", "on_false"):
                        if key in e:
                            found = replace(e[key]) or found
            return found

        def insert(effects, branch=branch):
            indices = [
                i
                for i, e in enumerate(effects)
                if "_spell_definition.family ==" in e.get("condition", "")
            ]
            if indices:
                effects.insert(indices[-1] + 1, deepcopy(branch))
                return True
            return any(insert(e[k]) for e in effects for k in ("on_true", "on_false") if k in e)

        if not replace(cast["effects"]) and not insert(cast["effects"]):
            raise ValueError("Gravity needs the genuine paid spell dispatch.")
        changed[name] = cast


def extend_runtime(text):
    """Emit source leaves; final receiver capture belongs after actual movement callbacks."""
    tools = _load_runtime(text)["tools"]
    changed = {
        "record_srd_reverse_gravity_anchoring": _anchoring_tool(),
        "qualify_srd_reverse_gravity_motion": _qualify_motion_tool(),
        "resolve_srd_reverse_gravity_grab": _grab_tool(),
        "resolve_srd_reverse_gravity_subject": _subject_tool(),
        "cast_srd_reverse_gravity": _cast_tool(),
        "land_srd_reverse_gravity_object": _object_landing_tool(),
        **_lifetime_tools(),
    }
    _cast_binding(tools, changed)
    clock = deepcopy(tools["advance_srd_lasting_clock"])
    if "refresh_srd_reverse_gravity_sources" not in str(clock["effects"]):
        clock["effects"].append(_call("refresh_srd_reverse_gravity_sources", {}))
    changed["advance_srd_lasting_clock"] = clock
    ending = deepcopy(tools["end_srd_concentration_node"])
    if "retire_srd_reverse_gravity_caster" not in str(ending["effects"]):
        ending["effects"].insert(
            0, _call("retire_srd_reverse_gravity_caster", {"source_state": "{_source_state_id}"})
        )
    changed["end_srd_concentration_node"] = ending
    for name in PUBLIC_TOOLS:
        tool = changed[name]
        node = deepcopy(tool)
        node["metadata"] = {
            "category": "internal",
            "visible_to_player": False,
            "costs_action": False,
        }
        changed[name + "_node"] = node
        tool["parameters"] = [p for p in tool["parameters"] if p["name"] != "lasting_state"]
        params = {p["name"]: "{" + p["name"] + "}" for p in tool["parameters"]}
        params["lasting_state"] = "state"
        tool["effects"] = [
            _call(name + "_node", params, ["_pending_action_id"] if "returns" in name else [])
        ]
    _normalize(changed)
    text = _emit(text, changed)
    properties = {
        k: {"type": "boolean" if type(v) is bool else "integer" if type(v) is int else "string"}
        for k, v in PROFILES["reverse_gravity"].items()
        if k != "family"
    }
    row = "      reverse_gravity: " + json.dumps(
        {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
        separators=(",", ":"),
    )
    pattern = r"(?m)^      reverse_gravity: .*"
    if re.search(pattern, text):
        return re.sub(pattern, lambda _: row, text)
    variants = _load_runtime(text)["definition_row_schemas"]["definitions.spells"]["variants"]
    anchor = "      " + next(iter(variants)) + ":"
    assert text.count(anchor) == 1
    return text.replace(anchor, row + "\n" + anchor, 1)


def bind_runtime_consumers(text):
    tools = _load_runtime(text)["tools"]
    changed = _receiver(tools)
    for owner in changed.values():
        bound = _tool_scene(owner["description"], owner["parameters"], owner["effects"])
        owner["parameters"] = bound["parameters"]
        owner["effects"] = bound["effects"]
    _normalize(changed)
    return _emit(text, changed)
