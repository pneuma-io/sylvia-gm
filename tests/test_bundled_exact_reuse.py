"""Bundled reuse retains exact source freshness and caller ownership."""

from copy import deepcopy

from sylvia_gm.application import bundled_packages as bundled
from sylvia_gm.application.package_materialization import NormalizedPackageDraft


def test_bundled_reuse_preserves_identity_and_invalidates_changed_record_or_payload(monkeypatch):
    bundled._exact_bundled_archive.cache_clear()
    original = bundled.materialize_package_archive
    calls = []

    def counted(drafts):
        calls.append(len(drafts))
        return original(drafts)

    monkeypatch.setattr(bundled, "materialize_package_archive", counted)
    drafts = bundled._srd_drafts()
    before = deepcopy(drafts)
    first = bundled._archive(drafts)
    assert bundled._archive(bundled._srd_drafts()) == first
    assert len(calls) == 1
    changed_record = deepcopy(drafts[0].record)
    changed_record["metadata"]["label"] += " (source freshness check)"
    second = bundled._archive(
        (NormalizedPackageDraft(changed_record, drafts[0].payloads, 4), drafts[1])
    )
    assert second != first and len(calls) == 2
    changed_payload = dict(drafts[0].payloads)
    changed_payload["prompts/gm-operating-guide.md"] += b"\n"
    third = bundled._archive(
        (NormalizedPackageDraft(drafts[0].record, changed_payload, 4), drafts[1])
    )
    assert third not in (first, second) and len(calls) == 3
    assert drafts == before
    assert bundled._archive(drafts) == first and len(calls) == 3
    assert bundled._exact_bundled_archive.cache_info().currsize <= 4
