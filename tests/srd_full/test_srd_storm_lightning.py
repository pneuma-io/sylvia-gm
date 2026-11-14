"""The actual third Storm start owns six real Lightning choices and one shared roll."""

from copy import deepcopy
from functools import lru_cache
from unittest.mock import patch

import pytest
import yaml
from scripts import srd_reaction_spells as reactions
from scripts import srd_storm_lightning as lightning
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema

from sylvia_gm import dice
from sylvia_gm.application import package_runtime
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime._types import ExecutionBudget
from tests import test_srd_storm_objects as base
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_wave3_information import _entity
from tests.srd_full.test_srd_sunbeam import Rolls

_BASE_RUNTIME = base._runtime
TARGETS = ["guard", "other", "orb", "box-a", "box-b", "box-c"]


def _fence(runtime):
    """Compose the existing final pending-window fence after the new public owner."""
    fenced = {}
    with patch.object(reactions, "ORDINARY_SUPPORT_TOOLS", lightning.PUBLIC_TOOLS):
        reactions._ordinary_support_fences(runtime["tools"], fenced)
    runtime["tools"].update(fenced)
    return runtime


@lru_cache
def _runtime():
    parent = _BASE_RUNTIME()[0]
    encoded = lightning.bind_runtime_consumers(
        yaml.dump(parent, Dumper=yaml.CSafeDumper, sort_keys=False)
    )
    runtime = yaml.load(encoded, Loader=yaml.CSafeLoader)
    _fence(runtime)
    for name in (
        "d20_test",
        "apply_srd_qualifying_damage",
        "modify_srd_actor_damage_roll",
        "resolve_spell_area_save_damage_target",
        "resolve_srd_save_damage",
        "resolve_srd_spell_object_attack",
    ):
        assert runtime["tools"][name] == parent["tools"][name]
    return runtime, yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode()


@lru_cache
def _mechanic(name):
    runtime, encoded = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=encoded,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=_tool_schema(runtime["tools"][name], tool_name=name),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    runtime, _ = _runtime()
    monkeypatch.setattr(base.base.base.base, "_runtime", _runtime)
    monkeypatch.setattr(base.base.base.base.budget, "_mechanic", _mechanic)
    monkeypatch.setattr(
        base.base.base.base,
        "PUBLIC_TOOLS",
        base.base.base.base.PUBLIC_TOOLS | lightning.PUBLIC_TOOLS | {"prepare_srd_spell_object"},
    )
    base.base.base.base._archive.cache_clear()
    monkeypatch.setattr(
        base.base.base.base.cards, "_spell_definitions", lambda: runtime["definitions"]["spells"]
    )
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Rolls())
    actual = package_runtime.execute_tool

    def diagnose(*args, **kwargs):
        result = actual(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnose)


def _unpaid_case():
    state, data, params = base.base.base._case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    next(row for row in scene["entities"] if row["id"] == "orb")["position"] = [100, -100, 0]
    for name, point in zip(TARGETS[3:], ([80, 40, 0], [120, -60, 0], [200, 100, 0]), strict=True):
        row = _entity(name, point)
        row["metadata"].update(is_object=True, plane="material", lighting="bright")
        scene["entities"].append(row)
    for name, modifier in (("guard", -30), ("other", 30)):
        state[f"{SRD}.entities"][name]["saving_throw_modifiers"]["dexterity"] = modifier
    return state, data, params


def _case():
    state, data, params = _unpaid_case()
    for name in TARGETS[2:]:
        _run(
            "prepare_srd_spell_object",
            state,
            object_id=name,
            material="wood",
            size="large",
            durability="resilient",
        )
    base.base.base._cast(state, data, params)
    base.base._start(state, data, 6)
    base.base._start(state, data, 7)
    state[f"{SRD}.combat"].update(turn_active=True, active_actor="hero")
    return state, data


def _choose(state, data, targets=None):
    return _run(
        lightning.CHOOSE,
        state,
        participant_data=data,
        caster_state="hero",
        targets=TARGETS if targets is None else targets,
    ).result


def test_original_paid_stage_three_six_mixed_choices_share_roll_real_saves_and_no_object_d20(
    monkeypatch,
):
    state, data = _case()
    slots = deepcopy(state[f"{SRD}.entities"]["hero"]["resources"])
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    result = _choose(state, data)
    assert rolls.sides == [6] * 10 + [20, 20]
    entities = state[f"{SRD}.entities"]
    assert entities["guard"]["hp"] == 952
    assert entities["other"]["hp"] == 973
    assert entities["hero"]["resources"] == slots
    scene = state[f"{SRD}.state"]["spatial_scene"]
    for row in scene["entities"]:
        if row["id"] in TARGETS[2:]:
            assert row["metadata"]["srd_spell_object"]["hp"] == 0
            assert row["metadata"]["srd_spell_object"]["destroyed"] is True
            assert row["id"] not in entities
    record = base.base.base._sources(state)[0]
    assert record["later_effect_pending"] is False and record["last_resolved_ordinal"] == 3
    assert record["storm_last_stage_result"]["targets"] == TARGETS
    assert record["storm_last_stage_result"]["raw_roll"]["results"] == [3] * 10
    assert result["result_card"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="pending third-start"):
        _choose(state, data)
    assert state == before and rolls.sides == [6] * 10 + [20, 20]


def test_invalid_actual_geometry_and_wrong_turn_reject_before_claim_roll_or_damage(monkeypatch):
    state, data = _case()
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    next(row for row in scene["entities"] if row["id"] == "box-c")["position"] = [800, 0, 0]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="beneath"):
        _choose(state, data)
    assert state == before and rolls.sides == []
    state[f"{SRD}.combat"]["active_actor"] = "guard"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="current turn"):
        _choose(state, data)
    assert state == before and rolls.sides == []


def test_current_source_plane_cover_and_repeat_preserve_native_owners(monkeypatch):
    state, data = _case()
    row = next(r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"] if r["id"] == "box-c")
    row["metadata"]["cover_from"] = {"hero": "total"}
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Total Cover"):
        _choose(state, data)
    assert state == before
    row = next(r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"] if r["id"] == "box-c")
    row["metadata"].pop("cover_from")
    row["metadata"]["plane"] = "ethereal"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="beneath"):
        _choose(state, data)
    assert state == before
    runtime, encoded = _runtime()
    assert (
        _fence(
            yaml.load(lightning.bind_runtime_consumers(encoded.decode()), Loader=yaml.CSafeLoader)
        )
        == runtime
    )


def test_native_evasion_success_skips_the_whole_packet_with_a_fresh_zero_receipt(monkeypatch):
    from tests.srd_full.test_srd_unarmed_creatures import _case as native_case

    state, data = native_case()
    data.update(classes=[{"id": "monk", "level": 7}], level=7)
    state[f"{SRD}.entities"]["hero"].update(
        hp=100,
        max_hp=100,
        temp_hp=5,
        saving_throw_modifiers={"dexterity": 30},
    )
    before = deepcopy(state)
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    result = _run(
        lightning.TARGET,
        state,
        participant_data=data,
        spell="storm_of_vengeance",
        target="hero",
        save_ability="dexterity",
        spell_save_dc=15,
        damage_total=61,
        damage_type="lightning",
        success_damage="half",
        construct_save_disadvantage=False,
        half_cover_targets=[],
        three_quarters_cover_targets=[],
        remaining_penalty=0,
    ).result["context"]
    assert result["_storm_evasion"] is True
    assert result["_storm_lightning_target_damage"] == 0
    assert result["_affliction_trigger_damage"] == 0
    assert "Evasion" in str(result["_spell_detail_rows"])
    assert state == before and rolls.sides == [20]


def test_paid_ray_penalty_is_consumed_once_before_actual_lightning_save_half(monkeypatch):
    from tests.srd_full.test_srd_flame_blade_objects import _enfeeble

    state, data, params = _unpaid_case()
    for name in TARGETS[2:]:
        _run(
            "prepare_srd_spell_object",
            state,
            object_id=name,
            material="wood",
            size="large",
            durability="resilient",
        )
    base.base.base._cast(state, data, params)
    _enfeeble(state, data, position=(80, 0, 0))
    base.base._start(state, data, 6)
    base.base._start(state, data, 7)
    state[f"{SRD}.combat"].update(turn_active=True, active_actor="hero")
    hp_before = {name: state[f"{SRD}.entities"][name]["hp"] for name in TARGETS[:2]}

    class RayRolls(Rolls):
        def randint(self, low, high):
            if high == 8:
                self.sides.append(high)
                return 1
            return super().randint(low, high)

    rolls = RayRolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    _choose(state, data)
    assert rolls.sides == [6] * 10 + [8, 20, 20]
    receipt = base.base.base._sources(state)[0]["storm_last_stage_result"]
    assert receipt["raw_roll"]["results"] == [3] * 10
    assert receipt["damage_total"] == 29 and receipt["remaining_penalty"] == 0
    assert hp_before["guard"] - state[f"{SRD}.entities"]["guard"]["hp"] == 29
    assert hp_before["other"] - state[f"{SRD}.entities"]["other"]["hp"] == 14
    assert all(
        row["metadata"]["srd_spell_object"]["hp"] == 0
        for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if row["id"] in TARGETS[2:]
    )

    def nodes(value):
        if isinstance(value, dict):
            return 1 + len(value) + sum(nodes(child) for child in value.values())
        if isinstance(value, list):
            return 1 + sum(nodes(child) for child in value)
        return 1

    print(
        "Storm expanded nodes added (objects, Lightning with pending fence):",
        nodes(_BASE_RUNTIME()[0]) - nodes(base._BASE_RUNTIME()[0]),
        nodes(_runtime()[0]) - nodes(_BASE_RUNTIME()[0]),
    )


def test_paid_public_rain_then_saved_lightning_choice_reopens_and_retries_one_roll(
    tmp_path, monkeypatch
):
    public = base.base.base.base.public_casting
    monkeypatch.setattr(public, "bundled_srd_archive", base.base.base.base._archive)
    monkeypatch.setattr(public, "_spell_case", lambda _spell: _unpaid_case())
    rolls, budgets = Rolls(), []
    monkeypatch.setattr(dice, "_rng", rolls)
    consume = ExecutionBudget.consume_operation

    def count(budget):
        if not budgets or budgets[-1] is not budget:
            budgets.append(budget)
        consume(budget)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", count)
    database, receipts = tmp_path / "storm-lightning.db", []
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner,
            "storm_of_vengeance",
            target_overrides={"hp": 1000, "max_hp": 1000},
        )
        for name in TARGETS[2:]:
            receipts.append(
                _act(
                    owner,
                    "prepare_srd_spell_object",
                    object_id=name,
                    material="wood",
                    size="large",
                    durability="resilient",
                )
            )
        slots_before = owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]["resources"][
            "spell_slot_9"
        ]
        receipts.append(_act(owner, "cast_spell_outside_combat", **params))
        receipts.append(
            _act(
                owner,
                "start_combat_encounter_entities",
                first_actor="hero",
                second_actor="guard",
                first_surprised=False,
                second_surprised=False,
                tie_winner="first",
            )
        )
        receipts.append(_act(owner, "begin_combat_turn"))
        assert "object damage" in str(receipts[-1][0].result["result_card"])
        for name in (
            "end_combat_turn",
            "advance_combat_encounter_turn",
            "begin_combat_turn",
            "end_combat_turn",
            "advance_combat_encounter_turn",
            "begin_combat_turn",
        ):
            receipts.append(_act(owner, name))
        saved = owner.snapshot("casting")
        original = base.base.base._sources(saved.state)[0]
        assert original["storm_ordinal"] == 3 and original["later_effect_pending"] is True
        faces = deepcopy(rolls.sides)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        receipts.append(_act(owner, lightning.CHOOSE, caster_state="hero", targets=TARGETS))
        assert rolls.sides[len(faces) :] == [6] * 10 + [20, 20]
        saved = owner.snapshot("casting")
        record = base.base.base._sources(saved.state)[0]
        assert record["source_id"] == original["source_id"]
        assert record["later_effect_pending"] is False and record["last_resolved_ordinal"] == 3
        assert saved.state[f"{SRD}.combat"]["action_available"] is True
        assert (
            saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_9"] == slots_before - 1
        )
        assert receipts[-1][0].result["result_card"]
        faces = deepcopy(rolls.sides)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        for result, command in receipts:
            assert result.status is ActionStatus.COMMITTED
            assert owner.execute_action(command) == result
        assert owner.snapshot("casting") == saved and rolls.sides == faces
    assert all(budget.max_operations == 10000 for budget in budgets)
    print(
        "Storm object/Lightning public max operations:",
        max(budget.operations for budget in budgets),
    )
