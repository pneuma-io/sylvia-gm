"""Separate people in one authored profile keep their prepared art through campaign transfer."""

import io
import json
from pathlib import Path

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.application.bundled_character_art import bundled_source_visuals
from sylvia_gm.application.bundled_packages import bundled_rift_archive
from sylvia_gm.application.image_art import ImageArt
from sylvia_gm.application.image_hooks import ImageData
from sylvia_gm.domain.authority import WriterRole
from sylvia_gm.domain.session import SessionMessageKind

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "npcs/lace-brothers.md"


def test_lace_brothers_have_separate_complete_bundles_and_installer_resources():
    catalogue = json.loads((ROOT / "artwork/character-art.json").read_bytes())
    entries = [
        row for row in catalogue["characters"] if row["id"].startswith("severed-npc-lace-brothers-")
    ]
    assert {row["name"] for row in entries} == {"Thread", "Hook", "Needle"}
    resources = json.loads((ROOT / "src-tauri/tauri.conf.json").read_bytes())["bundle"]["resources"]
    inventory = json.loads((ROOT / "src-tauri/sidecar-data-files.json").read_bytes())["files"]
    images = set()
    for entry in entries:
        for purpose in ("full_body", "reference_sheet", "portrait"):
            reference = entry["visuals"][purpose]
            image = ImageData((ROOT / "artwork" / reference["path"]).read_bytes())
            assert image.sha256 == reference["sha256"]
            output = entry["outputs"][purpose]
            assert image.dimensions == (output["width"], output["height"])
            portable = "artwork/" + reference["path"]
            assert resources["../" + portable] == portable
            assert portable not in inventory
            images.add(image.sha256)
    assert len(images) == 9


def test_lace_brothers_keep_distinct_images_crops_and_offline_transfer(tmp_path, monkeypatch):
    names = ("Thread", "Hook", "Needle")
    raw = (ROOT / "campaign-settings/severed-lands" / SOURCE).read_bytes()
    setting_ids = frozenset({"pneuma.severed-lands"})
    visuals = {
        name: bundled_source_visuals(
            "pneuma.severed-lands",
            SOURCE,
            raw,
            subject_name=name,
            campaign_setting_ids=setting_ids,
        )
        for name in names
    }
    assert all(visuals.values())
    assert len({value["portrait"]["sha256"] for value in visuals.values()}) == 3
    assert (
        bundled_source_visuals(
            "pneuma.severed-lands", SOURCE, raw, campaign_setting_ids=setting_ids
        )
        is None
    )
    archive = tmp_path / "brothers.zip"
    with AuthorityOwner.open(tmp_path / "source.db", "source") as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(bundled_rift_archive()), install_id="actual-lite"
        )
        owner.create_campaign(
            CampaignCreate(
                campaign_id="brothers",
                display_name="Three brothers",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id="turn",
            )
        )
        owner.create_agent_session(
            campaign_id="brothers",
            role=WriterRole.DM,
            provider_id="test",
            model_id="test",
            session_id="gm",
        )
        owner.append_agent_message(
            session_id="gm",
            message_id="basis",
            kind=SessionMessageKind.USER,
            content={"text": "Meet Thread, Hook and Needle, the three Lace Brothers."},
        )
        owner.update_campaign_file(
            session_id="gm",
            update_id="introduced",
            basis_message_ids=("basis",),
            updates=tuple(
                {
                    "operation": "record_entity",
                    "entity_id": name.lower(),
                    "entity_kind": "npc",
                    "name": name,
                    "attributes": {},
                    "visibility": "player_known",
                    "known_by": ["player"],
                    "source_ref": SOURCE,
                }
                for name in names
            ),
        )
        assert owner.character_visuals("brothers") == {
            name.lower(): visuals[name] for name in names
        }
        art = ImageArt(owner)
        try:
            assert len(art.view("gm", include_targets=False)["selections"]) == 9
            expected = {name: art.character_assets("gm", name.lower()) for name in names}
            art.frame(
                "gm",
                "hook",
                visuals["Hook"]["portrait"]["sha256"],
                {
                    "x": 0.48,
                    "y": 0.4,
                    "zoom": 1.2,
                },
            )
            saved = owner.character_visuals("brothers")
            assert saved["hook"]["frame"]["zoom"] == 1.2
            assert saved["thread"] == visuals["Thread"]
            assert saved["needle"] == visuals["Needle"]
            assert not art.adapters and not art._futures
            owner.export_campaign(operation_id="export", campaign_id="brothers", target=archive)
        finally:
            art.close()
    monkeypatch.setattr(
        "sylvia_gm.application.bundled_character_art.bundled_source_visuals",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "sylvia_gm.application.image_art.bundled_character_image", lambda *args: None
    )
    with AuthorityOwner.open(tmp_path / "recipient.db", "recipient") as owner:
        owner.recover_campaign(recovery_id="received", source=archive)
        art = ImageArt(owner)
        try:

            def portable_identity(visual):
                # Recovery supplies local receipts; every portable visual field must stay exact.
                return {
                    **visual,
                    **{
                        purpose: {
                            key: value
                            for key, value in visual[purpose].items()
                            if key != "operation_id"
                        }
                        for purpose in ("full_body", "reference_sheet", "portrait")
                    },
                }

            restored = owner.character_visuals("brothers")
            assert {
                identity: portable_identity(visual) for identity, visual in restored.items()
            } == {identity: portable_identity(visual) for identity, visual in saved.items()}
            for name in names:
                assert art.character_assets("gm", name.lower()) == expected[name]
            assert not art.adapters and not art._futures
        finally:
            art.close()
