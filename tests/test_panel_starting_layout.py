"""Ruleset-authored layouts survive package admission and reach the real panel projection."""

import json
import subprocess
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.application.package_materialization import materialize_package_archive
from sylvia_gm.builder._ruleset_source import compile_ruleset_source
from sylvia_gm.domain.authority import StateValue, WriterRole, thaw_json_value
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.domain.panel_layout import PANEL_LAYOUT_PATH


def tabs(panel):
    return {"kind": "tabs", "id": f"tabs-{panel}", "panels": [panel], "active": panel}


def starting_layout():
    return {
        "version": 1,
        "sidebar": None,
        "hidden": [],
        "windows": [
            {
                "id": "tactics",
                "title": "Tactical play",
                "tree": {
                    "kind": "split",
                    "id": "main-split",
                    "axis": "row",
                    "ratio": 0.7,
                    "first": tabs("map"),
                    "second": {
                        "kind": "split",
                        "id": "party-split",
                        "axis": "column",
                        "ratio": 0.6,
                        "first": tabs("party"),
                        "second": tabs("inventory"),
                    },
                },
            }
        ],
    }


def compile_layout(layout, *, inventory_renderer="key_value"):
    source = {
        "format": 1,
        "id": "example.panel-rules",
        "version": "1.0.0",
        "label": "Panel rules",
        "publisher": "Example author",
        "mechanic_families": {},
        "public_actions": [],
        "private_mechanics": [],
        "definitions": {},
        "participant_artifacts": [],
        "operating_guide": None,
        "ui_surfaces": [
            {
                "id": name,
                "capability": "example.panel-rules.presentation",
                "title": name.title(),
                "renderer": inventory_renderer if name == "inventory" else "key_value",
                "source": {
                    "kind": "state",
                    "namespace": "example.panel-rules",
                    "key": name,
                },
                "allowed_placements": ["docked", "window"],
            }
            for name in ("map", "party", "inventory")
        ],
        "panel_layout": layout,
    }
    return compile_ruleset_source(yaml.safe_dump(source).encode("utf-8"))


def test_ruleset_layout_is_admitted_retained_and_projected_after_restart(tmp_path):
    authored = starting_layout()
    compiled = compile_layout(authored)
    assert compiled.required_engine_features == ("panel_workspaces.v1",)
    assert json.loads(compiled.draft.payloads[PANEL_LAYOUT_PATH]) == authored
    database = tmp_path / "layout.db"
    with AuthorityOwner.open(database, "seed") as owner:
        with materialize_package_archive((compiled.draft,)) as artifact:
            candidate = owner.install_package_archive(artifact.archive, install_id="layout")
        owner.create_campaign(
            CampaignCreate(
                campaign_id="layout-campaign",
                display_name="Layouts",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id="turn",
                initial_state=(StateValue("example.panel-rules", "party", {"count": 2}),),
            )
        )
        projection = thaw_json_value(owner.campaign_panels("layout-campaign"))
        assert {key: projection["layout"][key] for key in authored} == authored
        assert projection["layout"]["known"] == ["inventory", "map", "party"]
        assert {surface["id"] for surface in projection["surfaces"]} == {
            "map",
            "party",
            "inventory",
            "sylvia-inventory",
        }
    with AuthorityOwner.open(database, "reopen") as owner:
        reopened = thaw_json_value(owner.campaign_panels("layout-campaign"))
        assert reopened["layout"] == projection["layout"]

    # The desktop consumes the owner's exact JSON, including the layout's versioned shape.
    frontend = Path(__file__).resolve().parents[1] / "src-tauri/frontend/panel-workspace.js"
    result = subprocess.run(
        [
            "node",
            "-e",
            """
const workspace = require(process.argv[1]);
const projection = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const store = workspace.createStore(null);
store.select({campaignId: projection.campaign_id, rulesetId: projection.ruleset_id,
  surfaces: projection.surfaces, original: projection.layout});
store.change({type: 'move', panelId: 'map', destination: {windowId: 'sidebar'}});
process.stdout.write(JSON.stringify(store.preference('original')));
""",
            str(frontend),
        ],
        input=json.dumps(reopened),
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    restored = json.loads(result.stdout)
    assert {key: restored[key] for key in authored if key != "sidebar"} == {
        key: value for key, value in authored.items() if key != "sidebar"
    }
    assert restored["sidebar"] == tabs("sylvia-inventory")


@pytest.mark.parametrize("problem", ["foreign-panel", "duplicate-panel", "code", "bad-ratio"])
def test_invalid_layout_is_rejected_at_package_admission(problem):
    layout = starting_layout()
    compiled = compile_layout(layout)
    first = layout["windows"][0]["tree"]["first"]
    if problem == "foreign-panel":
        first.update(panels=["campaign-settings"], active="campaign-settings")
    elif problem == "duplicate-panel":
        layout["hidden"] = ["map"]
    elif problem == "code":
        first["script"] = "executeSomething()"
    else:
        layout["windows"][0]["tree"]["ratio"] = True
    with (
        pytest.raises(CommandRejected, match="admission"),
        materialize_package_archive((replace(
            compiled.draft,
            payloads={**compiled.draft.payloads, PANEL_LAYOUT_PATH: canonical_json_bytes(layout)},
        ),)),
    ):
        pytest.fail("Invalid layout was admitted")


def test_package_layout_requires_supported_placement_and_host_capability():
    compiled = compile_layout(starting_layout())
    for problem in ("placement", "host-feature"):
        record = deepcopy(compiled.draft.record)
        if problem == "placement":
            record["ui_surfaces"][0]["allowed_placements"] = ["docked"]
        else:
            record["compatibility"]["requires"] = []
        with (
            pytest.raises(CommandRejected, match="admission"),
            materialize_package_archive((replace(compiled.draft, record=record),)),
        ):
            pytest.fail("Unsupported layout was admitted")
