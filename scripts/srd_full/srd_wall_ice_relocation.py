"""Consume only an actual paid Wall of Ice formation push through final Travel owners."""

import re
from copy import deepcopy

import yaml

if __package__:
    from .srd_timed_actor_defenses import (
        _call,
        _load_runtime,
        _require,
        _RuntimeDumper,
        _set,
    )
    from .srd_timed_senses import _replace_tools
else:
    from srd_timed_actor_defenses import (
        _call,
        _load_runtime,
        _require,
        _RuntimeDumper,
        _set,
    )
    from srd_timed_senses import _replace_tools


PUSH = "push_srd_wall_ice_subject"
NODE = PUSH + "_node"
SOURCE_NODE = "move_srd_travel_subject_node"
SOURCE_WRAPPER = "move_srd_travel_subject"


def _source_semantics(value):
    """Classify this authenticated spell consequence without changing ordinary callers."""
    if isinstance(value, list):
        return [_source_semantics(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _source_semantics(item) for key, item in value.items()}
    if result.get("call") == "check_srd_secure_area_movement":
        result["params"].update(magical=True, voluntary=False)
    elif result.get("call") == "check_srd_hallow_movement":
        result["params"]["voluntary"] = False
    return result


def _fixed_parameters(tool):
    """Keep real state bindings; callers cannot supply teleport or other source bypass IDs."""
    fixed = {
        "teleport": False,
        "levitate_source_id": "",
        "telekinesis_source_id": "",
        "sphere_source_id": "",
    }
    removed = [p for p in tool["parameters"] if p["name"] in fixed]
    tool["parameters"] = [p for p in tool["parameters"] if p["name"] not in fixed]
    aliases = {p["name"]: "_ice_fixed_" + p["name"] for p in removed}

    def bind_locals(value):
        if isinstance(value, str):
            for name, alias in aliases.items():
                value = re.sub(r"(?<![\w.'\"])" + re.escape(name) + r"\b", alias, value)
            return value
        if isinstance(value, dict):
            return {key: bind_locals(child) for key, child in value.items()}
        if isinstance(value, list):
            return [bind_locals(child) for child in value]
        return value

    tool["effects"] = bind_locals(tool["effects"])
    tool["effects"][:0] = [_set(aliases[p["name"]], fixed[p["name"]]) for p in removed]
    return {p["name"] for p in removed}


def _qualify():
    return [
        _call(
            "qualify_srd_wall_ice_push",
            {
                "subject": "{subject}",
                "destination": "{destination}",
                "plane": "{plane}",
                "lasting_state": "{_lasting_state_id}",
            },
            ["_ice_push_allowed", "_ice_push_caster"],
        ),
        _require(
            "_ice_push_allowed == true",
            "Only the exact unused paid Wall of Ice formation receipt may push this body.",
        ),
    ]


def _commit_guard():
    """Revalidate the source and swept body, then consume immediately before the sole mutation."""
    return [
        *_qualify(),
        _require(
            "plane == _travel_mover.metadata.plane or "
            "(_travel_mover.metadata.plane == null and plane == 'material')",
            "Wall formation pushes only within the actual body's plane.",
        ),
        _set("_ice_push_receiver_origin", "_travel_mover.position"),
        _set("_ice_push_material", "plane == 'material'"),
        {
            "spatial_query": {
                "scene": "state.spatial_scene",
                "operation": "sweep",
                "query": {
                    "map_id": "{lasting_state.spatial_scene.map.id}",
                    "moving_entity_id": "{_travel_mover.id}",
                    "destination": "{destination}",
                    "include_entities": True,
                    "metadata_filter": {
                        "key": "plane",
                        "value": "{plane}",
                        "missing_matches": "{_ice_push_material}",
                    },
                },
            },
            "store_as": "_ice_push_sweep",
        },
        _require(
            "_ice_push_sweep.supported == true and _ice_push_sweep.clear == true",
            "The actual body cannot be pushed through solids or occupied space.",
        ),
        _set("lasting_state.wall_ice_pending_push.consumed", True),
    ]


def _receiver_guards(tools, original):
    """Qualify existing secure sources using the printed involuntary magical consequence."""
    result = []
    for name, flag, exported, supplied in (
        (
            "check_srd_secure_area_movement",
            "lasting_state.has_spell_secure_areas",
            "_secure_area_movement_allowed",
            {"teleport": False, "magical": True, "voluntary": False},
        ),
        (
            "check_srd_hallow_movement",
            "lasting_state.has_hallow_sources",
            "_hallow_movement_allowed",
            {"teleport": False, "voluntary": False},
        ),
    ):
        if name in str(original["effects"]):
            continue
        if name not in tools:
            result.append(
                _require(
                    f"{flag} != true",
                    "Emit the actual secure source owner before its forced receiver.",
                )
            )
            continue
        exports = [exported]
        effects = [
            _call(
                name,
                {
                    "subject": "{subject}",
                    "destination": "{destination}",
                    "plane": "{plane}",
                    **supplied,
                },
                exports
                + (
                    ["_secure_area_magic_save_source_ids"]
                    if name == "check_srd_secure_area_movement"
                    else []
                ),
            ),
            _require(
                f"{exported} == true",
                "The actual secure source prevents this paid Ice consequence.",
            ),
        ]
        if name == "check_srd_secure_area_movement":
            effects.append(
                _require(
                    "len(_secure_area_magic_save_source_ids) == 0",
                    "An unsupported paid transit save cannot be replaced with an Ice push.",
                )
            )
        result.append({"condition": f"{flag} == true", "on_true": effects})
    return result


def _receiver_event(tools, original):
    """Observe entry at the real receiving body after the sole position commit."""
    if "resolve_srd_forbiddance_event" in str(original["effects"]):
        return []
    if "resolve_srd_forbiddance_event" not in tools:
        return [
            _require(
                "lasting_state.has_spell_secure_areas != true",
                "Emit the actual secure entry observer before its forced receiver.",
            )
        ]
    return [
        {
            "condition": "lasting_state.has_spell_secure_areas == true",
            "on_true": [
                _call(
                    "resolve_srd_forbiddance_event",
                    {
                        "target_state": "{lasting_state.wall_ice_pending_push.target_state_id}",
                        "event": "entry",
                        "origin": "{_ice_push_receiver_origin}",
                        "origin_plane": "{plane}",
                    },
                    ["_secure_forbiddance_packets"],
                )
            ],
        }
    ]


def _specialize(tool, tools):
    result = deepcopy(tool)
    result["effects"] = _source_semantics(result["effects"])
    _fixed_parameters(result)
    result["effects"][:0] = _qualify()
    mutations = [i for i, effect in enumerate(result["effects"]) if "spatial_mutation" in effect]
    if len(mutations) != 1:
        raise ValueError("Specialize only the final Travel owner's one actual position mutation.")
    boundary = mutations[0]
    guards = _receiver_guards(tools, tool)
    result["effects"][boundary:boundary] = [*guards, *_commit_guard()]
    result["description"] = "Commit an authenticated paid Wall of Ice push and its real aftermath."
    return result


def _emit(text, changed):
    existing = _load_runtime(text)["tools"]
    text = _replace_tools(text, {n: t for n, t in changed.items() if n in existing})
    additions = {n: t for n, t in changed.items() if n not in existing}
    if additions:
        rendered = yaml.dump(additions, Dumper=_RuntimeDumper, sort_keys=False, width=105)
        rendered = "".join(
            "  " + line if line.strip() else line for line in rendered.splitlines(True)
        )
        if "\nprocedures:" not in text:
            raise ValueError("The actual runtime procedures boundary is missing.")
        text = text.replace("\nprocedures:", "\n" + rendered + "procedures:", 1)
    return text


def _after_call(value, call, effects):
    """Keep receiver observation immediately after its owning call, including nested wrappers."""
    if isinstance(value, list):
        for i, item in enumerate(value):
            if item is call:
                value[i + 1 : i + 1] = effects
                return True
            if _after_call(item, call, effects):
                return True
    elif isinstance(value, dict):
        return any(_after_call(item, call, effects) for item in value.values())
    return False


def bind_runtime_consumers(text):
    """Capture final relocation guards and callbacks; the ordinary owners stay untouched."""
    text = text.replace("\r\n", "\n")
    tools = _load_runtime(text)["tools"]
    if "qualify_srd_wall_ice_push" not in tools:
        raise ValueError("Emit the actual paid Wall of Ice push qualifier before its consumer.")
    wrapper = deepcopy(tools[SOURCE_WRAPPER])
    calls = []

    def find_node(value):
        if isinstance(value, list):
            for child in value:
                find_node(child)
        elif isinstance(value, dict):
            if value.get("call") == SOURCE_NODE:
                calls.append(value)
            for child in value.values():
                find_node(child)

    find_node(wrapper["effects"])
    if len(calls) == 1 and SOURCE_NODE in tools:
        node = _specialize(tools[SOURCE_NODE], tools)
        wrapper["effects"] = _source_semantics(wrapper["effects"])
        _fixed_parameters(wrapper)
        calls.clear()
        find_node(wrapper["effects"])
        source_call = calls[0]
        source_call["call"] = NODE
        if source_call.get("export_context") != "*":
            source_call.setdefault("export_context", {})["_ice_push_receiver_origin"] = (
                "_ice_push_receiver_origin"
            )
        for name in ("teleport", "levitate_source_id", "telekinesis_source_id", "sphere_source_id"):
            source_call["params"].pop(name, None)
        wrapper["effects"][:0] = _qualify()
        if not _after_call(
            wrapper["effects"], source_call, _receiver_event(tools, tools[SOURCE_WRAPPER])
        ):
            raise ValueError("The actual relocation call must retain its receiver boundary.")
        changed = {NODE: node, PUSH: wrapper}
    else:
        tool = _specialize(wrapper, tools)
        boundary = next(i for i, e in enumerate(tool["effects"]) if "spatial_mutation" in e) + 1
        tool["effects"][boundary:boundary] = _receiver_event(tools, wrapper)
        changed = {PUSH: tool}
    return _emit(text, changed)
