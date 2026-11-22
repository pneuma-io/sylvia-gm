"""Generate reference vectors for sylvia_app::library_sources (application/library_sources.py).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_library_source_vectors.py

Each case builds a `LibrarySourceReader` over a fake owner holding the named candidates, whose
packages have the files, texts, records and labels in `packages`, then calls one method:
`candidates(query)`, `files(candidate_id)`, `read(candidate_id, path)` or
`record(candidate_id, prefix)`. Outcomes are the result or the exception class, text and code.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.application.library_sources import LibrarySourceReader  # noqa: E402
from sylvia_gm.domain.authority import (  # noqa: E402
    PackageDependency,
    PackageLock,
    PackageLockEntry,
    PackageRole,
    thaw_json_value,
)
from sylvia_gm.domain.errors import CommandRejected  # noqa: E402
from sylvia_gm.domain.package_library import CandidateStatus, PackageCandidate  # noqa: E402

OUTPUT = Path(__file__).with_name("library_source_vectors.json")


def sha(name: str) -> str:
    return (name.encode("utf-8").hex() * 64)[:64]


def entry(package_id, role, dependencies=()):
    return {"id": package_id, "version": "1.0.0", "role": role, "dependencies": [list(item) for item in dependencies]}


def lock_entry(order, item):
    return PackageLockEntry(
        package_id=item["id"], version=item["version"], sha256=sha(item["id"]),
        role=PackageRole.PRIMARY if item["role"] == "primary" else PackageRole.DEPENDENCY, load_order=order,
        dependencies=tuple(PackageDependency(package_id=d[0], version="1.0.0", sha256=sha(d[0]), kind=d[1], namespace=d[2])
                           for d in item["dependencies"]),
        capabilities=())


def candidate_from(spec):
    return PackageCandidate(candidate_id=spec["candidate_id"], install_id="install-" + spec["candidate_id"],
                            status=CandidateStatus(spec.get("status", "validated")), closure_sha256=sha(spec["candidate_id"]),
                            package_lock=PackageLock(tuple(lock_entry(i, item) for i, item in enumerate(spec["entries"]))),
                            validation_report={})


SNAPSHOT = ("pneuma.rules", "ruleset_snapshot", "ruleset")
RULES = entry("pneuma.rules", "primary", [("pneuma.lib", "library", "lib")])
LIB = entry("pneuma.lib", "dependency")
WORLD = entry("pneuma.world", "dependency", [SNAPSHOT, ("pneuma.world-lib", "library", "wlib")])
WORLD_LIB = entry("pneuma.world-lib", "dependency")
OTHER_WORLD = entry("pneuma.other-world", "dependency", [SNAPSHOT])
CANDIDATES = {
    "rules": {"candidate_id": "rules", "entries": [RULES, LIB]},
    "world": {"candidate_id": "world", "entries": [RULES, LIB, WORLD, WORLD_LIB]},
    "two-worlds": {"candidate_id": "two-worlds", "status": "activated",
                   "entries": [RULES, LIB, WORLD, WORLD_LIB, OTHER_WORLD]},
    "staging": {"candidate_id": "staging", "status": "staging", "entries": [RULES, LIB]},
    "failed": {"candidate_id": "failed", "status": "failed", "entries": [RULES, LIB]},
    "other": {"candidate_id": "other", "entries": [entry("pneuma.other", "primary")]},
    "wide": {"candidate_id": "wide", "entries": [entry("pneuma.rules", "primary", [(f"p{i:02d}", "library", f"n{i}") for i in range(64)]),
                                                *[entry(f"p{i:02d}", "dependency") for i in range(64)]]},
    "many-files": {"candidate_id": "many-files", "entries": [entry("pneuma.rules", "primary", [("pneuma.big", "library", "big")]),
                                                            entry("pneuma.big", "dependency")]},
}
PACKAGES = {
    "pneuma.rules": {"label": "Rules", "files": ["runtime/ruleset.yaml", "prompts/guide.md", "docs/Read Me.txt"],
                     "texts": {"runtime/ruleset.yaml": "tools: {}", "prompts/guide.md": "# Guide " + chr(0xe9)},
                     "record": {"id": "pneuma.rules", "version": "1.0.0", "content_kind": "ruleset", "content_sha256": "x",
                                "files": [], "dependencies": [{"id": "pneuma.lib", "version": "1.0.0", "namespace": "lib",
                                                               "content_sha256": "y"}], "metadata": {"label": "Rules"}}},
    "pneuma.lib": {"label": "Library", "files": ["lib/data.json"], "texts": {"lib/data.json": "{}"},
                   "record": {"id": "pneuma.lib", "version": "1.0.0", "dependencies": [], "metadata": {"label": "Library"}}},
    "pneuma.world": {"label": "Ash Coast", "files": ["world/atlas.md"], "texts": {"world/atlas.md": "Atlas"},
                     "record": {"id": "pneuma.world", "version": "1.0.0", "dependencies": [
                         {"id": "pneuma.rules", "version": "1.0.0", "namespace": "ruleset"},
                         {"id": "pneuma.world-lib", "version": "1.0.0", "namespace": "wlib"}], "metadata": {"label": "Ash Coast"}}},
    "pneuma.world-lib": {"label": "Coast Notes", "files": ["notes.md"], "texts": {}, "record": {"id": "pneuma.world-lib",
                         "version": "1.0.0", "dependencies": []}},
    "pneuma.other-world": {"label": "Elsewhere", "files": ["far.md"], "texts": {"far.md": "Far"},
                           "record": {"id": "pneuma.other-world", "version": "1.0.0", "dependencies": []}},
    "pneuma.other": {"label": "Other", "files": ["x.md"], "texts": {}, "record": {"id": "pneuma.other"}},
    "pneuma.big": {"label": "Big", "files": [f"f{i:04d}.md" for i in range(2050)], "texts": {}, "record": {"id": "pneuma.big"}},
    **{f"p{i:02d}": {"label": f"P{i}", "files": [], "texts": {}, "record": {"id": f"p{i:02d}"}} for i in range(64)},
}


class Owner:
    def __init__(self, names):
        self.candidates = [candidate_from(CANDIDATES[name]) for name in names]

    def package_candidates(self):
        return tuple(self.candidates)

    def package_candidate(self, candidate_id):
        for candidate in self.candidates:
            if candidate.candidate_id == candidate_id:
                return candidate
        raise CommandRejected("Package candidate is unavailable", code="candidate_unavailable")

    def package_file_paths(self, package_id, version, sha256, *, maximum_files):
        paths = PACKAGES[package_id]["files"]
        if len(paths) > maximum_files:
            raise CommandRejected("Package has too many files to inspect")
        return tuple(paths)

    def package_text_file(self, package_id, version, sha256, path, *, maximum_bytes):
        texts = PACKAGES[package_id]["texts"]
        if path not in texts:
            raise CommandRejected("Package source path is unavailable")
        return texts[path]

    def package_record(self, package_id, version, sha256):
        return json.loads(json.dumps(PACKAGES[package_id]["record"]))

    def package_label(self, package_id, version, sha256):
        return PACKAGES[package_id]["label"]


def outcome(owner_names, ruleset_id, setting_id, method, args):
    reader = LibrarySourceReader(Owner(owner_names), ruleset_id, setting_id)
    try:
        result = getattr(reader, method)(*args) if method != "candidates" else reader.candidates(query=args[0])
    except Exception as error:  # every class is behaviour
        return [type(error).__name__, str(error), getattr(error, "code", None)]
    return ["ok", thaw_json_value(result) if not isinstance(result, tuple) else [thaw_json_value(item) for item in result]]


def main() -> None:
    normal = ["rules", "world", "two-worlds", "staging", "failed", "other", "many-files"]
    calls = [
        ("candidates", [""]), ("candidates", ["ash"]), ("candidates", ["LIBRARY"]), ("candidates", ["1.0.0"]),
        ("candidates", ["zzz"]), ("candidates", [sha("world")[:8]]),
        ("files", ["rules"]), ("files", ["world"]), ("files", ["two-worlds"]), ("files", ["other"]),
        ("files", ["staging"]), ("files", ["missing"]), ("files", ["many-files"]),
        ("read", ["world", "ruleset/runtime/ruleset.yaml"]), ("read", ["world", "ruleset/prompts/guide.md"]),
        ("read", ["world", "setting/world/atlas.md"]), ("read", ["world", "dependencies/pneuma.lib/lib/data.json"]),
        ("read", ["world", "dependencies/pneuma.world-lib/notes.md"]), ("read", ["world", "ruleset/docs/Read Me.txt"]),
        ("read", ["world", "ruleset/missing.md"]), ("read", ["world", "elsewhere/x.md"]),
        ("read", ["two-worlds", "dependencies/pneuma.other-world/far.md"]), ("read", ["rules", "ruleset/"]),
        ("record", ["world", "ruleset"]), ("record", ["world", "setting"]), ("record", ["world", "dependencies/pneuma.lib"]),
        ("record", ["world", "dependencies/pneuma.world-lib"]), ("record", ["world", "nothing"]),
        ("record", ["two-worlds", "setting"]),
    ]
    cases = []
    for ruleset_id, setting_id in (("pneuma.rules", "pneuma.world"), ("pneuma.rules", None),
                                   ("pneuma.rules", "pneuma.other-world"), ("pneuma.other", None)):
        for method, args in calls:
            cases.append({"owner": normal, "ruleset_id": ruleset_id, "setting_id": setting_id, "method": method,
                          "args": args, "outcome": outcome(normal, ruleset_id, setting_id, method, args)})
    for method, args in (("candidates", [""]), ("files", ["wide"]), ("read", ["wide", "ruleset/runtime/ruleset.yaml"])):
        cases.append({"owner": ["wide", "rules"], "ruleset_id": "pneuma.rules", "setting_id": None, "method": method,
                      "args": args, "outcome": outcome(["wide", "rules"], "pneuma.rules", None, method, args)})
    many = {f"c{i:03d}": {"candidate_id": f"c{i:03d}", "entries": [RULES, LIB]} for i in range(101)}
    CANDIDATES.update(many)
    crowd = list(many)
    for query in ("", "c050", "nothing"):
        cases.append({"owner": crowd, "ruleset_id": "pneuma.rules", "setting_id": None, "method": "candidates",
                      "args": [query], "outcome": outcome(crowd, "pneuma.rules", None, "candidates", [query])})
    vectors = {"python": ".".join(sys.version.split()[0].split(".")[:2]), "candidates": CANDIDATES,
               "packages": PACKAGES, "cases": cases}
    OUTPUT.write_text(json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + chr(10), encoding="utf-8",
                      newline=chr(10))
    kinds = {}
    for case in cases:
        key = case["outcome"][0] if case["outcome"][0] == "ok" else case["outcome"][1][:60]
        kinds[key] = kinds.get(key, 0) + 1
    print(len(cases), "cases", OUTPUT.stat().st_size, "bytes")
    for key in sorted(kinds):
        print(" ", kinds[key], key)


if __name__ == "__main__":
    main()
