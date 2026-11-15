"""Gust's real Line, failed-save push, directional cost and ordinary flames."""

import json
import re
from copy import deepcopy
from pathlib import Path

if __package__:
    from .srd_courier_chronology import _emit
    from .srd_divination_spells import _normalize
    from .srd_illusion_appearance import HIDDEN_SOURCE
    from .srd_scene_relations import _array, _snapshot, _tool_scene
    from .srd_spell_environment import _wind_fog_mode
    from .srd_spell_object_content import _admission_guard, _held_object_geometry, _metadata_patch
    from .srd_spell_object_targets import _lookup_object
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
    from srd_spell_environment import _wind_fog_mode
    from srd_spell_object_content import _admission_guard, _held_object_geometry, _metadata_patch
    from srd_spell_object_targets import _lookup_object
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


SOURCE = {"gust_of_wind": (138, "Gust of Wind", "Hallow")}
PROFILES = {
    "gust_of_wind": {
        "family": "gust_of_wind",
        "base_level": 2,
        "casting_time": "action",
        "range_kind": "self",
        "range_feet": 0,
        "concentration": True,
        "duration_rounds": 10,
        "line_length_feet": 60,
        "line_width_feet": 10,
        "push_feet": 15,
        "save_ability": "strength",
    }
}
PUBLIC_TOOLS = {"change_srd_gust_direction", "record_srd_wind_flame_protection"}
CAST_CHOICES = [
    _array("gust_direction", default=[1, 0, 0]),
    _optional("gust_include_origin", "boolean", False),
    # The Line glossary supplies no vertical extent; the scene admits this fictional geometry.
    _optional("gust_height", "number", 10, minimum=0.001),
]
PENDING = "state.pending_srd_action_stack == null or len(state.pending_srd_action_stack) == 0"


def validate_profiles():
    """Check the full pinned spell before admitting any family declaration."""
    p = Path(__file__).resolve().parents[2] / "rulesets/dnd-srd-5.2.1/content/corpus/spells.json"
    corpus = json.loads(p.read_text(encoding="utf-8"))
    page = next(row for row in corpus["page_records"] if row["page"] == 138)
    text = " ".join(page["text"].split("Gust of Wind\n", 1)[1].split("Hallow\n", 1)[0].split())
    for clause in (
        "Level 2 Evocation (Druid, Ranger, Sorcerer, Wizard)",
        "Casting Time: Action",
        "Range: Self",
        "Components: V, S, M (a legume seed)",
        "Duration: Concentration, up to 1 minute",
        "60 feet long and 10 feet wide",
        "Strength saving throw",
        "pushed 15 feet",
        "ends its turn in the Line",
        "spend 2 feet",
        "moving closer to you",
        "gas or vapor",
        "unprotected flames",
        "protected flames",
        "50 percent",
        "Bonus Action on your later turns",
    ):
        if clause not in text:
            raise ValueError(f"Pinned Gust of Wind clause missing: {clause}")
    return page["text_sha256"]


def _source(identity="source_id"):
    return [
        _require("state.gust_sources != null", "Choose an authentic Gust source."),
        _set("_gust_source", None),
        _set("_gust_rows", "state.gust_sources"),
        _loop(
            "_gust_rows",
            "_gust_row",
            [
                _when(f"_gust_row.id == {identity}", [_set("_gust_source", "_gust_row")]),
            ],
        ),
        _require(
            "_gust_source != null and _gust_source.spell == 'gust_of_wind'",
            "Choose the exact retained Gust source.",
        ),
    ]


def _save():
    return [
        _set("_gust_saved", []),
        _set("_gust_prior", "state.gust_sources"),
        _loop(
            "_gust_prior",
            "_gust_previous",
            [
                {
                    "condition": "_gust_previous.id == _gust_source.id",
                    "on_true": [_push("_gust_saved", "_gust_source")],
                    "on_false": [_push("_gust_saved", "_gust_previous")],
                }
            ],
        ),
        _set("state.gust_sources", "_gust_saved"),
    ]


def _direction(direction="direction"):
    """The first playable geometry accepts honest horizontal cardinal Lines only."""
    return [
        _require(
            f"{direction}[2] == 0 and ((abs({direction}[0]) == 1 and "
            f"{direction}[1] == 0) or (abs({direction}[1]) == 1 and "
            f"{direction}[0] == 0))",
            "This Line supports horizontal cardinal directions; "
            "diagonal/vertical geometry is pending.",
        ),
    ]


def _geometry_tools():
    active = _tool_scene(
        "Authenticate current Gust concentration and derive the Line from the caster's real body.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            *_source(),
            _set(
                "_gust_active",
                "_gust_source.active == true and state.lasting_tick < "
                "_gust_source.expires_tick and caster_state.is_alive == true and "
                "caster_state.concentration_spell_id == 'gust_of_wind' and "
                "caster_state.concentration_effect_family == 'gust_of_wind' and "
                "caster_state.concentration_sequence == _gust_source.sequence and "
                "caster_state.gust_source_id == source_id and _gust_source.caster "
                "== _caster_state_id",
            ),
            _when(
                "_gust_active == true",
                [
                    *_scene_subject("{_caster_state_id}", "_gust_caster_body"),
                    _set("_gust_origin", "_gust_caster_body.position"),
                    _set("_gust_plane", "_gust_caster_body.metadata.plane"),
                    _require("_gust_plane != null", "The real Gust caster needs its actual plane."),
                    _set("_gust_direction", "_gust_source.direction"),
                    *_direction("_gust_direction"),
                    _set(
                        "_gust_min",
                        [
                            "_gust_origin[0] + min(0, _gust_direction[0] * 60) - "
                            "abs(_gust_direction[1]) * 5",
                            "_gust_origin[1] + min(0, _gust_direction[1] * 60) - "
                            "abs(_gust_direction[0]) * 5",
                            "_gust_origin[2]",
                        ],
                    ),
                    _set(
                        "_gust_max",
                        [
                            "_gust_origin[0] + max(0, _gust_direction[0] * 60) + "
                            "abs(_gust_direction[1]) * 5",
                            "_gust_origin[1] + max(0, _gust_direction[1] * 60) + "
                            "abs(_gust_direction[0]) * 5",
                            "_gust_origin[2] + _gust_source.height",
                        ],
                    ),
                    _set("_gust_volume", {"kind": "'box'", "min": "_gust_min", "max": "_gust_max"}),
                ],
            ),
        ],
    )
    body = _tool_scene(
        "Qualify the actual body's plane, complete Line containment and real Total Cover.",
        [_parameter("source_id"), _parameter("subject")],
        [
            *_source(),
            _call(
                "qualify_srd_gust_source",
                {"caster_state": "{_gust_source.caster}", "source_id": "{source_id}"},
                [
                    "_gust_active",
                    "_gust_origin",
                    "_gust_plane",
                    "_gust_min",
                    "_gust_max",
                    "_gust_volume",
                    "_gust_direction",
                ],
            ),
            _set("_gust_inside", False),
            _when(
                "_gust_active == true",
                [
                    *_scene_subject("{subject}", "_gust_subject_body"),
                    _when(
                        "_gust_subject_body.metadata.plane == _gust_plane and (subject != "
                        "_gust_source.caster or _gust_source.include_origin == true)",
                        [
                            _require(
                                "_gust_subject_body.footprint.kind == 'box'",
                                "Curved Line/body intersections need their actual geometry owner.",
                            ),
                            _set(
                                "_gust_body_min",
                                [
                                    "_gust_subject_body.position[0] - "
                                    "_gust_subject_body.footprint.half_extents[0]",
                                    "_gust_subject_body.position[1] - "
                                    "_gust_subject_body.footprint.half_extents[1]",
                                    "_gust_subject_body.position[2]",
                                ],
                            ),
                            _set(
                                "_gust_body_max",
                                [
                                    "_gust_subject_body.position[0] + "
                                    "_gust_subject_body.footprint.half_extents[0]",
                                    "_gust_subject_body.position[1] + "
                                    "_gust_subject_body.footprint.half_extents[1]",
                                    "_gust_subject_body.position[2] + _gust_subject_body.height",
                                ],
                            ),
                            _set("_gust_overlap", True),
                            _set("_gust_whole", True),
                            *[
                                _when(
                                    f"_gust_body_max[{i}] <= _gust_min[{i}] or "
                                    f"_gust_body_min[{i}] >= _gust_max[{i}]",
                                    [_set("_gust_overlap", False)],
                                )
                                for i in range(3)
                            ],
                            *[
                                _when(
                                    f"_gust_body_min[{i}] < _gust_min[{i}] or "
                                    f"_gust_body_max[{i}] > _gust_max[{i}]",
                                    [_set("_gust_whole", False)],
                                )
                                for i in range(3)
                            ],
                            _require(
                                "_gust_overlap != true or _gust_whole == true",
                                "A partially intersecting Line/body needs exact partial-area "
                                "qualification.",
                            ),
                            _when(
                                "_gust_overlap == true",
                                [
                                    *_snapshot(),
                                    {
                                        "spatial_query": {
                                            "scene": "state.spatial_scene",
                                            "operation": "exposure",
                                            "query": {
                                                "map_id": "{state.spatial_scene.map.id}",
                                                "observer": {"point": "{_gust_origin}"},
                                                "target": {"entity_id": "{_gust_subject_body.id}"},
                                                "samples": {"horizontal": 3, "vertical": 3},
                                                "include": ["geometry"],
                                                "metadata_filter": {
                                                    "key": "plane",
                                                    "value": "{_gust_plane}",
                                                    "missing_matches": False,
                                                },
                                            },
                                        },
                                        "store_as": "_gust_exposure",
                                    },
                                    _set("_gust_inside", "_gust_exposure.visible_samples > 0"),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    return {"qualify_srd_gust_source": active, "qualify_srd_gust_body": body}


def _push_tools():
    receipt = _tool_scene(
        "Authenticate only the unconsumed actual failed-save source push receipt.",
        [_parameter("subject"), _array("destination"), _parameter("plane")],
        [
            _set("_gust_receipt", "state.gust_pending_push"),
            _require(
                "_gust_receipt != null and _gust_receipt.kind == 'gust_of_wind_push' and "
                "_gust_receipt.consumed == false and _gust_receipt.subject == subject and "
                "_gust_receipt.plane == plane and _gust_receipt.tick == state.lasting_tick",
                "Only the exact current unused Gust failed-save receipt can force a push.",
            ),
            *_source("_gust_receipt.source_id"),
            _require(
                "_gust_source.sequence == _gust_receipt.sequence and _gust_source.caster == "
                "_gust_receipt.caster and _gust_source.started_tick == _gust_receipt.started_tick",
                "The saved push must belong to the same actual source instance.",
            ),
            _call(
                "qualify_srd_gust_body",
                {"source_id": "{_gust_receipt.source_id}", "subject": "{subject}"},
                [
                    "_gust_active",
                    "_gust_inside",
                    "_gust_subject_body",
                    "_gust_direction",
                    "_gust_plane",
                ],
            ),
            _require(
                "_gust_active == true and _gust_inside == true and "
                "_gust_subject_body.id == _gust_receipt.body_id and "
                "_gust_subject_body.height == _gust_receipt.height and "
                "_gust_subject_body.footprint.half_extents[0] == _gust_receipt.half_extents[0] and "
                "_gust_subject_body.footprint.half_extents[1] == _gust_receipt.half_extents[1]",
                "The actual saved failed target/body must still be in this source's Line.",
            ),
            *[
                _require(
                    f"_gust_subject_body.position[{i}] == _gust_receipt.origin[{i}] and "
                    f"destination[{i}] == _gust_receipt.destination[{i}] and "
                    f"destination[{i}] == _gust_receipt.origin[{i}] + _gust_direction[{i}] * 15",
                    "Use the exact body origin and source-derived fifteen-foot endpoint.",
                )
                for i in range(3)
            ],
            _set("_gust_push_allowed", True),
        ],
    )
    save = _tool_scene(
        "Resolve one source-owned initial or real end-turn Strength save and true forced push.",
        [
            _entity("target_state"),
            _parameter("source_id"),
            _parameter("event", "enum", enum_values=["initial", "turn_end"]),
        ],
        [
            *_source(),
            _call(
                "qualify_srd_gust_body",
                {"source_id": "{source_id}", "subject": "{_target_state_id}"},
                [
                    "_gust_active",
                    "_gust_inside",
                    "_gust_subject_body",
                    "_gust_direction",
                    "_gust_plane",
                ],
            ),
            _set("_gust_pushed", False),
            _set("_gust_save_roll", None),
            _set("_gust_save_outcome", None),
            _set("_gust_save_destination", []),
            _when(
                "_gust_inside == true and target_state.is_alive == true",
                [
                    _set("_gust_boundary_turn", 0),
                    _when(
                        "target_state.turns_started != null",
                        [_set("_gust_boundary_turn", "target_state.turns_started")],
                    ),
                    _when(
                        "event == 'turn_end'",
                        [
                            _require(
                                "combat.turn_active == true and combat.active_actor == "
                                "_target_state_id",
                                "Only the actual currently ending creature owns this repeat save.",
                            ),
                        ],
                    ),
                    _set("_gust_seen", False),
                    _set("_gust_events", "_gust_source.events"),
                    _loop(
                        "_gust_events",
                        "_gust_old_event",
                        [
                            _when(
                                "_gust_old_event.target == _target_state_id and "
                                "_gust_old_event.event == event and "
                                "(event == 'initial' or _gust_old_event.turn == "
                                "_gust_boundary_turn)",
                                [_set("_gust_seen", True)],
                            ),
                        ],
                    ),
                    _when(
                        "_gust_seen != true",
                        [
                            _require(
                                "target_state.saving_throw_modifiers.strength != null",
                                "Use the creature's actual retained Strength saving-throw "
                                "modifier.",
                            ),
                            _call(
                                "d20_test",
                                {
                                    "actor_state": "{_target_state_id}",
                                    "ability": "strength",
                                    "kind": "saving_throw",
                                    "ability_modifier": (
                                        "{target_state.saving_throw_modifiers.strength}"
                                    ),
                                    "proficiency_bonus": 0,
                                    "proficiency_factor": "none",
                                    "circumstantial_modifier": 0,
                                    "advantage": False,
                                    "disadvantage": False,
                                    "target_number": "{_gust_source.save_dc}",
                                },
                                ["_d20_test", "_outcome"],
                            ),
                            _set("_gust_save_roll", "_d20_test"),
                            _set("_gust_save_outcome", "_outcome"),
                            _set("_gust_save_origin", "_gust_subject_body.position"),
                            _set("_gust_save_destination", "_gust_save_origin"),
                            _when(
                                "_outcome == 'failure'",
                                [
                                    _set(
                                        "_gust_save_destination",
                                        [
                                            f"_gust_save_origin[{i}] + _gust_direction[{i}] * 15"
                                            for i in range(3)
                                        ],
                                    ),
                                    _set(
                                        "state.gust_pending_push",
                                        {
                                            "kind": "'gust_of_wind_push'",
                                            "source_id": "source_id",
                                            "sequence": "_gust_source.sequence",
                                            "caster": "_gust_source.caster",
                                            "started_tick": "_gust_source.started_tick",
                                            "tick": "state.lasting_tick",
                                            "subject": "_target_state_id",
                                            "body_id": "_gust_subject_body.id",
                                            "origin": "_gust_save_origin",
                                            "destination": "_gust_save_destination",
                                            "height": "_gust_subject_body.height",
                                            "half_extents": (
                                                "_gust_subject_body.footprint.half_extents"
                                            ),
                                            "plane": "_gust_plane",
                                            "consumed": False,
                                        },
                                    ),
                                    _call(
                                        "push_srd_gust_subject",
                                        {
                                            "subject": "{_target_state_id}",
                                            "destination": "{_gust_save_destination}",
                                            "plane": "{_gust_plane}",
                                        },
                                    ),
                                    _set("state.gust_pending_push", None),
                                    _set("_gust_pushed", True),
                                ],
                            ),
                            *_source(),
                            _set("_gust_events", "_gust_source.events"),
                            _push(
                                "_gust_events",
                                {
                                    "target": "_target_state_id",
                                    "event": "event",
                                    "turn": "_gust_boundary_turn",
                                    "tick": "state.lasting_tick",
                                    "roll": "_gust_save_roll",
                                    "outcome": "_gust_save_outcome",
                                    "origin": "_gust_save_origin",
                                    "destination": "_gust_save_destination",
                                    "pushed": "_gust_pushed",
                                },
                            ),
                            _set("_gust_patch", {"events": "_gust_events"}),
                            _set("_gust_source", "merge(_gust_source, _gust_patch)"),
                            *_save(),
                            _set(
                                "target_state.last_gust_save",
                                {
                                    "source_id": "source_id",
                                    "event": "event",
                                    "roll": "_gust_save_roll",
                                    "outcome": "_gust_save_outcome",
                                    "origin": "_gust_save_origin",
                                    "destination": "_gust_save_destination",
                                    "pushed": "_gust_pushed",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    ending = _tool_scene(
        "Use every active source only at the actual affected creature's ending turn.",
        [_entity("target_state")],
        [
            _set("_gust_end_details", []),
            _when(
                "state.has_gust_sources == true",
                [
                    _set("_gust_end_sources", "state.gust_sources"),
                    _loop(
                        "_gust_end_sources",
                        "_gust_end_source",
                        [
                            _when(
                                "_gust_end_source.active == true",
                                [
                                    _call(
                                        "resolve_srd_gust_save",
                                        {
                                            "target_state": "{_target_state_id}",
                                            "source_id": "{_gust_end_source.id}",
                                            "event": "turn_end",
                                        },
                                        [
                                            "_gust_save_roll",
                                            "_gust_save_outcome",
                                            "_gust_pushed",
                                            "_gust_save_destination",
                                        ],
                                    ),
                                    _when(
                                        "_gust_save_roll != null",
                                        [
                                            _call(
                                                "format_srd_information_projection",
                                                {
                                                    "message": (
                                                        "Gust {_gust_save_outcome}; "
                                                        "save {_gust_save_roll.total}; "
                                                        "pushed {_gust_pushed}; "
                                                        "actual endpoint {_gust_save_destination}"
                                                    )
                                                },
                                                ["_info_projection_message"],
                                            ),
                                            _push(
                                                "_gust_end_details",
                                                {
                                                    "label": "_target_state_id",
                                                    "value": "_info_projection_message",
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
    return {
        "qualify_srd_gust_push": receipt,
        "resolve_srd_gust_save": save,
        "resolve_srd_gust_turn_end": ending,
    }


def _flame_tools():
    admit = _public(
        _tool_scene(
            "Record actual mundane wind protection on an already admitted ordinary flame, once.",
            [_parameter("object_id"), _parameter("protected", "boolean")],
            [
                *_admission_guard(),
                _require(PENDING, "Finish the pending action before admitting flame facts."),
                _require(
                    "_object_scene.metadata.srd_ordinary_flame.kind == 'ordinary_flame' and "
                    "_object_scene.metadata.srd_ordinary_flame.wind_protected == null",
                    "Record wind protection only once on the actual ordinary flame.",
                ),
                _set("_gust_flame_fact", {"wind_protected": "protected"}),
                _set(
                    "_gust_flame",
                    "merge(_object_scene.metadata.srd_ordinary_flame, _gust_flame_fact)",
                ),
                *_metadata_patch({"srd_ordinary_flame": "_gust_flame"}),
            ],
        ),
        "exploration",
        "Flame wind protection",
        [("Protected", "protected")],
    )
    flame = _tool_scene(
        "Consume the actual saved flame in this source's Line; protected "
        "flames roll one real 50% chance.",
        [_parameter("source_id"), _parameter("object_id")],
        [
            *_source(),
            *_lookup_object(),
            *_held_object_geometry("{_gust_source.caster}"),
            _call(
                "qualify_srd_gust_body",
                {"source_id": "{source_id}", "subject": "{object_id}"},
                ["_gust_inside"],
            ),
            _set("_gust_flame_extinguished", False),
            _when(
                "_gust_inside != true and "
                "_object_scene.metadata.srd_ordinary_flame.gust_dancing_sources != null and "
                "source_id in _object_scene.metadata.srd_ordinary_flame.gust_dancing_sources",
                [
                    _call(
                        "retire_srd_gust_flame",
                        {"source_id": "{source_id}", "object_id": "{object_id}"},
                    ),
                ],
            ),
            _when(
                "_gust_inside == true and "
                "_object_scene.metadata.srd_ordinary_flame.burning == true",
                [
                    _require(
                        "_object_scene.metadata.srd_ordinary_flame.wind_protected != null",
                        "Admit whether this actual ordinary flame is protected from wind.",
                    ),
                    _set("_gust_flame_key", []),
                    _set("_gust_flame_seen", False),
                    _when(
                        "_object_scene.metadata.srd_ordinary_flame.gust_dancing_sources != null "
                        "and source_id in "
                        "_object_scene.metadata.srd_ordinary_flame.gust_dancing_sources",
                        [_set("_gust_flame_seen", True)],
                    ),
                    _set("_gust_flame_events", "_gust_source.flames"),
                    _loop(
                        "_gust_flame_events",
                        "_gust_old_flame",
                        [
                            _when(
                                "_gust_old_flame.object_id == object_id and _gust_old_flame.tick "
                                "== state.lasting_tick and "
                                "_gust_old_flame.direction_revision == "
                                "_gust_source.direction_revision",
                                [_set("_gust_flame_seen", True)],
                            ),
                        ],
                    ),
                    _when(
                        "_gust_flame_seen != true",
                        [
                            _set("_gust_flame_roll", None),
                            _set("_gust_flame_extinguished", True),
                            _when(
                                "_object_scene.metadata.srd_ordinary_flame.wind_protected == true",
                                [
                                    {"dice": {"expression": "1d2"}, "store_as": "_gust_flame_roll"},
                                    _set("_gust_flame_extinguished", "_gust_flame_roll.total == 1"),
                                ],
                            ),
                            _set("_gust_dancing_ids", []),
                            _when(
                                "_object_scene.metadata.srd_ordinary_flame."
                                "gust_dancing_sources != null",
                                [
                                    _set(
                                        "_gust_dancing_ids",
                                        "_object_scene.metadata.srd_ordinary_flame.gust_dancing_sources",
                                    ),
                                ],
                            ),
                            _set(
                                "_gust_native_dancing",
                                "_object_scene.metadata.srd_ordinary_flame.dancing_wildly == true",
                            ),
                            _when(
                                "_object_scene.metadata.srd_ordinary_flame."
                                "gust_native_dancing != null",
                                [
                                    _set(
                                        "_gust_native_dancing",
                                        "_object_scene.metadata.srd_ordinary_flame.gust_native_dancing",
                                    ),
                                ],
                            ),
                            _when(
                                "_object_scene.metadata.srd_ordinary_flame.wind_protected == true "
                                "and _gust_flame_extinguished != true "
                                "and source_id not in _gust_dancing_ids",
                                [
                                    _push("_gust_dancing_ids", "source_id"),
                                ],
                            ),
                            _set(
                                "_gust_flame_patch",
                                {
                                    "burning": "_gust_flame_extinguished != true",
                                    "dancing_wildly": (
                                        "_gust_native_dancing == true or len(_gust_dancing_ids) > 0"
                                    ),
                                    "gust_dancing_sources": "_gust_dancing_ids",
                                    "gust_native_dancing": "_gust_native_dancing",
                                },
                            ),
                            _set(
                                "_gust_flame",
                                "merge(_object_scene.metadata.srd_ordinary_flame, "
                                "_gust_flame_patch)",
                            ),
                            *_metadata_patch({"srd_ordinary_flame": "_gust_flame"}),
                            _push(
                                "_gust_flame_events",
                                {
                                    "object_id": "object_id",
                                    "tick": "state.lasting_tick",
                                    "direction_revision": "_gust_source.direction_revision",
                                    "roll": "_gust_flame_roll",
                                    "extinguished": "_gust_flame_extinguished",
                                },
                            ),
                            _set("_gust_patch", {"flames": "_gust_flame_events"}),
                            _set("_gust_source", "merge(_gust_source, _gust_patch)"),
                            *_save(),
                        ],
                    ),
                ],
            ),
        ],
    )
    world = _tool_scene(
        "Consume real ordinary flame objects after source cast, direction or caster movement.",
        [_parameter("source_id")],
        [
            _set("_gust_flame_entities", "state.spatial_scene.entities"),
            _loop(
                "_gust_flame_entities",
                "_gust_flame_entity",
                [
                    _when(
                        "_gust_flame_entity.active == true and "
                        "_gust_flame_entity.metadata.srd_ordinary_flame != null",
                        [
                            _call(
                                "resolve_srd_gust_flame",
                                {
                                    "source_id": "{source_id}",
                                    "object_id": "{_gust_flame_entity.id}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
            _call("resolve_srd_gust_fog", {"source_id": "{source_id}"}),
        ],
    )
    all_world = _tool_scene(
        "Refresh source-qualified moving Lines after a genuine committed scene movement.",
        [],
        [
            _when(
                "state.has_gust_sources == true",
                [
                    _set("_gust_world_sources", "state.gust_sources"),
                    _loop(
                        "_gust_world_sources",
                        "_gust_world_source",
                        [
                            _when(
                                "_gust_world_source.active == true",
                                [
                                    _call(
                                        "resolve_srd_gust_world",
                                        {"source_id": "{_gust_world_source.id}"},
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
        "record_srd_wind_flame_protection": admit,
        "resolve_srd_gust_flame": flame,
        "resolve_srd_gust_world": world,
        "refresh_srd_gust_world": all_world,
    }


def _retire_flame_tool():
    """Remove just this exact source's dancing without relighting an extinguished flame."""
    return _tool_scene(
        "Remove the exact source's protected-flame dancing, preserving other wind facts.",
        [_parameter("source_id"), _parameter("object_id")],
        [
            *_lookup_object(),
            _set("_gust_dancing_ids", []),
            _set(
                "_gust_old_dancing_ids",
                "_object_scene.metadata.srd_ordinary_flame.gust_dancing_sources",
            ),
            _loop(
                "_gust_old_dancing_ids",
                "_gust_dancing_id",
                [
                    _when(
                        "_gust_dancing_id != source_id",
                        [_push("_gust_dancing_ids", "_gust_dancing_id")],
                    ),
                ],
            ),
            _set(
                "_gust_native_dancing",
                "_object_scene.metadata.srd_ordinary_flame.gust_native_dancing == true",
            ),
            _set(
                "_gust_dancing_patch",
                {
                    "gust_dancing_sources": "_gust_dancing_ids",
                    "dancing_wildly": "_gust_native_dancing == true or len(_gust_dancing_ids) > 0",
                },
            ),
            _set(
                "_gust_flame",
                "merge(_object_scene.metadata.srd_ordinary_flame, _gust_dancing_patch)",
            ),
            _when(
                "len(_gust_dancing_ids) == 0",
                [
                    _set("_gust_native_clear", {"gust_native_dancing": None}),
                    _set("_gust_flame", "merge(_gust_flame, _gust_native_clear)"),
                ],
            ),
            *_metadata_patch({"srd_ordinary_flame": "_gust_flame"}),
        ],
    )


def _fog_tool():
    """Reuse the exact fog sources whose own printed rule ends them in strong wind."""
    return _tool_scene(
        "Disperse actual source fog when a real clear part of this strong-wind Line reaches it.",
        [_parameter("source_id")],
        [
            *_source(),
            _call(
                "qualify_srd_gust_source",
                {
                    "caster_state": "{_gust_source.caster}",
                    "source_id": "{source_id}",
                },
                ["_gust_active", "_gust_origin", "_gust_plane", "_gust_min", "_gust_max"],
            ),
            _when(
                "_gust_active == true and state.has_spell_environments == true",
                [
                    _set("_gust_fog_ids", "state.persistent_spell_area_ids"),
                    _loop(
                        "_gust_fog_ids",
                        "_gust_fog_id",
                        [
                            _set("_gust_fog", "state.persistent_spell_areas[_gust_fog_id]"),
                            _when(
                                "_gust_fog.profile == 'environment' and _gust_fog.active == true "
                                "and "
                                "state.lasting_tick < _gust_fog.expires_tick and _gust_fog.plane "
                                "== "
                                "_gust_plane and " + _wind_fog_mode("_gust_fog"),
                                [
                                    _set(
                                        "_gust_fog_closest",
                                        [
                                            f"max(_gust_min[{i}], "
                                            f"min(_gust_max[{i}], _gust_fog.origin[{i}]))"
                                            for i in range(3)
                                        ],
                                    ),
                                    _call(
                                        "measure_srd_information_distance",
                                        {
                                            "start": "{_gust_fog_closest}",
                                            "end": "{_gust_fog.origin}",
                                        },
                                        ["_scene_distance"],
                                    ),
                                    _when(
                                        "_scene_distance < _gust_fog.radius",
                                        [
                                            _call(
                                                "srd_information_barriers",
                                                {
                                                    "start": "{_gust_origin}",
                                                    "end": "{_gust_fog_closest}",
                                                    "rule": "solid",
                                                },
                                                ["_information_blocked"],
                                            ),
                                            _when(
                                                "_information_blocked != true",
                                                [
                                                    _call(
                                                        "qualify_srd_environment_concentration",
                                                        {
                                                            "source_state": "{_gust_fog.caster}",
                                                            "area_id": "{_gust_fog_id}",
                                                        },
                                                    ),
                                                    _call(
                                                        "end_srd_concentration_node",
                                                        {
                                                            "source_state": "{_gust_fog.caster}",
                                                            "target_state": "{_gust_fog.caster}",
                                                            "lasting_state": "state",
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
            ),
        ],
    )


def _cast_tool():
    return _tool_scene(
        "Install the paid source's actual concentration and resolve its initial Line creatures.",
        [
            _entity("caster_state"),
            _parameter("slot_level", "integer"),
            _parameter("save_dc", "integer"),
            _parameter("concentration_modifier", "integer"),
            *deepcopy(CAST_CHOICES),
            *deepcopy(HIDDEN_SOURCE),
        ],
        [
            *_direction("gust_direction"),
            _require(
                "save_dc > 0 and slot_level >= 2", "Use the actual paid level and spellcasting DC."
            ),
            _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
            _when("state.gust_sources == null", [_set("state.gust_sources", [])]),
            _require(
                "len(state.gust_sources) < 128",
                "Retire old source history before more Gust instances.",
            ),
            _set("_gust_focus", ["_caster_state_id"]),
            _set("_gust_cast_owner", None),
            _when("spell_cast_owner != null", [_set("_gust_cast_owner", "_spell_cast_owner_id")]),
            _call(
                "start_srd_condition_concentration",
                {
                    "caster_state": "{_caster_state_id}",
                    "lasting_state": "state",
                    "spell": "gust_of_wind",
                    "targets": "{_gust_focus}",
                    "duration_rounds": 10,
                    "save_modifier": "{concentration_modifier}",
                    "spell_cast_id": "{spell_cast_id}",
                    "spell_cast_owner": "{_gust_cast_owner}",
                    "actual_spell_level": "{actual_spell_level}",
                },
            ),
            _set("caster_state.concentration_effect_family", "'gust_of_wind'"),
            _call(
                "format_srd_information_projection",
                {"message": "gust:{_caster_state_id}:{caster_state.concentration_sequence}"},
                ["_info_projection_message"],
            ),
            _set("_gust_source_id", "_info_projection_message"),
            _set("caster_state.gust_source_id", "_gust_source_id"),
            _set("_gust_cast_turn", 0),
            _when(
                "caster_state.turns_started != null",
                [_set("_gust_cast_turn", "caster_state.turns_started")],
            ),
            _set(
                "_gust_source",
                {
                    "id": "_gust_source_id",
                    "spell": "'gust_of_wind'",
                    "caster": "_caster_state_id",
                    "sequence": "caster_state.concentration_sequence",
                    "started_tick": "state.lasting_tick",
                    "expires_tick": "state.lasting_tick + 20",
                    "active": True,
                    "slot_level": "slot_level",
                    "spell_level": "slot_level",
                    "save_dc": "save_dc",
                    "direction": "gust_direction",
                    "height": "gust_height",
                    "include_origin": "gust_include_origin",
                    "cast_turn": "_gust_cast_turn",
                    "direction_revision": 0,
                    "cast_id": "spell_cast_id",
                    "source_caster_id": "_caster_state_id",
                    "dispel_ongoing": True,
                    "ongoing_duration": True,
                    "events": [],
                    "flames": [],
                },
            ),
            _set("_gust_records", "state.gust_sources"),
            _push("_gust_records", "_gust_source"),
            _set("state.gust_sources", "_gust_records"),
            _set("state.has_gust_sources", True),
            _set("_gust_initial_entities", "state.spatial_scene.entities"),
            _set("_gust_initial_actors", []),
            _loop(
                "_gust_initial_entities",
                "_gust_initial_entity",
                [
                    _when(
                        "_gust_initial_entity.active == true and "
                        "_gust_initial_entity.metadata.participant_ref != null",
                        [
                            _require(
                                "_gust_initial_entity.metadata.participant_ref not in "
                                "_gust_initial_actors",
                                "Gust requires one actual active body per affected creature.",
                            ),
                            _push(
                                "_gust_initial_actors",
                                "_gust_initial_entity.metadata.participant_ref",
                            ),
                        ],
                    ),
                ],
            ),
            _require(
                "len(_gust_initial_actors) <= 64",
                "This Line supports at most sixty-four real scene creatures.",
            ),
            _loop(
                "_gust_initial_actors",
                "_gust_initial_actor",
                [
                    _call(
                        "resolve_srd_gust_save",
                        {
                            "target_state": "{_gust_initial_actor}",
                            "source_id": "{_gust_source_id}",
                            "event": "initial",
                        },
                    ),
                ],
            ),
            _call("resolve_srd_gust_world", {"source_id": "{_gust_source_id}"}),
            _set("_spell_summary_rows", [{"label": "'Gust source'", "value": "_gust_source_id"}]),
            *_source("_gust_source_id"),
            _set("_spell_detail_rows", []),
            _set("_gust_card_events", "_gust_source.events"),
            _loop(
                "_gust_card_events",
                "_gust_card_event",
                [
                    _call(
                        "format_srd_information_projection",
                        {
                            "message": (
                                "{_gust_card_event.outcome}; pushed {_gust_card_event.pushed}; "
                                "actual endpoint {_gust_card_event.destination}"
                            ),
                        },
                        ["_info_projection_message"],
                    ),
                    _push(
                        "_spell_detail_rows",
                        {"label": "_gust_card_event.target", "value": "_info_projection_message"},
                    ),
                ],
            ),
        ],
    )


def _lifetime_tools():
    matching = _tool_scene(
        "Read only this exact Gust instance's current concentration linkage.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            *_source(),
            _set(
                "_gust_current_concentration",
                "_gust_source.active == true and "
                "_gust_source.caster == _caster_state_id and caster_state.gust_source_id == "
                "source_id and caster_state.concentration_spell_id == 'gust_of_wind' and "
                "caster_state.concentration_effect_family == 'gust_of_wind' and "
                "caster_state.concentration_sequence == _gust_source.sequence",
            ),
        ],
    )
    refresh = _tool_scene(
        "End the source at its real one-minute boundary without clearing unrelated concentration.",
        [],
        [
            _when(
                "state.has_gust_sources == true",
                [
                    _set("_gust_expiry_rows", "state.gust_sources"),
                    _loop(
                        "_gust_expiry_rows",
                        "_gust_expiry",
                        [
                            _when(
                                "_gust_expiry.active == true",
                                [
                                    _call(
                                        "qualify_srd_gust_concentration",
                                        {
                                            "caster_state": "{_gust_expiry.caster}",
                                            "source_id": "{_gust_expiry.id}",
                                        },
                                        ["_gust_current_concentration"],
                                    ),
                                    _when(
                                        "state.lasting_tick >= _gust_expiry.expires_tick or "
                                        "_gust_current_concentration != true",
                                        [
                                            {
                                                "condition": "_gust_current_concentration == true",
                                                "on_true": [
                                                    _call(
                                                        "end_srd_concentration_node",
                                                        {
                                                            "source_state": "{_gust_expiry.caster}",
                                                            "target_state": "{_gust_expiry.caster}",
                                                            "lasting_state": "state",
                                                        },
                                                    ),
                                                ],
                                                "on_false": [
                                                    _call(
                                                        "retire_srd_gust_source",
                                                        {"source_id": "{_gust_expiry.id}"},
                                                    ),
                                                ],
                                            },
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            )
        ],
    )
    retire = _tool_scene(
        "Retire only the selected saved Gust instance.",
        [_parameter("source_id")],
        [
            *_source(),
            _set(
                "_gust_patch", {"active": False, "dispel_ongoing": False, "ongoing_duration": False}
            ),
            _set("_gust_source", "merge(_gust_source, _gust_patch)"),
            *_save(),
            _set("_gust_live", False),
            _set("_gust_final_rows", "state.gust_sources"),
            _loop(
                "_gust_final_rows",
                "_gust_final",
                [
                    _when("_gust_final.active == true", [_set("_gust_live", True)]),
                ],
            ),
            _set("state.has_gust_sources", "_gust_live"),
            _set("_gust_ending_flames", "state.spatial_scene.entities"),
            _loop(
                "_gust_ending_flames",
                "_gust_ending_flame",
                [
                    _when(
                        "_gust_ending_flame.active == true and "
                        "_gust_ending_flame.metadata.srd_ordinary_flame.gust_dancing_sources "
                        "!= null and source_id in "
                        "_gust_ending_flame.metadata.srd_ordinary_flame.gust_dancing_sources",
                        [
                            _call(
                                "retire_srd_gust_flame",
                                {
                                    "source_id": "{source_id}",
                                    "object_id": "{_gust_ending_flame.id}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    direction = _public(
        _tool_scene(
            "Spend the actual caster's later-turn Bonus Action to redirect this retained Line.",
            [_entity("actor_state"), _parameter("source_id"), _array("direction")],
            [
                _require(PENDING, "Complete the actual pending action before redirecting Gust."),
                *_direction(),
                *_source(),
                _require(
                    "_gust_source.caster == _actor_state_id",
                    "Only this source's real caster may redirect it.",
                ),
                _call(
                    "qualify_srd_gust_source",
                    {"caster_state": "{_actor_state_id}", "source_id": "{source_id}"},
                    ["_gust_active"],
                ),
                _require(
                    "_gust_active == true", "The actual Gust concentration must still be active."
                ),
                _call(
                    "check_srd_active_control_action",
                    {"actor_state": "{_actor_state_id}", "action_kind": "bonus_action"},
                ),
                {
                    "condition": "combat.encounter_active == true",
                    "on_true": [
                        _require(
                            "actor_state.turns_started > _gust_source.cast_turn",
                            "Redirect Gust only on a later caster turn.",
                        ),
                        _call(
                            "consume_srd_bonus_action",
                            {
                                "actor_state": "{_actor_state_id}",
                                "bonus_action": "gust_of_wind_direction",
                            },
                        ),
                    ],
                    "on_false": [
                        _require(
                            "state.lasting_tick >= _gust_source.started_tick + 2",
                            "A later exploration round must have elapsed.",
                        ),
                    ],
                },
                _set(
                    "_gust_patch",
                    {
                        "direction": "direction",
                        "direction_revision": "_gust_source.direction_revision + 1",
                    },
                ),
                _set("_gust_source", "merge(_gust_source, _gust_patch)"),
                *_save(),
                _call("resolve_srd_gust_world", {"source_id": "{source_id}"}),
            ],
        ),
        "spell",
        "Gust direction",
        [("Source", "source_id"), ("Direction", "direction")],
    )
    return {
        "retire_srd_gust_source": retire,
        "change_srd_gust_direction": direction,
        "qualify_srd_gust_concentration": matching,
        "refresh_srd_gust_sources": refresh,
    }


def _cast_binding(tools, changed):
    for name in ("cast_spell", "cast_spell_node", "cast_spell_outside_combat"):
        if name not in tools:
            continue
        owner = deepcopy(tools[name])
        names = {p["name"] for p in owner["parameters"]}
        owner["parameters"] += [deepcopy(p) for p in CAST_CHOICES if p["name"] not in names]
        branch = _when(
            "_spell_definition.family == 'gust_of_wind'",
            [
                _call(
                    "cast_srd_gust_of_wind",
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

        def replace(effects, branch=branch):
            found = False
            for i, e in enumerate(effects):
                if "_spell_definition.family == 'gust_of_wind'" in e.get("condition", ""):
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
            slots = [
                i
                for i, e in enumerate(effects)
                if "_spell_definition.family ==" in e.get("condition", "")
            ]
            if slots:
                effects.insert(slots[-1] + 1, deepcopy(branch))
                return True
            return any(insert(e[k]) for e in effects for k in ("on_true", "on_false") if k in e)

        if not replace(owner["effects"]) and not insert(owner["effects"]):
            raise ValueError("Gust needs the actual paid dispatcher.")
        changed[name] = owner


def extend_runtime(text):
    """Emit source-owned tools; the final physical receiver and route/turn joins bind later."""
    tools = _load_runtime(text)["tools"]
    changed = {
        **_geometry_tools(),
        **_push_tools(),
        **_flame_tools(),
        **_lifetime_tools(),
        "cast_srd_gust_of_wind": _cast_tool(),
        "resolve_srd_gust_fog": _fog_tool(),
        "retire_srd_gust_flame": _retire_flame_tool(),
    }
    _cast_binding(tools, changed)
    _normalize(changed)
    text = _emit(text, changed)
    schema = {
        "type": "object",
        "properties": {
            key: {
                "type": "boolean"
                if type(value) is bool
                else "integer"
                if type(value) is int
                else "string"
            }
            for key, value in PROFILES["gust_of_wind"].items()
            if key != "family"
        },
        "required": [key for key in PROFILES["gust_of_wind"] if key != "family"],
        "additionalProperties": False,
    }
    row = "      gust_of_wind: " + json.dumps(schema, separators=(",", ":"))
    if re.search(r"(?m)^      gust_of_wind: .*", text):
        text = re.sub(r"(?m)^      gust_of_wind: .*", lambda _: row, text)
    else:
        variants = _load_runtime(text)["definition_row_schemas"]["definitions.spells"]["variants"]
        anchor = "      " + next(iter(variants)) + ":"
        if text.count(anchor) != 1:
            raise ValueError("Gust needs the unique closed family schema anchor.")
        text = text.replace(anchor, row + "\n" + anchor, 1)
    return text


def _receiver(tools):
    """Capture the final physical receiver without a public forced-movement switch."""
    node_name, source_name = "push_srd_gust_subject_node", "move_srd_travel_subject_node"
    original = tools.get(source_name, tools["move_srd_travel_subject"])
    node = deepcopy(original)
    node["effects"] = _source_semantics(node["effects"])
    fixed = {
        "teleport": False,
        "levitate_source_id": "",
        "telekinesis_source_id": "",
        "sphere_source_id": "",
    }
    removed = [p for p in node["parameters"] if p["name"] in fixed]
    node["parameters"] = [p for p in node["parameters"] if p["name"] not in fixed]
    node["effects"][:0] = [_set(p["name"], fixed[p["name"]]) for p in removed]
    qualify = [
        _call(
            "qualify_srd_gust_push",
            {"subject": "{subject}", "destination": "{destination}", "plane": "{plane}"},
            ["_gust_push_allowed"],
        ),
        _require("_gust_push_allowed == true", "Use the actual failed-save Gust receipt."),
    ]
    node["effects"][:0] = deepcopy(qualify)
    mutation = [i for i, e in enumerate(node["effects"]) if "spatial_mutation" in e]
    if len(mutation) != 1:
        raise ValueError("Gust must capture the final sole actual relocation mutation.")
    guard = _receiver_guards(tools, original)
    # Keep the existing authentic Secure/Hallow joins, changing only the source-specific message.
    guard = json.loads(
        json.dumps(guard)
        .replace("paid Ice consequence", "failed-save Gust consequence")
        .replace("Ice push", "Gust push")
    )
    node["effects"][mutation[0] : mutation[0]] = [
        *guard,
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
            "store_as": "_gust_push_sweep",
        },
        _require(
            "_gust_push_sweep.supported == true and _gust_push_sweep.clear == true",
            "A blocked push needs its authentic partial displacement consumer; no fake passage.",
        ),
        _set("state.gust_pending_push.consumed", True),
    ]
    if source_name not in tools:
        return {"push_srd_gust_subject": node}
    wrapper = deepcopy(tools["move_srd_travel_subject"])

    def substitute(value):
        if isinstance(value, dict):
            if value.get("call") == source_name:
                value["call"] = node_name
                for key in fixed:
                    value["params"].pop(key, None)
            for child in value.values():
                substitute(child)
        elif isinstance(value, list):
            for child in value:
                substitute(child)

    wrapper["effects"] = _source_semantics(wrapper["effects"])
    removed = [p for p in wrapper["parameters"] if p["name"] in fixed]
    wrapper["parameters"] = [p for p in wrapper["parameters"] if p["name"] not in fixed]
    wrapper["effects"][:0] = [
        *deepcopy(qualify),
        *[_set(p["name"], fixed[p["name"]]) for p in removed],
    ]
    substitute(wrapper["effects"])
    return {"push_srd_gust_subject": wrapper, node_name: node}


def _route_binding(tools):
    """Split the actual path at Line edges and its closest point to each current caster."""
    cost = deepcopy(tools["srd_information_movement_cost"])
    if "_gust_route_sources" in str(cost["effects"]):
        return {"srd_information_movement_cost": cost}
    collect = [
        _set("_gust_route_sources", []),
        _set("_gust_route_extra", 0),
        _when(
            "state.has_gust_sources == true and _route_distance > 0",
            [
                _set("_gust_route_rows", "state.gust_sources"),
                _loop(
                    "_gust_route_rows",
                    "_gust_route_source",
                    [
                        _when(
                            "_gust_route_source.active == true",
                            [
                                _call(
                                    "qualify_srd_gust_source",
                                    {
                                        "caster_state": "{_gust_route_source.caster}",
                                        "source_id": "{_gust_route_source.id}",
                                    },
                                    ["_gust_active", "_gust_origin", "_gust_plane", "_gust_volume"],
                                ),
                                _when(
                                    "_gust_active == true and _gust_plane == plane",
                                    [
                                        _call(
                                            "srd_information_segment_interval",
                                            {
                                                "start": "{start}",
                                                "end": "{end}",
                                                "volume": "{_gust_volume}",
                                            },
                                            ["_segment_entry", "_segment_exit", "_segment_hit"],
                                        ),
                                        _when(
                                            "_segment_hit == true",
                                            [
                                                _push("_route_cuts", {"t": "_segment_entry"}),
                                                _push("_route_cuts", {"t": "_segment_exit"}),
                                                _set(
                                                    "_gust_route_dot",
                                                    "(start[0] - _gust_origin[0]) * "
                                                    "(end[0] - start[0]) + "
                                                    "(start[1] - _gust_origin[1]) * "
                                                    "(end[1] - start[1]) + "
                                                    "(start[2] - _gust_origin[2]) * "
                                                    "(end[2] - start[2])",
                                                ),
                                                _set(
                                                    "_gust_closest_t",
                                                    "-_gust_route_dot * _route_distance ** -2",
                                                ),
                                                _when(
                                                    "_gust_closest_t > 0 and _gust_closest_t < 1",
                                                    [
                                                        _push(
                                                            "_route_cuts", {"t": "_gust_closest_t"}
                                                        ),
                                                    ],
                                                ),
                                                _push(
                                                    "_gust_route_sources",
                                                    {
                                                        "origin": "_gust_origin",
                                                        "volume": "_gust_volume",
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
        ),
    ]
    sorting = next(i for i, e in enumerate(cost["effects"]) if "sort" in e)
    cost["effects"][sorting:sorting] = collect
    piece = [
        _set("_gust_toward", False),
        _loop(
            "_gust_route_sources",
            "_gust_route_line",
            [
                _call(
                    "srd_information_segment_interval",
                    {
                        "start": "{_route_point}",
                        "end": "{_route_point}",
                        "volume": "{_gust_route_line.volume}",
                    },
                    ["_segment_hit"],
                ),
                _when(
                    "_segment_hit == true",
                    [
                        _set(
                            "_gust_mid_dot",
                            "(_route_point[0] - _gust_route_line.origin[0]) * "
                            "(end[0] - start[0]) + (_route_point[1] - "
                            "_gust_route_line.origin[1]) * "
                            "(end[1] - start[1]) + (_route_point[2] - "
                            "_gust_route_line.origin[2]) * "
                            "(end[2] - start[2])",
                        ),
                        _when(
                            "_gust_mid_dot < 0",
                            [
                                _call(
                                    "srd_information_barriers",
                                    {
                                        "start": "{_gust_route_line.origin}",
                                        "end": "{_route_point}",
                                        "rule": "solid",
                                    },
                                    ["_information_blocked"],
                                ),
                                _when("_information_blocked != true", [_set("_gust_toward", True)]),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        _when(
            "_gust_toward == true",
            [
                _set("_gust_piece_extra", "(_route_cut.t - _route_previous) * _route_distance"),
                _set("_route_piece_cost", "_route_piece_cost + _gust_piece_extra"),
                _set("_gust_route_extra", "_gust_route_extra + _gust_piece_extra"),
            ],
        ),
    ]

    def attach(value):
        if isinstance(value, list):
            for i, e in enumerate(value):
                if (
                    isinstance(e, dict)
                    and e.get("set", {}).get("field") == "_route_cost"
                    and e["set"].get("value") == "_route_cost + _route_piece_cost"
                ):
                    value[i:i] = deepcopy(piece)
                    return True
                if attach(e):
                    return True
        elif isinstance(value, dict):
            return any(attach(child) for child in value.values())
        return False

    if not attach(cost["effects"]):
        raise ValueError("Gust needs the real final route-piece cost boundary.")
    changed = {"srd_information_movement_cost": cost}
    for name in ("move_srd_scene_creature_environment_segment", "move_srd_scene_creature_node"):
        if name not in tools:
            continue
        owner = deepcopy(tools[name])

        def preserve(value):
            if isinstance(value, dict):
                if (
                    value.get("call") == "srd_information_movement_cost"
                    and value.get("export_context") != "*"
                ):
                    value.setdefault("export_context", {})["_gust_route_extra"] = (
                        "_gust_route_extra"
                    )
                if (
                    value.get("set", {}).get("field") == "_route_cost"
                    and value["set"].get("value") == "_route_distance"
                ):
                    value["set"]["value"] = "_route_distance + _gust_route_extra"
                for child in value.values():
                    preserve(child)
            elif isinstance(value, list):
                for child in value:
                    preserve(child)

        preserve(owner["effects"])
        changed[name] = owner
    return changed


def bind_runtime_consumers(text):
    """Attach to final physical, source-removal and turn owners without another position owner."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    # Capture this actual motion aftermath in the forced receiver on the first pass too.
    for name in ("move_srd_scene_creature", "move_srd_travel_subject"):
        owner = deepcopy(tools[name])
        if "refresh_srd_gust_world" not in str(owner["effects"]):
            owner["effects"].append(_call("refresh_srd_gust_world", {}))
        tools[name] = owner
        changed[name] = owner
    changed.update(_receiver(tools))
    changed.update(_route_binding(tools))
    end = deepcopy(tools["end_combat_turn"])
    if "resolve_srd_gust_turn_end" not in str(end["effects"]):
        index = next(
            i
            for i, e in enumerate(end["effects"])
            if e.get("set", {}).get("field") == "combat.action_available"
        )
        end["effects"].insert(
            index,
            _when(
                "state.has_gust_sources == true",
                [
                    _call(
                        "resolve_srd_gust_turn_end",
                        {"target_state": "{_actor_id}"},
                        ["_gust_end_details"],
                    ),
                    _loop(
                        "_gust_end_details",
                        "_gust_end_detail",
                        [_push("_end_turn_rows", "_gust_end_detail")],
                    ),
                ],
            ),
        )
    changed["end_combat_turn"] = end
    concentration = deepcopy(tools["end_srd_concentration_node"])
    if "retire_srd_gust_source" not in str(concentration["effects"]):
        concentration["effects"].insert(
            0,
            _when(
                "source_state.concentration_effect_family == 'gust_of_wind'",
                [
                    _call("retire_srd_gust_source", {"source_id": "{source_state.gust_source_id}"}),
                    _set("source_state.gust_source_id", None),
                ],
            ),
        )
    changed["end_srd_concentration_node"] = concentration
    clock = deepcopy(tools["advance_srd_lasting_clock"])
    if "refresh_srd_gust_sources" not in str(clock["effects"]):
        clock["effects"].append(_call("refresh_srd_gust_sources", {}))
    changed["advance_srd_lasting_clock"] = clock
    for owner in changed.values():
        # Existing owners may bind the package state under lasting_state rather than state.
        bound = _tool_scene(owner["description"], deepcopy(owner["parameters"]), owner["effects"])
        owner["effects"] = bound["effects"]
        if not any(p["name"] == "lasting_state" for p in owner["parameters"]):
            owner["parameters"] += [p for p in bound["parameters"] if p["name"] == "lasting_state"]
    _normalize(changed)
    return _emit(text, changed)
