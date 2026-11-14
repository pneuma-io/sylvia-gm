"""Spell families preserve source-qualified protection through public saved play."""

import pytest

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from tests.srd_full.test_srd_npc_spell_casting import _act, _prepare_mage
from tests.srd_full.test_srd_public_spell_casting import _create_spell_campaign
from tests.srd_full.test_srd_spell_result_cards import SRD, _spell_case


@pytest.mark.parametrize("source_type,blocked", [("fey", True), ("humanoid", False)])
def test_public_protection_checks_charm_source_then_expires_after_reopen(
    tmp_path, source_type, blocked,
):
    database = tmp_path / "protected-control.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, protection = _create_spell_campaign(
            owner, "protection_from_evil_and_good",
            target_overrides={"creature_type": "humanoid", "saving_throw_modifiers": {
                "strength": 0, "dexterity": 0, "constitution": 0,
                "intelligence": 0, "wisdom": -100, "charisma": 0,
            }},
        )
        prepared, _ = _prepare_mage(
            owner, [{"spell": "charm_monster", "uses_per_day": 2}],
            creature_type=source_type,
        )
        assert prepared.status is ActionStatus.COMMITTED, prepared.result
        ward, _ = _act(owner, "cast_spell_outside_combat", **protection)
        assert ward.status is ActionStatus.COMMITTED, ward.result
        _, _, charm = _spell_case("charm_monster")
        charm.pop("caster")
        charm.pop("slot_resource")
        charm.update(caster_state="mage", targets=["guard"])
        attempted, command = _act(owner, "cast_spell_outside_combat", **charm)
        assert attempted.status is ActionStatus.COMMITTED, attempted.result
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["mage"]["creature_type"] == source_type
        assert ("charmed" in saved.state[f"{SRD}.entities"]["guard"]["conditions"]) is not blocked
        assert saved.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["charm_monster"] == 1

    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert StatelessMcpMechanics(owner).execute_action(command) == attempted
        assert owner.snapshot("casting").state == saved.state
        elapsed, _ = _act(owner, "advance_srd_lasting_time", elapsed_rounds=101)
        assert elapsed.status is ActionStatus.COMMITTED, elapsed.result
        later, _ = _act(owner, "cast_spell_outside_combat", **charm)
        assert later.status is ActionStatus.COMMITTED, later.result
        after = owner.snapshot("casting").state[f"{SRD}.entities"]
        assert "charmed" in after["guard"]["conditions"]
        assert after["mage"]["spell_uses_remaining"]["charm_monster"] == 0
