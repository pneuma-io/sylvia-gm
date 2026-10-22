"""Source-directed messengers consume actual clocks, movement qualifications and scene routes."""

from copy import deepcopy

import pytest

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_wave3_information import SRD, _entity, _wall
from tests.srd_full.test_srd_spell_wave4_divination import _case


def _courier(destination=(1000, 0, 0), **body):
    state, data, params = _case("animal_messenger")
    params["divination_destination"] = list(destination)
    state[f"{SRD}.entities"]["guard"].update(body)
    _run("cast_spell", state, participant_data=data, **params)
    return state, data, params


def _position(state):
    return next(
        entity["position"]
        for entity in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if entity["id"] == "guard"
    )


@pytest.mark.parametrize("flight,distance", [(0, 132000), (60, 264000)])
def test_native_flight_uses_actual_body_speed_and_real_clock(flight, distance):
    state, _, _ = _courier(flying_speed=flight)
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert _position(state) == pytest.approx([20 + distance * 20 / 28800, 0, 0])
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="actual position"):
        _run("advance_srd_animal_messenger", state, target_state="guard", destination=[1000, 0, 0])
    assert state == before


def test_actual_speed_zero_does_not_bank_unused_time_and_exhaustion_changes_pace():
    state, _, _ = _courier(exhaustion_level=2)
    state[f"{SRD}.entities"]["guard"]["conditions"] = ["grappled"]
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert _position(state) == [20, 0, 0]
    state[f"{SRD}.entities"]["guard"]["conditions"] = []
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert _position(state) == pytest.approx([20 + 132000 * 20 / 28800 * 20 / 30, 0, 0])


def test_daily_travel_cannot_bypass_actual_combat_turn_movement():
    state, _, _ = _courier()
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=True, active_actor="hero")
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=20)
    assert _position(state) == [20, 0, 0]
    assert state[f"{SRD}.entities"]["guard"]["animal_messenger"]["last_tick"] == 20


def test_unusable_native_flight_falls_back_to_actual_walking_speed():
    state, _, _ = _courier(flying_speed=5, exhaustion_level=1)
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert _position(state) == pytest.approx([20 + 132000 * 20 / 28800 * 25 / 30, 0, 0])


def test_partial_route_consumes_exact_difficult_terrain_pieces():
    state, _, _ = _courier(destination=(500, 0, 0))
    state[f"{SRD}.state"]["spatial_scene"]["zones"] = [
        {
            "id": "thicket",
            "map_id": "information",
            "shape": {"kind": "box", "min": [50, -10, -1], "max": [200, 10, 10]},
            "metadata": {"plane": "material", "difficult": True},
        }
    ]
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    credit = 132000 * 20 / 28800
    assert _position(state) == pytest.approx([50 + (credit - 30) / 2, 0, 0])


def test_magical_flight_expiry_splits_actual_large_interval_then_uses_ground_speed():
    state, data, params = _courier(destination=(5000, 0, 0))
    _run(
        "prepare_srd_creature",
        state,
        creature_id="flyer",
        name="Source flyer",
        source="Synthetic public caster with an explicit material-component exemption",
        armor_class=12,
        hit_points=25,
        speed=30,
        flying_speed=10,
        initiative_modifier=0,
        size_rank=2,
        constitution_save=0,
        spellcasting_ability="wisdom",
        spellcasting_modifier=3,
        spell_attack_bonus=7,
        spell_save_dc=15,
        spell_component_exemptions=["material"],
        spells=[{"spell": "fly", "uses_per_day": 0}],
    )
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity("flyer", [0, 0, 0], participant_ref="flyer")
    )
    casting = {
        key: value
        for key, value in params.items()
        if not key.startswith(("divination", "information"))
    }
    for key in ("caster", "slot_resource", "material_item_ids"):
        casting.pop(key, None)
    casting.update(spell="fly", caster_state="flyer", targets=["guard"])
    _run("cast_spell_outside_combat", state, **casting)
    _run("advance_srd_lasting_time", state, elapsed_rounds=200)
    assert _position(state) == pytest.approx([20 + (264000 + 132000) * 200 / 28800, 0, 0])
    assert state[f"{SRD}.entities"]["flyer"]["flying_speed"] == 10


def test_solid_barrier_uses_calculated_admitted_grid_detour():
    state, _, _ = _courier(destination=(80, 0, 0))
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["map"]["grid"].update(cell_size=5, origin=[-102.5, -102.5])
    scene["map"]["bounds"] = {"min": [-102.5, -102.5, -100], "max": [102.5, 102.5, 100]}
    scene["map"]["layers"] = [{"id": "world", "min_elevation": -100, "max_elevation": 100}]
    wall = _wall(5, low_y=-5, high_y=5)
    wall["volume"].update(min=[30, -5, -10], max=[35, 5, 10])
    scene["geometry"] = [wall]
    _run("advance_srd_lasting_time", state, elapsed_rounds=20)
    assert _position(state) == pytest.approx([80, 0, 0])
    courier = state[f"{SRD}.entities"]["guard"]["animal_messenger"]
    assert courier["route_reason"] == "calculated_detour"
    assert courier["phase"] == "arrived"


def test_expired_outbound_message_returns_during_same_clock_jump_and_later_finishes():
    state, _, _ = _courier(destination=(200000, 0, 0))
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["map"]["bounds"] = {"min": [-500000] * 3, "max": [500000] * 3}
    scene["map"]["grid"].update(cell_size=1000, origin=[-500000, -500000])
    _run("advance_srd_lasting_time", state, elapsed_rounds=20000)
    courier = state[f"{SRD}.entities"]["guard"]["animal_messenger"]
    assert courier["phase"] == "returning"
    assert courier["message_lost"] is True and courier["message_words"] == []
    assert _position(state) == pytest.approx([132020 - 132000 * 11200 / 28800, 0, 0])
    assert not state[f"{SRD}.state"]["has_divination_spells"]
    assert state[f"{SRD}.state"]["has_animal_messengers"] is True
    _run("advance_srd_lasting_time", state, elapsed_rounds=10000)
    assert _position(state) == [0, 0, 0]
    assert state[f"{SRD}.entities"]["guard"]["animal_messenger"]["phase"] == "returned"
    assert state[f"{SRD}.state"]["has_animal_messengers"] is False


def test_arrival_delivery_requires_actual_recipient_at_saved_destination():
    state, _, _ = _courier(destination=(80, 0, 0))
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity("other", [80, 0, 0], participant_ref="other")
    )
    _run("advance_srd_lasting_time", state, elapsed_rounds=20)
    _run(
        "advance_srd_animal_messenger",
        state,
        target_state="guard",
        destination=[80, 0, 0],
        recipient_state="other",
        recipient_matches_description=True,
    )
    message = deepcopy(state[f"{SRD}.entities"]["other"]["received_animal_message"])
    assert message["mimics_caster"] is True and message["caster"] == "hero"
    _run(
        "advance_srd_animal_messenger",
        state,
        target_state="guard",
        destination=[80, 0, 0],
        recipient_state="other",
        recipient_matches_description=True,
    )
    assert state[f"{SRD}.entities"]["other"]["received_animal_message"] == message


def test_public_courier_reopens_retries_and_delivers_after_actual_clock(tmp_path, monkeypatch):
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus
    from tests import test_srd_public_spell_casting as public
    from tests.srd_full.test_srd_npc_spell_casting import _act

    def case(spell):
        state, data, parameters = _case(spell)
        parameters["divination_destination"] = [500, 0, 0]
        state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
            _entity("other", [500, 0, 0], participant_ref="other")
        )
        return state, data, parameters

    monkeypatch.setattr(public, "_spell_case", case)
    database = tmp_path / "automatic-courier.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(
            owner, "animal_messenger", target_overrides={"flying_speed": 60}
        )
        mechanics = StatelessMcpMechanics(owner)
        schemas = {tool.name: tool.input_schema for tool in mechanics.list_tools("casting")}
        assert "advance_srd_animal_messenger" in schemas
        assert "lasting_state" not in schemas["advance_srd_animal_messenger"]["properties"]
        assert "flying_speed" in schemas["prepare_srd_creature"]["properties"]
        cast, command = _act(owner, "cast_spell_outside_combat", **parameters)
        assert cast.status is ActionStatus.COMMITTED
        elapsed, elapsed_command = _act(owner, "advance_srd_lasting_time", elapsed_rounds=1)
        assert elapsed.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert list(_position(saved.state)) == pytest.approx([20 + 264000 * 2 / 28800, 0, 0])
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(command) == cast
        assert mechanics.execute_action(elapsed_command) == elapsed
        elapsed2, _ = _act(owner, "advance_srd_lasting_time", elapsed_rounds=30)
        assert elapsed2.status is ActionStatus.COMMITTED
        assert list(_position(owner.snapshot("casting").state)) == [500, 0, 0]
        delivered, delivered_command = _act(
            owner,
            "advance_srd_animal_messenger",
            target_state="guard",
            destination=[500, 0, 0],
            recipient_state="other",
            recipient_matches_description=True,
        )
        assert delivered.status is ActionStatus.COMMITTED
        after = owner.snapshot("casting")
        assert after.state[f"{SRD}.entities"]["guard"]["animal_messenger"]["phase"] == "delivered"
        message = after.state[f"{SRD}.entities"]["other"]["received_animal_message"]
        assert message["mimics_caster"] is True
        assert list(message["words"]) == parameters["message_words"]
        assert after.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
        assert mechanics.execute_action(delivered_command) == delivered
        assert owner.snapshot("casting").state == after.state
