"""Floating Disk uses real floor, circle geometry, saved cargo and public movement."""

import json
from copy import deepcopy
from functools import lru_cache
from unittest.mock import patch

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.build_srd_5_2_1_spells import refresh_exploration_runtime
from scripts.srd_full.srd_source_relocation_node import bind_runtime_consumers as bind_relocation_node
from scripts.srd_full.srd_spell_floating_disk import PUBLIC_TOOLS, bind_runtime_consumers, extend_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, MechanicContext, owned_payload
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_public_spell_casting as public_casting
from tests import test_srd_spell_object_content as objects
from tests import test_srd_spell_object_forces as forces
from tests import test_srd_spell_object_utilities as utilities
from tests import test_srd_spell_result_cards as spell_cards
from tests import test_srd_spell_supplies as supplies
from tests.test_spatial_engine import _geometry
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_npc_spell_casting import _act, _prepare_mage

SRD = objects.SRD
_FORCE_RUNTIME_BYTES = forces._runtime_bytes


@lru_cache
def _runtime_bytes():
    return refresh_exploration_runtime(
        bind_runtime_consumers(
            bind_relocation_node(extend_runtime(_FORCE_RUNTIME_BYTES().decode()))
        )
    ).encode()


@lru_cache
def _mechanic(name):
    runtime_bytes = _runtime_bytes()
    runtime = _load_runtime(runtime_bytes.decode())
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.1.0",
        package_sha256="e" * 64,
        runtime_bytes=runtime_bytes,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


def _run(name, state, **parameters):
    result = _mechanic(name)(
        MechanicContext(
            campaign_id="floating-disk",
            turn_id="turn",
            action_id=name,
            state=owned_payload(state),
            parameters=owned_payload(parameters),
            participants=owned_payload({}),
        )
    )
    for mutation in result.mutations:
        state[f"{mutation.namespace}.{mutation.key}"] = deepcopy(mutation.value)
    return result.result["context"]


def _state():
    state = utilities._state()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    floor = _geometry("floor", [-99, -99, -1], [99, 99, 0], visibility="clear")
    floor["map_id"] = scene["map"]["id"]
    floor["metadata"].update(ground=True, plane="material")
    scene["geometry"].append(floor)
    scene["zones"].append(
        {
            "id": "actual-ambient",
            "map_id": scene["map"]["id"],
            "shape": {"kind": "box", "min": [-99, -99, -1], "max": [99, 99, 99]},
            "metadata": {"plane": "material", "lighting": "bright"},
        }
    )
    supplies._row(state, "door").update(
        footprint={"kind": "box", "half_extents": [0.3, 0.3]},
        height=0.5,
    )
    return state


def _cast(state, output="disk", point=None):
    _run(
        "cast_srd_floating_disk",
        state,
        caster_state="hero",
        spell="floating_disk",
        slot_level=1,
        output_id=output,
        point=point or [3, 0, 3],
    )
    return next(r for r in state[f"{SRD}.state"]["turn_effects"] if r.get("force_id") == output)


def _mass(state, weight):
    _run(
        "record_srd_object_utility_facts",
        state,
        object_id="door",
        weight_pounds=weight,
        manufactured=True,
        metal=False,
        object_kind="object",
        inventory_item="cargo-token",
    )


def _load(state, source):
    position = list(supplies._row(state, source["force_id"])["position"])
    position[2] += 1 / 12
    _run(
        "load_srd_floating_disk",
        state,
        actor_state="hero",
        source_id=source["source_id"],
        object_id="door",
        position=position,
    )


def test_disk_cast_uses_printed_circle_and_actual_seen_unoccupied_ground():
    state = _state()
    source = _cast(state)
    assert source["source_id"] == "hero:floating_disk:1"
    assert source["expires_tick"] == 1200 and source["floor_id"] == "floor"
    disk = supplies._row(state, "disk")
    assert disk["footprint"] == {"kind": "circle", "radius": 1.5}
    assert disk["height"] == pytest.approx(1 / 12) and disk["position"] == [3, 0, 3]
    assert "disk" not in state[f"{SRD}.entities"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="exactly three feet"):
        _cast(state, "wrong-height", [3, 0, 4])
    assert state == before
    with pytest.raises(RulesetPreconditionRejected, match="unoccupied space"):
        _cast(state, "occupied", [0, 0, 3])
    assert state == before
    state[f"{SRD}.entities"]["hero"]["conditions"] = ["blinded"]
    with pytest.raises(RulesetPreconditionRejected, match="actually see"):
        _cast(state, "unseen", [3, 0, 3])


def test_actual_disk_load_flat_follow_and_exact_inventory_transfer_survive_saved_state():
    state = _state()
    _mass(state, 500)
    state[f"{SRD}.entities"]["hero"].update(
        inventory=["cargo-token", "other-token"], held_items=["cargo-token"]
    )
    _run("record_srd_object_holder", state, actor_state="hero", object_id="door", held_as="carried")
    source = _cast(state, point=[0, 3, 3])
    _load(state, source)
    assert state[f"{SRD}.entities"]["hero"]["inventory"] == ["other-token"]
    assert supplies._row(state, "door")["metadata"]["carried_by"] is None
    restored = json.loads(json.dumps(state))
    _run(
        "move_srd_travel_subject",
        restored,
        subject="hero",
        destination=[0, 40, 0],
        plane="material",
        teleport=False,
    )
    disk = supplies._row(restored, "disk")
    cargo = supplies._row(restored, "door")
    assert disk["position"][1] == pytest.approx(40 - (400 - 9) ** 0.5)
    assert cargo["position"][1] == disk["position"][1]
    assert cargo["position"][2] == pytest.approx(3 + 1 / 12)
    _run(
        "move_srd_travel_subject",
        restored,
        subject="hero",
        destination=[0, disk["position"][1], 0],
        plane="material",
        teleport=False,
    )
    _run(
        "unload_srd_floating_disk",
        restored,
        actor_state="hero",
        source_id=source["source_id"],
        object_id="door",
    )
    assert restored[f"{SRD}.entities"]["hero"]["inventory"] == ["other-token", "cargo-token"]
    assert supplies._row(restored, "door")["metadata"]["srd_spell_carrier"] is None
    assert supplies._row(restored, "door")["metadata"]["carried_by"] == "hero"
    assert (
        next(r for r in restored[f"{SRD}.state"]["turn_effects"] if r.get("force_id") == "disk")[
            "cargo_ids"
        ]
        == []
    )


def test_actual_cargo_retrieval_requires_its_whole_body_clearance_before_holder_transfer():
    state = _state()
    _mass(state, 7)
    source = _cast(state)
    _load(state, source)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    wall = _geometry("cargo-obstruction", [1.4, 0.2, 1.4], [1.6, 0.4, 2.5])
    wall["map_id"] = scene["map"]["id"]
    wall["metadata"]["plane"] = "material"
    scene["geometry"].append(wall)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="clear full-body route"):
        _run(
            "unload_srd_floating_disk",
            state,
            actor_state="hero",
            source_id=source["source_id"],
            object_id="door",
        )
    assert state == before


def test_round_cargo_uses_its_actual_cylinder_then_saved_follow_and_ground_release():
    state = _state()
    supplies._row(state, "door")["footprint"] = {"kind": "circle", "radius": 0.5}
    _mass(state, 7)
    source = _cast(state, point=[0, 3, 3])
    scene = state[f"{SRD}.state"]["spatial_scene"]
    corner = _geometry("round-cargo-corner", [0.4, 2.5, 3.12], [0.5, 2.6, 3.5])
    corner["map_id"] = scene["map"]["id"]
    corner["metadata"]["plane"] = "material"
    scene["geometry"].append(corner)
    before_blocked_route = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="clear full-body route"):
        _load(state, source)
    assert state == before_blocked_route
    corner["volume"]["min"][2] = 3.56
    corner["volume"]["max"][2] = 3.58
    _load(state, source)
    assert supplies._row(state, "door")["footprint"] == {"kind": "circle", "radius": 0.5}
    assert supplies._row(state, "door")["position"] == [0, 3, 3 + 1 / 12]
    restored = json.loads(json.dumps(state))
    _run(
        "move_srd_travel_subject",
        restored,
        subject="hero",
        destination=[0, 40, 0],
        plane="material",
        teleport=False,
    )
    disk = supplies._row(restored, "disk")
    cargo = supplies._row(restored, "door")
    assert disk["position"][1] == pytest.approx(40 - (400 - 9) ** 0.5)
    assert cargo["position"] == pytest.approx(
        [0, disk["position"][1], 3 + 1 / 12], rel=0, abs=1e-12
    )
    assert cargo["metadata"]["srd_spell_carrier"]["source_id"] == source["source_id"]
    _run("advance_srd_lasting_time", restored, elapsed_rounds=600)
    assert supplies._row(restored, "disk")["active"] is False
    assert supplies._row(restored, "door")["position"] == [0, disk["position"][1], 0]
    assert supplies._row(restored, "door")["metadata"]["srd_spell_carrier"] is None
    assert not any(
        row.get("source_id") == source["source_id"]
        for row in restored[f"{SRD}.state"]["turn_effects"]
    )


@pytest.mark.parametrize("radius,offset", [(1.6, 0), (0.5, 1.01)])
def test_round_cargo_requires_actual_whole_circle_support_before_any_mutation(radius, offset):
    state = _state()
    supplies._row(state, "door")["footprint"] = {"kind": "circle", "radius": radius}
    _mass(state, 7)
    source = _cast(state, point=[0, 3, 3])
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="whole-footprint support"):
        _run(
            "load_srd_floating_disk",
            state,
            actor_state="hero",
            source_id=source["source_id"],
            object_id="door",
            position=[offset, 3, 3 + 1 / 12],
        )
    assert state == before


def _cylindrical_ground_state():
    state = _state()
    floor = next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["geometry"] if row["id"] == "floor"
    )
    floor["volume"] = {"kind": "cylinder", "center": [0, 0, -1], "radius": 30, "height": 1}
    return state


def test_disk_uses_actual_flat_cylinder_surface_for_saved_follow_and_release():
    state = _cylindrical_ground_state()
    _mass(state, 7)
    source = _cast(state, point=[0, 3, 3])
    assert source["floor_id"] == "floor"
    _load(state, source)
    restored = json.loads(json.dumps(state))
    _run(
        "move_srd_travel_subject",
        restored,
        subject="hero",
        destination=[0, 25, 0],
        plane="material",
        teleport=False,
    )
    disk_position = list(supplies._row(restored, "disk")["position"])
    assert disk_position == pytest.approx([0, 25 - (400 - 9) ** 0.5, 3])
    assert supplies._row(restored, "door")["position"] == pytest.approx(
        [disk_position[0], disk_position[1], 3 + 1 / 12], rel=0, abs=1e-12
    )
    _run("advance_srd_lasting_time", restored, elapsed_rounds=600)
    assert supplies._row(restored, "disk")["active"] is False
    assert supplies._row(restored, "door")["position"] == [0, disk_position[1], 0]
    assert supplies._row(restored, "door")["metadata"]["srd_spell_carrier"] is None


@pytest.mark.parametrize("offset,admitted", [(28.5, True), (29, False)])
def test_cylindrical_ground_requires_support_for_the_whole_actual_disk(offset, admitted):
    state = _cylindrical_ground_state()
    before = deepcopy(state)
    if admitted:
        source = _cast(state, point=[0, offset, 3])
        assert source["floor_id"] == "floor"
        assert supplies._row(state, "disk")["position"] == [0, offset, 3]
    else:
        with pytest.raises(RulesetPreconditionRejected, match="whole disk route"):
            _cast(state, point=[0, offset, 3])
        assert state == before


def test_exact_circular_follow_clears_a_real_corner_inside_its_bounding_box():
    state = _state()
    _mass(state, 7)
    source = _cast(state, point=[0, 3, 3])
    _load(state, source)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    corner = _geometry("real-corner", [1.2, 1.6, 3], [1.4, 1.8, 4])
    corner["map_id"] = scene["map"]["id"]
    corner["metadata"]["plane"] = "material"
    scene["geometry"].append(corner)
    _run(
        "move_srd_travel_subject",
        state,
        subject="hero",
        destination=[0, 40, 0],
        plane="material",
        teleport=False,
    )
    disk = supplies._row(state, "disk")
    assert disk["footprint"] == {"kind": "circle", "radius": 1.5}
    assert disk["position"][1] == pytest.approx(40 - (400 - 9) ** 0.5)
    assert supplies._row(state, "door")["position"][1] == disk["position"][1]
    retained_corner = next(
        row
        for row in state[f"{SRD}.state"]["spatial_scene"]["geometry"]
        if row["id"] == "real-corner"
    )
    assert retained_corner["volume"] == corner["volume"]
    assert retained_corner["active"] is True and retained_corner["metadata"]["plane"] == "material"


def test_supported_blocked_disk_stays_while_real_caster_moves_then_ends_beyond_100():
    state = _state()
    _mass(state, 7)
    source = _cast(state, point=[0, 3, 3])
    _load(state, source)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    wall = _geometry("disk-obstruction", [7, 1.9, 2.9], [8, 3.8, 5])
    wall["map_id"] = scene["map"]["id"]
    wall["metadata"]["plane"] = "material"
    scene["geometry"].append(wall)
    cargo_position = list(supplies._row(state, "door")["position"])
    _run(
        "move_srd_travel_subject",
        state,
        subject="hero",
        destination=[40, 0, 0],
        plane="material",
        teleport=False,
    )
    assert supplies._row(state, "hero")["position"] == [40, 0, 0]
    assert supplies._row(state, "disk")["position"] == [0, 3, 3]
    assert supplies._row(state, "door")["position"] == cargo_position
    retained_source = next(
        row
        for row in state[f"{SRD}.state"]["turn_effects"]
        if row.get("source_id") == source["source_id"]
    )
    assert retained_source["cargo_ids"] == ["door"]
    assert retained_source["caster"] == source["caster"]
    _run(
        "move_srd_travel_subject",
        state,
        subject="hero",
        destination=[99, 20, 0],
        plane="material",
        teleport=False,
    )
    assert supplies._row(state, "hero")["position"] == [99, 20, 0]
    assert supplies._row(state, "disk")["active"] is False
    assert supplies._row(state, "door")["position"] == [0, 3, 0]
    assert not any(
        row.get("source_id") == source["source_id"] for row in state[f"{SRD}.state"]["turn_effects"]
    )


def test_actual_tall_cargo_clearance_keeps_the_whole_disk_stationary_under_a_low_obstacle():
    state = _state()
    supplies._row(state, "door")["height"] = 2
    _mass(state, 7)
    source = _cast(state, point=[0, 3, 3])
    _load(state, source)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    overhead = _geometry("cargo-overhead", [7, 1.9, 4.5], [8, 3.8, 5])
    overhead["map_id"] = scene["map"]["id"]
    overhead["metadata"]["plane"] = "material"
    scene["geometry"].append(overhead)
    cargo_position = list(supplies._row(state, "door")["position"])
    _run(
        "move_srd_travel_subject",
        state,
        subject="hero",
        destination=[40, 0, 0],
        plane="material",
        teleport=False,
    )
    assert supplies._row(state, "hero")["position"] == [40, 0, 0]
    assert supplies._row(state, "disk")["position"] == [0, 3, 3]
    assert supplies._row(state, "door")["position"] == cargo_position
    retained_source = next(
        row
        for row in state[f"{SRD}.state"]["turn_effects"]
        if row.get("source_id") == source["source_id"]
    )
    assert retained_source["cargo_ids"] == ["door"]
    assert retained_source["caster"] == source["caster"]


@pytest.mark.parametrize("ending", ["overload", "deadline", "distance"])
def test_real_disk_endings_drop_exact_saved_cargo_onto_actual_ground(ending):
    state = _state()
    _mass(state, 501 if ending == "overload" else 7)
    source = _cast(state)
    _load(state, source)
    if ending == "deadline":
        _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    elif ending == "distance":
        _run(
            "move_srd_travel_subject",
            state,
            subject="hero",
            destination=[-99, 0, 0],
            plane="material",
            teleport=True,
        )
    assert supplies._row(state, "disk")["active"] is False
    assert supplies._row(state, "door")["position"] == [3, 0, 0]
    assert supplies._row(state, "door")["metadata"]["srd_spell_carrier"] is None
    assert not any(
        r.get("source_id") == source["source_id"] for r in state[f"{SRD}.state"]["turn_effects"]
    )


def test_disk_source_emission_and_both_movement_consumers_repeat_without_losing_hand():
    text = _FORCE_RUNTIME_BYTES().decode()
    once = bind_runtime_consumers(bind_relocation_node(extend_runtime(text)))
    twice = bind_runtime_consumers(bind_relocation_node(extend_runtime(once)))
    assert _load_runtime(twice) == _load_runtime(once)
    tools = _load_runtime(once)["tools"]
    for name in ("move_srd_travel_subject", "move_srd_scene_creature_node"):
        assert "refresh_srd_floating_disks" in str(tools[name]["effects"])
    assert "refresh_srd_object_forces" in str(tools["move_srd_travel_subject"]["effects"])


@lru_cache
def _test_archive():
    runtime_bytes = _runtime_bytes()
    with (
        patch.object(forces, "_runtime_bytes", lambda: runtime_bytes),
        patch.object(forces, "PUBLIC_TOOLS", forces.PUBLIC_TOOLS | PUBLIC_TOOLS),
    ):
        return forces._test_archive.__wrapped__()


def _campaign(owner, monkeypatch, ground_kind="box"):
    definitions = spell_cards._spell_definitions() | supplies.PROFILES | utilities.PROFILES

    def state_factory():
        state = _state()
        if ground_kind == "cylinder":
            floor = next(
                row
                for row in state[f"{SRD}.state"]["spatial_scene"]["geometry"]
                if row["id"] == "floor"
            )
            floor["volume"] = {
                "kind": "cylinder",
                "center": [0, 0, -1],
                "radius": 40,
                "height": 1,
            }
        return state

    with (
        patch.object(objects, "_state", state_factory),
        patch.object(objects, "_test_archive", _test_archive),
        patch.object(spell_cards, "_spell_definitions", lambda: definitions),
    ):
        return objects._create_object_campaign(owner, monkeypatch)


@pytest.mark.parametrize("ground_kind", ["box", "cylinder"])
def test_paid_public_disk_saved_loading_movement_and_transfer_do_not_repay_cast(
    tmp_path, monkeypatch, ground_kind
):
    database = tmp_path / "disk.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _campaign(owner, monkeypatch, ground_kind=ground_kind)
        prepared, _ = _prepare_mage(
            owner,
            [{"spell": "floating_disk", "uses_per_day": 1}],
            inventory=["mercury-drop"],
            spell_materials=[
                {
                    "id": "mercury-lot",
                    "inventory_item": "mercury-drop",
                    "kind": "specific",
                    "components": ["floating_disk.1"],
                    "quantity": 1,
                    "value_cp": 0,
                }
            ],
        )
        assert prepared.status is ActionStatus.COMMITTED, prepared.result
        fact, _ = _act(
            owner,
            "record_srd_object_utility_facts",
            object_id="door",
            weight_pounds=7,
            manufactured=True,
            metal=False,
            object_kind="object",
            inventory_item="cargo-token",
        )
        assert fact.status is ActionStatus.COMMITTED, fact.result
        cast, command = _act(
            owner,
            "cast_spell_outside_combat",
            caster_state="mage",
            spell="floating_disk",
            utility_output_id="disk",
            utility_point=[0, 3, 3],
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
            material_item_ids=["mercury-lot"],
        )
        assert cast.status is ActionStatus.COMMITTED, cast.result
        source = next(
            r
            for r in owner.snapshot("casting").state[f"{SRD}.state"]["turn_effects"]
            if r.get("force_id") == "disk"
        )
        assert source["source_id"] == "mage:floating_disk:1"
        loaded, load_command = _act(
            owner,
            "load_srd_floating_disk",
            actor_state="mage",
            source_id=source["source_id"],
            object_id="door",
            position=[0, 3, 3 + 1 / 12],
        )
        assert loaded.status is ActionStatus.COMMITTED, loaded.result
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.execute_action(command) == cast
        assert reopened.execute_action(load_command) == loaded
        assert reopened.snapshot("casting") == saved
        advanced, _ = _act(reopened, "advance_srd_lasting_time", elapsed_rounds=1)
        assert advanced.status is ActionStatus.COMMITTED, advanced.result
        moved, move_command = _act(
            reopened, "move_srd_scene_creature", actor_state="mage", destination=[0, 31, 0]
        )
        assert moved.status is ActionStatus.COMMITTED, moved.result
        moved_state = reopened.snapshot("casting")
        disk = supplies._row(moved_state.state, "disk")
        assert sum((disk["position"][i] - [0, 31, 0][i]) ** 2 for i in range(3)) == pytest.approx(
            400
        )
        assert disk["position"][1] > 3 and disk["position"][2] == 3
        assert supplies._row(moved_state.state, "door")["position"][1] == disk["position"][1]
        assert (
            moved_state.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["floating_disk"]
            == 0
        )
        assert reopened.execute_action(move_command) == moved
        assert reopened.snapshot("casting") == moved_state
        advanced_again, _ = _act(reopened, "advance_srd_lasting_time", elapsed_rounds=1)
        assert advanced_again.status is ActionStatus.COMMITTED, advanced_again.result
        approached, _ = _act(
            reopened,
            "move_srd_scene_creature",
            actor_state="mage",
            destination=[disk["position"][0], disk["position"][1], 0],
        )
        assert approached.status is ActionStatus.COMMITTED, approached.result
        retrieved, retrieval_command = _act(
            reopened,
            "unload_srd_floating_disk",
            actor_state="mage",
            source_id=source["source_id"],
            object_id="door",
        )
        assert retrieved.status is ActionStatus.COMMITTED, retrieved.result
        retrieved_state = reopened.snapshot("casting")
        assert "cargo-token" in retrieved_state.state[f"{SRD}.entities"]["mage"]["inventory"]
        assert supplies._row(retrieved_state.state, "door")["metadata"]["carried_by"] == "mage"
        assert supplies._row(retrieved_state.state, "door")["metadata"]["srd_spell_carrier"] is None
        assert reopened.execute_action(retrieval_command) == retrieved
        assert reopened.snapshot("casting") == retrieved_state


def test_public_disk_ritual_uses_real_saved_casting_time_and_no_spell_slot(tmp_path, monkeypatch):
    definitions = spell_cards._spell_definitions() | utilities.PROFILES
    with patch.object(spell_cards, "_spell_definitions", lambda: definitions):
        state, data, parameters = spell_cards._spell_case("floating_disk")
    state[f"{SRD}.state"]["spatial_scene"] = _state()[f"{SRD}.state"]["spatial_scene"]
    parameters.update(casting_mode="ritual", slot_resource="ritual", spellbook_readable=True)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _spell: (state, data, parameters))
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _test_archive)
    database = tmp_path / "disk-ritual.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public_casting._create_spell_campaign(owner, "floating_disk")
        resources = deepcopy(
            owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]["resources"]
        )
        begun, begin_command = _act(owner, "begin_srd_spell_cast", **_begin(parameters))
        assert begun.status is ActionStatus.COMMITTED, begun.result
        source_caster = owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]
        assert source_caster["extended_casting"]["required_rounds"] == 100
        assert source_caster["concentration_spell_id"] == "floating_disk"
        cast_parameters = _begin(parameters) | {
            "utility_output_id": "ritual-disk",
            "utility_point": [0, 3, 3],
        }
        before_early_cast = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected, match="Start and continue"):
            _act(owner, "cast_spell_outside_combat", **cast_parameters)
        assert owner.snapshot("casting") == before_early_cast
        progress, progress_command = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
            elapsed_rounds=99,
            uninterrupted=True,
            spellbook_readable=True,
        )
        assert progress.status is ActionStatus.COMMITTED, progress.result
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["hero"]["extended_casting"]["progress_rounds"] == 99
        assert not any(
            row["id"] == "ritual-disk"
            for row in saved.state[f"{SRD}.state"]["spatial_scene"]["entities"]
        )
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.execute_action(begin_command) == begun
        assert reopened.execute_action(progress_command) == progress
        assert reopened.snapshot("casting") == saved
        finished, _ = _act(
            reopened,
            "continue_srd_spell_cast",
            caster_state="hero",
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
            elapsed_rounds=1,
            uninterrupted=True,
            spellbook_readable=True,
        )
        assert finished.status is ActionStatus.COMMITTED, finished.result
        cast, cast_command = _act(reopened, "cast_spell_outside_combat", **cast_parameters)
        assert cast.status is ActionStatus.COMMITTED, cast.result
        final = reopened.snapshot("casting")
        caster = final.state[f"{SRD}.entities"]["hero"]
        assert caster["resources"] == resources
        assert caster["extended_casting"] is None and caster["concentration_spell_id"] is None
        assert final.state[f"{SRD}.state"]["lasting_tick"] == 200
        source = next(
            row
            for row in final.state[f"{SRD}.state"]["turn_effects"]
            if row.get("force_id") == "ritual-disk"
        )
        assert source["spell_level"] == 1 and source["started_tick"] == 200
        assert source["expires_tick"] == 1400
        assert supplies._row(final.state, "ritual-disk")["position"] == [0, 3, 3]
        assert cast.result["result_card"]["title"] == "Floating Disk"
        assert reopened.execute_action(cast_command) == cast
        assert reopened.snapshot("casting") == final


def test_public_combat_disk_transfer_uses_each_actual_actors_utilize_action_once(
    tmp_path, monkeypatch
):
    database = tmp_path / "disk-combat.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _campaign(owner, monkeypatch)
        prepared, _ = _prepare_mage(
            owner,
            [{"spell": "floating_disk", "uses_per_day": 1}],
            inventory=["mercury-drop", "cargo-token"],
            spell_materials=[
                {
                    "id": "mercury-lot",
                    "inventory_item": "mercury-drop",
                    "kind": "specific",
                    "components": ["floating_disk.1"],
                    "quantity": 1,
                    "value_cp": 0,
                }
            ],
        )
        assert prepared.status is ActionStatus.COMMITTED, prepared.result
        fact, _ = _act(
            owner,
            "record_srd_object_utility_facts",
            object_id="door",
            weight_pounds=7,
            manufactured=True,
            metal=False,
            object_kind="object",
            inventory_item="cargo-token",
        )
        assert fact.status is ActionStatus.COMMITTED, fact.result
        held, _ = _act(
            owner,
            "record_srd_object_holder",
            actor_state="mage",
            object_id="door",
            held_as="carried",
        )
        assert held.status is ActionStatus.COMMITTED, held.result
        cast, cast_command = _act(
            owner,
            "cast_spell_outside_combat",
            caster_state="mage",
            spell="floating_disk",
            utility_output_id="disk",
            utility_point=[0, 3, 3],
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
            material_item_ids=["mercury-lot"],
        )
        assert cast.status is ActionStatus.COMMITTED, cast.result
        source = next(
            row
            for row in owner.snapshot("casting").state[f"{SRD}.state"]["turn_effects"]
            if row.get("force_id") == "disk"
        )
        started, _ = _act(
            owner,
            "start_combat_encounter_entities",
            first_actor="mage",
            second_actor="hero",
            first_surprised=False,
            second_surprised=False,
            tie_winner="first",
        )
        assert started.status is ActionStatus.COMMITTED, started.result
        begun, _ = _act(owner, "begin_combat_turn")
        assert begun.status is ActionStatus.COMMITTED, begun.result
        before = owner.snapshot("casting")
        if before.state[f"{SRD}.combat"]["active_actor"] != "mage":
            ended, _ = _act(owner, "end_combat_turn")
            assert ended.status is ActionStatus.COMMITTED, ended.result
            advanced, _ = _act(owner, "advance_combat_encounter_turn")
            assert advanced.status is ActionStatus.COMMITTED, advanced.result
            begun, _ = _act(owner, "begin_combat_turn")
            assert begun.status is ActionStatus.COMMITTED, begun.result
            before = owner.snapshot("casting")
        assert before.state[f"{SRD}.combat"]["active_actor"] == "mage"
        loading = {
            "actor_state": "mage",
            "source_id": source["source_id"],
            "object_id": "door",
            "position": [0, 3, 3 + 1 / 12],
        }
        with pytest.raises(MechanicsRejected, match="Utilize"):
            _act(owner, "load_srd_floating_disk", **loading)
        assert owner.snapshot("casting") == before
        with pytest.raises(MechanicsRejected, match="active actor"):
            _act(
                owner,
                "load_srd_floating_disk",
                **(loading | {"actor_state": "hero", "interaction_mode": "utilize"}),
            )
        assert owner.snapshot("casting") == before
        with pytest.raises(MechanicsRejected, match="actual disk surface"):
            _act(
                owner,
                "load_srd_floating_disk",
                **(loading | {"position": [0, 3, 4], "interaction_mode": "utilize"}),
            )
        assert owner.snapshot("casting") == before
        loaded, load_command = _act(
            owner, "load_srd_floating_disk", **(loading | {"interaction_mode": "utilize"})
        )
        assert loaded.status is ActionStatus.COMMITTED, loaded.result
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.combat"]["action_available"] is False
        assert "cargo-token" not in saved.state[f"{SRD}.entities"]["mage"]["inventory"]
        assert (
            supplies._row(saved.state, "door")["metadata"]["srd_spell_carrier"]["source_id"]
            == source["source_id"]
        )
        assert saved.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["floating_disk"] == 0
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.execute_action(cast_command) == cast
        assert reopened.execute_action(load_command) == loaded
        assert reopened.snapshot("casting") == saved
        retrieval = {
            "source_id": source["source_id"],
            "object_id": "door",
            "interaction_mode": "utilize",
        }
        with pytest.raises(MechanicsRejected, match="no action remaining"):
            _act(reopened, "unload_srd_floating_disk", actor_state="mage", **retrieval)
        assert reopened.snapshot("casting") == saved
        ended, _ = _act(reopened, "end_combat_turn")
        assert ended.status is ActionStatus.COMMITTED, ended.result
        advanced, _ = _act(reopened, "advance_combat_encounter_turn")
        assert advanced.status is ActionStatus.COMMITTED, advanced.result
        begun, _ = _act(reopened, "begin_combat_turn")
        assert begun.status is ActionStatus.COMMITTED, begun.result
        before_retrieval = reopened.snapshot("casting")
        assert before_retrieval.state[f"{SRD}.combat"]["active_actor"] == "hero"
        with pytest.raises(MechanicsRejected, match="active actor"):
            _act(reopened, "unload_srd_floating_disk", actor_state="mage", **retrieval)
        assert reopened.snapshot("casting") == before_retrieval
        retrieved, retrieval_command = _act(
            reopened, "unload_srd_floating_disk", actor_state="hero", **retrieval
        )
        assert retrieved.status is ActionStatus.COMMITTED, retrieved.result
        final = reopened.snapshot("casting")
        assert final.state[f"{SRD}.combat"]["action_available"] is False
        assert final.state[f"{SRD}.entities"]["hero"]["inventory"].count("cargo-token") == 1
        assert "cargo-token" not in final.state[f"{SRD}.entities"]["mage"]["inventory"]
        assert supplies._row(final.state, "door")["metadata"]["carried_by"] == "hero"
        assert supplies._row(final.state, "door")["metadata"]["srd_spell_carrier"] is None
        assert (
            next(
                row
                for row in final.state[f"{SRD}.state"]["turn_effects"]
                if row.get("source_id") == source["source_id"]
            )["cargo_ids"]
            == []
        )
        assert final.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["floating_disk"] == 0
        assert reopened.execute_action(retrieval_command) == retrieved
        assert reopened.snapshot("casting") == final
