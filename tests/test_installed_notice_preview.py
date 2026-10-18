"""Required plain-text notices can be read from their exact installed package closure."""
import hashlib
import io
from pathlib import Path

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_packages import _rift_drafts
from sylvia_gm.application.package_materialization import materialize_package_archive
from sylvia_gm.domain.errors import CommandRejected


def test_installed_legal_notices_are_readable_and_keep_exact_bytes_after_reopen(tmp_path):
    root = Path(__file__).resolve().parents[1]
    expected = {name: (root / name).read_bytes() for name in (
        "LICENSE", "COPYRIGHT", "LICENSING-STATUS.md", "SYLVIA-ADDITIONAL-PERMISSIONS.md",
    )}
    expected["MIT-PREVIOUS-GRANT.txt"] = (root / "licenses/MIT-PREVIOUS-GRANT.txt").read_bytes()
    with materialize_package_archive(_rift_drafts()) as artifact:
        archive = artifact.archive.read()
    database = tmp_path / "notices.sqlite3"
    with AuthorityOwner.open(database, "notice-preview") as owner:
        candidate = owner.install_package_archive(io.BytesIO(archive), install_id="current-notices")
        candidate_id = candidate.candidate_id
        for package_id in ("sylvia.srd-lite.bg3", "pneuma.srd.5.2.1.reference"):
            for name, data in expected.items():
                preview = owner.package_candidate_text(candidate_id, package_id, name)
                assert preview["content"].encode("utf-8") == data
                assert preview["bytes"] == len(data)
                assert preview["sha256"] == hashlib.sha256(data).hexdigest()
    with AuthorityOwner.open(database, "notice-preview-reopen") as owner:
        for package_id in ("sylvia.srd-lite.bg3", "pneuma.srd.5.2.1.reference"):
            for name, data in expected.items():
                assert owner.package_candidate_text(candidate_id, package_id, name)["content"].encode("utf-8") == data


@pytest.mark.parametrize("path", ["images/example.png", "runtime/example.wasm", "unclassified-file"])
def test_notice_preview_does_not_enable_binary_or_unclassified_paths(tmp_path, path):
    with AuthorityOwner.open(tmp_path / "unsupported.sqlite3", "notice-preview-rejection") as owner:
        with pytest.raises(CommandRejected) as rejected:
            owner.package_candidate_text("unselected", "unselected", path)
        assert rejected.value.code == "package_preview_unsupported"
