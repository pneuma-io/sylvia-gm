"""Settle actual queued Mansion occupants at completed package action boundaries."""

from copy import deepcopy

import yaml

if __package__:
    from .srd_scene_relations import _array, _tool_scene
    from .srd_spell_travel_dwelling import _lookup
    from .srd_timed_actor_defenses import _call, _load_runtime, _parameter, _require, _set
    from .srd_timed_senses import _replace_tools
else:
    from srd_scene_relations import _array, _tool_scene
    from srd_spell_travel_dwelling import _lookup
    from srd_timed_actor_defenses import _call, _load_runtime, _parameter, _require, _set
    from srd_timed_senses import _replace_tools


RETURN = "return_srd_mansion_subject"
QUALIFY = "qualify_srd_mansion_return"
OUTERS = (
    "advance_srd_lasting_time",
    "advance_srd_turn",
    "cast_spell",
    "cast_spell_outside_combat",
    "continue_srd_spell_cast",
    "complete_srd_pending_action",
    "dismiss_srd_spell",
    "move_srd_scene_creature",
    "continue_srd_rest",
)


def _qualifier():
    """A return needs the exact queued source and current body, not a caller force verdict."""
    return _tool_scene(
        "Qualify the unused actual Mansion return receipt against its retained source and body.",
        [_parameter("subject"), _array("destination"), _parameter("plane")],
        [
            _set("_mr_receipt", "state.mansion_return_receipt"),
            _require(
                "_mr_receipt != null", "Only actual queued Mansion settlement may return a body."
            ),
            _require(
                "_mr_receipt.kind == 'mansion_return' and _mr_receipt.consumed != true and "
                "_mr_receipt.subject == subject and "
                "_mr_receipt.plane == plane",
                "Choose the exact unused source-derived return body and destination.",
            ),
            _require(
                " and ".join(f"_mr_receipt.destination[{i}] == destination[{i}]" for i in range(3)),
                "Keep the actual source-derived nearest return point.",
            ),
            *_lookup("_mr_receipt.source_id"),
            _require(
                "_xd_source.spell == 'magnificent_mansion' and _xd_source.active == false and "
                "_xd_source.return_pending == true and _xd_source.origin_plane == plane and "
                "_xd_source.room_id == _mr_receipt.origin_plane and "
                "_xd_source.outer_id == _mr_receipt.outer_id and "
                "_xd_source.created_tick == _mr_receipt.created_tick",
                "The exact retired Mansion source must still own this queued return.",
            ),
            _call(
                "srd_information_scene_entity",
                {"subject": "{subject}"},
                ["_info_scene_entity", "_info_position"],
            ),
            _require(
                "_info_scene_entity.active == true and "
                "_info_scene_entity.id == _mr_receipt.body_id and "
                "_info_scene_entity.metadata.plane == _xd_source.room_id and "
                "_info_scene_entity.footprint.kind == _mr_receipt.footprint.kind and "
                "_info_scene_entity.height == _mr_receipt.height and "
                "_info_scene_entity.id != _xd_source.inner_id",
                "Return only this actual body still inside its own source room.",
            ),
            _require(
                " and ".join(
                    f"_info_scene_entity.position[{i}] == _mr_receipt.origin[{i}]" for i in range(3)
                ),
                "The same actual body must still occupy its recorded source-room point.",
            ),
            {
                "condition": "_mr_receipt.footprint.kind == 'box'",
                "on_true": [
                    _require(
                        " and ".join(
                            f"_info_scene_entity.footprint.half_extents[{i}] == "
                            f"_mr_receipt.footprint.half_extents[{i}]"
                            for i in range(2)
                        ),
                        "Keep the actual body dimensions used by nearest placement.",
                    )
                ],
                "on_false": [
                    _require(
                        "_mr_receipt.footprint.kind == 'circle' and "
                        "_info_scene_entity.footprint.radius == _mr_receipt.footprint.radius",
                        "Keep the actual supported curved body used by nearest placement.",
                    )
                ],
            },
            _require(
                "state.pending_srd_action_stack == null or "
                "len(state.pending_srd_action_stack) == 0",
                "Complete the real saved window before Mansion return.",
            ),
        ],
    )


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _normalize(tool):
    normalized = _tool_scene(tool["description"], tool["parameters"], tool["effects"])
    tool["parameters"], tool["effects"] = normalized["parameters"], normalized["effects"]
    return tool


def _qualify_call():
    return _call(
        QUALIFY, {"subject": "{subject}", "destination": "{destination}", "plane": "{plane}"}
    )


def _receiver_guards(tools, original):
    """Existing wards distinguish this genuine involuntary magical return from voluntary travel."""
    effects = []
    for name, flag, export, supplied in (
        (
            "check_srd_secure_area_movement",
            "state.has_spell_secure_areas",
            "_secure_area_movement_allowed",
            {"teleport": False, "magical": True, "voluntary": False},
        ),
        (
            "check_srd_hallow_movement",
            "state.has_hallow_sources",
            "_hallow_movement_allowed",
            {"teleport": False, "voluntary": False},
        ),
    ):
        if name in str(original["effects"]):
            continue
        if name not in tools:
            effects.append(
                _require(f"{flag} != true", "Emit the real ward owner before Mansion return.")
            )
            continue
        exported = [export] + (
            ["_secure_area_magic_save_source_ids"] if "secure_area" in name else []
        )
        guarded = [
            _call(
                name,
                {
                    "subject": "{subject}",
                    "destination": "{destination}",
                    "plane": "{plane}",
                    **supplied,
                },
                exported,
            ),
            _require(f"{export} == true", "The actual ward prevents this source-owned return."),
        ]
        if "secure_area" in name:
            guarded.append(
                _require(
                    "len(_secure_area_magic_save_source_ids) == 0",
                    "An actual pending transit save needs its owning resolution.",
                )
            )
        effects.append({"condition": f"{flag} == true", "on_true": guarded})
    return effects


def _specialize(tools, original):
    """Clone final relocation without removing any original guard, mutation or aftermath."""
    tool = deepcopy(original)
    fixed = {
        "teleport": False,
        "levitate_source_id": "",
        "telekinesis_source_id": "",
        "sphere_source_id": "",
    }
    removed = [p for p in tool["parameters"] if p["name"] in fixed]
    tool["parameters"] = [p for p in tool["parameters"] if p["name"] not in fixed]
    tool["effects"][:0] = [_qualify_call(), *[_set(p["name"], fixed[p["name"]]) for p in removed]]
    for item in _walk(tool["effects"]):
        if item.get("call") == "check_srd_secure_area_movement":
            item["params"].update(magical=True, voluntary=False)
        elif item.get("call") == "check_srd_hallow_movement":
            item["params"]["voluntary"] = False
    return tool


def _insert_after(value, wanted, added):
    if isinstance(value, list):
        for index, item in enumerate(value):
            if item is wanted:
                value[index + 1 : index + 1] = added
                return True
            if _insert_after(item, wanted, added):
                return True
    elif isinstance(value, dict):
        return any(_insert_after(item, wanted, added) for item in value.values())
    return False


def _receiving_event(tools, original):
    if "resolve_srd_forbiddance_event" in str(original["effects"]):
        return []
    if "resolve_srd_forbiddance_event" not in tools:
        return [
            _require(
                "state.has_spell_secure_areas != true", "Emit the actual receiving ward owner."
            )
        ]
    return [
        {
            "condition": "state.has_spell_secure_areas == true and _travel_guard_needed == true",
            "on_true": [
                _call(
                    "resolve_srd_forbiddance_event",
                    {
                        "target_state": "{_travel_guard_target}",
                        "event": "entry",
                        "origin": "{state.mansion_return_receipt.origin}",
                        "origin_plane": "{state.mansion_return_receipt.origin_plane}",
                    },
                    ["_secure_forbiddance_packets"],
                ),
            ],
        }
    ]


def bind_runtime_consumers(text):
    """Join real outer completions; raw source retirement remains queue-only and idempotent."""
    text = text.replace("\r\n", "\n")
    tools = _load_runtime(text)["tools"]
    if "settle_srd_extradimensional_returns" not in tools:
        raise ValueError("Emit the actual retained Mansion source and return owner first.")
    changed = {QUALIFY: _qualifier()}
    wrapper = _specialize(tools, tools["move_srd_travel_subject"])
    node_calls = [
        e for e in _walk(wrapper["effects"]) if e.get("call") == "move_srd_travel_subject_node"
    ]
    if node_calls:
        if len(node_calls) != 1:
            raise ValueError("Mansion return must use exactly one final relocation node.")
        node = _specialize(tools, tools["move_srd_travel_subject_node"])
        node_call = node_calls[0]
        node_call["call"] = RETURN + "_node"
        for name in ("teleport", "levitate_source_id", "telekinesis_source_id", "sphere_source_id"):
            node_call["params"].pop(name, None)
        changed[RETURN + "_node"] = node
        position_owner = node
        _insert_after(
            wrapper["effects"], node_call, _receiving_event(tools, tools["move_srd_travel_subject"])
        )
    else:
        position_owner = wrapper
    mutation = [e for e in position_owner["effects"] if "spatial_mutation" in e]
    if len(mutation) != 1:
        raise ValueError("Preserve the final owner's one actual position mutation.")
    boundary = position_owner["effects"].index(mutation[0])
    position_owner["effects"][boundary:boundary] = [
        *_receiver_guards(tools, position_owner),
        _qualify_call(),
        _set("state.mansion_return_receipt.consumed", True),
    ]
    if not node_calls:
        _insert_after(
            position_owner["effects"],
            mutation[0],
            _receiving_event(tools, tools["move_srd_travel_subject"]),
        )
    changed[RETURN] = _normalize(wrapper)
    if node_calls:
        _normalize(changed[RETURN + "_node"])

    settle = deepcopy(tools["settle_srd_extradimensional_returns"])
    for effects in [value for value in _walk(settle["effects"]) if "for_each" in value]:
        body_effects = effects["for_each"]["do"]
        # Only the owning body branch already derives actual nearest entrance-face placement.
        for branch in body_effects:
            if "on_true" not in branch:
                continue
            selected = branch["on_true"]
            index = next(
                (
                    i
                    for i, e in enumerate(selected)
                    if e.get("call") in {"move_srd_travel_subject", RETURN}
                ),
                None,
            )
            if index is None:
                continue
            call = selected[index]
            call["call"] = RETURN
            call["params"].pop("teleport", None)
            if index and selected[index - 1].get("set", {}).get("field", "").endswith(
                ".mansion_return_receipt"
            ):
                continue
            selected[index:index] = [
                _set(
                    "state.mansion_return_receipt",
                    {
                        "kind": "'mansion_return'",
                        "source_id": "_xd_source.id",
                        "created_tick": "_xd_source.created_tick",
                        "subject": "_xd_return_body.id",
                        "body_id": "_xd_return_body.id",
                        "origin": "_xd_return_body.position",
                        "footprint": "_xd_return_body.footprint",
                        "height": "_xd_return_body.height",
                        "origin_plane": "_xd_source.room_id",
                        "outer_id": "_xd_source.outer_id",
                        "destination": "_travel_placement.position",
                        "plane": "_xd_source.origin_plane",
                        "consumed": False,
                    },
                )
            ]
            selected[index + 2 : index + 2] = [_set("state.mansion_return_receipt", None)]
    changed["settle_srd_extradimensional_returns"] = _normalize(settle)
    for name in OUTERS:
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        if "settle_srd_extradimensional_returns" not in str(tool["effects"]):
            tool["effects"].append(
                {
                    "condition": "state.has_extradimensional_pending_returns == true and "
                    "(state.pending_srd_action_stack == null or "
                    "len(state.pending_srd_action_stack) == 0)",
                    "on_true": [_call("settle_srd_extradimensional_returns", {})],
                }
            )
        changed[name] = _normalize(tool)
    existing = {name: tool for name, tool in changed.items() if name in tools}
    text = _replace_tools(text, existing)
    additions = {name: tool for name, tool in changed.items() if name not in tools}
    if additions:
        rendered = yaml.dump(
            additions,
            Dumper=getattr(yaml, "CSafeDumper", yaml.SafeDumper),
            sort_keys=False,
            width=105,
        )
        rendered = "".join(
            "  " + line if line.strip() else line for line in rendered.splitlines(True)
        )
        if "\nprocedures:" not in text:
            raise ValueError("Preserve the real runtime procedures boundary.")
        text = text.replace("\nprocedures:", "\n" + rendered + "procedures:", 1)
    return text
