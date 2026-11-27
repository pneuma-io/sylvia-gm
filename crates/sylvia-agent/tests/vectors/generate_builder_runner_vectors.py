"""Generate reference vectors for the Builder runner (B2b): paired runs of `AgentRunner.turn_stream`.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_builder_runner_vectors.py

Each scenario builds a real `AgentRunner` (BUILDER_FREEFORM, `history_path=None`) over a fresh
workspace, with a scripted provider: every request it receives answers with the next scripted
response, a list of `ProviderStreamChunk` fields or a raised provider error. `uuid4` is replaced
by a counter so response identities are fixed. Records, per turn: every request (messages, the
tool names, the keyword options), every stream event, the activity calls, the outcome (`ok` or
`[class, message]`), and the history afterwards. Scenario one also records the full tool
definitions. Construction refusals are recorded on their own.

A response may hang (`{"hang": true}`); the turn is then cancelled after 5 seconds, as Stop
cancels it, and its outcome is `TimeoutError`.

One file serves both platforms. The one platform path, a missing file's error in the `native`
scenario, ends in the platform's separator after `[workspace]`; the Rust test compares it with
`/` on both sides (`builder_tool_vectors` pins the separator per platform).
Reference tooling for the port; retired with the Python source.
"""

from __future__ import annotations

import asyncio
import copy
import json
import sys
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.agent import _runner  # noqa: E402
from sylvia_gm.agent._mode import AgentMode  # noqa: E402
from sylvia_gm.agent._runner import AgentRunner  # noqa: E402
from sylvia_gm.provider._base import ProviderStreamChunk  # noqa: E402
from sylvia_gm.provider._exceptions import (  # noqa: E402
    ProviderContinuationRequiredError,
    ProviderOutputLimitError,
)
from sylvia_gm.tools._spec import ToolSpec  # noqa: E402
from sylvia_gm.tools._toolsets import ToolsetName  # noqa: E402

OUTPUT = HERE / "builder_runner_vectors.json"
NL = chr(10)
BS = chr(92)


def call(index, call_id, name, arguments):
    delta = {"index": index, "function": {}}
    if call_id is not None:
        delta["id"] = call_id
    if name:
        delta["function"]["name"] = name
    if arguments:
        delta["function"]["arguments"] = arguments
    return {"tool_call_delta": delta}


def args(value):
    return json.dumps(value)


STOP = {"finish_reason": "stop"}

# Application tools: each answers from its own list of results, the last one repeating.
APPLICATION = {
    "steady": [{"success": True, "note": "same"}],
    "flaky": [{"success": False, "error": "flaky failed"}],
    "status_tool": [{"status": "rejected", "detail": 1}, {"success": True}],
    "inspect_builder_references": [
        {"success": False, "code": "reference_unavailable"},
        {"success": False, "code": "reference_unavailable"},
        {"success": False, "code": "reference_unavailable"},
    ],
    "copy_builder_reference": [{"success": False, "reason": "package_corrupt"}],
    "test_ruleset_draft": [{"success": True, "tests": 2}],
}


def scenarios():
    yield "text", {}, [
        ["Hello there", [[{"content": "Hel"}, {"content": "lo"}, STOP]]],
    ]
    yield "file_tools", {}, [
        ["Write and read", [
            [{"content": "Working"}, call(0, "c1", "write_file", '{"path": "notes.md", '),
             call(0, None, "", '"content": "One' + BS + 'nTwo"}'), call(1, "c2", "read_file", args({"path": "notes.md"})),
             STOP],
            [{"content": "Done."}, STOP],
        ]],
    ]
    yield "ask_then_answer", {}, [
        ["Ask me", [[call(0, "q1", "ask_question", args({"question": "Colour?", "options": ["Red", "Blue"], "id": "colour"})),
                     {"content": "hidden after ask"}, STOP]]],
        ["WIDGET_ANSWER|colour|Red", [[{"content": "Red it is."}, STOP]]],
        ["WIDGET_ANSWER|unknown|x", [[{"content": "Fine."}, STOP]]],
        ["WIDGET_ANSWER|short", [[{"content": "Short."}, STOP]]],
    ]
    yield "ask_among_others", {}, [
        ["Several", [[call(0, "w1", "write_file", args({"path": "a.md", "content": "A"})),
                      call(1, "q2", "ask_question", args({"type": "text", "question": "Name?", "id": 7})),
                      call(2, "r1", "read_file", args({"path": "a.md"})), STOP]]],
    ]
    yield "output_limit", {}, [
        ["Long", [[{"content": "part one "}, {"finish_reason": "length"}],
                  [call(0, "c9", "write_file", args({"path": "x.md", "content": "x"})), {"finish_reason": "max_tokens"}],
                  [{"content": "rest"}, STOP]]],
    ]
    yield "output_limit_exhausted", {}, [
        ["Longer", [[{"content": "a"}, {"finish_reason": "length"}]] * 4],
    ]
    yield "raised_output_limit", {}, [
        ["Raise", [[{"content": "partial"}, {"raise": "output_limit"}], [{"content": "after"}, STOP]]],
    ]
    yield "continuation_required", {}, [
        ["Continue", [[{"content": "partial"}, {"raise": "continuation"}]]],
    ]
    yield "native", {}, [
        ["Native", [[{"reasoning_content": "thinking "}, {"reasoning_content": "more"},
                     {"anthropic_content": [{"type": "thinking", "thinking": "t"}, {"type": "tool_use", "id": "n1"}]},
                     call(0, "n1", "read_file", args({"path": "missing.txt"})), STOP],
                    [{"reasoning_content": "final"}, {"content": "Answer"}, STOP]]],
    ]
    yield "steering", {"steer": ["Also check b.md", None]}, [
        ["Steer", [[call(0, "s1", "steady", args({"x": 1})), STOP],
                   [call(0, "s2", "steady", args({"x": 2})), STOP],
                   [{"content": "ok"}, STOP]]],
    ]
    yield "application_start", {"application_prompt": "Build the requested plan."}, [
        ["", [[{"content": "Starting."}, STOP]]],
    ]
    yield "application_start_with_text", {"application_prompt": "Build."}, [
        ["typed", []],
    ]
    yield "odd_arguments", {"allow_public_web": True}, [
        ["Odd", [[call(0, "o1", "read_file", "not json"), call(1, "o2", "ask_question", "[1, 2]"),
                  call(2, "o3", "write_file", "null"), call(3, None, "steady", ""),
                  call(4, "o5", "web_search", "[1]"), call(5, "o6", "web_search", '"text"'),
                  call(6, "o7", "no_such_tool", "{}"), call(7, "o8", "no_such_tool", "[1]"), STOP],
                 [{"content": "ok"}, STOP]]],
    ]
    yield "repeated_failure", {}, [
        ["Fail", [[call(0, f"f{i}", "flaky", args({"same": True})), STOP] for i in range(3)]],
    ]
    yield "reference_failure", {}, [
        ["Refs", [[call(0, "i1", "inspect_builder_references", args({"action": "read", "path": "ruleset/a.md"})), STOP],
                  [call(0, "i2", "inspect_builder_references", args({"action": "read", "path": "ruleset/a.md", "n": 2})), STOP],
                  [call(0, "i3", "inspect_builder_references", args({"action": "find", "path": "ruleset/a.md", "n": 3})),
                   call(1, "i4", "steady", args({"after": True})), STOP]]],
    ]
    yield "reference_corrupt", {}, [
        ["Copy", [[call(0, "k1", "copy_builder_reference", args({"source_path": "ruleset/big.md"})),
                   call(1, "k2", "steady", "{}"), STOP]]],
    ]
    yield "repeated_step", {}, [
        ["Loop", [[call(0, f"r{i}", "steady", args({"a": 1})), STOP] for i in range(6)]],
    ]
    yield "activity_retry", {}, [
        ["Retry", [[call(0, "t1", "status_tool", "{}"), STOP], [call(0, "t2", "status_tool", "{}"), STOP],
                   [call(0, "t3", "test_ruleset_draft", "{}"), STOP], [{"content": "done"}, STOP]]],
    ]
    yield "history", {"initial_history": [
        {"role": "user", "content": "Earlier"},
        {"role": "assistant", "content": "Earlier reply", "reasoning_content": "secret", "provider_response_id": "old"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "h1", "type": "function",
                                                              "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "h1", "content": "{}"},
    ]}, [
        ["Now", [[{"content": "Now."}, STOP]]],
    ]
    yield "untrusted_prompt", {"trusted": False, "override": "Please be brief."}, [
        ["Hi", [[{"content": "Hi."}, STOP]]],
    ]
    yield "empty_reply", {}, [
        ["Nothing", [[STOP]]],
    ]
    # A turn cancelled mid-stream (Stop): the history returns to what it was before the turn.
    yield "cancelled", {}, [
        ["First", [[{"content": "Kept."}, STOP]]],
        ["Second", [[call(0, "x1", "write_file", args({"path": "c.md", "content": "c"})), STOP],
                    [{"content": "partial"}, {"hang": True}]]],
        ["Third", [[{"content": "After."}, STOP]]],
    ]


class Scripted:
    model_info = None

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def generate_stream(self, messages, tools=None, **kwargs):
        self.requests.append({"messages": copy.deepcopy(messages),
                              "tools": None if tools is None else [t["function"]["name"] for t in tools],
                              "options": copy.deepcopy(kwargs)})
        if not hasattr(self, "definitions"):
            self.definitions = copy.deepcopy(tools)
        for item in self.responses.pop(0):
            if item.get("raise") == "output_limit":
                raise ProviderOutputLimitError("Output allowance reached")
            if item.get("hang"):
                await asyncio.sleep(3600)
            if item.get("raise") == "continuation":
                raise ProviderContinuationRequiredError("Continuation required", continuation_token="t",
                                                        native_parts=[])
            yield ProviderStreamChunk(**item)

    async def close(self):
        pass


def application_tools():
    tools = []
    for name, results in APPLICATION.items():
        state = {"results": list(results)}

        def handler(params, context, state=state):
            result = state["results"][0] if len(state["results"]) == 1 else state["results"].pop(0)
            return copy.deepcopy(result)

        tools.append(ToolSpec.from_openai_schema(
            {"type": "function", "function": {"name": name, "description": f"{name} tool",
                                              "parameters": {"type": "object", "properties": {}}}},
            handler=handler, toolsets=frozenset({ToolsetName.BUILDER_TOOLS})))
    return tools


def mask(value, roots):
    text = json.dumps(value, ensure_ascii=False)
    for root in roots:
        for form in (root.replace(BS, BS * 4), root.replace(BS, BS * 2), root.replace(BS, "/")):
            text = text.replace(form, "<ROOT>")
    return json.loads(text)


def run_scenario(name, options, turns):
    counter = {"n": 0}

    def fake_uuid():
        counter["n"] += 1
        return types.SimpleNamespace(hex=f"{counter['n']:032x}")

    _runner.uuid4 = fake_uuid
    with tempfile.TemporaryDirectory() as scratch:
        workspace = Path(scratch) / "workspace"
        workspace.mkdir()
        roots = [str(workspace.resolve()), str(workspace), str(Path(scratch).resolve()), scratch]
        responses = [response for _, script in turns for response in script]
        provider = Scripted(responses)
        activity = []
        trusted = options.get("trusted", True)
        runner = AgentRunner(
            mode=AgentMode.BUILDER_FREEFORM, provider=provider, history_path=None,
            initial_history=copy.deepcopy(options.get("initial_history", [])), project_root=workspace,
            ruleset_name="campaign", system_prompt_override=options.get("override", "You are the test Builder."),
            prompt_mode="replace", trusted_system_override=trusted, builder_application_tools=application_tools(),
            allow_public_web=options.get("allow_public_web", False),
            activity_progress=lambda phase, tool=None: activity.append([phase, tool]),
        )
        steer = list(options.get("steer", []))

        def between():
            return steer.pop(0) if steer else None

        recorded = []
        for text, _script in turns:
            before = len(provider.requests)
            queue: asyncio.Queue = asyncio.Queue()
            activity.clear()
            try:
                asyncio.run(asyncio.wait_for(runner.turn_stream(
                    text, queue, between_iterations=between if "steer" in options else None,
                    application_prompt=options.get("application_prompt")), timeout=5))
                outcome = ["ok"]
            except Exception as error:  # every class is behaviour
                outcome = [type(error).__name__, str(error)]
            events = []
            while not queue.empty():
                event = queue.get_nowait()
                events.append([event.type, event.data, event.meta])
            recorded.append({"text": text, "requests": mask(provider.requests[before:], roots), "events": mask(events, roots),
                             "activity": list(activity), "outcome": outcome,
                             "history": mask(runner.history_snapshot(), roots)})
        definitions = mask(getattr(provider, "definitions", None), roots)
    return {"name": name, "options": {key: value for key, value in options.items()}, "turns": recorded,
            "script": [script for _, script in turns], "definitions": definitions}


def construction():
    cases = []
    with tempfile.TemporaryDirectory() as scratch:
        for name, kwargs in (
            ("dm", {"mode": AgentMode.DM_SESSION}),
            ("collision", {"builder_application_tools": [ToolSpec.from_openai_schema(
                {"type": "function", "function": {"name": "read_file"}}, handler=lambda p, c: {},
                toolsets=frozenset({ToolsetName.BUILDER_TOOLS}))]}),
            ("toolset", {"builder_application_tools": [ToolSpec.from_openai_schema(
                {"type": "function", "function": {"name": "other"}}, handler=lambda p, c: {},
                toolsets=frozenset({ToolsetName.INTERVIEW_TOOLS}))]}),
            ("two_toolsets", {"builder_application_tools": [ToolSpec.from_openai_schema(
                {"type": "function", "function": {"name": "other"}}, handler=lambda p, c: {},
                toolsets=frozenset({ToolsetName.INTERVIEW_TOOLS, ToolsetName.BUILDER_TOOLS}))]}),
        ):
            arguments = {"mode": AgentMode.BUILDER_FREEFORM, "provider": Scripted([]), "history_path": None,
                         "initial_history": [], "project_root": Path(scratch), **kwargs}
            try:
                AgentRunner(**arguments)
                cases.append([name, ["ok"]])
            except Exception as error:  # every class is behaviour
                cases.append([name, [type(error).__name__, str(error)]])
    return cases


def main() -> None:
    vectors = {
        "python": ".".join(sys.version.split()[0].split(".")[:2]),
        "application": APPLICATION,
        "scenarios": [run_scenario(name, options, turns) for name, options, turns in scenarios()],
        "construction": construction(),
    }
    OUTPUT.write_text(json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + NL, encoding="utf-8",
                      newline=NL)
    print(OUTPUT.name, OUTPUT.stat().st_size, "bytes")
    for scenario in vectors["scenarios"]:
        print(scenario["name"], [turn["outcome"] for turn in scenario["turns"]])
    print(vectors["construction"])


if __name__ == "__main__":
    main()
