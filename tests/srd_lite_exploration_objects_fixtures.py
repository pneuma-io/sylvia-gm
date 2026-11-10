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
from tests.srd_lite_combat_target_facts_fixtures import state
from tests.srd_lite_mass_cure_geometry_fixtures import NAMESPACE, _calls, _scene_entity

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
