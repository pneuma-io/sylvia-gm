"""Bounded producer/presentation checks; closed spell mechanics are not replayed."""

import json
import re
from copy import deepcopy
from pathlib import Path

import pytest
from scripts.srd_control_behaviors import _each, _push
from scripts.srd_timed_actor_defenses import _call, _parameter, _set, _tool
from sylvia_gm.application.tactical_grid import project_coordinate_map
from sylvia_gm.domain.authority import thaw_json_value
from sylvia_gm.runtime._schema_validator import validate_parameter_declarations
from tests.test_srd_lite_pending_area_preview import _execute, _formatter, _scene

from scripts import srd_lite_persistent_spell_map as visual

W = Path("D:/Codex/2026-10-09/sylvia-main-01a11b3b/integration-01a120e9")


def _tools():
    tools = {}
    for filename in ("selected-map-feedback-owners-1135.json", "selected-additional-map-movement-owners-1138.json"):
        tools.update(json.loads((W / filename).read_text(encoding="utf-8"))["tools"])
    return tools


def _body(spell="guardian_of_faith"):
    body = deepcopy(_scene()["entities"][0])
    body.update(id="born-body", footprint={"kind": "box", "half_extents": [5, 5]}, height=10)
    body["metadata"].update(source_ref=spell, source_caster="caster", hp=17,
                            private_reason="Never copy this into a public label")
    return body


def _present(body, known=True, spell="guardian_of_faith", source="", state=None):
    owner = _tool("Present only the supplied exact effect body.", [], [
        _set("_computed_body_capsule", [body]),
        _call(visual.BODY, {"body_capsule": "{_computed_body_capsule}", "spell": spell, "caster_id": "caster",
                           "source_id": source, visual.KNOWN: known},
              ("_visual_body", "_visual_added")),
        _set("state.spatial_scene.entities", ["_visual_body"]),
    ])
    state = state or {"spatial_scene": _scene()}
    result, changed = _execute({"present": owner, visual.BODY: visual._body_tool(),
                                "format_srd_information_projection": _formatter()},
                               state, name="present")
    return result, changed


def test_known_native_body_has_public_feedback_without_altering_mechanics():
    body = _body()
    original = deepcopy(body)
    result, state = _present(body)
    assert result.success, result.error
    assert body == original
    produced = state["spatial_scene"]["entities"][0]
    assert {key: value for key, value in produced.items() if key != "metadata"} == {
        key: value for key, value in original.items() if key != "metadata"
    }
    assert {key: value for key, value in produced["metadata"].items()
            if key not in ("player", visual.TAG)} == original["metadata"]
    public = thaw_json_value(project_coordinate_map({"scene": state["spatial_scene"], "enabled": True}))
    assert public["entities"][0]["display"]["label"] == "Guardian of Faith"
    assert "Never copy" not in str(public)
    assert "caster" not in str(public)


def test_unknown_body_stays_private_and_existing_public_label_is_preserved():
    body = _body()
    result, state = _present(body, known=False)
    assert result.success, result.error
    assert state["spatial_scene"]["entities"][0] == body
    public = thaw_json_value(project_coordinate_map({"scene": state["spatial_scene"], "enabled": True}))
    assert public["entities"] == []
    body["metadata"]["player"] = {"label": "Player's known guardian", "kind": "spell_body"}
    result, state = _present(body)
    assert result.success, result.error
    assert state["spatial_scene"]["entities"][0]["metadata"]["player"] == body["metadata"]["player"]


@pytest.mark.parametrize("field", ["source_ref", "source_caster"])
def test_mismatched_native_body_identity_rejects_before_presentation(field):
    body = _body()
    body["metadata"][field] = "forged"
    result, _ = _present(body)
    assert not result.success
    assert "identity changed" in str(result.error)


def test_stone_panel_requires_its_real_source_serial_and_keeps_hp():
    body = _body("wall_of_stone")
    body["metadata"] = {"plane": "material", "solid_wall_source_id": "wall-1", "solid_wall_serial": 7,
                        "srd_spell_object": {"kind": "wall_stone_panel", "source_id": "wall-1",
                                             "serial": 7, "hp": 41, "max_hp": 180}}
    state = {"spatial_scene": _scene(), "solid_spell_wall_sources": {"wall-1": {
        "installed": True, "spell_id": "wall_of_stone", "caster_state_id": "caster", "serial": 7,
        "permanent": True,
    }}}
    result, changed = _present(body, spell="wall_of_stone", source="wall-1", state=state)
    assert result.success, result.error
    panel = changed["spatial_scene"]["entities"][0]
    assert panel["metadata"]["srd_spell_object"]["hp"] == 41
    assert changed["solid_spell_wall_sources"] == state["solid_spell_wall_sources"]
    state["solid_spell_wall_sources"]["wall-1"]["serial"] = 8
    result, _ = _present(body, spell="wall_of_stone", source="wall-1", state=state)
    assert not result.success
    assert "stamp changed" in str(result.error)


def test_real_mage_hand_uses_native_force_marker_and_never_reveals_an_eye():
    body = _body("mage_hand")
    body["metadata"].pop("source_ref")
    body["metadata"].pop("source_caster")
    body["metadata"]["srd_object_force"] = {"spell": "mage_hand", "caster": "caster", "source_id": "hand-1"}
    result, changed = _present(body, spell="mage_hand", source="hand-1")
    assert result.success, result.error
    assert changed["spatial_scene"]["entities"][0]["metadata"]["player"]["label"] == "Mage Hand"
    result, _ = _present(_body("arcane_eye"), spell="arcane_eye")
    assert not result.success
    assert "no public physical-body owner" in str(result.error)


def test_inactive_known_body_is_removed_by_the_existing_public_projector():
    result, state = _present(_body())
    assert result.success, result.error
    state["spatial_scene"]["entities"][0]["active"] = False
    public = thaw_json_value(project_coordinate_map({"scene": state["spatial_scene"], "enabled": True}))
    assert public["entities"] == []


@pytest.mark.parametrize("origin,radius,kind", [([0, 0, 0], 30, "cylinder"), ([90, 0, 0], 30, "box")])
def test_range_extent_is_labeled_honestly_and_preserves_other_zones(origin, radius, kind):
    state = {"spatial_scene": _scene()}
    owner = _tool("Project a qualified source's display extent.", [], [
        _set("_map_next_zones", []), _set("_extent_saved_zones", "state.spatial_scene.zones"),
        _set("_visual_zone_index", 0), _set("_visual_anchor_id", None),
        _each("_extent_saved_zones", "_extent_saved_zone", [_push("_map_next_zones", "_extent_saved_zone")]),
        _set("_visual_source", "actual-source"), _set("_visual_origin", origin),
        *visual._round_extent(radius, "nominal", "Aura of Life · 30-foot nominal extent"),
        _set("state.spatial_scene.zones", "_map_next_zones"),
    ])
    result, changed = _execute({"extent": owner, "format_srd_information_projection": _formatter()},
                               state, name="extent")
    assert result.success, result.error
    assert changed["spatial_scene"]["zones"][0] == state["spatial_scene"]["zones"][0]
    assert "display_anchor" not in changed["spatial_scene"]["zones"][1]["metadata"]
    public = thaw_json_value(project_coordinate_map({"scene": changed["spatial_scene"], "enabled": True}))
    assert len(public["zones"]) == 1
    zone = public["zones"][0]
    assert zone["shape"]["kind"] == kind
    assert zone["display"]["state"] == "Extent only"
    assert "qualification" in zone["display"]["height_label"] or "rules" in zone["display"]["height_label"]


@pytest.mark.parametrize("mode,expected", [("omitted", False), ("private", False), ("public", True), ("extra-private-key", False)])
def test_current_anchor_gate_omits_distant_or_private_bodies_without_blocking(mode, expected):
    scene = _scene()
    if mode == "omitted":
        scene["entities"] = []
    else:
        scene["entities"][0]["metadata"]["participant_ref"] = "anchor"
        if mode in ("public", "extra-private-key"):
            scene["entities"][0]["metadata"]["player"] = {"label": "Known anchor", "kind": "creature"}
        if mode == "extra-private-key":
            scene["entities"][0]["metadata"]["player"]["secret"] = "Not a valid public marker"
    result, changed = _execute({visual.ANCHOR: visual._anchor_tool(), "format_srd_information_projection": _formatter()},
                               {"spatial_scene": scene}, name=visual.ANCHOR, parameters={"subject": "anchor"})
    assert result.success, result.error
    assert result.context["_visual_anchor_known"] is expected
    assert result.context["_visual_anchor_id"] == (None if mode == "omitted" else scene["entities"][0]["id"])
    assert changed["spatial_scene"] == scene


def test_anchor_result_resets_exact_body_id_when_the_next_subject_is_omitted():
    scene = _scene()
    scene["entities"][0]["metadata"].update(participant_ref="anchor", player={"label": "Known", "kind": "creature"})
    owner = _tool("Read two separate current anchors.", [], [
        _call(visual.ANCHOR, {"subject": "anchor"}, ("_visual_anchor_id", "_visual_anchor_known")),
        _set("_first_anchor_id", "_visual_anchor_id"),
        _call(visual.ANCHOR, {"subject": "omitted"}, ("_visual_anchor_id", "_visual_anchor_known")),
    ])
    result, changed = _execute({"read_twice": owner, visual.ANCHOR: visual._anchor_tool(),
                                "format_srd_information_projection": _formatter()},
                               {"spatial_scene": scene}, name="read_twice")
    assert result.success, result.error
    assert result.context["_first_anchor_id"] == scene["entities"][0]["id"]
    assert result.context["_visual_anchor_id"] is None
    assert result.context["_visual_anchor_known"] is False
    assert changed["spatial_scene"] == scene


def test_static_point_light_does_not_inherit_the_previous_held_light_anchor():
    scene = _scene()
    holder = scene["entities"][0]
    holder["id"] = "holder-body"
    holder["metadata"].update(participant_ref="holder-state", player={"label": "Known holder", "kind": "creature"})
    lamp = deepcopy(holder)
    lamp["id"] = "lamp"
    lamp["metadata"] = {"plane": "material", "carried_by": "holder-state"}
    scene["entities"].append(lamp)
    base = {visual.KNOWN: True, "kind": "illumination_spell", "spell": "light", "caster": "caster",
            "started_tick": 0, "bright_radius": 20, "dim_radius": 40}
    records = [dict(base, source="lamp", instance=1, current_origin=[10, 0, 0]),
               dict(base, source="", instance=2, current_origin=[0, 10, 0], **{visual.MAP: "arena"})]
    owner = _tool("Draw only qualified illumination outputs.", [], [
        _set("_map_next_zones", []), _set("_visual_zone_index", 0),
        _set("_visual_anchor_id", "prior-unrelated-anchor"),
        *visual._light_zones(), _set("state.spatial_scene.zones", "_map_next_zones"),
    ])
    # Current-origin qualification is closed elsewhere; this case owns only anchor forwarding/reset.
    qualified_light = _tool("Supply the already qualified current light outputs.",
                            [_parameter("record", "object"), _parameter("caster_state")], [
                                _set("_illumination_active", True), _set("_illumination_covered", False),
                                _set("_illumination_origin", "record.current_origin"),
                                _set("_illumination_plane", "'material'"),
                            ])
    entity = _tool("Read the exact current held source.", [_parameter("subject")], [
        _set("_info_scene_entity", lamp),
    ])
    result, changed = _execute({"light_extents": owner, visual.ANCHOR: visual._anchor_tool(),
                                "check_srd_illumination_record": qualified_light,
                                "srd_information_scene_entity": entity,
                                "format_srd_information_projection": _formatter()},
                               {"spatial_scene": scene, "turn_effects": records}, name="light_extents")
    assert result.success, result.error
    zones = changed["spatial_scene"]["zones"]
    assert len(zones) == 4
    assert [zone["metadata"].get("display_anchor") for zone in zones] == ["holder-body", "holder-body", None, None]
    assert changed["turn_effects"] == records
    assert changed["spatial_scene"]["entities"] == scene["entities"]


def test_exact_exported_producers_receive_knowledge_without_rewriting_native_bodies():
    tools = _tools()
    original = deepcopy(tools)
    runtime = {"tools": tools}
    visual.bind_persistent_spell_map(runtime)
    assert not validate_parameter_declarations(runtime)
    for name, field in (("create_srd_source_actor_scene", "_created_actor_scene"),
                        ("cast_srd_stone_spell_wall", "_solid_wall_new_panel"),
                        ("cast_srd_mage_hand", "_world_created_entity")):
        before = [row["set"]["value"] for rows in visual._lists(original[name]["effects"]) for row in rows
                  if row.get("set", {}).get("field") == field]
        after = [row["set"]["value"] for rows in visual._lists(tools[name]["effects"]) for row in rows
                 if row.get("set", {}).get("field") == field and row["set"]["value"] != "_visual_body"]
        assert after == before
        assert any(row.get("call") == visual.BODY for rows in visual._lists(tools[name]["effects"]) for row in rows)
    for name in ("cast_srd_spell_ward", "install_srd_illumination_source", "install_srd_actor_spell_modifier"):
        assert tools[name]["effects"][-1]["call"] == visual.REFRESH


def test_actual_late_cast_capsule_and_tail_keep_the_owned_object_fact_merge():
    tools = _tools()
    tools.update(json.loads((W / "selected-truth-dispatch-owners-1144.json").read_text(encoding="utf-8"))["tools"])
    original = deepcopy(tools)
    visual.bind_persistent_spell_map({"tools": tools})
    captures = [row["set"]["value"] for rows in visual._lists(tools["cast_spell"]["effects"]) for row in rows
                if row.get("set", {}).get("field") == "_retained_cast_tail_choices"]
    original_captures = [row["set"]["value"] for rows in visual._lists(original["cast_spell"]["effects"]) for row in rows
                         if row.get("set", {}).get("field") == "_retained_cast_tail_choices"]
    assert len(captures) == len(original_captures) == 2
    assert captures[0][0][visual.KNOWN] == visual.KNOWN
    assert {key: value for key, value in captures[0][0].items() if key != visual.KNOWN} == original_captures[0][0]
    assert captures[1] == original_captures[1] == [visual.CAPSULE_FACT_MERGE]
    tail = tools["finish_srd_retained_cast_tail"]
    assert tail["metadata"]["retained_spell_reactions_v1_choices"].count(visual.KNOWN) == 1
    for name in ("cast_srd_spell_ward", "cast_srd_stone_spell_wall"):
        calls = [row for rows in visual._lists(tail["effects"]) for row in rows if row.get("call") == name]
        assert len(calls) == 1
        assert calls[0]["params"][visual.KNOWN] == "{_visual_cast_known}"


def test_actual_staged_dispatch_forwards_knowledge_without_an_extra_cast_or_payment_path():
    tools = _tools()
    for filename in ("selected-truth-dispatch-owners-1144.json", "selected-truth-pending-capture-1158.json"):
        tools.update(json.loads((W / filename).read_text(encoding="utf-8"))["tools"])
    complete = tools["complete_srd_pending_action"]
    original = deepcopy(complete)
    visual.bind_persistent_spell_map({"tools": tools})
    rows = [row for group in visual._lists(complete["effects"]) for row in group]
    old_rows = [row for group in visual._lists(original["effects"]) for row in group]
    calls = [row for row in rows if row.get("call") == "cast_spell"
             and row.get("params", {}).get("spell") == "{_pending_action.parameters.spell}"]
    old_calls = [row for row in old_rows if row.get("call") == "cast_spell"
                 and row.get("params", {}).get("spell") == "{_pending_action.parameters.spell}"]
    assert len(calls) == len(old_calls) == 1
    assert calls[0]["params"][visual.KNOWN] == "{_visual_pending_known}"
    assert {key: value for key, value in calls[0]["params"].items() if key != visual.KNOWN} == old_calls[0]["params"]
    assert [row["call"] for row in rows if "call" in row] == [row["call"] for row in old_rows if "call" in row]


@pytest.mark.parametrize("count", [0, 2])
def test_actual_staged_dispatch_rejects_a_missing_or_duplicate_owner(count):
    tools = _tools()
    tools.update(json.loads((W / "selected-truth-pending-capture-1158.json").read_text(encoding="utf-8"))["tools"])
    complete = tools["complete_srd_pending_action"]
    matches = [(rows, index, row) for rows in visual._lists(complete["effects"]) for index, row in enumerate(rows)
               if row.get("call") == "cast_spell"
               and row.get("params", {}).get("spell") == "{_pending_action.parameters.spell}"]
    assert len(matches) == 1
    rows, index, row = matches[0]
    if count == 0:
        del rows[index]
    else:
        rows.insert(index, deepcopy(row))
    with pytest.raises(ValueError, match="exactly one actual staged spell dispatcher"):
        visual.bind_persistent_spell_map({"tools": tools})


def test_new_effects_obey_dsl_expression_literal_and_foreach_limits():
    owners = {visual.BODY: visual._body_tool(), visual.REFRESH: visual._refresh_tool(), visual.ANCHOR: visual._anchor_tool()}
    expressions = []
    for owner in owners.values():
        for rows in visual._lists(owner["effects"]):
            for row in rows:
                if "for_each" in row:
                    assert re.fullmatch(r"_[A-Za-z0-9_]+", row["for_each"]["collection"])
                for value in (row.get("condition"), row.get("require", {}).get("condition"),
                              row.get("set", {}).get("value")):
                    if isinstance(value, str):
                        expressions.append(value)
    for expression in expressions:
        assert len(expression) <= 500, expression
        for literal in re.findall(r"'([^']*)'", expression):
            assert len(literal) <= 200, literal
