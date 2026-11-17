"""Expected failures cannot turn skips, missing cases or unexpected passes green."""

import xml.etree.ElementTree as ET

import pytest

from scripts.known_test_defects import (
    KNOWN_DEFECTS, KnownDefect, accepted_case, case_key, failure_reason, report_outcomes,
)


@pytest.mark.parametrize("change", ["none", "unknown", "reason", "skip", "xpass", "error"])
def test_only_an_exact_registered_xfail_is_accepted(change, monkeypatch):
    nodeid = "tests/example.py::test_known"
    monkeypatch.setitem(KNOWN_DEFECTS, nodeid, KnownDefect("Synthetic known failure", "Synthetic reproduction"))
    classname, name = case_key(nodeid).split("::", 1)
    case = ET.Element("testcase", classname=classname, name=name)
    skipped = ET.SubElement(case, "skipped", type="pytest.xfail", message=failure_reason(nodeid))
    if change == "unknown":
        case.set("name", "a_different_test")
    elif change == "reason":
        skipped.set("message", "unregistered reason")
    elif change == "skip":
        skipped.set("type", "pytest.skip")
    elif change in {"xpass", "error"}:
        case.remove(skipped)
        ET.SubElement(case, "failure" if change == "xpass" else "error")
    assert accepted_case(case) is (change == "none")


def test_plain_pass_is_accepted_and_known_defects_have_reproductions():
    assert accepted_case(ET.Element("testcase", classname="tests.example", name="passing"))
    assert all(defect.reason and defect.reproduction for defect in KNOWN_DEFECTS.values())


def test_receipt_does_not_count_a_known_failure_as_passing(monkeypatch):
    nodeid = "tests/example.py::test_known"
    monkeypatch.setitem(KNOWN_DEFECTS, nodeid, KnownDefect("Synthetic failure", "Synthetic reproduction"))
    classname, name = case_key(nodeid).split("::", 1)
    failed = ET.Element("testcase", classname=classname, name=name)
    ET.SubElement(failed, "skipped", type="pytest.xfail", message=failure_reason(nodeid))
    counts, expected = report_outcomes([
        ET.Element("testcase", classname="tests.example", name="passing"), failed,
    ])
    assert counts == {"passed": 1, "failed": 0, "errors": 0, "expected_failures": 1, "unexpected_skips": 0}
    assert expected[0]["nodeid"] == nodeid
