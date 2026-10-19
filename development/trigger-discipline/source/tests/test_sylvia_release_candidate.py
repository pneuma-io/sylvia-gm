"""Focused package construction checks; no provider calls or installed playtesting."""

import json
from pathlib import Path
from unittest.mock import patch

from sylvia_ruleset.builder import _read_package_draft, build_draft

ROOT = Path(__file__).resolve().parents[1]


def test_candidate_has_real_panel_layout_and_no_test_only_gameplay_tool(tmp_path):
    from sylvia_gm.application.package_library import validate_admitted_package_runtimes
    from sylvia_gm.application.package_materialization import (
        NormalizedPackageDraft,
        materialize_package_archive,
    )
    from sylvia_gm.application.schema_admission import validate_parameters
    from sylvia_gm.domain.package_library import parse_gameplay_support_bytes
    from sylvia_gm.infrastructure.package_archive import admit_package_archive

    # Ordinary package generation must not run the historical team-objective balance suite.
    with patch("balance_suite.specialties._execute_specialty_objective_scenario", side_effect=AssertionError("benchmark invoked")):
        build_draft(ROOT, draft_path=tmp_path / "candidate", allow_worktree_sources=True)
    descriptor, files = _read_package_draft(tmp_path / "candidate")
    runtime = json.loads(files["runtime/ruleset.yaml"])
    assert "exercise_specialty_counterfactual" not in runtime["tools"]
    assert "specialty_counterfactuals" not in runtime["definitions"]
    assert "handle_weapon" in {tool["name"] for tool in descriptor["tools"]}
    assert {"switch_weapon", "transfer_reserve_weapon", "drop_weapon", "pickup_weapon"} <= {tool["name"] for tool in descriptor["tools"]}
    assert not {"td_drop_held_weapon", "td_place_dropped_weapon"} & {tool["name"] for tool in descriptor["tools"]}
    inventory = next(tool for tool in descriptor["tools"] if tool["name"] == "handle_weapon")["input_schema"]["x-sylvia-gm-inventory"]
    assert inventory["format"] == "mechanics"
    assert inventory["surface_id"] == "operations.loadout"
    assert {"handle_weapon", "switch_weapon", "transfer_reserve_weapon", "drop_weapon", "pickup_weapon", "reload_weapon", "cycle_weapon"} == set(inventory["tools"])
    assert "exercise_specialty_counterfactual" not in {tool["name"] for tool in descriptor["tools"]}
    support = parse_gameplay_support_bytes(files["support/gameplay-resolution.json"])
    assert any(item.gameplay_resolution == "gm_adjudicated" for item in support.records)
    by_id = {item.identity: item for item in support.records}
    assert by_id["specialty.networker.entry.map_the_links"].gameplay_resolution == "engine_specialized"
    medicine = by_id["specialty.field_medic.entry.combat_medicine"]
    assert medicine.gameplay_resolution == "hybrid"
    assert "treat_casualty" in medicine.deterministic_part
    assert "issue_id" in medicine.gm_part
    for identity in ("tier_1.pivot", "tier_2.foothold", "tier_3.route_persistence"):
        boundary = by_id[f"specialty.networker.{identity}"]
        assert boundary.gameplay_resolution == "hybrid"
        assert boundary.deterministic_part and boundary.gm_part
    source = json.loads(files["provenance/source-index.json"])
    assert source["source_state"] == "worktree"
    assert source["source_commit_role"].startswith("base revision")
    layout = json.loads(files["ui/panel-layout.json"])
    assert layout["sidebar"]["first"]["panels"] == ["operations.map", "operations.network"]
    assert layout["sidebar"]["second"]["second"]["panels"] == ["operations.character", "operations.loadout"]
    surfaces = {surface["id"]: surface for surface in descriptor["ui_surfaces"]}
    assert surfaces["operations.party"]["actions"] == []
    assert surfaces["operations.character"]["renderer"] == "unit_list"
    assert runtime["definitions"]["authoring_defaults"]["cover_height_presets"] == [
        {"id": "quarter", "label": "Quarter", "height_m": 0.6},
        {"id": "half", "label": "Half", "height_m": 1.2},
        {"id": "full", "label": "Full", "height_m": 2.4},
    ]
    with (
        materialize_package_archive((NormalizedPackageDraft(descriptor, files, 4),)) as materialized,
        admit_package_archive(materialized.archive) as admitted,
    ):
        validate_admitted_package_runtimes(admitted)
        declaration = admitted.manifests[0].participant_artifacts[0]
        validate_parameters(json.loads(files["conformance/character.json"])["artifact"],
                            declaration.schema)


def test_counterfactual_tools_require_an_explicit_test_build(tmp_path):
    build_draft(ROOT, draft_path=tmp_path, include_test_tools=True, allow_worktree_sources=True)
    descriptor, files = _read_package_draft(tmp_path)
    assert "exercise_specialty_counterfactual" in {tool["name"] for tool in descriptor["tools"]}
    assert "exercise_specialty_counterfactual" in json.loads(files["runtime/ruleset.yaml"])["tools"]
