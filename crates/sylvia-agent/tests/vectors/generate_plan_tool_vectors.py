"""Record Python's Builder Plan, import repair and subflow tools (B7) as plan_tool_vectors.json.

Run from the repository root with the snapshot's interpreter:

    .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_plan_tool_vectors.py
    .venv/bin/python -I crates/sylvia-agent/tests/vectors/generate_plan_tool_vectors.py

Every tool is Python's own: `_campaign_plan_tools` (the source, draft and publication tools, with
a fixed authority and a deferred one), `plan_draft_check_tool`, `import_repair_tools` and
`_creation_subflow_completion_tool`. The owner's callbacks are scripted stand-ins
(`generate_character_tool_vectors.py`'s helpers); the authority is a real
`CampaignPlanToolAuthority` bound to the BUILD CAMPAIGN PLAN skill's own example identities, so
the example Plan's mechanics come from the skill reference. Publishing without a publisher runs
the real `publish_campaign_plan_draft` on the workspace the draft tool wrote.

`plan.md` is written in text mode, so its newlines are the platform's: the file records them as
written (CRLF on Windows), and the Rust test compares with the platform's newline. Two runs give
identical bytes; no recorded value holds a path. Reference tooling for the port; retired with the
Python source.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from generate_character_tool_vectors import Script, call, exception, plain, read_files, write_files  # noqa: E402

from sylvia_gm.agent import _builder_session as session  # noqa: E402
from sylvia_gm.agent._import_repair_tools import import_repair_tools  # noqa: E402
from sylvia_gm.agent._plan_draft_check_tool import plan_draft_check_tool  # noqa: E402
from sylvia_gm.agent._skills import load_builder_skill  # noqa: E402
from sylvia_gm.application.campaign_plan_sources import CampaignPlanToolAuthority  # noqa: E402
from sylvia_gm.builder._campaign_plan_materializer import CampaignPlanDraft  # noqa: E402
from sylvia_gm.domain.campaign_plan import PackageBinding, ValidatedCampaignPlan  # noqa: E402

OUTPUT = Path(__file__).with_name("plan_tool_vectors.json")
NL = chr(10)
E_ACUTE = chr(0xE9)


def identities():
    reference = json.loads(load_builder_skill("BUILD CAMPAIGN PLAN", reference="references/combat-plan.json").text)
    bindings = reference["bindings"]

    def binding(item):
        return {"package_id": item["id"], "version": item["version"], "content_sha256": item["content_sha256"]}
    return binding(bindings["ruleset"]), binding(bindings["campaign_setting"]), bindings["package_closure_sha256"]


RULESET, SETTING, CLOSURE = identities()
SETTING_FILES = {
    "locations/harbor.md": "# Harbor" + NL + "Gulls and rope" + E_ACUTE,
    "npcs/mara.md": "# Mara",
    "npcs/ash.md": "# Ash",
    "lore/broken.md": {"raise": {"class": "OSError", "message": "disk"}},
    "lore/refused.md": {"raise": {"class": "CommandRejected", "message": "No", "code": "setting_unavailable"}},
}
AUTHORITY = {
    "ruleset": RULESET, "setting": SETTING, "closure_sha256": CLOSURE, "setting_paths": sorted(SETTING_FILES),
    "setting_files": SETTING_FILES, "setting_knowledge_ids": ["harbor"],
    "participant_schemas": [{"kind": "player_character", "schema_id": "example.hero", "schema": {"type": "object"}}],
    "setting_character_schema": None, "state_schemas": [{"key": "state", "required": True}, {"key": "other"}],
}


def authority_of(spec, script):
    def read_setting(path):
        script.calls.append({"read_setting": path})
        content = spec["setting_files"][path]
        if isinstance(content, dict):
            raise exception(content["raise"])
        return content
    return CampaignPlanToolAuthority(
        ruleset=PackageBinding(**spec["ruleset"]), setting=PackageBinding(**spec["setting"]),
        closure_sha256=spec["closure_sha256"], setting_paths=frozenset(spec["setting_paths"]),
        read_setting=read_setting, setting_knowledge_ids=frozenset(spec["setting_knowledge_ids"]),
        participant_schemas=tuple(spec["participant_schemas"]),
        setting_character_schema=spec["setting_character_schema"],
        state_schemas=tuple(spec["state_schemas"]))


PUBLISHED = {"payload": {"bindings": {"ruleset": {"id": "r"}, "campaign_setting": {"id": "s"},
                                      "package_closure_sha256": "c" * 64}, "title": "Iron Debt"},
             "content_sha256": "d" * 64, "artifact_id": "campaign-plan-1", "title": "Iron Debt",
             "readable_markdown": "# Iron Debt" + NL + "x" * 7000, "referenced_setting_paths": ["npcs/mara.md"],
             "plan_path": "campaign-plan-draft/plan.json", "readable_path": "campaign-plan-draft/plan.md"}


def published_draft():
    fields = {key: value for key, value in PUBLISHED.items() if key not in ("plan_path", "readable_path")}
    plan = ValidatedCampaignPlan(**{**fields, "referenced_setting_paths": tuple(fields["referenced_setting_paths"])})
    return CampaignPlanDraft(plan=plan, plan_path=PUBLISHED["plan_path"], readable_path=PUBLISHED["readable_path"])


DIAGNOSTICS = [
    {"code": "campaign_plan_fields_invalid", "phase": "content", "message": "Fix fields", "file": "x.json", "path": "$"},
    {"code": "package_content_invalid", "phase": "content", "message": "Setting file", "file": "World.md",
     "package_id": SETTING["package_id"], "guidance": "Edit it", "expected": "string"},
    {"code": "package_content_invalid", "phase": "content", "message": "Ruleset file", "file": "rules.yaml",
     "package_id": RULESET["package_id"]},
    {"code": "package_content_invalid", "phase": "content", "message": "Other", "file": "a.md", "package_id": "other"},
] + [{"code": f"d{index}", "phase": "content", "message": f"problem {index}"} for index in range(6)]

GOOD_DRAFT = {
    "title": " The Iron Debt ", "premise": "A heist in the harbor" + E_ACUTE, "tone": "Tense",
    "player_character_mode": "fixed", "player_character_path": "setting/npcs/mara.md",
    "required_companion_paths": ["npcs/ash.md", "setting/npcs/mara.md"],
    "starting_location_path": "setting/locations/harbor.md", "opening_situation": "The tide turns.",
    "primary_objective": "Steal the ledger.", "completion_conditions": [" The ledger is taken ", "Escape"],
    "custom_companion_minimum": 0, "custom_companion_maximum": 2, "opening_direction": " Start at dawn ",
}


def without(key):
    return {name: value for name, value in GOOD_DRAFT.items() if name != key}


def plan_calls():
    source = "inspect_campaign_sources"
    draft = "create_campaign_plan_draft"
    publish = "publish_campaign_plan"
    return [
        [source, {"action": "list"}],
        [source, {"action": "list", "query": " NPCS "}],
        [source, {"action": "list", "offset": 2, "limit": 2}],
        [source, {"action": "list", "offset": 8}],
        [source, {"action": "list", "offset": 9}],
        [source, {"action": "list", "limit": 513}],
        [source, {"action": "list", "limit": True}],
        [source, {"action": "list", "path": "x"}],
        [source, {"action": "list", "query": 5}],
        [source, {"action": "read", "path": "campaign-plan/example/plan.json"}],
        [source, {"action": "read", "path": "campaign-plan/example/plan.md"}],
        [source, {"action": "read", "path": "ruleset/identity.json"}],
        [source, {"action": "read", "path": "setting/locations/harbor.md", "query": None}],
        [source, {"action": "read", "path": "setting/lore/broken.md"}],
        [source, {"action": "read", "path": "unknown.md"}],
        [source, {"action": "read", "path": "setting/npcs/mara.md", "offset": 0}],
        [source, {"action": "read", "path": "setting/npcs/mara.md", "query": "x"}],
        [source, {"action": "read"}],
        [source, {"action": "delete"}],
        [source, {"action": "read", "path": "setting/lore/refused.md"}],
        [draft, without("title")],
        [draft, {**GOOD_DRAFT, "title": "  "}],
        [draft, {**GOOD_DRAFT, "custom_companion_minimum": True}],
        [draft, {**GOOD_DRAFT, "custom_companion_maximum": 2.0}],
        [draft, {**GOOD_DRAFT, "unknown": 1}],
        [draft, {**GOOD_DRAFT, "completion_conditions": []}],
        [draft, {**GOOD_DRAFT, "completion_conditions": ["ok", " "]}],
        [draft, {**GOOD_DRAFT, "player_character_mode": "random"}],
        [draft, {**without("player_character_path")}],
        [draft, {**GOOD_DRAFT, "required_companion_paths": "npcs/ash.md"}],
        [draft, {**GOOD_DRAFT, "custom_companion_minimum": 3}],
        [draft, {**GOOD_DRAFT, "custom_companion_maximum": 65}],
        [draft, {**GOOD_DRAFT, "custom_companion_minimum": -1}],
        [draft, {**GOOD_DRAFT, "starting_location_path": "locations/missing.md"}],
        [draft, {**GOOD_DRAFT, "required_companion_paths": ["npcs/ghost.md"]}],
        [draft, {**GOOD_DRAFT, "opening_direction": " "}],
        [draft, {**without("opening_direction"), "player_character_mode": "compatible",
                 "player_character_path": 5, "required_companion_paths": []}],
        [draft, GOOD_DRAFT],
        [publish, {}],
        [publish, {"x": 1}],
    ]


def owner_outcomes():
    return [
        {"return": {"readiness": {"characters": "incomplete"}, "status": "kept"}},
        {"raise": {"class": "CommandRejected", "message": "Plan invalid", "code": "campaign_plan_fields_invalid",
                   "diagnostics": DIAGNOSTICS * 2}},
        {"raise": {"class": "ValueError", "message": "plain"}},
        {"raise": {"class": "StaleRevision", "message": "old"}},
        {"raise": {"class": "OSError", "message": "disk"}},
        {"raise": {"class": "KeyError", "message": "missing"}},
    ]


def cases():
    publisher_outcomes = [
        {"raise": {"class": "CommandRejected", "message": "Plan invalid because " + "a" * 300, "code": "x",
                   "diagnostics": DIAGNOSTICS}},
        {"raise": {"class": "ValueError", "message": "bad value"}},
        {"raise": {"class": "OSError", "message": "disk"}},
        {"raise": {"class": "StaleRevision", "message": "old"}},
        "published", "published-without-artifact",
    ]
    return [
        {"tool": "plan", "authority": AUTHORITY, "deferred": False, "publisher": None, "checker": None,
         "files": {}, "calls": plan_calls() + [["publish_campaign_plan", {}]]},
        {"tool": "plan", "authority": AUTHORITY, "deferred": False, "publisher": None, "checker": None,
         "files": {"campaign-plan-draft/setting-files/World.md": "edited"},
         "calls": [["publish_campaign_plan", {}]]},
        {"tool": "plan", "authority": AUTHORITY, "deferred": False, "publisher": publisher_outcomes, "checker": None,
         "files": {}, "calls": [["publish_campaign_plan", {}]] * 6},
        {"tool": "plan", "authority": {**AUTHORITY, "state_schemas": [], "setting_paths": ["npcs/mara.md"]},
         "deferred": False, "publisher": None, "checker": [{"return": {"ok": True}}], "files": {},
         "calls": [["inspect_campaign_sources", {"action": "read", "path": "campaign-plan/example/plan.json"}],
                   ["check_campaign_plan_draft", {}], ["check_campaign_plan_draft", {"x": 1}]]},
        {"tool": "plan", "authority": {**AUTHORITY, "setting_paths": []}, "deferred": False, "publisher": None,
         "checker": None, "files": {}, "calls": [
             ["inspect_campaign_sources", {"action": "read", "path": "campaign-plan/example/plan.json"}],
             ["inspect_campaign_sources", {"action": "list"}]]},
        {"tool": "plan", "authority": AUTHORITY, "deferred": True, "selections": [False, True, True], "publisher": None,
         "checker": None, "files": {}, "calls": [["inspect_campaign_sources", {"action": "list"}],
                                                 ["inspect_campaign_sources", {"action": "list", "limit": 1}],
                                                 ["create_campaign_plan_draft", {"x": 1}]]},
        {"tool": "plan_check", "outcomes": owner_outcomes(), "calls": [{}] * 6 + [{"x": 1}]},
        {"tool": "import_repair", "outcomes": owner_outcomes() + owner_outcomes()[:2],
         "calls": [["check_import_repair", {}]] * 6 + [["publish_import_repair", {}]] * 2
         + [["publish_import_repair", {"x": 1}]]},
        {"tool": "subflow", "calls": [
            {"summary": " Ready ", "boundary_conflicts": []},
            {"summary": "Ready", "boundary_conflicts": [" Conflict one ", "Two"]},
            {"summary": "  ", "boundary_conflicts": []},
            {"summary": "s" * 2001, "boundary_conflicts": []},
            {"summary": E_ACUTE * 2000, "boundary_conflicts": []},
            {"summary": "Ready", "boundary_conflicts": ["c"] * 17},
            {"summary": "Ready", "boundary_conflicts": ["x" * 501]},
            {"summary": "Ready", "boundary_conflicts": [" "]},
            {"summary": "Ready", "boundary_conflicts": "none"},
            {"summary": "Ready"},
            {"summary": "Ready", "boundary_conflicts": [], "extra": 1},
            {"summary": 5, "boundary_conflicts": []},
        ]},
    ]


def run(case):
    script = Script(case.get("outcomes", ()))
    kind = case["tool"]
    with tempfile.TemporaryDirectory() as scratch:
        workspace = Path(scratch)
        write_files(workspace, case.get("files", {}))
        if kind == "plan":
            fixed = authority_of(case["authority"], script)
            if case["deferred"]:
                selections = list(case["selections"])

                def authority():
                    script.calls.append({"select_authority": None})
                    return fixed if selections.pop(0) else None
            else:
                authority = fixed
            publisher = checker = None
            if case["publisher"] is not None:
                outcomes = list(case["publisher"])

                def publisher():
                    script.calls.append({"publish_plan": None})
                    outcome = outcomes.pop(0)
                    if outcome == "published":
                        return published_draft(), {"artifact_id": "library-1"}
                    if outcome == "published-without-artifact":
                        return published_draft(), None
                    raise exception(outcome["raise"])
            if case["checker"] is not None:
                check_script = Script(case["checker"])

                def checker():
                    return check_script.answer({"check_plan": None})
            tools = list(session._campaign_plan_tools(workspace, authority, publisher, checker))
        elif kind == "plan_check":
            tools = [plan_draft_check_tool(lambda: script.answer({"check_plan": None}))]
        elif kind == "import_repair":
            tools = list(import_repair_tools(lambda: script.answer({"check_repair": None}),
                                             lambda: script.answer({"publish_repair": None})))
        else:
            tools = [session._creation_subflow_completion_tool()]
        by_name = {tool.name: tool for tool in tools}
        answers = []
        for params in case["calls"]:
            if isinstance(params, list):
                name, params = params
                answers.append({"tool": name, **call(by_name[name], params)})
            else:
                answers.append(call(tools[0], params))
        return {**case, "tool_names": [tool.name for tool in tools], "answers": answers,
                "callback_calls": script.calls, "files_after": read_files(workspace)}


def main() -> None:
    output = plain({
        "python": ".".join(sys.version.split()[0].split(".")[:2]),
        "published": PUBLISHED,
        "cases": [run(case) for case in cases()],
    })
    OUTPUT.write_text(json.dumps(output, ensure_ascii=True, indent=1) + NL, encoding="utf-8", newline=NL)
    print(OUTPUT.name, len(output["cases"]), "cases")


if __name__ == "__main__":
    main()
