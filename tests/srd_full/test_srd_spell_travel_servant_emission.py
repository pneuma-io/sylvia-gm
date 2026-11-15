"""Early staff definitions defer canonical pouring until its final actual owner is emitted."""

import hashlib
from copy import deepcopy
from pathlib import Path

import pytest
from scripts import srd_spell_travel_servants as servants
from scripts import srd_spell_travel_telekinesis_pour as pour
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_timed_senses import _replace_tools

RUNTIME = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-spell-travel-servant-emission/runtime.yaml")
)
RUNTIME_SHA = "06ed5e03573e446a6d39bda08bb40d080b1dd724cfda594d1ab24de0c9471962"


def _actual():
    data = RUNTIME.read_bytes()
    assert hashlib.sha256(data).hexdigest() == RUNTIME_SHA
    return data.decode(), _load_runtime(data.decode())["tools"]


def test_early_actual_pour_definition_is_deferred_then_final_capture_matches_tested_owner():
    text, tools = _actual()
    early = dict(tools)
    early.pop("operate_srd_mage_hand")
    early["pour_srd_object_vial_source_node"] = pour._plain_pour_node(early)
    assert not any(
        "spatial_mutation" in row for row in early["pour_srd_object_vial_source_node"]["effects"]
    )
    changed = servants.servant_tools(early)
    deferred = changed["pour_srd_mansion_servant_vial_node"]
    assert not any("spatial_mutation" in row for row in deferred["effects"])
    assert deferred["effects"][-1]["require"]["condition"] == "false"
    early_text = _replace_tools(
        text,
        {
            "pour_srd_mansion_servant_vial_node": deferred,
            "pour_srd_object_vial_source_node": early["pour_srd_object_vial_source_node"],
        },
    )
    final_text = pour.emit_late_pour(early_text)
    final_text = servants.emit_late_servant_access(final_text)
    final = _load_runtime(final_text)["tools"]
    assert (
        final["pour_srd_mansion_servant_vial_node"] == tools["pour_srd_mansion_servant_vial_node"]
    )
    assert final["command_srd_mansion_servant"] == tools["command_srd_mansion_servant"]
    assert servants.emit_late_servant_access(final_text) == final_text


@pytest.mark.parametrize("defect", ["missing", "boundary", "no_mutation", "duplicate_mutation"])
def test_final_capture_stays_strict_for_missing_or_malformed_canonical_owner(defect):
    _, tools = _actual()
    changed = deepcopy(tools)
    if defect == "missing":
        changed.pop("pour_srd_object_vial_source_node")
    elif defect == "boundary":
        changed["qualify_srd_plain_water_vial"]["effects"][0]["call"] = "wrong_owner"
    else:
        owner = changed["pour_srd_object_vial_source_node"]["effects"]
        mutation = next(row for row in owner if "spatial_mutation" in row)
        if defect == "no_mutation":
            owner.remove(mutation)
        else:
            owner.append(deepcopy(mutation))
    with pytest.raises(
        ValueError, match="(Final servant|qualification boundary|one canonical vial mutation)"
    ):
        servants._pour_node(changed, capture=True)
