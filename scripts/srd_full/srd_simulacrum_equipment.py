"""Derive the equipment-free PC copy with the existing ordinary equipment calculation."""

from copy import deepcopy

if __package__:
    from .srd_simulacrum_characters import CHARACTER_CHOICE, _substitute, _walk
    from .srd_timed_actor_defenses import _call, _entity, _load_runtime, _require, _set
    from .srd_timed_senses import _replace_tools
else:
    from srd_simulacrum_characters import CHARACTER_CHOICE, _substitute, _walk
    from srd_timed_actor_defenses import _call, _entity, _load_runtime, _require, _set
    from srd_timed_senses import _replace_tools


HELPER = "project_srd_simulacrum_naked_character"
OLD_GEAR_GUARD = (
    "original_state.max_hp >= 2 and len(original_state.inventory) == 0 and "
    "original_state.worn_armor == null and original_state.worn_shield == null"
)


def _projection(tools):
    """Reuse the pure equipment owner on a local empty-gear view, never the original body."""
    native = deepcopy(tools["resolve_srd_equipment"])
    effects = native["effects"]
    if len(effects) == 1 and effects[0].get("condition") == (
        "actor_state.magic_jar_role == 'possessed'"
    ):
        effects = effects[0]["on_false"]
    # A physical body/identity transfer is never a source for the naked-copy calculation.
    assert not any("call" in node for node in _walk(effects))
    assert all(
        node["set"]["field"].startswith("_") for node in _walk(effects) if "set" in node
    )
    assert any(node.get("set", {}).get("field") == "_equipment_ac" for node in effects)
    assert any(node.get("set", {}).get("field") == "_equipment_speed" for node in effects)
    native["description"] = (
        "Calculate this actual PC original's equipment-free defenses and movement using "
        "the native armor/class projection without changing carried or worn stock."
    )
    native["parameters"] = [_entity("original_state"), deepcopy(CHARACTER_CHOICE)]
    native["pre_conditions"] = []
    native["effects"] = [
        _require(
            "target_character != null and target_character.validation_status == 'validated' "
            "and target_character.participant_id == _original_state_id and "
            "original_state.form_statistics_replaced != true and "
            "original_state.magic_jar_role == null",
            "Naked-copy projection requires the same actual ordinary validated PC original.",
        ),
        _set("_sim_empty_gear", {"inventory": [], "worn_armor": None, "worn_shield": None}),
        _set("_sim_naked_state", "merge(original_state, _sim_empty_gear)"),
        *_substitute(
            effects,
            [("actor.data", "target_character.data"), ("actor_state", "_sim_naked_state")],
        ),
        _set(
            "_sim_naked_fields",
            {
                "inventory": [],
                "worn_armor": None,
                "worn_shield": None,
                "armor_class": "_equipment_ac",
                "speed": "_equipment_speed",
                "climb_speed": "_equipment_climb_speed",
                "swim_speed": "_equipment_swim_speed",
                "armor_speed_penalty": "_equipment_speed_penalty",
                "armor_untrained": "_equipment_untrained_armor",
                "armor_stealth_disadvantage": "_equipment_stealth_disadvantage",
            },
        ),
    ]
    return native


def bind_sources(text):
    """After Characters1, allow carried/worn PC gear and project the copy's naked statistics."""
    tools = _load_runtime(text)["tools"]
    reader = deepcopy(tools["read_srd_simulacrum_original_character"])
    if not any(effect.get("call") == HELPER for effect in reader["effects"]):
        guards = [
            effect["require"]
            for effect in reader["effects"]
            if effect.get("require", {}).get("condition") == OLD_GEAR_GUARD
        ]
        assert len(guards) == 1, "The actual Characters1 equipment guard has changed."
        guards[0].update(
            condition="original_state.max_hp >= 2",
            error="The copy needs a positive half-maximum HP body.",
        )
        reader["effects"] += [
            _call(
                HELPER,
                {
                    "original_state": "{_original_state_id}",
                    "target_character": "{_target_character_id}",
                },
                ["_sim_naked_fields"],
            ),
            _set("_sim_original_profile", "merge(_sim_original_profile, _sim_naked_fields)"),
        ]
    return _replace_tools(
        text,
        {HELPER: _projection(tools), "read_srd_simulacrum_original_character": reader},
    )
