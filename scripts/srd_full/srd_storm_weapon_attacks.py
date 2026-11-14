"""Storm's live strong-wind path rejects genuine ranged weapons before native attack work."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import POSITION_EXPORTS
    from .srd_storm_conditions import SEGMENT
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _require,
        _set,
        _tool,
        _when,
    )
    from .srd_timed_senses import _replace_tools
else:
    from srd_scene_relations import POSITION_EXPORTS
    from srd_storm_conditions import SEGMENT
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _require,
        _set,
        _tool,
        _when,
    )
    from srd_timed_senses import _replace_tools

QUERY = "qualify_srd_storm_ranged_weapon_path"
OWNERS = (
    "resolve_combat_attack",
    "resolve_combat_attack_followup",
    "begin_srd_character_attack",
    "continue_srd_character_attack_targeting",
)


def _path_tool():
    """Use the actual admitted straight anchor path, without a caller visibility verdict."""
    return _tool(
        "Read current Storm wind along the actual saved attacker-to-target scene path.",
        [_entity("actor_state"), _entity("target_state")],
        [
            _set("_storm_ranged_weapon_blocked", False),
            _when(
                "state.has_storm_sources == true",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_actor_state_id}"},
                        POSITION_EXPORTS,
                    ),
                    _set("_storm_weapon_start", "_info_position"),
                    _set("_storm_weapon_actor_scene", "_info_scene_entity"),
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_target_state_id}"},
                        POSITION_EXPORTS,
                    ),
                    _require(
                        "_storm_weapon_actor_scene.metadata.plane != null and "
                        "_info_scene_entity.metadata.plane != null",
                        "Storm weapon qualification needs the actual admitted scene planes.",
                    ),
                    _when(
                        "_storm_weapon_actor_scene.metadata.plane == "
                        "_info_scene_entity.metadata.plane",
                        [
                            _call(
                                SEGMENT,
                                {
                                    "start": "{_storm_weapon_start}",
                                    "end": "{_info_position}",
                                    "plane": "{_info_scene_entity.metadata.plane}",
                                },
                                ("_storm_strong_wind_segment",),
                            ),
                            _set("_storm_ranged_weapon_blocked", "_storm_strong_wind_segment"),
                        ],
                    ),
                ],
            ),
        ],
    )


def _guard():
    """Use native definition/mode validation and the selected actor's actual inventory."""
    return _when(
        "lasting_state.has_storm_sources == true and "
        "(attack_mode == 'ranged' or attack_mode == 'thrown')",
        [
            _require(
                "actor_state.inventory != null and weapon in actor_state.inventory",
                "Storm weapon qualification needs the selected weapon in actual inventory.",
            ),
            _call(
                QUERY,
                {
                    "actor_state": "{_actor_state_id}",
                    "target_state": "{_target_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
                ("_storm_ranged_weapon_blocked",),
            ),
            _require(
                "_storm_ranged_weapon_blocked != true",
                "Ranged weapon attacks are impossible through this current Storm area.",
            ),
        ],
    )


def _bind(text, owners):
    """Preserve every existing effect while adding the same exact authentic path guard."""
    tools = _load_runtime(text)["tools"]
    if SEGMENT not in tools:
        raise ValueError("Storm ranged weapons require the actual same-source path qualifier.")
    changed = {QUERY: _path_tool()}
    for name in owners:
        if name not in tools:
            continue
        original = tools[name]
        parameters = {p["name"] for p in original["parameters"]}
        if not {"actor_state", "target", "attack_mode", "weapon"} <= parameters:
            raise ValueError("Storm weapon binding needs the authentic native weapon signature.")
        if name == "resolve_combat_attack" and any(
            effect.get("call") == "resolve_srd_settled_weapon_attack"
            for effect in original["effects"]
        ):
            raise ValueError(
                "Restore the ordinary weapon owner before Storm/Reaction regeneration."
            )
        if QUERY not in str(original["effects"]):
            owner = deepcopy(original)
            owner["effects"].insert(0, _guard())
            changed[name] = owner
    return _replace_tools(
        text, {name: tool for name, tool in changed.items() if tools.get(name) != tool}
    )


def bind_runtime_consumers(text):
    """Bind ordinary native weapons before Reaction construction and True Strike capture."""
    return _bind(text, OWNERS)


def bind_saved_attack_consumers(text):
    """Rebind only final saved targeting owners, preserving the later True Strike delegate."""
    return _bind(text, OWNERS[2:])
