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
    AwaitingChoiceReceipt,
    PackageLock,
    Participant,
    ParticipantValidationStatus,
    StateValue,
    WriterRole,
)
from sylvia_gm.domain.errors import MechanicsRejected

PACKAGE_ID = "wotc.srd.5.2.1"
ROOT = Path(__file__).resolve().parents[2]


def _installed_fighter(package_lock: PackageLock, *, fighter: bool) -> Participant:
    record = json.loads(
        (ROOT / "rulesets" / "dnd-srd-5.2.1" / "package-record.json").read_text(encoding="utf-8")
    )
    artifact = next(
        item for item in record["participant_artifacts"] if item["kind"] == "player_character"
    )
    exact = next(entry for entry in package_lock.entries if entry.package_id == PACKAGE_ID)
    class_id = "fighter" if fighter else "wizard"
    return Participant(
        participant_id="hero",
        kind="player_character",
        package_id=PACKAGE_ID,
        package_version=exact.version,
        package_sha256=exact.sha256,
        schema_id=artifact["schema_id"],
        schema_sha256=artifact["schema_sha256"],
        validation_status=ParticipantValidationStatus.VALIDATED,
        data={
            "name": "Synthetic Hero",
            "level": 1,
            "species": "human",
            "background": "soldier",
            "origin_feats": ["savage_attacker", "skilled"],
            "classes": [{"id": class_id, "level": 1}],
            "abilities": {
                "strength": 10,
                "dexterity": 10,
                "constitution": 10,
                "intelligence": 10,
                "wisdom": 10,
                "charisma": 10,
            },
            "speed": 30,
            "size_rank": 0,
            "saving_throw_proficiencies": [],
        },
    )


def _create_case(
    owner: AuthorityOwner,
    *,
    campaign_id: str,
    hp: int,
    uses: int,
    fighter: bool = True,
    combat: bool = False,
    bonus_action_available: bool = True,
) -> None:
    candidate = owner.install_package_archive(
        io.BytesIO(bundled_srd_archive()), install_id=f"install-{campaign_id}"
    )
    participant = _installed_fighter(candidate.package_lock, fighter=fighter)
    owner.create_campaign(
        CampaignCreate(
            campaign_id=campaign_id,
            display_name=f"Synthetic {campaign_id}",
            writer=WriterRole.DM,
            package_lock=candidate.package_lock,
            turn_id=f"turn-{campaign_id}",
            participants=(participant,),
            initial_state=(
                StateValue(
                    PACKAGE_ID,
                    "combat",
                    {
                        "encounter_active": combat,
                        "turn_active": combat,
                        "active_actor": "hero" if combat else None,
                    },
                ),
                StateValue(
                    PACKAGE_ID,
                    "entities",
                    {
                        "hero": {
                            "hp": hp,
                            "max_hp": 100,
                            "is_alive": True,
                            "fighter_level": 1 if fighter else 0,
                            "conditions": [],
                            "bonus_action_available": bonus_action_available,
                            "available_bonus_actions": ["second_wind"],
                            "last_bonus_action": None,
                            "resources": {"second_wind": uses},
                        }
                    },
                ),
            ),
        )
    )


def _use(owner: AuthorityOwner, campaign_id: str) -> ActionReceipt | AwaitingChoiceReceipt:
    mechanics = StatelessMcpMechanics(owner)
    tool = next(
        tool for tool in mechanics.list_tools(campaign_id) if tool.name == "use_srd_second_wind"
    )
    assert set(tool.input_schema["properties"]) == {"actor"}
    parameters = _derive_mechanics_parameters(
        {"actor": "hero"},
        tool.parameter_derivations or {},
        owner.snapshot(campaign_id).participants,
    )
    return mechanics.execute_action(
        ActionCommand(
            campaign_id=campaign_id,
            action_id=f"action-{campaign_id}",
            idempotency_key=f"idempotency-{campaign_id}",
            owner_id="second-wind-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id=f"turn-{campaign_id}",
            package_id=PACKAGE_ID,
            tool_name="use_srd_second_wind",
            parameters=parameters,
        )
    )


def _hero(owner: AuthorityOwner, campaign_id: str) -> dict[str, object]:
    return owner.snapshot(campaign_id).state[f"{PACKAGE_ID}.entities"]["hero"]


def test_second_wind_consumes_at_full_hp_and_persists_after_reopen(tmp_path: Path) -> None:
    database = tmp_path / "second-wind-full.db"
    with AuthorityOwner.open(database, "second-wind-owner") as owner:
        _create_case(owner, campaign_id="full", hp=100, uses=2)
        result = _use(owner, "full")
        assert result.status is ActionStatus.COMMITTED, result.result
        roll = result.result["context"]["_second_wind_roll"]
        assert roll["modifier"] == 1
        assert _hero(owner, "full")["hp"] == 100
        assert _hero(owner, "full")["resources"]["second_wind"] == 1

    with AuthorityOwner.open(database, "second-wind-owner") as reopened:
        assert _hero(reopened, "full")["resources"]["second_wind"] == 1


def test_second_wind_wounded_healing_includes_fighter_level(tmp_path: Path) -> None:
    with AuthorityOwner.open(tmp_path / "second-wind-wounded.db", "second-wind-owner") as owner:
        _create_case(owner, campaign_id="wounded", hp=1, uses=2)
        result = _use(owner, "wounded")
        assert result.status is ActionStatus.COMMITTED, result.result
        roll = result.result["context"]["_second_wind_roll"]
        assert roll["modifier"] == 1
        assert result.result["context"]["_second_wind_healing"] == roll["total"] + roll["modifier"]
        assert _hero(owner, "wounded")["hp"] == 1 + roll["total"] + roll["modifier"]
        assert _hero(owner, "wounded")["resources"]["second_wind"] == 1


@pytest.mark.parametrize(
    ("campaign_id", "uses", "fighter"),
    (("empty", 0, True), ("nonfighter", 2, False)),
)
def test_second_wind_rejection_leaves_state_unchanged(
    tmp_path: Path, campaign_id: str, uses: int, fighter: bool
) -> None:
    with AuthorityOwner.open(tmp_path / f"{campaign_id}.db", "second-wind-owner") as owner:
        _create_case(owner, campaign_id=campaign_id, hp=20, uses=uses, fighter=fighter)
        before = owner.snapshot(campaign_id)
        with pytest.raises(MechanicsRejected) as rejected:
            _use(owner, campaign_id)
        assert rejected.value.code == "ruleset_precondition_failed"
        after = owner.snapshot(campaign_id)
        assert after.revision == before.revision == 0
        assert after.state == before.state


def test_second_wind_combat_spends_bonus_action_and_charge_atomically(tmp_path: Path) -> None:
    with AuthorityOwner.open(tmp_path / "second-wind-combat.db", "second-wind-owner") as owner:
        _create_case(owner, campaign_id="combat", hp=20, uses=2, combat=True)
        result = _use(owner, "combat")
        assert result.status is ActionStatus.COMMITTED, result.result
        hero = _hero(owner, "combat")
        assert hero["bonus_action_available"] is False
        assert hero["last_bonus_action"] == "second_wind"
        assert hero["resources"]["second_wind"] == 1


def test_second_wind_combat_bonus_action_rejection_is_atomic(tmp_path: Path) -> None:
    with AuthorityOwner.open(
        tmp_path / "second-wind-combat-rejected.db", "second-wind-owner"
    ) as owner:
        _create_case(
            owner,
            campaign_id="combat-rejected",
            hp=20,
            uses=2,
            combat=True,
            bonus_action_available=False,
        )
        before = owner.snapshot("combat-rejected")
        with pytest.raises(MechanicsRejected, match="already spent its Bonus Action"):
            _use(owner, "combat-rejected")
        after = owner.snapshot("combat-rejected")
        assert after.revision == before.revision
        assert after.state == before.state
