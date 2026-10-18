"""Content notices survive the actual package materialization/admission boundary."""

import hashlib
import json
from pathlib import Path

from sylvia_gm.application.bundled_packages import _rift_drafts
from sylvia_gm.application.package_materialization import materialize_package_archive
from sylvia_gm.infrastructure.package_archive import admit_package_archive


def test_bundled_content_preserves_source_notices_and_implementation_publisher():
    with (
        materialize_package_archive(_rift_drafts()) as artifact,
        admit_package_archive(artifact.archive) as admitted,
    ):
        notices = {
            item.manifest.package_id: b"".join(admitted.iter_file_chunks(item)).decode()
            for item in admitted.files
            if item.file.path == "ATTRIBUTION.md"
        }
        assert set(notices) == {
            "sylvia.srd-lite.bg3",
            "pneuma.srd.5.2.1.reference",
            "pneuma.severed-lands",
        }
        for manifest in admitted.manifests:
            assert manifest.provenance["publisher"] == "Pneuma IO"
            notice = notices[manifest.package_id]
            assert "Wizards" in notice
            assert "https://creativecommons.org/licenses/by/4.0/legalcode" in notice
        assert "System Reference Document 5.1" in notices["pneuma.severed-lands"]
        assert "System Reference Document 5.2.1" in notices["pneuma.srd.5.2.1.reference"]

    root = Path(__file__).resolve().parents[1]
    bundle = json.loads((root / "src-tauri/tauri.conf.json").read_text(encoding="utf-8"))["bundle"]
    resources = bundle["resources"]
    for source, target in resources.items():
        assert (root / "src-tauri" / source).is_file()
        assert target.startswith("licenses/")
    assert set(resources.values()) >= {"licenses/SRD-5.2.1.md", "licenses/Severed-Lands.md"}


def test_adopted_polyform_delivers_standard_text_and_additional_permissions():
    root = Path(__file__).resolve().parents[1]
    expected = {
        "LICENSE": (root / "LICENSE").read_bytes(),
        "COPYRIGHT": (root / "COPYRIGHT").read_bytes(),
        "LICENSING-STATUS.md": (root / "LICENSING-STATUS.md").read_bytes(),
        "SYLVIA-ADDITIONAL-PERMISSIONS.md": (
            root / "SYLVIA-ADDITIONAL-PERMISSIONS.md"
        ).read_bytes(),
        "MIT-PREVIOUS-GRANT.txt": (root / "licenses/MIT-PREVIOUS-GRANT.txt").read_bytes(),
    }
    assert hashlib.sha256(expected["LICENSE"]).hexdigest() == (
        "ffcca38841adb694b6f380647e15f17c446a4d1656fed51a1e2041d064c94cc8"
    )
    assert b"Adopted on 16 October 2026" in expected["SYLVIA-ADDITIONAL-PERMISSIONS.md"]
    assert b"The Licensor grants each recipient" in expected["SYLVIA-ADDITIONAL-PERMISSIONS.md"]
    assert b"not an operative licence grant" not in expected["SYLVIA-ADDITIONAL-PERMISSIONS.md"]
    assert (
        b"Copyright (c) 2026 Daniel Young. Published under the Pneuma IO brand."
        in (expected["COPYRIGHT"])
    )
    with (
        materialize_package_archive(_rift_drafts()) as artifact,
        admit_package_archive(artifact.archive) as admitted,
    ):
        delivered = {
            (item.manifest.package_id, item.file.path): b"".join(admitted.iter_file_chunks(item))
            for item in admitted.files
        }
        for package in ("sylvia.srd-lite.bg3", "pneuma.srd.5.2.1.reference"):
            for name, content in expected.items():
                assert delivered[package, name] == content
            notice = delivered[package, "ATTRIBUTION.md"]
            assert b"PolyForm Noncommercial 1.0.0" in notice
            assert b"adopted on 16 October 2026" in notice
            assert b"not adopted or an operative grant" not in notice
            assert (package, "SYLVIA-ADDITIONAL-PERMISSIONS.DRAFT.md") not in delivered
            assert b"implementation code uses MIT" not in notice
        # The independently granted Setting notice is preserved through admission.
        assert (
            b"retain their existing MIT grant"
            in delivered["pneuma.severed-lands", "ATTRIBUTION.md"]
        )
    resources = json.loads((root / "src-tauri/tauri.conf.json").read_text())["bundle"]["resources"]
    for name in expected:
        assert "licenses/" + name in resources.values()
