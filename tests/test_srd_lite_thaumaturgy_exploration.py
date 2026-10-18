"""Bounded actual package DSL checks for Thaumaturgy's exploration consequences."""

import importlib.util
import re
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from scripts import srd_lite_exploration_objects as objects
from scripts.srd_control_behaviors import _lasting
from scripts.srd_information_spells import _candidate_tools
from scripts.srd_lite_profiles import lists
from scripts.srd_spell_minor_world import (
    CAST_CHOICES,
    PROFILES,
    SOURCE_PAGES,
    _casting_tool,
    _door_tool,
    _fact_tools,
    _source_tools,
)
from scripts.srd_spell_object_content import _fact_tools as object_facts
from scripts.srd_timed_actor_defenses import _call, _entity, _parameter, _set, _tool
from scripts.srd_timed_senses import _optional

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._expression_parser import parse_expression
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget

try:
    from scripts import srd_lite_thaumaturgy_exploration as wonder
except ImportError:
    spec = importlib.util.spec_from_file_location(
        "scripts.srd_lite_thaumaturgy_exploration",
        Path(__file__).with_name("srd_lite_thaumaturgy_exploration.py"),
    )
    wonder = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = wonder
    spec.loader.exec_module(wonder)

NAMESPACE = "test.thaumaturgy.exploration"
ACCESS = "record_srd_object_access"
ACCESS_EXPLORE = "lite_exploration_" + ACCESS + "_node"


def _fixture():
    """Real authored owners; public delegates/payment marker are selected composition seams."""
    subject = _fact_tools()[wonder.SUBJECT]
    access = object_facts()[ACCESS]
    adapted = deepcopy(access)
    adapted["effects"] = objects._without_scene(adapted["effects"])
    tools = _source_tools() | {
        wonder.OWNER: _casting_tool(),
        "apply_srd_minor_world_door": _door_tool(),
        wonder.SUBJECT_NODE: subject,
        wonder.SUBJECT: _tool(
            "Selected genuine public subject delegation shape.",
            deepcopy(subject["parameters"]),
            [
                _call(
                    wonder.SUBJECT_NODE,
                    {
                        p["name"]: "{_" + p["name"] + "_id}"
                        if p.get("resolve") == "entity"
                        else "{" + p["name"] + "}"
                        for p in subject["parameters"]
                    },
                )
            ],
        ),
        ACCESS_EXPLORE: adapted,
        ACCESS: adapted,
        objects.LOAD: objects._load_tool(),
        objects.ADMIT: objects._admit_tool(),
        objects.RECONCILE: objects._reconcile_tool(),
        "format_srd_information_projection": _candidate_tools()[
            "format_srd_information_projection"
        ],
    }
    parameters = [
        _entity("caster_state"),
        _parameter("spell"),
        _optional("origin_distance", "number", None),
        _optional("origin_has_clear_path", "boolean", None),
        *deepcopy(CAST_CHOICES),
        _lasting(),
    ]
    delivery = _call(
        wonder.OWNER,
        {
            "caster_state": "{_caster_state_id}",
            "spell": "{spell}",
            "object_id": "{minor_object_id}",
            "origin_distance": "{origin_distance}",
            "origin_has_clear_path": "{origin_has_clear_path}",
            **{p["name"]: "{" + p["name"] + "}" for p in CAST_CHOICES},
        },
    )
    delivery["export_context"] = {
        "_spell_summary_rows": "_spell_summary_rows",
        "_spell_detail_rows": "_spell_detail_rows",
    }
    tools["cast_spell"] = _tool(
        "Selected real preflight/order seam; no imitation of slot/action rules.",
        deepcopy(parameters),
        [
            _set("_casting_profile", {}),
            _set("caster_state.test_paid_actions", "caster_state.test_paid_actions + 1"),
            delivery,
        ],
    )
    tools["cast_spell_outside_combat"] = _tool(
        "Selected public exploration delegate.",
        deepcopy(parameters),
        [
            _call(
                "cast_spell",
                {
                    p["name"]: "{_" + p["name"] + "_id}"
                    if p.get("resolve") == "entity"
                    else "{" + p["name"] + "}"
                    for p in parameters
                },
            )
        ],
    )
    tools["begin_srd_spell_action"] = _tool(
        "Selected staged capture seam.",
        deepcopy(parameters),
        [
            _set("_ms", "spell"),
            _set("_casting_profile", {}),
            _set(
                "lasting_state.pending_srd_actions.{_pending_action_id}.parameters.minor_mode",
                "minor_mode",
            ),
        ],
    )
    tools["complete_srd_pending_action"] = _tool(
        "Selected genuine saved pending completion handoff.",
        [],
        [_call("cast_spell", {"minor_mode": "{_pending_action.parameters.minor_mode}"})],
    )
    tools["finish_srd_retained_cast_tail"] = _tool(
        "Selected checked continuation capsule.",
        deepcopy(parameters),
        [
            _set("_retained_cast_tail_choices", [{"spell": "spell", "minor_mode": "minor_mode"}]),
            deepcopy(delivery),
        ],
    )
    return {"tools": tools, "definitions": {"spells": deepcopy(PROFILES)}}


@pytest.fixture(scope="module")
def bound():
    data = _fixture()
    wonder.bind_thaumaturgy_exploration(data)
    return data


def _state(*, scene=False):
    lasting = {"lasting_tick": 10, "turn_effects": []}
    if scene:
        lasting["spatial_scene"] = _scene()
    return {
        NAMESPACE + ".state": lasting,
        NAMESPACE + ".combat": {"encounter_active": False, "pending_action": None},
        NAMESPACE + ".entities": {
            "hero": {
                "name": "Caster",
                "hp": 10,
                "is_alive": True,
                "conditions": [],
                "test_paid_actions": 0,
            }
        },
    }


def _scene(*, body=None):
    return {
        "format": 1,
        "map": {
            "id": "wonder",
            "revision": 0,
            "bounds": {"min": [-1000, -1000, -100], "max": [1000, 1000, 100]},
            "grid": {"cell_size": 5, "unit": "ft", "origin": [-1000, -1000]},
            "layers": [{"id": "world", "min_elevation": -100, "max_elevation": 100}],
        },
        "entities": [] if body is None else [body],
        "geometry": [],
        "terrain": [],
        "zones": [],
    }


def _invoke(bound, value, name, params):
    """Run only the selected real owner closure; original combat arms are checked structurally."""
    closure, pending = {}, [name]
    while pending:
        tool = pending.pop()
        if tool in closure:
            continue
        closure[tool] = deepcopy(bound["tools"][tool])
        if tool == wonder.OWNER:
            closure[tool]["effects"][0]["on_false"] = [
                {"require": {"condition": "false", "error": "Use existing native combat proof."}}
            ]
        if tool == wonder.SUBJECT:
            closure[tool]["effects"][0]["on_false"] = []
        pending.extend(
            row["call"] for rows in lists(closure[tool]["effects"]) for row in rows if "call" in row
        )
    runtime = RulesetRuntime(ruleset_dict={"tools": closure, "definitions": bound["definitions"]})
    bridge = AuthorityMemoryStateBridge(NAMESPACE, value)
    before, rng = deepcopy(value), dice._rng.getstate()

    def nested(child, parameters, parent):
        return execute_tool(
            child,
            parameters,
            runtime.tools,
            bridge,
            runtime.ruleset_config,
            runtime.definitions,
            combat_state=parent.combat_state,
            tool_executor=nested,
            budget=parent.budget,
        )

    result = execute_tool(
        name,
        params,
        runtime.tools,
        bridge,
        runtime.ruleset_config,
        runtime.definitions,
        combat_state=bridge.combat_state,
        tool_executor=nested,
        budget=ExecutionBudget(),
    )
    assert value == before
    assert dice._rng.getstate() == rng
    assert "" not in bridge.combat_state
    result.context["_test_paid_actions_in_bridge"] = bridge.read_field("hero", "test_paid_actions")
    if result.success:
        for mutation in bridge.mutations():
            value[mutation.namespace + "." + mutation.key] = deepcopy(mutation.value)
        assert set(value[NAMESPACE + ".entities"]["hero"]) == set(
            before[NAMESPACE + ".entities"]["hero"]
        )
    return result


def _success(bound, value, name, params):
    result = _invoke(bound, value, name, params)
    assert result.success, result.error
    return result


def _admit(bound, value, identity="lamp", kind="torch", lit=True):
    _success(
        bound,
        value,
        objects.ADMIT,
        {
            "object_id": identity,
            "fiction": "The actual established " + kind,
            "extent": [2, 2, 2],
            "size_rank": 0,
        },
    )
    _success(
        bound,
        value,
        wonder.SUBJECT,
        {
            "object_id": identity,
            "kind": kind,
            "nonliving": True,
            "lit": lit,
            "exposed": True,
        },
    )


def _cast(bound, value, mode="fire_play", **facts):
    return _invoke(
        bound,
        value,
        "cast_spell",
        {
            "caster_state": "hero",
            "spell": "thaumaturgy",
            "minor_object_id": "lamp",
            "minor_mode": mode,
            "minor_operation": "color",
            "minor_color": "blue",
            "origin_distance": 30,
            "origin_has_clear_path": True,
            wonder.PLANE: True,
            **facts,
        },
    )


def _body(value, identity="lamp"):
    return value[NAMESPACE + ".state"][objects.POOL][identity]["body"]


@pytest.mark.parametrize("scene", [False, True])
def test_native_fire_source_feedback_cap_retirement_and_expiry(bound, scene):
    value = _state(scene=scene)
    _admit(bound, value)
    body, stale = deepcopy(_body(value)), deepcopy(value[NAMESPACE + ".state"].get("spatial_scene"))
    first = _cast(bound, value)
    assert first.success, first.error
    source = deepcopy(value[NAMESPACE + ".state"]["turn_effects"][0])
    assert source["source_sha256"] == SOURCE_PAGES[169]
    assert (source["spell"], source["object_id"], source["caster"]) == (
        "thaumaturgy",
        "lamp",
        "hero",
    )
    assert source["expires_tick"] == 30 and source["started_tick"] == 10
    assert source["origin"] is None and source["plane"] is None and source["geometry"] is None
    assert source["operation"] == "color" and source["color"] == "blue"
    assert _body(value) == body
    assert value[NAMESPACE + ".state"].get("spatial_scene") == stale
    assert {row["label"]: row["value"] for row in first.context["_spell_detail_rows"]}[
        "Object"
    ] == "lamp"
    for mode in ("altered_eyes", "booming_voice"):
        assert _cast(
            bound,
            value,
            mode,
            origin_distance=None,
            origin_has_clear_path=None,
            **{wonder.PLANE: False},
        ).success
    before = deepcopy(value)
    capped = _cast(bound, value, "tremors")
    assert not capped.success and "Only three" in capped.error
    assert value == before
    replacement = _cast(bound, value, "tremors", minor_retire_source_id=source["source_id"])
    assert replacement.success, replacement.error
    assert source["source_id"] not in {
        row["source_id"] for row in value[NAMESPACE + ".state"]["turn_effects"]
    }
    value[NAMESPACE + ".state"]["lasting_tick"] = 30
    _success(bound, value, "refresh_srd_minor_world_effects", {})
    assert value[NAMESPACE + ".state"]["turn_effects"] == []
    assert _body(value)["metadata"]["srd_ordinary_flame"]["burning"] is True


@pytest.mark.parametrize(
    "facts",
    [
        {"origin_distance": None},
        {"origin_distance": 30.01},
        {"origin_has_clear_path": False},
        {wonder.PLANE: False},
        {"minor_object_id": "unknown"},
        {"minor_operation": "snuff"},
    ],
)
def test_current_remote_facts_and_printed_operations_fail_before_payment(bound, facts):
    value = _state(scene=True)
    _admit(bound, value)
    before = deepcopy(value)
    result = _cast(bound, value, **facts)
    assert not result.success
    assert value == before and value[NAMESPACE + ".entities"]["hero"]["test_paid_actions"] == 0
    assert result.context["_test_paid_actions_in_bridge"] == 0


def test_fire_requires_actual_burning_flame_and_facts_cannot_be_replaced(bound):
    value = _state()
    _admit(bound, value, lit=False)
    result = _cast(bound, value)
    assert not result.success and "actual burning flame" in result.error
    assert result.context["_test_paid_actions_in_bridge"] == 0
    second = _invoke(
        bound,
        value,
        wonder.SUBJECT,
        {
            "object_id": "lamp",
            "kind": "torch",
            "nonliving": True,
            "lit": True,
        },
    )
    assert not second.success and "already admitted" in second.error
    assert _body(value)["metadata"]["srd_ordinary_flame"]["burning"] is False


@pytest.mark.parametrize("lock", ["mundane", "stuck", "barred", "arcane"])
def test_native_locks_reject_before_payment(bound, lock):
    value = _state()
    _admit(bound, value, identity="lamp", kind="door", lit=False)
    _success(
        bound,
        value,
        ACCESS,
        {
            "object_id": "lamp",
            "object_kind": "door",
            "closed": True,
            "locks": ["physical-lock"] if lock == "mundane" else [],
            "stuck": lock == "stuck",
            "barred": lock == "barred",
        },
    )
    if lock == "arcane":
        _body(value)["metadata"]["srd_arcane_locks"] = [
            {"suppressed_until_tick": None, "source_id": "actual-lock"}
        ]
    before = deepcopy(value)
    result = _cast(bound, value, "invisible_hand", minor_operation="open")
    assert not result.success
    assert value == before
    assert result.context["_test_paid_actions_in_bridge"] == 0


def test_native_door_consequence_reconciles_at_new_actual_combat_position(bound):
    value = _state(scene=True)
    _admit(bound, value, kind="window", lit=False)
    _success(
        bound,
        value,
        ACCESS,
        {
            "object_id": "lamp",
            "object_kind": "window",
            "closed": True,
            "locks": [],
            "stuck": False,
            "barred": False,
        },
    )
    _body(value)["metadata"]["srd_arcane_locks"] = [
        {"suppressed_until_tick": 12, "source_id": "actual-lock"}
    ]
    original_scene = deepcopy(value[NAMESPACE + ".state"]["spatial_scene"])
    opened = _cast(bound, value, "invisible_hand", minor_operation="open")
    assert opened.success, opened.error
    assert value[NAMESPACE + ".state"]["turn_effects"] == []  # Instantaneous actual access change.
    retained = _body(value)
    assert retained["metadata"]["srd_object_access"]["closed"] is False
    assert retained["movement"] == "passable" and retained["visibility"] == "clear"
    assert retained["metadata"]["srd_arcane_locks"][0]["source_id"] == "actual-lock"
    assert value[NAMESPACE + ".state"]["spatial_scene"] == original_scene
    admitted = deepcopy(retained)
    admitted.update(
        map_id="wonder",
        position=[90, 20, 0],
        facing=[1, 0],
        movement="blocking",
        visibility="opaque",
    )
    admitted["metadata"].update(plane="material", lighting="bright")
    admitted["metadata"]["srd_object_access"]["closed"] = True
    value[NAMESPACE + ".state"]["spatial_scene"] = _scene(body=admitted)
    _success(bound, value, objects.RECONCILE, {})
    actual = value[NAMESPACE + ".state"]["spatial_scene"]["entities"][0]
    assert actual["position"] == [90, 20, 0]
    assert actual["metadata"]["srd_object_access"]["closed"] is False
    assert actual["movement"] == "passable" and actual["visibility"] == "clear"
    assert actual["metadata"]["srd_arcane_locks"][0]["source_id"] == "actual-lock"
    value[NAMESPACE + ".state"]["lasting_tick"] = 12
    before = deepcopy(value)
    reactivated = _cast(bound, value, "invisible_hand", minor_operation="close")
    assert not reactivated.success and "Arcane Lock" in reactivated.error
    assert value == before


def test_combat_other_cantrips_and_public_contracts_are_preserved(bound):
    original = _fixture()
    assert (
        bound["tools"][wonder.OWNER]["effects"][0]["on_false"]
        == original["tools"][wonder.OWNER]["effects"]
    )
    assert bound["tools"][wonder.SUBJECT_NODE] == original["tools"][wonder.SUBJECT_NODE]
    assert bound["definitions"] == original["definitions"]
    for name in ("cast_spell", "begin_srd_spell_action"):
        rows = [row for group in lists(bound["tools"][name]["effects"]) for row in group]
        assert any(row.get("call") == wonder.QUALIFY for row in rows)
    for name in (
        "cast_spell",
        "cast_spell_outside_combat",
        "begin_srd_spell_action",
        "finish_srd_retained_cast_tail",
    ):
        assert (
            next(p for p in bound["tools"][name]["parameters"] if p["name"] == wonder.PLANE)[
                "default"
            ]
            is False
        )
    complete = bound["tools"]["complete_srd_pending_action"]["effects"][0]
    assert complete["params"][wonder.PLANE] == "{_pending_action.parameters.minor_same_plane}"
    staged = bound["tools"]["begin_srd_spell_action"]["effects"]
    assert any(
        row.get("set", {}).get("field", "").endswith(".parameters.minor_same_plane")
        for row in staged
    )
    capsule = bound["tools"]["finish_srd_retained_cast_tail"]["effects"][0]["set"]["value"][0]
    assert capsule[wonder.PLANE] == wonder.PLANE
    with pytest.raises(ValueError, match="already bound"):
        wonder.bind_thaumaturgy_exploration(deepcopy(bound))


def test_selected_new_expressions_and_loops_fit_actual_runtime(bound):
    names = (wonder.CAST, wonder.QUALIFY, wonder.DOOR, wonder.SUBJECT_EXPLORE)
    for name in names:
        for rows in lists(bound["tools"][name]["effects"]):
            for row in rows:
                if "for_each" in row:
                    assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", row["for_each"]["collection"])
                field = row.get("set", {}).get("field", "")
                assert not field or field.startswith(("_", "lasting_state.", "caster_state.")), (
                    field
                )
                expression = row.get("require", {}).get("condition", row.get("condition"))
                if expression:
                    assert len(expression) <= 500
                    parse_expression(expression)
                value = row.get("set", {}).get("value")
                if isinstance(value, str) and value:
                    assert len(value) <= 500
                    parse_expression(value)
    corrupted = _fixture()
    corrupted["tools"]["apply_srd_minor_world_door"]["effects"][-1]["spatial_mutation"]["patch"][
        "active"
    ] = False
    with pytest.raises(ValueError, match="native door access frame"):
        wonder.bind_thaumaturgy_exploration(corrupted)
