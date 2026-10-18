"""Keep retained control follow-up actions inside the exact Lite selection.

Restore original SRD owners through Main's existing policy boundary. Shared
source consumers reject unselected imported orders before action or movement.
This changes no casting headers, grants, quotas, dice, core or original SRD file.
"""

from copy import deepcopy

RETAINED_CONTROL_FOLLOWUPS = {
    "shake_srd_sleeping_creature": frozenset({"sleep"}),
    "violate_srd_geas": frozenset({"geas"}),
    "speak_srd_truth_zone": frozenset({"zone_of_truth"}),
    "read_srd_truth_zone_outcomes": frozenset({"zone_of_truth"}),
    "resolve_srd_control_order": frozenset({"command"}),
    "resolve_srd_control_activity": frozenset({"suggestion"}),
}


def _bind_followup_card(operation, tool):
    """Display recorded requests and outcomes through the existing saved-card path."""
    metadata = tool.setdefault("metadata", {})
    if operation == "speak_srd_truth_zone":
        card = metadata["result_card"]
        card["title"] = "Zone of Truth speech"
        card["summary"].append(
            {
                "label": "Response",
                "value": {
                    "path": "parameters.statement_kind",
                    "labels": {
                        "truth": "Truth",
                        "evasion": "Evasion",
                        "deliberate_lie": "Deliberate lie",
                        "silence": "Silence",
                    },
                },
            }
        )
        card["details_label"] = "Recorded statement"
        card["details"] = [{"label": "Statement", "value": {"path": "parameters.statement"}}]
    elif operation == "read_srd_truth_zone_outcomes":
        metadata["result_card"]["title"] = "Zone of Truth saves"
        metadata["result_card"]["details_label"] = "Recorded saves"
    elif operation == "resolve_srd_control_order":
        metadata["result_card"] = {
            "version": 1,
            "footprint": "compact",
            "title": "Command movement",
            "summary": [{"label": "Actor", "value": {"path": "context._actor_state_id"}}],
            "details_label": "Recorded destination",
            "details": [{"label": "Destination", "value": {"path": "parameters.destination"}}],
        }
    elif operation == "resolve_srd_control_activity":
        metadata["result_card"] = {
            "version": 1,
            "footprint": "inline",
            "title": "Suggestion activity",
            "summary": [
                {"label": "Actor", "value": {"path": "context._actor_state_id"}},
                {
                    "label": "Task",
                    "value": {
                        "path": "parameters.completed",
                        "labels": {"true": "Completed", "false": "Continuing"},
                    },
                },
            ],
        }


def _restrict_sleep_sources(value):
    """Keep both original wake checks limited to the selected Sleep source."""
    original = (
        "_control_source.control_profile == 'sleep' or "
        "_control_source.control_profile == 'eyebite_sleep'"
    )
    if isinstance(value, dict):
        changes = 0
        for key, child in value.items():
            if key == "condition" and child == original:
                value[key] = "_control_source.control_profile == 'sleep'"
                changes += 1
            else:
                changes += _restrict_sleep_sources(child)
        return changes
    if isinstance(value, list):
        return sum(_restrict_sleep_sources(child) for child in value)
    return 0


def bind_retained_control_followups(runtime, selected):
    """Retain each selected operation and restrict its existing source selection."""
    selected = set(selected)
    restored = set()
    for operation, owners in RETAINED_CONTROL_FOLLOWUPS.items():
        if not owners & selected:
            continue
        tool = deepcopy(runtime["tools"].get(operation))
        if not isinstance(tool, dict) or not tool.get("effects"):
            raise ValueError("Missing original retained control owner: " + operation)
        if any(effect.get("require", {}).get("condition") == "false" for effect in tool["effects"]):
            raise ValueError("Bind retained controls before Lite pending guards: " + operation)
        if operation in {"resolve_srd_control_order", "resolve_srd_control_activity"}:
            spell = "command" if operation == "resolve_srd_control_order" else "suggestion"
            tool["effects"][:0] = [
                {
                    "require": {
                        "condition": (
                            "lasting_state.timed_condition_sources != null and "
                            "source_id in lasting_state.timed_condition_sources"
                        ),
                        "error": "This retained control action requires an existing spell source.",
                    }
                },
                {
                    "require": {
                        "condition": (
                            "lasting_state.timed_condition_sources[source_id].spell_id == '"
                            + spell
                            + "'"
                        ),
                        "error": (
                            "This control source is excluded from the selected "
                            "SRD Lite follow-up action."
                        ),
                    }
                },
            ]
        if operation == "shake_srd_sleeping_creature":
            if _restrict_sleep_sources(tool["effects"]) != 2:
                raise ValueError("Review the changed original Sleep/Eyebite source boundary")
            tool["description"] = "Spend an actual action to shake away selected Sleep sources."
        _bind_followup_card(operation, tool)
        runtime["tools"][operation] = tool
        restored.add(operation)
    return restored
