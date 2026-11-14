"""Printed Mansion meals consume canonical servings once, without nutrition conversions."""

import json
from copy import deepcopy

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
from tests import test_srd_spell_travel_dwelling as dwelling
from tests import test_srd_spell_travel_furnishings as furnishings
from tests import test_srd_spell_travel_telekinesis_fall as fall
from tests import test_srd_spell_travel_telekinesis_initial_fine as initial
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_spell_travel import SRD
from tests.srd_full.test_srd_spell_travel_telekinesis import _tk_case

BANQUET = [
    {
        "id": "food",
        "offset": [5, 2, 0],
        "extent": [1, 1, 1],
        "material": "food",
        "appearance": "a nine-course feast",
    }
]


@pytest.fixture
def banquet_scope(monkeypatch):
    monkeypatch.setenv("FC14_DWELLING_RUNTIME", ".scratch/mansion-banquet-composition/runtime.yaml")
    original = bundled_packages._srd_drafts

    def drafts():
        primary, reference = original()
        record = deepcopy(primary.record)
        template = next(
            row for row in record["tools"] if row["name"] == "traverse_srd_demiplane_door"
        )
        declaration = deepcopy(template)
        declaration["name"] = "eat_srd_mansion_banquet"
        declaration["input_schema"] = _tool_schema(
            fall._runtime()[0]["tools"][declaration["name"]], tool_name=declaration["name"]
        )
        record["tools"].append(declaration)
        payload = dict(primary.payloads)
        path = "content/content-declarations.json"
        data = json.loads(payload[path])
        inclusion = next(
            row
            for row in data["inclusions"]
            if "traverse_srd_demiplane_door" in row.get("links", {}).get("tools", [])
        )
        inclusion["links"]["tools"] = sorted(
            set(inclusion["links"]["tools"]) | {declaration["name"]}
        )
        payload[path] = canonical_json_bytes(data)
        return NormalizedPackageDraft(record, payload, primary.format_version), reference

    monkeypatch.setattr(bundled_packages, "_srd_drafts", drafts)
    yield from dwelling.dwelling_scope.__wrapped__(monkeypatch)


def _food(state):
    return furnishings._body(state, "food")


def test_actual_hundred_servings_are_canonical_and_exhaust_without_hp_or_clock_change(
    banquet_scope,
):
    state, _ = _tk_case()
    dwelling._install(state, banquet=deepcopy(BANQUET))
    source = deepcopy(dwelling._source(state))
    assert source["furnishings"] == [] and source["banquet"] == BANQUET
    assert _food(state)["metadata"]["srd_mansion_banquet"]["remaining_servings"] == 100
    destination = dwelling._enter(state)
    before_actor = deepcopy(state[f"{SRD}.entities"]["hero"])
    before_tick = state[f"{SRD}.state"]["lasting_tick"]
    for index in range(100):
        budget._run("eat_srd_mansion_banquet", state, actor_state="hero", object_id="food")
        assert _food(state)["metadata"]["srd_mansion_banquet"]["remaining_servings"] == 99 - index
    after_actor = deepcopy(state[f"{SRD}.entities"]["hero"])
    meals = after_actor.pop("srd_mansion_meals")
    assert after_actor == before_actor
    assert [meal["serving"] for meal in meals] == list(range(1, 101))
    assert all(meal["courses"] == 9 and meal["recipient"] == "hero" for meal in meals)
    assert _food(state)["active"] is False
    assert state[f"{SRD}.state"]["lasting_tick"] == before_tick
    assert furnishings._body(state, "hero")["position"] == destination
    assert dwelling._source(state) == source
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        budget._run("eat_srd_mansion_banquet", state, actor_state="hero", object_id="food")
    assert state == before


@pytest.mark.parametrize("defect", ["outside", "expired", "combat"])
def test_actual_recipient_lifetime_and_combat_reject_without_consumption(
    banquet_scope, defect, monkeypatch
):
    state, _ = _tk_case()
    if defect == "combat":
        state[f"{SRD}.combat"] = deepcopy(budget._state()[f"{SRD}.combat"])
    dwelling._install(state, banquet=deepcopy(BANQUET))
    if defect != "outside":
        dwelling._enter(state)
    if defect == "expired":
        budget._run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        budget._run("eat_srd_mansion_banquet", state, actor_state="hero", object_id="food")
    assert state == before


def test_actual_banquet_departure_smokes_and_does_not_create_supply_provenance(banquet_scope):
    state, _ = _tk_case()
    dwelling._install(state, banquet=deepcopy(BANQUET))
    assert "srd_created_supply" not in _food(state)["metadata"]
    budget._run(
        "move_srd_travel_subject", state, subject="food", destination=[15, 0, 0], plane="material"
    )
    assert _food(state)["active"] is False
    assert _food(state)["metadata"]["mansion_smoke"]["position"] == [15, 0, 0]
    assert _food(state)["metadata"]["srd_mansion_banquet"]["remaining_servings"] == 100


def test_actual_food_antimagic_suppression_restores_after_source_end(banquet_scope):
    state, _ = _tk_case()
    dwelling._install(state, banquet=deepcopy(BANQUET))
    dwelling._enter(state)
    dwelling._enter(state, "guard")
    budget._run(
        "cast_srd_spell_magic_zone",
        state,
        caster_state="guard",
        spell="antimagic_field",
        slot_level=8,
        concentration_save_modifier=0,
        save_dc=15,
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="suppresses this dwelling"):
        budget._run("eat_srd_mansion_banquet", state, actor_state="hero", object_id="food")
    assert state == before
    budget._run("end_concentration", state, caster_state="guard")
    budget._run("eat_srd_mansion_banquet", state, actor_state="hero", object_id="food")
    assert _food(state)["metadata"]["srd_mansion_banquet"]["remaining_servings"] == 99


def _banquet_case(spell):
    state, data, params = dwelling._mansion_case(spell)
    params["mansion_banquet"] = deepcopy(BANQUET)
    return state, data, params


def test_genuine_paid_minute_banquet_consumption_reopen_retry_decrements_once(
    banquet_scope, tmp_path, monkeypatch
):
    monkeypatch.setattr(public, "_spell_case", _banquet_case)
    actual = initial._dice(monkeypatch)
    path = tmp_path / "banquet.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "magnificent_mansion")
        mechanics = StatelessMcpMechanics(owner)
        for name, arguments, key in (
            ("begin_srd_spell_cast", _begin(params), "begin"),
            (
                "continue_srd_spell_cast",
                {
                    "caster_state": "hero",
                    "elapsed_rounds": 10,
                    "uninterrupted": True,
                    "verbal_component_available": True,
                    "somatic_component_available": True,
                    "material_component_available": True,
                },
                "ready",
            ),
        ):
            result = mechanics.execute_action(
                dwelling._command(arguments, owner.snapshot("casting").revision, name, key)
            )
            assert result.status is ActionStatus.COMMITTED, result.result
        command = dwelling._command(params, owner.snapshot("casting").revision, key="paid")
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        assert _food(saved.state)["metadata"]["srd_mansion_banquet"]["remaining_servings"] == 100
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == 2
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        mechanics = StatelessMcpMechanics(owner)
        source = dwelling._source(saved.state)
        room = dwelling._room(saved.state)
        for name, arguments, key in (
            (
                "move_srd_scene_creature",
                {"actor_state": "hero", "destination": [5, 9, 0], "mode": "walk"},
                "approach",
            ),
            (
                "traverse_srd_dwelling_door",
                {
                    "actor_state": "hero",
                    "source_id": source["id"],
                    "door_subject": source["outer_id"],
                    "destination": [room["shape"]["min"][0] + 1.5, 0, room["shape"]["min"][2]],
                },
                "enter",
            ),
            ("eat_srd_mansion_banquet", {"actor_state": "hero", "object_id": "food"}, "meal"),
        ):
            command = dwelling._command(arguments, owner.snapshot("casting").revision, name, key)
            result = mechanics.execute_action(command)
            assert result.status is ActionStatus.COMMITTED, result.result
            after = owner.snapshot("casting")
            assert (
                mechanics.execute_action(command) == result and owner.snapshot("casting") == after
            )
        assert _food(after.state)["metadata"]["srd_mansion_banquet"]["remaining_servings"] == 99
        meals = after.state[f"{SRD}.entities"]["hero"]["srd_mansion_meals"]
        assert len(meals) == 1 and meals[0]["courses"] == 9
        assert after.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == 2
        assert actual.consumed == []
