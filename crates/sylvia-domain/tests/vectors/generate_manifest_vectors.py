"""Generate reference vectors for sylvia_domain's manifests, declarations and closures.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-domain/tests/vectors/generate_manifest_vectors.py

Sources:
- the four shipped package records, which the Rust test reads from the Python owner's startup
  recording, and the small records and declaration files of the other archives in the
  repository (Python admits each archive to read them);
- mutations of every record and declaration file, stored as edits the Rust test applies;
- synthetic package graphs.

Packages of the private Trigger Discipline and Siren work are skipped and never copied here.
For each case it records Python's outcome: a SHA-256 of the result's field-by-field projection,
or the exception class and message (plus diagnostics for `ContentShapeError`).
The output is deterministic (fixed seed) and checked in, so the Rust tests need no Python.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import copy
import dataclasses
import enum
import glob
import hashlib
import json
import random
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.domain.authority import PackageRole  # noqa: E402
from sylvia_gm.domain.content_validation import ContentShapeError  # noqa: E402
from sylvia_gm.domain.package_library import (  # noqa: E402
    ExactPackageDependency,
    PackageGraphNode,
    campaign_setting_entries,
    canonical_json_bytes,
    closure_sha256,
    content_root_for_record,
    manifest_from_record,
    parse_campaign_setting_knowledge_bytes,
    parse_content_declarations_bytes,
    parse_gameplay_support_bytes,
    resolve_exact_closure,
    resolve_package_graph,
    validate_campaign_setting_knowledge_links,
    validate_content_declaration_links,
)
from sylvia_gm.infrastructure.package_archive import admit_package_archive  # noqa: E402

OUTPUT = Path(__file__).with_name("manifest_vectors.json")
RECORDING = ROOT / "crates/sylvia-compare/recordings/windows/startup.recording.json"
PRIVATE = ("trigger-discipline", "siren")
rng = random.Random(20261121)


def plain(value):
    """A dataclass result as JSON: fields in declaration order, tuples as lists."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: plain(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [plain(item) for item in value]
    return value


def digest(value) -> str:
    return hashlib.sha256(json.dumps(plain(value)).encode()).hexdigest()


def outcome(call):
    try:
        return ["ok", digest(call())]
    except ContentShapeError as error:
        return ["ContentShapeError", str(error), [item.as_payload() for item in error.diagnostics]]
    except Exception as error:  # every class is behaviour: ValueError, TypeError, KeyError...
        return [type(error).__name__, str(error)]


def private(package_id: str) -> bool:
    return any(word in package_id for word in PRIVATE)


def archive_material():
    records, documents = {}, {}
    paths = sorted(p for p in glob.glob(str(ROOT / "**/*.sylvia-gm-package"), recursive=True)
                   if "/.venv/" not in p.replace("\\", "/") and "/target/" not in p.replace("\\", "/"))
    for path in paths:
        with open(path, "rb") as source, admit_package_archive(source) as archive:
            for manifest in archive.manifests:
                if private(manifest.package_id) or len(manifest.canonical_json) > 40_000:
                    continue
                records[manifest.content_sha256] = json.loads(manifest.canonical_json)
            for item in archive.files:
                if private(item.manifest.package_id):
                    continue
                name = item.file.path
                kind = ("declarations" if name.endswith("content-declarations.json")
                        else "support" if name.endswith("gameplay-resolution.json")
                        else "knowledge" if name.endswith("setting-knowledge.json") else None)
                if kind is None:
                    continue
                data = b"".join(archive.iter_file_chunks(item))
                documents[hashlib.sha256(data).hexdigest()] = {
                    "kind": kind, "text": data.decode("utf-8"), "package": item.manifest.content_sha256}
    return records, documents


def leaves(value, path=()):
    yield path, value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from leaves(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from leaves(item, (*path, index))


JUNK = [None, True, False, 0, 1, -1, 1.0, 2.5, 10**6, "", " ", "x", "x ", "a/b", "A" * 64, "a" * 64,
        "sylvia.capability.t03", "primary", "dependency", "ruleset", "campaign_setting", "mechanics",
        [], [1], ["x"], ["b", "a"], ["a", "a"], {}, {"k": 1}, {"namespace": "n", "key": "k"}]


def apply(document, edits):
    document = copy.deepcopy(document)
    for op, path, value in edits:
        if not path:
            document = copy.deepcopy(value)
            continue
        parent = document
        for part in path[:-1]:
            parent = parent[part]
        if op == "delete":
            del parent[path[-1]]
        elif op == "swap":
            parent[path[-1]].reverse()
        else:
            parent[path[-1]] = copy.deepcopy(value)
    return document


def mutations(document, count):
    paths = list(leaves(document))
    edits = []
    for _ in range(count):
        path, value = rng.choice(paths)
        choice = rng.random()
        if isinstance(value, dict) and choice < 0.25 and value:
            edits.append([["delete", [*path, rng.choice(list(value))], None]])
        elif isinstance(value, dict) and choice < 0.35:
            edits.append([["set", [*path, rng.choice(["extra", "zz", "x-sylvia-gm-inventory"])], rng.choice(JUNK)]])
        elif isinstance(value, list) and len(value) > 1 and choice < 0.45:
            edits.append([["swap", list(path), None]])
        elif isinstance(value, list) and value and choice < 0.55:
            edits.append([["set", [*path, 0], copy.deepcopy(value[-1])]])
        elif path:
            edits.append([["set", list(path), rng.choice(JUNK)]])
    for _ in range(count // 5):
        group = rng.sample(edits, min(len(edits), 3)) if edits else []
        edits.append([edit for single in group for edit in single])
    return edits


def json_safe(document) -> bool:
    try:
        return json.loads(json.dumps(document)) == document and all_string_keys(document)
    except (TypeError, ValueError):
        return False


def all_string_keys(value) -> bool:
    if isinstance(value, dict):
        return all(isinstance(key, str) and all_string_keys(item) for key, item in value.items())
    if isinstance(value, list):
        return all(all_string_keys(item) for item in value)
    return True


def record_cases(name, record, count):
    cases = [{"base": name, "edits": [], "rehash": False, "outcome": outcome(lambda: manifest_from_record(record))}]
    for edits in mutations(record, count):
        try:
            document = apply(record, edits)
        except (KeyError, IndexError, TypeError, AttributeError):
            continue
        if not json_safe(document):
            continue
        touches_claim = any(edit[1][:1] == ["content_sha256"] for edit in edits)
        rehash = isinstance(document, dict) and "content_sha256" in document and not touches_claim
        if rehash:
            try:
                document["content_sha256"] = content_root_for_record(document)
            except Exception:
                rehash = False
        cases.append({"base": name, "edits": edits, "rehash": rehash,
                      "outcome": outcome(lambda: manifest_from_record(document))})
    return cases


PARSERS = {
    "declarations": parse_content_declarations_bytes,
    "support": parse_gameplay_support_bytes,
    "knowledge": parse_campaign_setting_knowledge_bytes,
}


def document_cases(name, item, count):
    parser = PARSERS[item["kind"]]
    raw = item["text"].encode("utf-8")
    cases = [{"base": name, "edits": [], "raw": None, "outcome": outcome(lambda: parser(raw))}]
    parsed = json.loads(raw)
    for variant in (json.dumps(parsed, indent=1), json.dumps(parsed), "{", "[]", "null"):
        cases.append({"base": name, "edits": [], "raw": variant,
                      "outcome": outcome(lambda: parser(variant.encode("utf-8")))})
    for edits in mutations(parsed, count):
        try:
            document = apply(parsed, edits)
        except (KeyError, IndexError, TypeError, AttributeError):
            continue
        if not json_safe(document):
            continue
        data = canonical_json_bytes(document)
        cases.append({"base": name, "edits": edits, "raw": None, "outcome": outcome(lambda: parser(data))})
    return cases


def link_cases(records, documents):
    cases = []
    for name, item in documents.items():
        record = records.get(item["package"])
        if record is None or item["kind"] == "support":
            continue
        manifest = manifest_from_record(record)
        parsed = PARSERS[item["kind"]](item["text"].encode("utf-8"))
        check = (validate_content_declaration_links if item["kind"] == "declarations"
                 else validate_campaign_setting_knowledge_links)
        cases.append({"document": name, "record": item["package"], "outcome": outcome(lambda: check(manifest, parsed))})
        # The same declarations against each other record: mostly unknown-link failures.
        for other_name, other in records.items():
            if other_name != item["package"]:
                other_manifest = manifest_from_record(other)
                cases.append({"document": name, "record": other_name,
                              "outcome": outcome(lambda: check(other_manifest, parsed))})
    return cases


SHA = [hashlib.sha256(str(i).encode()).hexdigest() for i in range(12)]


def node(index, role, fmt, kind, deps):
    return {"identity": [f"pkg.{index}", "1.0", SHA[index]], "role": role, "format_version": fmt,
            "content_kind": kind, "dependencies": deps}


def dep(target, kind="rules_reference", namespace=None):
    return [namespace or kind, kind, f"pkg.{target}", "1.0", SHA[target]]


def graph_inputs():
    graphs = [
        [node(0, "primary", 1, None, [])],
        [node(0, "primary", 1, None, [dep(1)]), node(1, "dependency", 1, None, [])],
        [node(0, "primary", 4, "ruleset", [dep(2, "rules_reference")]), node(1, "dependency", 4, "campaign_setting",
         [dep(0, "ruleset_snapshot")]), node(2, "dependency", 4, "support", [])],
        [node(0, "primary", 4, "ruleset", []), node(1, "dependency", 4, "campaign_setting", [dep(0, "ruleset_snapshot")])],
        [node(0, "primary", 4, "ruleset", []), node(1, "dependency", 1, None, [])],
        [node(0, "primary", 1, None, []), node(1, "primary", 1, None, [])],
        [node(0, "dependency", 1, None, [])],
        [node(0, "primary", 1, None, [dep(1)]), node(1, "dependency", 1, None, [dep(0)])],
        [node(0, "primary", 1, None, [dep(1)]), node(1, "dependency", 1, None, [dep(2)]),
         node(2, "dependency", 1, None, [dep(1)])],
        [node(0, "primary", 1, None, [dep(3)])],
        [node(0, "primary", 1, None, []), node(1, "dependency", 1, None, [])],
        [node(0, "primary", 4, "ruleset", [dep(1, "campaign_setting")]), node(1, "dependency", 4, "campaign_setting", [])],
        [node(0, "primary", 4, "ruleset", []), node(1, "dependency", 4, "campaign_setting", [dep(0, "ruleset_snapshot")]),
         node(2, "dependency", 4, "campaign_setting", [dep(0, "ruleset_snapshot")])],
        [node(0, "primary", 4, "ruleset", []), node(1, "dependency", 4, "campaign_setting", [])],
        [node(0, "primary", 4, "ruleset", []), node(1, "dependency", 4, "support", [dep(0, "ruleset_snapshot")])],
        [node(0, "primary", 4, "ruleset", []), node(1, "dependency", 4, "campaign_setting",
         [dep(0, "ruleset_snapshot", "other")])],
        [node(0, "primary", 1, None, [dep(1), dep(2, "x")]), node(1, "dependency", 1, None, [dep(2)]),
         node(2, "dependency", 1, None, [])],
        [node(0, "primary", 1, None, []), node(0, "dependency", 1, None, [])],
    ]
    conflicting = [node(0, "primary", 1, None, [dep(1), dep(2, "x")]), node(1, "dependency", 1, None, []),
                   node(2, "dependency", 1, None, [])]
    conflicting[2]["identity"][0] = "pkg.1"
    graphs.append(conflicting)
    for _ in range(60):
        size = rng.randint(1, 6)
        fmt = rng.choice((1, 4))
        nodes = []
        for index in range(size):
            role = "primary" if index == 0 else rng.choice(("dependency", "dependency", "primary"))
            kind = (rng.choice(("ruleset", "campaign_setting", "support")) if fmt == 4 else None)
            deps = [dep(rng.randrange(size + 1), rng.choice(("rules_reference", "ruleset_snapshot", "campaign_setting")))
                    for _ in range(rng.randint(0, 2))]
            nodes.append(node(index, role, fmt, kind, deps))
        graphs.append(nodes)
    return graphs


def graph_node(item):
    return PackageGraphNode(
        identity=tuple(item["identity"]), role=PackageRole(item["role"]), format_version=item["format_version"],
        content_kind=item["content_kind"],
        dependencies=tuple(ExactPackageDependency(*value) for value in item["dependencies"]))


def graph_cases():
    return [{"nodes": nodes, "outcome": outcome(lambda: resolve_package_graph(graph_node(n) for n in nodes))}
            for nodes in graph_inputs()]


def closure_cases(shipped):
    recording = json.loads(RECORDING.read_text(encoding="utf-8"))
    memberships = recording["database"]["tables"]["package_candidate_memberships"]["rows"]
    cases = []
    for candidate in sorted({row["candidate_id"] for row in memberships}):
        members = [row["content_sha256"] for row in memberships if row["candidate_id"] == candidate]
        manifests = [manifest_from_record(shipped[sha]) for sha in members]
        for order in (manifests, list(reversed(manifests))):
            lock = resolve_exact_closure(order)
            cases.append({
                "records": [m.content_sha256 for m in order],
                "closure_sha256": closure_sha256(lock),
                "lock": digest(lock),
                "settings": [entry.package_id for entry in campaign_setting_entries(lock)],
            })
    return cases


def main() -> None:
    recording = json.loads(RECORDING.read_text(encoding="utf-8"))
    shipped = {row["content_sha256"]: row["canonical_json"]["$json"]
               for row in recording["database"]["tables"]["package_manifests"]["rows"]}
    records, documents = archive_material()
    embedded = {sha: record for sha, record in records.items() if sha not in shipped}
    cases = []
    for sha, record in shipped.items():
        cases += record_cases(sha, record, 200)
    for sha, record in embedded.items():
        cases += record_cases(sha, record, 120)
    all_records = {**shipped, **embedded}
    document_vectors = []
    for name, item in documents.items():
        document_vectors += document_cases(name, item, 150)
    vectors = {
        "python": sys.version.split()[0],
        "records": embedded,
        "documents": documents,
        "record_cases": cases,
        "document_cases": document_vectors,
        "link_cases": link_cases(all_records, documents),
        "graph_cases": graph_cases(),
        "closure_cases": closure_cases(shipped),
    }
    OUTPUT.write_text(json.dumps(vectors, indent=None, ensure_ascii=True, separators=(",", ":")) + "\n",
                      encoding="utf-8", newline="\n")
    kinds = {}
    for case in cases + document_vectors:
        kinds[case["outcome"][0]] = kinds.get(case["outcome"][0], 0) + 1
    print(len(cases), "record cases,", len(document_vectors), "document cases", kinds)


if __name__ == "__main__":
    main()
