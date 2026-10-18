"""Validate explicitly authored Plan scenes selected by declared tactical surfaces."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, cast

from sylvia_gm.application.tactical_grid import resolve_surface_source
from sylvia_gm.domain.content_validation import ContentShapeError
from sylvia_gm.domain.errors import RulesetDiagnostic
from sylvia_gm.domain.package_library import SurfaceDeclaration
from sylvia_gm.spatial import SpatialValidationError, admit_spatial_scene


def _plan_state(payload: Mapping[str, object]) -> tuple[dict[str, object], dict[str, str]]:
    """Project Plan mechanics into the runtime surface state shape and retain source paths."""

    state: dict[str, object] = {}
    paths: dict[str, str] = {}
    initial = payload.get("initial_state")
    mechanics = initial.get("mechanics") if isinstance(initial, Mapping) else None
    if not isinstance(mechanics, Sequence) or isinstance(mechanics, (str, bytes, bytearray)):
        return state, paths
    for index, item in enumerate(mechanics):
        if not isinstance(item, Mapping):
            continue
        namespace = item.get("namespace")
        key = item.get("key")
        if isinstance(namespace, str) and isinstance(key, str) and "value" in item:
            identity = f"{namespace}.{key}"
            state[identity] = item["value"]
            paths[identity] = f"$.initial_state.mechanics[{index}].value"
    return state, paths


def _state_source_path(
    source: object, state: Mapping[str, object], paths: Mapping[str, str]
) -> str | None:
    """Return the authored value path when a state source exists, including explicit null."""

    if not isinstance(source, Mapping) or source.get("kind") != "state":
        return None
    namespace = source.get("namespace")
    key = source.get("key")
    if not isinstance(namespace, str) or not isinstance(key, str):
        return None
    exact = f"{namespace}.{key}"
    if exact in state:
        return paths[exact]
    root, separator, remainder = key.partition(".")
    root_identity = f"{namespace}.{root}"
    if root_identity not in state:
        return None
    path = paths[root_identity]
    current = state[root_identity]
    if not separator:
        return path
    for field in remainder.split("."):
        if not isinstance(current, Mapping):
            # The referenced branch exists but cannot contain the declared path.
            return path
        if field not in current:
            return None
        current = current[field]
        path += f".{field}"
    return path


def validate_plan_spatial_scenes(
    payload: Mapping[str, object], surfaces: Sequence[SurfaceDeclaration]
) -> None:
    """Admit each present Plan-authored scene reached by a tactical-grid declaration."""

    state, paths = _plan_state(payload)
    for surface in surfaces:
        if surface.renderer not in {"tactical_grid", "coordinate_map"} or not isinstance(surface.source, Mapping):
            continue
        source_kind = surface.source.get("kind")
        if source_kind == "object":
            fields = surface.source.get("fields")
            scene_source = fields.get("scene") if isinstance(fields, Mapping) else None
            authored_path = _state_source_path(scene_source, state, paths)
            scene = resolve_surface_source(scene_source, state=state, participants=())
        elif source_kind == "state":
            authored_path = _state_source_path(surface.source, state, paths)
            resolved = resolve_surface_source(surface.source, state=state, participants=())
            if isinstance(resolved, Mapping) and "scene" not in resolved:
                continue
            scene = resolved.get("scene") if isinstance(resolved, Mapping) else resolved
            if isinstance(resolved, Mapping):
                authored_path = None if authored_path is None else authored_path + ".scene"
        else:
            continue
        if authored_path is None:
            continue
        try:
            admit_spatial_scene(scene)
        except SpatialValidationError as error:
            suffix = "" if error.path == "$" else error.path[1:]
            raise ContentShapeError(
                (
                    RulesetDiagnostic(
                        code="plan_spatial_scene_invalid",
                        phase="content",
                        file="plan.json",
                        path=f"{authored_path}{suffix}",
                        message=str(error),
                        guidance=(
                            "Read the spatial_scene content definition and correct "
                            "the indicated field."
                        ),
                    ),
                )
            ) from error


def plan_spatial_warnings(
    payload: Mapping[str, object], surfaces: Sequence[SurfaceDeclaration],
) -> list[dict[str, str]]:
    """Explain offset multi-cell props in an admitted draft without changing or rejecting them."""
    state, _ = _plan_state(payload)
    warnings: list[dict[str, str]] = []
    for surface in surfaces:
        if surface.renderer not in {"tactical_grid", "coordinate_map"}:
            continue
        data = resolve_surface_source(surface.source, state=state, participants=())
        scene = data.get("scene") if isinstance(data, Mapping) else None
        if not isinstance(scene, Mapping):
            continue
        admitted = cast(dict[str, Any], admit_spatial_scene(scene).as_payload())
        grid = admitted["map"]["grid"]
        size, origin = grid["cell_size"], grid["origin"]
        for geometry in admitted["geometry"]:
            if (not geometry["active"] or geometry["movement"] != "blocking"
                    or not geometry["occupies_cells"]):
                continue
            box = geometry["volume"]
            lower = [(box["min"][axis] - origin[axis]) / size for axis in (0, 1)]
            upper = [(box["max"][axis] - origin[axis]) / size for axis in (0, 1)]
            cells = math.prod(math.ceil(upper[i]) - math.floor(lower[i]) for i in (0, 1))
            if cells < 2 or all(abs(value - round(value)) < 1e-9 for value in lower + upper):
                continue
            warnings.append({
                "code": "offset_prop_reserves_multiple_cells",
                "surface": surface.identity,
                "geometry": geometry["id"],
                "message": f"{geometry['id']} reserves {cells} grid cells because its box crosses "
                    "cell boundaries. For a one-cell prop, align both its origin and dimensions "
                    "to that cell. Keep intentional larger/offset props; use occupies_cells: false "
                    "for a thin physical barrier. This is advice, not a validation failure.",
            })
            if len(warnings) == 16:
                return warnings
    return warnings


__all__ = ["validate_plan_spatial_scenes", "plan_spatial_warnings"]
