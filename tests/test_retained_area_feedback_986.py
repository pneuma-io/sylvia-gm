"""Small real DSL/map/card probes; no full package, personal campaign or GUI."""

from copy import deepcopy
from random import Random

import pytest
from scripts import srd_lite_insect_feedback as insect
from scripts import srd_lite_retained_area_map as areas
from scripts.srd_scope96_guardian_entry import bind_guardian_movement_card
from scripts.srd_spell_environment import _sphere_hazard_tools
from scripts.srd_timed_actor_defenses import _parameter, _set, _tool

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.application.result_cards import project_result_card
from sylvia_gm.application.tactical_grid import project_coordinate_map
from sylvia_gm.domain.authority import thaw_json_value
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget


def _scene():
    return {
        "format": 1,
        "map": {
            "id": "test-map",
            "revision": 1,
            "bounds": {"min": [0, 0, 0], "max": [100, 100, 30]},
            "grid": {"cell_size": 5, "unit": "ft", "origin": [0, 0]},
            "layers": [{"id": "floor", "min_elevation": 0, "max_elevation": 30}],
        },
        "geometry": [],
        "entities": [],
        "terrain": [],
        "zones": [
            {
                "id": "unrelated-private-zone",
                "map_id": "test-map",
                "shape": {"kind": "box", "min": [0, 0, 0], "max": [5, 5, 5]},
                "metadata": {"gm_private": "Private fixture zone label"},
            }
        ],
    }


def _source(spell="cloud_of_daggers", **extra):
    return {
        "profile": "environment",
        "spell": spell,
        "mode": "lite_daggers" if spell == "cloud_of_daggers" else "web",
        "active": True,
        "expires_tick": 20,
        "sequence": 1,
        "known_area": True,
        "map_id": "test-map",
        "origin": [30, 30, 0],
        "radius": 3.5,
        **extra,
    }


def _state(sources=None):
    sources = sources or {"cloud": _source()}
    return {
        "spatial_scene": _scene(),
        "lasting_tick": 0,
        "persistent_spell_area_ids": list(sources),
        "persistent_spell_areas": sources,
    }


def _formatter():
    # Exact existing formatter primitive, without constructing its unrelated query owners.
    return _tool(
        "Format an authorized result.",
        [_parameter("message")],
        [_set("_info_projection_message", "message")],
    )


def _execute(name, tools, state, parameters=None, entities=None):
    parsed = RulesetRuntime(ruleset_dict={"tools": tools})
    namespace = "test.retained-feedback"
    bridge = AuthorityMemoryStateBridge(
        namespace,
        {
            namespace + ".state": deepcopy(state),
            namespace + ".combat": {},
            namespace + ".entities": deepcopy(entities or {}),
        },
    )

    def nested(child, values, parent):
        return execute_tool(
            child,
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
        name,
        parameters or {},
        parsed.tools,
        bridge,
        parsed.ruleset_config,
        parsed.definitions,
        combat_state=bridge.combat_state,
        tool_executor=nested,
        budget=ExecutionBudget(),
    )
    assert result.success, result.error
    return result, bridge.get_entity("state")


def _refresh(state):
    return _execute(
        areas.REFRESH,
        {
            areas.REFRESH: areas._refresh_tool(),
            "format_srd_information_projection": _formatter(),
        },
        state,
    )[1]


def test_actual_dsl_round_cloud_zone_is_public_inert_and_preserves_sources():
    state = _state()
    state["persistent_spell_areas"]["cloud"]["caster"] = "hidden-caster"
    original = deepcopy(state["persistent_spell_areas"])
    updated = _refresh(state)
    assert updated["persistent_spell_areas"] == original
    assert updated["spatial_scene"]["zones"][0] == state["spatial_scene"]["zones"][0]
    projection = thaw_json_value(
        project_coordinate_map(
            {
                "scene": updated["spatial_scene"],
                "enabled": True,
            }
        )
    )
    assert len(projection["zones"]) == 1
    zone = projection["zones"][0]
    assert zone["shape"] == {
        "kind": "cylinder",
        "center": [30, 30, 0],
        "radius": 3.5,
        "height": 3.5,
    }
    assert zone["display"]["label"] == "Cloud of Daggers · spherical footprint"
    assert "Spherical mechanical area" in zone["display"]["height_label"]
    assert "gm_private" not in str(projection)
    assert "hidden-caster" not in str(projection)
    metadata = updated["spatial_scene"]["zones"][1]["metadata"]
    assert set(metadata) == {areas.TAG, "overlay_area_id", "overlay_source_sequence", "player"}
    assert not {"source_environment_id", "source_sequence", "movement", "hazard"} & metadata.keys()
    # Repeating unchanged lifecycle projection is stable and does not churn revisions.
    assert _refresh(updated) == updated


@pytest.mark.parametrize("position", ([0, 30, 0], [100, 100, 30], [30, 0, 0]))
def test_cloud_boundary_display_is_labelled_clipped_extent_without_rejecting_mechanics(position):
    state = _state({"cloud": _source(origin=position)})
    original = deepcopy(state["persistent_spell_areas"])
    updated = _refresh(state)
    projection = thaw_json_value(project_coordinate_map({"scene": updated["spatial_scene"]}))
    zone = projection["zones"][0]
    assert zone["shape"]["kind"] == "box"
    assert zone["display"]["label"] == "Cloud of Daggers · clipped area bounds"
    assert zone["display"]["state"] == "Extent only"
    assert zone["display"]["height_label"] == "Extent only; sphere continues beyond map"
    assert updated["persistent_spell_areas"] == original


@pytest.mark.parametrize(
    "changed",
    ({"active": False}, {"expires_tick": 0}, {"known_area": False}, {"map_id": "different-map"}),
)
def test_ended_expired_unknown_and_other_map_areas_are_removed(changed):
    updated = _refresh(_state())
    updated["persistent_spell_areas"]["cloud"].update(changed)
    refreshed = _refresh(updated)
    assert refreshed["spatial_scene"]["zones"] == _scene()["zones"]


def test_removed_source_and_omitted_knowledge_do_not_leak_stale_map_zones():
    updated = _refresh(_state())
    updated["persistent_spell_areas"] = {}
    assert _refresh(updated)["spatial_scene"]["zones"] == _scene()["zones"]
    hidden = _state()
    hidden["persistent_spell_areas"]["cloud"].pop("known_area")
    hidden["persistent_spell_areas"]["cloud"]["label"] = "Cloud of Daggers"
    hidden["persistent_spell_areas"]["cloud"]["caster_label"] = "Player character"
    assert _refresh(hidden)["spatial_scene"]["zones"] == _scene()["zones"]


def test_web_cells_project_actual_geometry_and_remove_burned_or_old_instance_cells():
    cell = {
        "volume": {"kind": "box", "min": [20, 20, 0], "max": [25, 25, 5]},
        "source_sequence": 1,
        "status": "intact",
    }
    cells = {
        "intact": cell,
        "burning": {**deepcopy(cell), "status": "burning", "burn_end_tick": 2},
        "hole": {**deepcopy(cell), "status": "destroyed"},
        "old": {**deepcopy(cell), "source_sequence": 0},
    }
    state = _state(
        {"web": _source("web", web_cell_schema=1, web_cell_ids=list(cells), web_cells=cells)}
    )
    updated = _refresh(state)
    projection = thaw_json_value(project_coordinate_map({"scene": updated["spatial_scene"]}))
    assert len(projection["zones"]) == 2
    assert {zone["display"]["state"] for zone in projection["zones"]} == {"intact", "burning"}
    assert all(zone["shape"] == cell["volume"] for zone in projection["zones"])
    updated["lasting_tick"] = 2
    refreshed = _refresh(updated)
    assert len(refreshed["spatial_scene"]["zones"]) == 2  # private fixture plus one intact cell
    assert refreshed["spatial_scene"]["zones"][1]["metadata"]["player"]["state"] == "intact"


def _map_binding_runtime():
    def owner(effects=None):
        return {
            "description": "Existing owner",
            "parameters": [{"name": "lasting_state"}],
            "effects": effects or [],
            "metadata": {},
        }

    tools = {
        name: owner()
        for name in (
            "cast_spell_environment",
            "refresh_srd_environment_scene",
            "refresh_srd_environment_lifecycle",
            "finish_srd_shared_action_settlements",
            "end_concentration",
            "retire_srd_environment_ground_source",
            "settle_srd_retained_web_burns",
            "expose_srd_retained_web_fire",
        )
    }
    tools["cast_spell"] = owner(
        [
            _set("_retained_cast_tail_choices", [{"spell": "spell"}]),
            _set("state.pending.retained_cast_choices", [{"spell": "spell"}]),
        ]
    )
    tools["cast_spell_outside_combat"] = owner([{"call": "cast_spell", "params": {}}])
    tools["finish_srd_retained_cast_tail"] = owner(
        [{"call": "cast_spell_environment", "params": {}}]
    )
    tools["finish_srd_retained_cast_tail"]["metadata"]["retained_spell_reactions_v1_choices"] = [
        "spell"
    ]
    tools["begin_srd_spell_action"] = owner(
        [_set("lasting_state.pending_srd_actions.{_pending_action_id}.spell", "spell")]
    )
    tools["complete_srd_pending_action"] = owner(
        [{"call": "cast_spell", "params": {"spell": "{_pending_action.parameters.spell}"}}]
    )
    return {"tools": tools}


def test_known_intent_reaches_normal_deferred_and_staged_casts_after_commit():
    runtime = _map_binding_runtime()
    areas.bind_retained_area_map(runtime)
    tools = runtime["tools"]
    for name in (
        "cast_spell",
        "cast_spell_outside_combat",
        "begin_srd_spell_action",
        "cast_spell_environment",
    ):
        assert tools[name]["parameters"][-1] == areas._parameter()
        assert "only when the player knows" in tools[name]["description"]
    captures = [row["set"]["value"] for row in tools["cast_spell"]["effects"]]
    assert all(capture[0]["known_area"] == "known_area" for capture in captures)
    tail = tools["finish_srd_retained_cast_tail"]["effects"]
    assert tail[0] == _set("_map_cast_known", "_retained_cast_tail_choices.known_area == true")
    assert tail[1]["params"]["known_area"] == "{_map_cast_known}"
    complete = tools["complete_srd_pending_action"]["effects"]
    assert complete[0] == _set(
        "_map_pending_known", "_pending_action.parameters.known_area == true"
    )
    assert complete[1]["params"]["known_area"] == "{_map_pending_known}"
    for name in (
        "end_concentration",
        "refresh_srd_environment_lifecycle",
        "finish_srd_shared_action_settlements",
        "settle_srd_retained_web_burns",
        "expose_srd_retained_web_fire",
        "retire_srd_environment_ground_source",
    ):
        assert tools[name]["effects"][-1]["call"] == areas.REFRESH
    source_stamp = tools["cast_spell_environment"]["effects"][-1]["on_true"]
    assert source_stamp[-1]["call"] == areas.REFRESH


def test_map_binder_drift_is_atomic():
    runtime = _map_binding_runtime()
    runtime["tools"]["complete_srd_pending_action"]["effects"] = []
    original = deepcopy(runtime)
    with pytest.raises(ValueError, match="completion dispatcher"):
        areas.bind_retained_area_map(runtime)
    assert runtime == original


def test_insect_binding_uses_native_committed_receipt_and_existing_rolls():
    owner = _sphere_hazard_tools()["resolve_srd_environment_sphere_event"]
    original = deepcopy(owner)
    insect._bind_event(owner)
    before = [row for rows in insect._lists(original["effects"]) for row in rows]
    after = [row for rows in insect._lists(owner["effects"]) for row in rows]
    assert [row for row in after if "dice" in row] == [row for row in before if "dice" in row]
    assert len([row for row in after if row.get("call") == "d20_test"]) == 1
    save = next(row for row in after if row.get("call") == "d20_test")
    assert save["export_context"]["_lite_insect_save_modifier"] == "_combined_modifier"
    assert save["export_context"]["_lite_insect_save_total"] == "_adjusted_total"
    receipt = next(
        row
        for row in after
        if row.get("set", {}).get("field") == "target_state.last_environment_sphere_save"
    )
    assert receipt in before


def test_actual_dsl_insect_rows_project_recorded_faces_and_separate_applied_damage(monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(986))
    effects = [
        *insect._initial_fields(),
        _set("_sphere_event_source", {"spell": "'insect_plague'", "save_dc": 15}),
        _set("_sphere_profile", {"save_ability": "'constitution'", "damage_type": "'piercing'"}),
        {"dice": {"expression": "1d20"}, "store_as": "_d20_test"},
        {"dice": {"expression": "4d10"}, "store_as": "_sphere_damage_roll"},
        _set("_lite_insect_save_modifier", 3),
        _set("_lite_insect_save_total", "_d20_test.total + 3"),
        _set("_outcome", "'success'"),
        _set("_lite_insect_hp_before", 30),
        _set("_lite_insect_temp_before", 5),
        _set("_effective_damage", 2),
        *insect._receipt_rows(),
    ]
    probe = _tool(
        "Insect recorded feedback probe",
        [{"name": "target_state", "type": "string", "resolve": "entity", "required": True}],
        effects,
    )
    recorder = _tool(
        "Record the settled target label and text",
        [
            _parameter("target", resolve="entity"),
            _parameter("brief"),
            _parameter("detail"),
        ],
        [
            _set("_spell_target_summary", {"label": "target.name", "value": "brief"}),
            _set("_spell_target_detail", {"label": "target.name", "value": "detail"}),
        ],
    )
    result, _ = _execute(
        "insect_probe",
        {
            "insect_probe": probe,
            "format_srd_information_projection": _formatter(),
            "record_srd_spell_target_result": recorder,
        },
        {},
        {"target_state": "victim"},
        {"victim": {"name": "Goblin", "hp": 28, "temp_hp": 5}},
    )
    context = result.context
    card = project_result_card(
        {
            "version": 1,
            "footprint": "compact",
            "title": "Insect Plague",
            "summary": [],
            "details_label": "Targets and calculations",
            "details": {"path": "context." + insect.PREFIX + "detail_rows"},
        },
        parameters={},
        public_context=context,
    )
    assert card is not None
    assert len(card["details"]) == 3
    save, damage = card["details"][1:]
    assert save["equation"]["rolls"] == list(context["_d20_test"].results)
    assert save["equation"]["total"] == context["_d20_test"].total + 3
    assert damage["equation"]["rolls"] == list(context["_sphere_damage_roll"].results)
    assert damage["equation"]["total"] == sum(damage["equation"]["rolls"])
    assert "applied 2 after save and defenses" in damage["value"]
    assert context[insect.PREFIX + "applied"] == 2
    assert "HP 30 to 28" in card["details"][0]["value"]


def test_insect_collect_clears_conditional_child_rows_before_every_native_call():
    owner = {
        "effects": [
            {
                "call": "child",
                "params": {},
                "condition": "enabled == true",
                "export_context": {"preserved": "preserved"},
            }
        ]
    }
    insect._collect(owner, "child")
    rows = owner["effects"]
    assert rows[3:6] == insect._initial_fields(insect.PREFIX + "child_")
    assert rows[6]["condition"] == "enabled == true"
    assert rows[6]["export_context"]["preserved"] == "preserved"
    assert len(rows[6]["export_context"]) == 4


def _insect_binding_runtime():
    tools = {}
    for parent, child in insect.EDGES:
        tools[parent] = {
            "effects": [{"call": child, "params": {}, "export_context": {}}],
            "metadata": {},
        }
    tools["resolve_srd_environment_sphere_event"] = _sphere_hazard_tools()[
        "resolve_srd_environment_sphere_event"
    ]
    tools["finish_srd_retained_cast_tail"] = {
        "effects": [
            {
                "call": "cast_spell_environment",
                "params": {},
                "condition": "_retained_cast_tail_choices.spell == 'insect_plague'",
                "export_context": {"_spell_detail_rows": "_spell_detail_rows"},
            }
        ]
    }
    tools["move_srd_scene_creature"]["metadata"]["result_card"] = {
        "details": {"path": "context._lite_daggers_detail_rows"},
        "summary": [],
    }
    runtime = {"tools": tools}
    bind_guardian_movement_card(runtime)
    return runtime


def test_insect_cards_forward_through_existing_cast_movement_and_turn_boundaries():
    runtime = _insect_binding_runtime()
    movement_card_before = deepcopy(
        runtime["tools"]["move_srd_scene_creature"]["metadata"]["result_card"]
    )
    insect.bind_insect_feedback(runtime)
    tools = runtime["tools"]
    for parent, child in insect.EDGES:
        calls = [
            row
            for rows in insect._lists(tools[parent]["effects"])
            for row in rows
            if row.get("call") == child
        ]
        assert len(calls) == 1
        assert calls[0]["export_context"][insect.PREFIX + "child_detail_rows"] == (
            insect.PREFIX + "detail_rows"
        )
    assert tools["cast_spell_environment"]["effects"][-1] == _set(
        "_spell_detail_rows", "_spell_detail_rows + " + insect.PREFIX + "detail_rows"
    )
    assert tools["move_srd_scene_creature"]["effects"][-1] == _set(
        "_guardian_movement_card_rows", "_lite_daggers_detail_rows + _guardian_movement_rows"
    )
    assert tools["move_srd_scene_creature"]["effects"][-2] == _set(
        "_lite_daggers_detail_rows", "_lite_daggers_detail_rows + " + insect.PREFIX + "detail_rows"
    )
    assert tools["move_srd_scene_creature"]["effects"][-3] == _set(
        insect.PREFIX + "event_count", "len(" + insect.PREFIX + "summary_rows)"
    )
    movement_card = tools["move_srd_scene_creature"]["metadata"]["result_card"]
    assert movement_card["details"] == movement_card_before["details"]
    assert movement_card["details_label"] == movement_card_before["details_label"]
    assert movement_card["summary"][:-1] == movement_card_before["summary"]
    assert movement_card["summary"][-1]["value"]["path"] == (
        "context." + insect.PREFIX + "event_count"
    )
    assert tools["end_combat_turn"]["effects"][-1] == _set(
        "_end_turn_rows", "_end_turn_rows + " + insect.PREFIX + "detail_rows"
    )
    tail = tools["finish_srd_retained_cast_tail"]["effects"]
    assert tail[0]["export_context"]["_spell_detail_rows"] == "_spell_detail_rows"
    assert tail[0]["export_context"][insect.PREFIX + "summary_rows"] == (
        insect.PREFIX + "summary_rows"
    )
    assert "insect_plague" in tail[1]["condition"]


def test_insect_binder_drift_does_not_partially_change_mechanics_or_cards():
    runtime = _insect_binding_runtime()
    runtime["tools"]["move_srd_scene_creature"]["metadata"]["result_card"]["details"] = {}
    original = deepcopy(runtime)
    with pytest.raises(ValueError, match="movement recipient details"):
        insect.bind_insect_feedback(runtime)
    assert runtime == original
