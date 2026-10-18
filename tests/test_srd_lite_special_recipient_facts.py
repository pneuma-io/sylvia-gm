"""Check scaled stabilization and ordinary Dispel against actual combat bodies."""

import os
import zipfile
from copy import deepcopy

import pytest
from scripts.srd_lite_profiles import lists
from scripts.srd_lite_special_recipient_facts import MARKER, ROOTS, bind_special_recipient_facts
from scripts.srd_spell_removal_adoption import SELECTED_KINDS
from scripts.srd_timed_actor_defenses import _load_runtime, _set

from sylvia_gm.runtime import RulesetRuntime
from tests.test_srd_lite_combat_target_facts import state
from tests.test_srd_lite_instant_combat_areas import preflight
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _calls, _wall


@pytest.fixture(scope="module")
def special():
    with zipfile.ZipFile(os.environ["SRD_LITE_CANDIDATE"]) as archive:
        data = _load_runtime(
            archive.read("packages/" + NAMESPACE + "/runtime/ruleset.yaml").decode()
        )
    originals = {name: deepcopy(data["tools"][name]) for name in ROOTS}
    paid = {
        name: deepcopy(data["tools"][name])
        for name in ("cast_spell_instantaneous_stabilization", "cast_srd_recovery")
    }
    profiles, headers = (
        deepcopy(data["definitions"]["spells"]),
        deepcopy(data["definitions"]["spell_casting"]),
    )
    assert bind_special_recipient_facts(data) == list(ROOTS)
    assert profiles == data["definitions"]["spells"]
    assert headers == data["definitions"]["spell_casting"]
    for name, original in paid.items():
        assert data["tools"][name] == original
    pending = []
    for name in ROOTS:
        owner = data["tools"][name]
        restored = deepcopy(owner)
        inserted = []
        for effects in list(lists(restored["effects"])):
            for index in range(len(effects) - 1, -1, -1):
                row = effects[index]
                if (
                    row.get("condition") == "combat.encounter_active == true and "
                    "(_ms == 'spare_the_dying' or _ms == 'dispel_magic')"
                ):
                    assert effects[index - 1].get("set", {}).get("field") == "_casting_profile"
                    inserted.append(effects.pop(index))
        assert len(inserted) == 2 and inserted[0] == inserted[1]
        assert restored == originals[name]
        # This geometry slice reads an actual saved NPC casting profile. Full public
        # PC/profile admission stays with the representative paid integration proof.
        shim = deepcopy(owner)
        shim["pre_conditions"] = []
        shim["effects"] = [
            _set("_ms", "spell"),
            _set("_casting_profile", "caster_state.spellcasting_profile"),
            *[
                _set(field, "target_distance" if field.endswith("target_distance") else "cover")
                for field in inserted[0]["on_true"][1]["on_true"][-1]["export_context"]
            ],
            inserted[0],
        ]
        key = "preflight_special_" + name
        data["tools"][key] = shim
        pending.append(key)
    closure = {}
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data["tools"][name]
            pending.extend(_calls(closure[name]["effects"]))
    subset = {key: value for key, value in data.items() if key not in ("tools", "procedures")}
    subset["tools"] = closure
    parsed = RulesetRuntime(ruleset_dict=subset)
    with pytest.raises(ValueError, match="already bound"):
        bind_special_recipient_facts(data)
    return parsed


def check(
    special,
    spell,
    *,
    level=1,
    position=(10, 0, 0),
    root=ROOTS[0],
    combat=True,
    plane="material",
    lighting="bright",
    geometry=(),
    **parameters,
):
    value = state(position, combat=combat, plane=plane, lighting=lighting, geometry=geometry)
    value[NAMESPACE + ".entities"]["hero"]["spellcasting_profile"] = {"scaling_level": level}
    if not combat:
        del value[NAMESPACE + ".state"]["spatial_scene"]
    return preflight(
        special,
        value,
        "preflight_special_" + root,
        {
            "spell": spell,
            "caster_state": "hero",
            "target": "guard",
            "target_distance": 999,
            "cover": "total",
            "recovery_choice": "none",
            **parameters,
        },
    )


@pytest.mark.parametrize("root", ROOTS)
@pytest.mark.parametrize(("level", "limit"), [(1, 15), (5, 30), (11, 60), (17, 120)])
def test_stabilization_scales_from_saved_profile_before_payment(special, root, level, limit):
    legal = check(
        special, "spare_the_dying", root=root, level=level, position=(limit - 0.125, 0, 0)
    )
    assert legal.success, legal.error
    assert legal.context["_special_recipient_range"] == limit
    project = next(
        row
        for effects in lists(special.tools["preflight_special_" + root].effects)
        for row in effects
        if row.get("call") == "project_srd_lite_combat_recipient"
    )
    distance, cover = project["export_context"]
    assert legal.context[distance] == limit - 0.125
    assert legal.context[cover] == "none"
    illegal = check(
        special,
        "spare_the_dying",
        root=root,
        level=level,
        position=(limit + 0.001, 0, 0),
        target_distance=0,
        cover="none",
    )
    assert not illegal.success and "actual combat recipient" in illegal.error


@pytest.mark.parametrize("spell", ["spare_the_dying", "dispel_magic"])
@pytest.mark.parametrize("root", ROOTS)
def test_actual_plane_and_sealed_path_override_forged_facts(special, spell, root):
    cross_plane = check(
        special, spell, root=root, plane="ethereal", target_distance=0, cover="none"
    )
    assert not cross_plane.success and "actual combat recipient" in cross_plane.error
    sealed = check(special, spell, root=root, geometry=[_wall()], target_distance=0, cover="none")
    assert not sealed.success and "actual combat recipient" in sealed.error


@pytest.mark.parametrize("root", ROOTS)
def test_ordinary_dispel_uses_actual_range_without_a_sight_requirement(special, root):
    legal = check(
        special, "dispel_magic", root=root, position=(119.75, 0, 0), lighting="darkness"
    )
    assert legal.success, legal.error
    assert legal.context["_special_recipient_range"] == 120
    illegal = check(
        special,
        "dispel_magic",
        root=root,
        position=(120.001, 0, 0),
        target_distance=1,
        cover="none",
    )
    assert not illegal.success and "actual combat recipient" in illegal.error


@pytest.mark.parametrize(
    "choice",
    (
        *SELECTED_KINDS,
        "contingency",
        "earthquake",
        "illumination_object",
        "illumination_effect",
        "environment",
        "illusion",
        "minor_world",
    ),
)
def test_selected_source_dispel_stays_with_its_existing_geometry_owner(special, choice):
    result = check(special, "dispel_magic", recovery_choice=choice, target=None)
    assert result.success, result.error
    assert MARKER in result.context
    assert "_special_recipient_range" not in result.context


@pytest.mark.parametrize("spell", ["spare_the_dying", "dispel_magic"])
def test_exploration_keeps_fiction_range_without_scene(special, spell):
    result = check(special, spell, combat=False, target_distance=4.25, cover="half")
    assert result.success, result.error
    assert "_special_recipient_range" not in result.context


def test_stabilization_cannot_use_a_forged_level(special):
    result = check(special, "spare_the_dying", level=0)
    assert not result.success and "actual saved level" in result.error


def test_single_recipient_cannot_hide_an_extra_target(special):
    result = check(special, "spare_the_dying", targets=["guard", "hero"])
    assert not result.success and "one actual recipient" in result.error
