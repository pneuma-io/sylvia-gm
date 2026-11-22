"""Generate reference vectors for P1: context fitting, the token estimator and turn reasoning.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-provider/tests/vectors/generate_context_fit_vectors.py

Writes `context_fit_vectors.json`:

- `fit`: a chain of Sylvia's wrappers (`ContextFittingProvider` from `_context_fitted_provider`,
  `TurnReasoningProvider`, `SessionBoundRequestMetadataProvider`) over a fake provider that may
  have `provider_request_representation`, `record_context_fit` and `set_audit_context`. Each
  case runs `admit_request`, `generate` or `generate_stream` and records the measurement or the
  error, every call the fake received (with its options), and every capability call it saw.
- `options`: `TurnReasoningProvider._options` for selections and call options.
- `bind`: `bind_session_reasoning` for models, roles, effort preferences and speeds.
- `estimate`: a `TokenEstimateHook` fed reported responses, then asked for estimates.

Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.interfaces.owner_process import _context_fitted_provider  # noqa: E402
from sylvia_gm.provider._base import (  # noqa: E402
    ModelInfo,
    Provider,
    ProviderResponse,
    ProviderStreamChunk,
    SpeedOption,
    TokenUsage,
)
from sylvia_gm.provider._exceptions import InvalidRequestError  # noqa: E402
from sylvia_gm.provider._hooks import HookContext  # noqa: E402
from sylvia_gm.provider._reasoning import (  # noqa: E402
    ReasoningSelection,
    TurnReasoningProvider,
    bind_session_reasoning,
)
from sylvia_gm.provider._request_metadata import SessionBoundRequestMetadataProvider  # noqa: E402
from sylvia_gm.provider._speed import SpeedSelection  # noqa: E402
from sylvia_gm.provider._token_estimate import TokenEstimateHook, token_estimator  # noqa: E402

OUTPUT = Path(__file__).with_name("context_fit_vectors.json")


def plain(value: object) -> object:
    return json.loads(json.dumps(value, ensure_ascii=False))


def model(spec: dict[str, Any]) -> ModelInfo:
    fields = dict(spec)
    if fields.get("speed_options") is not None:
        fields["speed_options"] = tuple(SpeedOption(**item) for item in fields["speed_options"])
    if fields.get("supported_reasoning_efforts") is not None:
        fields["supported_reasoning_efforts"] = tuple(fields["supported_reasoning_efforts"])
    return ModelInfo(**fields)


class Fake(Provider):
    def __init__(self, model_info: ModelInfo | None, flags: list[str], events: list) -> None:
        super().__init__()
        self.model_info = model_info
        self.calls: list[dict[str, object]] = []
        self._events = events
        if "representation" in flags or "representation_error" in flags:
            failing = "representation_error" in flags

            def representation(messages, tools, options):
                if failing:
                    raise InvalidRequestError("Fake representation refused.")
                return {"kind": "fake", "messages": messages, "tools": tools,
                        "options": dict(options)}
            self.provider_request_representation = representation
        if "recorder" in flags:
            self.record_context_fit = lambda context: events.append(["record", plain(context)])
        if "setter" in flags:
            self.set_audit_context = lambda context: events.append(["set", plain(context)])

    async def generate(self, messages, tools=None, stream=False, timeout=60.0, **kwargs):
        self.calls.append({"method": "generate", "stream": stream, "timeout": timeout,
                           "tools": tools, "options": plain(kwargs)})
        return ProviderResponse(content="ok")

    async def generate_stream(self, messages, tools=None, timeout=120.0, **kwargs):
        self.calls.append({"method": "generate_stream", "stream": True, "timeout": timeout,
                           "tools": tools, "options": plain(kwargs)})
        yield ProviderStreamChunk(content="ok")

    async def close(self) -> None:
        pass


def build(chain: list[dict[str, Any]], fake: Fake) -> Provider:
    provider: Provider = fake
    for layer in reversed(chain):
        if layer["kind"] == "session_bound":
            provider = SessionBoundRequestMetadataProvider(provider, headers=layer["headers"])
        elif layer["kind"] == "reasoning":
            speed = layer.get("speed")
            provider = TurnReasoningProvider(
                provider, ReasoningSelection(**layer["selection"]),
                SpeedSelection(**speed) if speed else None)
        elif layer["kind"] == "fit":
            provider = _context_fitted_provider(provider, gm_mode=layer["gm_mode"])
            if layer.get("audit"):
                provider.set_audit_context(layer["audit"])
    return provider


def failure(error: BaseException) -> dict[str, object]:
    return {"class": type(error).__name__, "message": str(error)}


async def run_fit(case: dict[str, Any]) -> dict[str, object]:
    events: list = []
    fake = Fake(model(case["model"]) if case.get("model") else None, case.get("flags", []), events)
    provider = build(case["chain"], fake)
    construction = list(events)
    events.clear()
    request = case["request"]
    outcome: dict[str, object]
    try:
        if case["operation"] == "admit":
            measured = provider.admit_request(request["messages"], request.get("tools"),
                                              **request.get("options", {}))
            outcome = {"measurement": measured.projection()}
        elif case["operation"] == "generate":
            await provider.generate(request["messages"], tools=request.get("tools"),
                                    stream=request.get("stream", False),
                                    timeout=request.get("timeout", 60.0),
                                    **request.get("options", {}))
            outcome = {"ok": True}
        else:
            chunks = [chunk.content async for chunk in provider.generate_stream(
                request["messages"], tools=request.get("tools"),
                timeout=request.get("timeout", 120.0), **request.get("options", {}))]
            outcome = {"chunks": chunks}
    except Exception as error:  # the TokenLimitError and the representation's refusal
        outcome = {"error": failure(error)}
    return {**case, "construction_events": construction, "outcome": plain(outcome),
            "calls": plain(fake.calls), "events": events}


MESSAGES = [{"role": "system", "content": "Sé breve."}, {"role": "user", "content": "Hola"}]
TOOLS = [{"type": "function", "function": {"name": "roll", "parameters": {"type": "object"}}}]
SELECTION = {"requested": "high", "effective": "high", "control": "openai",
             "transport": "chat_completions", "scope": "session"}
SPEED = {"requested": "priority", "effective": "priority", "control": "openai", "scope": "session"}


def base(**fields: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {"id": "test/model-a", "name": "Model A", "provider_name": "Test",
                            "provider_key": "test", "provider_mode": "byok",
                            "context_window": 32_000, "max_output_tokens": 4096}
    spec.update(fields)
    return spec


def fit_cases() -> list[dict[str, Any]]:
    fit = {"kind": "fit", "gm_mode": False}
    gm = {"kind": "fit", "gm_mode": True}
    bound = {"kind": "session_bound", "headers": {"x-opencode-session": "s-1"}}
    reasoning = {"kind": "reasoning", "selection": SELECTION, "speed": SPEED}
    plain_request = {"messages": MESSAGES, "tools": TOOLS}
    local = base(provider_mode="local", context_window=131_072, runtime_context_window=131_072,
                 runtime_context_source="lm_studio_loaded")
    cases: list[dict[str, Any]] = [
        {"name": "admit_neutral", "chain": [fit], "model": base(), "operation": "admit",
         "request": plain_request},
        {"name": "admit_no_tools", "chain": [fit], "model": base(), "operation": "admit",
         "request": {"messages": MESSAGES}},
        {"name": "admit_representation", "chain": [fit], "model": base(),
         "flags": ["representation"], "operation": "admit", "request": plain_request},
        {"name": "admit_representation_deep",
         "chain": [fit, reasoning, bound], "model": base(), "flags": ["representation"],
         "operation": "admit", "request": plain_request},
        {"name": "admit_bound_neutral", "chain": [fit, bound], "model": base(),
         "operation": "admit", "request": plain_request},
        {"name": "admit_reasoning_neutral", "chain": [fit, reasoning], "model": base(),
         "operation": "admit", "request": plain_request},
        {"name": "admit_representation_refused", "chain": [fit], "model": base(),
         "flags": ["representation_error"], "operation": "admit", "request": plain_request},
        {"name": "admit_no_model", "chain": [fit], "model": None, "operation": "admit",
         "request": plain_request},
        {"name": "admit_rejected", "chain": [fit], "model": base(context_window=4200),
         "operation": "admit", "request": plain_request},
        {"name": "admit_no_window", "chain": [fit], "model": base(context_window=None),
         "operation": "admit", "request": plain_request},
        {"name": "admit_zero_window", "chain": [fit], "model": base(context_window=0),
         "operation": "admit", "request": plain_request},
        {"name": "admit_reserve_too_big", "chain": [fit], "model": base(), "operation": "admit",
         "request": {**plain_request, "options": {"max_tokens": 9000}}},
        {"name": "admit_reserve_given", "chain": [fit], "model": base(), "operation": "admit",
         "request": {**plain_request, "options": {"max_tokens": 1000}}},
        {"name": "admit_reserve_output_key", "chain": [fit], "model": base(), "operation": "admit",
         "request": {**plain_request, "options": {"max_output_tokens": 2000}}},
        {"name": "admit_reserve_both", "chain": [fit], "model": base(), "operation": "admit",
         "request": {**plain_request, "options": {"max_tokens": None, "max_output_tokens": 2000}}},
        {"name": "admit_reserve_odd",
         "chain": [fit], "model": base(), "operation": "admit",
         "request": {**plain_request, "options": {"max_tokens": True}}},
        {"name": "admit_reserve_float", "chain": [fit], "model": base(), "operation": "admit",
         "request": {**plain_request, "options": {"max_tokens": 100.0}}},
        {"name": "admit_reserve_negative", "chain": [fit], "model": base(), "operation": "admit",
         "request": {**plain_request, "options": {"max_tokens": -5}}},
        {"name": "admit_unlimited_output", "chain": [fit], "model": base(max_output_tokens=None,
                                                                           context_window=40_000),
         "operation": "admit", "request": plain_request},
        {"name": "admit_runtime_managed", "chain": [fit],
         "model": base(transport="claude_code", context_window=200_000), "operation": "admit",
         "request": {**plain_request, "options": {"max_tokens": 99_999}}},
        {"name": "admit_codex_runtime", "chain": [fit],
         "model": base(transport="codex_app_server", context_window=None),
         "flags": ["representation"], "operation": "admit", "request": plain_request},
        {"name": "admit_recorder_setter", "chain": [{**fit, "audit": {"request_id": "r-1"}}],
         "model": base(), "flags": ["recorder", "setter"], "operation": "admit",
         "request": plain_request},
        {"name": "admit_recorder_through_bound",
         "chain": [{**fit, "audit": {"request_id": "r-2"}}, bound], "model": base(),
         "flags": ["recorder", "setter"], "operation": "admit", "request": plain_request},
        {"name": "admit_recorder_through_reasoning",
         "chain": [{**fit, "audit": {"request_id": "r-3"}}, reasoning], "model": base(),
         "flags": ["recorder", "setter", "representation"], "operation": "admit",
         "request": plain_request},
        {"name": "admit_rejected_with_recorder", "chain": [fit],
         "model": base(context_window=4200), "flags": ["recorder", "setter"],
         "operation": "admit", "request": plain_request},
        {"name": "gm_local_ok", "chain": [gm], "model": local, "operation": "admit",
         "request": plain_request},
        {"name": "gm_local_small_runtime", "chain": [gm],
         "model": {**local, "runtime_context_window": 65_536, "context_window": 65_536},
         "operation": "admit", "request": plain_request},
        {"name": "gm_local_unknown_runtime", "chain": [gm],
         "model": {**local, "runtime_context_window": None}, "operation": "admit",
         "request": plain_request},
        {"name": "gm_local_unknown_effective", "chain": [gm],
         "model": {**local, "context_window": None, "runtime_context_window": 140_000},
         "operation": "admit", "request": plain_request},
        {"name": "gm_ollama_unnamed", "chain": [gm],
         "model": {**local, "provider_mode": "ollama", "name": "",
                   "runtime_context_window": 8192}, "operation": "admit",
         "request": plain_request},
        {"name": "gm_byok_not_checked", "chain": [gm], "model": base(), "operation": "admit",
         "request": plain_request},
        {"name": "generate_adds_max_tokens", "chain": [fit], "model": base(), "operation": "generate",
         "request": {**plain_request, "options": {"tool_choice": "auto"}}},
        {"name": "generate_keeps_max_tokens", "chain": [fit], "model": base(), "operation": "generate",
         "request": {**plain_request, "stream": True, "timeout": 30.0,
                     "options": {"max_tokens": 500, "temperature": 0}}},
        {"name": "generate_runtime_drops", "chain": [fit],
         "model": base(transport="claude_code"), "operation": "generate",
         "request": {**plain_request, "options": {"max_tokens": 500, "max_output_tokens": 9}}},
        {"name": "generate_no_model", "chain": [fit], "model": None, "operation": "generate",
         "request": plain_request},
        {"name": "generate_rejected", "chain": [fit], "model": base(context_window=4200),
         "operation": "generate", "request": plain_request},
        {"name": "stream_through_chain", "chain": [fit, reasoning, bound], "model": base(),
         "flags": ["representation", "recorder", "setter"], "operation": "stream",
         "request": {**plain_request, "options": {"reasoning_effort": "low", "speed": "x"}}},
        {"name": "stream_rejected", "chain": [fit], "model": base(context_window=4200),
         "operation": "stream", "request": plain_request},
        {"name": "stream_output_key", "chain": [fit], "model": base(), "operation": "stream",
         "request": {**plain_request, "options": {"max_output_tokens": 300}}},
    ]
    return cases


def option_cases() -> list[dict[str, Any]]:
    selections = [
        SELECTION,
        {"requested": None, "effective": "medium", "control": "openai", "transport": "responses",
         "scope": "mode_default"},
        {"requested": "max", "effective": "max", "control": "anthropic",
         "transport": "anthropic_messages", "scope": "once"},
        {"requested": None, "effective": "enabled", "control": "adaptive",
         "transport": "chat_completions", "scope": "mode_default"},
        {"requested": "provider_default", "effective": None, "control": "openai",
         "transport": "chat_completions", "scope": "session"},
        {"requested": "low", "effective": "low", "control": "codex",
         "transport": "codex_app_server", "scope": "session"},
    ]
    speeds = [None, SPEED, {"requested": "fast", "effective": "fast", "control": "anthropic",
                            "scope": "session"}]
    option_sets = [
        {},
        {"reasoning_effort": "low", "thinking": {"type": "enabled"}, "service_tier": "flex",
         "speed": "fast", "max_tokens": 10},
        {"reasoning": {"effort": "low", "summary": "auto"}, "output_config": {"effort": "low"}},
        {"reasoning": {"effort": "low"}, "output_config": {"effort": "x", "format": "json"}},
        {"reasoning": "not a dict", "output_config": None, "tool_choice": "auto"},
        {"reasoning": {"summary": "auto"}, "thinking": None},
    ]
    cases = []
    for selection in selections:
        for speed in speeds:
            for options in option_sets:
                fake = Fake(None, [], [])
                wrapper = TurnReasoningProvider(
                    fake, ReasoningSelection(**selection), SpeedSelection(**speed) if speed else None)
                cases.append({"selection": selection, "speed": speed, "options": options,
                              "result": plain(wrapper._options(options))})  # noqa: SLF001
    return cases


def bind_cases() -> list[dict[str, Any]]:
    models = [
        base(supported_reasoning_efforts=["low", "medium", "high"], default_reasoning_effort="medium",
             reasoning_control="openai",
             speed_options=[{"id": "priority", "name": "Fast"}], speed_control="openai"),
        base(transport="responses", supported_reasoning_efforts=["low", "high"],
             reasoning_control="openai"),
        base(transport="anthropic_messages", supported_reasoning_efforts=["low", "max"],
             default_reasoning_effort="max", reasoning_control="anthropic",
             speed_options=[{"id": "fast", "name": "Fast"}], speed_control="anthropic"),
        base(),
    ]
    sessions = [
        ("dm", None, None), ("dm", "high", None), ("builder", None, "low"),
        ("dm", "high", "low"), ("dm", None, "mode_default"), ("dm", "provider_default", None),
        ("dm", "turbo", None), ("builder", "high", "max"),
    ]
    cases = []
    for spec in models:
        for role, effort, once in sessions:
            for speed in (None, "standard", "priority", "fast", "warp"):
                session = SimpleNamespace(role=SimpleNamespace(value=role),
                                          reasoning_effort=effort, reasoning_effort_once=once)
                case: dict[str, Any] = {"model": spec, "role": role, "reasoning_effort": effort,
                                        "reasoning_effort_once": once, "speed": speed}
                try:
                    wrapper = bind_session_reasoning(Fake(model(spec), [], []), session, speed)
                    case["selection"] = plain(wrapper.selection.__dict__)
                    case["speed_selection"] = plain(wrapper.speed.__dict__)
                except Exception as error:
                    case["error"] = failure(error)
                cases.append(case)
    return cases


def estimate_cases() -> dict[str, Any]:
    hook = TokenEstimateHook()
    request = {"model": "m", "messages": [{"role": "user", "content": "Hola, ¿qué tal?" * 7}]}
    observations = [
        {"provider_key": "test", "model_id": "test/a", "request": request, "reported": True,
         "prompt_tokens": 40},
        {"provider_key": "test", "model_id": "test/a", "request": request, "reported": True,
         "prompt_tokens": 20},
        {"provider_key": "test", "model_id": "test/a", "request": request, "reported": True,
         "prompt_tokens": 80},
        {"provider_key": "test", "model_id": "test/b", "request": request, "reported": None,
         "prompt_tokens": 10},
        {"provider_key": "test", "model_id": "test/b", "request": request, "reported": False,
         "prompt_tokens": 10},
        {"provider_key": "test", "model_id": "test/b", "request": {}, "reported": True,
         "prompt_tokens": 10},
        {"provider_key": "test", "model_id": "test/b", "request": None, "reported": True,
         "prompt_tokens": 10},
        {"provider_key": "test", "model_id": "test/b", "request": request, "reported": True,
         "prompt_tokens": 0},
        {"provider_key": "test", "model_id": "test/b", "request": request, "reported": True,
         "prompt_tokens": -3},
        {"provider_key": "test", "model_id": "test/c", "request": request, "reported": True,
         "prompt_tokens": 12.5},
        {"provider_key": "test", "model_id": "test/d", "request": request, "reported": True,
         "prompt_tokens": True},
        {"provider_key": "test", "model_id": "test/e", "request": request, "reported": True,
         "prompt_tokens": "12"},
        {"provider_key": "test", "model_id": "test/e", "request": request, "reported": True,
         "prompt_tokens": None},
        {"provider_key": "", "model_id": "", "request": request, "reported": True,
         "prompt_tokens": 7},
    ]
    results = []
    for item in observations:
        usage = TokenUsage(prompt_tokens=item["prompt_tokens"])
        response = ProviderResponse(content="ok", usage=usage, usage_reported=item["reported"])
        context = HookContext(provider_key=item["provider_key"], model_id=item["model_id"],
                              message_count=1, tool_count=0, request_json=item["request"],
                              response=response)
        try:
            asyncio.run(hook.on_response(context))
            results.append({"ok": True})
        except Exception as error:
            results.append({"error": failure(error)})
    values = [request, {"x": "é" * 50}, [], "short"]
    models = [None, model(base(id="test/a", provider_key="test")),
              model(base(id="test/c", provider_key="test")), model(base(id="test/d", provider_key="test")),
              model(base(id="test/z", provider_key="test")), model(base(id="", provider_key=""))]
    estimates = [[hook.estimate(value, chosen) for chosen in models] for value in values]
    fake = Fake(None, [], [])
    first, second = token_estimator(fake), token_estimator(fake)
    return {
        "observations": observations, "results": results, "values": values,
        "models": [None if chosen is None else {"id": chosen.id, "provider_key": chosen.provider_key}
                   for chosen in models],
        "estimates": estimates,
        "shared": first is second, "registered": len(fake.hooks.registry.hooks),
    }


async def all_fits() -> list[dict[str, object]]:
    return [await run_fit(case) for case in fit_cases()]


def main() -> None:
    document = {
        "fit": asyncio.run(all_fits()),
        "options": option_cases(),
        "bind": bind_cases(),
        "estimate": estimate_cases(),
    }
    text = json.dumps(document, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
    OUTPUT.write_text(text, encoding="utf-8", newline="\n")
    print(len(document["fit"]), "fit,", len(document["options"]), "options,",
          len(document["bind"]), "bind,", len(text), "bytes")


if __name__ == "__main__":
    main()
