"""Characters and their art cross actual owner libraries without loose files or generation."""

import asyncio
import io
import json
import zipfile
from pathlib import Path

import aiohttp
import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.character_bundle import read_character_bundle
from sylvia_gm.application.character_card_import import (
    card_portrait,
    load_import_original,
    save_character_card_import,
)
from sylvia_gm.application.character_profiles import load_character_profile, save_character_profile
from sylvia_gm.application.character_transfer import (
    cancel_character_preview,
    confirm_character_preview,
    export_character_source,
    import_character_source,
    load_character_source,
    stage_character_preview,
)
from sylvia_gm.application.image_art import ImageArt
from sylvia_gm.application.image_hooks import ImageData
from sylvia_gm.application.ready_campaigns import ready_campaign_catalog, resolve_ready_campaign
from sylvia_gm.domain._content_schema_character_profile import EXAMPLE as LEGACY
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.interfaces.webui import OwnerProcess, WebUIError
from tests.test_character_art_import import card_png
from tests.test_image_art import Images
from tests.test_image_hooks import png
from tests.test_webui import Browser


def export(owner, root, source):
    stream = io.BytesIO()
    result = export_character_source(owner, root, source, target=stream)
    return stream.getvalue(), result


def test_real_owner_and_browser_transfer_selected_character_bytes_without_host_paths(tmp_path):
    profile = save_character_profile(
        tmp_path, operation_id="source", source=canonical_json_bytes(LEGACY)
    )

    async def check():
        owner = OwnerProcess(tmp_path)
        await owner.start()
        browser = await Browser().open(owner)
        try:
            params = {
                "exportId": "selected-character",
                "artifactId": profile.artifact_id,
                "kind": "character_profile",
                "expectedContentSha256": profile.content_sha256,
            }
            result = await browser.call("export_character_file", params)
            assert "artifact_path" not in result and str(tmp_path) not in json.dumps(result)
            response = await browser.client.get(browser.url + result["download"])
            raw = await response.read()
            assert response.status == 200
            assert ".sylvia-gm-character" in response.headers["Content-Disposition"]
            bundle = read_character_bundle(io.BytesIO(raw))
            assert bundle.character["name"] == LEGACY["name"]
            exported_path = tmp_path / "exports/selected-character.sylvia-gm-character"
            assert exported_path.read_bytes() == raw

            response = await browser.raw(
                "export_character_file", {**params, "expectedContentSha256": "0" * 64}
            )
            assert (await response.json())["ok"] is False
            assert exported_path.read_bytes() == raw

            async def upload():
                form = aiohttp.FormData()
                form.add_field("file", raw, filename="my-character.sylvia-gm-character")
                response = await browser.client.post(
                    browser.url + "/api/upload/character_bundle", headers=browser.headers, data=form
                )
                value = await response.json()
                assert response.status == 200 and value["ok"], value
                assert str(tmp_path) not in json.dumps(value)
                return value["result"]

            preview = await upload()
            assert preview["source_sha256"] == bundle.content_sha256
            token = {"sourceSha256": preview["source_sha256"]}
            await browser.call("cancel_character_bundle_import", token)
            response = await browser.raw("confirm_character_bundle_import", token)
            assert (await response.json())["ok"] is False
            assert await upload() == preview
            imported = await browser.call("confirm_character_bundle_import", token)
            assert imported["status"] == "imported"
            assert await browser.call("confirm_character_bundle_import", token) == imported
            assert imported["character"]["id"] != profile.artifact_id
            # Native protocol reads the same immutable saved entry as the browser.
            native = await owner.call(
                "export_character_file",
                {
                    "export_id": "native-copy",
                    "artifact_id": imported["character"]["id"],
                    "kind": "character_profile",
                    "expected_content_sha256": imported["character"]["content_sha256"],
                },
            )
            assert Path(native["artifact_path"]).is_file()
            assert not list(tmp_path.glob(".webui-upload-*"))
        finally:
            await browser.close()

    asyncio.run(check())


def test_owner_restart_retires_abandoned_previews_but_keeps_receipts_and_chosen_files(tmp_path):
    source_root, recipient_root = tmp_path / "source", tmp_path / "recipient"
    source_root.mkdir()
    recipient_root.mkdir()
    files = []
    with AuthorityOwner.open(source_root / "owner.db", "preview-source") as source_owner:
        for index in range(6):
            profile = save_character_profile(
                source_root, operation_id=f"source-{index}",
                source=canonical_json_bytes({**LEGACY, "name": f"Preview {index}"}),
            )
            raw, _ = export(source_owner, source_root, profile)
            path = tmp_path / f"chosen-{index}.sylvia-gm-character"
            path.write_bytes(raw)
            files.append((path, raw))

    async def check():
        owner = OwnerProcess(recipient_root)
        await owner.start()
        try:
            async def preview(index):
                return await owner.call(
                    "preview_character_bundle_file", {"source_path": str(files[index][0])}
                )

            first = await preview(0)
            token = {"source_sha256": first["source_sha256"]}
            imported = await owner.call("confirm_character_bundle_import", token)
            abandoned = await preview(1)
            for index in range(2, 5):
                await preview(index)
            with pytest.raises(WebUIError, match="Import or cancel a pending"):
                await preview(5)
            previews = recipient_root / "character-bundle-previews"
            unrelated = previews / "keep.txt"
            unrelated.write_text("not a pending preview", encoding="utf-8")
            await owner.close()

            owner = OwnerProcess(recipient_root)
            await owner.start()
            assert {path.name for path in previews.iterdir()} == {
                first["source_sha256"] + ".json", "keep.txt",
            }
            assert unrelated.read_text(encoding="utf-8") == "not a pending preview"
            assert await owner.call("confirm_character_bundle_import", token) == imported
            with pytest.raises(WebUIError, match="preview is no longer available") as unavailable:
                await owner.call("confirm_character_bundle_import", {
                    "source_sha256": abandoned["source_sha256"],
                })
            assert str(recipient_root) not in str(unavailable.value)
            assert (await preview(5))["status"] == "preview"
            assert all(path.read_bytes() == raw for path, raw in files)
        finally:
            await owner.close()

    asyncio.run(check())


def test_card_profile_crop_original_and_refs_survive_preview_import_edit_and_reexport(tmp_path):
    original = card_png()
    source_root = tmp_path / "source"
    source_root.mkdir()
    with AuthorityOwner.open(source_root / "owner.db", "source") as owner:
        saved = save_character_card_import(source_root, operation_id="card", source=original)
        profile = load_character_profile(source_root, saved["id"])
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="source-chat")
        art = ImageArt(owner)
        try:
            art.restore_profile("source-chat", profile, source_root)
            portrait = art.view("source-chat")["selections"][0]
            frame = {"x": 0.42, "y": 0.31, "zoom": 1.6}
            art.frame("source-chat", profile.artifact_id, portrait["sha256"], frame)
            archive, receipt = export(owner, source_root, profile)
            assert receipt["image_count"] == 1 and receipt["original_card_included"] is True
            assert export(owner, source_root, profile)[0] == archive
            assert art.adapters == () and not art._futures
        finally:
            art.close()
    selected = tmp_path / "selected.sylvia-gm-character"
    selected.write_bytes(archive)
    recipient_root = tmp_path / "recipient"
    recipient_root.mkdir()
    with AuthorityOwner.open(recipient_root / "owner.db", "recipient") as owner:
        preview = stage_character_preview(recipient_root, selected)
        assert preview["adaptation_required"] is True and preview["image_count"] == 1
        selected.write_bytes(b"The user's selected file changed after preview")
        received = confirm_character_preview(owner, recipient_root, preview["source_sha256"])
        assert (
            confirm_character_preview(owner, recipient_root, preview["source_sha256"]) == received
        )
        assert (
            cancel_character_preview(recipient_root, preview["source_sha256"])["status"]
            == "cancelled"
        )
        assert not (
            recipient_root / "character-bundle-previews" / preview["source_sha256"]
        ).exists()
        profile = load_character_profile(recipient_root, received["character"]["id"])
        assert profile.profile["visuals"]["frame"] == frame
        assert (
            load_import_original(recipient_root, profile.profile["import_source"]["sha256"])
            == original
        )
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="received-chat")
        art = ImageArt(owner)
        try:
            art.restore_profile("received-chat", profile, recipient_root)
            assert set(art.character_assets("received-chat", profile.artifact_id).values()) == {
                card_portrait(original)
            }
            data = json.loads(canonical_json_bytes(profile.profile))
            changed = {"x": 0.37, "y": 0.28, "zoom": 1.9}
            art.frame("received-chat", profile.artifact_id, portrait["sha256"], changed)
            data["visuals"] = art.visuals_for_publication(
                "received-chat", str(profile.profile["name"]), source_target_id=profile.artifact_id
            )
            updated = save_character_profile(
                recipient_root, operation_id="cropped-copy", source=canonical_json_bytes(data)
            )
            forwarded, _ = export(owner, recipient_root, updated)
        finally:
            art.close()
    third_root = tmp_path / "third"
    third_root.mkdir()
    with AuthorityOwner.open(third_root / "owner.db", "third") as owner:
        bundle = read_character_bundle(io.BytesIO(forwarded))
        received = import_character_source(
            owner, third_root, io.BytesIO(forwarded), expected_content_sha256=bundle.content_sha256
        )
        profile = load_character_profile(third_root, received["character"]["id"])
        assert profile.profile["visuals"]["frame"] == changed
        assert (
            load_import_original(third_root, profile.profile["import_source"]["sha256"]) == original
        )
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="third-chat")
        images = Images()
        art = ImageArt(owner, adapters=(images,), settings=lambda: {"mode": "auto"})
        try:
            art.restore_profile("third-chat", profile, third_root)
            row = art.request(
                "third-chat",
                operation_id="body",
                target_id=profile.artifact_id,
                purpose="full_body",
                brief="Keep the imported character's identity",
            )
            assert art._futures[row["operation_id"]].result(timeout=15)["status"] == "succeeded"
            assert images.calls[0].reference_images == (ImageData(card_portrait(original)),)
        finally:
            art.close()


def test_legacy_profile_keeps_long_authored_text_and_portable_art_without_format_rewrite(tmp_path):
    source_root, received_root = tmp_path / "legacy", tmp_path / "received"
    source_root.mkdir()
    received_root.mkdir()
    text = ("# Vela\n\n" + "A literal authored paragraph.\n" * 3000).rstrip()
    with AuthorityOwner.open(source_root / "owner.db", "legacy") as owner:
        profile = save_character_profile(
            source_root,
            operation_id="legacy",
            source=canonical_json_bytes({**LEGACY, "authored_profile": text}),
        )
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="legacy-chat")
        art = ImageArt(owner)
        try:
            art.restore_card_portrait(
                "legacy-chat",
                profile.artifact_id,
                name="Vela",
                appearance="Blue coat",
                image=png().data,
            )
            art.bind_publication(
                "legacy-chat", profile.artifact_id, profile.content_sha256, name="Vela"
            )
            archive, _ = export(owner, source_root, profile)
        finally:
            art.close()
    bundle = read_character_bundle(io.BytesIO(archive))
    assert bundle.character == {**LEGACY, "authored_profile": text}
    assert bundle.manifest["visuals"]["portrait"]
    with AuthorityOwner.open(received_root / "owner.db", "receiver") as owner:
        received = import_character_source(
            owner, received_root, io.BytesIO(archive), expected_content_sha256=bundle.content_sha256
        )
        restored = load_character_profile(received_root, received["character"]["id"])
        assert restored.content_sha256 == profile.content_sha256
        assert restored.profile["authored_profile"] == text
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="received-chat")
        art = ImageArt(owner)
        try:
            art.restore_profile("received-chat", restored, received_root)
            assert list(art.character_assets("received-chat", restored.artifact_id).values()) == [
                png().data
            ]
            assert art.adapters == () and not art._futures
        finally:
            art.close()


def test_actual_lite_character_requires_exact_packages_and_restores_prepared_images_offline(
    tmp_path,
):
    source_root, received_root = tmp_path / "source", tmp_path / "received"
    source_root.mkdir()
    received_root.mkdir()
    with AuthorityOwner.open(source_root / "owner.db", "source") as owner:
        ready = next(row for row in ready_campaign_catalog() if row["id"] == "last-cart-through")
        selected, party = resolve_ready_campaign(
            owner, source_root, ready["id"], ready["content_sha256"]
        )
        source = party[0]
        archive, receipt = export(owner, source_root, source)
        assert receipt["image_count"] == 3 and receipt["adaptation_required"] is False
        packages = io.BytesIO()
        owner.export_package_candidate(selected.candidate.candidate_id, packages)
    bundle = read_character_bundle(io.BytesIO(archive))
    with AuthorityOwner.open(received_root / "owner.db", "receiver") as owner:
        with pytest.raises(CommandRejected, match="matching Ruleset"):
            import_character_source(
                owner,
                received_root,
                io.BytesIO(archive),
                expected_content_sha256=bundle.content_sha256,
            )
        assert not (received_root / "participants").exists()
        owner.install_package_archive(io.BytesIO(packages.getvalue()), install_id="exact-packages")
        received = import_character_source(
            owner, received_root, io.BytesIO(archive), expected_content_sha256=bundle.content_sha256
        )
        member = received["character"]
        saved = load_character_source(
            owner,
            received_root,
            artifact_id=member["id"],
            expected_content_sha256=member["content_sha256"],
            kind=member["kind"],
        )
        assert saved.participant == source.participant
        assert saved.entity_state == source.entity_state
        assert saved.package_sha256 == source.package_sha256
        assert saved.campaign_setting_sha256 == source.campaign_setting_sha256
        assert saved.visuals["frame"] == source.visuals["frame"]
        expected = {path: raw for path, raw in bundle.files.items() if path.endswith(".png")}
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="received-chat")
        art = ImageArt(owner)
        try:
            art.restore_visuals("received-chat", saved.artifact_id, saved.visuals, name=saved.name)
            assert art.character_assets("received-chat", saved.artifact_id) == expected
        finally:
            art.close()
    with AuthorityOwner.open(received_root / "owner.db", "reopened") as owner:
        art = ImageArt(owner)
        try:
            assert art.character_assets("received-chat", saved.artifact_id) == expected
        finally:
            art.close()


@pytest.mark.parametrize("change", ["extra", "traversal", "duplicate", "crc", "unreferenced-image"])
def test_rejects_damaged_or_unexpected_character_bundle_files_before_import(tmp_path, change):
    with AuthorityOwner.open(tmp_path / "owner.db", "source") as owner:
        profile = save_character_profile(
            tmp_path, operation_id="profile", source=canonical_json_bytes(LEGACY)
        )
        raw, _ = export(owner, tmp_path, profile)
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as original, zipfile.ZipFile(output, "w") as altered:
        for name in original.namelist():
            value = original.read(name)
            altered.writestr(
                name, b"changed" if change == "crc" and name == "character.json" else value
            )
        if change == "extra":
            altered.writestr("notes.txt", b"must not travel")
        elif change == "traversal":
            altered.writestr("../escape", b"must not escape")
        elif change == "duplicate":
            altered.writestr("character.json", original.read("character.json"))
        elif change == "unreferenced-image":
            altered.writestr("assets/characters/" + "a" * 64 + ".png", png().data)
    with pytest.raises(ValueError):
        read_character_bundle(io.BytesIO(output.getvalue()))
