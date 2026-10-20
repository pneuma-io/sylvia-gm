"""Replay the native Monk proposal against the installed Lite engine and confirmation."""

from dataclasses import replace

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import AwaitingChoiceReceipt
from sylvia_gm.domain.errors import CommandRejected
from tests.test_srd_advancement import award, build, command, create, resolve
from tests.test_srd_second_wind import PACKAGE_ID


@pytest.mark.parametrize("option", ["apply", "cancel"])
def test_monk_confirmation_uses_computed_speed_and_preserves_base_speed(tmp_path, option):
    with AuthorityOwner.open(tmp_path / "monk.db", owner_id="test") as owner:
        create(
            owner,
            participant_overrides={
                "name": "Rowan QA",
                "classes": [{"id": "monk", "level": 1}],
                "abilities": {
                    "strength": 12,
                    "dexterity": 17,
                    "constitution": 14,
                    "intelligence": 10,
                    "wisdom": 14,
                    "charisma": 8,
                },
            },
            entity_overrides={
                "hp": 10,
                "max_hp": 10,
                "speed": 30,
                "armor_class": 15,
                "fighter_level": 0,
                "constitution_modifier": 2,
                "inventory": ["shield"],
            },
        )
        award(owner)
        before = owner.snapshot("advancement")
        proposal = build(owner)
        proposal.update(class_id="monk", choices_summary="Monk 2; resulting Speed 40.")
        proposal["build"][0].update(
            armor_class=15,
            speed=40,
            resources=[{"id": "focus_point", "maximum": 2, "source": "Monk 2"}],
        )
        request = command(owner, "level_up_srd_character", proposal, "level")
        waiting = owner.execute_action(request)
        assert isinstance(waiting, AwaitingChoiceReceipt)
        assert "Walking Speed: 40" in waiting.choice.prompt
        assert owner.snapshot("advancement").state == before.state
        receipt = resolve(owner, waiting, option)
        after = owner.snapshot("advancement")
        if option == "cancel":
            assert after.state == before.state
            assert after.participants == before.participants
        else:
            assert after.participants[0].data["speed"] == 30
            assert after.participants[0].data["classes"] == [{"id": "monk", "level": 2}]
            entity = after.state[f"{PACKAGE_ID}.entities"]["hero"]
            assert entity["speed"] == 40
            assert entity["max_hp"] == 17 and entity["hp"] == 10
            assert entity["resources"]["focus_point"] == 2
            assert owner.execute_action(request) == receipt
            for identity, item, speed in (
                (
                    "wear-shield",
                    [{"inventory_item": "shield", "armor_type": "shield", "magic_bonus": 0}],
                    30,
                ),
                ("remove-shield", [], 40),
                (
                    "wear-shield-again",
                    [{"inventory_item": "shield", "armor_type": "shield", "magic_bonus": 0}],
                    30,
                ),
                ("remove-shield-again", [], 40),
            ):
                equipment = replace(
                    command(owner, "change_srd_equipment", {}, identity),
                    parameters={
                        "actor": "hero",
                        "actor_state": "hero",
                        "slot": "shield",
                        "item": item,
                    },
                )
                owner.execute_action(equipment)
                saved = owner.snapshot("advancement")
                assert saved.participants[0].data["speed"] == 30
                assert saved.state[f"{PACKAGE_ID}.entities"]["hero"]["speed"] == speed


def test_monk_proposal_rejects_wrong_resulting_speed_without_changes(tmp_path):
    with AuthorityOwner.open(tmp_path / "bad-monk.db", owner_id="test") as owner:
        create(
            owner,
            participant_overrides={"classes": [{"id": "monk", "level": 1}]},
            entity_overrides={"speed": 30, "fighter_level": 0},
        )
        award(owner)
        before = owner.snapshot("advancement")
        proposal = build(owner)
        proposal["class_id"] = "monk"
        with pytest.raises(CommandRejected, match="Speed|speed"):
            owner.execute_action(command(owner, "level_up_srd_character", proposal, "wrong"))
        after = owner.snapshot("advancement")
        assert after.state == before.state and after.participants == before.participants
