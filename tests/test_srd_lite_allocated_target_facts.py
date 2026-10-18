"""Prove allocated combat recipients before the unchanged spell consequence owners."""

import os
import zipfile
from copy import deepcopy

import pytest
from scripts.srd_lite_allocated_target_facts import (
    FIELDS,
    SPELLS,
    _fact,
    _preflight,
    bind_allocated_target_facts,
)
from scripts.srd_lite_attacks import _effect_names
from scripts.srd_lite_profiles import lists
from scripts.srd_timed_actor_defenses import _load_runtime
from tests.test_srd_lite_instant_combat_areas import preflight
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _calls, _wall
from tests.test_srd_lite_multi_target_facts import scene

from sylvia_gm.runtime import RulesetRuntime


@pytest.fixture(scope="module")
def facts():
    with zipfile.ZipFile(os.environ["SRD_LITE_CANDIDATE"]) as archive:
        data = _load_runtime(
            archive.read("packages/" + NAMESPACE + "/runtime/ruleset.yaml").decode()
        )
    definitions = deepcopy(data["definitions"])
    owners = (
        "cast_spell",
        "begin_srd_spell_action",
        "cast_spell_automatic_force_projectiles",
        "cast_spell_multi_attack_damage",
        "resolve_spell_multi_attack_target",
    )
    original = {name: deepcopy(data["tools"][name]) for name in owners}
    assert bind_allocated_target_facts(data) == list(SPELLS)
    assert data["definitions"] == definitions
    prefix_length = len(FIELDS) + len(_preflight())
    mapping = {field: _fact(field) for field in FIELDS}
    pending = []
    for name in ("cast_spell", "begin_srd_spell_action"):
        owner = data["tools"][name]
        assert owner["parameters"] == original[name]["parameters"]
        assert owner.get("pre_conditions") == original[name].get("pre_conditions")
        assert owner["effects"][prefix_length:] == _effect_names(original[name]["effects"], mapping)
        tool = deepcopy(owner)
        tool["pre_conditions"] = []
        tool["effects"] = tool["effects"][:prefix_length]
        data["tools"]["preflight_" + name] = tool
        pending.append("preflight_" + name)
    for name in owners[2:4]:
        assert data["tools"][name] == original[name]
    leaf = deepcopy(original["resolve_spell_multi_attack_target"])
    branch = next(
        row
        for rows in lists(leaf["effects"])
        for row in rows
        if any(
            child.get("call") == "resolve_srd_lite_attack_facts" for child in row.get("on_true", [])
        )
    )
    branch["condition"] = branch["condition"].replace(
        "spell == 'eldritch_blast'", "(spell == 'eldritch_blast' or spell == 'scorching_ray')"
    )
    assert data["tools"]["resolve_spell_multi_attack_target"] == leaf
    closure = {}
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data["tools"][name]
            pending.extend(_calls(closure[name]["effects"]))
    subset = {key: value for key, value in data.items() if key not in ("tools", "procedures")}
    subset["tools"] = closure
    return RulesetRuntime(ruleset_dict=subset)


def check(parsed, value, spell="scorching_ray", *, root="cast_spell", **parameters):
    selected = {
        "spell": spell,
        "caster_state": "hero",
        "target": None,
        "attack_targets": ["guard", "other", "guard"],
        "targets_max_distance": 999,
        "all_attack_targets_have_clear_path": False,
        "all_targets_have_clear_path": False,
        "caster_can_see_all_targets": False,
        "half_cover_targets": ["other"],
        "caster_unseen_attack_targets": ["guard", "other"],
        "enemy_within_5_feet": True,
        "close_enemy_can_see_caster": False,
        "close_enemy_incapacitated": True,
    }
    if spell == "magic_missile":
        selected.update(
            target="guard",
            attack_targets=None,
            projectile_targets=["guard", "other", "guard"],
            target_distance=999,
            caster_can_see_target=False,
        )
    selected.update(parameters)
    return preflight(parsed, value, "preflight_" + root, selected)


@pytest.mark.parametrize("spell", SPELLS)
@pytest.mark.parametrize("root", ("cast_spell", "begin_srd_spell_action"))
def test_every_actual_allocation_qualifies_before_staging(facts, spell, root):
    result = check(facts, scene(first=(4.5, 0, 0), second=(119.75, 0, 0)), spell, root=root)
    assert result.success, result.error
    assert result.context[_fact("targets_max_distance")] == 119.75
    assert result.context[_fact("all_targets_have_clear_path")] is True
    assert result.context[_fact("all_attack_targets_have_clear_path")] is True
    assert result.context[_fact("caster_can_see_all_targets")] is True
    assert result.context[_fact("half_cover_targets")] == []
    if spell == "magic_missile":
        assert result.context[_fact("target_distance")] == 4.5
        assert result.context[_fact("caster_can_see_target")] is True
    else:
        assert result.context[_fact("target_distance")] is None
        assert result.context[_fact("caster_can_see_target")] is None
        assert result.context[_fact("attack_targets_within_5_feet")] == ["guard"]


@pytest.mark.parametrize("spell", SPELLS)
def test_last_far_recipient_and_closed_range_boundary(facts, spell):
    legal = check(facts, scene(second=(120, 0, 0)), spell)
    assert legal.success, legal.error
    rejected = check(
        facts,
        scene(second=(120.0001, 0, 0)),
        spell,
        targets_max_distance=1,
        all_targets_have_clear_path=True,
        all_attack_targets_have_clear_path=True,
    )
    assert not rejected.success and "actual combat recipient" in rejected.error


@pytest.mark.parametrize("spell", SPELLS)
def test_last_recipient_plane_and_total_cover_are_not_caller_facts(facts, spell):
    value = scene()
    value[NAMESPACE + ".state"]["spatial_scene"]["entities"][2]["metadata"]["plane"] = "ethereal"
    result = check(facts, value, spell)
    assert not result.success and "actual combat recipient" in result.error
    result = check(facts, scene(first=(2, 0, 0), geometry=[_wall()]), spell)
    assert not result.success and "actual combat recipient" in result.error


def test_sight_required_darts_and_unseen_rays_retain_their_rules(facts):
    value = scene(first=(10, 0, 0), second=(20, 0, 0), lighting="darkness")
    result = check(
        facts, value, "magic_missile", caster_can_see_target=True, caster_can_see_all_targets=True
    )
    assert not result.success and "cannot be seen" in result.error
    result = check(facts, value)
    assert result.success, result.error
    assert result.context[_fact("caster_unseen_attack_targets")] == ["guard", "other"]
    assert result.context[_fact("caster_can_see_all_targets")] is False


@pytest.mark.parametrize("spell", SPELLS)
def test_exploration_preserves_fictional_facts_without_any_scene(facts, spell):
    value = scene(combat=False)
    del value[NAMESPACE + ".state"]["spatial_scene"]
    result = check(
        facts, value, spell, targets_max_distance=119.75, all_attack_targets_have_clear_path=True
    )
    assert result.success, result.error
    assert result.context[_fact("targets_max_distance")] == 119.75
    assert result.context[_fact("caster_can_see_all_targets")] is False
    assert result.context[_fact("all_attack_targets_have_clear_path")] is True
    assert result.context[_fact("half_cover_targets")] == ["other"]


def test_magic_missile_primary_target_is_qualified_without_an_allocation_list(facts):
    result = check(facts, scene(first=(19.75, 0, 0)), "magic_missile", projectile_targets=None)
    assert result.success, result.error
    assert result.context[_fact("target_distance")] == 19.75
    assert result.context[_fact("targets_max_distance")] == 19.75


def test_empty_ray_allocation_fails_before_any_payment(facts):
    result = check(facts, scene(), attack_targets=[])
    assert not result.success and "must contain at least 1 items" in result.error


@pytest.mark.parametrize("cover", ("half", "three_quarters"))
def test_partial_cover_flags_preserve_recipient_identity_without_duplicates(facts, cover):
    geometry = [_wall(height=2)]
    if cover == "three_quarters":
        side = _wall()
        side["id"] = "side"
        side["volume"]["max"][1] = 0.05
        geometry.append(side)
    result = check(
        facts,
        scene(first=(10, 0, 0), second=(0, 10, 0), geometry=geometry),
        half_cover_targets=["other"],
        three_quarters_cover_targets=["other"],
    )
    assert result.success, result.error
    assert result.context[_fact(cover + "_cover_targets")] == ["guard"]
    opposite = "three_quarters" if cover == "half" else "half"
    assert result.context[_fact(opposite + "_cover_targets")] == []


def test_nearby_threat_and_reverse_visibility_use_actual_creature_facts(facts):
    value = scene(first=(10, 0, 0), second=(4.5, 0, 0))
    for body in value[NAMESPACE + ".state"]["spatial_scene"]["entities"][1:]:
        body["metadata"]["enemy_of"] = ["hero"]
    value[NAMESPACE + ".entities"]["guard"]["conditions"] = ["blinded"]
    result = check(facts, value, enemy_within_5_feet=False)
    assert result.success, result.error
    assert result.context[_fact("attack_targets_unable_to_see_caster")] == ["guard"]
    assert result.context[_fact("enemy_within_5_feet")] is True
    assert result.context[_fact("close_enemy_can_see_caster")] is True
    assert result.context[_fact("close_enemy_incapacitated")] is False
