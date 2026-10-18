"""Planar Binding commits actual long casting and retained-source obedience."""

import json
import os
from copy import deepcopy
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_control_binding import DURATION_ROUNDS, PUBLIC_TOOLS, extend_runtime

from sylvia_gm import dice
from tests.srd_full import full_package_fixture as bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests.srd_full import test_srd_action_budget as budget
from tests.srd_full import test_srd_public_spell_casting as public_casting
from tests.srd_full import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_illusion_removals import _prepare
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_shared_source_adoption import _case as _source_case

SRD = cards.SRD


@pytest.fixture(autouse=True)
def _compiled_owner(monkeypatch):
    root = os.environ.get("FC14_BINDING_RUNTIME_ROOT")
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


def _case(slot=5, creature_type="fiend"):
    state, data, params = _source_case("blink")
    params.update(
        spell="planar_binding",
        target="guard",
        slot_resource=f"spell_slot_{slot}",
        material_item_ids=_prepare(state, data, "planar_binding"),
    )
    state[f"{SRD}.entities"]["guard"].update(creature_type=creature_type, languages=["Common"])
    state[f"{SRD}.entities"]["hero"]["languages"] = ["Common"]
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["charisma"] = -100
    return state, data, params


def _start(state, data, params):
    return budget._run(
        "begin_srd_spell_cast", state, participant_data=data, **_begin(params), target="guard"
    )


def _finish(state, data, params):
    budget._run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
        elapsed_rounds=600,
        uninterrupted=True,
    )
    return budget._run("cast_spell_outside_combat", state, participant_data=data, **params)


def _source(state):
    return next(
        {**row, "id": source_id}
        for source_id, row in state[f"{SRD}.state"]["timed_condition_sources"].items()
        if row["spell_id"] == "planar_binding"
    )


def test_actual_hour_cost_source_and_not_charmed():
    state, data, params = _case()
    resources = deepcopy(state[f"{SRD}.entities"]["hero"]["resources"])
    _start(state, data, params)
    assert state[f"{SRD}.entities"]["hero"]["extended_casting"]["binding_target"] == "guard"
    _finish(state, data, params)
    source = _source(state)
    assert source["installed"] is True
    assert source["expires_tick"] - source["started_tick"] == 28800
    assert source["cast_id"] == state[f"{SRD}.entities"]["hero"]["last_spell_source_cast_id"]
    assert "charmed" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert (
        state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"]
        == resources["spell_slot_5"] - 1
    )


def test_invalid_type_consumes_cast_without_roll_or_binding():
    state, data, params = _case(creature_type="humanoid")
    _start(state, data, params)
    _finish(state, data, params)
    assert not any(
        row["spell_id"] == "planar_binding"
        for row in state[f"{SRD}.state"].get("timed_condition_sources", {}).values()
    )
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2


def test_actual_target_movement_out_of_range_interrupts_without_rejecting_movement():
    state, data, params = _case()
    _start(state, data, params)
    state[f"{SRD}.entities"]["guard"]["speed"] = 120
    budget._run(
        "move_srd_scene_creature",
        state,
        actor_state="guard",
        destination=[70, 0, 0],
        mode="walk",
        difficult_terrain=False,
    )
    assert state[f"{SRD}.entities"]["hero"]["extended_casting"] is None
    assert next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == "guard"
    )["position"] == [70, 0, 0]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 3


def test_real_task_action_then_owed_report_preserves_source():
    state, data, params = _case()
    _start(state, data, params)
    _finish(state, data, params)
    source = _source(state)
    budget._run(
        "issue_srd_bound_order",
        state,
        caster_state="hero",
        target_state="guard",
        source_id=source["id"],
        order_kind="fetch",
        order_description="Carry those supplies.",
        language="Common",
    )
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=False, current_actor="guard")
    budget._run("begin_combat_turn", state)
    budget._run(
        "resolve_srd_control_activity",
        state,
        actor_state="guard",
        source_id=source["id"],
        completed=True,
        completion_evidence="The supplies are delivered.",
        task_kind="fetch",
    )
    assert state[f"{SRD}.combat"]["normal_action_spent"] is True
    assert _source(state)["binding_return_pending"] is True
    budget._run(
        "resolve_srd_binding_return",
        state,
        target_state="guard",
        caster_state="hero",
        source_id=source["id"],
        destination=[1, 0, 0],
        report_description="The supplies were delivered.",
    )
    assert _source(state)["binding_reported"] is True
    assert _source(state)["installed"] is True


@pytest.mark.parametrize("slot", [6, 7, 8, 9])
def test_actual_upcast_source_duration(slot):
    state, data, params = _case(slot=slot)
    _start(state, data, params)
    _finish(state, data, params)
    source = _source(state)
    assert source["expires_tick"] - source["started_tick"] == DURATION_ROUNDS[slot] * 2
    assert source["spell_level"] == slot


def test_binding_does_not_use_charmed_immunity_and_successful_save_installs_no_source():
    state, data, params = _case()
    state[f"{SRD}.entities"]["guard"]["condition_immunities"] = ["charmed"]
    _start(state, data, params)
    _finish(state, data, params)
    assert _source(state)["installed"] is True
    state, data, params = _case()
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["charisma"] = 100
    _start(state, data, params)
    _finish(state, data, params)
    assert not state[f"{SRD}.state"].get("timed_condition_sources")


def test_actual_retained_target_cannot_be_switched_at_completion():
    state, data, params = _case()
    _start(state, data, params)
    budget._run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
        elapsed_rounds=600,
        uninterrupted=True,
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="same retained target"):
        budget._run(
            "cast_spell_outside_combat",
            state,
            participant_data=data,
            **(params | {"target": "hero"}),
        )
    assert state == before
    budget._run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert _source(state)["installed"] is True


def test_owning_emission_is_semantically_stable():
    text = (budget.ROOT / "runtime/ruleset.yaml").read_text(encoding="utf-8")
    first = extend_runtime(text)
    second = extend_runtime(first)
    assert yaml.load(first, Loader=yaml.CSafeLoader) == yaml.load(second, Loader=yaml.CSafeLoader)


def test_overlapping_binding_retains_previous_source_and_selects_newest():
    state, data, params = _case()
    _start(state, data, params)
    _finish(state, data, params)
    first = _source(state)
    _start(state, data, params)
    _finish(state, data, params)
    sources = state[f"{SRD}.state"]["timed_condition_sources"]
    assert len(sources) == 2 and sources[first["id"]]["installed"] is True
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="effective same-spell"):
        budget._run(
            "issue_srd_bound_order",
            state,
            caster_state="hero",
            target_state="guard",
            source_id=first["id"],
            order_kind="fetch",
            order_description="Carry the supplies.",
            language="Common",
        )
    assert state == before
    newest = state[f"{SRD}.entities"]["guard"]["control_order_source_ids"][0]
    assert newest != first["id"]
    budget._run(
        "issue_srd_bound_order",
        state,
        caster_state="hero",
        target_state="guard",
        source_id=newest,
        order_kind="fetch",
        order_description="Carry the supplies.",
        language="Common",
    )


def test_binding_expiry_preserves_an_independent_condition_source():
    state, data, params = _case()
    budget._run(
        "install_srd_timed_condition_source",
        state,
        target_state="guard",
        lasting_state="state",
        caster_state="hero",
        spell="hold_monster",
        condition="incapacitated",
        save_dc=15,
        duration_rounds=20000,
        repeat_save_timing="none",
    )
    independent = next(iter(state[f"{SRD}.state"]["timed_condition_sources"]))
    _start(state, data, params)
    _finish(state, data, params)
    source = _source(state)
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    assert state[f"{SRD}.state"]["timed_condition_sources"][source["id"]]["installed"] is False
    assert state[f"{SRD}.state"]["timed_condition_sources"][independent]["installed"] is True
    assert "incapacitated" in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_mind_blank_real_source_prevents_the_inferred_mind_control_effect():
    state, data, params = _case()
    blank = params | {
        "spell": "mind_blank",
        "slot_resource": "spell_slot_8",
        "material_item_ids": _prepare(state, data, "mind_blank"),
    }
    budget._run("cast_spell_outside_combat", state, participant_data=data, **blank)
    params["material_item_ids"] = _prepare(state, data, "planar_binding")
    _start(state, data, params)
    _finish(state, data, params)
    assert not any(
        row["spell_id"] == "planar_binding"
        for row in state[f"{SRD}.state"].get("timed_condition_sources", {}).values()
    )


def test_completed_order_returns_to_binding_place_and_requires_remaining_there_on_other_plane():
    state, data, params = _case()
    _start(state, data, params)
    _finish(state, data, params)
    source = _source(state)
    budget._run(
        "issue_srd_bound_order",
        state,
        caster_state="hero",
        target_state="guard",
        source_id=source["id"],
        order_kind="fetch",
        order_description="Deliver the supplies.",
        language="Common",
    )
    ethereal = params | {
        "spell": "etherealness",
        "target": "hero",
        "slot_resource": "spell_slot_7",
        "material_item_ids": _prepare(state, data, "etherealness"),
    }
    ethereal.pop("targets", None)
    budget._run("cast_spell_outside_combat", state, participant_data=data, **ethereal)
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=False, current_actor="guard")
    budget._run("begin_combat_turn", state)
    budget._run(
        "resolve_srd_control_activity",
        state,
        actor_state="guard",
        source_id=source["id"],
        completed=True,
        completion_evidence="The supplies arrived.",
        task_kind="fetch",
    )
    budget._run(
        "resolve_srd_binding_return",
        state,
        target_state="guard",
        caster_state="hero",
        source_id=source["id"],
        destination=[5, 0, 0],
    )
    assert _source(state)["binding_waiting_at_origin"] is True
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Binding"):
        budget._run(
            "move_srd_scene_creature",
            state,
            actor_state="guard",
            destination=[10, 0, 0],
            mode="walk",
            difficult_terrain=False,
        )
    assert state == before


def _archive():
    primary, reference = bundled_packages._srd_drafts()
    content = (budget.ROOT / "runtime/ruleset.yaml").read_bytes()
    runtime = yaml.load(content, Loader=yaml.CSafeLoader)
    record = deepcopy(primary.record)
    for declaration in record["tools"]:
        declaration["input_schema"] = _tool_schema(
            runtime["tools"][declaration["name"]], tool_name=declaration["name"]
        )
    sample = next(d for d in record["tools"] if d["name"] == "issue_srd_dominated_order")
    for name in PUBLIC_TOOLS:
        if not any(d["name"] == name for d in record["tools"]):
            declaration = deepcopy(sample)
            declaration.update(
                name=name, input_schema=_tool_schema(runtime["tools"][name], tool_name=name)
            )
            record["tools"].append(declaration)
    payloads = dict(primary.payloads) | {"runtime/ruleset.yaml": content}
    declaration_path = record["content_declarations"]
    content_declarations = json.loads(payloads[declaration_path])
    links = next(
        row["links"]["tools"]
        for row in content_declarations["inclusions"]
        if "issue_srd_dominated_order" in row["links"].get("tools", [])
    )
    links.extend(sorted(PUBLIC_TOOLS - set(links)))
    links.sort()
    payloads[declaration_path] = canonical_json_bytes(content_declarations)
    return bundled_packages._archive(
        (NormalizedPackageDraft(record, payloads, primary.format_version), reference)
    )


def test_public_full_hour_cast_reopen_command_and_exact_dismissal_retries(tmp_path, monkeypatch):
    package_parent = os.environ.get("FC14_SOURCE_PACKAGE_ROOT")
    assert package_parent
    monkeypatch.setattr(bundled_packages, "_bundled_srd_root", lambda: Path(package_parent))
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _archive)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _spell, **kwargs: _case())
    database = tmp_path / "binding.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(owner, "planar_binding")
        started, _ = _act(owner, "begin_srd_spell_cast", **_begin(params), target="guard")
        assert started.status is ActionStatus.COMMITTED, started.result
        continued, _ = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=600,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert continued.status is ActionStatus.COMMITTED, continued.result
        cast, cast_command = _act(owner, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED, cast.result
        saved = owner.snapshot("casting")
        source_id, source = next(
            iter(saved.state[f"{SRD}.state"]["timed_condition_sources"].items())
        )
        assert source["spell_id"] == "planar_binding" and source["installed"]
        assert owner.execute_action(cast_command) == cast
        assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        commanded, command = _act(
            owner,
            "issue_srd_bound_order",
            caster_state="hero",
            target_state="guard",
            source_id=source_id,
            order_kind="fetch",
            order_description="Deliver the real supplies.",
            language="Common",
        )
        assert commanded.status is ActionStatus.COMMITTED, commanded.result
        dismissed, dismissal = _act(
            owner, "dismiss_srd_spell", caster_state="hero", cast_id=source["cast_id"]
        )
        assert dismissed.status is ActionStatus.COMMITTED, dismissed.result
        settled = owner.snapshot("casting")
        assert (
            settled.state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"]
            is False
        )
        assert owner.execute_action(command) == commanded
        assert owner.execute_action(dismissal) == dismissed
        assert owner.snapshot("casting") == settled
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == settled
