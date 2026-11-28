"""Plan snapshot drafts and Plan draft checks through Python's own code.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_plan_snapshot_draft_vectors.py

On an owner holding the Coin Ruleset and the Glass Harbor Setting (installed as
`tests/test_setting_character_composition.py` installs them), one Plan is created with Builder's
own Plan draft tool. Each step then writes that Plan, with its edits, into a fresh workspace and
records `materialize_plan_snapshot_draft` or `check_plan_draft`:

- the unchanged Plan, which keeps the exported snapshot (an empty edit folder and an empty
  removal list too);
- refused Plan files: missing, a folder, oversized, not UTF-8, not JSON, duplicate keys and
  constants;
- refused bindings: missing, extra, not an object, a Plan that is not an object, and changed
  values;
- an unknown candidate and one whose closure differs from the authority's;
- inherited-file edits: an added character, a replaced Ruleset file, removals (and every
  removal-list refusal), package record and source selections, and a file where a folder
  belongs;
- the checks: ready, missing a Setting field, and the same with complete characters required.

Each candidate's exported archive is kept under `plan_snapshot_drafts/candidates`, so the Rust test
installs the same candidates under the same install identities. Workspace files are text, or
`{"hex": ...}` bytes, `{"repeat": ..., "count": ...}`, `{"text": ..., "pad_to": ...}` (padded
with spaces), or `null` for an empty folder.
Each step records its outcome (closure, archive digest and the validated Plan's identity, the
check's answer, or the exception's class, message, code and diagnostics). Two runs give
identical bytes; one file serves both platforms. Reference tooling only.
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

from sylvia_gm.agent._builder_session import _campaign_plan_draft_tool  # noqa: E402
from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402
from sylvia_gm.application.campaign_plan_sources import campaign_plan_sources  # noqa: E402
from sylvia_gm.builder._package_snapshots import retained_package_drafts  # noqa: E402
from sylvia_gm.builder._plan_draft_check import check_plan_draft  # noqa: E402
from sylvia_gm.builder._plan_snapshot_materializer import materialize_plan_snapshot_draft  # noqa: E402
from sylvia_gm.domain.campaign_plan import CAMPAIGN_PLAN_BYTE_LIMIT  # noqa: E402
from sylvia_gm.domain.package_library import canonical_json_bytes  # noqa: E402
from tests.test_setting_character_composition import _install_setting  # noqa: E402

OUT = HERE / "plan_snapshot_drafts"
DRAFT = "campaign-plan-draft"
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


def materialized_payload(materialized) -> dict:
    with materialized:
        materialized.archive.seek(0)
        return {"closure_sha256": materialized.closure_sha256,
                "archive_sha256": hashlib.sha256(materialized.archive.read()).hexdigest(),
                "plan": {"content_sha256": materialized.plan.content_sha256,
                         "artifact_id": materialized.plan.artifact_id,
                         "bindings": plain(materialized.plan.payload["bindings"])}}


def write_files(workspace: Path, files: dict) -> None:
    for relative, value in files.items():
        path = workspace / relative
        if value is None:
            path.mkdir(parents=True, exist_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, str):
            path.write_bytes(value.encode("utf-8"))
        elif "hex" in value:
            path.write_bytes(bytes.fromhex(value["hex"]))
        elif "pad_to" in value:
            path.write_bytes(value["text"].ljust(value["pad_to"]).encode("utf-8"))
        else:
            path.write_bytes((value["repeat"] * value["count"]).encode("utf-8"))


def text(value) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def cases(plan: dict, markdown: str, character: dict, declarations: dict, ids: dict, runtime: str) -> list[dict]:
    base = {f"{DRAFT}/plan.json": text(plan), f"{DRAFT}/plan.md": markdown}
    with_vela = {**plan, "starting_party": {**plan["starting_party"],
                                            "required_companions": [{"id": "vela", "path": "characters/vela.json"}]}}
    vela = {f"{DRAFT}/plan.json": text(with_vela), f"{DRAFT}/plan.md": markdown,
            f"{DRAFT}/setting-files/characters/vela.json": text(character),
            f"{DRAFT}/setting-files/content/content-declarations.json": text(declarations)}
    requirements = {
        "format_id": "sylvia-gm-setting-character-requirements",
        "format_version": 1,
        "schema": {"type": "object",
                   "properties": {"home": {"type": "string"}, "faction": {"type": "string"}},
                   "required": ["home", "faction"], "additionalProperties": False},
    }
    incomplete = {**vela, f"{DRAFT}/setting-files/character-requirements.json": text(requirements)}
    # The same character padded past the check's 256 KiB Setting file limit, and exactly at it.
    oversized = {**vela, f"{DRAFT}/setting-files/characters/vela.json": {"text": text(character), "pad_to": 256 * 1024 + 1}}
    at_limit = {**vela, f"{DRAFT}/setting-files/characters/vela.json": {"text": text(character), "pad_to": 256 * 1024}}

    def plan_with(**bindings):
        return {**base, f"{DRAFT}/plan.json": text({**plan, "bindings": bindings})}

    def removed(value):
        return {**base, f"{DRAFT}/removed-files.json": value if isinstance(value, str) else json.dumps(value)}

    bindings = plan["bindings"]
    # The Setting's own declarations without its requirements file.
    world_only = {**declarations, "inclusions": [{**declarations["inclusions"][0], "links": {
        **declarations["inclusions"][0]["links"], "artifacts": ["world.md"]}}]}
    without_requirements = {**removed(["setting/character-requirements.json"]),
                            f"{DRAFT}/setting-files/content/content-declarations.json": text(world_only)}
    without_closure = {key: value for key, value in bindings.items() if key != "package_closure_sha256"}
    source = json.dumps({"candidate_id": ids["rules"], "expected_closure_sha256": ids["rules_closure"]})
    setting_source = json.dumps({"candidate_id": ids["setting"], "expected_closure_sha256": ids["closure"]})
    return [
        {"name": "unchanged", "files": base},
        {"name": "check unchanged", "op": "check", "files": base},
        {"name": "empty setting files", "files": {**base, f"{DRAFT}/setting-files": None}},
        {"name": "empty removal list", "files": removed([])},
        {"name": "missing plan", "files": {f"{DRAFT}/plan.md": markdown}},
        {"name": "missing readable", "files": {f"{DRAFT}/plan.json": text(plan)}},
        {"name": "plan is a folder", "files": {f"{DRAFT}/plan.json": None, f"{DRAFT}/plan.md": markdown}},
        {"name": "plan oversized",
         "files": {**base, f"{DRAFT}/plan.json": {"repeat": " ", "count": CAMPAIGN_PLAN_BYTE_LIMIT + 1}}},
        {"name": "plan at the size limit",
         "files": {**base, f"{DRAFT}/plan.json": {"text": text(plan), "pad_to": CAMPAIGN_PLAN_BYTE_LIMIT}}},
        {"name": "plan not UTF-8", "files": {**base, f"{DRAFT}/plan.json": {"hex": "7bff7d"}}},
        {"name": "readable not UTF-8", "files": {**base, f"{DRAFT}/plan.md": {"hex": "23ff"}}},
        {"name": "plan not JSON", "files": {**base, f"{DRAFT}/plan.json": "{oops"}},
        {"name": "plan duplicate key", "files": {**base, f"{DRAFT}/plan.json": '{"a": 1, "a": 2}'}},
        {"name": "plan constant", "files": {**base, f"{DRAFT}/plan.json": '{"a": NaN}'}},
        {"name": "plan a list", "files": {**base, f"{DRAFT}/plan.json": "[]"}},
        {"name": "bindings missing", "files": {**base, f"{DRAFT}/plan.json": text({k: v for k, v in plan.items() if k != "bindings"})}},
        {"name": "bindings not an object", "files": {**base, f"{DRAFT}/plan.json": text({**plan, "bindings": [1]})}},
        {"name": "bindings key missing", "files": plan_with(**without_closure)},
        {"name": "bindings key extra", "files": plan_with(**bindings, extra=1, **{"bad key": 2})},
        {"name": "bindings closure changed", "files": plan_with(**{**bindings, "package_closure_sha256": "0" * 64})},
        {"name": "bindings ruleset changed",
         "files": plan_with(**{**bindings, "ruleset": {**bindings["ruleset"], "version": "9.9.9"}})},
        {"name": "unknown candidate", "candidate": "candidate-unknown", "files": base},
        {"name": "candidate closure differs", "candidate": ids["rules"], "files": base},
        {"name": "added character", "files": vela},
        {"name": "check added character", "op": "check", "files": vela},
        {"name": "check missing setting field", "op": "check", "files": incomplete},
        {"name": "check missing setting field required", "op": "check", "complete": True, "files": incomplete},
        {"name": "check unchanged required", "op": "check", "complete": True, "files": base},
        {"name": "check oversized character", "op": "check", "files": oversized},
        {"name": "check oversized character required", "op": "check", "complete": True, "files": oversized},
        {"name": "check character at the size limit", "op": "check", "files": at_limit},
        {"name": "added ruleset file", "files": {**base, f"{DRAFT}/ruleset-files/notes/extra.md": "Extra.\n"}},
        {"name": "setting files is a file", "files": {**base, f"{DRAFT}/setting-files": "x"}},
        {"name": "replaced ruleset runtime", "files": {**base, f"{DRAFT}/ruleset-files/runtime/ruleset.yaml": runtime}},
        {"name": "replaced world", "files": {**base, f"{DRAFT}/setting-files/world.md": "# Glass Harbor\nEdited.\n"}},
        {"name": "remove undeclared requirements", "files": removed(["setting/character-requirements.json"])},
        {"name": "remove requirements", "files": without_requirements},
        {"name": "check without requirements", "op": "check", "files": without_requirements},
        {"name": "remove the starting location", "files": removed(["setting/world.md"])},
        {"name": "remove an unknown file", "files": removed(["setting/missing.md"])},
        {"name": "remove a replaced file",
         "files": {**removed(["setting/world.md"]), f"{DRAFT}/setting-files/World.md": "# Glass Harbor\nEdited.\n"}},
        {"name": "removal list not JSON", "files": removed("{oops")},
        {"name": "removal list duplicate key", "files": removed('{"a": 1, "a": 2}')},
        {"name": "removal list constant", "files": removed("[NaN]")},
        {"name": "removal list not a list", "files": removed({"setting/world.md": True})},
        {"name": "removal list too long", "files": removed([f"setting/{index}.md" for index in range(513)])},
        {"name": "removal list at the count limit", "files": removed([f"setting/{index}.md" for index in range(512)])},
        {"name": "removal list not strings", "files": removed([1])},
        {"name": "removal list repeated", "files": removed(["setting/world.md", "setting/world.md"])},
        {"name": "removal without prefix", "files": removed(["world.md"])},
        {"name": "removal with another prefix", "files": removed(["other/world.md"])},
        {"name": "removal not portable", "files": removed(["setting/../world.md"])},
        {"name": "removals collide", "files": removed(["setting/a.md", "setting/A.md"])},
        {"name": "removals collide after full case folding", "files": removed(["setting/straße.md", "setting/STRASSE.md"])},
        {"name": "removal list oversized",
         "files": {**base, f"{DRAFT}/removed-files.json": {"repeat": " ", "count": 512 * 1024 + 1}}},
        {"name": "removal list at the size limit",
         "files": {**base, f"{DRAFT}/removed-files.json": {"text": "[]", "pad_to": 512 * 1024}}},
        {"name": "removal list a folder", "files": {**base, f"{DRAFT}/removed-files.json": None}},
        {"name": "empty package records", "files": {**base, f"{DRAFT}/package-records": None}},
        {"name": "ruleset source", "files": {**base, f"{DRAFT}/ruleset-source.json": source}},
        {"name": "setting source", "files": {**base, f"{DRAFT}/setting-source.json": setting_source}},
        {"name": "check ruleset source", "op": "check", "files": {**base, f"{DRAFT}/ruleset-source.json": source}},
    ]


def steps(root: Path) -> tuple[list, dict, dict, list]:
    database, setting_id = _install_setting(root / "install")
    archives = {}
    recorded = []
    with AuthorityOwner.open(database, "plan-snapshot-vectors") as owner:
        candidates = {item.install_id: item for item in owner.package_candidates()}
        install_order = ["coin-rules", "glass-harbor"]
        for install_id in install_order:
            exported = io.BytesIO()
            owner.export_package_candidate(candidates[install_id].candidate_id, exported)
            archives[install_id] = exported.getvalue()
        source = owner.package_candidate(setting_id)
        rules = candidates["coin-rules"]
        ids = {"setting": setting_id, "closure": source.closure_sha256, "rules": rules.candidate_id,
               "rules_closure": rules.closure_sha256}
        authority = campaign_plan_sources(owner, source.package_lock)
        assert authority is not None
        scaffold = root / "scaffold"
        created = _campaign_plan_draft_tool(scaffold, authority).handler(
            {
                "title": "Relay Expedition",
                "premise": "Restore the relay.",
                "tone": "Hopeful",
                "player_character_mode": "compatible",
                "starting_location_path": "world.md",
                "opening_situation": "The keeper asks for help.",
                "primary_objective": "Reach the silent relay.",
                "completion_conditions": ["The signal is disabled or the relay abandoned, and the keeper has "
                                          "responded to that outcome."],
                "custom_companion_minimum": 0,
                "custom_companion_maximum": 1,
            },
            None,
        )
        assert created["success"] is True, created
        plan = json.loads((scaffold / DRAFT / "plan.json").read_bytes())
        markdown = (scaffold / DRAFT / "plan.md").read_bytes().decode("utf-8")
        (declaration,) = authority.character_declarations
        character = {
            "format_id": "sylvia-gm-character-file",
            "format_version": 2,
            "id": "vela",
            "schema": {"id": declaration.schema_id, "content_sha256": declaration.schema_sha256},
            "summary": "Signal keeper.",
            "authored_profile": "# Vela\n\nThe harbor's keeper.",
            "participant": {"name": "Vela", "rank": 3},
            "entity_state": {"charge": 6},
            "setting_data": {"home": "Glass Harbor"},
        }
        declarations = json.loads(authority.read_setting("content/content-declarations.json"))
        declarations["inclusions"][0]["links"]["artifacts"].append("characters/vela.json")
        declarations["inclusions"][0]["links"]["artifacts"].sort()
        # The author's edit to the contained rules: the same runtime with a comment added.
        retained = retained_package_drafts(owner, setting_id, include_setting=True)
        runtime = next(item for item in retained if item.record["content_kind"] == "ruleset")
        runtime = runtime.payloads["runtime/ruleset.yaml"].decode("utf-8") + "# Edited by the Plan's author.\n"
        for case in cases(plan, markdown, character, declarations, ids, runtime):
            workspace = root / "workspaces" / str(len(recorded))
            write_files(workspace, case["files"])
            candidate_id = case.get("candidate", setting_id)
            if case.get("op") == "check":
                result = outcome(lambda: plain(check_plan_draft(
                    owner, workspace, candidate_id=candidate_id, expected_authority=authority,
                    require_complete_characters=case.get("complete", False))))
            else:
                result = outcome(lambda: materialized_payload(materialize_plan_snapshot_draft(
                    owner, workspace, candidate_id=candidate_id, expected_authority=authority)))
            step = {"name": case["name"], "op": case.get("op", "materialize"), "candidate": candidate_id,
                    "complete": case.get("complete", False), "files": case["files"], "outcome": result}
            recorded.append(step)
        after = sorted([item.candidate_id, item.closure_sha256] for item in owner.package_candidates())
    return recorded, archives, ids, after


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        recorded, archives, ids, after = steps(Path(scratch))
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
        "steps": recorded,
        "candidates_after": after,
    }
    (OUT / "vectors.json").write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
