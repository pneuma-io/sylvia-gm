"""Source-linked travel spells mutate only the admitted scene's positions and planes."""

import re
from copy import deepcopy

import yaml

if __package__:
    from .srd_scene_relations import _array, _tool_scene
    from .srd_spell_travel_banquet import banquet_intent, banquet_tools
    from .srd_spell_travel_blink import bind_blink_boundaries, blink_tools
    from .srd_spell_travel_chest import bind_chest_consumers, chest_tools
    from .srd_spell_travel_circle import (
        CIRCLE_CAST_CHOICES,
        bind_circle_cast_receipts,
        bind_circle_consumers,
        bind_circle_study,
        circle_tools,
    )
    from .srd_spell_travel_demiplane import bind_demiplane_consumers, demiplane_tools
    from .srd_spell_travel_dwelling import bind_dwelling_consumers, dwelling_tools
    from .srd_spell_travel_exile import bind_exile_lifecycle, exile_tools
    from .srd_spell_travel_furnishings import (
        bind_furnishing_consumers,
        furnishing_intent,
        furnishing_tools,
    )
    from .srd_spell_travel_gate import bind_gate_consumers, gate_tools
    from .srd_spell_travel_gate_transit import gate_transit_tools
    from .srd_spell_travel_levitate import bind_levitate_consumers, levitate_tools
    from .srd_spell_travel_levitate_control import control_tools
    from .srd_spell_travel_levitate_grip import bind_grip_consumers, grip_tools
    from .srd_spell_travel_passwall import bind_passwall_lifecycle, passwall_tools
    from .srd_spell_travel_rope import bind_rope_consumers, rope_tools
    from .srd_spell_travel_rope_state import bind_rope_return_consumers, rope_state_tools
    from .srd_spell_travel_servants import bind_servant_consumers, servant_intent, servant_tools
    from .srd_spell_travel_telekinesis import bind_telekinesis_consumers, telekinesis_tools
    from .srd_spell_travel_teleport_objects import (
        OBJECT_CAST_CHOICES,
        bind_teleport_object_tools,
    )
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _RuntimeDumper,
        _set,
        _tool,
        _when,
    )
    from .srd_timed_senses import _lookup, _loop, _optional, _public, _push, _replace_tools
else:
    from srd_scene_relations import _array, _tool_scene
    from srd_spell_travel_banquet import banquet_intent, banquet_tools
    from srd_spell_travel_blink import bind_blink_boundaries, blink_tools
    from srd_spell_travel_chest import bind_chest_consumers, chest_tools
    from srd_spell_travel_circle import (
        CIRCLE_CAST_CHOICES,
        bind_circle_cast_receipts,
        bind_circle_consumers,
        bind_circle_study,
        circle_tools,
    )
    from srd_spell_travel_demiplane import bind_demiplane_consumers, demiplane_tools
    from srd_spell_travel_dwelling import bind_dwelling_consumers, dwelling_tools
    from srd_spell_travel_exile import bind_exile_lifecycle, exile_tools
    from srd_spell_travel_furnishings import (
        bind_furnishing_consumers,
        furnishing_intent,
        furnishing_tools,
    )
    from srd_spell_travel_gate import bind_gate_consumers, gate_tools
    from srd_spell_travel_gate_transit import gate_transit_tools
    from srd_spell_travel_levitate import bind_levitate_consumers, levitate_tools
    from srd_spell_travel_levitate_control import control_tools
    from srd_spell_travel_levitate_grip import bind_grip_consumers, grip_tools
    from srd_spell_travel_passwall import bind_passwall_lifecycle, passwall_tools
    from srd_spell_travel_rope import bind_rope_consumers, rope_tools
    from srd_spell_travel_rope_state import bind_rope_return_consumers, rope_state_tools
    from srd_spell_travel_servants import bind_servant_consumers, servant_intent, servant_tools
    from srd_spell_travel_telekinesis import bind_telekinesis_consumers, telekinesis_tools
    from srd_spell_travel_teleport_objects import (
        OBJECT_CAST_CHOICES,
        bind_teleport_object_tools,
    )
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _RuntimeDumper,
        _set,
        _tool,
        _when,
    )
    from srd_timed_senses import _lookup, _loop, _optional, _public, _push, _replace_tools


SOURCE = {
    "gate": (135, "Gate", "Geas"),
    "secret_chest": (160, "Secret Chest", "See Invisibility"),
    "rope_trick": (159, "Rope Trick", "Sacred Flame"),
    "magnificent_mansion": (147, "Magnificent Mansion", "Major Image"),
    "banishment": (112, "Banishment", "Barkskin"),
    "maze": (148, "Maze", "Meld into Stone"),
    "blink": ([113, 114], "Blink", "Blur"),
    "misty_step": (150, "Misty Step", "Modify Memory"),
    "dimension_door": ([123, 124], "Dimension Door", "Disguise Self"),
    "plane_shift": (153, "Plane Shift", "Plant Growth"),
    "word_of_recall": (175, "Word of Recall", "Zone of Truth"),
    "transport_via_plants": (170, "Transport via Plants", "Tree Stride"),
    "tree_stride": (170, "Tree Stride", "True Polymorph"),
    "teleportation_circle": ([168, 169], "Teleportation Circle", "Thaumaturgy"),
    "etherealness": ([128, 129], "Etherealness", "Expeditious Retreat"),
    "teleport": (168, "Teleport", "Teleportation Circle"),
    "passwall": (151, "Passwall", "Pass without Trace"),
    "levitate": (144, "Levitate", "Light"),
    "telekinesis": ([167, 168], "Telekinesis", "Telepathic Bond"),
}
PROFILES = {}
for _id, _level, _time, _kind, _feet, _rounds, _concentration in (
    ("gate", 9, "action", "distance", 60, 10, True),
    ("secret_chest", 4, "action", "touch", 0, 0, False),
    ("rope_trick", 2, "action", "touch", 0, 600, False),
    ("banishment", 4, "action", "distance", 30, 10, True),
    ("maze", 8, "action", "distance", 60, 100, True),
    ("blink", 3, "action", "self", 0, 10, False),
    ("misty_step", 2, "bonus_action", "self", 0, 0, False),
    ("dimension_door", 4, "action", "distance", 500, 0, False),
    ("plane_shift", 7, "action", "touch", 0, 0, False),
    ("word_of_recall", 6, "action", "distance", 5, 0, False),
    ("transport_via_plants", 6, "action", "distance", 10, 10, False),
    ("tree_stride", 5, "action", "self", 0, 10, True),
    ("teleportation_circle", 5, "1_minute", "distance", 10, 1, False),
    ("etherealness", 7, "action", "self", 0, 4800, False),
    ("teleport", 7, "action", "distance", 10, 0, False),
    ("demiplane", 8, "action", "distance", 60, 600, False),
    ("passwall", 5, "action", "distance", 30, 600, False),
    ("levitate", 2, "action", "distance", 60, 100, True),
    ("telekinesis", 5, "action", "distance", 60, 100, True),
    ("magnificent_mansion", 7, "1_minute", "distance", 300, 14400, False),
):
    PROFILES[_id] = {
        "family": "travel_spell",
        "base_level": _level,
        "casting_time": _time,
        "range_kind": _kind,
        "range_feet": _feet,
        "effect_rounds": _rounds,
        "concentration": _concentration,
    }
    if _rounds:
        PROFILES[_id]["duration_rounds"] = _rounds

PUBLIC_TOOLS = {
    "learn_srd_initial_circle_sigils",
    "traverse_srd_gate",
    "look_through_srd_gate",
    "inspect_srd_gate",
    "command_srd_mansion_servant",
    "eat_srd_mansion_banquet",
    "record_srd_secret_chest_components",
    "use_srd_secret_chest",
    "record_srd_rope_length",
    "traverse_srd_rope_trick",
    "climb_srd_rope_trick",
    "manipulate_srd_rope_trick_rope",
    "operate_srd_mansion_door",
    "traverse_srd_dwelling_door",
    "pour_srd_telekinesis_vial",
    "operate_srd_telekinesis_object",
    "record_srd_telekinesis_object_size",
    "use_srd_telekinesis",
    "record_srd_levitate_surface_grip",
    "move_srd_levitate_grip",
    "adjust_srd_levitate_height",
    "record_srd_levitate_object_looseness",
    "record_srd_demiplane_room_knowledge",
    "traverse_srd_demiplane_door",
    "choose_srd_demiplane_shunt",
    "record_srd_banishment_plane",
    "study_srd_maze_escape",
    "choose_srd_blink_return_point",
    "traverse_srd_plant_link",
    "traverse_srd_tree_stride",
    "enter_srd_teleportation_circle",
    "study_srd_circle_sigil",
    "dismiss_srd_etherealness",
    "record_srd_teleport_destination",
    "record_srd_teleport_similar_area",
    "continue_srd_teleport_resolution",
}
CAST_CHOICES = [
    *CIRCLE_CAST_CHOICES,
    _optional("gate_id", default=""),
    _optional("gate_destination_subject", default=""),
    _optional("gate_diameter", "number", 5, minimum=5, maximum=20),
    _optional("gate_front", "enum", "up", enum_values=["up", "down"]),
    _optional("gate_depiction_depth", "number", 0.25, minimum=0.000001),
    _optional("secret_chest_id", default=""),
    _optional("secret_chest_replica", default=""),
    _optional("secret_chest_ethereal_anchor", default=""),
    _optional("rope_trick_rope", default=""),
    _optional("rope_trick_id", default=""),
    _optional("rope_trick_floor_cells", "array", [[0, 0, 0]],
              items={"type": "array", "items": {"type": "integer"}}, min_items=1, max_items=50),
    _optional("rope_trick_wall_thickness", "number", 0.25, minimum=0.001, maximum=1),
    _optional("rope_trick_door_depth", "number", 0.25, minimum=0.001, maximum=1),
    _optional("rope_trick_lighting", "enum", "bright", enum_values=["bright", "dim", "darkness"]),
    _optional("mansion_id", default=""),
    _optional("mansion_designated", "array", [], items={"type": "string"}, max_items=100),
    _optional("mansion_floor_cells", "array", [[0, 0, 0]],
              items={"type": "array", "items": {"type": "integer"}}, min_items=1, max_items=50),
    _optional("mansion_wall_thickness", "number", 0.25, minimum=0.001, maximum=1),
    _optional("mansion_door_depth", "number", 0.25, minimum=0.001, maximum=1),
    _optional("mansion_lighting", "enum", "bright", enum_values=["bright", "dim", "darkness"]),
    furnishing_intent("mansion_furnishings"),
    banquet_intent("mansion_banquet"),
    servant_intent("mansion_servants"),
    _optional("telekinesis_subject", default=""),
    _optional("telekinesis_operation", "enum", "move",
              enum_values=["move", "open", "close", "pour"]),
    _optional("telekinesis_destination", "array", [0, 0, 0],
              items={"type": "number"}, min_items=3, max_items=3),
    _optional("levitate_subject", default=""),
    _optional("levitate_rise", "number", 20, minimum=0, maximum=20),
    _optional(
        "travel_destination", "array", [0, 0, 0], items={"type": "number"}, min_items=3, max_items=3
    ),
    _optional(
        "travel_companion_destination",
        "array",
        [0, 0, 0],
        items={"type": "number"},
        min_items=3,
        max_items=3,
    ),
    _optional("travel_origin_subject", default=""),
    _optional("travel_destination_subject", default=""),
    *OBJECT_CAST_CHOICES,
    _optional("travel_prepare_sanctuary", "boolean", False),
    _optional("passwall_surface_id", default=""),
    _optional("passwall_face", "enum", "west",
              enum_values=["east", "west", "north", "south", "up", "down"]),
    _optional("passwall_width", "number", 5, minimum=0, maximum=5),
    _optional("passwall_height", "number", 8, minimum=0, maximum=8),
    _optional("passwall_depth", "number", 20, minimum=0, maximum=20),
    _optional("demiplane_surface_id", default=""),
    _optional("demiplane_face", "enum", "west",
              enum_values=["east", "west", "north", "south", "up", "down"]),
    _optional("demiplane_room", default=""),
    _optional("demiplane_material", "enum", "stone", enum_values=["wood", "stone"]),
    _optional("demiplane_wall_thickness", "number", 0.25, minimum=0.001),
    _optional("demiplane_door_width", "number", 5, minimum=0.001),
    _optional("demiplane_door_height", "number", 8, minimum=0.001),
    _optional("demiplane_door_depth", "number", 0.25, minimum=0.001),
]
ACTIVE = (
    "actor_state.is_alive == true and 'incapacitated' not in actor_state.conditions and "
    "'paralyzed' not in actor_state.conditions and 'petrified' not in "
    "actor_state.conditions and 'stunned' not in actor_state.conditions and "
    "'unconscious' not in actor_state.conditions"
)


def validate_profiles(definitions):
    """Reject drift between admitted records and the reviewed fixed SRD profiles."""
    for spell, definition in definitions.items():
        if definition.get("family") == "travel_spell" and definition != PROFILES.get(spell):
            raise SystemExit(f"Travel source profile differs: {spell}")


def _scene_subject(subject, local):
    return [
        _call(
            "srd_information_scene_entity",
            {"subject": subject},
            ["_info_scene_entity", "_info_position"],
        ),
        _set(local, "_info_scene_entity"),
    ]


def _measure(start, end, local):
    return [
        _call(
            "measure_srd_information_distance", {"start": start, "end": end}, ["_scene_distance"]
        ),
        _set(local, "_scene_distance"),
    ]


def _placement(subject, destination, plane, exclusions="{_travel_excluded}", reference=None):
    """Bind actual source identity to the shared placement owner without caller verdicts."""
    name = "resolve_srd_travel_placement"
    params = {
        "subject": subject, "destination": destination, "plane": plane, "excluded": exclusions,
    }
    if reference is not None:
        kind, identity, face = reference
        if kind not in {"geometry", "entity"}:
            raise ValueError("Placement references must name actual geometry or entity owners")
        name = "resolve_srd_travel_" + kind + "_face_placement"
        params.update(reference_id=identity, reference_face=face)
    return [_call(name, params, ["_travel_placement"])]


def _move(subject, destination, plane, teleport=True):
    return [
        _call(
            "move_srd_travel_subject",
            {"subject": subject, "destination": destination, "plane": plane, "teleport": teleport},
        )
    ]


def _movement_cost(amount):
    return [
        _call("spend_srd_travel_movement", {"actor_state": "{_actor_state_id}", "amount": amount})
    ]


def _tools():
    placement = _tool_scene(
        (
            "Derive exact nearest clear placement from admitted geometry, footprint and "
            "actual plane."
        ),
        [
            _parameter("subject"),
            _array("destination"),
            _parameter("plane"),
            _parameter("excluded", "array", items={"type": "string"}),
        ],
        _scene_subject("{subject}", "_travel_subject")
        + [
            _require("plane != ''", "Travel needs the destination's actual plane."),
            _set("_travel_missing_matches", "plane == 'material'"),
            {
                "spatial_query": {
                    "scene": "state.spatial_scene",
                    "operation": "placement",
                    "query": {
                        "map_id": "{state.spatial_scene.map.id}",
                        "moving_entity_id": "{_travel_subject.id}",
                        "origin": "{destination}",
                        "exclude_entity_ids": "{excluded}",
                        "metadata_filter": {
                            "key": "plane",
                            "value": "{plane}",
                            "missing_matches": "{_travel_missing_matches}",
                        },
                        "max_candidates": 10000,
                    },
                },
                "store_as": "_travel_placement",
            },
            _require(
                "_travel_placement.supported == true",
                "Exact travel placement is unsupported for curved solids or this candidate bound.",
            ),
            _require(
                "_travel_placement.found == true",
                "The admitted scene has no unoccupied destination space.",
            ),
        ],
    )
    face_placements = {}
    for kind in ("geometry", "entity"):
        face_placement = deepcopy(placement)
        face_placement["description"] = (
            "Derive nearest clear placement to an actual saved source box face."
        )
        face_placement["parameters"] += [
            _parameter("reference_id"),
            _parameter("reference_face", "enum",
                       enum_values=["east", "west", "north", "south", "up", "down"]),
        ]
        for effect in face_placement["effects"]:
            if effect.get("spatial_query", {}).get("operation") == "placement":
                effect["spatial_query"]["query"]["reference_face"] = {
                    "target": {"kind": kind, "id": "{reference_id}"}, "face": "{reference_face}",
                }
        face_placements["resolve_srd_travel_" + kind + "_face_placement"] = face_placement
    move = _tool_scene(
        (
            "Commit travel through the sole scene-position authority and refresh later "
            "spatial consumers."
        ),
        [
            _parameter("subject"),
            _array("destination"),
            _parameter("plane"),
            _optional("teleport", "boolean", True),
        ],
        _scene_subject("{subject}", "_travel_mover")
        + [
            _set(
                "_travel_patch",
                {"plane": "plane", "previous_position": "_travel_mover.position", "moving": False},
            ),
            _set("_travel_metadata", "merge(_travel_mover.metadata, _travel_patch)"),
            {
                "spatial_mutation": {
                    "scene": "state.spatial_scene",
                    "map_id": "{state.spatial_scene.map.id}",
                    "expected_revision": "{state.spatial_scene.map.revision}",
                    "target": {"kind": "entity", "id": "{_travel_mover.id}"},
                    "patch": {"position": "{destination}", "metadata": "{_travel_metadata}"},
                },
                "store_as": "_travel_scene_move",
            },
            _call("interrupt_srd_identify_movement", {"scene_entity_id": "{_travel_mover.id}"}),
            _call("refresh_srd_divination_spatial", {}),
        ],
    )
    spend = _tool(
        "Pay plant/traversal movement from the same ordinary movement budget.",
        [_entity("actor_state"), _parameter("amount", "number", minimum=0.5)],
        [
            _require(ACTIVE, "An Incapacitated creature cannot travel by movement."),
            _require(
                "state.pending_srd_action_stack == null or "
                "len(state.pending_srd_action_stack) == 0",
                "Complete the pending action before movement.",
            ),
            _when(
                "combat.encounter_active == true",
                [
                    _call(
                        "spend_combat_movement",
                        {
                            "actor": "{_actor_state_id}",
                            "actor_state": "{_actor_state_id}",
                            "distance": "{amount}",
                            "difficult_terrain": False,
                        },
                    )
                ],
            ),
            _when(
                "combat.encounter_active != true",
                [
                    _call(
                        "derive_srd_scene_movement_budget",
                        {"actor_state": "{_actor_state_id}", "mode": "walk"},
                        ["_scene_exploration_remaining"],
                    ),
                    _require(
                        "amount <= _scene_exploration_remaining",
                        "Advance the actual exploration round before more movement.",
                    ),
                    _set(
                        "actor_state.scene_movement_spent",
                        "actor_state.scene_movement_spent + amount",
                    ),
                ],
            ),
        ],
    )
    close_turn = _tool_scene(
        "Close each circle portal at the actual end of its caster's next turn.",
        [_entity("actor_state"), _parameter("boundary_turn", "integer")],
        [
            _when(
                "state.travel_circles != null",
                [
                    _set("_travel_turn_portals", "state.travel_circles"),
                    _set("_travel_retained_portals", []),
                    _loop(
                        "_travel_turn_portals",
                        "_travel_turn_portal",
                        [
                            _when(
                                "_travel_turn_portal.caster == _actor_state_id and "
                                "boundary_turn >= _travel_turn_portal.end_turn",
                                [
                                    _set("_travel_closed", {"active": False}),
                                    _set(
                                        "_travel_turn_portal",
                                        "merge(_travel_turn_portal, _travel_closed)",
                                    ),
                                ],
                            ),
                            _push("_travel_retained_portals", "_travel_turn_portal"),
                        ],
                    ),
                    _set("state.travel_circles", "_travel_retained_portals"),
                ],
            )
        ],
    )
    return {
        "resolve_srd_travel_placement": placement,
        **face_placements,
        "move_srd_travel_subject": move,
        "spend_srd_travel_movement": spend,
        "close_srd_travel_turn": close_turn,
    }


def _target_tools():
    return {
        "validate_srd_travel_companion": _tool_scene(
            "Validate an actual willing companion within the source's range and plane.",
            [
                _entity("caster_state"),
                _entity("target_state"),
                _parameter("range_feet", "number"),
                _parameter("sight", "boolean"),
            ],
            [
                _call(
                    "resolve_srd_scene_relation",
                    {"actor_state": "{_caster_state_id}", "target_state": "{_target_state_id}"},
                    ["_scene_distance", "_scene_same_plane", "_scene_can_see"],
                ),
                _require(
                    "_scene_same_plane == true and _scene_distance <= range_feet",
                    "The traveler must be within the actual same-plane range.",
                ),
                _require(
                    "sight != true or _scene_can_see == true",
                    "You must actually see this traveler.",
                ),
                _require(
                    "target_state.is_alive == true",
                    "The selected traveler must be a living creature.",
                ),
            ],
        ),
        "damage_srd_travel_failure": _tool(
            "Apply actual Force damage through the shared damage/concentration owner.",
            [_parameter("target"), _parameter("amount", "integer")],
            [
                _call(
                    "apply_srd_damage",
                    {
                        "target": "{target}",
                        "amount": "{amount}",
                        "adjustment": 0,
                        "damage_type": "force",
                        "source_state_id": "",
                    },
                )
            ],
        ),
    }


TELEPORT_FAMILIARITIES = (
    "very_familiar",
    "seen_casually",
    "viewed_once",
    "described",
    "false_destination",
)


def _teleport_tools(tools):
    """Keep an unbounded Mishap journey in finite, paid source-owned continuation steps."""
    knowledge = _public(
        _tool_scene(
            "Record the caster's descriptive familiarity with an actual destination anchor.",
            [
                _entity("caster_state"),
                _parameter("destination_subject"),
                _parameter("familiarity", "enum", enum_values=list(TELEPORT_FAMILIARITIES)),
            ],
            _scene_subject("{destination_subject}", "_teleport_anchor")
            + [
                _require(
                    "_teleport_anchor.metadata.plane != null",
                    "The destination anchor needs its actual plane.",
                ),
                _set("_teleport_known", []),
                _when(
                    "caster_state.teleport_destinations != null",
                    [_set("_teleport_known", "caster_state.teleport_destinations")],
                ),
                _set("_teleport_kept", []),
                _loop(
                    "_teleport_known",
                    "_teleport_known_destination",
                    [
                        _when(
                            "_teleport_known_destination.anchor != _teleport_anchor.id",
                            [_push("_teleport_kept", "_teleport_known_destination")],
                        ),
                    ],
                ),
                _push(
                    "_teleport_kept",
                    {
                        "anchor": "_teleport_anchor.id",
                        "position": "_teleport_anchor.position",
                        "plane": "_teleport_anchor.metadata.plane",
                        "familiarity": "familiarity",
                    },
                ),
                _set("caster_state.teleport_destinations", "_teleport_kept"),
                _set("_teleport_knowledge", "familiarity"),
            ],
        ),
        "information",
        "Teleport destination knowledge",
        [("Familiarity", "_teleport_knowledge")],
    )
    similar = _public(
        _tool_scene(
            "Record a visually or thematically similar place using two actual spatial anchors.",
            [_parameter("destination_subject"), _parameter("similar_subject")],
            _scene_subject("{destination_subject}", "_teleport_original_anchor")
            + _scene_subject("{similar_subject}", "_teleport_similar_anchor")
            + [
                _require(
                    "_teleport_original_anchor.id != _teleport_similar_anchor.id and "
                    "_teleport_original_anchor.metadata.plane != null and "
                    "_teleport_original_anchor.metadata.plane == "
                    "_teleport_similar_anchor.metadata.plane",
                    "A similar place must be a different actual anchor on the same plane.",
                ),
                _set("_teleport_similar_records", []),
                _when(
                    "state.travel_teleport_similar_areas != null",
                    [
                        _set("_teleport_similar_records", "state.travel_teleport_similar_areas"),
                    ],
                ),
                _set("_teleport_similar_present", False),
                _loop(
                    "_teleport_similar_records",
                    "_teleport_similar_record",
                    [
                        _when(
                            "_teleport_similar_record.original == _teleport_original_anchor.id and "
                            "_teleport_similar_record.similar == _teleport_similar_anchor.id",
                            [_set("_teleport_similar_present", True)],
                        ),
                    ],
                ),
                _when(
                    "_teleport_similar_present != true",
                    [
                        _push(
                            "_teleport_similar_records",
                            {
                                "original": "_teleport_original_anchor.id",
                                "similar": "_teleport_similar_anchor.id",
                            },
                        ),
                        _set("state.travel_teleport_similar_areas", "_teleport_similar_records"),
                    ],
                ),
                _set("_teleport_similar_subject", "_teleport_similar_anchor.id"),
            ],
        ),
        "information",
        "Teleport similar area",
        [("Actual anchor", "_teleport_similar_subject")],
    )
    preflight = _tool_scene(
        "Validate source party, known destination and planes before any casting payment or roll.",
        [
            _entity("caster_state"),
            _parameter("destination_subject"),
            _optional("targets", "array", None, items={"type": "string"}),
            _parameter("all_targets_willing", "boolean"),
        ],
        [
            _require(
                "caster_state.teleport_resolution == null or "
                "caster_state.teleport_resolution.status == 'completed'",
                "Finish the caster's paid Teleport journey before casting another Teleport.",
            ),
            _when(
                "caster_state.travel_ethereal != null",
                [
                    _call(
                        "refresh_srd_etherealness_target", {"target_state": "{_caster_state_id}"}
                    ),
                ],
            ),
        ]
        + _scene_subject("{_caster_state_id}", "_teleport_caster")
        + _scene_subject("{destination_subject}", "_teleport_anchor")
        + [
            _require(
                "_teleport_caster.metadata.plane != null and "
                "_teleport_anchor.metadata.plane == _teleport_caster.metadata.plane",
                "Teleport's actual known destination must be on the caster's current plane.",
            ),
            _set("_teleport_targets", []),
            _when("targets != null", [_set("_teleport_targets", "targets")]),
            _require("len(_teleport_targets) <= 8", "Teleport permits up to eight companions."),
            _require(
                "len(_teleport_targets) == 0 or all_targets_willing == true",
                "Every Teleport companion must be willing.",
            ),
            _set("_teleport_party", []),
            _push(
                "_teleport_party",
                {
                    "target": "_caster_state_id",
                    "origin": "_teleport_caster.position",
                    "offset": [0, 0, 0],
                },
            ),
            _set("_teleport_seen", []),
            _loop(
                "_teleport_targets",
                "_teleport_target",
                [
                    _require(
                        "_teleport_target != _caster_state_id and "
                        "_teleport_target not in _teleport_seen",
                        "Select each companion once, excluding the caster.",
                    ),
                    _push("_teleport_seen", "_teleport_target"),
                    _call(
                        "validate_srd_travel_companion",
                        {
                            "caster_state": "{_caster_state_id}",
                            "target_state": "{_teleport_target}",
                            "range_feet": 10,
                            "sight": True,
                        },
                    ),
                    *_scene_subject("{_teleport_target}", "_teleport_companion"),
                    _call(
                        "check_srd_travel_destination_sight",
                        {
                            "caster_state": "{_caster_state_id}",
                            "point": "{_teleport_companion.position}",
                        },
                    ),
                    _push(
                        "_teleport_party",
                        {
                            "target": "_teleport_target",
                            "origin": "_teleport_companion.position",
                            "offset": [
                                "_teleport_companion.position[0] - _teleport_caster.position[0]",
                                "_teleport_companion.position[1] - _teleport_caster.position[1]",
                                "_teleport_companion.position[2] - _teleport_caster.position[2]",
                            ],
                        },
                    ),
                ],
            ),
            _set("_teleport_familiarity", "''"),
            _set("_teleport_known", []),
            _when(
                "caster_state.teleport_destinations != null",
                [
                    _set("_teleport_known", "caster_state.teleport_destinations"),
                ],
            ),
            _loop(
                "_teleport_known",
                "_teleport_known_destination",
                [
                    _when(
                        "_teleport_known_destination.anchor == _teleport_anchor.id and "
                        "_teleport_known_destination.position[0] == "
                        "_teleport_anchor.position[0] and "
                        "_teleport_known_destination.position[1] == "
                        "_teleport_anchor.position[1] and "
                        "_teleport_known_destination.position[2] == "
                        "_teleport_anchor.position[2] and "
                        "_teleport_known_destination.plane == _teleport_anchor.metadata.plane",
                        [_set("_teleport_familiarity", "_teleport_known_destination.familiarity")],
                    ),
                ],
            ),
            _when(
                "_teleport_anchor.metadata.teleportation_circle == true and "
                "caster_state.known_circle_sigils != null and "
                "_teleport_anchor.metadata.sigil in caster_state.known_circle_sigils",
                [_set("_teleport_familiarity", "'permanent_circle'")],
            ),
            _require(
                "_teleport_familiarity != ''",
                "Record actual descriptive destination knowledge or learn its permanent sigil.",
            ),
            _set(
                "caster_state.teleport_preflight",
                {
                    "anchor": "_teleport_anchor.id",
                    "destination": "_teleport_anchor.position",
                    "plane": "_teleport_caster.metadata.plane",
                    "party": "_teleport_party",
                    "familiarity": "_teleport_familiarity",
                },
            ),
        ],
    )
    if "check_srd_progressive_travel" in tools:
        preflight["effects"].insert(
            -1,
            _loop(
                "_teleport_party",
                "_teleport_party_member",
                [
                    _call(
                        "check_srd_progressive_travel",
                        {
                            "target_state": "{_teleport_party_member.target}",
                            "destination_plane": "{_teleport_caster.metadata.plane}",
                            "teleport": True,
                        },
                    ),
                ],
            ),
        )
    if "resolve_srd_illumination_point_sight" in tools:
        knowledge_boundary = next(
            index
            for index, effect in enumerate(preflight["effects"])
            if effect.get("require", {}).get("condition") == "_teleport_familiarity != ''"
        )
        preflight["effects"].insert(
            knowledge_boundary,
            _when(
                "_teleport_familiarity == ''",
                [
                    _call(
                        "resolve_srd_illumination_point_sight",
                        {
                            "observer_state": "{_caster_state_id}",
                            "point": "{_teleport_anchor.position}",
                        },
                        ["_illumination_point_can_see"],
                    ),
                    _when(
                        "_illumination_point_can_see == true",
                        [
                            _set("_teleport_familiarity", "'very_familiar'"),
                        ],
                    ),
                ],
            ),
        )
    start = _tool(
        "Start a source-owned continuation for the exact paid Teleport casting.",
        [_entity("caster_state"), _parameter("slot_level", "integer")],
        [
            _require(
                "caster_state.teleport_preflight != null", "Teleport needs its validated party."
            ),
            _when(
                "state.travel_teleport_serial == null", [_set("state.travel_teleport_serial", 0)]
            ),
            _set("state.travel_teleport_serial", "state.travel_teleport_serial + 1"),
            _set("_teleport_preflight", "caster_state.teleport_preflight"),
            _set(
                "caster_state.teleport_resolution",
                {
                    "id": "state.travel_teleport_serial",
                    "caster": "_caster_state_id",
                    "spell": "'teleport'",
                    "slot_level": "slot_level",
                    "paid": True,
                    "reaction_action_id": None,
                    "anchor": "_teleport_preflight.anchor",
                    "destination": "_teleport_preflight.destination",
                    "plane": "_teleport_preflight.plane",
                    "party": "_teleport_preflight.party",
                    "familiarity": "_teleport_preflight.familiarity",
                    "stage": "'percentile'",
                    "status": "'pending'",
                    "history": [],
                    "mishap_index": 0,
                    "arrival_index": 0,
                },
            ),
            _when(
                "caster_state.pending_srd_completion_id != null",
                [
                    _set(
                        "caster_state.teleport_resolution.reaction_action_id",
                        "caster_state.pending_srd_completion_id",
                    ),
                ],
            ),
            _set("caster_state.teleport_preflight", None),
            _call("step_srd_teleport_resolution", {"caster_state": "{_caster_state_id}"}),
        ],
    )
    percentile = [
        {"dice": {"expression": "1d100"}, "store_as": "_teleport_percentile"},
        _set("_teleport_outcome", "'on_target'"),
    ]
    for familiarity, mishap, similar_limit, off in (
        ("very_familiar", 5, 13, 24),
        ("seen_casually", 33, 43, 53),
        ("viewed_once", 43, 53, 73),
        ("described", 43, 53, 73),
        ("false_destination", 50, 100, 100),
    ):
        percentile.append(
            _when(
                "caster_state.teleport_resolution.familiarity == '" + familiarity + "'",
                [
                    _when(
                        f"_teleport_percentile.total <= {off}",
                        [_set("_teleport_outcome", "'off_target'")],
                    ),
                    _when(
                        f"_teleport_percentile.total <= {similar_limit}",
                        [_set("_teleport_outcome", "'similar_area'")],
                    ),
                    _when(
                        f"_teleport_percentile.total <= {mishap}",
                        [_set("_teleport_outcome", "'mishap'")],
                    ),
                ],
            )
        )
    percentile += [
        _set("_teleport_history", "caster_state.teleport_resolution.history"),
        _push(
            "_teleport_history",
            {
                "kind": "'percentile'",
                "roll": "_teleport_percentile",
                "outcome": "_teleport_outcome",
            },
        ),
        _set("caster_state.teleport_resolution.history", "_teleport_history"),
        _set("caster_state.teleport_resolution.outcome", "_teleport_outcome"),
        _set("caster_state.teleport_resolution.stage", "_teleport_outcome"),
        _when(
            "_teleport_outcome == 'mishap'",
            [_set("caster_state.teleport_resolution.mishap_index", 0)],
        ),
        _when(
            "_teleport_outcome == 'on_target'",
            [
                _set(
                    "caster_state.teleport_resolution.arrival",
                    "caster_state.teleport_resolution.destination",
                ),
                _set("caster_state.teleport_resolution.stage", "'arrival'"),
            ],
        ),
    ]
    mishap_roll = [
        _set(
            "_teleport_member",
            "caster_state.teleport_resolution.party[caster_state.teleport_resolution.mishap_index]",
        ),
        {"dice": {"expression": "3d10"}, "store_as": "_teleport_mishap_roll"},
        _set("_teleport_mishap_amount", "_teleport_mishap_roll.total"),
        _when(
            "caster_state.has_actor_spell_modifiers == true",
            [
                _call(
                    "modify_srd_actor_damage_roll",
                    {
                        "actor_state": "{_caster_state_id}",
                        "lasting_state": "state",
                        "amount": "{_teleport_mishap_amount}",
                    },
                    ["_actor_modified_damage"],
                ),
                _set("_teleport_mishap_amount", "_actor_modified_damage"),
            ],
        ),
        _set(
            "caster_state.teleport_resolution.pending_mishap",
            {
                "target": "_teleport_member.target",
                "roll": "_teleport_mishap_roll",
                "modified_amount": "_teleport_mishap_amount",
            },
        ),
        _set("_teleport_history", "caster_state.teleport_resolution.history"),
        _push(
            "_teleport_history",
            {
                "kind": "'mishap_roll'",
                "target": "_teleport_member.target",
                "roll": "_teleport_mishap_roll",
                "modified_amount": "_teleport_mishap_amount",
            },
        ),
        _set("caster_state.teleport_resolution.history", "_teleport_history"),
        _set("caster_state.teleport_resolution.stage", "'mishap_damage'"),
    ]
    mishap_damage = [
        _set("_teleport_packet", "caster_state.teleport_resolution.pending_mishap"),
        _call(
            "apply_srd_qualifying_damage",
            {
                "target": "{_teleport_packet.target}",
                "source_state_id": "{_caster_state_id}",
                "amount": "{_teleport_packet.modified_amount}",
                "damage_type": "force",
                "adjustment": 0,
            },
            ["_effective_damage"],
        ),
        _set("_teleport_history", "caster_state.teleport_resolution.history"),
        _push(
            "_teleport_history",
            {
                "kind": "'mishap'",
                "target": "_teleport_packet.target",
                "roll": "_teleport_packet.roll",
                "modified_amount": "_teleport_packet.modified_amount",
                "effective_damage": "_effective_damage",
            },
        ),
        _set("caster_state.teleport_resolution.history", "_teleport_history"),
        _set("caster_state.teleport_resolution.pending_mishap", None),
        _set(
            "caster_state.teleport_resolution.mishap_index",
            "caster_state.teleport_resolution.mishap_index + 1",
        ),
        _when(
            "caster_state.teleport_resolution.mishap_index == "
            "len(caster_state.teleport_resolution.party)",
            [_set("caster_state.teleport_resolution.stage", "'percentile'")],
        ),
        _when(
            "caster_state.teleport_resolution.mishap_index < "
            "len(caster_state.teleport_resolution.party)",
            [_set("caster_state.teleport_resolution.stage", "'mishap'")],
        ),
    ]
    offset = [
        {"dice": {"expression": "2d12"}, "store_as": "_teleport_miles"},
        {"dice": {"expression": "1d8"}, "store_as": "_teleport_direction"},
        _set("_teleport_east", 0),
        _set("_teleport_north", 0),
    ]
    for direction, east, north in (
        (1, 1, 0),
        (2, 0.7071067811865476, -0.7071067811865476),
        (3, 0, -1),
        (4, -0.7071067811865476, -0.7071067811865476),
        (5, -1, 0),
        (6, -0.7071067811865476, 0.7071067811865476),
        (7, 0, 1),
        (8, 0.7071067811865476, 0.7071067811865476),
    ):
        offset.append(
            _when(
                f"_teleport_direction.total == {direction}",
                [
                    _set("_teleport_east", east),
                    _set("_teleport_north", north),
                ],
            )
        )
    offset += [
        _set(
            "caster_state.teleport_resolution.arrival",
            [
                "caster_state.teleport_resolution.destination[0] + "
                "_teleport_east * _teleport_miles.total * 5280",
                "caster_state.teleport_resolution.destination[1] + "
                "_teleport_north * _teleport_miles.total * 5280",
                "caster_state.teleport_resolution.destination[2]",
            ],
        ),
        _set("_teleport_history", "caster_state.teleport_resolution.history"),
        _push(
            "_teleport_history",
            {
                "kind": "'off_target'",
                "miles": "_teleport_miles",
                "direction": "_teleport_direction",
                "arrival": "caster_state.teleport_resolution.arrival",
            },
        ),
        _set("caster_state.teleport_resolution.history", "_teleport_history"),
        _set("caster_state.teleport_resolution.stage", "'arrival'"),
    ]
    similar_search = [
        _set("_teleport_closest", None),
        _set("_teleport_closest_distance", 0),
        _set("_teleport_similar_records", []),
        _when(
            "state.travel_teleport_similar_areas != null",
            [
                _set("_teleport_similar_records", "state.travel_teleport_similar_areas"),
            ],
        ),
        _set("_teleport_scene_entities", "state.spatial_scene.entities"),
        _loop(
            "_teleport_scene_entities",
            "_teleport_candidate",
            [
                _when(
                    "_teleport_candidate.active != false and "
                    "_teleport_candidate.metadata.plane == caster_state.teleport_resolution.plane",
                    [
                        _set("_teleport_qualifies", False),
                        _loop(
                            "_teleport_similar_records",
                            "_teleport_similar_record",
                            [
                                _when(
                                    "_teleport_similar_record.original == "
                                    "caster_state.teleport_resolution.anchor and "
                                    "_teleport_similar_record.similar == _teleport_candidate.id",
                                    [_set("_teleport_qualifies", True)],
                                ),
                            ],
                        ),
                        _when(
                            "_teleport_qualifies == true",
                            [
                                *_measure(
                                    "{caster_state.teleport_resolution.destination}",
                                    "{_teleport_candidate.position}",
                                    "_teleport_candidate_distance",
                                ),
                                _when(
                                    "_teleport_closest == null or "
                                    "_teleport_candidate_distance < _teleport_closest_distance or "
                                    "(_teleport_candidate_distance == _teleport_closest_distance "
                                    "and _teleport_candidate.id < _teleport_closest.id)",
                                    [
                                        _set("_teleport_closest", "_teleport_candidate"),
                                        _set(
                                            "_teleport_closest_distance",
                                            "_teleport_candidate_distance",
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        _set(
            "caster_state.teleport_resolution.waiting_reason",
            "'No admitted visually or thematically similar anchor.'",
        ),
        _when(
            "_teleport_closest != null",
            [
                _set("caster_state.teleport_resolution.arrival", "_teleport_closest.position"),
                _set("caster_state.teleport_resolution.similar_anchor", "_teleport_closest.id"),
                _set(
                    "caster_state.teleport_resolution.similar_distance",
                    "_teleport_closest_distance",
                ),
                _set("caster_state.teleport_resolution.waiting_reason", None),
                _set("caster_state.teleport_resolution.stage", "'arrival'"),
            ],
        ),
    ]
    arrival = [
        _set(
            "_teleport_member",
            "caster_state.teleport_resolution.party[caster_state.teleport_resolution.arrival_index]",
        ),
        _set(
            "_teleport_arrival_point",
            [
                "caster_state.teleport_resolution.arrival[0] + _teleport_member.offset[0]",
                "caster_state.teleport_resolution.arrival[1] + _teleport_member.offset[1]",
                "caster_state.teleport_resolution.arrival[2] + _teleport_member.offset[2]",
            ],
        ),
        _set("_teleport_excluded", []),
        _set("_teleport_saved_party", "caster_state.teleport_resolution.party"),
        _loop(
            "_teleport_saved_party",
            "_teleport_other",
            [
                _push("_teleport_excluded", "_teleport_other.target"),
            ],
        ),
        *_placement(
            "{_teleport_member.target}",
            "{_teleport_arrival_point}",
            "{caster_state.teleport_resolution.plane}",
            "{_teleport_excluded}",
        ),
        _require(
            "_travel_placement.distance <= 0.000000001",
            "Teleport's exact arrival is occupied or outside the admitted map; "
            "no shunt rule is supported.",
        ),
        *_move(
            "{_teleport_member.target}",
            "{_teleport_arrival_point}",
            "{caster_state.teleport_resolution.plane}",
        ),
        _set(
            "caster_state.teleport_resolution.arrival_index",
            "caster_state.teleport_resolution.arrival_index + 1",
        ),
        _when(
            "caster_state.teleport_resolution.arrival_index == "
            "len(caster_state.teleport_resolution.party)",
            [
                _set("caster_state.teleport_resolution.status", "'completed'"),
                _set("caster_state.teleport_resolution.stage", "'completed'"),
            ],
        ),
    ]
    step = _tool_scene(
        "Advance exactly one saved Teleport stage or recipient without a second casting cost.",
        [_entity("caster_state")],
        [
            _require(
                "caster_state.teleport_resolution != null and "
                "caster_state.teleport_resolution.status == 'pending' and "
                "caster_state.teleport_resolution.paid == true",
                "Continue only an actual paid pending Teleport.",
            ),
            _set("_teleport_stage", "caster_state.teleport_resolution.stage"),
            _when("_teleport_stage == 'percentile'", percentile),
            _when("_teleport_stage == 'mishap'", mishap_roll),
            _when("_teleport_stage == 'mishap_damage'", mishap_damage),
            _when("_teleport_stage == 'off_target'", offset),
            _when("_teleport_stage == 'similar_area'", similar_search),
            _when("_teleport_stage == 'arrival'", arrival),
            _set("_teleport_status", "caster_state.teleport_resolution.status"),
            _set("_teleport_next_stage", "caster_state.teleport_resolution.stage"),
            _set("_teleport_resolution_id", "caster_state.teleport_resolution.id"),
        ],
    )
    continuation = _public(
        _tool(
            "Continue the exact saved paid journey without a caller roll, result "
            "or destination verdict.",
            [_entity("caster_state"), _parameter("resolution_id", "integer", minimum=1)],
            [
                _require(
                    "caster_state.teleport_resolution != null and "
                    "caster_state.teleport_resolution.id == resolution_id",
                    "Continue this caster's exact saved Teleport identity.",
                ),
                _call(
                    "step_srd_teleport_resolution",
                    {"caster_state": "{_caster_state_id}"},
                    [
                        "_teleport_status",
                        "_teleport_next_stage",
                        "_teleport_resolution_id",
                    ],
                ),
            ],
        ),
        "spell",
        "Teleport journey",
        [
            ("Status", "_teleport_status"),
            ("Next stage", "_teleport_next_stage"),
            ("Paid cast", "_teleport_resolution_id"),
        ],
    )
    return bind_teleport_object_tools({
        "record_srd_teleport_destination": knowledge,
        "record_srd_teleport_similar_area": similar,
        "validate_srd_teleport_cast": preflight,
        "start_srd_teleport_resolution": start,
        "step_srd_teleport_resolution": step,
        "continue_srd_teleport_resolution": continuation,
    }, tools)


def _ethereal_tools():
    """Track each actual planar effect and return it through the shared clock/placement owners."""
    apply = _tool_scene(
        "Enter the Border Ethereal without creating another position authority.",
        [
            _entity("target_state"),
            _entity("caster_state"),
            _parameter("slot_level", "integer", minimum=7, maximum=9),
            _optional("spell_cast_id", default="", model_hidden=True),
            _optional("spell_cast_owner", default=None, resolve="entity", model_hidden=True),
            _optional(
                "actual_spell_level", "integer", -1, minimum=-1, maximum=9, model_hidden=True
            ),
        ],
        [
            _scene_subject("{_target_state_id}", "_travel_phase_entity"),
            _when(
                "spell_cast_id != ''",
                [
                    _require(
                        "spell_cast_owner != null and _spell_cast_owner_id == _caster_state_id "
                        "and caster_state.last_spell_source_cast_id == spell_cast_id "
                        "and caster_state.last_spell_source_spell == 'etherealness' "
                        "and actual_spell_level == slot_level",
                        "Etherealness must retain its actual allocated cast owner and slot.",
                    ),
                ],
            ),
            _require(
                "target_state.travel_ethereal == null",
                "Resolve the target's existing Ethereal effect before applying another source.",
            ),
            _set(
                "target_state.travel_ethereal",
                {
                    "spell": "'etherealness'",
                    "caster": "_caster_state_id",
                    "slot_level": "slot_level",
                    "cast_id": "spell_cast_id",
                    "source_caster_id": None,
                    "ongoing_duration": "spell_cast_id != ''",
                    "created_tick": "state.lasting_tick",
                    "return_plane": "_travel_phase_entity.metadata.plane",
                    "expires_tick": "state.lasting_tick + 9600",
                },
            ),
            _when(
                "spell_cast_id != ''",
                [_set("target_state.travel_ethereal.source_caster_id", "_spell_cast_owner_id")],
            ),
            *_move("{_target_state_id}", "{_travel_phase_entity.position}", "ethereal", False),
            _when("state.travel_phase_targets == null", [_set("state.travel_phase_targets", [])]),
            _set("_travel_phase_targets", "state.travel_phase_targets"),
            _when(
                "_target_state_id not in _travel_phase_targets",
                [_push("_travel_phase_targets", "_target_state_id")],
            ),
            _set("state.travel_phase_targets", "_travel_phase_targets"),
        ],
    )
    end_target = _tool_scene(
        "End the actual target's Etherealness through its corresponding position and exact shunt.",
        [_entity("target_state")],
        [
            _when(
                "target_state.travel_ethereal != null",
                [
                    _set("_travel_excluded", []),
                    _set("_travel_return", "target_state.travel_ethereal"),
                    _scene_subject("{_target_state_id}", "_travel_return_entity"),
                    *_placement(
                        "{_target_state_id}",
                        "{_travel_return_entity.position}",
                        "{_travel_return.return_plane}",
                    ),
                    *_move(
                        "{_target_state_id}",
                        "{_travel_placement.position}",
                        "{_travel_return.return_plane}",
                        False,
                    ),
                    _set("target_state.travel_ethereal", None),
                    _set("_travel_shunt_damage", "floor(_travel_placement.distance * 2)"),
                    _when(
                        "_travel_shunt_damage > 0",
                        [
                            _call(
                                "damage_srd_travel_failure",
                                {
                                    "target": "{_target_state_id}",
                                    "amount": "{_travel_shunt_damage}",
                                },
                            )
                        ],
                    ),
                ],
            ),
            _set("_travel_phase_active", "target_state.travel_ethereal != null"),
        ],
    )
    refresh = _tool_scene(
        "Return an expired Etherealness target through the same source removal owner.",
        [_entity("target_state")],
        [
            _when(
                "target_state.travel_ethereal != null and "
                "state.lasting_tick >= target_state.travel_ethereal.expires_tick",
                [_call("end_srd_etherealness_target", {"target_state": "{_target_state_id}"})],
            ),
            _set("_travel_phase_active", "target_state.travel_ethereal != null"),
        ],
    )
    all_targets = _tool_scene(
        "Refresh only tracked Ethereal sources at the authoritative lifetime boundary.",
        [],
        [
            _when(
                "state.travel_phase_targets != null",
                [
                    _set("_travel_phase_targets", "state.travel_phase_targets"),
                    _set("_travel_remaining_phases", []),
                    _loop(
                        "_travel_phase_targets",
                        "_travel_phase_target",
                        [
                            _call(
                                "refresh_srd_etherealness_target",
                                {"target_state": "{_travel_phase_target}"},
                                ["_travel_phase_active"],
                            ),
                            _when(
                                "_travel_phase_active == true",
                                [_push("_travel_remaining_phases", "_travel_phase_target")],
                            ),
                        ],
                    ),
                    _set("state.travel_phase_targets", "_travel_remaining_phases"),
                ],
            )
        ],
    )
    dismiss = _public(
        _tool_scene(
            "Dismiss this caster's ongoing Etherealness with no action under SRD time-span rules.",
            [_entity("caster_state")],
            [
                _require(
                    "caster_state.is_alive == true and not "
                    "('incapacitated' in caster_state.conditions or "
                    "'paralyzed' in caster_state.conditions or "
                    "'petrified' in caster_state.conditions or "
                    "'stunned' in caster_state.conditions or "
                    "'unconscious' in caster_state.conditions)",
                    "An Incapacitated caster cannot dismiss a time-span spell.",
                ),
                _set("_travel_dismiss_targets", []),
                _set("_travel_dismiss_count", 0),
                _when(
                    "state.travel_phase_targets != null",
                    [_set("_travel_dismiss_targets", "state.travel_phase_targets")],
                ),
                _loop(
                    "_travel_dismiss_targets",
                    "_travel_dismiss_target",
                    [
                        _call(
                            "dismiss_srd_etherealness_owned_target",
                            {
                                "target_state": "{_travel_dismiss_target}",
                                "caster_state": "{_caster_state_id}",
                            },
                            ["_travel_dismissed_target"],
                        ),
                        _when(
                            "_travel_dismissed_target == true",
                            [_set("_travel_dismiss_count", "_travel_dismiss_count + 1")],
                        ),
                    ],
                ),
                _require(
                    "_travel_dismiss_count > 0",
                    "This caster has no ongoing Etherealness to dismiss.",
                ),
                _call("refresh_srd_etherealness_sources", {}),
            ],
        ),
        "exploration",
        "Etherealness dismissed",
        [("Targets returned", "_travel_dismiss_count")],
    )
    owned_target = _tool_scene(
        "End only the selected caster's actual Etherealness grant on this target.",
        [_entity("target_state"), _entity("caster_state")],
        [
            _set("_travel_dismissed_target", False),
            _when(
                "target_state.travel_ethereal != null and "
                "target_state.travel_ethereal.caster == _caster_state_id",
                [
                    _call("end_srd_etherealness_target", {"target_state": "{_target_state_id}"}),
                    _set("_travel_dismissed_target", True),
                ],
            ),
        ],
    )
    return {
        "apply_srd_etherealness_target": apply,
        "end_srd_etherealness_target": end_target,
        "refresh_srd_etherealness_target": refresh,
        "refresh_srd_etherealness_sources": all_targets,
        "dismiss_srd_etherealness": dismiss,
        "dismiss_srd_etherealness_owned_target": owned_target,
    }


def _perception_tool():
    """Qualify projected sight from a live planar source and the actual observer position."""
    changed = {
        "resolve_srd_travel_perception": _tool_scene(
            "Admit actual-plane sight or source-qualified Ethereal projection within sixty feet.",
            [_entity("observer_state"), _array("point"), _optional("perception_plane", default="")],
            [
                _call(
                    "refresh_srd_etherealness_target",
                    {"target_state": "{_observer_state_id}"},
                ),
                _scene_subject("{_observer_state_id}", "_travel_perception_observer"),
                _set("_travel_perception_origin", "_travel_perception_observer.position"),
                _set(
                    "_travel_perception_observer_plane",
                    "'material'",
                ),
                _when(
                    "_travel_perception_observer.metadata.plane != null",
                    [
                        _set(
                            "_travel_perception_observer_plane",
                            "_travel_perception_observer.metadata.plane",
                        )
                    ],
                ),
                _set("_travel_perception_plane", "_travel_perception_observer_plane"),
                *_measure(
                    "{_travel_perception_origin}",
                    "{point}",
                    "_travel_perception_distance",
                ),
                _when(
                    "perception_plane != '' and "
                    "perception_plane != _travel_perception_observer_plane",
                    [
                        _set("_travel_projection_active", False),
                        _when(
                            "observer_state.travel_ethereal != null and "
                            "observer_state.travel_ethereal.spell == 'etherealness' and "
                            "state.lasting_tick < observer_state.travel_ethereal.expires_tick and "
                            "perception_plane == observer_state.travel_ethereal.return_plane",
                            [_set("_travel_projection_active", True)],
                        ),
                        _when(
                            "observer_state.travel_blink != null and "
                            "observer_state.travel_blink.spell == 'blink' and "
                            "observer_state.travel_blink.phase == true and "
                            "perception_plane == observer_state.travel_blink.return_plane and "
                            "(state.lasting_tick < observer_state.travel_blink.expires_tick or "
                            "observer_state.travel_blink.return_in_progress == true)",
                            [_set("_travel_projection_active", True)],
                        ),
                        _require(
                            "_travel_perception_observer_plane == 'ethereal' and "
                            "_travel_projection_active == true",
                            "The observer has no active source permitting sight on that plane.",
                        ),
                        _require(
                            "_travel_perception_distance <= 60",
                            "Projected Ethereal sight cannot reach beyond sixty actual feet.",
                        ),
                        _set("_travel_perception_plane", "perception_plane"),
                    ],
                ),
            ],
        ),
    }
    pure = deepcopy(changed["resolve_srd_travel_perception"])
    pure["description"] = (
        "Qualify actual-plane or live projected sight without refreshing or relocating sources."
    )
    pure["effects"] = pure["effects"][1:]
    changed["qualify_srd_travel_perception"] = pure
    changed["resolve_srd_travel_perception"]["effects"] = [
        _call("refresh_srd_etherealness_target", {"target_state": "{_observer_state_id}"}),
        _call(
            "qualify_srd_travel_perception",
            {
                "observer_state": "{_observer_state_id}",
                "point": "{point}",
                "perception_plane": "{perception_plane}",
            },
            [
                "_travel_perception_plane",
                "_travel_perception_origin",
                "_travel_perception_distance",
                "_travel_perception_observer_plane",
            ],
        ),
    ]
    return changed


def _plant_validation():
    return [
        _scene_subject("{travel_origin_subject}", "_travel_origin_plant"),
        _scene_subject("{travel_destination_subject}", "_travel_destination_plant"),
        _require(
            (
                "_travel_origin_plant.metadata.plane == _travel_caster.metadata.plane "
                "and _travel_destination_plant.metadata.plane == "
                "_travel_caster.metadata.plane"
            ),
            "Plants must share the caster's actual plane.",
        ),
        _require(
            (
                "_travel_origin_plant.metadata.inanimate_plant == true and "
                "_travel_origin_plant.metadata.size_rank >= 3 and "
                "_travel_destination_plant.metadata.inanimate_plant == true"
            ),
            (
                "Transport via Plants starts at a Large or larger inanimate plant and "
                "ends at an inanimate plant."
            ),
        ),
        *_measure(
            "{_travel_caster.position}",
            "{_travel_origin_plant.position}",
            "_travel_origin_distance",
        ),
        _require(
            "_travel_origin_distance <= 10", "The origin plant must actually be within ten feet."
        ),
        _require(
            (
                "caster_state.known_travel_plants != null and "
                "_travel_destination_plant.id in caster_state.known_travel_plants"
            ),
            "Record an actual previously seen or touched destination plant.",
        ),
        _set(
            "_travel_link",
            {
                "spell": "spell",
                "caster": "_caster_state_id",
                "origin": "_travel_origin_plant.id",
                "destination": "_travel_destination_plant.id",
                "expires_tick": "state.lasting_tick + 20",
                "plane": "_travel_caster.metadata.plane",
            },
        ),
        _when("state.travel_plant_links == null", [_set("state.travel_plant_links", [])]),
        _set("_travel_saved_links", "state.travel_plant_links"),
        _push("_travel_saved_links", "_travel_link"),
        _set("state.travel_plant_links", "_travel_saved_links"),
    ]


def _destination_sight_tool(tools):
    """Use the actual point-sight owner when composed, retaining the prior explicit partial path."""
    if "resolve_srd_illumination_point_sight" in tools:
        effects = [
            _call(
                "resolve_srd_illumination_point_sight",
                {"observer_state": "{_caster_state_id}", "point": "{point}"},
                ["_illumination_point_can_see"],
            ),
            _require(
                "_illumination_point_can_see == true",
                "Misty Step needs a destination you can actually see.",
            ),
        ]
    else:
        effects = _scene_subject("{_caster_state_id}", "_travel_sight_observer") + [
            _call(
                "srd_information_barriers",
                {
                    "start": "{_travel_sight_observer.position}",
                    "end": "{point}",
                    "rule": "solid",
                    "plane": "{_travel_sight_observer.metadata.plane}",
                },
                ["_information_blocked"],
            ),
            _require(
                "_information_blocked != true and 'blinded' not in caster_state.conditions",
                "Misty Step needs a destination you can actually see.",
            ),
        ]
    return {
        "check_srd_travel_destination_sight": _tool_scene(
            "Check a real destination against the owning admitted point-sight rules.",
            [_entity("caster_state"), _array("point")],
            effects,
        )
    }


def _casting_tool():
    effects = [
        _set("_travel_source_owner", None),
        _when("spell_cast_owner != null", [_set("_travel_source_owner", "_spell_cast_owner_id")]),
        _lookup("spell", "_travel_profile"),
        _when(
            "caster_state.travel_ethereal != null",
            [_call("refresh_srd_etherealness_target", {"target_state": "{_caster_state_id}"})],
        ),
        _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
        _set("_spell_summary_rows", []),
        _set("_spell_detail_rows", []),
        _set("_travel_excluded", []),
        _set("_travel_targets", []),
        _when("targets != null", [_set("_travel_targets", "targets")]),
        _set("_travel_seen_targets", []),
        _loop(
            "_travel_targets",
            "_travel_target",
            [
                _require(
                    "(spell == 'maze' or _travel_target != _caster_state_id) and "
                    "_travel_target not in _travel_seen_targets",
                    "Select each additional traveler once, excluding the caster.",
                ),
                _push("_travel_seen_targets", "_travel_target"),
            ],
        ),
        _scene_subject("{_caster_state_id}", "_travel_caster"),
        _require("_travel_caster.metadata.plane != null", "Record the caster's actual plane."),
        _set("_travel_outcome", "'resolved'"),
        _when("spell == 'gate'", [
            _call("install_srd_gate", {
                "caster_state": "{_caster_state_id}", "source_id": "{gate_id}",
                "destination_subject": "{gate_destination_subject}",
                "point": "{travel_destination}",
                "diameter": "{gate_diameter}", "front": "{gate_front}",
                "depiction_depth": "{gate_depiction_depth}", "slot_level": "{slot_level}",
                "concentration_modifier": "{concentration_modifier}",
                "spell_cast_id": "{spell_cast_id}", "spell_cast_owner": "{_travel_source_owner}",
                "actual_spell_level": "{actual_spell_level}",
            }, ["_gate_opened"]),
            _set("_travel_outcome", "'Actual paired circular portals opened; transit unsupported'"),
            _when("_gate_opened != true", [
                _set("_travel_outcome", "'Paid Gate prevented by actual planar ruler'"),
            ]),
        ]),
        _when("spell == 'secret_chest'", [
            _call("install_srd_secret_chest", {
                "caster_state": "{_caster_state_id}", "slot_level": "{slot_level}",
                "chest_id": "{secret_chest_id}", "replica_id": "{secret_chest_replica}",
                "ethereal_anchor": "{secret_chest_ethereal_anchor}",
                "material_item_ids": "{material_item_ids}",
                "spell_cast_id": "{spell_cast_id}", "spell_cast_owner": "{_travel_source_owner}",
                "actual_spell_level": "{actual_spell_level}",
            }),
        ]),
        _when("spell == 'rope_trick'", [
            _call("install_srd_rope_trick", {
                "caster_state": "{_caster_state_id}", "slot_level": "{slot_level}",
                "rope_id": "{rope_trick_rope}", "dwelling_id": "{rope_trick_id}",
                "designated": [], "floor_cells": "{rope_trick_floor_cells}",
                "wall_thickness": "{rope_trick_wall_thickness}",
                "door_depth": "{rope_trick_door_depth}", "lighting": "{rope_trick_lighting}",
                "spell_cast_id": "{spell_cast_id}", "spell_cast_owner": "{_travel_source_owner}",
                "actual_spell_level": "{actual_spell_level}",
            }),
            _set("_travel_outcome", "'actual rope suspended and paired opening created'"),
        ]),
        _when("spell == 'magnificent_mansion'", [
            _call("install_srd_magnificent_mansion", {
                "caster_state": "{_caster_state_id}", "slot_level": "{slot_level}",
                "dwelling_id": "{mansion_id}", "point": "{travel_destination}",
                "designated": "{mansion_designated}", "floor_cells": "{mansion_floor_cells}",
                "wall_thickness": "{mansion_wall_thickness}", "door_depth": "{mansion_door_depth}",
                "lighting": "{mansion_lighting}",
                "furnishings": "{mansion_furnishings}",
                "banquet": "{mansion_banquet}",
                "servants": "{mansion_servants}",
                "spell_cast_id": "{spell_cast_id}", "spell_cast_owner": "{_travel_source_owner}",
                "actual_spell_level": "{actual_spell_level}",
            }),
            _set("_travel_outcome", "'actual Mansion room and designated doorway created'"),
        ]),
        _when("spell == 'telekinesis'", [
            _call("install_srd_telekinesis", {
                "caster_state": "{_caster_state_id}", "subject": "{telekinesis_subject}",
                "destination": "{telekinesis_destination}", "slot_level": "{slot_level}",
                "operation": "{telekinesis_operation}",
                "save_dc": "{save_dc}", "concentration_modifier": "{concentration_modifier}",
                "spell_cast_id": "{spell_cast_id}", "spell_cast_owner": "{_travel_source_owner}",
                "actual_spell_level": "{actual_spell_level}",
            }),
            _set("_travel_outcome", "'Telekinesis ability and optional initial exertion resolved'"),
        ]),
        _when("spell == 'levitate'", [
            _call("install_srd_levitate", {
                "caster_state": "{_caster_state_id}", "subject": "{levitate_subject}",
                "slot_level": "{slot_level}", "save_dc": "{save_dc}",
                "concentration_modifier": "{concentration_modifier}",
                "all_targets_willing": "{all_targets_willing}", "rise": "{levitate_rise}",
                "spell_cast_id": "{spell_cast_id}", "spell_cast_owner": "{_travel_source_owner}",
                "actual_spell_level": "{actual_spell_level}",
            }, ["_levitate_installed", "_levitate_save_outcome"]),
            _set("_travel_outcome", "'actual Levitate source resolved through its creature save'"),
        ]),
        _when("spell == 'passwall'", [
            _call("install_srd_passwall", {
                "caster_state": "{_caster_state_id}", "slot_level": "{slot_level}",
                "surface_id": "{passwall_surface_id}", "point": "{travel_destination}",
                "face": "{passwall_face}", "width": "{passwall_width}",
                "height": "{passwall_height}", "depth": "{passwall_depth}",
            }),
            _set("_travel_outcome", "'actual physical passage opened for one hour'"),
        ]),
        _when("spell == 'demiplane'", [
            _call("install_srd_demiplane", {
                "caster_state": "{_caster_state_id}", "surface_id": "{demiplane_surface_id}",
                "slot_level": "{slot_level}",
                "point": "{travel_destination}", "face": "{demiplane_face}",
                "room_id": "{demiplane_room}", "material": "{demiplane_material}",
                "wall_thickness": "{demiplane_wall_thickness}",
                "door_width": "{demiplane_door_width}", "door_height": "{demiplane_door_height}",
                "door_depth": "{demiplane_door_depth}",
                "spell_cast_id": "{spell_cast_id}", "spell_cast_owner": "{_travel_source_owner}",
                "actual_spell_level": "{actual_spell_level}",
            }),
            _set("_travel_outcome", "'actual Demiplane room connected by a timed source door'"),
        ]),
        _when(
            "spell == 'blink'",
            [
                _require("len(_travel_targets) == 0", "Blink affects only its caster."),
                _call(
                    "apply_srd_blink_target",
                    {
                        "target_state": "{_caster_state_id}",
                        "caster_state": "{_caster_state_id}",
                        "slot_level": "{slot_level}",
                        "spell_cast_id": "{spell_cast_id}",
                        "spell_cast_owner": "{_travel_source_owner}",
                        "actual_spell_level": "{actual_spell_level}",
                    },
                ),
                _set("_travel_outcome", "'Blink installed: roll at each actual turn end'"),
            ],
        ),
        _when(
            "spell == 'banishment' or spell == 'maze'",
            [
                _call(
                    "cast_srd_travel_exile",
                    {
                        "caster_state": "{_caster_state_id}",
                        "spell": "{spell}",
                        "slot_level": "{slot_level}",
                        "targets": "{_travel_targets}",
                        "save_dc": "{save_dc}",
                        "concentration_modifier": "{concentration_modifier}",
                        "spell_cast_id": "{spell_cast_id}",
                        "spell_cast_owner": "{_travel_source_owner}",
                        "actual_spell_level": "{actual_spell_level}",
                    },
                ),
                _set("_travel_outcome", "'source exile resolved'"),
            ],
        ),
        _when(
            "spell == 'teleport'",
            [
                _call(
                    "start_srd_teleport_resolution",
                    {
                        "caster_state": "{_caster_state_id}",
                        "slot_level": "{slot_level}",
                    },
                ),
                _set("_travel_outcome", "'paid Teleport resolution pending'"),
                _push(
                    "_spell_detail_rows",
                    {
                        "label": "'Continue paid Teleport'",
                        "value": "caster_state.teleport_resolution.id",
                    },
                ),
            ],
        ),
        _when(
            "spell == 'etherealness'",
            [
                _require(
                    "(slot_level == 7 and len(_travel_targets) == 0) or "
                    "(slot_level > 7 and len(_travel_targets) + 1 <= 3 * (slot_level - 7))",
                    "Each slot above seventh permits three total travelers, including the caster.",
                ),
                _require(
                    "len(_travel_targets) == 0 or all_targets_willing == true",
                    "Every additional Ethereal traveler must be willing.",
                ),
                _loop(
                    "_travel_targets",
                    "_travel_target",
                    [
                        _call(
                            "validate_srd_travel_companion",
                            {
                                "caster_state": "{_caster_state_id}",
                                "target_state": "{_travel_target}",
                                "range_feet": 10,
                                "sight": False,
                            },
                        )
                    ],
                ),
                _set("_travel_outcome", "'plane has no Border Ethereal: spell ended'"),
                _when(
                    "_travel_caster.metadata.plane == 'material' or "
                    "(_travel_caster.metadata.plane != 'ethereal' and "
                    "_travel_caster.metadata.borders_ethereal == true)",
                    [
                        _call(
                            "apply_srd_etherealness_target",
                            {
                                "target_state": "{_caster_state_id}",
                                "caster_state": "{_caster_state_id}",
                                "slot_level": "{slot_level}",
                                "spell_cast_id": "{spell_cast_id}",
                                "spell_cast_owner": "{_travel_source_owner}",
                                "actual_spell_level": "{actual_spell_level}",
                            },
                        ),
                        _loop(
                            "_travel_targets",
                            "_travel_target",
                            [
                                _call(
                                    "apply_srd_etherealness_target",
                                    {
                                        "target_state": "{_travel_target}",
                                        "caster_state": "{_caster_state_id}",
                                        "slot_level": "{slot_level}",
                                        "spell_cast_id": "{spell_cast_id}",
                                        "spell_cast_owner": "{_travel_source_owner}",
                                        "actual_spell_level": "{actual_spell_level}",
                                    },
                                )
                            ],
                        ),
                        _set("_travel_outcome", "'entered the Border Ethereal'"),
                    ],
                ),
            ],
        ),
        _when(
            "spell == 'misty_step' or spell == 'dimension_door'",
            [
                *_measure("{_travel_caster.position}", "{travel_destination}", "_travel_distance"),
                _set("_travel_limit", 500),
                _when("spell == 'misty_step'", [_set("_travel_limit", 30)]),
                _require(
                    "_travel_distance <= _travel_limit",
                    "The chosen destination exceeds the spell's actual teleport range.",
                ),
                _require(
                    "len(_travel_targets) <= 1",
                    "Dimension Door permits only one additional creature.",
                ),
                _require(
                    "spell != 'misty_step' or len(_travel_targets) == 0",
                    "Misty Step transports only its caster.",
                ),
                _require(
                    "len(_travel_targets) == 0 or all_targets_willing == true",
                    "Only a willing companion can use Dimension Door.",
                ),
                _loop(
                    "_travel_targets",
                    "_travel_target",
                    [
                        _require(
                            "_travel_target != _caster_state_id",
                            "The caster is not an additional companion.",
                        ),
                        _call(
                            "validate_srd_travel_companion",
                            {
                                "caster_state": "{_caster_state_id}",
                                "target_state": "{_travel_target}",
                                "range_feet": 5,
                                "sight": False,
                            },
                        ),
                        *_measure(
                            "{travel_destination}",
                            "{travel_companion_destination}",
                            "_travel_companion_distance",
                        ),
                        _require(
                            "_travel_companion_distance <= 5",
                            "The companion's chosen arrival must be within five feet of yours.",
                        ),
                    ],
                ),
                *_placement(
                    "{_caster_state_id}", "{travel_destination}", "{_travel_caster.metadata.plane}"
                ),
                _set("_travel_failed", "_travel_placement.distance > 0"),
                _when(
                    "spell == 'misty_step'",
                    [
                        _require(
                            "_travel_failed != true",
                            "Misty Step needs an actual unoccupied destination.",
                        ),
                        _call(
                            "check_srd_travel_destination_sight",
                            {"caster_state": "{_caster_state_id}", "point": "{travel_destination}"},
                        ),
                    ],
                ),
                _loop(
                    "_travel_targets",
                    "_travel_target",
                    [
                        *_placement(
                            "{_travel_target}",
                            "{travel_companion_destination}",
                            "{_travel_caster.metadata.plane}",
                        ),
                        _set(
                            "_travel_failed",
                            "_travel_failed == true or _travel_placement.distance > 0",
                        ),
                        _scene_subject("{_travel_target}", "_travel_companion"),
                        _require(
                            "_travel_companion.footprint.kind == 'box' and "
                            "_travel_caster.footprint.kind == 'box'",
                            "Exact companion arrival needs supported admitted footprints.",
                        ),
                        _require(
                            (
                                "abs(travel_companion_destination[0] - "
                                "travel_destination[0]) >= "
                                "_travel_companion.footprint.half_extents[0] + "
                                "_travel_caster.footprint.half_extents[0] or "
                                "abs(travel_companion_destination[1] - "
                                "travel_destination[1]) >= "
                                "_travel_companion.footprint.half_extents[1] + "
                                "_travel_caster.footprint.half_extents[1] or "
                                "travel_companion_destination[2] >= "
                                "travel_destination[2] + _travel_caster.height or "
                                "travel_destination[2] >= "
                                "travel_companion_destination[2] + "
                                "_travel_companion.height"
                            ),
                            "Travelers cannot choose overlapping arrival spaces.",
                        ),
                    ],
                ),
                _when(
                    "_travel_failed == true",
                    [
                        {"dice": {"expression": "4d6"}, "store_as": "_travel_failure_damage"},
                        _call(
                            "damage_srd_travel_failure",
                            {
                                "target": "{_caster_state_id}",
                                "amount": "{_travel_failure_damage.total}",
                            },
                        ),
                        _loop(
                            "_travel_targets",
                            "_travel_target",
                            [
                                _call(
                                    "damage_srd_travel_failure",
                                    {
                                        "target": "{_travel_target}",
                                        "amount": "{_travel_failure_damage.total}",
                                    },
                                )
                            ],
                        ),
                        _set(
                            "_travel_outcome",
                            "'blocked arrival: 4d6 Force damage; teleportation fails'",
                        ),
                    ],
                ),
                _when(
                    "_travel_failed != true",
                    [
                        *_move(
                            "{_caster_state_id}",
                            "{travel_destination}",
                            "{_travel_caster.metadata.plane}",
                        ),
                        _loop(
                            "_travel_targets",
                            "_travel_target",
                            _move(
                                "{_travel_target}",
                                "{travel_companion_destination}",
                                "{_travel_caster.metadata.plane}",
                            ),
                        ),
                        _set("_travel_outcome", "'teleported'"),
                    ],
                ),
            ],
        ),
        _when("spell == 'transport_via_plants'", _plant_validation()),
        _when(
            "spell == 'tree_stride'",
            [
                _set(
                    "caster_state.tree_stride",
                    {
                        "expires_tick": "state.lasting_tick + 20",
                        "last_turn": -1,
                        "started_tick": "state.lasting_tick",
                    },
                ),
                _set("caster_state.concentration_spell_id", "spell"),
                _set("caster_state.concentration_effect_family", "'travel_spell'"),
                _set("caster_state.concentration_started_tick", "state.lasting_tick"),
                _set("caster_state.concentration_target_state_id", "_caster_state_id"),
                _set("caster_state.concentration_target_state_ids", ["_caster_state_id"]),
                _set("caster_state.concentration_save_modifier", "concentration_modifier"),
            ],
        ),
        _when(
            "spell == 'word_of_recall'",
            [
                _when(
                    "travel_prepare_sanctuary == true",
                    [
                        _set(
                            "caster_state.recall_sanctuary",
                            {
                                "position": "_travel_caster.position",
                                "plane": "_travel_caster.metadata.plane",
                            },
                        ),
                        _set("_travel_outcome", "'sanctuary designated'"),
                    ],
                ),
                _when(
                    "travel_prepare_sanctuary != true and caster_state.recall_sanctuary == null",
                    [_set("_travel_outcome", "'no prepared sanctuary: no effect'")],
                ),
                _when(
                    "travel_prepare_sanctuary != true and caster_state.recall_sanctuary != null",
                    [
                        _require(
                            (
                                "len(_travel_targets) <= 5 and (len(_travel_targets) == "
                                "0 or all_targets_willing == true)"
                            ),
                            "Word of Recall permits up to five willing companions.",
                        ),
                        _loop(
                            "_travel_targets",
                            "_travel_target",
                            [
                                _call(
                                    "validate_srd_travel_companion",
                                    {
                                        "caster_state": "{_caster_state_id}",
                                        "target_state": "{_travel_target}",
                                        "range_feet": 5,
                                        "sight": False,
                                    },
                                )
                            ],
                        ),
                        _set("_travel_arrival", "caster_state.recall_sanctuary"),
                        *_placement(
                            "{_caster_state_id}",
                            "{_travel_arrival.position}",
                            "{_travel_arrival.plane}",
                        ),
                        *_move(
                            "{_caster_state_id}",
                            "{_travel_placement.position}",
                            "{_travel_arrival.plane}",
                        ),
                        _loop(
                            "_travel_targets",
                            "_travel_target",
                            _placement(
                                "{_travel_target}",
                                "{_travel_arrival.position}",
                                "{_travel_arrival.plane}",
                            )
                            + _move(
                                "{_travel_target}",
                                "{_travel_placement.position}",
                                "{_travel_arrival.plane}",
                            ),
                        ),
                    ],
                ),
            ],
        ),
        _when(
            "spell == 'plane_shift'",
            [
                _require(
                    (
                        "len(_travel_targets) <= 8 and (len(_travel_targets) == 0 or "
                        "all_targets_willing == true)"
                    ),
                    "Plane Shift permits only eight willing companions.",
                ),
                _scene_subject("{travel_destination_subject}", "_travel_anchor"),
                _require(
                    (
                        "_travel_anchor.metadata.plane != null and "
                        "_travel_anchor.metadata.plane != _travel_caster.metadata.plane"
                    ),
                    "Plane Shift travels to an actual different plane.",
                ),
                _require(
                    (
                        "caster_state.plane_shift_linked_hands != null and "
                        "_caster_state_id in caster_state.plane_shift_linked_hands"
                    ),
                    "Record the actual closed linked-hands circle before Plane Shift.",
                ),
                _require(
                    "len(caster_state.plane_shift_linked_hands) == len(_travel_targets) + 1",
                    "The linked-hands circle must contain exactly these travelers.",
                ),
                _loop(
                    "_travel_targets",
                    "_travel_target",
                    [
                        _require(
                            (
                                "_travel_target in "
                                "caster_state.plane_shift_linked_hands and "
                                "_travel_target != _caster_state_id"
                            ),
                            "Each selected traveler must actually link hands in this circle.",
                        ),
                    ],
                ),
                _set("_travel_hand_seen", []),
                _set("_travel_hand_circle", "caster_state.plane_shift_linked_hands"),
                _set(
                    "_travel_hand_previous",
                    "_travel_hand_circle[len(_travel_hand_circle) - 1]",
                ),
                _loop(
                    "_travel_hand_circle",
                    "_travel_hand_member",
                    [
                        _require(
                            "_travel_hand_member not in _travel_hand_seen and "
                            "(_travel_hand_member == _caster_state_id or "
                            "_travel_hand_member in _travel_targets)",
                            "Every actual linked-hands member must be a unique selected traveler.",
                        ),
                        _call(
                            "validate_srd_travel_companion",
                            {
                                "caster_state": "{_travel_hand_previous}",
                                "target_state": "{_travel_hand_member}",
                                "range_feet": 5,
                                "sight": False,
                            },
                        ),
                        _push("_travel_hand_seen", "_travel_hand_member"),
                        _set("_travel_hand_previous", "_travel_hand_member"),
                    ],
                ),
                _set("_travel_fork_attuned", False),
                _set("_travel_materials", "caster_state.spell_materials"),
                _loop(
                    "_travel_materials",
                    "_travel_material",
                    [
                        _when(
                            (
                                "_travel_material.id in material_item_ids and "
                                "_travel_material.attuned_plane == "
                                "_travel_anchor.metadata.plane and 'plane_shift.1' in "
                                "_travel_material.components"
                            ),
                            [_set("_travel_fork_attuned", True)],
                        )
                    ],
                ),
                _require(
                    "_travel_fork_attuned == true",
                    "Use the actual forked metal rod attuned to the destination plane.",
                ),
                _when(
                    "_travel_anchor.metadata.teleportation_circle == true",
                    [
                        _require(
                            (
                                "caster_state.known_circle_sigils != null and "
                                "_travel_anchor.metadata.sigil in "
                                "caster_state.known_circle_sigils"
                            ),
                            "Record the actual known destination sigil sequence.",
                        )
                    ],
                ),
                *_placement(
                    "{_caster_state_id}",
                    "{_travel_anchor.position}",
                    "{_travel_anchor.metadata.plane}",
                ),
                *_move(
                    "{_caster_state_id}",
                    "{_travel_placement.position}",
                    "{_travel_anchor.metadata.plane}",
                ),
                _loop(
                    "_travel_targets",
                    "_travel_target",
                    _placement(
                        "{_travel_target}",
                        "{_travel_anchor.position}",
                        "{_travel_anchor.metadata.plane}",
                    )
                    + _move(
                        "{_travel_target}",
                        "{_travel_placement.position}",
                        "{_travel_anchor.metadata.plane}",
                    ),
                ),
            ],
        ),
        _when(
            "spell == 'teleportation_circle'",
            [
                _scene_subject("{travel_destination_subject}", "_travel_anchor"),
                _require(
                    (
                        "_travel_anchor.metadata.teleportation_circle == true and "
                        "_travel_anchor.metadata.plane == _travel_caster.metadata.plane"
                    ),
                    "The destination must be an actual permanent circle on the same plane.",
                ),
                _require(
                    (
                        "caster_state.known_circle_sigils != null and "
                        "_travel_anchor.metadata.sigil in "
                        "caster_state.known_circle_sigils"
                    ),
                    "The destination's actual sigil sequence must be known.",
                ),
                *_measure("{_travel_caster.position}", "{travel_destination}", "_travel_distance"),
                _require("_travel_distance <= 10", "Draw the actual circle within ten feet."),
                _when("state.travel_circles == null", [_set("state.travel_circles", [])]),
                _set("_travel_next_turn", 1),
                _when(
                    "caster_state.turns_started != null",
                    [_set("_travel_next_turn", "caster_state.turns_started + 1")],
                ),
                _set("_travel_saved_circles", "state.travel_circles"),
                _push(
                    "_travel_saved_circles",
                    {
                        "caster": "_caster_state_id",
                        "origin": "travel_destination",
                        "destination": "_travel_anchor.id",
                        "plane": "_travel_caster.metadata.plane",
                        "expires_tick": "state.lasting_tick + 2",
                        "end_turn": "_travel_next_turn",
                        "active": True,
                    },
                ),
                _set("state.travel_circles", "_travel_saved_circles"),
            ],
        ),
        _push("_spell_summary_rows", {"label": "'Travel'", "value": "_travel_outcome"}),
    ]
    return {
        "cast_spell_travel_spell": bind_circle_cast_receipts(_tool_scene(
            (
                "Resolve source-defined travel after shared casting costs, using "
                "admitted positions and planes."
            ),
            [
                _entity("caster_state"),
                _parameter("spell"),
                _parameter("slot_level", "integer"),
                _optional("spell_cast_id", default="", model_hidden=True),
                _optional("spell_cast_owner", default=None, resolve="entity", model_hidden=True),
                _optional(
                    "actual_spell_level", "integer", -1, minimum=-1, maximum=9, model_hidden=True
                ),
                _parameter("concentration_modifier", "integer"),
                _optional("save_dc", "integer", 0, minimum=0, maximum=40),
                _optional("targets", "array", None, items={"type": "string"}),
                _parameter("all_targets_willing", "boolean"),
                _parameter("material_item_ids", "array", items={"type": "string"}),
                *deepcopy(CAST_CHOICES),
            ],
            effects,
        ))
    }


def _traversal_tools():
    plant = _public(
        _tool_scene(
            (
                "Enter an active source plant and spend five feet to emerge beside its "
                "actual destination."
            ),
            [_entity("actor_state"), _parameter("origin_subject"), _array("destination")],
            [
                _set("_travel_excluded", []),
                _set("_travel_link", None),
                _require(
                    "state.travel_plant_links != null",
                    "There is no active Transport via Plants link.",
                ),
                _set("_travel_links", "state.travel_plant_links"),
                _loop(
                    "_travel_links",
                    "_travel_candidate",
                    [
                        _when(
                            (
                                "_travel_candidate.origin == origin_subject and "
                                "state.lasting_tick < _travel_candidate.expires_tick"
                            ),
                            [_set("_travel_link", "_travel_candidate")],
                        )
                    ],
                ),
                _require("_travel_link != null", "There is no active link from this plant."),
                _scene_subject("{_actor_state_id}", "_travel_actor"),
                _scene_subject("{_travel_link.origin}", "_travel_origin"),
                _scene_subject("{_travel_link.destination}", "_travel_end"),
                *_measure(
                    "{_travel_actor.position}", "{_travel_origin.position}", "_travel_distance"
                ),
                _require(
                    (
                        "_travel_actor.metadata.plane == _travel_link.plane and "
                        "_travel_origin.metadata.plane == _travel_link.plane and "
                        "_travel_end.metadata.plane == _travel_link.plane and "
                        "_travel_distance <= 5"
                    ),
                    "Actually reach the source plant on the link's plane.",
                ),
                *_measure("{destination}", "{_travel_end.position}", "_travel_distance"),
                _require(
                    "_travel_distance <= 5",
                    "Emerge immediately beside the actual destination plant.",
                ),
                *_placement("{_actor_state_id}", "{destination}", "{_travel_link.plane}"),
                _require("_travel_placement.distance == 0", "Choose an unoccupied exit space."),
                *_movement_cost(5),
                *_move("{_actor_state_id}", "{destination}", "{_travel_link.plane}"),
                _set("_travel_cost", 5),
            ],
        ),
        "exploration",
        "Transport via Plants",
        [("Movement spent", "_travel_cost")],
    )
    tree = _public(
        _tool_scene(
            (
                "Traverse living same-kind trees using Tree Stride's actual range, "
                "size, turn limit and movement costs."
            ),
            [
                _entity("actor_state"),
                _parameter("origin_subject"),
                _parameter("destination_subject"),
                _array("destination"),
            ],
            [
                _set("_travel_excluded", []),
                _require(
                    (
                        "actor_state.tree_stride != null and state.lasting_tick < "
                        "actor_state.tree_stride.expires_tick and "
                        "actor_state.concentration_spell_id == 'tree_stride' and "
                        "actor_state.concentration_effect_family == 'travel_spell'"
                    ),
                    "Maintain the exact active Tree Stride source.",
                ),
                _set("_travel_turn", "state.lasting_tick // 2"),
                _when(
                    "combat.encounter_active == true",
                    [_set("_travel_turn", "actor_state.turns_started")],
                ),
                _require(
                    "actor_state.tree_stride.last_turn != _travel_turn",
                    "Tree Stride can be used only once on each turn.",
                ),
                _scene_subject("{_actor_state_id}", "_travel_actor"),
                _scene_subject("{origin_subject}", "_travel_origin"),
                _scene_subject("{destination_subject}", "_travel_end"),
                _set("_travel_tree_remaining", "combat.movement_remaining"),
                _when(
                    "combat.encounter_active != true",
                    [
                        _call(
                            "derive_srd_scene_movement_budget",
                            {"actor_state": "{_actor_state_id}", "mode": "walk"},
                            ["_scene_exploration_remaining"],
                        ),
                        _set("_travel_tree_remaining", "_scene_exploration_remaining"),
                    ],
                ),
                _set("_travel_cost", 10),
                _when(
                    "_travel_tree_remaining == 5",
                    [
                        _set("_travel_end", "_travel_origin"),
                        _set("_travel_cost", 5),
                    ],
                ),
                _require(
                    (
                        "_travel_origin.metadata.living_tree == true and "
                        "_travel_end.metadata.living_tree == true and "
                        "_travel_origin.metadata.tree_kind != null and "
                        "_travel_origin.metadata.tree_kind == "
                        "_travel_end.metadata.tree_kind"
                    ),
                    "Both trees must actually be living and of the same kind.",
                ),
                _require(
                    (
                        "actor_state.size_rank != null and "
                        "_travel_origin.metadata.size_rank >= actor_state.size_rank and "
                        "_travel_end.metadata.size_rank >= actor_state.size_rank"
                    ),
                    "Both living trees must be at least the traveler's size.",
                ),
                _require(
                    (
                        "_travel_origin.metadata.plane == _travel_actor.metadata.plane "
                        "and _travel_end.metadata.plane == _travel_actor.metadata.plane"
                    ),
                    "Traverse trees on the actual same plane.",
                ),
                *_measure(
                    "{_travel_actor.position}", "{_travel_origin.position}", "_travel_distance"
                ),
                _require("_travel_distance <= 5", "Actually reach the tree you enter."),
                _set("_travel_tree_locations", []),
                _set("_travel_tree_scene", "state.spatial_scene.entities"),
                _loop(
                    "_travel_tree_scene",
                    "_travel_known_tree",
                    [
                        _when(
                            "_travel_known_tree.active == true and "
                            "_travel_known_tree.id != _travel_origin.id and "
                            "_travel_known_tree.metadata.living_tree == true and "
                            "_travel_known_tree.metadata.tree_kind == "
                            "_travel_origin.metadata.tree_kind and "
                            "_travel_known_tree.metadata.plane == "
                            "_travel_origin.metadata.plane",
                            _measure(
                                "{_travel_origin.position}",
                                "{_travel_known_tree.position}",
                                "_travel_known_distance",
                            )
                            + [
                                _when(
                                    "_travel_known_distance <= 500",
                                    [
                                        _push(
                                            "_travel_tree_locations",
                                            {
                                                "subject": "_travel_known_tree.id",
                                                "position": "_travel_known_tree.position",
                                            },
                                        )
                                    ],
                                )
                            ],
                        )
                    ],
                ),
                *_measure(
                    "{_travel_origin.position}", "{_travel_end.position}", "_travel_distance"
                ),
                _require(
                    "_travel_distance <= 500",
                    "The other tree must actually be within five hundred feet.",
                ),
                *_measure("{destination}", "{_travel_end.position}", "_travel_distance"),
                _require(
                    "_travel_distance <= 5", "Appear within five feet of the destination tree."
                ),
                *_placement("{_actor_state_id}", "{destination}", "{_travel_actor.metadata.plane}"),
                _require(
                    "_travel_placement.distance == 0", "The chosen tree exit must be unoccupied."
                ),
                *_movement_cost("{_travel_cost}"),
                *_move("{_actor_state_id}", "{destination}", "{_travel_actor.metadata.plane}"),
                _set("actor_state.tree_stride.last_turn", "_travel_turn"),
            ],
        ),
        "exploration",
        "Tree Stride",
        [("Movement spent", "_travel_cost"), ("Known trees", "_travel_tree_locations")],
    )
    circle = _public(
        _tool_scene(
            (
                "Enter an actual live Teleportation Circle portal and emerge in its "
                "nearest unoccupied destination space."
            ),
            [_entity("actor_state"), _entity("caster_state")],
            [
                _set("_travel_excluded", []),
                _set("_travel_circle", None),
                _require(
                    "state.travel_circles != null",
                    "There is no active Teleportation Circle portal.",
                ),
                _set("_travel_circles", "state.travel_circles"),
                _loop(
                    "_travel_circles",
                    "_travel_candidate",
                    [
                        _when(
                            (
                                "_travel_candidate.caster == _caster_state_id and "
                                "_travel_candidate.active != false and "
                                "(combat.encounter_active == true or "
                                "state.lasting_tick < _travel_candidate.expires_tick)"
                            ),
                            [_set("_travel_circle", "_travel_candidate")],
                        )
                    ],
                ),
                _require("_travel_circle != null", "This circle's portal has closed."),
                _scene_subject("{_actor_state_id}", "_travel_actor"),
                *_measure(
                    "{_travel_actor.position}", "{_travel_circle.origin}", "_travel_distance"
                ),
                _require(
                    "_travel_actor.metadata.plane == _travel_circle.plane and "
                    "_travel_distance <= 5",
                    "The traveler must actually enter the portal's five-foot circle.",
                ),
                _scene_subject("{_travel_circle.destination}", "_travel_end"),
                *_placement(
                    "{_actor_state_id}", "{_travel_end.position}", "{_travel_circle.plane}"
                ),
                *_move(
                    "{_actor_state_id}", "{_travel_placement.position}", "{_travel_circle.plane}"
                ),
            ],
        ),
        "exploration",
        "Teleportation Circle",
        [("Destination", "_travel_placement.position")],
    )
    study = _public(
        _tool_scene(
            (
                "Commit a new actual circle sigil after one uninterrupted minute of "
                "studying that circle."
            ),
            [
                _entity("actor_state"),
                _parameter("circle_subject"),
                _parameter("elapsed_rounds", "integer", minimum=1, maximum=10),
                _parameter("uninterrupted", "boolean"),
            ],
            [
                _require(
                    "combat.encounter_active != true",
                    "Minute-long circle study currently requires exploration time.",
                ),
                _require(ACTIVE, "An Incapacitated creature cannot study a circle."),
                _scene_subject("{circle_subject}", "_travel_circle_subject"),
                _scene_subject("{_actor_state_id}", "_travel_study_actor"),
                _require(
                    (
                        "_travel_circle_subject.metadata.teleportation_circle == true "
                        "and _travel_circle_subject.metadata.sigil != null"
                    ),
                    "Study an actual admitted permanent circle.",
                ),
                *_measure(
                    "{_travel_study_actor.position}",
                    "{_travel_circle_subject.position}",
                    "_travel_study_distance",
                ),
                _call(
                    "srd_information_barriers",
                    {
                        "start": "{_travel_study_actor.position}",
                        "end": "{_travel_circle_subject.position}",
                        "rule": "solid",
                    },
                    ["_information_blocked"],
                ),
                _call(
                    "resolve_srd_sight",
                    {
                        "observer_state": "{_actor_state_id}",
                        "distance": "{ceil(_travel_study_distance)}",
                        "lighting": "{_travel_circle_subject.metadata.lighting}",
                        "obstruction": "clear",
                    },
                    ["_sight_can_see"],
                ),
                _require(
                    (
                        "_travel_study_actor.metadata.plane == "
                        "_travel_circle_subject.metadata.plane and "
                        "_information_blocked != true and _sight_can_see == true and "
                        "_travel_study_distance <= 5 and uninterrupted == true"
                    ),
                    "Study the visible circle continuously within reach.",
                ),
                _when(
                    (
                        "actor_state.circle_study == null or "
                        "actor_state.circle_study.subject != circle_subject"
                    ),
                    [_set("actor_state.circle_study", {"subject": "circle_subject", "rounds": 0})],
                ),
                _set(
                    "actor_state.circle_study.rounds",
                    "actor_state.circle_study.rounds + elapsed_rounds",
                ),
                _call(
                    "advance_srd_lasting_clock",
                    {"lasting_state": "state", "elapsed_ticks": "{elapsed_rounds * 2}"},
                ),
                _when(
                    "actor_state.circle_study.rounds >= 10",
                    [
                        _when(
                            "actor_state.known_circle_sigils == null",
                            [_set("actor_state.known_circle_sigils", [])],
                        ),
                        _when(
                            "_travel_circle_subject.metadata.sigil not in "
                            "actor_state.known_circle_sigils",
                            [
                                _set("_travel_saved_sigils", "actor_state.known_circle_sigils"),
                                _push(
                                    "_travel_saved_sigils", "_travel_circle_subject.metadata.sigil"
                                ),
                                _set("actor_state.known_circle_sigils", "_travel_saved_sigils"),
                            ],
                        ),
                        _set("actor_state.circle_study", None),
                    ],
                ),
                _set("_travel_known_sigils", "actor_state.known_circle_sigils"),
            ],
        ),
        "exploration",
        "Study circle sigil",
        [("Known sigils", "_travel_known_sigils"), ("Minute completed", "_circle_study_complete")],
    )
    return {
        "traverse_srd_plant_link": plant,
        "traverse_srd_tree_stride": tree,
        "enter_srd_teleportation_circle": circle,
        "study_srd_circle_sigil": bind_circle_study(study, ACTIVE),
    }


def _normalize(changed):
    "Materialize calculated call arguments before the runtime's simple template binding."
    serial = 0

    def normalize(effects):
        nonlocal serial
        result = []
        for effect in effects:
            if isinstance(effect, list):
                result.extend(normalize(effect))
                continue
            for key in ("on_true", "on_false"):
                if key in effect:
                    effect[key] = normalize(effect[key])
            if "for_each" in effect:
                effect["for_each"]["do"] = normalize(effect["for_each"]["do"])
            if "call" in effect:
                for key, value in effect["params"].items():
                    if (
                        isinstance(value, str)
                        and re.fullmatch(r"\{[^{}]+\}", value)
                        and not re.fullmatch(r"\{[A-Za-z_][A-Za-z0-9_.]*\}", value)
                    ):
                        serial += 1
                        local = f"_travel_argument_{serial}"
                        result.append(_set(local, value[1:-1]))
                        effect["params"][key] = "{" + local + "}"
            result.append(effect)
        return result

    for tool in changed.values():
        tool["effects"] = normalize(tool["effects"])


def _wrap(changed):
    for name in PUBLIC_TOOLS.intersection(changed):
        tool = changed[name]
        if not any(p["name"] == "lasting_state" for p in tool["parameters"]):
            continue
        node = deepcopy(tool)
        node["metadata"] = {
            "category": "internal",
            "visible_to_player": False,
            "costs_action": False,
        }
        changed[name + "_node"] = node
        tool["parameters"] = [p for p in tool["parameters"] if p["name"] != "lasting_state"]
        params = {
            p["name"]: "{"
            + (
                "_" + p["name"] + "_id"
                if p.get("resolve") in {"entity", "participant"}
                else p["name"]
            )
            + "}"
            for p in tool["parameters"]
        }
        params["lasting_state"] = "state"
        exports = [
            row["value"]["path"].removeprefix("context.").split(".", 1)[0]
            for row in tool["metadata"]["result_card"]["summary"]
            if row["value"]["path"].startswith("context.")
        ]
        tool["effects"] = [_call(name + "_node", params, exports)]


def _bind_ethereal_movement(tools, changed):
    """Bind the ordinary segment owner to actual planes and retain all other movement costs."""
    barriers = deepcopy(tools["srd_information_barriers"])
    if not any(parameter["name"] == "plane" for parameter in barriers["parameters"]):
        barriers["parameters"].append(_optional("plane", default=""))
        for effect in barriers["effects"]:
            loop = effect.get("for_each")
            if loop is not None and loop.get("as") == "_barrier":
                guard = loop["do"][0]
                guard["condition"] += (
                    " and (plane == '' or _barrier.metadata.plane == plane or "
                    "(_barrier.metadata.plane == null and plane == 'material'))"
                )
    changed["srd_information_barriers"] = barriers
    movement_owner = (
        "move_srd_scene_creature_environment_segment"
        if "move_srd_scene_creature_environment_segment" in tools
        else "move_srd_scene_creature_node"
    )
    move = deepcopy(tools[movement_owner])
    move["effects"] = [
        effect
        for effect in move["effects"]
        if effect.get("call") != "srd_information_barriers"
        and effect.get("require", {}).get("condition") != "_information_blocked != true"
        and effect.get("spatial_query", {}).get("operation") != "sweep"
        and effect.get("call") != "check_srd_progressive_travel"
        and effect.get("call") != "check_srd_form_movement_permissions"
        and effect.get("condition") != "_form_narrow_openings == true"
        and "_travel_movement_sweep" not in effect.get("require", {}).get("condition", "")
        and effect.get("set", {}).get("field")
        not in {
            "_travel_movement_plane",
            "_travel_movement_missing_plane",
            "_form_narrow_openings",
        }
        and effect.get("condition") != "_move_scene_entity.metadata.plane == null"
    ]
    for effect in move["effects"]:
        if effect.get("call") == "srd_information_movement_cost":
            effect["params"]["plane"] = "{_travel_movement_plane}"
    boundary = next(
        index
        for index, effect in enumerate(move["effects"])
        if effect.get("call") == "srd_information_movement_cost"
    )
    clearance = [
        {
            "spatial_query": {
                "scene": "state.spatial_scene",
                "operation": "sweep",
                "query": {
                    "map_id": "{state.spatial_scene.map.id}",
                    "moving_entity_id": "{_move_scene_entity.id}",
                    "destination": "{destination}",
                    "include_entities": False,
                    "metadata_filter": {
                        "key": "plane",
                        "value": "{_travel_movement_plane}",
                        "missing_matches": "{_travel_movement_missing_plane}",
                    },
                },
            },
            "store_as": "_travel_movement_sweep",
        },
        _require(
            "_travel_movement_sweep.supported == true",
            "Exact movement footprint clearance is unsupported for selected curved geometry.",
        ),
        _require(
            "_travel_movement_sweep.clear == true",
            "A solid barrier or map boundary blocks this creature's movement footprint.",
        ),
    ]
    permissions = [_set("_form_narrow_openings", False)]
    if "check_srd_form_movement_permissions" in tools:
        permissions.append(
            _call(
                "check_srd_form_movement_permissions",
                {"actor_state": "{_actor_state_id}"},
                ["_form_narrow_openings", "_form_share_creature_space", "_form_liquids_solid"],
            )
        )
    move["effects"][boundary:boundary] = [
        _set("_travel_movement_plane", "_move_scene_entity.metadata.plane"),
        _when(
            "_move_scene_entity.metadata.plane == null",
            [_set("_travel_movement_plane", "'material'")],
        ),
        _set("_travel_movement_missing_plane", "_travel_movement_plane == 'material'"),
        *permissions,
        {
            "condition": "_form_narrow_openings == true",
            "on_true": [
                _require(
                    " and ".join(
                        f"destination[{i}] >= state.spatial_scene.map.bounds.min[{i}] and "
                        f"destination[{i}] <= state.spatial_scene.map.bounds.max[{i}]"
                        for i in range(3)
                    ),
                    "The actual Gaseous destination must remain within the admitted map bounds.",
                ),
                _call(
                    "srd_information_barriers",
                    {
                        "start": "{_move_origin}",
                        "end": "{destination}",
                        "rule": "solid",
                        "plane": "{_travel_movement_plane}",
                    },
                    ["_information_blocked"],
                ),
                _require(
                    "_information_blocked != true",
                    "A solid barrier blocks this Gaseous movement segment.",
                ),
            ],
            "on_false": clearance,
        },
    ]
    if "check_srd_progressive_travel" in tools:
        boundary = next(
            index
            for index, effect in enumerate(move["effects"])
            if effect.get("set", {}).get("field") == "_travel_movement_missing_plane"
        )
        move["effects"].insert(
            boundary,
            _call(
                "check_srd_progressive_travel",
                {
                    "target_state": "{_actor_state_id}",
                    "destination_plane": "{_travel_movement_plane}",
                    "teleport": False,
                },
            ),
        )
    if "actor_state.travel_ethereal" not in str(move["effects"]):
        boundary = next(
            index
            for index, effect in enumerate(move["effects"])
            if effect.get("require", {}).get("condition") == "_route_distance > 0"
        )
        move["effects"].insert(
            boundary,
            _when(
                "actor_state.travel_ethereal != null and "
                "_move_scene_entity.metadata.plane == 'ethereal' and "
                "destination[2] != _move_origin[2]",
                [_set("_route_cost", "_route_cost + _route_distance")],
            ),
        )
    changed[movement_owner] = move


def _bind_progressive_relocation(tools, changed):
    """Apply actual source restrictions at the existing sole relocation owner."""
    move = deepcopy(
        changed["move_srd_travel_subject"]
        if "move_srd_travel_subject" in changed
        else tools["move_srd_travel_subject"]
    )
    move["effects"] = [
        effect
        for effect in move["effects"]
        if effect.get("call") != "check_srd_progressive_travel"
        and effect.get("set", {}).get("field")
        not in {"_travel_guard_target", "_travel_guard_needed"}
        and effect.get("condition") != "_travel_mover.metadata.participant_ref != null"
        and effect.get("condition") != "_travel_guard_needed == true"
        and effect.get("condition")
        != (
            "_travel_mover.metadata.participant_ref == null and "
            "(_travel_mover.metadata.is_object == true or "
            "_travel_mover.metadata.creature_type == 'spell_force')"
        )
    ]
    if "check_srd_progressive_travel" in tools:
        # Bind the restriction to the admitted mover before its sole position mutation.
        boundary = next(
            index
            for index, effect in enumerate(move["effects"])
            if effect.get("set", {}).get("field") == "_travel_patch"
        )
        move["effects"][boundary:boundary] = [
            _set("_travel_guard_target", "_travel_mover.id"),
            _when(
                "_travel_mover.metadata.participant_ref != null",
                [_set("_travel_guard_target", "_travel_mover.metadata.participant_ref")],
            ),
            _set("_travel_guard_needed", True),
            _when(
                "_travel_mover.metadata.participant_ref == null and "
                "(_travel_mover.metadata.is_object == true or "
                "_travel_mover.metadata.creature_type == 'spell_force')",
                [_set("_travel_guard_needed", False)],
            ),
            _when(
                "_travel_guard_needed == true",
                [
                    _call(
                        "check_srd_progressive_travel",
                        {
                            "target_state": "{_travel_guard_target}",
                            "destination_plane": "{plane}",
                            "teleport": "{teleport}",
                        },
                    )
                ],
            ),
        ]
    changed["move_srd_travel_subject"] = move


def _bind_hound_relocation(tools, changed):
    """Refresh actual Hound anchors immediately after the existing relocation commits."""
    if "refresh_srd_hound_separation" not in tools:
        return
    move = deepcopy(changed.get("move_srd_travel_subject", tools["move_srd_travel_subject"]))
    move["effects"] = [
        effect for effect in move["effects"] if "refresh_srd_hound_separation" not in str(effect)
    ]
    boundary = next(
        index for index, effect in enumerate(move["effects"]) if "spatial_mutation" in effect
    )
    move["effects"].insert(
        boundary + 1,
        _when("state.has_hound_sources == true", [_call("refresh_srd_hound_separation", {})]),
    )
    changed["move_srd_travel_subject"] = move


def bind_runtime_consumers(text):
    """Bind final point sight, source permissions and the final wrapped movement segment."""
    tools = _load_runtime(text)["tools"]
    changed = _destination_sight_tool(tools)
    if "validate_srd_teleport_cast" in tools:
        teleport = _teleport_tools(tools)
        changed["validate_srd_teleport_cast"] = teleport["validate_srd_teleport_cast"]
        changed["release_srd_teleport_carried_object"] = teleport[
            "release_srd_teleport_carried_object"
        ]
    _bind_progressive_relocation(tools, changed)
    _bind_hound_relocation(tools, changed)
    _bind_ethereal_movement(tools, changed)
    bind_circle_consumers(tools, changed)
    if "apply_srd_blink_target" in tools:
        changed.update(blink_tools(tools))
        bind_blink_boundaries(tools, changed)
    if "apply_srd_travel_exile_target" in tools:
        changed.update(exile_tools(tools))
        bind_exile_lifecycle(tools, changed)
    bind_demiplane_consumers(tools, changed)
    if "install_srd_magnificent_mansion" in tools:
        changed.update(dwelling_tools(tools))
        changed.update(furnishing_tools())
        changed.update(banquet_tools())
        changed.update(servant_tools(tools))
        bind_dwelling_consumers(tools, changed)
        bind_furnishing_consumers(tools, changed)
        bind_servant_consumers(tools, changed)
    if "install_srd_rope_trick" in tools:
        changed.update(rope_tools(tools))
        changed.update(rope_state_tools(tools))
        changed.update(chest_tools(tools))
        bind_rope_consumers(tools, changed)
        bind_rope_return_consumers(tools, changed)
    if "install_srd_gate" in tools:
        changed.update(gate_tools(tools))
        changed.update(gate_transit_tools(tools))
        bind_gate_consumers(tools, changed)
    bind_chest_consumers(tools, changed)
    bind_passwall_lifecycle(tools, changed)
    if "install_srd_levitate" in tools:
        changed.update(levitate_tools(tools))
        changed.update(control_tools(tools))
        changed.update(grip_tools(tools))
        bind_levitate_consumers(tools, changed)
        bind_grip_consumers(tools, changed)
    if "install_srd_telekinesis" in tools:
        changed.update(telekinesis_tools(tools))
        bind_telekinesis_consumers(tools, changed)
    _normalize(changed)
    _wrap(changed)
    return _replace_tools(text, changed)


def extend_runtime(text):
    """Regenerate travel tools and only their shared cast/concentration consumers."""
    tools = _load_runtime(text)["tools"]
    changed = (
        _tools()
        | _target_tools()
        | _teleport_tools(tools)
        | _ethereal_tools()
        | _perception_tool()
        | _casting_tool()
        | _traversal_tools()
        | circle_tools()
        | _destination_sight_tool(tools)
        | blink_tools(tools)
        | exile_tools(tools)
        | demiplane_tools(tools)
        | dwelling_tools(tools)
        | furnishing_tools()
        | banquet_tools()
        | servant_tools(tools)
        | rope_tools(tools)
        | rope_state_tools(tools)
        | chest_tools(tools)
        | gate_tools(tools)
        | gate_transit_tools(tools)
        | passwall_tools(tools)
        | levitate_tools(tools)
        | control_tools(tools)
        | grip_tools(tools)
        | telekinesis_tools(tools)
    )
    _bind_progressive_relocation(tools, changed)
    bind_circle_consumers(tools, changed)
    _normalize(changed)
    _wrap(changed)
    cast = deepcopy(tools["cast_spell"])
    names = {p["name"] for p in cast["parameters"]}
    cast["parameters"] += [deepcopy(p) for p in CAST_CHOICES if p["name"] not in names]
    cast["effects"] = [
        e
        for e in cast["effects"]
        if "_spell_definition.family == 'travel_spell'" not in e.get("condition", "")
        and "validate_srd_teleport_cast" not in str(e)
        and "validate_srd_travel_exile_cast" not in str(e)
        and "validate_srd_demiplane_cast" not in str(e)
        and "validate_srd_passwall_cast" not in str(e)
        and "validate_srd_levitate_cast" not in str(e)
        and "validate_srd_telekinesis_cast" not in str(e)
        and "validate_srd_telekinesis_start" not in str(e)
        and "validate_srd_magnificent_mansion" not in str(e)
        and "validate_srd_rope_trick" not in str(e)
        and "validate_srd_secret_chest_cast" not in str(e)
        and "validate_srd_gate_cast" not in str(e)
        and "validate_srd_circle_cast" not in str(e)
    ]
    cost_boundary = next(
        i
        for i, effect in enumerate(cast["effects"])
        if effect.get("call") == "resolve_srd_spell_materials"
    )
    cast["effects"].insert(cost_boundary, _when("spell == 'teleportation_circle'", [
        _call("validate_srd_circle_cast", {
            "caster_state": "{_caster_state_id}",
            "destination_subject": "{travel_destination_subject}",
            "point": "{travel_destination}",
            "depiction_depth": "{circle_depiction_depth}",
        }),
    ]))
    cast["effects"].insert(cost_boundary, _when("spell == 'gate'", [
        _require("targets == null or len(targets) == 0",
                 "Gate opens paired portals; creature summoning remains unsupported."),
        _call("validate_srd_gate_cast", {
            "caster_state": "{_caster_state_id}", "source_id": "{gate_id}",
            "destination_subject": "{gate_destination_subject}", "point": "{travel_destination}",
            "diameter": "{gate_diameter}", "front": "{gate_front}",
            "depiction_depth": "{gate_depiction_depth}",
        }),
    ]))
    cast["effects"].insert(cost_boundary, _when("spell == 'secret_chest'", [
        _require("targets == null or len(targets) == 0",
                 "Select the actual chest and replica through object intent."),
        _call("validate_srd_secret_chest_cast", {
            "caster_state": "{_caster_state_id}", "chest_id": "{secret_chest_id}",
            "replica_id": "{secret_chest_replica}",
            "ethereal_anchor": "{secret_chest_ethereal_anchor}",
            "material_item_ids": "{material_item_ids}",
        }),
    ]))
    cast["effects"].insert(cost_boundary, _when("spell == 'rope_trick'", [
        _require(
            "targets == null or len(targets) == 0",
            "Select the actual rope through Rope Trick's object intent.",
        ),
        _call("validate_srd_rope_trick", {
            "caster_state": "{_caster_state_id}", "rope_id": "{rope_trick_rope}",
            "dwelling_id": "{rope_trick_id}", "designated": [],
            "floor_cells": "{rope_trick_floor_cells}",
            "wall_thickness": "{rope_trick_wall_thickness}",
            "door_depth": "{rope_trick_door_depth}", "lighting": "{rope_trick_lighting}",
        }),
    ]))
    cast["effects"].insert(cost_boundary, _when("spell == 'magnificent_mansion'", [
        _require(
            "targets == null or len(targets) == 0",
            "Designate guests with the Mansion's actual guest intent.",
        ),
        _call("validate_srd_magnificent_mansion", {
            "caster_state": "{_caster_state_id}", "dwelling_id": "{mansion_id}",
            "point": "{travel_destination}", "designated": "{mansion_designated}",
            "floor_cells": "{mansion_floor_cells}", "wall_thickness": "{mansion_wall_thickness}",
            "door_depth": "{mansion_door_depth}", "lighting": "{mansion_lighting}",
            "furnishings": "{mansion_furnishings}",
            "banquet": "{mansion_banquet}",
            "servants": "{mansion_servants}",
        }),
    ]))
    cast["effects"].insert(cost_boundary, _when("spell == 'levitate'", [
        _call("validate_srd_levitate_cast", {
            "caster_state": "{_caster_state_id}", "subject": "{levitate_subject}",
            "rise": "{levitate_rise}", "targets": "{targets}",
            "all_targets_willing": "{all_targets_willing}",
        }),
    ]))
    cast["effects"].insert(cost_boundary, _when("spell == 'telekinesis'", [
        _call("validate_srd_telekinesis_start", {
            "caster_state": "{_caster_state_id}", "subject": "{telekinesis_subject}",
            "destination": "{telekinesis_destination}", "targets": "{targets}",
            "operation": "{telekinesis_operation}",
        }),
    ]))
    cast["effects"].insert(cost_boundary, _when("spell == 'passwall'", [
        _call("validate_srd_passwall_cast", {
            "caster_state": "{_caster_state_id}", "surface_id": "{passwall_surface_id}",
            "point": "{travel_destination}", "face": "{passwall_face}", "targets": "{targets}",
            "width": "{passwall_width}", "height": "{passwall_height}", "depth": "{passwall_depth}",
        }),
    ]))
    cast["effects"].insert(cost_boundary, _when("spell == 'demiplane'", [
        _call("validate_srd_demiplane_cast", {
            "caster_state": "{_caster_state_id}", "surface_id": "{demiplane_surface_id}",
            "point": "{travel_destination}", "face": "{demiplane_face}", "targets": "{targets}",
            "room_id": "{demiplane_room}", "material": "{demiplane_material}",
            "wall_thickness": "{demiplane_wall_thickness}",
            "door_width": "{demiplane_door_width}", "door_height": "{demiplane_door_height}",
            "door_depth": "{demiplane_door_depth}",
        }),
    ]))
    cast["effects"].insert(
        cost_boundary,
        _when(
            "spell == 'teleport'",
            [
                _call(
                    "validate_srd_teleport_cast",
                    {
                        "caster_state": "{_caster_state_id}",
                        "destination_subject": "{travel_destination_subject}",
                        "teleport_object_subject": "{teleport_object_subject}",
                        "teleport_holder_willing": "{teleport_holder_willing}",
                        "targets": "{targets}",
                        "all_targets_willing": "{all_targets_willing}",
                    },
                ),
            ],
        ),
    )
    cast["effects"].insert(
        cost_boundary,
        _when(
            "spell == 'banishment' or spell == 'maze'",
            [
                _set("_exile_selected_slot", "_spell_definition.base_level"),
                *[
                    _when(
                        f"_cast_resource == 'spell_slot_{level}'",
                        [
                            _set("_exile_selected_slot", level),
                        ],
                    )
                    for level in range(1, 10)
                ],
                _call(
                    "validate_srd_travel_exile_cast",
                    {
                        "caster_state": "{_caster_state_id}",
                        "spell": "{spell}",
                        "slot_level": "{_exile_selected_slot}",
                        "targets": "{targets}",
                    },
                ),
            ],
        ),
    )
    params = {p["name"]: "{" + p["name"] + "}" for p in CAST_CHOICES}
    params.update(
        caster_state="{_caster_state_id}",
        spell="{spell}",
        slot_level="{_slot_level}",
        concentration_modifier="{_casting_profile.concentration_save_modifier}",
        save_dc="{_casting_profile.save_dc}",
        targets="{targets}",
        all_targets_willing="{all_targets_willing}",
        material_item_ids="{material_item_ids}",
    )
    branch = _when(
        "_spell_definition.family == 'travel_spell'",
        [_call("cast_spell_travel_spell", params, ["_spell_summary_rows", "_spell_detail_rows"])],
    )
    last = max(
        i
        for i, effect in enumerate(cast["effects"])
        if "_spell_definition.family ==" in effect.get("condition", "")
    )
    cast["effects"].insert(last + 1, branch)
    changed["cast_spell"] = cast
    end = deepcopy(tools["end_srd_concentration_node"])
    if "source_state.tree_stride" not in str(end["effects"]):
        end["effects"].insert(
            0,
            _when(
                "source_state.concentration_effect_family == 'travel_spell'",
                [_set("source_state.tree_stride", None)],
            ),
        )
    changed["end_srd_concentration_node"] = end
    end_turn = deepcopy(tools["end_combat_turn"])
    if "close_srd_travel_turn" not in str(end_turn["effects"]):
        boundary = next(
            index
            for index, effect in enumerate(end_turn["effects"])
            if effect.get("call") == "process_srd_turn_effects"
        )
        end_turn["effects"].insert(
            boundary,
            _call(
                "close_srd_travel_turn",
                {"actor_state": "{_actor_id}", "boundary_turn": "{_ending_turn}"},
            ),
        )
    changed["end_combat_turn"] = end_turn
    clock = deepcopy(tools["advance_srd_lasting_clock"])
    if "refresh_srd_etherealness_sources" not in str(clock["effects"]):
        clock["effects"].append(_call("refresh_srd_etherealness_sources", {}))
    changed["advance_srd_lasting_clock"] = clock
    relation = deepcopy(tools["resolve_srd_scene_relation_node"])
    if "actor_state.travel_ethereal" not in str(relation["effects"]):
        relation["effects"].append(
            _when(
                "actor_state.travel_ethereal != null and "
                "_scene_actor_entity.metadata.plane == 'ethereal' and "
                "_info_scene_entity.metadata.plane == actor_state.travel_ethereal.return_plane "
                "and _scene_distance <= 60 and _scene_exposure.visible_samples > 0 "
                "and _sight_can_see == true",
                [_set("_scene_can_see", True)],
            )
        )
    changed["resolve_srd_scene_relation_node"] = relation
    if "actor_state.travel_blink" not in str(relation["effects"]):
        relation["effects"].append(
            _when(
                "actor_state.travel_blink != null and actor_state.travel_blink.phase == true "
                "and state.lasting_tick < actor_state.travel_blink.expires_tick "
                "and _scene_actor_entity.metadata.plane == 'ethereal' "
                "and _info_scene_entity.metadata.plane == actor_state.travel_blink.return_plane "
                "and _scene_distance <= 60 and _scene_exposure.visible_samples > 0 "
                "and _sight_can_see == true",
                [_set("_scene_can_see", True)],
            )
        )
    relation_wrapper = deepcopy(tools["resolve_srd_scene_relation"])
    relation_wrapper["effects"] = [
        effect
        for effect in relation_wrapper["effects"]
        if "actor_state.travel_ethereal" not in effect.get("condition", "")
    ]
    changed["resolve_srd_scene_relation"] = relation_wrapper
    _bind_ethereal_movement(tools, changed)
    bind_blink_boundaries(tools, changed)
    bind_exile_lifecycle(tools, changed)
    bind_demiplane_consumers(tools, changed)
    bind_dwelling_consumers(tools, changed)
    bind_furnishing_consumers(tools, changed)
    bind_servant_consumers(tools, changed)
    bind_rope_consumers(tools, changed)
    bind_rope_return_consumers(tools, changed)
    bind_gate_consumers(tools, changed)
    bind_chest_consumers(tools, changed)
    bind_passwall_lifecycle(tools, changed)
    bind_levitate_consumers(tools, changed)
    bind_grip_consumers(tools, changed)
    bind_telekinesis_consumers(tools, changed)
    _normalize(changed)
    text = _replace_tools(text, changed)
    if "      travel_spell:" not in text:
        properties = {
            key: {
                "type": "boolean"
                if type(value) is bool
                else "integer"
                if type(value) is int
                else "string"
            }
            for definition in PROFILES.values()
            for key, value in definition.items()
            if key != "family"
        }
        schema = {
            "type": "object",
            "properties": properties,
            "required": [key for key in properties if key != "duration_rounds"],
            "additionalProperties": False,
        }
        fragment = yaml.dump({"travel_spell": schema}, Dumper=_RuntimeDumper, sort_keys=False)
        text = text.replace(
            "      divination_spell:",
            "".join("      " + line for line in fragment.splitlines(True))
            + "\n      divination_spell:",
            1,
        )
    return text
