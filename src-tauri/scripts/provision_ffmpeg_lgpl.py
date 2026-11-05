"""Recover the approved LGPL DLL inputs from the pinned public alpha.2 archive.

Only CArchiveReader reads the old owner; it is never executed or installed.
The matching source supplement and codec notices must verify before any DLL is
written. The current sidecar builder still supplies all current notices and
independently checks these inputs through stage_pyav_lgpl.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import urllib.request
import zipfile
from pathlib import Path

import pefile
from PyInstaller.archive.readers import CArchiveReader
from stage_pyav_lgpl import DLLS

RELEASE = "https://github.com/pneuma-io/sylvia-gm/releases/download/v0.3.0-alpha.2/"
PORTABLE = "sylvia-gm-0.3.0-alpha.2-windows-x86_64-portable.zip"
PORTABLE_SHA256 = "521fbbfd8660422fa3b3afcde5321266be0c158500c53ff99edc905ece513362"
SOURCE = "DICTATION-CODEC-SOURCE.zip"
SOURCE_SHA256 = "21bd6c785f4f7f2bdf4167e8157d39c261fb5becd467ca701fe69e78b141807b"
CODEC_NOTICES = ("PYAV-LGPL-BUILD.json", "FFMPEG-LGPLv2.1.txt", "FFMPEG-LGPLv3.txt", "GPLv3.txt")


def verified_archive(path: Path, expected_sha256: str) -> zipfile.ZipFile:
    """Reject changed release bytes and duplicate ZIP members before reading entries."""
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != expected_sha256:
            raise ValueError("Public codec archive differs from its pinned SHA256")
    archive = zipfile.ZipFile(path)
    names = archive.namelist()
    if len(names) != len(set(names)):
        archive.close()
        raise ValueError("Duplicate public archive entry")
    return archive


def read_codecs(
    reader: CArchiveReader, manifest: dict, notices: dict[str, bytes],
) -> dict[str, bytes]:
    """Verify the complete codec selection before returning any bytes for staging."""
    if manifest["ffmpeg_licence"] != "LGPL-2.1-or-later" or manifest["pyav_version"] != "18.0.0":
        raise ValueError("Unsupported release codec manifest")
    libraries = manifest["libraries"]
    if len(libraries) != 7 or {item["path"] for item in libraries} != DLLS:
        raise ValueError("Codec manifest must bind exactly the seven selected DLLs")
    expected = {"av.libs\\" + name for name in DLLS}
    actual = {name for name in reader.toc if name.replace("/", "\\").startswith("av.libs\\")}
    if actual != expected:
        raise ValueError("Owner must contain exactly the seven approved codec entries")
    for name, content in notices.items():
        if reader.extract("licenses\\dictation\\" + name) != content:
            raise ValueError(f"Embedded codec notice differs from current source: {name}")
    verified = {}
    for item in libraries:
        name = item["path"]
        entry = "av.libs\\" + name
        if reader.toc[entry][4] != "b" or reader.toc[entry][2] != item["bytes"]:
            raise ValueError("Invalid codec archive entry")
        content = reader.extract(entry)
        if len(content) != item["bytes"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise ValueError(f"FFmpeg DLL differs from the approved build manifest: {name}")
        with pefile.PE(data=content, fast_load=True) as image:
            if image.FILE_HEADER.Machine != 0x8664 or image.OPTIONAL_HEADER.DATA_DIRECTORY[4].Size:
                raise ValueError("Expected the approved unsigned x64 codec image")
        if not all(text in content for text in (
            b"LGPL version 2.1 or later", b"--disable-gpl", b"--disable-nonfree",
        )):
            raise ValueError("Approved LGPL codec configuration is missing")
        verified[name] = content
    return verified


def provision(work: Path, portable_path: Path | None, source_path: Path | None) -> Path:
    """Use only the pinned public inputs, retaining the attempt without overwriting prior work."""
    work.mkdir(parents=True, exist_ok=False)
    archives = []
    for name, provided in ((PORTABLE, portable_path), (SOURCE, source_path)):
        path = provided if provided is not None else work / name
        if provided is None:
            with (
                urllib.request.urlopen(RELEASE + name, timeout=60) as response,
                path.open("xb") as target,
            ):
                shutil.copyfileobj(response, target)
        archives.append(path)
    root = Path(__file__).resolve().parents[2]
    notice_root = root / "licenses/dictation"
    notices = {name: (notice_root / name).read_bytes() for name in CODEC_NOTICES}
    manifest = json.loads(notices["PYAV-LGPL-BUILD.json"])
    with verified_archive(archives[1], SOURCE_SHA256) as source:
        if source.read("PYAV-LGPL-BUILD.json") != notices["PYAV-LGPL-BUILD.json"]:
            raise ValueError("Source supplement codec manifest differs from current source")
        if source.read("FFMPEG-LGPLv2.1.txt") != notices["FFMPEG-LGPLv2.1.txt"]:
            raise ValueError("Source supplement LGPL notice differs from current source")
        source_bytes = source.read(f"ffmpeg-{manifest['ffmpeg_version']}.tar.xz")
        if hashlib.sha256(source_bytes).hexdigest() != manifest["ffmpeg_source_sha256"]:
            raise ValueError("FFmpeg source differs from the approved build manifest")
    with verified_archive(archives[0], PORTABLE_SHA256) as portable:
        for name, content in notices.items():
            if portable.read("licenses/dictation/" + name) != content:
                raise ValueError(f"Portable codec notice differs from current source: {name}")
        owner = work / "static-owner-input.exe"
        with portable.open("sylvia-gm-owner.exe") as source, owner.open("xb") as target:
            shutil.copyfileobj(source, target)
    libraries = read_codecs(CArchiveReader(str(owner)), manifest, notices)
    output = work / "ffmpeg-lgpl"
    output.mkdir()
    for name, content in libraries.items():
        (output / name).write_bytes(content)
    print(json.dumps({
        "release": RELEASE, "portable_sha256": PORTABLE_SHA256,
        "source_sha256": SOURCE_SHA256, "libraries": manifest["libraries"],
        "output": str(output), "old_owner_executed": False,
    }, indent=2))
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--portable-archive", type=Path)
    parser.add_argument("--codec-source-archive", type=Path)
    args = parser.parse_args()
    provision(args.work_dir, args.portable_archive, args.codec_source_archive)


if __name__ == "__main__":
    main()
