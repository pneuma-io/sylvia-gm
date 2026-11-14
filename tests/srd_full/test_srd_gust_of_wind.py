"""Paid Gust sources produce real saving throws, movement costs and later consequences."""

import hashlib
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.srd_divination_spells import _normalize
from scripts.srd_full.srd_gust_of_wind import (
    PROFILES,
    PUBLIC_TOOLS,
    bind_runtime_consumers,
    extend_runtime,
    validate_profiles,
)
from scripts.srd_full.srd_spell_object_content import _fact_tools
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application import package_runtime
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_telekinesis_fall as scope
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_travel import SRD, _point
from tests.srd_full.test_srd_spell_travel import _case as spell_case
from tests.srd_full.test_srd_spell_travel_telekinesis import _rolls, _tk_case

BASE = Path(str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-gust-of-wind/input-1"))
RUNTIME = BASE / "fc14-spells-20260930-wave8/portals/.scratch/rope32-runtime-immutable/runtime.yaml"


@lru_cache
def _runtime():
    """Retain authentic immutable owners in the bounded diagnostic archive."""
    encoded = RUNTIME.read_bytes()
    assert hashlib.sha256(encoded).hexdigest() == (
        "e3c4858d2b42efd1b4aba54ae570ca55a53cb87f83b6eab9b8b894fe0ad1a813"
    )
    text = bind_runtime_consumers(extend_runtime(encoded.decode().replace("\r\n", "\n")))
    runtime = _load_runtime(text)
    # Compose this exact current native fact owner absent from the older selected archive.
    native_flame = {"record_srd_ordinary_flame": _fact_tools()["record_srd_ordinary_flame"]}
    _normalize(native_flame)
    runtime["tools"].update(native_flame)
    runtime["definitions"]["spells"].update(deepcopy(PROFILES))
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
def gust_scope(monkeypatch):
    monkeypatch.setattr(scope, "_runtime", _runtime)
    monkeypatch.setattr(scope, "EXTRA_PUBLIC_TOOLS", frozenset(PUBLIC_TOOLS))
    execute = package_runtime.execute_tool

    def evidence(*args, **kwargs):
        result = execute(*args, **kwargs)
        if not result.success:
            print("Selected public-source fixture failure:", result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", evidence)
    yield from scope.actual_fall_scope.__wrapped__(monkeypatch)


def _case():
    return _tk_case("gust_of_wind")


def _install(state):
    return _run(
        "cast_srd_gust_of_wind",
        state,
        caster_state="hero",
        slot_level=2,
        save_dc=15,
        concentration_modifier=3,
    )


def _source(state):
    return state[f"{SRD}.state"]["gust_sources"][-1]


@pytest.mark.parametrize("face, pushed", [(1, True), (20, False)])
def test_actual_initial_strength_save_derives_forced_endpoint_without_target_budget(
    gust_scope, monkeypatch, face, pushed
):
    assert validate_profiles()
    state, _ = _case()
    actual = _rolls(monkeypatch, [face])
    _install(state)
    assert _point(state, "guard") == ([20, 0, 0] if pushed else [5, 0, 0])
    assert actual.consumed == [(20, face)]
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard.get("scene_movement_spent", 0) == 0
    assert guard["last_gust_save"]["pushed"] is pushed
    assert _source(state)["events"][0]["outcome"] == ("failure" if pushed else "success")
    assert state[f"{SRD}.state"].get("gust_pending_push") is None


def test_actual_movement_cost_is_directional_and_expiry_retires_own_concentration(
    gust_scope, monkeypatch
):
    state, _ = _case()
    _rolls(monkeypatch, [1])
    _install(state)
    inward = _run(
        "move_srd_scene_creature", state, actor_state="guard", destination=[10, 0, 0], mode="walk"
    )
    assert inward.result["context"]["_scene_movement_cost"] == 20
    assert state[f"{SRD}.entities"]["guard"]["scene_movement_spent"] == 20
    outward = _run(
        "move_srd_scene_creature", state, actor_state="guard", destination=[20, 0, 0], mode="walk"
    )
    assert outward.result["context"]["_scene_movement_cost"] == 10
    assert _point(state, "guard") == [20, 0, 0]
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert _source(state)["active"] is False
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    normal = _run(
        "move_srd_scene_creature", state, actor_state="guard", destination=[10, 0, 0], mode="walk"
    )
    assert normal.result["context"]["_scene_movement_cost"] == 10


def test_unreceipted_or_unsupported_direction_cannot_forge_actual_push(gust_scope):
    state, _ = _case()
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(
            "push_srd_gust_subject",
            state,
            subject="guard",
            destination=[20, 0, 0],
            plane="material",
        )
    assert state == before
    with pytest.raises(RulesetPreconditionRejected, match="cardinal"):
        _run(
            "cast_srd_gust_of_wind",
            state,
            caster_state="hero",
            slot_level=2,
            save_dc=15,
            concentration_modifier=3,
            gust_direction=[1, 1, 0],
        )
    assert state == before


@pytest.mark.parametrize(
    "protected, face, burning", [(False, None, False), (True, 1, False), (True, 2, True)]
)
def test_native_flame_protection_uses_real_one_half_chance(
    gust_scope, monkeypatch, protected, face, burning
):
    state, _ = _case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    flame = deepcopy(next(e for e in scene["entities"] if e["id"] == "orb"))
    flame.update(
        id="candle",
        position=[15, 0, 1],
        height=0.5,
        footprint={"kind": "box", "half_extents": [0.2, 0.2]},
    )
    scene["entities"].append(flame)
    _run("record_srd_ordinary_flame", state, object_id="candle", exposed=True)
    _run("record_srd_wind_flame_protection", state, object_id="candle", protected=protected)
    from tests.srd_full.test_srd_spell_travel import _TravelDice, dice

    rolls = _TravelDice([(20, 20)] + ([(2, face)] if face is not None else []))
    monkeypatch.setattr(dice, "_rng", rolls)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    _install(state)
    flame = next(
        e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "candle"
    )
    assert flame["metadata"]["srd_ordinary_flame"]["burning"] is burning
    assert rolls.consumed == [(20, 20)] + ([(2, face)] if face is not None else [])
    source_id = _source(state)["id"]
    _run("resolve_srd_gust_world", state, source_id=source_id)
    assert len(_source(state)["flames"]) == 1
    if protected and burning:
        assert flame["metadata"]["srd_ordinary_flame"]["dancing_wildly"] is True
        _run(
            "move_srd_scene_creature",
            state,
            actor_state="hero",
            destination=[0, 10, 0],
            mode="walk",
        )
        moved = next(
            e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "candle"
        )["metadata"]["srd_ordinary_flame"]
        assert moved["burning"] is True and moved["dancing_wildly"] is False
        assert moved["gust_dancing_sources"] == []
        assert rolls.consumed == [(20, 20), (2, 2)]
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    retired = next(
        e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "candle"
    )["metadata"]["srd_ordinary_flame"]
    assert retired["burning"] is burning
    assert retired.get("dancing_wildly", False) is False


def test_true_ending_turn_pushes_once_and_later_caster_turn_spends_bonus(gust_scope, monkeypatch):
    state, data = _case()
    _run("begin_combat_turn", state, actor="hero", participant_data=data)
    state[f"{SRD}.combat"]["encounter_active"] = True
    actual = _rolls(monkeypatch, [20, 1])
    _install(state)
    source_id = _source(state)["id"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="later caster turn"):
        _run(
            "change_srd_gust_direction",
            state,
            actor_state="hero",
            source_id=source_id,
            direction=[0, 1, 0],
        )
    assert state == before
    _run("end_combat_turn", state, actor="hero")
    # Admit the next initiative actor; begin/end bind from this saved state.
    state[f"{SRD}.combat"]["current_actor"] = "guard"
    _run("begin_combat_turn", state, actor="guard")
    qualified = _run("qualify_srd_gust_body", state, source_id=source_id, subject="guard").result[
        "context"
    ]
    assert qualified["_gust_inside"] is True, qualified
    assert state[f"{SRD}.entities"]["guard"]["is_alive"] is True
    ended = _run("end_combat_turn", state, actor="guard")
    assert _point(state, "guard") == [20, 0, 0]
    assert actual.consumed == [(20, 20), (20, 1)]
    assert any(
        "Gust failure" in row["value"] and "save 1" in row["value"]
        for row in ended.result["context"]["_end_turn_rows"]
    )
    state[f"{SRD}.combat"]["current_actor"] = "hero"
    _run("begin_combat_turn", state, actor="hero", participant_data=data)
    _run(
        "change_srd_gust_direction",
        state,
        actor_state="hero",
        source_id=source_id,
        direction=[0, 1, 0],
    )
    assert _source(state)["direction"] == [0, 1, 0]
    assert state[f"{SRD}.entities"]["hero"]["bonus_action_available"] is False


def test_real_source_fog_retires_only_when_actual_strong_wind_reaches_it(gust_scope, monkeypatch):
    state, _ = _case()
    _run(
        "cast_spell_environment",
        state,
        caster_state="guard",
        spell="fog_cloud",
        slot_level=1,
        concentration_save_modifier=0,
        save_dc=10,
        origin=[40, 0, 0],
    )
    area_id = state[f"{SRD}.entities"]["guard"]["environment_area_id"]
    _rolls(monkeypatch, [20])
    _install(state)
    assert not state[f"{SRD}.state"]["persistent_spell_areas"][area_id]["active"]
    assert state[f"{SRD}.entities"]["guard"]["concentration_spell_id"] is None
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] == "gust_of_wind"


def _paid_case(_spell):
    state, data = _case()
    _, _, parameters = spell_case("gust_of_wind")
    parameters.pop("targets", None)
    return state, data, parameters


def test_paid_public_cast_directional_motion_redirection_expiry_reopens_and_retries(
    gust_scope, monkeypatch, tmp_path
):
    monkeypatch.setattr(public, "_spell_case", _paid_case)
    _rolls(monkeypatch, [1])
    path = tmp_path / "gust.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, "gust_of_wind")
        command = ActionCommand(
            campaign_id="casting",
            action_id="gust",
            idempotency_key="gust",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        mechanics = StatelessMcpMechanics(owner)
        result = mechanics.execute_action(command)
        assert result.status is ActionStatus.COMMITTED, result.result
        created = owner.snapshot("casting")
        assert _point(created.state, "guard") == [20, 0, 0]
        source_id = _source(created.state)["id"]
        assert created.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
        assert mechanics.execute_action(command) == result and owner.snapshot("casting") == created
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

        moved = act(
            "inward",
            "move_srd_scene_creature",
            {"actor_state": "guard", "destination": [10, 0, 0], "mode": "walk"},
        )
        assert moved.state[f"{SRD}.entities"]["guard"]["scene_movement_spent"] == 20
        act("later-round", "advance_srd_lasting_time", {"elapsed_rounds": 1})
        changed = act(
            "redirect",
            "change_srd_gust_direction",
            {"actor_state": "hero", "source_id": source_id, "direction": [0, 1, 0]},
        )
        assert _source(changed.state)["direction"] == [0, 1, 0]
        normal = act(
            "outside-new-line",
            "move_srd_scene_creature",
            {"actor_state": "guard", "destination": [6, 0, 0], "mode": "walk"},
        )
        assert normal.state[f"{SRD}.entities"]["guard"]["scene_movement_spent"] == 4
        final = act("expire", "advance_srd_lasting_time", {"elapsed_rounds": 9})
        assert _source(final.state)["active"] is False
        assert final.state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
