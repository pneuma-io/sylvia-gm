"""Admit the first combat scene before the existing encounter and initiative owners."""

from .build_srd_5_2_1_spells import _tool_schema
from .srd_control_behaviors import _each, _push
from .srd_timed_actor_defenses import _call, _require, _set

ROOTS = ("start_combat_encounter_entities", "start_group_combat_encounter")
PARAMETER = {
    "name": "combat_scenes",
    "type": "array",
    "required": False,
    "default": [],
    "min_items": 0,
    "max_items": 1,
    "items": {"type": "object"},
}
GUIDANCE = (
    "\nCoordinate combat requires a saved spatial scene. For the first encounter, supply "
    "one scene in combat_scenes using the spatial_scene format 1 content definition: "
    "format=1; map with "
    "id, revision, bounds.min/max (three coordinates), grid.cell_size/unit='ft'/origin "
    "(two coordinates), and layers (id/min_elevation/max_elevation); entities, geometry, "
    "zones and terrain arrays. Each combatant needs exactly one active creature body: "
    "id, map_id, position (three coordinates), footprint (box.half_extents or circle.radius), "
    "height, facing (two coordinates), active, movement, visibility, and metadata with "
    "participant_ref equal to its saved actor ID, creature_type, plane and lighting. "
    "Choose physical positions and body dimensions from the encounter fiction; do not "
    "invent a target-distance substitute. Include known blockers and terrain. Declare "
    "metadata.player.label/kind only on bodies and geometry the player knows. "
    "An already saved scene is reused: omit combat_scenes rather than replacing positions, "
    "zones or persistent spell objects. Invalid scenes fail before initiative. "
    "Exploration does not require scene setup or map movement."
)


def _setup_effects(name):
    """Validate one authoritative scene and every selected actor before any initiative roll."""
    combatants = [_set("_setup_combatants", [])]
    if name == ROOTS[0]:
        combatants += [
            _push("_setup_combatants", "_first_actor_id"),
            _push("_setup_combatants", "_second_actor_id"),
        ]
    else:
        combatants = [_set("_setup_combatants", "combatants")]
    lookup = _call(
        "srd_information_scene_entity",
        {"subject": "{_setup_actor}", "lasting_state": "{_lasting_state_id}"},
    )
    lookup["export_context"] = {"_setup_body": "_info_scene_entity"}
    return [
        {
            "condition": "state.spatial_scene == null",
            "on_true": [
                _require(
                    "len(combat_scenes) == 1",
                    "Starting coordinate combat needs one combat_scenes entry or a saved scene.",
                ),
                _set("state.spatial_scene", "combat_scenes[0]"),
            ],
            "on_false": [
                _require(
                    "len(combat_scenes) == 0",
                    "A combat scene is already saved. Omit combat_scenes to preserve "
                    "its positions and spell objects.",
                )
            ],
        },
        {
            "spatial_query": {"scene": "state.spatial_scene", "operation": "snapshot"},
            "store_as": "_setup_snapshot",
        },
        _require(
            "state.spatial_scene.map.grid.unit == 'ft'",
            "SRD combat positions and body dimensions must use feet.",
        ),
        *combatants,
        _each(
            "_setup_combatants",
            "_setup_actor",
            [
                lookup,
                _require(
                    "_setup_body.metadata.participant_ref == _setup_actor and "
                    "_setup_body.metadata.is_object != true and "
                    "_setup_body.metadata.creature_type != null and "
                    "_setup_body.metadata.plane != null and _setup_body.metadata.plane != ''",
                    "Every combatant needs one active creature body bound to "
                    "its actual actor and plane.",
                ),
            ],
        ),
    ]


def _pair_roll_widgets():
    """Display each actor's recorded initiative faces and selected total without rolling again."""
    return [
        _set("_setup_initiative_equations", []),
        _each(
            "_initiative_order",
            "_setup_visual_entry",
            [_push("_setup_initiative_equations", {
                "label": "_setup_visual_entry.label",
                "value": "'Initiative'",
                "equation": {
                    "die_sides": 20,
                    "roll": "_setup_visual_entry.roll.total",
                    "rolls": "_setup_visual_entry.roll.results",
                    "modifier": "_setup_visual_entry.modifier",
                    "total": "_setup_visual_entry.initiative_result",
                },
            })],
        ),
    ]


def bind_combat_scene_setup(runtime, record):
    """Extend encounter entry only; retain existing initiative, turn and paid spell behavior."""
    tools = runtime["tools"]
    if "srd_information_scene_entity" not in tools:
        raise ValueError("Combat setup requires the existing authoritative body lookup")
    declarations = {row["name"]: row for row in record["tools"]}
    for name in ROOTS:
        owner = tools[name]
        if any(parameter["name"] == PARAMETER["name"] for parameter in owner["parameters"]):
            raise ValueError("Combat scene parameter is already bound: " + name)
        if not any(parameter["name"] == "lasting_state" for parameter in owner["parameters"]):
            raise ValueError("Encounter state binding changed: " + name)
        owner["parameters"].append(dict(PARAMETER))
        owner["effects"] = _setup_effects(name) + owner["effects"]
        if name == ROOTS[0]:
            card = owner["metadata"]["result_card"]
            if "equations" in card:
                raise ValueError("Two-creature initiative widgets are already bound")
            owner["effects"] += _pair_roll_widgets()
            card["equations"] = {"path": "context._setup_initiative_equations"}
        owner["description"] += GUIDANCE
        # Keep existing surprise guidance, reference annotations and model-hidden state fields.
        declarations[name]["input_schema"]["properties"][PARAMETER["name"]] = (
            _tool_schema(owner)["properties"][PARAMETER["name"]]
        )
    return list(ROOTS)
