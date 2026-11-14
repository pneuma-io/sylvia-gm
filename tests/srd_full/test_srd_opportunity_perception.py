"""Opportunity perception consumes actual saved targets and owning source sight."""

import json
from copy import deepcopy
from functools import lru_cache

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_spell_illumination import _target_sight_tools

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_packages import _archive, _srd_drafts
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget, test_srd_spell_result_cards
from tests import test_srd_public_spell_casting as public_casting
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_illumination_point_sight import _runtime as _point_runtime
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_illumination import _case
from tests.srd_full.test_srd_spell_wave3_information import _wall


@lru_cache
def _runtime():
    runtime = deepcopy(_point_runtime()[0])
    runtime["tools"].update(_target_sight_tools(projected=True, environment=True))
    return runtime, yaml.safe_dump(runtime, sort_keys=False).encode()


@lru_cache
def _mechanic(name):
    runtime, payload = _runtime()
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
def _actual_runtime(monkeypatch):
    monkeypatch.setattr(test_srd_action_budget, "_mechanic", _mechanic)
    monkeypatch.setattr(
        test_srd_spell_result_cards,
        "_spell_definitions",
        lambda: _runtime()[0]["definitions"]["spells"],
    )


def _target(state, identity="guard"):
    return next(
        entry
        for entry in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if entry["metadata"].get("participant_ref") == identity
    )


def _see(state, observer="hero", target="guard"):
    return _run(
        "resolve_srd_opportunity_perception", state, observer_state=observer, target_state=target
    ).result["context"]


@pytest.mark.parametrize(
    "spell,ambient,before,after,rounds",
    [
        ("light", "darkness", True, False, 600),
        ("darkness", "bright", False, True, 100),
        ("fog_cloud", "bright", False, True, 600),
    ],
)
def test_real_cast_persisted_source_and_expiry_change_actual_target_perception(
    spell, ambient, before, after, rounds
):
    state, data, params = _case(spell)
    _target(state)["metadata"]["lighting"] = ambient
    if spell == "darkness":
        params.update(illumination_object="", illumination_position=[5, 0, 0])
        state[f"{SRD}.entities"]["hero"]["darkvision_feet"] = 60
    if spell == "fog_cloud":
        params["environment_origin"] = [5, 0, 0]
    _run("cast_spell", state, participant_data=data, **params)
    assert _see(state)["_observer_can_see_target"] is before
    state = json.loads(json.dumps(state))
    assert _see(state)["_observer_can_see_target"] is before
    _run("advance_srd_lasting_time", state, elapsed_rounds=rounds)
    assert _see(state)["_observer_can_see_target"] is after


@pytest.mark.parametrize(
    "conditions,blindsight,visible",
    [
        ([], 0, True),
        (["blinded"], 0, False),
        (["blinded"], 4, False),
        (["blinded"], 5, True),
    ],
)
def test_actual_blindness_and_saved_blindsight_use_exact_target_distance(
    conditions, blindsight, visible
):
    state, _, _ = _case("light")
    state[f"{SRD}.entities"]["hero"].update(conditions=conditions, blindsight_feet=blindsight)
    before = deepcopy(state)
    context = _see(state)
    assert context["_observer_can_see_target"] is visible
    assert context["_illumination_point_distance"] == 5
    assert context["_illumination_point_origin"] == [0, 0, 0]
    assert state == before


def test_real_invisibility_is_perceived_only_with_in_range_actual_truesight():
    state, data, params = _case("invisibility")
    _run("cast_spell", state, participant_data=data, **params)
    assert _see(state)["_observer_can_see_target"] is False
    hero = state[f"{SRD}.entities"]["hero"]
    hero["truesight_feet"] = 4
    assert _see(state)["_observer_can_see_target"] is False
    hero["truesight_feet"] = 5
    assert _see(state)["_observer_can_see_target"] is True


def test_real_outline_is_seen_but_preserves_independent_native_invisible_after_expiry():
    state, data, params = _case("faerie_fire")
    hero = state[f"{SRD}.entities"]["hero"]
    state[f"{SRD}.entities"]["guard"]["conditions"].append("invisible")
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["dexterity"] = -100
    params.update(
        spell="faerie_fire",
        slot_resource="spell_slot_1",
        illumination_object="",
        illumination_position=[1, 0, 0],
    )
    _run("cast_spell", state, participant_data=data, **params)
    hero = state[f"{SRD}.entities"]["hero"]
    assert _see(state)["_observer_can_see_target"] is True
    hero["conditions"].append("blinded")
    assert _see(state)["_observer_can_see_target"] is False
    hero["conditions"].remove("blinded")
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert _see(state)["_observer_can_see_target"] is False
    assert "invisible" in state[f"{SRD}.entities"]["guard"]["conditions"]


@pytest.mark.parametrize(
    "spell,truesight,blindsight,visible",
    [
        ("darkness", 5, 0, True),
        ("fog_cloud", 5, 0, False),
        ("fog_cloud", 0, 5, True),
    ],
)
def test_owning_senses_distinguish_magical_darkness_from_real_fog(
    spell, truesight, blindsight, visible
):
    state, data, params = _case(spell)
    params.update(
        illumination_object="", illumination_position=[5, 0, 0], environment_origin=[5, 0, 0]
    )
    state[f"{SRD}.entities"]["hero"].update(truesight_feet=truesight, blindsight_feet=blindsight)
    _run("cast_spell", state, participant_data=data, **params)
    assert _see(state)["_observer_can_see_target"] is visible


def test_actual_total_cover_blocks_blindsight_and_actual_plane_filters_barriers():
    state, _, _ = _case("light")
    state[f"{SRD}.entities"]["hero"].update(conditions=["blinded"], blindsight_feet=30)
    wall = _wall(2)
    wall["volume"] = {"kind": "box", "min": [2, -5, -5], "max": [3, 5, 5]}
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [wall]
    assert _see(state)["_observer_can_see_target"] is False
    wall["metadata"]["plane"] = "ethereal"
    assert _see(state)["_observer_can_see_target"] is True


def test_cross_plane_sight_needs_actual_ethereal_sense_or_qualified_live_projection():
    state, _, _ = _case("light")
    _target(state)["metadata"]["plane"] = "ethereal"
    hero = state[f"{SRD}.entities"]["hero"]
    hero["blindsight_feet"] = 60
    assert _see(state)["_observer_can_see_target"] is False
    hero["truesight_feet"] = 5
    assert _see(state)["_observer_can_see_target"] is True
    _target(state)["metadata"]["plane"] = "astral"
    assert _see(state)["_observer_can_see_target"] is False
    _target(state)["metadata"]["plane"] = "material"
    hero.update(truesight_feet=0, blindsight_feet=0)
    _run(
        "apply_srd_etherealness_target",
        state,
        target_state="hero",
        caster_state="hero",
        slot_level=7,
    )
    _target(state)["position"] = [60, 0, 0]
    context = _see(state)
    assert context["_observer_can_see_target"] is True
    assert context["_illumination_target_projected"] is True
    assert context["_illumination_target_observer_plane"] == "ethereal"
    assert context["_illumination_target_plane"] == "material"
    _target(state)["position"] = [61, 0, 0]
    assert _see(state)["_observer_can_see_target"] is False
    state[f"{SRD}.state"]["lasting_tick"] = 9600
    context = _see(state)
    assert context["_observer_can_see_target"] is True
    assert context["_illumination_target_same_plane"] is True
    assert state[f"{SRD}.entities"]["hero"]["travel_ethereal"] is None


@pytest.mark.parametrize("missing", ["plane", "lighting"])
def test_missing_actual_geometry_facts_fail_clearly_without_caller_verdict(missing):
    state, _, _ = _case("light")
    del _target(state)["metadata"][missing]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="plane and ambient"):
        _see(state)
    assert state == before


@lru_cache
def _test_archive():
    runtime, payload = _runtime()
    primary, reference = _srd_drafts()
    record = deepcopy(primary.record)
    for tool in record["tools"]:
        if tool["name"] in runtime["tools"]:
            tool["input_schema"] = _tool_schema(
                runtime["tools"][tool["name"]], tool_name=tool["name"]
            )
    return _archive(
        (
            NormalizedPackageDraft(
                record,
                dict(primary.payloads) | {"runtime/ruleset.yaml": payload},
                primary.format_version,
            ),
            reference,
        )
    )


def test_actual_archived_public_light_cast_reopens_then_expires_for_private_opportunity_owner(
    tmp_path, monkeypatch
):
    def dark_target_case(spell):
        state, data, params = _case(spell)
        _target(state)["metadata"]["lighting"] = "darkness"
        return state, data, params

    monkeypatch.setattr(public_casting, "bundled_srd_archive", _test_archive)
    monkeypatch.setattr(public_casting, "_spell_case", dark_target_case)
    with AuthorityOwner.open(tmp_path / "perception.db", "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(owner, "light")
        cast, command = _act(owner, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED, cast.result
        saved = owner.snapshot("casting")
        assert _see(deepcopy(saved.state))["_observer_can_see_target"] is True
    with AuthorityOwner.open(tmp_path / "perception.db", "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        assert owner.execute_action(command) == cast
        assert owner.snapshot("casting") == saved
        elapsed, _ = _act(owner, "advance_srd_lasting_time", elapsed_rounds=600)
        assert elapsed.status is ActionStatus.COMMITTED, elapsed.result
        assert _see(deepcopy(owner.snapshot("casting").state))["_observer_can_see_target"] is False
