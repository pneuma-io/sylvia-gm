"""Prove Misty Step's exploration authority and unchanged native payment/staging seams."""

import json
import os
import zipfile
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator
from scripts.srd_lite_mass_cure_geometry import _effect_lists
from scripts.srd_lite_misty_step_exploration import (
    ARRIVAL,
    CARD,
    COMBAT_VALIDATE,
    EXPLORE_CAST,
    EXPLORE_VALIDATE,
    FACTS,
    LOCATION_SCHEMA,
    MOUNT_FIELD,
    PAID,
    PARAMETER,
    RIDER_FIELD,
    ROWS,
    VALIDATE,
    bind_misty_step_exploration,
)
from scripts.srd_timed_actor_defenses import _load_runtime
from tests.srd_lite_instant_combat_areas_fixtures import scene
from tests.srd_lite_mass_cure_geometry_fixtures import NAMESPACE, _calls, _wall

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.domain.package_library import participant_schema_sha256
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget


def _without_new_facts(value):
    """Reconstruct the old transport exactly, keeping all old cost/roll/staging expressions."""
    if isinstance(value, list):
        return [
            _without_new_facts(row)
            for row in value
            if not (
                isinstance(row, dict)
                and (
                    row.get("name") == FACTS
                    or row.get("key") == repr(FACTS)
                    or row.get("set", {})
                    .get("field", "")
                    .endswith(".parameters." + FACTS)
                )
            )
        ]
    if isinstance(value, dict):
        return {
            key: _without_new_facts(child)
            for key, child in value.items()
            if key != FACTS
        }
    return value


def _subset(data, starts):
    closure, pending = {}, list(starts)
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data["tools"][name]
            pending.extend(_calls(closure[name]["effects"]))
    subset = {
        key: value for key, value in data.items() if key not in {"tools", "procedures"}
    }
    subset["tools"] = closure
    return RulesetRuntime(ruleset_dict=subset)


@pytest.fixture(scope="module")
def misty_record():
    with zipfile.ZipFile(os.environ["SRD_LITE_CANDIDATE"]) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    return next(record for record in manifest["packages"] if record["id"] == NAMESPACE)


@pytest.fixture(scope="module")
def misty(misty_record):
    with zipfile.ZipFile(os.environ["SRD_LITE_CANDIDATE"]) as archive:
        data = _load_runtime(
            archive.read("packages/" + NAMESPACE + "/runtime/ruleset.yaml").decode()
        )
    original = {
        "definitions": {
            key: deepcopy(data["definitions"][key])
            for key in ("spells", "spell_casting")
        },
        "tools": {
            name: deepcopy(data["tools"][name])
            for name in (
                PAID,
                CARD,
                VALIDATE,
                "cast_spell",
                "begin_srd_spell_action",
                "cast_spell_outside_combat",
                "complete_srd_pending_action",
            )
        },
    }
    original_artifacts = deepcopy(misty_record["participant_artifacts"])
    bind_misty_step_exploration(data, misty_record)
    for before, after in zip(
        original_artifacts, misty_record["participant_artifacts"], strict=True
    ):
        if before["kind"] != "player_character":
            assert before == after
            continue
        assert after["schema"] == before["schema"]
        assert (
            after["entity_state_schema"]["required"]
            == before["entity_state_schema"]["required"]
        )
        assert (
            after["initial_state_calculations"] == before["initial_state_calculations"]
        )
        assert after.get("initial_state_validation") == before.get(
            "initial_state_validation"
        )
        expected = deepcopy(before["entity_state_schema"])
        expected["properties"][ARRIVAL] = LOCATION_SCHEMA
        assert after["entity_state_schema"] == expected
        assert after["schema_sha256"] == participant_schema_sha256(
            after["schema"],
            entity_state_schema=expected,
            initial_state_calculations=after["initial_state_calculations"],
            initial_state_validation=after.get("initial_state_validation"),
        )
        assert after["schema_sha256"] != before["schema_sha256"]
    assert data["definitions"]["spells"] == original["definitions"]["spells"]
    assert (
        data["definitions"]["spell_casting"] == original["definitions"]["spell_casting"]
    )
    assert data["tools"][COMBAT_VALIDATE] == original["tools"][VALIDATE]
    assert data["tools"][PAID]["effects"][0]["condition"] == (
        "spell == 'misty_step' and combat.encounter_active != true"
    )
    assert (
        _without_new_facts(data["tools"][PAID]["effects"][0]["on_false"])
        == (original["tools"][PAID]["effects"])
    )
    assert (
        data["tools"][CARD]["effects"][0]["on_true"]
        == original["tools"][CARD]["effects"]
    )
    # Existing public effect order and paid/staged action owners stay intact.
    # Only the additional choice, pending parameter and preflight argument differ.
    for name in (
        "cast_spell",
        "begin_srd_spell_action",
        "cast_spell_outside_combat",
        "complete_srd_pending_action",
    ):
        assert (
            _without_new_facts(data["tools"][name]["effects"])
            == original["tools"][name]["effects"]
        )
    for root in ("cast_spell", "begin_srd_spell_action"):
        sites = [
            row
            for rows in _effect_lists(data["tools"][root]["effects"])
            for row in rows
            if row.get("call") == VALIDATE
        ]
        assert len(sites) == 1 and sites[0]["params"][FACTS] == "{" + FACTS + "}"
    # Exercise the actual saved-choice preflight branch using its native parameters,
    # excluding unrelated PC preparation checks already proved by the public owner.
    for root in ("cast_spell", "begin_srd_spell_action"):
        owner = data["tools"][root]
        branch = next(
            row
            for rows in _effect_lists(owner["effects"])
            for row in rows
            if any(child.get("call") == VALIDATE for child in row.get("on_true", []))
        )
        preview = deepcopy(owner)
        preview["pre_conditions"] = []
        preview["parameters"] = [
            p
            for p in preview["parameters"]
            if p["name"]
            in {"caster_state", "lasting_state", "spell", "travel_destination", FACTS}
        ]
        preview["effects"] = [
            {"set": {"field": "_ms", "value": "spell"}},
            {"set": {"field": "_instant_cast_targets", "value": []}},
            deepcopy(branch),
        ]
        data["tools"]["preflight_" + root] = preview
    starts = (
        EXPLORE_VALIDATE,
        EXPLORE_CAST,
        VALIDATE,
        CARD,
        "preflight_cast_spell",
        "preflight_begin_srd_spell_action",
    )
    return data, _subset(data, starts)


def state(*, combat=False, cached_scene=False):
    value = scene([10, 0, 0], hero=(0, 0, 0))
    value[NAMESPACE + ".combat"]["encounter_active"] = combat
    value[NAMESPACE + ".entities"]["hero"]["resources"] = {
        "spell_slot_2": 2,
        "spell_slot_4": 2,
    }
    if not cached_scene:
        value[NAMESPACE + ".state"].pop("spatial_scene")
    return value


def facts(**changes):
    return [
        {
            "place": "West balcony",
            "current_plane": "material",
            "plane": "material",
            "horizontal_distance_feet": 30,
            "elevation_change_feet": 0,
            "visible": True,
            "unoccupied": True,
            **changes,
        }
    ]


def run(parsed, value, tool, parameters, *, mutable=False):
    """Run actual owners with authoritative combat context and apply only bridge mutations."""
    before, rng = json.dumps(value, sort_keys=True), dice._rng.getstate()
    bridge = AuthorityMemoryStateBridge(NAMESPACE, value)

    def nested(name, values, parent):
        return execute_tool(
            name,
            values,
            parsed.tools,
            bridge,
            parsed.ruleset_config,
            parsed.definitions,
            combat_state=parent.combat_state,
            tool_executor=nested,
            budget=parent.budget,
        )

    result = execute_tool(
        tool,
        parameters,
        parsed.tools,
        bridge,
        parsed.ruleset_config,
        parsed.definitions,
        combat_state=value[NAMESPACE + ".combat"],
        tool_executor=nested,
        budget=ExecutionBudget(),
    )
    after = deepcopy(value)
    if result.success:
        for mutation in bridge.mutations():
            after[mutation.namespace + "." + mutation.key] = deepcopy(mutation.value)
    assert json.dumps(value, sort_keys=True) == before
    assert dice._rng.getstate() == rng
    if not mutable:
        assert not bridge.mutations()
        assert after == value
    return result, after






























def test_pooled_exploration_fact_descriptor_uses_supported_native_fields():
    from sylvia_gm.runtime._schema_validator import validate_parameter_declarations

    assert validate_parameter_declarations({"parameter_declarations": {"param_misty": PARAMETER}}) == []
