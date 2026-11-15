"""Ordinary information casts use admitted facts without requiring grid coordinates."""

from copy import deepcopy

import pytest
import yaml
from scripts.srd_full.srd_divination_spells import _normalize
from scripts.srd_full.srd_information_spells import _casting_tools

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import MechanicsRejected
from tests import test_srd_information_self_casting as self_casting
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_wave3_information as information
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act


@pytest.fixture(autouse=True)
def _authored(monkeypatch):
    runtime, _ = self_casting._runtime()
    runtime = deepcopy(runtime)
    changed = _casting_tools()
    _normalize(changed)
    runtime["tools"].update(changed)
    content = yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode()
    monkeypatch.setattr(self_casting, "_runtime", lambda: (runtime, content))
    self_casting._mechanic.cache_clear()
    self_casting._authored.__wrapped__(monkeypatch)
    yield
    self_casting._mechanic.cache_clear()


def test_paid_self_detection_cast_needs_no_spatial_scene():
    state, data, parameters = information._case("detect_magic")
    state[f"{SRD}.state"].pop("spatial_scene")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    parameters.pop("target")
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    caster = state[f"{SRD}.entities"]["hero"]
    assert caster["resources"]["spell_slot_1"] == 2
    assert caster["concentration_spell_id"] == "detect_magic"
    record = next(
        row for row in state[f"{SRD}.state"]["turn_effects"] if row["spell"] == "detect_magic"
    )
    assert record["origin"] is None and record["plane"] is None


def test_native_scene_still_rejects_out_of_reach_tongues():
    information.test_tongues_cannot_cast_using_a_forged_contact_fact("distance")


def test_paid_tongues_contact_fact_rejects_then_casts_reopens_and_retries(tmp_path, monkeypatch):
    from sylvia_gm.application import bundled_packages
    from tests import test_srd_spell_appearance as appearance
    from tests.srd_full.test_srd_dream_spell import _linked_drafts

    monkeypatch.setattr(bundled_packages, "_srd_drafts", _linked_drafts)
    monkeypatch.setattr(appearance, "_runtime", self_casting._runtime)
    monkeypatch.setattr(appearance, "PUBLIC_TOOLS", set())
    appearance._appearance_archive.cache_clear()
    monkeypatch.setattr(public, "bundled_srd_archive", appearance._appearance_archive)
    actual_case = information._case

    def without_coordinates(spell):
        state, data, parameters = actual_case(spell)
        state[f"{SRD}.state"].pop("spatial_scene")
        return state, data, parameters

    monkeypatch.setattr(public, "_spell_case", without_coordinates)
    database = tmp_path / "tongues-facts.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, "tongues")
        before = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected, match="touch"):
            _act(
                owner,
                "cast_spell_outside_combat",
                **(parameters | {"caster_can_touch_target": False}),
            )
        assert owner.snapshot("casting").state == before.state
        receipt, command = _act(owner, "cast_spell_outside_combat", **parameters)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
        record = next(
            row for row in saved.state[f"{SRD}.state"]["turn_effects"] if row["spell"] == "tongues"
        )
        assert record["caster"] == "hero" and record["target"] == "guard"
        assert StatelessMcpMechanics(owner).execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        mechanics = StatelessMcpMechanics(reopened)
        assert reopened.snapshot("casting").state == saved.state
        assert mechanics.execute_action(command) == receipt
        consequence, later_command = _act(
            reopened, "communicate_srd_spell", listener_state="guard", speaker_state="hero"
        )
        assert consequence.status is ActionStatus.COMMITTED
        assert information._summary(consequence)["Understands"] is True
        after = reopened.snapshot("casting")
        assert mechanics.execute_action(later_command) == consequence
        assert reopened.snapshot("casting").state == after.state
