"""Generate `package_model_decision_boundary` vectors for sylvia-app::package_runtime from the
frozen Python.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_decision_boundary_vectors.py

(`.venv/bin/python` on Linux.) Deterministic and checked in. Reference tooling for the port,
retired with the Python source.

Each case is a runtime (the shipped SRD Lite and Riichi runtimes, the conformance fixture, and
edits of the fixture) and a package id. The record is the projection Python returns, or the
exception's class and message. The edits cover the scope/boundary choice (an empty scope, a
scope that is not an object, both absent), what the execution load accepts that the authoring
load refuses (duplicate keys, fields outside the content schema), and the checks both loads
share.
"""

from __future__ import annotations

import json
import os
import random
import sys
import zipfile
from pathlib import Path

import jsonschema._keywords

import sylvia_gm.application.package_runtime as pr
from sylvia_gm.application.package_runtime import package_model_decision_boundary

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OUTPUT = HERE / "decision_boundary_vectors.json"
FIXTURE = "tests/fixtures/rulesets/signal-and-static/runtime/ruleset.yaml"
SRD = "rulesets/srd-lite"
RIICHI = "rulesets/riichi-mahjong/riichi-mahjong-0.1.1.sylvia-gm-package"
NAMESPACE = "sylvia-gm.conformance.signal-static"

os.environ.pop("SYLVIA_RUNTIME_CACHE_DIR", None)


class _SortedSet(set):
    """A `set` that iterates in sorted order where its items allow it (as the runtime vectors)."""

    def __iter__(self):
        items = list(super().__iter__())
        try:
            return iter(sorted(items))
        except TypeError:
            return iter(items)


pr.set = _SortedSet
jsonschema._keywords.set = lambda iterable: list(dict.fromkeys(iterable))  # row 6


def outcome(package_id: str, runtime_bytes: bytes) -> dict:
    pr._PARSED_RUNTIMES.clear()  # each case parses afresh, as Rust does
    try:
        value = package_model_decision_boundary(package_id=package_id, runtime_bytes=runtime_bytes)
    except Exception as error:  # noqa: BLE001 - every exception Python lets through
        return {"error": [type(error).__name__, str(error)]}
    return {"result": value}


SCOPE = f"""model_decision_scope:
  decision_owner: {{namespace: {NAMESPACE}, key: state.owner}}
  active_side: {{namespace: {NAMESPACE}, key: state.side}}
  eligible_actors: {{namespace: {NAMESPACE}, key: state.eligible}}
  focused_actor: {{namespace: {NAMESPACE}, key: state.focus}}
  actor_entities: {{namespace: {NAMESPACE}, key: state.entities}}
"""


def edits(text: str) -> list[tuple[str, str]]:
    boundary_start = text.index("model_decision_boundary:")
    boundary_end = text.index("tools:")
    boundary = text[boundary_start:boundary_end]
    without = text[:boundary_start] + text[boundary_end:]
    cases = [
        ("fixture", text),
        ("no boundary", without),
        ("scope and boundary", text + SCOPE),
        ("scope only", without + SCOPE),
        ("empty scope", text + "model_decision_scope: {}\n"),
        ("empty scope, no boundary", without + "model_decision_scope: {}\n"),
        ("scope list", text + "model_decision_scope: [1]\n"),
        ("boundary list", without + "model_decision_boundary: [1]\n"),
        ("empty boundary", without + "model_decision_boundary: {}\n"),
        ("null scope", text + "model_decision_scope: null\n"),
        ("scope missing a field", text + SCOPE.replace(f"  focused_actor: {{namespace: {NAMESPACE}, key: state.focus}}\n", "")),
        ("scope with preparation", text + SCOPE + "  preparation: {}\n"),
        # Duplicate keys: the execution load keeps the later value; the authoring load refuses.
        ("duplicate top-level boundary", text + boundary),
        ("duplicate boundary field", text.replace("  default_owner: gm\n", "  default_owner: gm\n  default_owner: player\n")),
        ("duplicate skills", text + "skills: []\n"),
        # Outside the authored content schema, inside what execution checks.
        ("unknown top-level field", text + "notes: hello\n"),
        ("boundary extra field", text.replace("  default_owner: gm\n", "  default_owner: gm\n  colour: red\n")),
        # What both loads refuse.
        ("not utf-8", "\udcff"),
        ("invalid yaml", text + "tools: [\n"),
        ("not an object", "- 1\n- 2\n"),
        ("wrong namespace", text.replace(f"state_namespace: {NAMESPACE}", "state_namespace: other.package")),
        ("bad descriptor", text.replace("  format: 1\n", "  format: 1\n  extra: 2\n")),
        ("meta repeated", text + "meta: {}\n"),
        ("too deep", text + "deep: " + "[" * 70 + "]" * 70 + "\n"),
    ]
    return cases


def random_edit(rng: random.Random, text: str) -> str:
    lines = text.splitlines(keepends=True)
    choice = rng.random()
    if choice < 0.3:
        index = rng.randrange(len(lines))
        lines.insert(index, lines[rng.randrange(len(lines))])
    elif choice < 0.5:
        del lines[rng.randrange(len(lines))]
    elif choice < 0.8:
        index = rng.randrange(len(lines))
        lines[index] = lines[index].replace(rng.choice(["gm", "player", "state", "1", "true", ":"]),
                                            rng.choice(["", "x", "2", "null", "[]", "{}"]), 1)
    else:
        lines.append(rng.choice([SCOPE, "model_decision_scope: {}\n", "model_decision_boundary: {}\n", "x: 1\n"]))
    return "".join(lines)


def main() -> int:
    fixture = (ROOT / FIXTURE).read_text(encoding="utf-8")
    cases = []
    for name, text in edits(fixture):
        data = text.encode("utf-8", errors="surrogateescape")
        cases.append({"name": name, "runtime_hex": data.hex(), "package_id": NAMESPACE, **outcome(NAMESPACE, data)})
    rng = random.Random(4811)
    for index in range(400):
        text = random_edit(rng, fixture)
        data = text.encode("utf-8")
        cases.append({"name": f"random {index}", "runtime_hex": data.hex(), "package_id": NAMESPACE, **outcome(NAMESPACE, data)})
    record = json.loads((ROOT / SRD / "package-record.json").read_text(encoding="utf-8"))
    srd_bytes = (ROOT / SRD / "runtime/ruleset.yaml").read_bytes()
    shipped = [{"name": "srd lite", "source": f"{SRD}/runtime/ruleset.yaml", "package_id": record["id"],
                **outcome(record["id"], srd_bytes)}]
    archive = zipfile.ZipFile(ROOT / RIICHI)
    riichi = json.loads(archive.read("manifest.json"))["packages"][0]
    riichi_bytes = archive.read(f"packages/{riichi['id']}/runtime/ruleset.yaml")
    shipped.append({"name": "riichi", "runtime_hex": riichi_bytes.hex(), "package_id": riichi["id"],
                    **outcome(riichi["id"], riichi_bytes)})
    shipped.append({"name": "riichi as another package", "runtime_hex": riichi_bytes.hex(), "package_id": "pneuma.other",
                    **outcome("pneuma.other", riichi_bytes)})
    OUTPUT.write_bytes((json.dumps({"python": sys.version.split()[0], "cases": cases, "shipped": shipped},
                                   ensure_ascii=True, allow_nan=False) + "\n").encode("ascii"))
    found = sum(1 for case in cases if case.get("result"))
    print(f"{len(cases)} cases ({found} with a projection), {len(shipped)} shipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
