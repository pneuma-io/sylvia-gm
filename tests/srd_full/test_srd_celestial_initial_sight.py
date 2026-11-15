"""Initial Celestial light derives from actual creation geometry and preserves native sight."""

from copy import deepcopy
from pathlib import Path

import pytest
from scripts.srd_full.srd_celestial_initial_sight import COLLECTOR, SIGHT, emit_late_initial_sight
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_celestial_lights as light
from tests import test_srd_conjured_presences as presence
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_dwelling as dwelling
from tests import test_srd_spell_travel_telekinesis_fall as fall
from tests.srd_full.test_srd_spell_travel import SRD
from tests.srd_full.test_srd_spell_wave3_information import _wall

actual_fall_scope = light.actual_fall_scope
conjured_scope = light.conjured_scope
celestial_scope = light.celestial_scope


@pytest.fixture
def initial_scope(celestial_scope, monkeypatch):
    monkeypatch.setattr(
        fall, "RUNTIME_PATH", Path(".scratch/celestial-initial-sight-composition/runtime.yaml")
    )


def _case(*, face="bottom"):
    state, data, params = presence._case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    for zone in scene["zones"]:
        if zone["metadata"].get("plane") == "material":
            zone["metadata"]["lighting"] = "darkness"
    state[f"{SRD}.entities"]["hero"]["hp"] = 100
    params.update(celestial_origin_face=face, conjured_point=[5, 0, 40 if face == "top" else 0])
    return state, data, params


def _sight(state, data, *, point=(5, 0, 0), face="bottom", prospective=True):
    params = {"observer_state": "hero", "subject": "guard"}
    if prospective:
        params.update(creation_point=list(point), creation_origin_face=face)
    return budget._run(
        SIGHT if prospective else "resolve_srd_conjured_subject_sight",
        state,
        participant_data=data,
        **params,
    ).result["context"]


def _begin(state, data):
    state[f"{SRD}.combat"].update(
        encounter_active=True, turn_active=False, current_actor="hero", phase="awaiting_turn"
    )
    budget._run("begin_combat_turn", state, participant_data=data, actor="hero")


def test_initial_sight_keeps_final_sensory_body_and_all_other_tools_exactly():
    text = Path(".scratch/celestial-entries-frozen-v2/excluded/runtime.yaml").read_text()
    before = _load_runtime(text)
    joined = _load_runtime(emit_late_initial_sight(text))
    for name, tool in before["tools"].items():
        if name != COLLECTOR:
            assert joined["tools"][name] == tool
    expected = before["tools"]["resolve_srd_conjured_subject_sight"]["effects"]
    actual = deepcopy(joined["tools"][SIGHT]["effects"])
    index = next(
        i
        for i, effect in enumerate(actual)
        if effect.get("set", {}).get("field") == "_presence_cylinder_min"
    )
    del actual[index : index + 4]
    assert actual == expected
    assert _load_runtime(emit_late_initial_sight(emit_late_initial_sight(text))) == joined


@pytest.mark.parametrize("face", ["bottom", "top"])
def test_paid_initial_dark_cylinder_uses_its_printed_bright_light_and_native_packets(
    initial_scope, actual_fall_scope, monkeypatch, face
):
    state, data, params = _case(face=face)
    assert _sight(state, data, prospective=False)["_illumination_point_can_see"] is False
    game = light._rolls(monkeypatch, [(12, 2)] * 6 + [(12, 3)] * 4 + [(20, 10)])
    _begin(state, data)
    light._cast(state, data, params)
    source = presence._source(state)
    assert source["cast_id"] == "srd_cast:1"
    assert source["cylinder_origin_face"] == face
    assert source["cylinder_min"] == 0 and source["cylinder_max"] == 40
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 494
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 116
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == 2
    assert not game.rolls


@pytest.mark.parametrize("defect", ["blind", "obscured", "solid"])
def test_prospective_bright_light_preserves_actual_blindness_obscurement_and_solids(
    initial_scope, actual_fall_scope, defect
):
    state, data, _ = _case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    if defect == "blind":
        state[f"{SRD}.entities"]["hero"]["conditions"].append("blinded")
    elif defect == "obscured":
        scene["zones"].append(
            {
                "id": "smoke",
                "map_id": "information",
                "shape": {"kind": "box", "min": [1, -1, -1], "max": [2, 1, 8]},
                "metadata": {"plane": "material", "obstruction": "heavy_obscurement"},
            }
        )
    else:
        wall = _wall(1)
        wall["volume"].update(min=[1, -1, -1], max=[2, 1, 8])
        wall["metadata"]["plane"] = "material"
        scene["geometry"].append(wall)
    before = deepcopy(state)
    sight = _sight(state, data)
    assert sight["_illumination_point_lighting"] == "bright"
    assert sight["_illumination_point_can_see"] is False
    assert state == before


@pytest.mark.parametrize("defect", ["radius", "height", "plane"])
def test_prospective_light_never_extends_beyond_actual_circle_faces_or_plane(
    initial_scope, actual_fall_scope, defect
):
    state, data, _ = _case()
    body = state[f"{SRD}.state"]["spatial_scene"]["entities"][1]
    if defect == "radius":
        body["position"] = [15.000001, 0, 0]
    elif defect == "height":
        body["position"] = [5, 0, 40.000001]
    else:
        body["metadata"]["plane"] = "ethereal"
    sight = _sight(state, data)
    assert sight["_illumination_point_lighting"] == "darkness"
    assert sight["_illumination_point_can_see"] is False


def test_actual_dark_initial_omitted_target_rejects_atomically_before_payment_or_roll(
    initial_scope, actual_fall_scope, monkeypatch
):
    state, data, params = _case()
    params["celestial_searing_targets"] = []
    game = light._rolls(monkeypatch, [])
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="every actual visible creature"):
        presence._cast(state, data, params)
    assert state == before and not game.rolls


def test_real_paid_initial_dark_light_source_reopens_and_retries_without_new_cost(
    initial_scope, actual_fall_scope, monkeypatch, tmp_path
):
    monkeypatch.setattr(public, "_spell_case", lambda _spell: _case())
    game = light._rolls(
        monkeypatch,
        [(20, 20), (20, 10), (20, 1)] + [(12, 2)] * 6 + [(12, 3)] * 4 + [(20, 10)],
    )
    database = tmp_path / "celestial-dark.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner, "conjure_celestial", target_overrides={"hp": 500, "max_hp": 500}
        )
        mechanics = StatelessMcpMechanics(owner)
        for name, intent, action in [
            ("start_group_combat_encounter", {"combatants": ["hero", "guard", "other"]}, "start"),
            ("begin_combat_turn", {}, "begin-hero"),
        ]:
            setup = dwelling._command(intent, owner.snapshot("casting").revision, name, action)
            assert mechanics.execute_action(setup).status is ActionStatus.COMMITTED
        command = dwelling._command(
            params,
            owner.snapshot("casting").revision,
            "cast_spell",
            "paid-celestial-dark",
        )
        result = mechanics.execute_action(command)
        assert result.status is ActionStatus.COMMITTED, result.result
        saved = owner.snapshot("casting")
        assert presence._source(saved.state)["cast_id"] == "srd_cast:1"
        assert saved.state[f"{SRD}.entities"]["guard"]["hp"] == 494
        assert saved.state[f"{SRD}.entities"]["hero"]["hp"] == 116
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == 2
        assert mechanics.execute_action(command) == result and owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        assert StatelessMcpMechanics(owner).execute_action(command) == result
        assert owner.snapshot("casting") == saved
    assert not game.rolls
