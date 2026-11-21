"""Generate reference vectors for sylvia_app::character_profiles and reusable_library_deletion.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_character_profile_vectors.py
    .venv/bin/python -I crates/sylvia-app/tests/vectors/generate_character_profile_vectors.py

The output is the same on Windows and Linux (a link is a junction on Windows and a symbolic link
elsewhere, and paths in error text are masked as `<ROOT>` with `/` separators), so one file
serves both; rerunning on either platform must leave it unchanged.

Three groups of cases:
- `validate`: `validate_character_profile` on the two published examples, random leaf edits of
  them, targeted v2 edits (duplicate IDs, asset references) and raw texts (malformed JSON,
  duplicate keys, NaN and overflowing numbers in walk order, nesting, size, encoding). Cases
  marked `row` meet a declared limit of docs/rust-port/differences.md instead.
- `snapshot` and `source`: `profile_from_snapshot` and `validate_source_profile`.
- `storage`: scripted sequences against a fresh storage folder: saves, retries, loads, catalogs,
  tampering, links, the 256-entry limit, and `delete_exact_library_entry`.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import random
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.application.character_profiles import (  # noqa: E402
    MAX_PROFILE_BYTES,
    character_profile_catalog,
    load_character_profile,
    profile_from_snapshot,
    save_character_profile,
    validate_character_profile,
    validate_source_profile,
)
from sylvia_gm.application.reusable_library_deletion import delete_exact_library_entry  # noqa: E402
from sylvia_gm.domain._content_schema_character_profile import EXAMPLE as V1  # noqa: E402
from sylvia_gm.domain._content_schema_character_profile_v2 import EXAMPLE as V2  # noqa: E402
from sylvia_gm.domain.package_library import canonical_json_bytes  # noqa: E402

OUTPUT = Path(__file__).with_name("character_profile_vectors.json")
BACKSLASH, NUL, NEWLINE = chr(92), chr(0), chr(10)
rng = random.Random(20261121)
BASES = {"v1": V1, "v2": V2}
JUNK = [None, True, False, 0, 2, 1.5, -1, "", " x", "x", "y" * 161, [], ["x"], {}, {"k": 1}, NUL]
PYTHON_DEPTH_LIMIT = 2997  # deepest nesting the Windows reference json.loads reads; Linux reads deeper
BEYOND_ANY_LIMIT = 120_000  # past every interpreter's limit, and still within 256 KiB


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def error_outcome(error: Exception) -> list:
    diagnostics = getattr(error, "diagnostics", None) or ()
    return [type(error).__name__, str(error), [item.as_payload() for item in diagnostics],
            getattr(error, "code", None)]


def apply(document, edits):
    document = copy.deepcopy(document)
    for op, path, value in edits:
        parent = document
        for part in path[:-1]:
            parent = parent[part]
        if op == "delete":
            del parent[path[-1]]
        elif op == "append":
            parent[path[-1]].append(copy.deepcopy(value))
        else:
            parent[path[-1]] = copy.deepcopy(value)
    return document


def leaves(value, path=()):
    yield path, value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from leaves(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from leaves(item, (*path, index))


def source_bytes(spec) -> bytes:
    """The Rust test rebuilds the same bytes from the same spec."""
    if "base" in spec:
        return canonical_json_bytes(apply(BASES[spec["base"]], spec.get("edits", [])))
    if "text" in spec:
        return spec["text"].encode("utf-8")
    if "nest" in spec:
        return nested(*spec["nest"]).encode("utf-8")
    if "latin1" in spec:
        return spec["latin1"].encode("latin-1")
    prefix, unit, count, suffix = spec["repeat"]
    return (prefix + unit * count + suffix).encode("utf-8")


def validate_case(spec, row=None):
    source = source_bytes(spec)
    try:
        value = validate_character_profile(source)
        outcome = ["ok", digest(canonical_json_bytes(value))]
    except Exception as error:  # every class is behaviour
        outcome = error_outcome(error)
    case = {"source": spec, "outcome": outcome}
    if row:
        case["row"] = row
    return case


def nested(depth: int, inner: str = "0") -> str:
    return "[" * depth + inner + "]" * depth


def validate_cases() -> list:
    cases = [validate_case({"base": "v1"}), validate_case({"base": "v2"})]
    for name, count in (("v1", 80), ("v2", 220)):
        paths = [(path, value) for path, value in leaves(BASES[name]) if path]
        for _ in range(count):
            path, value = rng.choice(paths)
            if isinstance(value, dict) and value and rng.random() < 0.3:
                edits = [["delete", [*path, rng.choice(list(value))], None]]
            else:
                edits = [["set", list(path), rng.choice(JUNK)]]
            cases.append(validate_case({"base": name, "edits": edits}))
    for section, item in (("relationships", {"id": "mentor", "name": "Ash", "relationship": "Peer."}),
                          ("dialogue_examples", {"id": "example-1", "text": "Again."}),
                          ("assets", {"id": "portrait", "uri": "card://other", "status": "reference_only"})):
        cases.append(validate_case({"base": "v2", "edits": [["append", [section], item]]}))
    cases.append(validate_case({"base": "v2", "edits": [["append", ["lore", "entries"],
                                                          {"id": "archive", "content": "Again."}]]}))
    cases.append(validate_case({"base": "v2", "edits": [["append", ["relationships"],
                                                          {"id": "other", "name": "Ash"}]]}))
    uris = [
        "https://invalid.example/a.png", "http://h/a", "https://user:secret@invalid.example/a", "https://user@h/a",
        "https://:@h/a", "https://:pw@h/a", "https:///a", "http://[::1]/a", "http://[::1", "http://h]/a",
        "http://[1.2.3.4]/a", "http://[v1.x]/a", "http://[vz.x]/a", "http://x[::1]/a", "http://[::1]x/a",
        "http://[::1]:80/a", "https://h:99999/a", "https://H.EXAMPLE/a", "https://" + chr(0x2100) + ".example/a",
        "https://" + chr(0xff21) + ".example/a", "https://h/a" + chr(0x3000), "https://h/" + chr(0x1c),
        "https://h/" + chr(0x200b), "card://portrait", "card://", "card://../a", "card://a/../b", "card://a/..",
        "card://a" + BACKSLASH + "b", "card:///abs", "card://a//b", "card://...", "asset:sha256:" + "a" * 64,
        "asset:sha256:" + "A" * 64, "asset:sha256:" + "a" * 63, "asset:sha256:" + "a" * 65, "asset:sha256:",
        "asset-sha256:bad", "file:///secret", "HTTPS://h/a", "",
    ]
    for uri in uris:
        cases.append(validate_case({"base": "v2", "edits": [["set", ["assets", 0, "uri"], uri]]}))
    deep33 = nested(33)
    texts = [
        "", " ", "{}", "[]", "null", "0", '"x"', chr(0xfeff) + "{}", '{"a":1,"a":2}', '{"a":{"b":1,"b":2}}',
        '{"a":NaN}', '{"a":Infinity}', "[-Infinity]", '{"a":1e999}', "[-1e999]", "[1E400, 1]",
        "[1e999," + deep33 + "]", "[" + deep33 + ",1e999]", "[" + nested(40, "1e999") + "]",
        "[1e999, NaN]", "[NaN, 1e999]", '{"a":1,"a":NaN}', '{"a":NaN,"a":1}', "{", '{"a":}', "[1,]",
        NEWLINE + NEWLINE + "  {x", "{}" + NEWLINE + "x", '{"a":"' + BACKSLASH + 'x"}', '"' + chr(1) + '"',
        nested(31), nested(32), nested(33), nested(127), nested(128), nested(129), nested(200),
        nested(PYTHON_DEPTH_LIMIT), '{"format_version":2}', '{"format_version":2.0}', '{"format_version":true}',
        json.dumps({**V1, "format_version": 1.0}), json.dumps({**V2, "format_version": True}),
        json.dumps(V1, indent=2), json.dumps(V2, ensure_ascii=False, indent=1),
    ]
    for text in texts:
        cases.append(validate_case({"text": text}))
    cases.append(validate_case({"latin1": "{" + chr(0xff) + "}"}))
    cases.append(validate_case({"latin1": '{"name":"' + chr(0xed) + chr(0xa0) + chr(0x80) + '"}'}))
    # Row 4: beyond Python's nesting, or malformed past 128 levels, Python reports malformed JSON.
    cases.append(validate_case({"nest": [BEYOND_ANY_LIMIT, "0"]}, row=4))
    cases.append(validate_case({"text": "[" * 200}, row=4))
    cases.append(validate_case({"text": nested(150, "NaN")}, row=4))
    # Row 1: an integer beyond 64 bits; row 3: a lone surrogate.
    cases.append(validate_case({"text": '{"a":123456789012345678901234567890}'}, row=1))
    cases.append(validate_case({"text": json.dumps({**V1, "name": "Vela"}).replace('"Vela"', '"' + BACKSLASH + 'ud800"')},
                               row=3))
    budget = MAX_PROFILE_BYTES
    cases.append(validate_case({"repeat": ["", " ", budget - 2, "{}"]}))
    cases.append(validate_case({"repeat": ["", " ", budget - 1, "{}"]}))
    head = json.dumps({**V2, "foreign_configuration": {"status": "preserved_only", "format": "x",
                                                       "settings": {"n": []}}})[:-4]
    # 14 arrays of 1,024 `1e15`: about 72 KB of text, but each is `1000000000000000.0` canonically.
    row_of_numbers = "[" + "1e15," * 1023 + "1e15],"
    cases.append(validate_case({"repeat": [head, row_of_numbers, 14, "[0]]}}}"]}))
    cases.append(validate_case({"repeat": [head, row_of_numbers, 12, "[0]]}}}"]}))
    return cases


def snapshot_cases() -> list:
    v2 = canonical_json_bytes(V2)
    sha = digest(v2)
    good = {"artifact_id": "character-profile-" + "0" * 24 + "-" + sha[:16], "content_sha256": sha, "profile": V2}
    changes = [
        {}, {"artifact_id": "character-profile-" + "0" * 24 + "-" + "f" * 16},
        {"artifact_id": "character-profile-" + "0" * 23 + "-" + sha[:16]}, {"artifact_id": 7},
        {"content_sha256": "0" * 64}, {"content_sha256": sha.upper()}, {"content_sha256": None},
        {"profile": V1}, {"profile": {**V2, "name": ""}}, {"profile": []}, {"extra": 1},
    ]
    cases = []
    for change in changes:
        value = {**good, **change}
        cases.append({"value": value, "outcome": snapshot_outcome(value)})
    for value in (None, [], {"artifact_id": good["artifact_id"]}):
        cases.append({"value": value, "outcome": snapshot_outcome(value)})
    return cases


def snapshot_outcome(value) -> list:
    try:
        profile = profile_from_snapshot(value)
    except Exception as error:
        return error_outcome(error)
    return ["ok", profile.artifact_id, profile.content_sha256, digest(canonical_json_bytes(profile.profile)),
            profile.provenance(), profile.catalog_payload(), profile.snapshot() == value]


def source_cases() -> list:
    sha = digest(canonical_json_bytes(V1))
    good = {"artifact_id": "character-profile-" + "1" * 24 + "-" + sha[:16], "content_sha256": sha, "name": "Vela"}
    values = [None, good, [], {**good, "extra": 1}, {**good, "name": ""}, {**good, "name": " Vela"},
              {**good, "name": "Vela" + chr(0x3000)}, {**good, "name": "V" * 160}, {**good, "name": "V" * 161},
              {**good, "name": "V" + NUL}, {**good, "name": 3}, {**good, "content_sha256": "0" * 64},
              {**good, "artifact_id": "x"}, {**good, "name": chr(0x1f600) * 160}]
    cases = []
    for value in values:
        try:
            outcome = ["ok", validate_source_profile(value)]
        except Exception as error:
            outcome = error_outcome(error)
        cases.append({"value": value, "outcome": outcome})
    return cases


def profile_id(operation_id: str, spec) -> str:
    content = digest(canonical_json_bytes(apply(BASES[spec["base"]], spec.get("edits", []))))
    return "character-profile-" + digest(operation_id.encode("utf-8"))[:24] + "-" + content[:16]


def make_link(path: Path, target: Path) -> None:
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(path))
    else:
        os.symlink(target, path, target_is_directory=True)


class Masker:
    def __init__(self, base: Path):
        self.marks = [(str(base), "<ROOT>")]

    def __call__(self, value):
        if isinstance(value, str):
            for real, mark in self.marks:
                value = value.replace(real.replace(BACKSLASH, BACKSLASH * 2), mark).replace(real, mark)
            return value.replace(BACKSLASH * 2, "/").replace(BACKSLASH, "/")
        if isinstance(value, list):
            return [self(item) for item in value]
        if isinstance(value, dict):
            return {key: self(item) for key, item in value.items()}
        return value


def run_step(base: Path, step: dict, mask: Masker):
    storage = base / step.get("storage", "storage")
    op = step["op"]
    try:
        if op == "save":
            profile = save_character_profile(storage, operation_id=step["operation_id"],
                                             source=source_bytes(step["source"]))
            return ["ok", profile.artifact_id, profile.content_sha256, profile.catalog_payload()]
        if op == "load":
            profile = load_character_profile(storage, step["artifact_id"], step.get("expected"))
            return ["ok", profile.artifact_id, profile.content_sha256, profile.provenance()]
        if op == "catalog":
            invalid = []
            found = character_profile_catalog(storage, on_invalid=lambda name, error: invalid.append(
                [name, *mask(error_outcome(error))]))
            return ["ok", [profile.artifact_id for profile in found], invalid]
        if op == "delete":
            return ["ok", delete_exact_library_entry(storage, library_name=step["library"],
                                                     artifact_id=step["artifact_id"],
                                                     owned_names=frozenset(step["owned"]))]
    except Exception as error:
        return mask(error_outcome(error))
    path = base / step["path"]
    if op == "write":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(source_bytes(step["source"]))
    elif op == "mkdir":
        path.mkdir(parents=True, exist_ok=True)
    elif op == "remove":
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    elif op == "copy":
        shutil.copytree(base / step["from"], path)
    elif op == "link":
        path.parent.mkdir(parents=True, exist_ok=True)
        make_link(path, base / step["target"])
    elif op == "unlink":
        os.rmdir(path) if os.name == "nt" else path.unlink()
    elif op == "fill":
        path.mkdir(parents=True, exist_ok=True)
        for index in range(step["count"]):
            (path / f"filler-{index:03d}").mkdir()
    else:
        raise AssertionError(op)
    return None


def storage_scripts() -> dict:
    v1, v2 = {"base": "v1"}, {"base": "v2"}
    a1, a2, b1 = profile_id("a", v1), profile_id("a", v2), profile_id("b", v1)
    lib = "storage/character-profiles"
    bad = {"base": "v2", "edits": [["set", ["name"], ""]]}
    edited = {"base": "v1", "edits": [["set", ["name"], "Other"]]}
    unknown = "character-profile-" + "0" * 24 + "-" + "0" * 16
    owned = ["profile.json", "record.json"]
    record = lambda **fields: {"text": json.dumps({"format_id": "sylvia-gm-character-profile-record",  # noqa: E731
                                                    "format_version": 1, "artifact_id": a1,
                                                    "content_sha256": digest(canonical_json_bytes(V1)), **fields})}
    return {
        "basic": [
            {"op": "catalog"}, {"op": "save", "operation_id": "a", "source": v1},
            {"op": "save", "operation_id": "a", "source": v1}, {"op": "save", "operation_id": "b", "source": v1},
            {"op": "save", "operation_id": "a", "source": v2}, {"op": "save", "operation_id": "", "source": v1},
            {"op": "save", "operation_id": "c", "source": bad}, {"op": "save", "operation_id": "c", "source": {"text": "{"}},
            {"op": "load", "artifact_id": a1}, {"op": "load", "artifact_id": a1, "expected": "0" * 64},
            {"op": "load", "artifact_id": a1, "expected": digest(canonical_json_bytes(V1))},
            {"op": "load", "artifact_id": unknown}, {"op": "load", "artifact_id": "bad"},
            {"op": "load", "artifact_id": a1.upper()}, {"op": "catalog"},
        ],
        "tamper": [
            {"op": "save", "operation_id": "a", "source": v1}, {"op": "save", "operation_id": "b", "source": v1},
            {"op": "write", "path": f"{lib}/{a1}/extra.txt", "source": {"text": "x"}},
            {"op": "load", "artifact_id": a1}, {"op": "catalog"}, {"op": "remove", "path": f"{lib}/{a1}/extra.txt"},
            {"op": "mkdir", "path": f"{lib}/{a1}/sub"}, {"op": "load", "artifact_id": a1},
            {"op": "remove", "path": f"{lib}/{a1}/sub"},
            {"op": "write", "path": f"{lib}/{a1}/profile.json", "source": edited}, {"op": "load", "artifact_id": a1},
            {"op": "write", "path": f"{lib}/{a1}/profile.json", "source": {"text": "{}"}},
            {"op": "load", "artifact_id": a1},
            {"op": "write", "path": f"{lib}/{a1}/profile.json", "source": v1},
            {"op": "write", "path": f"{lib}/{a1}/record.json", "source": record(format_version=1.0)},
            {"op": "load", "artifact_id": a1},
            {"op": "write", "path": f"{lib}/{a1}/record.json", "source": record(artifact_id=b1)},
            {"op": "load", "artifact_id": a1},
            {"op": "write", "path": f"{lib}/{a1}/record.json", "source": record(extra=1)},
            {"op": "load", "artifact_id": a1},
            {"op": "write", "path": f"{lib}/{a1}/record.json", "source": record(content_sha256="0" * 64)},
            {"op": "load", "artifact_id": a1},
            {"op": "write", "path": f"{lib}/{a1}/record.json", "source": {"text": "[1,1e999]"}},
            {"op": "load", "artifact_id": a1},
            {"op": "remove", "path": f"{lib}/{a1}/record.json"}, {"op": "load", "artifact_id": a1},
            {"op": "remove", "path": f"{lib}/{a1}/profile.json"}, {"op": "load", "artifact_id": a1},
            {"op": "write", "path": f"{lib}/{a1}/record.json", "source": record()},
            {"op": "load", "artifact_id": a1},
            {"op": "write", "path": f"{lib}/notes.txt", "source": {"text": "x"}},
            {"op": "mkdir", "path": f"{lib}/{unknown}"}, {"op": "write", "path": f"{lib}/{b1}x", "source": {"text": ""}},
            {"op": "catalog"}, {"op": "save", "operation_id": "a", "source": v1},
        ],
        "unsafe_root": [
            {"op": "write", "path": lib, "source": {"text": "x"}}, {"op": "save", "operation_id": "a", "source": v1},
            {"op": "load", "artifact_id": a1}, {"op": "catalog"},
            {"op": "delete", "library": "character-profiles", "artifact_id": a1, "owned": owned},
        ],
        "links": [
            {"op": "save", "operation_id": "a", "source": v1}, {"op": "save", "operation_id": "b", "source": v1},
            {"op": "copy", "path": f"outside/{a2}", "from": f"{lib}/{a1}"},
            {"op": "link", "path": f"{lib}/{a2}", "target": f"outside/{a2}"},
            {"op": "load", "artifact_id": a2}, {"op": "catalog"},
            {"op": "delete", "library": "character-profiles", "artifact_id": a2, "owned": owned},
            {"op": "unlink", "path": f"{lib}/{a2}"},
            {"op": "copy", "path": "outside/library", "from": lib},
            {"op": "link", "path": "linked/character-profiles", "target": "outside/library"},
            {"op": "save", "storage": "linked", "operation_id": "a", "source": v1},
            {"op": "load", "storage": "linked", "artifact_id": a1}, {"op": "catalog", "storage": "linked"},
            {"op": "delete", "storage": "linked", "library": "character-profiles", "artifact_id": a1,
             "owned": owned},
        ],
        "limits": [
            {"op": "fill", "path": lib, "count": 255}, {"op": "save", "operation_id": "a", "source": v1},
            {"op": "save", "operation_id": "b", "source": v1}, {"op": "save", "operation_id": "a", "source": v1},
            {"op": "fill", "path": "storage/spare", "count": 0}, {"op": "mkdir", "path": f"{lib}/zz-extra"},
            {"op": "catalog"},
        ],
        "delete": [
            {"op": "save", "operation_id": "a", "source": v1},
            {"op": "delete", "library": "character-profiles", "artifact_id": a1, "owned": owned},
            {"op": "delete", "library": "character-profiles", "artifact_id": a1, "owned": owned},
            {"op": "catalog"}, {"op": "save", "operation_id": "a", "source": v1},
            {"op": "write", "path": f"{lib}/{a1}/extra.txt", "source": {"text": "x"}},
            {"op": "delete", "library": "character-profiles", "artifact_id": a1, "owned": owned},
            {"op": "delete", "library": "character-profiles", "artifact_id": a1,
             "owned": [*owned, "extra.txt"]},
            {"op": "load", "artifact_id": a1},
            {"op": "delete", "library": "missing-library", "artifact_id": a1, "owned": owned},
        ],
    }


def storage_cases() -> dict:
    results = {}
    for name, steps in storage_scripts().items():
        base = Path(tempfile.mkdtemp(prefix="sylvia-profiles-")).resolve()
        try:
            mask = Masker(base)
            recorded = []
            for step in steps:
                recorded.append({**step, "outcome": run_step(base, step, mask)})
            results[name] = recorded
        finally:
            shutil.rmtree(base, ignore_errors=True)
    return results


def main() -> None:
    vectors = {
        "python": ".".join(sys.version.split()[0].split(".")[:2]),
        "bases": BASES,
        "validate": validate_cases(),
        "snapshot": snapshot_cases(),
        "source": source_cases(),
        "storage": storage_cases(),
    }
    text = json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + NEWLINE
    for marker in (tempfile.gettempdir(), os.path.expanduser("~")):
        assert marker not in text and marker.replace(BACKSLASH, "/") not in text, "a machine path leaked"
    OUTPUT.write_text(text, encoding="utf-8", newline=NEWLINE)
    kinds = {}
    for case in vectors["validate"]:
        outcome = case["outcome"]
        key = "ok" if outcome[0] == "ok" else (outcome[2][0]["code"] if outcome[2] else outcome[0])
        kinds[key] = kinds.get(key, 0) + 1
    print(len(vectors["validate"]), "validate,", len(vectors["snapshot"]), "snapshot,", len(vectors["source"]),
          "source,", sum(map(len, vectors["storage"].values())), "storage steps;", len(text), "bytes")
    print(" ", dict(sorted(kinds.items())))


if __name__ == "__main__":
    main()
