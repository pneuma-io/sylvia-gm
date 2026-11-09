"""One invalid saved artifact cannot suppress healthy campaign creation choices."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.campaign_plan_sources import campaign_plan_sources
from sylvia_gm.application.plan_snapshot import validate_plan_snapshot
from sylvia_gm.application.reusable_plans import (
    load_reusable_campaign_plan,
    save_reusable_campaign_plan,
)
from sylvia_gm.builder._plan_snapshot_materializer import _rebind_plan_to_snapshot
from sylvia_gm.domain.authority import SafePayload, thaw_json_value
from sylvia_gm.domain.content_validation import ContentShapeError
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.interfaces.owner_process import _campaign_creation_catalog
from tests.test_campaign_library_selection import seed_campaign_library
from tests.test_plan_spatial_admission import _snapshot_with_tactical_surface


def _assert_healthy_choices_survive(catalog: Sequence[SafePayload]) -> None:
    assert any(
        item.get("kind") == "ruleset" and item.get("id") == "sylvia.srd-lite.bg3" for item in catalog
    )
    assert any(
        item.get("kind") == "campaign_plan" and item.get("id") == "last-cart-through"
        for item in catalog
    )
    assert any(
        item.get("kind") == "ruleset"
        and item.get("selection_id")
        and item.get("id") != "sylvia.srd-lite.bg3"
        for item in catalog
    )


def test_invalid_saved_plan_isolated_with_safe_spatial_diagnostic(tmp_path: Path) -> None:
    with AuthorityOwner.open(tmp_path / "plan.sqlite3", "catalog-plan-owner") as owner:
        candidate, setting_id, source, _characters = seed_campaign_library(owner, tmp_path)
        ruleset_id = source.plan.payload["bindings"]["ruleset"]["id"]
        snapshot = _snapshot_with_tactical_surface(
            owner,
            candidate,
            {
                "kind": "object",
                "fields": {
                    "scene": {
                        "kind": "state",
                        "namespace": ruleset_id,
                        "key": "state.spatial_scene",
                    }
                },
            },
        )
        authority = campaign_plan_sources(owner, candidate.package_lock, setting_id=setting_id)
        assert authority is not None
        rebound = _rebind_plan_to_snapshot(snapshot, thaw_json_value(source.plan.payload))
        saved = save_reusable_campaign_plan(
            tmp_path,
            operation_id="saved-plan-that-later-became-invalid",
            source_payload=rebound,
            source_markdown=source.plan.readable_markdown,
            validate_plan=lambda payload, text: validate_plan_snapshot(snapshot, payload, text),
            package_snapshot=snapshot,
        )
        plan_path = tmp_path / "campaign-plans" / saved.artifact_id / "plan.json"
        payload = thaw_json_value(saved.plan.payload)
        payload["initial_state"]["mechanics"][0]["value"]["spatial_scene"]["geometry"][0][
            "undeclared"
        ] = "PRIVATE VALUE MUST NOT APPEAR"
        plan_path.write_bytes(canonical_json_bytes(payload))
        with pytest.raises(ContentShapeError):
            load_reusable_campaign_plan(tmp_path, saved.artifact_id)

        catalog = _campaign_creation_catalog(tmp_path, owner)

    _assert_healthy_choices_survive(catalog)
    unavailable = [item for item in catalog if item.get("kind") == "unavailable_content"]
    assert len([item for item in unavailable if item.get("id") == saved.artifact_id]) == 1
    row = next(item for item in unavailable if item.get("id") == saved.artifact_id)
    assert row["content_kind"] == "campaign_plan"
    assert row["title"] == "Saved Campaign Plan unavailable"
    assert row["summary"]
    assert "PRIVATE VALUE" not in str(row)
    assert row["validation_errors"] == [
        {
            "code": "plan_spatial_scene_invalid",
            "phase": "content",
            "file": "plan.json",
            "path": "$.initial_state.mechanics[0].value.spatial_scene.geometry[0]",
            "message": "Spatial object uses a closed schema at $.geometry[0]",
            "guidance": (
                "Read the spatial_scene content definition and correct the indicated field."
            ),
        }
    ]


def test_invalid_saved_character_is_reported_once_across_compatibility_scans(
    tmp_path: Path,
) -> None:
    with AuthorityOwner.open(tmp_path / "character.sqlite3", "catalog-character-owner") as owner:
        _candidate, _setting_id, _plan, characters = seed_campaign_library(owner, tmp_path)
        invalid = characters[0]
        payload_path = tmp_path / "participants" / invalid.artifact_id / "payload.json"
        payload_path.write_bytes(b'{"participant":')

        catalog = _campaign_creation_catalog(tmp_path, owner)

    _assert_healthy_choices_survive(catalog)
    unavailable = [
        item
        for item in catalog
        if item.get("kind") == "unavailable_content" and item.get("id") == invalid.artifact_id
    ]
    assert len(unavailable) == 1
    assert unavailable[0]["content_kind"] == "participant"
    assert unavailable[0]["title"] == "Saved character unavailable"
    assert unavailable[0]["summary"]
    assert unavailable[0]["validation_errors"] == []
