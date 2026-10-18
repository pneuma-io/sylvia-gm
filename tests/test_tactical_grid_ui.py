"""Public contracts for package-driven tactical-grid projection and intents."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import zipfile
from pathlib import Path
from typing import cast

import pytest
import yaml

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.application.tactical_grid import (
    TacticalGridError,
    project_tactical_grid,
    resolve_tactical_action_parameters,
)
from sylvia_gm.domain.authority import (
    ActionStatus,
    StateValue,
    WriterRole,
    owned_payload,
    thaw_json_value,
)
from sylvia_gm.domain.errors import CommandRejected, StaleRevision
from sylvia_gm.domain.package_library import (
    canonical_json_bytes,
    content_root_for_record,
    manifest_from_record,
)


def _public(label: str, kind: str, **extra: object) -> dict[str, object]:
    return {"player": {"label": label, "kind": kind, **extra}}


def _scene() -> dict[str, object]:
    return {
        "format": 1,
        "map": {
            "id": "training-floor",
            "revision": 7,
            "bounds": {"min": [0, 0, 0], "max": [8, 6, 3]},
            "grid": {"cell_size": 1, "unit": "m", "origin": [0, 0]},
            "layers": [{"id": "floor", "min_elevation": 0, "max_elevation": 3}],
        },
        "geometry": [
            {
                "id": "door-a",
                "map_id": "training-floor",
                "volume": {"kind": "box", "min": [3, 2, 0], "max": [3.2, 3, 2.2]},
                "active": True,
                "movement": "blocking",
                "visibility": "opaque",
                "metadata": _public("Security door", "door", state="closed"),
                "destroyed": {
                    "active": False,
                    "movement": "passable",
                    "visibility": "clear",
                },
            },
            {
                "id": "secret-wall",
                "map_id": "training-floor",
                "volume": {"kind": "box", "min": [5, 2, 0], "max": [5.2, 3, 3]},
                "active": True,
                "movement": "blocking",
                "visibility": "opaque",
                "metadata": {"gm_secret": "passage-behind-wall"},
                "destroyed": {
                    "active": False,
                    "movement": "passable",
                    "visibility": "clear",
                },
            },
        ],
        "entities": [
            {
                "id": "operator",
                "map_id": "training-floor",
                "position": [1.5, 2.5, 0],
                "footprint": {"kind": "box", "half_extents": [0.4, 0.4]},
                "height": 1.8,
                "facing": [1, 0],
                "active": True,
                "movement": "blocking",
                "visibility": "opaque",
                "metadata": _public("Operator", "unit", stance="standing", side="blue"),
            },
            {
                "id": "ambusher",
                "map_id": "training-floor",
                "position": [6.5, 2.5, 0],
                "footprint": {"kind": "circle", "radius": 0.4},
                "height": 1.8,
                "facing": [-1, 0],
                "active": True,
                "movement": "blocking",
                "visibility": "opaque",
                "metadata": {"gm_secret": "concealed-hostile"},
            },
        ],
        "zones": [
            {
                "id": "objective-zone",
                "map_id": "training-floor",
                "shape": {"kind": "box", "min": [6, 4, 0], "max": [8, 6, 2]},
                "metadata": _public("Extraction", "objective"),
            },
            {
                "id": "hidden-trigger",
                "map_id": "training-floor",
                "shape": {"kind": "box", "min": [2, 4, 0], "max": [3, 5, 2]},
                "metadata": {"gm_secret": "alarm"},
            },
        ],
        "terrain": [{"cell": [2, 2], "layer_id": "floor", "cost": 2}],
    }


def _surface(renderer: str = "tactical_grid") -> dict[str, object]:
    return {
        "id": "operations.map",
        "capability": "operations.move",
        "title": "Operations floor",
        "icon": "▦",
        "renderer": renderer,
        "source": {
            "kind": "object",
            "fields": {
                "scene": {
                    "kind": "state",
                    "namespace": "synthetic.coordinate",
                    "key": "state.spatial_scene",
                },
                "objectives": {
                    "kind": "state",
                    "namespace": "synthetic.coordinate",
                    "key": "state.objectives",
                },
                "selection": {
                    "kind": "state",
                    "namespace": "synthetic.coordinate",
                    "key": "state.selection",
                },
                "preview": {
                    "kind": "state",
                    "namespace": "synthetic.coordinate",
                    "key": "state.preview",
                },
            },
        },
        "group": None,
        "order": 0,
        "default_size": 480,
        "minimum_size": 320,
        "default_placement": "docked",
        "allowed_placements": ["docked", "window"],
        "actions": [
            {
                "id": "move",
                "label": "Move",
                "intent": "move_unit",
                "params": {"mode": "careful"},
                "bindings": {
                    "actor": "actor",
                    "destination": "destination",
                    "path": "path",
                },
            }
        ],
    }


def _record(surface: dict[str, object]) -> dict[str, object]:
    tool = {
        "name": "move_unit",
        "capability": "operations.move",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": True,
        },
        "state_predicates": [],
        "turn_statuses": ["open"],
        "writers": ["dm"],
    }
    record: dict[str, object] = {
        "id": "synthetic.coordinate",
        "version": "1.0.0",
        "role": "primary",
        "metadata": {"label": "Coordinate conformance"},
        "provenance": {"publisher": "tests", "source_sha256": "a" * 64},
        "compatibility": {
            "host_contract": 1,
            "runtime_format": 1,
            "requires": ["tactical_grid.v1"],
        },
        "minimum_model_capability_tier": "sylvia.capability.t00",
        "dependencies": [],
        "capabilities": ["operations.move"],
        "tools": [tool],
        "prompt_fragments": [],
        "ui_surfaces": [surface],
        "files": [],
    }
    record["content_sha256"] = content_root_for_record(record)
    return record


def _installed_archive(*, path_binding: bool = False) -> bytes:
    runtime = yaml.safe_dump(
        {
            "sylvia_gm_runtime": {
                "format": 1,
                "state_namespace": "synthetic.coordinate",
            },
            "tools": {
                "move_unit": {
                    "description": "Commit one package-defined movement intent.",
                    "parameters": [
                        {"name": "actor", "type": "string", "required": True},
                        {
                            "name": "destination",
                            "type": "array",
                            "required": True,
                            "items": {"type": "integer"},
                            "min_items": 2,
                            "max_items": 2,
                        },
                        {
                            "name": "mode",
                            "type": "enum",
                            "enum_values": ["careful"],
                            "required": True,
                        },
                    ],
                    "effects": [
                        {"set": {"field": "state.last_actor", "value": "actor"}},
                        {
                            "set": {
                                "field": "state.last_destination",
                                "value": "destination",
                            }
                        },
                        {
                            "set": {
                                "field": "state.preview",
                                "value": "definitions.committed_preview",
                            }
                        },
                    ],
                    "metadata": {"costs_action": True},
                }
            },
            "procedures": {},
            "definitions": {
                "committed_preview": {
                    "map_revision": 7,
                    "path_cost": 3,
                    "los": True,
                    "exposure": 1,
                    "crossed_zones": [],
                }
            },
            "narrative": {},
        },
        sort_keys=False,
    ).encode()
    surface = _surface()
    cast(dict[str, object], cast(list[object], surface["actions"])[0])["bindings"] = {
        "actor": "actor",
        "destination": "destination",
    }
    record = _record(surface)
    cast(list[object], record["tools"])[0] = {
        "name": "move_unit",
        "capability": "operations.move",
        "input_schema": {
            "type": "object",
            "properties": {
                "actor": {"type": "string"},
                "destination": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 2,
                    "maxItems": 2,
                },
                "mode": {"type": "string", "enum": ["careful"]},
            },
            "required": ["actor", "destination", "mode"],
            "additionalProperties": False,
        },
        "state_predicates": [],
        "turn_statuses": ["open"],
        "writers": ["dm"],
    }
    if path_binding:
        runtime_record = yaml.safe_load(runtime)
        path_items = {"type": "array", "items": {"type": "integer"}}
        runtime_record["tools"]["move_unit"]["parameters"].append({
            "name": "path", "type": "array", "required": True, "items": path_items,
        })
        runtime = yaml.safe_dump(runtime_record, sort_keys=False).encode()
        record["tools"][0]["input_schema"]["properties"]["path"] = {
            "type": "array", "items": path_items,
        }
        record["tools"][0]["input_schema"]["required"].append("path")
        record["ui_surfaces"][0]["actions"][0]["bindings"]["path"] = "path"
    record["files"] = [
        {
            "path": "runtime/ruleset.yaml",
            "bytes": len(runtime),
            "sha256": hashlib.sha256(runtime).hexdigest(),
        }
    ]
    record["content_sha256"] = content_root_for_record(record)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", canonical_json_bytes({"format": 1, "packages": [record]}))
        archive.writestr("packages/synthetic.coordinate/runtime/ruleset.yaml", runtime)
    return output.getvalue()


def test_manifest_admits_builtin_tactical_grid_and_closed_selection_bindings() -> None:
    manifest = manifest_from_record(_record(_surface()), format_version=1)

    surface = manifest.ui_surfaces[0]
    assert surface.renderer == "tactical_grid"
    assert surface.actions[0]["bindings"] == {
        "actor": "actor",
        "destination": "destination",
        "path": "path",
    }


def test_manifest_admits_declared_spatial_capabilities_and_rejects_unknown_features() -> None:
    record = _record(_surface())
    compatibility = cast(dict[str, object], record["compatibility"])
    compatibility["requires"] = [
        "spatial_clearance.v1",
        "spatial_mutation.v1",
        "spatial_path_moving_entity.v1",
        "tactical_grid.v1",
    ]
    record["content_sha256"] = content_root_for_record(record)

    manifest_from_record(record, format_version=1)

    compatibility["requires"] = ["spatial.future.v1"]
    record["content_sha256"] = content_root_for_record(record)
    with pytest.raises(ValueError, match="Package engine feature is unsupported"):
        manifest_from_record(record, format_version=1)


@pytest.mark.parametrize("binding", ("script", "hidden_entity", "raw_state"))
def test_manifest_rejects_unbounded_dynamic_selection_sources(binding: str) -> None:
    surface = _surface()
    cast(dict[str, object], cast(list[object], surface["actions"])[0])["bindings"] = {
        "destination": binding
    }

    with pytest.raises(ValueError, match="selection binding|Content fields do not match"):
        manifest_from_record(_record(surface), format_version=1)


def test_projection_omits_hidden_objects_and_private_metadata_by_construction() -> None:
    projected = project_tactical_grid(
        {
            "scene": _scene(),
            "objectives": [
                {
                    "id": "extract",
                    "label": "Reach extraction",
                    "status": "active",
                    "position": [7, 5, 0],
                    "visible": True,
                },
                {
                    "id": "secret",
                    "label": "Hidden reinforcement trigger",
                    "status": "active",
                    "position": [2, 5, 0],
                    "visible": False,
                },
            ],
            "selection": {},
            "preview": {
                "map_revision": 7,
                "path_cost": 5,
                "los": False,
                "exposure": 0.25,
                "crossed_zones": ["objective-zone"],
            },
        }
    )

    assert projected["map_revision"] == 7
    assert [item["id"] for item in projected["geometry"]] == ["door-a"]
    assert [item["id"] for item in projected["entities"]] == ["operator"]
    assert [item["id"] for item in projected["zones"]] == ["objective-zone"]
    assert [item["id"] for item in projected["objectives"]] == ["extract"]
    assert projected["preview"]["authoritative"] is False
    serialized = json.dumps(thaw_json_value(projected), sort_keys=True)
    for private_value in (
        "secret-wall",
        "passage-behind-wall",
        "ambusher",
        "concealed-hostile",
        "hidden-trigger",
        "alarm",
        "Hidden reinforcement trigger",
    ):
        assert private_value not in serialized


def test_dynamic_intent_binds_only_declared_selections_and_current_map_revision() -> None:
    action = cast(dict[str, object], cast(list[object], _surface()["actions"])[0])
    projection = project_tactical_grid(
        {"scene": _scene(), "objectives": [], "selection": {}, "preview": None}
    )

    parameters = resolve_tactical_action_parameters(
        action,
        {
            "actor": "operator",
            "destination": [4, 4],
            "map_revision": 7,
        },
        projection,
        authoritative_scene=_scene(),
    )

    assert parameters == {
        "mode": "careful",
        "actor": "operator",
        "destination": [4, 4],
        "path": [[1, 2], [2, 3], [3, 3], [4, 4]],
    }


def test_prepared_zone_binding_accepts_only_current_public_map_zones() -> None:
    projection = project_tactical_grid({"scene": _scene()})
    action = {"id": "watch", "params": {}, "bindings": {"actor": "actor", "zone_id": "zone"}}
    selected = {"actor": "operator", "zone": "objective-zone", "map_revision": 7}
    assert resolve_tactical_action_parameters(action, selected, projection) == {
        "actor": "operator", "zone_id": "objective-zone",
    }
    for invalid in ({"zone": "hidden-zone"}, {"map_revision": 6}, {"zone": "missing-zone"}):
        with pytest.raises(TacticalGridError):
            resolve_tactical_action_parameters(action, {**selected, **invalid}, projection)


def test_device_binding_uses_a_visible_entity_without_granting_actor_control() -> None:
    projection = project_tactical_grid({"scene": _scene()})
    action = {"id": "operate", "params": {}, "bindings": {"drone": "device"}}
    assert resolve_tactical_action_parameters(
        action, {"device": "operator", "map_revision": 7}, projection,
    ) == {"drone": "operator"}
    with pytest.raises(TacticalGridError):
        resolve_tactical_action_parameters(
            action, {"device": "hidden-device", "map_revision": 7}, projection,
        )


def test_owner_tactical_path_excludes_the_mover_but_keeps_other_actors_blocking() -> None:
    action = cast(dict[str, object], cast(list[object], _surface()["actions"])[0])
    projection = project_tactical_grid(
        {"scene": _scene(), "objectives": [], "selection": {}, "preview": None}
    )

    parameters = resolve_tactical_action_parameters(
        action,
        {"actor": "operator", "destination": [7, 2], "map_revision": 7},
        projection,
        authoritative_scene=_scene(),
    )

    assert parameters["path"][0] == (1, 2)
    assert parameters["path"][-1] == (7, 2)
    assert (6, 2) not in parameters["path"]


@pytest.mark.parametrize(
    ("selection", "message"),
    (
        (
            {
                "actor": "operator",
                "destination": [4, 4],
                "path": [[1, 2], [4, 4]],
                "map_revision": 6,
            },
            "stale",
        ),
        (
            {
                "actor": "operator",
                "destination": [4, 4],
                "path": [[1, 2], [4, 4]],
                "hidden_entity": "ambusher",
                "map_revision": 7,
            },
            "undeclared",
        ),
    ),
)
def test_dynamic_intent_rejects_stale_or_undeclared_renderer_input(
    selection: dict[str, object], message: str
) -> None:
    action = cast(dict[str, object], cast(list[object], _surface()["actions"])[0])
    projection = project_tactical_grid(
        {"scene": _scene(), "objectives": [], "selection": {}, "preview": None}
    )

    with pytest.raises(TacticalGridError, match=message):
        resolve_tactical_action_parameters(action, selection, projection)


def test_projection_is_deterministic_and_bounded() -> None:
    raw = {"scene": _scene(), "objectives": [], "selection": {}, "preview": None}
    first = project_tactical_grid(raw)
    second = project_tactical_grid(raw)

    assert first == second
    assert (
        hashlib.sha256(
            json.dumps(thaw_json_value(first), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        == hashlib.sha256(
            json.dumps(thaw_json_value(second), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def test_installed_tactical_surface_commits_refreshes_reopens_and_rejects_stale_input(
    tmp_path: Path,
) -> None:
    database = tmp_path / "tactical-grid.sqlite3"
    campaign_id = "campaign-tactical-grid"
    owner_id = "owner-tactical-grid"
    state = {
        "spatial_scene": _scene(),
        "objectives": [],
        "selection": {},
        "preview": {
            "map_revision": 7,
            "path_cost": 999,
            "los": False,
            "exposure": 0,
            "crossed_zones": ["objective-zone"],
        },
        "last_actor": None,
        "last_destination": None,
    }

    with AuthorityOwner.open(database, owner_id) as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(_installed_archive()), install_id="install-tactical-grid"
        )
        owner.create_campaign(
            CampaignCreate(
                campaign_id=campaign_id,
                display_name="Tactical grid",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id="turn-tactical-grid",
                initial_state=(StateValue("synthetic.coordinate", "state", state),),
            )
        )

        before = owner.campaign_panels(campaign_id)
        assert before["ruleset_id"] == "synthetic.coordinate"
        surface = cast(dict[str, object], before["surfaces"][0])
        assert surface["renderer"] == "tactical_grid"
        assert surface["data"]["preview"]["path_cost"] == 999
        assert "ambusher" not in json.dumps(thaw_json_value(before))

        receipt = owner.execute_panel_action(
            campaign_id=campaign_id,
            surface_id="operations.map",
            panel_action_id="move",
            operation_id="panel-move-1",
            expected_revision=0,
            selection=cast(
                dict[str, object],
                owned_payload({"actor": "operator", "destination": [4, 4], "map_revision": 7}),
            ),
        )
        assert receipt.status is ActionStatus.COMMITTED
        after = owner.campaign_panels(campaign_id)
        assert after["revision"] == 1
        assert after["surfaces"][0]["data"]["preview"]["path_cost"] == 3
        canonical = owner.snapshot(campaign_id).state["synthetic.coordinate.state"]
        assert canonical["last_actor"] == "operator"
        assert canonical["last_destination"] == (4, 4)

        with pytest.raises(StaleRevision):
            owner.execute_panel_action(
                campaign_id=campaign_id,
                surface_id="operations.map",
                panel_action_id="move",
                operation_id="panel-move-stale",
                expected_revision=0,
                selection=owned_payload(
                    {"actor": "operator", "destination": [5, 4], "map_revision": 7}
                ),
            )
        with pytest.raises(CommandRejected, match="selection"):
            owner.execute_panel_action(
                campaign_id=campaign_id,
                surface_id="operations.map",
                panel_action_id="move",
                operation_id="panel-move-hidden",
                expected_revision=1,
                selection=owned_payload(
                    {"actor": "ambusher", "destination": [5, 4], "map_revision": 7}
                ),
            )
        assert owner.snapshot(campaign_id).revision == 1

    with AuthorityOwner.open(database, owner_id) as reopened:
        panels = reopened.campaign_panels(campaign_id)
        assert panels["revision"] == 1
        assert panels["surfaces"][0]["data"]["preview"]["path_cost"] == 3
        assert "ambusher" not in json.dumps(thaw_json_value(panels))


def test_invalid_saved_tactical_scene_reaches_frontend_as_public_unavailable_panel(
    tmp_path: Path,
) -> None:
    scene = _scene()
    scene["geometry"][0]["center"] = "secret-geometry-value"
    state = {"spatial_scene": scene, "objectives": [], "selection": {}, "preview": None}
    campaign_id = "campaign-invalid-map"
    with AuthorityOwner.open(tmp_path / "invalid-map.sqlite3", "owner-invalid-map") as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(_installed_archive()), install_id="install-invalid-map",
        )
        owner.create_campaign(CampaignCreate(
            campaign_id=campaign_id,
            display_name="Invalid saved map",
            writer=WriterRole.DM,
            package_lock=candidate.package_lock,
            turn_id="turn-invalid-map",
            initial_state=(StateValue("synthetic.coordinate", "state", state),),
        ))
        before = owner.snapshot(campaign_id)
        projection = thaw_json_value(owner.campaign_panels(campaign_id))
        after = owner.snapshot(campaign_id)

    assert {surface["id"] for surface in projection["surfaces"]} == {
        "operations.map", "sylvia-inventory",
    }
    surface = projection["surfaces"][0]
    assert surface["id"] == "operations.map"
    assert surface["renderer"] == "tactical_grid"
    assert surface["title"] == "Operations floor"
    assert surface["allowed_placements"] == ["docked", "window"]
    assert surface["actions"] == []
    reason = "The saved map data is invalid. This campaign needs a corrected map."
    assert surface["data"] == {"unavailable_reason": reason}
    assert before.revision == after.revision
    assert before.state == after.state
    serialized = json.dumps(projection)
    for private_value in ("secret-geometry-value", "secret-wall", "ambusher"):
        assert private_value not in serialized

    frontend = Path(__file__).resolve().parents[1] / "src-tauri" / "frontend" / "app.js"
    script = r"""
const fs = require('node:fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
const renderer = source.slice(source.indexOf('  function renderTacticalGridPanel('),
  source.indexOf('  function updateTacticalSelection('));
const escapeStart = source.indexOf('  function escapeHtml(');
const escapeEnd = source.indexOf('\n  }', escapeStart) + '\n  }'.length;
const escapeSource = source.slice(escapeStart, escapeEnd);
const render = new Function('data', 'surfaceId',
  `${escapeSource}\n${renderer}\nreturn renderTacticalGridPanel(data, surfaceId);`);
const surface = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(render(surface.data, surface.id));
"""
    rendered = subprocess.run(
        ["node", "-e", script, str(frontend)],
        input=json.dumps(surface), capture_output=True, text=True, check=True,
    ).stdout
    assert reason in rendered
    assert 'role="status"' in rendered
    assert "tactical-world" not in rendered
    assert "Tactical map is unavailable or too large" not in rendered


@pytest.mark.parametrize("state", ({}, {"spatial_scene": None}))
def test_optional_unauthored_tactical_scene_remains_absent(
    tmp_path: Path, state: dict[str, object],
) -> None:
    with AuthorityOwner.open(tmp_path / "optional-map.sqlite3", "owner-optional-map") as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(_installed_archive()), install_id="install-optional-map",
        )
        owner.create_campaign(CampaignCreate(
            campaign_id="campaign-optional-map",
            display_name="Optional map",
            writer=WriterRole.DM,
            package_lock=candidate.package_lock,
            turn_id="turn-optional-map",
            initial_state=(StateValue("synthetic.coordinate", "state", state),),
        ))
        panels = owner.campaign_panels("campaign-optional-map")["surfaces"]
        assert {surface["id"] for surface in panels} == {"sylvia-inventory"}
