from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import materialize_package_archive
from sylvia_gm.application.plan_snapshot import validate_plan_snapshot
from sylvia_gm.application.plan_spatial_admission import plan_spatial_warnings
from sylvia_gm.builder._package_snapshots import retained_package_drafts
from sylvia_gm.builder._plan_snapshot_materializer import _rebind_plan_to_snapshot
from sylvia_gm.domain.authority import PackageRole, thaw_json_value
from sylvia_gm.domain.content_validation import ContentShapeError
from sylvia_gm.domain.package_library import SurfaceDeclaration
from tests.test_campaign_library_selection import seed_campaign_library
from tests.test_tactical_grid_ui import _scene


def _snapshot_with_tactical_surface(owner, candidate, source, *, renderer="tactical_grid"):
    drafts = list(retained_package_drafts(owner, candidate.candidate_id, include_setting=True))
    primary_id = next(
        item.package_id
        for item in candidate.package_lock.entries
        if item.role is PackageRole.PRIMARY
    )
    for index, draft in enumerate(drafts):
        if draft.record["id"] != primary_id:
            continue
        record = thaw_json_value(draft.record)
        surface = record["ui_surfaces"][0]
        surface["renderer"] = renderer
        surface["source"] = source
        drafts[index] = replace(draft, record=record)
    with materialize_package_archive(tuple(drafts)) as materialized:
        return materialized.archive.read()


@pytest.mark.parametrize("source_form", ["object", "state"])
def test_snapshot_admission_checks_present_null_and_absent_tactical_scene(tmp_path, source_form):
    with AuthorityOwner.open(tmp_path / f"{source_form}.sqlite3", "plan-map-owner") as owner:
        candidate, _setting_id, saved, _characters = seed_campaign_library(owner, tmp_path)
        ruleset_id = saved.plan.payload["bindings"]["ruleset"]["id"]
        source = (
            {
                "kind": "object",
                "fields": {
                    "scene": {
                        "kind": "state",
                        "namespace": ruleset_id,
                        "key": "state.spatial_scene",
                    }
                },
            }
            if source_form == "object"
            else {
                "kind": "state",
                "namespace": ruleset_id,
                "key": "state.surface",
            }
        )
        snapshot = _snapshot_with_tactical_surface(owner, candidate, source)
        payload = thaw_json_value(saved.plan.payload)
        state = payload["initial_state"]["mechanics"][0]["value"]
        if source_form == "state":
            state["surface"] = {"scene": state.pop("spatial_scene")}
        payload = _rebind_plan_to_snapshot(snapshot, payload)

        validate_plan_snapshot(snapshot, payload, saved.plan.readable_markdown)

        malformed = copy.deepcopy(payload)
        value = malformed["initial_state"]["mechanics"][0]["value"]
        scene = value["spatial_scene"] if source_form == "object" else value["surface"]["scene"]
        scene["map"]["grid"]["origin"] = [999, 999]
        with pytest.raises(ContentShapeError) as rejected:
            validate_plan_snapshot(snapshot, malformed, saved.plan.readable_markdown)
        diagnostic = rejected.value.diagnostics[0]
        prefix = "spatial_scene" if source_form == "object" else "surface.scene"
        assert (diagnostic.file, diagnostic.phase, diagnostic.code) == (
            "plan.json",
            "content",
            "plan_spatial_scene_invalid",
        )
        assert diagnostic.path == f"$.initial_state.mechanics[0].value.{prefix}.map.grid.origin"

        explicit_null = copy.deepcopy(payload)
        value = explicit_null["initial_state"]["mechanics"][0]["value"]
        if source_form == "object":
            value["spatial_scene"] = None
            null_path = "spatial_scene"
        else:
            value["surface"]["scene"] = None
            null_path = "surface.scene"
        with pytest.raises(ContentShapeError) as rejected:
            validate_plan_snapshot(snapshot, explicit_null, saved.plan.readable_markdown)
        assert rejected.value.diagnostics[0].path == (
            f"$.initial_state.mechanics[0].value.{null_path}"
        )

        absent = copy.deepcopy(payload)
        value = absent["initial_state"]["mechanics"][0]["value"]
        if source_form == "object":
            del value["spatial_scene"]
        else:
            del value["surface"]["scene"]
        validate_plan_snapshot(snapshot, absent, saved.plan.readable_markdown)


def test_ruleset_without_tactical_surface_does_not_gain_a_map_requirement(tmp_path):
    with AuthorityOwner.open(tmp_path / "neutral.sqlite3", "neutral-owner") as owner:
        candidate, _setting_id, saved, _characters = seed_campaign_library(owner, tmp_path)
        drafts = retained_package_drafts(owner, candidate.candidate_id, include_setting=True)
        with materialize_package_archive(drafts) as materialized:
            snapshot = materialized.archive.read()
        payload = _rebind_plan_to_snapshot(snapshot, thaw_json_value(saved.plan.payload))
        payload["initial_state"]["mechanics"][0]["value"].pop("spatial_scene")
        validate_plan_snapshot(snapshot, payload, saved.plan.readable_markdown)


def test_draft_warns_on_offset_occupied_props_without_changing_intentional_geometry():
    scene = _scene()
    prop = scene["geometry"][0]
    prop.update(id="crate", volume={"kind": "box", "min": [2.4, 2.4, 0],
                                   "max": [3.6, 3.6, 1.2]}, occupies_cells=True)
    scene["geometry"] = [prop]
    payload = {"initial_state": {"mechanics": [{
        "namespace": "example", "key": "state", "value": {"scene": scene},
    }]}}
    surfaces = [SurfaceDeclaration(
        "map", "example.map", renderer="tactical_grid",
        source={"kind": "state", "namespace": "example", "key": "state"},
    )]
    before = copy.deepcopy(payload)
    warnings = plan_spatial_warnings(payload, surfaces)
    assert len(warnings) == 1
    assert warnings[0]["geometry"] == "crate"
    assert "reserves 4 grid cells" in warnings[0]["message"]
    assert payload == before
    prop["occupies_cells"] = False
    assert plan_spatial_warnings(payload, surfaces) == []
    prop["occupies_cells"] = True
    for upper in ([3, 3, 1.2], [4, 4, 1.2]):
        prop["volume"] = {"kind": "box", "min": [2, 2, 0], "max": upper}
        assert plan_spatial_warnings(payload, surfaces) == []
