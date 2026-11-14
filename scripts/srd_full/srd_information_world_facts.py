"""Information discovery uses admitted physical observations when no scene exists."""

from copy import deepcopy

if __package__:
    from .srd_divination_spells import _normalize
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
        _when,
    )
    from .srd_timed_senses import _loop, _public, _push, _replace_tools
else:
    from srd_divination_spells import _normalize
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
        _when,
    )
    from srd_timed_senses import _loop, _public, _push, _replace_tools


DISCOVERY = (
    "detect_evil_and_good",
    "detect_magic",
    "detect_poison_and_disease",
    "find_traps",
    "locate_animals_or_plants",
    "locate_creature",
    "locate_object",
)
PUBLIC_TOOLS = {"record_srd_information_world_facts"}
PENDING = "state.pending_srd_action_stack == null or len(state.pending_srd_action_stack) == 0"
ALIASES = {
    "scan_srd_information_spell": "scan_srd_information_scene_spell",
    "cast_srd_nature_divination": "cast_srd_nature_scene_divination",
}


def _fact_parameter():
    """Only physical observations are accepted; native mechanics have separate owners."""
    properties = {
        "id": {"type": "string", "minLength": 1},
        "subject_state_id": {"type": "string"},
        "plane": {"type": "string", "minLength": 1},
        "distance_feet": {"type": "number", "minimum": 0},
        "direction": {"type": "string", "minLength": 1},
        "lighting": {"type": "string", "enum": ["bright", "dim", "darkness", "magical_darkness"]},
        "obstruction": {"type": "string", "enum": ["clear", "solid", "heavy_obscurement"]},
        "crossings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "material": {
                        "type": "string",
                        "enum": ["stone", "dirt", "wood", "metal", "lead", "water"],
                    },
                    "thickness_feet": {"type": "number", "minimum": 0},
                    "flowing": {"type": "boolean"},
                },
                "required": ["material", "thickness_feet"],
            },
        },
        "kind": {"type": "string"},
        "is_object": {"type": "boolean"},
        "nonmagical_plant": {"type": "boolean"},
        "poison": {"type": "boolean"},
        "poisonous": {"type": "boolean"},
        "venomous": {"type": "boolean"},
        "magical_contagion": {"type": "boolean"},
        "detected_kind": {"type": "string"},
        "intentional_trap": {"type": "boolean"},
        "general_danger": {"type": "string"},
        "settlement": {"type": "boolean"},
        "planar_portal": {"type": "boolean"},
        "body_of_water": {"type": "boolean"},
        "nature_prevalent_category": {"type": "string", "enum": ["plant", "mineral", "beast"]},
        "nature_prevalent_kind": {"type": "string", "minLength": 1},
        "movement_direction": {"type": "string"},
    }
    return _parameter(
        "candidates",
        "array",
        min_items=0,
        max_items=64,
        items={
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
            "required": [
                "id",
                "plane",
                "distance_feet",
                "direction",
                "lighting",
                "obstruction",
                "crossings",
            ],
        },
    )


def _native_tool():
    """Refresh body mechanics from the actual actor, never from candidate input fields."""
    return _tool(
        "Read actual native creature facts for an information candidate.",
        [
            _entity("subject_state"),
            _parameter("metadata", "object", from_state="state.turn_effects"),
        ],
        [
            _set("_world_native_metadata", "metadata"),
            _set("_world_native_active", "subject_state.is_alive != false"),
            _set(
                "_world_native_patch",
                {
                    "participant_ref": "_subject_state_id",
                    "creature_type": "subject_state.creature_type",
                    "challenge_rating": None,
                    "invisible": (
                        "subject_state.conditions != null and 'invisible' in "
                        "subject_state.conditions"
                    ),
                    "different_form": "subject_state.form_current_source_id != null",
                },
            ),
            _set("_world_native_metadata", "merge(_world_native_metadata, _world_native_patch)"),
            _when(
                "subject_state.metadata != null",
                [
                    _set(
                        "_world_native_metadata",
                        "merge(_world_native_metadata, subject_state.metadata)",
                    ),
                    # Current creature statistics take precedence over legacy metadata.
                    _set(
                        "_world_native_metadata",
                        "merge(_world_native_metadata, _world_native_patch)",
                    ),
                ],
            ),
        ],
    )


def _tools():
    fact_items = _fact_parameter()["items"]
    missing_fields = {
        name: None for name in fact_items["properties"] if name not in fact_items["required"]
    }
    recorder = _public(
        _tool(
            (
                "Record GM-observed physical candidates at this named place and "
                "plane; no spell answer is supplied."
            ),
            [
                _entity("caster_state"),
                _parameter("spell", "enum", enum_values=[*DISCOVERY, "commune_with_nature"]),
                _parameter("current_place"),
                _parameter("current_plane"),
                _parameter("coverage_feet", "number", minimum=0),
                _fact_parameter(),
            ],
            [
                _require(PENDING, "Complete the pending action before recording observations."),
                _require(
                    "state.spatial_scene == null", "Use the authoritative scene when it exists."
                ),
                _require(
                    "len(current_place) > 0 and len(current_plane) > 0",
                    "Record the actual place and plane.",
                ),
                _set("_world_seen_ids", []),
                _set("_world_fact_defaults", missing_fields),
                _set("_world_record_candidates", []),
                _loop(
                    "candidates",
                    "_world_fact",
                    [
                        _set("_world_fact", "merge(_world_fact_defaults, _world_fact)"),
                        _require(
                            "len(_world_fact.crossings) <= 16",
                            "Use at most sixteen crossed materials.",
                        ),
                        _set("_world_fact_crossings", "_world_fact.crossings"),
                        _loop(
                            "_world_fact_crossings",
                            "_world_fact_crossing",
                            [
                                _require(
                                    "_world_fact_crossing.material != 'water' or "
                                    "_world_fact_crossing.flowing != null",
                                    "A crossed watercourse needs its actual flowing fact.",
                                ),
                            ],
                        ),
                        _require(
                            "_world_fact.id not in _world_seen_ids",
                            "Candidate identities must be unique.",
                        ),
                        _push("_world_seen_ids", "_world_fact.id"),
                        _require(
                            "_world_fact.distance_feet <= coverage_feet",
                            "A candidate must lie in the observed area.",
                        ),
                        _when(
                            (
                                "_world_fact.subject_state_id != null and "
                                "_world_fact.subject_state_id != ''"
                            ),
                            [
                                _call(
                                    "read_srd_information_world_native",
                                    {
                                        "subject_state": "{_world_fact.subject_state_id}",
                                        "metadata": {},
                                    },
                                    ["_world_native_metadata"],
                                ),
                            ],
                        ),
                        _push("_world_record_candidates", "_world_fact"),
                    ],
                ),
                _set("caster_state.information_world_place", "current_place"),
                _set("caster_state.information_world_plane", "current_plane"),
                _set(
                    "caster_state.information_world_facts",
                    {
                        "spell": "spell",
                        "source_id": None,
                        "place": "current_place",
                        "plane": "current_plane",
                        "coverage_feet": "coverage_feet",
                        "candidates": "_world_record_candidates",
                        "observed_tick": "state.lasting_tick",
                    },
                ),
                _set("_world_observers", []),
                _when(
                    "state.information_world_observers != null",
                    [_set("_world_observers", "state.information_world_observers")],
                ),
                _when(
                    "_caster_state_id not in _world_observers",
                    [
                        _require(
                            "len(_world_observers) < 64",
                            "Use the bounded current information observers.",
                        ),
                        _push("_world_observers", "_caster_state_id"),
                    ],
                ),
                _set("state.information_world_observers", "_world_observers"),
            ],
        ),
        "information",
        "Physical information observations",
        [("Place", "current_place"), ("Spell", "spell")],
    )
    collect = _tool(
        "Read source-bound physical observations and refresh each actual native subject.",
        [
            _entity("caster_state"),
            _parameter("spell"),
            _parameter("source_id"),
            _parameter("radius", "number"),
        ],
        [
            _require(
                "state.spatial_scene == null", "Factual acquisition is only the absent-scene path."
            ),
            _set("_world_record", "caster_state.information_world_facts"),
            _require(
                "_world_record != null and _world_record.spell == spell",
                "Record current physical observations for this spell.",
            ),
            _require(
                (
                    "_world_record.place == caster_state.information_world_place and "
                    "_world_record.plane == caster_state.information_world_plane"
                ),
                "Observations belong to the current place and plane.",
            ),
            _require(
                "_world_record.source_id == null or _world_record.source_id == source_id",
                "Refresh observations after recasting this spell.",
            ),
            _require(
                "_world_record.coverage_feet >= radius",
                "The physical observations must cover this source search area.",
            ),
            _set("caster_state.information_world_facts.source_id", "source_id"),
            _set("_world_plane", "_world_record.plane"),
            _set("_query_candidates", []),
            _set("_world_candidates", "_world_record.candidates"),
            _loop(
                "_world_candidates",
                "_world_fact",
                [
                    _set("_world_native_active", True),
                    _set("_world_metadata", {}),
                    _set("_world_metadata", "merge(_world_metadata, _world_fact)"),
                    _set(
                        "_world_metadata.nature_region_qualified",
                        "_world_record.coverage_feet == radius",
                    ),
                    _set("_world_metadata.invisible", False),
                    _set("_world_metadata.magical_effect", False),
                    _when(
                        (
                            "_world_fact.subject_state_id != null and "
                            "_world_fact.subject_state_id != ''"
                        ),
                        [
                            _call(
                                "read_srd_information_world_native",
                                {
                                    "subject_state": "{_world_fact.subject_state_id}",
                                    "metadata": "{_world_metadata}",
                                },
                                ["_world_native_metadata", "_world_native_active"],
                            ),
                            _set("_world_metadata", "_world_native_metadata"),
                        ],
                    ),
                    _set(
                        "_world_candidate",
                        {
                            "id": "_world_fact.id",
                            "active": "_world_native_active",
                            "scene_kind": "'entity'",
                            "metadata": "_world_metadata",
                            "distance_feet": "_world_fact.distance_feet",
                            "direction": "_world_fact.direction",
                            "obstruction": "_world_fact.obstruction",
                            "crossings": "_world_fact.crossings",
                            "movement_direction": None,
                            "motion_tick": "_world_record.observed_tick",
                        },
                    ),
                    _when(
                        "_world_fact.movement_direction != null",
                        [
                            _set(
                                "_world_candidate.movement_direction",
                                "_world_fact.movement_direction",
                            )
                        ],
                    ),
                    _push("_query_candidates", "_world_candidate"),
                ],
            ),
        ],
    )
    barriers = _tool(
        "Apply printed blocking thresholds to observed crossed materials.",
        [
            _parameter("candidate", "object", from_state="state.turn_effects"),
            _parameter("rule"),
            _parameter("spell"),
        ],
        [
            _set("_information_blocked", False),
            _set("_world_crossings", "candidate.crossings"),
            _loop(
                "_world_crossings",
                "_world_crossing",
                [
                    _when(
                        "rule != 'none' and _world_crossing.thickness_feet > 0",
                        [
                            _when(
                                "_world_crossing.material == 'lead'",
                                [_set("_information_blocked", True)],
                            ),
                            _when(
                                (
                                    "rule == 'detect' and ((_world_crossing.material in ['stone', "
                                    "'dirt', 'wood'] and _world_crossing.thickness_feet >= 1) or "
                                    "(_world_crossing.material == 'metal' and "
                                    "_world_crossing.thickness_feet >= 1 * 12 ** -1))"
                                ),
                                [_set("_information_blocked", True)],
                            ),
                            _when(
                                (
                                    "spell == 'locate_creature' and "
                                    "_world_crossing.material == 'water' "
                                    "and _world_crossing.flowing == true and "
                                    "_world_crossing.thickness_feet >= 10"
                                ),
                                [_set("_information_blocked", True)],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    invalidator = _tool(
        "Discard observations after this observer or an observed native subject actually moves.",
        [_entity("observer_state"), _parameter("subject")],
        [
            _set("_world_motion_record", "observer_state.information_world_facts"),
            _when(
                "_world_motion_record != null",
                [
                    _set("_world_moved", "subject == _observer_state_id"),
                    _set("_world_motion_candidates", "_world_motion_record.candidates"),
                    _loop(
                        "_world_motion_candidates",
                        "_world_motion_fact",
                        [
                            _when(
                                (
                                    "subject == _world_motion_fact.id or subject == "
                                    "_world_motion_fact.subject_state_id"
                                ),
                                [_set("_world_moved", True)],
                            ),
                        ],
                    ),
                    _when(
                        "_world_moved == true",
                        [_set("observer_state.information_world_facts", None)],
                    ),
                    _when(
                        "subject == _observer_state_id",
                        [
                            _set("observer_state.information_world_place", None),
                            _set("observer_state.information_world_plane", None),
                        ],
                    ),
                ],
            ),
        ],
    )
    motion = _tool(
        "Invalidate current observers from an authentic committed movement owner.",
        [_parameter("subject")],
        [
            _set("_world_observer_ids", "state.information_world_observers"),
            _loop(
                "_world_observer_ids",
                "_world_observer",
                [
                    _call(
                        "invalidate_srd_information_world_observer",
                        {"observer_state": "{_world_observer}", "subject": "{subject}"},
                    ),
                ],
            ),
        ],
    )
    return {
        "record_srd_information_world_facts": recorder,
        "read_srd_information_world_native": _native_tool(),
        "collect_srd_information_world_candidates": collect,
        "srd_information_world_barriers": barriers,
        "invalidate_srd_information_world_observer": invalidator,
        "invalidate_srd_information_world_motion": motion,
    }


def _walk(effects, transform):
    """Rewrite only selected acquisition statements, preserving every other ordered effect."""
    result = []
    for original in effects:
        effect = deepcopy(original)
        for branch in ("on_true", "on_false"):
            if branch in effect:
                effect[branch] = _walk(effect[branch], transform)
        if "for_each" in effect:
            effect["for_each"]["do"] = _walk(effect["for_each"]["do"], transform)
        result.extend(transform(effect))
    return result


def _without_snapshot(effects):
    """Remove only the scene snapshot prefix from a factual clone."""
    effects = deepcopy(effects)
    if len(effects) >= 3 and effects[2].get("spatial_query", {}).get("operation") == "snapshot":
        if "spatial_scene != null" not in str(effects[0]) or "map.grid.unit" not in str(effects[1]):
            raise ValueError("Information snapshot prefix ownership changed.")
        return effects[3:]
    return effects


def _factual_qualifier(original):
    qualifier = deepcopy(original)
    replaced = 0

    def transform(effect):
        nonlocal replaced
        condition = effect.get("condition", "")
        if condition == (
            "_candidate_eligible == true and (spell == 'find_traps' or (spell == "
            "'detect_magic' and mode == 'aura'))"
        ):
            replaced += 1
            return [
                _when(
                    condition,
                    [
                        _set("_candidate_subject", None),
                        _when(
                            "candidate.metadata.participant_ref != null",
                            [_set("_candidate_subject", "candidate.metadata.participant_ref")],
                        ),
                        _call(
                            "resolve_srd_sight",
                            {
                                "observer_state": "{_caster_state_id}",
                                "subject_state": "{_candidate_subject}",
                                "distance": "{ceil(candidate.distance_feet)}",
                                "lighting": "{candidate.metadata.lighting}",
                                "obstruction": "{candidate.obstruction}",
                                "subject_invisible": "{candidate.metadata.invisible == true}",
                            },
                            ["_sight_can_see"],
                        ),
                        _set("_candidate_eligible", "_sight_can_see == true"),
                    ],
                )
            ]
        return [effect]

    qualifier["effects"] = _walk(_without_snapshot(qualifier["effects"]), transform)
    if replaced != 1 or "spatial_query" in str(qualifier["effects"]):
        raise ValueError("Expected the exact Information physical-visibility acquisition owner.")
    return qualifier


def _factual_scan(original):
    scanner = deepcopy(original)
    state_name = _state_name(scanner)
    removed = []

    def transform(effect):
        field = effect.get("set", {}).get("field")
        if field in {"_query_origin", "_query_plane"}:
            return []
        if effect.get("call") == "srd_information_scene_entity":
            removed.append("origin")
            return []
        if "spatial_scene.map.bounds" in str(
            effect.get("condition", "")
        ) or "spatial_scene.map.bounds" in str(effect.get("require", "")):
            removed.append("bounds")
            return []
        if "observer needs its actual scene plane" in str(effect):
            return []
        if field == "_query_record":
            return [_set("_query_record", {"radius": "_query_profile.query_radius"})]
        if effect.get("call") == "collect_srd_information_candidates":
            return [
                _call(
                    "read_srd_information_instance", {"record": "{record}"}, ["_info_instance_id"]
                ),
                _call(
                    "collect_srd_information_world_candidates",
                    {
                        "caster_state": "{_caster_state_id}",
                        "spell": "{spell}",
                        "source_id": "{_info_instance_id}",
                        "radius": "{_query_profile.query_radius}",
                    },
                    ["_query_candidates", "_world_plane"],
                ),
                _set("_query_plane", "_world_plane"),
            ]
        if effect.get("call") == "measure_srd_information_distance":
            return [
                _set("_scene_distance", "_candidate.distance_feet"),
                _set("_scene_direction", "_candidate.direction"),
            ]
        if effect.get("call") == "qualify_srd_information_candidate":
            effect["call"] = "qualify_srd_information_world_candidate"
        if effect.get("call") == "srd_information_barriers":
            effect["call"] = "srd_information_world_barriers"
            effect["params"] = {
                "candidate": "{_candidate}",
                "rule": "{_query_barrier_rule}",
                "spell": "{spell}",
            }
        if "_candidate.metadata.previous_position" in effect.get("condition", ""):
            return [
                _when(
                    (
                        "_candidate.movement_direction != null and _candidate.motion_tick == "
                        f"{state_name}.lasting_tick"
                    ),
                    [_set("_info_movement_direction", "_candidate.movement_direction")],
                )
            ]
        if effect.get(
            "call"
        ) == "format_srd_information_projection" and "_candidate.position" in str(effect):
            effect["params"]["message"] = (
                "{_candidate_kind}; distance {_candidate_distance} feet; direction "
                "{_candidate_direction}"
            )
        return [effect]

    scanner["effects"] = _walk(_without_snapshot(scanner["effects"]), transform)
    if removed.count("origin") != 1 or removed.count("bounds") != 3:
        raise ValueError("Expected the exact Information whole-scene acquisition owner.")
    return scanner


def _factual_nature(original):
    nature = deepcopy(original)
    counts = {"origin": 0, "bounds": 0, "candidates": 0}

    def transform(effect):
        field = effect.get("set", {}).get("field")
        if field in {"_nature_origin", "_nature_plane"}:
            return []
        if effect.get("call") == "srd_information_scene_entity":
            counts["origin"] += 1
            return []
        if "spatial_scene.map.bounds" in str(effect.get("require", "")):
            counts["bounds"] += 1
            return []
        if "Nature divination needs the actual plane" in str(effect):
            return []
        if effect.get("call") == "collect_srd_information_candidates":
            counts["candidates"] += 1
            return [
                _call(
                    "collect_srd_information_world_candidates",
                    {
                        "caster_state": "{_caster_state_id}",
                        "spell": "commune_with_nature",
                        "source_id": "{record.instance_id}",
                        "radius": "{_nature_radius}",
                    },
                    ["_query_candidates", "_world_plane"],
                ),
                _set("_nature_plane", "_world_plane"),
            ]
        if effect.get("call") == "measure_srd_information_distance":
            return [
                _set("_scene_distance", "_nature_candidate.distance_feet"),
                _set("_scene_direction", "_nature_candidate.direction"),
            ]
        return [effect]

    nature["effects"] = _walk(_without_snapshot(nature["effects"]), transform)
    if counts != {"origin": 1, "bounds": 3, "candidates": 1}:
        raise ValueError("Expected the exact Nature whole-scene acquisition owner.")
    return nature


def _forward(tool):
    return {
        p["name"]: "{"
        + ("_" + p["name"] + "_id" if p.get("resolve") in {"entity", "participant"} else p["name"])
        + "}"
        for p in tool["parameters"]
    }


def _state_name(tool):
    return (
        "lasting_state"
        if any(p["name"] == "lasting_state" for p in tool["parameters"])
        else "state"
    )


def _public_recorder(changed):
    tool = changed["record_srd_information_world_facts"]
    node = deepcopy(tool)
    node["metadata"] = {"category": "internal", "visible_to_player": False, "costs_action": False}
    changed["record_srd_information_world_facts_node"] = node
    tool["parameters"] = [p for p in tool["parameters"] if p["name"] != "lasting_state"]
    params = _forward(tool) | {"lasting_state": "state"}
    tool["effects"] = [_call("record_srd_information_world_facts_node", params)]


def _wrapper(scene, name, alias):
    """Preserve conditional private outputs as well as the original source/input interface."""
    wrapper = deepcopy(scene)
    wrapper["description"] += " [world-fact acquisition]"
    scene_call = _call(alias, _forward(wrapper))
    world_call = _call(name + "_world", _forward(wrapper))
    scene_call["export_context"] = world_call["export_context"] = "*"
    allowed = (
        " or ".join(f"spell == '{spell}'" for spell in DISCOVERY)
        if name == "scan_srd_information_spell"
        else "spell == 'commune_with_nature'"
    )
    branch = _when(_state_name(scene) + ".spatial_scene != null", [scene_call])
    branch["on_false"] = [
        _require(allowed, "This source needs its native scene consumer."),
        world_call,
    ]
    wrapper["effects"] = [
        _when(p["name"] + " == null", [_set("_" + p["name"] + "_id", None)])
        for p in wrapper["parameters"]
        if p.get("resolve") in {"entity", "participant"} and p.get("required") is False
    ] + [branch]
    return wrapper


def _movement_hooks(tools, changed, restore=False):
    for name, subject, distance in (
        ("move_srd_travel_subject", "{subject}", None),
        ("move_srd_scene_creature", "{_actor_state_id}", None),
        ("spend_combat_movement", "{_actor_state_id}", "distance"),
        ("spend_srd_movement", "{_target_id}", "distance"),
    ):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        state_name = _state_name(tool)
        condition = (
            f"{state_name}.information_world_observers != null and "
            f"len({state_name}.information_world_observers) > 0"
        )
        if distance:
            names = {p["name"] for p in tool["parameters"]}
            distance = distance if distance in names else "amount"
            if distance not in names:
                raise ValueError(
                    "Information observations need actual committed movement distance."
                )
            condition += f" and {distance} > 0"
        hook = _when(
            condition, [_call("invalidate_srd_information_world_motion", {"subject": subject})]
        )
        tool["effects"] = [e for e in tool["effects"] if e != hook]
        if not restore:
            tool["effects"].append(hook)
        changed[name] = tool


def _query_place_hook(state_name):
    return _when(
        state_name + ".spatial_scene == null",
        [
            _require(
                "current_place != '' and current_plane != '' and "
                "current_place == caster_state.information_world_place and "
                "current_plane == caster_state.information_world_plane",
                "Query from the actual current named place and plane; "
                "refresh changed observations.",
            ),
        ],
    )


def _bind_query_place(tools, changed, restore=False):
    """The current-place intent prevents querying a previous Narrative location implicitly."""
    descriptors = [
        _parameter(name, required=False, default="") for name in ("current_place", "current_plane")
    ]
    for name in ("query_srd_information_spell", "query_srd_information_spell_node"):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        for descriptor in descriptors:
            prior = next((p for p in tool["parameters"] if p["name"] == descriptor["name"]), None)
            if prior is not None and prior != descriptor:
                raise ValueError("Information current-place parameter ownership changed.")
            if restore:
                tool["parameters"] = [
                    p for p in tool["parameters"] if p["name"] != descriptor["name"]
                ]
            elif prior is None:
                tool["parameters"].append(deepcopy(descriptor))
        is_node = name.endswith("_node")
        direct = not is_node and "query_srd_information_spell_node" not in tools
        if is_node or direct:
            if (
                direct
                and sum(e.get("call") == "scan_srd_information_spell" for e in tool["effects"]) != 1
            ):
                raise ValueError("Expected the actual direct Information query owner.")
            hook = _query_place_hook(_state_name(tool))
            tool["effects"] = [e for e in tool["effects"] if e != hook]
            if not restore:
                tool["effects"].insert(0, hook)
        else:
            calls = [
                e for e in tool["effects"] if e.get("call") == "query_srd_information_spell_node"
            ]
            if len(calls) != 1:
                raise ValueError("Expected the actual Information query public delegate.")
            for descriptor in descriptors:
                key = descriptor["name"]
                if restore:
                    calls[0]["params"].pop(key, None)
                else:
                    calls[0]["params"][key] = "{" + key + "}"
        changed[name] = tool


def bind_runtime_consumers(text):
    "Compose after final source/secrecy and divination filters; preserve the actual scene branch."
    tools = _load_runtime(text)["tools"]
    changed = _tools()
    qualifier = tools["qualify_srd_information_candidate"]
    changed["qualify_srd_information_world_candidate"] = _factual_qualifier(qualifier)
    for name, alias in ALIASES.items():
        original = tools[name]
        if original.get("description", "").endswith(" [world-fact acquisition]"):
            if original != _wrapper(tools[alias], name, alias):
                raise ValueError("The captured Information wrapper has unowned changes.")
            original = tools[alias]
        scene = deepcopy(original)
        changed[alias] = scene
        factual_name = name + "_world"
        changed[factual_name] = (
            _factual_scan(scene) if name == "scan_srd_information_spell" else _factual_nature(scene)
        )
        changed[name] = _wrapper(scene, name, alias)
    _movement_hooks(tools, changed)
    _bind_query_place(tools, changed)
    _normalize(changed)
    _public_recorder(changed)
    return _replace_tools(text, changed)


def restore_runtime(text):
    """Restore the exact captured family owners before source regeneration."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    for name, alias in ALIASES.items():
        if tools.get(name, {}).get("description", "").endswith(" [world-fact acquisition]"):
            if alias not in tools:
                raise ValueError("Missing captured Information acquisition owner.")
            if tools[name] != _wrapper(tools[alias], name, alias):
                raise ValueError("The captured Information wrapper has unowned changes.")
            changed[name] = deepcopy(tools[alias])
    if "record_srd_information_world_facts" in tools:
        _bind_query_place(tools, changed, restore=True)
        _movement_hooks(tools, changed, restore=True)
    return _replace_tools(text, changed) if changed else text
