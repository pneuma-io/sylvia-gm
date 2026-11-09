"""Bounded package queries project optional keyed values without adding game rules."""

import io
import json
import zipfile
from copy import deepcopy

import pytest

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.application.package_library import project_surfaces
from sylvia_gm.application.tactical_grid import resolve_surface_source
from sylvia_gm.domain.authority import PackageLock, StateValue, WriterRole
from sylvia_gm.domain.package_library import (
    canonical_json_bytes,
    content_root_for_record,
    manifest_from_record,
)
from tests.test_tactical_grid_ui import _installed_archive, _record, _surface


def _state(key):
    return {"kind": "state", "namespace": "synthetic.coordinate", "key": key}


def _source():
    return {
        "kind": "keyed_values",
        "entries": [
            {"key": _state(f"values.{place}.id"), "value": _state(f"values.{place}.total")}
            for place in ("first", "second", "third", "fourth")
        ],
    }


def test_keyed_values_preserve_zero_and_negative_values_and_omit_missing_data():
    state = {
        "synthetic.coordinate.values": {
            "first": {"id": "a", "total": 0},
            "second": {"id": "b", "total": -2},
            "third": {"id": "c"},
            "fourth": {"total": 12},
        }
    }
    before = deepcopy(state)
    assert resolve_surface_source(_source(), state=state, participants=()) == {"a": 0, "b": -2}
    assert state == before
    assert resolve_surface_source(_source(), state={}, participants=()) == {}


def test_manifest_admits_keyed_values_as_a_nested_surface_query():
    surface = _surface()
    surface["source"]["fields"]["values"] = _source()
    manifest = manifest_from_record(_record(surface), format_version=1)
    projection = project_surfaces(
        (), manifest.ui_surfaces, active_capabilities=manifest.capabilities
    )
    assert len(projection.ui_surfaces) == 1


@pytest.mark.parametrize(
    "entries",
    [
        [],
        [{}] * 17,
        [{"key": _state("id")}],
        [{"key": _state("id"), "value": _state("total"), "script": "unbounded"}],
        [{"key": {"kind": "script"}, "value": _state("total")}],
    ],
)
def test_manifest_rejects_malformed_or_unbounded_keyed_values(entries):
    surface = _surface()
    surface["source"] = {"kind": "keyed_values", "entries": entries}
    with pytest.raises(ValueError):
        manifest_from_record(_record(surface), format_version=1)


def test_keyed_values_cannot_exceed_the_existing_source_depth_limit():
    source = _state("total")
    for _ in range(5):
        source = {"kind": "keyed_values", "entries": [{"key": _state("id"), "value": source}]}
    surface = _surface()
    surface["source"] = source
    with pytest.raises(ValueError, match="nesting is too deep"):
        manifest_from_record(_record(surface), format_version=1)


@pytest.mark.parametrize("private_field", ["key", "value"])
def test_owner_omits_surface_with_private_source_nested_in_keyed_values(tmp_path, private_field):
    with zipfile.ZipFile(io.BytesIO(_installed_archive())) as original:
        payloads = {name: original.read(name) for name in original.namelist()}
    manifest = json.loads(payloads["manifest.json"])
    record = manifest["packages"][0]
    entry = {"key": _state("id"), "value": _state("total")}
    entry[private_field] = {"kind": "campaign_plan", "path": ["private"]}
    record["ui_surfaces"][0]["source"]["fields"]["values"] = {
        "kind": "keyed_values",
        "entries": [entry],
    }
    record["content_sha256"] = content_root_for_record(record)
    payloads["manifest.json"] = canonical_json_bytes(manifest)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as package:
        for name, value in payloads.items():
            package.writestr(name, value)
    with AuthorityOwner.open(tmp_path / "private.sqlite3", "projection-owner") as owner:
        installed = owner.install_package_archive(
            io.BytesIO(output.getvalue()),
            install_id="private",
        )
        owner.create_campaign(
            CampaignCreate(
                campaign_id="private",
                display_name="Private query",
                writer=WriterRole.DM,
                package_lock=installed.package_lock,
                turn_id="turn",
                initial_state=(StateValue("synthetic.coordinate", "values", {}),),
            )
        )
        assert all(
            surface["id"] != record["ui_surfaces"][0]["id"]
            for surface in owner.campaign_panels("private")["surfaces"]
        )


def test_empty_ruleset_owner_has_no_projected_game_values(tmp_path):
    with AuthorityOwner.open(tmp_path / "empty.sqlite3", "empty-owner") as owner:
        owner.create_campaign(
            CampaignCreate(
                campaign_id="empty",
                display_name="No rules",
                writer=WriterRole.DM,
                package_lock=PackageLock(entries=()),
                turn_id="turn",
            )
        )
        for surface in owner.campaign_panels("empty")["surfaces"]:
            assert "order_values" not in surface["data"]
            assert "spell_categories" not in surface["data"]
