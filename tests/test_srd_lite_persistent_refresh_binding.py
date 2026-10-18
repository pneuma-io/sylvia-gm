"""Check saved-state refresh binding against the four exact selected1165 public owners."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from scripts import srd_lite_persistent_spell_map as visual

FIXTURE = Path(__file__).parent / "fixtures" / "srd_lite_public_refresh_wrappers_1171.json"


def _owners():
    return json.loads(
        FIXTURE.read_text(encoding="utf-8")
    )["tools"]


def test_actual_explicit_state_owner_refreshes_the_bound_saved_state():
    owner = _owners()["set_srd_illumination_cover"]
    original = deepcopy(owner)
    assert visual._refresh_binding("set_srd_illumination_cover", owner) == {
        "lasting_state": "{_lasting_state_id}"
    }
    assert owner == original


def test_actual_parameterless_public_wrapper_uses_its_exact_state_receiver():
    owner = _owners()["dismiss_srd_illumination_spell"]
    original = deepcopy(owner)
    assert not any(parameter["name"] == "lasting_state" for parameter in owner["parameters"])
    assert visual._refresh_binding("dismiss_srd_illumination_spell", owner) == {
        "lasting_state": "state"
    }
    assert owner == original


@pytest.mark.parametrize("drift", ["missing", "duplicate", "dynamic_state"])
def test_actual_parameterless_wrapper_rejects_unsafe_receiver_drift(drift):
    name = "dismiss_srd_illumination_spell"
    owner = _owners()[name]
    matches = [
        (rows, index, row)
        for rows in visual._lists(owner["effects"])
        for index, row in enumerate(rows)
        if row.get("call") == name + "_node"
    ]
    assert len(matches) == 1
    rows, index, row = matches[0]
    assert row["params"]["lasting_state"] == "state"
    if drift == "missing":
        del rows[index]
    elif drift == "duplicate":
        rows.insert(index, deepcopy(row))
    else:
        row["params"]["lasting_state"] = "{unproved_state_binding}"
    with pytest.raises(ValueError, match="actual mutable saved-state binding"):
        visual._refresh_binding(name, owner)
