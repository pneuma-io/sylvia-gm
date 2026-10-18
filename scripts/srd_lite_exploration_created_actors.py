"""Birth selected creatures from established fiction; admit their real bodies at combat entry."""

from copy import deepcopy

from .build_srd_5_2_1_mechanics import _tool_schema
from .srd_control_behaviors import _each, _push
from .srd_scene_relations import _snapshot
from .srd_spell_summoning import _created_actor_placement_tools
from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _tool

OWNER = "create_srd_source_actor_scene"
STEED = "cast_spell_independent_created_creature"
RECONCILE = "reconcile_srd_lite_exploration_created_actors"
ONE = "reconcile_srd_lite_exploration_created_actor"
PLACEMENT = "qualify_srd_lite_admitted_created_actor_body"
IDS = "exploration_created_actor_ids"
RECORDS = "exploration_created_actor_records"
SCENE = "_created_actor_has_scene_proxy"
PROXIMITY = "establish_srd_lite_steed_healing_proximity"
PROXIMITY_FIELD = "exploration_steed_healing_proximity"
LIFE_BOND = "qualify_srd_lite_steed_life_bond"
EXPLORATION = (
    "combat.encounter_active != true and (spell == 'find_steed' or spell == 'summon_dragon')"
)
CHOICES = (
    ("created_actor_placement", "string", "", {}),
    ("created_actor_distance", "number", -1, {"minimum": -1, "maximum": 10000}),
    ("created_actor_space_unoccupied", "boolean", False, {}),
    ("created_actor_space_visible", "boolean", False, {}),
    ("created_actor_clear_path", "boolean", False, {}),
)
GUIDANCE = (
    "\nOutside combat, Find Steed and Summon Dragon use established fictional placement. "
    "Supply created_actor_placement (a short established location), created_actor_distance "
    "in feet, created_actor_space_unoccupied=true and created_actor_clear_path=true. "
    "Summon Dragon also requires created_actor_space_visible=true; Find Steed does not. "
    "These facts do not create coordinates. The paid creature keeps its exact saved ID "
    "and source statistics. At later combat entry, explicitly include its same ID, printed "
    "Large box footprint, actual height/position/plane/ambient lighting and source metadata "
    "in combat_scenes. Combat movement, mounting and attacks use that admitted body. "
    "Exploration Steed abilities and mounting use current fiction as described by their tools. "
    "Combat casting still uses the authoritative scene and its actual placement/sight checks."
)
HEALING_GUIDANCE = (
    "\nOutside combat, when this spell heals a caster with an active Otherworldly Steed, "
    "first call establish_srd_lite_steed_healing_proximity with that exact caster and Steed, "
    "their current established nearest separation in feet, and whether they currently share "
    "a plane. This fact authorizes one healing qualification at the current lasting tick; "
    "establish it immediately before the healing event. Birth placement is never current "
    "proximity. Combat Life Bond always measures the admitted actual bodies."
)


def _lists(rows):
    yield rows
    for row in rows:
        for branch in ("on_true", "on_false"):
            yield from _lists(row.get(branch, []))
        if "for_each" in row:
            yield from _lists(row["for_each"]["do"])


def _choose(condition, on_true, on_false=()):
    return {"condition": condition, "on_true": list(on_true), "on_false": list(on_false)}


def _parameters():
    return [
        _parameter(name, kind, required=False, default=default, **bounds)
        for name, kind, default, bounds in CHOICES
    ]


def _bound(rows):
    return _tool("Bind canonical saved package state.", [], rows)["effects"]


def _ensure_lasting_state(owner):
    """Bind saved-state reads added to owners that originally only called other tools."""
    if not any(parameter["name"] == "lasting_state" for parameter in owner["parameters"]):
        owner["parameters"].append(
            _parameter("lasting_state", resolve="entity", required=False, default="state")
        )


def _capture(prefix, source):
    return [
        _choose(
            source + "." + name + " != null",
            [_set(prefix + name, source + "." + name)],
            [_set(prefix + name, default)],
        )
        for name, _, default, _ in CHOICES
    ]


def _exploration_birth():
    return _bound(
        [
            _require(
                "summon_id != null and len(summon_id) > 0 and len(summon_id) <= 128",
                "Choose the created creature's stable saved identity.",
            ),
            _require(
                "len(created_actor_placement) > 0 and len(created_actor_placement) <= 240 and "
                "created_actor_distance >= 0",
                "Declare an established fictional placement and distance for this creature.",
            ),
            _require(
                "created_actor_distance <= range_feet",
                "The creature's established space is outside spell range.",
            ),
            _require(
                "created_actor_space_unoccupied == true and created_actor_clear_path == true",
                "The creature needs an established unoccupied space and clear spell path.",
            ),
            _require(
                "spell != 'summon_dragon' or created_actor_space_visible == true",
                "The caster must see the Dragon's established unoccupied space.",
            ),
            _require("half_extent == 5", "Preserve the selected source's printed Large footprint."),
            _choose(
                "state." + IDS + " == null",
                [_set("state." + IDS, []), _set("state." + RECORDS, {})],
            ),
            _set("_birth_ids", "state." + IDS),
            _choose(
                "summon_id not in _birth_ids",
                [
                    _require(
                        "len(_birth_ids) < 256",
                        "The saved exploration creature placement bound is full.",
                    ),
                    _push("_birth_ids", "summon_id"),
                    _set("state." + IDS, "_birth_ids"),
                ],
            ),
            _set(
                "state." + RECORDS + ".{summon_id}",
                {
                    "id": "summon_id",
                    "spell": "spell",
                    "caster": "_caster_state_id",
                    "placement": "created_actor_placement",
                    "distance": "created_actor_distance",
                    "half_extent": "half_extent",
                    "height": None,
                    "body_admitted": False,
                },
            ),
            _choose(
                "created_actor_height != null",
                [_set("state." + RECORDS + ".{summon_id}.height", "created_actor_height")],
            ),
            _set(SCENE, False),
        ]
    )


def _unplaced(identity):
    return (
        f"lasting_state.{RECORDS} != null and {identity} in lasting_state.{RECORDS} and "
        f"lasting_state.{RECORDS}[{identity}] != null and "
        f"lasting_state.{RECORDS}[{identity}].body_admitted != true"
    )


def _without_body(identity):
    return (
        "(" + _unplaced(identity) + ") or (combat.encounter_active != true and "
        "lasting_state.spatial_scene == null)"
    )


def _pending_fiction_flag(identity):
    """A pending fictional step makes cached exploration coordinates stale."""
    return _bound([
        _set("_fiction_steed_step", False),
        _choose(
            "combat.encounter_active != true and "
            "lasting_state.exploration_steed_placements != null and "
            + identity + " in lasting_state.exploration_steed_placements and "
            "lasting_state.exploration_steed_placements[" + identity + "].pending == true",
            [_set("_fiction_steed_step", True)],
        ),
    ])


def _admitted_placement():
    """Pass the actual body through native placement using genuine tool parameters."""
    original = _created_actor_placement_tools()[OWNER]["effects"]
    start = next(
        index
        for index, row in enumerate(original)
        if row.get("set", {}).get("field") == "_created_actor_missing_plane_matches"
    )
    tail = deepcopy(original[start:])
    return _tool(
        "Qualify an explicitly admitted created creature's full body using native placement.",
        [
            _parameter("summon_id"),
            _parameter(
                "created_actor_position",
                "array",
                items={"type": "number"},
                min_items=3,
                max_items=3,
            ),
            _parameter("created_actor_plane"),
        ],
        [_set("_created_actor_plane", "created_actor_plane"), *_snapshot(), *tail],
    )


def _reconcile_one():
    """Validate the paid actor and saved actual body before native placement admission."""
    live_steed = (
        "_birth.spell == 'find_steed' and actor_state.created_active == true and "
        "actor_state.created_spell_id == _birth.spell and "
        "actor_state.created_caster_state_id == _caster_state_id and "
        "caster_state.created_steed_id == _actor_state_id and "
        "lasting_state.created_actor_records[_actor_state_id].active == true"
    )
    live_dragon = (
        "_birth.spell == 'summon_dragon' and actor_state.summon_active == true and "
        "actor_state.summon_spell_id == _birth.spell and "
        "actor_state.summon_caster_state_id == _caster_state_id and "
        "caster_state.concentration_spell_id == _birth.spell and "
        "caster_state.concentration_target_state_id == _actor_state_id and "
        "lasting_state.lasting_tick < caster_state.concentration_expires_tick"
    )
    body_checks = [
        _require(
            "_birth.id == _actor_state_id and _birth.caster == _caster_state_id",
            "Preserve the paid exploration creature's exact saved actor and caster.",
        ),
        _call(
            "srd_information_scene_entity",
            {"subject": "{_actor_state_id}"},
            ("_info_scene_entity", "_info_position"),
        ),
        _set("_birth_body", "_info_scene_entity"),
        _require(
            "_birth_body.id == _actor_state_id and _birth_body.active == true and "
            "_birth_body.metadata.participant_ref == _actor_state_id and "
            "_birth_body.metadata.is_object != true and "
            "_birth_body.metadata.source_ref == _birth.spell and "
            "_birth_body.metadata.source_caster == _caster_state_id and "
            "_birth_body.metadata.creature_type == actor_state.creature_type",
            "Explicitly admit this paid creature's exact live body and source metadata.",
        ),
        _require(
            "_birth_body.footprint.kind == 'box' and "
            "_birth_body.footprint.half_extents[0] == _birth.half_extent and "
            "_birth_body.footprint.half_extents[1] == _birth.half_extent and "
            "_birth_body.height > 0 and "
            "(_birth.height == null or _birth_body.height == _birth.height)",
            "Preserve the creature's printed Large footprint and established physical height.",
        ),
        _require(
            "_birth_body.metadata.plane != null and "
            "(_birth_body.metadata.lighting == 'bright' or "
            "_birth_body.metadata.lighting == 'dim' or "
            "_birth_body.metadata.lighting == 'darkness' or "
            "_birth_body.metadata.lighting == 'magical_darkness')",
            "Admit the creature's actual plane and ambient lighting.",
        ),
        *[
            _require(
                f"_birth_body.position[{axis}] - _birth.half_extent >= "
                f"lasting_state.spatial_scene.map.bounds.min[{axis}] and "
                f"_birth_body.position[{axis}] + _birth.half_extent <= "
                f"lasting_state.spatial_scene.map.bounds.max[{axis}]",
                "The full created body must fit its admitted map.",
            )
            for axis in (0, 1)
        ],
        _require(
            "_birth_body.position[2] >= lasting_state.spatial_scene.map.bounds.min[2] and "
            "_birth_body.position[2] + _birth_body.height <= "
            "lasting_state.spatial_scene.map.bounds.max[2]",
            "The full created body must fit its admitted map.",
        ),
        _call(
            PLACEMENT,
            {
                "summon_id": "{_actor_state_id}",
                "created_actor_position": "{_birth_body.position}",
                "created_actor_plane": "{_birth_body.metadata.plane}",
                "lasting_state": "{_lasting_state_id}",
            },
        ),
        _set("lasting_state." + RECORDS + ".{_actor_state_id}.body_admitted", True),
        _choose(
            "lasting_state." + PROXIMITY_FIELD + " != null",
            [
                _choose(
                    "_caster_state_id in lasting_state." + PROXIMITY_FIELD,
                    [_set("lasting_state." + PROXIMITY_FIELD + ".{_caster_state_id}", None)],
                ),
            ],
        ),
        _choose(
            "_birth.spell == 'summon_dragon'", [_set("actor_state.summon_has_scene_proxy", True)]
        ),
    ]
    return _tool(
        "Reconcile the paid creature with its explicitly admitted full combat body.",
        [_entity("actor_state"), _entity("caster_state")],
        [
            _set("_birth", "state." + RECORDS + "[_actor_state_id]"),
            _set("_birth_live_steed", live_steed),
            _set("_birth_live_dragon", live_dragon),
            _choose(
                "actor_state.is_alive == true and "
                "(_birth_live_steed == true or _birth_live_dragon == true)",
                body_checks,
            ),
        ],
    )


def _reconcile():
    return _tool(
        "Keep saved exploration creatures linked when the GM admits their actual combat bodies.",
        [],
        [
            _choose(
                "state." + IDS + " != null",
                [
                    _set("_birth_ids", "state." + IDS),
                    _each(
                        "_birth_ids",
                        "_birth_id",
                        [
                            _set("_birth", "state." + RECORDS + "[_birth_id]"),
                            _require(
                                "_birth != null and _birth.id == _birth_id",
                                "The saved exploration birth identity changed.",
                            ),
                            _call(
                                ONE,
                                {
                                    "actor_state": "{_birth.id}",
                                    "caster_state": "{_birth.caster}",
                                    "lasting_state": "{_lasting_state_id}",
                                },
                            ),
                        ],
                    ),
                ],
            )
        ],
    )


def _bind_intent(tools):
    public = [
        name
        for name in ("cast_spell", "cast_spell_node", "cast_spell_outside_combat")
        if name in tools
    ]
    tail = tools["finish_srd_retained_cast_tail"]
    tail["parameters"].extend(_parameters())
    unwrap = [
        (rows, index)
        for rows in _lists(tail["effects"])
        for index, row in enumerate(rows)
        if row.get("set", {}).get("field") == "_retained_cast_tail_choices"
        and row["set"].get("value") == "_retained_cast_tail_choices[0]"
    ]
    if len(unwrap) != 1:
        raise ValueError("Keep the actual retained cast choice unwrap")
    rows, index = unwrap[0]
    rows[index + 1 : index + 1] = _capture("_birth_choice_", "_retained_cast_tail_choices")
    for name in public:
        owner = tools[name]
        owner["parameters"].extend(_parameters())
        owner["description"] += GUIDANCE
        for rows in _lists(owner["effects"]):
            for row in rows:
                if row.get("call") in {
                    "cast_spell",
                    "cast_spell_node",
                    "finish_srd_retained_cast_tail",
                }:
                    row["params"].update(
                        {choice: "{" + choice + "}" for choice, _, _, _ in CHOICES}
                    )
                field = row.get("set", {}).get("field", "")
                if field == "_retained_cast_tail_choices" or field.endswith(
                    ".retained_cast_choices"
                ):
                    # Object exploration appends this exact merge after the genuine
                    # capture. It retains prior choices; only the capture gains births.
                    if row == _set(
                        "_retained_cast_tail_choices",
                        ["merge(_retained_cast_tail_choices[0], _lite_object_retained_facts)"],
                    ):
                        continue
                    capture = row["set"]["value"]
                    if (
                        not isinstance(capture, list)
                        or len(capture) != 1
                        or not isinstance(capture[0], dict)
                    ):
                        raise ValueError("Keep the checked retained cast choice capsule")
                    capture[0].update({choice: choice for choice, _, _, _ in CHOICES})
    begin = tools["begin_srd_spell_action"]
    begin["parameters"].extend(_parameters())
    begin["description"] += GUIDANCE
    stamps = [
        (rows, index)
        for rows in _lists(begin["effects"])
        for index, row in enumerate(rows)
        if row.get("set", {}).get("field")
        == "lasting_state.pending_srd_actions.{_pending_action_id}.spell"
    ]
    if len(stamps) != 1:
        raise ValueError("Keep the existing staged spell allocation")
    rows, index = stamps[0]
    rows[index:index] = [
        _set("lasting_state.pending_srd_actions.{_pending_action_id}.parameters." + choice, choice)
        for choice, _, _, _ in CHOICES
    ]
    complete = tools["complete_srd_pending_action"]
    calls = [
        (rows, index)
        for rows in _lists(complete["effects"])
        for index, row in enumerate(rows)
        if row.get("call") == "cast_spell"
        and row.get("params", {}).get("spell") == "{_pending_action.parameters.spell}"
    ]
    if len(calls) != 1:
        raise ValueError("Keep the existing staged spell completion")
    rows, index = calls[0]
    rows[index]["params"].update(
        {choice: "{_birth_pending_" + choice + "}" for choice, _, _, _ in CHOICES}
    )
    rows[index:index] = _capture("_birth_pending_", "_pending_action.parameters")


def _bind_life_bond(tools):
    """Consume one current fictional proximity fact; retain the combat body qualifier."""
    owner = tools[LIFE_BOND]
    _ensure_lasting_state(owner)
    original = deepcopy(owner["effects"])
    ownership = [
        row
        for row in original
        if "require" in row
        and row["require"].get("error")
        == "Life Bond requires this caster's actual active Otherworldly Steed."
    ]
    if len(ownership) != 1:
        raise ValueError("Keep the genuine Life Bond source and ownership qualification")
    source_check = ownership[0]
    fact_path = "lasting_state." + PROXIMITY_FIELD + ".{_caster_state_id}"
    fact_read = "lasting_state." + PROXIMITY_FIELD + "[_caster_state_id]"
    tools[PROXIMITY] = _tool(
        "Establish this owned active Steed's current fictional proximity for one imminent "
        "spell healing event. Use current nearest separation, including vertical separation, "
        "and actual shared-plane knowledge; "
        "never reuse birth distance. This is a fact declaration, not movement or healing.",
        [
            _entity("caster_state"),
            _entity("steed_state"),
            _parameter("distance_feet", "number", minimum=0, maximum=10000),
            _parameter("same_plane", "boolean"),
        ],
        [
            _require(
                "combat.encounter_active != true",
                "Combat Life Bond uses authoritative saved bodies.",
            ),
            deepcopy(source_check),
            _require(
                "state.lasting_tick != null and state.lasting_tick >= 0",
                "Use the actual current lasting tick for this healing fact.",
            ),
            _choose(
                "state." + PROXIMITY_FIELD + " == null",
                [
                    _set("state." + PROXIMITY_FIELD, {}),
                ],
            ),
            _require(
                "_caster_state_id in lasting_state."
                + PROXIMITY_FIELD
                + " or len(lasting_state."
                + PROXIMITY_FIELD
                + ") < 256",
                "The saved exploration Steed proximity fact bound is full.",
            ),
            _set(
                fact_path,
                {
                    "caster": "_caster_state_id",
                    "steed": "_steed_state_id",
                    "tick": "state.lasting_tick",
                    "distance": "distance_feet",
                    "same_plane": "same_plane",
                },
            ),
        ],
    )
    tools[PROXIMITY]["metadata"] = {
        "category": "spells",
        "costs_action": False,
        "result_card": {
            "version": 1,
            "footprint": "compact",
            "title": "Life Bond proximity established",
            "summary": [
                {"label": "Caster", "value": {"path": "context.caster_state.name"}},
                {"label": "Steed", "value": {"path": "context.steed_state.name"}},
                {"label": "Current separation (ft)", "value": {"path": "parameters.distance_feet"}},
                {"label": "Same plane", "value": {"path": "parameters.same_plane"}},
            ],
        },
    }
    exploration = _bound(
        [
            _set("_bond_current_fact", None),
            _choose(
                "lasting_state." + PROXIMITY_FIELD + " != null",
                [
                    _choose(
                        "_caster_state_id in lasting_state." + PROXIMITY_FIELD,
                        [_choose(fact_read + " != null", [_set("_bond_current_fact", fact_read)])],
                    )
                ],
            ),
            _require(
                "_bond_current_fact != null and _bond_current_fact.caster == _caster_state_id and "
                "_bond_current_fact.steed == _steed_state_id and "
                "_bond_current_fact.tick == state.lasting_tick and "
                "_bond_current_fact.distance >= 0 and _bond_current_fact.distance <= 10000 and "
                "(_bond_current_fact.same_plane == true or _bond_current_fact.same_plane == false)",
                "Establish the exact caster and active Steed's current proximity immediately "
                "before this healing event.",
            ),
            _choose(
                "_bond_current_fact.same_plane == true and _bond_current_fact.distance <= 5",
                [_set("_life_bond_steed_id", "_steed_state_id")],
            ),
            _set(fact_path, None),
        ]
    )
    combat = [
        _choose(
            "lasting_state." + PROXIMITY_FIELD + " != null",
            [
                _choose(
                    "_caster_state_id in lasting_state." + PROXIMITY_FIELD, [_set(fact_path, None)]
                )
            ],
        ),
        *[row for row in original if row is not source_check],
    ]
    owner["effects"] = [
        source_check,
        _set("_life_bond_steed_id", "''"),
        _choose("combat.encounter_active != true", exploration, combat),
    ]
    owner["description"] += HEALING_GUIDANCE
    for name in (
        "cast_spell",
        "cast_spell_node",
        "cast_spell_outside_combat",
        "begin_srd_spell_action",
        "complete_srd_pending_action",
        "cast_spell_healing",
        "cast_spell_instantaneous_restoration",
        "cast_spell_instantaneous_multi_target_healing",
        "cast_spell_instantaneous_healing_pool",
        "start_srd_aura_of_life_turn",
    ):
        if name in tools:
            tools[name]["description"] += HEALING_GUIDANCE


def bind_exploration_created_actors(runtime, record=None):
    """Compose once after summon sight, Steed cards/lifecycle and retained-tail factoring."""
    tools = runtime["tools"]
    names = {
        OWNER,
        STEED,
        LIFE_BOND,
        "finish_srd_retained_cast_tail",
        "cast_spell",
        "cast_spell_outside_combat",
        "begin_srd_spell_action",
        "complete_srd_pending_action",
        "retire_srd_created_actor",
        "remove_srd_inactive_created_scene",
        "format_srd_otherworldly_steed_cast_receipt",
    }
    if "cast_spell_node" in tools:
        names.add("cast_spell_node")
    if not names <= tools.keys() or any(
        name in tools for name in (ONE, RECONCILE, PLACEMENT, PROXIMITY)
    ):
        raise ValueError("Keep the complete unbound selected creature owners")
    names.update(
        name
        for name in (
            "cast_spell_healing",
            "cast_spell_instantaneous_restoration",
            "cast_spell_instantaneous_multi_target_healing",
            "cast_spell_instantaneous_healing_pool",
            "start_srd_aura_of_life_turn",
        )
        if name in tools
    )
    changed = {name: deepcopy(tools[name]) for name in names}
    if any(
        choice == parameter["name"]
        for owner in changed.values()
        for parameter in owner["parameters"]
        for choice, _, _, _ in CHOICES
    ):
        raise ValueError("Exploration creature placement is already bound")
    original = deepcopy(changed[OWNER]["effects"])
    if not any(row.get("set", {}).get("field") == "_created_actor_scene" for row in original):
        raise ValueError("Keep the existing real created-body producer")
    changed[OWNER]["parameters"].extend(_parameters())
    combat_handoff = _bound(
        [
            _set(SCENE, True),
            _choose(
                "state." + RECORDS + " != null",
                [
                    _choose(
                        "summon_id in state." + RECORDS,
                        [
                            _require(
                                "state."
                                + RECORDS
                                + "[summon_id].id == summon_id and state."
                                + RECORDS
                                + "[summon_id].caster == _caster_state_id and state."
                                + RECORDS
                                + "[summon_id].spell == spell",
                                "Preserve this returning exploration creature's exact identity "
                                "and source.",
                            ),
                            _set("state." + RECORDS + ".{summon_id}.body_admitted", True),
                        ],
                    ),
                ],
            ),
        ]
    )
    changed[OWNER]["effects"] = [
        _choose(EXPLORATION, _exploration_birth(), [*original, *combat_handoff])
    ]
    _bind_intent(changed)
    changed[STEED]["parameters"].extend(_parameters())
    for name, owner in changed.items():
        for rows in _lists(owner["effects"]):
            for row in rows:
                call = row.get("call")
                if call in {OWNER, STEED}:
                    prefix = "_birth_choice_" if name == "finish_srd_retained_cast_tail" else ""
                    row["params"].update(
                        {choice: "{" + prefix + choice + "}" for choice, _, _, _ in CHOICES}
                    )
                    if call == OWNER:
                        exports = row.setdefault("export_context", {})
                        if not isinstance(exports, dict):
                            raise ValueError("Keep named source-body exports")
                        exports[SCENE] = SCENE
                if call == "install_srd_summoned_creature":
                    if row["params"].get("scene_proxy_created") is not True:
                        raise ValueError("Keep the genuine summoned-creature proxy handoff")
                    row["params"]["scene_proxy_created"] = "{" + SCENE + "}"
    # A Steed without an admitted/current scene cannot resolve physical item or rider drops.
    # Pending fictional travel invalidates a cached old-map body for physical drops.
    retire = changed["retire_srd_created_actor"]
    retire["effects"] = [
        *_pending_fiction_flag("_actor_state_id"),
        _choose(
            "actor_state.created_spell_id == 'find_steed' and "
            "actor_state.created_active == true and (" + _without_body("_actor_state_id")
            + " or _fiction_steed_step == true)",
            _bound(
                [
                    _require(
                        "lasting_state.created_actor_records[_actor_state_id].active == true and "
                        "lasting_state.created_actor_records[_actor_state_id].caster == "
                        "actor_state.created_caster_state_id and "
                        "lasting_state.created_actor_records[_actor_state_id].spell == "
                        "'find_steed'",
                        "Retire only the actual active saved Steed.",
                    ),
                    _choose(
                        "lasting_state."
                        + RECORDS
                        + " != null and _actor_state_id in lasting_state."
                        + RECORDS,
                        [
                            _require(
                                "lasting_state."
                                + RECORDS
                                + "[_actor_state_id].id == _actor_state_id and lasting_state."
                                + RECORDS
                                + "[_actor_state_id].caster == actor_state.created_caster_state_id",
                                "Preserve the saved exploration creature's actual owner and ID.",
                            ),
                        ],
                    ),
                    _require(
                        "actor_state.created_steed_rider_id == null and "
                        "(actor_state.inventory == null or len(actor_state.inventory) == 0) and "
                        "(actor_state.held_items == null or len(actor_state.held_items) == 0)",
                        "Admit the actual Steed body before resolving physical rider or item "
                        "drops.",
                    ),
                    *[
                        _set("actor_state." + field, value)
                        for field, value in {
                            "inventory": [],
                            "held_items": [],
                            "created_active": False,
                            "created_disappearance": "reason",
                            "is_alive": False,
                            "body_available": False,
                            "vital_parts_present": False,
                        }.items()
                    ],
                    _set("lasting_state.created_actor_records.{_actor_state_id}.active", False),
                    _set(
                        "lasting_state.created_actor_records.{_actor_state_id}.disappearance",
                        "reason",
                    ),
                    _call(
                        "clear_srd_created_actor_link",
                        {
                            "caster_state": "{actor_state.created_caster_state_id}",
                            "actor_id": "{_actor_state_id}",
                        },
                    ),
                ]
            ),
            retire["effects"],
        )
    ]
    remove = changed["remove_srd_inactive_created_scene"]
    remove["effects"] = [
        *_pending_fiction_flag("actor_id"),
        _choose(
            "spell == 'find_steed' and (" + _without_body("actor_id")
            + " or _fiction_steed_step == true)",
            [
                _require(
                    "lasting_state.created_actor_records[actor_id].caster == _caster_state_id and "
                    "lasting_state.created_actor_records[actor_id].spell == spell and "
                    "lasting_state.created_actor_records[actor_id].active != true",
                    "Return only this caster's exact inactive saved Steed.",
                )
            ],
            remove["effects"],
        )
    ]
    receipt = changed["format_srd_otherworldly_steed_cast_receipt"]
    _ensure_lasting_state(receipt)
    calls = [
        (rows, index)
        for rows in _lists(receipt["effects"])
        for index, row in enumerate(rows)
        if row.get("call") == "srd_information_scene_entity"
    ]
    labels = [
        row["set"]["value"]
        for rows in _lists(receipt["effects"])
        for row in rows
        if row.get("set", {}).get("field") == "_steed_cast_receipt_rows"
    ]
    if len(calls) != 1 or len(labels) != 1:
        raise ValueError("Keep the existing paid Steed result rows")
    rows, index = calls[0]
    rows[index] = _choose(
        _unplaced("_steed_state_id"),
        [
            _set("_info_position", "lasting_state." + RECORDS + "[_steed_state_id].placement"),
            _set("_birth_position_label", "'Steed placement'"),
        ],
        [rows[index], _set("_birth_position_label", "'Steed position'")],
    )
    position = [row for row in labels[0] if row.get("label") == "'Steed position'"]
    if len(position) != 1:
        raise ValueError("Keep the honest Steed position caption")
    position[0]["label"] = "_birth_position_label"
    _bind_life_bond(changed)
    changed.update(
        {ONE: _reconcile_one(), RECONCILE: _reconcile(), PLACEMENT: _admitted_placement()}
    )
    declarations = None
    if record is not None:
        declarations = deepcopy(record["tools"])
        if any(row["name"] == PROXIMITY for row in declarations):
            raise ValueError("The current exploration Life Bond fact is already declared")
        for declaration in declarations:
            if declaration["name"] in changed and any(
                parameter["name"] in {choice for choice, _, _, _ in CHOICES}
                for parameter in changed[declaration["name"]]["parameters"]
            ):
                declaration["input_schema"] = _tool_schema(
                    changed[declaration["name"]],
                    declaration["input_schema"],
                    tool_name=declaration["name"],
                )
        declarations.append(
            {
                "name": PROXIMITY,
                "capability": "dnd.srd.5.2.1.spells",
                "writers": ["dm"],
                "turn_statuses": ["open"],
                "state_predicates": [],
                "input_schema": _tool_schema(changed[PROXIMITY], tool_name=PROXIMITY),
            }
        )
    tools.update(changed)
    if declarations is not None:
        record["tools"] = declarations
    return sorted(changed)
