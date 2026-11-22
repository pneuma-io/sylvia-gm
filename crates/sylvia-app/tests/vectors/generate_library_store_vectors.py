"""Generate reference vectors for sylvia_app::library_sources over the real AuthorityStore.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_library_store_vectors.py

Input: lane 3's package-read base (`crates/sylvia-store/tests/vectors/package_reads/base.sqlite3.gz`,
the Riichi profile plus one synthetic package with a file that is not UTF-8), opened by Python's
`AuthorityOwner`. Each reader (Riichi with and without its Setting, the synthetic package, and an
uninstalled Ruleset) lists candidates, then every candidate's files, reads of some of its paths
and missing ones, and records for each prefix and a bogus one.
Strings over 256 characters are kept as their length and SHA-256.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402
from sylvia_gm.application.library_sources import LibrarySourceReader  # noqa: E402
from sylvia_gm.domain.authority import thaw_json_value  # noqa: E402

BASE = "crates/sylvia-store/tests/vectors/package_reads/base.sqlite3.gz"
OUTPUT = Path(__file__).with_name("library_store_vectors.json")
READERS = [["pneuma.riichi-mahjong", "pneuma.riichi-club"], ["pneuma.riichi-mahjong", None],
           ["synthetic.package-reads", None], ["pneuma.absent", None]]


def shrink(value):
    if isinstance(value, str) and len(value) > 256:
        return {"length": len(value), "sha256": hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()}
    if isinstance(value, dict):
        return {key: shrink(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [shrink(item) for item in value]
    return value


def outcome(call):
    try:
        value = call()
    except Exception as error:  # every class is behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}
    return {"ok": shrink(thaw_json_value(value))}


def main() -> None:
    operations = []
    with tempfile.TemporaryDirectory() as scratch:
        database = Path(scratch) / "authority.sqlite3"
        database.write_bytes(gzip.decompress((ROOT / BASE).read_bytes()))
        with AuthorityOwner.open(database, "library-store-vectors") as owner:
            candidate_ids = [item.candidate_id for item in owner.package_candidates()]
            for ruleset_id, setting_id in READERS:
                reader = LibrarySourceReader(owner, ruleset_id, setting_id)

                def add(op, call, **arguments):
                    operations.append({"reader": [ruleset_id, setting_id], "op": op, **arguments,
                                       "outcome": outcome(call)})

                for query in ["", "riichi", "RIICHI", "synthetic", "zzz"]:
                    add("candidates", lambda: reader.candidates(query=query), query=query)
                for candidate_id in [*candidate_ids, "missing-candidate"]:
                    add("files", lambda: reader.files(candidate_id), candidate_id=candidate_id)
                    try:
                        listed = thaw_json_value(reader.files(candidate_id))
                    except Exception:  # noqa: BLE001 - already recorded
                        continue
                    paths = listed["paths"]
                    chosen = paths[:4] + [path for path in paths if "/notes/" in path]
                    prefixes = sorted({path.split("/", 1)[0] for path in paths})
                    for path in [*chosen, *(f"{prefix}/missing.txt" for prefix in prefixes), "nowhere/x.txt"]:
                        add("read", lambda: reader.read(candidate_id, path), candidate_id=candidate_id, path=path)
                    for prefix in [*prefixes, "bogus"]:
                        add("record", lambda: reader.record(candidate_id, prefix), candidate_id=candidate_id,
                            prefix=prefix)
    vectors = {"python": sys.version.split()[0], "base": BASE, "operations": operations}
    text = json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + "\n"
    assert str(ROOT) not in text and str(ROOT).replace(chr(92), "/") not in text
    OUTPUT.write_text(text, encoding="utf-8", newline="\n")
    kinds = {}
    for op in operations:
        result = op["outcome"]
        key = op["op"] + ": " + ("ok" if "ok" in result else f"{result['error']} {result['code']} {result['message'][:60]}")
        kinds[key] = kinds.get(key, 0) + 1
    print(len(operations), "operations")
    for key in sorted(kinds):
        print(" ", kinds[key], key)


if __name__ == "__main__":
    main()
