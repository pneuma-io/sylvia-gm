"""Inherited subject artwork is scoped to its Setting and resolves from admitted metadata."""

import hashlib
import io
import json
from collections import Counter

import pytest
from scripts.inventory_inherited_artwork import inherited_artwork_subjects

from sylvia_gm.application import bundled_character_art as library
from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.application.bundled_packages import bundled_rift_archive
from sylvia_gm.application.image_art import ImageArt
from sylvia_gm.domain.authority import PackageRole, WriterRole
from sylvia_gm.domain.package_library import PackageFile, campaign_setting_entries
from sylvia_gm.domain.session import SessionMessageKind
from tests.test_image_hooks import png


@pytest.fixture(scope="module")
def inherited_subjects():
    return inherited_artwork_subjects()


def character(identity, source):
    visuals = {"appearance": identity}
    for purpose in ("full_body", "reference_sheet", "portrait"):
        digest = hashlib.sha256((identity + purpose).encode()).hexdigest()
        visuals[purpose] = {"path": f"assets/characters/{digest}.png", "sha256": digest}
    return {"id": identity, "name": identity, "visuals": visuals, "sources": [source]}


def test_inherited_inventory_binds_each_severed_subject_to_its_admitted_file(
    tmp_path, monkeypatch, inherited_subjects
):
    subjects = inherited_subjects["subjects"]
    assert len(subjects) == 330
    assert len({subject["id"] for subject in subjects}) == len(subjects)
    assert Counter(subject["kind"] for subject in subjects) == {"beast": 90, "non_beast": 240}
    setting_id = inherited_subjects["campaign_setting_id"]
    files = {
        (item["package_id"], item["path"], item["sha256"]): PackageFile(
            path=item["path"], byte_count=item["byte_count"], sha256=item["sha256"]
        )
        for item in inherited_subjects["source_files"]
    }
    assert {item.path for item in files.values()} == {
        "runtime/ruleset.yaml",
        "content/non-beast-form-index.json",
    }
    assert all(len(item["origins"]) > 1 for item in inherited_subjects["source_files"])
    catalogue = {
        "format": 1,
        "characters": [
            character(subject["id"], source)
            for subject in subjects
            for source in subject["sources"]
        ],
    }
    (tmp_path / "character-art.json").write_text(json.dumps(catalogue), encoding="utf-8")
    monkeypatch.setattr(library, "_art_root", lambda: tmp_path)
    for subject in subjects:
        assert "stats" not in subject and "mechanics" not in subject
        for source in subject["sources"]:
            assert source["campaign_setting_id"] == setting_id
            path, pointer = source["path"].split("#", 1)
            assert pointer.startswith(("/forms/", "/definitions/beast_forms/"))
            descriptor = files[(source["package_id"], path, source["sha256"])]
            visuals = library.bundled_source_visuals(
                source["package_id"],
                source["path"],
                descriptor,
                campaign_setting_ids=frozenset({setting_id}),
            )
            assert visuals["appearance"] == subject["id"]


def test_manifest_subjects_require_the_exact_file_and_setting(tmp_path, monkeypatch):
    path = "content/forms.json"
    raw = b'{"forms":{"observer":{"name":"Observer"}}}\n'
    source_hash = hashlib.sha256(raw).hexdigest()
    manifest_file = PackageFile(path=path, byte_count=len(raw), sha256=source_hash)
    reference = path + "#/forms/observer"
    source = {
        "package_id": "example.rules",
        "path": reference,
        "sha256": source_hash,
        "campaign_setting_id": "example.severed-world",
    }
    catalogue = {"format": 1, "characters": [character("Severed Observer", source)]}
    catalogue_path = tmp_path / "character-art.json"
    catalogue_path.write_text(json.dumps(catalogue), encoding="utf-8")
    monkeypatch.setattr(library, "_art_root", lambda: tmp_path)

    def lookup(**overrides):
        return library.bundled_source_visuals(
            overrides.get("package_id", "example.rules"),
            overrides.get("reference", reference),
            overrides.get("source", manifest_file),
            campaign_setting_ids=overrides.get("settings", frozenset({"example.severed-world"})),
        )

    assert lookup()["appearance"] == "Severed Observer"
    assert lookup(source=raw)["appearance"] == "Severed Observer"
    assert lookup(settings=frozenset()) is None
    assert lookup(settings=frozenset({"another.world"})) is None
    assert lookup(package_id="another.rules") is None
    assert lookup(reference=path + "#/forms/another") is None
    assert (
        lookup(source=PackageFile(path="other.json", byte_count=len(raw), sha256=source_hash))
        is None
    )
    assert lookup(source=PackageFile(path=path, byte_count=len(raw), sha256="a" * 64)) is None
    assert lookup(source=raw + b"changed") is None
    # A Setting-specific rendition is more specific than a common Ruleset image.
    catalogue["characters"].append(
        character(
            "Generic Observer",
            {key: value for key, value in source.items() if key != "campaign_setting_id"},
        )
    )
    catalogue_path.write_text(json.dumps(catalogue), encoding="utf-8")
    assert lookup()["appearance"] == "Severed Observer"
    assert lookup(settings=frozenset({"another.world"}))["appearance"] == "Generic Observer"


def test_actual_lite_inherited_art_uses_metadata_and_transfers_without_the_library(
    tmp_path, monkeypatch, inherited_subjects
):
    art_root = tmp_path / "artwork"
    art_root.mkdir()
    monkeypatch.setattr(library, "_art_root", lambda: art_root)
    source_paths = ("content/non-beast-form-index.json", "runtime/ruleset.yaml")
    references = (
        source_paths[0] + "#/forms/aboleth",
        source_paths[1] + "#/definitions/beast_forms/giant_rat",
    )
    with AuthorityOwner.open(tmp_path / "source.db", "owner") as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(bundled_rift_archive()), install_id="lite"
        )
        primary = next(
            entry for entry in candidate.package_lock.entries if entry.role is PackageRole.PRIMARY
        )
        setting = campaign_setting_entries(candidate.package_lock)[0]
        manifest = owner._store.exact_package_manifest(
            primary.package_id, primary.version, primary.sha256
        )
        files = {item.path: item for item in manifest.files}
        assert files[source_paths[1]].byte_count > 256 * 1024
        entries = []
        for index, reference in enumerate(references):
            subject = next(
                item
                for item in inherited_subjects["subjects"]
                if item["source_id"] == ("aboleth", "giant_rat")[index]
            )
            source = next(item.copy() for item in subject["sources"] if item["path"] == reference)
            assert source["package_id"] == primary.package_id
            assert source["sha256"] == files[source_paths[index]].sha256
            assert source["campaign_setting_id"] == setting.package_id
            entry = character(
                "severed-fixture-" + str(index),
                source,
            )
            for step, purpose in enumerate(("full_body", "reference_sheet", "portrait")):
                image = png(3, 3, 60 + index * 3 + step)
                path = f"assets/characters/{image.sha256}.png"
                target = art_root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(image.data)
                entry["visuals"][purpose] = {"path": path, "sha256": image.sha256}
            entries.append(entry)
        catalogue = {"format": 1, "characters": entries}
        catalogue_path = art_root / "character-art.json"
        catalogue_path.write_text(json.dumps(catalogue), encoding="utf-8")
        owner.create_campaign(
            CampaignCreate(
                campaign_id="inherited",
                display_name="Inherited art",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id="turn",
            )
        )
        owner.create_agent_session(
            campaign_id="inherited",
            role=WriterRole.DM,
            provider_id="test",
            model_id="test",
            session_id="gm",
        )
        owner.append_agent_message(
            session_id="gm",
            message_id="introduced",
            kind=SessionMessageKind.USER,
            content={"text": "The player meets two synthetic inherited-creature fixtures."},
        )
        owner.update_campaign_file(
            session_id="gm",
            update_id="record",
            basis_message_ids=("introduced",),
            updates=tuple(
                {
                    "operation": "record_entity",
                    "entity_id": identity,
                    "entity_kind": "creature",
                    "name": name,
                    "attributes": {},
                    "visibility": visibility,
                    "known_by": ["player"] if visibility == "player_known" else [],
                    "source_ref": reference,
                }
                for identity, name, visibility, reference in (
                    ("observer", "Bog Watcher", "player_known", references[0]),
                    ("beast", "Moss Rat", "player_known", references[1]),
                    ("secret", "Unseen creature", "gm_private", references[0]),
                    (
                        "unknown",
                        "Unnamed fixture",
                        "player_known",
                        source_paths[0] + "#/forms/unknown",
                    ),
                )
            ),
        )
        original_reader = owner._store.package_file_bytes

        def no_definition_read(*args, **kwargs):
            assert args[3] not in source_paths, (
                "Portrait lookup must not read inherited definition blobs"
            )
            return original_reader(*args, **kwargs)

        def no_state(*args, **kwargs):
            raise AssertionError("Portrait lookup must not load mechanics state")

        with monkeypatch.context() as metadata_only:
            metadata_only.setattr(owner._store, "package_file_bytes", no_definition_read)
            metadata_only.setattr(owner._store, "snapshot", no_state)
            assert set(owner.character_visuals("inherited")) == {"observer", "beast"}
            for entry in entries:
                entry["sources"][0]["campaign_setting_id"] = "another.world"
            catalogue_path.write_text(json.dumps(catalogue), encoding="utf-8")
            assert owner.character_visuals("inherited") == {}
            for entry in entries:
                entry["sources"][0]["campaign_setting_id"] = setting.package_id
            catalogue_path.write_text(json.dumps(catalogue), encoding="utf-8")
        art = ImageArt(owner, adapters=(), settings=lambda: {"mode": "off"})
        try:
            assert len(art.view("gm", include_targets=False)["selections"]) == 6
            portrait = owner.character_visuals("inherited")["observer"]["portrait"]["sha256"]
            frame = {"x": 0.3, "y": 0.4, "zoom": 1.8}
            art.frame("gm", "observer", portrait, frame)
            expected = art.character_assets("gm", "observer")
            archive = tmp_path / "campaign.sylvia-gm-campaign"
            owner.export_campaign(operation_id="export", campaign_id="inherited", target=archive)
            assert art._futures == {}
        finally:
            art.close()
    monkeypatch.setattr(library, "_art_root", lambda: tmp_path / "library-not-installed")
    with AuthorityOwner.open(tmp_path / "recipient.db", "recipient") as owner:
        owner.recover_campaign(recovery_id="imported", source=archive)
        art = ImageArt(owner, adapters=(), settings=lambda: {"mode": "off"})
        try:
            assert len(art.view("gm", include_targets=False)["selections"]) == 6
            assert art.character_assets("gm", "observer") == expected
            assert owner.character_visuals("inherited")["observer"]["frame"] == frame
            assert art._futures == {}
        finally:
            art.close()
