"""Use native fall staging, protection and clock with current exploration facts."""

from copy import deepcopy

from sylvia_gm.domain.package_library import participant_schema_sha256

from .srd_control_behaviors import _each, _push
from .srd_feather_fall import _ground, _subject, _sweep
from .srd_lite_steed_disappearance import FALLING, ONE, QUEUE
from .srd_lite_steed_exploration_abilities import RECORDS, _bound, _choose, _native_plane
from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _tool, _when

MODE = "exploration_steed_loss"
RELATION = "qualify_srd_lite_exploration_fall_relation"
READ_RELATION = "read_srd_lite_exploration_fall_relation"
ARRIVE = "admit_srd_lite_exploration_fall_position"
NATIVE_ARRIVAL = "read_srd_lite_exploration_fall_native_arrival"
QUALIFY = "qualify_srd_scene_fall_target"
BIND = "bind_srd_scene_fall_target"
OBSERVE = "qualify_srd_feather_fall_observation"
TARGET = "qualify_srd_feather_fall_target"
VALIDATE = "validate_srd_feather_fall_trigger"
SETTLE_TARGET = "settle_srd_scene_fall_target"
RESOLVE_EVENT = "resolve_srd_scene_fall_event"

RELATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "caster": {"type": "string", "minLength": 1},
        "target": {"type": "string", "minLength": 1},
        "distance": {"type": "number", "minimum": 0},
        "same_plane": {"type": "boolean"},
        "visible": {"type": "boolean"},
    },
    "required": ["caster", "target", "distance", "same_plane", "visible"],
}

SCENE_FALL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "event_id": {"type": "string", "maxLength": 100},
        "status": {
            "type": "string",
            "enum": ["ready", "pending", "descending", "landed", "arrested"],
        },
        "started_tick": {"type": "number", "minimum": 0},
        "settled_tick": {"type": "number", "minimum": 0},
        "scene_id": {"type": "string", "maxLength": 100},
        "plane": {"type": "string", "minLength": 1, "maxLength": 100},
        "origin": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
        "cause": {"type": "string", "enum": ["departure", "support_lost", "flight_ended"]},
        "ended_reason": {"type": "string", "maxLength": 100},
        "settling": {"type": "boolean"},
        "mode": {"type": "string", "enum": [MODE]},
        "steed_id": {"type": "string", "minLength": 1, "maxLength": 100},
        "remaining_distance": {"type": "number", "minimum": 0},
        "landing_placement": {"type": "string", "minLength": 1, "maxLength": 240},
        "current_placement": {"type": "string", "minLength": 1, "maxLength": 240},
    },
    "required": ["event_id", "status", "started_tick", "settled_tick", "plane", "cause"],
}


def _native_arrival():
    """Snapshot admitted arrival scalars; literal nulls avoid the DSL's missing-ref fallback."""
    return _tool(
        "Read this exact creature's optional native fictional arrival without inventing facts.",
        [_entity("subject")],
        [
            _set(
                "_native_arrival",
                {"has_travel": False, "place": None, "plane": None, "path_plane": None},
            ),
            _when(
                "subject.travel_location != null",
                [
                    _require(
                        "len(subject.travel_location) == 4 and "
                        "'place' in subject.travel_location and "
                        "'plane' in subject.travel_location and "
                        "'native_anchor' in subject.travel_location and "
                        "'via_spell' in subject.travel_location",
                        "Use the admitted native fictional arrival shape.",
                    ),
                    _require(
                        "subject.travel_location.native_anchor == false and "
                        "subject.travel_location.via_spell == 'misty_step' and "
                        "subject.travel_location.place != null and "
                        "subject.travel_location.plane != null",
                        "Keep the actual native Misty Step arrival source and named place/plane.",
                    ),
                    _set(
                        "_native_arrival",
                        {
                            "has_travel": True,
                            "place": "subject.travel_location.place",
                            "plane": "subject.travel_location.plane",
                            "path_plane": None,
                        },
                    ),
                ],
            ),
            _when(
                "subject.find_path_current_plane != null",
                [
                    _set(
                        "_native_arrival_path",
                        {"path_plane": "subject.find_path_current_plane"},
                    ),
                    _set("_native_arrival", "merge(_native_arrival, _native_arrival_path)"),
                ],
            ),
        ],
    )


def read_arrival(actor, destination):
    """Call one shared reader; optional absent fields remain actual nulls."""
    identity = "_" + actor + "_id"
    return [
        _call(NATIVE_ARRIVAL, {"subject": "{" + identity + "}"}, ("_native_arrival",)),
        _set(destination, "_native_arrival"),
    ]


def same_arrival(left, right, error):
    """Compare only bounded scalar fields; the native DSL does not compare whole objects."""
    return [
        _require(
            " and ".join(left + "." + field + " == " + right + "." + field for field in fields),
            error,
        )
        for fields in (("has_travel", "place"), ("plane", "path_plane"))
    ]


def check_arrival(actor, saved, error):
    return [
        *read_arrival(actor, "_current_native_arrival"),
        *same_arrival("_current_native_arrival", saved, error),
    ]


def relation_parameters():
    return [
        _parameter(
            "established_feather_relations",
            "array",
            required=False,
            default=[],
            items=RELATION_SCHEMA,
            max_items=64,
        ),
        _parameter("established_clear_descent", "boolean", required=False, default=False),
        _parameter("established_rider_current_placement", required=False, default=""),
    ]


def collect_relations():
    """Bind declared pairs to genuine current entities, not caller-provided snapshots."""
    return [
        _set("_loss_feather_relations", []),
        _set("_loss_relation_pairs", []),
        _set("_loss_future_rider", ["_loss_rider_id"]),
        _set("_loss_relation_inputs", "established_feather_relations"),
        _each(
            "_loss_relation_inputs",
            "_loss_relation_input",
            [
                _require(
                    "len(_loss_relation_input.caster) <= 100 and "
                    "len(_loss_relation_input.target) <= 100",
                    "Fall relation caster and target identities must be at most 100 characters.",
                ),
                _set(
                    "_loss_relation_pair",
                    ["_loss_relation_input.caster", "_loss_relation_input.target"],
                ),
                _require(
                    "_loss_relation_pair not in _loss_relation_pairs",
                    "Declare each actual caster/target pair once.",
                ),
                _call(
                    RELATION,
                    {
                        "caster_state": "{_loss_relation_input.caster}",
                        "target_state": "{_loss_relation_input.target}",
                        "future_rider": "{_loss_future_rider}",
                        "future_distance": "{established_rider_fall_distance}",
                        "plane": "{established_plane}",
                        "distance": "{_loss_relation_input.distance}",
                        "same_plane": "{_loss_relation_input.same_plane}",
                        "visible": "{_loss_relation_input.visible}",
                    },
                    ("_exploration_fall_relation",),
                ),
                _push("_loss_feather_relations", "_exploration_fall_relation"),
                _push("_loss_relation_pairs", "_loss_relation_pair"),
            ],
        ),
    ]


def _relation():
    return _tool(
        "Check a current Feather Fall relation for exact real exploration creatures.",
        [
            _entity("caster_state"),
            _entity("target_state"),
            _parameter(
                "future_rider",
                "array",
                items={"type": "string"},
                min_items=1,
                max_items=1,
            ),
            _parameter("future_distance", "number"),
            _parameter("plane"),
            _parameter("distance", "number", minimum=0),
            _parameter("same_plane", "boolean"),
            _parameter("visible", "boolean"),
        ],
        [
            _require(
                "len(_caster_state_id) <= 100 and len(_target_state_id) <= 100 and "
                "len(future_rider[0]) <= 128",
                "Fall relation identities must be at most 100 characters and "
                "the future rider identity at most 128 characters.",
            ),
            _require(
                "combat.encounter_active != true",
                "Declare exploration fall relations only outside combat.",
            ),
            _require(
                "_target_state_id == future_rider[0] or (target_state.scene_fall != null and "
                "target_state.scene_fall.mode == '" + MODE + "' and "
                "(target_state.scene_fall.status == 'pending' or "
                "target_state.scene_fall.status == 'descending'))",
                "Relate only the exact current rider or another actual saved "
                "falling exploration creature.",
            ),
            *_native_plane("caster_state", "plane"),
            *_native_plane("target_state", "plane"),
            _set("_relation_target_height", "future_distance"),
            _when(
                "_target_state_id != future_rider[0]",
                [
                    _set("_relation_target_height", "target_state.scene_fall.remaining_distance"),
                ],
            ),
            *read_arrival("caster_state", "_relation_caster_arrival"),
            *read_arrival("target_state", "_relation_target_arrival"),
            _set(
                "_exploration_fall_relation",
                {
                    "caster": "_caster_state_id",
                    "target": "_target_state_id",
                    "plane": "plane",
                    "distance": "distance",
                    "same_plane": "same_plane",
                    "visible": "visible",
                    "tick": "state.lasting_tick",
                    "caster_arrival": "_relation_caster_arrival",
                    "target_arrival": "_relation_target_arrival",
                    "target_height": "_relation_target_height",
                },
            ),
        ],
    )


def queue_rider():
    """Keep the original deferred queue so an enclosing damage action finishes first."""
    return [
        _when(
            "_loss_record.loss_facts.remaining_fall > 0 and rider_state.is_alive == true",
            [
                _require(
                    "rider_state.scene_fall == null or "
                    "(rider_state.scene_fall.status != 'ready' and "
                    "rider_state.scene_fall.status != 'pending' and "
                    "rider_state.scene_fall.status != 'descending')",
                    "Do not replace this rider's existing saved fall while releasing its Steed.",
                ),
                _set(
                    "rider_state.scene_fall",
                    {
                        "event_id": "''",
                        "status": "'ready'",
                        "mode": "'" + MODE + "'",
                        "started_tick": "state.lasting_tick",
                        "settled_tick": "state.lasting_tick",
                        "plane": "_loss_record.plane",
                        "cause": "'support_lost'",
                        "steed_id": "_steed_state_id",
                        "remaining_distance": "_loss_record.loss_facts.remaining_fall",
                        "landing_placement": "_loss_record.rider_placement",
                        "current_placement": "_loss_record.loss_facts.rider_current_placement",
                    },
                ),
                _set(
                    "state." + RECORDS + ".{_steed_state_id}.fall_relations",
                    "_loss_record.loss_facts.feather_relations",
                ),
                _set(
                    "state." + RECORDS + ".{_steed_state_id}.rider_placement",
                    "_loss_record.loss_facts.rider_current_placement",
                ),
                _when(QUEUE + " == null", [_set(QUEUE, [])]),
                _set("_loss_released_queue", QUEUE),
                _push(
                    "_loss_released_queue", {"steed": "_steed_state_id", "rider": "_rider_state_id"}
                ),
                _set(QUEUE, "_loss_released_queue"),
            ],
        ),
    ]


def _source(target="target_state", steed="target_state.scene_fall.steed_id"):
    return [
        _set("_fiction_fall_steed", steed),
        _set("_fiction_fall_source", "state.created_actor_records[_fiction_fall_steed]"),
        _set("_fiction_fall_placement", "state." + RECORDS + "[_fiction_fall_steed]"),
        _require(
            "_fiction_fall_source.individual == _fiction_fall_steed and "
            "_fiction_fall_source.spell == 'find_steed' and "
            "_fiction_fall_source.active == false and "
            "_fiction_fall_source.caster == _fiction_fall_placement.caster",
            "Keep the actual retired Find Steed individual/caster source for this fall.",
        ),
        _require(
            "_fiction_fall_placement.steed == _fiction_fall_steed and "
            "_fiction_fall_placement.rider == _" + target.removesuffix("_state") + "_state_id and "
            "_fiction_fall_placement.mounted == false and "
            "_fiction_fall_placement.released_by_disappearance == true and "
            "_fiction_fall_placement.disappearance == _fiction_fall_source.disappearance",
            "Use the genuinely released exact rider and the actual native disappearance cause.",
        ),
        _require(
            target + ".otherworldly_steed_mount == null",
            "The released falling rider must remain unmounted.",
        ),
        *_native_plane(target, target + ".scene_fall.plane"),
    ]


def _register(target_id):
    return [
        _when("state.falling_target_ids == null", [_set("state.falling_target_ids", [])]),
        _set("_fiction_fall_ids", "state.falling_target_ids"),
        _when(
            target_id + " not in _fiction_fall_ids",
            [
                _push("_fiction_fall_ids", target_id),
                _set("state.falling_target_ids", "_fiction_fall_ids"),
            ],
        ),
        _set("state.has_scene_falls", True),
    ]


def _released():
    return _bound(
        [
            *_source("rider_state", "steed_id"),
            _set(FALLING, False),
            _when(
                "rider_state.is_alive == true",
                [
                    _call(
                        "qualify_srd_mirage_support_flight",
                        {"target_state": "{_rider_state_id}"},
                        ("_support_flight", "_support_scene_descent", "_support_wind_descent"),
                    ),
                    _choose(
                        "_support_flight == true",
                        [
                            _set("rider_state.scene_fall.status", "'arrested'"),
                            _set("rider_state.scene_fall.ended_reason", "'resumed_flight'"),
                        ],
                        [
                            _choose(
                                "_support_wind_descent == true",
                                [
                                    _set("rider_state.scene_fall.status", "'descending'"),
                                    *_register("_rider_state_id"),
                                ],
                                [_set(FALLING, True)],
                            ),
                        ],
                    ),
                ],
            ),
            _when(
                "rider_state.is_alive != true",
                [
                    _set("rider_state.scene_fall.status", "'landed'"),
                    _set(
                        "state." + RECORDS + ".{_fiction_fall_steed}.rider_placement",
                        "rider_state.scene_fall.landing_placement",
                    ),
                ],
            ),
        ]
    )


def _qualify():
    return _bound(
        [
            *_source(),
            _require(
                "target_state.is_alive == true and target_state.scene_fall.status == 'ready' and "
                "target_state.scene_fall.remaining_distance > 0 and fall_cause == 'support_lost'",
                "Stage only this genuinely released living creature's declared positive fall.",
            ),
            _set(
                "_falling_event",
                {
                    "actor_id": "_target_state_id",
                    "mode": "'" + MODE + "'",
                    "steed_id": "_fiction_fall_steed",
                    "plane": "target_state.scene_fall.plane",
                    "distance": "target_state.scene_fall.remaining_distance",
                    "landing_placement": "target_state.scene_fall.landing_placement",
                    "current_placement": "target_state.scene_fall.current_placement",
                    "started_tick": "target_state.scene_fall.started_tick",
                    "cause": "fall_cause",
                    "feather_relations": "_fiction_fall_placement.fall_relations",
                },
            ),
        ]
    )


def _read_relation():
    return _tool(
        "Read fresh facts for this actual caster and falling target from the real event.",
        [
            _entity("caster_state"),
            _entity("target_state"),
            _parameter("reaction_trigger_id"),
        ],
        [
            _set("_fiction_relation", None),
            _set("_fiction_relation_start_tick", None),
            _set("_fiction_trigger", "state.pending_srd_actions[reaction_trigger_id]"),
            _require(
                "_fiction_trigger.kind == 'scene_fall' and _fiction_trigger.status == 'open' and "
                "_fiction_trigger.stage == 'reaction'",
                "Use the actual saved open Feather Fall reaction window.",
            ),
            _set("_fiction_trigger_rows", "_fiction_trigger.parameters.falling_events"),
            _each(
                "_fiction_trigger_rows",
                "_fiction_trigger_row",
                [
                    _when(
                        "_fiction_trigger_row.mode == '" + MODE + "'",
                        [
                            _set(
                                "_fiction_relation_rows", "_fiction_trigger_row.feather_relations"
                            ),
                            _each(
                                "_fiction_relation_rows",
                                "_fiction_relation_row",
                                [
                                    _when(
                                        "_fiction_relation_row.caster == _caster_state_id and "
                                        "_fiction_relation_row.target == _target_state_id",
                                        [
                                            _when(
                                                "_fiction_relation != null",
                                                [
                                                    *[
                                                        _require(
                                                            " and ".join(
                                                                "_fiction_relation."
                                                                + field
                                                                + " == _fiction_relation_row."
                                                                + field
                                                                for field in fields
                                                            ),
                                                            "Contradictory current fall facts.",
                                                        )
                                                        for fields in (
                                                            ("plane", "distance", "same_plane"),
                                                            ("visible", "tick", "target_height"),
                                                        )
                                                    ],
                                                    *same_arrival(
                                                        "_fiction_relation.caster_arrival",
                                                        "_fiction_relation_row.caster_arrival",
                                                        "Contradictory caster arrival.",
                                                    ),
                                                    *same_arrival(
                                                        "_fiction_relation.target_arrival",
                                                        "_fiction_relation_row.target_arrival",
                                                        "Contradictory target arrival.",
                                                    ),
                                                ],
                                            ),
                                            _set("_fiction_relation", "_fiction_relation_row"),
                                            _set(
                                                "_fiction_relation_start_tick",
                                                "_fiction_trigger_row.started_tick",
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _require(
                "_fiction_relation != null and (_fiction_relation.tick == state.lasting_tick or "
                "_fiction_relation.tick == _fiction_relation_start_tick) and "
                "target_state.scene_fall.remaining_distance == _fiction_relation.target_height",
                "Establish fresh actual range and sight for this caster/target pair "
                "before disappearance.",
            ),
            *check_arrival(
                "caster_state",
                "_fiction_relation.caster_arrival",
                "Current Feather Fall facts cannot override the caster's later native arrival.",
            ),
            *check_arrival(
                "target_state",
                "_fiction_relation.target_arrival",
                "Current Feather Fall facts cannot override the target's later native arrival.",
            ),
            *_native_plane("caster_state", "_fiction_relation.plane"),
            *_native_plane("target_state", "_fiction_relation.plane"),
            _require(
                "target_state.scene_fall.plane == _fiction_relation.plane",
                "Keep the actual saved fall plane consistent with current same-plane facts.",
            ),
            _set("_scene_distance", "_fiction_relation.distance"),
            _set("_scene_same_plane", "_fiction_relation.same_plane"),
            _set(
                "_fall_trigger_seen",
                "_fiction_relation.visible and "
                "(caster_state.conditions == null or not ('blinded' in caster_state.conditions))",
            ),
        ],
    )


def _feather_relation():
    return _bound(
        [
            _require(
                "combat.encounter_active != true",
                "A fictional fall needs actual combat admission before combat targeting.",
            ),
            _choose(
                "_caster_state_id == _target_state_id",
                [
                    *_native_plane("caster_state", "target_state.scene_fall.plane"),
                    _set("_scene_distance", 0),
                    _set("_scene_same_plane", True),
                    _set("_fall_trigger_seen", True),
                ],
                [
                    _call(
                        READ_RELATION,
                        {
                            "caster_state": "{_caster_state_id}",
                            "target_state": "{_target_state_id}",
                            "reaction_trigger_id": "{reaction_trigger_id}",
                        },
                        ("_scene_distance", "_scene_same_plane", "_fall_trigger_seen"),
                    ),
                ],
            ),
        ]
    )


def _land(reason, damage=False):
    rows = [
        _set("_fall_effective_damage", 0),
        _set("_fall_hp_lost", 0),
        _set("_fall_temp_hp_absorbed", 0),
    ]
    if damage:
        rows += [
            _when(
                "target_state.is_alive == true",
                [
                    {
                        "call": "resolve_srd_fall",
                        "params": {
                            "target_state": "{_target_state_id}",
                            "distance": "{target_state.scene_fall.remaining_distance}",
                            "landing": "ground",
                            "reaction": "none",
                        },
                        "export_context": {
                            "_fall_effective_damage": "_fall_effective_damage",
                            "_fall_hp_lost": "_fall_hp_lost",
                            "_fall_temp_hp_absorbed": "_fall_temp_hp_absorbed",
                            "_scene_fall_roll": "_fall_roll",
                        },
                    },
                ],
            )
        ]
    rows += [
        _set("target_state.scene_fall.remaining_distance", 0),
        _set("target_state.scene_fall.status", "'landed'"),
        _set(
            "target_state.scene_fall.current_placement", "target_state.scene_fall.landing_placement"
        ),
        _set("target_state.scene_fall.ended_reason", "'" + reason + "'"),
        _set("target_state.scene_fall.settled_tick", "state.lasting_tick"),
        _set("target_state.spell_flight_fall_pending", False),
        _set("target_state.flying_aloft", False),
        _set(
            "state." + RECORDS + ".{_fiction_fall_steed}.rider_placement",
            "target_state.scene_fall.landing_placement",
        ),
        _set("state." + RECORDS + ".{_fiction_fall_steed}.pending", True),
        _set("state." + RECORDS + ".{_fiction_fall_steed}.tick", "state.lasting_tick"),
        _call(
            "retire_srd_feather_fall_target",
            {"target_state": "{_target_state_id}", "reason": reason},
        ),
    ]
    return rows


def _settle():
    return _bound(
        [
            *_source(),
            _set("_scene_fall_pending", False),
            _set("_fall_effective_damage", 0),
            _set("_fall_hp_lost", 0),
            _set("_fall_temp_hp_absorbed", 0),
            _set("_scene_fall_roll", None),
            _set("_scene_fall_actor_name", "_target_state_id"),
            _when(
                "target_state.name != null and target_state.name != ''",
                [_set("_scene_fall_actor_name", "target_state.name")],
            ),
            _when(
                "target_state.scene_fall.status == 'descending' or "
                "(resolve_pending == true and target_state.scene_fall.status == 'pending')",
                [
                    _call(
                        "read_srd_scene_descent_sources",
                        {"target_state": "{_target_state_id}"},
                        ("_slow_descent_sources_found", "_slow_descent_end_tick"),
                    ),
                    _choose(
                        "_slow_descent_sources_found == true",
                        [
                            _set("target_state.scene_fall.status", "'descending'"),
                            _set(
                                "_fall_interval_end",
                                "min(state.lasting_tick, _slow_descent_end_tick)",
                            ),
                            _set(
                                "_fall_elapsed",
                                "max(0, _fall_interval_end - target_state.scene_fall.settled_tick)",
                            ),
                            _set(
                                "target_state.scene_fall.remaining_distance",
                                "max(0, "
                                "target_state.scene_fall.remaining_distance - _fall_elapsed * 30)",
                            ),
                            _set("target_state.scene_fall.settled_tick", "_fall_interval_end"),
                            _choose(
                                "target_state.scene_fall.remaining_distance <= 0",
                                _land("feather_landing"),
                                [
                                    _when(
                                        "state.lasting_tick >= _slow_descent_end_tick",
                                        _land("feather_expired", True),
                                    ),
                                ],
                            ),
                        ],
                        _land("ordinary_fall", True),
                    ),
                ],
            ),
            _set("_fiction_fall_landed", "target_state.scene_fall.status == 'landed'"),
            _call(
                "settle_srd_scene_descent_receipts",
                {
                    "target_state": "{_target_state_id}",
                    "landed": "{_fiction_fall_landed}",
                },
            ),
            _set(
                "_scene_fall_pending",
                "target_state.scene_fall.status == 'pending' or "
                "target_state.scene_fall.status == 'descending'",
            ),
            _set("_scene_fall_status", "target_state.scene_fall.status"),
            _set(
                "_scene_fall_prone_after",
                "target_state.conditions != null and 'prone' in target_state.conditions",
            ),
            _set("_scene_fall_fiction_location", "target_state.scene_fall.current_placement"),
            _set("_scene_fall_remaining_height", "target_state.scene_fall.remaining_distance"),
        ]
    )


def _arrive():
    """Convert only supplied real geometry after pending rider/body admission, preserving clock."""
    return _tool(
        "Admit a saved exploration fall against this exact supplied actual combat body.",
        [_entity("target_state")],
        [
            _when(
                "target_state.scene_fall != null and target_state.scene_fall.mode == '"
                + MODE
                + "' and "
                "(target_state.scene_fall.status == 'pending' or "
                "target_state.scene_fall.status == 'descending')",
                [
                    *_source(),
                    _subject(),
                    *_ground(),
                    *_sweep("_fall_ground_position"),
                    _require(
                        "_info_scene_entity.metadata.plane == target_state.scene_fall.plane and "
                        "abs(_info_position[2] - _fall_ground_height - "
                        "target_state.scene_fall.remaining_distance) <= 0.000000001",
                        "The actual supplied combat body's same-plane ground-relative height must "
                        "match its saved remaining descent.",
                    ),
                    _when(
                        "target_state.scene_fall.event_id != ''",
                        [
                            _set("_fiction_arrival_event", "target_state.scene_fall.event_id"),
                            _set(
                                "_fiction_arrival_window",
                                "state.pending_srd_actions[_fiction_arrival_event]",
                            ),
                            _require(
                                "_fiction_arrival_window.kind == 'scene_fall'",
                                "Preserve this rider's exact saved native fall event at entry.",
                            ),
                            _set(
                                "_fiction_arrival_inputs",
                                "_fiction_arrival_window.parameters.falling_events",
                            ),
                            _set("_fiction_arrival_rows", []),
                            _set("_fiction_arrival_count", 0),
                            _each(
                                "_fiction_arrival_inputs",
                                "_fiction_arrival_row",
                                [
                                    _when(
                                        "_fiction_arrival_row.actor_id == _target_state_id",
                                        [
                                            _require(
                                                "_fiction_arrival_row.mode == '" + MODE + "' and "
                                                "_fiction_arrival_row.steed_id == "
                                                "_fiction_fall_steed",
                                                "Convert only the exact saved fictional "
                                                "fall participant.",
                                            ),
                                            _set(
                                                "_fiction_arrival_row",
                                                {
                                                    "actor_id": "_target_state_id",
                                                    "scene_id": "_info_scene_entity.id",
                                                    "origin": "_info_position",
                                                    "plane": "_info_scene_entity.metadata.plane",
                                                    "ground_id": "_fall_ground_id",
                                                    "ground_position": "_fall_ground_position",
                                                    "distance": (
                                                        "target_state.scene_fall.remaining_distance"
                                                    ),
                                                    "started_tick": (
                                                        "target_state.scene_fall.started_tick"
                                                    ),
                                                    "cause": "target_state.scene_fall.cause",
                                                },
                                            ),
                                            _set(
                                                "_fiction_arrival_count",
                                                "_fiction_arrival_count + 1",
                                            ),
                                        ],
                                    ),
                                    _push("_fiction_arrival_rows", "_fiction_arrival_row"),
                                ],
                            ),
                            _require(
                                "_fiction_arrival_count == 1",
                                "Keep exactly one actual event participant for this rider.",
                            ),
                            _set(
                                "state.pending_srd_actions.{_fiction_arrival_event}.parameters.falling_events",
                                "_fiction_arrival_rows",
                            ),
                        ],
                    ),
                    _set(
                        "target_state.scene_fall",
                        {
                            "event_id": "target_state.scene_fall.event_id",
                            "status": "target_state.scene_fall.status",
                            "started_tick": "target_state.scene_fall.started_tick",
                            "settled_tick": "target_state.scene_fall.settled_tick",
                            "scene_id": "_info_scene_entity.id",
                            "plane": "_info_scene_entity.metadata.plane",
                            "origin": "_info_position",
                            "cause": "target_state.scene_fall.cause",
                        },
                    ),
                ],
            ),
            _require(
                "target_state.scene_fall == null or target_state.scene_fall.status != 'ready'",
                "Complete the actual owning disappearance action before combat entry.",
            ),
        ],
    )


def _declare_state(record):
    artifacts = [
        row for row in record["participant_artifacts"] if row["kind"] == "player_character"
    ]
    if len(artifacts) != 1:
        raise ValueError("Use the actual admitted PC entity-state contract for saved fall mutation")
    artifact = artifacts[0]
    entity = artifact["entity_state_schema"]
    if entity.get("type") != "object" or entity.get("additionalProperties") is not False:
        raise ValueError("Preserve the strict actual PC entity-state contract")
    additions = {
        "scene_fall": SCENE_FALL_SCHEMA,
        "spell_flight_fall_pending": {"type": "boolean"},
        "flying_aloft": {"type": "boolean"},
    }
    for field, schema in additions.items():
        if field in entity.get("required", []):
            raise ValueError("New fall state must remain optional for existing PCs")
        if field in entity["properties"] and entity["properties"][field] != schema:
            raise ValueError("Do not replace another declared native fall-state contract")
        entity["properties"][field] = deepcopy(schema)
    if "schema_sha256" in artifact:
        artifact["schema_sha256"] = participant_schema_sha256(
            artifact["schema"],
            entity_state_schema=entity,
            initial_state_calculations=artifact.get("initial_state_calculations", ()),
            initial_state_validation=artifact.get("initial_state_validation"),
        )


def bind_exploration_fall(runtime, record):
    """Add selected fact/settlement arms while retaining native stage, payment and sources."""
    tools = runtime["tools"]
    if any(name in tools for name in (RELATION, READ_RELATION, ARRIVE)):
        raise ValueError("Exploration Steed fall is already bound")
    changed = {
        name: deepcopy(tools[name])
        for name in (ONE, QUALIFY, BIND, OBSERVE, TARGET, VALIDATE, SETTLE_TARGET, RESOLVE_EVENT)
    }
    changed[ONE]["effects"] = [
        _choose(
            "rider_state.scene_fall != null and rider_state.scene_fall.mode == '" + MODE + "' and "
            "rider_state.scene_fall.status == 'ready'",
            _released(),
            changed[ONE]["effects"],
        )
    ]
    changed[QUALIFY]["effects"] = [
        _choose(
            "target_state.scene_fall != null and target_state.scene_fall.mode == '"
            + MODE
            + "' and "
            "target_state.scene_fall.status == 'ready'",
            _qualify(),
            changed[QUALIFY]["effects"],
        )
    ]
    bind = changed[BIND]
    rows = [
        row
        for row in bind["effects"]
        if row.get("set", {}).get("field") == "target_state.scene_fall"
    ]
    if len(rows) != 1 or rows[0]["set"]["value"].get("origin") != "_bound_row.origin":
        raise ValueError("Keep the genuine checked native fall participant binding")
    index = bind["effects"].index(rows[0])
    fictional = {
        key: "_bound_row." + source
        for key, source in {
            "started_tick": "started_tick",
            "settled_tick": "started_tick",
            "plane": "plane",
            "cause": "cause",
            "steed_id": "steed_id",
            "remaining_distance": "distance",
            "landing_placement": "landing_placement",
            "current_placement": "current_placement",
        }.items()
    }
    fictional.update(event_id="pending_action_id", status="'pending'", mode="'" + MODE + "'")
    bind["effects"][index] = _choose(
        "_bound_row.mode == '" + MODE + "'", [_set("target_state.scene_fall", fictional)], rows
    )
    for name in (OBSERVE, TARGET):
        owner = changed[name]
        owner["parameters"].append(_parameter("reaction_trigger_id", required=False, default=""))
        original = deepcopy(owner["effects"])
        outside = _feather_relation()
        if name == OBSERVE:
            outside += [
                _set(
                    "_fall_trigger_seen",
                    "_fall_trigger_seen and _scene_same_plane == true and _scene_distance <= 60",
                ),
                _require(
                    "target_state.scene_fall.status == 'pending'",
                    "The triggering fall is no longer pending.",
                ),
            ]
        else:
            outside.insert(0, deepcopy(original[0]))
            outside.append(deepcopy(original[-1]))
        owner["effects"] = [
            _choose(
                "target_state.scene_fall != null and target_state.scene_fall.mode == '"
                + MODE
                + "'",
                outside,
                original,
            )
        ]
    for group in _walk(changed[VALIDATE]["effects"]):
        for row in group:
            if row.get("call") in (OBSERVE, TARGET):
                row["params"]["reaction_trigger_id"] = "{reaction_trigger_id}"
    settle = changed[SETTLE_TARGET]
    settle["effects"] = [
        _choose(
            "target_state.scene_fall != null and target_state.scene_fall.mode == '" + MODE + "'",
            _settle(),
            settle["effects"],
        )
    ]
    feedback = changed[RESOLVE_EVENT]
    windows = [
        row["for_each"]["do"]
        for row in feedback["effects"]
        if row.get("for_each", {}).get("collection") == "_resolve_fall_rows"
    ]
    if len(windows) != 1 or windows[0][0].get("call") != SETTLE_TARGET:
        raise ValueError("Keep the original actual fall result-card participant owner")
    windows[0][0]["export_context"].update(
        _scene_fall_prone_after="_scene_fall_prone_after",
        _scene_fall_fiction_location="_scene_fall_fiction_location",
        _scene_fall_remaining_height="_scene_fall_remaining_height",
    )
    windows[0] += _bound(
        [
            _when(
                "_resolve_fall_row.mode == '" + MODE + "'",
                [
                    _when(
                        "_scene_fall_status == 'landed' and _scene_fall_prone_after == true",
                        [
                            _push(
                                "_scene_fall_summary_rows",
                                {"label": "_scene_fall_actor_name", "value": "'Prone'"},
                            ),
                        ],
                    ),
                    _call(
                        "record_srd_result_text",
                        {
                            "text": "{_scene_fall_fiction_location}; "
                            "remaining height {_scene_fall_remaining_height} feet."
                        },
                        ("_recorded_result_text",),
                    ),
                    _push(
                        "_scene_fall_detail_rows",
                        {
                            "label": "'Current location'",
                            "value": "_recorded_result_text",
                        },
                    ),
                ],
            ),
        ]
    )
    changed.update(
        {
            RELATION: _relation(),
            READ_RELATION: _read_relation(),
            ARRIVE: _arrive(),
            NATIVE_ARRIVAL: _native_arrival(),
        }
    )
    _declare_state(record)
    tools.update(changed)
    return list(changed)


def _walk(rows):
    if isinstance(rows, dict):
        rows = [rows]
    yield rows
    for row in rows:
        for key in ("on_true", "on_false"):
            if key in row:
                yield from _walk(row[key])
        if "for_each" in row:
            yield from _walk(row["for_each"]["do"])
