"""Reference vectors for the central-directory fields sylvia_domain's `zipfile` reader exposes.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-domain/tests/vectors/generate_zip_info_vectors.py

Python writes a few small archives with `zipfile` (some patched afterwards, as damaged or foreign
archives would arrive) and records what `ZipFile(...).infolist()` and `.comment` read from each:
every member's `date_time`, `create_system`, `internal_attr`, `external_attr`, `extra` and
`comment`, with its name, flags, method and size. The archives are stored as hex, so the Rust
test reads the same bytes. Portable campaign archive validation reads these fields. Reference
tooling only.
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

OUTPUT = Path(__file__).with_name("zip_info_vectors.json")


def archive(members, comment: bytes = b"") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as output:
        for info, data in members:
            output.writestr(info, data)
        output.comment = comment
    return buffer.getvalue()


def info(name: str, *, date_time=(1980, 1, 1, 0, 0, 0), create_system: int = 3,
         external_attr: int = 0o100600 << 16, internal_attr: int = 0, extra: bytes = b"",
         comment: bytes = b"", compress_type: int = zipfile.ZIP_STORED) -> zipfile.ZipInfo:
    member = zipfile.ZipInfo(name, date_time=date_time)
    member.compress_type = compress_type
    member.create_system = create_system
    member.external_attr = external_attr
    member.internal_attr = internal_attr
    member.extra = extra
    member.comment = comment
    return member


def patch_central_date(data: bytes, time: int, date: int) -> bytes:
    """Overwrite every central-directory entry's raw time and date (offsets 12 and 14)."""
    patched = bytearray(data)
    start = 0
    while (start := patched.find(b"PK\x01\x02", start)) >= 0:
        patched[start + 12:start + 14] = time.to_bytes(2, "little")
        patched[start + 14:start + 16] = date.to_bytes(2, "little")
        start += 4
    return bytes(patched)


CASES: list[tuple[str, bytes]] = [
    ("portable", archive([(info("authority.jsonl"), b"{}\n"), (info("manifest.json"), b"{}")])),
    ("comments_and_extra", archive(
        [(info("a.txt", comment=b"member note", extra=b"\x99\x99\x02\x00ab"), b"a"),
         (info("b.txt", internal_attr=1, create_system=0, external_attr=0), b"bb")],
        comment=b"archive note")),
    ("deflated_dated", archive([(info("c.txt", date_time=(2026, 11, 22, 21, 40, 58),
                                      compress_type=zipfile.ZIP_DEFLATED), b"c" * 100)])),
    # Month 0, day 0, 31 two-second units (second 62): CPython does not range-check.
    ("odd_date", patch_central_date(archive([(info("d.txt"), b"d")]), time=0x001F, date=0x0000)),
    ("latest_date", patch_central_date(archive([(info("e.txt"), b"e")]), time=0xFFFF, date=0xFFFF)),
    ("utf8_name", archive([(info("ünder/ß.txt"), b"u")])),
]


def described(data: bytes) -> dict[str, object]:
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        return {
            "comment": source.comment.hex(),
            "members": [
                {
                    "filename": member.filename,
                    "date_time": list(member.date_time),
                    "create_system": member.create_system,
                    "internal_attr": member.internal_attr,
                    "external_attr": member.external_attr,
                    "extra": member.extra.hex(),
                    "comment": member.comment.hex(),
                    "flag_bits": member.flag_bits,
                    "compress_type": member.compress_type,
                    "file_size": member.file_size,
                }
                for member in source.infolist()
            ],
        }


def main() -> None:
    record = {
        "python": sys.version.split()[0],
        "cases": [{"name": name, "archive": data.hex(), **described(data)} for name, data in CASES],
    }
    OUTPUT.write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
