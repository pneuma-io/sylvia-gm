"""Owned sealed-Source65 adapter; execution requires a separate parent lease."""

import hashlib
from copy import deepcopy

from sylvia_gm.runtime._parameter_declarations import resolve_parameter_declarations

if __package__:
    from .srd_parameter_declarations import compact_runtime
    from .srd_simulacrum_actions import PUBLIC_TOOLS, bind_runtime_consumers
    from .srd_timed_actor_defenses import _load_runtime
    from .srd_timed_senses import _replace_tools
else:
    from srd_parameter_declarations import compact_runtime
    from srd_simulacrum_actions import PUBLIC_TOOLS, bind_runtime_consumers
    from srd_timed_actor_defenses import _load_runtime
    from srd_timed_senses import _replace_tools

PARENT_RUNTIME_SHA256 = "3540dec3751e7f150584e86bf4329f1110d84be21a53500ec88215d3b3ef8c47"


def compose_sealed_source65_actions(text):
    """Add only copied actions and their pending fence to the accepted final native owners."""
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != PARENT_RUNTIME_SHA256:
        raise ValueError("Refresh the Source65 parent and dependency boundary before composition")
    before = _load_runtime(text)
    emitted = bind_runtime_consumers(text)
    resolved = _load_runtime(emitted)
    changed = {}
    for name in sorted(PUBLIC_TOOLS):
        tool = deepcopy(resolved["tools"][name])
        tool["effects"].insert(0, {"call": "require_srd_pending_window_closed"})
        changed[name] = tool
    emitted = _replace_tools(emitted, changed)
    expected = _load_runtime(emitted)
    # Resolve the existing pool before its compatible compaction; use the native
    # compactor's exact dictionary equality, retaining hidden/default descriptors.
    compacted = compact_runtime(emitted)
    after = resolve_parameter_declarations(_load_runtime(compacted))
    if after != expected:
        raise ValueError("Shared parameters changed the exact resolved proposal")
    if set(after["tools"]) != set(before["tools"]) | PUBLIC_TOOLS:
        raise ValueError("The finite Actions1 roster changed")
    for name, original in before["tools"].items():
        if after["tools"][name] != original:
            raise ValueError(f"Unowned native tool changed: {name}")
    for key, original in before.items():
        if key != "tools" and after[key] != original:
            raise ValueError(f"Unowned runtime section changed: {key}")
    return compacted
