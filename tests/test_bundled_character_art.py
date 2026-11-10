"""Prepared art uses real starter files and survives retained Plan pins and offline transfer."""

import hashlib
import json
import sys
from pathlib import Path

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_character_art import (
    bundled_character_image,
    bundled_source_visuals,
)
from sylvia_gm.application.bundled_packages import _rift_drafts
from sylvia_gm.application.campaign_library_selection import materialize_library_party
from sylvia_gm.application.image_art import ImageArt
from sylvia_gm.application.image_hooks import ImageData
from sylvia_gm.domain.image_art import validate_character_visuals
from tests.test_bundled_starter_characters import selection

ROOT = Path(__file__).resolve().parents[1]
WORLD = ROOT / "campaign-settings/severed-lands"


def test_prepared_bundles_match_starter_data_package_payloads_and_frozen_inventory():
    catalogue = json.loads((WORLD / "provenance/character-art.json").read_bytes())
    starters = json.loads((ROOT / "src/sylvia_gm/campaign/srd-starters.json").read_bytes())
    inventory = json.loads((ROOT / "src-tauri/sidecar-data-files.json").read_bytes())["files"]
    rules, reference, setting = _rift_drafts()
    declarations = json.loads(setting.payloads["content/content-declarations.json"])
    included = next(
        row["links"]["artifacts"] for row in declarations["inclusions"]
        if row["id"] == "image-hooks-selected-assets"
    )
    assert catalogue["characters"]
    for entry in catalogue["characters"]:
        visuals = validate_character_visuals(entry["visuals"])
        if entry["id"].startswith("srd-starter-"):
            source = next(row for row in starters if row["participant"]["name"] == entry["name"])
            assert source["visuals"] == visuals
        else:
            path = entry["sources"][0]["path"]
            assert bundled_source_visuals(
                "pneuma.severed-lands", path, (WORLD / path).read_bytes()
            ) == visuals
        for purpose in ("full_body", "reference_sheet", "portrait"):
            asset = entry["visuals"][purpose]
            raw = bundled_character_image(asset["path"], asset["sha256"])
            assert raw is not None and raw == setting.payloads[asset["path"]]
            assert asset["path"] in included
            assert f"campaign-settings/severed-lands/{asset['path']}" in inventory
            assert ImageData(raw).dimensions == (
                entry["outputs"][purpose]["width"], entry["outputs"][purpose]["height"]
            )
    assert "campaign-settings/severed-lands/provenance/character-art.json" in inventory
    assert rules.record["id"] == "sylvia.srd-lite.bg3"
    assert reference.record["id"] == "pneuma.srd.5.2.1.reference"


def test_bundled_lookup_never_uses_unknown_hashes_or_a_checkout_fallback(tmp_path, monkeypatch):
    entry = json.loads((WORLD / "provenance/character-art.json").read_bytes())["characters"][0]
    portrait = entry["visuals"]["portrait"]
    assert bundled_character_image("../private.png", portrait["sha256"]) is None
    assert bundled_character_image(portrait["path"], "0" * 64) is None
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert bundled_character_image(portrait["path"], portrait["sha256"]) is None
    frozen_world = tmp_path / "campaign-settings/severed-lands"
    catalogue = frozen_world / "provenance/character-art.json"
    catalogue.parent.mkdir(parents=True)
    catalogue.write_text(json.dumps({"characters": [entry]}), encoding="utf-8")
    image = frozen_world / portrait["path"]
    image.parent.mkdir(parents=True)
    expected = (WORLD / portrait["path"]).read_bytes()
    image.write_bytes(expected)
    assert bundled_character_image(portrait["path"], portrait["sha256"]) == expected
    image.write_bytes(b"damaged synthetic fixture")
    assert bundled_character_image(portrait["path"], portrait["sha256"]) is None


def test_prepared_starter_reuses_art_offline_through_campaign_crop_reopen_and_transfer(
    tmp_path, monkeypatch
):
    database = tmp_path / "owner.db"
    archive = tmp_path / "campaign.zip"
    with AuthorityOwner.open(database, "prepared-starter") as owner:
        selected = selection(owner, tmp_path)
        starter = next(item for item in selected.participants if item.name == "Mara Flint")
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
            builder_session_id="campaign-builder", builder_provider_id="test",
            builder_model_id="test", participants=participants, initial_state=state,
        )
        owner.begin_campaign_construction(
            operation_id="begin", campaign_id=confirmed.campaign_id, lane="customize",
            expected_revision=0, canonical_input={"selected_party": party},
        )
        actor = participants[0].participant_id
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
