"""Supported outcomes remain saved and visible through the admitted package."""

import pytest
import yaml
from scripts.srd_runtime_defaults import compact_runtime_defaults

from sylvia_gm.runtime._runtime import RulesetRuntime
from tests.lite_fixture_archive import archive_closed_case
from tests.lite_imported_fixture import ImportedCase


def rows(card):
    return {row["label"]: row["value"] for row in card["summary"]}


@pytest.mark.parametrize(
    "elapsed,expected,hp", [(1, "Recovery time advanced", 0), (2, "Regained 1 HP", 1)]
)
def test_stable_recovery_card_keeps_actual_recovery_and_clock(tmp_path, elapsed, expected, hp):
    case = ImportedCase(
        tmp_path / "recovery.db",
        "light",
        entity_changes={
            "hero": {
                "hp": 0,
                "stable": True,
                "conditions": ["unconscious"],
                "stable_recovery_hours_remaining": 2,
            }
        },
    )
    try:
        receipt, command = case.act("advance_stable_recovery", target="hero", elapsed_hours=elapsed)
        card = receipt.result["result_card"]
        after = case.entities()["hero"]
        assert rows(card)["Outcome"] == expected
        assert rows(card)["HP"] == after["hp"] == hp
        assert (
            rows(card)["Hours remaining"] == after["stable_recovery_hours_remaining"] == 2 - elapsed
        )
        assert (
            next(row["value"] for row in card["details"] if row["label"] == "Conditions")
            == after["conditions"]
        )
        saved = case.snapshot()
        case.reopen()
        assert case.snapshot() == saved and case.execute(command) == receipt
    finally:
        archive_closed_case(case)


@pytest.mark.parametrize("restraint", ["grappled", "restrained"])
def test_freedom_escape_card_records_actual_removed_condition_and_movement(tmp_path, restraint):
    case = ImportedCase(
        tmp_path / "escape.db",
        "freedom_of_movement",
        entity_changes={
            "hero": {
                "spell_freedom_of_movement": True,
                "conditions": [restraint],
                "unowned_grappled_is_magical": False,
                "unowned_restrained_is_magical": False,
                "timed_condition_unowned_restrained": restraint == "restrained",
            }
        },
    )
    try:
        case.start()
        before = case.snapshot().state["sylvia.srd-lite.bg3.combat"]["movement_remaining"]
        receipt, command = case.act(
            "escape_srd_nonmagical_restraint", actor_state="hero", restraint=restraint
        )
        card = receipt.result["result_card"]
        after = case.entities()["hero"]
        assert rows(card)["Removed condition"] == restraint
        assert rows(card)["Movement spent"] == 5
        assert restraint not in after["conditions"]
        assert (
            case.snapshot().state["sylvia.srd-lite.bg3.combat"]["movement_remaining"] == before - 5
        )
        assert (
            next(row["value"] for row in card["details"] if row["label"] == "Conditions")
            == after["conditions"]
        )
        saved = case.snapshot()
        case.reopen()
        assert case.snapshot() == saved and case.execute(command) == receipt
    finally:
        archive_closed_case(case)


def test_null_default_compaction_preserves_tools_and_shared_parameter_references():
    source = """parameter_declarations:
  shared:
    name: value
    required: false
    default: null
tools:
  example:
    parameters:
    - shared
    - name: other
      required: false
      default: null
    effects: []
"""
    compact = compact_runtime_defaults(source)
    assert "default: null" not in compact
    assert (
        RulesetRuntime(ruleset_dict=yaml.safe_load(source)).tools
        == RulesetRuntime(ruleset_dict=yaml.safe_load(compact)).tools
    )
    assert compact_runtime_defaults(compact) == compact


def test_supported_factual_results_retain_exact_committed_public_values(tmp_path):
    case = ImportedCase(
        tmp_path / "facts.db",
        "light",
        entity_changes={
            "hero": {"concentration_target_state_id": "guard", "inventory": ["quarterstaff"]},
            "guard": {"summon_active": True, "summon_caster_state_id": "hero"},
        },
    )
    try:
        operations = [
            (
                "prepare_srd_creature",
                {
                    "creature_id": "witness",
                    "name": "Witness",
                    "source": "Synthetic admitted statblock.",
                    "armor_class": 13,
                    "hit_points": 10,
                    "speed": 30,
                    "initiative_modifier": 1,
                    "size_rank": 2,
                },
                {"Creature": "Witness", "Creature ID": "witness", "Hit Points": 10},
            ),
            (
                "command_srd_summoned_creature",
                {"caster_state": "hero", "summon_state": "guard", "command": "dodge"},
                {"Command": "dodge", "Summon ID": "guard"},
            ),
            (
                "record_srd_creature_relationship",
                {
                    "actor_state": "guard",
                    "toward_state": "hero",
                    "attitude": "friendly",
                    "is_ally": True,
                },
                {"Recorded attitude": "friendly", "Ally recorded": True},
            ),
            (
                "read_srd_creature_attitude",
                {"actor_state": "guard", "toward_state": "hero"},
                {"Attitude": "friendly"},
            ),
            (
                "record_srd_severed_body_part",
                {"target_state": "guard", "part_id": "left_hand", "part_name": "Left hand"},
                {"Body part": "Left hand", "Creature ID": "guard"},
            ),
            (
                "read_srd_carried_item_size",
                {"actor_state": "hero", "item": "quarterstaff"},
                {"Item": "quarterstaff", "Size category adjustment": 0},
            ),
        ]
        for name, parameters, expected in operations:
            receipt, command = case.act(name, **parameters)
            assert rows(receipt.result["result_card"]) == expected
            saved = case.snapshot()
            assert case.execute(command) == receipt and case.snapshot() == saved
            case.reopen()
            assert case.snapshot() == saved and case.execute(command) == receipt
        assert case.entities()["witness"]["hp"] == 10
        assert case.entities()["guard"]["summon_command"] == "dodge"
        assert case.entities()["guard"]["body_parts"]["left_hand"]["missing"] is True
    finally:
        archive_closed_case(case)


def test_empty_parameter_list_compaction_preserves_exact_tool_defaults():
    source = "tools:\n  example:\n    parameters: []\n    effects: []\n"
    compact = compact_runtime_defaults(source)
    assert "parameters: []" not in compact
    assert (
        RulesetRuntime(ruleset_dict=yaml.safe_load(source)).tools
        == RulesetRuntime(ruleset_dict=yaml.safe_load(compact)).tools
    )
