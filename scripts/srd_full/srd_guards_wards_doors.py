"""Ward door benefits follow their actual retained parent without deleting sibling locks."""

from copy import deepcopy

if __package__:
    from .srd_control_behaviors import _call, _lasting, _param, _set, _tool, _walk, _when
    from .srd_timed_actor_defenses import _load_runtime
    from .srd_timed_senses import _replace_tools
else:
    from srd_control_behaviors import _call, _lasting, _param, _set, _tool, _walk, _when
    from srd_timed_actor_defenses import _load_runtime
    from srd_timed_senses import _replace_tools


def _child_tool():
    """Read only the selected child and parent; suppression never changes source lifetime."""
    return _tool(
        "Qualify one real ward Arcane Lock against its exact active parent and door.",
        [
            _param("source_id"),
            _param("group"),
            _param("spell_level", "integer"),
            _param("object_id"),
            _param("plane"),
            _lasting(),
        ],
        [
            _set("_guards_door_child_available", False),
            _when(
                "source_id != '' and group == 'doors' and "
                "lasting_state.spell_guards_wards_sources != null and source_id "
                "in lasting_state.spell_guards_wards_sources",
                [
                    _set(
                        "_guards_door_parent",
                        "lasting_state.spell_guards_wards_sources[source_id]",
                    ),
                    _set(
                        "_guards_door_child_available",
                        "_guards_door_parent.installed == true and "
                        "_guards_door_parent.spell_id == 'guards_and_wards' and "
                        "_guards_door_parent.groups.doors == true and "
                        "_guards_door_parent.suppressed != true and "
                        "(_guards_door_parent.permanent == true or "
                        "lasting_state.lasting_tick < _guards_door_parent.expires_tick) and "
                        "object_id in _guards_door_parent.door_ids and "
                        "plane == _guards_door_parent.plane and "
                        "spell_level == _guards_door_parent.spell_level",
                    ),
                ],
            ),
        ],
    )


def bind_door_consumers(text):
    """Join actual lock selection after object/ward owners and before final public wrappers."""
    tools = _load_runtime(text)["tools"]
    if "operate_srd_locked_object" not in tools or "end_srd_guards_wards_effect" not in tools:
        return text
    owner = (
        "operate_srd_locked_object_node"
        if "operate_srd_locked_object_node" in tools
        else "operate_srd_locked_object"
    )
    access = deepcopy(tools[owner])
    changed = {"check_srd_guards_wards_door_lock": _child_tool()}
    marker = "_guards_door_child_available == true"
    if not any(node.get("condition") == marker for node in _walk(access["effects"])):
        matched = False
        for node in _walk(access["effects"]):
            if node.get("set") == {"field": "_world_effective_lock", "value": "_world_lock"}:
                original = deepcopy(node)
                node.clear()
                node.update(
                    _when(
                        "_world_lock.ward_source_id != null",
                        [
                            _call(
                                "check_srd_guards_wards_door_lock",
                                {
                                    "source_id": "{_world_lock.ward_source_id}",
                                    "group": "{_world_lock.ward_group}",
                                    "spell_level": "{_world_lock.spell_level}",
                                    "object_id": "{_object_scene.id}",
                                    "plane": "{_object_scene.metadata.plane}",
                                    "lasting_state": "{_lasting_state_id}",
                                },
                                ("_guards_door_child_available",),
                            ),
                            _when(marker, [original]),
                        ],
                        [original],
                    )
                )
                matched = True
                break
        if not matched:
            raise SystemExit("Ward Door binding needs the actual Arcane Lock selection owner.")
    for node in _walk(access["effects"]):
        if node.get("condition") == "_world_lock_index == len(_world_locks) - 1":
            node["condition"] = "_world_lock.source_id == _world_effective_lock.source_id"
    if "check_srd_magic_zone_effect" in tools:
        gate = "_guards_door_magic_available"
        if not any(node.get("set", {}).get("field") == gate for node in access["effects"]):
            index = next(
                i
                for i, node in enumerate(access["effects"])
                if node.get("set", {}).get("field") == "_world_magically_locked"
            )
            access["effects"][index:index] = [
                _set(gate, True),
                _when(
                    "lasting_state.has_spell_magic_zones == true and len(_world_locks) > 0",
                    [
                        _call(
                            "check_srd_magic_zone_effect",
                            {
                                "source_subject": "{_object_scene.id}",
                                "target_subject": "{_object_scene.id}",
                                "effect_kind": "other_magic",
                            },
                            ("_magic_zone_effect_allowed",),
                        ),
                        _set(gate, "_magic_zone_effect_allowed == true"),
                        _when(f"{gate} != true", [_set("_world_effective_lock", None)]),
                    ],
                ),
            ]
    if not any(parameter["name"] == "lasting_state" for parameter in access["parameters"]):
        access["parameters"].append(_lasting())
    changed[owner] = access
    return _replace_tools(text, changed)
