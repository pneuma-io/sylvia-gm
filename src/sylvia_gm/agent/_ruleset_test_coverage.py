"""Count observed, asserted scenario outcomes rather than authored coverage labels."""

from typing import Any


def checked_outcomes(
    assertions: list[dict[str, Any]],
    receipt: dict | None,
    tool_results: list[dict],
) -> dict[str, list[str]]:
    """Attribute checks to the result they inspect; state checks cover this step's actions."""
    actions: set[str] = set()
    rejections: set[str] = set()
    preconditions: set[str] = set()
    committed = {
        item["tool"] for item in tool_results
        if item["result"].get("status") == "action_committed"
    }
    if receipt and receipt.get("status") == "committed":
        name = receipt.get("result", {}).get("tool")
        if name:
            committed.add(name)
    for assertion in assertions:
        subject, path = assertion["subject"], assertion["path"]
        if subject == "state" and path:
            actions.update(committed)
        elif receipt and (
            subject == "control" and path
            or subject == "receipt" and path[:2] == ["result", "context"] and len(path) > 2
        ):
            name = receipt.get("result", {}).get("tool")
            if name and receipt.get("status") == "committed":
                actions.add(name)
        elif subject == "tool_results" and len(path) >= 3 and type(path[0]) is int:
            if not 0 <= path[0] < len(tool_results) or path[1] != "result":
                continue
            item = tool_results[path[0]]
            status = item["result"].get("status")
            if status == "action_committed" and path[2] == "result" and len(path) > 3:
                actions.add(item["tool"])
            elif status in {"rejected", "action_failed"} and path[2] in {"status", "code"}:
                rejections.add(item["tool"])
                if item["result"].get("code") == "ruleset_precondition_failed":
                    preconditions.add(item["tool"])
    return {
        "actions": sorted(actions), "rejections": sorted(rejections),
        "preconditions": sorted(preconditions),
    }


def summarize_coverage(
    required: set[str], results: list[dict], scenarios: list[dict],
    *, required_rejections: set[str] | None = None,
    required_preconditions: set[str] | None = None, requires_handoff: bool = False,
) -> dict:
    """A coverage label only counts after its scenario successfully checks the action."""
    checked: set[str] = set()
    rejected: set[str] = set()
    unproven: set[str] = set()
    interactions: list[str] = []
    preconditions: set[str] = set()
    handoffs: list[str] = []
    for scenario, result in zip(scenarios, results, strict=True):
        declared = set(scenario["covers"])
        observed: set[str] = set()
        observed_rejections: set[str] = set()
        phases: set[str] = set()
        if result["status"] == "passed":
            for step in result["steps"]:
                observed.update(step["coverage"]["actions"])
                observed_rejections.update(step["coverage"]["rejections"])
                preconditions.update(set(step["coverage"].get("preconditions", [])) & declared)
                phases.update(step["coverage"].get("handoff_phases", []))
        if {"held", "released"}.issubset(phases):
            handoffs.append(scenario["id"])
        rejected.update(observed_rejections & declared)
        checked.update(observed & declared)
        if len(observed & declared & required) > 1:
            interactions.append(scenario["id"])
        unproven.update(declared - observed - observed_rejections)
    return {
        "required_actions": sorted(required),
        "checked_actions": sorted(checked & required),
        "checked_rejections": sorted(rejected & required),
        "missing_actions": sorted(required - checked),
        "unproven_claims": sorted(unproven),
        "missing_rejections": sorted((required_rejections or set()) - rejected),
        "interaction_scenarios": interactions,
        "missing_interaction": len(required) > 1 and not interactions,
        "missing_preconditions": sorted((required_preconditions or set()) - preconditions),
        "handoff_scenarios": handoffs,
        "missing_handoff": requires_handoff and not handoffs,
    }
