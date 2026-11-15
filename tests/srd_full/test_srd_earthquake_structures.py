"""Real object HP, collapse saves and persistent rubble reach later actions and public retries."""

from copy import deepcopy
from random import Random

import pytest
import yaml
from scripts import srd_earthquake_structures as structures
from scripts import srd_magic_jar_container as jar_container
from scripts.srd_full.srd_runtime_defaults import restore_tool_defaults

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.runtime._types import ExecutionBudget
from tests import test_srd_action_budget as budget
from tests import test_srd_earthquake as quake
from tests import test_srd_earthquake_sources as sources
from tests import test_srd_magic_jar as jar
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_wave3_information import _entity

SRD = quake.SRD


@pytest.fixture(autouse=True)
def _compiled(monkeypatch):
    yield from quake._compiled.__wrapped__(monkeypatch)


def _scene(state):
    return state[f"{SRD}.state"]["spatial_scene"]


def _structure(state, object_id="pillar", *, position=None):
    return next(e for e in _scene(state)["entities"] if e["id"] == object_id)


def _case(*, hero_inside=False):
    state, data, params = sources._case()
    if hero_inside:
        next(e for e in _scene(state)["entities"] if e["id"] == "hero")["position"] = [150, -10, 0]
    next(e for e in _scene(state)["entities"] if e["id"] == "successful")["position"] = [
        155,
        -4.5,
        0,
    ]
    pillar = _entity("pillar", [150, -4.5, 0], structure=True)
    pillar["height"] = 10
    pillar["footprint"]["half_extents"] = [2, 2]
    _scene(state)["entities"].append(pillar)
    quake._run(
        "prepare_srd_spell_object",
        state,
        data,
        object_id="pillar",
        material="stone",
        size="large",
        durability="resilient",
    )
    params["earthquake_structures"] = True
    return state, data, params


def _burial(state, target="victim"):
    return next(
        e
        for e in state[f"{SRD}.state"]["turn_effects"]
        if e.get("kind") == "earthquake_burial" and e["target"] == target
    )


def test_fixed_structure_damage_uses_real_hp_and_collapse_saves_without_attack_roll():
    state, data, params = _case()
    before_hp = quake._target(state, "victim")["hp"]
    cast = quake._cast(state, data, params)
    pillar = _structure(state)
    source = quake._source(state)
    assert pillar["metadata"]["srd_spell_object"]["max_hp"] == 27
    assert pillar["metadata"]["srd_spell_object"]["hp"] == 0
    assert pillar["metadata"]["srd_spell_object"]["destroyed"] is True
    assert pillar["active"] is False and pillar["metadata"]["earthquake_collapsed"] is True
    row = next(r for r in source["last_collapse_rows"] if r["target"] == "victim")
    assert row["outcome"] == "failure" and 12 <= row["damage_roll"] <= 72
    assert row["damage_taken"] == row["damage_roll"]
    success = next(r for r in source["last_collapse_rows"] if r["target"] == "successful")
    assert success["outcome"] == "success" and success["buried"] is False
    assert success["damage_roll"] == row["damage_roll"]
    assert success["damage_taken"] == row["damage_roll"] // 2
    assert quake._target(state, "victim")["hp"] == max(0, before_hp - row["damage_taken"])
    assert "prone" in quake._target(state, "victim")["conditions"]
    assert _burial(state)["ongoing_duration"] is False
    assert _burial(state)["dispel_ongoing"] is False
    assert cast.result["context"].get("_object_attack_roll") is None


@pytest.mark.parametrize("unsupported", ["unclassified", "huge", "roof", "threshold"])
def test_unsupported_structure_rejects_before_slot_material_or_geometry_costs(unsupported):
    state, data, params = _case()
    pillar = _structure(state)
    if unsupported == "unclassified":
        pillar["metadata"].pop("srd_spell_object")
    elif unsupported == "huge":
        pillar["footprint"]["half_extents"] = [10, 10]
    elif unsupported == "threshold":
        pillar["metadata"]["damage_threshold"] = 10
    else:
        roof = deepcopy(_scene(state)["geometry"][0])
        roof.update(id="roof", volume={"kind": "box", "min": [145, -10, 9], "max": [155, 1, 10]})
        roof["metadata"] = {"structure_id": "pillar", "plane": "material", "ground": True}
        _scene(state)["geometry"].append(roof)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        quake._cast(state, data, params)
    assert state == before


def test_earned_rubble_survives_exact_source_removal_and_caster_end_receipt_does_not_replay():
    state, data, params = _case()
    quake._cast(state, data, params)
    source = deepcopy(quake._source(state))
    burial = deepcopy(_burial(state))
    quake._run("begin_combat_turn", state, data, actor="hero")
    quake._run("end_combat_turn", state, data, actor="hero")
    receipt = quake._source(state)["last_end_turn"]
    assert quake._source(state)["last_collapse_rows"] == []
    before = deepcopy(state)
    quake._run(
        "process_srd_earthquake_boundary",
        state,
        data,
        boundary_actor="hero",
        boundary_phase="end",
        boundary_turn=receipt,
    )
    assert state == before
    quake._run("dismiss_srd_spell", state, data, caster_state="hero", cast_id=source["cast_id"])
    assert _burial(state) == burial and "prone" in quake._target(state, "victim")["conditions"]
    assert quake._cost(state, data, [149, -4.5, 0], [151, -4.5, 0]) == pytest.approx(4)
    assert quake._cost(state, data, [140, -4.5, 0], [144, -4.5, 0]) == pytest.approx(4)


def test_buried_pc_actual_action_athletics_failure_success_and_movement(monkeypatch):
    state, data, params = _case(hero_inside=True)
    sources._second_cast(state, data, params)
    buried = deepcopy(_burial(state, "hero"))
    combat = state[f"{SRD}.combat"]
    combat.update(encounter_active=True, turn_active=False, current_actor="hero")
    quake._run("begin_combat_turn", state, data, actor="hero")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="rubble"):
        quake._run(
            "move_srd_scene_creature",
            state,
            data,
            actor_state="hero",
            destination=[145, -10, 0],
            mode="walk",
            difficult_terrain=False,
        )
    assert state == before
    monkeypatch.setattr(dice, "_rng", Random(0))
    failed = quake._run(
        "escape_srd_earthquake_rubble",
        state,
        data,
        actor="hero",
        actor_state="hero",
        burial_id=buried["source_id"],
    )
    assert failed.result["context"]["_outcome"] == "failure"
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert _burial(state, "hero")["source_id"] == buried["source_id"]
    quake._run("end_combat_turn", state, data, actor="hero")
    quake._run("begin_combat_turn", state, data, actor="hero")
    monkeypatch.setattr(dice, "_rng", Random(5))
    escaped = quake._run(
        "escape_srd_earthquake_rubble",
        state,
        data,
        actor="hero",
        actor_state="hero",
        burial_id=buried["source_id"],
    )
    assert escaped.result["context"]["_outcome"] == "success"
    assert quake._target(state)["earthquake_buried"] is False
    assert _burial(state)["target"] == "victim"
    quake._run(
        "move_srd_scene_creature",
        state,
        data,
        actor_state="hero",
        destination=[145, -10, 0],
        mode="walk",
        difficult_terrain=False,
    )
    assert "prone" in quake._target(state)["conditions"]


def test_final_object_suffix_and_unowned_attack_are_preserved_and_binder_repeats():
    text = (budget.ROOT / "runtime/ruleset.yaml").read_text()
    before = yaml.load(text, Loader=yaml.CSafeLoader)
    after_text = structures.bind_final_receivers(text)
    after = yaml.load(after_text, Loader=yaml.CSafeLoader)
    repeat = yaml.load(structures.bind_final_receivers(after_text), Loader=yaml.CSafeLoader)
    for document in (before, after, repeat):
        restore_tool_defaults(document)
    original = before["tools"]["resolve_srd_spell_object_attack"]
    assert original == after["tools"]["resolve_srd_spell_object_attack"]
    index = next(
        i
        for i, e in enumerate(original["effects"])
        if e.get("set", {}).get("field") == "_object_hp_after"
    )
    receiver = after["tools"]["damage_srd_earthquake_structure"]

    def normalize_context(value):
        if isinstance(value, str):
            return value.replace("lasting_state.", "state.")
        if isinstance(value, dict):
            return {key: normalize_context(item) for key, item in value.items()}
        if isinstance(value, list):
            return [normalize_context(item) for item in value]
        return value

    assert normalize_context(receiver["effects"][-len(original["effects"][index:]) :]) == (
        normalize_context(original["effects"][index:])
    )
    assert jar_container.CALLBACK in str(receiver["effects"])
    assert "'expression':" not in str(receiver["effects"])
    assert after == repeat


def test_two_structures_eight_actual_collapse_recipients_stay_within_unchanged_budget(monkeypatch):
    state, data, params = _case()
    pillar = deepcopy(_structure(state))
    pillar.update(id="second-pillar", position=[165, -4.5, 0])
    pillar["metadata"].pop("srd_spell_object")
    _scene(state)["entities"].append(pillar)
    quake._run(
        "prepare_srd_spell_object",
        state,
        data,
        object_id="second-pillar",
        material="stone",
        size="large",
        durability="resilient",
    )
    for index, point in enumerate(
        ([148, 0, 0], [151.5, 0, 0], [153.5, 0, 0], [162, -1, 0], [165, 0, 0], [168, -1, 0])
    ):
        quake._prepare_target(state, data, f"nearby-{index}", -10, point)
    budgets = {}
    consume = ExecutionBudget.consume_operation

    def count(self):
        budgets[id(self)] = self
        consume(self)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", count)
    quake._cast(state, data, params)
    assert len(quake._source(state)["last_collapse_rows"]) == 8
    operations = max(b.operations for b in budgets.values())
    print("Earthquake structure operations:", operations)
    assert operations <= 10000


def test_escape_one_actual_burial_preserves_second_caster_obstruction(monkeypatch):
    state, data, params = _case(hero_inside=True)
    _structure(state)["position"] = [146, -10, 0]
    second = deepcopy(_structure(state))
    second.update(id="second-pillar", position=[154, -10, 0])
    second["metadata"].pop("srd_spell_object")
    _scene(state)["entities"].append(second)
    quake._run(
        "prepare_srd_spell_object",
        state,
        data,
        object_id="second-pillar",
        material="stone",
        size="large",
        durability="resilient",
    )
    monkeypatch.setattr(dice, "_rng", Random(2))
    quake._cast(state, data, params | {"earthquake_point": [48, -10, 0]})
    assert _burial(state, "hero")["caster"] == "hero"
    monkeypatch.setattr(dice, "_rng", Random(2))
    sources._second_cast(state, data, params | {"earthquake_point": [200, -10, 0]})
    burials = [
        deepcopy(e)
        for e in state[f"{SRD}.state"]["turn_effects"]
        if e.get("kind") == "earthquake_burial" and e["target"] == "hero"
    ]
    assert len(burials) == 2 and burials[0]["caster"] != burials[1]["caster"]
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=False, current_actor="hero")
    quake._run("begin_combat_turn", state, data, actor="hero")
    monkeypatch.setattr(dice, "_rng", Random(5))
    result = quake._run(
        "escape_srd_earthquake_rubble",
        state,
        data,
        actor="hero",
        actor_state="hero",
        burial_id=burials[0]["source_id"],
    )
    assert result.result["context"]["_outcome"] == "success"
    assert _burial(state, "hero") == burials[1]
    assert quake._target(state)["earthquake_buried"] is True


def test_public_actual_paid_structure_cast_reopens_dismisses_and_keeps_physical_consequences(
    tmp_path,
    monkeypatch,
):
    quake._public_fixture(monkeypatch)
    monkeypatch.setattr(jar.public_casting, "_spell_case", lambda *_a, **_k: _case())
    database = tmp_path / "earthquake-structures.db"
    with AuthorityOwner.open(database, "casting-owner") as authority:
        _, _, params = jar.public_casting._create_spell_campaign(authority, "earthquake")
        initial = authority.snapshot("casting")
        cast, command = _act(authority, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED, cast.result
        saved = authority.snapshot("casting")
        source = deepcopy(quake._source(saved.state))
        assert quake._target(saved.state)["resources"]["spell_slot_8"] == (
            quake._target(initial.state)["resources"]["spell_slot_8"] - 1
        )
        assert _structure(saved.state)["active"] is False
        assert authority.execute_action(command) == cast and authority.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as authority:
        assert authority.snapshot("casting") == saved
        dismissed, dismiss_command = _act(
            authority, "dismiss_srd_spell", caster_state="hero", cast_id=source["cast_id"]
        )
        assert dismissed.status is ActionStatus.COMMITTED, dismissed.result
        ended = authority.snapshot("casting")
        assert _burial(ended.state) == _burial(saved.state)
        assert _structure(ended.state)["metadata"]["srd_spell_object"]["hp"] == 0
        assert authority.execute_action(dismiss_command) == dismissed
        assert authority.execute_action(command) == cast
        assert authority.snapshot("casting") == ended
