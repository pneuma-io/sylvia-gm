"""Attribute actual retained healing and conditions to their creating Time Stop."""

from copy import deepcopy

if __package__:
    from .srd_runtime_fragments import _ToolFragments
    from .srd_time_stop import ACTIVE, _bind_state
    from .srd_time_stop_effects import _lists, _stamp_creation
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _set,
        _tool,
        _when,
    )
    from .srd_timed_senses import _replace_tools
else:
    from srd_runtime_fragments import _ToolFragments
    from srd_time_stop import ACTIVE, _bind_state
    from srd_time_stop_effects import _lists, _stamp_creation
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _set,
        _tool,
        _when,
    )
    from srd_timed_senses import _replace_tools


OBSERVER = "observe_srd_time_stop_retained_change"
READER = "read_srd_time_stop_change_target"
MARKER = "time_stop_retained_changes"
WARD_FIELD = "lasting_state.spell_ward_sources.{_new_ward_id}"
EYE_FIELD = "caster_state.eyebite_time_stop_receipt"
LIFE_ID = "_time_stop_life_source_id"
LIFE_SERIAL = "_time_stop_life_serial"
# These actual Conjured constructors already carry the retained-damage creation stamp.
CONJURED = {
    "resolve_srd_celestial_initial_lights": ("restore_shared_spell_healing_target", "healing"),
    "resolve_srd_celestial_event_lights": ("restore_shared_spell_healing_target", "healing"),
    "install_srd_conjured_fey_fear": ("install_srd_timed_condition_source", "conditions"),
}


def _helpers():
    """Read genuine native state and consume a private before/after change witness."""
    return {
        READER: _tool(
            "Read the current target's actual HP and conditions for a retained effect.",
            [_entity("target_state")],
            [
                _set("_time_stop_change_hp", "target_state.hp"),
                _set("_time_stop_change_conditions", []),
                _when(
                    "target_state.conditions != null",
                    [_set("_time_stop_change_conditions", "target_state.conditions")],
                ),
            ],
        ),
        OBSERVER: _tool(
            "End only the stop that created this actually changing retained source.",
            [
                _entity("target_state"),
                _entity("source_state"),
                _parameter("created_time_stop_id", model_hidden=True),
                _parameter("changed", "boolean", model_hidden=True),
            ],
            [
                _when(
                    ACTIVE + " and changed == true and created_time_stop_id == "
                    "state.time_stop_source.cast_id and "
                    "_source_state_id == state.time_stop_source.caster",
                    [
                        _call(
                            "observe_srd_time_stop_creature_effect",
                            {
                                "target_state": "{_target_state_id}",
                                "source_state": "{_source_state_id}",
                            },
                        )
                    ],
                )
            ],
        ),
    }


def _read(target, suffix):
    call = _call(READER, {"target_state": target})
    call["export_context"] = {
        "_time_stop_change_hp_" + suffix: "_time_stop_change_hp",
        "_time_stop_change_conditions_" + suffix: "_time_stop_change_conditions",
    }
    return call


def _witness(target, source, receipt, kind, guard=None):
    """Snapshot around the original native mutation, without changing its call or order."""
    guard = guard or receipt + " != null"
    changes = []
    changed = "_time_stop_change_hp_after > _time_stop_change_hp_before"
    if kind == "conditions":
        changes = [
            _set("_time_stop_condition_added", False),
            {
                "for_each": {
                    "collection": "_time_stop_change_conditions_after",
                    "as": "_time_stop_added_condition",
                    "do": [
                        _when(
                            "_time_stop_added_condition not in _time_stop_change_conditions_before",
                            [_set("_time_stop_condition_added", True)],
                        )
                    ],
                }
            },
        ]
        changed = "_time_stop_condition_added == true"
    return (
        _when(guard, [_read(target, "before")]),
        _when(
            guard,
            [
                _read(target, "after"),
                *changes,
                _when(
                    changed,
                    [
                        _call(
                            OBSERVER,
                            {
                                "target_state": target,
                                "source_state": source,
                                "created_time_stop_id": "{" + receipt + "}",
                                "changed": True,
                            },
                        )
                    ],
                ),
            ],
        ),
    )


def _around(tool, call_name, source, receipt, kind, guard=None):
    found = [
        (nodes, i, e)
        for nodes in _lists(tool["effects"])
        for i, e in enumerate(nodes)
        if e.get("call") == call_name
    ]
    if len(found) != 1:
        raise ValueError("The actual retained change owner changed: " + call_name)
    nodes, index, call = found[0]
    params = call.get("params", {})
    target = params.get("target", params.get("target_state"))
    if not isinstance(target, str) or not target.startswith("{") or not target.endswith("}"):
        raise ValueError("The retained change target changed.")
    before, after = _witness(target, source, receipt, kind, guard)
    end = _mutation_end(nodes, index, kind)
    nodes.insert(end + 1, after)
    nodes.insert(index, before)


def _mutation_end(nodes, index, kind):
    if kind == "healing":
        return index
    refresh = [
        i
        for i, e in enumerate(nodes)
        if i > index and e.get("call") == "refresh_srd_timed_condition_target"
    ]
    if len(refresh) != 1:
        raise ValueError("The actual retained condition projection changed.")
    return refresh[0]


def _remove_around(tool, call_name, source, receipt, kind, guard=None):
    found = [
        (nodes, i, e)
        for nodes in _lists(tool["effects"])
        for i, e in enumerate(nodes)
        if e.get("call") == call_name
    ]
    if len(found) != 1:
        raise ValueError("The retained change mutation owner changed.")
    nodes, index, call = found[0]
    params = call.get("params", {})
    target = params.get("target", params.get("target_state"))
    before, after = _witness(target, source, receipt, kind, guard)
    end = _mutation_end(nodes, index, kind)
    if index == 0 or nodes[index - 1] != before or nodes[end + 1 : end + 2] != [after]:
        raise ValueError("The retained change witness changed.")
    del nodes[end + 1]
    del nodes[index - 1]


def _life_selection():
    return _when(
        "_ward_record.serial > " + LIFE_SERIAL,
        [_set(LIFE_ID, "_ward_id"), _set(LIFE_SERIAL, "_ward_record.serial")],
    )


def _life_branch(tool):
    found = [
        e
        for nodes in _lists(tool["effects"])
        for e in nodes
        if e.get("condition") == "_ward_record.mode == 'life'"
        and _set("_ward_life_effective", True) in e.get("on_true", [])
    ]
    if len(found) != 1:
        raise ValueError("The effective Aura of Life source branch changed.")
    return found[0]["on_true"]


def _eye_branch(tool):
    found = [
        e
        for nodes in _lists(tool["effects"])
        for e in nodes
        if e.get("condition") == "spell == 'eyebite'"
        and _set("caster_state.eyebite_saved_success_ids", []) in e.get("on_true", [])
    ]
    if len(found) != 1:
        raise ValueError("The actual Eyebite retained source setup changed.")
    if not any(p["name"] == "spell_cast_id" for p in tool["parameters"]):
        raise ValueError("Eyebite requires its actual paid source identity before stamping.")
    return found[0]["on_true"]


def _eye_stamp():
    return [
        _set(
            EYE_FIELD,
            {
                "cast_id": "spell_cast_id",
                "concentration_sequence": "_condition_concentration_sequence",
                "created_time_stop_id": None,
            },
        ),
        _when(
            ACTIVE + " and _caster_state_id == state.time_stop_source.caster and "
            "state.time_stop_source.turn_running == true and spell_cast_id != ''",
            [_set(EYE_FIELD + ".created_time_stop_id", "state.time_stop_source.cast_id")],
        ),
    ]


def _eye_guard():
    return (
        EYE_FIELD
        + " != null and "
        + EYE_FIELD
        + ".created_time_stop_id != null and "
        + EYE_FIELD
        + ".cast_id == caster_state.concentration_cast_id and "
        + EYE_FIELD
        + ".concentration_sequence == caster_state.eyebite_cast_sequence"
    )


def _eye_witness():
    return _witness(
        "{_target_state_id}",
        "{_caster_state_id}",
        EYE_FIELD + ".created_time_stop_id",
        "conditions",
        _eye_guard(),
    )


def _mark(tool):
    added = not any(p["name"] == "time_stop_state" for p in tool["parameters"])
    _bind_state({"owner": tool})
    tool["metadata"][MARKER] = added


def _unmark(tool):
    added = tool.get("metadata", {}).pop(MARKER)
    if not isinstance(added, bool):
        raise ValueError("The retained change binding marker changed.")
    if added:
        tool["parameters"] = [p for p in tool["parameters"] if p["name"] != "time_stop_state"]


def _bound(nodes):
    holder = {"owner": {"effects": deepcopy(nodes), "parameters": []}}
    _bind_state(holder)
    return holder["owner"]["effects"]


def _ward_unstamp(tool):
    prelude = [
        _set("_time_stop_creation_receipt", None),
        _when(
            ACTIVE + " and _caster_state_id == state.time_stop_source.caster and "
            "state.time_stop_source.turn_running == true",
            [_set("_time_stop_creation_receipt", "state.time_stop_source.cast_id")],
        ),
    ]
    if tool["effects"][:2] != _bound(prelude):
        raise ValueError("The Ward creation receipt prelude changed.")
    del tool["effects"][:2]
    records = [
        (nodes, i, e)
        for nodes in _lists(tool["effects"])
        for i, e in enumerate(nodes)
        if e.get("set", {}).get("field") == WARD_FIELD
    ]
    if len(records) != 1:
        raise ValueError("The actual Ward source constructor changed.")
    nodes, index, node = records[0]
    if node["set"]["value"].pop("created_time_stop_id", "missing") is not None:
        raise ValueError("The Ward source receipt field changed.")
    stamp = _when(
        "_time_stop_creation_receipt != null",
        [_set(WARD_FIELD + ".created_time_stop_id", "_time_stop_creation_receipt")],
    )
    if nodes[index + 1 : index + 2] != [stamp]:
        raise ValueError("The Ward source receipt stamp changed.")
    del nodes[index + 1]


def restore_runtime(text):
    """Restore only these finite owned additions before their native family regeneration."""
    runtime = _load_runtime(text)
    tools = runtime["tools"]
    changed = {}
    for name, original in tools.items():
        if MARKER not in original.get("metadata", {}):
            continue
        tool = deepcopy(original)
        _unmark(tool)
        if name == "cast_srd_spell_ward":
            _ward_unstamp(tool)
        elif name == "resolve_srd_spell_ward_benefits":
            resets = [_set(LIFE_ID, None), _set(LIFE_SERIAL, -1)]
            if tool["effects"][:2] != resets or _life_branch(tool)[-1] != _life_selection():
                raise ValueError("The Aura source selection changed.")
            del tool["effects"][:2]
            _life_branch(tool).pop()
        elif name == "start_srd_aura_of_life_turn":
            call = next(
                e
                for nodes in _lists(tool["effects"])
                for e in nodes
                if e.get("call") == "resolve_srd_spell_ward_benefits"
            )
            if call.get("export_context", {}).pop(LIFE_ID, None) != LIFE_ID:
                raise ValueError("The Aura source export changed.")
            _remove_around(
                tool,
                "restore_hit_points",
                "{_time_stop_life_source.caster_state_id}",
                "_time_stop_life_source.created_time_stop_id",
                "healing",
            )
            setup = _set(
                "_time_stop_life_source", "lasting_state.spell_ward_sources[" + LIFE_ID + "]"
            )
            found = [nodes for nodes in _lists(tool["effects"]) if setup in nodes]
            if len(found) != 1:
                raise ValueError("The Aura actual source snapshot changed.")
            found[0].remove(setup)
            tool["parameters"] = [p for p in tool["parameters"] if p["name"] != "lasting_state"]
        elif name == "cast_srd_control_afflictions":
            branch = _eye_branch(tool)
            if branch[-2:] != _bound(_eye_stamp()):
                raise ValueError("The Eyebite source stamp changed.")
            del branch[-2:]
        elif name == "retarget_srd_eyebite":
            before, after = _eye_witness()
            if tool["effects"][:1] != [before] or tool["effects"].count(after) != 1:
                raise ValueError("The Eyebite retained change witness changed.")
            del tool["effects"][0]
            tool["effects"].remove(after)
        elif name in CONJURED:
            call, kind = CONJURED[name]
            _remove_around(tool, call, "{_presence.caster}", "_presence.created_time_stop_id", kind)
        else:
            raise ValueError("Unknown retained change owner: " + name)
        changed[name] = tool
    for name, helper in _helpers().items():
        if name in tools and tools[name] != helper:
            raise ValueError("The retained change helper ownership changed.")
    result = _replace_tools(text, changed)
    fragments = _ToolFragments(result)
    for name in _helpers():
        if name in tools:
            fragments.replace(name, "")
    return fragments.render()


def bind_runtime_consumers(text):
    """Bind actual source creation and final finite healing/condition delivery owners."""
    text = restore_runtime(text)
    tools = _load_runtime(text)["tools"]
    changed = _helpers()
    required = {
        "cast_srd_spell_ward",
        "resolve_srd_spell_ward_benefits",
        "start_srd_aura_of_life_turn",
        "cast_srd_control_afflictions",
        "retarget_srd_eyebite",
    }
    if required - tools.keys():
        raise ValueError("The native Ward/Eyebite owners are required before retained changes.")
    for name in required:
        changed[name] = deepcopy(tools[name])
    ward = changed["cast_srd_spell_ward"]
    _stamp_creation(ward, WARD_FIELD, "_caster_state_id")
    _mark(ward)
    query = changed["resolve_srd_spell_ward_benefits"]
    query["effects"][:0] = [_set(LIFE_ID, None), _set(LIFE_SERIAL, -1)]
    _life_branch(query).append(_life_selection())
    _mark(query)
    life = changed["start_srd_aura_of_life_turn"]
    call = next(
        e
        for nodes in _lists(life["effects"])
        for e in nodes
        if e.get("call") == "resolve_srd_spell_ward_benefits"
    )
    call["export_context"][LIFE_ID] = LIFE_ID
    owners = [
        nodes
        for nodes in _lists(life["effects"])
        if any(e.get("call") == "restore_hit_points" for e in nodes)
    ]
    if len(owners) != 1 or any(p["name"] == "lasting_state" for p in life["parameters"]):
        raise ValueError("The actual Aura healing binding changed.")
    owners[0].insert(0, _set("_time_stop_life_source", "state.spell_ward_sources[" + LIFE_ID + "]"))
    _around(
        life,
        "restore_hit_points",
        "{_time_stop_life_source.caster_state_id}",
        "_time_stop_life_source.created_time_stop_id",
        "healing",
    )
    # Native Life had no state binding; qualify the actual persistent Ward source.
    life["parameters"].append(_entity("lasting_state") | {"required": False, "default": "state"})
    owners[0][0]["set"]["value"] = "lasting_state.spell_ward_sources[" + LIFE_ID + "]"
    _mark(life)
    eye = changed["cast_srd_control_afflictions"]
    _eye_branch(eye).extend(_eye_stamp())
    _mark(eye)
    retarget = changed["retarget_srd_eyebite"]
    before, after = _eye_witness()
    retarget["effects"].insert(0, before)
    # Keep the existing public finalizer last, after the actual native condition observer.
    finalizer = [
        i
        for i, e in enumerate(retarget["effects"])
        if any(n.get("call") == "finalize_srd_time_stop" for nodes in _lists([e]) for n in nodes)
    ]
    if len(finalizer) != 1:
        raise ValueError("The public Eyebite Time Stop finalizer changed.")
    retarget["effects"].insert(finalizer[0], after)
    _mark(retarget)
    for name, (call, kind) in CONJURED.items():
        if name in tools:
            tool = deepcopy(tools[name])
            _around(tool, call, "{_presence.caster}", "_presence.created_time_stop_id", kind)
            _mark(tool)
            changed[name] = tool
    return _replace_tools(text, changed)
