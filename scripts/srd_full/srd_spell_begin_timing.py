"""Settle the actual Slow decision before exposing an accepted casting window."""

from copy import deepcopy

from scripts.srd_full.srd_timed_actor_defenses import _call, _load_runtime, _set, _when
from scripts.srd_full.srd_timed_senses import _replace_tools


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def bind_runtime_consumers(text):
    """Reuse final reaction completion and Light owners without changing their cost plans."""
    runtime = _load_runtime(text)
    tools = runtime["tools"]
    begin = deepcopy(tools["begin_srd_spell_action"])
    if any(node.get("call") == "resolve_srd_slow_cast_failure" for node in _walk(begin)):
        return text
    if "end_srd_invisibility_spell_source" not in tools:
        raise ValueError("Accepted casting timing requires the composed Light source callback.")
    index = next(
        i
        for i, effect in enumerate(begin["effects"])
        if any(node.get("call") == "continue_srd_spell_targeting" for node in _walk(effect))
    )
    prefix, tail = begin["effects"][:index], begin["effects"][index:]
    if not any(
        node.get("set", {}).get("field", "").endswith(".{_pending_action_id}.components")
        for node in _walk(prefix)
    ):
        raise ValueError("The saved actual casting components must precede the Slow decision.")
    begin["effects"] = [
        *prefix,
        _set("_accepted_actual_somatic", "'somatic' in _pending_actual_components"),
        _call(
            "resolve_srd_slow_cast_failure",
            {
                "caster_state": "{_caster_state_id}",
                "spell": "{spell}",
                "has_somatic_component": "{_accepted_actual_somatic}",
                "pending_action_id": "{_pending_action_id}",
                "lasting_state": "{_lasting_state_id}",
            },
            ["_slow_cast_failed", "_slow_cast_failure_roll"],
        ),
        # The Slow owner stores actual rolls. A settled null-roll decision prevents
        # a subsequently acquired Slow source from retroactively changing this cast.
        _when(
            "lasting_state.pending_srd_actions[_pending_action_id].slow_cast == null",
            [
                _set(
                    "lasting_state.pending_srd_actions.{_pending_action_id}.slow_cast",
                    {
                        "roll": None,
                        "failed": "_slow_cast_failed",
                        "spell": "spell",
                        "caster_state_id": "_caster_state_id",
                    },
                ),
            ],
        ),
        _set("_pending_card_title", "_casting_requirements.name"),
        _set(
            "_pending_card_summary",
            [{"label": "'Casting'", "value": "'Accepted; spell completion pending'"}],
        ),
        _set("_pending_card_details", []),
        _call("end_srd_invisibility_spell_source", {"caster_state": "{_caster_state_id}"}),
        {
            "condition": "_slow_cast_failed == true",
            "on_true": [
                _call(
                    "complete_srd_pending_action",
                    {
                        "pending_action_id": "{_pending_action_id}",
                        "lasting_state": "{_lasting_state_id}",
                    },
                    [
                        "_pending_card_title",
                        "_pending_card_summary",
                        "_pending_card_details",
                        "_spell_card_summary",
                        "_spell_detail_rows",
                        "_spell_summary_rows",
                    ],
                ),
            ],
            "on_false": [
                *tail,
            ],
        },
    ]
    begin["metadata"]["result_card"] = deepcopy(
        tools["complete_srd_pending_action"]["metadata"]["result_card"]
    )
    return _replace_tools(text, {"begin_srd_spell_action": begin})
