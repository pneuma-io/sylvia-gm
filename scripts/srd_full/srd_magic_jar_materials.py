"""Magic Jar retains casting capability while spending the occupied body's real supplies."""

import re
from copy import deepcopy

if __package__:
    from .srd_control_behaviors import _call, _param, _require, _when
    from .srd_magic_jar_consumers import _forward_params, _walk
    from .srd_timed_actor_defenses import _load_runtime
    from .srd_timed_senses import _replace_tools
else:
    from srd_control_behaviors import _call, _param, _require, _when
    from srd_magic_jar_consumers import _forward_params, _walk
    from srd_timed_actor_defenses import _load_runtime
    from srd_timed_senses import _replace_tools


MATERIAL_OWNER = "resolve_srd_magic_jar_body_materials"
MATERIAL_GUARD = (
    "jar_material_state.has_magic_jar_sources == true and "
    "caster_state.magic_jar_role == 'possessed'"
)


def _physical_material_fields(value):
    """Redirect only physical stock; entitlement and source callbacks retain the logical caster."""
    if isinstance(value, str):
        return re.sub(
            r"\bcaster_state\.(inventory|spell_materials)\b",
            r"jar_material_body.\1",
            value,
        )
    if isinstance(value, list):
        return [_physical_material_fields(item) for item in value]
    if isinstance(value, dict):
        return {key: _physical_material_fields(item) for key, item in value.items()}
    return value


def bind_material_consumers(text):
    """Bind after final material hooks and Jar owners, before the final cast/continuation clones."""
    tools = _load_runtime(text)["tools"]
    for name in ("resolve_srd_spell_materials", "read_srd_magic_jar_source"):
        if name not in tools:
            raise ValueError(f"Magic Jar physical materials require {name}")
    original = deepcopy(tools["resolve_srd_spell_materials"])
    effects = []
    for node in original["effects"]:
        if node.get("condition") == MATERIAL_GUARD:
            effects.extend(node["on_false"])
        else:
            effects.append(node)
    original["effects"] = effects
    original["parameters"] = [
        p for p in original["parameters"] if p["name"] != "jar_material_state"
    ]
    exports = {"_material_rows"}

    def collect_exports(node):
        if node.get("call") == "resolve_srd_spell_materials":
            exports.update(node.get("export_context", {}).values())

    _walk(tools, collect_exports)
    delegated = deepcopy(original)
    delegated["parameters"].append(_param("jar_material_body", resolve="entity", model_hidden=True))
    delegated["effects"] = _physical_material_fields(delegated["effects"])
    delegated["pre_conditions"] = _physical_material_fields(delegated.get("pre_conditions", []))
    specialized = []
    if "resolve_srd_warding_bond_materials" in str(original["effects"]):
        specialized.append(
            _require(
                "spell != 'warding_bond'",
                "Occupied-body Warding Bond Material access needs its actual worn-ring owner.",
            )
        )
    forwarded = _forward_params(original["parameters"]) | {
        "jar_material_body": "{_jar_source.host}"
    }
    wrapper = deepcopy(original)
    wrapper["parameters"].append(
        _param(
            "jar_material_state",
            resolve="entity",
            required=False,
            default="state",
            model_hidden=True,
        )
    )
    wrapper["effects"] = [
        _when(
            MATERIAL_GUARD,
            [
                _call(
                    "read_srd_magic_jar_source",
                    {"caster_state": "{_caster_state_id}"},
                    ("_jar_source",),
                ),
                *specialized,
                _call(MATERIAL_OWNER, forwarded, tuple(sorted(exports))),
            ],
            original["effects"],
        )
    ]
    return _replace_tools(text, {"resolve_srd_spell_materials": wrapper, MATERIAL_OWNER: delegated})
