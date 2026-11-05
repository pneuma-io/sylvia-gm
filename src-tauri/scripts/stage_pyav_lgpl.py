"""Stage locked PyAV bindings with separately built LGPL-only FFmpeg DLLs.

No installed package is modified. PE import names change, not extension code.
The output is disposable release input and records every changed binding hash.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import re
import shutil
import struct
from importlib.metadata import distribution
from pathlib import Path

import pefile

DLLS = {
    "avcodec-62.dll",
    "avdevice-62.dll",
    "avfilter-11.dll",
    "avformat-62.dll",
    "avutil-60.dll",
    "swresample-6.dll",
    "swscale-9.dll",
}
PATTERN = re.compile(
    rb"(avcodec|avdevice|avfilter|avformat|avutil|swresample|swscale)-(\d+)(?:-[0-9a-f]+)?\.dll",
    re.I,
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def patch_binding(path: Path) -> dict:
    """Rebind imports while preserving every compiled executable section."""
    before = digest(path)
    raw = bytearray(path.read_bytes())
    pe = pefile.PE(data=bytes(raw))
    if pe.FILE_HEADER.Machine != 0x8664:
        raise ValueError("Binding architecture must be x64")
    if pe.OPTIONAL_HEADER.DATA_DIRECTORY[4].Size:
        raise ValueError("Do not rewrite a signed binding")
    executable_sections = [
        (section.PointerToRawData, section.SizeOfRawData, section.get_data())
        for section in pe.sections
        if section.Characteristics & 0x20000000
    ]
    changed = []
    for group in ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT"):
        for item in getattr(pe, group, []):
            match = PATTERN.fullmatch(item.dll)
            if not match:
                if b"libx264" in item.dll.lower() or b"libx265" in item.dll.lower():
                    raise ValueError("Unexpected direct GPL codec binding")
                continue
            replacement = match[1].lower() + b"-" + match[2] + b".dll"
            if replacement.decode() not in DLLS or len(replacement) > len(item.dll):
                raise ValueError("Unsupported FFmpeg import ABI")
            offset = pe.get_offset_from_rva(item.struct.Name)
            raw[offset : offset + len(item.dll) + 1] = replacement + b"\0" * (
                len(item.dll) + 1 - len(replacement)
            )
            changed.append(
                {
                    "old": item.dll.decode(),
                    "new": replacement.decode(),
                    "symbols": [
                        x.name.decode() if x.name else {"ordinal": x.ordinal} for x in item.imports
                    ],
                }
            )
    pe.close()
    updated = pefile.PE(data=bytes(raw))
    struct.pack_into(
        "<I",
        raw,
        updated.OPTIONAL_HEADER.get_field_absolute_offset("CheckSum"),
        updated.generate_checksum(),
    )
    updated.close()
    for offset, size, original in executable_sections:
        if raw[offset : offset + size] != original:
            raise ValueError("Import rebinding changed compiled executable code")
    path.write_bytes(raw)
    return {
        "path": str(path.name),
        "before_sha256": before,
        "after_sha256": digest(path),
        "imports": changed,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ffmpeg", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    args = ap.parse_args()
    if args.output.exists():
        raise ValueError("Stage output must be new; preserve prior attempts")
    actual = {p.name for p in args.ffmpeg.glob("*.dll")}
    if actual != DLLS:
        raise ValueError(f"Expected only seven FFmpeg ABI DLLs, got {sorted(actual)}")
    expected = json.loads(args.manifest.read_text(encoding="utf-8"))
    if expected["pyav_version"] != "18.0.0" or expected["ffmpeg_licence"] != "LGPL-2.1-or-later":
        raise ValueError("Unsupported release codec manifest")
    if {item["path"] for item in expected["libraries"]} != DLLS or len(expected["libraries"]) != 7:
        raise ValueError("Codec manifest must bind exactly the seven selected DLLs")
    for library in expected["libraries"]:
        path = args.ffmpeg / library["path"]
        if path.stat().st_size != library["bytes"] or digest(path) != library["sha256"]:
            raise ValueError("FFmpeg DLL differs from the approved build manifest")
    dist = distribution("av")
    if dist.version != "18.0.0":
        raise ValueError("This recipe is bound to locked PyAV 18.0.0")
    for binding in expected["bindings"]:
        if digest(Path(dist.locate_file(binding["path"]))) != binding["before_sha256"]:
            raise ValueError("Locked PyAV binding differs from the release input")
    if digest(Path(dist.locate_file("av/__init__.py"))) != expected["source_wrapper_init_sha256"]:
        raise ValueError("Locked PyAV bootstrap differs from the release input")
    args.output.mkdir(parents=True)
    copied = []
    for item in dist.files or []:
        name = item.as_posix()
        if not (name.startswith("av/") or name.startswith("av-18.0.0.dist-info/")):
            continue
        if (
            "__pycache__" in item.parts
            or item.suffix == ".pyc"
            or item.name in {"RECORD", "direct_url.json", "INSTALLER", "DELVEWHEEL"}
        ):
            continue
        source = Path(dist.locate_file(item))
        if item.hash and item.hash.mode == "sha256":
            actual_hash = (
                base64.urlsafe_b64encode(hashlib.sha256(source.read_bytes()).digest())
                .decode()
                .rstrip("=")
            )
            if actual_hash != item.hash.value:
                raise ValueError(f"Locked wrapper file differs from its wheel RECORD: {name}")
        target = args.output / name
        if not target.resolve().is_relative_to(args.output.resolve()):
            raise ValueError("Unsafe package path")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(name)
    bindings = []
    expected_bindings = {item["path"]: item for item in expected["bindings"]}
    for pyd in sorted((args.output / "av").rglob("*.pyd")):
        item = patch_binding(pyd)
        item["path"] = pyd.relative_to(args.output).as_posix()
        if item["after_sha256"] != expected_bindings[item["path"]]["after_sha256"]:
            raise ValueError("Rebound binding differs from its release manifest")
        bindings.append(item)
    libs = args.output / "av.libs"
    libs.mkdir()
    for name in sorted(DLLS):
        shutil.copy2(args.ffmpeg / name, libs / name)
    init = args.output / "av/__init__.py"
    text = init.read_text(encoding="utf-8")
    begin, end = (
        text.index("# start delvewheel patch"),
        text.index("# end delvewheel patch") + len("# end delvewheel patch"),
    )
    loader = (
        "# Sylvia release packaging: retain the DLL search handle for the supplied "
        "LGPL-only shared libraries.\nimport os as _os\n"
        "_av_dll_directory = _os.add_dll_directory(_os.path.abspath(_os.path.join("
        "_os.path.dirname(__file__), _os.pardir, 'av.libs')))"
    )
    init.write_text(text[:begin] + loader + text[end:], encoding="utf-8", newline="\n")
    metadata = args.output / "av-18.0.0.dist-info"
    (metadata / "INSTALLER").write_text("sylvia-release-packaging\n", encoding="utf-8")
    report = {
        "pyav_version": dist.version,
        "code_changes": (
            "Only PE import-name rebinding/checksums and DLL directory bootstrap; "
            "no extension instruction changes"
        ),
        "bindings": bindings,
        "libraries": [
            {"path": p.name, "bytes": p.stat().st_size, "sha256": digest(p)}
            for p in sorted(libs.iterdir())
        ],
    }
    (args.output / "PYAV-LGPL-STAGING.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    with (metadata / "RECORD").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        for file in sorted(args.output.rglob("*")):
            if file.is_file() and file.name != "RECORD":
                sha = (
                    base64.urlsafe_b64encode(hashlib.sha256(file.read_bytes()).digest())
                    .decode()
                    .rstrip("=")
                )
                writer.writerow(
                    [file.relative_to(args.output).as_posix(), "sha256=" + sha, file.stat().st_size]
                )
        writer.writerow(["av-18.0.0.dist-info/RECORD", "", ""])
    print(
        json.dumps(
            {"bindings": len(bindings), "libraries": len(DLLS), "output": str(args.output)},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
