"""Attribute retained hazard damage to the Time Stop that created its real source."""

from copy import deepcopy

if __package__:
    from .srd_runtime_fragments import _ToolFragments
    from .srd_time_stop import ACTIVE, _bind_state
    from .srd_time_stop_effects import _lists
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
    from srd_time_stop_effects import _lists
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


OBSERVER = "observe_srd_time_stop_retained_damage"
CREATION_MARKER = "time_stop_retained_presence_stamp"
# These are actual source-qualified packet leaves, including the two renamed receipts.
PRODUCERS = {
    "resolve_srd_wall_of_fire_event": "_sphere_event_source",
    "resolve_srd_blade_barrier_event": "_sphere_event_source",
    "resolve_srd_black_tentacles_event": "_black_tentacles_source",
    "resolve_srd_spirit_guardians_event": "_guardian_event_source",
    "resolve_srd_woodland_event": "_woodland_source",
    "apply_srd_conjure_elemental_damage": "_elemental_damage_source",
    "apply_srd_spike_growth_damage": "_spike_damage_source",
    "resolve_srd_celestial_initial_lights": "_presence",
    "resolve_srd_celestial_event_lights": "_presence",
    "attack_srd_conjured_fey": "_presence",
}


def _observer():
    """Consume an earned packet receipt without rechecking a source ended by its damage."""
    return _tool(
        "End only the current stop that created this positively damaging retained source.",
        [
            _entity("target_state"),
            _entity("source_state"),
            _parameter("created_time_stop_id", model_hidden=True),
            _parameter("effective_damage", "number", minimum=0, model_hidden=True),
        ],
        [
            _when(
                ACTIVE + " and effective_damage > 0 and "
                "created_time_stop_id == state.time_stop_source.cast_id and "
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
    )


def _remove_observation(tool):
    """Remove only this adapter's exact one-call wrapper before rebinding the fresh leaf."""
    for nodes in _lists(tool["effects"]):
        for index in range(len(nodes) - 1, -1, -1):
            node = nodes[index]
            children = node.get("on_true", [])
            if not any(child.get("call") == OBSERVER for child in children):
                continue
            if set(node) != {"condition", "on_true"} or len(children) != 1:
                raise ValueError("The retained Time Stop observation wrapper changed.")
            params = children[0].get("params", {})
            receipt = params.get("created_time_stop_id", "")
            effective = params.get("effective_damage", "")
            if (
                set(params)
                != {"target_state", "source_state", "created_time_stop_id", "effective_damage"}
                or not receipt.startswith("{")
                or not receipt.endswith("}")
                or not effective.startswith("{")
                or not effective.endswith("}")
                or node["condition"] != receipt[1:-1] + " != null and " + effective[1:-1] + " > 0"
            ):
                raise ValueError("The retained Time Stop receipt guard changed.")
            del nodes[index]
    if any(node.get("call") == OBSERVER for nodes in _lists(tool["effects"]) for node in nodes):
        raise ValueError("The retained Time Stop callback has an unknown owner.")


def _packet_observation(tool, source, name):
    """Read the actual packet's export map; a differently named receipt cannot be stale."""
    _remove_observation(tool)
    found = []
    for nodes in _lists(tool["effects"]):
        for index, node in enumerate(nodes):
            if node.get("call") != "apply_srd_qualifying_damage":
                continue
            if node.get("params", {}).get("source_state_id") != "{" + source + ".caster}":
                raise ValueError("The retained source's actual caster changed: " + name)
            target = node["params"].get("target")
            if (
                not isinstance(target, str)
                or not target.startswith("{")
                or not target.endswith("}")
            ):
                raise ValueError("The retained source's actual target changed: " + name)
            exports = node.get("export_context", {})
            if not isinstance(exports, dict):
                raise ValueError(
                    "The retained source requires an explicit fresh packet receipt: " + name
                )
            receipts = [alias for alias, value in exports.items() if value == "_effective_damage"]
            if len(receipts) != 1:
                raise ValueError("The retained packet's effective damage export changed: " + name)
            receipt = receipts[0]
            found.append(
                (
                    nodes,
                    index,
                    _when(
                        source + ".created_time_stop_id != null and " + receipt + " > 0",
                        [
                            _call(
                                OBSERVER,
                                {
                                    "target_state": target,
                                    "source_state": "{" + source + ".caster}",
                                    "created_time_stop_id": "{" + source + ".created_time_stop_id}",
                                    "effective_damage": "{" + receipt + "}",
                                },
                            )
                        ],
                    ),
                )
            )
    if len(found) != 1:
        raise ValueError("The actual retained damage leaf changed: " + name)
    nodes, index, observation = found[0]
    nodes.insert(index + 1, observation)


def _unstamp_presence(tool):
    """Restore just the independent Conjured constructor's original source and binding."""
    metadata = tool.get("metadata", {})
    if CREATION_MARKER not in metadata:
        return
    added = metadata.pop(CREATION_MARKER)
    if not isinstance(added, bool):
        raise ValueError("The retained Conjured creation marker changed.")
    prelude = [
        _set("_time_stop_creation_receipt", None),
        _when(
            ACTIVE + " and _caster_state_id == state.time_stop_source.caster and "
            "state.time_stop_source.turn_running == true",
            [_set("_time_stop_creation_receipt", "state.time_stop_source.cast_id")],
        ),
    ]
    holder = {"owner": {"effects": prelude, "parameters": []}}
    _bind_state(holder)
    if tool["effects"][:2] != holder["owner"]["effects"]:
        raise ValueError("The retained Conjured creation prelude changed.")
    del tool["effects"][:2]
    stamps = 0
    records = 0
    stamp = _when(
        "_time_stop_creation_receipt != null",
        [_set("_presence.created_time_stop_id", "_time_stop_creation_receipt")],
    )
    for nodes in _lists(tool["effects"]):
        for index in range(len(nodes) - 1, -1, -1):
            node = nodes[index]
            if node == stamp:
                del nodes[index]
                stamps += 1
            value = node.get("set", {}).get("value")
            if node.get("set", {}).get("field") == "_presence" and isinstance(value, dict):
                if value.pop("created_time_stop_id", "missing") is not None:
                    raise ValueError("The retained Conjured creation record changed.")
                records += 1
    if (stamps, records) != (1, 1):
        raise ValueError("The retained Conjured creation ownership changed.")
    if added:
        tool["parameters"] = [p for p in tool["parameters"] if p["name"] != "time_stop_state"]


def _stamp_presence(tool):
    """Stamp the genuine constructor dict; its later merge assignments preserve that field."""
    records = [
        (nodes, index)
        for nodes in _lists(tool["effects"])
        for index, node in enumerate(nodes)
        if node.get("set", {}).get("field") == "_presence"
        and isinstance(node["set"]["value"], dict)
    ]
    if len(records) != 1:
        raise ValueError("The retained Conjured source constructor changed.")
    nodes, index = records[0]
    record = nodes[index]["set"]["value"]
    if record.get("caster") != "_caster_state_id" or "created_time_stop_id" in record:
        raise ValueError("The retained Conjured creation receipt ownership changed.")
    record["created_time_stop_id"] = None
    nodes.insert(
        index + 1,
        _when(
            "_time_stop_creation_receipt != null",
            [_set("_presence.created_time_stop_id", "_time_stop_creation_receipt")],
        ),
    )
    tool["effects"][:0] = [
        _set("_time_stop_creation_receipt", None),
        _when(
            ACTIVE + " and _caster_state_id == state.time_stop_source.caster and "
            "state.time_stop_source.turn_running == true",
            [_set("_time_stop_creation_receipt", "state.time_stop_source.cast_id")],
        ),
    ]


def restore_runtime(text):
    """Restore original producer bodies before Environment or Conjured regeneration."""
    runtime = _load_runtime(text)
    if OBSERVER in runtime["tools"] and runtime["tools"][OBSERVER] != _observer():
        raise ValueError("The retained Time Stop helper ownership changed.")
    changed = {}
    for name in PRODUCERS:
        if name in runtime["tools"]:
            tool = deepcopy(runtime["tools"][name])
            _remove_observation(tool)
            changed[name] = tool
    if "install_srd_conjured_presence" in runtime["tools"]:
        tool = deepcopy(runtime["tools"]["install_srd_conjured_presence"])
        _unstamp_presence(tool)
        changed["install_srd_conjured_presence"] = tool
    # Tool emission owns its one private helper; other Time Stop observers stay intact.
    from_text = _replace_tools(text, changed)
    if OBSERVER not in runtime["tools"]:
        return from_text
    fragments = _ToolFragments(from_text)
    if not fragments.prior(OBSERVER):
        raise ValueError("The retained Time Stop helper fragment changed.")
    fragments.replace(OBSERVER, "")
    return fragments.render()


def bind_runtime_consumers(text):
    """Bind final retained delivery leaves after their source, geometry and roll owners."""
    tools = _load_runtime(text)["tools"]
    if OBSERVER in tools and tools[OBSERVER] != _observer():
        raise ValueError("The retained Time Stop helper ownership changed.")
    changed = {OBSERVER: _observer()}
    for name, source in PRODUCERS.items():
        if name in tools:
            tool = deepcopy(tools[name])
            _packet_observation(tool, source, name)
            changed[name] = tool
    if "install_srd_conjured_presence" in tools:
        tool = deepcopy(tools["install_srd_conjured_presence"])
        _unstamp_presence(tool)
        added = not any(p["name"] == "time_stop_state" for p in tool["parameters"])
        _stamp_presence(tool)
        _bind_state({"constructor": tool})
        tool["metadata"][CREATION_MARKER] = added
        changed["install_srd_conjured_presence"] = tool
    return _replace_tools(text, changed)
