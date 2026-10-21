"""Completed bundled admission is shared; projections never expose its mutable data."""

import io
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from sylvia_gm.application import bundled_campaign_plans
from sylvia_gm.application import bundled_packages as bundled
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.package_library import (
    CAMPAIGN_SETTING_KNOWLEDGE_PATH,
    ParticipantArtifactDeclaration,
)


def test_cold_bundle_consumers_share_admission_and_keep_exact_source_keys(monkeypatch):
    bundled._exact_bundled_archive.cache_clear()
    bundled.bundled_rift_authority.cache_clear()
    bundled._bundled_campaign_content_catalog.cache_clear()
    declaration = ParticipantArtifactDeclaration(
        "player_character",
        "example.character",
        "a" * 64,
        {"type": "object", "properties": {"value": {"type": "integer"}}},
    )
    rules = NormalizedPackageDraft(
        {"id": "sylvia.srd-lite.bg3", "version": "1.0.0"}, {"body.json": b"{}"}, 4
    )
    reference = NormalizedPackageDraft(
        {"id": "example.reference", "version": "1.0.0"}, {"text.md": b"reference"}, 4
    )
    setting = NormalizedPackageDraft(
        {"id": "pneuma.severed-lands", "version": "1.0.0"},
        {CAMPAIGN_SETTING_KNOWLEDGE_PATH: b"{}"},
        4,
    )
    monkeypatch.setattr(bundled, "_srd_drafts", lambda: (rules, reference))
    monkeypatch.setattr(bundled, "_rift_drafts", lambda: (rules, reference, setting))
    monkeypatch.setattr(
        bundled, "parse_campaign_setting_knowledge_bytes", lambda _: SimpleNamespace(facts=())
    )
    monkeypatch.setattr(bundled_campaign_plans, "bundled_plan_catalog", lambda: ())
    calls = []

    @contextmanager
    def admitted(drafts):
        calls.append(tuple(draft.payloads for draft in drafts))
        manifests = tuple(
            SimpleNamespace(
                package_id=draft.record["id"],
                version="1.0.0",
                identity=(draft.record["id"], "1.0.0", "a" * 64),
                participant_artifacts=(declaration,),
            )
            for draft in drafts
        )
        yield SimpleNamespace(
            archive=io.BytesIO(b"admitted bytes"), manifests=manifests, closure_sha256="b" * 64
        )

    monkeypatch.setattr(bundled, "materialize_package_archive", admitted)
    try:
        bundled.bundled_campaign_content_catalog()
        bundled.bundled_rift_authority()
        first = bundled.bundled_rift_participant_declaration()
        assert bundled.bundled_rift_archive() == b"admitted bytes"
        assert len(calls) == 2  # One SRD closure and one full Setting closure.
        first.schema["properties"]["value"]["type"] = "caller-local"
        assert (
            bundled.bundled_rift_participant_declaration().schema["properties"]["value"]["type"]
            == "integer"
        )
        assert len(calls) == 2
        setting.payloads[CAMPAIGN_SETTING_KNOWLEDGE_PATH] = b'{"changed":true}'
        bundled.bundled_rift_participant_declaration()
        assert len(calls) == 3
        rules.record["version"] = "1.0.1"
        bundled.bundled_rift_participant_declaration()
        assert len(calls) == 4
    finally:
        bundled._exact_bundled_archive.cache_clear()
        bundled.bundled_rift_authority.cache_clear()
        bundled._bundled_campaign_content_catalog.cache_clear()


def test_bundled_failed_admission_is_never_reused(monkeypatch):
    bundled._exact_bundled_archive.cache_clear()
    draft = NormalizedPackageDraft({"id": "example"}, {"body": b"bad"})
    calls = []

    def rejected(drafts):
        calls.append(drafts)
        raise ValueError("Invalid bundled source")

    monkeypatch.setattr(bundled, "materialize_package_archive", rejected)
    for _ in range(2):
        with pytest.raises(ValueError, match="Invalid bundled source"):
            bundled._archive((draft,))
    assert len(calls) == 2
