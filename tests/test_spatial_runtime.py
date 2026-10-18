"""Declarative package runtime integration for universal spatial facts."""

from __future__ import annotations

import hashlib
import io
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest
import yaml

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
from sylvia_gm.application.package_runtime import (
    PackageRuntimeError,
    materialize_package_mechanic,
    validate_package_runtime,
)
from sylvia_gm.domain.authority import (
    ActionStatus,
    MechanicContext,
    StateValue,
    WriterRole,
    owned_payload,
    thaw_json_value,
)
from sylvia_gm.domain.package_library import (
    ToolDeclaration,
    canonical_json_bytes,
    content_root_for_record,
)

_PACKAGE_ID = "sylvia-gm.conformance.spatial-runtime"


def _geometry(item_id: str, lower: list[float], upper: list[float]) -> dict[str, object]:
    return {
        "id": item_id,
        "map_id": "runtime-map",
        "volume": {"kind": "box", "min": lower, "max": upper},
        "active": True,
        "movement": "blocking",
        "visibility": "opaque",
        "metadata": {"source_ref": item_id},
        "destroyed": {
            "active": False,
            "movement": "passable",
            "visibility": "clear",
        },
    }


def _entity(
    item_id: str = "subject",
    position: list[float] | None = None,
) -> dict[str, object]:
    return {
        "id": item_id,
        "map_id": "runtime-map",
        "position": position or [8, 3, 0],
        "footprint": {"kind": "box", "half_extents": [0.4, 0.4]},
        "height": 1.8,
        "facing": [1, 0],
        "active": True,
        "movement": "blocking",
        "visibility": "opaque",
        "metadata": {"participant_ref": item_id},
    }


def _scene(
    *,
    revision: int = 0,
    active: bool = True,
    entities: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    geometry = [
        _geometry("near-surface", [3, 2, 0], [3.2, 4, 3]),
        _geometry("far-surface", [6, 2, 0], [6.2, 4, 3]),
    ]
    if not active:
        for item in geometry:
            item["active"] = False
    return {
        "format": 1,
        "map": {
            "id": "runtime-map",
            "revision": revision,
            "bounds": {"min": [0, 0, 0], "max": [12, 8, 4]},
            "grid": {"cell_size": 1, "unit": "m", "origin": [0, 0]},
            "layers": [{"id": "floor", "min_elevation": 0, "max_elevation": 4}],
        },
        "geometry": geometry,
        "entities": [_entity()] if entities is None else entities,
        "zones": [
            {
                "id": "observation-zone",
                "map_id": "runtime-map",
                "shape": {
                    "kind": "cylinder",
                    "center": [9, 3, 0],
                    "radius": 1,
                    "height": 3,
                },
                "metadata": {"zone_ref": "observation-zone"},
            }
        ],
        "terrain": [],
    }


def _query(operation: str) -> dict[str, object]:
    if operation == "ray":
        return {
            "map_id": "runtime-map",
            "origin": [1, 3, 1],
            "destination": [10, 3, 1],
            "include": ["geometry", "entities"],
            "max_intersections": 16,
        }
    if operation == "exposure":
        return {
            "map_id": "runtime-map",
            "observer": {"point": [1, 3, 1.6]},
            "target": {"entity_id": "subject"},
            "samples": {"horizontal": 3, "vertical": 3},
            "include": ["geometry", "entities"],
        }
    if operation == "path":
        return {
            "map_id": "runtime-map",
            "start": [0, 0],
            "goal": [10, 6],
            "allow_diagonal": True,
            "corner_policy": "forbid_cutting",
            "include_entities": True,
            "max_steps": 256,
        }
    if operation == "sweep":
        return {
            "map_id": "runtime-map", "moving_entity_id": "subject",
            "destination": [1, 3, 0], "include_entities": True,
        }
    if operation == "crossed_zones":
        return {
            "map_id": "runtime-map",
            "path": [[1, 3, 1], [11, 3, 1]],
            "max_crossings": 16,
        }
    return {
        "map_id": "runtime-map",
        "shape": {"kind": "box", "min": [1, 1, 0], "max": [10, 7, 3]},
        "include": ["geometry", "entities", "zones"],
        "max_results": 32,
    }


def _effect(
    operation: str,
    *,
    store_as: str,
    snapshot: str | None = None,
) -> dict[str, object]:
    body: dict[str, object] = {"scene": "state.spatial_scene", "operation": operation}
    if operation != "snapshot":
        body["query"] = _query(operation)
    if snapshot is not None:
        body["snapshot"] = snapshot
    return {"spatial_query": body, "store_as": store_as}


def _mutation_effect(*, store_as: str = "_mutation") -> dict[str, object]:
    return {
        "spatial_mutation": {
            "scene": "state.spatial_scene",
            "map_id": "runtime-map",
            "expected_revision": 0,
            "target": {"kind": "geometry", "id": "near-surface"},
            "patch": {"active": False, "movement": "passable", "visibility": "clear"},
        },
        "store_as": store_as,
    }


def _runtime(
    effects: list[dict[str, object]],
    *,
    procedures: dict[str, object] | None = None,
    definitions: dict[str, object] | None = None,
) -> bytes:
    value = {
        "sylvia_gm_runtime": {
            "format": 1,
            "state_namespace": _PACKAGE_ID,
        },
        "tools": {
            "inspect_scene": {
                "description": "Inspect authoritative physical relationships.",
                "parameters": [],
                "effects": effects,
                "metadata": {"costs_action": False},
            }
        },
        "procedures": procedures or {},
        "definitions": definitions or {},
        "narrative": {},
    }
    return yaml.safe_dump(value, sort_keys=False).encode()


def _declaration() -> ToolDeclaration:
    return ToolDeclaration(
        name="inspect_scene",
        capability="spatial.inspect",
        input_schema=owned_payload(
            {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            }
        ),
        state_predicates=(),
        turn_statuses=("open",),
        writers=("dm",),
    )


def _context(scene: dict[str, object], *, action_id: str = "action-spatial") -> MechanicContext:
    return MechanicContext(
        campaign_id="campaign-spatial",
        turn_id="turn-spatial",
        action_id=action_id,
        state=owned_payload(
            {
                f"{_PACKAGE_ID}.state": {
                    "spatial_scene": scene,
                    "first": None,
                    "second": None,
                    "branch": None,
                    "historical": None,
                    "current_snapshot": None,
                }
            }
        ),
        parameters=owned_payload({}),
    )


def _mechanic(runtime_bytes: bytes) -> object:
    validate_package_runtime(
        package_id=_PACKAGE_ID,
        declarations=(_declaration(),),
        runtime_bytes=runtime_bytes,
    )
    return materialize_package_mechanic(
        package_id=_PACKAGE_ID,
        package_version="1.0.0",
        package_sha256="a" * 64,
        declaration=_declaration(),
        runtime_bytes=runtime_bytes,
    )


def _archive(runtime_bytes: bytes) -> bytes:
    declarations = {
        "format_id": "sylvia-gm-package-content-declarations",
        "format_version": 1,
        "scope": {
            "scope_id": "spatial-runtime-conformance",
            "scope_kind": "bounded_package",
            "claim_status": "NOT_COMPLETE",
            "source_boundary_refs": [],
        },
        "source_authorities": [
            {
                "id": "sylvia-gm.spatial-runtime",
                "document_id": "spatial-runtime-fixture",
                "version": "1.0.0",
                "content_sha256": hashlib.sha256(runtime_bytes).hexdigest(),
                "media_type": "application-yaml",
                "license_id": "CC0-1.0",
            }
        ],
        "inclusions": [
            {
                "id": "spatial-runtime",
                "kind": "mechanic",
                "family": "ruleset-neutrality",
                "disposition": "included_mechanical",
                "classifications": ["sylvia_gm_implementation"],
                "sources": [],
                "links": {
                    "artifacts": ["runtime/ruleset.yaml"],
                    "capabilities": ["spatial.inspect"],
                    "mechanics": ["inspect_scene"],
                    "procedures": [],
                    "prompt_fragments": [],
                    "schemas": [],
                    "state_namespaces": [f"{_PACKAGE_ID}.state"],
                    "tools": ["inspect_scene"],
                    "ui_surfaces": [],
                },
                "evidence": {"tests": ["spatial-runtime-owner-replay"]},
            }
        ],
        "exclusions": [],
    }
    files = {
        "runtime/ruleset.yaml": runtime_bytes,
        "content/content-declarations.json": canonical_json_bytes(declarations),
    }
    record: dict[str, object] = {
        "id": _PACKAGE_ID,
        "version": "1.0.0",
        "role": "primary",
        "metadata": {"label": "Spatial Runtime Conformance"},
        "provenance": {
            "publisher": "sylvia-gm-conformance",
            "source_sha256": hashlib.sha256(runtime_bytes).hexdigest(),
        },
        "compatibility": {
            "host_contract": 1,
            "runtime_format": 1,
            "requires": ["tactical_grid.v1"],
        },
        "minimum_model_capability_tier": "sylvia.capability.t02",
        "dependencies": [],
        "capabilities": ["spatial.inspect"],
        "tools": [
            {
                "name": "inspect_scene",
                "capability": "spatial.inspect",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                "state_predicates": [],
                "turn_statuses": ["open"],
                "writers": ["dm"],
            }
        ],
        "prompt_fragments": [],
        "ui_surfaces": [],
        "participant_artifacts": [],
        "content_declarations": "content/content-declarations.json",
        "files": [
            {
                "path": path,
                "bytes": len(value),
                "sha256": hashlib.sha256(value).hexdigest(),
            }
            for path, value in sorted(files.items())
        ],
    }
    record["content_sha256"] = content_root_for_record(record)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", canonical_json_bytes({"format": 3, "packages": [record]}))
        for path, value in files.items():
            archive.writestr(f"packages/{_PACKAGE_ID}/{path}", value)
    return output.getvalue()


def test_runtime_admits_every_closed_spatial_operation() -> None:
    effects = [_effect("snapshot", store_as="_snapshot")]
    effects.extend(
        _effect(operation, store_as=f"_{operation}", snapshot="{_snapshot}")
        for operation in ("ray", "exposure", "path", "sweep", "crossed_zones", "area")
    )

    validate_package_runtime(
        package_id=_PACKAGE_ID,
        declarations=(_declaration(),),
        runtime_bytes=_runtime(effects),
    )


@pytest.mark.parametrize("filtered", [False, True])
def test_runtime_sweep_records_actual_origin_and_keeps_query_facts_private(filtered: bool) -> None:
    effect = _effect("sweep", store_as="_sweep")
    if filtered:
        effect["spatial_query"]["query"]["metadata_filter"] = {
            "key": "plane", "value": "ethereal", "missing_matches": False,
        }
    mechanic = _mechanic(_runtime([
        effect,
        {"set": {"field": "state.route_clear", "value": "_sweep.clear"}},
        {"set": {"field": "state.route_distance", "value": "_sweep.distance"}},
    ]))
    context = _context(_scene())
    outcome = mechanic(context)
    assert outcome == mechanic(context)
    state = outcome.mutations[0].value
    assert state["route_clear"] is filtered
    assert state["route_distance"] == 7
    evidence = outcome.result["spatial_evidence"][0]
    assert evidence["operation"] == "sweep"
    assert "origin" not in evidence["query"]
    assert evidence["query"]["moving_entity_id"] == "subject"
    assert outcome.result["context"] == {}


@pytest.mark.parametrize("templated", [False, True])
def test_runtime_placement_uses_saved_zone_identity_without_new_position_authority(templated):
    scene = _scene()
    scene["zones"].append({
        "id": "room", "map_id": "runtime-map",
        "shape": {"kind": "box", "min": [7, 1, 0], "max": [10, 6, 4]},
        "metadata": {"plane": "material"},
    })
    effect = {
        "spatial_query": {
            "scene": "state.spatial_scene", "operation": "placement",
            "query": {
                "map_id": "runtime-map", "moving_entity_id": "subject",
                "origin": [2, 3, 0], "exclude_entity_ids": [],
                "metadata_filter": {"key": "plane", "value": "material", "missing_matches": True},
                "max_candidates": 10000,
                "containing_zone_id": "{_room_id}" if templated else "room",
            },
        },
        "store_as": "_placement",
    }
    effects = [
        {"set": {"field": "_room_id", "value": "'room'"}}, effect,
        {"set": {"field": "state.nearest", "value": "_placement.position"}},
    ]
    runtime = _runtime(effects)
    validate_package_runtime(
        package_id=_PACKAGE_ID, declarations=(_declaration(),), runtime_bytes=runtime
    )
    mechanic, context = _mechanic(runtime), _context(scene)
    result = mechanic(context)
    assert result == mechanic(context)
    assert result.mutations[0].value["nearest"] == [7.4, 3, 0]
    assert result.mutations[0].value["spatial_scene"] == scene
    assert result.result["context"] == {"_room_id": "room"}
    evidence = result.result["spatial_evidence"][0]
    assert evidence["query"]["containing_zone_id"] == "room"
    assert len(evidence["ordered_result_sha256"]) == 64


@pytest.mark.parametrize("filtered", [False, True])
def test_runtime_path_evidence_records_the_exact_moving_entity_exclusion(filtered: bool) -> None:
    query = {
        "map_id": "runtime-map",
        "start": [0, 0],
        "goal": [4, 0],
        "allow_diagonal": False,
        "corner_policy": "forbid_cutting",
        "include_entities": True,
        "moving_entity_id": "mover",
        "max_steps": 256,
    }
    if filtered:
        query["metadata_filter"] = {
            "key": "plane", "value": "ethereal", "missing_matches": False
        }
    mechanic = _mechanic(
        _runtime(
            [
                {
                    "spatial_query": {
                        "scene": "state.spatial_scene",
                        "operation": "path",
                        "query": query,
                    },
                    "store_as": "_path",
                },
                {"set": {"field": "state.path_cost", "value": "_path.cost"}},
            ]
        )
    )
    context = _context(
        _scene(
            entities=[
                _entity("mover", [0.5, 0.5, 0]),
                _entity("other", [2.5, 0.5, 0]),
            ]
        )
    )

    outcome = mechanic(context)  # type: ignore[operator]

    assert outcome.result["spatial_evidence"][0]["query"] == {"kind": "path", **query}
    assert outcome.result["context"] == {}
    assert outcome.mutations[0].value["path_cost"] == (4 if filtered else 6)


def test_runtime_scene_mutation_advances_live_rays_and_keeps_frozen_evidence() -> None:
    mechanic = _mechanic(
        _runtime(
            [
                _effect("snapshot", store_as="_snapshot"),
                _effect("ray", store_as="_before"),
                _mutation_effect(),
                _effect("ray", store_as="_after"),
                _effect("ray", store_as="_frozen", snapshot="{_snapshot}"),
                {"set": {"field": "state.first", "value": "_after.intersections[0].id"}},
                {"set": {"field": "state.second", "value": "_frozen.intersections[0].id"}},
            ]
        )
    )

    outcome = mechanic(_context(_scene()))  # type: ignore[operator]
    state = cast(Mapping[str, object], outcome.mutations[0].value)
    evidence = cast(list[Mapping[str, object]], outcome.result["spatial_evidence"])

    assert state["first"] == "far-surface"
    assert state["second"] == "near-surface"
    assert cast(Mapping[str, object], state["spatial_scene"])["map"]["revision"] == 1
    assert [item["operation"] for item in evidence] == [
        "snapshot",
        "ray",
        "scene_mutation",
        "ray",
        "ray",
    ]
    mutation = evidence[2]
    assert mutation["target"] == {"kind": "geometry", "id": "near-surface"}
    assert mutation["before"]["geometry_revision"] == 0
    assert mutation["after"]["geometry_revision"] == 1
    assert evidence[3]["geometry_revision"] == 1
    assert evidence[4]["geometry_revision"] == 0


@pytest.mark.parametrize(
    "bad_mutation",
    (
        {
            "spatial_mutation": {
                "scene": "state.spatial_scene",
                "map_id": "runtime-map",
                "expected_revision": 0,
                "target": {"kind": "geometry", "id": "near-surface"},
                "patch": {"damage": 5},
            },
            "store_as": "_mutation",
        },
        {
            "spatial_mutation": {
                "scene": "state.spatial_scene",
                "map_id": "runtime-map",
                "expected_revision": 0,
                "target": {"kind": "geometry", "id": "near-surface"},
                "patch": {"active": False},
                "ap_cost": 1,
            },
            "store_as": "_mutation",
        },
        {
            "spatial_mutation": {
                "scene": "other.state.spatial_scene",
                "map_id": "runtime-map",
                "expected_revision": 0,
                "target": {"kind": "geometry", "id": "near-surface"},
                "patch": {"active": False},
            },
            "store_as": "public",
        },
    ),
)
def test_runtime_rejects_open_or_non_owned_scene_mutation_effects(
    bad_mutation: dict[str, object],
) -> None:
    with pytest.raises(PackageRuntimeError):
        validate_package_runtime(
            package_id=_PACKAGE_ID,
            declarations=(_declaration(),),
            runtime_bytes=_runtime([bad_mutation]),
        )


def test_runtime_rejects_invalid_scene_mutation_nested_in_a_procedure() -> None:
    invalid_mutation = _mutation_effect()
    cast(dict[str, object], invalid_mutation["spatial_mutation"])["patch"] = {"damage": 5}

    with pytest.raises(PackageRuntimeError):
        validate_package_runtime(
            package_id=_PACKAGE_ID,
            declarations=(_declaration(),),
            runtime_bytes=_runtime(
                [],
                procedures={
                    "cover_changed": {
                        "trigger": "on_cover_changed",
                        "steps": [invalid_mutation],
                    }
                },
            ),
        )


def test_runtime_rejects_invalid_scene_mutation_in_a_procedure_branch() -> None:
    invalid_mutation = _mutation_effect()
    cast(dict[str, object], invalid_mutation["spatial_mutation"])["patch"] = {"damage": 5}

    with pytest.raises(PackageRuntimeError):
        validate_package_runtime(
            package_id=_PACKAGE_ID,
            declarations=(_declaration(),),
            runtime_bytes=_runtime(
                [],
                procedures={
                    "cover_changed": {
                        "trigger": "on_cover_changed",
                        "steps": [
                            {
                                "condition": "true",
                                "on_true": [invalid_mutation],
                            }
                        ],
                    }
                },
            ),
        )


def test_runtime_scene_mutation_rolls_back_when_a_later_effect_fails() -> None:
    mechanic = _mechanic(
        _runtime(
            [
                _mutation_effect(),
                _effect("ray", store_as="_ray", snapshot="{_missing}"),
            ]
        )
    )
    context = _context(_scene())

    with pytest.raises(PackageRuntimeError, match="execution failed"):
        mechanic(context)  # type: ignore[operator]

    retained = cast(Mapping[str, object], context.state[f"{_PACKAGE_ID}.state"])
    assert cast(Mapping[str, object], retained["spatial_scene"])["map"]["revision"] == 0


@pytest.mark.parametrize(
    "bad_effect",
    (
        {"spatial_query": {"scene": "state.spatial_scene", "operation": "warp"}, "store_as": "_x"},
        {
            "spatial_query": {
                "scene": "state.spatial_scene",
                "operation": "ray",
                "query": {"map_id": "runtime-map", "origin": [0, 0, 0]},
            },
            "store_as": "_x",
        },
        {
            "spatial_query": {
                "scene": "other-package.state.spatial_scene",
                "operation": "snapshot",
            },
            "store_as": "_x",
        },
        {
            "spatial_query": {"scene": "state.spatial_scene", "operation": "snapshot"},
            "store_as": "public",
        },
        {
            "spatial_query": {
                "scene": "state.spatial_scene",
                "operation": "ray",
                "query": {
                    **_query("ray"),
                    "origin": [0, float("nan"), 0],
                },
            },
            "store_as": "_x",
        },
        {
            "spatial_query": {
                "scene": "state.spatial_scene",
                "operation": "area",
                "query": {
                    **_query("area"),
                    "shape": {
                        "kind": "cylinder",
                        "center": [4, 4, 0],
                        "radius": -1,
                        "height": 2,
                    },
                },
            },
            "store_as": "_x",
        },
    ),
)
def test_runtime_rejects_invalid_spatial_effects_during_package_admission(
    bad_effect: dict[str, object],
) -> None:
    with pytest.raises(PackageRuntimeError):
        validate_package_runtime(
            package_id=_PACKAGE_ID,
            declarations=(_declaration(),),
            runtime_bytes=_runtime([bad_effect]),
        )


def test_package_branches_on_ordered_results_and_emits_digest_not_private_result() -> None:
    runtime_bytes = _runtime(
        [
            _effect("snapshot", store_as="_snapshot"),
            _effect("ray", store_as="_ray", snapshot="{_snapshot}"),
            {"set": {"field": "state.first", "value": "_ray.intersections[0].id"}},
            {"set": {"field": "state.second", "value": "_ray.intersections[1].id"}},
            {
                "condition": "_ray.intersections[0].id == 'near-surface'",
                "on_true": [{"set": {"field": "state.branch", "value": "'near-first'"}}],
                "on_false": [{"set": {"field": "state.branch", "value": "'unexpected'"}}],
            },
            {"dice": {"expression": "1d6"}, "store_as": "_die"},
        ]
    )
    mechanic = _mechanic(runtime_bytes)

    first = mechanic(_context(_scene()))
    replay = mechanic(_context(_scene()))
    assert first == replay
    state = cast(Mapping[str, object], first.mutations[0].value)
    assert (state["first"], state["second"], state["branch"]) == (
        "near-surface",
        "far-surface",
        "near-first",
    )
    result = cast(Mapping[str, object], first.result)
    context = cast(Mapping[str, object], result["context"])
    assert "_snapshot" not in context
    assert "_ray" not in context
    evidence = cast(list[Mapping[str, object]], result["spatial_evidence"])
    assert [item["operation"] for item in evidence] == ["snapshot", "ray"]
    assert evidence[1]["geometry_revision"] == 0
    assert evidence[1]["query"] == {"kind": "ray", **_query("ray")}
    assert len(cast(str, evidence[1]["ordered_result_sha256"])) == 64


def test_immutable_snapshot_survives_scene_change_and_triggered_subprocedure() -> None:
    opened = _scene(revision=1, active=False)
    runtime_bytes = _runtime(
        [
            _effect("snapshot", store_as="_snapshot"),
            {"set": {"field": "state.spatial_scene", "value": "definitions.open_scene"}},
            _effect("ray", store_as="_current"),
            {
                "set": {
                    "field": "state.current_snapshot",
                    "value": "_current.snapshot_id",
                }
            },
            {
                "trigger": {
                    "name": "on_snapshot_check",
                    "context": {"snapshot": "{_snapshot}"},
                }
            },
        ],
        procedures={
            "check_historical": {
                "trigger": "on_snapshot_check",
                "steps": [
                    _effect("ray", store_as="_historical", snapshot="{snapshot}"),
                    {
                        "set": {
                            "field": "state.historical",
                            "value": "_historical.intersections[0].id",
                        }
                    },
                ],
            }
        },
        definitions={"open_scene": opened},
    )
    mechanic = _mechanic(runtime_bytes)
    outcome = mechanic(_context(_scene()))
    state = cast(Mapping[str, object], outcome.mutations[0].value)

    assert state["historical"] == "near-surface"
    assert (
        state["current_snapshot"]
        != cast(Mapping[str, object], outcome.result)["spatial_evidence"][0]["snapshot_id"]
    )
    assert cast(Mapping[str, object], state["spatial_scene"])["map"]["revision"] == 1


def test_installed_package_mcp_commit_reopens_and_replays_identically(tmp_path: Path) -> None:
    database = tmp_path / "spatial-runtime.sqlite3"
    runtime_bytes = _runtime(
        [
            _effect("snapshot", store_as="_snapshot"),
            _mutation_effect(),
            _effect("ray", store_as="_current"),
            _effect("ray", store_as="_frozen", snapshot="{_snapshot}"),
            {"set": {"field": "state.first", "value": "_current.intersections[0].id"}},
            {"set": {"field": "state.second", "value": "_frozen.intersections[0].id"}},
            _effect("sweep", store_as="_current_sweep"),
            _effect("sweep", store_as="_frozen_sweep", snapshot="{_snapshot}"),
            {"set": {"field": "state.current_solids", "value": "len(_current_sweep.blockers)"}},
            {"set": {"field": "state.frozen_solids", "value": "len(_frozen_sweep.blockers)"}},
        ]
    )
    command = ActionCommand(
        campaign_id="campaign-spatial-installed",
        action_id="action-spatial-installed",
        idempotency_key="idempotency-spatial-installed",
        owner_id="owner-spatial-installed",
        writer=WriterRole.DM,
        expected_revision=0,
        turn_id="turn-spatial-installed",
        package_id=_PACKAGE_ID,
        tool_name="inspect_scene",
        parameters={},
    )
    with AuthorityOwner.open(database, "owner-spatial-installed") as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(_archive(runtime_bytes)),
            install_id="install-spatial-runtime",
        )
        owner.create_campaign(
            CampaignCreate(
                campaign_id=command.campaign_id,
                display_name="Spatial Runtime",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id=command.turn_id,
                initial_state=(
                    StateValue(
                        _PACKAGE_ID,
                        "state",
                        {
                            "spatial_scene": _scene(),
                            "first": None,
                            "second": None,
                        },
                    ),
                ),
            )
        )
        mechanics = StatelessMcpMechanics(owner)
        assert [tool.name for tool in mechanics.list_tools(command.campaign_id)] == [
            "inspect_scene"
        ]
        committed = mechanics.execute_action(command)
        replay = mechanics.execute_action(command)
        assert committed.status is ActionStatus.COMMITTED
        assert replay.resulting_revision == committed.resulting_revision == 1
        assert replay.result == committed.result
        assert (
            owner.snapshot(command.campaign_id).state[f"{_PACKAGE_ID}.state"]["first"]
            == "far-surface"
        )
        assert owner.snapshot(command.campaign_id).state[f"{_PACKAGE_ID}.state"]["second"] == (
            "near-surface"
        )

    with AuthorityOwner.open(database, "owner-spatial-installed") as reopened:
        after = reopened.snapshot(command.campaign_id)
        assert after.revision == 1
        assert after.state[f"{_PACKAGE_ID}.state"]["first"] == "far-surface"
        assert after.state[f"{_PACKAGE_ID}.state"]["second"] == "near-surface"
        assert after.state[f"{_PACKAGE_ID}.state"]["current_solids"] == 1
        assert after.state[f"{_PACKAGE_ID}.state"]["frozen_solids"] == 2
        assert after.state[f"{_PACKAGE_ID}.state"]["spatial_scene"]["map"]["revision"] == 1
        replay = StatelessMcpMechanics(reopened).execute_action(command)
        assert replay.resulting_revision == 1
        assert replay.result["spatial_evidence"][1]["operation"] == "scene_mutation"
        assert replay.result["spatial_evidence"][1]["after"]["geometry_revision"] == 1


def test_missing_scene_or_unregistered_snapshot_fails_without_mutation() -> None:
    missing_scene_runtime = _runtime([_effect("ray", store_as="_ray")])
    mechanic = _mechanic(missing_scene_runtime)
    context = _context(_scene())
    state = cast(dict[str, object], thaw_json_value(context.state))
    cast(dict[str, object], state[f"{_PACKAGE_ID}.state"]).pop("spatial_scene")
    missing = MechanicContext(
        campaign_id=context.campaign_id,
        turn_id=context.turn_id,
        action_id=context.action_id,
        state=owned_payload(state),
        parameters=context.parameters,
    )
    with pytest.raises(PackageRuntimeError, match="execution failed"):
        mechanic(missing)

    stale_runtime = _runtime([_effect("ray", store_as="_ray", snapshot="{_unregistered_snapshot}")])
    stale_mechanic = _mechanic(stale_runtime)
    with pytest.raises(PackageRuntimeError, match="execution failed"):
        stale_mechanic(_context(_scene()))


@pytest.mark.parametrize("templated", [False, True])
def test_runtime_placement_reference_face_uses_saved_surface_without_mutating_position(templated):
    scene = _scene(active=False)
    reference = {
        "target": {"kind": "geometry", "id": "{_surface_id}" if templated else "near-surface"},
        "face": "{_surface_face}" if templated else "east",
    }
    effect = {
        "spatial_query": {
            "scene": "state.spatial_scene",
            "operation": "placement",
            "query": {
                "map_id": "runtime-map",
                "moving_entity_id": "subject",
                "origin": [8, 3, 0],
                "exclude_entity_ids": [],
                "metadata_filter": {"key": "plane", "value": "material", "missing_matches": True},
                "max_candidates": 10000,
                "reference_face": reference,
            },
        },
        "store_as": "_placement",
    }
    runtime = _runtime(
        [
            {"set": {"field": "_surface_id", "value": "'near-surface'"}},
            {"set": {"field": "_surface_face", "value": "'east'"}},
            effect,
            {"set": {"field": "state.nearest", "value": "_placement.position"}},
            {"set": {"field": "state.nearest_distance", "value": "_placement.distance"}},
        ]
    )
    validate_package_runtime(
        package_id=_PACKAGE_ID, declarations=(_declaration(),), runtime_bytes=runtime
    )
    mechanic, context = _mechanic(runtime), _context(scene)
    result = mechanic(context)
    assert result == mechanic(context)
    assert result.mutations[0].value["nearest"] == [3.2, 3, 0]
    assert result.mutations[0].value["spatial_scene"] == scene
    evidence = result.result["spatial_evidence"][0]
    assert evidence["query"]["reference_face"] == {
        "target": {"kind": "geometry", "id": "near-surface"},
        "face": "east",
    }
    assert result.mutations[0].value["nearest_distance"] == 0


@pytest.mark.parametrize(
    "reference",
    [
        None,
        {},
        {"target": {"kind": "zone", "id": "near-surface"}, "face": "east"},
        {"target": {"kind": "geometry", "id": "near-surface"}, "face": "curved"},
        {"target": {"kind": "geometry", "id": "near-surface", "point": [3, 3, 0]}, "face": "east"},
    ],
)
def test_runtime_placement_reference_face_rejects_open_or_invented_schema(reference):
    effect = {
        "spatial_query": {
            "scene": "state.spatial_scene",
            "operation": "placement",
            "query": {
                "map_id": "runtime-map",
                "moving_entity_id": "subject",
                "origin": [8, 3, 0],
                "exclude_entity_ids": [],
                "metadata_filter": {"key": "", "value": "", "missing_matches": False},
                "max_candidates": 10000,
                "reference_face": reference,
            },
        },
        "store_as": "_placement",
    }
    with pytest.raises(PackageRuntimeError):
        validate_package_runtime(
            package_id=_PACKAGE_ID, declarations=(_declaration(),), runtime_bytes=_runtime([effect])
        )


def test_repeated_spatial_reads_share_evidence_without_mutable_aliases() -> None:
    from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge

    bridge = AuthorityMemoryStateBridge(_PACKAGE_ID, {_PACKAGE_ID + ".state": {"scene": _scene()}})
    first = bridge.spatial_query("state.scene", operation="ray", query=_query("ray"), snapshot=None)
    expected = first["intersections"][0]["id"]
    first["intersections"][0]["id"] = "caller-mutated"
    for _ in range(130):
        result = bridge.spatial_query("state.scene", operation="ray", query=_query("ray"), snapshot=None)
        assert result["intersections"][0]["id"] == expected
    assert len(bridge._spatial_evidence) == 1
    bridge.write_field("state", "scene", _scene(revision=1, active=False))
    changed = bridge.spatial_query("state.scene", operation="ray", query=_query("ray"), snapshot=None)
    assert changed["snapshot_id"] != result["snapshot_id"]
    assert len(bridge._spatial_evidence) == 2


def test_distinct_spatial_queries_still_enforce_the_evidence_limit() -> None:
    from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge

    bridge = AuthorityMemoryStateBridge(_PACKAGE_ID, {_PACKAGE_ID + ".state": {"scene": _scene()}})
    for i in range(128):
        query = _query("ray") | {"destination": [10, 3 + i / 1000, 1]}
        bridge.spatial_query("state.scene", operation="ray", query=query, snapshot=None)
    with pytest.raises(PackageRuntimeError, match="evidence exceeds"):
        bridge.spatial_query("state.scene", operation="ray", query=_query("ray") | {"destination": [10, 4, 1]}, snapshot=None)


def test_repeated_snapshot_references_share_evidence_but_changed_scenes_do_not() -> None:
    from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
    bridge = AuthorityMemoryStateBridge(_PACKAGE_ID, {_PACKAGE_ID + ".state": {"scene": _scene()}})
    original = bridge.spatial_snapshot("state.scene")
    for _ in range(130):
        assert bridge.spatial_snapshot("state.scene") == original
    assert len(bridge._spatial_evidence) == 1
    bridge.write_field("state", "scene", _scene(revision=1, active=False))
    changed = bridge.spatial_snapshot("state.scene")
    assert changed["snapshot_id"] != original["snapshot_id"]
    assert len(bridge._spatial_evidence) == 2
    historical = bridge.spatial_query("state.scene", operation="ray", query=_query("ray"), snapshot=original)
    assert historical["snapshot_id"] == original["snapshot_id"]
    assert historical["intersections"][0]["id"] == "near-surface"
