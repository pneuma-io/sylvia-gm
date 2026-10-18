"""Validate a portable receipt against source authority and actual archive executables."""

import hashlib
import json


def verify_portable_identity(archive, source_identity):
    metadata = json.loads(archive.read("BUILD-IDENTITY.json"))
    expected = {
        "source_sha256": source_identity["sha256"],
        "source_inputs": len(source_identity["files"]),
    }
    for field, value in expected.items():
        if metadata.get(field) != value:
            raise ValueError("Stale portable identity: " + field)
    files = archive.namelist()
    for field, name in [
        ("owner_sha256", "sylvia-gm-owner.exe"),
        ("desktop_sha256", "Sylvia GM Panels QA.exe"),
    ]:
        matches = [path for path in files if path.endswith("/" + name)]
        if len(matches) != 1:
            raise ValueError("Portable executable inventory mismatch: " + name)
        actual = hashlib.sha256(archive.read(matches[0])).hexdigest()
        if metadata.get(field) != actual:
            raise ValueError("Stale portable identity: " + field)
    for module in metadata.get("first_party_compiled", []):
        if source_identity["files"].get(module["source_path"]) != module["source_sha256"]:
            raise ValueError("Stale compiled module source: " + module["source_path"])
    return metadata
