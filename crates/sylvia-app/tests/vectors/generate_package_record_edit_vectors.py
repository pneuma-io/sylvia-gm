"""Generate reference vectors for sylvia_app::package_record_edits (builder/_package_record_edits.py).

Run with the snapshot's own interpreter, from the repository root, on Windows or Linux:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_package_record_edit_vectors.py
    .venv/bin/python -I crates/sylvia-app/tests/vectors/generate_package_record_edit_vectors.py

The output is the same on both (links are junctions on Windows and symbolic links elsewhere;
colliding identities differ only in case, which `casefold` folds before any file exists), so
one file serves both.

Each script names a set of drafts (from `drafts`, with edits) and file steps in a fresh
workspace whose draft root is `draft/`; then `apply_package_record_edits(workspace, "draft",
drafts)` runs once. Success records each retained draft's record, its key order and its
payload paths; failure records the class, text, diagnostics and code.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.application.package_materialization import NormalizedPackageDraft  # noqa: E402
from sylvia_gm.builder._package_record_edits import apply_package_record_edits  # noqa: E402

OUTPUT = Path(__file__).with_name("package_record_edit_vectors.json")
NEWLINE = chr(10)
SHA = "a" * 64


def record(package_id, version, role, kind, dependencies, **extra):
    return {"id": package_id, "version": version, "role": role, "content_kind": kind,
            "metadata": {"label": package_id.title()}, "dependencies": dependencies,
            "files": [{"path": "runtime/x.yaml", "sha256": SHA}], "content_sha256": SHA, **extra}


def dependency(package_id, version, namespace):
    return {"id": package_id, "version": version, "namespace": namespace, "content_sha256": SHA}


DRAFTS = {
    "ruleset": {"record": record("ruleset", "1.0.0", "primary", "ruleset", [dependency("lib", "1.0.0", "lib")]),
                "payloads": {"runtime/x.yaml": "a: 1"}, "format_version": 4},
    "lib": {"record": record("lib", "1.0.0", "dependency", "library", []), "payloads": {"lib.json": "{}"},
            "format_version": 4},
    "world": {"record": record("world", "2.0.0", "dependency", "campaign_setting",
                               [dependency("ruleset", "1.0.0", "rules"), dependency("lib", "1.0.0", "lib")]),
              "payloads": {"world.md": "# World"}, "format_version": 4},
    "stray": {"record": record("stray", "0.1.0", "dependency", "library", []), "payloads": {}, "format_version": 4},
}
BASE = ["ruleset", "lib", "world", "stray"]


def drafts_from(spec) -> tuple:
    names = spec.get("names", BASE)
    drafts = []
    for name in names:
        draft = copy.deepcopy(DRAFTS[name])
        for path, value in spec.get("edits", {}).get(name, []):
            target = draft
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = value
        drafts.append(NormalizedPackageDraft(draft["record"], {key: text.encode("utf-8") for key, text in
                                                               draft["payloads"].items()}, draft["format_version"]))
    return tuple(drafts)


def edited(name, changes=(), drop=()):
    value = {key: item for key, item in DRAFTS[name]["record"].items() if key not in drop}
    for key, item in changes:
        value[key] = item
    return value


def file_content(spec) -> bytes:
    if "json" in spec:
        return json.dumps(spec["json"]).encode("utf-8")
    if "latin1" in spec:
        return spec["latin1"].encode("latin-1")
    if "repeat" in spec:
        text, count = spec["repeat"]
        return (text * count).encode("utf-8")
    return spec["text"].encode("utf-8")


def make_link(path: Path, target: Path) -> None:
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(path))
    else:
        os.symlink(target, path, target_is_directory=True)


def run(base: Path, script: dict) -> list:
    workspace = base / "workspace"
    (workspace / "draft").mkdir(parents=True)
    for step in script.get("files", []):
        path = workspace / step["path"]
        if step["op"] == "write":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(file_content(step["content"]))
        elif step["op"] == "mkdir":
            path.mkdir(parents=True, exist_ok=True)
        elif step["op"] == "link":
            path.parent.mkdir(parents=True, exist_ok=True)
            make_link(path, workspace / step["target"])
        elif step["op"] == "remove":
            shutil.rmtree(path)
    draft_root = Path("draft") if not script.get("outside") else base / "outside"
    if script.get("outside"):
        draft_root.mkdir()
    try:
        drafts = apply_package_record_edits(workspace, draft_root, drafts_from(script.get("drafts", {})))
    except Exception as error:  # every class is behaviour
        diagnostics = getattr(error, "diagnostics", None) or ()
        return [type(error).__name__, str(error), [item.as_payload() for item in diagnostics],
                getattr(error, "code", None)]
    return ["ok", [{"record": draft.record, "order": list(draft.record),
                    "dependency_order": [list(item) for item in draft.record.get("dependencies", [])],
                    "payloads": list(draft.payloads), "format_version": draft.format_version} for draft in drafts]]


def records_dir(*entries):
    return [{"op": "write", "path": f"draft/package-records/{name}", "content": content} for name, content in entries]


def scripts() -> dict:
    ruleset_json = "draft/package-records/ruleset.json"
    return {
        "no_records": {},
        "empty_records": {"files": [{"op": "mkdir", "path": "draft/package-records"}]},
        "edit_label": {"files": records_dir(("ruleset.json", {"json": edited("ruleset", [("metadata", {"label": "New"})])}))},
        "edit_without_derived": {"files": records_dir(("ruleset.json", {"json": edited(
            "ruleset", [("metadata", {"label": "New"})], drop=("files", "content_sha256"))}))},
        "edit_only_files": {"files": records_dir(("ruleset.json", {"json": edited("ruleset", drop=("content_sha256",))}))},
        "edit_missing_key": {"files": records_dir(("ruleset.json", {"json": edited("ruleset", drop=("metadata",))}))},
        "edit_extra_key": {"files": records_dir(("ruleset.json", {"json": edited("ruleset", [("extra", 1)])}))},
        "change_id": {"files": records_dir(("ruleset.json", {"json": edited("ruleset", [("id", "other")])}))},
        "change_role": {"files": records_dir(("lib.json", {"json": edited("lib", [("role", "primary")])}))},
        "change_kind": {"files": records_dir(("lib.json", {"json": edited("lib", [("content_kind", "ruleset")])}))},
        "role_as_one": {"drafts": {"edits": {"lib": [[["record", "role"], 1]]}},
                        "files": records_dir(("lib.json", {"json": edited("lib", [("role", True)])}))},
        "bump_library": {"files": records_dir(("lib.json", {"json": edited("lib", [("version", "2.0.0")])}))},
        "bump_and_rebind": {"files": records_dir(
            ("lib.json", {"json": edited("lib", [("version", "2.0.0")])}),
            ("ruleset.json", {"json": edited("ruleset", [("dependencies", [dependency("lib", "2.0.0", "lib")])])}))},
        "wrong_dependency_version": {"files": records_dir(("ruleset.json", {"json": edited(
            "ruleset", [("dependencies", [dependency("lib", "9.9.9", "lib")])])}))},
        "renamed_namespace": {"files": records_dir(
            ("lib.json", {"json": edited("lib", [("version", "2.0.0")])}),
            ("ruleset.json", {"json": edited("ruleset", [("dependencies", [dependency("lib", "1.0.0", "other")])])}))},
        "absent_dependency": {"files": records_dir(("ruleset.json", {"json": edited(
            "ruleset", [("dependencies", [dependency("ghost", "1.0.0", "ghost")])])}))},
        "target_version_number": {"files": records_dir(("lib.json", {"json": edited("lib", [("version", 5)])}))},
        "dependencies_not_list": {"files": records_dir(("ruleset.json", {"json": edited("ruleset", [("dependencies", {})])}))},
        "dependency_not_object": {"files": records_dir(("ruleset.json", {"json": edited("ruleset", [("dependencies", ["lib"])])}))},
        "namespace_list": {"files": records_dir(("ruleset.json", {"json": edited(
            "ruleset", [("dependencies", [{**dependency("lib", "1.0.0", "lib"), "namespace": ["lib"]}])])}))},
        "dependency_extra_field": {"files": records_dir(("ruleset.json", {"json": edited(
            "ruleset", [("dependencies", [{"note": "kept", **dependency("lib", "1.0.0", "lib")}])])}))},
        "invalid_json": {"files": records_dir(("ruleset.json", {"text": "{" + NEWLINE + "  \"id\": }"}))},
        "duplicate_key": {"files": records_dir(("ruleset.json", {"text": '{"id": "ruleset", "id": "ruleset"}'}))},
        "nan": {"files": records_dir(("ruleset.json", {"text": '{"id": NaN}'}))},
        "overflow": {"files": records_dir(("ruleset.json", {"text": '{"id": 1e999}'}))},
        "deep": {"files": records_dir(("ruleset.json", {"text": '{"a": ' + "[" * 24 + "]" * 24 + "}"}))},
        "not_object": {"files": records_dir(("ruleset.json", {"text": "[]"}))},
        "latin1": {"files": records_dir(("ruleset.json", {"latin1": "{" + chr(0xff) + "}"}))},
        "too_large": {"files": records_dir(("ruleset.json", {"repeat": [" ", 2 * 1024 * 1024 + 1]}))},
        "record_is_directory": {"files": [{"op": "mkdir", "path": ruleset_json}]},
        "unknown_entry": {"files": records_dir(("notes.txt", {"text": "x"}))},
        "case_entry": {"files": records_dir(("Ruleset.json", {"json": edited("ruleset")}))},
        "records_is_file": {"files": [{"op": "write", "path": "draft/package-records", "content": {"text": "x"}}]},
        "linked_records": {"files": [{"op": "mkdir", "path": "elsewhere"},
                                     {"op": "link", "path": "draft/package-records", "target": "elsewhere"}]},
        "linked_draft": {"files": [{"op": "remove", "path": "draft"}, {"op": "mkdir", "path": "elsewhere"},
                                   {"op": "link", "path": "draft", "target": "elsewhere"}]},
        "outside": {"outside": True},
        "two_primaries": {"drafts": {"edits": {"lib": [[["record", "role"], "primary"]]}}},
        "two_settings": {"drafts": {"edits": {"stray": [[["record", "content_kind"], "campaign_setting"]]}}},
        "no_primary": {"drafts": {"names": ["lib", "world"]}},
        "format_three": {"drafts": {"edits": {"lib": [[["format_version"], 3]]}}},
        "no_drafts": {"drafts": {"names": []}},
        "duplicate_ids": {"drafts": {"names": ["ruleset", "lib", "lib"]}},
        "id_not_text": {"drafts": {"edits": {"stray": [[["record", "id"], 7]]}}},
        "colliding_ids": {"drafts": {"edits": {"stray": [[["record", "id"], "LIB"]]}},
                          "files": [{"op": "mkdir", "path": "draft/package-records"}]},
        "colliding_ids_no_records": {"drafts": {"edits": {"stray": [[["record", "id"], "LIB"]]}}},
        "setting_only_reach": {"drafts": {"edits": {"ruleset": [[["record", "dependencies"], []]]}}},
        "unreachable_dependency": {"drafts": {"edits": {"world": [[["record", "dependencies"], [dependency("stray", "0.1.0", "x")]]]}}},
    }


def main() -> None:
    results = {}
    for name, script in scripts().items():
        base = Path(tempfile.mkdtemp(prefix="sylvia-record-edits-")).resolve()
        try:
            results[name] = {**script, "outcome": run(base, script)}
        finally:
            shutil.rmtree(base, ignore_errors=True)
    vectors = {"python": ".".join(sys.version.split()[0].split(".")[:2]), "drafts": DRAFTS, "base": BASE,
               "scripts": results}
    text = json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + NEWLINE
    for marker in (tempfile.gettempdir(), os.path.expanduser("~")):
        assert marker not in text and marker.replace(chr(92), "/") not in text, "a machine path leaked"
    OUTPUT.write_text(text, encoding="utf-8", newline=NEWLINE)
    for name, script in results.items():
        outcome = script["outcome"]
        print(f"{name:26}", "ok " + ",".join(item["record"]["id"] for item in outcome[1]) if outcome[0] == "ok"
              else f"{outcome[0]}: {outcome[1][:70]}")


if __name__ == "__main__":
    main()
