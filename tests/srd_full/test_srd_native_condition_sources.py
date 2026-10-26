"""Real created-body Claw saves remain physical through Freedom and source-pool reuse."""

import importlib.util
import json
import os
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_native_condition_sources import bind_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime, _RuntimeDumper

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_spell_wave3_information import SRD, _case, _entity, _free

ROOT = Path(__file__).resolve().parents[2]


@lru_cache
def _runtime():
    """Compose the immutable real native producer, then select its complete callable closure."""
    import scripts.srd_full.srd_spell_summoning as summoning

    if path := os.environ.get("FC14_NATIVE_CONDITION_SUMMONING"):
        spec = importlib.util.spec_from_file_location("scripts._native_summoning", path)
        summoning = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(summoning)
        # The frozen producer reads only the unchanged public corpus in this isolate.
        summoning.__file__ = str(ROOT / "scripts/srd_spell_summoning.py")
    text = Path(
        os.environ.get(
            "FC14_NATIVE_CONDITION_RUNTIME", ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml"
        )
    ).read_text()
    text = summoning.extend_runtime(text)
    text = summoning.bind_animated_undead_turn_consumers(text)
    text = bind_runtime(text)
    assert bind_runtime(text) == text
    runtime = _load_runtime(text)
    runtime["definitions"]["spells"]["create_undead"] = summoning.CREATED_UNDEAD_PROFILE
    for tool in runtime["tools"].values():
        for parameter in tool["parameters"]:
            if parameter["name"] == "spell" and "enum_values" in parameter:
                parameter["enum_values"] = sorted(set(parameter["enum_values"]) | {"create_undead"})
    pending = [
        "create_srd_created_ghoul_body",
        "resolve_srd_created_ghoul_claw",
        "check_srd_native_condition_source",
        "refresh_srd_timed_condition_target",
        "install_srd_actor_spell_modifier",
        "install_srd_timed_condition_source",
        "retire_srd_timed_condition_source",
        "expire_srd_created_ghoul_claw_sources",
        "apply_srd_damage",
        "advance_srd_lasting_clock",
        "install_srd_transformation_source",
        "cast_spell_transformation",
        "prepare_srd_creature",
        "command_srd_animated_undead",
        "start_group_combat_encounter",
        "begin_combat_turn",
        "end_combat_turn",
        "advance_group_combat_encounter_turn",
    ]
    selected = {}
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        selected[name] = runtime["tools"][name]
        stack = [selected[name]]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                if "call" in item:
                    pending.append(item["call"])
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
    closure = runtime | {"tools": selected}
    assert not (errors := validate_ruleset_data(closure)), errors
    return closure, yaml.dump(closure, Dumper=_RuntimeDumper, sort_keys=False).encode()


@lru_cache
def _mechanic(name):
    runtime, encoded = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="5.2.1",
        package_sha256="c" * 64,
        runtime_bytes=encoded,
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
def _owner(monkeypatch):
    import sylvia_gm.application.package_runtime as package

    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: _runtime()[0]["definitions"]["spells"])
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    original = package.execute_tool

    def checked(name, *args, **kwargs):
        result = original(name, *args, **kwargs)
        assert result.success, (name, result.error)
        return result

    monkeypatch.setattr(package, "execute_tool", checked)


def _run(tool_name, state, data, **parameters):
    return budget._run(tool_name, state, participant_data=data, **parameters).result["context"]


def _native_body(*, with_mage=False, form_id="ghoul"):
    state, data, _ = _case("create_undead")
    data["species"] = "human"
    if form_id == "ghast":
        state[f"{SRD}.entities"]["hero"]["creature_type"] = "humanoid"
        state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
            _entity("other", [0, 30, 0], participant_ref="other", creature_type="humanoid")
        )
    _free(state)
    _run("advance_srd_lasting_clock", state, data, lasting_state="state", elapsed_ticks=1)
    corpse = state[f"{SRD}.entities"]["guard"]
    corpse.update(
        creature_kind="monster",
        creature_type="humanoid",
        living_creature_type="humanoid",
        size_rank=2,
        inventory=[],
        equipment=[],
        body_available=True,
        vital_parts_present=True,
        soul_willing=True,
    )
    scene = state[f"{SRD}.state"]["spatial_scene"]["entities"]
    scene[1]["position"] = [5, 0, 0]
    scene[1]["metadata"]["creature_type"] = "humanoid"
    _run(
        "apply_srd_damage",
        state,
        data,
        target="guard",
        amount=corpse["hp"] + corpse["temp_hp"],
        adjustment=0,
        damage_type="radiant",
        critical_hit=False,
    )
    _run(
        f"create_srd_created_{form_id}_body",
        state,
        data,
        corpse_state="guard",
        caster_state="hero",
        individual_id="ghoul_0",
        appearance_height=6,
    )
    if with_mage:
        _run(
            "prepare_srd_creature",
            state,
            data,
            creature_id="mage",
            name="NPC Mage",
            source="Synthetic admitted caster: Wisdom +3, spell attack +7, DC15, CON save +6.",
            armor_class=12,
            hit_points=25,
            speed=30,
            initiative_modifier=20,
            size_rank=2,
            constitution_save=6,
            spellcasting_ability="wisdom",
            spellcasting_modifier=3,
            spell_attack_bonus=7,
            spell_save_dc=15,
            spells=[{"spell": "polymorph", "uses_per_day": 1}],
        )
    _run(
        "command_srd_animated_undead",
        state,
        data,
        caster_state="hero",
        actor_ids=["ghoul_0"],
        order_kind="specific",
        action="attack",
    )
    state[f"{SRD}.entities"]["hero"]["initiative_modifier"] = (
        data["abilities"]["dexterity"] - 10
    ) // 2
    state[f"{SRD}.entities"]["other"]["initiative_modifier"] = 0
    _run("start_group_combat_encounter", state, data, combatants=["hero", "ghoul_0", "other"])
    for _ in range(3):
        actor = state[f"{SRD}.combat"]["current_actor"]
        _run("begin_combat_turn", state, data, actor=actor)
        if actor == "ghoul_0":
            break
        _run("end_combat_turn", state, data)
        _run("advance_group_combat_encounter_turn", state, data)
    assert state[f"{SRD}.combat"]["current_actor"] == "ghoul_0"
    return state, data


def _freedom(state, data):
    return _run(
        "install_srd_actor_spell_modifier",
        state,
        data,
        target_state="hero",
        caster_state="hero",
        lasting_state="state",
        spell="freedom_of_movement",
        mode="freedom",
        save_dc=15,
        duration_rounds=600,
        concentration=False,
        actual_spell_level=4,
    )


def _claw(state, data, monkeypatch, form_id="ghoul"):
    class FailedSave:
        def randint(self, low, high):
            assert (low, high) == (1, 20)
            return 1

    monkeypatch.setattr(dice, "_rng", FailedSave())
    return _run(
        f"resolve_srd_created_{form_id}_claw",
        state,
        data,
        actor_state="ghoul_0",
        target_state="hero",
        lasting_state="state",
    )


def test_real_native_claw_remains_paralyzed_under_freedom_after_reopen(monkeypatch):
    state, data = _native_body()
    _freedom(state, data)
    context = _claw(state, data, monkeypatch)
    sid = context["_ghoul_claw_source_id"]
    assert context["_ghoul_claw_save_outcome"] == "failure"
    assert (
        _run(
            "check_srd_native_condition_source", state, data, source_id=sid, lasting_state="state"
        )["_native_condition_source"]
        is True
    )
    state = json.loads(json.dumps(state))
    _run(
        "refresh_srd_timed_condition_target",
        state,
        data,
        target_state="hero",
        lasting_state="state",
    )
    hero = state[f"{SRD}.entities"]["hero"]
    source = state[f"{SRD}.state"]["timed_condition_sources"][sid]
    assert source["installed"] and not source["suppressed"]
    assert "paralyzed" in hero["conditions"] and hero["spell_freedom_of_movement"] is True
    hero["turns_started"] = source["target_end_turn"]
    _run(
        "expire_srd_created_ghoul_claw_sources",
        state,
        data,
        target_state="hero",
        lasting_state="state",
    )
    assert (
        not source["installed"]
        or not state[f"{SRD}.state"]["timed_condition_sources"][sid]["installed"]
    )
    assert "paralyzed" not in state[f"{SRD}.entities"]["hero"]["conditions"]


@pytest.mark.parametrize(
    "change", ["dead_attacker", "different_body", "native_immunity", "body_immunity"]
)
def test_native_history_survives_attacker_changes_and_honors_target_immunity(monkeypatch, change):
    state, data = _native_body(with_mage=change == "different_body")
    sid = _claw(state, data, monkeypatch)["_ghoul_claw_source_id"]
    if change == "dead_attacker":
        _run(
            "apply_srd_damage",
            state,
            data,
            target="ghoul_0",
            amount=100,
            adjustment=0,
            damage_type="radiant",
            critical_hit=False,
        )
    elif change == "different_body":
        profile = state[f"{SRD}.entities"]["mage"]["spellcasting_profile"]
        _run(
            "cast_spell_transformation",
            state,
            data,
            target_state="ghoul_0",
            caster_state="mage",
            lasting_state="state",
            spell="polymorph",
            form_id="wolf",
            actual_spell_level=4,
            save_dc=profile["save_dc"],
            casting_modifier=profile["modifier"],
            casting_ability=profile["ability"],
            target_distance=5,
            caster_can_see_target=True,
            target_willing=True,
            forego_save_targets=["ghoul_0"],
            concentration_save_modifier=profile["concentration_save_modifier"],
        )
        ghoul = state[f"{SRD}.entities"]["ghoul_0"]
        assert ghoul["form_statistics_replaced"] is True and ghoul["form_appearance"] == "Wolf"
        assert (
            state[f"{SRD}.state"]["transformation_sources"][ghoul["form_current_source_id"]][
                "form_id"
            ]
            == "wolf"
        )
    elif change == "native_immunity":
        state[f"{SRD}.entities"]["hero"]["condition_immunities"] = ["paralyzed"]
    else:
        state[f"{SRD}.state"]["timed_condition_sources"][sid]["body_immunity_suppressed"] = True
    _freedom(state, data)
    _run(
        "refresh_srd_timed_condition_target",
        state,
        data,
        target_state="hero",
        lasting_state="state",
    )
    assert (
        _run(
            "check_srd_native_condition_source", state, data, source_id=sid, lasting_state="state"
        )["_native_condition_source"]
        is True
    )
    hero = state[f"{SRD}.entities"]["hero"]
    source = state[f"{SRD}.state"]["timed_condition_sources"][sid]
    assert source["installed"] is True
    assert ("paralyzed" in hero["conditions"]) is (change in ("dead_attacker", "different_body"))


def test_reused_native_pool_slot_cannot_exempt_the_new_magical_source(monkeypatch):
    state, data = _native_body()
    sid = _claw(state, data, monkeypatch)["_ghoul_claw_source_id"]
    _run("retire_srd_timed_condition_source", state, data, source_id=sid, lasting_state="state")
    context = _run(
        "install_srd_timed_condition_source",
        state,
        data,
        target_state="hero",
        caster_state="guard",
        lasting_state="state",
        spell="hold_person",
        condition="paralyzed",
        duration_rounds=10,
        save_dc=10,
        save_ability="wisdom",
        repeat_save_timing="none",
    )
    assert context["_timed_condition_source_id"] == sid
    assert (
        _run(
            "check_srd_native_condition_source", state, data, source_id=sid, lasting_state="state"
        )["_native_condition_source"]
        is False
    )
    _freedom(state, data)
    _run(
        "refresh_srd_timed_condition_target",
        state,
        data,
        target_state="hero",
        lasting_state="state",
    )
    assert "paralyzed" not in state[f"{SRD}.entities"]["hero"]["conditions"]
    assert state[f"{SRD}.state"]["timed_condition_sources"][sid]["suppressed"] is True


def test_exact_native_retirement_preserves_an_independent_suppressed_spell(monkeypatch):
    state, data = _native_body()
    native = _claw(state, data, monkeypatch)["_ghoul_claw_source_id"]
    magical = _run(
        "install_srd_timed_condition_source",
        state,
        data,
        target_state="hero",
        caster_state="guard",
        lasting_state="state",
        spell="hold_person",
        condition="paralyzed",
        duration_rounds=1000,
        save_dc=10,
        save_ability="wisdom",
        repeat_save_timing="none",
    )["_timed_condition_source_id"]
    assert native != magical
    _freedom(state, data)
    pool = state[f"{SRD}.state"]["timed_condition_sources"]
    assert not pool[native]["suppressed"] and pool[magical]["suppressed"]
    assert "paralyzed" in state[f"{SRD}.entities"]["hero"]["conditions"]
    _run("retire_srd_timed_condition_source", state, data, source_id=native, lasting_state="state")
    _run(
        "refresh_srd_timed_condition_target",
        state,
        data,
        target_state="hero",
        lasting_state="state",
    )
    assert "paralyzed" not in state[f"{SRD}.entities"]["hero"]["conditions"]
    assert state[f"{SRD}.state"]["timed_condition_sources"][magical]["installed"] is True
    assert state[f"{SRD}.state"]["timed_condition_sources"][magical]["suppressed"] is True


def test_classification_requires_actual_body_provenance_not_a_false_magic_flag(monkeypatch):
    state, data = _native_body()
    sid = _claw(state, data, monkeypatch)["_ghoul_claw_source_id"]
    for field in ("spell", "individual", "body_source", "body_source_id", "source_block_sha256"):
        forged = deepcopy(state)
        forged[f"{SRD}.state"]["created_actor_records"]["ghoul_0"][field] = None
        assert (
            _run(
                "check_srd_native_condition_source",
                forged,
                data,
                source_id=sid,
                lasting_state="state",
            )["_native_condition_source"]
            is False
        )
