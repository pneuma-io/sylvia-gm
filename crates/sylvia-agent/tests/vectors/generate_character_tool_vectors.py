"""Record Python's Builder character tools (B5) as character_tool_vectors.json.

Run from the repository root with the snapshot's interpreter:

    .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_character_tool_vectors.py

Every tool is Python's own: `_participant_artifact_tool`, `_participant_draft_check_tool`,
`participant_file_tool`, `character_draft_tool`, `reuse_character_copy_tool`,
`character_profile_tools` and `content_definition_tool`. The owner callbacks are stand-ins that
record each call and answer the case's scripted outcome: a result, or an exception of a named
class (`raise`), which the Rust test raises as the same class. Declarations are SRD Lite's real
player-character declaration and a small companion one.

Each case records the tool's full definition (`tool_spec_to_openai`), every call's parameters
and answer (or the exception it let through), the callback calls, and the workspace files after
the calls. No recorded value holds a platform path, so one file serves both platforms.
Two runs give identical bytes. Reference tooling for the port; retired with the Python source.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
import tempfile
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.agent._builder_session import (  # noqa: E402
    _participant_artifact_tool,
    _participant_draft_check_tool,
    _participant_entity_state,
)
from sylvia_gm.agent._character_draft_tool import character_draft_tool  # noqa: E402
from sylvia_gm.agent._character_profile_tools import character_profile_tools  # noqa: E402
from sylvia_gm.agent._character_reuse_tool import reuse_character_copy_tool  # noqa: E402
from sylvia_gm.agent._content_definition_tool import content_definition_tool  # noqa: E402
from sylvia_gm.agent._participant_file_tool import participant_file_tool  # noqa: E402
from sylvia_gm.application.character_profiles import CharacterProfile  # noqa: E402
from sylvia_gm.application.initial_character_state import initial_character_input_schema  # noqa: E402
from sylvia_gm.application.reusable_participants import ParticipantValidationError  # noqa: E402
from sylvia_gm.builder._workspace_files import StaleWorkspaceFileError  # noqa: E402
from sylvia_gm.domain import errors  # noqa: E402
from sylvia_gm.domain.content_definitions import get_content_definition  # noqa: E402
from sylvia_gm.domain.errors import CommandRejected, MechanicsValidationIssue, RulesetDiagnostic  # noqa: E402
from sylvia_gm.domain.package_library import (  # noqa: E402
    ParticipantArtifactDeclaration,
    canonical_json_bytes,
    manifest_from_record,
)
from sylvia_gm.tools._converter import tool_spec_to_openai  # noqa: E402

OUTPUT = Path(__file__).with_name("character_tool_vectors.json")
NL = chr(10)
SRD = "rulesets/srd-lite/package-record.json"
STARTERS = "src/sylvia_gm/campaign/srd-starters.json"
PARTICIPANT = "participant-" + "a" * 24 + "-" + "b" * 16
PROFILE = "character-profile-" + "c" * 24 + "-" + "d" * 16
DIGEST = "e" * 64


# ── Declarations ──────────────────────────────────────────────────────────────────────────


def declaration_fields(declaration):
    return {
        "kind": declaration.kind, "schema_id": declaration.schema_id,
        "schema_sha256": declaration.schema_sha256, "schema": declaration.schema,
        "entity_state_schema": declaration.entity_state_schema,
        "initial_state_calculations": list(declaration.initial_state_calculations),
        "initial_state_validation": declaration.initial_state_validation,
    }


def companion_declaration():
    schema = {"type": "object", "additionalProperties": False, "required": ["name"],
              "properties": {"name": {"type": "string", "minLength": 1}, "loyal": {"type": "boolean"}}}
    return ParticipantArtifactDeclaration(
        kind="companion", schema_id="example.hound", schema_sha256=hashlib.sha256(canonical_json_bytes(schema)).hexdigest(),
        schema=schema,
        entity_state_schema={"type": "object", "additionalProperties": False,
                             "properties": {"bond": {"type": "integer"}}},
        initial_state_calculations=(), initial_state_validation=None)


def srd_declaration():
    record = json.loads((ROOT / SRD).read_text(encoding="utf-8"))
    manifest = manifest_from_record(record, format_version=4)
    return next(item for item in manifest.participant_artifacts if item.kind == "player_character")


def starter_draft(declaration):
    """An SRD Lite starter as authored input: calculated state omitted."""
    starter = json.loads((ROOT / STARTERS).read_text(encoding="utf-8"))[0]
    authored = initial_character_input_schema(declaration.entity_state_schema,
                                              declaration.initial_state_calculations)
    keys = set(authored.get("properties", {}))
    state = {key: value for key, value in starter["entity_state"].items() if key in keys}
    return starter, state


# ── Compact markers ───────────────────────────────────────────────────────────────────────


def repeat(text, count):
    """`text * count`, kept short in the vectors."""
    return {"$repeat": [text, count]}


def as_json(value):
    """`json.dumps(value)` of a value that may hold markers."""
    return {"$json": value}


def expand(value):
    """The value a marker stands for; the Rust test expands them the same way."""
    if isinstance(value, dict):
        if set(value) == {"$repeat"}:
            text, count = value["$repeat"]
            return text * count
        if set(value) == {"$json"}:
            return json.dumps(expand(value["$json"]))
        return {key: expand(item) for key, item in value.items()}
    if isinstance(value, list):
        return [expand(item) for item in value]
    return value


# ── Scripted callbacks ────────────────────────────────────────────────────────────────────


def exception(spec):
    """The exception a scripted outcome raises, by class."""
    name, message = spec["class"], spec.get("message", "scripted refusal")
    if name == "CommandRejected":
        diagnostics = tuple(RulesetDiagnostic(**item) for item in spec.get("diagnostics", ()))
        return CommandRejected(message, code=spec.get("code", "command_rejected"), diagnostics=diagnostics)
    if name == "ParticipantValidationError":
        return ParticipantValidationError(tuple(MechanicsValidationIssue(**item) for item in spec["issues"]))
    if name == "StaleWorkspaceFileError":
        return StaleWorkspaceFileError(message)
    if hasattr(errors, name):
        return getattr(errors, name)(message)
    return {"ValueError": ValueError, "OSError": OSError, "KeyError": KeyError, "TypeError": TypeError,
            "UnicodeDecodeError": None, "RuntimeError": RuntimeError}[name](message)


class Script:
    """Answers each call with the next scripted outcome and records the call."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def answer(self, call):
        self.calls.append(call)
        outcome = self.outcomes.pop(0) if self.outcomes else {"return": {"note": "default"}}
        if "raise" in outcome:
            raise exception(outcome["raise"])
        return deepcopy(outcome["return"])


def publisher(script):
    def publish(**kwargs):
        return script.answer({"publish": kwargs})
    return publish


def reader(script, kind):
    def read(*, artifact_id, expected_content_sha256, schema_id=None):
        return script.answer({kind: {"artifact_id": artifact_id,
                                     "expected_content_sha256": expected_content_sha256,
                                     "schema_id": schema_id}})
    return read


def source_resolver(script):
    def resolve(source_profile):
        return script.answer({"resolve_source_profile": source_profile})
    return resolve


def profile_callbacks(script):
    def save(data):
        return script.answer({"save": hashlib.sha256(data).hexdigest()})

    def read(artifact_id, expected_content_sha256):
        profile = script.answer({"read_profile": [artifact_id, expected_content_sha256]})
        return CharacterProfile(artifact_id=artifact_id, content_sha256=expected_content_sha256, profile=profile)

    return save, read


# ── Running a case ────────────────────────────────────────────────────────────────────────


def write_files(workspace: Path, files: dict) -> None:
    for relative, content in files.items():
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, dict) and "base64" in content:
            path.write_bytes(base64.b64decode(content["base64"]))
        else:
            path.write_bytes(content.encode("utf-8"))


def read_files(workspace: Path) -> dict:
    files = {}
    for path in sorted(workspace.rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            relative = path.relative_to(workspace).as_posix()
            if len(data) > 4096:
                files[relative] = {"sha256": hashlib.sha256(data).hexdigest(), "length": len(data)}
                continue
            try:
                files[relative] = data.decode("utf-8")
            except UnicodeDecodeError:
                files[relative] = {"base64": base64.b64encode(data).decode("ascii")}
    return files


def call(tool, params):
    try:
        return {"result": tool.handler(deepcopy(params), None)}
    except Exception as error:  # noqa: BLE001 - recorded
        return {"raised": type(error).__name__, "message": str(error)}


def file_version(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── The cases ─────────────────────────────────────────────────────────────────────────────

ISSUE = {"path": "$.name", "kind": "type", "expected_type": "string", "actual_type": "integer"}
DIAGNOSTICS = [{"code": f"d{index}", "phase": "content", "message": f"problem {index}"} for index in range(20)]
DRAFT_TOOL_FILE = "drafts/hero.json"


def artifact_calls(summary_text, profile_text, participant, state):
    """Calls of the publication tool, valid and invalid, for a role-bound chat."""
    good = {"summary": f"  {summary_text}  ", "authored_profile": profile_text, "participant": participant,
            "entity_state": json.dumps(state)}
    return [
        good,
        {**good, "setting_data": json.dumps({"home": "Waterdeep"}), "source_profile": {"artifact_id": PROFILE,
                                                                                         "content_sha256": DIGEST}},
        {key: value for key, value in good.items() if key != "summary"},
        {**good, "unexpected": 1},
        {**good, "summary": "   "},
        {**good, "participant": ["not", "an", "object"]},
        {**good, "entity_state": {"already": "an object"}},
        {**good, "entity_state": "not json"},
        {**good, "entity_state": '{"a": 1, "a": 2}'},
        {**good, "entity_state": "[1, 2]"},
        {**good, "entity_state": "{\"x\": " + "[" * 33 + "]" * 33 + "}"},
        {**good, "entity_state": "{\"x\": " + "[" * 32 + "]" * 32 + "}"},
        {**good, "entity_state": '{"x": NaN}'},
        {**good, "entity_state": as_json({"big": repeat("x", 256 * 1024)})},
        {**good, "setting_data": "{oops"},
        {**good, "schema_id": "unknown.schema"},
    ]


def cases():
    srd = srd_declaration()
    hound = companion_declaration()
    starter, state = starter_draft(srd)
    good_hound = {"summary": "A loyal hound.", "authored_profile": "# Rex" + NL + NL + "A hound.",
                  "participant": {"name": "Rex", "loyal": True}, "entity_state": json.dumps({"bond": 2})}
    result = []

    # The publication tool, bound to the chat's role, against scripted publisher outcomes.
    outcomes = [
        {"return": {"artifact_id": PARTICIPANT, "status": "kept-in-place", "content_sha256": DIGEST}},
        {"return": {"artifact_id": PARTICIPANT}},
    ] + [{"return": {}}] * 14
    result.append({"tool": "artifact", "role": "player_character", "declarations": [], "schemas": [srd.schema_id],
                   "checking": False, "outcomes": outcomes,
                   "calls": artifact_calls(starter["summary"], starter["authored_profile"],
                                           starter["participant"], state)})
    raised = [{"raise": {"class": "ParticipantValidationError", "issues": [ISSUE, {"path": "$", "kind": "required"}]}},
              {"raise": {"class": "CommandRejected", "message": "Setting is stale", "code": "setting_stale",
                         "diagnostics": DIAGNOSTICS}},
              {"raise": {"class": "StaleRevision", "message": "old revision"}},
              {"raise": {"class": "OSError", "message": "disk"}},
              {"raise": {"class": "ValueError", "message": "bad"}},
              {"raise": {"class": "TypeError", "message": "wrong"}},
              {"raise": {"class": "KeyError", "message": "missing"}},
              {"raise": {"class": "RuntimeError", "message": "boom"}}]
    result.append({"tool": "artifact", "role": "companion", "declarations": [], "schemas": [hound.schema_id],
                   "checking": False, "outcomes": raised, "calls": [good_hound] * len(raised)})
    two = [{"return": {"ok": 1}}] * 6
    result.append({"tool": "artifact", "role": None, "declarations": [], "schemas": [srd.schema_id, hound.schema_id],
                   "checking": True, "outcomes": two, "calls": [
                       {**good_hound, "role": "companion", "schema_id": hound.schema_id},
                       {**good_hound, "role": "companion"},
                       {**good_hound, "schema_id": hound.schema_id},
                       {**good_hound, "role": "villain", "schema_id": hound.schema_id},
                       {**good_hound, "role": "player_character", "schema_id": srd.schema_id},
                       {**good_hound, "role": "companion", "schema_id": 7},
                   ]})
    result.append({"tool": "artifact", "role": None, "declarations": [], "schemas": [], "checking": False,
                   "outcomes": [], "calls": []})

    # The draft check, through the real validator.
    check_calls = [
        {"summary": starter["summary"], "authored_profile": starter["authored_profile"],
         "participant": starter["participant"], "entity_state": json.dumps(state)},
        {"summary": starter["summary"], "authored_profile": starter["authored_profile"],
         "participant": {**starter["participant"], "level": "one"}, "entity_state": json.dumps(state)},
        {"summary": starter["summary"], "authored_profile": starter["authored_profile"],
         "participant": starter["participant"], "entity_state": json.dumps({})},
        {"summary": starter["summary"], "authored_profile": starter["authored_profile"],
         "participant": starter["participant"], "entity_state": json.dumps(state),
         "source_profile": {"artifact_id": PROFILE, "content_sha256": DIGEST}},
    ]
    result.append({"tool": "draft_check", "role": "player_character", "declarations": [srd.schema_id],
                   "setting_character_schema": None, "resolver": None, "outcomes": [], "calls": check_calls})
    world = {"type": "object", "additionalProperties": False, "required": ["home"],
             "properties": {"home": {"type": "string", "minLength": 1}}}
    hound_checks = [
        {**good_hound, "setting_data": json.dumps({"home": "Kennel"})},
        {**good_hound},
        {**good_hound, "setting_data": json.dumps({"home": 3})},
        {**good_hound, "setting_data": json.dumps({"home": "Kennel"}),
         "source_profile": {"artifact_id": PROFILE, "content_sha256": DIGEST}},
        {**good_hound, "setting_data": json.dumps({"home": "Kennel"}), "source_profile": "not an object"},
    ]
    result.append({"tool": "draft_check", "role": "companion", "declarations": [hound.schema_id],
                   "setting_character_schema": world, "resolver": True,
                   "outcomes": [{"return": None}, {"return": None}, {"return": None}, {"return": {"ok": True}},
                                {"raise": {"class": "CommandRejected", "message": "Source profile is unknown",
                                           "code": "source_profile_unknown"}}],
                   "calls": hound_checks})
    result.append({"tool": "draft_check", "role": None,
                   "declarations": [srd.schema_id, hound.schema_id],
                   "setting_character_schema": None, "resolver": None, "outcomes": [],
                   "calls": [{**good_hound, "role": "companion", "schema_id": hound.schema_id},
                             {**good_hound, "role": "companion"}]})

    # The file tools, around a scripted inline publication tool and the real draft check.
    hound_file = json.dumps({**good_hound, "entity_state": {"bond": 2}}, indent=2)
    files = {
        "drafts/rex.json": hound_file,
        "drafts/not-json.json": "{oops",
        "drafts/latin1.json": {"base64": base64.b64encode(b"{\"summary\": \"caf\xe9\"}").decode("ascii")},
        "drafts/list.json": "[1]",
        "drafts/huge.json": as_json({"x": repeat("y", 256 * 1024)}),
    }
    file_calls = [
        {"path": "drafts/rex.json", "expected_version": file_version(hound_file)},
        {"path": "drafts/rex.json", "expected_version": DIGEST},
        {"path": "drafts/rex.json"},
        {"path": "drafts/rex.json", "expected_version": "ABC"},
        {"path": "  ", "expected_version": DIGEST},
        {"path": "../outside.json", "expected_version": DIGEST},
        {"path": "drafts/missing.json", "expected_version": DIGEST},
        {"path": "drafts/not-json.json", "expected_version": file_version("{oops")},
        {"path": "drafts/latin1.json", "expected_version": hashlib.sha256(b"{\"summary\": \"caf\xe9\"}").hexdigest()},
        {"path": "drafts/list.json", "expected_version": file_version("[1]")},
        {"path": "drafts/huge.json", "expected_version": DIGEST},
    ]
    for checking in (False, True):
        result.append({"tool": "file", "role": "companion", "checking": checking,
                       "declarations": [hound.schema_id], "schemas": [hound.schema_id],
                       "outcomes": [{"return": {"artifact_id": PARTICIPANT}}] * len(file_calls),
                       "files": files, "calls": file_calls})

    # Opening a saved character as a workspace draft.
    draft = {"summary": "Rex", "participant": {"name": "Rex"}, "note": "caf" + chr(233)}
    draft_calls = [
        {"artifact_id": PARTICIPANT, "expected_content_sha256": DIGEST, "destination_path": DRAFT_TOOL_FILE},
        {"artifact_id": PARTICIPANT, "expected_content_sha256": DIGEST, "destination_path": DRAFT_TOOL_FILE},
        {"artifact_id": PARTICIPANT, "expected_content_sha256": DIGEST, "destination_path": DRAFT_TOOL_FILE,
         "expected_version": file_version(json.dumps(draft, ensure_ascii=False, indent=2) + NL)},
        {"artifact_id": PARTICIPANT, "expected_content_sha256": DIGEST, "destination_path": "../out.json"},
        {"artifact_id": "participant-bad", "expected_content_sha256": DIGEST, "destination_path": "x.json"},
        {"artifact_id": PARTICIPANT, "expected_content_sha256": DIGEST, "destination_path": "x.json", "extra": "1"},
        {"artifact_id": PARTICIPANT, "expected_content_sha256": DIGEST, "destination_path": 5},
        {"artifact_id": PARTICIPANT, "expected_content_sha256": DIGEST, "destination_path": "x.json",
         "schema_id": "unknown"},
        {"artifact_id": PARTICIPANT, "expected_content_sha256": DIGEST, "destination_path": "y.json"},
        {"artifact_id": PARTICIPANT, "expected_content_sha256": DIGEST, "destination_path": "z.json"},
        {"artifact_id": PARTICIPANT, "expected_content_sha256": DIGEST, "destination_path": "w.json"},
    ]
    result.append({"tool": "character_draft", "schema_ids": [], "files": {}, "calls": draft_calls, "outcomes": [
        {"return": draft}, {"return": draft}, {"return": {**draft, "summary": "Rex again"}},
        {"raise": {"class": "CommandRejected", "message": "Character is unavailable", "code": "participant_unavailable"}},
        {"raise": {"class": "ValueError", "message": "bad"}},
        {"raise": {"class": "StaleRevision", "message": "old"}},
    ]})
    result.append({"tool": "character_draft", "schema_ids": [srd.schema_id, hound.schema_id], "files": {},
                   "outcomes": [{"return": draft}] * 3, "calls": [
                       {"artifact_id": PARTICIPANT, "expected_content_sha256": DIGEST, "destination_path": "a.json"},
                       {"artifact_id": PARTICIPANT, "expected_content_sha256": DIGEST, "destination_path": "a.json",
                        "schema_id": hound.schema_id}]})

    # Reusing a saved character unchanged.
    reuse_calls = [{"artifact_id": PARTICIPANT, "expected_content_sha256": DIGEST}] * 6 + [
        {"expected_content_sha256": DIGEST, "artifact_id": PARTICIPANT, "schema_id": hound.schema_id},
        {"artifact_id": PARTICIPANT, "expected_content_sha256": "E" * 64},
        {"artifact_id": PARTICIPANT, "expected_content_sha256": DIGEST, "extra": True},
        {"artifact_id": 5, "expected_content_sha256": DIGEST},
    ]
    result.append({"tool": "reuse", "schema_ids": [hound.schema_id], "calls": reuse_calls, "outcomes": [
        {"return": {"artifact_id": PARTICIPANT, "status": "already-there", "copied_from": PARTICIPANT}},
        {"raise": {"class": "CommandRejected", "message": "Needs adaptation", "code": "character_adaptation_required",
                   "diagnostics": DIAGNOSTICS}},
        {"raise": {"class": "OSError", "message": "disk"}},
        {"raise": {"class": "TypeError", "message": "wrong"}},
        {"raise": {"class": "StaleRevision", "message": "old"}},
        {"raise": {"class": "KeyError", "message": "missing"}},
        {"return": {"artifact_id": PARTICIPANT}},
    ]})
    result.append({"tool": "reuse", "schema_ids": [], "calls": [], "outcomes": []})

    # The profile tools.
    profile = {"format": "character_profile_v2", "identity": {"name": "Wren"},
               "lore": {"entries": [{"id": "l1", "text": "Old tale"}, {"id": "l2", "text": repeat("x", 70000)}]},
               "relationships": [{"id": "r1", "name": "Ash"}, {"id": "r2"}], "greetings": "Hello", "assets": [1]}
    profile_text = json.dumps(get_content_definition("character_profile_v2")["example"], indent=2)
    profile_files = {"profiles/wren.json": profile_text, "profiles/bad.json": "{oops"}
    profile_calls = [
        ["get_character_card_import_contract", {}],
        ["get_character_card_import_contract", {"extra": "x"}],
        ["read_character_profile_section", {"artifact_id": PROFILE, "expected_content_sha256": DIGEST,
                                            "section": "identity"}],
        ["read_character_profile_section", {"artifact_id": PROFILE, "expected_content_sha256": DIGEST,
                                            "section": "lore", "entry_id": "l1"}],
        ["read_character_profile_section", {"artifact_id": PROFILE, "expected_content_sha256": DIGEST,
                                            "section": "lore", "entry_id": "l2"}],
        ["read_character_profile_section", {"artifact_id": PROFILE, "expected_content_sha256": DIGEST,
                                            "section": "relationships", "entry_id": "r9"}],
        ["read_character_profile_section", {"artifact_id": PROFILE, "expected_content_sha256": DIGEST,
                                            "section": "greetings", "entry_id": "g1"}],
        ["read_character_profile_section", {"artifact_id": PROFILE, "expected_content_sha256": DIGEST,
                                            "section": "assets", "entry_id": "a1"}],
        ["read_character_profile_section", {"artifact_id": PROFILE, "expected_content_sha256": DIGEST,
                                            "section": "secrets"}],
        ["read_character_profile_section", {"artifact_id": PROFILE, "expected_content_sha256": DIGEST,
                                            "section": "portrayal"}],
        ["read_character_profile_section", {"artifact_id": PROFILE, "expected_content_sha256": DIGEST,
                                            "section": "identity", "entry_id": ""}],
        ["open_character_profile", {"artifact_id": PROFILE, "expected_content_sha256": DIGEST,
                                    "destination_path": "profiles/open.json"}],
        ["open_character_profile", {"artifact_id": PROFILE, "expected_content_sha256": DIGEST,
                                    "destination_path": "profiles/open.json"}],
        ["open_character_profile", {"artifact_id": "character-profile-bad", "expected_content_sha256": DIGEST,
                                    "destination_path": "profiles/x.json"}],
        ["open_character_profile", {"artifact_id": PROFILE, "expected_content_sha256": DIGEST,
                                    "destination_path": "../x.json"}],
        ["check_character_profile_draft", {"path": "profiles/wren.json"}],
        ["check_character_profile_draft", {"path": "profiles/bad.json"}],
        ["check_character_profile_draft", {"path": "profiles/missing.json"}],
        ["publish_character_profile", {"path": "profiles/wren.json", "expected_version": DIGEST}],
        ["publish_character_profile", {"path": "profiles/wren.json", "expected_version": file_version(profile_text)}],
        ["publish_character_profile", {"path": "profiles/wren.json", "expected_version": file_version(profile_text)}],
        ["preview_character_card_import", {}],
        ["import_character_card", {"path": "card.json"}],
        ["read_character_profile_section", {"artifact_id": PROFILE, "expected_content_sha256": DIGEST,
                                            "section": "identity"}],
    ]
    # One outcome per callback call, in order: eight section reads, three profile opens (one
    # refused), two saves (one refused), and a last read that fails on storage.
    profile_outcomes = [{"return": profile}] * 9 + [
        {"raise": {"class": "CommandRejected", "message": "Profile is gone", "code": "character_profile_missing"}},
        {"return": profile},
        {"return": {"artifact_id": PROFILE, "content_sha256": DIGEST}},
        {"raise": {"class": "CommandRejected", "message": "Library full", "code": "library_full"}},
        {"raise": {"class": "OSError", "message": "disk"}},
    ]
    result.append({"tool": "profile", "files": profile_files, "calls": profile_calls, "outcomes": profile_outcomes})

    # The content definition tool.
    definition_calls = [{}, {"name": "character_creation"}, {"name": "character_profile_v2"},
                        {"name": "character_profile_v2", "section": "/requirements"},
                        {"name": "character_profile_v2", "section": "/requirements/0"},
                        {"name": "character_profile_v2", "section": "/requirements/99"},
                        {"name": "character_profile_v2", "section": "/requirements/x"},
                        {"name": "character_profile_v2", "section": "/requirements/" + chr(0x661)},
                        {"name": "character_profile_v2", "section": "requirements"},
                        {"name": "character_profile_v2", "section": 5},
                        {"name": "character_creation", "section": "/declarations/0/schema_id"},
                        {"name": "character_creation", "section": "/setting_data"},
                        {"section": "/x"}, {"name": "unknown"}, {"name": 5}, {"extra": 1},
                        {"name": "character_profile_v2", "section": "/schema/properties/a~1b"}]
    for declarations, world in (([], None), ([srd.schema_id, hound.schema_id], world)):
        result.append({"tool": "content_definition", "declarations": declarations,
                       "setting_character_schema": world, "calls": definition_calls})
    return result, {srd.schema_id: declaration_fields(srd), hound.schema_id: declaration_fields(hound)}


# ── Building and running the tools ────────────────────────────────────────────────────────


def as_declaration(fields):
    return ParticipantArtifactDeclaration(
        kind=fields["kind"], schema_id=fields["schema_id"], schema_sha256=fields["schema_sha256"],
        schema=fields["schema"], entity_state_schema=fields["entity_state_schema"],
        initial_state_calculations=tuple(fields["initial_state_calculations"]),
        initial_state_validation=fields["initial_state_validation"])


def run(compact, named):
    case = expand(compact)
    script = Script(case.get("outcomes", ()))
    kind = case["tool"]
    with tempfile.TemporaryDirectory() as scratch:
        workspace = Path(scratch)
        write_files(workspace, case.get("files", {}))
        declarations = tuple(named[name] for name in case.get("declarations", ()))
        schemas = {name: named[name].schema for name in case.get("schemas", ())}
        try:
            if kind == "artifact":
                tools = [_participant_artifact_tool(role=case["role"], publisher=publisher(script), schemas=schemas,
                                                    checking=case["checking"])]
            elif kind == "draft_check":
                tools = [_participant_draft_check_tool(
                    case["role"], declarations, case["setting_character_schema"],
                    source_resolver(script) if case["resolver"] else None)]
            elif kind == "file":
                inline = (_participant_draft_check_tool(case["role"], declarations) if case["checking"]
                          else _participant_artifact_tool(role=case["role"], publisher=publisher(script),
                                                          schemas=schemas))
                tools = [participant_file_tool(inline, workspace, _participant_entity_state)]
            elif kind == "character_draft":
                tools = [character_draft_tool(reader(script, "read"), workspace, tuple(case["schema_ids"]))]
            elif kind == "reuse":
                tools = [reuse_character_copy_tool(reader(script, "reuse"), tuple(case["schema_ids"]))]
            elif kind == "profile":
                save, read = profile_callbacks(script)
                tools = list(character_profile_tools(workspace, save=save, read=read))
            else:
                tools = [content_definition_tool(declarations, case["setting_character_schema"])]
        except Exception as error:  # noqa: BLE001 - recorded
            return {**compact, "construction": {"raised": type(error).__name__, "message": str(error)}}
        by_name = {tool.name: tool for tool in tools}
        answers = []
        for params in case["calls"]:
            if kind == "profile":
                name, params = params
                answers.append({"tool": name, **call(by_name[name], params)})
            else:
                answers.append(call(tools[0], params))
        return {**compact, "definitions": [tool_spec_to_openai(tool) for tool in tools], "answers": answers,
                "callback_calls": script.calls, "files_after": read_files(workspace)}


def plain(value):
    """Frozen mappings and tuples as the dicts and lists JSON writes, keys in order."""
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [plain(item) for item in value]
    return value


def main() -> None:
    raw, declarations = cases()
    named = {fields["schema_id"]: as_declaration(fields) for fields in declarations.values()}
    output = plain({
        "python": ".".join(sys.version.split()[0].split(".")[:2]),
        "declarations": declarations,
        "cases": [run(case, named) for case in raw],
    })
    OUTPUT.write_text(json.dumps(output, ensure_ascii=True, indent=1) + NL, encoding="utf-8", newline=NL)
    print(OUTPUT.name, len(output["cases"]), "cases")


if __name__ == "__main__":
    main()
