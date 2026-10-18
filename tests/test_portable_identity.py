import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile

import pytest
from scripts.qa.portable_identity import verify_portable_identity

SOURCE = {"sha256": "a" * 64, "files": {"src/example.py": "b" * 64}}


def test_distinct_installed_and_portable_database_owners_can_coexist(tmp_path):
    from sylvia_gm.application.authority import AuthorityOwner

    installed = tmp_path / "installed" / "authority.sqlite3"
    portable = tmp_path / "portable" / "isolated-data" / "authority.sqlite3"
    installed.parent.mkdir(parents=True)
    portable.parent.mkdir(parents=True)
    code = (
        "from pathlib import Path; import sys; "
        "from sylvia_gm.application.authority import AuthorityOwner; "
        "owner=AuthorityOwner.open(Path(sys.argv[1]), 'portable-child'); owner.close()"
    )
    with AuthorityOwner.open(installed, "installed-parent"):
        child = subprocess.run([sys.executable, "-B", "-c", code, str(portable)],
                               env=dict(os.environ), capture_output=True, text=True)
        assert child.returncode == 0, child.stderr
        assert installed.is_file() and portable.is_file()


def archive(change=None):
    files = {
        "candidate/sylvia-gm-owner.exe": b"current-owner",
        "candidate/Sylvia GM Panels QA.exe": b"current-desktop",
    }
    identity = {
        "source_sha256": SOURCE["sha256"],
        "source_inputs": 1,
        "owner_sha256": hashlib.sha256(files["candidate/sylvia-gm-owner.exe"]).hexdigest(),
        "desktop_sha256": hashlib.sha256(files["candidate/Sylvia GM Panels QA.exe"]).hexdigest(),
    }
    identity.update(change or {})
    files["BUILD-IDENTITY.json"] = json.dumps(identity).encode()
    # Even a self-consistent entry manifest must not admit stale build metadata.
    files["ENTRY-HASHES.json"] = json.dumps(
        {
            n: {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
            for n, raw in files.items()
        }
    ).encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, raw in files.items():
            z.writestr(name, raw)
    out.seek(0)
    return zipfile.ZipFile(out)


def test_current_identity_matches_actual_source_and_executables():
    with archive() as z:
        assert verify_portable_identity(z, SOURCE)["source_inputs"] == 1


@pytest.mark.parametrize(
    "change",
    [
        {"source_sha256": "old-source"},
        {"source_inputs": 2695},
        {"owner_sha256": "old-owner"},
        {"desktop_sha256": "old-desktop"},
    ],
)
def test_self_hashed_but_stale_identity_is_rejected(change):
    with archive(change) as z:
        manifest = json.loads(z.read("ENTRY-HASHES.json"))
        assert (
            hashlib.sha256(z.read("BUILD-IDENTITY.json")).hexdigest()
            == manifest["BUILD-IDENTITY.json"]["sha256"]
        )
        with pytest.raises(ValueError, match="Stale portable identity"):
            verify_portable_identity(z, SOURCE)
