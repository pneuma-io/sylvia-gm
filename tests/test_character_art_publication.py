"""Builder publication carries generated character PNGs into a fresh recipient's package."""

import io
import json

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.image_art import ImageArt
from sylvia_gm.application.package_character_art import include_character_art
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.builder._setting_draft_check import check_setting_draft
from sylvia_gm.builder._setting_materializer import materialize_setting_draft
from sylvia_gm.domain.package_library import campaign_setting_entries, canonical_json_bytes
from tests.test_character_content_definition import _character
from tests.test_image_art import Images
from tests.test_setting_draft_check import ARGS, _requirements, _setting_files, _source_candidate


def test_generated_art_is_packaged_checked_and_restored_without_source_database(tmp_path):
    workspace = tmp_path / "workspace"
    with AuthorityOwner.open(tmp_path / "source.db", "source") as owner:
        candidate = _source_candidate(owner, workspace)
        _setting_files(workspace, _requirements())
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="builder")
        images = Images()
        art = ImageArt(owner, adapters=(images,), settings=lambda: {"mode": "auto"})
        row = art.request(
            "builder",
            operation_id="keeper",
            purpose="character",
            name="Keeper",
            appearance="Blue coat, silver hair",
            brief="Painterly science fiction",
        )
        for _ in range(3):
            art._futures[row["operation_id"]].result(timeout=15)
            row = art.view("builder")["requests"][0]
        bundle = art.view("builder")["character_visuals"][row["target_id"]]
        expected_assets = art.character_assets("builder", row["target_id"])
        character_path = workspace / "setting-draft" / "files" / "characters" / "keeper.json"
        character_path.parent.mkdir()
        character_path.write_bytes(canonical_json_bytes({**_character(), "visuals": bundle}))
        original = character_path.read_bytes()

        def reader(reference):
            return art.package_asset("builder", reference)

        assert art.visuals_for_publication("builder", "Keeper") == bundle
        checked = check_setting_draft(
            owner,
            workspace,
            candidate_id=candidate.candidate_id,
            image_asset_reader=reader,
            **ARGS,
        )
        assert checked["published"] is False
        with materialize_setting_draft(
            owner,
            workspace,
            candidate_id=candidate.candidate_id,
            image_asset_reader=reader,
            **ARGS,
        ) as materialized:
            archive = materialized.archive.read()
            assert materialized.closure_sha256 == checked["closure_sha256"]
        assert character_path.read_bytes() == original
        assert not (workspace / "setting-draft" / "files" / "assets").exists()
        assert len(images.calls) == 3
        art.close()
    with AuthorityOwner.open(tmp_path / "recipient.db", "recipient") as recipient:
        candidate = recipient.install_package_archive(io.BytesIO(archive), install_id="received")
        setting = campaign_setting_entries(candidate.package_lock)[0]

        def read_asset(path):
            return recipient._store.package_file_bytes(
                setting.package_id,
                setting.version,
                setting.sha256,
                path,
                maximum_bytes=16 * 1024 * 1024,
            )

        received = json.loads(read_asset("characters/keeper.json"))
        recipient.create_builder_chat(provider_id="test", model_id="test", session_id="recipient")
        art = ImageArt(recipient)
        art.restore_visuals(
            "recipient", "keeper", received["visuals"], name="Keeper", asset_reader=read_asset
        )
        assert art.character_assets("recipient", "keeper") == expected_assets
        assert art.view("recipient")["needed_art"] == []
        art.close()


def test_publication_rejects_missing_image_assets_without_mutating_the_draft():
    character = {
        **_character(),
        "visuals": {
            "appearance": "Blue coat",
            "full_body": {"sha256": "a" * 64, "operation_id": "body"},
            "reference_sheet": {"sha256": "b" * 64, "operation_id": "sheet"},
        },
    }
    original = canonical_json_bytes(character)
    draft = NormalizedPackageDraft({}, {"characters/keeper.json": original}, 4)
    with pytest.raises(ValueError, match="missing"):
        include_character_art(draft)
    assert draft.payloads == {"characters/keeper.json": original}
