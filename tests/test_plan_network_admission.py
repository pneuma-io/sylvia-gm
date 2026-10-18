from __future__ import annotations

from copy import deepcopy

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.network_graph import project_network_graph
from sylvia_gm.application.plan_network_admission import validate_plan_networks
from sylvia_gm.application.plan_snapshot import validate_plan_snapshot
from sylvia_gm.builder._plan_snapshot_materializer import _rebind_plan_to_snapshot
from sylvia_gm.domain.authority import thaw_json_value
from sylvia_gm.domain.content_validation import ContentShapeError
from sylvia_gm.domain.package_library import SurfaceDeclaration
from tests.test_campaign_library_selection import seed_campaign_library
from tests.test_plan_spatial_admission import _snapshot_with_tactical_surface


def _graph():
    return {
        "graph_id": "dock", "revision": 0, "groups": {}, "selection": {},
        "nodes": {
            "console": {"label": "Dock console", "kind": "terminal", "visible": True,
                        "functions": {}, "physical_binding": {
                            "surface_id": "map", "entity_id": "device",
                        }},
            "gate": {"label": "Floodgate", "kind": "controller", "visible": False,
                     "functions": {"open": {"label": "Open", "enabled": True, "visible": False}}},
        },
        "links": {"cable": {"source": "console", "target": "gate", "visible": False}},
    }


def _fixture():
    payload = {"initial_state": {"mechanics": [{
        "namespace": "example", "key": "state", "value": {
            "graph": _graph(), "map": {"scene": {"entities": [{"id": "device"}]}},
        },
    }]}}
    surfaces = [SurfaceDeclaration(
        identity=name, capability=f"example.{name}", renderer=renderer,
        source={"kind": "state", "namespace": "example", "key": f"state.{field}"},
    ) for name, renderer, field in [
        ("network", "network_graph", "graph"), ("map", "tactical_grid", "map"),
    ]]
    return payload, surfaces, payload["initial_state"]["mechanics"][0]["value"]


def test_authored_hidden_graph_is_checked_without_revealing_or_changing_it():
    payload, surfaces, state = _fixture()
    before = deepcopy(payload)
    validate_plan_networks(payload, surfaces)
    assert payload == before
    projection = project_network_graph(state["graph"])
    assert [node["id"] for node in projection["nodes"]] == ["console"]
    assert not projection["links"]


@pytest.mark.parametrize("change, suffix", [
    (lambda g: g["links"]["cable"].update(target="missing"), ".links.cable.target"),
    (lambda g: g["links"]["cable"].update(source=[]), ".links.cable.source"),
    (lambda g: g["nodes"]["gate"].update(group_id="missing"), ".nodes.gate.group_id"),
    (lambda g: g["nodes"]["console"]["physical_binding"].update(surface_id="missing"),
     ".nodes.console.physical_binding.surface_id"),
    (lambda g: g["nodes"]["console"]["physical_binding"].update(entity_id="missing"),
     ".nodes.console.physical_binding.entity_id"),
    (lambda g: g["nodes"]["gate"].update(visible="false"), ".nodes.gate"),
    (lambda g: g["nodes"]["gate"]["functions"]["open"].update(enabled="yes"), ""),
    (lambda g: g["nodes"]["gate"].update(private_secret="NEVER_ECHO_THIS"), ""),
])
def test_malformed_or_dangling_graph_rejected_before_discovery(change, suffix):
    payload, surfaces, state = _fixture()
    change(state["graph"])
    with pytest.raises(ContentShapeError) as failure:
        validate_plan_networks(payload, surfaces)
    diagnostic = failure.value.diagnostics[0]
    assert diagnostic.path == "$.initial_state.mechanics[0].value.graph" + suffix
    assert diagnostic.code == "plan_network_graph_invalid"
    assert "NEVER_ECHO_THIS" not in str(failure.value)


def test_absent_runtime_map_or_network_remains_valid_but_explicit_null_graph_is_rejected():
    payload, surfaces, state = _fixture()
    del state["map"]
    validate_plan_networks(payload, surfaces)
    del state["graph"]
    validate_plan_networks(payload, surfaces)
    state["graph"] = None
    with pytest.raises(ContentShapeError) as failure:
        validate_plan_networks(payload, surfaces)
    assert "must be an object" in failure.value.diagnostics[0].message
    validate_plan_networks(payload, surfaces[1:])


def test_snapshot_admission_uses_graph_validation_in_the_normal_plan_path(tmp_path):
    with AuthorityOwner.open(tmp_path / "network.sqlite3", "plan-network-owner") as owner:
        candidate, _setting_id, saved, _characters = seed_campaign_library(owner, tmp_path)
        ruleset_id = saved.plan.payload["bindings"]["ruleset"]["id"]
        snapshot = _snapshot_with_tactical_surface(owner, candidate, {
            "kind": "state", "namespace": ruleset_id, "key": "state.graph",
        }, renderer="network_graph")
        payload = _rebind_plan_to_snapshot(snapshot, thaw_json_value(saved.plan.payload))
        graph = _graph()
        del graph["nodes"]["console"]["physical_binding"]
        payload["initial_state"]["mechanics"][0]["value"]["graph"] = graph
        validate_plan_snapshot(snapshot, payload, saved.plan.readable_markdown)
        graph["links"]["cable"]["target"] = "missing"
        with pytest.raises(ContentShapeError) as failure:
            validate_plan_snapshot(snapshot, payload, saved.plan.readable_markdown)
        assert failure.value.diagnostics[0].path.endswith(".graph.links.cable.target")
