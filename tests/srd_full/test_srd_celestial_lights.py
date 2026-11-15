"""Later Celestial lights follow real movement, actual Cylinder entry and native turn endings."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_celestial_lights import bind_runtime_consumers
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests import test_srd_action_budget as budget
from tests import test_srd_conjured_presences as presence
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_dwelling as dwelling
from tests import test_srd_spell_travel_telekinesis_fall as fall
from tests.srd_full.test_srd_spell_travel import SRD, _point, _TravelDice, dice

actual_fall_scope = presence.actual_fall_scope
conjured_scope = presence.conjured_scope


@pytest.fixture
def celestial_scope(conjured_scope, monkeypatch):
    monkeypatch.setattr(
        fall, "RUNTIME_PATH", Path(".scratch/celestial-lights-composition/runtime.yaml")
    )
    original = bundled_packages._srd_drafts

    def drafts():
        primary, reference = original()
        record = deepcopy(primary.record)
        template = next(row for row in record["tools"] if row["name"] == "use_srd_conjured_fey")
        declaration = deepcopy(template)
        declaration["name"] = "move_srd_celestial_cylinder"
        declaration["input_schema"] = _tool_schema(
            fall._runtime()[0]["tools"][declaration["name"]], tool_name=declaration["name"]
        )
        record["tools"].append(declaration)
        payload = dict(primary.payloads)
        declared = json.loads(payload["content/content-declarations.json"])
        inclusion = next(
            row
            for row in declared["inclusions"]
            if "use_srd_conjured_fey" in row.get("links", {}).get("tools", [])
        )
        inclusion["links"]["tools"] = sorted(
            set(inclusion["links"]["tools"]) | {declaration["name"]}
        )
        payload["content/content-declarations.json"] = canonical_json_bytes(declared)
        return NormalizedPackageDraft(record, payload, primary.format_version), reference

    monkeypatch.setattr(bundled_packages, "_srd_drafts", drafts)


def _case(begin=True):
    state, data, params = presence._case(celestial_searing_targets=[])
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["position"] = [45, 0, 0]
    scene["entities"][2]["position"] = [0, 60, 0]
    state[f"{SRD}.entities"]["hero"]["hp"] = 100
    if begin:
        state[f"{SRD}.combat"].update(
            encounter_active=True, turn_active=False, current_actor="hero", phase="awaiting_turn"
        )
        budget._run("begin_combat_turn", state, participant_data=data, actor="hero")
    return state, data, params


def _rolls(monkeypatch, rolls):
    game = _TravelDice(rolls)
    monkeypatch.setattr(dice, "_rng", game)
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    return game


def _cast(state, data, params):
    return budget._run("cast_spell", state, participant_data=data, **params)


def _walk(state, data, point):
    return budget._run(
        "move_srd_scene_creature",
        state,
        participant_data=data,
        actor_state="hero",
        destination=point,
    )


def _cylinder(state, data, point, **choices):
    return budget._run(
        "move_srd_celestial_cylinder",
        state,
        participant_data=data,
        caster_state="hero",
        source_id="presence",
        point=point,
        **choices,
    )


def test_later_binding_changes_only_its_initial_claim_and_real_movement_turn_owners():
    text = Path(".scratch/celestial-lights-composition/origin-runtime.yaml").read_text()
    before = _load_runtime(text)
    joined = _load_runtime(bind_runtime_consumers(text))
    changed = {
        "resolve_srd_celestial_initial_lights",
        "move_srd_scene_creature_node",
        "end_combat_turn",
    }
    for name, tool in before["tools"].items():
        if name not in changed:
            assert joined["tools"][name] == tool
    assert {key: value for key, value in joined.items() if key != "tools"} == {
        key: value for key, value in before.items() if key != "tools"
    }
    assert _load_runtime(bind_runtime_consumers(bind_runtime_consumers(text))) == joined


def test_actual_paid_cast_then_movement_earns_one_cylinder_move_and_one_native_light(
    celestial_scope, actual_fall_scope, monkeypatch
):
    state, data, params = _case()
    game = _rolls(monkeypatch, [(12, 3)] * 4 + [(12, 2)] * 6 + [(20, 10)])
    _cast(state, data, params)
    caster = state[f"{SRD}.entities"]["hero"]
    assert caster["hp"] == 116
    assert presence._source(state)["cast_id"] == "srd_cast:1"
    assert presence._source(state)["event_target_ids"] == ["hero"]
    assert caster["resources"]["spell_slot_7"] == 2
    assert state[f"{SRD}.combat"]["action_available"] is False
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Move the actual caster"):
        _cylinder(state, data, [35, 0, 0], searing_targets=["guard"])
    assert state == before
    _walk(state, data, [0, 5, 0])
    assert _point(state) == [0, 5, 0]
    receipt = deepcopy(presence._source(state)["celestial_movement_receipt"])
    assert receipt["caster"] == "hero" and receipt["cast_id"] == "srd_cast:1"
    assert receipt["position"] == [0, 5, 0]
    _cylinder(state, data, [35, 0, 0], searing_targets=["guard"])
    source = presence._source(state)
    assert source["origin"] == [35, 0, 0] and source["cylinder_min"] == 0
    assert source["celestial_movement_receipt"] is None
    assert source["event_target_ids"] == ["hero", "guard"]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 494
    assert caster["resources"]["spell_slot_7"] == 2
    assert state[f"{SRD}.combat"]["movement_spent"] == 5
    assert state[f"{SRD}.combat"]["action_available"] is False
    after = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Move the actual caster"):
        _cylinder(state, data, [5, 0, 0])
    assert state == after and not game.rolls


def test_additional_real_movement_allows_another_thirty_feet_but_not_same_turn_redamage(
    celestial_scope, actual_fall_scope, monkeypatch
):
    state, data, params = _case()
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["dexterity"] = 0
    game = _rolls(monkeypatch, [(12, 3)] * 4 + [(12, 2)] * 6 + [(20, 1)])
    _cast(state, data, params)
    _walk(state, data, [0, 5, 0])
    _cylinder(state, data, [35, 0, 0], searing_targets=["guard"])
    _walk(state, data, [0, 10, 0])
    _cylinder(state, data, [5, 0, 0])
    _walk(state, data, [0, 15, 0])
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="not yet affected"):
        _cylinder(state, data, [35, 0, 0], searing_targets=["guard"])
    assert state == before
    _cylinder(state, data, [35, 0, 0])
    assert presence._source(state)["celestial_movement_serial"] == 3
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 488
    assert state[f"{SRD}.combat"]["movement_spent"] == 15
    assert not game.rolls


@pytest.mark.parametrize("defect", ["distance", "outside", "duplicate", "blind"])
def test_invalid_later_light_intent_preserves_the_paid_source_and_unspent_receipt(
    celestial_scope, actual_fall_scope, monkeypatch, defect
):
    state, data, params = _case()
    _rolls(monkeypatch, [(12, 3)] * 4)
    _cast(state, data, params)
    _walk(state, data, [0, 5, 0])
    point, choices = [35, 0, 0], {"searing_targets": ["guard"]}
    if defect == "distance":
        point = [36, 0, 0]
    elif defect == "outside":
        choices = {"searing_targets": ["hero"]}
    elif defect == "duplicate":
        choices["healing_targets"] = ["guard"]
    elif defect == "blind":
        state[f"{SRD}.entities"]["hero"]["conditions"].append("blinded")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _cylinder(state, data, point, **choices)
    assert state == before


def test_actual_creature_turn_end_earns_new_turn_light_without_a_caster_action(
    celestial_scope, actual_fall_scope, monkeypatch
):
    state, data, params = _case()
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["dexterity"] = 0
    game = _rolls(
        monkeypatch, [(12, 3)] * 4 + [(12, 2)] * 6 + [(20, 20)] + [(12, 3)] * 6 + [(20, 1)]
    )
    _cast(state, data, params)
    _walk(state, data, [0, 5, 0])
    _cylinder(state, data, [35, 0, 0], searing_targets=["guard"])
    budget._run("end_combat_turn", state, actor="hero")
    state[f"{SRD}.combat"]["current_actor"] = "guard"
    budget._run("begin_combat_turn", state, actor="guard")
    before = deepcopy(state)
    budget._run(
        "end_combat_turn",
        state,
        actor="guard",
        celestial_light_choices=[{"source_id": "presence", "light": "searing"}],
    )
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 476
    assert presence._source(state)["event_target_ids"] == ["guard"]
    assert presence._source(state)["event_turn"] == state[f"{SRD}.combat"]["turn_sequence"]
    assert (
        state[f"{SRD}.entities"]["hero"]["resources"]
        == before[f"{SRD}.entities"]["hero"]["resources"]
    )
    assert _point(state, "guard") == [45, 0, 0] and not game.rolls


def test_invalid_actual_turn_end_intent_is_atomic_before_native_boundary_consequences(
    celestial_scope, actual_fall_scope, monkeypatch
):
    state, data, params = _case()
    _rolls(monkeypatch, [(12, 3)] * 4)
    _cast(state, data, params)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="already had this light"):
        budget._run(
            "end_combat_turn",
            state,
            celestial_light_choices=[{"source_id": "presence", "light": "healing"}],
        )
    assert state == before


def test_exact_ended_source_does_not_obstruct_later_ordinary_caster_movement(
    celestial_scope, actual_fall_scope, monkeypatch
):
    state, data, params = _case()
    _rolls(monkeypatch, [(12, 3)] * 4)
    _cast(state, data, params)
    budget._run("end_concentration", state, caster_state="hero")
    assert presence._source(state)["active"] is False
    _walk(state, data, [0, 5, 0])
    assert _point(state) == [0, 5, 0]
    assert presence._source(state).get("celestial_movement_receipt") is None


@pytest.mark.parametrize("face,height", [("bottom", 0), ("top", 40)])
def test_paid_top_or_bottom_origin_retains_the_actual_circular_face_during_movement(
    celestial_scope, actual_fall_scope, monkeypatch, face, height
):
    state, data, params = _case()
    params.update(conjured_point=[5, 0, height], celestial_origin_face=face)
    game = _rolls(monkeypatch, [(12, 3)] * 4)
    _cast(state, data, params)
    source = presence._source(state)
    assert source["origin"] == [5, 0, height]
    assert source["cylinder_origin_face"] == face
    assert source["cylinder_min"] == 0 and source["cylinder_max"] == 40
    _walk(state, data, [0, 5, 0])
    _cylinder(state, data, [35, 0, height])
    source = presence._source(state)
    assert source["origin"] == [35, 0, height]
    assert source["cylinder_min"] == 0 and source["cylinder_max"] == 40
    assert source["cylinder_origin_face"] == face and not game.rolls


def test_mid_height_point_is_not_substituted_for_the_printed_circular_origin(
    celestial_scope, actual_fall_scope
):
    state, data, params = _case()
    params["conjured_point"] = [5, 0, 20]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="one light|visible creature"):
        _cast(state, data, params)
    assert state == before


def test_real_paid_celestial_movement_light_saves_reopens_retries_and_ends_exact_source(
    celestial_scope, actual_fall_scope, monkeypatch, tmp_path
):
    monkeypatch.setattr(public, "_spell_case", lambda _spell: _case(begin=False))
    game = _rolls(
        monkeypatch, [(20, 20), (20, 10), (20, 1)] + [(12, 3)] * 4 + [(12, 2)] * 6 + [(20, 10)]
    )
    database = tmp_path / "celestial-lights.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner, "conjure_celestial", target_overrides={"hp": 500, "max_hp": 500}
        )
        mechanics = StatelessMcpMechanics(owner)

        def commit(name, intent, action):
            command = dwelling._command(intent, owner.snapshot("casting").revision, name, action)
            result = mechanics.execute_action(command)
            assert result.status is ActionStatus.COMMITTED, result.result
            return command, result

        commit("start_group_combat_encounter", {"combatants": ["hero", "guard", "other"]}, "start")
        commit("begin_combat_turn", {}, "begin")
        paid_command, paid = commit("cast_spell", params, "paid-celestial")
        paid_state = owner.snapshot("casting")
        assert presence._source(paid_state.state)["cast_id"] == "srd_cast:1"
        assert paid_state.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == 2
        assert paid_state.state[f"{SRD}.combat"]["action_available"] is False
        assert mechanics.execute_action(paid_command) == paid
        commit("move_srd_scene_creature", {"actor_state": "hero", "destination": [0, 5, 0]}, "walk")
        moved = owner.snapshot("casting")
        assert presence._source(moved.state)["celestial_movement_receipt"]["position"] == [0, 5, 0]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == moved
        mechanics = StatelessMcpMechanics(owner)
        command = dwelling._command(
            {
                "caster_state": "hero",
                "source_id": "presence",
                "point": [35, 0, 0],
                "searing_targets": ["guard"],
            },
            moved.revision,
            "move_srd_celestial_cylinder",
            "earned-light",
        )
        result = mechanics.execute_action(command)
        assert result.status is ActionStatus.COMMITTED, result.result
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["guard"]["hp"] == 494
        assert presence._source(saved.state)["event_target_ids"] == ["hero", "guard"]
        assert saved.state[f"{SRD}.combat"]["movement_spent"] == 5
        assert saved.state[f"{SRD}.combat"]["action_available"] is False
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == 2
        assert mechanics.execute_action(command) == result and owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        mechanics = StatelessMcpMechanics(owner)
        command = dwelling._command(
            {"caster_state": "hero"}, saved.revision, "end_concentration", "end"
        )
        result = mechanics.execute_action(command)
        assert result.status is ActionStatus.COMMITTED, result.result
        ended = owner.snapshot("casting")
        assert presence._source(ended.state)["active"] is False
        assert presence._source(ended.state)["cast_id"] == "srd_cast:1"
        assert (
            ended.state[f"{SRD}.entities"]["hero"]["resources"]
            == saved.state[f"{SRD}.entities"]["hero"]["resources"]
        )
        assert mechanics.execute_action(command) == result and owner.snapshot("casting") == ended
    assert not game.rolls
