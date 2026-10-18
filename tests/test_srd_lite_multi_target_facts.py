"""Prove all chosen combat recipients qualify before staging or paying for a spell."""

import os
import zipfile
from copy import deepcopy

import pytest
from scripts.srd_lite_combat_target_facts import FIELDS, PROJECT
from scripts.srd_lite_multi_target_facts import PREFIX_LENGTH, bind_multi_target_facts
from scripts.srd_timed_actor_defenses import _load_runtime
from tests.test_srd_lite_combat_target_facts import state
from tests.test_srd_lite_instant_combat_areas import preflight
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _calls, _wall

from sylvia_gm.runtime import RulesetRuntime


@pytest.fixture(scope="module")
def facts():
    with zipfile.ZipFile(os.environ["SRD_LITE_CANDIDATE"]) as archive:
        data = _load_runtime(
            archive.read("packages/" + NAMESPACE + "/runtime/ruleset.yaml").decode()
        )
    profiles = deepcopy(data["definitions"]["spells"])
    headers = deepcopy(data["definitions"]["spell_casting"])
    paid = {
        name: deepcopy(tool)
        for name, tool in data["tools"].items()
        if name.startswith("cast_spell_")
    }
    selected = bind_multi_target_facts(data)
    assert len(profiles) == 96
    assert len(selected) == 34
    assert profiles == data["definitions"]["spells"]
    assert headers == data["definitions"]["spell_casting"]
    for name, original in paid.items():
        expected = deepcopy(original)
        expected["parameters"] = [
            {**parameter, "type": "number"}
            if parameter.get("name") == "targets_max_distance"
            and parameter.get("type") == "integer"
            else parameter
            for parameter in expected["parameters"]
        ]
        assert data["tools"][name] == expected, name
    pending = [PROJECT]
    for root in ("cast_spell", "begin_srd_spell_action"):
        tool = deepcopy(data["tools"][root])
        tool["effects"] = tool["effects"][: PREFIX_LENGTH + len(FIELDS) + 3]
        tool["pre_conditions"] = []
        data["tools"]["preflight_" + root] = tool
        pending.append("preflight_" + root)
    closure = {}
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data["tools"][name]
            pending.extend(_calls(closure[name]["effects"]))
    subset = {key: value for key, value in data.items() if key not in ("tools", "procedures")}
    subset["tools"] = closure
    return RulesetRuntime(ruleset_dict=subset)


def scene(first=(4.5, 0, 0), second=(29.5, 0, 0), **options):
    value = state(first, **options)
    entities = value[NAMESPACE + ".entities"]
    entities["other"] = deepcopy(entities["guard"])
    body = deepcopy(value[NAMESPACE + ".state"]["spatial_scene"]["entities"][1])
    body.update(id="other-body", position=list(second))
    body["metadata"]["participant_ref"] = "other"
    value[NAMESPACE + ".state"]["spatial_scene"]["entities"].append(body)
    return value


def check(parsed, value, spell="aid", *, root="preflight_cast_spell", **parameters):
    return preflight(
        parsed,
        value,
        root,
        {
            "spell": spell,
            "caster_state": "hero",
            "target": None,
            "targets": ["guard", "other"],
            "targets_max_distance": 0,
            "caster_can_see_all_targets": True,
            "caster_can_touch_all_targets": True,
            "all_targets_have_clear_path": True,
            "target_distance": 0,
            "cover": "none",
            **parameters,
        },
    )


@pytest.mark.parametrize(
    ("spell", "limit"), [("aid", 30), ("command", 60), ("hold_monster", 90), ("fly", 5)]
)
def test_every_actual_recipient_obeys_closed_range(facts, spell, limit):
    result = check(facts, scene(second=(limit, 0, 0)), spell)
    assert result.success, result.error
    assert result.context["_multi_fact_targets_max_distance"] == limit
    rejected = check(facts, scene(second=(limit + 0.0001, 0, 0)), spell)
    assert not rejected.success
    assert "actual combat recipient" in rejected.error


@pytest.mark.parametrize("root", ["preflight_cast_spell", "preflight_begin_srd_spell_action"])
def test_actual_fractional_facts_override_false_supplied_flags(facts, root):
    result = check(
        facts,
        scene(),
        root=root,
        targets_max_distance=999,
        caster_can_see_all_targets=False,
        caster_can_touch_all_targets=False,
        all_targets_have_clear_path=False,
    )
    assert result.success, result.error
    assert result.context["_multi_fact_targets_max_distance"] == 29.5
    assert result.context["_multi_fact_caster_can_see_all_targets"] is True
    assert result.context["_multi_fact_caster_can_touch_all_targets"] is False
    assert result.context["_multi_fact_all_targets_have_clear_path"] is True


def test_second_recipient_3d_distance_is_not_rounded(facts):
    result = check(facts, scene(second=(3, 4, 2)), "longstrider")
    assert not result.success
    assert "actual combat recipient" in result.error


@pytest.mark.parametrize("spell", ["hold_person", "mass_healing_word", "command"])
def test_required_sight_comes_from_each_actual_body(facts, spell):
    value = scene()
    value[NAMESPACE + ".state"]["spatial_scene"]["entities"][2]["metadata"]["lighting"] = "darkness"
    result = check(facts, value, spell)
    assert not result.success
    assert "cannot be seen" in result.error


def test_bless_does_not_invent_a_sight_requirement(facts):
    result = check(facts, scene(lighting="darkness"), "bless")
    assert result.success, result.error
    assert result.context["_multi_fact_caster_can_see_all_targets"] is False


def test_last_recipient_wrong_plane_is_not_hidden_by_good_first_recipient(facts):
    value = scene()
    value[NAMESPACE + ".state"]["spatial_scene"]["entities"][2]["metadata"]["plane"] = "ethereal"
    result = check(facts, value)
    assert not result.success
    assert "actual combat recipient" in result.error


def test_last_recipient_total_cover_rejected_before_payment(facts):
    result = check(facts, scene(first=(2, 0, 0), geometry=[_wall()]))
    assert not result.success
    assert "actual combat recipient" in result.error


def test_partial_cover_lists_come_from_actual_exposure(facts):
    result = check(
        facts,
        scene(first=(0, 5, 0), second=(10, 0, 0), geometry=[_wall(height=2)]),
        half_cover_targets=["guard"],
    )
    assert result.success, result.error
    assert result.context["_multi_fact_half_cover_targets"] == ["other"]
    assert result.context["_multi_fact_three_quarters_cover_targets"] == []


def test_single_target_alternative_for_a_multi_target_spell_is_qualified(facts):
    result = check(facts, scene(first=(30.01, 0, 0)), target="guard", targets=[])
    assert not result.success
    assert "actual combat recipient" in result.error


def test_scalar_target_cannot_hide_behind_a_different_legal_list(facts):
    result = check(
        facts, scene(first=(80, 0, 0), second=(4, 0, 0)), "heal", target="guard", targets=["other"]
    )
    assert not result.success
    assert "actual combat recipient" in result.error


def test_singleton_list_supplies_scalar_facts_when_no_scalar_id_is_given(facts):
    result = check(
        facts, scene(first=(4.5, 0, 0)), "cure_wounds", targets=["guard"], target_distance=999
    )
    assert result.success, result.error
    assert result.context["_coord_fact_target_distance"] == 4.5
    assert result.context["_coord_fact_caster_can_touch_target"] is True


def test_exploration_keeps_fiction_facts_without_positions(facts):
    value = scene(combat=False)
    del value[NAMESPACE + ".state"]["spatial_scene"]
    result = check(facts, value, targets_max_distance=25.75, all_targets_have_clear_path=False)
    assert result.success, result.error
    assert result.context["_multi_fact_targets_max_distance"] == 25.75
    assert result.context["_multi_fact_all_targets_have_clear_path"] is False


@pytest.mark.parametrize("spell", ["fireball", "mass_cure_wounds", "blur"])
def test_other_area_or_self_preflight_keeps_its_own_geometry(facts, spell):
    value = scene()
    del value[NAMESPACE + ".state"]["spatial_scene"]
    result = check(facts, value, spell)
    assert result.success, result.error
