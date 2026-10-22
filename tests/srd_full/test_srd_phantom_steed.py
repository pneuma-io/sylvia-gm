"""The source horse is a real actor/body; later packets and clocks own its fading."""

import hashlib
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts import srd_courier_body_movement, srd_mirage_support
from scripts.srd_full.srd_phantom_steed import (
    PROFILES,
    PUBLIC_TOOLS,
    _horse,
    bind_damage_consumers,
    bind_runtime_consumers,
    extend_runtime,
)
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application import package_runtime
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_telekinesis_fall as scope
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_spell_travel import SRD, _point
from tests.srd_full.test_srd_spell_travel import _case as spell_case
from tests.srd_full.test_srd_spell_travel_telekinesis import _tk_case

BASE = Path(str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-phantom-steed/input-1"))
RUNTIME = BASE / "fc14-spells-20260930-wave8/portals/.scratch/rope32-runtime-immutable/runtime.yaml"
CURRENT = BASE / "fc14-spells-20260930-wave27/build/rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml"


def _calls(value):
    if isinstance(value, dict):
        if "call" in value:
            yield value["call"]
        for child in value.values():
            yield from _calls(child)
    elif isinstance(value, list):
        for child in value:
            yield from _calls(child)


@lru_cache
def _runtime():
    """Compose new source tools onto immutable real owners without a whole-build claim."""
    encoded = RUNTIME.read_bytes()
    assert hashlib.sha256(encoded).hexdigest() == (
        "e3c4858d2b42efd1b4aba54ae570ca55a53cb87f83b6eab9b8b894fe0ad1a813"
    )
    text = bind_runtime_consumers(bind_damage_consumers(extend_runtime(encoded.decode("utf-8"))))
    runtime = _load_runtime(text)
    current_bytes = CURRENT.read_bytes()
    assert hashlib.sha256(current_bytes).hexdigest() == (
        "1f18ab6bfdf306ed1730403dbd09097b8c5788ddb4877a0caa2416846b59e529"
    )
    dependencies = _load_runtime(current_bytes)["tools"]
    # These already source-reviewed leaves have not reached a full new admitted graph yet.
    source_text = srd_mirage_support.extend_runtime(
        current_bytes.decode("utf-8").replace("\r\n", "\n")
    )
    source_text = srd_courier_body_movement.bind_runtime_consumers(source_text)
    source_dependencies = _load_runtime(source_text)["tools"]
    missing = set(_calls(runtime["tools"])) - runtime["tools"].keys()
    while missing:
        for name in sorted(missing):
            runtime["tools"][name] = deepcopy(source_dependencies.get(name, dependencies.get(name)))
            assert runtime["tools"][name] is not None, name
        missing = set(_calls(runtime["tools"])) - runtime["tools"].keys()
    runtime["definitions"]["spells"].update(deepcopy(PROFILES))
    # Only source-derived declarations change; genuine paid shared cast bodies are retained.
    for name in (
        "cast_spell",
        "cast_spell_node",
        "cast_spell_outside_combat",
        "begin_srd_spell_cast",
        "begin_srd_spell_cast_internal",
        "begin_srd_spell_action",
    ):
        if name in runtime["tools"]:
            for parameter in runtime["tools"][name]["parameters"]:
                if parameter["name"] == "spell" and "enum_values" in parameter:
                    parameter["enum_values"] = sorted(
                        set(parameter["enum_values"]) | PROFILES.keys()
                    )
    encoded = yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode()
    return runtime, encoded


@pytest.fixture
def steed_scope(monkeypatch):
    monkeypatch.setattr(scope, "_runtime", _runtime)
    monkeypatch.setattr(scope, "EXTRA_PUBLIC_TOOLS", frozenset(PUBLIC_TOOLS))
    execute = package_runtime.execute_tool

    def evidence(*args, **kwargs):
        result = execute(*args, **kwargs)
        if not result.success:
            print("Selected public fixture failure:", result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", evidence)
    yield from scope.actual_fall_scope.__wrapped__(monkeypatch)


def _case():
    return _tk_case()


def _install(state):
    return _run(
        "cast_srd_phantom_steed",
        state,
        caster_state="hero",
        slot_level=3,
        phantom_steed_id="horse",
        phantom_steed_position=[0, 10, 0],
        phantom_steed_appearance="a pale horse",
        phantom_steed_height=6,
        phantom_saddle_height=4,
    )


def _source(state):
    return state[f"{SRD}.state"]["phantom_steed_sources"][-1]


def test_pinned_source_horse_and_exact_body_fade_are_later_consumed(steed_scope):
    assert _horse()["hit_points"] == 13
    state, _ = _case()
    _install(state)
    horse = state[f"{SRD}.entities"]["horse"]
    assert horse["hp"] == 13 and horse["armor_class"] == 11 and horse["speed"] == 100
    assert horse["size_rank"] == 3 and horse["ability_scores"]["strength"] == 16
    source = _source(state)
    assert source["status"] == "active" and source["expires_tick"] == 1200
    assert len(source["equipment_ids"]) == 3
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    assert _source(state)["status"] == "fading"
    assert _source(state)["ended_tick"] == 1200 and _source(state)["fade_until"] == 1220
    assert (
        next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "horse")[
            "active"
        ]
        is True
    )
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert _source(state)["status"] == "retired"
    assert state[f"{SRD}.entities"]["horse"]["body_available"] is False
    assert (
        next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "horse")[
            "active"
        ]
        is False
    )


def test_chosen_mount_and_dismount_spend_real_half_speed(steed_scope):
    state, _ = _case()
    _install(state)
    identity = _source(state)["id"]
    _run("mount_srd_phantom_steed", state, actor_state="hero", source_id=identity)
    assert _point(state, "hero") == [0, 10, 4]
    assert state[f"{SRD}.entities"]["hero"]["scene_movement_spent"] == 15
    assert _source(state)["rider"] == "hero"
    _run(
        "dismount_srd_phantom_steed",
        state,
        actor_state="hero",
        source_id=identity,
        destination=[0, 4, 0],
    )
    assert _point(state, "hero") == [0, 4, 0]
    assert state[f"{SRD}.entities"]["hero"]["scene_movement_spent"] == 30
    assert _source(state)["rider"] == ""


def test_horse_movement_carries_real_rider_and_tack_separation_is_consumed(steed_scope):
    state, _ = _case()
    _install(state)
    identity = _source(state)["id"]
    _run("mount_srd_phantom_steed", state, actor_state="hero", source_id=identity)
    _run(
        "move_srd_scene_creature", state, actor_state="horse", destination=[20, 10, 0], mode="walk"
    )
    assert _point(state, "horse") == [20, 10, 0]
    assert _point(state, "hero") == [20, 10, 4]
    assert state[f"{SRD}.entities"]["horse"]["scene_movement_spent"] == 20
    _run(
        "handle_srd_phantom_steed_equipment",
        state,
        actor_state="hero",
        source_id=identity,
        equipment_id=_source(state)["equipment_ids"][1],
        operation="take",
    )
    _run(
        "dismount_srd_phantom_steed",
        state,
        actor_state="hero",
        source_id=identity,
        destination=[20, 4, 0],
    )
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[20, -1, 0], mode="walk")
    tack = next(
        e
        for e in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if e["id"] == _source(state)["equipment_ids"][1]
    )
    assert tack["active"] is False
    assert _source(state)["status"] == "active"


@pytest.mark.parametrize("immune", [False, True])
def test_actual_temp_hp_receiving_packet_ends_only_damaged_source(steed_scope, immune):
    state, _ = _case()
    _install(state)
    state[f"{SRD}.entities"]["horse"].update(
        temp_hp=10, damage_immunities=["fire"] if immune else []
    )
    _run(
        "apply_srd_damage",
        state,
        target="horse",
        amount=1,
        adjustment=0,
        damage_type="fire",
        is_melee=False,
        incoming_attack_total=0,
    )
    assert _source(state)["status"] == ("active" if immune else "fading")
    assert state[f"{SRD}.entities"]["horse"]["hp"] == 13
    assert state[f"{SRD}.entities"]["horse"]["temp_hp"] == (10 if immune else 9)
    if not immune:
        assert _source(state)["fade_until"] == 20
        _run("advance_srd_lasting_time", state, elapsed_rounds=9)
        assert _source(state)["status"] == "fading"
        _run("advance_srd_lasting_time", state, elapsed_rounds=1)
        assert _source(state)["status"] == "retired"


def test_unowned_rider_and_unreceipted_position_cannot_bypass_source(steed_scope):
    state, _ = _case()
    _install(state)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="chosen rider"):
        _run("mount_srd_phantom_steed", state, actor_state="guard", source_id=_source(state)["id"])
    assert state == before
    with pytest.raises(RulesetPreconditionRejected, match="actual source mount"):
        _run(
            "move_srd_phantom_steed_rider",
            state,
            subject="hero",
            destination=[100, 100, 4],
            plane="material",
        )
    assert state == before


@pytest.mark.parametrize("immune", [False, True])
def test_actual_composite_receiving_packet_preserves_damage_grace(steed_scope, immune):
    state, _ = _case()
    _install(state)
    horse = state[f"{SRD}.entities"]["horse"]
    horse.update(temp_hp=10, damage_immunities=["fire", "cold"] if immune else [])
    _run(
        "apply_srd_qualifying_composite_damage",
        state,
        target="horse",
        component_1_amount=1,
        component_1_damage_type="fire",
        component_2_amount=1,
        component_2_damage_type="cold",
        adjustment=0,
        source_state_id="hero",
        source_damage_kind="spell",
    )
    assert _source(state)["status"] == ("active" if immune else "fading")
    horse = state[f"{SRD}.entities"]["horse"]
    assert horse["hp"] == 13 and horse["temp_hp"] == (10 if immune else 8)
    if not immune:
        assert _source(state)["ended_tick"] == 0 and _source(state)["fade_until"] == 20


def _paid_case(_spell):
    state, data, parameters = spell_case("phantom_steed")
    grounded, _ = _tk_case("phantom_steed")
    state[f"{SRD}.state"]["spatial_scene"] = grounded[f"{SRD}.state"]["spatial_scene"]
    for row in data["spellcasting_grants"]:
        if row["source"] == "wizard":
            row["spells"].append("magic_missile")
    data["wizard_spellbook"].append("magic_missile")
    actor = state[f"{SRD}.entities"]["hero"]
    actor["prepared_spells"].append("magic_missile")
    actor["resources"]["spell_slot_1"] = 3
    parameters.pop("targets", None)
    parameters.update(
        phantom_steed_id="horse",
        phantom_steed_position=[0, 10, 0],
        phantom_steed_appearance="a pale horse",
        phantom_steed_height=6,
        phantom_saddle_height=4,
    )
    return state, data, parameters


def test_paid_minute_cast_real_riding_and_damage_grace_reopens_and_retries(
    steed_scope,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(public, "_spell_case", _paid_case)
    path = tmp_path / "steed.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, "phantom_steed")
        command = ActionCommand(
            campaign_id="casting",
            action_id="begin",
            idempotency_key="begin",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="begin_srd_spell_cast",
            parameters=_begin(parameters),
        )
        mechanics = StatelessMcpMechanics(owner)
        begin = mechanics.execute_action(command)
        assert begin.status is ActionStatus.COMMITTED, begin.result
        initial = owner.snapshot("casting")
        assert initial.state[f"{SRD}.entities"]["hero"]["extended_casting"] is not None
        assert "horse" not in initial.state[f"{SRD}.entities"]
        assert mechanics.execute_action(command) == begin
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == initial
        continuation = replace(
            command,
            action_id="continue",
            idempotency_key="continue",
            expected_revision=initial.revision,
            tool_name="continue_srd_spell_cast",
            parameters={
                "caster_state": "hero",
                "elapsed_rounds": 10,
                "verbal_component_available": True,
                "somatic_component_available": True,
                "uninterrupted": True,
            },
        )
        continued = owner.execute_action(continuation)
        assert continued.status is ActionStatus.COMMITTED, continued.result
        progress = owner.snapshot("casting")
        completion = replace(
            command,
            action_id="complete",
            idempotency_key="complete",
            expected_revision=progress.revision,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        paid = owner.execute_action(completion)
        assert paid.status is ActionStatus.COMMITTED, paid.result
        created = owner.snapshot("casting")
        assert created.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
        source = _source(created.state)
        assert source["started_tick"] == 20 and source["expires_tick"] == 1220
        assert owner.execute_action(completion) == paid and owner.snapshot("casting") == created
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == created

        def act(identity, name, params):
            chosen = replace(
                command,
                action_id=identity,
                idempotency_key=identity,
                expected_revision=owner.snapshot("casting").revision,
                tool_name=name,
                parameters=params,
            )
            result = owner.execute_action(chosen)
            assert result.status is ActionStatus.COMMITTED, result.result
            saved = owner.snapshot("casting")
            assert owner.execute_action(chosen) == result and owner.snapshot("casting") == saved
            return saved

        mounted = act(
            "mount", "mount_srd_phantom_steed", {"actor_state": "hero", "source_id": source["id"]}
        )
        assert _point(mounted.state, "hero") == [0, 10, 4]
        travelled = act(
            "ride",
            "move_srd_scene_creature",
            {"actor_state": "horse", "destination": [20, 10, 0], "mode": "walk"},
        )
        assert _point(travelled.state, "hero") == [20, 10, 4]
        _, _, missile = spell_case("magic_missile")
        missile.update(target="horse", target_distance=4)
        damaged = act("missile", "cast_spell_outside_combat", missile)
        assert _source(damaged.state)["status"] == "fading"
        assert _source(damaged.state)["fade_until"] == 40
        grace = act("grace", "advance_srd_lasting_time", {"elapsed_rounds": 1})
        assert _source(grace.state)["status"] == "fading"
        dismounted = act(
            "dismount",
            "dismount_srd_phantom_steed",
            {"actor_state": "hero", "source_id": source["id"], "destination": [20, 4, 0]},
        )
        assert _point(dismounted.state, "hero") == [20, 4, 0]
        final = act("finish-fade", "advance_srd_lasting_time", {"elapsed_rounds": 9})
        assert _source(final.state)["status"] == "retired"
        assert not final.state[f"{SRD}.entities"]["horse"]["body_available"]
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
