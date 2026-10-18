"""Keep admitted preparatory mechanics reachable during a declared pending action."""


def bind_pending_player_preparations(runtime):
    changed = []
    for name in ("move_srd_scene_creature",):
        tool = runtime["tools"][name]
        metadata = tool.setdefault("metadata", {})
        original = metadata.get("model_behavior", "optional")
        if original != "optional":
            raise ValueError("Preparatory model behavior changed: " + name)
        metadata["model_behavior"] = "supports_player_intent"
        changed.append(name)
    return changed
