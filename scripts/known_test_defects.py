"""Exact, reviewed baseline defects; ordinary skips never satisfy a required check."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KnownDefect:
    reason: str
    reproduction: str
    category: str = "suspected product defect"


KNOWN_DEFECTS: dict[str, KnownDefect] = {
    "tests/test_campaign_act_markers.py::test_markdown_only_plans_and_builder_chats_have_no_invented_acts": KnownDefect(
        "An unstructured new campaign history read queries an absent continuity table",
        "Create the ordinary markdown-only Builder-to-DM handoff, then read its history page.",
    ),
    "tests/test_campaign_plan_skill_examples.py::test_builder_retrieves_a_complete_valid_example_from_its_advertised_references": KnownDefect(
        "The advertised worked Plan reference has stale package bindings",
        "Load the Plan skill's combat/multi-act examples and compare/admit their bindings against the bundled source closure.",
    ),
}


def case_key(nodeid):
    """Use pytest's JUnit classname/name spelling, including classes and parameters."""
    unparameterized, bracket, parameters = nodeid.partition("[")
    path, *parts = unparameterized.split("::")
    classname = path.removesuffix(".py").replace("/", ".")
    if len(parts) > 1:
        classname += "." + ".".join(parts[:-1])
    return classname + "::" + parts[-1] + bracket + parameters


def failure_reason(nodeid):
    return f"Known defect: {KNOWN_DEFECTS[nodeid].reason}"


def accepted_case(case):
    """Accept only a real strict-xfail outcome with its registered exact identity/reason."""
    if case.find("failure") is not None or case.find("error") is not None:
        return False
    skipped = case.find("skipped")
    if skipped is None:
        return True
    if skipped.get("type") != "pytest.xfail":
        return False
    key = case.get("classname", "") + "::" + case.get("name", "")
    return any(
        key == case_key(nodeid) and skipped.get("message") == failure_reason(nodeid)
        for nodeid in KNOWN_DEFECTS
    )


def report_outcomes(cases):
    """Keep expected failures separate from passes in the local gate receipt."""
    counts = dict.fromkeys(("passed", "failed", "errors", "expected_failures", "unexpected_skips"), 0)
    expected = []
    for case in cases:
        if case.find("failure") is not None:
            counts["failed"] += 1
        elif case.find("error") is not None:
            counts["errors"] += 1
        elif case.find("skipped") is not None:
            if accepted_case(case):
                counts["expected_failures"] += 1
                key = case.get("classname", "") + "::" + case.get("name", "")
                nodeid = next(nodeid for nodeid in KNOWN_DEFECTS if case_key(nodeid) == key)
                expected.append({
                    "nodeid": nodeid, "reason": KNOWN_DEFECTS[nodeid].reason,
                    "category": KNOWN_DEFECTS[nodeid].category,
                })
            else:
                counts["unexpected_skips"] += 1
        else:
            counts["passed"] += 1
    return counts, expected
