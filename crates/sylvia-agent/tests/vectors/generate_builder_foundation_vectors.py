"""Generate reference vectors for the Builder foundations (B1).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_builder_foundation_vectors.py

Every case calls the real Python function. Records:
- `modes`: `AgentMode` values;
- `skills`: `builder_skill_references` and `load_builder_skill` (the text's SHA-256 or the error)
  for every skill (keyed `""`) and reference, an unknown skill, an unknown reference and
  `SKILL.md` asked for as a reference;
- `titles`: `opening_title`, and `FirstReplyTitle.feed` over chunk sequences;
- `questions`: `builder_question_details` over saved contents, and
  `builder_question_answer_content` over contexts, answers and texts;
- `histories`: `_provider_history` over contexts (with and without an excluded message);
- `coalesce`: `_coalesce_builder_responses` over raw histories;
- `native`: `_builder_native_request` over messages, transports, retained native data, the
  reasoning resend and provider identities;
- `prompts`: `_creation_subflow_purpose`, `_effective_tracked_skill` and `_builder_prompt`
  (its SHA-256 and length) over contexts;
- `evidence`: `_tool_call_details` and `_builder_tool_evidence` over runner messages.

A context is `{"session": <SessionRecord fields>, "messages": [[kind, message_id, content]]}`;
sequences count from 1. No case holds NaN: Rust's JSON cannot, and tool results are the
runner's own JSON. Errors are `[class, message, code]`, `code` being `None` for a built-in
exception. This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.agent import _builder_question_answers as questions  # noqa: E402
from sylvia_gm.agent import _builder_session as session  # noqa: E402
from sylvia_gm.agent import _builder_title as title  # noqa: E402
from sylvia_gm.agent import _skills as skills  # noqa: E402
from sylvia_gm.agent._mode import AgentMode  # noqa: E402
from sylvia_gm.domain.authority import WriterRole, thaw_json_value  # noqa: E402
from sylvia_gm.domain.session import (  # noqa: E402
    SessionContext,
    SessionMessage,
    SessionMessageKind,
    SessionRecord,
)

OUTPUT = HERE / "builder_foundation_vectors.json"
NL = chr(10)


def error_of(error: BaseException) -> list:
    return [type(error).__name__, str(error), getattr(error, "code", None)]


def run(call):
    try:
        return ["ok", call()]
    except Exception as error:  # every class is behaviour
        return error_of(error)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


STANDALONE = {"session_id": "builder-chat", "campaign_id": None, "role": "builder",
              "provider_id": "provider", "model_id": "model", "selected_skill": None}


def bound(skill: str) -> dict:
    return {**STANDALONE, "session_id": "builder-bound", "campaign_id": "campaign-one", "selected_skill": skill}


DM = {**STANDALONE, "session_id": "dm-session", "campaign_id": "campaign-one", "role": "dm"}


def context_of(spec: dict) -> SessionContext:
    fields = dict(spec["session"])
    record = SessionRecord(**{**fields, "role": WriterRole(fields["role"])})
    return SessionContext(session=record, messages=tuple(
        SessionMessage(session_id=record.session_id, sequence=index + 1, message_id=message_id,
                       kind=SessionMessageKind(kind), content=content)
        for index, (kind, message_id, content) in enumerate(spec["messages"])))


def ctx(session_fields: dict, *messages) -> dict:
    return {"session": session_fields, "messages": [list(message) for message in messages]}


def ask(widget_type="select", options=("Red", "Blue"), *, call_id="call-1", status="awaiting_answer",
        question="Pick a colour?", widget_id="colour", success=True, extra=None):
    data = {"id": widget_id, "question": question}
    if options is not None:
        data["options"] = list(options) if isinstance(options, (list, tuple)) else options
    result = {"success": success, "status": status, "widget": {"type": widget_type, "data": data}}
    content = {"kind": "builder_tool_result", "tool_name": "ask_question", "tool_call_id": call_id,
               "arguments": "{}", "result": result}
    if extra:
        content.update(extra)
    return content


def tool_result(name="read_file", call_id="call-r", arguments='{"path": "a.md"}', result=None, **extra):
    return {"kind": "builder_tool_result", "tool_name": name, "tool_call_id": call_id, "arguments": arguments,
            "result": {"success": True, "content": "text"} if result is None else result, **extra}


def skill_cases() -> list:
    cases = []
    for name in [*skills.builder_skills(), "BUILD NOTHING"]:
        references = run(lambda: list(skills.builder_skill_references(name)))
        loads = {"": run(lambda: digest(skills.load_builder_skill(name).text))}
        for reference in (references[1] if references[0] == "ok" else []) + ["references/missing.md", "SKILL.md"]:
            loads[reference] = run(lambda: digest(skills.load_builder_skill(name, reference=reference).text))
        cases.append({"name": name, "references": references, "loads": loads})
    return cases


def title_cases() -> dict:
    openings = ["Short", "  many   spaces\tand\nlines  ", "x" * 56, "x" * 57,
                "A long opening sentence that keeps going well beyond the title limit, yes.",
                "Words, words, words; words: more words and still more words past the limit",
                "\x1cinfo separators\x1f around", "Ünïcödé wörds " * 6, "", "   "]
    feeds = [
        [["<!-- sylvia-chat-title: A Good Title -->\nHello there", True]],
        [["<!-- sylvia-", False], ["chat-title: Split", False], [" Title -->", False], ["\n\nBody", False], ["", True]],
        [["Plain answer", True]],
        [["<", False], ["!-", False], ["x", False], ["more", True]],
        [["  <!-- sylvia-chat-title: " + "y" * 60 + " -->Body", True]],
        [["<!-- sylvia-chat-title: bad <b> -->Body", True]],
        [["<!-- sylvia-chat-title:    -->Body", True]],
        [["<!-- sylvia-chat-title: never closed\nAnswer line", False], ["", True]],
        [["<!-- sylvia-chat-title: never closed", True]],
        [["<!-- sylvia-chat-title: " + "z" * 300, False]],
        [["", False], ["", True]],
        [["<!-- sylvia-chat-title: T -->", False], ["\r\n ", False], ["  Body", False], [" more", True]],
        [["\n\n<!-- sylvia-chat-title: Lead -->X", True]],
    ]
    feed_cases = []
    for chunks in feeds:
        stripper = title.FirstReplyTitle()
        outputs = [stripper.feed(text, final=final) for text, final in chunks]
        feed_cases.append({"chunks": chunks, "outputs": outputs, "title": stripper.title})
    return {"openings": [[text, title.opening_title(text)] for text in openings], "feeds": feed_cases}


def question_cases() -> dict:
    contents = [
        ask(), ask("text", None), ask("confirm", None), ask("confirm", []), ask("confirm", ["Sure"]),
        ask("cascade", [{"value": "v1", "label": "One"}, {"value": "", "label": "x"}, "Two", "", 5]),
        ask("select", []), ask("select", "Red"), ask("select", {"a": 1}), ask("select", [5, None]),
        ask("radio"), ask(success=1), ask(status="answered"), ask(call_id=""), ask(question="   "),
        ask(widget_id=5), {**ask(), "tool_name": "other"}, {**ask(), "kind": "other"},
        {**ask(), "result": "x"}, {**ask(), "tool_call_id": None},
        ask("text", ["ignored"]), ask(question="\x1c"),
    ]
    details = [[content, run(lambda: questions.builder_question_details(content))] for content in contents]
    form = [ask(call_id="call-1", widget_id="colour"),
            ask("text", None, call_id="call-2", widget_id="name", question="Your name?"),
            ask("select", [{"value": "a", "label": "Same"}, {"value": "b", "label": "Same"}],
                call_id="call-3", widget_id="dup", question="Which one?")]
    messages = [["user", "u-1", {"text": "start"}], ["evidence", "q-old", ask(call_id="call-0")],
                ["user", "u-2", {"text": "go"}]] + [["evidence", f"q-{i}", item] for i, item in enumerate(form, 1)]
    base = ctx(STANDALONE, *messages)

    def answers_text(rows):
        return "Here are my answers:" + NL + NL + (NL + NL).join(
            f"{i}. {q}{NL}Answer: {label}" for i, (q, label) in enumerate(rows, 1))

    good = [{"question_message_id": "q-1", "value": "Red", "custom": False},
            {"question_message_id": "q-2", "value": "Ada", "custom": False},
            {"question_message_id": "q-3", "value": "b", "custom": False}]
    good_text = answers_text([("Pick a colour?", "Red"), ("Your name?", "Ada"), ("Which one?", "Same (b)")])

    def changed(index, **fields):
        rows = copy.deepcopy(good)
        rows[index].update(fields)
        return rows

    answer_cases = [
        ("good", base, good, good_text),
        ("custom_choice", base, changed(0, value="Green", custom=True),
         answers_text([("Pick a colour?", "Green"), ("Your name?", "Ada"), ("Which one?", "Same (b)")])),
        ("label_value_same", base, changed(2, value="a"),
         answers_text([("Pick a colour?", "Red"), ("Your name?", "Ada"), ("Which one?", "Same (a)")])),
        ("wrong_text", base, good, good_text + " "),
        ("dm_session", ctx(DM, *messages), good, good_text),
        ("not_list", base, {"a": 1}, good_text),
        ("empty", base, [], good_text),
        ("extra_field", base, changed(0, extra=1), good_text),
        ("not_mapping", base, ["x"], good_text),
        ("stale", base, changed(0, question_message_id="q-old"), good_text),
        ("duplicate", base, [good[0], good[0], good[2]], good_text),
        ("blank_value", base, changed(1, value="  "), good_text),
        ("custom_not_bool", base, changed(1, custom=0), good_text),
        ("text_custom", base, changed(1, custom=True), good_text),
        ("not_offered", base, changed(0, value="Green"), good_text),
        ("missing_one", base, good[:2], good_text),
        ("wrong_order", base, [good[1], good[0], good[2]], good_text),
        ("number_identity", base, changed(0, question_message_id=5), good_text),
    ]
    answers = []
    for name, spec, rows, text in answer_cases:
        outcome = run(lambda: thaw_json_value(questions.builder_question_answer_content(context_of(spec), rows, text)))
        answers.append({"name": name, "context": spec, "answers": rows, "text": text, "outcome": outcome})
    return {"details": details, "answers": answers}


LAUNCH = {"kind": "builder_launch_context", "purpose": "plan", "interview_step": "participants",
          "references": {"candidate_id": "cand", "packages": [{"id": "p", "prefix": "ruleset"}],
                         "plan": {"id": "plan-1", "payload": {"big": True}, "readable_markdown": "# Plan", "title": "T"},
                         "character_examples": [{"path": "character-examples/a.json", "draft": {"x": 1}, "name": "A"}],
                         "character_profile": {"artifact_id": "prof", "content_sha256": "0" * 64, "profile": {"n": 1}}}}


def history_specs() -> list:
    native = {"provider_id": "provider", "model_id": "model", "reasoning_required": True}
    return [
        ("empty", ctx(STANDALONE)),
        ("conversation", ctx(STANDALONE, ["user", "u-1", {"text": "Hello"}],
                             ["talk", "t-1", {"text": "Hi", "provider_response_id": "resp-1"}],
                             ["steering", "s-1", {"text": "Also"}],
                             ["talk", "t-2", {"text": "Ünïcode", "native_continuation": native}],
                             ["user", "u-2", {"text": 5}], ["user", "u-3", {}], ["user", "u-4", {"text": None}])),
        ("tool_pairs", ctx(STANDALONE, ["user", "u-1", {"text": "Read"}],
                           ["evidence", "e-1", tool_result(provider_response_id="resp-2")],
                           ["evidence", "e-2", tool_result(call_id="call-s", name="search_files",
                                                           result={"matches": [{"path": "é", "content": "x"}]},
                                                           provider_response_id="resp-2", native_continuation=native)],
                           ["talk", "t-1", {"text": "Done", "provider_response_id": "resp-2"}],
                           ["talk", "t-2", {"text": "Other", "provider_response_id": "resp-3"}],
                           ["evidence", "e-3", tool_result(call_id="call-x", result=[1, 2.5, None, "ü"])])),
        ("skipped", ctx({**bound("BUILD CAMPAIGN PLAN")}, ["evidence", "c-1", {"kind": "working_context_checkpoint", "x": 1}],
                        ["evidence", "c-2", {"kind": "context_compaction_requested"}],
                        ["evidence", "c-3", {"kind": "context_compaction_terminal"}],
                        ["evidence", "c-4", {"kind": "builder_title_preference", "title": "T"}],
                        ["evidence", "c-5", {"kind": "builder_attempt_terminal", "status": "completed"}],
                        ["evidence", "c-6", {"kind": "builder_attempt_terminal", "status": "failed"}],
                        ["evidence", "c-7", {"kind": "campaign_construction_brief", "premise": "Ünï", "n": [1, {"b": 2, "a": 1}]}],
                        ["safe_think", "c-8", {"text": "thinking"}],
                        ["human_review", "c-9", {"note": "ok"}])),
        ("subflow_bound", ctx(bound("BUILD CAMPAIGN SETTING"),
                              ["evidence", "l-1", {"kind": "creation_subflow_link", "purpose": "companion",
                                                   "interview_transcript": [{"role": "assistant", "content": "Q?"},
                                                                            {"role": "user", "content": 7},
                                                                            {"role": "user"}]}],
                              ["evidence", "l-2", {"kind": "creation_subflow_link", "interview_transcript": "text"}])),
        ("subflow_standalone", ctx(STANDALONE, ["evidence", "l-1", {"kind": "creation_subflow_link",
                                                                   "interview_transcript": [{"role": "x"}]}])),
        ("subflow_bad_role", ctx(bound("BUILD RULESET"), ["evidence", "l-1", {"kind": "creation_subflow_link",
                                                                             "interview_transcript": [{"role": "system"}]}])),
        ("subflow_bad_item", ctx(bound("BUILD RULESET"), ["evidence", "l-1", {"kind": "creation_subflow_link",
                                                                             "interview_transcript": ["x"]}])),
        ("subflow_list_role", ctx(bound("BUILD RULESET"), ["evidence", "l-1", {"kind": "creation_subflow_link",
                                                                              "interview_transcript": [{"role": ["user"]}]}])),
        ("launch", ctx(STANDALONE, ["evidence", "l-1", LAUNCH])),
        ("launch_plain", ctx(STANDALONE, ["evidence", "l-1", {**LAUNCH, "references": {"candidate_id": "c"}}])),
        ("launch_not_mapping", ctx(STANDALONE, ["evidence", "l-1", {**LAUNCH, "references": ["x"]}])),
        ("launch_examples_text", ctx(STANDALONE, ["evidence", "l-1", {**LAUNCH, "references": {"character_examples": "ab"}}])),
        ("launch_examples_none", ctx(STANDALONE, ["evidence", "l-1", {**LAUNCH, "references": {"character_examples": None}}])),
        ("launch_examples_list", ctx(STANDALONE, ["evidence", "l-1", {**LAUNCH, "references": {"character_examples": [[1]]}}])),
        ("bad_tool", ctx(STANDALONE, ["evidence", "e-1", {**tool_result(), "arguments": {"a": 1}}])),
        ("tool_no_result", ctx(STANDALONE, ["evidence", "e-1", {k: v for k, v in tool_result().items() if k != "result"}])),
        ("tool_empty_id", ctx(STANDALONE, ["evidence", "e-1", tool_result(call_id="")])),
        ("kind_list", ctx(STANDALONE, ["user", "u-1", {"kind": ["x"], "text": "hi"}])),
        ("kind_mapping", ctx(STANDALONE, ["user", "u-1", {"kind": {"x": 1}, "text": "hi"}])),
    ]


def history_cases() -> list:
    cases = []
    for name, spec in history_specs():
        for exclude in (None, "t-1", "e-1"):
            outcome = run(lambda: session._provider_history(context_of(spec), exclude_message_id=exclude))
            cases.append({"name": name, "exclude": exclude, "context": spec, "outcome": outcome})
    return cases


def coalesce_cases() -> list:
    a = {"role": "assistant", "content": "Part one ", "provider_response_id": "r1"}
    call = {"role": "assistant", "content": None, "provider_response_id": "r1",
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]}
    tool = {"role": "tool", "tool_call_id": "c1", "content": "{}"}
    histories = [
        [a, call, tool, {**call, "tool_calls": [{"id": "c2"}]}, {"role": "tool", "tool_call_id": "c2", "content": "1"},
         {"role": "assistant", "content": "done", "provider_response_id": "r1", "native_continuation": {"n": 1}}],
        [a, {"role": "user", "content": "x"}, {**a, "content": "again"}],
        [{"role": "assistant", "content": "no id"}, {"role": "assistant", "content": "no id"}],
        [{**call, "native_continuation": {"first": 1}}, {**a, "native_continuation": {"second": 2}}],
        [{"role": "assistant", "content": "", "provider_response_id": "r2"},
         {"role": "assistant", "content": "", "provider_response_id": "r2", "tool_calls": []}],
        [{"role": "system", "content": "s"}, a, {"role": "system", "content": "s"}, a],
        [{"role": "assistant", "content": None, "provider_response_id": "r3"},
         {"role": "assistant", "content": "text", "provider_response_id": "r3"}],
    ]
    return [[history, session._coalesce_builder_responses(copy.deepcopy(history))] for history in histories]


def native_cases() -> list:
    calls = [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]
    origin = {"provider_id": "provider", "model_id": "model"}
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "x", "provider_response_id": "r1", "reasoning_content": "kept?",
         "native_continuation": {**origin, "reasoning_required": True}},
        {"role": "assistant", "content": None, "provider_response_id": "r2", "tool_calls": calls,
         "native_continuation": {**origin, "anthropic_required": True, "chat_completion_required": True}},
        {"role": "tool", "tool_call_id": "c1", "content": "{}"},
        {"role": "assistant", "content": "y", "provider_response_id": 5},
        {"role": "assistant", "content": "z", "native_continuation": "text"},
    ]
    natives = {
        "full": {"r1": {"reasoning_content": "thought"},
                 "r2": {"reasoning_content": "t2", "anthropic_content": [{"type": "thinking"}, {"type": "tool_use", "id": "c1"}],
                        "chat_completion_metadata": None}},
        "mismatch": {"r2": {"anthropic_content": [{"type": "tool_use", "id": "other"}]}},
        "not_list": {"r2": {"anthropic_content": "blocks"}},
        "none": {},
        "reasoning_number": {"r1": {"reasoning_content": 5}},
    }
    cases = []
    for transport in (None, "anthropic_messages", "chat_completions", "responses", "gemini_generate_content", "ollama"):
        for native_name, native in natives.items():
            for resend in (False, True):
                for identity in (None, ["provider", "model"], ["provider", "other"]):
                    for has_read in (True, False):
                        read = (lambda rid, native=native: native.get(rid)) if has_read else None
                        outcome = run(lambda: session._builder_native_request(
                            copy.deepcopy(messages), transport=transport, read=read, resend_reasoning=resend,
                            provider_identity=None if identity is None else tuple(identity)))
                        cases.append({"transport": transport, "native": native_name, "resend": resend,
                                      "identity": identity, "read": has_read, "outcome": outcome})
    return {"messages": messages, "natives": natives, "cases": cases}


def prompt_cases() -> list:
    specs = [
        ("standalone", ctx(STANDALONE)),
        ("ruleset", ctx(bound("BUILD RULESET"))),
        ("setting", ctx(bound("BUILD CAMPAIGN SETTING"))),
        ("plan", ctx(bound("BUILD CAMPAIGN PLAN"))),
        ("subflow", ctx(bound("BUILD CAMPAIGN SETTING"), ["evidence", "l-1", {"kind": "creation_subflow_link"}])),
        ("subflow_companion", ctx(bound("BUILD CAMPAIGN SETTING"),
                                  ["evidence", "l-1", {"kind": "creation_subflow_link", "purpose": "companion"}])),
        ("subflow_user_kind", ctx(bound("BUILD CAMPAIGN SETTING"), ["user", "l-1", {"kind": "creation_subflow_link"}])),
        ("launch_player", ctx(STANDALONE, ["evidence", "l-1", {"kind": "builder_launch_context", "purpose": "player_character"}])),
        ("launch_required", ctx(STANDALONE, ["evidence", "l-1", LAUNCH])),
        ("launch_user_kind", ctx(STANDALONE, ["user", "l-1", {"kind": "builder_launch_context", "purpose": "plan",
                                                              "interview_step": "participants"}])),
        ("purpose_empty", ctx(STANDALONE, ["evidence", "l-1", {"kind": "builder_launch_context", "purpose": ""}],
                              ["evidence", "l-2", {"kind": "builder_launch_context", "purpose": "companion"}])),
        ("purpose_number", ctx(STANDALONE, ["evidence", "l-1", {"kind": "builder_launch_context", "purpose": 5}])),
        ("repair", ctx(STANDALONE, ["evidence", "r-1", {"kind": "import_repair_context"}])),
        ("everything", ctx(bound("BUILD CAMPAIGN PLAN"),
                           ["evidence", "l-1", {"kind": "creation_subflow_link", "purpose": "plan"}],
                           ["evidence", "l-2", LAUNCH], ["evidence", "r-1", {"kind": "import_repair_context"}])),
    ]
    cases = []
    for name, spec in specs:
        context = context_of(spec)
        prompt = session._builder_prompt(context)
        cases.append({"name": name, "context": spec, "purpose": session._creation_subflow_purpose(context),
                      "skill": session._effective_tracked_skill(context), "sha256": digest(prompt),
                      "length": len(prompt)})
    return cases


def evidence_cases() -> list:
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": None, "provider_response_id": "r1", "reasoning_content": "secret",
         "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"a"}'}},
                        {"id": "c2", "function": {"name": "search_files", "arguments": "{}"}},
                        {"id": 5, "function": {"name": "x", "arguments": "{}"}},
                        {"id": "c3", "function": {"name": "x", "arguments": {"a": 1}}},
                        {"id": "c4", "function": "f"}, "not a call"]},
        {"role": "assistant", "content": "", "provider_response_id": "", "tool_calls": [
            {"id": "c5", "function": {"name": "write_file", "arguments": "{}"}}]},
        {"role": "assistant", "tool_calls": "x"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "function": {"name": "read_file", "arguments": "{}"}}]},
    ]
    tool_messages = [
        {"role": "tool", "tool_call_id": "c1", "content": '{"success": true, "content": "file text", "path": "a"}'},
        {"role": "tool", "tool_call_id": "c2", "content": '{"matches": [{"path": "a", "content": "x"}, 3]}'},
        {"role": "tool", "tool_call_id": "c5", "content": "not json"},
        {"role": "tool", "tool_call_id": "c5", "content": "[1, 2.5e3, -0.0]"},
        {"role": "tool", "tool_call_id": "c5"},
        {"role": "tool", "tool_call_id": "c5", "content": None},
        {"role": "tool", "tool_call_id": "c5", "content": {"already": "parsed"}},
        {"role": "tool", "tool_call_id": "missing", "content": "{}"},
        {"role": "tool", "tool_call_id": 5, "content": "{}"},
    ]
    details = session._tool_call_details(copy.deepcopy(messages))
    return {"messages": messages, "details": details,
            "tools": [[message, run(lambda: session._builder_tool_evidence(copy.deepcopy(message), details))]
                      for message in tool_messages],
            "durable": [[name, value, session._durable_tool_result(name, copy.deepcopy(value))] for name, value in (
                ("read_file", {"content": "abc", "path": "p"}), ("read_file", {"content": 5}),
                ("search_files", {"matches": [{"content": "x", "path": "a"}, "y"]}), ("search_files", {"matches": "x"}),
                ("write_file", {"content": "kept"}), ("read_file", ["not", "a", "dict"]))]}


def main() -> None:
    vectors = {
        "python": ".".join(sys.version.split()[0].split(".")[:2]),
        "modes": [mode.value for mode in AgentMode],
        "skills": skill_cases(),
        "titles": title_cases(),
        "questions": question_cases(),
        "histories": history_cases(),
        "coalesce": coalesce_cases(),
        "native": native_cases(),
        "prompts": prompt_cases(),
        "evidence": evidence_cases(),
    }
    OUTPUT.write_text(json.dumps(vectors, ensure_ascii=True, separators=(",", ":"), allow_nan=True) + NL,
                      encoding="utf-8", newline=NL)
    print(OUTPUT.name, OUTPUT.stat().st_size, "bytes")
    for group in ("histories",):
        for case in vectors[group]:
            if case["outcome"][0] != "ok":
                print(group, case["name"], case["exclude"], case["outcome"][:2])
    for case in vectors["questions"]["answers"]:
        print("answer", case["name"], case["outcome"][:2] if case["outcome"][0] != "ok" else "ok")


if __name__ == "__main__":
    main()
