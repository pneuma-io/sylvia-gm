"""Generate reference vectors for one canonical Builder turn (B3a): `stream_builder_turn`.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_builder_turn_vectors.py

Each scenario runs the real `stream_builder_turn` generator over a saved session context, with a
scripted provider (each request answers with the next scripted response), a real
`ProviderControl`, and stand-ins for the owner's collaborators: their presence decides which tools
the chat offers, and the scenarios call only tools already ported (the skill loader, the
workspace tools, `ask_question` and `read_session_history`). `uuid4` is a counter. Records: every
provider event, every request (messages, tool names, options), the full tool definitions of
the first request, the result and the outcome.

Reference tooling for the port; retired with the Python source.
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from sylvia_gm.agent import _runner, _working_context  # noqa: E402
from sylvia_gm.agent import _builder_session as session  # noqa: E402
from sylvia_gm.agent._character_profile_tools import character_profile_tools  # noqa: E402
from sylvia_gm.agent._session_loop import ProviderControl  # noqa: E402
from sylvia_gm.domain.authority import WriterRole, thaw_json_value  # noqa: E402
from sylvia_gm.domain.session import (  # noqa: E402
    SessionContext,
    SessionMessage,
    SessionMessageKind,
    SessionRecord,
    SessionStatus,
)
from sylvia_gm.provider._base import ModelInfo, ProviderStreamChunk  # noqa: E402
from sylvia_gm.provider._hooks import HookManager  # noqa: E402
from generate_character_tool_vectors import as_declaration, companion_declaration, declaration_fields, plain  # noqa: E402

OUTPUT = HERE / "builder_turn_vectors.json"
NL = chr(10)
BS = chr(92)
STOP = {"finish_reason": "stop"}


def call(index, call_id, name, arguments):
    return {"tool_call_delta": {"index": index, "id": call_id, "function": {"name": name, "arguments": arguments}}}


STANDALONE = {"session_id": "builder-chat", "campaign_id": None, "role": "builder", "provider_id": "provider",
              "model_id": "model", "selected_skill": None, "status": "active"}


def bound(skill):
    return {**STANDALONE, "session_id": "builder-bound", "campaign_id": "campaign-one", "selected_skill": skill}


def stub(*_args, **_kwargs):
    return {}


class Anything:
    def __getattr__(self, _name):
        return stub


def scenarios():
    yield "standalone_text", {"session": STANDALONE, "messages": [], "user_text": "Hello Builder",
                              "tools": ["publish_ruleset_candidate", "campaign_content", "library_sources",
                                        "profile_tools"]}, [
        [{"content": "Hello, "}, {"content": "what shall we build?"}, STOP],
    ]
    yield "skill_and_files", {"session": bound("BUILD RULESET"), "messages": [
        ["user", "m-1", {"text": "Earlier"}], ["talk", "m-2", {"text": "Earlier reply"}]],
        "user_text": "Load the skill and write notes", "tools": ["publish_ruleset_candidate"]}, [
        [call(0, "c1", "load_builder_skill", json.dumps({"name": "BUILD RULESET"})), STOP],
        [call(0, "c2", "write_file", json.dumps({"path": "notes.md", "content": "Notes"})),
         call(1, "c3", "read_file", json.dumps({"path": "notes.md"})), STOP],
        [call(0, "c4", "load_builder_skill", json.dumps({"name": "NOPE"})),
         call(1, "c5", "load_builder_skill", json.dumps({"name": "BUILD CAMPAIGN PLAN", "reference": "references/variations.md"})),
         call(2, "c6", "load_builder_skill", json.dumps({"name": "BUILD RULESET", "reference": "missing.md"})), STOP],
        [{"content": "Done."}, STOP],
    ]
    yield "history_tool", {"session": STANDALONE, "messages": [["user", "m-1", {"text": "Old"}]],
                           "user_text": "Read history", "tools": []}, [
        [call(0, "h1", "read_session_history", json.dumps({"direction": "older"})), STOP],
        [{"content": "Read."}, STOP],
    ]
    yield "title", {"session": STANDALONE, "messages": [], "user_text": "A long first request " * 4,
                    "request_title": True, "tools": []}, [
        [{"content": "<!-- sylvia-chat-title: Short "}, {"content": "Title -->\nAnswer text"}, STOP],
    ]
    yield "steering", {"session": STANDALONE, "messages": [], "user_text": "Work", "tools": [],
                       "steer_before": ["Also do this"], "allow_public_web": True}, [
        [call(0, "s1", "write_file", json.dumps({"path": "a.md", "content": "A"})), STOP],
        [{"content": "First pass."}, STOP],
    ]
    yield "steering_after", {"session": STANDALONE, "messages": [], "user_text": "Work", "tools": [],
                             "steer_after_first": ["Change it"], "allow_public_web": True}, [
        [{"content": "First."}, STOP],
        [{"content": "Second."}, STOP],
    ]
    yield "application_start", {"session": STANDALONE, "messages": [
        ["evidence", "l-1", {"kind": "builder_launch_context", "purpose": "plan", "interview_step": "participants",
                             "references": {"candidate_id": None}}]],
        "user_text": "", "application_start": True, "tools": ["references"]}, [
        [{"content": "Starting from the launch."}, STOP],
    ]
    yield "subflow", {"session": bound("BUILD CAMPAIGN SETTING"), "messages": [
        ["evidence", "l-1", {"kind": "creation_subflow_link", "purpose": "setting"}]],
        "user_text": "Build the setting", "tools": ["publish_campaign_setting", "check_campaign_setting"]}, [
        [{"content": "Building."}, STOP],
    ]
    yield "plan_tools", {"session": STANDALONE, "messages": [], "user_text": "Plan", "tools": [
        "campaign_plan_authority", "check_campaign_plan", "publish_campaign_plan"]}, [
        [{"content": "Planning."}, STOP],
    ]
    hound = declaration_fields(companion_declaration())
    world = {"type": "object", "additionalProperties": False, "required": ["home"],
             "properties": {"home": {"type": "string", "minLength": 1}}}
    draft = {"summary": "A loyal hound.", "authored_profile": "# Rex", "participant": {"name": "Rex", "loyal": True},
             "entity_state": json.dumps({"bond": 2}), "setting_data": json.dumps({"home": "Kennel"})}
    yield "companion_subflow", {"session": bound("BUILD CAMPAIGN SETTING"), "messages": [
        ["evidence", "l-1", {"kind": "creation_subflow_link", "purpose": "companion"}]],
        "user_text": "Make my companion", "declarations": [hound], "setting_character_schema": world,
        "tools": ["publish_participant", "reuse_participant", "read_participant", "resolve_source_profile"]}, [
        [call(0, "c1", "get_content_definition", json.dumps({"name": "character_creation"})),
         call(1, "c2", "check_participant_draft", json.dumps(draft)), STOP],
        [{"content": "Rex is ready."}, STOP],
    ]
    yield "plan_characters", {"session": STANDALONE, "messages": [], "user_text": "Plan the party",
                              "plan_declarations": [hound], "tools": ["campaign_plan_authority"]}, [
        [call(0, "p1", "check_participant_draft", json.dumps({**draft, "role": "companion"})), STOP],
        [{"content": "Checked."}, STOP],
    ]
    yield "ask", {"session": STANDALONE, "messages": [], "user_text": "Ask me", "tools": []}, [
        [{"content": "Let me ask."}, call(0, "q1", "ask_question", json.dumps({"question": "Colour?", "options": ["Red"], "id": "c"})), STOP],
    ]
    yield "reasoning", {"session": STANDALONE, "messages": [], "user_text": "Think", "tools": []}, [
        [{"reasoning_content": "hmm"}, {"content": "Thought."}, STOP],
    ]
    yield "loop_stop", {"session": STANDALONE, "messages": [], "user_text": "Loop", "tools": []}, [
        [call(0, f"x{i}", "load_builder_skill", json.dumps({"name": "NOPE"})), STOP] for i in range(3)
    ]
    # A saved reply needs chat continuation metadata that is no longer retained.
    yield "missing_continuation", {"session": STANDALONE, "messages": [
        ["user", "m-1", {"text": "Earlier"}],
        ["talk", "m-2", {"text": "Earlier reply", "provider_response_id": "old-1",
                         "native_continuation": {"provider_id": "provider", "model_id": "model",
                                                 "chat_completion_required": True}}]],
        "user_text": "Continue", "tools": [], "model": {"transport": "chat_completions"}, "reader": "empty"}, [
        [{"content": "unreachable"}, STOP],
    ]
    yield "inactive", {"session": {**STANDALONE, "status": "stopped"}, "messages": [], "user_text": "Hi", "tools": []}, []
    yield "cancelled", {"session": STANDALONE, "messages": [], "user_text": "Hi", "tools": [], "cancel": True}, []


class Scripted:
    model_info = None

    def __init__(self, responses, after_first=None, model=None):
        if model is not None:
            self.model_info = ModelInfo(id="model", name="Model", provider_name="Provider", provider_key="provider",
                                        provider_mode="byok", transport=model["transport"])
        self.hooks = HookManager()
        self.responses = list(responses)
        self.requests = []
        self.after_first = after_first

    async def generate_stream(self, messages, tools=None, **kwargs):
        self.requests.append({"messages": copy.deepcopy(messages),
                              "tools": None if tools is None else [t["function"]["name"] for t in tools],
                              "options": copy.deepcopy(kwargs)})
        if not hasattr(self, "definitions"):
            self.definitions = copy.deepcopy(tools)
        for item in self.responses.pop(0):
            yield ProviderStreamChunk(**item)
        if self.after_first is not None:
            self.after_first()
            self.after_first = None

    async def close(self):
        pass


def context_of(spec):
    fields = dict(spec["session"])
    record = SessionRecord(**{**{k: v for k, v in fields.items() if k != "status"}, "role": WriterRole(fields["role"]),
                              "status": SessionStatus(fields["status"])})
    return SessionContext(session=record, messages=tuple(
        SessionMessage(session_id=record.session_id, sequence=index + 1, message_id=message_id,
                       kind=SessionMessageKind(kind), content=content)
        for index, (kind, message_id, content) in enumerate(spec["messages"])))


def event_of(event):
    return [event.kind.value, event.text, event.message_id,
            None if event.evidence is None else thaw_json_value(event.evidence), event.response_id]


def mask(value, roots):
    text = json.dumps(value, ensure_ascii=False)
    for root in roots:
        for form in (root.replace(BS, BS * 4), root.replace(BS, BS * 2), root.replace(BS, "/")):
            text = text.replace(form, "<ROOT>")
    return json.loads(text)


def run(name, spec, script):
    counter = {"n": 0}

    def fake_uuid():
        counter["n"] += 1
        return types.SimpleNamespace(hex=f"{counter['n']:032x}")

    _runner.uuid4 = fake_uuid
    _working_context.uuid4 = fake_uuid
    with tempfile.TemporaryDirectory() as scratch:
        workspace = Path(scratch) / "workspace"
        roots = [str(workspace.resolve()), str(workspace), str(Path(scratch).resolve()), scratch]
        control = ProviderControl()
        for text in spec.get("steer_before", []):
            control.add_steering(text)
        provider = Scripted(script, after_first=(
            (lambda: [control.add_steering(text) for text in spec["steer_after_first"]])
            if spec.get("steer_after_first") else None), model=spec.get("model"))
        if spec.get("cancel"):
            control.cancel()
        tools = set(spec["tools"])
        kwargs = {}
        if "publish_ruleset_candidate" in tools:
            kwargs["publish_ruleset_candidate"] = stub
        if "campaign_content" in tools:
            kwargs["campaign_content_authority"] = session.CampaignContentToolAuthority(items=())
        if "library_sources" in tools:
            kwargs["library_sources"] = Anything()
        if "references" in tools:
            kwargs["references"] = Anything()
        if "profile_tools" in tools:
            kwargs["profile_tools"] = character_profile_tools(workspace, save=stub, read=stub)
        if "publish_campaign_setting" in tools:
            kwargs["publish_campaign_setting"] = stub
        if "check_campaign_setting" in tools:
            kwargs["check_campaign_setting"] = stub
        if "campaign_plan_authority" in tools:
            plan = None
            if spec.get("plan_declarations"):
                plan = types.SimpleNamespace(
                    character_declarations=tuple(as_declaration(item) for item in spec["plan_declarations"]),
                    setting_character_schema=None)
            kwargs["campaign_plan_authority"] = lambda: plan
        if spec.get("declarations"):
            declared = tuple(as_declaration(item) for item in spec["declarations"])
            kwargs["participant_declarations"] = declared
            kwargs["participant_schemas"] = {item.schema_id: item.schema for item in declared}
            kwargs["setting_character_schema"] = spec.get("setting_character_schema")
        for callback in ("publish_participant", "reuse_participant", "read_participant", "resolve_source_profile"):
            if callback in tools:
                kwargs[callback] = (lambda *_a, **_k: None) if callback == "resolve_source_profile" else stub
        if "check_campaign_plan" in tools:
            kwargs["check_campaign_plan"] = stub
        if "publish_campaign_plan" in tools:
            kwargs["publish_campaign_plan"] = stub
        result = {}
        events = []
        try:
            for event in session.stream_builder_turn(
                provider, context=context_of(spec), message_id="msg-1", user_text=spec["user_text"],
                workspace=workspace, control=control, complete=lambda r: result.update(
                    {"tool_calls_made": list(r.tool_calls_made), "suggested_title": r.suggested_title}),
                request_title=spec.get("request_title", False), allow_public_web=spec.get("allow_public_web", False),
                continuation_reader=(lambda _response_id: None) if spec.get("reader") == "empty" else None,
                application_start=spec.get("application_start", False), **kwargs,
            ):
                events.append(event_of(event))
            outcome = ["ok"]
        except Exception as error:  # every class is behaviour
            outcome = [type(error).__name__, str(error), getattr(error, "code", None)]
        return {"name": name, "spec": spec, "script": script, "events": mask(events, roots),
                "requests": mask(provider.requests, roots),
                "definitions": mask(getattr(provider, "definitions", None), roots),
                "result": result or None, "outcome": outcome}


def main() -> None:
    with tempfile.TemporaryDirectory() as scratch:
        profile_names = [spec.name for spec in character_profile_tools(Path(scratch), save=stub, read=stub)]
    vectors = plain({"python": ".".join(sys.version.split()[0].split(".")[:2]), "profile_tool_names": profile_names,
                     "scenarios": [run(name, spec, script) for name, spec, script in scenarios()]})
    OUTPUT.write_text(json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + NL, encoding="utf-8",
                      newline=NL)
    print(OUTPUT.name, OUTPUT.stat().st_size, "bytes")
    for scenario in vectors["scenarios"]:
        print(scenario["name"], scenario["outcome"], len(scenario["events"]), "events", len(scenario["requests"]), "requests")


if __name__ == "__main__":
    main()
