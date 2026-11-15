"""Conjured Celestial/Fey use paid casts, actual Cylinder/body geometry and native rolls."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_result_cards as cards
from tests import test_srd_spell_travel_dwelling as dwelling
from tests import test_srd_spell_travel_telekinesis_fall as fall
from tests.srd_full.test_srd_spell_travel import SRD, _TravelDice, dice
from tests.srd_full.test_srd_spell_travel import _case as travel_case

actual_fall_scope = fall.actual_fall_scope


@pytest.fixture
def conjured_scope(monkeypatch):
    monkeypatch.setattr(fall, "RUNTIME_PATH", Path(".scratch/conjured-composition/runtime.yaml"))
    monkeypatch.setattr(
        cards, "_spell_definitions", lambda: fall._runtime()[0]["definitions"]["spells"]
    )

    original = bundled_packages._srd_drafts

    def drafts():
        primary, reference = original()
        record = deepcopy(primary.record)
        template = next(row for row in record["tools"] if row["name"] == "use_srd_telekinesis")
        declaration = deepcopy(template)
        declaration["name"] = "use_srd_conjured_fey"
        declaration["input_schema"] = _tool_schema(
            fall._runtime()[0]["tools"]["use_srd_conjured_fey"], tool_name="use_srd_conjured_fey"
        )
        record["tools"].append(declaration)
        payload = dict(primary.payloads)
        declarations = json.loads(payload["content/content-declarations.json"])
        inclusion = next(
            row
            for row in declarations["inclusions"]
            if "use_srd_telekinesis" in row.get("links", {}).get("tools", [])
        )
        inclusion["links"]["tools"] = sorted(
            set(inclusion["links"]["tools"]) | {"use_srd_conjured_fey"}
        )
        payload["content/content-declarations.json"] = canonical_json_bytes(declarations)
        return NormalizedPackageDraft(record, payload, primary.format_version), reference

    monkeypatch.setattr(bundled_packages, "_srd_drafts", drafts)


def _case(spell="conjure_celestial", **changes):
    state, data, params = travel_case(spell)
    params.pop("target", None)
    params.update(conjured_source_id="presence", conjured_point=[5, 0, 0])
    if spell == "conjure_celestial":
        params.update(celestial_healing_targets=["hero"], celestial_searing_targets=["guard"])
    else:
        params.update(
            conjured_point=[12, 0, 0],
            fey_height=5,
            fey_appearance="silver-winged Fey",
            fey_attack_target="guard",
        )
    params.update(changes)
    return state, data, params


def _cast(state, data, params):
    return budget._run("cast_spell_outside_combat", state, participant_data=data, **params)


def _source(state):
    return state[f"{SRD}.state"]["persistent_spell_areas"]["presence"]


@pytest.mark.parametrize("slot", [7, 9])
def test_paid_celestial_heals_and_actual_dex_save_halves_one_native_radiant_roll(
    conjured_scope, actual_fall_scope, monkeypatch, slot
):
    state, data, params = _case(slot_resource=f"spell_slot_{slot}")
    state[f"{SRD}.entities"]["hero"].update(hp=100)
    state[f"{SRD}.entities"]["guard"].update(temp_hp=0)
    game = _TravelDice([(12, 2)] * (6 + slot - 7) + [(12, 3)] * (4 + slot - 7) + [(20, 10)])
    monkeypatch.setattr(dice, "_rng", game)
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    _cast(state, data, params)
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 100 + (4 + slot - 7) * 3 + 4
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 500 - (6 + slot - 7)
    assert state[f"{SRD}.entities"]["hero"]["resources"][f"spell_slot_{slot}"] == 2
    source = _source(state)
    assert source["origin"] == [5, 0, 0] and source["cylinder_origin_face"] == "bottom"
    assert source["cylinder_min"] == 0 and source["cylinder_max"] == 40
    assert source["upcast"] == slot - 7 and source["expires_tick"] == 200
    assert not game.rolls


@pytest.mark.parametrize("defect", ["range", "height", "duplicate", "missing", "outside"])
def test_invalid_celestial_initial_intent_is_atomic(conjured_scope, actual_fall_scope, defect):
    changes = {
        "range": {"conjured_point": [91, 0, 20]},
        "height": {"conjured_point": [5, 0, 30000]},
        "duplicate": {"celestial_healing_targets": ["hero", "guard"]},
        "missing": {"celestial_healing_targets": []},
        "outside": {"celestial_searing_targets": ["other"]},
    }[defect]
    state, data, params = _case(**changes)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _cast(state, data, params)
    assert state == before


def test_fey_initial_attack_uses_actual_medium_anchor_native_roll_and_next_caster_boundary(
    conjured_scope, actual_fall_scope, monkeypatch
):
    state, data, params = _case("conjure_fey")
    state[f"{SRD}.entities"]["guard"].update(temp_hp=0)
    game = _TravelDice([(20, 10), (12, 2), (12, 2), (12, 2)])
    monkeypatch.setattr(dice, "_rng", game)
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    _cast(state, data, params)
    source = _source(state)
    body = next(
        b
        for b in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if b["id"] == source["subject_id"]
    )
    assert body["position"] == [12, 0, 0] and body["footprint"]["half_extents"] == [2.5, 2.5]
    assert body["metadata"]["is_object"] is False
    assert body["metadata"]["creature_type"] == "spell_force"
    assert source["height"] == 5 and source["upcast"] == 0
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 490
    assert "frightened" in state[f"{SRD}.entities"]["guard"]["conditions"]
    record = next(
        r
        for r in state[f"{SRD}.state"]["timed_condition_sources"].values()
        if r.get("fey_source_id")
    )
    assert record["fey_spirit_id"] == source["subject_id"] and record["fey_caster_next_start"] == 1
    assert not game.rolls


def test_fey_optional_no_attack_and_exact_source_end_retires_only_actual_anchor(
    conjured_scope, actual_fall_scope
):
    state, data, params = _case("conjure_fey", fey_attack_target="")
    _cast(state, data, params)
    hero = deepcopy(state[f"{SRD}.entities"]["hero"])
    assert "frightened" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    budget._run("end_concentration", state, caster_state="hero")
    assert not _source(state)["active"]
    body = next(
        b
        for b in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if b["id"] == "presence:spirit"
    )
    assert not body["active"]
    assert state[f"{SRD}.entities"]["hero"]["resources"] == hero["resources"]


@pytest.mark.parametrize("point", [[5, 0, 0], [61, 0, 0], [12, 0, 0]])
def test_fey_actual_occupied_or_out_of_range_or_unseen_intent_rejects_before_cost(
    conjured_scope, actual_fall_scope, point
):
    state, data, params = _case("conjure_fey", conjured_point=point)
    if point == [12, 0, 0]:
        state[f"{SRD}.entities"]["hero"]["conditions"].append("blinded")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _cast(state, data, params)
    assert state == before


def test_actual_next_caster_start_retires_hit_fear_then_later_bonus_moves_same_spirit(
    conjured_scope, actual_fall_scope, monkeypatch
):
    state, data, params = _case("conjure_fey")
    game = _TravelDice([(20, 10)] + [(12, 2)] * 3 + [(20, 1)])
    monkeypatch.setattr(dice, "_rng", game)
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    _cast(state, data, params)
    budget._run("begin_combat_turn", state, actor="hero", participant_data=data)
    assert "frightened" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert all(
        r.get("installed") is False
        for r in state[f"{SRD}.state"]["timed_condition_sources"].values()
        if r.get("fey_source_id")
    )
    before = deepcopy(state)
    budget._run(
        "use_srd_conjured_fey",
        state,
        participant_data=data,
        caster_state="hero",
        source_id="presence",
        point=[12, 3, 0],
        target_state="guard",
    )
    body = next(
        b
        for b in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if b["id"] == "presence:spirit"
    )
    assert body["position"] == [12, 3, 0]
    assert state[f"{SRD}.entities"]["hero"]["bonus_action_available"] is False
    assert (
        state[f"{SRD}.entities"]["hero"]["resources"]
        == before[f"{SRD}.entities"]["hero"]["resources"]
    )
    assert _source(state) == _source(before)
    current = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        budget._run(
            "use_srd_conjured_fey",
            state,
            participant_data=data,
            caster_state="hero",
            source_id="presence",
            point=[12, 3, 0],
            target_state="guard",
        )
    assert state == current and not game.rolls


def test_fey_dual_origin_fear_disadvantage_reads_actual_spirit_when_caster_hidden(
    conjured_scope, actual_fall_scope, monkeypatch
):
    state, data, params = _case("conjure_fey")
    game = _TravelDice([(20, 10)] + [(12, 2)] * 3 + [(20, 18), (20, 4)])
    monkeypatch.setattr(dice, "_rng", game)
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    _cast(state, data, params)
    hero = next(b for b in state[f"{SRD}.state"]["spatial_scene"]["entities"] if b["id"] == "hero")
    hero["metadata"]["invisible"] = True
    state[f"{SRD}.entities"]["hero"]["conditions"].append("invisible")
    result = budget._run(
        "d20_test",
        state,
        actor_state="guard",
        kind="ability_check",
        ability="strength",
        ability_modifier=0,
        proficiency_bonus=0,
        proficiency_factor="none",
        circumstantial_modifier=0,
        target_number=10,
    )
    assert result.result["context"]["_d20_test"]["total"] == 4
    assert not game.rolls


def test_fey_frightened_ordinary_segment_cannot_approach_spirit_while_retreating_from_caster(
    conjured_scope, actual_fall_scope, monkeypatch
):
    state, data, params = _case("conjure_fey")
    state[f"{SRD}.combat"]["current_actor"] = "guard"
    game = _TravelDice([(20, 10)] + [(12, 2)] * 3)
    monkeypatch.setattr(dice, "_rng", game)
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    _cast(state, data, params)
    budget._run("begin_combat_turn", state, actor="guard")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="caster or Fey"):
        budget._run(
            "move_srd_scene_creature_node", state, actor_state="guard", destination=[6, 0, 0]
        )
    assert state == before
    budget._run("move_srd_scene_creature_node", state, actor_state="guard", destination=[5, 2, 0])
    body = next(b for b in state[f"{SRD}.state"]["spatial_scene"]["entities"] if b["id"] == "guard")
    assert body["position"] == [5, 2, 0]
    assert not game.rolls


def test_celestial_actual_source_bright_light_ends_with_exact_concentration(
    conjured_scope, actual_fall_scope, monkeypatch
):
    state, data, params = _case()
    game = _TravelDice([(12, 2)] * 6 + [(12, 3)] * 4 + [(20, 10)])
    monkeypatch.setattr(dice, "_rng", game)
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    _cast(state, data, params)
    query = {"point": [5, 0, 0], "plane": "material", "ambient": "darkness"}
    assert (
        budget._run("resolve_srd_information_light", state, **query).result["context"][
            "_information_light"
        ]
        == "bright"
    )
    assert (
        budget._run(
            "resolve_srd_information_light", state, **(query | {"point": [16, 0, 0]})
        ).result["context"]["_information_light"]
        == "darkness"
    )
    budget._run("end_concentration", state, caster_state="hero")
    assert _source(state)["active"] is False
    assert (
        budget._run("resolve_srd_information_light", state, **query).result["context"][
            "_information_light"
        ]
        == "darkness"
    )
    assert not game.rolls


@pytest.mark.parametrize("spell", ["conjure_celestial", "conjure_fey"])
def test_genuine_paid_presence_archive_reopen_retry_and_exact_ten_minute_expiry(
    conjured_scope, actual_fall_scope, monkeypatch, tmp_path, spell
):
    monkeypatch.setattr(public, "_spell_case", _case)
    rolls = (
        [(12, 2)] * 6 + [(12, 3)] * 4 + [(20, 10)]
        if spell == "conjure_celestial"
        else [(20, 10)] + [(12, 2)] * 3
    )
    game = _TravelDice(rolls)
    monkeypatch.setattr(dice, "_rng", game)
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    database = tmp_path / (spell + ".db")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner, spell, target_overrides={"hp": 500, "max_hp": 500}
        )
        mechanics = StatelessMcpMechanics(owner)
        initial = owner.snapshot("casting")
        command = dwelling._command(
            params, initial.revision, "cast_spell_outside_combat", "paid-presence"
        )
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        source = _source(saved.state)
        assert source["spell"] == spell and source["expires_tick"] == 200
        slot = 7 if spell == "conjure_celestial" else 6
        assert (
            saved.state[f"{SRD}.entities"]["hero"]["resources"][f"spell_slot_{slot}"]
            == initial.state[f"{SRD}.entities"]["hero"]["resources"][f"spell_slot_{slot}"] - 1
        )
        assert mechanics.execute_action(command) == receipt and owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        mechanics = StatelessMcpMechanics(owner)
        command = dwelling._command(
            {"elapsed_rounds": 100}, saved.revision, "advance_srd_lasting_time", "expire-presence"
        )
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        ended = owner.snapshot("casting")
        assert _source(ended.state)["active"] is False
        assert _source(ended.state)["created_tick"] == source["created_tick"]
        if spell == "conjure_fey":
            body = next(
                b
                for b in ended.state[f"{SRD}.state"]["spatial_scene"]["entities"]
                if b["id"] == source["subject_id"]
            )
            assert body["active"] is False
        assert mechanics.execute_action(command) == receipt and owner.snapshot("casting") == ended
        assert (
            ended.state[f"{SRD}.entities"]["hero"]["resources"]
            == saved.state[f"{SRD}.entities"]["hero"]["resources"]
        )
    assert not game.rolls


@pytest.mark.parametrize("pip,slot", [(1, 6), (20, 9)])
def test_paid_fey_miss_or_ninth_slot_critical_uses_actual_native_dice(
    conjured_scope, actual_fall_scope, monkeypatch, pip, slot
):
    state, data, params = _case("conjure_fey", slot_resource=f"spell_slot_{slot}")
    count = (3 + slot - 6) * 2 if pip == 20 else 0
    game = _TravelDice([(20, pip)] + [(12, 1)] * count)
    monkeypatch.setattr(dice, "_rng", game)
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    _cast(state, data, params)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 500 - (count + 4 if count else 0)
    assert ("frightened" in state[f"{SRD}.entities"]["guard"]["conditions"]) == (pip == 20)
    assert _source(state)["upcast"] == slot - 6 and not game.rolls


def test_fey_actual_immunity_does_not_block_native_psychic_hit(
    conjured_scope, actual_fall_scope, monkeypatch
):
    state, data, params = _case("conjure_fey")
    state[f"{SRD}.entities"]["guard"]["condition_immunities"] = ["frightened"]
    game = _TravelDice([(20, 10)] + [(12, 2)] * 3)
    monkeypatch.setattr(dice, "_rng", game)
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    _cast(state, data, params)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 490
    assert "frightened" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert not game.rolls


def test_unsupported_actual_target_modifier_rejects_before_paid_cast(
    conjured_scope, actual_fall_scope
):
    state, data, params = _case("conjure_fey")
    state[f"{SRD}.entities"]["guard"]["conditions"] = ["paralyzed"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="target-condition"):
        _cast(state, data, params)
    assert state == before
