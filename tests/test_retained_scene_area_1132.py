"""Owning DSL/public-map checks for approved retained round footprints and explicit knowledge."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from scripts import srd_lite_retained_area_map as areas
from scripts.srd_control_behaviors import _lasting
from scripts.srd_spell_environment import _environment_tool
from scripts.srd_timed_actor_defenses import _parameter, _set
from tests.test_retained_area_feedback_986 import (
    _execute,
    _formatter,
    _map_binding_runtime,
    _refresh,
    _source,
    _state,
)

from sylvia_gm.application.tactical_grid import project_coordinate_map
from sylvia_gm.domain.authority import thaw_json_value

SELECTED_SCENE_SPHERES = (
    ("insect_plague", "insects", "Insect Plague"),
    ("stinking_cloud", "stinking_cloud", "Stinking Cloud"),
)


def _sphere_state(spell, mode):
    """Use the admitted native Insect source dimensions, with a hidden synthetic caster."""
    state = _state(
        {
            "sphere": _source(
                spell,
                mode=mode,
                origin=[0, 0, 0],
                radius=20,
                caster="hidden-synthetic-caster",
                plane="material",
                sequence=1,
                save_dc=18,
                spell_level=5,
                expires_tick=200,
            )
        }
    )
    scene = state["spatial_scene"]
    scene["map"]["bounds"] = {"min": [-100, -100, -100], "max": [100, 100, 100]}
    scene["map"]["grid"]["origin"] = [-100, -100]
    scene["map"]["layers"] = [{"id": "floor", "min_elevation": -100, "max_elevation": 100}]
    return state


def _public(state):
    return thaw_json_value(
        project_coordinate_map({"scene": state["spatial_scene"], "enabled": True})
    )


@pytest.mark.parametrize("spell,mode,label", SELECTED_SCENE_SPHERES)
def test_selected_known_scene_sphere_projects_same_geometry_without_exposing_source(
    spell, mode, label
):
    state = _sphere_state(spell, mode)
    original = deepcopy(state["persistent_spell_areas"])
    updated = _refresh(state)
    assert updated["persistent_spell_areas"] == original
    assert updated["spatial_scene"]["zones"][0] == state["spatial_scene"]["zones"][0]
    public = _public(updated)
    assert len(public["zones"]) == 1
    zone = public["zones"][0]
    assert zone["shape"] == {"kind": "cylinder", "center": [0, 0, -20], "radius": 20, "height": 40}
    assert zone["display"] == {
        "label": label + " · spherical footprint",
        "kind": "spell_area",
        "state": "Active",
        "interactive": False,
        "height_label": "Spherical mechanical area; map shows XY footprint",
    }
    assert "hidden-synthetic-caster" not in str(public)
    assert "gm_private" not in str(public)
    saved = updated["spatial_scene"]["zones"][1]
    assert saved["metadata"]["overlay_area_id"] == "sphere"
    assert saved["metadata"]["overlay_source_sequence"] == original["sphere"]["sequence"]
    assert set(saved["metadata"]) == {
        areas.TAG,
        "overlay_area_id",
        "overlay_source_sequence",
        "player",
    }
    assert _refresh(updated) == updated


@pytest.mark.parametrize("spell,mode,label", SELECTED_SCENE_SPHERES)
def test_omitted_scene_sphere_knowledge_preserves_old_hidden_sources(spell, mode, label):
    state = _sphere_state(spell, mode)
    source = state["persistent_spell_areas"]["sphere"]
    source.pop("known_area")
    source.pop("map_id")
    source["label"] = label
    original = deepcopy(source)
    updated = _refresh(state)
    assert updated["persistent_spell_areas"]["sphere"] == original
    assert updated["spatial_scene"]["zones"] == state["spatial_scene"]["zones"]
    assert _public(updated)["zones"] == []


def test_insect_source_replacement_and_expiry_remove_only_the_old_owned_overlay():
    initial = _sphere_state("insect_plague", "insects")
    updated = _refresh(initial)
    old_id = _public(updated)["zones"][0]["id"]
    source = updated["persistent_spell_areas"]["sphere"]
    source.update(sequence=2, origin=[12, 6, 0], radius=10)
    replaced = _refresh(updated)
    public = _public(replaced)["zones"]
    assert len(public) == 1 and public[0]["id"] != old_id
    assert public[0]["shape"] == {
        "kind": "cylinder",
        "center": [12, 6, -10],
        "radius": 10,
        "height": 20,
    }
    assert replaced["spatial_scene"]["zones"][1]["metadata"]["overlay_source_sequence"] == 2
    replaced["lasting_tick"] = source["expires_tick"]
    expired = _refresh(replaced)
    assert expired["spatial_scene"]["zones"] == initial["spatial_scene"]["zones"]
    assert expired["persistent_spell_areas"] == replaced["persistent_spell_areas"]


@pytest.mark.parametrize(
    "spell,mode,label",
    (
        *SELECTED_SCENE_SPHERES,
        ("fog_cloud", "fog", "Fog Cloud"),
        ("sleet_storm", "sleet", "Sleet Storm"),
    ),
)
def test_actual_postcommit_known_stamp_projects_only_explicit_selected_intent(spell, mode, label):
    runtime = _map_binding_runtime()
    areas.bind_retained_area_map(runtime)
    stamp = runtime["tools"]["cast_spell_environment"]["effects"][-1]
    assert repr(spell) in stamp["condition"]
    assert not any(
        repr(excluded) in stamp["condition"]
        for excluded in ("cloudkill", "incendiary_cloud", "moonbeam", "silence")
    )
    for parameters in (
        {"spell": spell},
        {"spell": spell, "known_area": False},
        {"spell": spell, "known_area": True},
    ):
        state = _sphere_state(spell, mode)
        if spell == "sleet_storm":
            state["persistent_spell_areas"]["sphere"]["volume"] = {
                "kind": "cylinder",
                "center": [0, 0, -40],
                "radius": 20,
                "height": 40,
            }
        original = state["persistent_spell_areas"]["sphere"]
        original.pop("known_area")
        original.pop("map_id")
        original = deepcopy(original)
        tools = {
            "selected_postcommit_stamp": _environment_tool(
                "Execute only the existing postcommit public-knowledge stamp; no paid cast.",
                [_lasting(), _parameter("spell"), areas._parameter()],
                [_set("_environment_cast_id", "'sphere'"), deepcopy(stamp)],
            ),
            areas.REFRESH: runtime["tools"][areas.REFRESH],
            "format_srd_information_projection": _formatter(),
        }
        _, updated = _execute("selected_postcommit_stamp", tools, state, parameters)
        source = updated["persistent_spell_areas"]["sphere"]
        known = parameters.get("known_area", False)
        assert source == original | {"known_area": known, "map_id": "test-map"}
        public = _public(updated)["zones"]
        assert len(public) == int(known)
        if known:
            suffix = (
                " · source extent"
                if spell == "fog_cloud"
                else " · cylinder footprint"
                if spell == "sleet_storm"
                else " · spherical footprint"
            )
            assert public[0]["display"]["label"] == label + suffix


def test_known_fog_is_labelled_source_extent_without_claiming_or_revealing_clearances():
    state = _sphere_state("fog_cloud", "fog")
    state["persistent_spell_areas"]["sphere"]["fog_clearances"] = [
        {
            "volume": {"kind": "box", "min": [-5, -5, -5], "max": [5, 5, 5]},
            "water_source": "hidden-water-source",
        }
    ]
    original = deepcopy(state["persistent_spell_areas"])
    updated = _refresh(state)
    assert updated["persistent_spell_areas"] == original
    public = _public(updated)
    assert len(public["zones"]) == 1
    assert public["zones"][0]["shape"] == {
        "kind": "cylinder",
        "center": [0, 0, -20],
        "radius": 20,
        "height": 40,
    }
    assert public["zones"][0]["display"]["label"] == "Fog Cloud · source extent"
    assert public["zones"][0]["display"]["state"] == "Extent only"
    assert "cleared subregions are not shown" in public["zones"][0]["display"]["height_label"]
    assert "hidden-water-source" not in str(public)
    assert "fog_clearances" not in str(public)
    assert _refresh(updated) == updated


@pytest.mark.parametrize("bottom", (0, -40))
def test_known_sleet_uses_saved_cylinder_and_cleanup_preserves_unrelated_zones(bottom):
    state = _sphere_state("sleet_storm", "sleet")
    source = state["persistent_spell_areas"]["sphere"]
    source.update(
        volume={"kind": "cylinder", "center": [4, 8, bottom], "radius": 20, "height": 40},
        cylinder_min=bottom,
        cylinder_max=bottom + 40,
    )
    original = deepcopy(source)
    updated = _refresh(state)
    assert updated["persistent_spell_areas"]["sphere"] == original
    zone = _public(updated)["zones"][0]
    assert zone["shape"] == source["volume"]
    assert zone["display"]["label"] == "Sleet Storm · cylinder footprint"
    assert zone["display"]["height_label"] == "Saved cylinder; map shows XY footprint"
    updated["persistent_spell_areas"]["sphere"]["active"] = False
    assert _refresh(updated)["spatial_scene"]["zones"] == state["spatial_scene"]["zones"]


def _truth_state():
    state = _sphere_state("insect_plague", "insects")
    state["persistent_spell_areas"] = {
        "truth-cast-7": {
            "profile": "truth_zone",
            "active": True,
            "origin": [7, 11, 0],
            "plane": "material",
            "source_id": "truth-source-7",
            "cast_id": "truth-cast-7",
            "caster_state_id": "hidden-truth-caster",
            "expires_tick": 200,
            "truth_outcomes": [{"target": "hidden-truth-target", "success": False}],
            "known_area": True,
            "map_id": "test-map",
        }
    }
    state["persistent_spell_area_ids"] = ["truth-cast-7"]
    state["timed_condition_sources"] = {
        "truth-source-7": {
            "installed": True,
            "cast_id": "truth-cast-7",
            "dialogue_profile": "truth_zone",
            "expires_tick": 200,
            "truth_outcomes": [{"target": "hidden-truth-target", "roll": 9}],
        }
    }
    return state


def test_known_truth_area_uses_native_cast_identity_radius_and_hides_all_save_outcomes():
    state = _truth_state()
    original_areas = deepcopy(state["persistent_spell_areas"])
    original_sources = deepcopy(state["timed_condition_sources"])
    updated = _refresh(state)
    assert updated["persistent_spell_areas"] == original_areas
    assert updated["timed_condition_sources"] == original_sources
    public = _public(updated)
    assert len(public["zones"]) == 1
    zone = public["zones"][0]
    assert zone["id"] == "retained-truth:truth-cast-7:sphere-footprint"
    assert zone["shape"] == {"kind": "cylinder", "center": [7, 11, -15], "radius": 15, "height": 30}
    assert zone["display"]["label"] == "Zone of Truth · spherical footprint"
    assert "hidden-truth" not in str(public) and "truth_outcomes" not in str(public)
    metadata = updated["spatial_scene"]["zones"][1]["metadata"]
    assert metadata["overlay_source_cast_id"] == "truth-cast-7"
    assert "overlay_source_sequence" not in metadata
    assert _refresh(updated) == updated


@pytest.mark.parametrize("change", ("unknown", "other_map", "retired", "replaced", "expired"))
def test_truth_area_unknown_or_retired_source_removes_only_its_own_overlay(change):
    original = _truth_state()
    state = _refresh(original)
    area = state["persistent_spell_areas"]["truth-cast-7"]
    timed = state["timed_condition_sources"]["truth-source-7"]
    if change == "unknown":
        area.pop("known_area")
    elif change == "other_map":
        area["map_id"] = "another-map"
    elif change == "retired":
        timed["installed"] = False
    elif change == "replaced":
        timed["cast_id"] = "replacement-cast"
    else:
        state["lasting_tick"] = timed["expires_tick"]
    updated = _refresh(state)
    assert updated["spatial_scene"]["zones"] == original["spatial_scene"]["zones"]
    assert _public(updated)["zones"] == []


def _truth_binding_runtime():
    """Rebind exact selected compiled1096 roots, reversing only their prior map augmentation."""
    from scripts.srd_memory_spells import _memory_tools

    runtime = _map_binding_runtime()
    selected = json.loads(
        (Path(__file__).parent / "fixtures" / "srd_lite_truth_dispatch_1144.json").read_text(
            encoding="utf-8"
        )
    )
    assert selected["normal_package_admission"] is True
    assert selected["package_sha256"] == (
        "5ba6a30057e75cfee5703c28f9e3f459f5e98f7dc9531e46d4e53e9ac2f4245b"
    )
    assert "cast_spell_node" not in selected["tools"]
    runtime["tools"].update(deepcopy(selected["tools"]))
    runtime["tools"][areas.MEMORY_SETTLE] = _memory_tools()[areas.MEMORY_SETTLE]
    root = runtime["tools"]["cast_spell"]
    root["parameters"] = [p for p in root["parameters"] if p["name"] != areas.KNOWN]
    for rows in areas._lists(root["effects"]):
        for row in rows:
            setter = row.get("set", {})
            field = setter.get("field", "")
            if field == "_retained_cast_tail_choices" or field.endswith(".retained_cast_choices"):
                if isinstance(setter["value"][0], dict):
                    setter["value"][0].pop(areas.KNOWN, None)
                else:
                    assert setter["value"] == [areas.CHOICE_MERGE]
    tail = runtime["tools"]["finish_srd_retained_cast_tail"]
    tail["metadata"]["retained_spell_reactions_v1_choices"].remove(areas.KNOWN)
    for rows in areas._lists(tail["effects"]):
        rows[:] = [row for row in rows if row.get("set", {}).get("field") != "_map_cast_known"]
        for row in rows:
            if row.get("call") == "cast_spell_environment":
                row["params"].pop(areas.KNOWN, None)
    return runtime


def test_truth_known_fact_reaches_actual_memory_leaf_after_install_without_reading_outcomes():
    runtime = _truth_binding_runtime()
    original = deepcopy(runtime)
    areas.bind_retained_area_map(runtime)
    tools = runtime["tools"]
    leaf = tools[areas.MEMORY_CAST]
    assert leaf["effects"][:-1] == original["tools"][areas.MEMORY_CAST]["effects"]
    assert (
        tools[areas.MEMORY_SETTLE]["effects"][:-1]
        == original["tools"][areas.MEMORY_SETTLE]["effects"]
    )
    assert tools[areas.MEMORY_SETTLE]["effects"][-1]["call"] == areas.REFRESH
    dispatches = [
        row
        for rows in areas._lists(tools["cast_spell"]["effects"])
        for row in rows
        if row.get("call") == areas.MEMORY_CAST
    ]
    assert len(dispatches) == 1
    actual = next(
        row
        for rows in areas._lists(original["tools"]["cast_spell"]["effects"])
        for row in rows
        if row.get("call") == areas.MEMORY_CAST
    )
    assert dispatches[0] == actual | {"params": actual["params"] | {"known_area": "{known_area}"}}
    assert not any(
        row.get("call") == areas.MEMORY_CAST
        for rows in areas._lists(tools["finish_srd_retained_cast_tail"]["effects"])
        for row in rows
    )
    stamp = leaf["effects"][-1]
    for parameters in (
        {"spell": "zone_of_truth"},
        {"spell": "zone_of_truth", "known_area": False},
        {"spell": "zone_of_truth", "known_area": True},
    ):
        state = _truth_state()
        state["persistent_spell_areas"]["truth-cast-7"].pop("known_area")
        state["persistent_spell_areas"]["truth-cast-7"].pop("map_id")
        original_area = deepcopy(state["persistent_spell_areas"]["truth-cast-7"])
        probe_tools = {
            "truth_postcommit_stamp": _environment_tool(
                "Execute the real post-install knowledge stamp; no paid cast.",
                [_lasting(), _parameter("spell"), areas._parameter()],
                [
                    _set("_truth_area_id", "'truth-cast-7'"),
                    _set("_dialogue_installed", True),
                    deepcopy(stamp),
                ],
            ),
            areas.REFRESH: tools[areas.REFRESH],
            "format_srd_information_projection": _formatter(),
        }
        _, updated = _execute("truth_postcommit_stamp", probe_tools, state, parameters)
        known = parameters.get("known_area", False)
        assert updated["persistent_spell_areas"]["truth-cast-7"] == original_area | {
            "known_area": known,
            "map_id": "test-map",
        }
        assert len(_public(updated)["zones"]) == int(known)


def test_truth_dispatch_drift_fails_atomically_without_changing_installed_memory_owners():
    runtime = _truth_binding_runtime()
    for rows in areas._lists(runtime["tools"]["cast_spell"]["effects"]):
        rows[:] = [row for row in rows if row.get("call") != areas.MEMORY_CAST]
    original = deepcopy(runtime)
    with pytest.raises(ValueError, match="actual paid memory/dialogue"):
        areas.bind_retained_area_map(runtime)
    assert runtime == original
