"""Admit Plane Shift's established fork attunement through ordinary material stock facts."""

import hashlib
import json
from copy import deepcopy

from sylvia_gm.domain.package_library import participant_schema_sha256

if __package__:
    from .srd_timed_actor_defenses import _load_runtime
    from .srd_timed_senses import _replace_tools
else:
    from srd_timed_actor_defenses import _load_runtime
    from srd_timed_senses import _replace_tools


STOCK_FIELDS = {"id", "inventory_item", "kind", "components", "quantity", "value_cp"}
ATTUNED_PLANE = {
    "type": "string",
    "minLength": 1,
}


def _stock(schema):
    """Add one optional fact, preserving original stock validation and quantity meanings."""
    row = schema["items"]
    properties = row["properties"]
    if (
        row.get("type") != "object"
        or row.get("additionalProperties") is not False
        or set(row.get("required", [])) != STOCK_FIELDS
        or set(properties) - {"attuned_plane"} != STOCK_FIELDS
    ):
        raise ValueError("Plane Shift attunement requires the established material stock row.")
    if "attuned_plane" in properties and properties["attuned_plane"] != ATTUNED_PLANE:
        raise ValueError("Plane Shift attunement cannot replace another stock fact declaration.")
    properties["attuned_plane"] = deepcopy(ATTUNED_PLANE)


def _updates(schema):
    return schema["items"]["properties"]["fields"]["properties"]["spell_materials"]


MATERIAL_BINDING_OPERATIONS = [
    {
        "tool": "resolve_srd_spell_materials",
        "op": "insert",
        "path": ["effects"],
        "index": 0,
        "value": {"set": {"field": "_plane_shift_qualified_rods", "value": []}},
        "before_sha256": "7c18fdbc9d9ce7123a1912cbd25c427b70ca3a94bbc79d1f58b125af94591e22",
    },
    {
        "tool": "resolve_srd_spell_materials",
        "op": "insert",
        "path": [
            "effects",
            1,
            "on_false",
            0,
            "on_false",
            3,
            "on_true",
            10,
            "for_each",
            "do",
            6,
            "for_each",
            "do",
            3,
            "on_true",
            0,
            "on_true",
        ],
        "index": 2,
        "value": {
            "list_push": {"list": "_plane_shift_qualified_rods", "value": "_supply"},
            "condition": (
                "spell == 'plane_shift' and _part.id == 'plane_shift.1' and "
                "_part.id in _supply.components and "
                "_supply.value_cp >= _part.minimum_value_cp and "
                "_supply.quantity >= _required_units"
            ),
        },
        "before_sha256": "ffe9b0e9a280c8e2d69ef93f63448050554ab2b1f99fb3fb60c5d58c06b64f62",
    },
    {
        "tool": "resolve_srd_spell_materials",
        "op": "add",
        "path": ["effects", 1, "on_true", 2, "export_context", "_plane_shift_qualified_rods"],
        "value": "_plane_shift_qualified_rods",
    },
    {
        "tool": "resolve_srd_magic_jar_body_materials",
        "op": "insert",
        "path": ["effects"],
        "index": 0,
        "value": {"set": {"field": "_plane_shift_qualified_rods", "value": []}},
        "before_sha256": "9782acf689ce9c7eff670f7f9538d9f96e3f1471dce9b32b1e0caf44dd21100c",
    },
    {
        "tool": "resolve_srd_magic_jar_body_materials",
        "op": "insert",
        "path": [
            "effects",
            1,
            "on_false",
            3,
            "on_true",
            10,
            "for_each",
            "do",
            6,
            "for_each",
            "do",
            3,
            "on_true",
            0,
            "on_true",
        ],
        "index": 2,
        "value": {
            "list_push": {"list": "_plane_shift_qualified_rods", "value": "_supply"},
            "condition": (
                "spell == 'plane_shift' and _part.id == 'plane_shift.1' and "
                "_part.id in _supply.components and "
                "_supply.value_cp >= _part.minimum_value_cp and "
                "_supply.quantity >= _required_units"
            ),
        },
        "before_sha256": "ffe9b0e9a280c8e2d69ef93f63448050554ab2b1f99fb3fb60c5d58c06b64f62",
    },
    {
        "tool": "cast_spell",
        "op": "add",
        "path": ["effects", 56, "export_context", "_plane_shift_qualified_rods"],
        "value": "_plane_shift_qualified_rods",
    },
    {
        "tool": "cast_spell",
        "op": "insert",
        "path": ["effects", 164, "on_true", 0, "set", "value"],
        "index": 36,
        "value": {
            "key": "'_plane_shift_qualified_rods'",
            "missing": "_plane_shift_qualified_rods == null",
            "value": "_plane_shift_qualified_rods",
        },
        "before_sha256": "6a670e23afc450ff81bd8bdaebc6f0acaa7e5a4232b7a2fc86087c607797866a",
    },
    {
        "tool": "cast_spell",
        "op": "insert",
        "path": ["effects", 164, "on_false", 0, "set", "value"],
        "index": 36,
        "value": {
            "key": "'_plane_shift_qualified_rods'",
            "missing": "_plane_shift_qualified_rods == null",
            "value": "_plane_shift_qualified_rods",
        },
        "before_sha256": "6a670e23afc450ff81bd8bdaebc6f0acaa7e5a4232b7a2fc86087c607797866a",
    },
    {
        "tool": "finish_srd_retained_cast_tail",
        "op": "add",
        "path": ["effects", 26, "on_true", 3, "params", "_plane_shift_qualified_rods"],
        "value": "{_plane_shift_qualified_rods}",
    },
    {
        "tool": "cast_spell_travel_spell",
        "op": "insert",
        "path": ["parameters"],
        "index": 61,
        "value": {
            "name": "_plane_shift_qualified_rods",
            "type": "array",
            "items": {"type": "object"},
            "required": False,
            "default": [],
            "max_items": 8,
            "model_hidden": True,
        },
        "before_sha256": "b52bbf82cced46b92f43625558ed6ecff8b6811b730a8c417dd8734c8a8c683c",
    },
    {
        "tool": "cast_spell_travel_spell",
        "op": "replace",
        "path": ["effects", 32, "on_true", 12, "set", "value"],
        "before": "caster_state.spell_materials",
        "value": "_plane_shift_qualified_rods",
    },
]
MATERIAL_BINDING_TOOLS = {operation["tool"] for operation in MATERIAL_BINDING_OPERATIONS}


def _canonical_hash(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _private_material_binding(tools):
    """Carry the actual component-qualified row from its existing material owner.

    The saved Jar-source reader and its resolved host delegate stay authoritative.
    Never replace logical caster_state or reinterpret human-readable material rows.
    Exact Source65 fragment guards fail closed at any changed composition boundary.
    """
    updated = deepcopy(tools)
    if (
        updated["resolve_srd_spell_materials"]["effects"][0]
        == MATERIAL_BINDING_OPERATIONS[0]["value"]
    ):
        for operation in reversed(MATERIAL_BINDING_OPERATIONS):
            parent = updated[operation["tool"]]
            for part in operation["path"][:-1]:
                parent = parent[part]
            key = operation["path"][-1]
            if operation["op"] == "insert":
                rows = parent[key]
                if rows[operation["index"]] != operation["value"]:
                    raise ValueError("Plane Shift material binding inserted node drift.")
                rows.pop(operation["index"])
                if _canonical_hash(rows) != operation["before_sha256"]:
                    raise ValueError("Plane Shift material binding source list drift.")
            elif operation["op"] == "add":
                if parent.pop(key) != operation["value"]:
                    raise ValueError("Plane Shift material binding private export drift.")
            else:
                if parent[key] != operation["value"]:
                    raise ValueError("Plane Shift material binding source expression drift.")
                parent[key] = operation["before"]
    for operation in MATERIAL_BINDING_OPERATIONS:
        parent = updated[operation["tool"]]
        for part in operation["path"][:-1]:
            parent = parent[part]
        key = operation["path"][-1]
        value = deepcopy(operation["value"])
        if operation["op"] == "insert":
            rows = parent[key]
            index = operation["index"]
            if _canonical_hash(rows) == operation["before_sha256"]:
                rows.insert(index, value)
            elif not (
                index < len(rows)
                and rows[index] == value
                and _canonical_hash(rows[:index] + rows[index + 1 :]) == operation["before_sha256"]
            ):
                raise ValueError("Plane Shift material binding source list drift.")
        elif operation["op"] == "add":
            if key in parent and parent[key] != value:
                raise ValueError("Plane Shift material binding private export drift.")
            parent[key] = value
        else:
            if parent[key] not in (operation["before"], value):
                raise ValueError("Plane Shift material binding source expression drift.")
            parent[key] = value
    return {name: updated[name] for name in MATERIAL_BINDING_TOOLS}


def assert_material_binding_preserved(before, after):
    """Exact mutation allowlist; every other byte-resolved tool node must survive."""
    expected = _private_material_binding(before)
    for name in MATERIAL_BINDING_TOOLS:
        if after[name] != expected[name]:
            raise ValueError("Plane Shift material binding changed an unowned node: " + name)


def bind_attunement_formals(text):
    """Extend inventory formals and the exact private Source65 material-row binding."""
    tools = _load_runtime(text)["tools"]
    updated = {}
    for name, parameter_name in (
        ("set_srd_carried_inventory", "spell_materials"),
        ("update_srd_carried_inventory", "updates"),
    ):
        tool = deepcopy(tools[name])
        parameter = next(p for p in tool["parameters"] if p["name"] == parameter_name)
        _stock(parameter if parameter_name == "spell_materials" else _updates(parameter))
        updated[name] = tool
    updated.update(_private_material_binding(tools))
    changed = {name: tool for name, tool in updated.items() if tool != tools[name]}
    return _replace_tools(text, changed)


def bind_attunement_record(record):
    """Keep package inventory declarations and the hashed character stock contract aligned."""
    updated = deepcopy(record)
    artifact = next(a for a in updated["participant_artifacts"] if a["kind"] == "player_character")
    _stock(artifact["entity_state_schema"]["properties"]["spell_materials"])
    artifact["schema_sha256"] = participant_schema_sha256(
        artifact["schema"],
        entity_state_schema=artifact["entity_state_schema"],
        initial_state_calculations=artifact.get("initial_state_calculations", []),
        initial_state_validation=artifact.get("initial_state_validation"),
    )
    for declaration in updated["tools"]:
        name = declaration["name"]
        if name == "set_srd_carried_inventory":
            _stock(declaration["input_schema"]["properties"]["spell_materials"])
        elif name == "update_srd_carried_inventory":
            _stock(_updates(declaration["input_schema"]["properties"]["updates"]))
    return updated
