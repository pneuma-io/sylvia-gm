"""Observe real sleeping-condition removals at the completed public dismissal boundary."""

from copy import deepcopy

if __package__:
    from .srd_control_behaviors import _call, _each, _walk, _when
    from .srd_timed_actor_defenses import _load_runtime
    from .srd_timed_senses import _replace_tools
else:
    from srd_control_behaviors import _call, _each, _walk, _when
    from srd_timed_actor_defenses import _load_runtime
    from srd_timed_senses import _replace_tools


def bind_runtime_consumers(text):
    """Settle earned nightmares only after this action actually removes condition sources."""
    tools = _load_runtime(text)["tools"]
    owners = {"dismiss_srd_spell", "observe_srd_dream_sleep", "drain_srd_dream_wakes"}
    if not owners <= tools.keys():
        return text
    sleep = deepcopy(tools["read_srd_dream_sleep"])
    old = "_dream_sleep_source.stage == 'sleeping'"
    qualified = (
        "(_dream_sleep_source.control_profile == 'eyebite_sleep' or "
        "_dream_sleep_source.stage == 'sleeping') and "
        "_dream_sleep_source.suppressed != true"
    )
    matches = 0
    for effect in _walk(sleep["effects"]):
        condition = effect.get("condition", "")
        if qualified in condition:
            matches += 1
        elif old in condition:
            effect["condition"] = condition.replace(old, qualified)
            matches += 1
    if matches != 1:
        raise ValueError("Dream's exact installed sleeping-source predicate has changed.")
    dismiss = deepcopy(tools["dismiss_srd_spell"])
    marker = (
        "lasting_state.has_dream_spells == true and len(_dismiss_condition_targets) > 0"
    )
    dismiss["effects"] = [e for e in dismiss["effects"] if e.get("condition") != marker]
    dismiss["effects"].append(
        _when(
            marker,
            [
                _each(
                    "_dismiss_condition_targets",
                    "_dismiss_dream_target",
                    [
                        _call(
                            "observe_srd_dream_sleep",
                            {"target_state": "{_dismiss_dream_target}"},
                        )
                    ],
                ),
                _call("drain_srd_dream_wakes", {}),
            ],
        )
    )
    return _replace_tools(text, {"read_srd_dream_sleep": sleep, "dismiss_srd_spell": dismiss})
