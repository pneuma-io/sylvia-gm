"""Independent non-concentration information casts retain their exact later consumers."""

from copy import deepcopy

import pytest

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_wave3_information import SRD, _case, _free, _summary


def _two_tongues():
    state, data, params = _case("tongues")
    _free(state)
    _run("cast_spell", state, participant_data=data, **params)
    first = deepcopy(state[f"{SRD}.state"]["turn_effects"][-1])
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    params["target"] = "hero"
    _run("cast_spell", state, participant_data=data, **params)
    second = deepcopy(state[f"{SRD}.state"]["turn_effects"][-1])
    return state, first, second


def test_tongues_recast_on_another_target_preserves_both_language_consumers():
    state, first, second = _two_tongues()
    assert first["instance_id"] != second["instance_id"]
    assert {x["target"] for x in state[f"{SRD}.state"]["turn_effects"]} == {"hero", "guard"}
    for listener, speaker in (("guard", "hero"), ("hero", "guard")):
        assert (
            _summary(
                _run("communicate_srd_spell", state, listener_state=listener, speaker_state=speaker)
            )["Understands"]
            is True
        )


def test_tongues_expiry_removes_only_the_expired_target_source():
    state, first, second = _two_tongues()
    _run("advance_srd_lasting_time", state, elapsed_rounds=599)
    assert state[f"{SRD}.state"]["lasting_tick"] == first["expires_tick"]
    assert [x["instance_id"] for x in state[f"{SRD}.state"]["turn_effects"]] == [
        second["instance_id"]
    ]
    state[f"{SRD}.entities"]["hero"]["languages"] = []
    state[f"{SRD}.entities"]["other"]["languages"] = ["dwarvish"]
    assert (
        _summary(
            _run("communicate_srd_spell", state, listener_state="hero", speaker_state="other")
        )["Understands"]
        is True
    )
    with pytest.raises(RulesetPreconditionRejected, match="understand"):
        _run("communicate_srd_spell", state, listener_state="guard", speaker_state="other")


def test_tongues_explicit_source_dismissal_preserves_other_target_and_unrelated_source():
    state, first, second = _two_tongues()
    _run(
        "dismiss_srd_information_spell",
        state,
        caster_state="hero",
        spell="tongues",
        instance_id=first["instance_id"],
    )
    assert [x["instance_id"] for x in state[f"{SRD}.state"]["turn_effects"]] == [
        second["instance_id"]
    ]
    state[f"{SRD}.entities"]["hero"]["languages"] = []
    assert (
        _summary(
            _run("communicate_srd_spell", state, listener_state="hero", speaker_state="guard")
        )["Understands"]
        is True
    )


def test_legacy_information_record_expiry_cannot_delete_a_later_new_instance():
    state, first, second = _two_tongues()
    state[f"{SRD}.state"]["turn_effects"][0].pop("instance_id")
    _run("advance_srd_lasting_time", state, elapsed_rounds=599)
    assert [x["instance_id"] for x in state[f"{SRD}.state"]["turn_effects"]] == [
        second["instance_id"]
    ]


def test_independent_plants_sources_keep_both_areas_and_update_only_selected_cast():
    state, data, params = _case("speak_with_plants")
    _free(state)
    _run("cast_spell", state, participant_data=data, **params)
    first = state[f"{SRD}.state"]["turn_effects"][-1]["instance_id"]
    state[f"{SRD}.state"]["spatial_scene"]["entities"][0]["position"] = [100, 0, 0]
    _run("cast_spell", state, participant_data=data, **params)
    second = state[f"{SRD}.state"]["turn_effects"][-1]["instance_id"]
    _run(
        "command_srd_plants",
        state,
        caster_state="hero",
        command="difficult_terrain",
        instance_id=first,
    )
    records = {x["instance_id"]: x for x in state[f"{SRD}.state"]["turn_effects"]}
    assert records[first]["origin"] == [0, 0, 0]
    assert records[first]["terrain"] == "difficult_terrain"
    assert records[second]["origin"] == [100, 0, 0]
    assert records[second]["terrain"] == "unchanged"


def test_public_independent_instances_reopen_retry_and_dismiss_exact_source(tmp_path, monkeypatch):
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus
    from tests import test_srd_public_spell_casting as public
    from tests.srd_full.test_srd_npc_spell_casting import _act

    monkeypatch.setattr(public, "_spell_case", _case)
    database = tmp_path / "independent-information.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "tongues")
        first, first_command = _act(owner, "cast_spell_outside_combat", **params)
        assert first.status is ActionStatus.COMMITTED
        params["target"] = "hero"
        second, second_command = _act(owner, "cast_spell_outside_combat", **params)
        assert second.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        records = saved.state[f"{SRD}.state"]["turn_effects"]
        assert len(records) == 2
        first_id, second_id = [r["instance_id"] for r in records]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert mechanics.execute_action(first_command) == first
        assert mechanics.execute_action(second_command) == second
        advertised = mechanics.list_tools("casting")
        assert "dismiss_srd_information_spell" in {t.name for t in advertised}
        receipt, command = _act(
            owner,
            "dismiss_srd_information_spell",
            caster_state="hero",
            spell="tongues",
            instance_id=first_id,
        )
        assert receipt.status is ActionStatus.COMMITTED
        after = owner.snapshot("casting")
        assert [r["instance_id"] for r in after.state[f"{SRD}.state"]["turn_effects"]] == [
            second_id
        ]
        assert mechanics.execute_action(command) == receipt
        assert owner.snapshot("casting").state == after.state
        consequence, _ = _act(
            owner, "communicate_srd_spell", listener_state="hero", speaker_state="guard"
        )
        assert consequence.status is ActionStatus.COMMITTED
        assert {r["label"]: r["value"] for r in consequence.result["result_card"]["summary"]}[
            "Understands"
        ] is True
