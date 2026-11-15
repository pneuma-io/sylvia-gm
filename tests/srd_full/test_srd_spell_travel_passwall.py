"""Passwall source geometry, exact expiry and later movement use actual admitted facts."""

from copy import deepcopy
from dataclasses import replace

import pytest

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.application.package_runtime import PackageRuntimeError
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from sylvia_gm.spatial import SpatialEngine
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_travel import SRD, _case, _plane, _point
from tests.srd_full.test_srd_spell_wave3_information import _wall


def _passwall_case(material="stone", *, face="west", thickness=10, spell="misty_step"):
    state, data, _ = _case(spell)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["geometry"] = [_wall(thickness, material)]
    surface = scene["geometry"][0]["volume"]
    points = {
        "west": [10, 0, 0],
        "east": [10 + thickness, 0, 0],
        "north": [10 + thickness * 0.5, 10, 0],
        "south": [10 + thickness * 0.5, -10, 0],
        "up": [10 + thickness * 0.5, 0, 10],
        "down": [10 + thickness * 0.5, 0, -10],
    }
    caster = {
        "west": [0, 0, 0],
        "east": [surface["max"][0] + 5, 0, 0],
        "north": [points[face][0], 15, 0],
        "south": [points[face][0], -15, 0],
        "up": [points[face][0], 0, 12],
        "down": [points[face][0], 0, -16],
    }[face]
    scene["entities"][0]["position"] = caster
    scene["entities"][1]["position"] = [40, 0, 0]
    scene["entities"][2]["position"] = [45, 0, 0]
    params = {
        "caster_state": "hero",
        "surface_id": "wall",
        "point": points[face],
        "face": face,
        "slot_level": 5,
    }
    return state, data, params


def _source(state):
    return state[f"{SRD}.state"]["passwall_sources"][-1]


@pytest.mark.parametrize("material", ["wood", "plaster", "stone"])
def test_passwall_actual_solid_subtraction_opens_ray_and_body_route(material):
    state, _, params = _passwall_case(material)
    original = deepcopy(state[f"{SRD}.state"]["spatial_scene"]["geometry"][0])
    _run("install_srd_passwall", state, **params)
    source = _source(state)
    assert source["original"] == original
    assert source["open_min"] == [10, -2.5, 0] and source["open_max"] == [20, 2.5, 8]
    assert source["expires_tick"] == 1200 and source["active"] is True
    scene = state[f"{SRD}.state"]["spatial_scene"]
    assert next(row for row in scene["geometry"] if row["id"] == "wall")["active"] is False
    fragments = [row for row in scene["geometry"] if row["active"]]
    assert len(fragments) == 4
    assert all(row["metadata"]["material"] == material for row in fragments)
    ray = (
        SpatialEngine(scene)
        .query(
            {
                "kind": "ray",
                "map_id": "information",
                "origin": [0, 0, 3],
                "destination": [30, 0, 3],
                "include": ["geometry"],
                "max_intersections": 32,
            }
        )
        .as_payload()
    )
    assert ray["intersections"] == []
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[25, 0, 0], mode="walk")
    assert _point(state) == [25, 0, 0]
    with pytest.raises(RulesetPreconditionRejected, match="solid barrier"):
        _run(
            "move_srd_scene_creature", state, actor_state="hero", destination=[0, 4, 0], mode="walk"
        )


def test_passwall_chosen_depth_preserves_real_solid_beyond_the_opening():
    state, _, params = _passwall_case()
    _run("install_srd_passwall", state, **params, depth=4)
    assert _source(state)["open_max"] == [14, 2.5, 8]
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[12, 0, 0], mode="walk")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="solid barrier"):
        _run(
            "move_srd_scene_creature",
            state,
            actor_state="hero",
            destination=[16, 0, 0],
            mode="walk",
        )
    assert state == before


@pytest.mark.parametrize("face", ["west", "east", "north", "south", "up", "down"])
def test_passwall_all_real_faces_remove_only_the_actual_inward_solid(face):
    state, _, params = _passwall_case(face=face)
    _run("install_srd_passwall", state, **params)
    source = _source(state)
    original = source["original"]["volume"]
    assert all(
        original["min"][i] <= source["open_min"][i] < source["open_max"][i] <= original["max"][i]
        for i in range(3)
    )

    def volume(box):
        return (
            (box["max"][0] - box["min"][0])
            * (box["max"][1] - box["min"][1])
            * (box["max"][2] - box["min"][2])
        )

    hole = {"min": source["open_min"], "max": source["open_max"]}
    assert sum(volume(row["volume"]) for row in source["fragments"]) + volume(hole) == volume(
        original
    )
    _run("end_srd_passwall_source", state, source_id=source["id"])
    assert (
        next(
            row for row in state[f"{SRD}.state"]["spatial_scene"]["geometry"] if row["id"] == "wall"
        )["active"]
        is True
    )


@pytest.mark.parametrize(
    "defect",
    ["material", "distant", "not_face", "wide", "tall", "zero", "dark", "plane", "overlap"],
)
def test_passwall_preflight_rejects_actual_disqualifications_without_mutation(defect):
    state, _, params = _passwall_case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    if defect == "material":
        scene["geometry"][0]["metadata"]["material"] = "metal"
    elif defect == "distant":
        scene["entities"][0]["position"] = [-21, 0, 0]
    elif defect == "not_face":
        params["point"] = [11, 0, 0]
    elif defect == "wide":
        params["width"] = 6
    elif defect == "tall":
        params["point"] = [10, 0, 5]
    elif defect == "zero":
        params["depth"] = 0
    elif defect == "dark":
        scene["zones"][0]["metadata"]["lighting"] = "darkness"
    elif defect == "plane":
        scene["geometry"][0]["metadata"]["plane"] = "ethereal"
    else:
        scene["geometry"].append({**_wall(10), "id": "overlapping-wall"})
    before = deepcopy(state)
    error = PackageRuntimeError if defect == "wide" else RulesetPreconditionRejected
    with pytest.raises(error):
        _run(
            "validate_srd_passwall_cast",
            state,
            **{k: v for k, v in params.items() if k != "slot_level"},
        )
    assert state == before


def test_passwall_exact_clock_ejects_actual_creature_and_object_without_air_tail_or_cost():
    state, _, params = _passwall_case(thickness=5)
    _run("install_srd_passwall", state, **params)
    source = _source(state)
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[12, 0, 0], mode="walk")
    _run(
        "move_srd_travel_subject",
        state,
        subject="orb",
        destination=[12, 1, 0],
        plane="material",
        teleport=False,
    )
    _run(
        "move_srd_travel_subject",
        state,
        subject="guard",
        destination=[25, 0, 0],
        plane="material",
        teleport=False,
    )
    creature = state[f"{SRD}.entities"]["hero"]
    before_movement, before_hp = creature["scene_movement_spent"], creature["hp"]
    _run("advance_srd_lasting_time", state, elapsed_rounds=599)
    assert _point(state) == [12, 0, 0] and _source(state)["active"] is True
    ending = _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _point(state) == [10, 0, 10] and _point(state, "orb") == [10, 1, 10]
    assert _point(state, "guard") == [25, 0, 0] and _plane(state) == "material"
    creature = state[f"{SRD}.entities"]["hero"]
    assert creature["hp"] == before_hp and creature["scene_movement_spent"] == before_movement
    assert "prone" not in state[f"{SRD}.entities"]["hero"]["conditions"]
    assert _source(state)["active"] is False
    assert all(
        row["query"]["reference_face"]
        == {
            "target": {"kind": "geometry", "id": "wall"},
            "face": "west",
        }
        for row in ending.result["spatial_evidence"]
        if row.get("query", {}).get("reference_face")
    )
    before = deepcopy(state)
    _run("end_srd_passwall_source", state, source_id=source["id"])
    assert state == before
    with pytest.raises(RulesetPreconditionRejected, match="solid barrier"):
        _run(
            "move_srd_scene_creature",
            state,
            actor_state="hero",
            destination=[25, 0, 0],
            mode="walk",
        )


@pytest.mark.parametrize("change", ["inactive", "volume", "metadata"])
def test_passwall_later_source_geometry_change_rejects_restoration_without_overwrite(change):
    state, _, params = _passwall_case()
    _run("install_srd_passwall", state, **params)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    fragment = next(row for row in scene["geometry"] if row["active"])
    if change == "inactive":
        fragment["active"] = False
    elif change == "volume":
        fragment["volume"]["min"][0] += 0.1
    else:
        fragment["metadata"]["later_fiction"] = {"altered": True}
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="reconciliation"):
        _run("end_srd_passwall_source", state, source_id=_source(state)["id"])
    assert state == before


def test_public_passwall_paid_opening_reopens_then_ejects_once_without_damage(
    tmp_path, monkeypatch
):
    import tests.srd_full.test_srd_public_spell_casting as public

    def actual_case(_spell):
        state, data, choices = _passwall_case(thickness=5, spell="passwall")
        _, _, params = _case("passwall")
        params.update(
            passwall_surface_id=choices["surface_id"],
            passwall_face=choices["face"],
            travel_destination=choices["point"],
        )
        return state, data, params

    monkeypatch.setattr(public, "_spell_case", actual_case)
    database = tmp_path / "passwall.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "passwall")
        command = ActionCommand(
            campaign_id="casting",
            action_id="passwall",
            idempotency_key="passwall",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=params,
        )
        mechanics = StatelessMcpMechanics(owner)
        before = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected, match="full chosen opening"):
            mechanics.execute_action(
                replace(
                    command,
                    action_id="too-tall",
                    idempotency_key="too-tall",
                    parameters={**params, "travel_destination": [10, 0, 5]},
                )
            )
        assert owner.snapshot("casting").state == before.state
        assert owner.snapshot("casting").revision == before.revision
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        assert mechanics.execute_action(command) == receipt
        assert _source(owner.snapshot("casting").state)["active"] is True
        entered = mechanics.execute_action(
            replace(
                command,
                action_id="enter",
                idempotency_key="enter",
                expected_revision=owner.snapshot("casting").revision,
                tool_name="move_srd_scene_creature",
                parameters={"actor_state": "hero", "destination": [12, 0, 0], "mode": "walk"},
            )
        )
        assert entered.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert _point(saved.state) == [12, 0, 0]
        hero = saved.state[f"{SRD}.entities"]["hero"]
        assert hero["resources"]["spell_slot_5"] == 2
        hp, movement = hero["hp"], hero["scene_movement_spent"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        mechanics = StatelessMcpMechanics(owner)
        before_expiry = replace(
            command,
            action_id="before-expiry",
            idempotency_key="before-expiry",
            expected_revision=saved.revision,
            tool_name="advance_srd_lasting_time",
            parameters={"elapsed_rounds": 599},
        )
        mechanics.execute_action(before_expiry)
        assert _point(owner.snapshot("casting").state) == [12, 0, 0]
        ending = replace(
            before_expiry,
            action_id="expiry",
            idempotency_key="expiry",
            expected_revision=owner.snapshot("casting").revision,
            parameters={"elapsed_rounds": 1},
        )
        receipt = mechanics.execute_action(ending)
        assert receipt.status is ActionStatus.COMMITTED
        assert mechanics.execute_action(ending) == receipt
        after = owner.snapshot("casting")
        assert _source(after.state)["active"] is False
        assert _point(after.state) == [10, 0, 10] and _plane(after.state) == "material"
        hero = after.state[f"{SRD}.entities"]["hero"]
        assert hero["hp"] == hp and hero["scene_movement_spent"] == movement
        assert "prone" not in hero["conditions"] and hero["resources"]["spell_slot_5"] == 2
        with pytest.raises(MechanicsRejected, match="solid barrier"):
            mechanics.execute_action(
                replace(
                    command,
                    action_id="restored-wall",
                    idempotency_key="restored-wall",
                    expected_revision=after.revision,
                    tool_name="move_srd_scene_creature",
                    parameters={"actor_state": "hero", "destination": [12, 0, 0], "mode": "walk"},
                )
            )
        assert owner.snapshot("casting").state == after.state
