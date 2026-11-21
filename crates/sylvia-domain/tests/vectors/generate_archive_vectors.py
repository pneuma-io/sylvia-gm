"""Generate reference vectors for sylvia_domain::package_archive from the frozen Python.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-domain/tests/vectors/generate_archive_vectors.py

Cases:
- every `.sylvia-gm-package` in the repository, which the Rust test reads from its path;
- hostile variants of the two small Riichi archives: byte patches at structural offsets and
  truncations (stored as edits), and rebuilt archives (stored whole, base64);
- tiny synthetic ZIP files for directory-level failures;
- each exception class raised from inside admission, for its final handlers
  (`archive_handler_vectors.json`, read by `package_archive`'s unit tests).

Python's outcome is a SHA-256 of the admitted result's projection, the `CommandRejected`'s class,
code, message and diagnostics, or, for an exception admission lets escape, its class.
Cases whose outcome depends on Windows path handling (a backslash in a member name) are marked.
The output is deterministic and checked in. This script is reference tooling for the port and
is retired with the Python source.
"""

from __future__ import annotations

import base64
import glob
import hashlib
import io
import json
import struct
import sys
import unicodedata
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.domain.errors import CommandRejected  # noqa: E402
from sylvia_gm.domain.package_library import canonical_json_bytes, closure_sha256, content_root_for_record  # noqa: E402
from sylvia_gm.infrastructure.package_archive import admit_package_archive  # noqa: E402

OUTPUT = Path(__file__).with_name("archive_vectors.json")
HANDLER_OUTPUT = Path(__file__).with_name("archive_handler_vectors.json")
BASES = ["rulesets/riichi-mahjong/riichi-mahjong-0.1.1.sylvia-gm-package",
         "campaign-plans/riichi-quick-test/setting.sylvia-gm-package"]
DATE = (2026, 1, 1, 0, 0, 0)


def outcome(data: bytes):
    try:
        with admit_package_archive(io.BytesIO(data)) as admitted:
            projection = {
                "format_version": admitted.format_version,
                "manifests": [m.canonical_json for m in admitted.manifests],
                "files": [[admitted.manifests.index(f.manifest), f.file.path, f.file.byte_count, f.file.sha256,
                           f.member_name] for f in admitted.files],
                "closure": closure_sha256(admitted.package_lock),
                "first_file": hashlib.sha256(b"".join(admitted.iter_file_chunks(admitted.files[0]))).hexdigest()
                if admitted.files else None,
            }
            return ["ok", hashlib.sha256(json.dumps(projection).encode()).hexdigest()]
    except CommandRejected as error:
        return [type(error).__name__, error.code, str(error), [d.as_payload() for d in error.diagnostics]]
    except Exception as error:  # escapes admission: the owner's internal error
        return ["uncaught", type(error).__name__]


def members(data: bytes):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return [(info, archive.read(info)) for info in archive.infolist()]


def rebuild(entries, *, comment=b""):
    """A ZIP from (name, data, options) entries, written deterministically."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.comment = comment
        for name, data, options in entries:
            info = zipfile.ZipInfo(name, DATE)
            info.compress_type = options.get("compress_type", zipfile.ZIP_DEFLATED)
            info.external_attr = options.get("external_attr", 0o100644 << 16)
            info.extra = options.get("extra", b"")
            archive.writestr(info, data)
    return buffer.getvalue()


def base_entries(data: bytes):
    return [(info.filename, content, {"compress_type": info.compress_type}) for info, content in members(data)]


def replace(entries, name, data):
    return [(n, data if n == name else d, o) for n, d, o in entries]


def manifest_edits(entries, edit):
    """Rewrite manifest.json after `edit(document)`, keeping each record's content root valid."""
    document = json.loads(dict((n, d) for n, d, _ in entries)["manifest.json"])
    edit(document)
    for record in document.get("packages", []) if isinstance(document, dict) else []:
        if isinstance(record, dict) and "content_sha256" in record:
            record["content_sha256"] = content_root_for_record(record)
    return replace(entries, "manifest.json", canonical_json_bytes(document))


def structure(data: bytes):
    """Byte offsets of the structural fields, for patch recipes."""
    offsets = {}
    eocd = data.rfind(b"PK\x05\x06")
    offsets["eocd"] = eocd
    cd_offset = struct.unpack_from("<L", data, eocd + 16)[0]
    offsets["cd"] = cd_offset
    first_local = struct.unpack_from("<L", data, cd_offset + 42)[0]
    offsets["local"] = first_local
    name_length, extra_length = struct.unpack_from("<2H", data, first_local + 26)
    offsets["data"] = first_local + 30 + name_length + extra_length
    return offsets


def patch(data: bytes, edits):
    data = bytearray(data)
    for op, *args in edits:
        if op == "patch":
            offset, hexdata = args
            data[offset:offset + len(bytes.fromhex(hexdata))] = bytes.fromhex(hexdata)
        elif op == "truncate":
            del data[args[0]:]
        elif op == "append":
            data += bytes.fromhex(args[0])
        elif op == "prepend":
            data[0:0] = bytes.fromhex(args[0])
    return bytes(data)


def le16(value):
    return struct.pack("<H", value).hex()


def le32(value):
    return struct.pack("<L", value).hex()


def patch_cases(name, data):
    s = structure(data)
    cd, local, eocd = s["cd"], s["local"], s["eocd"]
    recipes = [[["truncate", n]] for n in (0, 10, 22, len(data) // 2, len(data) - 1, len(data) - 22, eocd + 10, s["data"] + 5)]
    recipes += [
        [["patch", eocd + 10, le16(0)]], [["patch", eocd + 8, le16(1)]], [["patch", eocd + 12, le32(1)]],
        [["patch", eocd + 16, le32(0)]], [["patch", eocd + 20, le16(5)]], [["patch", eocd + 4, le16(1)]],
        [["patch", cd, "504b0102"[::-1]]], [["patch", cd, "00000000"]], [["patch", cd + 6, "40"]],
        [["patch", cd + 8, le16(0x0800)]], [["patch", cd + 8, le16(0x0001)]], [["patch", cd + 8, le16(0x0020)]],
        [["patch", cd + 8, le16(0x0040)]], [["patch", cd + 10, le16(12)]], [["patch", cd + 10, le16(14)]],
        [["patch", cd + 10, le16(99)]], [["patch", cd + 10, le16(0)]], [["patch", cd + 16, le32(0)]],
        [["patch", cd + 20, le32(1)]], [["patch", cd + 24, le32(0)]], [["patch", cd + 24, le32(10**6)]],
        [["patch", cd + 38, le32(0o120777 << 16)]], [["patch", cd + 38, le32(0o040755 << 16)]],
        [["patch", cd + 42, le32(1)]], [["patch", cd + 42, le32(10**7)]], [["patch", cd + 28, le16(0xFFF0)]],
        [["patch", cd + 30, le16(4)]], [["patch", cd + 46, "ff"]], [["patch", cd + 46, "4d"]],
        [["patch", local, "00000000"]], [["patch", local + 6, le16(0x0800)]], [["patch", local + 26, le16(3)]],
        [["patch", local + 30, "5a"]], [["patch", s["data"], "ff"]], [["patch", s["data"] + 2, "000000"]],
        [["append", "00" * 30]], [["prepend", "00" * 16]], [["patch", eocd + 20, le16(2)], ["append", "4142"]],
    ]
    return [{"base": name, "edits": recipe} for recipe in recipes]


def rebuilt_cases(name, data):
    entries = base_entries(data)
    manifest = dict((n, d) for n, d, _ in entries)["manifest.json"]
    first = next(n for n, _, _ in entries if n != "manifest.json")
    nfd = unicodedata.normalize("NFD", "\u00e9")
    variants = {
        "unchanged": entries,
        "stored": [(n, d, {"compress_type": zipfile.ZIP_STORED}) for n, d, _ in entries],
        "bzip2": [(n, d, {"compress_type": zipfile.ZIP_BZIP2 if n == first else o["compress_type"]}) for n, d, o in entries],
        "directory": entries + [("packages/", b"", {})],
        "extra member": entries + [("packages/stray.txt", b"x", {})],
        "duplicate": entries + [(first, b"again", {})],
        "case collision": entries + [(first.upper(), b"x", {})],
        "non-NFC": entries + [(f"packages/{nfd}.txt", b"x", {})],
        "unportable": entries + [("packages/a:b.txt", b"x", {})],
        "backslash": entries + [("packages\\b.txt", b"x", {})],
        "nul name": entries + [("packages/n\x00ul.txt", b"x", {})],
        "symlink": [(n, d, {**o, "external_attr": 0o120777 << 16} if n == first else o) for n, d, o in entries],
        "missing manifest": [e for e in entries if e[0] != "manifest.json"],
        "manifest only": [e for e in entries if e[0] == "manifest.json"],
        "empty file": replace(entries, first, b""),
        "changed file": replace(entries, first, dict((n, d) for n, d, _ in entries)[first] + b" "),
        "manifest pretty": replace(entries, "manifest.json", json.dumps(json.loads(manifest), indent=1).encode()),
        "manifest list": replace(entries, "manifest.json", b"[]"),
        "manifest invalid": replace(entries, "manifest.json", b"{"),
        "manifest big": replace(entries, "manifest.json", b" " * (2 * 1024 * 1024 + 1)),
        "format 9": manifest_edits(entries, lambda d: d.__setitem__("format", 9)),
        "format bool": manifest_edits(entries, lambda d: d.__setitem__("format", True)),
        "packages object": manifest_edits(entries, lambda d: d.__setitem__("packages", {})),
        "extra field": manifest_edits(entries, lambda d: d.__setitem__("extra", 1)),
        "record label": manifest_edits(entries, lambda d: d["packages"][0]["metadata"].__setitem__("label", "a/b")),
        "record capability": manifest_edits(entries, lambda d: d["packages"][0]["capabilities"].append("zzz.unused")),
        "record files reversed": manifest_edits(entries, lambda d: d["packages"][-1]["files"].reverse()),
        "record file size": manifest_edits(entries, lambda d: d["packages"][-1]["files"][0].__setitem__("bytes", 1)),
        "record file sha": manifest_edits(entries, lambda d: d["packages"][-1]["files"][0].__setitem__("sha256", "0" * 64)),
        "record file huge": manifest_edits(entries, lambda d: d["packages"][-1]["files"][0].__setitem__("bytes", 2**63)),
        "record file beyond 64 bits": manifest_edits(entries, lambda d: d["packages"][-1]["files"][0].__setitem__("bytes", 2**64)),
        "record dropped": manifest_edits(entries, lambda d: d["packages"].pop()),
        "unicode path extra": [(n, d, {**o, "extra": struct.pack("<HHBL", 0x7075, 5 + 3, 1,
                                                                 zipfile.crc32(n.encode())) + b"abc"} if n == first else o)
                               for n, d, o in entries],
        "corrupt extra": [(n, d, {**o, "extra": b"\x01\x00\x20\x00ab"} if n == first else o) for n, d, o in entries],
        "short unicode extra": [(n, d, {**o, "extra": struct.pack("<HH", 0x7075, 2) + b"\x01\x00"} if n == first else o)
                                for n, d, o in entries],
    }
    cases = []
    for label, variant in variants.items():
        try:
            archive = rebuild(variant)
        except Exception as error:
            raise SystemExit(f"could not build {label}: {error}")
        cases.append({"base": name, "label": label, "archive": base64.b64encode(archive).decode(),
                      "windows_only": any("\\" in n for n, _, _ in variant)})
    cases.append({"base": name, "label": "comment", "archive": base64.b64encode(rebuild(entries, comment=b"c" * 40)).decode(),
                  "windows_only": False})
    return cases


def synthetic_cases():
    cases = []

    def add(label, archive):
        cases.append({"base": None, "label": label, "archive": base64.b64encode(archive).decode(), "windows_only": False})

    add("not a zip", b"hello world, not a zip at all")
    add("empty zip", rebuild([]))
    add("eocd only short", b"PK\x05\x06" + b"\x00" * 10)
    many = rebuild([(f"m{i}", b"", {"compress_type": zipfile.ZIP_STORED}) for i in range(4097)])
    add("too many members", many)
    named = bytearray(rebuild([("ab", b"x", {"compress_type": zipfile.ZIP_STORED})]))
    for header, flag_at, name_at in ((b"PK", 6, 30), (b"PK", 8, 46)):
        at = named.find(header)
        named[at + flag_at:at + flag_at + 2] = struct.pack("<H", 0x0800)
        named[at + name_at:at + name_at + 2] = b"\x81\xff"
    add("utf8 flagged bad name", bytes(named))
    add("zip64 locator only", rebuild([("a", b"x", {})])[:-22] + bytes.fromhex("504b060700000000000000000000000001000000")
        + rebuild([("a", b"x", {})])[-22:])
    return cases


def handler_cases():
    """Python's final handlers, class by class. The operator exceptions (`TypeError`, `KeyError`,
    `AttributeError`, `IndexError`) cannot reach admission through a real archive: the archive's
    schema refuses those manifest shapes first, and the overlapped-entries check refuses a member
    that would end in `EOFError`. So each exception is raised from inside admission, in place of
    `_normalized_name`, and the outcome shows which handler takes it and with what text."""
    import zlib

    import sylvia_gm.infrastructure.package_archive as module
    from sylvia_gm.domain.errors import OwnerConflict, RulesetPreconditionRejected, StaleRevision

    raised = [
        ("ValueError", "Archive member name is not normalized", ValueError),
        ("TypeError", "unhashable type: 'list'", TypeError),
        ("TypeError", "unhashable type: 'dict'", TypeError),
        ("RuntimeError", "File 'x' is encrypted, password required for extraction", RuntimeError),
        ("NotImplementedError", "That compression method is not supported", NotImplementedError),
        ("BadZipFile", "Bad CRC-32 for file 'x'", zipfile.BadZipFile),
        ("UnicodeDecodeError", None, None),
        ("OwnerConflict", "Another owner holds the database", OwnerConflict),
        ("StaleRevision", "Campaign revision is stale", StaleRevision),
        ("CommandRejected", "Package contents are too large", None),
        ("RulesetPreconditionRejected", "The table is not ready", None),
        ("KeyError", "'tools'", KeyError),
        ("AttributeError", "'list' object has no attribute 'values'", AttributeError),
        ("IndexError", "list index out of range", IndexError),
        ("error", "Error -3 while decompressing data: invalid block type", zlib.error),
        ("EOFError", "", EOFError),
    ]
    data = (ROOT / BASES[0]).read_bytes()
    original = module._normalized_name
    cases = []
    try:
        for name, message, kind in raised:
            if name == "UnicodeDecodeError":
                error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
                message = str(error)
            elif name == "CommandRejected":
                error = CommandRejected(message, code="archive_limit")
            elif name == "RulesetPreconditionRejected":
                error = RulesetPreconditionRejected(message)
            elif name == "KeyError":
                error = KeyError("tools")
            else:
                error = kind(message)

            def fail(_name, error=error):
                raise error

            module._normalized_name = fail
            cases.append({"class": name, "message": message, "outcome": outcome(data)})
    finally:
        module._normalized_name = original
    return cases


def main() -> None:
    repo = sorted(str(Path(p).relative_to(ROOT)).replace("\\", "/") for p in glob.glob(str(ROOT / "**/*.sylvia-gm-package"), recursive=True)
                  if "/.venv/" not in p.replace("\\", "/") and "/target/" not in p.replace("\\", "/"))
    repository = [{"path": path, "outcome": outcome((ROOT / path).read_bytes())} for path in repo]
    hostile = []
    for name in BASES:
        data = (ROOT / name).read_bytes()
        for case in patch_cases(name, data):
            case["outcome"] = outcome(patch(data, case["edits"]))
            hostile.append(case)
        for case in rebuilt_cases(name, data):
            case["outcome"] = outcome(base64.b64decode(case["archive"]))
            hostile.append(case)
    for case in synthetic_cases():
        case["outcome"] = outcome(base64.b64decode(case["archive"]))
        hostile.append(case)
    handler = handler_cases()
    HANDLER_OUTPUT.write_text(json.dumps({"python": sys.version.split()[0], "cases": handler}, ensure_ascii=True,
                                         indent=1) + "\n", encoding="utf-8", newline="\n")
    vectors = {"python": sys.version.split()[0], "platform": sys.platform, "repository": repository, "hostile": hostile}
    text = json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + "\n"
    OUTPUT.write_text(text, encoding="utf-8", newline="\n")
    kinds = {}
    for case in repository + hostile:
        key = case["outcome"][0] if case["outcome"][0] != "CommandRejected" else case["outcome"][1]
        kinds[key] = kinds.get(key, 0) + 1
    print(len(text) // 1000, "KB", len(repository), "repository archives", len(hostile), "hostile", kinds)


if __name__ == "__main__":
    main()
