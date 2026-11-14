"""The joined package pays and persists the actual detection-mask casting identity."""

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from tests import test_srd_detection_masks as masks
from tests import test_srd_public_spell_casting as casting
from tests.srd_full.test_srd_action_budget import SRD
from tests.srd_full.test_srd_npc_spell_casting import _act


def test_joined_public_paid_mask_source_stealth_reopen_and_exact_cast_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(
        casting,
        "_spell_case",
        lambda spell: masks._case(spell, "hero", mask_chosen_creatures=["guard"]),
    )
    database = tmp_path / "joined-mask.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = casting._create_spell_campaign(owner, "pass_without_trace")
        before = owner.snapshot("casting")
        result, command = _act(owner, "cast_spell_outside_combat", **parameters)
        assert result.status is ActionStatus.COMMITTED, result.result
        saved = owner.snapshot("casting")
        record = masks._record(saved.state)
        caster = saved.state[f"{SRD}.entities"]["hero"]
        assert record["cast_id"] == caster["last_spell_source_cast_id"]
        assert record["cast_id"].startswith("srd_cast:")
        assert record["source_caster_id"] == "hero"
        assert saved.state[f"{SRD}.state"]["spell_source_serial"] == 1
        assert saved.state != before.state
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting") == saved
        assert reopened.execute_action(command) == result
        assert reopened.snapshot("casting") == saved
        roll, roll_command = _act(
            reopened,
            "d20_test",
            actor_state="guard",
            kind="ability_check",
            ability="dexterity",
            skill="stealth",
            ability_modifier=0,
            proficiency_bonus=0,
            proficiency_factor="none",
            circumstantial_modifier=0,
            target_number=10,
        )
        assert roll.status is ActionStatus.COMMITTED, roll.result
        assert roll.result["context"]["_combined_modifier"] == 10
        after_roll = reopened.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting") == after_roll
        assert reopened.execute_action(roll_command) == roll
        assert reopened.snapshot("casting") == after_roll
