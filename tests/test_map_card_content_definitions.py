"""Focused checks for spatial-scene and result-card content definitions."""

from __future__ import annotations

import copy

import pytest

from sylvia_gm.application.result_cards import project_result_card
from sylvia_gm.application.tactical_grid import project_tactical_grid
from sylvia_gm.domain.content_definitions import get_content_definition
from sylvia_gm.domain.content_validation import ContentShapeError, validate_content_shape
from sylvia_gm.spatial import SpatialValidationError, admit_spatial_scene


def test_spatial_scene_example_passes_shared_and_owning_validation() -> None:
    definition = get_content_definition("spatial_scene")

    validate_content_shape("spatial_scene", definition["example"])
    admitted = admit_spatial_scene(definition["example"])

    assert admitted.map_id == "relay-floor"
    assert admitted.geometry_revision == 1


@pytest.mark.parametrize(
    "mutate,expected_path",
    [
        (lambda scene: scene.update(undeclared=True), "$"),
        (lambda scene: scene["entities"][0].update(speed=3), "$.entities[0]"),
        (lambda scene: scene["terrain"][0].update(cost="slow"), "$.terrain[0].cost"),
    ],
)
def test_spatial_scene_owner_rejects_undeclared_and_wrong_typed_fields(
    mutate, expected_path
) -> None:
    scene = copy.deepcopy(get_content_definition("spatial_scene")["example"])
    mutate(scene)

    with pytest.raises(SpatialValidationError) as rejected:
        admit_spatial_scene(scene)

    assert rejected.value.path == expected_path


def test_result_card_example_passes_shape_and_resolves_public_values() -> None:
    definition = get_content_definition("result_card")
    declaration = definition["example"]

    validate_content_shape("result_card", declaration)
    card = project_result_card(
        declaration,
        parameters={"channel": "north"},
        public_context={"signal_status": "clear"},
    )

    assert card is not None
    assert card["summary"] == [{"label": "Signal", "value": "Clear", "emphasis": True}]
    assert card["actions"][0]["parameters"] == {"channel": "north"}


@pytest.mark.parametrize(
    "mutate,expected_path",
    [
        (lambda card: card.update(undeclared=True), "$.undeclared"),
        (
            lambda card: card["summary"][0].update(value={"path": ["context", "status"]}),
            "$.summary[0].value.path",
        ),
        (lambda card: card["actions"][0].update(parameters=[]), "$.actions[0].parameters"),
    ],
)
def test_result_card_shape_reports_closed_and_typed_field_errors(mutate, expected_path) -> None:
    declaration = copy.deepcopy(get_content_definition("result_card")["example"])
    mutate(declaration)

    with pytest.raises(ContentShapeError) as rejected:
        validate_content_shape("result_card", declaration)

    assert expected_path in {item.path for item in rejected.value.diagnostics}


def test_result_card_context_rules_remain_with_projection_owner() -> None:
    declaration = copy.deepcopy(get_content_definition("result_card")["example"])
    declaration["summary"][0]["value"] = {"path": "state.secret"}

    validate_content_shape("result_card", declaration)
    assert project_result_card(declaration, parameters={}, public_context={}) is None


def test_tactical_grid_source_example_passes_shape_scene_and_projection() -> None:
    definition = get_content_definition("tactical_grid_source")
    source = definition["example"]

    validate_content_shape("tactical_grid_source", source)
    admit_spatial_scene(source["scene"])
    projected = project_tactical_grid(source)

    assert projected["map_revision"] == 1
    assert projected["objectives"][0]["id"] == "reach-console"
    assert projected["entities"][0]["display"] == {"label": "Keeper", "kind": "unit"}


@pytest.mark.parametrize(
    "mutate,expected_path",
    [
        (lambda source: source.update(private_overlay={}), "$.private_overlay"),
        (lambda source: source["objectives"][0].update(status="waiting"), "$.objectives[0].status"),
        (lambda source: source.update(selection={"raw_state": True}), "$.selection.raw_state"),
    ],
)
def test_tactical_grid_source_authoring_shape_rejects_unprojected_fields(
    mutate, expected_path
) -> None:
    source = copy.deepcopy(get_content_definition("tactical_grid_source")["example"])
    mutate(source)

    with pytest.raises(ContentShapeError) as rejected:
        validate_content_shape("tactical_grid_source", source)

    assert expected_path in {item.path for item in rejected.value.diagnostics}
