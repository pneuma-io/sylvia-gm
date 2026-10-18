"""Mount the actual saved Otherworldly Steed outside combat using established fiction."""

from copy import deepcopy

from .build_srd_5_2_1_mechanics import _tool_schema
from .srd_control_behaviors import _push
from .srd_lite_steed_fey_step import MOUNT_FIELD, RIDER_FIELD
from .srd_lite_steed_healing_touch import OWNER
from .srd_lite_steed_mount import BUDGET, DISMOUNT, MOUNT
from .srd_timed_actor_defenses import _call, _require, _set, _when
from .srd_timed_senses import _optional

SCOPE = "combat.encounter_active != true"
COST = "read_srd_lite_exploration_mount_cost"
SPEED = "read_srd_lite_exploration_mount_speed"
PLACEMENTS = "exploration_steed_placements"
IDS = "exploration_steed_placement_ids"
CHOICES = (
    ("mount_placement", "string", "", {}),
    ("mount_current_plane", "string", "", {}),
    ("mount_same_plane", "boolean", False, {}),
    ("mount_distance_feet", "number", -1, {"minimum": -1, "maximum": 10000}),
    ("mount_clear_path", "boolean", False, {}),
    ("mount_space_unoccupied", "boolean", False, {}),
    ("dismount_placement", "string", "", {}),
    ("dismount_ground_supported", "boolean", False, {}),
    ("mount_guardian_speed_halved", "boolean", None, {}),
)
GUIDANCE = (
    " Outside combat, supply the actual current mount_placement, mount_current_plane, "
    "mount_same_plane=true, nearest mount_distance_feet within five feet, "
    "mount_clear_path=true and mount_space_unoccupied=true. For dismounting, also name "
    "dismount_placement and establish dismount_ground_supported=true. These current "
    "fictional facts do not create coordinates or an exploration movement budget. "
    "The rider must still be able to spend half their current Speed. Combat continues "
    "to use saved bodies, actual supporting ground and the original movement payment. "
    "If Spirit Guardians is active in the world, supply the current established "
    "mount_guardian_speed_halved fact for this exact rider; an unrelated source does "
    "not halve the rider's Speed."
)


def _parameters():
    return [_optional(name, kind, default, **bounds) for name, kind, default, bounds in CHOICES]


def _speed_tool(runtime):
    """Reuse native Speed and conditions without scene-budget writes or spatial proof."""
    tool = deepcopy(runtime["tools"]["derive_srd_scene_movement_budget"])
    tool["parameters"].append(_optional("mount_guardian_speed_halved", "boolean", None))
    effects = []
    guardian_sites = 0
    for original in tool["effects"]:
        row = deepcopy(original)
        field = row.get("set", {}).get("field", "")
        if field in ("_movement_dashes", "_movement_spent", "_scene_exploration_remaining"):
            continue
        if row.get("condition", "").startswith("actor_state.scene_movement_tick == null"):
            expected = {
                "actor_state.scene_movement_tick",
                "actor_state.scene_movement_spent",
                "actor_state.scene_dashes_taken",
            }
            if {child.get("set", {}).get("field") for child in row["on_true"]} != expected:
                raise ValueError("Keep the actual three exploration movement-counter resets")
            continue
        if row.get("condition") == "actor_state.earthquake_buried == true":
            row = _require(
                "actor_state.earthquake_buried != true",
                "Escape the actual rubble before mounting or dismounting.",
            )
        if row.get("condition") == "lasting_state.has_spirit_guardians == true":
            calls = [child for child in row["on_true"] if child.get("call")]
            if len(calls) != 1 or calls[0]["call"] != "resolve_srd_spirit_guardians_speed":
                raise ValueError(
                    "Preserve the one original position-dependent Guardian speed owner"
                )
            row["on_true"][:1] = [
                _set("_guardian_speed_halved", False),
                _when(
                    "actor_state.spell_freedom_of_movement != true",
                    [
                        _require(
                            "mount_guardian_speed_halved == true or "
                            "mount_guardian_speed_halved == false",
                            "Establish whether active Spirit Guardians halves this rider's Speed.",
                        ),
                        _set("_guardian_speed_halved", "mount_guardian_speed_halved"),
                    ],
                ),
            ]
            guardian_sites += 1
        effects.append(row)
    if guardian_sites != 1 or not any(
        row.get("set", {}).get("field") == "_movement_speed" for row in effects
    ):
        raise ValueError("Keep the native Speed projection and selected Guardian constraint")
    tool["effects"] = [_require(SCOPE, "Read fictional mount Speed only outside combat."), *effects]
    tool["description"] = (
        "Read the actual rider's current native Speed without an exploration map budget."
    )
    return tool


def _guard_rows(native, mounted):
    """Retain selected native ownership/link guards verbatim, excluding geometry guards."""
    selected = []
    prefixes = (
        "lasting_state.pending_srd_action_stack == null",
        "rider_state.is_alive == true",
        "rider_state." + MOUNT_FIELD,
        "rider_state.phantom_steed_mount == null",
        "_rider_state_id == steed_state.created_caster_state_id",
    )
    for row in native:
        condition = row.get("require", {}).get("condition", "")
        if condition.startswith(prefixes) or row.get("call") == OWNER:
            selected.append(deepcopy(row))
    required_count = 6 if mounted else 4
    if len(selected) != required_count or sum(row.get("call") == OWNER for row in selected) != 1:
        raise ValueError(
            "Keep native Steed source, rider, size, loyalty and reciprocal-link guards"
        )
    return selected


def _save_placement(mounted):
    record = "lasting_state." + PLACEMENTS
    index = "lasting_state." + IDS
    return [
        _when(record + " == null", [_set(record, {})]),
        _when(index + " == null", [_set(index, [])]),
        _require(index + " != null", "Preserve the saved pending Steed placement index."),
        _when(
            "_steed_state_id not in " + index,
            [
                _require("len(" + index + ") < 128", "At most 128 pending Steeds are supported."),
                _set("_mount_pending_ids", index),
                _push("_mount_pending_ids", "_steed_state_id"),
                _set(index, "_mount_pending_ids"),
            ],
        ),
        _set(
            record + ".{_steed_state_id}",
            {
                "steed": "_steed_state_id",
                "caster": "steed_state.created_caster_state_id",
                "rider": "_rider_state_id",
                "steed_placement": "mount_placement",
                "rider_placement": "mount_placement" if mounted else "dismount_placement",
                "plane": "mount_current_plane",
                "mounted": mounted,
                "pending": True,
                "tick": "lasting_state.lasting_tick",
            },
        ),
    ]


def _outside(native, mounted):
    return [
        *_guard_rows(native, mounted),
        _require("combat.pending_action == null", "Complete the actual pending action first."),
        _require(
            "len(mount_placement) > 0 and len(mount_placement) <= 240 and "
            "len(mount_current_plane) > 0 and len(mount_current_plane) <= 100",
            "Name the established current Steed placement and plane.",
        ),
        _require(
            "mount_same_plane == true and mount_distance_feet >= 0 and "
            "mount_distance_feet <= 5 and mount_clear_path == true and "
            "mount_space_unoccupied == true",
            "Establish a clear, unoccupied same-plane mount or dismount space within five feet.",
        ),
        _when(
            "lasting_state." + PLACEMENTS + " != null",
            [
                _when(
                    "_steed_state_id in lasting_state." + PLACEMENTS,
                    [
                        _when(
                            "lasting_state."
                            + PLACEMENTS
                            + "[_steed_state_id].pending == true and lasting_state."
                            + PLACEMENTS
                            + "[_steed_state_id].plane != mount_current_plane",
                            [
                                _require(
                                    "steed_state.travel_location.plane == mount_current_plane or "
                                    "steed_state.find_path_current_plane == mount_current_plane",
                                    "Only the exact Steed's genuine current native arrival can "
                                    "supersede its unresolved pending plane.",
                                ),
                                _when(
                                    "lasting_state."
                                    + PLACEMENTS
                                    + "[_steed_state_id].rider == _rider_state_id",
                                    [
                                        _require(
                                            "rider_state.travel_location.plane == "
                                            "mount_current_plane or "
                                            "rider_state.find_path_current_plane == "
                                            "mount_current_plane",
                                            "Preserve this exact pending rider's current native "
                                            "plane arrival too.",
                                        )
                                    ],
                                ),
                            ],
                        )
                    ],
                )
            ],
        ),
        *[
            _when(
                actor + "." + field + " != null",
                [
                    _require(
                        actor + "." + field + " == mount_current_plane",
                        "Preserve the actual rider and Steed's saved current planes.",
                    )
                ],
            )
            for actor in ("steed_state", "rider_state")
            for field in ("travel_location.plane", "find_path_current_plane")
        ],
        *(
            []
            if mounted
            else [
                _require(
                    "len(dismount_placement) > 0 and len(dismount_placement) <= 240 and "
                    "dismount_ground_supported == true",
                    "Dismount to the established unoccupied supported space; airborne dismounts "
                    "remain unsupported.",
                )
            ]
        ),
        _call(
            COST,
            {
                "actor_state": "{_rider_state_id}",
                "mount_guardian_speed_halved": "{mount_guardian_speed_halved}",
            },
            ("_ps_mount_cost",),
        ),
        *_save_placement(mounted),
        _set("rider_state." + MOUNT_FIELD, "_steed_state_id" if mounted else None),
        _set("steed_state." + RIDER_FIELD, "_rider_state_id" if mounted else None),
        _set("lasting_state.otherworldly_steed_movement_receipt", None),
        _set("_steed_mounted", mounted),
        _set("_steed_mount_destination", "mount_placement" if mounted else "dismount_placement"),
        _set(
            "_otherworldly_start_rows",
            [
                {"label": "'Mode'", "value": "'Exploration'"},
                {"label": "'Plane'", "value": "mount_current_plane"},
            ],
        ),
    ]


def bind_exploration_steed_mount(runtime, record):
    """Bind after native Steed mount/turn composition, before exploration actor/reentry hooks."""
    tools = runtime["tools"]
    if any(name in tools for name in (COST, SPEED)):
        raise ValueError("Exploration Steed mounting is already bound")
    tools[SPEED] = _speed_tool(runtime)
    cost = deepcopy(tools[BUDGET])
    cost["parameters"].append(_optional("mount_guardian_speed_halved", "boolean", None))
    cost["effects"] = [
        _require(SCOPE, "Use fictional mount costs only outside combat."),
        *deepcopy(cost["effects"][:2]),
        _call(
            SPEED,
            {
                "actor_state": "{_actor_state_id}",
                "mode": "walk",
                "mount_guardian_speed_halved": "{mount_guardian_speed_halved}",
            },
            ("_movement_speed",),
        ),
        deepcopy(cost["effects"][1]),
        _set("_ps_mount_cost", "floor(_movement_speed * 0.5)"),
        _require("_ps_mount_cost > 0", "The actual rider needs enough Speed to mount or dismount."),
    ]
    tools[COST] = cost
    declarations = {item["name"]: item for item in record["tools"]}
    for name, mounted in ((MOUNT, True), (DISMOUNT, False)):
        owner = tools[name]
        original = deepcopy(owner["effects"])
        outside = _outside(original, mounted)
        owner["parameters"].extend(_parameters())
        combat = original
        if not mounted:
            destination = next(p for p in owner["parameters"] if p["name"] == "destination")
            destination.update(required=False, default=None)
            combat = [
                _require("destination != null", "Combat dismounting needs its actual destination."),
                *original,
            ]
        owner["effects"] = [{"condition": SCOPE, "on_true": outside, "on_false": combat}]
        owner["description"] += GUIDANCE
        card = owner["metadata"]["result_card"]
        cost_rows = [
            row for row in card["summary"] if row["value"].get("path") == "context._ps_mount_cost"
        ]
        if len(cost_rows) != 1:
            raise ValueError("Keep the existing truthful half-Speed mount feedback")
        cost_rows[0]["label"] = "Movement cost (ft)"
        card["details"] = {"path": "context._otherworldly_start_rows"}
        declarations[name]["input_schema"] = _tool_schema(
            owner, declarations[name]["input_schema"], tool_name=name
        )
    return [MOUNT, DISMOUNT, COST, SPEED]
