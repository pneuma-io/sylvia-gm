"""Staging and materializing a Builder import repair through Python's own code.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_import_repair_vectors.py

Steps run in order on two workspaces (`package` and `plan`) and a folder of selected sources:
`stage_repair_copy` (first copy, a bound replay that keeps edits, an unbound replay, and each
refusal: hash, bound source, changed source, kind, a missing file, an unsafe nested snapshot),
edits to the staged draft (`write` and `delete`), and `materialize_repair_draft` for package and
Plan drafts, valid and broken in each way its checks catch. Each step records its outcome
(staging: kind, digest and the draft's folder; materializing: the archive's and closure's
digests and the Plan's facts; or the exception's class, message and diagnostics, an `OSError`
by its class only) and every file in that workspace afterwards (path and SHA-256).

The package source (`broken-package.archive`: any name but a Plan's is a package, and `.zip` is ignored by `.gitignore`) is built as `tests/test_import_repair.py` builds it, from the Signal and
Static test Ruleset with two undeclared manifest fields; the Plan sources are the
`plan_bundle/selected` files. Sources are kept under `import_repair/sources`. Two runs give
identical bytes, and the output serves both platforms. Reference tooling only.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import shutil
import sys
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[3]
sys.path.insert(0, str(REPOSITORY / "src"))

from sylvia_gm.application.import_repair import materialize_repair_draft, stage_repair_copy  # noqa: E402
from sylvia_gm.application.package_materialization import (  # noqa: E402
    NormalizedPackageDraft,
    materialize_package_archive,
)
from sylvia_gm.domain.package_library import canonical_json_bytes  # noqa: E402

OUT = HERE / "import_repair"
PLANS = HERE / "plan_bundle" / "selected"
FIXTURE = REPOSITORY / "tests" / "fixtures" / "rulesets" / "signal-and-static"
# `tests/test_effective_interview_content.py`'s contribution, owned by a Campaign Plan.
CONTRIBUTION = {
    "format_id": "sylvia-gm-interview-contribution", "format_version": 1, "owner_kind": "campaign_plan",
    "id": "relay-questions", "title": "relay-questions questions",
    "questions": [{"id": "selection", "prompt": "Which signal?", "type": "choice", "required": True,
                   "adaptive": False, "options": [{"id": "first", "label": "First"}, {"id": "second", "label": "Second"}],
                   "condition": None}],
}
OS_ERRORS = {"OSError", "FileNotFoundError", "FileExistsError", "PermissionError", "IsADirectoryError",
             "NotADirectoryError"}


def valid_package() -> bytes:
    record = json.loads((FIXTURE / "package-record.json").read_text(encoding="utf-8"))
    payloads = {path.relative_to(FIXTURE).as_posix(): path.read_bytes()
                for path in sorted(FIXTURE.rglob("*")) if path.is_file() and path.name != "package-record.json"}
    with materialize_package_archive((NormalizedPackageDraft(record, payloads),)) as built:
        return built.archive.read()


def rewrite(source: bytes, changes: dict[str, bytes]) -> bytes:
    result = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(source)) as original, zipfile.ZipFile(result, "w") as output:
        files = {item.filename: original.read(item) for item in original.infolist()}
        files.update(changes)
        for name, value in files.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            output.writestr(info, value)
    return result.getvalue()


def sources() -> dict[str, bytes]:
    package = valid_package()
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    manifest["unexpected"] = "leave for Builder"
    manifest["packages"][0]["unexpected"] = {"kept": True}
    broken = rewrite(package, {"manifest.json": json.dumps(manifest, separators=(",", ":")).encode()})
    found = {"broken-package.archive": broken}
    for name in ("extra-member.sylvia-gm-plan", "junk-snapshot.sylvia-gm-plan", "relay.sylvia-gm-plan"):
        found[name] = (PLANS / name).read_bytes()
    return found


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def steps(found: dict[str, bytes]) -> list[dict]:
    digest = {name: hashlib.sha256(data).hexdigest() for name, data in found.items()}
    package, plan = digest["broken-package.archive"], digest["extra-member.sylvia-gm-plan"]
    with zipfile.ZipFile(io.BytesIO(found["broken-package.archive"])) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    repaired = {key: value for key, value in manifest.items() if key != "unexpected"}
    repaired["packages"] = [{key: value for key, value in repaired["packages"][0].items() if key != "unexpected"}]
    repaired_bytes = json.dumps(repaired).encode()

    def stage(workspace, source, kind, expected=None, bound=None):
        return {"op": "stage", "workspace": workspace, "source": source, "kind": kind,
                "expected": expected or digest.get(source, "0" * 64), "bound": bound}

    def materialize(workspace, kind):
        return {"op": "materialize", "workspace": workspace, "kind": kind}

    def write(workspace, path, data: bytes):
        return {"op": "write", "workspace": workspace, "path": path, "data": b64(data)}

    def delete(workspace, path):
        return {"op": "delete", "workspace": workspace, "path": path}

    manifest_path = "repair-draft/manifest.json"
    return [
        materialize("package", "package"),
        stage("package", "broken-package.archive", "package", expected="x"),
        stage("package", "broken-package.archive", "package", bound="1" * 64),
        stage("package", "broken-package.archive", "package", expected="0" * 64),
        stage("package", "broken-package.archive", "plan"),
        stage("package", "missing-package.archive", "package"),
        stage("package", "broken-package.archive", "package"),
        materialize("package", "package"),
        write("package", manifest_path, repaired_bytes),
        stage("package", "broken-package.archive", "package", bound=package),
        stage("package", "broken-package.archive", "package"),
        materialize("package", "package"),
        materialize("package", "plan"),
        write("package", manifest_path, json.dumps({**repaired, "format": True}).encode()),
        materialize("package", "package"),
        write("package", manifest_path, json.dumps({**repaired, "format": 3.0}).encode()),
        materialize("package", "package"),
        write("package", manifest_path, json.dumps({**repaired, "format": "3"}).encode()),
        materialize("package", "package"),
        write("package", manifest_path, json.dumps({**repaired, "packages": []}).encode()),
        materialize("package", "package"),
        write("package", manifest_path, json.dumps({**repaired, "packages": [1]}).encode()),
        materialize("package", "package"),
        write("package", manifest_path, json.dumps({**repaired, "packages": [{"id": ""}]}).encode()),
        materialize("package", "package"),
        write("package", manifest_path, json.dumps({"format": 3, "extra": 1}).encode()),
        materialize("package", "package"),
        write("package", manifest_path, b'{"format": 3, "format": 3, "packages": []}'),
        materialize("package", "package"),
        write("package", manifest_path, b"[]"),
        materialize("package", "package"),
        write("package", manifest_path, repaired_bytes),
        write("package", "repair-draft/notes.txt", b"outside"),
        materialize("package", "package"),
        delete("package", "repair-draft/notes.txt"),
        delete("package", manifest_path),
        materialize("package", "package"),
        write("package", manifest_path, repaired_bytes),
        materialize("package", "package"),
        stage("plan", "junk-snapshot.sylvia-gm-plan", "plan"),
        stage("plan", "extra-member.sylvia-gm-plan", "plan"),
        materialize("plan", "plan"),
        delete("plan", "repair-draft/notes.txt"),
        materialize("plan", "plan"),
        materialize("plan", "package"),
        # A valid Plan contribution is part of the Plan's identity.
        write("plan", "repair-draft/interview-contribution.json", json.dumps(CONTRIBUTION).encode()),
        materialize("plan", "plan"),
        delete("plan", "repair-draft/interview-contribution.json"),
        # A stale digest on a dependency inside the draft is dropped and recomputed.
        {"op": "edit_json", "workspace": "plan", "path": "repair-draft/snapshot/manifest.json",
         "set": [["packages", 1, "dependencies", 0, "content_sha256"], "0" * 64]},
        materialize("plan", "plan"),
        {"op": "restore", "workspace": "plan", "path": "repair-draft/snapshot/manifest.json"},
        write("plan", "repair-draft/plan.md", b"\xff\xfe broken"),
        materialize("plan", "plan"),
        {"op": "restore", "workspace": "plan", "path": "repair-draft/plan.md"},
        {"op": "edit_json", "workspace": "plan", "path": "repair-draft/record.json", "set": [["format_version"], 2]},
        materialize("plan", "plan"),
        {"op": "restore", "workspace": "plan", "path": "repair-draft/record.json"},
        {"op": "edit_json", "workspace": "plan", "path": "repair-draft/record.json", "set": [["package_snapshot"], "x.zip"]},
        materialize("plan", "plan"),
        {"op": "restore", "workspace": "plan", "path": "repair-draft/record.json"},
        {"op": "edit_json", "workspace": "plan", "path": "repair-draft/plan.json",
         "set": [["bindings", "campaign_setting", "id"], "local.other"]},
        materialize("plan", "plan"),
        {"op": "restore", "workspace": "plan", "path": "repair-draft/plan.json"},
        {"op": "edit_json", "workspace": "plan", "path": "repair-draft/plan.json",
         "set": [["bindings", "ruleset", "version"], "9.9.9"]},
        materialize("plan", "plan"),
        {"op": "restore", "workspace": "plan", "path": "repair-draft/plan.json"},
        {"op": "edit_json", "workspace": "plan", "path": "repair-draft/plan.json", "set": [["bindings"], []]},
        materialize("plan", "plan"),
        {"op": "restore", "workspace": "plan", "path": "repair-draft/plan.json"},
        write("plan", "repair-draft/interview-contribution.json", b'{"x": 1}'),
        materialize("plan", "plan"),
        delete("plan", "repair-draft/interview-contribution.json"),
        delete("plan", "repair-draft/plan.md"),
        materialize("plan", "plan"),
        {"op": "restore", "workspace": "plan", "path": "repair-draft/plan.md"},
        {"op": "delete_tree", "workspace": "plan", "path": "repair-draft/snapshot"},
        materialize("plan", "plan"),
        stage("plan", "extra-member.sylvia-gm-plan", "plan", bound=plan),
        materialize("plan", "plan"),
    ]


def failure(error: Exception) -> dict:
    name = type(error).__name__
    if name in OS_ERRORS:
        return {"error": name}
    return {"error": name, "message": str(error),
            "diagnostics": [item.as_payload() for item in getattr(error, "diagnostics", ())]}


def plain(value):
    """Frozen mappings and tuples as the dicts and lists JSON writes, keys in order."""
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [plain(item) for item in value]
    return value


def plan_facts(plan) -> dict | None:
    if plan is None:
        return None
    return {"artifact_id": plan.artifact_id, "content_sha256": plan.content_sha256, "title": plan.title,
            "payload_sha256": hashlib.sha256(canonical_json_bytes(plan.payload)).hexdigest(),
            "markdown_sha256": hashlib.sha256(plan.readable_markdown.encode()).hexdigest()}


def tree(workspace: Path) -> list[list[str]]:
    if not workspace.exists():
        return []
    return [[path.relative_to(workspace).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()]
            for path in sorted(workspace.rglob("*")) if path.is_file()]


def run(step: dict, root: Path, kept: dict) -> dict:
    workspace = root / step["workspace"]
    op = step["op"]
    try:
        if op == "stage":
            staged = stage_repair_copy(root / "sources" / step["source"], step["expected"], workspace,
                                       expected_kind=step["kind"], bound_source_sha256=step["bound"])
            return {"ok": {"kind": staged.kind, "source_sha256": staged.source_sha256,
                           "draft_root": staged.draft_root.relative_to(workspace).as_posix()}}
        if op == "materialize":
            with materialize_repair_draft(workspace, expected_kind=step["kind"]) as materialized:
                materialized.archive.seek(0)
                return {"ok": {"kind": materialized.kind,
                               "archive_sha256": hashlib.sha256(materialized.archive.read()).hexdigest(),
                               "closure_sha256": materialized.closure_sha256,
                               "plan": plan_facts(materialized.plan),
                               "interview_contribution": plain(materialized.interview_contribution),
                               "setup_id": materialized.setup_id}}
        path = workspace / step["path"]
        if op == "write":
            path.write_bytes(base64.b64decode(step["data"]))
        elif op == "delete":
            path.unlink()
        elif op == "delete_tree":
            shutil.rmtree(path)
        elif op == "restore":
            path.write_bytes(kept[(step["workspace"], step["path"])])
        elif op == "edit_json":
            value = json.loads(path.read_bytes())
            keys, item = step["set"]
            target = value
            for key in keys[:-1]:
                target = target[key]
            target[keys[-1]] = item
            path.write_bytes(json.dumps(value).encode())
        return {"ok": None}
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return failure(error)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    found = sources()
    kept_sources = OUT / "sources"
    shutil.rmtree(kept_sources, ignore_errors=True)
    kept_sources.mkdir()
    for name, data in found.items():
        (kept_sources / name).write_bytes(data)
    recorded = []
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        shutil.copytree(kept_sources, root / "sources")
        kept: dict = {}
        for step in steps(found):
            # The staged bytes a later `restore` puts back.
            if step["op"] in ("restore", "edit_json") and (step["workspace"], step["path"]) not in kept:
                raise AssertionError("restore before the file is kept")
            outcome = run(step, root, kept)
            if step["op"] == "stage" and "ok" in outcome:
                draft = root / step["workspace"] / "repair-draft"
                for path in draft.rglob("*"):
                    if path.is_file():
                        relative = "repair-draft/" + path.relative_to(draft).as_posix()
                        kept.setdefault((step["workspace"], relative), path.read_bytes())
            recorded.append({**step, "outcome": outcome, "tree": tree(root / step["workspace"])})
    record = {"python": ".".join(map(str, sys.version_info[:2])), "steps": recorded}
    (OUT / "repair_vectors.json").write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
