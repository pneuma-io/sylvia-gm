"""Preserve admitted component facts when the native material owner consumes quantity."""

from copy import deepcopy

if __package__:
    from .srd_timed_actor_defenses import _load_runtime, _set
    from .srd_timed_senses import _replace_tools
else:
    from srd_timed_actor_defenses import _load_runtime, _set
    from srd_timed_senses import _replace_tools


MATERIAL_OWNERS = ("resolve_srd_spell_materials", "resolve_srd_magic_jar_body_materials")
ORIGINAL_WRITE = {
    "list_push": {
        "list": "_updated_materials",
        "value": {
            "id": "_supply.id",
            "inventory_item": "_supply.inventory_item",
            "kind": "_supply.kind",
            "components": "_supply.components",
            "quantity": "_remaining_units",
            "value_cp": "_supply.value_cp",
        },
    }
}
PRESERVING_WRITE = {
    "list_push": {
        "list": "_updated_materials",
        "value": "_material_remaining_supply",
    }
}
PRESERVING_PATCH = _set("_material_remaining_patch", {"quantity": "_remaining_units"})
PRESERVING_VALUE = _set(
    "_material_remaining_supply", "merge(_supply, _material_remaining_patch)"
)


def _writes(value):
    """Find the genuine saved-stock writer through existing material wrappers and loops."""
    if isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, dict) and item.get("list_push", {}).get("list") == (
                "_updated_materials"
            ):
                yield value, index
            yield from _writes(item)
    elif isinstance(value, dict):
        for child in value.values():
            yield from _writes(child)


def bind_material_stock(text):
    """Bind after material hooks, before Magic Jar captures physical-stock consumption."""
    tools = _load_runtime(text)["tools"]
    if MATERIAL_OWNERS[0] not in tools:
        raise ValueError("Material stock preservation requires the native material owner.")
    updated = {}
    for name in MATERIAL_OWNERS:
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        writes = list(_writes(tool["effects"]))
        if len(writes) != 1:
            raise ValueError(f"{name}: expected one genuine material quantity write.")
        effects, index = writes[0]
        if effects[index] == ORIGINAL_WRITE:
            effects[index : index + 1] = deepcopy(
                [PRESERVING_PATCH, PRESERVING_VALUE, PRESERVING_WRITE]
            )
        elif not (
            effects[index] == PRESERVING_WRITE
            and index > 1
            and effects[index - 1] == PRESERVING_VALUE
            and effects[index - 2] == PRESERVING_PATCH
        ):
            raise ValueError(f"{name}: expected the genuine material quantity write.")
        updated[name] = tool
    return _replace_tools(text, updated)
