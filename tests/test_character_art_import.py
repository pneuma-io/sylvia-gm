"""Usable card portraits and portable character assets through the actual saved owner."""

import base64
import hashlib
import json
import struct
import zlib

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.character_card_import import (
    card_portrait,
    load_import_original,
    preview_character_card,
    save_character_card_import,
)
from sylvia_gm.application.character_profiles import load_character_profile
from sylvia_gm.application.image_art import ImageArt
from sylvia_gm.application.image_hooks import ImageData, ImageHookError
from sylvia_gm.domain.image_art import validate_character_visuals
from tests.test_image_art import Images
from tests.test_image_hooks import png
from tests.test_rich_character_profiles import card


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def card_png():
    image = png(32, 48, 72).data
    text = b"chara\0" + base64.b64encode(json.dumps(card(2)).encode())
    return image[:-12] + chunk(b"tEXt", text) + image[-12:]


def portable_art():
    assets = {}
    visuals = {"appearance": "Silver hair, brown eyes, blue coat"}
    for index, purpose in enumerate(("full_body", "reference_sheet", "portrait")):
        pixels = png(32, 48, 40 + index).data
        image = pixels[:33] + chunk(b"bKGD", b"\0" * 6) + pixels[33:]
        digest = hashlib.sha256(image).hexdigest()
        path = f"assets/characters/{digest}.png"
        assets[path] = image
        visuals[purpose] = {"sha256": digest, "path": path}
    visuals["frame"] = {"x": 0.4, "y": 0.3, "zoom": 1.7}
    return visuals, assets


def test_portable_assets_restore_on_fresh_owner_without_generation_and_survive_reopen(tmp_path):
    visuals, assets = portable_art()
    assert validate_character_visuals(visuals) == visuals
    provider = Images()
    database = tmp_path / "recipient.db"
    with AuthorityOwner.open(database, "recipient") as owner:
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="builder")
        service = ImageArt(owner, adapters=(provider,), settings=lambda: {"mode": "off"})
        service.restore_visuals("builder", "keeper", visuals, assets=assets, name="Keeper")
        view = service.view("builder")
        assert view["needed_art"] == []
        assert view["targets"] == [{"id": "keeper", "kind": "character", "name": "Keeper"}]
        assert len(view["selections"]) == 3
        portrait = next(row for row in view["selections"] if row["purpose"] == "portrait")
        assert service.adopt("builder", portrait["operation_id"], expected_sha256=None) == portrait
        assert service.character_assets("builder", "keeper") == assets
        service.close()
    with AuthorityOwner.open(database, "reopened") as owner:
        service = ImageArt(owner, adapters=(provider,), settings=lambda: {"mode": "off"})
        reopened = service.view("builder")
        assert reopened["targets"][0]["name"] == "Keeper"
        assert reopened["character_visuals"]["keeper"]["frame"] == visuals["frame"]
        assert service.character_assets("builder", "keeper") == assets
        service.close()
    assert provider.calls == []


def test_corrupt_package_art_does_not_partially_adopt(tmp_path):
    visuals, assets = portable_art()
    assets[visuals["reference_sheet"]["path"]] = png().data
    with AuthorityOwner.open(tmp_path / "art.db", "owner") as owner:
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="builder")
        service = ImageArt(owner)
        with pytest.raises(ValueError, match="saved identity"):
            service.restore_visuals("builder", "keeper", visuals, assets=assets)
        assert owner._store.images.list_selections("builder") == []
        assert owner._store.images.list_requests(campaign_id=None, session_id="builder") == []
        service.close()


@pytest.mark.parametrize(
    "path", ["../portrait.png", "C:/portrait.png", "https://x/a.png", "a\\b.png"]
)
def test_character_assets_cannot_point_outside_the_package(path):
    visuals, _ = portable_art()
    visuals["portrait"]["path"] = path
    with pytest.raises(ValueError):
        validate_character_visuals(visuals)


def test_imported_card_is_usable_offline_and_guides_first_full_body_generation(tmp_path):
    original = card_png()
    preview = preview_character_card(original)
    assert preview.portrait == card_portrait(original)
    assert b"chara" not in preview.portrait
    assert b"Run a command" not in preview.portrait
    saved = save_character_card_import(tmp_path, operation_id="card", source=original)
    source = load_character_profile(tmp_path, saved["id"])
    provider = Images()
    preferences = {"mode": "off"}
    with AuthorityOwner.open(tmp_path / "card.db", "owner") as owner:
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="builder")
        service = ImageArt(owner, adapters=(provider,), settings=lambda: dict(preferences))
        service.restore_profile("builder", source, tmp_path)
        view = service.view("builder")
        portrait = view["selections"][0]
        assert portrait["purpose"] == "portrait"
        assert view["targets"][0]["name"] == "Vela"
        assert owner._store.images.image(portrait["sha256"]) == preview.portrait
        assert provider.calls == []
        preferences["mode"] = "auto"
        row = service.request(
            "builder",
            operation_id="body",
            target_id=source.artifact_id,
            purpose="full_body",
            brief="Preserve Vela's appearance from her card",
        )
        assert service._futures[row["operation_id"]].result(timeout=15)["status"] == "succeeded"
        assert provider.calls[0].reference_images == (ImageData(preview.portrait),)
        service.restore_profile("builder", source, tmp_path)
        assert len(provider.calls) == 1
        service.close()
    assert load_import_original(tmp_path, preview.source_sha256) == original


def test_builder_interview_adaptation_loads_selected_card_art_without_an_agent_art_tool(tmp_path):
    from sylvia_gm.provider._base import ProviderResponse
    from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness
    from tests.test_builder_character_profiles import prepare, submit
    from tests.test_builder_library import LibraryProvider
    from tests.test_campaign_library_selection import seed_campaign_library

    database = tmp_path / "interview.db"
    with AuthorityOwner.open(database, "seed") as owner:
        candidate, setting, plan, _ = seed_campaign_library(owner, tmp_path)
    saved = save_character_card_import(tmp_path, operation_id="card", source=card_png())
    profile = load_character_profile(tmp_path, saved["id"])
    provider = LibraryProvider(responses=[ProviderResponse(content="Let's adapt Vela.")])
    harness = _OwnerHarness(database, provider)
    try:
        draft = prepare(harness, candidate, setting, plan)
        child = harness.request(
            "adapt-card",
            "start_guided_creation_subflow",
            {
                "operation_id": "adapt-card",
                "campaign_id": draft["campaign_id"],
                "parent_session_id": draft["builder_session_id"],
                "expected_construction_revision": 4,
                "purpose": "companion",
                "visible_answer": "Adapt Vela",
                "character_profile_id": profile.artifact_id,
                "character_profile_sha256": profile.content_sha256,
            },
        )
        session_id = child["child_session_id"]
        assert submit(harness, session_id)["interruption"] is None
        view = harness.request("art", "image_art", {"session_id": session_id, "action": "view"})
        assert view["targets"][0]["name"] == "Vela"
        assert len(view["selections"]) == 1
        assert view["selections"][0]["purpose"] == "portrait"
        assert view["selections"][0]["metadata"]["source"] == "character_card"
        assert not any(
            "image_art" in message.get("tool_call_id", "")
            for message in provider.calls[-1]["messages"]
        )
    finally:
        harness.close()


@pytest.mark.parametrize(
    "color,depth,interlace,raw,extra",
    [
        (0, 1, 0, b"\0\x80", b""),
        (3, 1, 0, b"\0\x80", chunk(b"PLTE", b"\xff\0\0\0\xff\0")),
        (4, 8, 0, b"\0\x80\xff", b""),
        (2, 16, 0, b"\0" + b"\xff\0" * 3, b""),
        (2, 8, 1, b"\0\xff\0\0", b""),
    ],
)
def test_common_local_png_formats_are_validated(color, depth, interlace, raw, extra):
    image = ImageData(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, depth, color, 0, 0, interlace))
        + extra
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    assert image.dimensions == (1, 1)
    invalid = ImageData(image.data[:-12] + chunk(b"IDAT", zlib.compress(raw)) + image.data[-12:])
    with pytest.raises(ImageHookError):
        _ = invalid.dimensions
