"""Source-owned Moonbeam joins actual Light queries and Darkness overlap retirement."""

import hashlib
import importlib.util
import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_spell_illumination import bind_moonbeam_runtime

from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime._types import ExecutionBudget
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public_casting
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_result_cards import _spell_case
from tests.srd_full.test_srd_spell_wave3_information import _entity, _scene

PARENT = Path(str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-moonbeam-light/input-1"))
MOON = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-moonbeam-light/srd_spell_environment.py")
)


@lru_cache
def _moon_owner():
    assert hashlib.sha256(MOON.read_bytes()).hexdigest() == (
        "4dd47d722ee80a8a8c992c90e482dcd4560f1b983b8afae95314051f25bb2b74"
    )
    spec = importlib.util.spec_from_file_location("scripts._frozen_moon_light", MOON)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@lru_cache
def _runtime(bound=True):
    """Overlay the exact Moon source constructors; keep completed public casting consumers."""
    if bound:
        _, payload = _runtime(False)
        text = bind_moonbeam_runtime(payload.decode())
        return yaml.load(text, Loader=yaml.CSafeLoader), text.encode()
    payload = (PARENT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_bytes()
    assert hashlib.sha256(payload).hexdigest() == (
        "64bc7b418f7918e7e7bf4e8bb46079b48fd19e72fbda5199ed6453fa9c011305"
    )
    owner = _moon_owner()
    parent_runtime = yaml.load(payload, Loader=yaml.CSafeLoader)
    runtime = yaml.load(
        owner.extend_runtime(payload.decode().replace("\r\n", "\n")), Loader=yaml.CSafeLoader
    )
    runtime["definitions"]["spells"]["moonbeam"] = deepcopy(owner.PROFILES["moonbeam"])
    # Environment re-emits this node before Light in canonical. Retain Light's exact
    # admitted movement suffix here because this fixture starts from completed clones.
    movement = runtime["tools"]["move_srd_scene_creature_node"]
    suffix = [
        e
        for e in parent_runtime["tools"]["move_srd_scene_creature_node"]["effects"]
        if e.get("call") in {"refresh_srd_illumination_spells", "resolve_srd_illumination_overlaps"}
    ]
    assert [e["call"] for e in suffix] == [
        "refresh_srd_illumination_spells",
        "resolve_srd_illumination_overlaps",
    ]
    movement["effects"] += deepcopy(suffix)
    for name in (
        "cast_spell",
        "cast_spell_outside_combat",
        "begin_srd_spell_cast_internal",
        "begin_srd_spell_action",
    ):
        next(p for p in runtime["tools"][name]["parameters"] if p["name"] == "spell")[
            "enum_values"
        ] = sorted(runtime["definitions"]["spells"])
    text = yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False)
    return runtime, text.encode()


@lru_cache
def _mechanic(name, bound=True):
    runtime, payload = _runtime(bound)
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=payload,
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
def _actual_owner(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: _runtime()[0]["definitions"]["spells"])


def _case(slot=2, spell="moonbeam"):
    state, data, parameters = _spell_case(spell)
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    state[f"{SRD}.state"] = {
        "lasting_tick": 0,
        "turn_effects": [],
        "spatial_scene": _scene(
            _entity("hero", [0, 0, 0], participant_ref="hero"),
            _entity("mage", [40, 8, 30], participant_ref="mage"),
            _entity("guard", [10, 0, 0], participant_ref="guard"),
            _entity(
                "orb",
                [40, 0, 30],
                is_object=True,
                size_rank=2,
                carried_by="",
                worn_by="",
                opaque_cover=False,
            ),
        ),
    }
    parameters.update(environment_origin=[40, 0, 0], slot_resource=f"spell_slot_{slot}")
    _prepare_darkness(state)
    return state, data, parameters


def _cast(slot=2):
    state, data, parameters = _case(slot)
    result = _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert result.result["status"] == "committed"
    assert state[f"{SRD}.entities"]["hero"]["resources"][f"spell_slot_{slot}"] == 2
    return state, data, parameters


def _light(state, point=(40, 0, 30), plane="material", ambient="darkness"):
    return _run(
        "resolve_srd_information_light", state, point=list(point), plane=plane, ambient=ambient
    ).result["context"]["_information_light"]


def _prepare_darkness(state):
    supplies = _spell_case("darkness")[0][f"{SRD}.entities"]["hero"]["spell_materials"]
    _run(
        "prepare_srd_creature",
        state,
        creature_id="mage",
        name="Darkness Mage",
        source="Synthetic NPC: AC13, HP1000, Speed30, Con0, Int3, attack7, DC15.",
        armor_class=13,
        hit_points=1000,
        speed=30,
        initiative_modifier=0,
        size_rank=2,
        constitution_save=0,
        spellcasting_ability="intelligence",
        spellcasting_modifier=3,
        spell_attack_bonus=7,
        spell_save_dc=15,
        inventory=[item["inventory_item"] for item in supplies],
        spell_materials=supplies,
        spells=[
            {"spell": "darkness", "uses_per_day": 2},
            {"spell": "fog_cloud", "uses_per_day": 2},
        ],
    )


def _darkness(state, *, object_id="", point=(40, 0, 30)):
    result = _run(
        "cast_spell_outside_combat",
        state,
        spell="darkness",
        caster_state="mage",
        target="mage",
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
        material_item_ids=[
            item["id"] for item in state[f"{SRD}.entities"]["mage"]["spell_materials"]
        ],
        illumination_position=list(point),
        illumination_object=object_id,
    )
    assert result.result["status"] == "committed"
    return result


@pytest.mark.parametrize(
    "point,plane,ambient,expected",
    [
        ([40, 0, 30], "material", "darkness", "dim"),
        ([45, 0, 40], "material", "darkness", "dim"),
        ([45.01, 0, 30], "material", "darkness", "darkness"),
        ([40, 0, 40.01], "material", "darkness", "darkness"),
        ([40, 0, 30], "ethereal", "darkness", "darkness"),
        ([40, 0, 30], "material", "bright", "bright"),
    ],
)
def test_actual_paid_moonbeam_point_light_uses_cylinder_plane_and_ambient_precedence(
    point, plane, ambient, expected
):
    state, _, _ = _cast()
    assert _light(state, point, plane, ambient) == expected


@pytest.mark.parametrize("slot,expected,active", [(2, "magical_darkness", False), (3, "dim", True)])
def test_new_paid_darkness_reaches_existing_overlap_and_source_level_retirement(
    slot, expected, active
):
    state, _, _ = _cast(slot)
    _darkness(state)
    assert state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"] is active
    assert _light(state) == expected


def test_covered_darkness_defers_retirement_until_actual_uncover_and_survives_reopen():
    state, _, _ = _cast()
    _run("set_srd_illumination_cover", state, object_id="orb", covered=True)
    _darkness(state, object_id="orb")
    assert _light(state) == "dim"
    assert state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
    state = json.loads(json.dumps(state))
    _run("set_srd_illumination_cover", state, object_id="orb", covered=False)
    assert not state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
    assert _light(state) == "magical_darkness"


def test_actual_holder_walking_moves_darkness_into_moonbeam_and_retires_at_overlap_owner():
    state, _, _ = _cast()
    entities = state[f"{SRD}.state"]["spatial_scene"]["entities"]
    mage = next(e for e in entities if e["id"] == "mage")
    orb = next(e for e in entities if e["id"] == "orb")
    mage["position"] = [80, 8, 30]
    orb["position"] = [80, 8, 30]
    _darkness(state, object_id="orb")
    assert state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
    # The admitted holder fact follows the actual caster on the genuine walking owner.
    orb = next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "orb")
    orb["metadata"]["carried_by"] = "mage"
    _run("move_srd_scene_creature", state, actor_state="mage", destination=[50, 8, 30])
    assert not state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
    assert _light(state) == "magical_darkness"


def test_real_clock_expiry_removes_moonbeam_dim_without_removing_other_sources():
    state, _, _ = _cast(3)
    _darkness(state)
    assert _light(state) == "dim"
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert _light(state) == "magical_darkness"
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    assert state[f"{SRD}.entities"]["mage"]["concentration_spell_id"] == "darkness"


def test_actual_target_sight_uses_moonbeam_light_and_fog_still_obscures():
    state, _, _ = _cast(3)
    target = next(
        e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "guard"
    )
    target["position"] = [40, 0, 30]
    target["metadata"]["lighting"] = "darkness"
    assert _run(
        "resolve_srd_illumination_target_sight", state, observer_state="hero", target_state="guard"
    ).result["context"]["_illumination_target_can_see"]
    _run(
        "cast_spell_outside_combat",
        state,
        spell="fog_cloud",
        caster_state="mage",
        verbal_component_available=True,
        somatic_component_available=True,
        environment_origin=[40, 0, 30],
    )
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] == "moonbeam"
    assert not _run(
        "resolve_srd_illumination_target_sight", state, observer_state="hero", target_state="guard"
    ).result["context"]["_illumination_target_can_see"]
    _run("end_srd_concentration_node", state, source_state="mage", target_state="mage")
    assert _run(
        "resolve_srd_illumination_target_sight", state, observer_state="hero", target_state="guard"
    ).result["context"]["_illumination_target_can_see"]


@pytest.mark.parametrize("source", ["none", "fog_cloud", "moonbeam"])
def test_source_light_query_operation_cost_preserves_cap(source, monkeypatch):
    state, data, params = _case(
        slot=1 if source == "fog_cloud" else 2,
        spell="fog_cloud" if source == "fog_cloud" else "moonbeam",
    )
    if source != "none":
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
    original = ExecutionBudget.consume_operation
    count = 0

    def consume(budget):
        nonlocal count
        count += 1
        original(budget)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", consume)
    counts = []
    for bound in (False, True):
        monkeypatch.setattr(budget, "_mechanic", lambda name, b=bound: _mechanic(name, b))
        count = 0
        light = _light(deepcopy(state))
        counts.append(count)
        assert light == ("dim" if source == "moonbeam" and bound else "darkness")
    print(f"Moon light query {source}: {counts[0]} -> {counts[1]} operations")
    assert ExecutionBudget().max_operations == 10_000


def test_moonbeam_light_binding_is_idempotent_and_changes_only_two_actual_owners():
    before, payload = _runtime(False)
    once = bind_moonbeam_runtime(payload.decode())
    assert bind_moonbeam_runtime(once) == once
    after = yaml.load(once, Loader=yaml.CSafeLoader)
    changed = {name for name in before["tools"] if before["tools"][name] != after["tools"][name]}
    assert changed == {"resolve_srd_information_light", "resolve_srd_illumination_overlaps"}
    assert before["tools"]["d20_test"] == after["tools"]["d20_test"]


@lru_cache
def _test_archive():
    with patch.object(
        bundled_packages, "_bundled_srd_root", lambda: PARENT / "rulesets/dnd-srd-5.2.1"
    ):
        primary, reference = bundled_packages._srd_drafts()
    record = deepcopy(primary.record)
    runtime, payload = _runtime()
    for row in record["tools"]:
        row["input_schema"] = _tool_schema(runtime["tools"][row["name"]], tool_name=row["name"])
    return bundled_packages._archive(
        (
            NormalizedPackageDraft(
                record, dict(primary.payloads) | {"runtime/ruleset.yaml": payload}, 4
            ),
            reference,
        )
    )


def test_actual_archive_paid_moonbeam_light_reopen_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _test_archive)
    monkeypatch.setattr(public_casting, "ROOT", PARENT)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _spell: _case(3))
    database = tmp_path / "moonbeam.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(owner, "moonbeam")
        cast, command = _act(owner, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert _light(deepcopy(saved.state)) == "dim"
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting") == saved
        assert reopened.execute_action(command) == cast
        assert reopened.snapshot("casting") == saved
