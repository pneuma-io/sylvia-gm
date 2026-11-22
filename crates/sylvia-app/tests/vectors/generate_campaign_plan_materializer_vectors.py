"""Generate reference vectors for sylvia_app::campaign_plan_materializer.

Run with the snapshot's own interpreter, from the repository root, on Windows or Linux:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_campaign_plan_materializer_vectors.py

Each case lays out `campaign-plan-draft/plan.json` and `plan.md` in a fresh workspace (from a
shipped Plan, read by the Rust test from the repository, with text edits), then runs
`materialize_campaign_plan_draft` and `publish_campaign_plan_draft` with that Plan's own
expectations (as the campaign-plan vectors derive them). A publish records the written files'
hashes. Links are junctions on Windows and symbolic links elsewhere, so one file serves both.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "crates/sylvia-domain/tests/vectors"))

from generate_campaign_plan_vectors import expectation  # noqa: E402

from sylvia_gm.builder._campaign_plan_materializer import (  # noqa: E402
    materialize_campaign_plan_draft,
    publish_campaign_plan_draft,
)
from sylvia_gm.domain.campaign_plan import PackageBinding  # noqa: E402

OUTPUT = Path(__file__).with_name("campaign_plan_materializer_vectors.json")
SRD = "rulesets/srd-lite/package-record.json"
NEWLINE = chr(10)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def binding(value):
    return PackageBinding(value["id"], value["version"], value["content_sha256"])


def make_link(path: Path, target: Path) -> None:
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(path))
    else:
        os.symlink(target, path, target_is_directory=True)


def edited_text(text: str, edit) -> bytes:
    if edit is None:
        return text.encode("utf-8")
    kind = edit[0]
    if kind == "replace":
        assert edit[1] in text, edit
        return text.replace(edit[1], edit[2], 1).encode("utf-8")
    if kind == "append":
        return (text + edit[1]).encode("utf-8")
    if kind == "latin1":
        return edit[1].encode("latin-1")
    if kind == "repeat":
        return (edit[1] * edit[2]).encode("utf-8")
    raise AssertionError(edit)


def run(base: Path, case: dict, plan_text: str, markdown: str, expect: dict, state_schemas) -> dict:
    workspace = base / "workspace"
    draft = workspace / "campaign-plan-draft"
    draft.mkdir(parents=True)
    layout = case.get("layout", {})
    for name, text, edit in (("plan.json", plan_text, case.get("plan_edit")), ("plan.md", markdown, case.get("md_edit"))):
        state = layout.get(name)
        if state == "missing":
            continue
        if state == "directory":
            (draft / name).mkdir()
            continue
        (draft / name).write_bytes(edited_text(text, edit))
    for name in case.get("linked_outputs", []):
        (base / "elsewhere").mkdir(exist_ok=True)
        make_link(workspace / name, base / "elsewhere")
    kwargs = dict(expected_ruleset=binding(expect["ruleset"]), expected_setting=binding(expect["setting"]),
                  expected_closure_sha256=case.get("closure", expect["closure"]),
                  setting_paths=frozenset(expect["paths"]), setting_knowledge_ids=frozenset(expect["knowledge"]))
    result = {}
    try:
        draft_result = materialize_campaign_plan_draft(workspace, **kwargs)
        plan = draft_result.plan
        result["materialize"] = ["ok", plan.content_sha256, plan.artifact_id, plan.title, draft_result.plan_path,
                                 draft_result.readable_path, sorted(plan.referenced_setting_paths)]
    except Exception as error:  # every class is behaviour
        result["materialize"] = [type(error).__name__, str(error)]
    try:
        published = publish_campaign_plan_draft(workspace, **kwargs, state_schemas=state_schemas)
        files = [digest((workspace / name).read_bytes()) for name in ("campaign-plan.json", "campaign-plan.md")]
        result["publish"] = ["ok", published.plan_path, published.readable_path, *files]
    except Exception as error:
        result["publish"] = [type(error).__name__, str(error)]
    return result


def main() -> None:
    srd_schemas = tuple(json.loads((ROOT / SRD).read_text(encoding="utf-8")).get("state_schemas", ()))
    plans = sorted(str(Path(p).parent.relative_to(ROOT)).replace(chr(92), "/")
                   for p in glob.glob(str(ROOT / "campaign-plans/*/plan.json")))
    first = plans[0]
    cases = [{"plan": folder} for folder in plans]
    targeted = [
        {"plan_edit": ["replace", '"format_id"', '"format_id":1,"format_id"']},
        {"plan_edit": ["replace", '"format_version":2', '"format_version":NaN']},
        {"plan_edit": ["replace", '"format_version":2', '"format_version":Infinity']},
        # Differences row 2: a number beyond the float range is refused when the JSON is read.
        {"plan_edit": ["replace", '"format_version":2', '"format_version":1e999'], "row": 2},
        {"plan_edit": ["append", "x"]}, {"plan_edit": ["latin1", "{" + chr(0xff) + "}"]},
        {"plan_edit": ["replace", "{", chr(0xfeff) + "{"]}, {"plan_edit": ["repeat", " ", 512 * 1024 + 1]},
        {"md_edit": ["repeat", "#", 512 * 1024 + 1]}, {"md_edit": ["repeat", "#", 512 * 1024]},
        {"md_edit": ["latin1", "# " + chr(0xff)]}, {"layout": {"plan.json": "missing"}}, {"layout": {"plan.md": "missing"}},
        {"layout": {"plan.json": "directory"}}, {"layout": {"plan.md": "directory"}}, {"closure": "0" * 64},
        {"linked_outputs": ["campaign-plan.json"]}, {"linked_outputs": ["campaign-plan.md"]},
        {"state_schemas": "srd"}, {"state_schemas": "none"},
    ]
    cases.extend({"plan": first, **case} for case in targeted)
    for case in cases:
        folder = case["plan"]
        plan_text = (ROOT / folder / "plan.json").read_text(encoding="utf-8")
        markdown = (ROOT / folder / "plan.md").read_text(encoding="utf-8")
        expect = expectation(json.loads(plan_text))
        schemas = {"srd": srd_schemas, "none": ()}.get(case.get("state_schemas"), ())
        if "state_schemas" not in case and expect["ruleset"]["id"] == "sylvia.srd-lite.bg3":
            schemas = srd_schemas
            case["state_schemas"] = "srd"
        base = Path(tempfile.mkdtemp(prefix="sylvia-plan-draft-")).resolve()
        try:
            case["expect"] = expect
            case["outcome"] = run(base, case, plan_text, markdown, expect, schemas)
        finally:
            shutil.rmtree(base, ignore_errors=True)
    vectors = {"python": ".".join(sys.version.split()[0].split(".")[:2]), "srd": SRD, "cases": cases}
    text = json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + NEWLINE
    for marker in (tempfile.gettempdir(), os.path.expanduser("~")):
        assert marker not in text and marker.replace(chr(92), "/") not in text, "a machine path leaked"
    OUTPUT.write_text(text, encoding="utf-8", newline=NEWLINE)
    for case in cases:
        label = case["plan"].split("/")[-1] + " " + json.dumps({k: v for k, v in case.items()
                                                               if k not in ("plan", "outcome", "expect")})[:60]
        print(f"{label:90}", case["outcome"]["materialize"][0], case["outcome"]["publish"][0],
              (case["outcome"]["publish"][1] if case["outcome"]["publish"][0] != "ok" else "")[:60])


if __name__ == "__main__":
    main()
