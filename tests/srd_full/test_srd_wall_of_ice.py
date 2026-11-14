"""Ice sections are actual source objects; a genuine hit opens only its cold-air sheet."""

from copy import deepcopy
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_environment import SRD, _fixture, _ground, _position, _spell_case


def _case(*, slot=6, axis="north_south", save_success=False):
    state, data, params = _fixture("wall_of_ice", origin=(20, 0, 0), slot=slot)
    _ground(state)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["zones"].append(
        {
            "id": "ice-daylight",
            "map_id": scene["map"]["id"],
            "shape": {"kind": "box", **deepcopy(scene["map"]["bounds"])},
            "metadata": {"plane": "material", "lighting": "bright"},
        }
    )
    _position(state, "guard")["position"] = [15 if axis == "east_west" else 5, -5, 0]
    if axis == "east_west":
        _position(state, "hero")["position"] = [0, -20, 0]
    _position(state, "other")["position"] = [10, 8, 0]
    for target in ("guard", "other"):
        state[f"{SRD}.entities"][target].update(hp=1000, max_hp=1000, temp_hp=0)
        state[f"{SRD}.entities"][target]["saving_throw_modifiers"]["constitution"] = (
            100 if save_success else -100
        )
    params.update(environment_wall_axis=axis)
    return state, data, params


def _cast(**choices):
    state, data, params = _case(**choices)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    return state, data, params


def _panels(state):
    return [
        row
        for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if row["metadata"].get("srd_spell_object", {}).get("kind") == "wall_ice_panel"
    ]


def _attack(state, monkeypatch, *, damage_type="fire", seed=1):
    monkeypatch.setattr(dice, "_rng", Random(seed))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    panel_id = _panels(state)[4]["id"]
    return _run(
        "resolve_srd_spell_object_attack",
        state,
        caster_state="hero",
        object_id=panel_id,
        attack_modifier=10,
        damage_die="d10",
        dice_count=4,
        damage_type=damage_type,
        max_range_feet=120,
    ).result["context"]


def test_wall_ice_paid_ten_actual_panels_and_whole_supported_body():
    state, _, _ = _cast()
    panels = _panels(state)
    assert len(panels) == 10
    assert [p["position"] for p in panels] == [[20, -45 + i * 10, 0] for i in range(10)]
    assert all(p["footprint"] == {"kind": "box", "half_extents": [0.5, 5]} for p in panels)
    assert all(p["height"] == 10 and p["movement"] == "blocking" for p in panels)
    assert all(p["metadata"]["srd_spell_object"]["hp"] == 30 for p in panels)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] == "wall_of_ice"


@pytest.mark.parametrize("invalid", ["support", "bounds", "air"])
def test_wall_ice_rejects_unsupported_placement_before_paid_cost(invalid):
    state, data, params = _case()
    if invalid == "support":
        state[f"{SRD}.state"]["spatial_scene"]["geometry"][-1]["volume"]["max"][1] = 30
    elif invalid == "bounds":
        scene = state[f"{SRD}.state"]["spatial_scene"]
        scene["map"]["bounds"]["max"][2] = 8
        scene["map"]["layers"][0]["max_elevation"] = 8
        scene["zones"][-1]["shape"]["max"][2] = 8
    else:
        params["environment_origin"][2] = 2
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state == before


@pytest.mark.parametrize("damage_type", ["cold", "poison", "psychic"])
def test_wall_ice_printed_immunity_preserves_panel_hp(monkeypatch, damage_type):
    state, _, _ = _cast()
    context = _attack(state, monkeypatch, damage_type=damage_type)
    assert context["_object_attack_kind"] == "hit"
    assert context["_object_damage_rolled"] > 0 and context["_object_damage_applied"] == 0
    assert _panels(state)[4]["metadata"]["srd_spell_object"]["hp"] == 30


def test_wall_ice_real_fire_hit_breaches_only_actual_section(monkeypatch):
    state, _, _ = _cast()
    context = _attack(state, monkeypatch)
    assert context["_object_attack_kind"] == "hit"
    assert context["_object_damage_applied"] == min(30, context["_object_damage_rolled"] * 2)
    panel = _panels(state)[4]
    assert panel["metadata"]["srd_spell_object"]["hp"] == 0
    assert panel["movement"] == "passable" and panel["active"]
    assert all(p["metadata"]["srd_spell_object"]["hp"] == 30 for p in _panels(state) if p != panel)


def test_wall_ice_actual_sweep_and_solid_barrier_open_only_breached_section(monkeypatch):
    state, _, _ = _cast()
    with pytest.raises(RulesetPreconditionRejected):
        _run("move_srd_scene_creature", state, actor_state="guard", destination=[25, -5, 0])
    result = _run(
        "srd_information_barriers",
        state,
        start=[5, -5, 3],
        end=[25, -5, 3],
        plane="material",
        rule="solid",
    )
    assert result.result["context"]["_information_blocked"]
    _attack(state, monkeypatch)
    result = _run(
        "srd_information_barriers",
        state,
        start=[5, -5, 3],
        end=[25, -5, 3],
        plane="material",
        rule="solid",
    )
    assert not result.result["context"]["_information_blocked"]
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[25, -5, 0])
    guard = state[f"{SRD}.entities"]["guard"]
    assert _position(state, "guard")["position"] == [25, -5, 0]
    assert guard["last_wall_of_ice_sheet"]["raw_damage"]["count"] == 5
    assert guard["hp"] == 1000 - guard["last_wall_of_ice_sheet"]["applied_damage"]


def test_wall_ice_concentration_retirement_deactivates_source_objects():
    state, _, _ = _cast()
    _run("end_concentration", state, caster_state="hero")
    assert not state[f"{SRD}.state"]["has_wall_of_ice"]
    assert all(not p["active"] and p["movement"] == "passable" for p in _panels(state))


def _public_case():
    """Use real Wizard/Druid grants so the later object attack has a genuine source."""
    state, data, params = _case()
    data["classes"].append({"id": "druid", "level": 1})
    data["level"] = 18
    state[f"{SRD}.entities"]["hero"]["level"] = 18
    state[f"{SRD}.entities"]["hero"]["prepared_spells"].append("produce_flame")
    data["abilities"]["wisdom"] = 18
    data["spellcasting_grants"].append(
        {"source": "druid", "ability": "wisdom", "spells": ["produce_flame"]}
    )
    return state, data, params


def test_wall_ice_later_source_derived_public_flame_opens_cold_sheet(monkeypatch):
    state, data, params = _public_case()
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    _, _, flame = _spell_case("produce_flame")
    _run("cast_spell_outside_combat", state, participant_data=data, **flame)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Random(1))
    result = _run(
        "hurl_srd_produce_flame",
        state,
        participant_data=data,
        caster="hero",
        caster_state="hero",
        object_id=_panels(state)[4]["id"],
    )
    assert result.result["context"]["_produce_armor_class"] == 12
    assert _panels(state)[4]["movement"] == "passable"
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[25, -5, 0])
    assert state[f"{SRD}.entities"]["guard"]["last_wall_of_ice_sheet"]["raw_damage"]["count"] == 5


def test_wall_ice_upcast_sheet_save_half_and_first_movement_receipt(monkeypatch):
    state, _, _ = _cast(slot=8, axis="east_west", save_success=True)
    _attack(state, monkeypatch)
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[15, 5, 0])
    guard = state[f"{SRD}.entities"]["guard"]
    packet = deepcopy(guard["last_wall_of_ice_sheet"])
    assert packet["raw_damage"]["count"] == 7 and packet["outcome"] == "success"
    assert packet["applied_damage"] == packet["raw_damage"]["total"] // 2
    hp = guard["hp"]
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[15, -5, 0])
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["hp"] == hp and guard["last_wall_of_ice_sheet"] == packet
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[15, 5, 0])
    later = state[f"{SRD}.entities"]["guard"]
    assert later["hp"] < hp
    assert (
        later["last_wall_of_ice_sheet"]["turn"]
        == state[f"{SRD}.state"]["lasting_tick"]
        > packet["turn"]
    )


def test_wall_ice_panel_body_tangency_and_actual_plane(monkeypatch):
    state, _, _ = _cast()
    for start, end in (([0, -5, 10], [30, -5, 10]), ([0, -50, 3], [30, -50, 3])):
        context = _run(
            "srd_information_barriers", state, start=start, end=end, plane="material", rule="solid"
        ).result["context"]
        assert not context["_information_blocked"]
    context = _run(
        "srd_information_barriers",
        state,
        start=[0, -5, 3],
        end=[30, -5, 3],
        plane="ethereal",
        rule="solid",
    ).result["context"]
    assert not context["_information_blocked"]
    before = deepcopy(state)
    _panels(state)[4]["metadata"]["srd_spell_object"]["armor_class"] = 1
    with pytest.raises(RulesetPreconditionRejected):
        _attack(state, monkeypatch)
    assert _panels(state)[4]["metadata"]["srd_spell_object"]["hp"] == 30
    assert state[f"{SRD}.entities"] == before[f"{SRD}.entities"]


@pytest.mark.parametrize("damage_type", ["fire", "cold"])
def test_wall_ice_real_public_sorcerous_packet_uses_same_object_defenses(monkeypatch, damage_type):
    from tests.srd_full.test_srd_spell_damage_consumers import _record_rolls

    state, data, params = _case()
    data["classes"].append({"id": "sorcerer", "level": 1})
    data["level"] = 18
    data["abilities"]["charisma"] = 18
    data["spellcasting_grants"].append(
        {"source": "sorcerer", "ability": "charisma", "spells": ["sorcerous_burst"]}
    )
    hero = state[f"{SRD}.entities"]["hero"]
    hero["level"] = 18
    hero["prepared_spells"].append("sorcerous_burst")
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    _, _, burst = _spell_case("sorcerous_burst")
    burst.pop("target", None)
    burst.update(
        object_target_id=_panels(state)[4]["id"],
        selected_damage_type=damage_type,
        sorcerous_burst_extra_dice=False,
    )
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Random(1))
    rolls = _record_rolls(monkeypatch)
    result = _run("cast_spell_outside_combat", state, participant_data=data, **burst)
    context = result.result["context"]
    assert context["_spell_attack_kind"] == "hit"
    assert context["_spell_target_armor_class"] == 12
    panel = _panels(state)[4]
    if damage_type == "cold":
        assert panel["metadata"]["srd_spell_object"]["hp"] == 30
        assert panel["movement"] == "blocking"
    else:
        damage = next(roll for roll in rolls if roll.sides == 8)
        assert damage.count == 4
        assert context["_spell_damage_amount"] == min(30, damage.total * 2)
        assert panel["metadata"]["srd_spell_object"]["hp"] == 0
        assert panel["movement"] == "passable"


def test_wall_ice_object_binding_preserves_ordinary_breaking_objects(monkeypatch):
    state, _, _ = _case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    crate = deepcopy(_position(state, "other"))
    crate.update(
        id="wooden-crate",
        position=[10, 0, 0],
        height=2,
        footprint={"kind": "box", "half_extents": [1, 1]},
    )
    crate["metadata"] = {"is_object": True, "plane": "material", "lighting": "bright"}
    scene["entities"].append(crate)
    _run(
        "prepare_srd_spell_object",
        state,
        object_id="wooden-crate",
        material="wood",
        size="medium",
        durability="resilient",
    )
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Random(1))
    context = _run(
        "resolve_srd_spell_object_attack",
        state,
        caster_state="hero",
        object_id="wooden-crate",
        attack_modifier=10,
        damage_die="d8",
        dice_count=1,
        damage_type="fire",
        max_range_feet=60,
    ).result["context"]
    # Read the persisted scene, since the sole mutation owner replaces its aggregate.
    actual = next(
        row
        for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if row["id"] == "wooden-crate"
    )
    assert context["_object_effective_armor_class"] == 15
    assert context["_object_damage_applied"] == context["_object_damage_rolled"]
    assert actual["metadata"]["srd_spell_object"]["hp"] == 18 - context["_object_damage_applied"]


@pytest.mark.parametrize("removal", ["expiry", "dispel"])
def test_wall_ice_actual_lifetime_or_dispel_retires_each_panel_and_sheet(monkeypatch, removal):
    state, data, _ = _cast()
    _attack(state, monkeypatch)
    if removal == "expiry":
        _run("advance_srd_lasting_time", state, elapsed_rounds=100)
    else:
        _run(
            "dispel_srd_environment_source",
            state,
            participant_data=data,
            caster_state="hero",
            caster="hero",
            area_id="hero",
            point=[20, -5, 3],
            slot_level=6,
            casting_modifier=4,
            casting_ability="intelligence",
        )
    assert not state[f"{SRD}.state"]["has_wall_of_ice"]
    assert all(not panel["active"] and panel["movement"] == "passable" for panel in _panels(state))
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[25, -5, 0])
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000
    assert "last_wall_of_ice_sheet" not in state[f"{SRD}.entities"]["guard"]


def test_public_wall_ice_paid_later_flame_sheet_reopen_retry_and_removal(tmp_path, monkeypatch):
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import MechanicsRejected, StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from tests import test_srd_public_spell_casting as public_casting
    from tests.srd_full.test_srd_spell_environment import _woodland_test_archive

    monkeypatch.setattr(public_casting, "bundled_srd_archive", _woodland_test_archive)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _name: _public_case())
    database = tmp_path / "wall-of-ice.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public_casting._create_spell_campaign(
            owner, "wall_of_ice", target_overrides={"hp": 1000, "max_hp": 1000, "temp_hp": 0}
        )
        command = ActionCommand(
            campaign_id="casting",
            action_id="ice-cast",
            idempotency_key="ice-cast",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        mechanics = StatelessMcpMechanics(owner)
        cast = mechanics.execute_action(command)
        assert cast.status is ActionStatus.COMMITTED, cast.result
        panels = _panels(owner.snapshot("casting").state)
        assert len(panels) == 10
        assert (
            owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"]
            == 2
        )
        panel_id = panels[4]["id"]
        blocked = replace(
            command,
            action_id="ice-intact-move",
            idempotency_key="ice-intact-move",
            expected_revision=owner.snapshot("casting").revision,
            tool_name="move_srd_scene_creature",
            parameters={"actor_state": "guard", "destination": [25, -5, 0]},
        )
        before = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected, match="intact Ice section"):
            mechanics.execute_action(blocked)
        assert owner.snapshot("casting").state == before.state
        assert owner.snapshot("casting").revision == before.revision
        _, _, flame_params = _spell_case("produce_flame")
        flame_command = replace(
            command,
            action_id="ice-flame",
            idempotency_key="ice-flame",
            expected_revision=before.revision,
            parameters=flame_params,
        )
        flame = mechanics.execute_action(flame_command)
        assert flame.status is ActionStatus.COMMITTED, flame.result
        monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
        monkeypatch.setattr(dice, "_rng", Random(1))
        hurl_command = replace(
            command,
            action_id="ice-hurl",
            idempotency_key="ice-hurl",
            expected_revision=owner.snapshot("casting").revision,
            tool_name="hurl_srd_produce_flame",
            parameters={"caster": "hero", "caster_state": "hero", "object_id": panel_id},
        )
        hit = mechanics.execute_action(hurl_command)
        assert hit.status is ActionStatus.COMMITTED, hit.result
        assert hit.result["context"]["_produce_armor_class"] == 12
        saved = owner.snapshot("casting")
        assert _panels(saved.state)[4]["movement"] == "passable"
        assert _panels(saved.state)[4]["metadata"]["srd_spell_object"]["hp"] == 0
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        for original, result in ((command, cast), (flame_command, flame), (hurl_command, hit)):
            assert mechanics.execute_action(original).result == result.result
        assert owner.snapshot("casting").state == saved.state
        move_command = replace(
            blocked,
            action_id="ice-breached-move",
            idempotency_key="ice-breached-move",
            expected_revision=saved.revision,
        )
        moved = mechanics.execute_action(move_command)
        assert moved.status is ActionStatus.COMMITTED, moved.result
        after_move = owner.snapshot("casting")
        guard = after_move.state[f"{SRD}.entities"]["guard"]
        assert guard["last_wall_of_ice_sheet"]["raw_damage"]["count"] == 5
        assert guard["hp"] == 1000 - guard["last_wall_of_ice_sheet"]["applied_damage"]
        assert _position(after_move.state, "guard")["position"] == [25, -5, 0]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == after_move.state
        assert mechanics.execute_action(move_command).result == moved.result
        assert owner.snapshot("casting").state == after_move.state
        ended_command = replace(
            command,
            action_id="ice-source-end",
            idempotency_key="ice-source-end",
            expected_revision=after_move.revision,
            tool_name="end_concentration",
            parameters={"caster_state": "hero"},
        )
        ended = mechanics.execute_action(ended_command)
        assert ended.status is ActionStatus.COMMITTED, ended.result
        final = owner.snapshot("casting")
        assert not final.state[f"{SRD}.state"]["has_wall_of_ice"]
        assert all(
            not panel["active"] and panel["movement"] == "passable"
            for panel in _panels(final.state)
        )
        assert final.state[f"{SRD}.entities"]["guard"]["hp"] == guard["hp"]
        assert mechanics.execute_action(hurl_command).result == hit.result
        assert mechanics.execute_action(ended_command).result == ended.result
        assert owner.snapshot("casting").state == final.state
