"""Public source packages exclude the owner's private rules and setting payloads."""

import json
import subprocess
import zipfile
from pathlib import Path


def test_tracked_packages_exclude_private_content():
    root = Path(__file__).resolve().parents[1]
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z", "--", "*.sylvia-gm-package"], cwd=root
    ).decode().split("\0")
    paths = [root / name for name in tracked if name and (root / name).is_file()]
    assert paths, "The privacy check must inspect the actual public package inventory"
    for path in paths:
        with zipfile.ZipFile(path) as archive:
            assert not any("/references/canon/" in name for name in archive.namelist()), path
            records = json.loads(archive.read("manifest.json"))["packages"]
            assert all(row["id"] != "pneuma-io.siren-protocol" for row in records), path
            assert all(
                dependency["id"] != "pneuma-io.siren-protocol"
                for row in records for dependency in row.get("dependencies", [])
            ), path
