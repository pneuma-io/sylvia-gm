"""Courier crossings use actual paid sources, physical motion and one chronological clock."""

import json
import os
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_courier_chronology import bind_runtime_consumers
from scripts.srd_full.srd_spell_heat_metal import _grip_tools
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime, _RuntimeDumper
from scripts.srd_full.srd_timed_senses import _replace_tools

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import PackageRuntimeError, materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_courier_body_movement as body
from tests import test_srd_courier_interval_pace as pace
from tests import test_srd_spell_result_cards as cards
from tests import test_srd_spell_wave7_courier as courier
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_spell_wave3_information import _entity

SOURCE = Path(
    os.environ.get(
        "FC14_COURIER_CHRONOLOGY_RUNTIME",
        str(
            Path(__file__).parents[2]
            / "../fc14-spells-20260930-wave27/build/rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml"
        ),
    )
).resolve()
SOURCE_SHA = "1f18ab6bfdf306ed1730403dbd09097b8c5788ddb4877a0caa2416846b59e529"
ROOTS = {
    "cast_spell",
    "cast_spell_outside_combat",
    "prepare_srd_creature",
    "advance_srd_lasting_clock",
    "advance_srd_lasting_time",
    "move_srd_scene_creature",
    "advance_srd_courier_clock_boundary",
    "project_srd_animal_messenger_pace",
    "check_srd_heat_metal_grip_clock",
    "initialize_srd_npc_equipment",
    "change_srd_equipment",
}


def _calls(value):
    if isinstance(value, dict):
        if isinstance(value.get("call"), str):
            yield value["call"]
        for child in value.values():
            yield from _calls(child)
    elif isinstance(value, list):
        for child in value:
            yield from _calls(child)


@lru_cache
def _runtime():
    raw = SOURCE.read_bytes()
    assert sha256(raw).hexdigest() == SOURCE_SHA
    text = bind_runtime_consumers(raw.decode())
    actual = _load_runtime(
        _replace_tools(
            text,
            {
                "check_srd_heat_metal_grip_clock": _grip_tools()["check_srd_heat_metal_grip_clock"],
            },
        )
    )
    selected = set()
    pending = list(ROOTS)
    while pending:
        name = pending.pop()
        if name not in selected:
            assert name in actual["tools"], name
            selected.add(name)
            pending.extend(_calls(actual["tools"][name]))
    runtime = deepcopy(actual)
    runtime["tools"] = {name: tool for name, tool in actual["tools"].items() if name in selected}
    assert all(runtime["tools"][name] == actual["tools"][name] for name in selected)
    return runtime, yaml.dump(runtime, Dumper=_RuntimeDumper, sort_keys=False).encode()


@lru_cache
def _mechanic(name):
    runtime, content = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=content,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _authored(monkeypatch):
    runtime, _ = _runtime()
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: runtime["definitions"]["spells"])
    from sylvia_gm.application import package_runtime

    execute = package_runtime.execute_tool

    def observed(*args, **kwargs):
        result = execute(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", observed)


def test_private_fraction_uses_one_clock_and_preserves_public_integer_contract():
    state, _, _ = courier._courier(destination=(1000, 0, 0))
    before_position = deepcopy(courier._position(state))
    _run("advance_srd_courier_clock_boundary", state, lasting_state="state", elapsed_ticks=20.4)
    assert state[f"{SRD}.state"]["lasting_tick"] == 20.4
    assert courier._position(state) == before_position
    runtime, _ = _runtime()
    public = next(
        p
        for p in runtime["tools"]["advance_srd_lasting_clock"]["parameters"]
        if p["name"] == "elapsed_ticks"
    )
    assert public["type"] == "integer"
    before = deepcopy(state)
    with pytest.raises(PackageRuntimeError):
        _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=0.4)
    assert state == before


def test_paid_source_expiry_keeps_real_movement_on_chronological_clock():
    state, _, params = courier._courier(destination=(1000, 0, 0), flying_speed=60)
    pace._cast(state, params, "slow")
    _run("advance_srd_lasting_time", state, elapsed_rounds=15)
    assert courier._position(state) == pytest.approx([20 + 264000 * 20 / 28800, 0, 0])
    assert state[f"{SRD}.state"]["lasting_tick"] == 30
    assert state[f"{SRD}.entities"]["guard"]["animal_messenger"]["last_tick"] == 30


def test_paid_body_flight_expiry_reprojects_actual_native_walk_before_later_motion():
    state = body._gas_courier()
    source_id = state[f"{SRD}.entities"]["guard"]["form_current_source_id"]
    assert state[f"{SRD}.state"]["transformation_sources"][source_id]["expires_tick"] == 1200
    _run("advance_srd_lasting_time", state, elapsed_rounds=700)
    assert courier._position(state) == pytest.approx(
        [20 + (264000 * 1200 + 132000 * 200) / 28800, 0, 0]
    )
    actor = state[f"{SRD}.entities"]["guard"]
    assert actor["flying_speed"] in (None, 0)
    assert actor["animal_messenger"]["last_tick"] == 1400
    assert state[f"{SRD}.state"]["lasting_tick"] == 1400
    assert state[f"{SRD}.entities"]["mist_caster"]["spell_uses_remaining"]["gaseous_form"] == 0


def _hazard_case(destination=1000):
    state, data, params = courier._case("animal_messenger")
    params["divination_destination"] = [destination, 0, 0]
    prepared = pace._npc("haste", "guard")
    prepared.update(
        size_rank=0,
        creature_type="beast",
        challenge_rating_eighths=0,
        constitution_save=0,
        hit_points=500,
    )
    state[f"{SRD}.entities"].pop("guard")
    _run("prepare_srd_creature", state, **prepared)
    for entity in state[f"{SRD}.state"]["spatial_scene"]["entities"]:
        if entity.get("metadata", {}).get("participant_ref") == "guard":
            entity["metadata"]["creature_type"] = "beast"
    guard = state[f"{SRD}.entities"]["guard"]
    guard["saving_throw_modifiers"] = dict.fromkeys(data["abilities"], 0)
    dice._rng = Random(2)
    _run("cast_spell", state, participant_data=data, **params)
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    casting = body._gas_casting(params)
    casting.update(spell="haste", caster_state="guard", target="guard")
    _run("cast_spell_outside_combat", state, **casting)
    caster = pace._npc("spirit_guardians", "hazard_caster")
    _run("prepare_srd_creature", state, **caster)
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity(
            "hazard_caster", [128.9, 0, 0], participant_ref="hazard_caster", alignment_ethic="good"
        )
    )
    casting = body._gas_casting(params)
    casting.update(
        spell="spirit_guardians",
        caster_state="hazard_caster",
        environment_unaffected_targets=[],
        environment_spirit_appearance="angelic",
    )
    _run("cast_spell_outside_combat", state, **casting)
    boundary = _run(
        "resolve_srd_environment_hazard_entry", state, actor_state="guard", destination=[1000, 0, 0]
    ).result["context"]["_hazard_entry_destination"]
    corrected_origin = 128.9 + 113.5 - boundary[0]
    _run(
        "move_srd_scene_creature",
        state,
        actor_state="hazard_caster",
        destination=[corrected_origin, 0, 0],
    )
    assert state[f"{SRD}.entities"]["guard"]["animal_messenger"]["instance_id"]
    return state, data, params


def test_actual_crossing_retires_haste_at_20_4_and_aftermath_at_22_4(monkeypatch):
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, _, _ = _hazard_case()
    monkeypatch.setattr(dice, "_rng", Random(2))
    _run("advance_srd_lasting_time", state, elapsed_rounds=12)
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["haste_lethargy_until_tick"] == pytest.approx(22.4)
    assert guard.get("concentration_spell_id") is None
    assert guard["spell_haste"] is False
    assert guard["spell_uses_remaining"]["haste"] == 0
    assert state[f"{SRD}.state"]["lasting_tick"] == 26
    assert guard["animal_messenger"]["last_tick"] == 26
    assert guard["hp"] == 490
    assert guard["last_spirit_guardians_save"]["raw_damage"]["count"] == 3
    assert guard["last_spirit_guardians_save"]["turn"] == pytest.approx(20.4)
    assert courier._position(state) == pytest.approx([121.75, 0, 0])


def test_private_heat_grip_timer_reads_fraction_and_rejects_invalid_numbers():
    state, _, _ = courier._courier(destination=(1000, 0, 0))
    _run("advance_srd_courier_clock_boundary", state, lasting_state="state", elapsed_ticks=20.4)
    values = {"caster_state": "hero", "expires_caster_turn": 0, "expires_tick": 22.4}
    result = _run("check_srd_heat_metal_grip_clock", state, **values)
    assert result.result["context"]["_heat_grip_active"] is True
    _run("advance_srd_courier_clock_boundary", state, lasting_state="state", elapsed_ticks=2)
    result = _run("check_srd_heat_metal_grip_clock", state, **values)
    assert result.result["context"]["_heat_grip_active"] is False
    for invalid in ("tomorrow", -1):
        before = deepcopy(state)
        with pytest.raises(PackageRuntimeError):
            _run("check_srd_heat_metal_grip_clock", state, **{**values, "expires_tick": invalid})
        assert state == before
    for invalid in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(TypeError, match="JSON-compatible"):
            owned_payload({"expires_tick": invalid})


def test_repeat_preserves_all_ordinary_movement_owners_and_one_clock_branch():
    text = bind_runtime_consumers(SOURCE.read_text(encoding="utf-8"))
    once = _load_runtime(text)
    twice = _load_runtime(bind_runtime_consumers(text))
    assert twice == once
    original = _load_runtime(SOURCE.read_text(encoding="utf-8"))
    for name in (
        "move_srd_scene_creature",
        "move_srd_scene_creature_node",
        "move_srd_scene_creature_environment_segment",
        "spend_combat_movement",
        "derive_srd_scene_movement_budget",
    ):
        assert once["tools"][name] == original["tools"][name]


def test_actual_combat_movement_still_spends_current_turn_and_does_not_bank_time():
    state, _, _ = courier._courier(destination=(1000, 0, 0))
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="guard",
        current_actor="guard",
        movement_remaining=30,
        movement_spent=0,
        movement_mode="walk",
        dashes_taken=0,
    )
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[30, 0, 0])
    assert state[f"{SRD}.combat"]["movement_spent"] == 10
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=20)
    assert courier._position(state) == [30, 0, 0]
    assert state[f"{SRD}.entities"]["guard"]["animal_messenger"]["last_tick"] == 20


def test_actual_equipment_change_updates_the_next_chronological_motion():
    pace.test_admitted_equipment_change_updates_actual_later_courier_pace()


def _two_hazards():
    state, _, params = _hazard_case()
    _run("prepare_srd_creature", state, **pace._npc("spirit_guardians", "second_hazard"))
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity(
            "second_hazard", [200, 0, 0], participant_ref="second_hazard", alignment_ethic="good"
        )
    )
    casting = body._gas_casting(params)
    casting.update(
        spell="spirit_guardians",
        caster_state="second_hazard",
        environment_unaffected_targets=[],
        environment_spirit_appearance="angelic",
    )
    _run("cast_spell_outside_combat", state, **casting)
    return state


def test_two_actual_emanation_crossings_use_distinct_saved_event_times(monkeypatch):
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state = _two_hazards()
    monkeypatch.setattr(dice, "_rng", Random(2))
    for rounds in (12, 10, 8):
        _run("advance_srd_lasting_time", state, elapsed_rounds=rounds)
    areas = state[f"{SRD}.state"]["persistent_spell_areas"]
    assert areas["hazard_caster"]["event_turn"] == pytest.approx(20.4)
    assert areas["second_hazard"]["event_turn"] > areas["hazard_caster"]["event_turn"]
    assert (
        state[f"{SRD}.entities"]["guard"]["last_spirit_guardians_save"]["area_id"]
        == "second_hazard"
    )
    assert state[f"{SRD}.state"]["lasting_tick"] == 62


def test_long_multiple_hazard_action_fails_atomically_at_unchanged_evidence_cap(
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state = _two_hazards()
    monkeypatch.setattr(dice, "_rng", Random(2))
    before = deepcopy(state)
    with pytest.raises(PackageRuntimeError):
        _run("advance_srd_lasting_time", state, elapsed_rounds=30)
    assert state == before
    assert "Spatial query evidence exceeds the action limit" in capsys.readouterr().out


def test_paid_hazard_source_expires_before_a_later_would_be_crossing(monkeypatch):
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, _, _ = _hazard_case(destination=5000)
    _run(
        "move_srd_travel_subject",
        state,
        subject="hazard_caster",
        destination=[1500, 0, 0],
        plane="material",
    )
    _run("advance_srd_lasting_time", state, elapsed_rounds=250)
    actor = state[f"{SRD}.entities"]["guard"]
    assert actor["hp"] == 500
    assert actor.get("last_spirit_guardians_save") is None
    assert state[f"{SRD}.state"]["persistent_spell_areas"]["hazard_caster"]["active"] is False
    assert state[f"{SRD}.state"]["lasting_tick"] == 502


@lru_cache
def _archive():
    """Strictly admit the unchanged selected owner closure and its actual public schemas."""
    from sylvia_gm.application.bundled_packages import _archive as archive_bytes
    from sylvia_gm.application.package_materialization import NormalizedPackageDraft
    from sylvia_gm.domain.package_library import canonical_json_bytes
    from tests import test_srd_dream_spell as dream

    primary, reference = dream._linked_drafts()
    record = deepcopy(dict(primary.record))
    payloads = dict(primary.payloads)
    runtime, content = _runtime()
    record["tools"] = [row for row in record["tools"] if row["name"] in runtime["tools"]]
    names = {row["name"] for row in record["tools"]}
    for surface in record.get("ui_surfaces", []):
        surface["actions"] = [action for action in surface["actions"] if action["intent"] in names]
    for row in record["tools"]:
        row["input_schema"] = _tool_schema(runtime["tools"][row["name"]], tool_name=row["name"])
    payloads["runtime/ruleset.yaml"] = content
    declarations = json.loads(payloads["content/content-declarations.json"])
    for inclusion in declarations["inclusions"]:
        if "tools" in inclusion.get("links", {}):
            inclusion["links"]["tools"] = [
                name for name in inclusion["links"]["tools"] if name in names
            ]
    payloads["content/content-declarations.json"] = canonical_json_bytes(declarations)
    return archive_bytes((NormalizedPackageDraft(record, payloads, 4), reference))


def _public_case(spell):
    state, data, params = courier._case(spell)
    params["divination_destination"] = [1000, 0, 0]
    state[f"{SRD}.entities"].pop("guard")
    prepared = pace._npc("haste", "guard")
    prepared.update(
        size_rank=0,
        creature_type="beast",
        challenge_rating_eighths=0,
        constitution_save=0,
        hit_points=500,
    )
    _run("prepare_srd_creature", state, **prepared)
    for entity in state[f"{SRD}.state"]["spatial_scene"]["entities"]:
        if entity.get("metadata", {}).get("participant_ref") == "guard":
            entity["metadata"]["creature_type"] = "beast"
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"] = dict.fromkeys(
        data["abilities"], 0
    )
    _run("prepare_srd_creature", state, **pace._npc("spirit_guardians", "hazard_caster"))
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity(
            "hazard_caster", [129.3, 0, 0], participant_ref="hazard_caster", alignment_ethic="good"
        )
    )
    return state, data, params


def test_strict_paid_public_journey_reopens_fractional_aftermath_and_retries_once(
    tmp_path,
    monkeypatch,
):
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus
    from tests import test_srd_public_spell_casting as public
    from tests.srd_full.test_srd_npc_spell_casting import _act

    monkeypatch.setattr(public, "bundled_srd_archive", _archive)
    monkeypatch.setattr(public, "_spell_case", _public_case)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "courier-chronology.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner, "animal_messenger", target_overrides={"hp": 500, "max_hp": 500}
        )
        names = {tool.name for tool in StatelessMcpMechanics(owner).list_tools("casting")}
        assert {"cast_spell_outside_combat", "advance_srd_lasting_time"} <= names
        assert "advance_srd_courier_clock_boundary" not in names
        monkeypatch.setattr(dice, "_rng", Random(2))
        messenger, messenger_command = _act(owner, "cast_spell_outside_combat", **params)
        elapsed, _ = _act(owner, "advance_srd_lasting_time", elapsed_rounds=1)
        haste_params = body._gas_casting(params)
        haste_params.update(spell="haste", caster_state="guard", target="guard")
        haste, haste_command = _act(owner, "cast_spell_outside_combat", **haste_params)
        hazard_params = body._gas_casting(params)
        hazard_params.update(
            spell="spirit_guardians",
            caster_state="hazard_caster",
            environment_unaffected_targets=[],
            environment_spirit_appearance="angelic",
        )
        hazard, hazard_command = _act(owner, "cast_spell_outside_combat", **hazard_params)
        assert (
            messenger.status
            is elapsed.status
            is haste.status
            is hazard.status
            is ActionStatus.COMMITTED
        )
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["guard"]["spell_uses_remaining"]["haste"] == 0
        assert (
            saved.state[f"{SRD}.entities"]["hazard_caster"]["spell_uses_remaining"][
                "spirit_guardians"
            ]
            == 0
        )
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        for command, receipt in (
            (messenger_command, messenger),
            (haste_command, haste),
            (hazard_command, hazard),
        ):
            assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
        monkeypatch.setattr(dice, "_rng", Random(2))
        crossed, crossed_command = _act(owner, "advance_srd_lasting_time", elapsed_rounds=10)
        assert crossed.status is ActionStatus.COMMITTED
        after = owner.snapshot("casting")
        actor = after.state[f"{SRD}.entities"]["guard"]
        assert actor["hp"] == 490
        assert actor["haste_lethargy_until_tick"] == pytest.approx(22.4)
        assert actor["last_spirit_guardians_save"]["turn"] == pytest.approx(20.4)
        assert list(courier._position(after.state)) == pytest.approx([113.5, 0, 0])
        assert after.state[f"{SRD}.state"]["lasting_tick"] == 22
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == after.state
        assert owner.execute_action(crossed_command) == crossed
        assert owner.snapshot("casting").state == after.state
        continued, continued_command = _act(owner, "advance_srd_lasting_time", elapsed_rounds=1)
        assert continued.status is ActionStatus.COMMITTED
        final = owner.snapshot("casting")
        assert list(courier._position(final.state)) == pytest.approx([117.1666666666667, 0, 0])
        assert final.state[f"{SRD}.entities"]["guard"]["hp"] == 490
        assert final.state[f"{SRD}.state"]["lasting_tick"] == 24
        assert owner.execute_action(continued_command) == continued
        assert owner.snapshot("casting").state == final.state
