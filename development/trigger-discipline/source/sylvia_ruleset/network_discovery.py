"""QUIET ENUMERATION uses ordinary discovery and the canonical visibility bands."""

from copy import deepcopy


def specification(ordinary):
    """Lower discovery visibility one band without granting routes, privilege or immunity."""
    tool = deepcopy(ordinary)
    tool["description"] = (
        "Use selected QUIET ENUMERATION for an ordinary DISCOVER action. Requires the same "
        "live compromised node, route, privileges, authored action cost and discovery result. "
        "Lower its HACKING VISIBILITY one ordered band, stopping at LOW. Relevant monitoring "
        "and detection still apply; this reveals no otherwise undiscoverable topology."
    )
    for parameter in tool["parameters"]:
        if parameter["name"] == "action":
            parameter.update(enum_values=["DISCOVER"], required=False, default="DISCOVER")
    for index, effect in enumerate(tool["effects"]):
        if effect.get("condition") != "_intrusion_allowed == true" or "on_true" not in effect:
            continue
        tool["effects"].insert(index, {
            "set": {"field": "_intrusion_allowed", "value": False},
            "condition": (
                "_intrusion_allowed == true and (method != 'INTRUDE' or "
                "'specialty.networker.tier_1.quiet_enumeration' not in actor.specialties)"
            ),
        })
        # The rule remains local to this execution; authored difficulty, cost,
        # discovery links and monitoring records are preserved.
        insertion = next(i + 1 for i, node in enumerate(effect["on_true"])
                         if node.get("set", {}).get("field") == "_granted_privilege")
        effect["on_true"][insertion:insertion] = [
            {"set": {"field": "_quiet_visibility_before", "value": "'STANDARD'"}},
            {"set": {"field": "_quiet_visibility_before", "value": "_rule.visibility"},
             "condition": "'visibility' in _rule"},
            {"set": {"field": "_quiet_patch", "value": {"visibility": "'LOW'"}}},
            {"set": {"field": "_quiet_patch", "value": {"visibility": "'STANDARD'"}},
             "condition": "_quiet_visibility_before == 'HIGH'"},
            {"set": {"field": "_rule", "value": "merge(_rule, _quiet_patch)"}},
            {"set": {"field": "_quiet_visibility_after", "value": "_rule.visibility"}},
        ]
        return tool
    raise ValueError("Ordinary discovery commit boundary is unavailable")
