"""The admitted Lite public spell route retains one typed damage packet."""

from dataclasses import replace

import pytest

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import MechanicsRejected
from tests.test_srd_action_budget import SRD
from tests.test_srd_public_spell_casting import _create_spell_campaign


def test_public_flame_strike_packet_survives_refusal_retry_and_reopen(tmp_path):
    database = tmp_path / "composite-spell.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        owner.set_admin_full_audit(True)
        _, _, parameters = _create_spell_campaign(
            owner,
            "flame_strike",
            target_overrides={
                "hp": 1000,
                "max_hp": 1000,
                "temp_hp": 5,
                "saving_throw_modifiers": {"dexterity": -100},
                "damage_immunities": ["fire"],
                "damage_resistances": ["radiant"],
                "damage_vulnerabilities": ["radiant"],
            },
        )
        command = ActionCommand(
            campaign_id="casting",
            action_id="flame-strike",
            idempotency_key="flame-strike",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters | {"targets": ["guard"]},
        )
        mechanics = StatelessMcpMechanics(owner)
        before = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected):
            mechanics.execute_action(
                replace(
                    command,
                    action_id="out-of-range",
                    idempotency_key="out-of-range",
                    parameters=command.parameters | {"origin_distance": 61},
                )
            )
        assert owner.snapshot("casting") == before
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        guard = saved.state[f"{SRD}.entities"]["guard"]
        raw_radiant = receipt.result["context"]["_spell_component_2_damage"]["total"]
        damage = (raw_radiant // 2) * 2
        assert guard["hp"] == 1000 - max(0, damage - 5)
        assert guard["temp_hp"] == max(0, 5 - damage)
        assert guard["is_alive"] is True and guard["conditions"] == []
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
        assert saved.revision == 1 and len(saved.actions) == 1
        assert receipt.result["result_card"] is not None
        assert mechanics.execute_action(command) == receipt
        assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        owner.set_admin_full_audit(True)
        assert owner.snapshot("casting") == saved
        assert StatelessMcpMechanics(owner).execute_action(command) == receipt
        assert owner.snapshot("casting") == saved
