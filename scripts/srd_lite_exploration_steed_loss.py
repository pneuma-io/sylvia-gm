"""Retire an exploration Steed using fresh facts and the existing native fall queue."""

from copy import deepcopy

from .build_srd_5_2_1_mechanics import _tool_schema
from .srd_control_behaviors import _each, _push
from .srd_lite_exploration_created_actors import _unplaced
from .srd_lite_exploration_fall import (
    ARRIVE,
    bind_exploration_fall,
    check_arrival,
    collect_relations,
    queue_rider,
    read_arrival,
    relation_parameters,
)
from .srd_lite_reachable_tools import _calls
from .srd_lite_steed_disappearance import SETTLE
from .srd_lite_steed_exploration_abilities import (
    ADMIT_RIDER,
    IDS,
    MOUNT_FIELD,
    RECORDS,
    RIDER_FIELD,
    _bound,
    _native_plane,
    _pending_plane,
)
from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _tool, _when

SCOPE = "combat.encounter_active != true"
FACTS = "record_srd_lite_steed_disappearance_facts"
RETIRE = "retire_srd_created_actor"
RELEASE = "release_srd_lite_exploration_steed"
RIDER = "qualify_srd_lite_exploration_disappearance_rider"
UNLINK = "release_srd_lite_exploration_disappearance_rider"
PACKET = "loss_facts"
EMPTY_GUARD = (
    "actor_state.created_steed_rider_id == null and "
    "(actor_state.inventory == null or len(actor_state.inventory) == 0) and "
    "(actor_state.held_items == null or len(actor_state.held_items) == 0)"
)
SHARED = "finish_srd_shared_action_settlements"
PUBLIC_TERMINALS = (
    "death_saving_throw",
    "end_concentration",
    "use_srd_steed_fey_step",
    "resolve_srd_scene_relation",
    "resolve_srd_sight",
    "use_srd_steed_fell_glare",
)


def _completed_loss_boundaries(tools, record):
    """Drain released riders at completed action boundaries, never inside native retirement."""
    public = {row["name"] for row in record["tools"]}
    if not set(PUBLIC_TERMINALS) <= public:
        raise ValueError("Preserve the exact public loss-settlement exception owners")
    terminals = (SHARED, *PUBLIC_TERMINALS)
    graph = {name: set(_calls(owner["effects"])) for name, owner in tools.items()}
    seen, pending = set(), [SETTLE]
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        if name in terminals:
            raise ValueError("A loss-settlement terminal must not reenter itself: " + name)
        if name not in graph:
            raise ValueError("Preserve the actual native loss-settlement callee: " + name)
        seen.add(name)
        pending.extend(graph[name])
    changed = {name: deepcopy(tools[name]) for name in terminals}
    for name, owner in changed.items():
        if SETTLE in set(_calls(owner["effects"])):
            raise ValueError("Keep a single completed loss-settlement tail: " + name)
        if not any(row["name"] == "lasting_state" for row in owner["parameters"]):
            raise ValueError("Preserve the actual saved state identity on the terminal: " + name)
        owner["effects"].append(_call(SETTLE, {"lasting_state": "{_lasting_state_id}"}))
    return changed


def _source(actor, identity):
    """Use the native individual/caster source even during a genuine death callback."""
    return [
        _require(
            actor + ".created_spell_id == 'find_steed' and " + actor + ".created_active == true",
            "Use the actual still-active Find Steed source, including its death callback.",
        ),
        _require(
            "state.created_actor_ids != null and " + identity + " in state.created_actor_ids "
            "and state.created_actor_records != null and "
            + identity
            + " in state.created_actor_records",
            "Keep this Steed's actual saved native source record.",
        ),
        _set("_loss_source", "state.created_actor_records[" + identity + "]"),
        _require(
            "_loss_source.individual == " + identity + " and "
            "_loss_source.spell == 'find_steed' and _loss_source.active == true and "
            "_loss_source.caster == " + actor + ".created_caster_state_id",
            "Preserve the native individual, spell, active source and actual caster.",
        ),
    ]


def _paired_rider():
    """Qualify actual reciprocal links without requiring the already dying rider to live."""
    return _tool(
        "Read the exact reciprocal rider's current exploration disappearance facts.",
        [_entity("steed_state"), _entity("rider_state"), _parameter("plane")],
        [
            _require(
                "steed_state." + RIDER_FIELD + " == _rider_state_id and "
                "rider_state." + MOUNT_FIELD + " == _steed_state_id and "
                "rider_state.size_rank < steed_state.size_rank",
                "Preserve the exact native smaller rider and reciprocal Steed links.",
            ),
            *_native_plane("rider_state", "plane"),
            _when(
                "state." + RECORDS + " != null and _steed_state_id in state." + RECORDS,
                [
                    _set("_loss_previous", "state." + RECORDS + "[_steed_state_id]"),
                    _when(
                        "_loss_previous.pending == true and "
                        "_loss_previous.rider == _rider_state_id and "
                        "_loss_previous.plane != plane",
                        _pending_plane("rider_state", "plane"),
                    ),
                ],
            ),
            *read_arrival("rider_state", "_loss_rider_arrival"),
            _set("_loss_rider_name", "rider_state.name"),
        ],
    )


def _cargo(actor):
    """Keep real carried and held item identities, including genuine held-only items."""
    return [
        _set("_loss_cargo", []),
        *[
            _when(
                actor + "." + field + " != null",
                [
                    _set("_loss_items", actor + "." + field),
                    _each(
                        "_loss_items",
                        "_loss_item",
                        [
                            _when(
                                "_loss_item not in _loss_cargo",
                                [_push("_loss_cargo", "_loss_item")],
                            )
                        ],
                    ),
                ],
            )
            for field in ("inventory", "held_items")
        ],
    ]


def _saved_items(actor):
    """Keep absent optional inventories as real empty arrays, never unresolved references."""
    return [
        effect
        for field in ("inventory", "held_items")
        for effect in (
            _set("_loss_" + field, []),
            _when(actor + "." + field + " != null", [_set("_loss_" + field, actor + "." + field)]),
        )
    ]


def _check_items():
    """Require the same ordered scalar item identities without comparing whole lists."""
    return [
        effect
        for field in ("inventory", "held_items")
        for effect in (
            _set("_loss_current_items", []),
            _when(
                "actor_state." + field + " != null",
                [_set("_loss_current_items", "actor_state." + field)],
            ),
            _set("_loss_saved_items", "_loss_packet." + field),
            _require(
                "len(_loss_current_items) == len(_loss_saved_items)",
                "Refresh facts after the actual carried item count changes.",
            ),
            _set("_loss_item_index", 0),
            _each(
                "_loss_current_items",
                "_loss_current_item",
                [
                    _require(
                        "_loss_current_item == _loss_saved_items[_loss_item_index]",
                        "Refresh facts after the actual carried item identities change.",
                    ),
                    _set("_loss_item_index", "_loss_item_index + 1"),
                ],
            ),
        )
    ]


def _facts():
    """Declare current support immediately before loss; never infer it from an old map."""
    return _tool(
        "Immediately before a Find Steed disappearance, establish its actual current "
        "placement/plane, rider height/clear descent and actual landing/cargo drop. "
        "This one-shot record is "
        "valid only at this native clock tick with these exact rider/item/native-arrival facts. "
        "A positive remaining fall uses the real saved fall/Feather Fall Reaction, paid casting "
        "and native protection clock. Declare current caster/target range and sight pairs for "
        "other Feather Fall casters; self needs no invented pair. "
        "This records facts without costs.",
        [
            _entity("steed_state"),
            _parameter("established_steed_placement"),
            _parameter("established_plane"),
            _parameter("established_landing_supported", "boolean"),
            _parameter("established_rider_landing", required=False, default=""),
            _parameter("established_cargo_drop", required=False, default=""),
            _parameter(
                "established_rider_fall_distance",
                "number",
                required=False,
                default=-1,
                minimum=-1,
            ),
            *relation_parameters(),
        ],
        [
            _require(SCOPE, "Record fictional disappearance facts only outside combat."),
            _require(
                "state.lasting_tick != null and state.lasting_tick >= 0",
                "Use the actual initialized native clock for one-shot current loss facts.",
            ),
            *_source("steed_state", "_steed_state_id"),
            _require(
                "len(established_steed_placement) > 0 and "
                "len(established_steed_placement) <= 240 and "
                "len(established_plane) > 0 and len(established_plane) <= 100 and "
                "established_landing_supported == true",
                "Name the current same-plane Steed location and genuine supported landing.",
            ),
            *_native_plane("steed_state", "established_plane"),
            _when(
                "state." + RECORDS + " != null and _steed_state_id in state." + RECORDS,
                [
                    _set("_loss_previous", "state." + RECORDS + "[_steed_state_id]"),
                    _when(
                        "_loss_previous.pending == true and "
                        "_loss_previous.plane != established_plane",
                        _pending_plane("steed_state", "established_plane"),
                    ),
                ],
            ),
            _set("_loss_rider_id", "''"),
            _set("_loss_rider_name", "''"),
            _set("_loss_rider_arrival", None),
            _when(
                "steed_state." + RIDER_FIELD + " != null and steed_state." + RIDER_FIELD + " != ''",
                [
                    _set("_loss_rider_id", "steed_state." + RIDER_FIELD),
                    _require(
                        "len(established_rider_landing) > 0 and "
                        "len(established_rider_landing) <= 240 and "
                        "established_rider_fall_distance >= 0",
                        "Establish the actual landing and remaining fall distance "
                        "from current facts.",
                    ),
                    _when(
                        "established_rider_fall_distance > 0",
                        [
                            _require(
                                "established_clear_descent == true and "
                                "len(established_rider_current_placement) > 0 and "
                                "len(established_rider_current_placement) <= 240",
                                "Name the actual current aloft rider location and "
                                "clear supported descent.",
                            ),
                        ],
                    ),
                    _call(
                        RIDER,
                        {
                            "steed_state": "{_steed_state_id}",
                            "rider_state": "{_loss_rider_id}",
                            "plane": "{established_plane}",
                        },
                        ("_loss_rider_arrival", "_loss_rider_name"),
                    ),
                ],
            ),
            *collect_relations(),
            *_cargo("steed_state"),
            *_saved_items("steed_state"),
            *read_arrival("steed_state", "_loss_steed_arrival"),
            _when(
                "len(_loss_cargo) > 0",
                [
                    _require(
                        "len(established_cargo_drop) > 0 and len(established_cargo_drop) <= 240",
                        "Name the actual current cargo drop; do not assume its location.",
                    )
                ],
            ),
            _require(
                "state."
                + IDS
                + " == null or _steed_state_id in state."
                + IDS
                + " or len(state."
                + IDS
                + ") < 128",
                "The pending Steed bound is full.",
            ),
            _when("state." + IDS + " == null", [_set("state." + IDS, [])]),
            _when("state." + RECORDS + " == null", [_set("state." + RECORDS, {})]),
            _when(
                "_steed_state_id not in state." + IDS,
                [
                    _require("len(state." + IDS + ") < 128", "The pending Steed bound is full."),
                    _set("_loss_ids", "state." + IDS),
                    _push("_loss_ids", "_steed_state_id"),
                    _set("state." + IDS, "_loss_ids"),
                ],
            ),
            _set(
                "state." + RECORDS + ".{_steed_state_id}",
                {
                    "steed": "_steed_state_id",
                    "caster": "steed_state.created_caster_state_id",
                    "rider": "_loss_rider_id",
                    "steed_placement": "established_steed_placement",
                    "rider_placement": "established_rider_landing",
                    "plane": "established_plane",
                    "mounted": "_loss_rider_id != ''",
                    "pending": True,
                    "tick": "state.lasting_tick",
                    PACKET: {
                        "tick": "state.lasting_tick",
                        "rider": "_loss_rider_id",
                        "inventory": "_loss_inventory",
                        "held_items": "_loss_held_items",
                        "steed_arrival": "_loss_steed_arrival",
                        "rider_arrival": "_loss_rider_arrival",
                        "ground_supported": "established_landing_supported",
                        "remaining_fall": "established_rider_fall_distance",
                        "cargo_placement": "established_cargo_drop",
                        "rider_current_placement": "established_rider_current_placement",
                        "feather_relations": "_loss_feather_relations",
                    },
                },
            ),
        ],
    )


def _unlink():
    return _tool(
        "Release this exact reciprocal exploration rider without changing HP or alive state.",
        [_entity("steed_state"), _entity("rider_state")],
        [
            _set("_loss_record", "state." + RECORDS + "[_steed_state_id]"),
            _require(
                "_loss_record.rider == _rider_state_id and "
                "steed_state." + RIDER_FIELD + " == _rider_state_id and "
                "rider_state." + MOUNT_FIELD + " == _steed_state_id",
                "Release only the exact current reciprocal rider, without a fabricated recipient.",
            ),
            *check_arrival(
                "rider_state",
                "_loss_record.loss_facts.rider_arrival",
                "Refresh loss facts after this actual rider's genuine later native travel.",
            ),
            *_native_plane("rider_state", "_loss_record.plane"),
            *queue_rider(),
            _set("steed_state." + RIDER_FIELD, None),
            _set("rider_state." + MOUNT_FIELD, None),
            _set("_loss_rider_name", "rider_state.name"),
        ],
    )


def _release():
    """Consume current support/fall facts and actual items, then finish native retirement."""
    return _tool(
        "Resolve this current mapless Find Steed disappearance using its real native fall queue.",
        [_entity("actor_state"), _parameter("reason")],
        [
            _require(SCOPE, "Resolve fictional release only outside combat."),
            *_source("actor_state", "_actor_state_id"),
            _require(
                "state." + RECORDS + " != null and _actor_state_id in state." + RECORDS,
                "Immediately establish this Steed's current exploration disappearance facts.",
            ),
            _set("_loss_record", "state." + RECORDS + "[_actor_state_id]"),
            _set("_loss_packet", "_loss_record." + PACKET),
            _require(
                "_loss_record.steed == _actor_state_id and "
                "_loss_record.caster == actor_state.created_caster_state_id and "
                "_loss_record.pending == true and _loss_packet != null and "
                "_loss_packet.tick == state.lasting_tick and _loss_packet.ground_supported == true",
                "Use only this exact source's one-shot current supported loss facts at this tick.",
            ),
            *_check_items(),
            *check_arrival(
                "actor_state",
                "_loss_packet.steed_arrival",
                "Refresh facts after the actual Steed's native travel changes.",
            ),
            *_native_plane("actor_state", "_loss_record.plane"),
            _set("_loss_current_rider", "''"),
            _when(
                "actor_state." + RIDER_FIELD + " != null",
                [_set("_loss_current_rider", "actor_state." + RIDER_FIELD)],
            ),
            _require(
                "_loss_record.rider == _loss_current_rider and "
                "_loss_packet.rider == _loss_current_rider and "
                "_loss_record.mounted == (_loss_current_rider != '')",
                "Refresh facts after the actual mounted pair changes.",
            ),
            *_cargo("actor_state"),
            _when(
                "len(_loss_cargo) > 0",
                [
                    _require(
                        "len(_loss_packet.cargo_placement) > 0 and "
                        "len(_loss_packet.cargo_placement) <= 240",
                        "Preserve the explicit actual cargo drop before releasing the rider.",
                    )
                ],
            ),
            _set("_loss_rider_name", "''"),
            _when(
                "actor_state." + RIDER_FIELD + " != null and actor_state." + RIDER_FIELD + " != ''",
                [
                    _require(
                        "_loss_record.rider == actor_state." + RIDER_FIELD + " and "
                        "_loss_packet.rider == actor_state." + RIDER_FIELD + " and "
                        "_loss_record.mounted == true and _loss_packet.remaining_fall >= 0",
                        "Keep the actual current rider and genuine declared remaining fall.",
                    ),
                    _call(
                        UNLINK,
                        {
                            "steed_state": "{_actor_state_id}",
                            "rider_state": "{actor_state." + RIDER_FIELD + "}",
                        },
                        ("_loss_rider_name",),
                    ),
                    _set("state." + RECORDS + ".{_actor_state_id}.released_by_disappearance", True),
                ],
            ),
            _set("_loss_drops", []),
            _when(
                "state.created_actor_dropped_items != null",
                [_set("_loss_drops", "state.created_actor_dropped_items")],
            ),
            _each(
                "_loss_cargo",
                "_loss_item",
                [
                    _push(
                        "_loss_drops",
                        {
                            "item_id": "_loss_item",
                            "placement": "_loss_packet.cargo_placement",
                            "plane": "_loss_record.plane",
                            "former_carrier": "_actor_state_id",
                        },
                    )
                ],
            ),
            _set("state.created_actor_dropped_items", "_loss_drops"),
            _set("actor_state.inventory", []),
            _set("actor_state.held_items", []),
            _set("state." + RECORDS + ".{_actor_state_id}.mounted", False),
            _set("state." + RECORDS + ".{_actor_state_id}.disappearance", "reason"),
            _set("state." + RECORDS + ".{_actor_state_id}." + PACKET, None),
            _set("_loss_cargo_count", "len(_loss_cargo)"),
            _set("_loss_rider_landing", "_loss_record.rider_placement"),
            _set("_loss_cargo_landing", "_loss_packet.cargo_placement"),
            _set("_loss_current_plane", "_loss_record.plane"),
        ],
    )


def _bind_dead_rider(owner):
    """Admit a real deceased released rider only after this exact native source retired."""
    expected = (
        "rider_state.is_alive == true and rider_state.hp >= 1 and "
        "rider_state.size_rank < steed_state.size_rank"
    )
    rows = [row for row in owner["effects"] if row.get("require", {}).get("condition") == expected]
    if len(rows) != 1:
        raise ValueError("Keep the original smaller living pending-rider admission guard")
    index = owner["effects"].index(rows[0])
    owner["effects"][index : index + 1] = _bound(
        [
            _set("_loss_admit_released_dead", False),
            _when(
                "_step_record.mounted == false and _step_record.released_by_disappearance == true "
                "and steed_state.created_active == false and rider_state.is_alive == false",
                [
                    _set("_loss_admit_source", "state.created_actor_records[_steed_state_id]"),
                    _require(
                        "_step_record.steed == _steed_state_id and "
                        "_step_record.rider == _rider_state_id and "
                        "_step_record.caster == steed_state.created_caster_state_id and "
                        "_loss_admit_source.individual == _steed_state_id and "
                        "_loss_admit_source.caster == steed_state.created_caster_state_id and "
                        "_loss_admit_source.spell == 'find_steed' and "
                        "_loss_admit_source.active == false",
                        "A deceased released rider requires the exact genuine "
                        "inactive Steed source.",
                    ),
                    _require(
                        "len(_step_record.disappearance) > 0 and "
                        "_step_record.disappearance == steed_state.created_disappearance and "
                        "_loss_admit_source.disappearance == steed_state.created_disappearance",
                        "Keep the genuine native disappearance cause on the source "
                        "and pending release.",
                    ),
                    _set("_loss_admit_released_dead", True),
                ],
            ),
            _require(
                "rider_state.size_rank < steed_state.size_rank and "
                "((rider_state.is_alive == true and rider_state.hp >= 1) or "
                "_loss_admit_released_dead == true)",
                "Admit the native smaller living rider or its exact genuine deceased loss release.",
            ),
        ]
    )


def bind_exploration_steed_loss(runtime, record):
    """Extend only the selected scene-free Steed retirement; retain combat retirement verbatim."""
    tools = runtime["tools"]
    if any(name in tools for name in (FACTS, RELEASE, RIDER, UNLINK)):
        raise ValueError("Exploration Steed loss is already bound")
    retire, admit = deepcopy(tools[RETIRE]), deepcopy(tools[ADMIT_RIDER])
    branches = [
        row
        for row in retire["effects"]
        if "on_true" in row
        and any(
            child.get("require", {}).get("condition") == EMPTY_GUARD for child in row["on_true"]
        )
    ]
    if len(branches) != 1:
        raise ValueError("Keep the exact selected exploration Steed retirement branch")
    branch = branches[0]
    if not branch["condition"].startswith(
        "actor_state.created_spell_id == 'find_steed' and actor_state.created_active == true and "
    ):
        raise ValueError("Keep the genuine Find Steed-only scene-free retirement source guard")
    # Split this selected geometry predicate so every runtime expression remains under 500 chars.
    index = retire["effects"].index(branch)
    retire["effects"][index:index] = _bound(
        [
            _set("_loss_nonspatial_retire", False),
            _when(SCOPE, [_set("_loss_nonspatial_retire", True)]),
            _when(_unplaced("_actor_state_id"), [_set("_loss_nonspatial_retire", True)]),
        ]
    )
    branch["condition"] = (
        "actor_state.created_spell_id == 'find_steed' and actor_state.created_active == true "
        "and _loss_nonspatial_retire == true"
    )
    index = next(
        i
        for i, row in enumerate(branch["on_true"])
        if row.get("require", {}).get("condition") == EMPTY_GUARD
    )
    branch["on_true"][index:index] = _bound(
        [
            _when(
                "actor_state." + RIDER_FIELD + " == ''",
                [_set("actor_state." + RIDER_FIELD, None)],
            ),
            _when(
                "actor_state." + RIDER_FIELD + " != null or "
                "(actor_state.inventory != null and len(actor_state.inventory) > 0) or "
                "(actor_state.held_items != null and len(actor_state.held_items) > 0)",
                [_call(RELEASE, {"actor_state": "{_actor_state_id}", "reason": "{reason}"})],
            ),
        ]
    )
    _bind_dead_rider(admit)
    changed = {
        RETIRE: retire,
        ADMIT_RIDER: admit,
        FACTS: _facts(),
        RELEASE: _release(),
        RIDER: _paired_rider(),
        UNLINK: _unlink(),
    }
    changed.update(_completed_loss_boundaries(tools, record))
    declarations = deepcopy(record["tools"])
    if any(row["name"] == FACTS for row in declarations):
        raise ValueError("Keep a single current disappearance-fact public declaration")
    declarations.append(
        {
            "name": FACTS,
            "capability": "dnd.srd.5.2.1.spells",
            "writers": ["dm"],
            "turn_statuses": ["open"],
            "state_predicates": [],
            "input_schema": _tool_schema(changed[FACTS], tool_name=FACTS),
        }
    )
    tools.update(changed)
    record["tools"] = declarations
    # The completed public/shared boundaries drain the queue after native retirement returns.
    fall_changed = bind_exploration_fall(runtime, record)
    placement = tools["reconcile_srd_lite_exploration_steed_placement"]
    if any(row.get("call") == ARRIVE for row in placement["effects"]):
        raise ValueError("Keep a single native scene admission of the saved fictional fall")
    placement["effects"] += _bound(
        [
            _when(
                "_step_record.rider != ''",
                [
                    _call(ARRIVE, {"target_state": "{_step_record.rider}"}),
                ],
            ),
        ]
    )
    return [*changed, *fall_changed, "reconcile_srd_lite_exploration_steed_placement"]
