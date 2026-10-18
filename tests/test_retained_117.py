"""Every preserved spell executes from actual final-117 admitted package bytes.

These are synthetic existing-caster effect checks. Complete character acquisition,
native Windows input and live providers are separate qualification layers.
"""

from dataclasses import replace
import io
from pathlib import Path

import pytest

from scripts.srd_lite_catalogue import RETAINED_FULL_AUTHORED_SPELLS
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.application.bundled_packages import bundled_srd_archive
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from tests.admitted_library_fixture import admitted_fixture_owner
from tests.test_srd_public_spell_casting import _create_spell_campaign
from tests.test_srd_spell_result_cards import SRD, _casting_headers

CHOICES = {
    "blindness_deafness": {"targets": ["guard"], "blinded_targets": ["guard"]},
    "enhance_ability": {"targets": ["guard"], "strength_targets": ["guard"]},
    "bane": {"targets": ["guard", "other"], "forego_save_targets": ["guard", "other"]},
    "chain_lightning": {"targets": ["guard", "other"]},
    "ice_knife": {"targets": ["guard", "other"]},
    "freezing_sphere": {"targets": ["guard", "other"], "all_targets_in_area": True},
    "meteor_swarm": {"targets": ["guard", "other"], "all_targets_in_area": True},
    "shatter": {"targets": ["guard", "other"], "all_targets_in_area": True},
    "thunderwave": {"targets": ["guard", "other"], "all_targets_in_area": True},
    "vitriolic_sphere": {"targets": ["guard", "other"], "all_targets_in_area": True},
}


@pytest.mark.parametrize("spell", sorted(RETAINED_FULL_AUTHORED_SPELLS))
def test_retained_spell_cast_receipt_slot_retry_and_reopen(tmp_path, spell):
    database = tmp_path / (spell + ".sqlite3")
    with admitted_fixture_owner(database, "casting-owner", bundled_srd_archive()) as (owner, _candidate):
        _, _, parameters = _create_spell_campaign(owner, spell,
            target_overrides={"hp": 1000, "max_hp": 1000,
                "abilities": dict.fromkeys(("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"), 10),
                "saving_throw_modifiers": dict.fromkeys(("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"), -100)})
        parameters.update(CHOICES.get(spell, {}))
        before = owner.snapshot("casting")
        command = ActionCommand(campaign_id="casting", action_id=spell, idempotency_key=spell,
            owner_id="casting-owner", writer=WriterRole.DM, expected_revision=0,
            turn_id="casting-turn", package_id=SRD, tool_name="cast_spell_outside_combat", parameters=parameters)
        receipt = owner.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        assert receipt.result["result_card"]["title"] == _casting_headers()[spell]["name"]
        saved = owner.snapshot("casting")
        slot = parameters["slot_resource"]
        if slot != "cantrip":
            assert saved.state[SRD + ".entities"]["hero"]["resources"][slot] == before.state[SRD + ".entities"]["hero"]["resources"][slot] - 1
        assert saved.state != before.state
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert owner.execute_action(command) == receipt
