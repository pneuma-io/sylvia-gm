"""Known NPC/enemy identities reuse prepared source art without generation or name matching."""

import hashlib
import io
import json
from pathlib import Path

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.application.bundled_character_art import bundled_source_visuals
from sylvia_gm.application.bundled_packages import bundled_rift_archive
from sylvia_gm.application.image_art import ImageArt
from sylvia_gm.domain.authority import WriterRole
from sylvia_gm.domain.session import SessionMessageKind

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "content/monsters/anchor-sentinel.md"


def test_shared_source_subjects_are_exact_and_ambiguous_bindings_are_refused(tmp_path, monkeypatch):
    from sylvia_gm.application import bundled_character_art as library

    raw = b"Thread, Hook and Needle are three distinct siblings.\n"
    digest = hashlib.sha256(raw).hexdigest()
    reference = {"package_id": "example.world", "path": "npcs/siblings.md", "sha256": digest}

    def character(identity, subject=None):
        visual = {"appearance": identity}
        for purpose in ("full_body", "reference_sheet", "portrait"):
            image_sha = hashlib.sha256((identity + purpose).encode()).hexdigest()
            visual[purpose] = {"path": f"assets/characters/{image_sha}.png", "sha256": image_sha}
        return {
            "id": identity,
            "name": identity,
            "visuals": visual,
            "sources": [
                {**reference, **({"subject_name": subject} if subject is not None else {})}
            ],
        }

    catalogue = {
        "format": 1,
        "characters": [character("thread", "Thread"), character("hook", "Hook")],
    }
    path = tmp_path / "character-art.json"
    path.write_text(json.dumps(catalogue), encoding="utf-8")
    monkeypatch.setattr(library, "_art_root", lambda: tmp_path)

    def read(subject=None, **overrides):
        return library.bundled_source_visuals(
            overrides.get("package", "example.world"),
            overrides.get("path", "npcs/siblings.md"),
            overrides.get("source", raw),
            subject_name=subject,
        )

    assert read("Thread")["appearance"] == "thread"
    assert read("Hook")["appearance"] == "hook"
    assert read() is None and read("Needle") is None
    assert read(" thread ")["appearance"] == "thread"
    assert read("Thread", package="another.world") is None
    assert read("Thread", path="npcs/another.md") is None
    assert read("Thread", source=raw + b"changed") is None
    catalogue["characters"].append(character("group"))
    path.write_text(json.dumps(catalogue), encoding="utf-8")
    assert read("Thread")["appearance"] == "thread"
    assert read("Rust Watcher")["appearance"] == "group"
    catalogue["characters"].append(character("another-thread", "THREAD"))
    path.write_text(json.dumps(catalogue), encoding="utf-8")
    assert read("Thread") is None


def test_prepared_source_requires_exact_identity_and_preserves_original_text():
    world = ROOT / "campaign-settings/severed-lands"
    raw = (world / SOURCE).read_bytes()
    bundle = bundled_source_visuals("pneuma.severed-lands", SOURCE, raw)
    assert bundle is not None and "portrait" in bundle
    assert bundled_source_visuals("another-world", SOURCE, raw) is None
    assert bundled_source_visuals("pneuma.severed-lands", "npcs/anchor-sentinel.md", raw) is None
    assert bundled_source_visuals("pneuma.severed-lands", SOURCE, raw + b"changed") is None
    # The authored text preceding the added appearance section is retained for old Plan editions.
    earlier = raw.replace(b"\r\n", b"\n").split(b"\n\n## Appearance and artwork")[0] + b"\n"
    assert bundled_source_visuals("pneuma.severed-lands", SOURCE, earlier) == bundle
    assert b"**Eye Rays.**" in earlier and b"cr: '3'" in earlier


def test_known_enemy_gets_art_in_normal_status_and_transfers_without_catalogue(
    tmp_path, monkeypatch
):
    archive = tmp_path / "campaign.zip"
    with AuthorityOwner.open(tmp_path / "source.db", "prepared-enemy") as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(bundled_rift_archive()), install_id="prepared-source"
        )
        owner.create_campaign(CampaignCreate(
            campaign_id="enemies", display_name="Prepared enemies", writer=WriterRole.DM,
            package_lock=candidate.package_lock, turn_id="turn",
        ))
        owner.create_agent_session(
            campaign_id="enemies", role=WriterRole.DM, provider_id="test", model_id="test",
            session_id="gm",
        )
        owner.append_agent_message(
            session_id="gm", message_id="basis", kind=SessionMessageKind.USER,
            content={"text": "The player meets a sentinel called the Rust Watcher."},
        )
        owner.update_campaign_file(
            session_id="gm", update_id="introduced", basis_message_ids=("basis",), updates=tuple(
                {"operation": "record_entity", "entity_id": identity, "entity_kind": "creature",
                 "name": name, "attributes": {}, "visibility": visibility,
                 "known_by": ["player"] if visibility == "player_known" else [],
                 "source_ref": path}
                for identity, name, visibility, path in (
                    ("watcher", "Rust Watcher", "player_known", SOURCE),
                    ("watcher-two", "Second Watcher", "player_known", SOURCE),
                    ("secret", "Hidden Sentinel", "gm_private", SOURCE),
                    ("other", "Anchor Sentinel", "player_known", "npcs/guard-miri.md"),
                    ("image-source", "Not a profile", "player_known", "assets/characters/"
                     "b5d89a10fe76c5cb60806910ee0ebd815725358fdeb72e1b2cec422e3010f2da.png"),
                )
            ),
        )
        assert set(owner.character_visuals("enemies")) == {"watcher", "watcher-two"}
        art = ImageArt(owner)
        try:
            view = art.view("gm", include_targets=False)
            rows = view["selections"]
            assert len(rows) == 6
            assert {row["target_id"] for row in rows} == {"watcher", "watcher-two"}
            assert {row["metadata"]["target_name"] for row in rows} == {
                "Rust Watcher", "Second Watcher",
            }
            assert not art.adapters and not art._futures
            expected = art.character_assets("gm", "watcher")
            assert art.character_assets("gm", "watcher-two") == expected
            portrait = next(row for row in rows if row["purpose"] == "portrait")
            art.frame("gm", "watcher", portrait["sha256"], {"x": 0.45, "y": 0.4, "zoom": 1.2})
            portraits = {
                row["target_id"]: row["frame"]
                for row in art.view("gm", include_targets=False)["selections"]
                if row["purpose"] == "portrait"
            }
            assert portraits["watcher"]["zoom"] == 1.2
            assert portraits["watcher-two"]["zoom"] == 1
            canonical = owner.character_visuals("enemies")
            assert canonical["watcher"]["frame"] == portraits["watcher"]
            assert canonical["watcher-two"]["frame"] == portraits["watcher-two"]
            def no_mechanics_snapshot(*args):
                raise AssertionError("Image metadata refresh must not load mechanics state")

            with monkeypatch.context() as metadata_only:
                metadata_only.setattr(owner._store, "snapshot", no_mechanics_snapshot)
                assert owner.character_visuals("enemies") == canonical
            owner.export_campaign(operation_id="export", campaign_id="enemies", target=archive)
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
        owner.recover_campaign(recovery_id="recover", source=archive)
        art = ImageArt(owner)
        try:
            restored = art.view("gm", include_targets=False)["selections"]
            assert len(restored) == 6
            assert {
                row["target_id"]: row["frame"] for row in restored
                if row["purpose"] == "portrait"
            } == portraits
            assert art.character_assets("gm", "watcher") == expected
            assert art.character_assets("gm", "watcher-two") == expected
            assert set(owner.character_visuals("enemies")) == {"watcher", "watcher-two"}
        finally:
            art.close()
