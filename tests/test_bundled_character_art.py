"""Prepared art uses real starter files and survives retained Plan pins and offline transfer."""

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest
from scripts.inventory_authored_artwork import authored_artwork_subjects
from scripts.inventory_inherited_artwork import inherited_artwork_subjects

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_character_art import (
    bundled_character_image,
    bundled_plan_visuals,
    bundled_source_visuals,
)
from sylvia_gm.application.bundled_packages import _rift_drafts
from sylvia_gm.application.campaign_library_selection import materialize_library_party
from sylvia_gm.application.image_art import ImageArt
from sylvia_gm.application.image_hooks import ImageData
from sylvia_gm.domain.image_art import validate_character_visuals
from sylvia_gm.domain.package_library import PackageFile
from tests.test_bundled_starter_characters import selection

ROOT = Path(__file__).resolve().parents[1]
WORLD = ROOT / "campaign-settings/severed-lands"
ART = ROOT / "artwork"


def test_prepared_bundles_match_starter_data_package_payloads_and_frozen_inventory():
    catalogue = json.loads((ART / "character-art.json").read_bytes())
    starters = json.loads((ROOT / "src/sylvia_gm/campaign/srd-starters.json").read_bytes())
    inventory = json.loads((ROOT / "src-tauri/sidecar-data-files.json").read_bytes())["files"]
    rules, reference, setting = _rift_drafts()
    resources = json.loads((ROOT / "src-tauri/tauri.conf.json").read_bytes())["bundle"]["resources"]
    authored_sources = {
        (source["package_id"], source["path"], source["sha256"]): (
            edition["document"].encode("utf-8")
        )
        for subject in authored_artwork_subjects()["subjects"]
        for edition in subject["editions"]
        for source in (edition["source"],)
    }
    inherited_sources = {
        (item["package_id"], item["path"], item["sha256"]): PackageFile(
            path=item["path"], byte_count=item["byte_count"], sha256=item["sha256"]
        )
        for item in inherited_artwork_subjects()["source_files"]
    }
    setting_character_sources = {}
    for archive_path in (ROOT / "campaign-plans").glob("*/setting.sylvia-gm-package"):
        with zipfile.ZipFile(archive_path) as archive:
            for package in json.loads(archive.read("manifest.json"))["packages"]:
                for descriptor in package["files"]:
                    path = descriptor["path"]
                    if not (path.startswith("characters/") and path.endswith(".json")):
                        continue
                    raw = archive.read(f"packages/{package['id']}/{path}")
                    assert hashlib.sha256(raw).hexdigest() == descriptor["sha256"]
                    assert len(raw) == descriptor["bytes"]
                    setting_character_sources[package["id"], path, descriptor["sha256"]] = raw
    assert catalogue["characters"]
    for entry in catalogue["characters"]:
        visuals = validate_character_visuals(entry["visuals"])
        if entry["id"].startswith("srd-starter-"):
            source = next(row for row in starters if row["participant"]["name"] == entry["name"])
            assert source["visuals"] == visuals
        elif entry["id"].startswith("severed-party-"):
            for source in entry["sources"]:
                raw = setting_character_sources[
                    source["package_id"], source["path"], source["sha256"]
                ]
                assert json.loads(raw)["format_id"] == "sylvia-gm-character-file"
                assert bundled_source_visuals(
                    source["package_id"], source["path"], raw, subject_name=entry["name"],
                    campaign_setting_ids=frozenset({source["campaign_setting_id"]}),
                ) == visuals
        elif entry["id"].startswith("severed-plan-"):
            for source in entry["sources"]:
                assert "package_id" not in source
                plan = json.loads(
                    (ROOT / "campaign-plans" / source["plan_id"] / "plan.json").read_bytes()
                )
                assert bundled_plan_visuals(
                    plan, source["path"], subject_name=source["subject_name"],
                    campaign_setting_ids=frozenset({source["campaign_setting_id"]}),
                ) == visuals
                assert bundled_plan_visuals(
                    plan, source["path"], subject_name=source["subject_name"],
                    campaign_setting_ids=frozenset({"another-setting"}),
                ) is None
        elif entry["id"].startswith("siren-protocol-"):
            # Siren is an independently delivered Setting; its exact file descriptors bind art.
            for source in entry["sources"]:
                assert (
                    source["package_id"]
                    == source["campaign_setting_id"]
                    == "pneuma-io.siren-protocol"
                )
                descriptor = PackageFile(
                    path=source["path"].partition("#")[0],
                    byte_count=source["byte_count"],
                    sha256=source["sha256"],
                )
                assert (
                    bundled_source_visuals(
                        source["package_id"],
                        source["path"],
                        descriptor,
                        campaign_setting_ids=frozenset({source["campaign_setting_id"]}),
                    )
                    == visuals
                )
                assert (
                    bundled_source_visuals(
                        source["package_id"],
                        source["path"],
                        descriptor,
                        campaign_setting_ids=frozenset({"pneuma.severed-lands"}),
                    )
                    is None
                )
            assert visuals["reference_sheet"]["sha256"] == entry["reference_authority"]["sha256"]
            assert entry["asset_origins"]["reference_sheet"] == "supplied locked normalized master"
        else:
            for source in entry["sources"]:
                if "#/" in source["path"]:
                    raw = inherited_sources[
                        (source["package_id"], source["path"].partition("#")[0], source["sha256"])
                    ]
                    assert bundled_source_visuals(
                        source["package_id"], source["path"], raw,
                        campaign_setting_ids=frozenset({"another-setting"}),
                    ) is None
                else:
                    raw = authored_sources[
                        (source["package_id"], source["path"], source["sha256"])
                    ]
                assert bundled_source_visuals(
                    source["package_id"], source["path"], raw, subject_name=entry["name"],
                    campaign_setting_ids=frozenset({"pneuma.severed-lands"}),
                ) == visuals
        for purpose in ("full_body", "reference_sheet", "portrait"):
            asset = entry["visuals"][purpose]
            raw = bundled_character_image(asset["path"], asset["sha256"])
            assert raw is not None and raw == (ART / asset["path"]).read_bytes()
            assert asset["path"] not in setting.payloads
            assert f"campaign-settings/severed-lands/{asset['path']}" not in inventory
            assert resources[f"../artwork/{asset['path']}"] == f"artwork/{asset['path']}"
            assert ImageData(raw).dimensions == (
                entry["outputs"][purpose]["width"], entry["outputs"][purpose]["height"]
            )
    assert not any("character-art.json" in path for path in inventory)
    assert "provenance/character-art.json" not in setting.payloads
    assert rules.record["id"] == "sylvia.srd-lite.bg3"
    assert reference.record["id"] == "pneuma.srd.5.2.1.reference"


def test_bundled_lookup_never_uses_unknown_hashes_or_a_checkout_fallback(tmp_path, monkeypatch):
    entry = json.loads((ART / "character-art.json").read_bytes())["characters"][0]
    portrait = entry["visuals"]["portrait"]
    assert bundled_character_image("../private.png", portrait["sha256"]) is None
    assert bundled_character_image(portrait["path"], "0" * 64) is None
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "onefile-extracted"), raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "installed/sylvia-gm-owner.exe"))
    assert bundled_character_image(portrait["path"], portrait["sha256"]) is None
    frozen_world = tmp_path / "installed/artwork"
    catalogue = frozen_world / "character-art.json"
    catalogue.parent.mkdir(parents=True)
    catalogue.write_text(json.dumps({"format": 1, "characters": [entry]}), encoding="utf-8")
    image = frozen_world / portrait["path"]
    image.parent.mkdir(parents=True)
    expected = (ART / portrait["path"]).read_bytes()
    image.write_bytes(expected)
    assert bundled_character_image(portrait["path"], portrait["sha256"]) == expected
    image.write_bytes(b"damaged synthetic fixture")
    assert bundled_character_image(portrait["path"], portrait["sha256"]) is None


@pytest.mark.parametrize("starter_name", ("Luma Ash", "Mara Flint"))
def test_prepared_starter_reuses_art_offline_through_campaign_crop_reopen_and_transfer(
    tmp_path, monkeypatch, starter_name
):
    database = tmp_path / "owner.db"
    archive = tmp_path / "campaign.zip"
    with AuthorityOwner.open(database, "prepared-starter") as owner:
        selected = selection(owner, tmp_path)
        starter = next(item for item in selected.participants if item.name == starter_name)
        source_visuals = json.loads(json.dumps(starter.visuals))
        # The retained Plan remains pinned to its original Setting, without these new PNGs.
        world = selected.sources.setting
        assert owner._store.package_file_bytes(
            world.package_id, world.version, world.content_sha256,
            starter.visuals["portrait"]["path"], maximum_bytes=16 * 1024 * 1024,
        ) is None
        participants, state, party = materialize_library_party(
            owner, selected, operation_id="prepared", player_character_id=starter.artifact_id,
            companion_ids=(),
        )
        confirmed = owner.confirm_new_campaign(
            operation_id="confirm", display_name="Prepared artwork",
            package_lock=selected.candidate.package_lock, turn_id="construction",
            package_candidate_id=selected.candidate.candidate_id,
            builder_session_id="campaign-builder", builder_provider_id="test",
            builder_model_id="test", participants=participants, initial_state=state,
        )
        owner.begin_campaign_construction(
            operation_id="begin", campaign_id=confirmed.campaign_id, lane="customize",
            expected_revision=0, canonical_input={"selected_party": party},
        )
        actor = participants[0].participant_id
        # Export itself retains the used art before the first image view is opened.
        assert owner._store.images.list_selections(confirmed.campaign_id) == []
        before_view = tmp_path / "before-view.zip"
        owner.export_campaign(
            operation_id="export-before-view", campaign_id=confirmed.campaign_id, target=before_view
        )
        assert len(owner._store.images.list_selections(confirmed.campaign_id)) == 3
        art = ImageArt(owner)
        try:
            view = art.view("campaign-builder")
            rows = [row for row in view["selections"] if row["target_id"] == actor]
            assert {row["purpose"] for row in rows} == {"full_body", "reference_sheet", "portrait"}
            assert not art.adapters and not art._futures
            expected = art.character_assets("campaign-builder", actor)
            assert len(expected) == 3
            art.frame("campaign-builder", actor, source_visuals["portrait"]["sha256"], {
                "x": 0.45, "y": 0.3, "zoom": 1.2,
            })
            assert owner.character_visuals(confirmed.campaign_id)[actor]["frame"]["zoom"] == 1.2
            assert starter.visuals == source_visuals
            owner.export_campaign(
                operation_id="export", campaign_id=confirmed.campaign_id, target=archive
            )
        finally:
            art.close()
    # A recipient has only the exported campaign; the installed art catalogue is unavailable.
    monkeypatch.setattr(
        "sylvia_gm.application.image_art.bundled_character_image", lambda *args: None
    )
    with AuthorityOwner.open(tmp_path / "before-view-recipient.db", "early-reader") as recipient:
        recipient.recover_campaign(recovery_id="early-recover", source=before_view)
        art = ImageArt(recipient)
        try:
            assert art.character_assets("campaign-builder", actor) == expected
        finally:
            art.close()
    for path in (database, tmp_path / "recipient.db"):
        with AuthorityOwner.open(path, "prepared-reader") as owner:
            if path != database:
                owner.recover_campaign(recovery_id="recover", source=archive)
            art = ImageArt(owner)
            try:
                assert art.character_assets("campaign-builder", actor) == expected
                rows = art.view("campaign-builder")["selections"]
                portrait = next(
                    row for row in rows
                    if row["target_id"] == actor and row["purpose"] == "portrait"
                )
                assert portrait["frame"] == {"x": 0.45, "y": 0.3, "zoom": 1.2}
                assert all(
                    hashlib.sha256(raw).hexdigest() + ".png" in name
                    for name, raw in expected.items()
                )
            finally:
                art.close()
