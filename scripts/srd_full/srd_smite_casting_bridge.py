"""Reserve a real paid Smite child above its authenticated, settled weapon hit."""

import re
from copy import deepcopy

if __package__:
    from .srd_spell_damage_riders import AFTER_HIT_SPELLS
    from .srd_timed_actor_defenses import _call, _load_runtime, _parameter, _require, _set
    from .srd_timed_senses import _replace_tools
else:
    from srd_spell_damage_riders import AFTER_HIT_SPELLS
    from srd_timed_actor_defenses import _call, _load_runtime, _parameter, _require, _set
    from srd_timed_senses import _replace_tools

BEGIN = "begin_srd_after_hit_smite"
PUBLIC_TOOLS = {BEGIN}
MARKER = "after_hit_smite_bridge_v1"


def bridge_tool(original):
    """Change only the ordinary-stack fence; allocation and all paid casting owners remain real."""
    tool = deepcopy(original)
    tool["description"] = "Reserve an after-hit spell Bonus Action on this saved final weapon hit."
    names = {p["name"] for p in tool["parameters"]}
    if "rider_pending_action_id" not in names:
        tool["parameters"].append(_parameter("rider_pending_action_id"))
    replaced = 0
    for effect in tool["effects"]:
        body = effect.get("require", {})
        clause = body.get("condition", "")
        if "len(state.pending_srd_action_stack) == 0" in clause or (
            "len(lasting_state.pending_srd_action_stack) == 0" in clause
        ):
            if "_spell_definition.casting_time == 'reaction'" not in clause:
                raise ValueError("Smite bridge found an unrelated pending-stack fence.")
            body["condition"] = "(" + clause + ") or _smite_parent_authorized == true"
            replaced += 1
    if replaced != 2:
        raise ValueError(
            "Smite bridge requires the final casting owner's two ordinary-stack fences."
        )
    preflight = [
        _require(
            " or ".join("spell == '" + spell + "'" for spell in AFTER_HIT_SPELLS),
            "Select a supported paid after-hit rider for this final weapon hit.",
        ),
        _call(
            "prepare_srd_smite_hit",
            {
                "caster_state": "{_caster_state_id}",
                "pending_action_id": "{rider_pending_action_id}",
                "spell": "{spell}",
            },
            ("_smite_target_id",),
        ),
        _require(
            "state.pending_srd_action_stack != null and "
            "len(state.pending_srd_action_stack) > 0 and "
            "state.pending_srd_action_stack[-1] == rider_pending_action_id and "
            "state.pending_srd_actions[rider_pending_action_id].rider_marks_captured == true",
            "Use the innermost accepted hit with its already-earned rider dice captured.",
        ),
        _require(
            "state.pending_srd_actions[rider_pending_action_id].smite_pending_cast_id == null",
            "This hit already reserved its one Smite casting action.",
        ),
        _set("_smite_parent_authorized", True),
    ]
    tool["effects"] = (
        preflight
        + tool["effects"]
        + [
            _set(
                "state.pending_srd_actions.{_pending_action_id}.smite_parent_hit_id",
                "rider_pending_action_id",
            ),
            _set(
                "state.pending_srd_actions.{_pending_action_id}.parameters.rider_pending_action_id",
                "rider_pending_action_id",
            ),
            _set(
                "state.pending_srd_actions.{rider_pending_action_id}.smite_pending_cast_id",
                "_pending_action_id",
            ),
        ]
    )

    def bind(value):
        if isinstance(value, str):
            return re.sub(r"\bstate\.", "lasting_state.", value)
        if isinstance(value, list):
            return [bind(v) for v in value]
        if isinstance(value, dict):
            return {k: bind(v) for k, v in value.items()}
        return value

    tool["effects"] = bind(tool["effects"])
    tool.setdefault("metadata", {})[MARKER] = True
    return tool


def bind_runtime(text):
    """Capture the final real begin owner after Reaction generation; never change that owner."""
    tools = _load_runtime(text)["tools"]
    changed = {BEGIN: bridge_tool(tools["begin_srd_spell_action"])}
    return _replace_tools(text, changed)
