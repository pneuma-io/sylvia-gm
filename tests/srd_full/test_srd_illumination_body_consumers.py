"""Actual exposed body rays can escape Fog while an object's anchor remains obscured."""

from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from random import Random
from unittest.mock import patch

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_illumination_body_consumers import _tools, bind_runtime_consumers
from scripts.srd_full.srd_illumination_object_consumers import _tools as _outline_tools
from scripts.srd_full.srd_spell_illumination import _casting_tool

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime import _effect_executor
from sylvia_gm.runtime._types import ExecutionBudget
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public_casting
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_produce_flame import _flame_case, _records
from tests.srd_full.test_srd_spell_wave3_information import _entity

NATIVE = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-illumination-body-consumers/ruleset.yaml")
)


@lru_cache
def _runtime(bound=True):
    encoded = NATIVE.read_bytes()
    assert sha256(encoded).hexdigest() == (
        "8499f04a288bd35c92302c21cd01345c43111c9223b60f91a032af071ac62d07"
    )
    runtime = yaml.load(encoded, Loader=yaml.CSafeLoader)
    # Native267 predates accepted targetless10; borrow only its exact scalar parameter.
    actual = runtime["tools"]["cast_spell_illumination_spell"]["parameters"]
    actual[next(i for i, p in enumerate(actual) if p["name"] == "target_state")] = deepcopy(
        next(p for p in _casting_tool()["parameters"] if p["name"] == "target_state")
    )
    runtime["tools"].update(_outline_tools(runtime))
    if bound:
        runtime["tools"].update(_tools(runtime))
    return runtime, yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode()


@lru_cache
def _mechanic(name, bound=True):
    runtime, encoded = _runtime(bound)
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
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
def _owners(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: _runtime()[0]["definitions"]["spells"])
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Random(0))


def _bare_case():
    state, data, flame = _flame_case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    orb = next(e for e in scene["entities"] if e["id"] == "orb")
    orb["position"] = [15.2, 5, 0]
    orb["metadata"]["lighting"] = "bright"
    for row in scene["entities"]:
        if row["id"] not in {"hero", "orb"}:
            row["position"] = [100, 100, 0]
    scene["entities"] += [
        _entity("outline", [0, -10, 0], participant_ref="outline"),
        _entity("fog", [0, 10, 0], participant_ref="fog"),
    ]
    return state, data, flame


def _npc_spec(caster, spell):
    return {
        "creature_id": caster,
        "name": caster,
        "source": "Synthetic caster: AC12 HP30 Speed30 Int3 spell attack7 DC15.",
        "armor_class": 12,
        "hit_points": 30,
        "speed": 30,
        "initiative_modifier": 0,
        "size_rank": 2,
        "constitution_save": 0,
        "spellcasting_ability": "intelligence",
        "spellcasting_modifier": 3,
        "spell_attack_bonus": 7,
        "spell_save_dc": 15,
        "spells": [{"spell": spell, "uses_per_day": 1}],
    }


def _case():
    state, data, flame = _bare_case()
    for caster, spell in (("outline", "faerie_fire"), ("fog", "fog_cloud")):
        _run("prepare_srd_creature", state, **_npc_spec(caster, spell))
    _run(
        "prepare_srd_spell_object",
        state,
        object_id="orb",
        material="wood",
        size="large",
        durability="resilient",
    )
    _run("cast_spell_outside_combat", state, participant_data=data, **flame)
    _run(
        "cast_spell_outside_combat",
        state,
        spell="faerie_fire",
        caster_state="outline",
        verbal_component_available=True,
        somatic_component_available=True,
        illumination_position=[10, -10, 0],
        illumination_color="blue",
    )
    _run(
        "cast_spell_outside_combat",
        state,
        spell="fog_cloud",
        caster_state="fog",
        verbal_component_available=True,
        somatic_component_available=True,
        environment_origin=[35, 5, 0],
    )
    return state, data


def _relation(state):
    return _run(
        "resolve_srd_spell_object_relation",
        state,
        caster_state="hero",
        object_id="orb",
        max_range_feet=60,
    ).result["context"]


def test_partial_body_fog_uses_actual_exposure_rays_and_retains_native_cover(monkeypatch):
    exposure = []
    actual = _effect_executor._execute_effect

    def observe(effect, context, tool_executor=None):
        actual(effect, context, tool_executor)
        if effect.get("store_as") == "_object_exposure":
            exposure.append(deepcopy(context.vars["_object_exposure"]))

    monkeypatch.setattr(_effect_executor, "_execute_effect", observe)
    state, _ = _case()
    before = deepcopy(state)
    seen = _relation(state)
    monkeypatch.setattr(budget, "_mechanic", lambda name: _mechanic(name, False))
    anchor = _relation(before)
    assert anchor["_object_can_see"] is False and seen["_object_can_see"] is True
    assert len(exposure) == 2 and exposure[0] == exposure[1]
    assert seen["_object_cover_bonus"] == anchor["_object_cover_bonus"]
    assert seen["_object_effective_armor_class"] == anchor["_object_effective_armor_class"]
    assert any(sample["point"][0] < 15 for sample in exposure[0]["samples"])
    assert "_object_exposure" not in seen and "_illumination_body_samples" not in seen


def test_fully_obscured_body_remains_unseen_and_source_expiry_restores_it():
    state, _ = _case()
    orb = next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "orb")
    orb["position"] = [35, 5, 0]
    assert _relation(state)["_object_can_see"] is False
    state[f"{SRD}.state"]["lasting_tick"] = 12001
    _run("refresh_srd_environment_lifecycle", state)
    assert _relation(state)["_object_can_see"] is True


def test_clear_sample_does_not_bypass_blindness():
    state, _ = _case()
    state[f"{SRD}.entities"]["hero"]["conditions"] = ["blinded"]
    assert _relation(state)["_object_can_see"] is False


def test_real_retained_object_attack_gets_outline_advantage_from_visible_body(monkeypatch):
    state, data = _case()

    class Rolls:
        def __init__(self):
            self.faces = iter((1, 20))
            self.sides = []

        def randint(self, _a, b):
            self.sides.append(b)
            return next(self.faces) if b == 20 else 1

    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    source = deepcopy(_records(state, "produce_flame"))
    result = _run(
        "hurl_srd_produce_flame",
        state,
        participant_data=data,
        caster="hero",
        caster_state="hero",
        object_id="orb",
    ).result["context"]
    assert result["_produce_advantage"] is True and result["_produce_disadvantage"] is False
    assert result["_produce_attack_kind"] == "critical_hit"
    assert rolls.sides[:2] == [20, 20] and result["_produce_damage_applied"] > 0
    assert _records(state, "produce_flame") == source


def test_body_binder_is_idempotent_and_keeps_public_signatures():
    original, _ = _runtime(False)
    bound, encoded = _runtime()
    assert bind_runtime_consumers(encoded.decode()) == encoded.decode()
    for name in ("hurl_srd_produce_flame", "cast_spell", "cast_spell_outside_combat"):
        assert original["tools"][name] == bound["tools"][name]


@pytest.mark.parametrize("distance,visible", [(20, False), (60, True)])
def test_actual_blindsight_reach_qualifies_fully_obscured_samples(distance, visible):
    state, _ = _case()
    orb = next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "orb")
    orb["position"] = [35, 5, 0]
    state[f"{SRD}.entities"]["hero"].update(conditions=["blinded"], blindsight_feet=distance)
    assert _relation(state)["_object_can_see"] is visible


def test_truesight_does_not_see_through_actual_fog():
    state, _ = _case()
    orb = next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "orb")
    orb["position"] = [35, 5, 0]
    state[f"{SRD}.entities"]["hero"]["truesight_feet"] = 60
    assert _relation(state)["_object_can_see"] is False


def test_actual_outline_expiry_restores_native_object_invisibility_and_truesight():
    state, _ = _case()
    orb = next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "orb")
    orb["metadata"]["invisible"] = True
    assert _relation(state)["_object_can_see"] is True
    state[f"{SRD}.state"]["lasting_tick"] = 21
    assert _relation(state)["_object_can_see"] is False
    state[f"{SRD}.entities"]["hero"]["truesight_feet"] = 60
    assert _relation(state)["_object_can_see"] is True


def test_actual_partial_darkness_keeps_visible_body_and_retires_lower_level_outline():
    state, _ = _case()
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(_entity("dark", [0, 20, 0]))
    spec = _npc_spec("dark", "darkness")
    spec.update(
        inventory=["dark-pouch"],
        spell_materials=[
            {
                "id": "pouch",
                "inventory_item": "dark-pouch",
                "kind": "component_pouch",
                "components": [],
                "quantity": 1,
                "value_cp": 0,
            }
        ],
    )
    _run("prepare_srd_creature", state, **spec)
    _run(
        "cast_spell_outside_combat",
        state,
        spell="darkness",
        caster_state="dark",
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
        material_item_ids=["pouch"],
        illumination_position=[30.2, 5, 0],
    )
    result = _relation(state)
    assert result["_object_can_see"] is True
    assert result["_object_faerie"] is False


def test_body_visibility_does_not_promote_physically_occluded_samples():
    from sylvia_gm.domain.errors import RulesetPreconditionRejected
    from tests.srd_full.test_srd_spell_wave3_information import _wall

    state, _ = _case()
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [_wall(2)]
    with pytest.raises(RulesetPreconditionRejected):
        _relation(state)


def test_actual_body_source_queries_keep_unchanged_operation_cap(monkeypatch):
    state, _ = _case()
    budgets = []
    original = ExecutionBudget.consume_operation

    def observe(active):
        if not budgets or budgets[-1] is not active:
            budgets.append(active)
        return original(active)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", observe)
    _relation(state)
    partial = budgets[-1].operations
    orb = next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "orb")
    orb["position"] = [35, 5, 0]
    _relation(state)
    full = budgets[-1].operations
    assert all(b.max_operations == 10_000 for b in budgets)
    assert partial < full < 10_000
    print(f"Body sight partial={partial}, full Fog={full} operations at unchanged10000 cap")


@lru_cache
def _archive():
    runtime, encoded = _runtime()
    with patch.object(bundled_packages, "_bundled_srd_root", return_value=NATIVE.parent.parent):
        primary, reference = bundled_packages._srd_drafts()
    record = deepcopy(primary.record)
    for declaration in record["tools"]:
        declaration["input_schema"] = _tool_schema(runtime["tools"][declaration["name"]])
    payloads = dict(primary.payloads) | {"runtime/ruleset.yaml": encoded}
    return bundled_packages._archive((NormalizedPackageDraft(record, payloads, 4), reference))


def test_actual_paid_sources_and_body_advantage_survive_public_reopen_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _archive)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _spell: _bare_case())
    database = tmp_path / "body-sight.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, flame = public_casting._create_spell_campaign(owner, "produce_flame")
        for caster, spell in (("outline", "faerie_fire"), ("fog", "fog_cloud")):
            receipt, _ = _act(owner, "prepare_srd_creature", **_npc_spec(caster, spell))
            assert receipt.status is ActionStatus.COMMITTED
        prepared, _ = _act(
            owner,
            "prepare_srd_spell_object",
            object_id="orb",
            material="wood",
            size="large",
            durability="resilient",
        )
        assert prepared.status is ActionStatus.COMMITTED
        paid, _ = _act(owner, "cast_spell_outside_combat", **flame)
        assert paid.status is ActionStatus.COMMITTED
        for caster, spell, choices in (
            (
                "outline",
                "faerie_fire",
                {"illumination_position": [10, -10, 0], "illumination_color": "blue"},
            ),
            ("fog", "fog_cloud", {"environment_origin": [35, 5, 0]}),
        ):
            receipt, _ = _act(
                owner,
                "cast_spell_outside_combat",
                spell=spell,
                caster_state=caster,
                verbal_component_available=True,
                somatic_component_available=True,
                **choices,
            )
            assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        attack, command = _act(
            owner, "hurl_srd_produce_flame", caster="hero", caster_state="hero", object_id="orb"
        )
        assert attack.status is ActionStatus.COMMITTED
        assert attack.result["context"]["_produce_advantage"] is True
        assert attack.result["context"]["_produce_disadvantage"] is False
        after = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:

        class NoReroll(Random):
            def randint(self, _a, _b):
                pytest.fail("A durable body-sight attack retry rolled again")

        monkeypatch.setattr(dice, "_rng", NoReroll())
        assert owner.execute_action(command) == attack
        assert owner.snapshot("casting") == after
