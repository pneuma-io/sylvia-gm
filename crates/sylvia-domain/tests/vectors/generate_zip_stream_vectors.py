"""Reference vectors for sylvia_domain::package_archive::zipfile_write::StreamingZipWriter.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-domain/tests/vectors/generate_zip_stream_vectors.py

CPython writes each archive as the portable campaign export does: `ZipFile(file, "w",
compression=ZIP_STORED, allowZip64=True)`, then for every member `fixed_zip_info(name)` with
`file_size` set beforehand and `open(info, "w", force_zip64=False)`, written in 64 KiB chunks.
Member `i` of `n` bytes holds `(i + j) % 251` for `j` in `range(n)`, so the Rust test rebuilds
every input. A member may carry a third value, the `file_size` set before `open`, when it differs
from the bytes written. The cases cover an empty archive, small and non-ASCII members, a member
opened as zip64 by a preset `file_size` of 2,100,000,000 with only a few bytes written, 65,535 and
65,536 members (`ZIP_FILECOUNT_LIMIT` and one past it), and archives written into a sparse file
positioned so that a member's header offset is exactly `ZIP64_LIMIT` and one past it: CPython
3.12's offsets are the file's own positions, so those archives carry zip64 central extras and end
records. The vectors hold each archive's bias, the SHA-256 and length of the bytes CPython wrote
from the bias on, and the members; the sparse files are deleted afterwards. Reference tooling
only.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

OUTPUT = Path(__file__).with_name("zip_stream_vectors.json")
ZIP64_LIMIT = (1 << 31) - 1
CHUNK = 65_536


def data(index: int, size: int) -> bytes:
    return bytes((index + offset) % 251 for offset in range(size))


def fixed_zip_info(name: str) -> zipfile.ZipInfo:
    """`campaign_archive.fixed_zip_info`."""
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = 0o100600 << 16
    info.internal_attr = 0
    info.flag_bits = 0
    info.extra = b""
    info.comment = b""
    return info


def make_sparse(handle) -> None:  # noqa: ANN001
    """Mark an open file sparse, so a seek far past its end costs no disk."""
    if os.name != "nt":
        return
    import ctypes
    import msvcrt
    from ctypes import wintypes

    fsctl_set_sparse = 0x000900C4
    returned = wintypes.DWORD()
    ok = ctypes.windll.kernel32.DeviceIoControl(
        wintypes.HANDLE(msvcrt.get_osfhandle(handle.fileno())), fsctl_set_sparse,
        None, 0, None, 0, ctypes.byref(returned), None)
    if not ok:
        raise OSError(ctypes.get_last_error(), "FSCTL_SET_SPARSE failed")


def write(path: Path, bias: int, members: list[tuple]) -> tuple[str, int]:
    with path.open("w+b") as handle:
        make_sparse(handle)
        handle.seek(bias)
        with zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for index, (name, size, *preset) in enumerate(members):
                info = fixed_zip_info(name)
                info.file_size = preset[0] if preset else size
                content = data(index, size)
                with archive.open(info, "w", force_zip64=False) as destination:
                    for start in range(0, size, CHUNK):
                        destination.write(content[start:start + CHUNK])
        handle.seek(bias)
        digest = hashlib.sha256()
        length = 0
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
            length += len(chunk)
    return digest.hexdigest(), length


def header_length(name: str) -> int:
    return 30 + len(name.encode("utf-8"))


def cases() -> list[tuple[str, int, list[tuple]]]:
    many = [(f"m/{index:05d}", 1) for index in range(65_536)]
    first = ("authority.jsonl", 1000)
    first_end = header_length(first[0]) + first[1]
    return [
        ("empty", 0, []),
        ("small", 0, [("authority.jsonl", 300), ("blobs/" + "a" * 64, 70_000), ("manifest.json", 0)]),
        ("non_ascii", 0, [("café.txt", 5), ("ünder/ß.txt", 9)]),
        # Opened as zip64 (`2_100_000_000 * 1.05 > ZIP64_LIMIT`), then closed with 7 bytes: zip64
        # local headers, provisional and rewritten, and central version 45 without an extra.
        ("zip64_at_open", 0, [("authority.jsonl", 40), ("big.bin", 7, 2_100_000_000), ("manifest.json", 3)]),
        ("count_at_limit", 0, many[:65_535]),
        ("count_past_limit", 0, many),
        # The second member's header starts exactly at ZIP64_LIMIT, then one byte past it.
        ("offset_at_limit", ZIP64_LIMIT - first_end, [first, ("manifest.json", 200)]),
        ("offset_past_limit", ZIP64_LIMIT - first_end + 1, [first, ("manifest.json", 200)]),
    ]


def main() -> None:
    records = []
    with tempfile.TemporaryDirectory() as scratch:
        for name, bias, members in cases():
            path = Path(scratch) / f"{name}.zip"
            sha256, length = write(path, bias, members)
            path.unlink()
            listed: object = [list(member) for member in members]
            if len(members) > 100:
                # `m/{index:05d}` of one byte each: the Rust test rebuilds the names.
                assert members == [(f"m/{index:05d}", 1) for index in range(len(members))]
                listed = {"pattern": "m/{index:05d}", "count": len(members), "size": 1}
            records.append({"name": name, "bias": bias, "sha256": sha256, "length": length, "members": listed})
    record = {"python": sys.version.split()[0], "cases": records}
    OUTPUT.write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
