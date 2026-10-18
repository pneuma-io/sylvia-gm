"""Bounded genuine DSL/geometry checks; no full package load, campaign data or GUI."""

import ast
import importlib.util
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from scripts import srd_lite_exploration_objects as exploration_objects
from scripts.srd_lite_steed_cast_cards import OWNER as RECEIPT
from scripts.srd_lite_steed_cast_cards import _formatter
from scripts.srd_lite_steed_life_bond import _qualifier, _receiver, _wrap_restore
from scripts.srd_scene_relations import _scene_tools
from scripts.srd_spell_summoning import (
    _created_actor_placement_tools,
    _independent_actor_tools,
    _proxy_tools,
)
from scripts.srd_timed_actor_defenses import _call, _entity, _parameter, _set, _tool

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.application.result_cards import project_result_card
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget

try:
    from scripts import srd_lite_exploration_created_actors as birth
except ImportError:
    spec = importlib.util.spec_from_file_location(
        "scripts.srd_lite_exploration_created_actors",
        Path(__file__).with_name("srd_lite_exploration_created_actors.py"),
    )
    birth = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = birth
    spec.loader.exec_module(birth)

NAMESPACE = "test.exploration.created"


def _binding_fixture():
    """Real placement/lifecycle/receipt owners plus minimal public dispatch seam shapes."""
    tools = _created_actor_placement_tools() | _scene_tools() | _proxy_tools()
    tools.update(
        _independent_actor_tools(
            {"prepare_srd_creature": _tool("Unexecuted preparation seam.", [], [])}
        )
    )
    tools[birth.LIFE_BOND] = _qualifier()
    tools[RECEIPT] = _formatter()
    public = _tool(
        "Selected normal cast seam.",
        [],
        [
            _set("_retained_cast_tail_choices", [{"spell": "spell"}]),
            _call("finish_srd_retained_cast_tail", {}),
        ],
    )
    tools["cast_spell"] = public
    tools["cast_spell_outside_combat"] = _tool(
        "Selected exploration dispatcher.", [], [_call("cast_spell", {})]
    )
    tools["finish_srd_retained_cast_tail"] = _tool(
        "Selected genuine retained choice unwrap.",
        [],
        [
            _set("_retained_cast_tail_choices", "_retained_cast_tail_choices[0]"),
            _call(birth.STEED, {}),
            _call(birth.OWNER, {"spell": "summon_dragon"}),
            _call("install_srd_summoned_creature", {"scene_proxy_created": True}),
        ],
    )
    tools["begin_srd_spell_action"] = _tool(
        "Selected pending allocation seam.",
        [],
        [
            _set("lasting_state.pending_srd_actions.{_pending_action_id}.spell", "spell"),
        ],
    )
    tools["complete_srd_pending_action"] = _tool(
        "Selected pending dispatcher seam.",
        [],
        [
            _call("cast_spell", {"spell": "{_pending_action.parameters.spell}"}),
        ],
    )
    return {"tools": tools}


@pytest.fixture
def bound():
    data = _binding_fixture()
    birth.bind_exploration_created_actors(data)
    return data


def _scene(*, summon_position=(20, 0, 0), source="find_steed", metadata=None, footprint=None):
    def body(identity, position, half_extent, creature_type):
        return {
            "id": identity,
            "map_id": "birth-fixture",
            "position": list(position),
            "footprint": {"kind": "box", "half_extents": [half_extent, half_extent]},
            "height": 6,
            "facing": [1, 0],
            "active": True,
            "movement": "blocking",
            "visibility": "opaque",
            "metadata": {
                "participant_ref": identity,
                "creature_type": creature_type,
                "plane": "material",
                "lighting": "bright",
            },
        }

    hero, summon = (
        body("hero", [-20, 0, 0], 0.4, "humanoid"),
        body("paid-summon", summon_position, 5, "celestial"),
    )
    summon["metadata"].update({"source_ref": source, "source_caster": "hero"} | (metadata or {}))
    if footprint is not None:
        summon["footprint"] = footprint
    return {
        "format": 1,
        "map": {
            "id": "birth-fixture",
            "revision": 0,
            "bounds": {"min": [-100, -100, -100], "max": [100, 100, 100]},
            "grid": {"cell_size": 5, "unit": "ft", "origin": [-100, -100]},
            "layers": [{"id": "world", "min_elevation": -100, "max_elevation": 100}],
        },
        "entities": [hero, summon],
        "geometry": [],
        "terrain": [],
        "zones": [],
    }


def _bridge(*, scene=None, combat=False, born=False, dragon=False):
    hero = {
        "name": "Caster",
        "hp": 49,
        "max_hp": 50,
        "is_alive": True,
        "creature_type": "humanoid",
        "created_steed_id": "paid-summon" if not dragon else None,
        "resources": {"spell_slot_2": 1},
    }
    summon = {
        "name": "Otherworldly Steed",
        "hp": 5,
        "max_hp": 25,
        "is_alive": True,
        "creature_type": "celestial",
        "created_spell_id": "find_steed",
        "created_active": True,
        "created_caster_state_id": "hero",
        "inventory": [],
        "held_items": [],
        "created_steed_rider_id": None,
        "conditions": [],
        "native_stats_marker": [11, 4, 30],
        "resources": {"healing_touch": 1},
    }
    source = "summon_dragon" if dragon else "find_steed"
    if dragon:
        summon.update(
            created_active=False,
            summon_active=True,
            summon_spell_id=source,
            summon_caster_state_id="hero",
            summon_has_scene_proxy=False,
        )
        hero.update(
            concentration_spell_id=source,
            concentration_target_state_id="paid-summon",
            concentration_expires_tick=100,
        )
    lasting = {
        "spatial_scene": deepcopy(scene),
        "lasting_tick": 10,
        "created_actor_records": {
            "paid-summon": {"caster": "hero", "spell": "find_steed", "active": True}
        },
    }
    if born:
        lasting[birth.IDS] = ["paid-summon"]
        lasting[birth.RECORDS] = {
            "paid-summon": {
                "id": "paid-summon",
                "caster": "hero",
                "spell": source,
                "placement": "beside the established oak",
                "distance": 12,
                "half_extent": 5,
                "height": None,
                "body_admitted": False,
            }
        }
    state = {
        NAMESPACE + ".state": lasting,
        NAMESPACE + ".combat": {"encounter_active": combat},
        NAMESPACE + ".entities": {"hero": hero, "paid-summon": summon},
    }
    return AuthorityMemoryStateBridge(NAMESPACE, state)


def _run(data, bridge, name, params, selected_calls=None):
    """Parse only a bounded owning closure, then execute the real package DSL."""
    closure, pending = {}, [name]
    while pending:
        selected = pending.pop()
        if selected in closure:
            continue
        closure[selected] = deepcopy(data["tools"][selected])
        if selected == birth.OWNER:
            # This probe selects only the real exploration branch. Original combat source is
            # checked separately; its sight/barrier closure belongs to existing combat proofs.
            closure[selected]["effects"][0]["on_false"] = [
                {"require": {"condition": "false", "error": "Use existing native combat proof."}}
            ]
        pending.extend(
            row["call"]
            for rows in birth._lists(closure[selected]["effects"])
            for row in rows
            if "call" in row
        )
    parsed = RulesetRuntime(ruleset_dict={"tools": closure})

    def nested(child, parameters, parent):
        if selected_calls is not None:
            selected_calls.append(child)
        return execute_tool(
            child,
            parameters,
            parsed.tools,
            bridge,
            parsed.ruleset_config,
            parsed.definitions,
            combat_state=parent.combat_state,
            tool_executor=nested,
            budget=parent.budget,
        )

    return execute_tool(
        name,
        params,
        parsed.tools,
        bridge,
        parsed.ruleset_config,
        parsed.definitions,
        combat_state=bridge.combat_state,
        tool_executor=nested,
        budget=ExecutionBudget(),
    )


@pytest.mark.parametrize("spell,visible", [("find_steed", False), ("summon_dragon", True)])
def test_scene_free_birth_records_fiction_without_geometry_or_stat_changes(bound, spell, visible):
    bridge = _bridge()
    before = deepcopy(bridge.get_entity("paid-summon"))
    rng_before = dice._rng.getstate()
    result = _run(
        bound,
        bridge,
        birth.OWNER,
        {
            "caster_state": "hero",
            "summon_id": "new-paid-id",
            "spell": spell,
            "range_feet": 30,
            "half_extent": 5,
            "creature_type": "celestial",
            "created_actor_placement": "beside the established oak",
            "created_actor_distance": 12,
            "created_actor_space_unoccupied": True,
            "created_actor_space_visible": visible,
            "created_actor_clear_path": True,
        },
    )
    assert result.success, result.error
    assert result.context[birth.SCENE] is False
    state = bridge.get_entity("state")
    assert state["spatial_scene"] is None and state[birth.IDS] == ["new-paid-id"]
    record = state[birth.RECORDS]["new-paid-id"]
    assert record["placement"] == "beside the established oak" and record["body_admitted"] is False
    assert record["caster"] == "hero" and record["spell"] == spell and record["height"] is None
    assert "position" not in record and "plane" not in record
    assert bridge.get_entity("paid-summon") == before and dice._rng.getstate() == rng_before


@pytest.mark.parametrize(
    "changes,error",
    [
        ({"created_actor_distance": -1}, "established fictional"),
        ({"created_actor_distance": 31}, "outside spell range"),
        ({"created_actor_space_visible": False}, "must see"),
        ({"created_actor_space_unoccupied": False}, "unoccupied space"),
        ({"created_actor_clear_path": False}, "clear spell path"),
    ],
)
def test_scene_free_dragon_birth_fails_before_any_state_mutation(bound, changes, error):
    bridge = _bridge()
    params = {
        "caster_state": "hero",
        "summon_id": "new-paid-id",
        "spell": "summon_dragon",
        "range_feet": 30,
        "half_extent": 5,
        "creature_type": "celestial",
        "created_actor_placement": "beside the established oak",
        "created_actor_distance": 12,
        "created_actor_space_unoccupied": True,
        "created_actor_space_visible": True,
        "created_actor_clear_path": True,
        **changes,
    }
    before = deepcopy(bridge.get_entity("state"))
    result = _run(bound, bridge, birth.OWNER, params)
    assert not result.success and error in result.error
    assert bridge.get_entity("state") == before


@pytest.mark.parametrize("dragon", [False, True])
def test_actual_full_body_admission_preserves_paid_actor_and_links(bound, dragon):
    source = "summon_dragon" if dragon else "find_steed"
    bridge = _bridge(scene=_scene(source=source), born=True, dragon=dragon)
    actor_before, caster_before = (
        deepcopy(bridge.get_entity("paid-summon")),
        deepcopy(bridge.get_entity("hero")),
    )
    scene_before = deepcopy(bridge.get_entity("state")["spatial_scene"])
    combat_before = deepcopy(bridge.combat_state)
    result = _run(bound, bridge, birth.RECONCILE, {})
    assert result.success, result.error
    assert bridge.get_entity("state")[birth.RECORDS]["paid-summon"]["body_admitted"] is True
    after = bridge.get_entity("paid-summon")
    if dragon:
        assert after.pop("summon_has_scene_proxy") is True
        actor_before.pop("summon_has_scene_proxy")
    assert after == actor_before
    assert bridge.get_entity("hero") == caster_before
    assert bridge.get_entity("state")["spatial_scene"] == scene_before
    assert bridge.combat_state == combat_before and "" not in bridge.combat_state
    assert "summon_id" not in bridge.get_entity("state")
    assert "created_actor_position" not in bridge.get_entity("state")
    assert "summon_id" not in bridge.get_entity("hero")
    assert "created_actor_position" not in bridge.get_entity("paid-summon")


@pytest.mark.parametrize(
    "scene,error",
    [
        (_scene(metadata={"source_caster": "stranger"}), "exact live body"),
        (_scene(footprint={"kind": "box", "half_extents": [2.5, 2.5]}), "printed Large"),
        pytest.param(
            _scene(summon_position=(99, 0, 0)),
            "Entity volume is outside map bounds at $.entities[1]",
            id="scene2-full created body",
        ),
        (_scene(summon_position=(-20, 0, 0)), "unoccupied"),
    ],
)
def test_forged_wrong_size_outside_or_occupied_admission_fails_before_proxy_mark(
    bound, scene, error
):
    bridge = _bridge(scene=scene, born=True)
    actor_before = deepcopy(bridge.get_entity("paid-summon"))
    result = _run(bound, bridge, birth.RECONCILE, {})
    assert not result.success and error in result.error
    assert bridge.get_entity("paid-summon") == actor_before
    assert bridge.get_entity("state")[birth.RECORDS]["paid-summon"]["body_admitted"] is False


def test_combat_placement_branch_and_stat_installers_remain_original(bound):
    original = _binding_fixture()
    old_effects = original["tools"][birth.OWNER]["effects"]
    combat_effects = bound["tools"][birth.OWNER]["effects"][0]["on_false"]
    assert combat_effects[: len(old_effects)] == old_effects
    assert combat_effects[len(old_effects)]["set"] == {"field": birth.SCENE, "value": True}
    for name in ("prepare_srd_source_created_actor", "finish_srd_steed_source_actor"):
        assert bound["tools"][name] == original["tools"][name]
    params = next(
        row["params"]
        for rows in birth._lists(bound["tools"]["finish_srd_retained_cast_tail"]["effects"])
        for row in rows
        if row.get("call") == "install_srd_summoned_creature"
    )
    assert params == {"scene_proxy_created": "{" + birth.SCENE + "}"}
    effects = bound["tools"]["finish_srd_retained_cast_tail"]["effects"]
    assert effects[0]["set"]["value"] == "_retained_cast_tail_choices[0]"
    assert effects[1]["condition"].startswith("_retained_cast_tail_choices.created_actor_placement")
    assert all(
        "{_birth_choice_" in value
        for row in effects
        for key, value in row.get("params", {}).items()
        if key in {choice for choice, _, _, _ in birth.CHOICES}
    )


def test_reconciliation_writes_only_private_locals_or_explicit_owned_state(bound):
    for name in (birth.ONE, birth.RECONCILE, birth.PLACEMENT):
        for rows in birth._lists(bound["tools"][name]["effects"]):
            for row in rows:
                field = row.get("set", {}).get("field")
                if field is None:
                    continue
                assert field.startswith(("_", "lasting_state.", "actor_state.")), field
    placement = next(
        row
        for rows in birth._lists(bound["tools"][birth.ONE]["effects"])
        for row in rows
        if row.get("call") == birth.PLACEMENT
    )
    assert placement["params"] == {
        "summon_id": "{_actor_state_id}",
        "created_actor_position": "{_birth_body.position}",
        "created_actor_plane": "{_birth_body.metadata.plane}",
        "lasting_state": "{_lasting_state_id}",
    }


def _value_expressions(value):
    if isinstance(value, str):
        # Empty set values are real string literals under _resolve_value's fallback.
        if value.strip():
            yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _value_expressions(child)
    elif isinstance(value, list):
        for child in value:
            yield from _value_expressions(child)


def _owner_expressions(owner):
    for rows in birth._lists(owner.get("effects", [])):
        for row in rows:
            if "condition" in row:
                yield row["condition"]
            if "require" in row:
                yield row["require"]["condition"]
            if "set" in row:
                yield from _value_expressions(row["set"]["value"])


def test_new_emitted_expressions_fit_ast_and_runtime_limit_and_loops_use_locals(bound):
    """Check emitted expressions, including dynamic concatenations, against the real cap."""
    from sylvia_gm.runtime._expression_parser import MAX_EXPRESSION_LENGTH, parse_expression

    original = _binding_fixture()["tools"]
    source_ast = ast.parse(Path(birth.__file__).read_text(encoding="utf-8"))
    assert isinstance(source_ast, ast.Module) and MAX_EXPRESSION_LENGTH == 500
    checked = 0
    for name, owner in bound["tools"].items():
        old_expressions = set(_owner_expressions(original.get(name, {})))
        for expression in _owner_expressions(owner):
            if expression in old_expressions:
                continue
            assert len(expression) <= MAX_EXPRESSION_LENGTH, (name, len(expression), expression)
            ast.parse(expression, mode="eval")
            parse_expression(expression)
            checked += 1
        if owner == original.get(name):
            continue
        for rows in birth._lists(owner["effects"]):
            for row in rows:
                if "for_each" in row:
                    collection = row["for_each"]["collection"]
                    assert collection.startswith("_") and collection.isidentifier(), (
                        name,
                        collection,
                    )
    assert checked > 0
    for name in (birth.LIFE_BOND, RECEIPT):
        parameter = next(
            row for row in bound["tools"][name]["parameters"] if row["name"] == "lasting_state"
        )
        assert parameter == {
            "name": "lasting_state",
            "type": "string",
            "required": False,
            "resolve": "entity",
            "default": "state",
        }


def _healing_probe(bound):
    data = deepcopy(bound)
    # Exercise the unchanged native heal primitive, HP caps and original Life Bond wrapper.
    # Final package HP restrictions/receipts are separately the root's native acceptance.
    data["tools"]["restore_hit_points"] = _tool(
        "Owning core heal primitive probe.",
        [_entity("target"), _parameter("amount", "integer")],
        [{"heal": {"target": "target", "amount": "amount"}}],
    )
    data["tools"]["restore_srd_lite_steed_life_bond"] = _receiver()
    data["tools"]["record_srd_spell_target_result"] = _tool(
        "Transparent result formatting seam; no game mutation.",
        [_entity("target"), _parameter("brief"), _parameter("detail")],
        [
            _set("_spell_target_summary", {"label": "target.name", "value": "brief"}),
            _set("_spell_target_detail", {"label": "target.name", "value": "detail"}),
        ],
    )
    rows = [_call("restore_hit_points", {"target": "{_caster_state_id}", "amount": "{amount}"})]
    _wrap_restore(rows, 0, "1")
    data["tools"]["heal_probe"] = _tool(
        "One original healing application and Life Bond gain.",
        [_entity("caster_state"), _parameter("amount", "integer")],
        rows,
    )
    return data


@pytest.mark.parametrize(
    "distance,same_plane,steed_hp", [(5, True, 6), (5.01, True, 5), (0, False, 5)]
)
def test_current_single_use_life_bond_fact_uses_real_gain_without_coordinates_or_reroll(
    bound, distance, same_plane, steed_hp
):
    bridge, data = _bridge(born=True), _healing_probe(bound)
    pc_fields_before = set(bridge.get_entity("hero"))
    rng_before = dice._rng.getstate()
    fact = _run(
        data,
        bridge,
        birth.PROXIMITY,
        {
            "caster_state": "hero",
            "steed_state": "paid-summon",
            "distance_feet": distance,
            "same_plane": same_plane,
        },
    )
    assert fact.success, fact.error
    card = project_result_card(
        data["tools"][birth.PROXIMITY]["metadata"]["result_card"],
        parameters={"distance_feet": distance, "same_plane": same_plane},
        public_context=fact.context,
    )
    assert (
        card
        and next(row for row in card["summary"] if row["label"] == "Current separation (ft)")[
            "value"
        ]
        == distance
    )
    selected_calls = []
    result = _run(
        data, bridge, "heal_probe", {"caster_state": "hero", "amount": 12}, selected_calls
    )
    assert result.success, result.error
    assert (
        bridge.get_entity("hero")["hp"] == 50 and bridge.get_entity("paid-summon")["hp"] == steed_hp
    )
    assert bridge.get_entity("state")[birth.PROXIMITY_FIELD]["hero"] is None
    assert set(bridge.get_entity("hero")) == pc_fields_before
    assert bridge.get_entity("state")["spatial_scene"] is None
    assert bridge.get_entity("state")[birth.RECORDS]["paid-summon"]["distance"] == 12
    assert dice._rng.getstate() == rng_before
    assert selected_calls.count(birth.LIFE_BOND) == 1
    assert selected_calls.count("restore_srd_lite_steed_life_bond") == (1 if steed_hp > 5 else 0)
    repeated = _run(data, bridge, "heal_probe", {"caster_state": "hero", "amount": 12})
    assert not repeated.success and "current proximity" in repeated.error


@pytest.mark.parametrize(
    "fact",
    [
        None,
        {
            "caster": "foreign",
            "steed": "paid-summon",
            "tick": 10,
            "distance": 0,
            "same_plane": True,
        },
        {"caster": "hero", "steed": "foreign", "tick": 10, "distance": 0, "same_plane": True},
        {"caster": "hero", "steed": "paid-summon", "tick": 9, "distance": 0, "same_plane": True},
    ],
)
def test_missing_stale_or_mismatched_facts_reject_before_healing(bound, fact):
    bridge, data = _bridge(born=True), _healing_probe(bound)
    bridge.write_field("state", birth.PROXIMITY_FIELD + ".hero", fact)
    result = _run(data, bridge, "heal_probe", {"caster_state": "hero", "amount": 12})
    assert not result.success and "current proximity" in result.error
    assert bridge.get_entity("hero")["hp"] == 49 and bridge.get_entity("paid-summon")["hp"] == 5
    assert bridge.get_entity("hero")["resources"] == {"spell_slot_2": 1}


def test_combat_life_bond_ignores_fiction_fact_and_uses_original_body_distance(bound):
    bridge, data = (
        _bridge(scene=_scene(summon_position=(50, 0, 0)), combat=True),
        _healing_probe(bound),
    )
    bridge.write_field(
        "state",
        birth.PROXIMITY_FIELD + ".hero",
        {"caster": "hero", "steed": "paid-summon", "tick": 10, "distance": 0, "same_plane": True},
    )
    fact = _run(
        data,
        bridge,
        birth.PROXIMITY,
        {
            "caster_state": "hero",
            "steed_state": "paid-summon",
            "distance_feet": 0,
            "same_plane": True,
        },
    )
    assert not fact.success and "authoritative saved bodies" in fact.error
    result = _run(data, bridge, "heal_probe", {"caster_state": "hero", "amount": 12})
    assert result.success, result.error
    assert bridge.get_entity("hero")["hp"] == 50 and bridge.get_entity("paid-summon")["hp"] == 5
    assert bridge.get_entity("state")[birth.PROXIMITY_FIELD]["hero"] is None


def test_exploration_steed_receipt_and_retirement_preserve_native_lifecycle(
    bound,
):
    bridge = _bridge(born=True)
    result = _run(bound, bridge, RECEIPT, {"caster_state": "hero", "steed_state": "paid-summon"})
    assert result.success, result.error
    assert {row["label"]: row["value"] for row in result.context["_steed_cast_receipt_rows"]}[
        "Steed placement"
    ] == "beside the established oak"
    retired = _run(
        bound,
        bridge,
        "retire_srd_created_actor",
        {"actor_state": "paid-summon", "reason": "dismissed"},
    )
    assert retired.success, retired.error
    assert bridge.get_entity("hero")["created_steed_id"] is None
    assert bridge.get_entity("paid-summon")["created_active"] is False
    assert bridge.get_entity("state")["created_actor_records"]["paid-summon"]["active"] is False
    assert bridge.get_entity("state")["spatial_scene"] is None


def test_pending_fiction_steed_retirement_ignores_stale_admitted_coordinates(bound):
    bridge = _bridge(scene=_scene(), born=True)
    lasting = bridge.get_entity("state")
    lasting[birth.RECORDS]["paid-summon"]["body_admitted"] = True
    lasting["exploration_steed_placements"] = {
        "paid-summon": {"steed": "paid-summon", "caster": "hero", "pending": True}
    }
    bridge.write_field("state", birth.RECORDS, lasting[birth.RECORDS])
    bridge.write_field("state", "exploration_steed_placements", lasting["exploration_steed_placements"])
    scene_before = deepcopy(bridge.get_entity("state")["spatial_scene"])
    rng = dice._rng.getstate()
    retired = _run(
        bound, bridge, "retire_srd_created_actor", {"actor_state": "paid-summon", "reason": "dismissed"}
    )
    assert retired.success, retired.error
    assert bridge.get_entity("paid-summon")["created_active"] is False
    assert bridge.get_entity("hero")["created_steed_id"] is None
    assert bridge.get_entity("state")["spatial_scene"] == scene_before
    assert dice._rng.getstate() == rng


def test_source_drift_rejects_without_partial_binding_mutation():
    data = _binding_fixture()
    data["tools"][birth.LIFE_BOND]["effects"] = []
    before = deepcopy(data)
    with pytest.raises(ValueError, match="genuine Life Bond"):
        birth.bind_exploration_created_actors(data)
    assert data == before


@pytest.mark.parametrize("had_exploration_record", [False, True])
def test_absent_scene_retirement_and_return_use_actual_saved_source_without_coordinates(
    bound, had_exploration_record
):
    bridge = _bridge(born=had_exploration_record)
    if had_exploration_record:
        bridge.write_field("state", birth.RECORDS + ".paid-summon.body_admitted", True)
    retired = _run(
        bound,
        bridge,
        "retire_srd_created_actor",
        {"actor_state": "paid-summon", "reason": "dismissed"},
    )
    assert retired.success, retired.error
    returned = _run(
        bound,
        bridge,
        "remove_srd_inactive_created_scene",
        {"caster_state": "hero", "actor_id": "paid-summon", "spell": "find_steed"},
    )
    assert returned.success, returned.error
    assert bridge.get_entity("state")["spatial_scene"] is None


def test_unplaced_steed_with_carried_items_fails_before_any_lifecycle_change(bound):
    bridge = _bridge(born=True)
    bridge.write_field("paid-summon", "inventory", ["actual-carried-item"])
    before = bridge.get_entity("paid-summon")
    retired = _run(
        bound,
        bridge,
        "retire_srd_created_actor",
        {"actor_state": "paid-summon", "reason": "dismissed"},
    )
    assert not retired.success and "physical rider or item drops" in retired.error
    assert bridge.get_entity("paid-summon") == before


def test_public_fact_is_normally_declared_and_creation_intents_are_admitted():
    data = _binding_fixture()
    record = {
        "participant_artifacts": [
            {
                "entity_state_schema": {
                    "additionalProperties": False,
                    "properties": {"hp": {"type": "integer"}},
                },
                "schema_sha256": "existing-participant-schema-identity",
            }
        ],
        "tools": [
            {"name": name, "input_schema": {"type": "object", "properties": {}}}
            for name in ("cast_spell", "cast_spell_outside_combat", "begin_srd_spell_action")
        ],
    }
    original_pc_artifacts = deepcopy(record["participant_artifacts"])
    birth.bind_exploration_created_actors(data, record)
    declarations = {row["name"]: row for row in record["tools"]}
    fact = declarations[birth.PROXIMITY]
    assert record["participant_artifacts"] == original_pc_artifacts
    assert fact["writers"] == ["dm"] and fact["turn_statuses"] == ["open"]
    assert fact["input_schema"]["properties"]["distance_feet"]["minimum"] == 0
    for name in ("cast_spell", "cast_spell_outside_combat", "begin_srd_spell_action"):
        properties = declarations[name]["input_schema"]["properties"]
        assert properties["created_actor_distance"]["minimum"] == -1
        assert properties["created_actor_space_visible"]["type"] == "boolean"
        assert "created_actor_distance" not in declarations[name]["input_schema"]["required"]
        parameters = {row["name"]: row for row in data["tools"][name]["parameters"]}
        assert parameters["created_actor_distance"]["default"] == -1
        assert parameters["created_actor_space_visible"]["default"] is False


def _object_and_actor_intent_fixture():
    """Compose actual object root additions onto the existing retained-cast seams."""
    data = _binding_fixture()
    tools = data["tools"]
    for name in ("cast_spell", "begin_srd_spell_action"):
        tools[name]["effects"].insert(0, _set("_casting_profile", {}))
    tools["cast_spell"]["effects"][1]["set"]["value"][0]["already_retained"] = "'native'"
    tools["cast_spell_outside_combat"]["effects"][0]["params"].update(
        {fact: "{" + fact + "}" for fact in ("target_distance", "caster_can_touch_target", "cover")}
    )
    exploration_objects._bind_roots(data)
    return data


def _object_merge_rows(data):
    return [
        row
        for rows in birth._lists(data["tools"]["cast_spell"]["effects"])
        for row in rows
        if row.get("set", {}).get("field") == "_retained_cast_tail_choices"
    ]


def test_object_merge_preserves_created_actor_choices_and_checked_capsule_guards():
    data = _object_and_actor_intent_fixture()
    capture, merge = _object_merge_rows(data)
    original_merge = deepcopy(merge)
    birth._bind_intent(data["tools"])
    assert merge == original_merge
    assert all(capture["set"]["value"][0][choice] == choice for choice, _, _, _ in birth.CHOICES)

    # Execute the actual generated fact-copy/capture/merge and retained unwrap.
    # Payment, qualification and creature birth stay in their existing proofs.
    object_merge = next(
        row
        for row in data["tools"]["cast_spell"]["effects"]
        if any(child == merge for rows in birth._lists([row]) for child in rows)
    )
    unwrap = []
    for row in data["tools"]["finish_srd_retained_cast_tail"]["effects"]:
        if "call" in row:
            break
        unwrap.append(deepcopy(row))
    data["tools"]["transport_probe"] = _tool(
        "Execute the actual composed retained choices.",
        [
            _parameter("spell"),
            _parameter("target_distance", "number"),
            _parameter("caster_can_touch_target", "boolean"),
            _parameter("cover"),
            *birth._parameters(),
        ],
        [
            *deepcopy(data["tools"]["cast_spell"]["effects"][:3]),
            deepcopy(capture),
            deepcopy(object_merge),
            *unwrap,
        ],
    )
    choices = {
        "created_actor_placement": "beside the established oak",
        "created_actor_distance": 12,
        "created_actor_space_unoccupied": True,
        "created_actor_space_visible": True,
        "created_actor_clear_path": True,
    }
    bridge = _bridge()
    before = deepcopy(bridge.get_entity("state"))
    rng_before = dice._rng.getstate()
    result = _run(
        data,
        bridge,
        "transport_probe",
        {
            "spell": "light",
            "target_distance": 4.75,
            "caster_can_touch_target": True,
            "cover": "none",
            **choices,
        },
    )
    assert result.success, result.error
    retained = result.context["_retained_cast_tail_choices"]
    assert retained["already_retained"] == "native" and retained["spell"] == "light"
    assert retained["exploration_object_distance"] == 4.75
    assert retained["exploration_object_touch"] is True
    assert retained["exploration_object_cover"] == "none"
    for choice, value in choices.items():
        assert retained[choice] == value and result.context["_birth_choice_" + choice] == value
    assert bridge.get_entity("state") == before and dice._rng.getstate() == rng_before

    # Only the exact known merge is admitted; malformed original captures,
    # another merge source, and a merge writing a different capsule still fail.
    for drift in ("capture", "merge_source", "merge_field"):
        changed = _object_and_actor_intent_fixture()
        changed_capture, changed_merge = _object_merge_rows(changed)
        if drift == "capture":
            changed_capture["set"]["value"] = ["unapproved capture"]
        elif drift == "merge_source":
            changed_merge["set"]["value"] = [
                changed_merge["set"]["value"][0].replace(
                    "_lite_object_retained_facts", "_unapproved_facts"
                )
            ]
        else:
            changed_merge["set"]["field"] = "saved.retained_cast_choices"
        with pytest.raises(ValueError, match="checked retained cast choice capsule"):
            birth._bind_intent(changed["tools"])
