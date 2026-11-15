"""Minor sensory casting accepts admitted theatre-of-mind facts without a scene."""

from copy import deepcopy
from pathlib import Path

import pytest

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import MechanicsRejected
from tests import test_srd_celestial_initial_sight as initial
from tests import test_srd_celestial_lights as light
from tests import test_srd_conjured_presences as presence
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_dwelling as dwelling
from tests import test_srd_spell_travel_telekinesis_fall as fall
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_travel import SRD

actual_fall_scope = initial.actual_fall_scope
conjured_scope = initial.conjured_scope
celestial_scope = initial.celestial_scope


def test_paid_druidcraft_weather_without_scene_rejects_range_and_reopens_retry(
    celestial_scope, actual_fall_scope, monkeypatch, tmp_path
):
    monkeypatch.setattr(
        fall, "RUNTIME_PATH", Path(".scratch/minor-world-alpha-composition/runtime.yaml")
    )

    def case(_spell):
        state, data, params = presence._case("druidcraft")
        state[f"{SRD}.state"].pop("spatial_scene")
        return state, data, params

    monkeypatch.setattr(public, "_spell_case", case)
    game = light._rolls(monkeypatch, [(20, 20), (20, 10), (20, 1)])
    database = tmp_path / "minor-without-scene.db"
    forecast = "Synthetic local rain during the next day"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "druidcraft")
        admitted, _ = _act(
            owner,
            "record_srd_local_weather_forecast",
            caster_state="hero",
            forecast=forecast,
        )
        assert admitted.status is ActionStatus.COMMITTED, admitted.result
        saved_forecast = owner.snapshot("casting").state[f"{SRD}.entities"]["hero"][
            "srd_local_forecast"
        ]
        assert saved_forecast["origin"] is None and saved_forecast["plane"] is None
        assert saved_forecast["through_tick"] - saved_forecast["recorded_tick"] == 28800
        mechanics = StatelessMcpMechanics(owner)
        for name, intent, action in [
            ("start_group_combat_encounter", {"combatants": ["hero", "guard", "other"]}, "start"),
            ("begin_combat_turn", {}, "begin-hero"),
        ]:
            setup = dwelling._command(intent, owner.snapshot("casting").revision, name, action)
            assert mechanics.execute_action(setup).status is ActionStatus.COMMITTED
        params.update(
            minor_mode="weather_sensor",
            origin_distance=0,
            origin_has_clear_path=True,
            minor_description="Caller does not replace the admitted forecast",
        )
        before = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected, match="printed spell range"):
            _act(owner, "cast_spell", **(params | {"origin_distance": 31}))
        assert owner.snapshot("casting") == before
        command = dwelling._command(params, before.revision, "cast_spell", "paid-weather")
        result = mechanics.execute_action(command)
        assert result.status is ActionStatus.COMMITTED, result.result
        saved = owner.snapshot("casting")
        state = saved.state[f"{SRD}.state"]
        assert state.get("spatial_scene") is None
        event = state["srd_last_minor_world_event"]
        assert event["description"] == forecast
        assert event["origin"] is None and event["plane"] is None and event["geometry"] is None
        source = next(
            row for row in state["turn_effects"] if row["source_id"] == event["source_id"]
        )
        assert source["expires_tick"] - source["started_tick"] == 2
        assert source["geometry"] is None and event["deals_damage"] is False
        assert saved.state[f"{SRD}.combat"]["action_available"] is False
        assert (
            saved.state[f"{SRD}.entities"]["hero"]["resources"]
            == before.state[f"{SRD}.entities"]["hero"]["resources"]
        )
        assert forecast in str(result.result["result_card"])
        assert mechanics.execute_action(command) == result and owner.snapshot("casting") == saved
        assert not game.rolls
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        assert StatelessMcpMechanics(owner).execute_action(command) == result
        assert owner.snapshot("casting") == saved

    from scripts.srd_full.srd_spell_minor_world import _casting_tool

    tool = deepcopy(_casting_tool())
    scene = next(
        effect
        for effect in tool["effects"]
        if effect.get("condition") == "lasting_state.spatial_scene != null"
    )
    assert scene["on_true"][0]["call"] == "srd_information_scene_entity"
    assert scene["on_true"][-1]["require"]["condition"] == "_world_plane != null"
