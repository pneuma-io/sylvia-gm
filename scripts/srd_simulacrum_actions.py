"""Bind represented ordinary class actions to the genuine Construct's own resources."""

from copy import deepcopy

if __package__:
    from .srd_simulacrum_characters import _substitute
    from .srd_timed_actor_defenses import _call, _entity, _load_runtime, _require
    from .srd_timed_senses import _replace_tools
else:
    from srd_simulacrum_characters import _substitute
    from srd_timed_actor_defenses import _call, _entity, _load_runtime, _require
    from srd_timed_senses import _replace_tools


PUBLIC_TOOLS = {"use_srd_simulacrum_action_surge", "take_srd_simulacrum_dodge_action"}


def _dodge(tools):
    """Use ordinary Dodge's same actual body checks and action spend without a PC alias."""
    native = deepcopy(tools["take_dodge_action"])
    original_guards = native.pop("pre_conditions")
    native["parameters"] = [_entity("actor_state")] + [
        p for p in native["parameters"] if p["name"] not in {"actor", "actor_state"}
    ]
    guards = []
    validation = 0
    for guard in original_guards:
        condition = guard["condition"]
        if condition == "actor.validation_status == 'validated'":
            validation += 1
            continue
        condition = condition.replace("actor.participant_id", "_actor_state_id")
        guards.append(_require(condition, guard["error"]))
    assert validation == 1, "The native Dodge qualification has changed."
    native = _substitute(
        native,
        [
            ("_actor_id", "_actor_state_id"),
            ("context.actor.data.name", "context.actor_state.name"),
        ],
    )
    native["description"] = (
        "Take the native Dodge action with this genuine PC-original Construct's own body "
        "and actual action budget."
    )
    native["effects"] = [
        _call(
            "read_srd_simulacrum_character_capabilities",
            {"caster_state": "{_actor_state_id}"},
        ),
        *guards,
        *native["effects"],
    ]
    return native


def _capture_native_tools(text):
    """Capture resolved descriptors and genuine owners before adding either copy action."""
    tools = _load_runtime(text)["tools"]
    required = {
        "begin_srd_simulacrum_turn",
        "end_srd_simulacrum_turn",
        "install_srd_simulacrum",
        "project_srd_simulacrum_naked_character",
        "read_srd_simulacrum_character_capabilities",
        "read_srd_simulacrum_original_character",
        "read_srd_simulacrum_source",
        "take_dodge_action",
        "use_srd_action_surge",
    }
    assert required <= tools.keys(), "The actual Simulacrum/native owners are missing."
    assert not PUBLIC_TOOLS.intersection(tools), "Actions1 is already bound."
    for name in ("take_dodge_action", "use_srd_action_surge"):
        assert all(isinstance(p, dict) for p in tools[name]["parameters"]), (
            "Resolve existing shared parameter descriptors before copying native tools."
        )
    return tools


def bind_runtime_consumers(text):
    """Reuse Action Surge's exact native resource/action kernel with detached class facts."""
    tools = _capture_native_tools(text)
    native = deepcopy(tools["use_srd_action_surge"])
    original_guards = native.pop("pre_conditions")
    native["parameters"] = [_entity("actor_state")] + [
        p for p in native["parameters"] if p["name"] not in {"actor", "actor_state"}
    ]
    guards = []
    validation = 0
    fighter = 0
    for guard in original_guards:
        condition = guard["condition"]
        if condition == "actor.validation_status == 'validated'":
            validation += 1
            continue
        if condition == "actor_state.fighter_level != null and actor_state.fighter_level >= 2":
            fighter += 1
            condition = (
                "len(_sim_pc_data.classes) == 1 and _sim_pc_data.classes[0].id == 'fighter' "
                "and _sim_pc_data.classes[0].level >= 2"
            )
        guards.append(_require(condition.replace("_actor_id", "_actor_state_id"), guard["error"]))
    assert validation == 1 and fighter == 1, "The native Action Surge qualification has changed."
    native = _substitute(native, [("context.actor.data.name", "context.actor_state.name")])
    native["description"] = (
        "Use the actual PC-original Construct's copied Fighter grant and remaining Action "
        "Surge charge. The native once-per-turn extra non-Magic action kernel applies; "
        "no original participant's resources or action budget are used."
    )
    native["effects"] = [
        _call(
            "read_srd_simulacrum_character_capabilities",
            {"caster_state": "{_actor_state_id}"},
            ["_sim_pc_data"],
        ),
        *guards,
        *native["effects"],
    ]
    return _replace_tools(
        text,
        {
            "use_srd_simulacrum_action_surge": native,
            "take_srd_simulacrum_dodge_action": _dodge(tools),
        },
    )
