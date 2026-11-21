"""Generate reference vectors for sylvia_domain::package_archive::zipfile_write.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-domain/tests/vectors/generate_zip_write_vectors.py

For every member of the public repository package archives (the Trigger Discipline fixtures
are skipped), Python writes a one-member ZIP the way `package_materialization._zip_member` and
`ZipFile.writestr` do (DEFLATE, 1980-01-01, create_system 3, mode 0644), plus a stored copy and
a few synthetic members. The vectors hold only the archive path, member name and the SHA-256 of
Python's ZIP bytes, so the Rust test rebuilds every input from the repository.
Records `zlib.ZLIB_RUNTIME_VERSION`: DEFLATE output is zlib's, and Rust must use the same zlib.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import glob
import hashlib
import io
import json
import sys
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUTPUT = Path(__file__).with_name("zip_write_vectors.json")
PRIVATE = ("td-", "trigger-discipline", "siren")


def zip_bytes(members, compress_type=zipfile.ZIP_DEFLATED):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as output:
        for name, value in members:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = compress_type
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            output.writestr(info, value)
    return buffer.getvalue()


def main() -> None:
    archives = sorted(str(Path(p).relative_to(ROOT)).replace("\\", "/")
                      for p in glob.glob(str(ROOT / "**/*.sylvia-gm-package"), recursive=True)
                      if "/.venv/" not in p.replace("\\", "/") and "/target/" not in p.replace("\\", "/"))
    archives = [path for path in archives if not any(mark in path for mark in PRIVATE)]
    cases = []
    for path in archives:
        with zipfile.ZipFile(ROOT / path) as archive:
            for info in archive.infolist():
                data = archive.read(info)
                cases.append({"archive": path, "member": info.filename,
                              "deflated": hashlib.sha256(zip_bytes([(info.filename, data)])).hexdigest()})
        with zipfile.ZipFile(ROOT / path) as archive:
            members = [(info.filename, archive.read(info)) for info in archive.infolist()]
        cases.append({"archive": path, "member": None,
                      "deflated": hashlib.sha256(zip_bytes(members)).hexdigest(),
                      "stored": hashlib.sha256(zip_bytes(members, zipfile.ZIP_STORED)).hexdigest()})
    # Rebuilt by the Rust test from these names.
    synthetic = [
        ("synthetic/empty.bin", "empty", b""),
        ("synthetic/one.bin", "one", b"x"),
        ("synthetic/zeros.bin", "zeros", bytes(200_000)),
        ("synthetic/text.bin", "text", b"The Lantern Table. " * 5_000),
        ("synthetic/random.bin", "random", bytes((i * 7919 + 13) % 251 for i in range(300_000))),
        ("synthetic/caf" + chr(0xE9) + ".txt", "non-ascii name", b"name with a non-ASCII character"),
    ]
    for member, name, data in synthetic:
        cases.append({"archive": None, "member": member, "synthetic": name,
                      "deflated": hashlib.sha256(zip_bytes([(member, data)])).hexdigest()})
    vectors = {"python": sys.version.split()[0], "zlib_runtime": zlib.ZLIB_RUNTIME_VERSION,
               "zlib_compiled": zlib.ZLIB_VERSION, "cases": cases}
    OUTPUT.write_text(json.dumps(vectors, ensure_ascii=True, indent=None, separators=(",", ":")) + "\n",
                      encoding="utf-8", newline="\n")
    print(len(archives), "archives,", len(cases), "cases, zlib", zlib.ZLIB_RUNTIME_VERSION)


if __name__ == "__main__":
    main()
