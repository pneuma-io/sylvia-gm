"""Printed side consequences use actual damage and admitted object/body owners."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import POSITION_EXPORTS, _tool_scene
    from .srd_sunburst_geometry import compose_sunburst_geometry
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from .srd_timed_senses import _loop, _push, _replace_tools
else:
    from srd_scene_relations import POSITION_EXPORTS, _tool_scene
    from srd_sunburst_geometry import compose_sunburst_geometry
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from srd_timed_senses import _loop, _push, _replace_tools


def _effect_lists(effects):
    yield effects
    for effect in effects:
        for key in ("on_true", "on_false"):
            if isinstance(effect.get(key), list):
                yield from _effect_lists(effect[key])
        if "for_each" in effect:
            yield from _effect_lists(effect["for_each"]["do"])


def _starry_light_tool():
    """Install hit-derived Dim Light and Invisible suppression with the native next-turn owner."""
    return _tool_scene(
        "Retain an actual Starry Wisp hit's ten-foot Dim Light and Invisible suppression.",
        [_entity("caster_state"), _parameter("subject_id")],
        [
            _call("srd_information_scene_entity", {"subject": "{subject_id}"}, POSITION_EXPORTS),
            _require(
                "_info_scene_entity.metadata.plane != null",
                "Starry Wisp needs its hit subject's actual scene plane.",
            ),
            _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
            _when("caster_state.turns_started == null", [_set("caster_state.turns_started", 0)]),
            _when("state.turn_effects == null", [_set("state.turn_effects", [])]),
            _require(
                "len(state.turn_effects) < 128",
                "Resolve existing lasting effects before adding another.",
            ),
            _when(
                "state.illumination_cast_serial == null",
                [_set("state.illumination_cast_serial", 0)],
            ),
            _set("state.illumination_cast_serial", "state.illumination_cast_serial + 1"),
            _set("_starry_light_instance", "state.illumination_cast_serial"),
            _set(
                "_starry_light_record",
                {
                    "kind": "'illumination_spell'",
                    "spell": "'starry_wisp'",
                    "label": "'Starry Wisp'",
                    "caster": "_caster_state_id",
                    "source": "subject_id",
                    "target": "_caster_state_id",
                    "origin": "_info_position",
                    "plane": "_info_scene_entity.metadata.plane",
                    "instance": "_starry_light_instance",
                    "slot_level": 0,
                    "started_tick": "state.lasting_tick",
                    "expires_tick": "state.lasting_tick + 2",
                    "concentration": False,
                    "mode": "'outline'",
                    "bright_radius": 0,
                    "dim_radius": 10,
                    "dark_radius": 0,
                    "suppress_invisible": True,
                    "owner": "_caster_state_id",
                    "phase": "'end'",
                    "turn": "caster_state.turns_started + 1",
                    "magnitude": 0,
                    "dice_count": 0,
                    "damage_die": "''",
                    "damage_type": "''",
                },
            ),
            _set("_starry_light_records", "state.turn_effects"),
            _push("_starry_light_records", "_starry_light_record"),
            _set("state.turn_effects", "_starry_light_records"),
            _set("state.has_illumination_spells", True),
            _when(
                "_info_scene_entity.metadata.participant_ref != null and "
                "_info_scene_entity.metadata.is_object != true",
                [
                    _call(
                        "sync_srd_illumination_invisible",
                        {"actor_state": "{_info_scene_entity.metadata.participant_ref}"},
                    )
                ],
            ),
        ],
    )


def compose_light_damage_riders(tools, changed):
    """Join exact printed riders after family emission and before saved hit leaves are cloned."""
    changed["install_srd_starry_wisp_light"] = _starry_light_tool()
    tool = deepcopy(changed.get("cast_spell_attack_damage", tools["cast_spell_attack_damage"]))
    if "install_srd_starry_wisp_light" not in str(tool["effects"]):
        found = False
        for effects in list(_effect_lists(tool["effects"])):
            for index in range(len(effects) - 1, -1, -1):
                effect = effects[index]
                if effect.get("call") != "apply_srd_qualifying_damage":
                    continue
                effects.insert(
                    index + 1,
                    _when(
                        "spell == 'starry_wisp' and _spell_attack_outcome == 'success'",
                        [
                            _call(
                                "install_srd_starry_wisp_light",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "subject_id": "{_target_id}",
                                },
                            )
                        ],
                    ),
                )
                found = True
        if not found:
            raise SystemExit("Starry Wisp's light rider lost its owning actual hit packet")
    changed["cast_spell_attack_damage"] = tool

    check = deepcopy(
        changed.get("check_srd_illumination_record", tools["check_srd_illumination_record"])
    )
    active = next(
        effect
        for effect in check["effects"]
        if effect.get("set", {}).get("field") == "_illumination_active"
    )
    if "record.spell == 'starry_wisp'" not in active["set"]["value"]:
        prior = active["set"]["value"]
        active["set"]["value"] = (
            f"({prior}) or (record.spell == 'starry_wisp' and record.expires_tick != null "
            "and combat.encounter_active == true and state.lasting_tick <= "
            "record.expires_tick + 0.000000001)"
        )
    changed["check_srd_illumination_record"] = check
    compose_sunburst_geometry(tools | changed, changed)


AREA = "cast_spell_instantaneous_area_save_damage"
COMPOSITE_AREA = "cast_spell_instantaneous_area_save_composite_damage"
IGNITE = "ignite_srd_area_object"
OBJECTS = "area_object_targets"
IGNITION_SPELLS = ("burning_hands", "fireball", "fire_storm")
IGNITION_CONDITION = " or ".join(f"spell == {spell!r}" for spell in IGNITION_SPELLS)


def _object_intent():
    return _parameter(
        OBJECTS,
        "array",
        required=False,
        default=[],
        items={"type": "string"},
        max_items=64,
        unique_items=True,
        description="Actual scenery selected by the GM as lying in the spell's area.",
    )


def _ordinary_flame_facts(tool):
    """Reuse the existing fact recorder for unlit fuel and explicit combustibility."""
    tool = deepcopy(tool)
    if "flammability" in {p["name"] for p in tool["parameters"]}:
        return tool
    tool["parameters"].extend(
        [
            _parameter("burning", "boolean", required=False, default=True),
            _parameter(
                "flammability",
                "enum",
                required=False,
                default="unrecorded",
                enum_values=["unrecorded", "flammable", "nonflammable"],
            ),
        ]
    )
    tool["description"] = "Record actual ordinary fuel/flame and rain exposure facts, once."
    for index, effect in enumerate(tool["effects"]):
        row = effect.get("set", {})
        if row.get("field") != "_world_changes":
            continue
        if row["value"].get("srd_ordinary_flame", {}).get("burning") is not True:
            raise ValueError("The ordinary flame fact owner changed.")
        row["value"]["srd_ordinary_flame"]["burning"] = "burning"
        tool["effects"].insert(
            index + 1,
            _when(
                "flammability != 'unrecorded'",
                [
                    _set("_area_fuel_fact", {"flammable": "flammability == 'flammable'"}),
                    _set("_world_changes", "merge(_world_changes, _area_fuel_fact)"),
                ],
            ),
        )
        return tool
    raise ValueError("The ordinary flame metadata writer is absent.")


def _ignite_object(tool):
    """Change existing flame state through the original scene lookup and mutation owner."""
    tool = deepcopy(tool)
    tool["description"] = "Ignite selected flammable scenery which is neither worn nor carried."
    tool["parameters"] = [
        _parameter("object_id"),
        *[p for p in tool["parameters"] if p["name"] == "lasting_state"],
    ]
    effects = tool["effects"]
    writes = next(
        i for i, e in enumerate(effects) if e.get("set", {}).get("field") == "_world_changes"
    )
    prefix = [
        e
        for e in effects[:writes]
        if "require" not in e
        or e["require"]["condition"]
        not in {
            "combat.encounter_active != true",
            "combat.pending_action == null",
            "_object_scene.metadata.srd_ordinary_flame == null",
            "_object_scene.footprint.kind == 'box'",
        }
    ]
    suffix = effects[writes:]
    if suffix[0]["set"]["value"].get("srd_ordinary_flame", {}).get("burning") is not True:
        raise ValueError("Ignition needs the unchanged ordinary-flame scene mutation owner.")
    suffix[0]["set"]["value"] = {"srd_ordinary_flame": "_area_flame_state"}
    tool["effects"] = [
        _set("_area_object_ignited", False),
        *prefix,
        _require(
            "_object_scene.metadata.flammable != null",
            "Record the selected object's actual combustibility before casting.",
        ),
        _when(
            "_object_scene.metadata.flammable == true and "
            "_object_scene.metadata.worn_by == null and "
            "_object_scene.metadata.carried_by == null",
            [
                _require(
                    "_object_scene.metadata.srd_ordinary_flame.kind == 'ordinary_flame'",
                    "Record this object's actual ordinary fuel/flame facts before casting.",
                ),
                _set("_area_flame_patch", {"burning": True}),
                _set(
                    "_area_flame_state",
                    "merge(_object_scene.metadata.srd_ordinary_flame, _area_flame_patch)",
                ),
                *suffix,
                _set("_area_object_ignited", True),
            ],
        ),
    ]
    return tool


def bind_area_consequences(text):
    """Add two printed consequences to native recipients; retain all roll and payment owners."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    flame_name = "record_srd_ordinary_flame"
    flame_leaf = flame_name + "_node" if flame_name + "_node" in tools else flame_name
    changed[flame_leaf] = _ordinary_flame_facts(tools[flame_leaf])
    if flame_leaf != flame_name:
        wrapper = deepcopy(tools[flame_name])
        existing = {p["name"] for p in wrapper["parameters"]}
        wrapper["parameters"].extend(
            deepcopy(p)
            for p in changed[flame_leaf]["parameters"]
            if p["name"] in {"burning", "flammability"} and p["name"] not in existing
        )
        for effects in _effect_lists(wrapper["effects"]):
            for effect in effects:
                if effect.get("call") == flame_leaf:
                    effect["params"].update(burning="{burning}", flammability="{flammability}")
        changed[flame_name] = wrapper
    # Clone the original mutation, not the expanded fact recorder's optional fields.
    if IGNITE not in tools:
        changed[IGNITE] = _ignite_object(tools[flame_leaf])

    body = deepcopy(tools["record_srd_deceased_body"])
    if "thawed" not in {p["name"] for p in body["parameters"]}:
        body["parameters"].append(_parameter("thawed", "boolean", required=False, default=False))
        body["effects"].append(
            _when(
                "thawed == true",
                [
                    _require(
                        "target_state.body_frozen_statue != null",
                        "Only record observed thawing of an existing frozen body.",
                    ),
                    _set("target_state.body_frozen_statue", None),
                ],
            )
        )
    changed["record_srd_deceased_body"] = body

    receiving = deepcopy(tools["resolve_spell_area_save_damage_target"])
    if "body_frozen_statue" not in str(receiving["effects"]):
        receiving["effects"].insert(
            0,
            _when(
                "spell == 'cone_of_cold'",
                [
                    _set("_area_alive_before", "target.is_alive"),
                    _set("_area_death_before", None),
                    _when(
                        "target.death_tick != null",
                        [
                            _set("_area_death_before", "target.death_tick"),
                        ],
                    ),
                    _set("_area_body_source", "_target_id"),
                    _when(
                        "target.current_body_source_id != null",
                        [
                            _set("_area_body_source", "target.current_body_source_id"),
                        ],
                    ),
                ],
            ),
        )
        receiving["effects"].append(
            _when(
                "spell == 'cone_of_cold' and _area_alive_before == true and "
                "_affliction_trigger_damage > 0 and "
                "_affliction_trigger_alive == false and target.is_alive == false and "
                "target.death_tick != null and (_area_death_before == null or "
                "target.death_tick != _area_death_before) and "
                "(target.current_body_source_id == null or "
                "target.current_body_source_id == _area_body_source)",
                [
                    _set(
                        "target.body_frozen_statue",
                        {
                            "spell": "'cone_of_cold'",
                            "body_source_id": "_area_body_source",
                            "death_tick": "target.death_tick",
                        },
                    ),
                    _push(
                        "_spell_detail_rows",
                        {
                            "label": "target.name",
                            "value": "'Killed by Cone of Cold; frozen statue until it thaws.'",
                        },
                    ),
                ],
            )
        )
    changed["resolve_spell_area_save_damage_target"] = receiving

    for name in (
        "cast_spell",
        "cast_spell_outside_combat",
        "begin_srd_spell_action",
        AREA,
        COMPOSITE_AREA,
    ):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        targets = next(p for p in tool["parameters"] if p["name"] == "targets")
        targets["min_items"] = 0
        if OBJECTS not in {p["name"] for p in tool["parameters"]}:
            tool["parameters"].append(_object_intent())
        for effects in _effect_lists(tool["effects"]):
            for effect in effects:
                if effect.get("call") in {
                    AREA,
                    COMPOSITE_AREA,
                    "cast_spell",
                    "cast_spell_outside_combat",
                }:
                    effect["params"][OBJECTS] = "{" + OBJECTS + "}"
        if name == AREA and IGNITE not in str(tool["effects"]):
            for effects in _effect_lists(tool["effects"]):
                for index, effect in enumerate(effects):
                    if effect.get("set", {}).get("field") == "_spell_area_target_count":
                        effects.insert(
                            index + 1,
                            _when(
                                "_spell_area_target_count == 0",
                                [
                                    _set("_spell_area_last_save", None),
                                    _set("_spell_area_last_save_outcome", "''"),
                                    _set("_spell_area_last_save_advantage", False),
                                    _set("_spell_area_last_save_disadvantage", False),
                                    _set("_spell_area_last_damage_amount", 0),
                                ],
                            ),
                        )
                        break
            tool["effects"].insert(
                0,
                _require(
                    f"({IGNITION_CONDITION}) or len(area_object_targets) == 0",
                    "This object-recipient intent belongs to a printed ignition spell.",
                ),
            )
            tool["effects"].append(
                _when(
                    IGNITION_CONDITION,
                    [
                        _loop(
                            OBJECTS,
                            "_area_object_id",
                            [
                                _call(
                                    IGNITE,
                                    {"object_id": "{_area_object_id}"},
                                    ["_area_object_ignited"],
                                ),
                                _when(
                                    "_area_object_ignited == true",
                                    [
                                        _push(
                                            "_spell_detail_rows",
                                            {
                                                "label": "_area_object_id",
                                                "value": "'Starts burning.'",
                                            },
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                )
            )
        if name == AREA:
            # Re-emission upgrades the earlier Burning Hands-only condition in place.
            for effects in _effect_lists(tool["effects"]):
                for effect in effects:
                    guard = effect.get("require", {})
                    if guard.get("condition") == (
                        "spell == 'burning_hands' or len(area_object_targets) == 0"
                    ):
                        guard["condition"] = (
                            f"({IGNITION_CONDITION}) or len(area_object_targets) == 0"
                        )
                        guard["error"] = (
                            "This object-recipient intent belongs to a printed ignition spell."
                        )
                    if effect.get("condition") == "spell == 'burning_hands'" and IGNITE in str(
                        effect
                    ):
                        effect["condition"] = IGNITION_CONDITION
        if name == COMPOSITE_AREA and "apply_srd_area_object_damage" not in str(tool["effects"]):
            tool["effects"].insert(
                0,
                _require(
                    "spell == 'meteor_swarm' or len(area_object_targets) == 0",
                    "This composite object-recipient intent belongs to Meteor Swarm.",
                ),
            )
            for effects in _effect_lists(tool["effects"]):
                for index, effect in enumerate(effects):
                    if effect.get("set", {}).get("field") == "_spell_area_target_count":
                        effects.insert(
                            index + 1,
                            _when(
                                "_spell_area_target_count == 0",
                                [
                                    _set("_spell_area_last_save", None),
                                    _set("_spell_area_last_save_outcome", "''"),
                                    _set("_spell_area_last_save_advantage", False),
                                    _set("_spell_area_last_save_disadvantage", False),
                                    _set("_spell_area_last_component_1_amount", 0),
                                    _set("_spell_area_last_component_2_amount", 0),
                                    _set("_spell_area_last_component_1_effective_damage", 0),
                                    _set("_spell_area_last_component_2_effective_damage", 0),
                                    _set("_spell_area_last_effective_damage", 0),
                                    _set("_spell_area_last_concentration_save_dc", None),
                                    _set("_spell_area_last_concentration_save_outcome", "''"),
                                    _set("_spell_area_last_concentration_save_roll", None),
                                ],
                            ),
                        )
                        break
            tool["effects"].append(
                _when(
                    "spell == 'meteor_swarm'",
                    [
                        _set(
                            "_area_object_component_1_total",
                            "max(0, _spell_component_1_damage.total)",
                        ),
                        _set(
                            "_area_object_component_2_total",
                            "max(0, _spell_component_2_damage.total)",
                        ),
                        _loop(
                            OBJECTS,
                            "_area_object_id",
                            [
                                _call(
                                    "apply_srd_area_object_damage",
                                    {
                                        "caster_state": "{_caster_state_id}",
                                        "object_id": "{_area_object_id}",
                                        "component_1_amount": "{_area_object_component_1_total}",
                                        "component_1_damage_type": "{component_1_damage_type}",
                                        "component_2_amount": "{_area_object_component_2_total}",
                                        "component_2_damage_type": "{component_2_damage_type}",
                                    },
                                    [
                                        "_area_object_eligible",
                                        "_object_damage_applied",
                                        "_object_hp_before",
                                        "_object_hp_after",
                                    ],
                                ),
                                _when(
                                    "_area_object_eligible == true",
                                    [
                                        _call(
                                            "record_srd_result_text",
                                            {
                                                "text": (
                                                    "Object damage {_object_damage_applied}; "
                                                    "HP {_object_hp_before} -> {_object_hp_after}."
                                                )
                                            },
                                            ["_recorded_result_text"],
                                        ),
                                        _push(
                                            "_spell_detail_rows",
                                            {
                                                "label": "_area_object_id",
                                                "value": "_recorded_result_text",
                                            },
                                        ),
                                        _call(
                                            IGNITE,
                                            {"object_id": "{_area_object_id}"},
                                            ["_area_object_ignited"],
                                        ),
                                        _when(
                                            "_area_object_ignited == true",
                                            [
                                                _push(
                                                    "_spell_detail_rows",
                                                    {
                                                        "label": "_area_object_id",
                                                        "value": "'Starts burning.'",
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
            )
        changed[name] = tool
    return _replace_tools(text, changed)
