"""Manifested Flame sources illuminate actual later queries through their source lifecycle."""

from copy import deepcopy
from functools import lru_cache

import pytest
import yaml
from scripts.srd_full.srd_flame_blade_light import bind_runtime_consumers
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.application import package_runtime
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_runtime import validate_ruleset_data
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_flame_blade as flame
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_produce_flame_reactions import _mage_spec
from tests.srd_full.test_srd_spell_wave3_information import _entity, _wall

_BASE_RUNTIME = flame._runtime


@lru_cache
def _runtime():
    runtime = deepcopy(_BASE_RUNTIME()[0])
    text = yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False)
    text = bind_runtime_consumers(text)
    emitted = _load_runtime(text)
    assert not validate_ruleset_data(emitted), validate_ruleset_data(emitted)
    assert emitted["tools"]["d20_test"] == runtime["tools"]["d20_test"]
    return emitted, text.encode()


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    _runtime()
    monkeypatch.setattr(flame, "_runtime", _runtime)
    flame._mechanic.cache_clear()
    flame._archive.cache_clear()
    monkeypatch.setattr(flame.budget, "_mechanic", flame._mechanic)
    monkeypatch.setattr(
        flame.cards, "_spell_definitions", lambda: _runtime()[0]["definitions"]["spells"]
    )
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", flame.attacks.Rolls())
    actual = package_runtime.execute_tool

    def diagnosed(*args, **kwargs):
        result = actual(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnosed)


def _light(state, point, plane="material"):
    return _run(
        "resolve_srd_information_light", state, point=point, plane=plane, ambient="darkness"
    ).result["context"]["_information_light"]


def _darkness(state, position):
    spec = _mage_spec()
    spec["spells"] = [{"spell": "darkness", "uses_per_day": 2}]
    materials = flame.cards._spell_case("darkness")[0][f"{SRD}.entities"]["hero"]["spell_materials"]
    spec.update(inventory=[m["inventory_item"] for m in materials], spell_materials=materials)
    _run("prepare_srd_creature", state, **spec)
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity("mage", [40, 0, 0], participant_ref="mage", creature_type="humanoid")
    )
    _run(
        "cast_spell_outside_combat",
        state,
        spell="darkness",
        caster_state="mage",
        target="guard",
        illumination_object="",
        illumination_position=position,
        verbal_component_available=True,
        material_component_available=True,
        material_item_ids=[m["id"] for m in materials],
    )


def test_real_paid_source_light_has_printed_edges_current_anchor_plane_and_no_sunlight():
    state, data, params = flame._case()
    assert _light(state, [1, 0, 0]) == "darkness"
    flame._cast(state, data, params)
    assert state[f"{SRD}.state"]["has_flame_blade_light"] is True
    assert [_light(state, [x, 0, 0]) for x in (10, 10.1, 20, 20.1)] == [
        "bright",
        "dim",
        "dim",
        "darkness",
    ]
    result = _run(
        "resolve_srd_information_light",
        state,
        point=[10, 0, 0],
        plane="material",
        ambient="darkness",
    ).result["context"]
    assert result["_information_sunlight"] is False
    assert _light(state, [5, 0, 0], plane="ethereal") == "darkness"
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[30, 0, 0])
    assert _light(state, [10, 0, 0]) == "dim"
    assert _light(state, [40, 0, 0]) == "bright"


def test_actual_opaque_wall_and_release_reevoke_lifecycle_control_light():
    state, data, params = flame._case()
    flame._cast(state, data, params)
    state[f"{SRD}.state"]["spatial_scene"]["geometry"].append(_wall(1))
    assert _light(state, [20, 0, 0]) == "darkness"
    _run("release_srd_flame_blade", state, caster_state="hero")
    assert _light(state, [5, 0, 0]) == "darkness"
    _run("evoke_srd_flame_blade", state, caster_state="hero", hand_index=1)
    assert _light(state, [5, 0, 0]) == "bright"
    _run("advance_srd_lasting_time", state, elapsed_rounds=100)
    assert state[f"{SRD}.state"]["has_flame_blade_light"] is False
    assert _light(state, [5, 0, 0]) == "darkness"


def test_actual_floor_boundary_is_clear_and_opaque_wall_matches_the_real_source_plane():
    state, data, params = flame._case()
    flame._cast(state, data, params)
    floor = _wall(1)
    floor["id"] = "floor"
    floor["volume"] = {"kind": "box", "min": [-30, -30, -1], "max": [30, 30, 0]}
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["geometry"].append(floor)
    assert _light(state, [10, 0, 0]) == "bright"
    scene["geometry"].append(_wall(1))
    caster = next(r for r in scene["entities"] if r["id"] == "hero")
    caster["metadata"]["plane"] = "ethereal"
    assert _light(state, [20, 0, 0], plane="ethereal") == "dim"
    scene["geometry"][-1]["metadata"]["plane"] = "ethereal"
    assert _light(state, [20, 0, 0], plane="ethereal") == "darkness"


def test_actual_cylinder_cap_tangency_is_clear_and_interior_crossing_blocks():
    state, data, params = flame._case()
    flame._cast(state, data, params)
    floor = _wall(1)
    floor["volume"] = {"kind": "cylinder", "center": [0, 0, -1], "radius": 30, "height": 1}
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["geometry"].append(floor)
    assert _light(state, [10, 0, 0]) == "bright"
    floor["volume"]["height"] = 2
    assert _light(state, [10, 0, 0]) == "darkness"


@pytest.mark.parametrize("slot,retired", [(2, True), (3, False)])
def test_actual_darkness_overlaps_additional_dim_radius_and_uses_original_slot(slot, retired):
    state, data, params = flame._case(slot)
    flame._cast(state, data, params)
    _darkness(state, [35, 0, 0])
    sources = [r for r in state[f"{SRD}.state"]["turn_effects"] if r.get("spell") == "flame_blade"]
    assert bool(sources) is not retired
    assert state[f"{SRD}.state"]["has_flame_blade_light"] is not retired
    assert _run("qualify_srd_scene_hand_occupancy", state, actor_state="hero").result["context"][
        "_scene_free_hand_count"
    ] == (2 if retired else 1)
    assert _light(state, [20, 0, 0]) == ("magical_darkness" if retired else "dim")


def test_unmanifested_source_survives_darkness_until_real_bonus_action_evokes_light():
    state, data, params = flame._case()
    flame._cast(state, data, params)
    _run("release_srd_flame_blade", state, caster_state="hero")
    _darkness(state, [5, 0, 0])
    assert flame._source(state)["blade_manifested"] is False
    _run("evoke_srd_flame_blade", state, caster_state="hero", hand_index=1)
    assert not any(r.get("spell") == "flame_blade" for r in state[f"{SRD}.state"]["turn_effects"])
    assert state[f"{SRD}.state"]["has_flame_blade_light"] is False


def test_occluded_overlap_rejects_unknown_clipped_volume_without_retiring_flame():
    state, data, params = flame._case()
    flame._cast(state, data, params)
    source = deepcopy(flame._source(state))
    state[f"{SRD}.state"]["spatial_scene"]["geometry"].append(_wall(1))
    with pytest.raises(RulesetPreconditionRejected, match="clipped light volumes"):
        _darkness(state, [35, 0, 0])
    assert flame._source(state) == source
    assert not any(r.get("spell") == "darkness" for r in state[f"{SRD}.state"]["turn_effects"])


def test_paid_public_light_release_reopen_and_exact_concentration_retirement(tmp_path, monkeypatch):
    def case(_spell):
        state, data, params = flame._case()
        for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]:
            row["metadata"]["lighting"] = "darkness"
        return state, data, params

    monkeypatch.setattr(flame.public_casting, "bundled_srd_archive", flame._archive)
    monkeypatch.setattr(flame.public_casting, "_spell_case", case)
    path = tmp_path / "blade-light.sqlite"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, params = flame.public_casting._create_spell_campaign(owner, "flame_blade")
        paid, paid_command = _act(owner, "cast_spell_outside_combat", **params)
        assert paid.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        assert _light(deepcopy(saved.state), [10, 0, 0]) == "bright"
        attack, attack_command = _act(
            owner, "use_srd_flame_blade", caster="hero", caster_state="hero", target="guard"
        )
        assert attack.result["context"]["_flame_damage_applied"] == 13
        assert len(attack.result["context"]["_flame_attack_dice"]) == 1
        release, _ = _act(owner, "release_srd_flame_blade", caster_state="hero")
        assert release.status is ActionStatus.COMMITTED
        assert _light(deepcopy(owner.snapshot("casting").state), [10, 0, 0]) == "darkness"
        _act(owner, "evoke_srd_flame_blade", caster_state="hero", hand_index=1)
        _act(owner, "end_concentration", caster_state="hero")
        saved = owner.snapshot("casting")
        assert owner.execute_action(paid_command) == paid
        assert owner.execute_action(attack_command) == attack
        assert owner.snapshot("casting") == saved
        assert _light(deepcopy(saved.state), [10, 0, 0]) == "darkness"


def test_repeated_light_binding_preserves_real_roll_owner_and_other_final_hooks():
    runtime, encoded = _runtime()
    assert _load_runtime(bind_runtime_consumers(encoded.decode())) == runtime

    def nodes(value):
        if isinstance(value, dict):
            return 1 + sum(nodes(k) + nodes(v) for k, v in value.items())
        if isinstance(value, list):
            return 1 + sum(map(nodes, value))
        return 1

    print("Flame light expanded-node delta:", nodes(runtime) - nodes(_BASE_RUNTIME()[0]))
