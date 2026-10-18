"""Require a scene only when a source cleanup has actual queued spatial work."""

from .srd_timed_actor_defenses import _when

CLEANUPS = {
    "handoff_srd_rope_trick_drops": "lasting_state.has_rope_trick_pending_drops",
}


def bind_cleanup_scene_scope(runtime):
    """Keep queued work unchanged; avoid querying a map for an empty cleanup."""
    changed = []
    for name, field in CLEANUPS.items():
        owner = runtime["tools"][name]
        effects = owner["effects"]
        if (
            len(effects) < 4
            or effects[0].get("require", {}).get("condition")
            != "lasting_state.spatial_scene != null"
            or effects[1].get("require", {}).get("condition")
            != "lasting_state.spatial_scene.map.grid.unit == 'ft'"
            or effects[2].get("spatial_query", {}).get("operation") != "snapshot"
        ):
            raise ValueError("The cleanup scene snapshot changed: " + name)
        queued = [
            row
            for row in effects[3:]
            if field + " == true" in row.get("condition", "") and row.get("on_true")
        ]
        if len(queued) != 1:
            raise ValueError("The cleanup's queued-work guard changed: " + name)
        owner["effects"] = [_when(queued[0]["condition"], effects[:3]), *effects[3:]]
        changed.append(name)
    return changed
