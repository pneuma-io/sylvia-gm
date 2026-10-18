"""Deterministic, ruleset-neutral spatial state and query interface."""

from sylvia_gm.spatial._engine import SpatialEngine
from sylvia_gm.spatial._interface import (
    SPATIAL_ALGORITHM_VERSION,
    AdmittedSpatialMutation,
    AdmittedSpatialQuery,
    AdmittedSpatialResult,
    AdmittedSpatialScene,
    SpatialValidationError,
    admit_spatial_query,
    admit_spatial_result,
    admit_spatial_scene,
    mutate_spatial_scene,
)

__all__ = [
    "SPATIAL_ALGORITHM_VERSION",
    "SpatialEngine",
    "AdmittedSpatialMutation",
    "AdmittedSpatialQuery",
    "AdmittedSpatialResult",
    "AdmittedSpatialScene",
    "SpatialValidationError",
    "admit_spatial_query",
    "admit_spatial_result",
    "admit_spatial_scene",
    "mutate_spatial_scene",
]
