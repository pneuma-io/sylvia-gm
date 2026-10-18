"""Observe real Time Stop changes at their existing object, condition and source owners."""

from copy import deepcopy

if __package__:
    from .srd_time_stop import ACTIVE, _bind_state
    from .srd_timed_actor_defenses import _call, _load_runtime, _parameter, _require, _set, _when
    from .srd_timed_senses import _replace_tools
else:
    from srd_time_stop import ACTIVE, _bind_state
    from srd_timed_actor_defenses import _call, _load_runtime, _parameter, _require, _set, _when
    from srd_timed_senses import _replace_tools


def _lists(effects):
    yield effects
    for effect in effects:
        for side in ("on_true", "on_false"):
            if side in effect:
                yield from _lists(effect[side])
        if "for_each" in effect:
            yield from _lists(effect["for_each"].get("do", []))


def _strip(tool, marker):
    """Remove only our direct observation nodes and saved local values on regeneration."""
    for effects in _lists(tool["effects"]):
        effects[:] = [
            e
            for e in effects
            if not (
                e.get("call") == marker
                or (
                    set(e) == {"condition", "on_true"}
                    and len(e["on_true"]) == 1
                    and e["on_true"][0].get("call") == marker
                )
            )
        ]


def _object_observation(actor, condition="true"):
    return _when(
        ACTIVE + " and " + condition,
        [
            _call(
                "observe_srd_time_stop_object_effect",
                {
                    "subject_id": "{object_id}",
                    "source_state": "{" + actor + "}",
                },
            )
        ],
    )


def _bind_cast_receipt(tools, changed):
    """Use the committed cast witness, allocating only when this installer has none."""
    installer = deepcopy(tools["cast_srd_time_stop"])
    params = {p["name"] for p in installer["parameters"]}
    if "spell_cast_id" not in params:
        installer["parameters"].append(
            _parameter(
                "spell_cast_id",
                required=False,
                default="",
                model_hidden=True,
            )
        )
    # The one owned prelude is separate from the actual Time Stop installation.
    installer["effects"] = [
        e
        for e in installer["effects"]
        if e.get("set", {}).get("field") != "_time_stop_cast_receipt"
        and e.get("condition") != "spell_cast_id != ''"
    ]
    installer["effects"][:0] = [
        _set("_time_stop_cast_receipt", "spell_cast_id"),
        {
            "condition": "spell_cast_id != ''",
            "on_true": [
                _require(
                    "caster_state.last_spell_source_cast_id == spell_cast_id and "
                    "caster_state.last_spell_source_spell == 'time_stop'",
                    "Time Stop requires its actual committed cast identity.",
                )
            ],
            "on_false": [
                _call(
                    "begin_srd_spell_source_instance",
                    {
                        "caster_state": "{_caster_state_id}",
                        "spell": "time_stop",
                        "slot_level": 9,
                    },
                    ("_spell_source_cast_id",),
                ),
                _set("_time_stop_cast_receipt", "_spell_source_cast_id"),
            ],
        },
    ]
    records = [
        e["set"]["value"]
        for effects in _lists(installer["effects"])
        for e in effects
        if e.get("set", {}).get("field") == "lasting_state.time_stop_source"
    ]
    if len(records) != 1 or not isinstance(records[0], dict):
        raise ValueError("Time Stop's actual saved installation changed.")
    records[0]["cast_id"] = "_time_stop_cast_receipt"
    changed["cast_srd_time_stop"] = installer
    cast = deepcopy(tools["cast_spell"])
    found = 0
    for effects in _lists(cast["effects"]):
        for e in effects:
            if e.get("call") == "cast_srd_time_stop":
                e["params"]["spell_cast_id"] = "{_spell_source_cast_id}"
                found += 1
    if found != 1:
        raise ValueError("Time Stop's actual paid dispatch changed.")
    # Earlier family groups can be inside the saved lost-operation wrapper.
    # Attribute before that actual group, retaining its costs and failure gates.
    _strip(cast, "begin_srd_time_stop_spell_effect")
    dispatches = [
        i
        for i, effect in enumerate(cast["effects"])
        if any(
            "_spell_definition.family ==" in node.get("condition", "")
            and any(child.get("call", "").startswith("cast_") for child in node.get("on_true", []))
            for nodes in _lists([effect])
            for node in nodes
        )
    ]
    if not dispatches:
        raise ValueError("Time Stop's actual paid family dispatch is missing.")
    cast["effects"].insert(
        dispatches[0],
        _call("begin_srd_time_stop_spell_effect", {"caster_state": "{_caster_state_id}"}),
    )
    changed["cast_spell"] = cast


def _stamp_creation(tool, field, caster):
    """Save this stop's actual cast receipt on the newly installed source itself."""
    receipt_field = field + ".created_time_stop_id"
    for nodes in _lists(tool["effects"]):
        nodes[:] = [
            e
            for e in nodes
            if not (
                e.get("condition") == "_time_stop_creation_receipt != null"
                and e.get("on_true") == [_set(receipt_field, "_time_stop_creation_receipt")]
            )
        ]
    tool["effects"] = [
        e
        for e in tool["effects"]
        if e.get("set", {}).get("field") != "_time_stop_creation_receipt"
        and not (
            e.get("condition", "").endswith(".turn_running == true")
            and "_time_stop_creation_receipt" in str(e)
        )
    ]
    tool["effects"][:0] = [
        _set("_time_stop_creation_receipt", None),
        _when(
            ACTIVE + " and " + caster + " == state.time_stop_source.caster and "
            "state.time_stop_source.turn_running == true",
            [_set("_time_stop_creation_receipt", "state.time_stop_source.cast_id")],
        ),
    ]
    records = [
        (nodes, i)
        for nodes in _lists(tool["effects"])
        for i, e in enumerate(nodes)
        if e.get("set", {}).get("field") == field
    ]
    if len(records) != 1:
        raise ValueError("Time Stop retained-source creation changed: " + field)
    nodes, index = records[0]
    record = nodes[index]["set"]["value"]
    if not isinstance(record, dict):
        raise ValueError("Time Stop retained-source creation changed: " + field)
    record["created_time_stop_id"] = None
    nodes.insert(
        index + 1,
        _when(
            "_time_stop_creation_receipt != null",
            [_set(receipt_field, "_time_stop_creation_receipt")],
        ),
    )


def _bind_object_changes(tools, changed):
    """Only committed HP, break, lock or access changes can end stopped time."""
    for name in ("resolve_srd_spell_object_attack", "resolve_srd_sorcerous_burst_object_attack"):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        _strip(tool, "observe_srd_time_stop_object_effect")
        found = 0
        for effects in _lists(tool["effects"]):
            for index in range(len(effects) - 1, -1, -1):
                e = effects[index]
                if "spatial_mutation" in e and e.get("store_as") == "_object_updated":
                    effects.insert(
                        index + 1,
                        _object_observation("_caster_state_id", "_object_damage_applied > 0"),
                    )
                    found += 1
        if found != 1:
            raise ValueError("Actual object HP persistence changed: " + name)
        changed[name] = tool
    content = deepcopy(tools["cast_spell_object_content_spell"])
    _strip(content, "observe_srd_time_stop_object_effect")
    found = 0
    for effects in _lists(content["effects"]):
        for index in range(len(effects) - 1, -1, -1):
            if effects[index].get("store_as") == "_world_mutation":
                # Other content spells also commit physical effects; these three
                # selected clauses repair, lock or unlock a genuinely held object.
                effects.insert(
                    index + 1,
                    _object_observation(
                        "_caster_state_id",
                        "(spell == 'mending' or spell == 'knock' or spell == 'arcane_lock')",
                    ),
                )
                found += 1
    if found < 3:
        raise ValueError("Actual Mending/Knock/Arcane Lock persistence changed.")
    changed["cast_spell_object_content_spell"] = content
    for name in ("operate_srd_locked_object_node", "operate_srd_object_access_source_node"):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        _strip(tool, "observe_srd_time_stop_object_effect")
        tool["effects"] = [
            e
            for e in tool["effects"]
            if e.get("set", {}).get("field") != "_time_stop_access_closed"
        ]
        found = 0
        for effects in _lists(tool["effects"]):
            for index in range(len(effects) - 1, -1, -1):
                e = effects[index]
                if (
                    e.get("set", {}).get("field") == "_world_access"
                    and e["set"]["value"] == "_object_scene.metadata.srd_object_access"
                ):
                    effects.insert(
                        index + 1, _set("_time_stop_access_closed", "_world_access.closed")
                    )
                if e.get("store_as") == "_world_mutation":
                    effects.insert(
                        index + 1,
                        _object_observation(
                            "_actor_state_id",
                            "(operation == 'speak_password' or "
                            "_world_access.closed != _time_stop_access_closed)",
                        ),
                    )
                    found += 1
        if found != 2:
            raise ValueError("Actual source-qualified object access changed: " + name)
        changed[name] = tool


def bind_source_consumers(text):
    """Bind committed identities and real sources after the shared allocator exists."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    _bind_cast_receipt(tools, changed)
    for name, field in (
        (
            "cast_spell_environment",
            "lasting_state.persistent_spell_areas.{_environment_cast_id}",
        ),
        ("cast_spell_heat_metal_spell", "_heat_source"),
    ):
        if name in tools:
            tool = deepcopy(tools[name])
            _stamp_creation(tool, field, "_caster_state_id")
            changed[name] = tool
    _bind_state(changed)
    return _replace_tools(text, changed)


def _packet_owner(tools, name):
    """Keep exact shared qualified facades intact and observe their actual receiving kernel."""
    core = {
        "apply_srd_qualifying_damage": "execute_srd_qualified_scalar_packet",
        "apply_srd_qualifying_composite_damage": "execute_srd_qualified_composite_packet",
    }.get(name)
    entry = tools[name]
    if not core or not entry["effects"] or entry["effects"][0].get("call") != core:
        return name
    expected = {
        p["name"]: "{"
        + ("_" + p["name"] + "_id" if p.get("resolve") in {"entity", "participant"} else p["name"])
        + "}"
        for p in entry["parameters"]
    }
    expected["render_damage_snapshot"] = True
    if core not in tools or entry["effects"][0] != {
        "call": core,
        "params": expected,
        "export_context": "*",
    }:
        raise ValueError("Time Stop qualified receiving facade changed.")
    return core


def bind_received_effects(text):
    """Join final real receiving owners before Root captures their reaction continuations."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    _bind_object_changes(tools, changed)
    removal = deepcopy(tools["remove_srd_condition"])
    _strip(removal, "observe_srd_time_stop_creature_effect")
    removal["effects"] = [
        e
        for e in removal["effects"]
        if e.get("set", {}).get("field") != "_time_stop_removed_condition"
    ]
    removal["effects"].insert(
        0, _set("_time_stop_removed_condition", "condition in target.conditions")
    )
    removal["effects"].append(
        _when(
            ACTIVE + " and state.time_stop_source.spell_action_active == true and "
            "_time_stop_removed_condition == true and condition not in target.conditions",
            [
                _call(
                    "observe_srd_time_stop_creature_effect",
                    {
                        "target_state": "{target}",
                        "source_state": "{state.time_stop_source.caster}",
                    },
                )
            ],
        )
    )
    changed["remove_srd_condition"] = removal
    for name in (
        "apply_srd_damage",
        "apply_srd_composite_damage",
        "apply_srd_qualifying_damage",
        "apply_srd_qualifying_composite_damage",
    ):
        owner = _packet_owner(tools, name)
        if owner in changed:
            continue
        tool = deepcopy(tools[owner])
        _strip(tool, "observe_srd_time_stop_creature_effect")
        tool["effects"].append(
            _when(
                ACTIVE + " and state.time_stop_source.spell_action_active == true and "
                "source_state_id != null and len(source_state_id) > 0 and _effective_damage > 0",
                [
                    _call(
                        "observe_srd_time_stop_creature_effect",
                        {
                            "target_state": "{target}",
                            "source_state": "{source_state_id}",
                        },
                    )
                ],
            )
        )
        changed[owner] = tool
    for name, source, target in (
        ("resolve_srd_flaming_sphere_event", "_sphere_event_source", "_target_state_id"),
        ("resolve_srd_environment_sphere_event", "_sphere_event_source", "_target_state_id"),
        ("resolve_srd_heat_metal_damage", "_heat_source", "_heat_holder"),
    ):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        _strip(tool, "observe_srd_time_stop_creature_effect")
        found = 0
        for effects in _lists(tool["effects"]):
            for index in range(len(effects) - 1, -1, -1):
                if effects[index].get("call") == "apply_srd_qualifying_damage":
                    effects.insert(
                        index + 1,
                        _when(
                            ACTIVE
                            + " and "
                            + source
                            + ".created_time_stop_id != null and "
                            + source
                            + ".created_time_stop_id == state.time_stop_source.cast_id and "
                            "_effective_damage > 0",
                            [
                                _call(
                                    "observe_srd_time_stop_creature_effect",
                                    {
                                        "target_state": "{" + target + "}",
                                        "source_state": "{" + source + ".caster}",
                                    },
                                )
                            ],
                        ),
                    )
                    found += 1
        if found != 1:
            raise ValueError("Actual retained damage producer changed: " + name)
        changed[name] = tool
    _bind_state(changed)
    return _replace_tools(text, changed)
