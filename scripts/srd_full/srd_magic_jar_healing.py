"""Occupied-body healing delegates the final HP owner and synchronizes only that exact host."""

from copy import deepcopy

if __package__:
    from .srd_control_behaviors import _call, _param, _when
    from .srd_magic_jar_consumers import _forward_params, _walk
    from .srd_timed_actor_defenses import _load_runtime
    from .srd_timed_senses import _replace_tools
else:
    from srd_control_behaviors import _call, _param, _when
    from srd_magic_jar_consumers import _forward_params, _walk
    from srd_timed_actor_defenses import _load_runtime
    from srd_timed_senses import _replace_tools


HEALING_OWNER = "restore_srd_magic_jar_body_hit_points"
HEALING_GUARD = (
    "magic_jar_state.has_magic_jar_sources == true and "
    "(target.magic_jar_role == 'host_soul' or target.magic_jar_role == 'possessed' or "
    "target.magic_jar_role == 'container')"
)


def bind_healing_consumers(text):
    """Compose after final healing hooks; keep their actual callbacks in the one delegated body."""
    tools = _load_runtime(text)["tools"]
    for name in (
        "restore_hit_points",
        "resolve_srd_magic_jar_target",
        "sync_srd_magic_jar_host_body",
    ):
        if name not in tools:
            raise ValueError(f"Magic Jar healing requires the existing owner {name}")
    original = deepcopy(tools["restore_hit_points"])
    effects = []
    for node in original["effects"]:
        if node.get("condition") == HEALING_GUARD:
            effects.extend(node["on_false"])
        else:
            effects.append(node)
    original["effects"] = effects
    original["parameters"] = [p for p in original["parameters"] if p["name"] != "magic_jar_state"]
    exports = set()

    def collect(node):
        field = node.get("set", {}).get("field", "")
        if field.startswith("_") and "." not in field:
            exports.add(field)
        exports.update(node.get("export_context", {}).keys())

    _walk(original["effects"], collect)
    delegated = deepcopy(original)
    delegated["parameters"].append(_param("jar_healing_host", resolve="entity", model_hidden=True))

    def qualify_body_flags(node):
        condition = node.get("condition")
        if isinstance(condition, str):
            node["condition"] = condition.replace(
                "target.spell_beacon_of_hope == true",
                "(target.spell_beacon_of_hope == true or "
                "jar_healing_host.spell_beacon_of_hope == true)",
            ).replace(
                "target.hit_point_recovery_blocked != true",
                "target.hit_point_recovery_blocked != true and "
                "jar_healing_host.hit_point_recovery_blocked != true",
            )

    _walk(delegated["effects"], qualify_body_flags)
    _walk(delegated.get("pre_conditions", []), qualify_body_flags)
    changed = {HEALING_OWNER: delegated}
    forwarded = _forward_params(original["parameters"]) | {
        "target": "{_magic_jar_target_id}",
        "jar_healing_host": "{_jar_source.host}",
    }
    wrapper = deepcopy(original)
    wrapper["parameters"].append(
        _param(
            "magic_jar_state",
            resolve="entity",
            required=False,
            default="state",
            model_hidden=True,
        )
    )
    wrapper["effects"] = [
        _when(
            HEALING_GUARD,
            [
                _call(
                    "resolve_srd_magic_jar_target",
                    {"target_state": "{target}"},
                    ("_magic_jar_target_id",),
                ),
                _call(
                    "read_srd_magic_jar_source",
                    {"caster_state": "{_magic_jar_target_id}"},
                    ("_jar_source",),
                ),
                _call(HEALING_OWNER, forwarded, tuple(sorted(exports))),
                _call(
                    "sync_srd_magic_jar_host_body",
                    {
                        "caster_state": "{_magic_jar_target_id}",
                        "host_state": "{_jar_source.host}",
                    },
                ),
            ],
            original["effects"],
        )
    ]
    changed["restore_hit_points"] = wrapper
    return _replace_tools(text, changed)
