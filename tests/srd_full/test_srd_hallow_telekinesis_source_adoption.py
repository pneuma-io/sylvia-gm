"""Permanent wards and finite manipulation retain genuine cast identity through removals."""

import os
from copy import deepcopy
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.srd_spell_removals import extend_runtime as removal
from scripts.srd_full.srd_spell_source_instances import extend_runtime as source

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_lifetime_source_adoption as lifetime
from tests import test_srd_public_spell_casting as public_casting
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_npc_spell_casting import _act

budget = lifetime.budget
SRD = lifetime.SRD


@pytest.fixture(autouse=True)
def _compiled_owner(monkeypatch):
    yield from lifetime._compiled_owner.__wrapped__(monkeypatch)


def _case(spell="hallow"):
    state, data, params = lifetime._case(spell)
    if spell == "hallow":
        source_state, data, params = lifetime.cards._spell_case(spell)
        state[f"{SRD}.entities"]["hero"] = source_state[f"{SRD}.entities"]["hero"]
        state[f"{SRD}.entities"]["hero"]["creature_type"] = "humanoid"
    state[f"{SRD}.entities"]["hero"]["prepared_spells"].append("dispel_magic")
    data["spellcasting_grants"][0]["spells"].append("dispel_magic")
    if "wizard_spellbook" in data:
        data["wizard_spellbook"].append("dispel_magic")
    if spell == "hallow":
        params.update(
            hallow_origin=[0, 0, 0], hallow_radius=10, hallow_extra="courage",
            selected_damage_type="radiant",
        )
    else:
        params.update(telekinesis_subject="guard", telekinesis_destination=[5, 10, 0])
    return state, data, params


def _records(state, spell):
    lasting = state[f"{SRD}.state"]
    return (
        list(lasting["spell_hallow_sources"].values())
        if spell == "hallow"
        else lasting["telekinesis_sources"]
    )


def _cast(state, data, params):
    if params["spell"] == "hallow":
        before = deepcopy(state[f"{SRD}.entities"]["hero"]["spell_materials"])
        serial = state[f"{SRD}.state"].get("spell_source_serial")
        budget._run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
        assert state[f"{SRD}.state"].get("spell_source_serial") == serial
        budget._run(
            "continue_srd_spell_cast",
            state,
            caster_state="hero",
            elapsed_rounds=14400,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert state[f"{SRD}.entities"]["hero"]["spell_materials"] == before
    return budget._run("cast_spell_outside_combat", state, participant_data=data, **params)


def _dispel_params(params, spell, source_id, *, slot=5, ordinary=False, target="guard"):
    choices = (
        {
            "recovery_choice": "hallow",
            "recovery_source_id": source_id,
            "recovery_position": [0, 0, 0],
        }
        if spell == "hallow"
        else {
            "recovery_choice": "none" if ordinary else "telekinesis",
            "recovery_source_id": source_id,
            "target": target,
        }
    )
    return (
        params
        | {"spell": "dispel_magic", "slot_resource": f"spell_slot_{slot}", "material_item_ids": []}
        | choices
    )


def test_paid_hallow_permanent_source_is_dispellable_and_cannot_be_freely_dismissed():
    state, data, params = _case()
    supplies = deepcopy(state[f"{SRD}.entities"]["hero"]["spell_materials"])
    _cast(state, data, params)
    saved = _records(state, "hallow")[0]
    assert saved["cast_id"] == "srd_cast:1" and saved["source_caster_id"] == "hero"
    assert saved["permanent"] is True and saved["ongoing_duration"] is False
    assert saved["spell_level"] == 5 and saved["dispel_ongoing"] is True
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["resources"]["spell_slot_5"] == 2
    assert all(
        after["quantity"] == before["quantity"] - 1
        for after, before in zip(hero["spell_materials"], supplies, strict=True)
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="active supported cast"):
        budget._run("dismiss_srd_spell", state, caster_state="hero", cast_id=saved["cast_id"])
    assert state == before
    sid = state[f"{SRD}.state"]["spell_hallow_source_ids"][-1]
    ended = budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **_dispel_params(params, "hallow", sid),
    )
    assert any(
        row["label"] == "Selected source ended" and row["value"] is True
        for row in ended.result["result_card"]["summary"]
    )
    assert _records(state, "hallow")[0]["installed"] is False


def test_hallow_actual_failed_dispel_pays_and_wrong_area_or_creature_does_not_remove(monkeypatch):
    state, data, params = _case()
    _cast(state, data, params)
    sid = state[f"{SRD}.state"]["spell_hallow_source_ids"][-1]
    monkeypatch.setattr(dice, "_rng", Random(2))
    failed = budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **_dispel_params(params, "hallow", sid, slot=3),
    )
    assert any(
        row["label"] == "Selected source ended" and row["value"] is False
        for row in failed.result["result_card"]["summary"]
    )
    assert "d20 2;" in str(failed.result["result_card"])
    assert "DC 15" in str(failed.result["result_card"])
    assert _records(state, "hallow")[0]["installed"] is True
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
    bad = _dispel_params(params, "hallow", sid) | {"recovery_position": [20, 0, 0]}
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="actual saved hallow"):
        budget._run("cast_spell_outside_combat", state, participant_data=data, **bad)
    assert state == before
    ordinary = _dispel_params(params, "hallow", sid) | {"recovery_choice": "none"}
    budget._run("cast_spell_outside_combat", state, participant_data=data, **ordinary)
    assert _records(state, "hallow")[0]["installed"] is True


@pytest.mark.parametrize("ordinary,target", [(False, "guard"), (True, "guard"), (True, "hero")])
def test_actual_telekinesis_ends_once_from_real_recipient_or_caster(monkeypatch, ordinary, target):
    state, data, params = _case("telekinesis")
    monkeypatch.setattr(dice, "_rng", Random(2))
    _cast(state, data, params)
    saved = _records(state, "telekinesis")[0]
    assert saved["cast_id"] == "srd_cast:1" and saved["source_caster_id"] == "hero"
    assert saved["ongoing_duration"] is True and saved["spell_level"] == 5
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
    linked = state[f"{SRD}.state"]["timed_condition_sources"][saved["restraint_id"]]
    assert linked["dispel_ongoing"] is False
    result = budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **_dispel_params(params, "telekinesis", saved["id"], ordinary=ordinary, target=target),
    )
    assert sum(
        str(row["value"]).count(saved["id"])
        for row in result.result["result_card"]["details"]
    ) == 1
    assert _records(state, "telekinesis")[0]["active"] is False
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    budget._run("prepare_srd_telekinesis_releases", state)
    assert "restrained" not in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_elevated_telekinesis_dismissal_queues_release_without_fabricating_descent(monkeypatch):
    state, data, params = _case("telekinesis")
    params["telekinesis_destination"] = [5, 10, 20]
    monkeypatch.setattr(dice, "_rng", Random(2))
    _cast(state, data, params)
    saved = _records(state, "telekinesis")[0]
    hp = state[f"{SRD}.entities"]["guard"]["hp"]
    budget._run("dismiss_srd_spell", state, caster_state="hero", cast_id=saved["cast_id"])
    assert _records(state, "telekinesis")[0]["active"] is False
    prepared = budget._run("prepare_srd_telekinesis_releases", state)
    assert prepared.result["context"]["_tk_release_targets"] == ["guard"]
    lasting = state[f"{SRD}.state"]
    assert lasting["has_telekinesis_pending_releases"] is True
    release = lasting["telekinesis_releases"][0]
    assert release["source_id"] == saved["id"] and release["processed"] is False
    assert state[f"{SRD}.entities"]["guard"]["hp"] == hp
    body = next(e for e in lasting["spatial_scene"]["entities"] if e["id"] == "guard")
    assert body["position"] == [5, 10, 20]


def test_independent_paid_hallow_areas_keep_distinct_casts_and_selected_retirement():
    state, data, params = _case()
    for material in state[f"{SRD}.entities"]["hero"]["spell_materials"]:
        material["quantity"] = 2
    _cast(state, data, params)
    first = deepcopy(_records(state, "hallow")[0])
    first_id = state[f"{SRD}.state"]["spell_hallow_source_ids"][-1]
    budget._run("move_srd_scene_creature", state, actor_state="hero", destination=[25, 0, 0])
    _cast(state, data, params | {"hallow_origin": [25, 0, 0]})
    second = deepcopy(_records(state, "hallow")[1])
    assert {first["cast_id"], second["cast_id"]} == {"srd_cast:1", "srd_cast:2"}
    budget._run(
        "cast_spell_outside_combat", state, participant_data=data,
        **_dispel_params(params, "hallow", first_id),
    )
    assert [record["installed"] for record in _records(state, "hallow")] == [False, True]


def test_emitter_repeat_preserves_exact_source_and_retirement_semantics():
    text = (budget.ROOT / "runtime/ruleset.yaml").read_text()
    first = removal(source(text))
    assert yaml.load(first, Loader=yaml.CSafeLoader) == yaml.load(
        removal(source(first)), Loader=yaml.CSafeLoader
    )


@pytest.mark.parametrize("spell", ["hallow", "telekinesis"])
def test_public_paid_source_reopen_exact_removal_and_retry(tmp_path, monkeypatch, spell):
    parent = os.environ.get("FC14_SOURCE_PACKAGE_ROOT")
    if os.environ.get("FC14_SOURCE_RUNTIME_ROOT"):
        assert parent
        monkeypatch.setattr(bundled_packages, "_bundled_srd_root", lambda: Path(parent))
        monkeypatch.setattr(public_casting, "bundled_srd_archive", lifetime._composed_archive)
    monkeypatch.setattr(public_casting, "_spell_case", _case)
    path = tmp_path / f"{spell}-removal.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(owner, spell)
        if spell == "hallow":
            begin, command = _act(owner, "begin_srd_spell_cast", **_begin(params))
            assert begin.status is ActionStatus.COMMITTED, begin.result
            assert owner.execute_action(command) == begin
            continued, _ = _act(
                owner,
                "continue_srd_spell_cast",
                caster_state="hero",
                elapsed_rounds=14400,
                uninterrupted=True,
                verbal_component_available=True,
                somatic_component_available=True,
                material_component_available=True,
            )
            assert continued.status is ActionStatus.COMMITTED, continued.result
        monkeypatch.setattr(dice, "_rng", Random(2))
        cast, cast_command = _act(owner, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED, cast.result
        saved = owner.snapshot("casting")
        assert owner.execute_action(cast_command) == cast
        assert owner.snapshot("casting") == saved
        source_record = _records(saved.state, spell)[0]
        sid = (
            saved.state[f"{SRD}.state"]["spell_hallow_source_ids"][0]
            if spell == "hallow"
            else source_record["id"]
        )
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        if spell == "hallow":
            tool = "cast_spell_outside_combat"
            args = _dispel_params(params, spell, sid)
        else:
            tool = "dismiss_srd_spell"
            args = {"caster_state": "hero", "cast_id": source_record["cast_id"]}
        removed, command = _act(owner, tool, **args)
        assert removed.status is ActionStatus.COMMITTED, removed.result
        ended = owner.snapshot("casting")
        assert (
            _records(ended.state, spell)[0]["installed" if spell == "hallow" else "active"] is False
        )
        assert owner.execute_action(command) == removed and owner.snapshot("casting") == ended
        assert owner.execute_action(cast_command) == cast and owner.snapshot("casting") == ended
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == ended
