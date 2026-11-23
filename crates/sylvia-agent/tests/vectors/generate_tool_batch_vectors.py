"""Write tool_batch_vectors.json: Python's answers for the tool batch's helpers (T3a).

Run from the checkout root with the snapshot's interpreter:
    PYTHONHASHSEED=0 .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_tool_batch_vectors.py

Sections:
- `safe_error`: `_stateless_mcp._safe_error` on each kind of `AuthorityError`. Each case gives the
  error as the fields `_safe_error` reads (class, code, message and the `MechanicsRejected`
  fields), then the structured content and the text of the result.
- `helpers`: the tool batch's checks, refusals and settled results in `_provider_turn_phases.py`,
  each run on a built state with an adapter on a legacy route. A case holds the helper, its
  arguments, the turn locals before and after (`trace_turn_phases.dump_state`), the owner calls
  it made, the warnings it recorded and what it returned or raised. An SRD Lite tool's input
  schema is written as `{"__srd_input_schema__": <tool index>}`; the Rust test reads it from the
  package record, as this generator does.

Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
# The tracer's state dump and fixtures; no phase is traced (`SYLVIA_TURN_PHASES` is empty).
os.environ["SYLVIA_TURN_PHASES_TRACE"] = str(Path(tempfile.gettempdir()) / "tool-batch-unused.jsonl")
os.environ["SYLVIA_TURN_PHASES"] = ""

from trace_turn_phases import boundary, dump_state, plain, tool  # noqa: E402

from sylvia_gm.agent import _provider_session, _provider_turn_phases  # noqa: E402
from sylvia_gm.agent._provider_turn_state import ProviderTurnState  # noqa: E402
from sylvia_gm.agent._stateless_mcp import ModelMechanicsTool  # noqa: E402
from sylvia_gm.domain.authority import WriterRole  # noqa: E402

from sylvia_gm.agent._stateless_mcp import _safe_error  # noqa: E402
from sylvia_gm.domain.errors import (  # noqa: E402
    CommandRejected,
    IdempotencyConflict,
    MechanicsRejected,
    MechanicsValidationIssue,
    OwnerConflict,
    RulesetPreconditionRejected,
    StaleRevision,
    WrongOwner,
)


def described(error: Exception) -> dict:
    """The fields of `error` that `_safe_error` reads."""
    entry: dict = {"class": type(error).__name__,
                   "code": str(getattr(error, "code", "authority_rejected")),
                   "message": str(error)}
    if isinstance(error, MechanicsRejected):
        entry["mechanics"] = {
            "field_paths": list(error.field_paths),
            "validation_issues": [issue.as_payload() for issue in error.validation_issues],
            "state_changed": error.state_changed,
            "refusal_reasons": list(error.refusal_reasons),
        }
    return entry


def safe_error_cases() -> list[dict]:
    issues = (
        MechanicsValidationIssue(path="spell", kind="invalid_enum_value", expected_type="string",
                                 allowed_values=("fire_bolt", "shield")),
        MechanicsValidationIssue(path="targets", kind="too_long", parent_path="", maximum=3,
                                 actual_length=5, minimum=1),
    )
    errors: list[Exception] = [
        CommandRejected("Operation parameter 'target' is required; field 'mode' is unknown"),
        CommandRejected("Unknown parameter 'a.b-c' and parameter 'a.b-c' again, field '9x'"),
        CommandRejected("Search limit is invalid", code="campaign_knowledge_limit_invalid"),
        CommandRejected("Read is too large", code="campaign_reference_too_large"),
        CommandRejected("MCP tool is no longer available", code="mcp_tool_unavailable"),
        CommandRejected("Something else entirely", code="campaign_not_found"),
        StaleRevision("Expected revision 4, found 5"),
        WrongOwner("Not this owner"),
        IdempotencyConflict("Reused key"),
        OwnerConflict("Another owner holds the database"),
        MechanicsRejected("Arguments invalid", code="mechanics_validation_failed",
                          field_paths=("targets", "spell", "targets"), validation_issues=issues,
                          state_changed=False, refusal_reasons=("Only one reason",)),
        MechanicsRejected("Already moved", code="command_rejected", state_changed=True,
                          refusal_reasons=("First reason", "Second reason")),
        MechanicsRejected("Spell is not castable", code="spell_unavailable",
                          validation_issues=issues[:1]),
        RulesetPreconditionRejected("Missing input 'target_distance'",
                                    reasons=("The target must be within 30 feet",
                                             "Missing required parameter 'target_willing'")),
        RulesetPreconditionRejected("The caster has no spell slots left"),
    ]
    cases = []
    for error in errors:
        result = _safe_error(error)
        cases.append({"error": described(error), "structured": result.structured_content,
                      "text": result.content[0].text, "is_error": result.is_error})
    return cases


# --- helpers: the tool batch's checks, refusals and settled results, on built states ---------

class Owner:
    """The owner calls a helper makes, answered from `answers` and logged as the tracer does."""

    def __init__(self, answers: dict | None = None) -> None:
        self.calls: list[dict] = []
        self.answers = answers or {}

    def _call(self, name: str, args: list, result: object) -> object:
        self.calls.append({"event": "call", "name": name, "args": plain(args), "kwargs": {},
                           "result": plain(result)})
        return result

    def record_mechanics_diagnostic(self, **fields: object) -> None:
        # `AuthorityOwner.record_mechanics_diagnostic` builds this payload from its arguments.
        payload = {"kind": "mechanics_rejection", "privacy": "safe_metadata_only",
                   "session_id": fields["session_id"], "turn_id": fields["turn_id"],
                   "action_id": fields["action_id"], "tool_name": fields["tool_name"],
                   "rejection_code": fields["rejection_code"],
                   "validation_field_paths": list(fields["validation_field_paths"]),
                   "state_changed": fields["state_changed"]}
        self._call("record_mechanics_diagnostic", [payload], None)

    def campaign_preparation_boundary(self, campaign_id: str) -> object:
        return self._call("campaign_preparation_boundary", [campaign_id],
                          self.answers.get("campaign_preparation_boundary"))


class Adapter:
    """The parts of `ProviderSessionAdapter` the helpers read, on a legacy route."""

    _one_tool_list = False
    _fixed_tool_list = False
    _deferred_tool_list = None
    _owner_id = "owner"
    _filter_private_deliberation = _provider_session.ProviderSessionAdapter._filter_private_deliberation

    def __init__(self, owner: Owner) -> None:
        self._owner = owner
        self.warnings: list[str] = []
        self._record_warning = self.warnings.append

    def _ledger_active(self, provider_input: object) -> bool:
        return False


INPUT = SimpleNamespace(session=SimpleNamespace(session_id="session-1", role=WriterRole.DM))


def srd_tool(index: int) -> ModelMechanicsTool:
    record = json.loads((ROOT / "rulesets/srd-lite/package-record.json").read_text(encoding="utf-8"))
    raw = record["tools"][index]
    return ModelMechanicsTool(
        package_id="sylvia.srd-lite.bg3", package_version="0.3.29", package_sha256="0" * 64,
        name=raw["name"], capability=raw.get("capability", raw["name"]),
        input_schema=raw["input_schema"], description="")


def built(**values: object) -> ProviderTurnState:
    state = ProviderTurnState()
    for group in ("obligations", "progress", "decisions"):
        for key, value in values.pop(group, {}).items():  # type: ignore[union-attr]
            setattr(getattr(state, group), key, value)
    for key, value in values.items():
        setattr(state, key, value)
    return state


def assembly(arguments: str = "{}", name: str = "tool_a_x") -> object:
    return _provider_turn_phases._ToolCallAssembly(call_id="call-1", name=name, arguments=arguments)


def results(**values: object) -> dict:
    return {"local_tool_results": {}, "local_tool_failures": set(), **values}


HELD = {"buffered_narration": "Held prose.", "buffered_raw_response_blocks": None}
NOT_HELD = {"buffered_narration": None, "buffered_raw_response_blocks": None}


def helper_cases() -> list[dict]:
    phases = _provider_turn_phases
    x, y = tool("x"), tool("y")
    spell, outside = srd_tool(16), srd_tool(19)
    campaign_file = ModelMechanicsTool(
        package_id="sylvia.application", package_version="1", package_sha256="0" * 64,
        name="update_campaign_file", capability="narrative_state.update_campaign_file",
        input_schema={"type": "object", "properties": {}}, category="narrative_state")
    blocks = [{"kind": "narration", "text": "The player wants a fight.\nRain falls."},
              {"kind": "npc_utterance", "speaker_id": "anonymous", "spoken_words": "Halt!"}]
    decode_errors = {}

    def decode(text: str) -> object:
        try:
            json.loads(text or "{}")
        except json.JSONDecodeError as error:
            decode_errors[text] = error
            return error
        raise AssertionError(text)

    specs: list[tuple[str, str, dict, dict]] = [
        # (name, helper, locals, extra): extra holds "error" or owner "answers".
        ("unavailable tool", "reject_unavailable_tool",
         results(aliases={"tool_a_x": x, "tool_b_y": y}, assembly=assembly(name="tool_z")), {}),
        ("read selected arguments", "read_selected_arguments",
         results(aliases={"tool_a_begin_srd_spell_action": spell, "tool_b_x": x},
                 assembly=assembly('{"tool": "begin_srd_spell_action", "value": "aid"}')), {}),
        ("read selected arguments, unknown value", "read_selected_arguments",
         results(aliases={"tool_a_begin_srd_spell_action": spell},
                 assembly=assembly('{"tool": "tool_a_begin_srd_spell_action", "value": "nope"}')),
         {}),
        ("read selected arguments, bad JSON", "read_selected_arguments",
         results(aliases={"tool_a_begin_srd_spell_action": spell}, round_finish_reason="length",
                 assembly=assembly('{"tool": "x", ')), {}),
        ("read selected arguments, no contract tools", "read_selected_arguments",
         results(aliases={"tool_b_x": x}, assembly=assembly("{}")), {}),
        ("unwrap selected arguments, no contract", "unwrap_selected_arguments",
         results(catalog_tool=x, parameters={"a": 1}, assembly=assembly()), {}),
        ("unwrap selected arguments, flattened", "unwrap_selected_arguments",
         results(catalog_tool=outside, parameters={
             "spell": "aid", "caster_state": "p-1",
             "spell_arguments": {"material_component_available": True}}, assembly=assembly()), {}),
        ("unwrap selected arguments, refused", "unwrap_selected_arguments",
         results(catalog_tool=spell, parameters={
             "spell": "acid_arrow", "caster": "p-1",
             "spell_arguments": {"material_component_available": "yes", "caster": "p-2"}},
                 assembly=assembly()), {}),
        ("invalid JSON, no finish reason", "reject_invalid_tool_json",
         results(round_finish_reason=None, assembly=assembly('{"spell": "aid",\n "x": ]')),
         {"decode": True}),
        ("invalid JSON, length", "reject_invalid_tool_json",
         results(round_finish_reason="length", assembly=assembly("{\"a\": \"café")),
         {"decode": True}),
        ("missing fields, narrative state", "record_missing_tool_fields",
         results(catalog_tool=campaign_file, missing=["state_change", "summary"],
                 assembly=assembly()), {}),
        ("missing fields, mechanic", "record_missing_tool_fields",
         results(catalog_tool=x, missing=["target"], assembly=assembly()), {}),
        ("invalid revision", "reject_invalid_revision", results(assembly=assembly()), {}),
        ("Campaign File rejection, held", "record_campaign_file_rejection",
         results(assembly=assembly(), **HELD),
         {"error": CommandRejected("bad update", code="campaign_file_update_invalid")}),
        ("Campaign File rejection, mechanics", "record_campaign_file_rejection",
         results(assembly=assembly(), **NOT_HELD),
         {"error": MechanicsRejected(
             "inconsistent", code="campaign_file_disposition_inconsistent",
             field_paths=("memories", "state_change"), state_changed=False,
             validation_issues=(MechanicsValidationIssue(path="memories", kind="too_short",
                                                         minimum=1, actual_length=0),))}),
        ("Campaign File rejection, other code", "record_campaign_file_rejection",
         results(assembly=assembly(), **HELD),
         {"error": CommandRejected("pending", code="campaign_preparation_pending")}),
        ("duplicate collections", "restore_unchanged_narration",
         results(assembly=assembly(), mutation_attempted_before_call=True,
                 campaign_file_mutation_attempted=False), {}),
        ("unchanged, settled", "record_unchanged_campaign_file",
         results(assembly=assembly(), update={"state_change": "unchanged"},
                 settled_narration="Settled.", buffered_response_blocks=None,
                 buffered_narration=None), {}),
        ("unchanged, unsettled with blocks", "record_unchanged_campaign_file",
         results(assembly=assembly(), update={}, settled_narration=None,
                 buffered_response_blocks=({"kind": "narration", "text": "A"},),
                 buffered_narration=None), {}),
        ("updated, settled", "record_updated_campaign_file",
         results(assembly=assembly(), update={"state_change": "changed", "memories": [1]},
                 settled_narration="Settled.", buffered_response_blocks=None,
                 buffered_narration="Old."), {}),
        ("updated, unsettled", "record_updated_campaign_file",
         results(assembly=assembly(), update={}, settled_narration=None,
                 buffered_response_blocks=None, buffered_narration=None), {}),
        ("settle, held prose", "settle_campaign_file_narration",
         dict(buffered_narration="Held.", revised_raw_blocks=None, revised_narration=None,
              revised_narration_supplied=False, buffered_response_blocks=None,
              buffered_raw_response_blocks=None,
              parameters={"state_change": "changed", "memories": [], "scene": {"a": 1}}), {}),
        ("settle, revised prose", "settle_campaign_file_narration",
         dict(buffered_narration="Held.", revised_raw_blocks=None, revised_narration="Revised.",
              revised_narration_supplied=True, buffered_response_blocks=({"kind": "x"},),
              buffered_raw_response_blocks=({"kind": "y"},),
              parameters={"state_change": "unchanged", "state_change_summary": "s"}), {}),
        ("settle, revised blocks", "settle_campaign_file_narration",
         dict(buffered_narration=None, revised_raw_blocks=({"kind": "narration", "text": "B"},),
              revised_narration="", revised_narration_supplied=True,
              buffered_response_blocks=({"kind": "x"},), buffered_raw_response_blocks=None,
              parameters={"entities": [{"entity_id": "e-1"}], "memories": []}), {}),
        ("settle, nothing", "settle_campaign_file_narration",
         dict(buffered_narration=None, revised_raw_blocks=None, revised_narration=None,
              revised_narration_supplied=False, buffered_response_blocks=None,
              buffered_raw_response_blocks=None, parameters={}), {}),
        ("correction, held", "retry_rejected_tool_call",
         dict(rejected_tool_corrections_used=1, max_rejected_tool_corrections=5,
              current_application_controls=[], **HELD), {}),
        ("correction, nothing held", "retry_rejected_tool_call",
         dict(rejected_tool_corrections_used=0, max_rejected_tool_corrections=5,
              current_application_controls=[{"role": "system", "content": "earlier"}],
              **NOT_HELD), {}),
        ("revision, prose with deliberation", "prepare_campaign_file_revision",
         dict(campaign_id="campaign-1", buffered_raw_response_blocks=None, parameters={
             "revised_player_facing_response": "The player wants to leave.\nThe door opens.",
             "state_change": "unchanged"}), {}),
        ("revision, blocks", "prepare_campaign_file_revision",
         dict(campaign_id="campaign-1", buffered_raw_response_blocks=None,
              parameters={"revised_player_facing_response": blocks}),
         {"answers": {"campaign_preparation_boundary": boundary("player")}}),
        ("revision, only deliberation", "prepare_campaign_file_revision",
         dict(campaign_id="campaign-1", buffered_raw_response_blocks=None,
              parameters={"revised_player_facing_response": [
                  {"kind": "narration", "text": "The user wants more."}]}), {}),
        ("revision, invalid blocks", "prepare_campaign_file_revision",
         dict(campaign_id="campaign-1", buffered_raw_response_blocks=None,
              parameters={"revised_player_facing_response": [{"kind": "npc_dialogue",
                                                              "text": "x"}]}), {}),
        ("revision, absent", "prepare_campaign_file_revision",
         dict(campaign_id="campaign-1",
              buffered_raw_response_blocks=({"kind": "narration", "text": "Held."},),
              parameters={"state_change": "unchanged"}), {}),
        ("revision, not text", "prepare_campaign_file_revision",
         dict(campaign_id="campaign-1", buffered_raw_response_blocks=None,
              parameters={"revised_player_facing_response": 5}), {}),
    ]
    cases = []
    for name, helper, values, extra in specs:
        state = built(**values)
        owner = Owner(extra.get("answers"))
        adapter = Adapter(owner)
        before = dump_state(state)
        function = getattr(phases, helper)
        arguments: dict = {}
        try:
            if helper in {"reject_unavailable_tool", "read_selected_arguments",
                          "record_missing_tool_fields", "reject_invalid_revision",
                          "restore_unchanged_narration", "record_unchanged_campaign_file",
                          "record_updated_campaign_file", "settle_campaign_file_narration",
                          "retry_rejected_tool_call", "prepare_campaign_file_revision"}:
                returned = function(adapter, state, INPUT, turn_id="turn-1")
            elif helper == "unwrap_selected_arguments":
                returned = function(adapter, state)
            elif helper == "reject_invalid_tool_json":
                returned = function(adapter, state, INPUT, turn_id="turn-1",
                                    error=decode(state.assembly.arguments))
            elif helper == "record_campaign_file_rejection":
                error = extra["error"]
                arguments["error"] = described(error)
                returned = function(adapter, state, INPUT, turn_id="turn-1", error=error)
            else:
                raise AssertionError(helper)
            outcome: dict = {"returned": plain(returned)}
        except Exception as error:  # noqa: BLE001 - the error is the vector
            outcome = {"raised": {"class": type(error).__name__, "message": str(error)}}
        cases.append({"name": name, "helper": helper, "arguments": arguments,
                      "state": before, "calls": owner.calls, "warnings": adapter.warnings,
                      **outcome, "after": dump_state(state)})
    schemas = {json.dumps(srd_tool(index).input_schema): index for index in (16, 19)}

    def slim(value: object) -> object:
        if isinstance(value, dict):
            index = schemas.get(json.dumps(value))
            if index is not None:
                return {"__srd_input_schema__": index}
            return {key: slim(item) for key, item in value.items()}
        if isinstance(value, list):
            return [slim(item) for item in value]
        return value

    return [slim(case) for case in cases]


def main() -> None:
    vectors = {"safe_error": safe_error_cases(), "helpers": helper_cases()}
    out = Path(__file__).with_name("tool_batch_vectors.json")
    out.write_bytes((json.dumps(vectors, indent=1, ensure_ascii=False) + "\n").encode())
    print(f"wrote {out.name}: {sum(len(cases) for cases in vectors.values())} cases")


if __name__ == "__main__":
    main()
