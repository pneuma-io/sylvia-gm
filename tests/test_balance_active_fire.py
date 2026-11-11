"""Instruction-driven active fire uses the current runtime's material routing."""

import pytest

from tests.td_source_fixture import candidate as candidate

from balance_suite.lane_parity import NS, lab_action, paired_fixture, projection
from balance_suite.policies import PolicyDecision
from tests.test_balance_lane_contract import fixture


@pytest.mark.parametrize("resistance", [1, 8])
@pytest.mark.parametrize("roll", [1, 12, 20])
@pytest.mark.parametrize(
    "tool,mode,rounds", [("fire_single", "SEMI AUTO", 1),
                         ("fire_burst", "BURST", 3),
                         ("fire_full_auto", "FULL AUTO", 3)],
)
def test_mapped_target_behind_material_matches_the_current_package(
    candidate, resistance, roll, tool, mode, rounds,
):
    spec = fixture("OPAQUE-LOS-V1")
    spec["positions"] = {"vesper": [2, 2], "hostile_alpha": [8, 2]}
    spec["geometry"][0]["metadata"].update(material_resistance=resistance, cover_hp=1000)
    spec["actor_values"] = {"hostile_alpha": {"hp": 1000, "max_hp": 1000, "res": 0}}
    oracle, engine, grid, metrics = paired_fixture(candidate, spec)
    command = {
        "attacker": "vesper", "target": "hostile_alpha",
        "aim_point": oracle.state[NS + ".entities"]["hostile_alpha"]["center"],
        "critical_effect": "DEEP_HIT",
    }
    if mode == "FULL AUTO":
        command["rounds"] = rounds
    oracle.execute(tool, command, roll=roll)
    event = lab_action(
        engine, grid, metrics, "vesper",
        PolicyDecision("FIRE", "hostile_alpha", mode, rounds, None, "mapped target instruction",
                       critical_choice="DEEP HIT"),
        "active-material", roll=roll,
    )
    assert not event.rejected_reason
    assert projection(oracle, engine)[0] == projection(oracle, engine)[1]
