"""Capture final sensory-only sight without repeating casting or source bindings."""

from .srd_conjured_presences import _sight, _subject_sight
from .srd_spell_travel import _normalize
from .srd_timed_actor_defenses import _load_runtime
from .srd_timed_senses import _replace_tools


def emit_late_sight(text):
    """Run after final Light and actual sight-origin consumers, before outer completion."""
    tools = _load_runtime(text)["tools"]
    if "install_srd_conjured_presence" not in tools:
        raise ValueError("Late conjured sight needs the actual emitted family owner.")
    emitted = {
        "resolve_srd_conjured_point_sight": _sight(tools),
        "resolve_srd_conjured_subject_sight": _subject_sight(tools),
    }
    _normalize(emitted)
    return _replace_tools(text, emitted)
