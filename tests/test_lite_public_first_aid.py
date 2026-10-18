"""Ordinary Medicine reaches the admitted Lite surface and preserves zero-HP truth."""

from copy import deepcopy

import pytest

from sylvia_gm.domain.errors import MechanicsRejected
from tests.lite_fixture_archive import archive_closed_case
from tests.lite_imported_fixture import ImportedCase

SRD = "sylvia.srd-lite.bg3"


def _case(path, *, changes=None):
    values = {
        "hero": {
            "hp": 0,
            "is_alive": True,
            "stable": False,
            "conditions": ["unconscious"],
            "death_successes": 1,
            "death_failures": 1,
            "stable_recovery_hours_remaining": 0,
            "initiative_modifier": -10,
        },
        "guard": {"initiative_modifier": 20},
    }
    for actor, fields in (changes or {}).items():
        values.setdefault(actor, {}).update(fields)
    return ImportedCase(path, "light", entity_changes=values)


def _help(case, modifier):
    return case.act(
        "stabilize_srd_creature",
        actor_state="guard",
        target="hero",
        medicine_modifier=modifier,
        advantage=False,
        disadvantage=False,
    )


def test_admitted_lite_exposes_ordinary_first_aid(tmp_path):
    case = _case(tmp_path / "first-aid.sqlite3")
    try:
        names = {tool.name for tool in case.owner.catalog("lite")}
        assert "stabilize_srd_creature" in names, (
            "Ordinary first aid needs an admitted public operation"
        )
    finally:
        archive_closed_case(case)


@pytest.mark.parametrize("modifier,stable", [(20, True), (-20, False)])
@pytest.mark.parametrize("combat", [False, True])
def test_native_check_preserves_hp_conditions_cost_and_exact_reopen(
    tmp_path, modifier, stable, combat
):
    case = _case(tmp_path / "first-aid.sqlite3")
    try:
        if combat:
            case.act(
                "start_combat_encounter_entities",
                first_actor="guard",
                second_actor="hero",
                first_surprised=False,
                second_surprised=False,
                tie_winner="first",
            )
            case.act("begin_combat_turn")
            assert case.snapshot().state[SRD + ".combat"]["active_actor"] == "guard"
        before = deepcopy(case.entities()["hero"])
        receipt, command = _help(case, modifier)
        target = case.entities()["hero"]
        assert target["hp"] == before["hp"] == 0 and target["is_alive"]
        assert target["conditions"] == before["conditions"] == ["unconscious"]
        assert target["stable"] is stable
        assert target["death_successes"] == (0 if stable else 1)
        assert target["death_failures"] == (0 if stable else 1)
        assert (
            target["stable_recovery_hours_remaining"] in range(1, 5)
            if stable
            else target["stable_recovery_hours_remaining"] == 0
        )
        card = receipt.result["result_card"]
        assert card["title"] == "Stabilize"
        assert card["equation"]["total"] == card["equation"]["roll"] + modifier
        assert {row["label"]: row["value"] for row in card["summary"]}["Outcome"] == (
            "Stable" if stable else "Not stabilized"
        )
        if combat:
            assert case.snapshot().state[SRD + ".combat"]["action_available"] is False
        saved = case.snapshot()
        assert case.execute(command) == receipt and case.snapshot() == saved
        case.reopen()
        assert case.snapshot() == saved and case.execute(command) == receipt
        if combat and not stable:
            with pytest.raises(MechanicsRejected):
                _help(case, modifier)
            assert case.snapshot().state == saved.state
        if stable:
            if combat:
                case.act("end_combat_encounter", end_reason="fighting_stopped")
            hours = case.entities()["hero"]["stable_recovery_hours_remaining"]
            case.act("advance_stable_recovery", target="hero", elapsed_hours=hours)
            assert case.entities()["hero"]["hp"] == 1
            assert "unconscious" not in case.entities()["hero"]["conditions"]
            recovered = case.snapshot()
            case.reopen()
            assert case.snapshot() == recovered
    finally:
        archive_closed_case(case)


@pytest.mark.parametrize(
    "changes",
    [
        {"guard": {"hp": 0, "conditions": ["unconscious"]}},
        {"guard": {"conditions": ["stunned"]}},
        {"hero": {"is_alive": False}},
        {"hero": {"hp": 5}},
        {"hero": {"stable": True}},
    ],
)
def test_invalid_first_aid_is_atomic(tmp_path, changes):
    case = _case(tmp_path / "first-aid.sqlite3", changes=changes)
    try:
        before = case.snapshot()
        with pytest.raises(MechanicsRejected):
            _help(case, 20)
        assert case.snapshot().state == before.state
        assert case.snapshot().participants == before.participants
    finally:
        archive_closed_case(case)
