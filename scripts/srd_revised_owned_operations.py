"""Restore only reviewed retained-profile consumers hidden by the Lite wrapper.

No spell profile, declaration, writer permission or original restriction is
changed. Capture the final producer metadata before the ordinary pending
policy, then expose after broader reviewed helpers and before denied-tail trim.
"""

from copy import deepcopy
from types import MappingProxyType


OWNED_PROFILE_OPERATIONS = MappingProxyType({
    "misty_step": (),
    "darkvision": ("dismiss_srd_senses_spell",),
    "longstrider": (),
    "light": (),  # The existing Light helper remains its integration owner.
    "foresight": (),
    "alter_self": (
        "change_srd_transformation",
        "read_srd_transformed_appearance",
        "set_srd_respiration",
    ),
    "arcane_eye": (
        "operate_srd_information_proxy",
        "query_srd_information_spell",
        "observe_srd_information_proxy",
        "dismiss_srd_information_spell",
    ),
    "commune": ("ask_srd_divination_question",),
    "contact_other_plane": ("ask_srd_divination_question",),
    "etherealness": ("dismiss_srd_etherealness",),
    "fly": (),
    "legend_lore": (),
    "speak_with_animals": (
        "communicate_srd_spell",
        "query_srd_information_spell",  # Retain its genuine rejection control.
        "dismiss_srd_information_spell",
    ),
    "tree_stride": ("traverse_srd_tree_stride",),
    "dispel_magic": (),
})

PENDING_PREFIX = {
    "require": {
        "condition": "false",
        "error": "This spell operation is pending or excluded in the SRD Lite foundation.",
    }
}


def required_owned_operations(selected):
    """Other owners' selected names do not contribute any operation here."""
    selected = frozenset(selected)
    return tuple(sorted({
        operation
        for spell, operations in OWNED_PROFILE_OPERATIONS.items()
        if spell in selected
        for operation in operations
    }))


def capture_owned_operation_visibility(runtime, selected):
    """Capture existing visibility after producer binders, before pending policy."""
    captured = {}
    for name in required_owned_operations(selected):
        tool = runtime["tools"][name]
        effects = tool.get("effects", [])
        if effects and effects[0] == PENDING_PREFIX:
            raise ValueError("Capture owned operation visibility before pending policy: " + name)
        metadata = tool.get("metadata", {})
        if metadata.get("category") == "internal":
            raise ValueError("An owned consumer must retain its public producer: " + name)
        captured[name] = {
            "present": "visible_to_player" in metadata,
            "value": deepcopy(metadata.get("visible_to_player")),
        }
    return captured


def expose_owned_spell_operations(runtime, denied, selected, captured):
    """Remove only exact pending prefixes, restoring rather than widening visibility.

Main's boundary remains responsible for scalar spell selectors. Consumers
without a spell parameter retain their real saved-source, participant,
geometry, eligibility, pending-window and budget owners verbatim.
"""
    names = [name for name in required_owned_operations(selected) if name in denied]
    # Validate the complete delta first; a drifted wrapper cannot partially
    # mutate another consumer or silently discard its original effects.
    for name in names:
        tool = runtime["tools"][name]
        effects = tool.get("effects")
        if not isinstance(effects, list) or not effects or effects[0] != PENDING_PREFIX:
            raise ValueError("Owned pending-operation prefix drift: " + name)
        if denied.count(name) != 1:
            raise ValueError("Owned pending-operation accounting is ambiguous: " + name)
        if name not in captured or set(captured[name]) != {"present", "value"}:
            raise ValueError("Missing owned operation visibility capture: " + name)
        if not isinstance(captured[name]["present"], bool):
            raise ValueError("Invalid owned operation visibility capture: " + name)
        if tool.get("metadata", {}).get("visible_to_player") is not False:
            raise ValueError("Owned pending-operation visibility drift: " + name)
    for name in names:
        tool = runtime["tools"][name]
        del tool["effects"][0]
        if captured[name]["present"]:
            tool["metadata"]["visible_to_player"] = deepcopy(captured[name]["value"])
        else:
            tool["metadata"].pop("visible_to_player", None)
        denied.remove(name)
    return tuple(names)
