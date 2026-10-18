"""Check combat-only scene reads and actual cantrip hearing/cover at their owning boundary."""

import os
import zipfile
from copy import deepcopy

import pytest
from scripts.srd_lite_cantrip_combat_scope import COMBAT, LEAVES, ROOTS, bind_cantrip_combat_scope
from scripts.srd_lite_offensive_cantrips import OFFENSIVE
from scripts.srd_lite_profiles import lists
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
    definitions = deepcopy(data["definitions"])
    originals = {name: deepcopy(data["tools"][name]) for name in (*ROOTS, *LEAVES)}
    assert bind_cantrip_combat_scope(data) == [*ROOTS, *LEAVES]
    assert data["definitions"] == definitions
    for name, original in originals.items():
        assert data["tools"][name]["parameters"] == original["parameters"]
        assert data["tools"][name].get("pre_conditions", []) == original.get("pre_conditions", [])
        before = [
            row
            for rows in lists(original["effects"])
            for row in rows
            if "dice" in row or row.get("call") == "d20_test"
        ]
        after = [
            row
            for rows in lists(data["tools"][name]["effects"])
            for row in rows
            if "dice" in row or row.get("call") == "d20_test"
        ]
        assert before == after, "Keep original roll owners: " + name
        before_allocations = [
            row
            for rows in lists(original["effects"])
            for row in rows
            if "saved caster level's number of beams" in str(row.get("require", {}))
        ]
        after_allocations = [
            row
            for rows in lists(data["tools"][name]["effects"])
            for row in rows
            if "saved caster level's number of beams" in str(row.get("require", {}))
        ]
        assert before_allocations == after_allocations
    pending = []
    for name in ROOTS:
        tool = deepcopy(data["tools"][name])
        end = (
            max(
                index
                for index, row in enumerate(tool["effects"])
                if any(
                    child.get("require", {}).get("error")
                    in (
                        "Choose one creature target for this cantrip.",
                        "Allocate every Eldritch Blast beam before casting.",
                    )
                    for child in row.get("on_true", [])
                )
            )
            + 1
        )
        tool["effects"] = tool["effects"][:end]
        tool["pre_conditions"] = []
        data["tools"]["preflight_" + name] = tool
        pending.append("preflight_" + name)
    needed = {
        "spell",
        "caster_state",
        "target",
        "lasting_state",
        "target_distance",
        "caster_can_see_target",
        "target_can_see_caster",
        "enemy_within_5_feet",
        "close_enemy_can_see_caster",
        "close_enemy_incapacitated",
        "target_within_5_feet",
        "cover",
    }
    for name in LEAVES:
        owner = data["tools"][name]
        defaults = [
            deepcopy(row)
            for row in owner["effects"]
            if row.get("set", {}).get("field", "").startswith("_cantrip_")
            or (
                not row.get("condition", "").startswith(COMBAT)
                and any(
                    child.get("set", {}).get("field", "").startswith("_cantrip_")
                    for child in row.get("on_true", [])
                )
            )
        ]
        projection = next(
            row
            for row in owner["effects"]
            if row.get("condition", "").startswith(COMBAT)
            and any(
                child.get("call") == "resolve_srd_lite_cantrip_range"
                for child in row.get("on_true", [])
            )
        )
        cover_consumers = [
            deepcopy(row)
            for rows in lists(owner["effects"])
            for row in rows
            if row.get("set", {}).get("field") == "_hand_existing_cover"
        ]
        data["tools"]["preflight_" + name] = {
            "parameters": [p for p in owner["parameters"] if p["name"] in needed],
            "pre_conditions": [],
            "effects": [*defaults, deepcopy(projection), *cover_consumers],
        }
        pending.append("preflight_" + name)
    scalar = data["tools"]["cast_spell_attack_damage"]
    lighting = next(
        row
        for rows in lists(scalar["effects"])
        for row in rows
        if row.get("condition") == COMBAT
        and any(
            child.get("call") == "resolve_srd_illumination_target_sight"
            and child.get("export_context")
            == {"_invisibility_attack_target_can_see": "_illumination_target_can_see"}
            for child in row.get("on_true", [])
        )
    )
    from scripts.srd_lite_attacks import _copy_optional_fact

    data["tools"]["preflight_lighting"] = {
        "parameters": [
            p
            for p in scalar["parameters"]
            if p["name"] in {"caster_state", "target", "target_can_see_caster"}
        ],
        "effects": [
            _copy_optional_fact("_cantrip_fact_target_can_see_caster", "target_can_see_caster"),
            _copy_optional_fact(
                "_invisibility_attack_target_can_see", "_cantrip_fact_target_can_see_caster"
            ),
            deepcopy(lighting),
        ],
    }
    pending.append("preflight_lighting")
    closure = {}
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data["tools"][name]
            pending.extend(_calls(closure[name]["effects"]))
    subset = {key: value for key, value in data.items() if key not in ("tools", "procedures")}
    subset["tools"] = closure
    return RulesetRuntime(ruleset_dict=subset)


def scene(position=(10, 0, 0), *, combat=True, **options):
    value = state(position, combat=combat, **options)
    for name, actor in value[NAMESPACE + ".entities"].items():
        actor.update(
            creature_kind="character",
            melee_reach=5,
            speed=30,
            exhaustion_level=0,
            faction="heroes" if name == "hero" else "foes",
        )
    if not combat:
        del value[NAMESPACE + ".state"]["spatial_scene"]
    return value


@pytest.mark.parametrize("declared", (False, True))
def test_live_attack_lighting_keeps_exploration_fact_without_a_scene(facts, declared):
    result = preflight(
        facts,
        scene(combat=False),
        "preflight_lighting",
        {"caster_state": "hero", "target": "guard", "target_can_see_caster": declared},
    )
    assert result.success, result.error
    assert result.context["_invisibility_attack_target_can_see"] is declared


def test_live_attack_lighting_uses_actual_sight_in_combat(facts):
    result = preflight(
        facts,
        scene(),
        "preflight_lighting",
        {"caster_state": "hero", "target": "guard", "target_can_see_caster": False},
    )
    assert result.success, result.error
    assert result.context["_invisibility_attack_target_can_see"] is True


def check(parsed, value, *, owner="cast_spell", spell="fire_bolt", **parameters):
    return preflight(
        parsed,
        value,
        "preflight_" + owner,
        {
            "spell": spell,
            "caster_state": "hero",
            "target": "guard",
            "targets": [],
            "attack_targets": None,
            "object_target_id": "",
            "target_distance": 19.75,
            "caster_can_see_target": True,
            "caster_can_hear_target": True,
            "target_can_see_caster": False,
            "enemy_within_5_feet": False,
            "close_enemy_can_see_caster": False,
            "close_enemy_incapacitated": True,
            "target_within_5_feet": False,
            "cover": "none",
            **parameters,
        },
    )


@pytest.mark.parametrize("owner", ROOTS)
@pytest.mark.parametrize("spell", sorted(OFFENSIVE))
def test_all_seven_exploration_preflights_preserve_fiction_without_a_map(facts, owner, spell):
    options = {"target": None, "attack_targets": ["guard"] * 4} if spell == "eldritch_blast" else {}
    result = check(facts, scene(combat=False), owner=owner, spell=spell, cover="half", **options)
    assert result.success, result.error
    assert result.context["_cantrip_fact_target_distance"] == 19.75
    assert result.context["_cantrip_fact_caster_can_see_target"] is True
    assert result.context["_coord_fact_cover"] == "half"


@pytest.mark.parametrize(
    ("owner", "spell"), list(zip(LEAVES, ("fire_bolt", "eldritch_blast"), strict=True))
)
def test_exploration_attack_projection_and_cover_keep_fiction_without_a_map(facts, owner, spell):
    result = check(facts, scene(combat=False), owner=owner, spell=spell, cover="three_quarters")
    assert result.success, result.error
    # The beam receiver's original contract carries proximity flags rather than scalar feet.
    expected_distance = None if owner == "resolve_spell_multi_attack_target" else 19.75
    assert result.context["_cantrip_fact_target_distance"] == expected_distance
    assert result.context["_cantrip_fact_target_can_see_caster"] is False
    assert result.context["_cantrip_combat_cover"] == "three_quarters"
    assert result.context["_hand_existing_cover"] == 5


@pytest.mark.parametrize("owner", ROOTS)
@pytest.mark.parametrize(
    ("spell", "limit"), [("fire_bolt", 120), ("poison_spray", 30), ("shocking_grasp", 5)]
)
def test_combat_still_requires_actual_closed_range_before_payment(facts, owner, spell, limit):
    legal = check(facts, scene((limit, 0, 0)), owner=owner, spell=spell, target_distance=0)
    assert legal.success, legal.error
    assert legal.context["_cantrip_fact_target_distance"] == limit
    illegal = check(
        facts, scene((limit + 0.0001, 0, 0)), owner=owner, spell=spell, target_distance=0
    )
    assert not illegal.success
    assert "beyond range or lacks a clear path" in illegal.error


@pytest.mark.parametrize("owner", ROOTS)
def test_actual_hearing_cannot_be_forged_for_vicious_mockery(facts, owner):
    value = scene()
    value[NAMESPACE + ".entities"]["hero"]["conditions"] = ["blinded", "deafened"]
    result = check(facts, value, owner=owner, spell="vicious_mockery")
    assert not result.success
    assert "actual sight or hearing" in result.error


@pytest.mark.parametrize("owner", ROOTS)
def test_required_sight_remains_actual_in_combat(facts, owner):
    result = check(facts, scene(lighting="darkness"), owner=owner, spell="sacred_flame")
    assert not result.success
    assert "requires actual sight" in result.error


@pytest.mark.parametrize(
    ("owner", "spell"), list(zip(LEAVES, ("fire_bolt", "eldritch_blast"), strict=True))
)
@pytest.mark.parametrize(
    ("geometry", "expected", "bonus"), [([_wall(height=2)], "half", 2), ([], "none", 0)]
)
def test_actual_cover_reaches_the_existing_attack_bonus_consumer(
    facts, owner, spell, geometry, expected, bonus
):
    result = check(
        facts, scene(geometry=geometry), owner=owner, spell=spell, cover="three_quarters"
    )
    assert result.success, result.error
    assert result.context["_cantrip_combat_cover"] == expected
    assert result.context["_hand_existing_cover"] == bonus


@pytest.mark.parametrize("owner", ROOTS)
def test_actual_cover_overrides_the_public_cantrip_cast_input(facts, owner):
    result = check(facts, scene(geometry=[_wall(height=2)]), owner=owner, cover="none")
    assert result.success, result.error
    assert result.context["_coord_fact_cover"] == "half"


@pytest.mark.parametrize("owner", ROOTS)
def test_combat_beam_allocation_checks_the_last_target(facts, owner):
    result = check(
        facts,
        scene((120.001, 0, 0)),
        owner=owner,
        spell="eldritch_blast",
        target=None,
        attack_targets=["hero", "guard"],
    )
    assert not result.success
    assert "beyond range or lacks a clear path" in result.error


def test_later_dead_beam_recipient_skips_geometry_without_an_attack(facts):
    value = scene(combat=False)
    value[NAMESPACE + ".combat"]["encounter_active"] = True
    value[NAMESPACE + ".entities"]["guard"]["is_alive"] = False
    result = check(facts, value, owner="resolve_spell_multi_attack_target", spell="eldritch_blast")
    assert result.success, result.error
    assert result.context["_cantrip_combat_cover"] == "none"


def test_exploration_does_not_fake_unsupported_object_attacks(facts):
    result = check(facts, scene(combat=False), object_target_id="door")
    assert not result.success
    assert "does not resolve object" in result.error
