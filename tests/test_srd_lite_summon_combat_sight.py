"""Exercise real summon placement sight without replaying paid casts or actor admission."""

import os
import zipfile
from copy import deepcopy

import pytest
from scripts.srd_lite_summon_combat_sight import (
    AMBIENT,
    CONDITION,
    OWNER,
    POINT_SIGHT,
    bind_summon_combat_sight,
)
from scripts.srd_timed_actor_defenses import _load_runtime, _set

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget
from tests.test_srd_lite_combat_target_facts import state
from tests.test_srd_lite_instant_combat_areas import preflight
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _calls, _scene_entity, _wall

PREFLIGHT = "preflight_summon_combat_sight"
INSTALL = "install_srd_illumination_source"


@pytest.fixture(scope="module")
def summons():
    with zipfile.ZipFile(os.environ["SRD_LITE_CANDIDATE"]) as archive:
        data = _load_runtime(
            archive.read("packages/" + NAMESPACE + "/runtime/ruleset.yaml").decode()
        )
    original = deepcopy(data["tools"][OWNER])
    profiles = deepcopy(data["definitions"]["spells"])
    headers = deepcopy(data["definitions"]["spell_casting"])
    sight_owner = deepcopy(data["tools"][POINT_SIGHT])
    assert bind_summon_combat_sight(data) == [OWNER]
    assert data["definitions"]["spells"] == profiles
    assert data["definitions"]["spell_casting"] == headers
    assert data["tools"][POINT_SIGHT] == sight_owner
    # The only changes are a private ambient fact and the existing sight handoff.
    restored = deepcopy(data["tools"][OWNER])
    ambient_copy = restored["effects"].pop(0)
    assert ambient_copy["on_true"][0]["set"]["field"] == AMBIENT
    assert ambient_copy["on_false"][0]["set"]["field"] == AMBIENT
    replacement = next(row for row in restored["effects"] if row.get("condition") == CONDITION)
    restored["effects"][restored["effects"].index(replacement)] = replacement["on_false"][0]
    body = next(
        row["set"]["value"]
        for row in restored["effects"]
        if row.get("set", {}).get("field") == "_created_actor_scene"
    )
    body["metadata"]["lighting"] = "created_actor_lighting"
    assert restored == original
    with pytest.raises(ValueError, match="already bound"):
        bind_summon_combat_sight(data)
    owner = deepcopy(data["tools"][OWNER])
    stop = next(
        index
        for index, row in enumerate(owner["effects"])
        if row.get("set", {}).get("field") == "_created_actor_prior_scene"
    )
    born_body = next(
        row["set"]["value"]
        for row in owner["effects"]
        if row.get("set", {}).get("field") == "_created_actor_scene"
    )
    # Keep the real range, plane, barriers, point sight and map bounds. Stop before
    # mutating the scene; native full-body placement/admission is retained evidence.
    owner["effects"] = owner["effects"][:stop] + [_set("_proof_birth_body", born_body)]
    data["tools"][PREFLIGHT] = owner
    closure, pending = {}, [PREFLIGHT, INSTALL]
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data["tools"][name]
            pending.extend(_calls(closure[name]["effects"]))
    subset = {key: value for key, value in data.items() if key not in ("tools", "procedures")}
    subset["tools"] = closure
    return RulesetRuntime(ruleset_dict=subset)


def scene(*, ambient="bright", combat=True, zone_plane="material", geometry=()):
    value = state((-70, 0, 0), combat=combat, geometry=geometry)
    value[NAMESPACE + ".state"]["spatial_scene"]["zones"] = [
        {
            "id": "ambient-room",
            "map_id": "mass-cure-fixture",
            "shape": {"kind": "box", "min": [-200, -200, -100], "max": [200, 200, 100]},
            "metadata": {"active": True, "plane": zone_plane, "lighting": ambient},
        }
    ]
    return value


def check(summons, value, *, position=(10, 0, 0), spell="summon_dragon", **facts):
    return preflight(
        summons,
        value,
        PREFLIGHT,
        {
            "caster_state": "hero",
            "summon_id": "new-dragon",
            "spell": spell,
            "range_feet": 90,
            "half_extent": 5,
            "creature_type": "dragon",
            "requires_sight": True,
            "created_actor_position": list(position),
            "created_actor_height": 6,
            "created_actor_lighting": "bright",
            **facts,
        },
    )


def install_light(summons, value):
    """Create a saved Light record with its native owner in public synthetic state."""
    body = _scene_entity("torch", [0, 10, 0])
    body["metadata"] = {"is_object": True, "plane": "material", "lighting": "darkness"}
    value[NAMESPACE + ".state"]["spatial_scene"]["entities"].append(body)
    bridge = AuthorityMemoryStateBridge(NAMESPACE, value)
    rng = dice._rng.getstate()
    result = execute_tool(
        INSTALL,
        {
            "caster_state": "hero",
            "spell": "light",
            "source": ["torch"],
            "origin": [0, 10, 0],
            "plane": "material",
            "slot_level": 0,
            "instance": 1,
            "color": "white",
        },
        summons.tools,
        bridge,
        summons.ruleset_config,
        summons.definitions,
        combat_state=value[NAMESPACE + ".combat"],
        budget=ExecutionBudget(),
    )
    assert result.success, result.error
    assert dice._rng.getstate() == rng
    assert bridge.mutations()
    for mutation in bridge.mutations():
        value[mutation.namespace + "." + mutation.key] = deepcopy(mutation.value)
    return value


@pytest.mark.parametrize("ambient", ["darkness", "magical_darkness"])
def test_actual_darkness_rejects_forged_bright_destination(summons, ambient):
    result = check(summons, scene(ambient=ambient))
    assert not result.success and "must see the created actor's space" in result.error


def test_actual_bright_destination_overrides_forged_darkness(summons):
    result = check(summons, scene(), created_actor_lighting="darkness")
    assert result.success, result.error
    assert result.context["_proof_birth_body"]["metadata"]["lighting"] == "bright"


def test_saved_light_illuminates_point_without_becoming_permanent_ambient(summons):
    value = install_light(summons, scene(ambient="darkness"))
    result = check(summons, value, created_actor_lighting="darkness")
    assert result.success, result.error
    assert result.context["_proof_birth_body"]["metadata"]["lighting"] == "darkness"
    source = value[NAMESPACE + ".state"]["turn_effects"][0]
    value[NAMESPACE + ".state"]["lasting_tick"] = source["expires_tick"]
    expired = check(summons, value)
    assert not expired.success and "must see the created actor's space" in expired.error


def test_real_light_plane_and_opaque_cover_control_visibility(summons):
    value = install_light(summons, scene(ambient="darkness"))
    torch = value[NAMESPACE + ".state"]["spatial_scene"]["entities"][-1]
    torch["metadata"]["plane"] = "ethereal"
    wrong_plane = check(summons, value)
    assert not wrong_plane.success and "must see the created actor's space" in wrong_plane.error
    torch["metadata"]["plane"] = "material"
    torch["metadata"]["opaque_cover"] = True
    covered = check(summons, value)
    assert not covered.success and "must see the created actor's space" in covered.error


def test_unadmitted_point_lighting_cannot_be_supplied_by_caller(summons):
    value = scene()
    value[NAMESPACE + ".state"]["spatial_scene"]["zones"] = []
    missing = check(summons, value)
    assert not missing.success and "ambient" in missing.error
    wrong_plane = check(summons, scene(zone_plane="ethereal"))
    assert not wrong_plane.success and "ambient" in wrong_plane.error


def test_existing_actual_range_and_solid_path_remain_authoritative(summons):
    within = check(summons, scene(), position=(89.75, 0, 0))
    assert within.success, within.error
    outside = check(summons, scene(), position=(90.001, 0, 0))
    assert not outside.success and "outside spell range" in outside.error
    blocked = check(summons, scene(geometry=[_wall()]))
    assert not blocked.success and "clear path to its space" in blocked.error


def test_actual_caster_senses_can_see_dark_destination(summons):
    value = scene(ambient="darkness")
    value[NAMESPACE + ".entities"]["hero"]["darkvision_feet"] = 30
    result = check(summons, value, created_actor_lighting="darkness")
    assert result.success, result.error


def test_find_steed_keeps_its_printed_absence_of_sight_requirement(summons):
    value = scene(ambient="darkness")
    value[NAMESPACE + ".state"]["spatial_scene"]["zones"] = []
    result = check(
        summons, value, spell="find_steed", requires_sight=False, created_actor_lighting="darkness"
    )
    assert result.success, result.error
    assert result.context["_proof_birth_body"]["metadata"]["lighting"] == "darkness"


def test_exploration_retains_original_supplied_lighting(summons):
    result = check(summons, scene(ambient="darkness", combat=False))
    assert result.success, result.error
    assert result.context["_proof_birth_body"]["metadata"]["lighting"] == "bright"
