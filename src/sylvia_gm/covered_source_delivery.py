"""Read the fixed recipient source supplement without executing its contents."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path, PurePosixPath

COVERED_SOURCE_NOTICE_MARKER = b"Recipient covered-source supplement (local): COVERED-SOURCE.zip"
COVERED_SOURCE_DELIVERY_SHA256 = {
    "licenses/COVERED-SOURCE.zip": (
        "338502f6af9bc463bdc2cfff88d8a4f4b70c5bf5e00ae8bcc49b36b6c5efdeff"
    ),
    "licenses/COVERED-SOURCE-MAP.json": (
        "402f7e1cc4bd44e9eafd98a5d9155f4fc96623573ba25e64b87a56a68623b9cd"
    ),
    "licenses/COVERED-SOURCE-README.md": (
        "96c50236299a441a5a362325d5ec5df13ff78c3d52ef0a6cb703e73fdc48fe79"
    ),
}


class CoveredSourceError(ValueError):
    """The selected source bytes or their original notices are incomplete."""


def covered_source_required(notices: bytes) -> bool:
    """Keep old layouts readable while enforcing a new recipient source claim."""
    return COVERED_SOURCE_NOTICE_MARKER in notices


def _validate_archive(blob: bytes, source_map: dict[str, object], readme: bytes) -> None:
    """Verify the complete local source map and required original notices."""
    archive_record = source_map["archive"]
    if not isinstance(archive_record, dict) or (
        archive_record.get("size_bytes") != len(blob)
        or archive_record.get("sha256") != hashlib.sha256(blob).hexdigest()
    ):
        raise CoveredSourceError("Covered-source archive identity does not match")
    records = source_map.get("source_files")
    if not isinstance(records, list) or not records:
        raise CoveredSourceError("Covered-source map is missing")
    required = {
        "selectors/selectors-0.36.1.crate",
        "certifi/cacert.pem",
        "certifi/LICENSE",
        "certifi/Makefile",
        "certifi/README.rst",
        "notices/MPL-2.0.txt",
        "README.md",
    }
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        names = archive.namelist()
        if len(set(names)) != len(names):
            raise CoveredSourceError("Covered-source archive contains duplicate paths")
        mapped = set()
        for row in records:
            if not isinstance(row, dict) or not isinstance(row.get("path"), str):
                raise CoveredSourceError("Covered-source file record is invalid")
            name = row["path"]
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name or str(path) != name:
                raise CoveredSourceError("Covered-source path is invalid")
            if name in mapped:
                raise CoveredSourceError("Covered-source map repeats a path")
            mapped.add(name)
            value = archive.read(name)
            if len(value) != row.get("size_bytes") or hashlib.sha256(value).hexdigest() != row.get(
                "sha256"
            ):
                raise CoveredSourceError("Covered-source file does not match its source map")
            if not name.endswith(".crate"):
                text = value.decode("utf8")
                if not text.strip() or any(ord(c) < 32 and c not in "\t\r\n" for c in text):
                    raise CoveredSourceError("Covered source or notice is unreadable")
        if not required <= mapped or set(names) != mapped | {"SOURCE-MAP.json"}:
            raise CoveredSourceError("Covered-source files or original notices are missing")
        internal = json.loads(archive.read("SOURCE-MAP.json"))
        external = {k: v for k, v in source_map.items() if k not in {"archive", "recipient_readme"}}
        if internal != external or archive.read("README.md") != readme:
            raise CoveredSourceError("Covered-source recipient instructions or maps disagree")


def read_covered_source_companions(directory: Path) -> dict[str, bytes]:
    """Return the three exact source companions, rejecting missing or redirected files."""
    try:
        values = {}
        for name, digest in COVERED_SOURCE_DELIVERY_SHA256.items():
            path = directory / Path(name).name
            resolved = path.resolve(strict=True)
            if path.is_symlink() or resolved != path.absolute() or not resolved.is_file():
                raise CoveredSourceError("Covered-source path is not a regular unredirected file")
            value = resolved.read_bytes()
            if hashlib.sha256(value).hexdigest() != digest:
                raise CoveredSourceError("Covered-source file does not match its fixed pin")
            values[name] = value
        source_map = json.loads(values["licenses/COVERED-SOURCE-MAP.json"])
        _validate_archive(
            values["licenses/COVERED-SOURCE.zip"],
            source_map,
            values["licenses/COVERED-SOURCE-README.md"],
        )
        return values
    except (
        OSError,
        UnicodeError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
        zipfile.BadZipFile,
    ) as error:
        raise CoveredSourceError(
            "Covered-source files and original notices cannot be read"
        ) from error
