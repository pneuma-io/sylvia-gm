"""Sunburst's actual source point selects full bodies before the existing saves and light owner."""

from copy import deepcopy

import pytest

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import PackageRuntimeError
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests.srd_full.test_srd_minor_attack_packets import actual_receipt_owner as actual_receipt_owner
from tests.srd_full.test_srd_minor_attack_packets import receipt_runtime as receipt_runtime
from tests.srd_full.test_srd_spell_damage_consumers import _grant
from tests.srd_full.test_srd_spell_result_cards import SRD
from tests.srd_full.test_srd_spell_wave3_information import _case, _entity, _wall


def _sunburst_case():
    state, data, parameters = _case("sunburst")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False, active_actor=None)
    for actor in ("hero", "guard"):
        state[f"{SRD}.entities"][actor].update(
            hp=1000, max_hp=1000, temp_hp=1000, is_alive=True, creature_type="humanoid"
        )
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["constitution"] = -100
    parameters.pop("targets", None)
    parameters.update(sunburst_origin=[100, 0, 3], origin_distance=0)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["position"] = [159, 0, 0]
    scene["entities"][1]["footprint"]["half_extents"] = [2, 2]
    scene["entities"][2]["position"] = [100, 0, 3]
    scene["entities"][2]["movement"] = "passable"
    return state, data, parameters


def _add_actor(state, actor, position, plane="material"):
    state[f"{SRD}.entities"][actor] = deepcopy(state[f"{SRD}.entities"]["guard"])
    row = _entity(actor, position, participant_ref=actor, creature_type="humanoid", plane=plane)
    row["footprint"]["half_extents"] = [2, 2]
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(row)


def test_actual_sphere_selects_box_edge_height_and_plane_instead_of_centers():
    state, _, _ = _sunburst_case()
    _add_actor(state, "touch", [162, 0, 0])
    _add_actor(state, "outside", [162.01, 0, 0])
    _add_actor(state, "upper", [100, 0, 63])
    _add_actor(state, "above", [100, 0, 63.01])
    _add_actor(state, "other-plane", [100, 0, 0], plane="ethereal")
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity(
            "force-proxy", [100, 0, 0], participant_ref="not-an-actor", creature_type="spell_force"
        )
    )
    context = budget._run(
        "collect_srd_sunburst_creature_targets",
        state,
        caster_state="hero",
        sunburst_origin=[100, 0, 3],
    ).result["context"]
    assert context["_sunburst_sphere_targets"] == ["guard", "touch", "upper"]


def test_actual_sphere_rejects_ambiguous_body_even_when_second_proxy_is_outside():
    state, _, _ = _sunburst_case()
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity("other-guard-proxy", [200, 0, 0], participant_ref="guard", creature_type="humanoid")
    )
    with pytest.raises(RulesetPreconditionRejected, match="ambiguous scene proxies"):
        budget._run(
            "collect_srd_sunburst_creature_targets",
            state,
            caster_state="hero",
            sunburst_origin=[100, 0, 3],
        )


def test_actual_sphere_rejects_a_living_body_without_its_plane():
    state, _, _ = _sunburst_case()
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["metadata"].pop("plane")
    with pytest.raises(RulesetPreconditionRejected, match="admitted plane"):
        budget._run(
            "collect_srd_sunburst_creature_targets",
            state,
            caster_state="hero",
            sunburst_origin=[100, 0, 3],
        )


@pytest.mark.parametrize("unsupported", ["range", "plane", "solid", "body"])
def test_rejected_actual_source_point_or_body_leaves_paid_state_unchanged(monkeypatch, unsupported):
    import sylvia_gm.application.package_runtime as runtime

    reasons = []
    execute = runtime.execute_tool

    def observed(*args, **kwargs):
        result = execute(*args, **kwargs)
        if result.error:
            reasons.append(result.error)
        return result

    monkeypatch.setattr(runtime, "execute_tool", observed)
    state, data, parameters = _sunburst_case()
    if unsupported == "range":
        parameters["sunburst_origin"] = [151, 0, 3]
    elif unsupported == "plane":
        parameters["sunburst_plane"] = "ethereal"
    elif unsupported == "solid":
        state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [_wall(1)]
    else:
        state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["footprint"] = {
            "kind": "circle",
            "radius": 2,
        }
    before = deepcopy(state)
    with pytest.raises((PackageRuntimeError, RulesetPreconditionRejected)):
        budget._run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state == before
    expected = {
        "range": "beyond150",
        "plane": "actual plane",
        "solid": "solid barrier",
        "body": "creature box volumes",
    }[unsupported]
    assert any(expected in reason for reason in reasons)


class ActualSunburstFaces:
    def randint(self, low, high):
        assert low == 1
        return 10 if high == 20 else 1


def test_paid_sunburst_uses_derived_recipient_blinded_source_and_all_level_darkness(monkeypatch):
    state, data, parameters = _sunburst_case()
    monkeypatch.setattr(dice, "_rng", ActualSunburstFaces())
    parameters.update(sunburst_origin=[65, 0, 3], caster_can_see_origin=False)
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["position"] = [127, 0, 0]
    darkness = _grant(state, data, "darkness")
    darkness.update(slot_resource="spell_slot_9", illumination_position=[55, 0, 3])
    budget._run("cast_spell_outside_combat", state, participant_data=data, **darkness)
    assert any(
        record.get("spell") == "darkness" for record in state[f"{SRD}.state"]["turn_effects"]
    )
    light = budget._run(
        "resolve_srd_information_light", state, point=[65, 0, 3], plane="material", ambient="bright"
    ).result["context"]
    assert light["_information_light"] == "magical_darkness"
    result = budget._run(
        "cast_spell_outside_combat", state, participant_data=data, **parameters
    ).result
    assert state[f"{SRD}.entities"]["guard"]["temp_hp"] == 988
    assert state[f"{SRD}.entities"]["hero"]["temp_hp"] == 1000
    sources = state[f"{SRD}.state"]["timed_condition_sources"]
    installed = [
        r for r in sources.values() if r.get("installed") and r.get("spell_id") == "sunburst"
    ]
    assert len(installed) == 1
    assert installed[0]["condition"] == "blinded"
    assert installed[0]["save_ability"] == "constitution"
    assert installed[0]["dispel_ongoing"] is False
    assert "blinded" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert not any(
        record.get("spell") == "darkness" for record in state[f"{SRD}.state"]["turn_effects"]
    )
    assert result["result_card"]["details"]


def test_distant_point_cover_includes_the_casters_actual_body():
    state, _, _ = _sunburst_case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["position"] = [-9, 0, 0]
    with pytest.raises(RulesetPreconditionRejected, match="categorical Cover"):
        budget._run(
            "collect_srd_sunburst_cover_targets",
            state,
            source_state="hero",
            targets=["guard"],
            origin=[50, 0, 3],
        )
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["metadata"]["cover_from"] = {"hero": "half"}
    context = budget._run(
        "collect_srd_sunburst_cover_targets",
        state,
        source_state="hero",
        targets=["guard"],
        origin=[50, 0, 3],
    ).result["context"]
    assert context["_srd_area_targets"] == ["guard"]
    assert context["_srd_area_half_cover"] == ["guard"]
