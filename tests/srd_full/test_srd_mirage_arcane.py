"""Mirage consumes actual sight, tangible scenery and exact independent source lifetimes."""

from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_actor_spell_modifiers import refresh_exploration_runtime
from scripts.srd_full.srd_extended_casting import extend_runtime as extend_casting
from scripts.srd_full.srd_mirage_arcane import PROFILES, bind_runtime_consumers, extend_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime, _RuntimeDumper

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as cards
from tests.test_spatial_engine import _geometry
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_scene_relations import _state


@lru_cache
def _runtime():
    source = Path(__file__).parents[1] / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml"
    text = refresh_exploration_runtime(extend_runtime(source.read_text(encoding="utf-8")))
    runtime = _load_runtime(text)
    runtime["definitions"]["spells"].update(PROFILES)
    for tool in runtime["tools"].values():
        for parameter in tool["parameters"]:
            if parameter["name"] == "spell" and "acid_arrow" in parameter.get("enum_values", []):
                parameter["enum_values"] = sorted(runtime["definitions"]["spells"])
    text = extend_casting(
        yaml.dump(runtime, Dumper=_RuntimeDumper, sort_keys=False),
        spell_ids=sorted(runtime["definitions"]["spells"]),
    )
    text = bind_runtime_consumers(text)
    return _load_runtime(text), text.encode()


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


def _case():
    state, data, parameters = cards._spell_case("mirage_arcane")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    scene = deepcopy(_state()[f"{SRD}.state"]["spatial_scene"])
    scene["geometry"] = [_geometry("floor", [-90, -90, -2], [90, 90, 0])]
    scene["geometry"][0]["map_id"] = scene["map"]["id"]
    scene["geometry"][0]["metadata"].update(ground=True, plane="material")
    scene["zones"] = [
        {
            "id": "ambient",
            "map_id": scene["map"]["id"],
            "shape": {"kind": "box", "min": [-100, -100, -100], "max": [100, 100, 100]},
            "metadata": {"plane": "material", "lighting": "bright"},
        }
    ]
    scene["entities"].append(
        {
            "id": "field",
            "map_id": "relation",
            "position": [10, 0, 0],
            "footprint": {"kind": "box", "half_extents": [5, 5]},
            "height": 0.1,
            "facing": [1, 0],
            "active": True,
            "movement": "passable",
            "visibility": "clear",
            "metadata": {
                "natural_terrain": True,
                "terrain_kind": "flat meadow",
                "plane": "material",
            },
        }
    )
    state[f"{SRD}.state"] = {"lasting_tick": 0, "turn_effects": [], "spatial_scene": scene}
    parameters.update(
        mirage_corner=[-20, -20, 0],
        mirage_side_feet=40,
        mirage_description="A swamp with low stone walls",
        mirage_terrain_kind="swamp",
        mirage_tactile_kind="soft mud",
        mirage_sound="Quiet water",
        mirage_smell="Wet earth",
    )
    return state, data, parameters


def _cast(state, **changes):
    _, _, parameters = _case()
    choices = {key: value for key, value in parameters.items() if key.startswith("mirage_")}
    choices.update(changes)
    _run("cast_srd_mirage_arcane", state, caster_state="hero", slot_level=7, **choices)
    return next(reversed(state[f"{SRD}.state"]["mirage_sources"]))


def _wall():
    return {
        "id": "illusion-wall",
        "position": [10, 0, 0],
        "extent": [2, 10, 8],
        "description": "A stone wall",
        "tactile_kind": "cold stone",
        "blocks_movement": True,
        "ground": False,
    }


def test_actual_square_sight_and_source_tangible_structure():
    state, _, _ = _case()
    identity = _cast(state, mirage_structures=[_wall()])
    source = state[f"{SRD}.state"]["mirage_sources"][identity]
    assert source["expires_tick"] == 288000
    geometry = next(
        row
        for row in state[f"{SRD}.state"]["spatial_scene"]["geometry"]
        if row["id"] == "illusion-wall"
    )
    assert geometry["movement"] == "blocking" and geometry["visibility"] == "clear"
    assert geometry["metadata"]["mirage_source_id"] == identity
    assert "illusion-wall" not in state[f"{SRD}.entities"]
    assert geometry["metadata"].get("srd_spell_object") is None


@pytest.mark.parametrize("sense", ["ordinary", "near_true", "far_true"])
def test_actual_truesight_changes_visual_truth_but_preserves_tactile_source(sense):
    state, _, _ = _case()
    _cast(state)
    state[f"{SRD}.state"]["spatial_scene"]["entities"][0]["position"] = [5, 0, 0]
    if sense != "ordinary":
        state[f"{SRD}.entities"]["hero"]["truesight_feet"] = 120 if sense == "near_true" else 2
    result = _run("perceive_srd_mirage_terrain", state, observer_state="hero", subject="field")
    rows = {r["label"]: r["value"] for r in result.result["result_card"]["summary"]}
    assert rows["Terrain"] == ("flat meadow" if sense == "near_true" else "swamp")
    assert rows["Physical feel"] == "soft mud"
    assert rows["True form visible"] is (sense == "near_true")


def test_expiry_and_dismissal_remove_only_exact_source_scenery():
    state, _, _ = _case()
    identity = _cast(state, mirage_structures=[_wall()])
    _run("dismiss_srd_mirage_arcane", state, caster_state="hero", mirage_id=identity)
    assert [row["id"] for row in state[f"{SRD}.state"]["spatial_scene"]["geometry"]] == ["floor"]
    assert state[f"{SRD}.entities"]["guard"]["hp"] > 0
    assert state[f"{SRD}.state"]["has_mirage_spells"] is False


def test_creature_perception_cannot_be_disguised_or_concealed():
    state, _, _ = _case()
    _cast(state)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="creature"):
        _run("perceive_srd_mirage_terrain", state, observer_state="hero", subject="guard")
    assert state == before


def _summary(result):
    return {row["label"]: row["value"] for row in result.result["result_card"]["summary"]}


@pytest.mark.parametrize("ground_mode,expected", [("difficult", 30), ("clear", 20)])
def test_actual_route_splits_at_square_and_spends_one_actor_budget(ground_mode, expected):
    state, _, _ = _case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][0]["position"] = [-5, 0, 0]
    if ground_mode == "clear":
        scene["zones"].append(
            {
                "id": "rough-ground",
                "map_id": scene["map"]["id"],
                "shape": {"kind": "box", "min": [0, -5, -2], "max": [10, 5, 2]},
                "metadata": {"plane": "material", "difficult": True},
            }
        )
    _cast(
        state,
        mirage_corner=[0, -5, 0],
        mirage_side_feet=10,
        mirage_ground_mode=ground_mode,
    )
    result = _run("move_srd_scene_creature", state, actor_state="hero", destination=[15, 0, 0])
    assert _summary(result)["Movement spent"] == expected
    assert state[f"{SRD}.entities"]["hero"]["scene_movement_spent"] == expected
    moved = next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == "hero"
    )
    assert moved["position"] == [15, 0, 0]


def test_tangible_wall_blocks_actual_body_movement_but_cannot_conceal_creature():
    state, _, _ = _case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["position"] = [15, 0, 0]
    _cast(state, mirage_structures=[_wall()])
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="blocked|obstruct|movement"):
        _run("move_srd_scene_creature", state, actor_state="hero", destination=[15, 0, 0])
    assert state == before
    relation = _summary(
        _run("resolve_srd_scene_relation", state, actor_state="hero", target_state="guard")
    )
    assert relation["Can see"] is True


def test_actual_later_clock_expiry_retires_scenery_once():
    state, _, _ = _case()
    identity = _cast(state, mirage_structures=[_wall()])
    _run("advance_srd_lasting_time", state, elapsed_rounds=100000)
    assert state[f"{SRD}.state"]["mirage_sources"][identity]["installed"] is True
    _run("advance_srd_lasting_time", state, elapsed_rounds=44000)
    assert state[f"{SRD}.state"]["mirage_sources"][identity]["installed"] is False
    assert [row["id"] for row in state[f"{SRD}.state"]["spatial_scene"]["geometry"]] == ["floor"]
    revision = state[f"{SRD}.state"]["spatial_scene"]["map"]["revision"]
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert state[f"{SRD}.state"]["spatial_scene"]["map"]["revision"] == revision


def test_final_binder_preserves_semantic_repeat():
    runtime, content = _runtime()
    repeated = _load_runtime(bind_runtime_consumers(content.decode()))
    assert repeated == runtime


def test_clear_ground_preserves_actual_paid_grease_source_cost(monkeypatch):
    from random import Random

    from sylvia_gm import dice

    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _case()
    grease_state, _, grease_parameters = cards._spell_case("grease")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][0]["position"] = [-5, -10, 0]
    scene["entities"][1]["position"] = [-5, 0, 0]
    state[f"{SRD}.entities"]["hero"]["prepared_spells"].append("grease")
    data["spellcasting_grants"][0]["spells"].append("grease")
    data["wizard_spellbook"].append("grease")
    materials = grease_state[f"{SRD}.entities"]["hero"]["spell_materials"]
    state[f"{SRD}.entities"]["hero"]["spell_materials"] = materials
    state[f"{SRD}.entities"]["hero"]["inventory"] += [row["inventory_item"] for row in materials]
    _run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(
            parameters
            | grease_parameters
            | {"spell": "grease", "slot_resource": "spell_slot_1", "environment_origin": [5, 0, 0]}
        ),
    )
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
    _cast(state, mirage_corner=[0, -5, 0], mirage_side_feet=10, mirage_ground_mode="clear")
    result = _run(
        "move_srd_scene_creature",
        state,
        participant_data=data,
        actor_state="guard",
        destination=[15, 0, 0],
    )
    assert _summary(result)["Movement spent"] == 30
    assert state[f"{SRD}.entities"]["guard"]["scene_movement_spent"] == 30


def test_strict_paid_ten_minute_cast_reopen_retry_and_actual_later_movement(tmp_path, monkeypatch):
    from scripts.srd_full.srd_mirage_arcane import PUBLIC_TOOLS

    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application import bundled_packages
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus
    from tests import test_srd_public_spell_casting as public
    from tests import test_srd_spell_appearance as appearance
    from tests.srd_full.test_srd_dream_spell import _linked_drafts
    from tests.srd_full.test_srd_extended_casting import _begin
    from tests.srd_full.test_srd_npc_spell_casting import _act

    monkeypatch.setattr(bundled_packages, "_srd_drafts", _linked_drafts)
    monkeypatch.setattr(appearance, "_runtime", _runtime)
    monkeypatch.setattr(appearance, "PUBLIC_TOOLS", PUBLIC_TOOLS)
    appearance._appearance_archive.cache_clear()
    monkeypatch.setattr(public, "bundled_srd_archive", appearance._appearance_archive)
    monkeypatch.setattr(public, "_spell_case", lambda spell: _case())
    database = tmp_path / "mirage.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, "mirage_arcane")
        parameters.update(mirage_ground_mode="difficult", mirage_structures=[_wall()])
        names = {tool.name for tool in StatelessMcpMechanics(owner).list_tools("casting")}
        assert names >= PUBLIC_TOOLS
        begun, begin_command = _act(owner, "begin_srd_spell_cast", **_begin(parameters))
        assert begun.status is ActionStatus.COMMITTED
        channel = owner.snapshot("casting")
        assert channel.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == 3
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.execute_action(begin_command) == begun
        assert owner.snapshot("casting").state == channel.state
        progress, _ = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=100,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert progress.status is ActionStatus.COMMITTED
        cast, cast_command = _act(owner, "cast_spell_outside_combat", **parameters)
        assert cast.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == 2
        source = next(iter(saved.state[f"{SRD}.state"]["mirage_sources"].values()))
        assert source["started_tick"] == 200 and source["expires_tick"] == 288200
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.execute_action(cast_command) == cast
        assert owner.snapshot("casting").state == saved.state
        moved, movement_command = _act(
            owner, "move_srd_scene_creature", actor_state="hero", destination=[5, 0, 0]
        )
        assert moved.status is ActionStatus.COMMITTED
        assert _summary(moved)["Movement spent"] == 10
        movement = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.execute_action(movement_command) == moved
        assert owner.snapshot("casting").state == movement.state
        observed, _ = _act(
            owner, "perceive_srd_mirage_terrain", observer_state="hero", subject="field"
        )
        assert observed.status is ActionStatus.COMMITTED
        assert _summary(observed)["Physical feel"] == "soft mud"
