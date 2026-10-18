"""Exercise native Light/Mending object owners without exploration map movement."""

import json
import os
import re
import zipfile
from copy import deepcopy

import pytest
from scripts.srd_lite_exploration_objects import (
    ADMIT,
    CAPTURE,
    FACT_NODES,
    FACTS,
    INDEX,
    LIGHT,
    LOAD,
    MENDING,
    POOL,
    QUALIFY,
    RECONCILE,
    ROOTS,
    bind_exploration_objects,
)
from scripts.srd_lite_profiles import lists
from scripts.srd_timed_actor_defenses import _load_runtime
from tests.test_srd_lite_combat_target_facts import state
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _calls, _scene_entity

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget

CHECK = "check_srd_illumination_record"
REFRESH = "refresh_srd_illumination_spells"
INFORMATION_LIGHT = "resolve_srd_information_light"
OVERLAPS = "resolve_srd_illumination_overlaps"


def check_expression_limits(value):
    """Check the finite new expressions against the existing evaluator limits."""
    if isinstance(value, list):
        for child in value:
            check_expression_limits(child)
    elif isinstance(value, dict):
        for key, child in value.items():
            if key == "condition" or key == "value":
                expressions = child if isinstance(child, str) else []
                if expressions:
                    assert len(expressions) <= 500, expressions
                    for literal in re.findall(r"'([^']*)'", expressions):
                        assert len(literal) <= 200, literal
            if key == "for_each":
                assert "." not in child["collection"], child["collection"]
            check_expression_limits(child)


@pytest.fixture(scope="module")
def objects():
    with zipfile.ZipFile(
        os.environ.get("SRD_LITE_EXPLORATION_BASE", os.environ["SRD_LITE_CANDIDATE"])
    ) as archive:
        data = _load_runtime(
            archive.read("packages/" + NAMESPACE + "/runtime/ruleset.yaml").decode()
        )
    profiles = deepcopy(data["definitions"]["spells"])
    headers = deepcopy(data["definitions"]["spell_casting"])
    originals = {name: deepcopy(data["tools"][name]) for name in (LIGHT, MENDING, *FACTS)}
    native_fact_nodes = {name: deepcopy(data["tools"][name]) for name in FACT_NODES}
    exploration_delegate = deepcopy(data["tools"]["cast_spell_outside_combat"]["effects"])
    declarations = {"tools": []}
    assert ADMIT not in data["tools"], "Select an unbound package as SRD_LITE_EXPLORATION_BASE."
    changed = bind_exploration_objects(data, declarations)
    assert {ADMIT, LOAD, QUALIFY, CAPTURE, RECONCILE} <= set(changed)
    assert profiles == data["definitions"]["spells"]
    assert headers == data["definitions"]["spell_casting"]
    assert declarations["tools"][0]["name"] == ADMIT
    for name in changed:
        if name in ROOTS:
            continue
        effects = data["tools"][name]["effects"]
        check_expression_limits(
            effects[0]["on_true"] if name in (LIGHT, MENDING, *FACTS) else effects
        )
    for name, original in originals.items():
        assert data["tools"][name]["effects"][0]["on_false"] == original["effects"]
        assert data["tools"][name].get("outputs") == original.get("outputs")
        assert data["tools"][name].get("ui") == original.get("ui")
    for native_name, exploration_name in FACT_NODES.items():
        original = native_fact_nodes[native_name]
        assert data["tools"][native_name] == original
        adapted = data["tools"][exploration_name]
        assert adapted["parameters"] == original["parameters"]
        assert adapted["metadata"] == original["metadata"]
        assert LOAD in set(_calls(adapted["effects"]))
        assert "srd_information_scene_entity" not in set(_calls(adapted["effects"]))
        original_guards = {
            row["require"]["condition"]
            for group in lists(original["effects"])
            for row in group
            if "require" in row
        } - {
            "lasting_state.spatial_scene != null",
            "lasting_state.spatial_scene.map.grid.unit == 'ft'",
        }
        adapted_guards = {
            row["require"]["condition"]
            for group in lists(adapted["effects"])
            for row in group
            if "require" in row
        }
        assert original_guards <= adapted_guards
        public = data["tools"][native_name.removesuffix("_node")]
        assert exploration_name in set(_calls(public["effects"][0]["on_true"]))
        for group in lists(adapted["effects"]):
            assert not any("spatial_query" in row or "spatial_mutation" in row for row in group)
    break_parameter = next(
        p for p in data["tools"][QUALIFY]["parameters"] if p["name"] == "object_break_id"
    )
    assert break_parameter["required"] is False and break_parameter["default"] == ""
    for root in ROOTS:
        owner = data["tools"][root]
        original_delegates = [
            row
            for group in lists(exploration_delegate)
            for row in group
            if row.get("call") == "cast_spell"
        ]
        original_profiles = [
            row
            for group in lists(exploration_delegate)
            for row in group
            if row.get("set", {}).get("field") == "_casting_profile"
            or row.get("store_as") == "_casting_profile"
        ]
        if (
            root == "cast_spell_outside_combat"
            and len(original_delegates) == 1
            and not original_profiles
        ):
            assert owner["effects"] == exploration_delegate
            delegate = original_delegates[0]
            assert delegate["call"] == "cast_spell"
            for fact in ("target_distance", "caster_can_touch_target", "cover"):
                assert delegate["params"][fact] == "{" + fact + "}"
            continue
        profile_sites = 0
        choice_sites = 0
        for group in lists(owner["effects"]):
            for index, row in enumerate(group):
                if (
                    row.get("set", {}).get("field") == "_casting_profile"
                    or row.get("store_as") == "_casting_profile"
                ):
                    profile_sites += 1
                    validation = group[index + 1]
                    assert "combat.encounter_active != true" in validation["condition"]
                    assert QUALIFY in set(_calls(validation["on_true"]))
                if (
                    row.get("set", {}).get("field") == "_retained_cast_tail_choices"
                    and isinstance(row["set"]["value"], list)
                    and row["set"]["value"]
                    and isinstance(row["set"]["value"][0], dict)
                ):
                    choice_sites += 1
                    facts = group[index + 1]["on_true"][0]["set"]["value"]
                    assert set(facts) == {
                        "exploration_object_distance",
                        "exploration_object_touch",
                        "exploration_object_cover",
                    }
        assert profile_sites, root
        if root == "cast_spell":
            assert choice_sites, root
    deliveries = [
        row
        for tool in data["tools"].values()
        for group in lists(tool["effects"])
        for row in group
        if row.get("call") in (LIGHT, MENDING)
    ]
    assert len(deliveries) == 4
    projected = [row for row in deliveries if "object_distance" in row["params"]]
    assert len(projected) == 2
    for row in projected:
        assert (
            row["params"]["object_distance"]
            == "{_retained_cast_tail_choices.exploration_object_distance}"
        )
        assert (
            row["params"]["object_touch"]
            == "{_retained_cast_tail_choices.exploration_object_touch}"
        )
        assert (
            row["params"]["object_cover"]
            == "{_retained_cast_tail_choices.exploration_object_cover}"
        )
    with pytest.raises(ValueError, match="already bound"):
        bind_exploration_objects(data, declarations)
    closure, pending = (
        {},
        [
            ADMIT,
            LOAD,
            QUALIFY,
            LIGHT,
            MENDING,
            *FACTS,
            CAPTURE,
            RECONCILE,
            CHECK,
            REFRESH,
            INFORMATION_LIGHT,
            OVERLAPS,
        ],
    )
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data["tools"][name]
            pending.extend(_calls(closure[name]["effects"]))
    subset = {key: value for key, value in data.items() if key not in ("tools", "procedures")}
    subset["tools"] = closure
    return RulesetRuntime(ruleset_dict=subset)


def fictional(*, scene=False):
    value = state(combat=False)
    value[NAMESPACE + ".combat"]["pending_action"] = None
    if not scene:
        del value[NAMESPACE + ".state"]["spatial_scene"]
    return value


def invoke(objects, value, name, parameters, *, allow_rng=False):
    """Execute actual owners and commit only successful synthetic public-state mutations."""
    before = json.dumps(value, sort_keys=True)
    rng = dice._rng.getstate()
    bridge = AuthorityMemoryStateBridge(NAMESPACE, value)

    def nested(tool, values, parent):
        return execute_tool(
            tool,
            values,
            objects.tools,
            bridge,
            objects.ruleset_config,
            objects.definitions,
            combat_state=parent.combat_state,
            tool_executor=nested,
            budget=parent.budget,
        )

    result = execute_tool(
        name,
        parameters,
        objects.tools,
        bridge,
        objects.ruleset_config,
        objects.definitions,
        combat_state=bridge.combat_state,
        tool_executor=nested,
        budget=ExecutionBudget(),
    )
    if not allow_rng:
        assert dice._rng.getstate() == rng
    assert json.dumps(value, sort_keys=True) == before
    if result.success:
        assert "" not in bridge.combat_state
        for mutation in bridge.mutations():
            value[mutation.namespace + "." + mutation.key] = deepcopy(mutation.value)
    return result


def require_success(objects, value, name, parameters):
    result = invoke(objects, value, name, parameters)
    assert result.success, result.error
    return result


def admit(objects, value, identity="cup", **facts):
    return require_success(
        objects,
        value,
        ADMIT,
        {
            "object_id": identity,
            "fiction": "A cracked wooden cup",
            "extent": [2, 2, 2],
            "size_rank": 0,
            "carried_by": "hero",
            **facts,
        },
    )


def record_break(objects, value, identity="cup", break_id="crack", **facts):
    return require_success(
        objects,
        value,
        FACTS[0],
        {
            "object_id": identity,
            "break_id": break_id,
            "break_min": [-0.25, -0.1, 0],
            "break_max": [0.25, 0.1, 0.5],
            **facts,
        },
    )


def qualify(objects, value, spell="mending", **facts):
    return invoke(
        objects,
        value,
        QUALIFY,
        {
            "caster_state": "hero",
            "object_id": "cup",
            "spell": spell,
            "object_break_id": "crack" if spell == "mending" else "",
            "object_distance": 4.75,
            "object_touch": True,
            "object_cover": "none",
            **facts,
        },
    )


def cast(objects, value, spell="mending", **facts):
    parameters = {
        "caster_state": "hero",
        "spell": spell,
        "slot_level": 0,
        "object_distance": 4.75,
        "object_touch": True,
        "object_cover": "none",
        **(
            {"object_id": "cup", "object_break_id": "crack"}
            if spell == "mending"
            else {
                "illumination_object": "cup",
                "illumination_color": "blue",
                "concentration_modifier": 0,
                "casting_ability": "wisdom",
                "save_dc": 10,
                "caster_can_touch_target": True,
            }
        ),
        **facts,
    }
    return invoke(objects, value, MENDING if spell == "mending" else LIGHT, parameters)


def body(value, identity="cup"):
    return value[NAMESPACE + ".state"][POOL][identity]["body"]


def test_scene_free_native_facts_repair_one_break_and_preserve_other_metadata(objects):
    value = fictional()
    admit(objects, value)
    record_break(objects, value)
    record_break(
        objects,
        value,
        break_id="second",
        break_min=[-0.9, -0.9, 0],
        break_max=[0.9, -0.9, 0],
    )
    require_success(
        objects,
        value,
        FACTS[1],
        {
            "object_id": "cup",
            "material": "vegetable",
            "raw_stock": False,
            "magical": True,
        },
    )
    require_success(
        objects,
        value,
        FACTS[2],
        {
            "object_id": "cup",
            "capacity_gallons": 0.5,
            "water_gallons": 0.25,
            "is_open": True,
            "food_capacity_pounds": 1,
            "food_pounds": 0.5,
        },
    )
    before = deepcopy(body(value))
    result = cast(objects, value)
    assert result.success, result.error
    after = body(value)
    assert [row["id"] for row in after["metadata"]["srd_object_breaks"]] == ["second"]
    for key in (
        "carried_by",
        "srd_object_material",
        "srd_object_container",
        "size_rank",
    ):
        assert after["metadata"][key] == before["metadata"][key]
    assert "srd_spell_object" not in after["metadata"]
    assert "spatial_scene" not in value[NAMESPACE + ".state"]
    assert (
        result.context["_spell_detail_rows"][-1]["value"]
        == "Existing magic and numerical HP are unchanged."
    )
    assert not cast(objects, value).success
    large = cast(objects, value, object_break_id="second")
    assert not large.success and "one foot" in large.error


@pytest.mark.parametrize(
    "facts",
    [
        {"object_distance": 5.001},
        {"object_touch": False},
        {"object_cover": "total"},
    ],
)
@pytest.mark.parametrize("spell", ["light", "mending"])
def test_contact_failure_leaves_native_spells_and_randomness_unchanged(objects, facts, spell):
    value = fictional()
    admit(objects, value)
    record_break(objects, value)
    before = deepcopy(value)
    result = cast(objects, value, spell, **facts)
    assert not result.success and "contact" in result.error
    assert value == before


def test_native_light_size_and_other_creature_holder_restrictions_survive(objects):
    value = fictional()
    admit(objects, value, size_rank=4)
    large = qualify(objects, value, "light")
    assert not large.success and "Large or smaller" in large.error
    value = fictional()
    admit(objects, value, carried_by="guard")
    held = qualify(objects, value, "light")
    assert not held.success and "someone else" in held.error
    value[NAMESPACE + ".entities"]["hero"]["melee_reach"] = 10
    admitted = qualify(objects, value, "mending", object_distance=9.75)
    assert not admitted.success and "physical break" in admitted.error


def test_actual_native_light_instance_cover_recast_and_expiry_without_scene(objects):
    value = fictional()
    admit(objects, value)
    first = cast(objects, value, "light")
    assert first.success, first.error
    source = deepcopy(value[NAMESPACE + ".state"]["turn_effects"][0])
    profile = objects.definitions["spells"]["light"]
    assert source["source"] == "cup" and source["caster"] == "hero"
    assert source["origin"] is None and source["plane"] is None
    assert source["bright_radius"] == profile["bright_radius"]
    assert source["dim_radius"] == profile["dim_radius"]
    assert source["expires_tick"] == profile["effect_rounds"] * 2
    assert source["color"] == "blue"
    require_success(objects, value, FACTS[3], {"object_id": "cup", "covered": True})
    covered = require_success(objects, value, CHECK, {"record": source, "caster_state": "hero"})
    assert covered.context["_illumination_covered"] is True
    assert covered.context["_illumination_origin"] is None
    admit(objects, value, "second-cup")
    other = cast(objects, value, "light", caster_state="guard", illumination_object="second-cup")
    # The approved Light adaptation rejects another creature's held object.
    assert not other.success and "someone else" in other.error
    admit(objects, value, "guard-cup", carried_by="guard")
    other = cast(objects, value, "light", caster_state="guard", illumination_object="guard-cup")
    assert other.success, other.error
    replacement = cast(objects, value, "light", illumination_object="second-cup")
    assert replacement.success, replacement.error
    rows = value[NAMESPACE + ".state"]["turn_effects"]
    assert {(row["caster"], row["source"]) for row in rows} == {
        ("hero", "second-cup"),
        ("guard", "guard-cup"),
    }
    assert source["instance"] not in {row["instance"] for row in rows}
    value[NAMESPACE + ".state"]["lasting_tick"] = max(row["expires_tick"] for row in rows)
    require_success(objects, value, REFRESH, {})
    assert not [
        row
        for row in value[NAMESPACE + ".state"]["turn_effects"]
        if row["kind"] == "illumination_spell"
    ]


def test_new_fictional_object_can_keep_light_while_an_old_map_is_saved(objects):
    value = fictional(scene=True)
    admit(objects, value)
    lit = cast(objects, value, "light")
    assert lit.success, lit.error
    source = value[NAMESPACE + ".state"]["turn_effects"][0]
    current = require_success(objects, value, CHECK, {"record": source, "caster_state": "hero"})
    assert current.context["_illumination_active"] is True
    assert current.context["_illumination_origin"] is None
    assert current.context["_illumination_plane"] is None
    require_success(objects, value, REFRESH, {})
    assert value[NAMESPACE + ".state"]["turn_effects"][0]["source"] == "cup"


def scene_object(position=(100, 0, 0)):
    row = _scene_entity("cup", list(position), footprint={"kind": "box", "half_extents": [1, 1]})
    row.update(height=2, movement="blocking", visibility="opaque")
    row["metadata"] = {
        "is_object": True,
        "size_rank": 0,
        "carried_by": "hero",
        "worn_by": "",
        "plane": "material",
        "lighting": "darkness",
        "opaque_cover": False,
        "srd_spell_object": {
            "hp": 7,
            "max_hp": 10,
            "destroyed": False,
            "magical": True,
        },
        "srd_object_breaks": [
            {
                "id": "crack",
                "min": [position[0] - 0.25, position[1], position[2]],
                "max": [position[0] + 0.25, position[1], position[2] + 0.5],
            },
            {
                "id": "second",
                "min": [position[0] - 0.1, position[1], position[2]],
                "max": [position[0] + 0.1, position[1], position[2] + 0.2],
            },
        ],
        "srd_object_material": {
            "material": "vegetable",
            "materials": ["vegetable"],
            "magical": True,
            "raw_stock": False,
        },
        "srd_object_container": {
            "capacity_gallons": 0.5,
            "water_gallons": 0.25,
            "is_open": True,
            "food_capacity_pounds": 0,
            "food_pounds": 0,
        },
    }
    return row


def relocate(value, position=(20, 0, 0)):
    incoming = scene_object(position)
    incoming["metadata"].update(lighting="bright", carried_by="", srd_spell_object={"hp": 99})
    value[NAMESPACE + ".state"]["spatial_scene"]["entities"] = [incoming]
    return incoming


def test_capture_and_reconcile_preserve_repair_plus_later_hp_holder_and_native_light(
    objects,
):
    value = fictional(scene=True)
    old = scene_object()
    value[NAMESPACE + ".state"]["spatial_scene"]["entities"].append(old)
    # Stale exploration map distance is irrelevant; the supplied actual contact works.
    repaired = cast(objects, value)
    assert repaired.success, repaired.error
    lit = cast(objects, value, "light")
    assert lit.success, lit.error
    old = value[NAMESPACE + ".state"]["spatial_scene"]["entities"][-1]
    old["metadata"]["srd_spell_object"]["hp"] = 3
    old["metadata"]["carried_by"] = "guard"
    old["position"] = [110, 0, 0]
    require_success(objects, value, CAPTURE, {})
    relocate(value)
    require_success(objects, value, RECONCILE, {})
    saved = value[NAMESPACE + ".state"]["spatial_scene"]["entities"][0]
    assert saved["position"] == [20, 0, 0]
    assert saved["metadata"]["plane"] == "material" and saved["metadata"]["lighting"] == "bright"
    assert saved["metadata"]["carried_by"] == "guard"
    assert saved["metadata"]["srd_spell_object"]["hp"] == 3
    assert saved["metadata"]["srd_spell_object"]["magical"] is True
    assert [row["id"] for row in saved["metadata"]["srd_object_breaks"]] == ["second"]
    assert saved["metadata"]["srd_object_breaks"][0]["min"] == pytest.approx([19.9, 0, 0])
    assert saved["metadata"]["srd_object_container"]["water_gallons"] == 0.25
    assert value[NAMESPACE + ".state"][POOL]["cup"]["pending_patch"] == {}
    assert value[NAMESPACE + ".state"][POOL]["cup"]["pending_scene_reconciliation"] is False
    assert value[NAMESPACE + ".state"]["turn_effects"][0]["source"] == "cup"
    before = deepcopy(saved)
    require_success(objects, value, RECONCILE, {})
    assert value[NAMESPACE + ".state"]["spatial_scene"]["entities"][0] == before


@pytest.mark.parametrize("change", ["carried_missing", "creature", "dimensions"])
def test_combat_admission_rejects_lost_replaced_or_resized_objects(objects, change):
    value = fictional()
    admit(objects, value)
    template = fictional(scene=True)[NAMESPACE + ".state"]["spatial_scene"]
    value[NAMESPACE + ".state"]["spatial_scene"] = template
    incoming = relocate(value)
    if change == "carried_missing":
        template["entities"] = [_scene_entity("hero", [0, 0, 0])]
    elif change == "creature":
        incoming["metadata"]["participant_ref"] = "hero"
    else:
        incoming["footprint"]["half_extents"][0] = 2
    before = deepcopy(value)
    rejected = invoke(objects, value, RECONCILE, {})
    assert not rejected.success
    assert value == before


@pytest.mark.parametrize("cast_in_combat", [False, True])
def test_omitted_world_object_preserves_repair_hp_and_global_light_without_map_illumination(
    objects, cast_in_combat
):
    value = fictional(scene=True)
    source_body = scene_object((2, 0, 0))
    source_body["metadata"]["carried_by"] = ""
    value[NAMESPACE + ".state"]["spatial_scene"]["entities"].append(source_body)
    if not cast_in_combat:
        repaired = cast(objects, value)
        assert repaired.success, repaired.error
    value[NAMESPACE + ".combat"]["encounter_active"] = cast_in_combat
    lit = cast(objects, value, "light")
    assert lit.success, lit.error
    if cast_in_combat:
        assert POOL not in value[NAMESPACE + ".state"]
    global_light = deepcopy(value[NAMESPACE + ".state"]["turn_effects"][0])
    value[NAMESPACE + ".combat"]["encounter_active"] = False
    require_success(objects, value, CAPTURE, {})
    retained = deepcopy(value[NAMESPACE + ".state"][POOL]["cup"])
    assert retained["body"]["metadata"]["srd_spell_object"]["hp"] == 7
    assert [row["id"] for row in retained["body"]["metadata"]["srd_object_breaks"]] == (
        ["crack", "second"] if cast_in_combat else ["second"]
    )
    new_scene = fictional(scene=True)[NAMESPACE + ".state"]["spatial_scene"]
    new_scene["map"]["id"] = "unrelated-arena"
    for mapped in new_scene["entities"]:
        mapped["map_id"] = "unrelated-arena"
        mapped["metadata"]["lighting"] = "darkness"
    value[NAMESPACE + ".state"]["spatial_scene"] = new_scene
    require_success(objects, value, RECONCILE, {})
    assert value[NAMESPACE + ".state"][POOL]["cup"] == retained
    assert all(mapped["id"] != "cup" for mapped in new_scene["entities"])
    value[NAMESPACE + ".combat"]["encounter_active"] = True
    checked = require_success(
        objects, value, CHECK, {"record": global_light, "caster_state": "hero"}
    )
    assert checked.context["_illumination_active"] is True
    assert checked.context["_illumination_origin"] is None
    assert checked.context["_illumination_plane"] is None
    require_success(objects, value, REFRESH, {})
    assert value[NAMESPACE + ".state"]["turn_effects"] == [global_light]
    dark = require_success(
        objects,
        value,
        INFORMATION_LIGHT,
        {
            "point": [0, 0, 0],
            "plane": "material",
            "ambient": "darkness",
        },
    )
    assert dark.context["_information_light"] == "darkness"
    # The selected catalogue excludes Darkness. The actual ambient-darkness
    # query above proves that this retained Light does not illuminate an arena
    # that omits its source; casting another spell is outside this proof.
    assert global_light in value[NAMESPACE + ".state"]["turn_effects"]
    assert value[NAMESPACE + ".state"][POOL]["cup"] == retained


def test_reconciliation_does_not_resurrect_a_later_destroyed_scene_object(objects):
    value = fictional(scene=True)
    value[NAMESPACE + ".state"]["spatial_scene"]["entities"].append(scene_object())
    result = cast(objects, value)
    assert result.success, result.error
    old = value[NAMESPACE + ".state"]["spatial_scene"]["entities"][-1]
    old["active"] = False
    old["metadata"]["srd_spell_object"].update(hp=0, destroyed=True)
    require_success(objects, value, CAPTURE, {})
    relocate(value)
    require_success(objects, value, RECONCILE, {})
    saved = value[NAMESPACE + ".state"]["spatial_scene"]["entities"][0]
    assert saved["active"] is False
    assert saved["metadata"]["srd_spell_object"]["hp"] == 0
    assert saved["metadata"]["srd_spell_object"]["destroyed"] is True


def test_scene_free_identity_admission_cannot_overwrite_existing_facts(objects):
    value = fictional()
    admit(objects, value)
    before = deepcopy(value)
    duplicate = invoke(
        objects,
        value,
        ADMIT,
        {
            "object_id": "cup",
            "fiction": "Replacement",
            "extent": [1, 1, 1],
            "size_rank": 0,
        },
    )
    assert not duplicate.success and "cannot be replaced" in duplicate.error
    assert value == before
    assert value[NAMESPACE + ".state"][INDEX] == ["cup"]
