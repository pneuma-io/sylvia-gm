from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from sylvia_gm.agent._provider_session import _derive_mechanics_parameters
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
from sylvia_gm.application.bundled_packages import bundled_srd_archive
from sylvia_gm.domain.authority import (
    ActionReceipt,
    ActionStatus,
    Participant,
    ParticipantValidationStatus,
    StateValue,
    WriterRole,
)

PACKAGE_ID = "sylvia.srd-lite.bg3"
ABILITIES = {
    "strength": 18,
    "dexterity": 16,
    "constitution": 14,
    "intelligence": 12,
    "wisdom": 10,
    "charisma": 8,
}
SKILLS = {
    "athletics": "strength",
    "acrobatics": "dexterity",
    "sleight_of_hand": "dexterity",
    "stealth": "dexterity",
    "arcana": "intelligence",
    "history": "intelligence",
    "investigation": "intelligence",
    "nature": "intelligence",
    "religion": "intelligence",
    "animal_handling": "wisdom",
    "insight": "wisdom",
    "medicine": "wisdom",
    "perception": "wisdom",
    "survival": "wisdom",
    "deception": "charisma",
    "intimidation": "charisma",
    "performance": "charisma",
    "persuasion": "charisma",
}


@pytest.fixture
def case(tmp_path: Path, request: pytest.FixtureRequest):
    combat = getattr(request, "param", False)
    from tests.admitted_library_fixture import admitted_fixture_owner
    with admitted_fixture_owner(tmp_path / "checks.db", "checks-owner", bundled_srd_archive()) as (owner, candidate):
        record = json.loads(
            (
                Path(__file__).resolve().parents[1] / "rulesets/srd-lite/package-record.json"
            ).read_text(encoding="utf-8")
        )
        artifact = next(
            a for a in record["participant_artifacts"] if a["kind"] == "player_character"
        )
        exact = next(e for e in candidate.package_lock.entries if e.package_id == PACKAGE_ID)
        participant = Participant(
            participant_id="hero",
            kind="player_character",
            package_id=PACKAGE_ID,
            package_version=exact.version,
            package_sha256=exact.sha256,
            schema_id=artifact["schema_id"],
            schema_sha256=artifact["schema_sha256"],
            validation_status=ParticipantValidationStatus.VALIDATED,
            data={
                "name": "Check Hero",
                "level": 5,
                "species": "human",
                "background": "soldier",
                "origin_feats": ["skilled"],
                "classes": [{"id": "fighter", "level": 5}],
                "abilities": ABILITIES,
                "speed": 30,
                "size_rank": 1,
                "skill_proficiencies": ["athletics", "intimidation", "investigation"],
                "tool_proficiencies": ["thieves_tools"],
                "saving_throw_proficiencies": [],
            },
        )
        owner.create_campaign(
            CampaignCreate(
                campaign_id="checks",
                display_name="Ability check coverage",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id="checks-turn",
                participants=(participant,),
                initial_state=(
                    StateValue(
                        PACKAGE_ID,
                        "combat",
                        {
                            "encounter_active": combat,
                            "turn_active": combat,
                            "phase": "turn_active",
                            "active_actor": "hero",
                            "action_available": True,
                            "active_actor_kind": "character",
                            "active_actor_can_act": True,
                        },
                    ),
                    StateValue(
                        PACKAGE_ID,
                        "entities",
                        {
                            "hero": {
                                "hp": 30,
                                "max_hp": 30,
                                "conditions": [],
                                "exhaustion_level": 0,
                            },
                        },
                    ),
                ),
            )
        )
        yield owner


def test_catalog_discovery_does_not_open_a_database_connection_per_tool(case, monkeypatch):
    """Discovery keeps its database work bounded as a real package exposes many tools."""
    expected = case.catalog("checks")
    assert len(expected) > 10
    connections = 0
    connect = case._store._connect

    def counted_connect():
        nonlocal connections
        connections += 1
        return connect()

    monkeypatch.setattr(case._store, "_connect", counted_connect)
    assert case.catalog("checks") == expected
    assert connections <= 4


def _check(
    owner: AuthorityOwner,
    name: str = "ability_check",
    *,
    mutates: bool = False,
    **choices: object,
) -> ActionReceipt:
    mechanics = StatelessMcpMechanics(owner)
    tool = next(t for t in mechanics.list_tools("checks") if t.name == name)
    public = tool.input_schema["properties"]
    assert "ability_modifier" not in public and "proficiency_bonus" not in public
    assert "actor_state" not in public and "proficiency_factor" not in public
    before = owner.snapshot("checks")
    parameters = _derive_mechanics_parameters(
        {
            "actor": "hero",
            "circumstantial_modifier": 0,
            "advantage": False,
            "disadvantage": False,
            **({"target_number": 12} if name != "hide_srd_action" else {}),
            **choices,
        },
        tool.parameter_derivations or {},
        before.participants,
    )
    receipt = mechanics.execute_action(
        ActionCommand(
            campaign_id="checks",
            action_id=f"check-{before.revision}",
            idempotency_key=f"check-{before.revision}",
            owner_id="checks-owner",
            writer=WriterRole.DM,
            expected_revision=before.revision,
            turn_id="checks-turn",
            package_id=PACKAGE_ID,
            tool_name=name,
            parameters=parameters,
        )
    )
    assert isinstance(receipt, ActionReceipt)
    assert receipt.status is ActionStatus.COMMITTED, receipt.result
    if not mutates:
        assert owner.snapshot("checks").state == before.state
    return receipt


def test_six_bare_abilities_and_eighteen_skills_use_saved_character(case: AuthorityOwner) -> None:
    for ability, score in ABILITIES.items():
        receipt = _check(case, ability=ability)
        card = receipt.result["result_card"]
        assert card["title"] == ability.title()
        assert card["subtitle"] == "Ability check"
        assert card["equation"]["modifier"] == (score - 10) // 2
        assert {"label": "Ability", "value": ability.title(), "emphasis": False} in card["summary"]
        assert any(row["value"] == "Check Hero" for row in card["summary"])
    for skill, ability in SKILLS.items():
        receipt = _check(case, name="d20_test", skill=skill)
        card = receipt.result["result_card"]
        expected = (ABILITIES[ability] - 10) // 2
        if skill in {"athletics", "intimidation", "investigation"}:
            expected += 3
        assert card["title"] == (
            "Sleight of Hand" if skill == "sleight_of_hand" else skill.replace("_", " ").title()
        )
        assert card["equation"]["modifier"] == expected
        assert card["equation"]["total"] == card["equation"]["roll"] + expected
        assert {"label": "Ability", "value": ability.title(), "emphasis": False} in card["summary"]


def test_alternate_ability_tool_and_combined_proficiencies(case: AuthorityOwner) -> None:
    alternate = _check(case, ability="strength", skill="intimidation")
    assert alternate.result["result_card"]["equation"]["modifier"] == 7
    tool = _check(case, ability="dexterity", tool="thieves_tools")
    assert tool.result["result_card"]["title"] == "Thieves' Tools"
    assert tool.result["result_card"]["equation"]["modifier"] == 6
    both = _check(case, ability="intelligence", skill="investigation", tool="thieves_tools")
    context = both.result["context"]
    assert context["_proficiency_modifier"] == 3
    assert context["_effective_advantage"] is True
    assert len(context["_d20_test"]["results"]) == 2
    assert context["_d20_test"]["total"] == max(context["_d20_test"]["results"])
    cancelled = _check(
        case,
        ability="intelligence",
        skill="investigation",
        tool="thieves_tools",
        disadvantage=True,
    )
    assert len(cancelled.result["context"]["_d20_test"]["results"]) == 1
    untrained = _check(case, ability="dexterity", tool="smiths_tools")
    assert untrained.result["result_card"]["equation"]["modifier"] == 3


def test_ordinary_advantage_disadvantage_and_outcomes(case: AuthorityOwner) -> None:
    for flags, selector in (({"advantage": True}, max), ({"disadvantage": True}, min)):
        result = _check(case, ability="constitution", **flags).result
        dice = result["context"]["_d20_test"]
        assert len(dice["results"]) == 2
        assert dice["total"] == selector(dice["results"])
    success = _check(case, ability="strength", target_number=1)
    failure = _check(case, ability="charisma", target_number=30)
    assert success.result["context"]["_outcome"] == "success"
    assert failure.result["context"]["_outcome"] == "failure"


@pytest.mark.parametrize("case", [True], indirect=True)
@pytest.mark.parametrize(
    ("tool", "skill", "ability", "modifier"),
    (
        ("search_srd_action", "perception", "Wisdom", 0),
        ("study_srd_action", "investigation", "Intelligence", 4),
        ("influence_srd_action", "animal_handling", "Wisdom", 0),
        ("influence_srd_action", "performance", "Charisma", -1),
        ("hide_srd_action", None, "Dexterity", 3),
    ),
)
def test_special_action_retains_full_check_card_and_spends_action(
    case: AuthorityOwner,
    tool: str,
    skill: str | None,
    ability: str,
    modifier: int,
) -> None:
    choices = (
        {"skill": skill}
        if skill
        else {
            "heavily_obscured": True,
            "cover": "none",
            "outside_enemy_line_of_sight": True,
            "enemy_within_5_feet": False,
        }
    )
    result = _check(case, name=tool, mutates=True, **choices).result
    card = result["result_card"]
    assert card["subtitle"] == "Ability check"
    assert card["equation"]["modifier"] == modifier
    assert any(row["label"] == "Ability" and row["value"] == ability for row in card["summary"])
    assert case.snapshot("checks").state[f"{PACKAGE_ID}.combat"]["action_available"] is False
