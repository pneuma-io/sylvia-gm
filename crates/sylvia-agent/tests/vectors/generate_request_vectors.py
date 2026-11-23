"""Generate reference vectors for one provider round's request helpers (T2c).

Run from the repository root with the snapshot's interpreter:

    .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_request_vectors.py

Writes `request_vectors.json`, every value read back from the Python functions:

- `serialized`: `_contains_serialized_tool_call`;
- `forced`: `_forced_single_tool_call_from_json` (its `uuid4().hex` replaced by a numbered one);
- `native_text`: `_native_content_for_text_operation`;
- `legacy_exchange`: `_legacy_tool_exchange`;
- `chat_metadata`: `attach_chat_metadata`, every refusal included;
- `reasoning`: `requires_reasoning_with_tools`;
- `request_messages`: the adapter's `_request_messages` per route;
- `native`: the adapter's `_attach_native_continuation` against a scripted owner, with the
  owner calls it made (in order, with results);
- `adoption`: the adapter's `_legacy_adopted_history` against a scripted owner, with its calls.

Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.agent import _provider_session as session  # noqa: E402
from sylvia_gm.agent._native_continuation import (  # noqa: E402
    attach_chat_metadata,
    requires_reasoning_with_tools,
)
from sylvia_gm.agent._stateless_mcp import ModelMechanicsTool  # noqa: E402
from sylvia_gm.domain.authority import PackageLock, PackageLockEntry, PackageRole  # noqa: E402
from sylvia_gm.domain.errors import CommandRejected  # noqa: E402
from sylvia_gm.domain.package_library import (  # noqa: E402
    PackageLockAvailability,
    PackageLockRecord,
)
from sylvia_gm.domain.session import SessionRecord, SessionStatus  # noqa: E402
from sylvia_gm.domain.authority import WriterRole  # noqa: E402
from sylvia_gm.provider._base import ModelInfo  # noqa: E402

OUTPUT = Path(__file__).with_name("request_vectors.json")


def plain(value: object) -> object:
    import enum
    from collections.abc import Mapping
    if isinstance(value, enum.Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {"__class__": type(value).__name__,
                **{f.name: plain(getattr(value, f.name)) for f in dataclasses.fields(value)}}
    if isinstance(value, Mapping):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


class Ids:
    def __init__(self) -> None:
        self.count = 0

    def uuid4(self) -> SimpleNamespace:
        self.count += 1
        return SimpleNamespace(hex=f"id{self.count:030d}")


def outcome(function):  # type: ignore[no-untyped-def]
    try:
        return {"result": plain(function())}
    except CommandRejected as error:
        return {"error": {"code": error.code, "message": str(error)}}
    except (KeyError, ValueError, TypeError) as error:
        return {"error": {"class": type(error).__name__, "message": str(error)}}


def tool(name: str) -> ModelMechanicsTool:
    return ModelMechanicsTool(package_id="p", package_version="1", package_sha256="0" * 64,
                              name=name, capability="c", input_schema={})


def model(transport: str = "chat_completions", key: str = "openai_compat",
          wire: str = "standin-narrator") -> ModelInfo:
    return ModelInfo(id=f"{key}/{wire}", name=wire, provider_name=key, provider_key=key,
                     provider_mode="api", transport=transport, provider_model_id=wire)


def model_facts(value: ModelInfo) -> dict[str, object]:
    return {"id": value.id, "name": value.name, "provider_name": value.provider_name,
            "provider_key": value.provider_key, "provider_mode": value.provider_mode,
            "transport": value.transport, "provider_model_id": value.provider_model_id,
            "context_window": value.context_window, "max_output_tokens": value.max_output_tokens}


def serialized_cases() -> list[dict[str, object]]:
    texts = ["plain prose", "]<]MiniMax[>[<tool_call x", "<tool_call>", "<TOOL_CALL><Invoke name=x>",
             "<invoke name=x> only", ""]
    return [{"text": text, "result": session._contains_serialized_tool_call(text)}  # noqa: SLF001
            for text in texts]


def forced_cases() -> list[dict[str, object]]:
    cases = []
    for aliases in ([], ["tool_a_roll"], ["tool_a_roll", "tool_b_end"]):
        for text in ['{"b": 2, "a": "ü"}', '  {"x": [1, 2.5]}\n', "[1]", "not json", '"s"', "{}"]:
            ids = Ids()
            session.uuid = ids  # type: ignore[assignment]
            result = session._forced_single_tool_call_from_json(  # noqa: SLF001
                text, {alias: tool(alias) for alias in aliases})
            cases.append({"aliases": aliases, "text": text, "ids_drawn": ids.count,
                          "result": plain(result)})
    return cases


def native_text_cases() -> list[dict[str, object]]:
    assembly = session._ToolCallAssembly(call_id="call-1", name="tool_a", arguments='{"x":1}')  # noqa: SLF001
    block_sets = [
        None, [], [{"type": "text", "text": '{"x":1}'}],
        [{"type": "thinking", "thinking": "t"}, {"type": "text", "text": '{"x":'},
         {"type": "text", "text": "1}"}],
        [{"type": "text", "text": "other"}], [{"type": "tool_use", "id": "z"}, {"type": "text", "text": '{"x":1}'}],
        [{"type": "redacted_thinking", "data": "d"}, {"type": "text", "text": '{"x":1}'}],
    ]
    return [{"blocks": blocks, "text": '{"x":1}',
             "result": session._native_content_for_text_operation(  # noqa: SLF001
                 blocks, '{"x":1}', assembly)} for blocks in block_sets]


def legacy_exchange_cases() -> list[dict[str, object]]:
    calls = [{"id": "c1", "type": "function", "function": {"name": "old_roll", "arguments": "{}"}},
             {"id": "c2", "type": "function", "function": {"name": "kept", "arguments": "{\"a\":1}"}},
             {"id": "c3", "type": "function", "function": {"name": "retired", "arguments": "{}"}}]
    results = [{"role": "tool", "tool_call_id": "c1", "name": "old_roll", "content": "{\"ok\":1}"},
               {"role": "tool", "tool_call_id": "c2", "content": "{}"},
               {"role": "tool", "tool_call_id": "c3", "content": "{\"gone\":\"ü\"}"}]
    cases = []
    for content in ("Checking.", "", None):
        assistant = {"role": "assistant", "content": content, "tool_calls": calls,
                     "_native_reference": {"response_id": "r"}}
        changes = {"old_roll": "new_roll", "retired": None}
        cases.append({"assistant": assistant, "calls": calls, "results": results, "changes": changes,
                      "result": session._legacy_tool_exchange(  # noqa: SLF001
                          assistant, calls, results, changes)})
    return cases


def chat_metadata_cases() -> list[dict[str, object]]:
    calls = [{"id": "c1", "function": {"name": "roll", "arguments": "{\"n\": 1}"}}]
    message = {"role": "assistant", "content": "x", "reasoning_content": "old", "tool_calls": calls}
    metadata_sets: list[tuple[object, bool]] = [
        (None, False), (None, True), ("bad", False),
        ({"reasoning_fields": {"reasoning": "r"}}, False),
        ({"reasoning_fields": {"other": "r"}}, False), ({"reasoning_fields": {"reasoning": 1}}, False),
        ({"chat_content": [{"type": "text"}]}, False), ({"chat_content": ["x"]}, False),
        ({"responses_output": [{"type": "function_call", "call_id": "c1", "name": "roll",
                                "arguments": "{\"n\": 1.0}"}]}, False),
        ({"responses_output": [{"type": "function_call", "call_id": "c2", "name": "roll",
                                "arguments": "{}"}]}, False),
        ({"responses_output": "x"}, False),
        ({"gemini_parts": [{"functionCall": {"name": "roll", "args": {"n": 1}}}],
          "gemini_call_ids": ["c1"]}, False),
        ({"gemini_parts": [{"functionCall": {"name": "roll", "args": {"n": 2}}}],
          "gemini_call_ids": ["c1"]}, False),
        ({"gemini_parts": [], "gemini_call_ids": ["zz"]}, False),
        ({"reasoning_details": [{"d": 1}]}, False), ({"reasoning_details": "x"}, False),
        ({"tool_call_extras": {"c1": {"thought_signature": "s"}}}, False),
        ({"tool_call_extras": {"c9": {}}}, False), ({"tool_call_extras": []}, False),
    ]
    return [{"message": message, "metadata": metadata, "required": required,
             "outcome": outcome(lambda m=metadata, r=required: attach_chat_metadata(
                 message, m, required=r))} for metadata, required in metadata_sets]


def reasoning_cases() -> list[dict[str, object]]:
    cases = []
    for transport, wire in [("chat_completions", "deepseek-chat"), ("chat_completions", "org/Kimi-K3-x"),
                            ("chat_completions", "glm-4.6"), ("chat_completions", "kimi-k2.7-code"),
                            ("chat_completions", "gpt-5"), ("responses", "deepseek-chat")]:
        for has_tools in (True, False):
            value = model(transport, wire=wire)
            cases.append({"model": model_facts(value), "has_tools": has_tools,
                          "result": requires_reasoning_with_tools(value, has_tools=has_tools)})
    cases.append({"model": None, "has_tools": True, "result": requires_reasoning_with_tools(None)})
    return cases


class Owner:
    """A scripted owner: each method answers from `answers` and logs the call."""

    def __init__(self, answers: dict[str, list[object]]) -> None:
        self.answers = {name: list(values) for name, values in answers.items()}
        self.calls: list[dict[str, object]] = []

    def _answer(self, name: str, args: list[object], kwargs: dict[str, object]) -> object:
        value = self.answers[name].pop(0)
        call: dict[str, object] = {"event": "call", "name": name, "args": plain(args),
                                   "kwargs": plain(kwargs)}
        if isinstance(value, BaseException):
            call["raised"] = {"class": type(value).__name__, "message": str(value),
                              "code": getattr(value, "code", None)}
            self.calls.append(call)
            raise value
        call["result"] = plain(value)
        self.calls.append(call)
        return value

    def __getattr__(self, name: str) -> object:
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *args, **kwargs: self._answer(name, list(args), kwargs)


def adapter(owner: Owner, value: ModelInfo) -> session.ProviderSessionAdapter:
    provider = SimpleNamespace(model_info=value)
    return session.ProviderSessionAdapter(owner, provider, owner_id="owner")  # type: ignore[arg-type]


def session_record(campaign: str | None = "campaign-1") -> SessionRecord:
    return SessionRecord(session_id="session-1", campaign_id=campaign, role=WriterRole.DM,
                         provider_id="prov", model_id="mod", status=SessionStatus.ACTIVE)


def request_message_cases() -> list[dict[str, object]]:
    lists = [
        [{"role": "system", "content": "SYS"}, {"role": "user", "content": "Hello"}],
        [{"role": "system", "content": "SYS"},
         {"role": "system", "content": "SYLVIA_GM_CURRENT_PLAYER_MESSAGE (application-owned control, x)"},
         {"role": "user", "content": "SYLVIA_CONTINUATION_NOTE (note)", "_application_data": True},
         {"role": "user", "content": "Go north ü"},
         {"role": "system", "content": "SYLVIA_GM_FRESH_CAMPAIGN_STATE (data):\n{\"a\":1}"}],
        [{"role": "system", "content": request_guidance()}, {"role": "user", "content": "x"}],
        [],
    ]
    cases = []
    for key in ("chatgpt_subscription", "openrouter", "openai_compat"):
        value = model(key=key)
        for messages in lists:
            cases.append({"model": model_facts(value), "messages": messages,
                          "outcome": outcome(lambda m=messages, v=value: adapter(Owner({}), v)
                                             ._request_messages(m))})  # noqa: SLF001
    return cases


def request_guidance() -> str:
    from sylvia_gm.agent._request_data import APPLICATION_DATA_GUIDANCE
    return APPLICATION_DATA_GUIDANCE


def native_cases() -> list[dict[str, object]]:
    owner_ctx = {"kind": "gm", "campaign_id": "campaign-1"}
    reference = {"response_id": "resp-1", "requirement": {
        "chat_completion_required": True, "provider_id": "prov", "model_id": "mod"}}
    tool_calls = [{"id": "c1", "type": "function", "function": {"name": "roll", "arguments": "{}"}}]
    messages = [
        {"role": "system", "content": "S", "_context_owner": {"x": 1}},
        {"role": "assistant", "content": "a", "reasoning_content": "r", "tool_calls": tool_calls,
         "_native_reference": reference},
        {"role": "tool", "tool_call_id": "c1", "content": "{}"},
        {"role": "assistant", "content": "plain", "_native_reference": "not a mapping"},
    ]
    native = {"context_owner": {**owner_ctx, "session_id": "session-1"},
              "chat_completion_metadata": {"reasoning_details": [{"d": 1}]},
              "anthropic_content": [{"type": "tool_use", "id": "c1"}, {"type": "text", "text": "a"}],
              "reasoning_content": "saved reasoning"}
    scenarios = [
        ("chat route with metadata", model(), True, {"provider_continuation": [native],
                                                     "model_context_owner": [owner_ctx]}),
        ("chat route, continuation missing", model(), True,
         {"provider_continuation": [None], "model_context_owner": [owner_ctx]}),
        ("another context owner", model(), True,
         {"provider_continuation": [{**native, "context_owner": {"other": 1}}],
          "model_context_owner": [owner_ctx]}),
        ("DeepSeek resends reasoning", model(wire="deepseek-chat"), True,
         {"provider_continuation": [{**native, "chat_completion_metadata": {}}],
          "model_context_owner": [None]}),
        ("Anthropic tool exchange", model("anthropic_messages", key="anthropic_api", wire="claude-x"), True,
         {"provider_continuation": [native], "model_context_owner": [owner_ctx]}),
        ("Anthropic mismatched tools", model("anthropic_messages", key="anthropic_api", wire="claude-x"),
         True, {"provider_continuation": [{**native, "anthropic_content": [{"type": "tool_use", "id": "zz"}]}],
                "model_context_owner": [None]}),
        ("no tools, plain route", model(), False, {"provider_continuation": [native],
                                                   "model_context_owner": [owner_ctx]}),
    ]
    cases = []
    for name, value, has_tools, answers in scenarios:
        owner = Owner(answers)
        provider_input = SimpleNamespace(session=session_record())
        result = outcome(lambda o=owner, v=value, h=has_tools: adapter(o, v)._attach_native_continuation(  # noqa: SLF001
            [dict(message) for message in messages], provider_input, has_tools=h))
        cases.append({"name": name, "model": model_facts(value), "has_tools": has_tools,
                      "messages": messages, "outcome": result, "calls": owner.calls})
    return cases


def lock(lock_id: str, entries: tuple[PackageLockEntry, ...], predecessor: str | None) -> PackageLockRecord:
    return PackageLockRecord(lock_id=lock_id, campaign_id="campaign-1", closure_sha256="0" * 64,
                             package_lock=PackageLock(entries),
                             availability=PackageLockAvailability.AVAILABLE,
                             predecessor_lock_id=predecessor, created_revision=1)


def entry(version: str) -> PackageLockEntry:
    return PackageLockEntry(package_id="sylvia.test", version=version, sha256=version * 64,
                            role=PackageRole.PRIMARY, load_order=0, dependencies=(), capabilities=())


def adoption_cases() -> list[dict[str, object]]:
    old, new = entry("1"), entry("2")
    old_alias = session._package_tool_alias("sylvia.test", "1", "1" * 64, "roll", "dice")  # noqa: SLF001
    gone_alias = session._package_tool_alias("sylvia.test", "1", "1" * 64, "gone", "dice")  # noqa: SLF001
    calls = [{"id": "c1", "type": "function", "function": {"name": old_alias, "arguments": "{}"}},
             {"id": "c2", "type": "function", "function": {"name": gone_alias, "arguments": "{}"}}]
    messages = [
        {"role": "system", "content": "S"},
        {"role": "assistant", "content": "Rolling.", "tool_calls": calls, "_native_reference": {"response_id": "r"}},
        {"role": "tool", "tool_call_id": "c1", "content": "{\"total\": 3}"},
        {"role": "tool", "tool_call_id": "c2", "content": "{}"},
        {"role": "user", "content": "next"},
    ]
    current = lock("lock-2", (new,), "lock-1")
    lineage = [lock("lock-1", (old,), None), current]
    old_record = {"tools": [{"name": "roll", "capability": "dice"}, {"name": "gone", "capability": "dice"}]}
    new_record = {"tools": [{"name": "roll", "capability": "dice"}]}
    scenarios = [
        ("remapped and retired", messages, {"current_package_lock": [current],
                                            "package_lock_lineage": [lineage],
                                            "package_record": [new_record, old_record]}),
        ("first lock", messages, {"current_package_lock": [lock("lock-1", (old,), None)]}),
        ("nothing saved", messages[:1] + messages[4:], {}),
        ("predecessor unavailable", messages, {
            "current_package_lock": [current], "package_lock_lineage": [lineage],
            "package_record": [new_record, CommandRejected("gone", code="package_unavailable")]}),
    ]
    cases = []
    for name, history, answers in scenarios:
        owner = Owner(answers)
        result = outcome(lambda o=owner, h=history: adapter(o, model())._legacy_adopted_history(  # noqa: SLF001
            [dict(message) for message in h], "campaign-1"))
        cases.append({"name": name, "messages": history, "outcome": result, "calls": owner.calls})
    return cases


def main() -> None:
    vectors = {
        "serialized": serialized_cases(),
        "forced": forced_cases(),
        "native_text": native_text_cases(),
        "legacy_exchange": legacy_exchange_cases(),
        "chat_metadata": chat_metadata_cases(),
        "reasoning": reasoning_cases(),
        "request_messages": request_message_cases(),
        "native": native_cases(),
        "adoption": adoption_cases(),
    }
    text = json.dumps(vectors, ensure_ascii=False, indent=1, allow_nan=False, default=repr) + "\n"
    OUTPUT.write_bytes(text.encode("utf-8"))
    print({key: len(value) for key, value in vectors.items()}, len(text), "bytes")
    for case in vectors["native"] + vectors["adoption"]:
        print(" ", case["name"], "->", case["outcome"].get("error", "ok"), len(case["calls"]), "calls")


if __name__ == "__main__":
    main()
