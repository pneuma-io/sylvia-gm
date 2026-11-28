"""Package snapshots, Setting drafts and Builder source selection through Python's own code.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_setting_snapshot_vectors.py

Two parts, written to `setting_snapshots/vectors.json`:

- `snapshots`: on an owner holding the Signal and Static test packages (installed as
  `tests/test_builder_library.py` installs them) and the shipped Riichi package:
  `retained_package_drafts` with and without the Setting, `ruleset_copy`, and
  `materialize_setting_draft` / `check_setting_draft` on Setting draft workspaces that reach
  every branch: a plain world, replaced Ruleset files, `ruleset-source.json` and
  `setting-source.json` selections (valid, changed, unreadable, duplicate keys, constants,
  missing or extra keys, an unknown or a different-Ruleset candidate, oversized), and
  `select_snapshot_sources` over a copy that keeps its Setting (`rebind_ruleset_snapshot`).
  Each installed candidate's exported archive is kept under `setting_snapshots/candidates`, so
  the Rust test installs the same candidates under the same install identities.
- `selection`: `BuilderSourceSelection` on a fresh owner, over Python's recorded creation
  catalog: selecting the bundled Ruleset, again, a different one, an unadvertised Setting and a
  compatible one, the candidate and reader, and a chat whose repair context keeps its selection.

Each step records its outcome (drafts as records and payload digests; materialized archives as
closure, archive digest and lock; or the exception's class, message, code and diagnostics).
Two runs give identical bytes; one file serves both platforms. Reference tooling only.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[3]
sys.path.insert(0, str(REPOSITORY / "src"))
sys.path.insert(0, str(REPOSITORY))

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402
from sylvia_gm.application.builder_source_selection import BuilderSourceSelection  # noqa: E402
from sylvia_gm.builder._package_snapshots import (  # noqa: E402
    retained_package_drafts,
    ruleset_copy,
    select_snapshot_sources,
)
from sylvia_gm.builder._setting_draft_check import check_setting_draft  # noqa: E402
from sylvia_gm.builder._setting_materializer import materialize_setting_draft  # noqa: E402
from sylvia_gm.domain.session import SessionMessageKind  # noqa: E402
from sylvia_gm.interfaces.owner_process import _campaign_creation_catalog  # noqa: E402
from tests.test_builder_library import install_character_packages  # noqa: E402

OUT = HERE / "setting_snapshots"
RIICHI = REPOSITORY / "rulesets" / "riichi-mahjong" / "riichi-mahjong-0.1.0.sylvia-gm-package"
OS_ERRORS = {"OSError", "FileNotFoundError", "FileExistsError", "PermissionError", "IsADirectoryError",
             "NotADirectoryError"}


def plain(value):
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [plain(item) for item in value]
    return value


def failure(error: Exception) -> dict:
    name = type(error).__name__
    if name in OS_ERRORS:
        return {"error": name}
    return {"error": name, "message": str(error), "code": getattr(error, "code", None),
            "diagnostics": [item.as_payload() for item in getattr(error, "diagnostics", ())]}


def outcome(call):
    try:
        return {"ok": call()}
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return failure(error)


def drafts_payload(drafts) -> list:
    return [{"record": plain(draft.record), "format_version": draft.format_version,
             "payloads": [[path, hashlib.sha256(value).hexdigest()] for path, value in draft.payloads.items()]}
            for draft in drafts]


def materialized_payload(materialized) -> dict:
    with materialized:
        materialized.archive.seek(0)
        return {"closure_sha256": materialized.closure_sha256,
                "archive_sha256": hashlib.sha256(materialized.archive.read()).hexdigest(),
                "lock": [[item.package_id, item.version, item.sha256] for item in materialized.package_lock.entries]}


def setting_cases(original: str, riichi: str, closure: str, riichi_closure: str, runtime: str) -> list[dict]:
    world = {"setting-draft/files/world.md": "# Relay Island\nA silent radio tower."}

    def source(name, value):
        return {f"setting-draft/{name}": value if isinstance(value, str) else json.dumps(value)}

    good = {"candidate_id": original, "expected_closure_sha256": closure}
    return [
        {"name": "plain world", "files": world},
        {"name": "check plain world", "check": True, "files": world},
        {"name": "no files", "files": {"setting-draft/notes.md": "x"}},
        {"name": "replaced ruleset runtime", "files": {**world, "setting-draft/ruleset-files/runtime/ruleset.yaml": runtime}},
        {"name": "replaced ruleset declarations", "files": {**world, "setting-draft/ruleset-files/content/content-declarations.json": "{}"}},
        {"name": "added ruleset file", "files": {**world, "setting-draft/ruleset-files/notes/extra.md": "Extra.\n"}},
        {"name": "ruleset source", "files": {**world, **source("ruleset-source.json", good)}},
        {"name": "ruleset source changed", "files": {**world, **source("ruleset-source.json", {**good, "expected_closure_sha256": "0" * 64})}},
        {"name": "ruleset source not JSON", "files": {**world, **source("ruleset-source.json", "{oops")}},
        {"name": "ruleset source duplicate", "files": {**world, **source("ruleset-source.json", '{"candidate_id": "a", "candidate_id": "b"}')}},
        {"name": "ruleset source constant", "files": {**world, **source("ruleset-source.json", '{"candidate_id": NaN}')}},
        {"name": "ruleset source missing key", "files": {**world, **source("ruleset-source.json", {"candidate_id": original})}},
        {"name": "ruleset source extra key", "files": {**world, **source("ruleset-source.json", {**good, "extra": 1})}},
        {"name": "ruleset source empty id", "files": {**world, **source("ruleset-source.json", {**good, "candidate_id": ""})}},
        {"name": "ruleset source unknown candidate", "files": {**world, **source("ruleset-source.json", {**good, "candidate_id": "candidate-unknown"})}},
        {"name": "ruleset source other ruleset", "files": {**world, **source("ruleset-source.json", {"candidate_id": riichi, "expected_closure_sha256": riichi_closure})}},
        {"name": "ruleset source oversized", "files": {**world, **source("ruleset-source.json", " " * 65537)}},
        {"name": "ruleset source at the size limit",
         "files": {**world, **source("ruleset-source.json", json.dumps(good).ljust(65536))}},
        {"name": "ruleset source list", "files": {**world, **source("ruleset-source.json", "[]")}},
        {"name": "setting source without a setting", "files": {**world, **source("setting-source.json", good)}},
    ]


def snapshots(root: Path) -> tuple[list, dict, dict]:
    recorded = []
    archives = {}
    with AuthorityOwner.open(root / "snapshots.sqlite3", "setting-snapshot-vectors") as owner:
        original, _ = install_character_packages(owner)
        riichi = owner.install_package_archive(io.BytesIO(RIICHI.read_bytes()), install_id="install-riichi")
        for candidate in owner.package_candidates():
            exported = io.BytesIO()
            owner.export_package_candidate(candidate.candidate_id, exported)
            archives[candidate.install_id] = exported.getvalue()
        ids = {"original": original.candidate_id, "riichi": riichi.candidate_id, "closure": original.closure_sha256}
        for name, call in [
            ("retained without setting", lambda: drafts_payload(retained_package_drafts(owner, original.candidate_id, include_setting=False))),
            ("retained with setting", lambda: drafts_payload(retained_package_drafts(owner, original.candidate_id, include_setting=True))),
            ("retained riichi", lambda: drafts_payload(retained_package_drafts(owner, riichi.candidate_id, include_setting=True))),
            ("ruleset copy", lambda: drafts_payload(ruleset_copy(owner, original.candidate_id))),
            ("retained unknown", lambda: drafts_payload(ruleset_copy(owner, "candidate-unknown"))),
        ]:
            recorded.append({"name": name, "op": "drafts", "outcome": outcome(call)})
        # The author's edit to the contained rules: the same runtime with a comment added.
        runtime = ruleset_copy(owner, original.candidate_id)[0].payloads["runtime/ruleset.yaml"].decode("utf-8")
        runtime += "# Edited by the Setting's author.\n"
        for case in setting_cases(original.candidate_id, riichi.candidate_id, original.closure_sha256,
                                  riichi.closure_sha256, runtime):
            workspace = root / "workspaces" / str(len(recorded))
            for relative, text in case["files"].items():
                path = workspace / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8", newline="\n")
            arguments = dict(candidate_id=original.candidate_id, package_id="local.relay-island", version="1.0.0",
                             title="Relay Island")
            if case.get("check"):
                result = outcome(lambda: plain(check_setting_draft(owner, workspace, **arguments)))
            else:
                result = outcome(lambda: materialized_payload(materialize_setting_draft(owner, workspace, **arguments)))
            recorded.append({"name": case["name"], "op": "check" if case.get("check") else "setting",
                             "files": case["files"], "outcome": result})
        # `select_snapshot_sources` over a copy that keeps its Setting.
        with_setting = retained_package_drafts(owner, original.candidate_id, include_setting=True)
        good = json.dumps({"candidate_id": original.candidate_id, "expected_closure_sha256": original.closure_sha256})
        for name, files in [
            ("rebind with setting", {"draft/ruleset-source.json": good}),
            ("reselect setting", {"draft/setting-source.json": good}),
            ("reselect both", {"draft/setting-source.json": good, "draft/ruleset-source.json": good}),
            ("no selection", {"draft/notes.md": "x"}),
        ]:
            workspace = root / "workspaces" / str(len(recorded))
            for relative, text in files.items():
                path = workspace / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8", newline="\n")
            result = outcome(lambda: drafts_payload(select_snapshot_sources(owner, workspace, workspace / "draft", with_setting)))
            recorded.append({"name": name, "op": "select", "files": files, "outcome": result})
    return recorded, archives, ids


def selection(root: Path) -> tuple[list, list]:
    recorded = []
    storage = root / "selection-storage"
    storage.mkdir()
    with AuthorityOwner.open(storage / "authority.sqlite3", "source-selection-vectors") as owner:
        catalog = plain(_campaign_creation_catalog(storage, owner))
        # The shipped catalog names no selection alias; one is added to the recorded catalog both
        # sides read, so selecting by alias is reached.
        next(item for item in catalog if item.get("kind") == "ruleset")["selection_aliases"] = ["ruleset-alias"]
        ruleset = next(item for item in catalog if item.get("kind") == "ruleset" and item.get("compatible_settings"))
        other = next(item for item in catalog if item.get("kind") == "ruleset" and item is not ruleset)
        setting = ruleset["compatible_settings"][0]
        owner.create_builder_chat(provider_id="standin", model_id="standin/model", session_id="session-builder-plain",
                                  display_name="Plain")
        owner.create_builder_chat(provider_id="standin", model_id="standin/model", session_id="session-builder-repair",
                                  display_name="Repair")
        owner.append_agent_message(session_id="session-builder-repair", message_id="repair-source-x",
                                   kind=SessionMessageKind.EVIDENCE, content={"kind": "import_repair_context"})
        plain_chat = BuilderSourceSelection(owner, storage, owner.session_context("session-builder-plain"), lambda: catalog)
        repair_chat = BuilderSourceSelection(owner, storage, owner.session_context("session-builder-repair"), lambda: catalog)
        ruleset_id = ruleset.get("selection_id", ruleset["id"])
        steps = [
            ("select unadvertised", lambda: plain(plain_chat.select("no-such-ruleset"))),
            ("select by alias", lambda: plain(plain_chat.select("ruleset-alias"))),
            ("select setting first unadvertised", lambda: plain(plain_chat.select_setting(None, "unadvertised.setting"))),
            ("select ruleset", lambda: plain(plain_chat.select(ruleset_id))),
            ("select ruleset again", lambda: plain(plain_chat.select(ruleset_id))),
            ("select other ruleset", lambda: plain(plain_chat.select(other.get("selection_id", other["id"])))),
            ("candidate", lambda: getattr(plain_chat.candidate(), "candidate_id", None)),
            ("select setting", lambda: plain(plain_chat.select_setting(setting.get("candidate_id"), setting["id"]))),
            ("select setting again", lambda: plain(plain_chat.select_setting(setting.get("candidate_id"), setting["id"]))),
            ("reader", lambda: (lambda reader: None if reader is None else len(reader.paths))(plain_chat.reader())),
            ("repair chat select", lambda: plain(repair_chat.select(ruleset_id))),
            ("repair chat setting", lambda: plain(repair_chat.select_setting(setting.get("candidate_id"), setting["id"]))),
        ]
        for name, call in steps:
            recorded.append({"name": name, "outcome": outcome(call),
                             "references": plain(plain_chat.references), "selectable": [plain_chat.selectable, repair_chat.selectable]})
        candidates = sorted([item.candidate_id, item.closure_sha256] for item in owner.package_candidates())
    return recorded, catalog, candidates


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        snapshot_steps, archives, ids = snapshots(root)
        selection_steps, catalog, candidates = selection(root)
    kept = OUT / "candidates"
    shutil.rmtree(kept, ignore_errors=True)
    kept.mkdir()
    for install_id, data in archives.items():
        # Not `.zip` or `.sylvia-gm-package`: `.gitignore` ignores both.
        (kept / f"{install_id}.archive").write_bytes(data)
    record = {
        "python": ".".join(map(str, sys.version_info[:2])),
        "install_order": list(archives),
        "ids": ids,
        "snapshots": snapshot_steps,
        "catalog": catalog,
        "selection": selection_steps,
        "selection_candidates": candidates,
    }
    (OUT / "vectors.json").write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
