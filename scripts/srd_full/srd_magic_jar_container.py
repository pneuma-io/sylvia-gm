"""Actual object destruction ends only the Magic Jar installed on that container."""

from copy import deepcopy

if __package__:
    from .srd_control_behaviors import _call, _lasting, _param, _require, _set, _when
    from .srd_magic_jar import _raw_scene
    from .srd_scene_relations import _tool_scene
    from .srd_timed_actor_defenses import _load_runtime
    from .srd_timed_senses import _replace_tools
else:
    from srd_control_behaviors import _call, _lasting, _param, _require, _set, _when
    from srd_magic_jar import _raw_scene
    from srd_scene_relations import _tool_scene
    from srd_timed_actor_defenses import _load_runtime
    from srd_timed_senses import _replace_tools


CALLBACK = "end_srd_destroyed_magic_jar_container"
ATTACK_OWNERS = (
    "resolve_srd_spell_object_attack",
    "resolve_srd_sorcerous_burst_object_attack",
)
HOOK_CONDITION = (
    "_object_destroyed == true and state.has_magic_jar_sources == true and "
    "_object_hp_before > 0 and _object_damage_applied > 0"
)


def _destroyed_container_tool():
    """Read persisted object HP and the exact installed source before resolving its souls."""
    return _tool_scene(
        "Resolve Magic Jar's actual source-ending consequence after its container is destroyed.",
        [_param("object_id"), _lasting()],
        [
            _call(
                "format_srd_information_projection",
                {"message": "{object_id}"},
                ("_info_projection_message",),
            ),
            _set("_jar_destroyed_object_id", "_info_projection_message"),
            *_raw_scene("_jar_destroyed_object_id", "_jar_destroyed_container"),
            _require(
                "_jar_destroyed_container.metadata.srd_spell_object.hp == 0 and "
                "_jar_destroyed_container.metadata.srd_spell_object.destroyed == true",
                "Container destruction must already be persisted by the actual object owner.",
            ),
            _set("_jar_destroyed_container_source", None),
            _when(
                "_jar_destroyed_container.metadata.srd_magic_jar != null",
                [
                    _set(
                        "_jar_destroyed_container_source",
                        "_jar_destroyed_container.metadata.srd_magic_jar",
                    ),
                ],
            ),
            _when(
                "_jar_destroyed_container_source != null and "
                "_jar_destroyed_container_source.installed == true",
                [
                    _require(
                        "_jar_destroyed_container_source.kind == 'magic_jar' and "
                        "_jar_destroyed_container_source.spell == 'magic_jar' and "
                        "_jar_destroyed_container_source.container == _jar_destroyed_object_id",
                        "Only the source actually installed on this destroyed container may end.",
                    ),
                    _call(
                        "end_srd_magic_jar_source",
                        {
                            "caster_state": "{_jar_destroyed_container_source.caster}",
                            "reason": "container_destroyed",
                        },
                    ),
                ],
            ),
        ],
    )


def bind_container_consumers(text):
    """Bind after object and Burst owners, preserving their single roll and persisted HP result."""
    tools = _load_runtime(text)["tools"]
    if "end_srd_magic_jar_source" not in tools:
        raise ValueError("Magic Jar's exact ending owner must precede its container binder")
    changed = {CALLBACK: _destroyed_container_tool()}
    for name in ATTACK_OWNERS:
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        tool["effects"] = [
            effect
            for effect in tool["effects"]
            if not (
                effect.get("condition") == HOOK_CONDITION
                and any(node.get("call") == CALLBACK for node in effect.get("on_true", []))
            )
        ]
        tool["effects"].append(
            _when(
                HOOK_CONDITION,
                [_call(CALLBACK, {"object_id": "{object_id}"})],
            )
        )
        changed[name] = tool
    return _replace_tools(text, changed)
