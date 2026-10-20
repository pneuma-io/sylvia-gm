"""Shipped Riichi content is discoverable without replacing a user's installed copies."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_packages import (
    BUNDLED_RIICHI_PACKAGES,
    bundled_riichi_archive,
)
from sylvia_gm.application.package_library import validate_admitted_package_runtimes
from sylvia_gm.application.package_materialization import materialize_package_archive
from sylvia_gm.builder._package_snapshots import retained_package_drafts
from sylvia_gm.infrastructure.package_archive import admit_package_archive
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness


def test_bundled_riichi_is_the_validated_original_package() -> None:
    archive = bundled_riichi_archive()
    assert hashlib.sha256(archive).hexdigest() == (
        "c090627edfcc91346fad1e55769ddb5423076fada077d43951fd535e09313a2f"
    )
    with admit_package_archive(io.BytesIO(archive)) as admitted:
        validate_admitted_package_runtimes(admitted)
        assert {(item.package_id, item.version) for item in admitted.manifests} == set(
            BUNDLED_RIICHI_PACKAGES
        )
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        assert package.testzip() is None
        assert json.loads(package.read("manifest.json"))["format"] == 4


def test_fresh_owner_offers_srd_and_riichi_and_reopens_without_duplicates(tmp_path: Path) -> None:
    database = tmp_path / "fresh.db"
    snapshots = []
    for _ in range(2):
        harness = _OwnerHarness(database, None)
        try:
            candidates = harness.request("candidates", "package_candidates")
            snapshots.append(candidates)
            catalog = harness.request("catalog", "campaign_creation_catalog")
            rulesets = {item["id"]: item for item in catalog if item["kind"] == "ruleset"}
            assert {"sylvia.srd-lite.bg3", "pneuma.riichi-mahjong"} <= rulesets.keys()
            riichi = rulesets["pneuma.riichi-mahjong"]
            assert riichi["title"] == "Riichi Mahjong"
            assert any(
                item["id"] == "pneuma.riichi-club"
                for item in riichi["compatible_settings"]
            )
            assert [item["id"] for item in catalog if item["kind"] == "campaign_plan"
                    and item.get("ruleset", {}).get("id") == "pneuma.riichi-mahjong"] == [
                "riichi-quick-test"
            ]
        finally:
            harness.close()
    assert snapshots[0] == snapshots[1]
    assert len([
        item for item in snapshots[0]
        if any(entry["package_id"] == "pneuma.riichi-mahjong"
               for entry in item["package_lock"]["entries"])
    ]) == 1


@pytest.mark.parametrize("include_setting", [True, False])
def test_startup_preserves_edited_content_and_supplies_a_missing_setting(
    tmp_path: Path, include_setting: bool,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    database = tmp_path / "edited.db"
    with AuthorityOwner.open(source / "source.db", "prepare-user-content") as owner:
        original = owner.install_package_archive(
            io.BytesIO(bundled_riichi_archive()), install_id="user-import-original",
        )
        drafts = retained_package_drafts(
            owner, original.candidate_id, include_setting=include_setting,
        )
        edited = []
        for draft in drafts:
            record = {**draft.record, "metadata": {"label": "My retained Riichi content"}}
            payloads = dict(draft.payloads)
            if record["content_kind"] == "ruleset":
                payloads["prompts/gm-operating-guide.md"] += b"\nKeep my local table guidance.\n"
            else:
                payloads["world.md"] += b"\nKeep my local club and its story.\n"
            edited.append(replace(draft, record=record, payloads=payloads))
        with materialize_package_archive(tuple(edited)) as materialized:
            edited_archive = materialized.archive.read()
    with AuthorityOwner.open(database, "install-user-content") as owner:
        changed = owner.install_package_archive(
            io.BytesIO(edited_archive), install_id="user-edited-content",
        )
        with io.BytesIO() as exported:
            owner.export_package_candidate(changed.candidate_id, exported)
            expected = exported.getvalue()
        before = owner.package_candidates()
    # Ordinary startup must also preserve unrelated authored files in this profile.
    authored = tmp_path / "campaigns" / "user-authored" / "campaign-plan.md"
    authored.parent.mkdir(parents=True)
    authored.write_bytes(b"# My unfinished campaign\nDo not replace this draft.\n")
    for _ in range(2):
        harness = _OwnerHarness(database, None)
        try:
            catalog = harness.request("catalog", "campaign_creation_catalog")
            riichi = next(item for item in catalog
                          if item["kind"] == "ruleset" and item["id"] == "pneuma.riichi-mahjong")
            assert any(item["id"] == "pneuma.riichi-club"
                       for item in riichi["compatible_settings"])
        finally:
            harness.close()
    with AuthorityOwner.open(database, "inspect-user-content") as owner:
        assert all(owner.package_candidate(item.candidate_id) == item for item in before)
        with io.BytesIO() as exported:
            owner.export_package_candidate(changed.candidate_id, exported)
            assert exported.getvalue() == expected
    assert authored.read_bytes() == b"# My unfinished campaign\nDo not replace this draft.\n"
