"""Actual lifetime sources retain paid cast identity through exact owning removals."""

import json
import os
from copy import deepcopy
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_spell_removals import extend_runtime as removals
from scripts.srd_full.srd_spell_source_instances import extend_runtime as sources

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public_casting
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_illusion_removals import _prepare
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_shared_source_adoption import _case as _source_case
from tests.srd_full.test_srd_shared_source_adoption import _scene_row
from tests.srd_full.test_srd_spell_wave3_information import _wall

SRD = cards.SRD


@pytest.fixture(autouse=True)
def _compiled_owner(monkeypatch):
    root = os.environ.get("FC14_SOURCE_RUNTIME_ROOT")
    if root:
        monkeypatch.setattr(budget, "ROOT", Path(root))
    budget._mechanic.cache_clear()
    monkeypatch.setattr(
        cards,
        "_spell_definitions",
        lambda: budget._runtime_definition((budget.ROOT / "runtime/ruleset.yaml").read_bytes())[
            "definitions"
        ]["spells"],
    )
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    yield
    budget._mechanic.cache_clear()


def _case(spell="dream", *, slot=None):
    state, data, params = _source_case(spell, slot)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    _scene_row(state, "guard")["metadata"]["known_by"] = ["hero"]
    scene["geometry"].append(
        {
            "id": "ground",
            "map_id": "information",
            "volume": {"kind": "box", "min": [-500, -500, -1], "max": [500, 500, 0]},
            "active": True,
            "movement": "blocking",
            "occupies_cells": True,
            "visibility": "opaque",
            "metadata": {"plane": "material", "ground": True},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
        }
    )
    if spell == "dream":
        params.update(target="guard", dream_messenger="guard", dream_messenger_willing=True)
    if spell in {"magic_circle", "private_sanctum"}:
        params.update(
            secure_area_origin=[0, 0, 0],
            secure_area_types=["fiend"] if spell == "magic_circle" else [],
            secure_area_security=["divination_targets"] if spell == "private_sanctum" else [],
            caster_can_see_origin=True,
            caster_can_touch_origin=True,
        )
    if spell == "levitate":
        _scene_row(state, "guard")["position"] = [20, 0, 0]
        params.update(target="guard", levitate_subject="guard", all_targets_willing=True)
    if spell == "passwall":
        scene["geometry"].append(_wall(5))
        _scene_row(state, "guard")["position"] = [40, 0, 0]
        _scene_row(state, "orb")["position"] = [45, 0, 0]
        params.update(
            passwall_surface_id="wall",
            travel_destination=[10, 0, 0],
            passwall_face="west",
            passwall_depth=5,
        )
    return state, data, params


def _cast(state, data, params):
    state[f"{SRD}.entities"]["hero"]["spell_slot_cast_this_turn"] = False
    rounds = {"dream": 10, "magic_circle": 10, "private_sanctum": 100}.get(params["spell"])
    if rounds:
        budget._run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
        budget._run(
            "continue_srd_spell_cast",
            state,
            caster_state="hero",
            elapsed_rounds=rounds,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
    return budget._run("cast_spell_outside_combat", state, participant_data=data, **params)


def _records(state, spell):
    lasting = state[f"{SRD}.state"]
    pool = {
        "dream": "dream_spell_sources",
        "magic_circle": "spell_secure_area_sources",
        "private_sanctum": "spell_secure_area_sources",
        "forbiddance": "spell_secure_area_sources",
        "levitate": "levitate_sources",
        "passwall": "passwall_sources",
    }[spell]
    saved = lasting.get(pool, {})
    candidates = (
        [source | {"id": source.get("id", identity)} for identity, source in saved.items()]
        if isinstance(saved, dict)
        else saved
    )
    return [
        source
        for source in candidates
        if source.get("spell", source.get("spell_id")) == spell
        and source.get("active", source.get("installed"))
    ]


def _dispel(state, data, params, *, slot=9, **choices):
    params = (
        params
        | {
            "spell": "dispel_magic",
            "slot_resource": f"spell_slot_{slot}",
            "material_item_ids": _prepare(state, data, "dispel_magic"),
        }
        | choices
    )
    return _cast(state, data, params)


@pytest.mark.parametrize(
    "spell", ["dream", "magic_circle", "private_sanctum", "levitate", "passwall"]
)
def test_paid_cast_then_exact_no_action_dismissal_uses_owning_lifetime(spell):
    state, data, params = _case(spell)
    before = state[f"{SRD}.entities"]["hero"]["resources"][params["slot_resource"]]
    _cast(state, data, params)
    record = _records(state, spell)[0]
    assert record["cast_id"] == "srd_cast:1" and record["source_caster_id"] == "hero"
    assert record["spell_level"] == cards._casting_headers()[spell]["level"]
    assert record["ongoing_duration"] is True and record["dispel_ongoing"] is True
    assert state[f"{SRD}.entities"]["hero"]["resources"][params["slot_resource"]] == before - 1
    resources = deepcopy(state[f"{SRD}.entities"]["hero"]["resources"])
    budget._run("dismiss_srd_spell", state, caster_state="hero", cast_id=record["cast_id"])
    if spell == "levitate":
        budget._run("settle_srd_levitate_returns", state)
        assert _scene_row(state, "guard")["position"][2] == 0
    assert not _records(state, spell)
    assert state[f"{SRD}.entities"]["hero"]["resources"] == resources
    if spell == "dream":
        assert state[f"{SRD}.entities"]["guard"]["dream_trance_speed_zero"] is False
        assert "incapacitated" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    if spell == "passwall":
        assert (
            next(
                row
                for row in state[f"{SRD}.state"]["spatial_scene"]["geometry"]
                if row["id"] == "wall"
            )["active"]
            is True
        )


@pytest.mark.parametrize("spell", ["dream", "levitate"])
def test_creature_dispel_ends_actual_recipient_source_instead_of_bookkeeping(spell):
    state, data, params = _case(spell)
    _cast(state, data, params)
    _dispel(state, data, params, target="hero")
    assert _records(state, spell)
    result = _dispel(state, data, params, target="guard")
    if spell == "levitate":
        budget._run("settle_srd_levitate_returns", state)
    assert not _records(state, spell)
    assert "Actual source checks" in str(result.result["result_card"])


@pytest.mark.parametrize(
    "spell,choice", [("private_sanctum", "secure_area"), ("passwall", "passwall")]
)
def test_selected_area_dispel_preserves_real_source_geometry(spell, choice):
    state, data, params = _case(spell)
    _cast(state, data, params)
    source = _records(state, spell)[0]
    result = _dispel(
        state,
        data,
        params,
        recovery_choice=choice,
        recovery_source_id=source["id"],
        recovery_position=[10, 0, 0] if spell == "passwall" else [0, 0, 0],
    )
    assert not _records(state, spell)
    assert "Selected source ended" in str(result.result["result_card"])


@pytest.mark.parametrize("seed,ended", [(0, False), (5, True)])
def test_actual_lower_slot_failure_commits_cost_and_retains_failed_roll(monkeypatch, seed, ended):
    state, data, params = _case("private_sanctum", slot=9)
    _cast(state, data, params)
    source = _records(state, "private_sanctum")[0]
    monkeypatch.setattr(dice, "_rng", Random(seed))
    result = _dispel(
        state,
        data,
        params,
        slot=3,
        recovery_choice="secure_area",
        recovery_source_id=source["id"],
        recovery_position=[0, 0, 0],
    )
    assert bool(_records(state, "private_sanctum")) is not ended
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
    detail = next(
        row
        for row in result.result["result_card"]["details"]
        if row["label"] == "Actual Dispel check"
    )
    assert "DC 19" in detail["value"]
    assert f"selected {20 if ended else 13}" in detail["value"]


def test_dream_saved_independent_cast_keeps_other_trance_after_exact_removal():
    state, data, params = _case()
    _cast(state, data, params)
    _cast(state, data, params)
    assert {s["cast_id"] for s in _records(state, "dream")} == {"srd_cast:1", "srd_cast:2"}
    reopened = json.loads(json.dumps(state))
    budget._run("dismiss_srd_spell", reopened, caster_state="hero", cast_id="srd_cast:1")
    assert {s["cast_id"] for s in _records(reopened, "dream")} == {"srd_cast:2"}
    assert "incapacitated" in reopened[f"{SRD}.entities"]["guard"]["conditions"]
    before = deepcopy(reopened)
    with pytest.raises(RulesetPreconditionRejected):
        budget._run("dismiss_srd_spell", reopened, caster_state="hero", cast_id="srd_cast:1")
    assert reopened == before


def test_emission_preserves_actual_cast_and_removal_structure_on_repeat():
    text = (budget.ROOT / "runtime/ruleset.yaml").read_text(encoding="utf-8")
    first = removals(sources(text))
    second = removals(sources(first))
    assert yaml.load(first, Loader=yaml.CSafeLoader) == yaml.load(second, Loader=yaml.CSafeLoader)


def _private_install(state, spell, **identity):
    level = cards._casting_headers()[spell]["level"]
    params = {"caster_state": "hero", "slot_level": level} | identity
    if spell in {"magic_circle", "forbiddance", "private_sanctum"}:
        return budget._run(
            "cast_srd_spell_secure_area",
            state,
            **params,
            spell=spell,
            save_dc=15,
            origin=[0, 0, 0],
            chosen_types=[] if spell == "private_sanctum" else ["fiend"],
            security=["teleport"] if spell == "private_sanctum" else [],
            caster_can_see_origin=True,
            caster_can_touch_origin=True,
        )
    if spell == "dream":
        return budget._run(
            "cast_srd_dream_spell",
            state,
            **params,
            target_state="guard",
            save_dc=15,
            dream_messenger="guard",
            dream_messenger_willing=True,
        )
    if spell == "levitate":
        return budget._run(
            "install_srd_levitate",
            state,
            **params,
            subject="guard",
            save_dc=15,
            concentration_modifier=3,
            all_targets_willing=True,
        )
    return budget._run(
        "install_srd_passwall",
        state,
        **params,
        surface_id="wall",
        point=[10, 0, 0],
        face="west",
        depth=5,
    )


@pytest.mark.parametrize("spell", ["dream", "forbiddance", "levitate", "passwall"])
def test_source_guard_rejects_invented_identity_and_legacy_install_has_typed_absence(spell):
    state, _, _ = _case(spell)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="actual lifetime cast owner"):
        _private_install(
            state,
            spell,
            spell_cast_id="invented",
            spell_cast_owner="hero",
            actual_spell_level=cards._casting_headers()[spell]["level"],
        )
    assert state == before
    _private_install(state, spell)
    record = _records(state, spell)[0]
    assert record["cast_id"] == "" and record["source_caster_id"] is None
    assert record["ongoing_duration"] is False
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        budget._run("dismiss_srd_spell", state, caster_state="hero", cast_id="")
    assert state == before


def _composed_archive():
    """Admit the selected authentic spell fragment over the immutable public package."""
    primary, reference = bundled_packages._srd_drafts()
    content = (budget.ROOT / "runtime/ruleset.yaml").read_bytes()
    runtime = yaml.load(content, Loader=yaml.CSafeLoader)
    record = deepcopy(primary.record)
    record["tools"] = [d for d in record["tools"] if d["name"] in runtime["tools"]]
    for declaration in record["tools"]:
        declaration["input_schema"] = _tool_schema(
            runtime["tools"][declaration["name"]], tool_name=declaration["name"]
        )
    payloads = dict(primary.payloads) | {"runtime/ruleset.yaml": content}
    declarations = json.loads(payloads["content/content-declarations.json"])
    for inclusion in declarations["inclusions"]:
        links = inclusion.get("links", {})
        if "tools" in links:
            links["tools"] = [name for name in links["tools"] if name in runtime["tools"]]
    payloads["content/content-declarations.json"] = canonical_json_bytes(declarations)
    return bundled_packages._archive(
        (NormalizedPackageDraft(record, payloads, primary.format_version), reference)
    )


def test_public_paid_passage_reopen_exact_dismissal_and_retry(tmp_path, monkeypatch):
    package_parent = os.environ.get("FC14_SOURCE_PACKAGE_ROOT")
    if os.environ.get("FC14_SOURCE_RUNTIME_ROOT"):
        assert package_parent, "Select the immutable public package for composed archive proof."
        monkeypatch.setattr(bundled_packages, "_bundled_srd_root", lambda: Path(package_parent))
        monkeypatch.setattr(public_casting, "bundled_srd_archive", _composed_archive)
    monkeypatch.setattr(public_casting, "_spell_case", _case)
    database = tmp_path / "lifetime-adoption.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(owner, "passwall")
        cast, cast_command = _act(owner, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED, cast.result
        saved = owner.snapshot("casting")
        source = saved.state[f"{SRD}.state"]["passwall_sources"][-1]
        assert source["cast_id"] == "srd_cast:1" and source["source_caster_id"] == "hero"
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
        assert owner.execute_action(cast_command) == cast
        assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        dismissed, command = _act(
            owner, "dismiss_srd_spell", caster_state="hero", cast_id=source["cast_id"]
        )
        assert dismissed.status is ActionStatus.COMMITTED, dismissed.result
        settled = owner.snapshot("casting")
        assert not settled.state[f"{SRD}.state"]["passwall_sources"][-1]["active"]
        assert (
            next(
                g
                for g in settled.state[f"{SRD}.state"]["spatial_scene"]["geometry"]
                if g["id"] == "wall"
            )["active"]
            is True
        )
        assert settled.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
        assert owner.execute_action(command) == dismissed
        assert owner.execute_action(cast_command) == cast
        assert owner.snapshot("casting") == settled
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == settled
