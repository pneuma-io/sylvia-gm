"""Public contracts for persistent, privacy-safe network graph surfaces."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import cast

import pytest
import yaml

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.application.network_graph import (
    NetworkGraphError,
    bind_physical_network_surfaces,
    project_network_graph,
    resolve_network_action_parameters,
)
from sylvia_gm.application.tactical_grid import project_tactical_grid
from sylvia_gm.domain.authority import (
    ActionStatus,
    JsonValue,
    StateValue,
    WriterRole,
    thaw_json_value,
)
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import (
    canonical_json_bytes,
    content_root_for_record,
    manifest_from_record,
)


def _graph(*, revision: int = 4) -> dict[str, object]:
    return {
        "graph_id": "building-operations",
        "revision": revision,
        "groups": {
            "operations": {"label": "Operations", "visible": True},
            "black_site": {"label": "BLACK SITE", "visible": False},
        },
        "nodes": {
            "lobby_terminal": {
                "label": "Lobby terminal",
                "kind": "access_terminal",
                "group_id": "operations",
                "status": "online",
                "access": "local session",
                "control": "operator",
                "detection": "quiet",
                "functions": {
                    "unlock_door": {
                        "label": "Unlock security door",
                        "state": "available",
                        "enabled": True,
                        "visible": True,
                    },
                    "hidden_override": {
                        "label": "SECRET OVERRIDE",
                        "state": "ready",
                        "enabled": True,
                        "visible": False,
                    },
                },
                "physical_binding": {
                    "surface_id": "operations.map",
                    "entity_id": "terminal-device",
                },
                "visible": True,
            },
            "camera": {
                "label": "Loading-bay camera",
                "kind": "camera",
                "group_id": "operations",
                "status": "online",
                "functions": {},
                "visible": True,
            },
            "admin_core": {
                "label": "SECRET ADMIN CORE",
                "kind": "server",
                "group_id": "black_site",
                "functions": {},
                "physical_binding": {
                    "surface_id": "operations.map",
                    "entity_id": "secret-device",
                },
                "visible": False,
            },
            "buried_decoy": {
                "visible": False,
                "unvalidated_private_shape": {"label": "BURIED SECRET"},
            },
        },
        "links": {
            "terminal_camera": {
                "source": "lobby_terminal",
                "target": "camera",
                "label": "Operations LAN",
                "kind": "ethernet",
                "status": "online",
                "direction": "bidirectional",
                "visible": True,
            },
            "camera_admin": {
                "source": "camera",
                "target": "admin_core",
                "label": "SECRET UPLINK",
                "kind": "fiber",
                "status": "online",
                "direction": "directed",
                "visible": True,
            },
        },
        "selection": {
            "node": "lobby_terminal",
            "link": "camera_admin",
            "remote_function": {"private": "hidden_override"},
            "private_cursor": "admin_core",
        },
    }


def _scene(*, terminal_active: bool = True) -> dict[str, object]:
    return {
        "format": 1,
        "map": {
            "id": "ground-floor",
            "revision": 2,
            "bounds": {"min": [0, 0, 0], "max": [5, 5, 3]},
            "grid": {"cell_size": 1, "unit": "m", "origin": [0, 0]},
            "layers": [{"id": "floor", "min_elevation": 0, "max_elevation": 3}],
        },
        "geometry": [],
        "entities": [
            {
                "id": "terminal-device",
                "map_id": "ground-floor",
                "position": [1.5, 1.5, 0],
                "footprint": {"kind": "box", "half_extents": [0.4, 0.4]},
                "height": 1.2,
                "facing": [1, 0],
                "active": terminal_active,
                "movement": "passable",
                "visibility": "opaque",
                "metadata": {"player": {"label": "Lobby terminal", "kind": "device"}},
            }
        ],
        "zones": [],
        "terrain": [],
    }


def _surface() -> dict[str, object]:
    return {
        "id": "operations.network",
        "capability": "building.operate",
        "title": "Building network",
        "icon": "◇",
        "renderer": "network_graph",
        "source": {
            "kind": "state",
            "namespace": "synthetic.graph",
            "key": "state.network",
        },
        "group": "operations",
        "order": 1,
        "default_size": 480,
        "minimum_size": 320,
        "default_placement": "docked",
        "allowed_placements": ["docked", "window"],
        "actions": [
            {
                "id": "operate",
                "label": "Operate function",
                "intent": "operate_function",
                "params": {"mode": "normal"},
                "bindings": {
                    "node_id": "node",
                    "function": "remote_function",
                },
            }
        ],
    }


def _record() -> dict[str, object]:
    record: dict[str, object] = {
        "id": "synthetic.graph",
        "version": "1.0.0",
        "role": "primary",
        "metadata": {"label": "Generic graph conformance"},
        "provenance": {"publisher": "tests", "source_sha256": "a" * 64},
        "compatibility": {"host_contract": 1, "runtime_format": 1, "requires": []},
        "minimum_model_capability_tier": "sylvia.capability.t00",
        "dependencies": [],
        "capabilities": ["building.operate"],
        "tools": [
            {
                "name": "operate_function",
                "capability": "building.operate",
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
        ],
        "prompt_fragments": [],
        "ui_surfaces": [_surface()],
        "files": [],
    }
    record["content_sha256"] = content_root_for_record(record)
    return record


def _archive(*, actor_binding: bool = False) -> bytes:
    runtime_document = {
        "sylvia_gm_runtime": {
            "format": 1,
            "state_namespace": "synthetic.graph",
        },
        "tools": {
            "discover_archive": {
                "description": "Reveal one package-authored graph node and link.",
                "parameters": [],
                "effects": [
                    {
                        "set": {
                            "field": "state.network.nodes.admin_core.visible",
                            "value": "true",
                        }
                    },
                    {
                        "set": {
                            "field": "state.network.groups.black_site.visible",
                            "value": "true",
                        }
                    },
                    {
                        "increment": {
                            "field": "state.network.revision",
                            "amount": 1,
                        }
                    },
                ],
                "metadata": {"costs_action": True},
            },
            "operate_function": {
                "description": "Invoke one currently visible package-authored function.",
                "parameters": [
                    {"name": "node_id", "type": "string", "required": True},
                    {"name": "function", "type": "string", "required": True},
                    {
                        "name": "mode",
                        "type": "enum",
                        "enum_values": ["normal"],
                        "required": True,
                    },
                ],
                "effects": [
                    {"set": {"field": "state.last_node", "value": "node_id"}},
                    {"set": {"field": "state.last_function", "value": "function"}},
                    {
                        "set": {
                            "field": "state.network.nodes.lobby_terminal.control",
                            "value": "'remote'",
                        }
                    },
                    {
                        "increment": {
                            "field": "state.network.revision",
                            "amount": 1,
                        }
                    },
                ],
                "metadata": {"costs_action": True},
            },
        },
        "procedures": {},
        "definitions": {},
        "narrative": {},
    }
    if actor_binding:
        runtime_document["model_decision_scope"] = {
            name: {"namespace": "synthetic.graph", "key": f"state.{key}"}
            for name, key in {
                "decision_owner": "decision_owner", "active_side": "active_side",
                "eligible_actors": "eligible_actor_ids", "focused_actor": "focused_actor",
                "actor_entities": "entities",
            }.items()
        }
        operation = runtime_document["tools"]["operate_function"]
        operation["parameters"].append({"name": "actor", "type": "string", "required": True})
        operation["effects"].append({"set": {"field": "state.last_actor", "value": "actor"}})
        operation["metadata"]["player_controls"] = {"actor_parameter": "actor"}
    runtime_text = yaml.safe_dump(runtime_document, sort_keys=False)
    runtime = str(runtime_text).encode()

    surface = _surface()
    cast(list[object], surface["actions"]).insert(
        0,
        {
            "id": "discover",
            "label": "Discover archive",
            "intent": "discover_archive",
            "params": {},
        },
    )
    record = _record()
    record["ui_surfaces"] = [surface]
    tools = cast(list[object], record["tools"])
    tools[0] = {
        "name": "operate_function",
        "capability": "building.operate",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "function": {"type": "string"},
                "mode": {"type": "string", "enum": ["normal"]},
            },
            "required": ["node_id", "function", "mode"],
            "additionalProperties": False,
        },
        "state_predicates": [],
        "turn_statuses": ["open"],
        "writers": ["dm"],
    }
    tools.append(
        {
            "name": "discover_archive",
            "capability": "building.operate",
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            "state_predicates": [
                {
                    "namespace": "synthetic.graph",
                    "key": "state.network.nodes.admin_core.visible",
                    "equals": False,
                }
            ],
            "turn_statuses": ["open"],
            "writers": ["dm"],
        }
    )
    if actor_binding:
        surface["actions"][1]["bindings"]["actor"] = "actor"
        tools[0]["input_schema"]["properties"]["actor"] = {"type": "string"}
        tools[0]["input_schema"]["required"].append("actor")
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
        archive.writestr(
            "manifest.json",
            canonical_json_bytes({"format": 1, "packages": [record]}),
        )
        archive.writestr("packages/synthetic.graph/runtime/ruleset.yaml", runtime)
    return output.getvalue()


def test_manifest_admits_network_renderer_with_closed_binding_vocabulary() -> None:
    manifest = manifest_from_record(_record())
    assert manifest.ui_surfaces[0].renderer == "network_graph"

    invalid = _record()
    action = cast(
        dict[str, object],
        cast(list[object], cast(list[object], invalid["ui_surfaces"])[0]["actions"])[0],
    )
    cast(dict[str, object], action["bindings"])["target"] = "aim_point"
    invalid["content_sha256"] = content_root_for_record(invalid)
    with pytest.raises(ValueError, match="declared format"):
        manifest_from_record(invalid)


def test_projection_omits_hidden_topology_functions_and_unverified_physical_ids() -> None:
    projection = project_network_graph(_graph())
    payload = thaw_json_value(projection)
    encoded = json.dumps(payload, sort_keys=True)

    assert [item["id"] for item in payload["nodes"]] == ["camera", "lobby_terminal"]
    assert [item["id"] for item in payload["links"]] == ["terminal_camera"]
    assert [item["id"] for item in payload["groups"]] == ["operations"]
    terminal = next(item for item in payload["nodes"] if item["id"] == "lobby_terminal")
    assert [item["id"] for item in terminal["functions"]] == ["unlock_door"]
    assert terminal.get("physical_binding") is None
    assert payload["selection"] == {"node": "lobby_terminal"}
    for secret in (
        "SECRET ADMIN CORE",
        "SECRET UPLINK",
        "SECRET OVERRIDE",
        "admin_core",
        "secret-device",
        "black_site",
        "BURIED SECRET",
        "buried_decoy",
    ):
        assert secret not in encoded


def test_dynamic_selections_validate_current_visible_node_link_route_and_function() -> None:
    projection = project_network_graph(_graph())
    action = cast(dict[str, object], _surface()["actions"][0])
    parameters = resolve_network_action_parameters(
        action,
        {
            "graph_revision": 4,
            "node": "lobby_terminal",
            "remote_function": "unlock_door",
        },
        projection,
    )
    assert thaw_json_value(parameters) == {
        "mode": "normal",
        "node_id": "lobby_terminal",
        "function": "unlock_door",
    }

    route_action = {
        "id": "route",
        "params": {},
        "bindings": {"link_id": "link", "step": "route_step"},
    }
    route = {
        "graph_revision": 4,
        "link": "terminal_camera",
        "route_step": ["camera", "lobby_terminal", "terminal_camera"],
    }
    assert thaw_json_value(resolve_network_action_parameters(route_action, route, projection)) == {
        "link_id": "terminal_camera",
        "step": route["route_step"],
    }

    with pytest.raises(NetworkGraphError, match="stale"):
        resolve_network_action_parameters(
            action,
            {
                "graph_revision": 3,
                "node": "lobby_terminal",
                "remote_function": "unlock_door",
            },
            projection,
        )
    with pytest.raises(NetworkGraphError, match="remote_function"):
        resolve_network_action_parameters(
            action,
            {
                "graph_revision": 4,
                "node": "lobby_terminal",
                "remote_function": "hidden_override",
            },
            projection,
        )


def test_network_action_uses_current_controlled_actor_and_retains_it_after_reopen(
    tmp_path: Path,
) -> None:
    database = tmp_path / "network-actors.sqlite3"
    campaign_id = "network-actors"
    actor_id = "participant-admitted-hex"
    initial = {
        "network": _graph(), "last_actor": None, "last_node": None, "last_function": None,
        "decision_owner": "player", "active_side": "allies", "focused_actor": None,
        "eligible_actor_ids": [actor_id],
        "entities": {actor_id: {"name": "Hex", "ap": 6}, "hostile": {"name": "Hostile"}},
    }
    selection = {
        "graph_revision": 4, "actor": actor_id,
        "node": "lobby_terminal", "remote_function": "unlock_door",
    }
    with AuthorityOwner.open(database, "network-owner") as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(_archive(actor_binding=True)), install_id="network-actors",
        )
        owner.create_campaign(CampaignCreate(
            campaign_id=campaign_id, display_name="Network actor selection",
            writer=WriterRole.DM, package_lock=candidate.package_lock, turn_id="network-turn",
            initial_state=(StateValue("synthetic.graph", "state", cast(JsonValue, initial)),),
        ))
        projection = thaw_json_value(owner.campaign_panels(campaign_id))
        action = next(a for a in projection["surfaces"][0]["actions"] if a["id"] == "operate")
        assert action["available"] is True
        assert action["actor_parameter"] == "actor"
        assert [actor["id"] for actor in projection["player_control"]["actors"]] == [actor_id]
        for invalid_actor in ("hex", "hostile", "", None):
            with pytest.raises(CommandRejected):
                owner.execute_panel_action(
                    campaign_id=campaign_id, surface_id="operations.network",
                    panel_action_id="operate", operation_id=f"invalid-{invalid_actor}",
                    expected_revision=0, selection={**selection, "actor": invalid_actor},
                )
        assert owner.snapshot(campaign_id).revision == 0
        result = owner.execute_panel_action(
            campaign_id=campaign_id, surface_id="operations.network", panel_action_id="operate",
            operation_id="open-door", expected_revision=0, selection=selection,
        )
        assert result.status is ActionStatus.COMMITTED
        state = thaw_json_value(owner.snapshot(campaign_id).state["synthetic.graph.state"])
        assert state["last_actor"] == actor_id
        assert state["last_function"] == "unlock_door"
    with AuthorityOwner.open(database, "network-owner") as owner:
        state = thaw_json_value(owner.snapshot(campaign_id).state["synthetic.graph.state"])
        assert state["last_actor"] == actor_id
        assert owner.snapshot(campaign_id).revision == 1
        with pytest.raises(CommandRejected):
            owner.execute_panel_action(
                campaign_id=campaign_id, surface_id="operations.network", panel_action_id="operate",
                operation_id="stale-door", expected_revision=1, selection=selection,
            )


def test_cross_surface_binding_reuses_identity_and_disappears_with_device() -> None:
    tactical = thaw_json_value(project_tactical_grid({"scene": _scene()}))
    visible = {("operations.map", "terminal-device")}
    network = thaw_json_value(project_network_graph(_graph(), visible_physical_entities=visible))
    linked = bind_physical_network_surfaces(
        (
            {"id": "operations.map", "renderer": "tactical_grid", "data": tactical},
            {
                "id": "operations.network",
                "renderer": "network_graph",
                "data": network,
            },
        )
    )
    map_surface = thaw_json_value(linked[0])
    graph_surface = thaw_json_value(linked[1])
    terminal = next(
        item for item in map_surface["data"]["entities"] if item["id"] == "terminal-device"
    )
    assert terminal["network_bindings"] == [
        {"surface_id": "operations.network", "node_id": "lobby_terminal"}
    ]
    node = next(item for item in graph_surface["data"]["nodes"] if item["id"] == "lobby_terminal")
    assert node["physical_binding"] == {
        "surface_id": "operations.map",
        "entity_id": "terminal-device",
    }

    destroyed_tactical = thaw_json_value(
        project_tactical_grid({"scene": _scene(terminal_active=False)})
    )
    destroyed_network = thaw_json_value(project_network_graph(_graph()))
    destroyed = bind_physical_network_surfaces(
        (
            {
                "id": "operations.map",
                "renderer": "tactical_grid",
                "data": destroyed_tactical,
            },
            {
                "id": "operations.network",
                "renderer": "network_graph",
                "data": destroyed_network,
            },
        )
    )
    destroyed_map = thaw_json_value(destroyed[0])
    destroyed_graph = thaw_json_value(destroyed[1])
    destroyed_terminal = destroyed_map["data"]["entities"][0]
    destroyed_node = next(
        item for item in destroyed_graph["data"]["nodes"] if item["id"] == "lobby_terminal"
    )
    assert destroyed_terminal["active"] is False
    assert "network_bindings" not in destroyed_terminal
    assert "physical_binding" not in destroyed_node


def test_packaged_graph_discovers_operates_reopens_and_rejects_stale_selection(
    tmp_path: Path,
) -> None:
    database = tmp_path / "network-graph.sqlite3"
    campaign_id = "campaign-network-graph"
    owner_id = "owner-network-graph"
    initial = {"network": _graph(), "last_node": None, "last_function": None}

    with AuthorityOwner.open(database, owner_id) as owner:
        candidate = owner.install_package_archive(io.BytesIO(_archive()), install_id="graph")
        owner.create_campaign(
            CampaignCreate(
                campaign_id=campaign_id,
                display_name="Persistent generic graph",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id="turn-network-graph",
                initial_state=(StateValue("synthetic.graph", "state", cast(JsonValue, initial)),),
            )
        )
        before = thaw_json_value(owner.campaign_panels(campaign_id))
        graph = before["surfaces"][0]["data"]
        assert graph["graph_revision"] == 4
        assert [item["id"] for item in graph["nodes"]] == ["camera", "lobby_terminal"]

        discovered = owner.execute_panel_action(
            campaign_id=campaign_id,
            surface_id="operations.network",
            panel_action_id="discover",
            operation_id="discover-admin",
            expected_revision=0,
        )
        assert discovered.status is ActionStatus.COMMITTED
        after = thaw_json_value(owner.campaign_panels(campaign_id))
        graph = after["surfaces"][0]["data"]
        assert graph["graph_revision"] == 5
        assert [item["id"] for item in graph["nodes"]] == [
            "admin_core",
            "camera",
            "lobby_terminal",
        ]
        assert [item["id"] for item in graph["links"]] == [
            "camera_admin",
            "terminal_camera",
        ]

    with AuthorityOwner.open(database, owner_id) as reopened:
        persisted = thaw_json_value(reopened.campaign_panels(campaign_id))
        assert persisted["surfaces"][0]["data"] == graph
        operated = reopened.execute_panel_action(
            campaign_id=campaign_id,
            surface_id="operations.network",
            panel_action_id="operate",
            operation_id="operate-door",
            expected_revision=1,
            selection={
                "graph_revision": 5,
                "node": "lobby_terminal",
                "remote_function": "unlock_door",
            },
        )
        assert operated.status is ActionStatus.COMMITTED
        state = thaw_json_value(reopened.snapshot(campaign_id).state["synthetic.graph.state"])
        assert state["last_node"] == "lobby_terminal"
        assert state["last_function"] == "unlock_door"
        assert state["network"]["revision"] == 6

        with pytest.raises(CommandRejected) as rejected:
            reopened.execute_panel_action(
                campaign_id=campaign_id,
                surface_id="operations.network",
                panel_action_id="operate",
                operation_id="operate-stale-graph",
                expected_revision=2,
                selection={
                    "graph_revision": 5,
                    "node": "lobby_terminal",
                    "remote_function": "unlock_door",
                },
            )
        assert rejected.value.code == "panel_action_invalid"

    with AuthorityOwner.open(database, owner_id) as final_reopen:
        final_panels = thaw_json_value(final_reopen.campaign_panels(campaign_id))
        final_graph = final_panels["surfaces"][0]["data"]
        final_terminal = next(
            item for item in final_graph["nodes"] if item["id"] == "lobby_terminal"
        )
        assert final_graph["graph_revision"] == 6
        assert final_terminal["control"] == "remote"
