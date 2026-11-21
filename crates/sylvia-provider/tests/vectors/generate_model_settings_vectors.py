"""Generate reference vectors for the model catalogue and settings (config C3).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe crates/sylvia-provider/tests/vectors/generate_model_settings_vectors.py

Writes `model_settings_vectors.json`: `build_catalog()`, `output_allowance`, `reasoning_values`,
`speed_options`, `configure_reasoning` and `configure_speed` over every documented model and
route, `mode_reasoning_default`, `select_reasoning`, `select_speed`, the OpenCode request headers,
and `ProviderRegistry` resolution with a recording factory. Reference tooling only; retired with
the Python source.
"""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.provider import _factory
from sylvia_gm.provider import _model_facts as facts
from sylvia_gm.provider._base import ModelInfo, SpeedOption
from sylvia_gm.provider._exceptions import ModelNotFoundError
from sylvia_gm.provider._models import build_catalog
from sylvia_gm.provider._output_policy import output_allowance
from sylvia_gm.provider._reasoning import (
    configure_reasoning,
    mode_reasoning_default,
    reasoning_values,
    select_reasoning,
)
from sylvia_gm.provider._registry import ProviderRegistry
from sylvia_gm.provider._request_metadata import (
    _with_protocol_headers,
    opencode_request_headers,
    without_opencode_protocol_headers,
)
from sylvia_gm.provider._speed import SpeedSelection, configure_speed, select_speed, speed_options

OUTPUT = Path(__file__).parent / "model_settings_vectors.json"


def plain(value):
    if isinstance(value, SpeedOption):
        return {"id": value.id, "name": value.name, "description": value.description}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def model_record(model: ModelInfo) -> dict:
    return {field.name: plain(getattr(model, field.name)) for field in fields(model)}


def outcome(call) -> dict:
    try:
        return {"value": call()}
    except CommandRejected as error:
        return {"error": {"class": "CommandRejected", "code": error.code, "message": str(error)}}
    except Exception as error:  # noqa: BLE001
        return {"error": {"class": type(error).__name__, "message": str(error)}}


def model_from(spec: dict) -> ModelInfo:
    return ModelInfo(
        id=spec["id"], name="N", provider_name="P", provider_key="p", provider_mode="byok",
        transport=spec.get("transport", "chat_completions"),
        provider_model_id=spec.get("provider_model_id", ""),
        supported_reasoning_efforts=(
            None if spec.get("supported_reasoning_efforts") is None else tuple(spec["supported_reasoning_efforts"])
        ),
        default_reasoning_effort=spec.get("default_reasoning_effort"),
        reasoning_control=spec.get("reasoning_control"),
        reasoning_source=spec.get("reasoning_source", "unknown"),
        speed_options=(
            None if spec.get("speed_options") is None
            else tuple(SpeedOption(**option) for option in spec["speed_options"])
        ),
        max_output_tokens=spec.get("max_output_tokens", 4096),
        context_window=spec.get("context_window"),
        builder_output_tokens=spec.get("builder_output_tokens", 32000),
    )


SETTING_FIELDS = (
    "supported_reasoning_efforts", "default_reasoning_effort", "reasoning_control", "reasoning_source",
    "speed_options", "speed_control", "speed_source",
)


def settings(model: ModelInfo) -> dict:
    return {name: plain(getattr(model, name)) for name in SETTING_FIELDS}


ROUTE_URLS = {
    "openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com/v1",
    "groq": "https://api.groq.com/openai/v1", "deepseek": "https://api.deepseek.com",
    "claude_runtime": "https://claude.ai", "chatgpt_subscription": "https://chatgpt.com",
    "opencode_go": "https://opencode.ai/zen/go/v1",
}
EXTRA_URLS = [
    "http://api.openai.com/v1", "https://api.openai.com:443/v1", "https://api.openai.com:8443/v1",
    "https://API.OpenAI.com/v1/", "https://api.groq.com/openai/v1/", "https://api.groq.com/openai",
    "https://api.deepseek.com/", "https://api.deepseek.com/v1", "https://api.minimax.io/anthropic/v1",
    "https://api.minimaxi.com", "https://opencode.ai/zen/v1", "https://opencode.ai/zen/go/v1/",
    "https://opencode.ai/inference/go/v1", "http://127.0.0.1:1234/v1", "", "https://api.openai.com:99999/v1",
    "http://api.openai.com:99999/v1", "https://[::1/v1", "https://api.anthropic.com:0443",
]
TRANSPORTS = ("chat_completions", "responses", "anthropic_messages", "codex_app_server", "claude_code",
              "gemini_generate_content")
SPEEDS = [{"id": "fast", "name": "Fast", "description": "Quick"}, {"id": "flex", "name": "Flex", "description": ""}]


def configure_cases() -> list:
    specs = []
    for route, model_id in facts._MODEL_FACTS:
        for wire in ("chat_completions", "responses", "anthropic_messages"):
            specs.append(({"id": f"x/{model_id}", "transport": wire}, [ROUTE_URLS.get(route, "https://h")]))
    cross_ids = ["gpt-5.2", "gpt-5", "claude-opus-4-6", "claude-sonnet-4-5", "MiniMax-M3", "minimax-m3", "unknown-model"]
    cross_ids += [model_id for route, model_id in facts._MODEL_FACTS if route in ("groq", "deepseek")]
    for model_id in cross_ids:
        for wire in TRANSPORTS:
            specs.append(({"id": model_id, "transport": wire}, list(ROUTE_URLS.values()) + EXTRA_URLS))
    specs.append(({"id": "a/b", "provider_model_id": "gpt-5.2", "transport": "responses"}, ["https://api.openai.com/v1"]))
    specs.append(({"id": "kept", "reasoning_control": "openai", "reasoning_source": "s",
                   "supported_reasoning_efforts": ["low"], "default_reasoning_effort": "low"},
                  ["http://h", "https://h"]))
    for efforts in (None, [], ["low", "high"]):
        for speeds in (None, [], SPEEDS):
            specs.append(({"id": "codex/m", "transport": "codex_app_server", "supported_reasoning_efforts": efforts,
                           "speed_options": speeds}, ["https://chatgpt.com", "http://[::1"]))
    cases = []
    for spec, urls in specs:
        for url in urls:
            record = {"model": spec, "base_url": url}
            try:
                reasoned = configure_reasoning(model_from(spec), url)
                record["reasoning"] = settings(reasoned)
            except ValueError as error:
                record["reasoning_error"] = str(error)
                reasoned = model_from(spec)
            try:
                record["speed"] = settings(configure_speed(reasoned, url))
            except ValueError as error:
                record["speed_error"] = str(error)
            cases.append(record)
    return cases


REASONING_VALUES = [
    (None, None), (None, "low"), ([], None), ([], "low"), (["low", "high"], None), (["low", "high"], "high"),
    (["low", "low", "high"], "low"), (["low"], "x"), (["low"], 3), (["Low"], None), (["a" * 32], None),
    (["a" * 33], None), ([1], None), ("low", None), ({"a": 1}, None), (["x"] * 16, None), (["x"] * 17, None),
    (["a-b_c9"], None), (["9a"], None), (["é"], None), ([None], None), (["low"], None),
]
SPEED_VALUES = [
    None, [], "fast", {"id": "fast"}, [SPEEDS[0]], SPEEDS, [SPEEDS[0], SPEEDS[0]], [{"id": "fast", "name": "Fast"}],
    [{"id": "standard", "name": "S"}], [{"id": "Fast", "name": "F"}], [{"id": "fast", "name": "  "}],
    [{"id": "fast", "name": " Fast  ", "description": " d  "}], [{"id": "fast", "name": "x" * 40}],
    [{"id": "fast", "name": "x" * 41}], [{"id": "fast", "name": "F", "description": "d" * 200}],
    [{"id": "fast", "name": "F", "description": "d" * 201}], [{"id": "fast", "name": "F", "description": None}],
    [{"id": 1, "name": "F"}], [{"name": "F"}], ["fast"], [SPEEDS[0]] * 8, [SPEEDS[0]] * 9,
    [{"id": "fast", "name": "é" * 40}],
]

DEFAULT_MODELS = []
for supported in (None, [], ["low"], ["low", "medium"], ["high"], ["xhigh", "high"], ["max"], ["max", "ultra"],
                  ["none", "minimal"], ["medium", "max"], ["disabled", "adaptive"], ["weird", "other"]):
    for control in (None, "openai", "adaptive"):
        for default in (None, "max", "medium", "weird"):
            DEFAULT_MODELS.append({"id": "m", "supported_reasoning_efforts": supported, "reasoning_control": control,
                                   "default_reasoning_effort": default})

SELECT_REQUESTS = [None, "mode_default", "provider_default", "low", "high", "xhigh", "bogus", 5, "", "adaptive"]
SPEED_REQUESTS = [None, "standard", "fast", "flex", "slow", 5, ""]


def allowance_cases() -> list:
    cases = []
    for wire in ("chat_completions", "codex_app_server", "claude_code", "responses"):
        for output in (None, 0, -1, 5000, 100):
            for context in (None, 100, 2, 0, 8192):
                for builder_tokens in (0, 50, 32000):
                    spec = {"id": "m", "transport": wire, "max_output_tokens": output, "context_window": context,
                            "builder_output_tokens": builder_tokens}
                    model = model_from(spec)
                    cases.append({"model": spec, "dm": output_allowance(model), "builder": output_allowance(model, builder=True)})
    return cases


class Recorded:
    def __init__(self, number: int, config) -> None:
        self.number = number
        self.config = config


def registry_cases() -> list:
    built = []

    def create(config):
        built.append(config)
        return Recorded(len(built), config)

    _factory.ProviderFactory.create = staticmethod(create)
    catalog = [
        ModelInfo(id="alpha/one", name="One", provider_name="A", provider_key="alpha", provider_mode="byok"),
        ModelInfo(id="alpha/two", name="Two", provider_name="A", provider_key="alpha", provider_mode="byok",
                  transport="responses", provider_model_id="two-wire"),
        ModelInfo(id="beta/two", name="Two", provider_name="B", provider_key="beta", provider_mode="byok"),
        ModelInfo(id="beta/claude", name="C", provider_name="B", provider_key="beta", provider_mode="byok",
                  transport="claude_code"),
        ModelInfo(id="plain", name="P", provider_name="B", provider_key="beta", provider_mode="byok"),
        ModelInfo(id="alpha/one", name="One again", provider_name="A", provider_key="alpha", provider_mode="byok"),
    ]
    operations = [
        {"op": "resolve", "model_id": "alpha/one"},
        {"op": "configure", "key": "alpha", "base_url": "https://a", "api_key": "k-a", "headers": {"X": "1"}},
        {"op": "resolve", "model_id": "alpha/one"},
        {"op": "resolve", "model_id": "one"},
        {"op": "resolve", "model_id": "alpha/one"},
        {"op": "resolve", "model_id": "two"},
        {"op": "resolve", "model_id": "alpha/two"},
        {"op": "resolve", "model_id": "nope"},
        {"op": "resolve", "model_id": "beta/two"},
        {"op": "configure", "key": "beta", "base_url": "https://b", "api_key": "", "headers": {},
         "runtime_home": "/r"},
        {"op": "resolve", "model_id": "beta/two"},
        {"op": "resolve", "model_id": "plain"},
        {"op": "resolve", "model_id": "claude"},
        {"op": "resolve", "model_id": "beta/claude"},
        {"op": "configure", "key": "alpha", "base_url": "https://a2", "api_key": "k-2", "headers": {}},
        {"op": "resolve", "model_id": "alpha/one"},
        {"op": "resolve", "model_id": "alpha/two"},
        {"op": "list", "key": None},
        {"op": "list", "key": "alpha"},
        {"op": "list", "key": "gamma"},
    ]
    registry = ProviderRegistry(catalog=catalog)
    results = []
    for operation in operations:
        if operation["op"] == "configure":
            registry.configure_provider(operation["key"], operation["base_url"], operation["api_key"],
                                        headers=operation["headers"], runtime_home=operation.get("runtime_home", ""))
            results.append(None)
        elif operation["op"] == "list":
            results.append([[model.id, model.name] for model in registry.list_models(operation["key"])])
        else:
            try:
                provider, info = registry.resolve(operation["model_id"])
            except ModelNotFoundError as error:
                results.append({"error": str(error)})
                continue
            config = provider.config
            results.append({
                "provider": provider.number, "model": [info.id, info.name],
                "config": {"provider_key": config.provider_key, "transport": config.transport,
                           "base_url": config.base_url, "runtime_home": config.runtime_home,
                           "default_headers": config.default_headers, "api_key": config.api_key,
                           "models": [model.id for model in config.models], "default_model": config.default_model},
            })
    return [{"catalog": [model_record(model) for model in catalog], "operations": operations, "results": results},
            {"empty_catalog": [model.id for model in ProviderRegistry(catalog=[]).list_models()]}]


def main() -> int:
    model_cases = [{"model": spec} for spec in DEFAULT_MODELS]
    document = {
        "catalog": [model_record(model) for model in build_catalog()],
        "allowance": allowance_cases(),
        "reasoning_values": [
            {"values": values, "suggested": suggested, **outcome(lambda v=values, s=suggested: list(reasoning_values(v, s)))}
            for values, suggested in REASONING_VALUES
        ],
        "speed_options": [
            {"values": values, **outcome(lambda v=values: plain(speed_options(v)))} for values in SPEED_VALUES
        ],
        "configure": configure_cases(),
        "mode_defaults": [
            {**case, "dm": mode_reasoning_default(model_from(case["model"]), "dm"),
             "builder": mode_reasoning_default(model_from(case["model"]), "builder"),
             "other": mode_reasoning_default(model_from(case["model"]), "planner")}
            for case in model_cases
        ] + [{"model": None, "dm": mode_reasoning_default(None, "dm"), "builder": None, "other": None}],
        "select_reasoning": [
            {"model": spec, "role": role, "requested": requested,
             **outcome(lambda m=spec, r=role, q=requested: select_reasoning(
                 None if m is None else model_from(m), r, q, scope="session").projection()
                 | {"control": None if m is None else model_from(m).reasoning_control})}
            for spec in [None, {"id": "m", "supported_reasoning_efforts": ["low", "high"], "reasoning_control": "openai",
                                "transport": "responses"},
                         {"id": "m", "supported_reasoning_efforts": ["low", "high"], "reasoning_control": "openai"},
                         {"id": "m", "supported_reasoning_efforts": ["low", "max"], "reasoning_control": "anthropic",
                          "transport": "anthropic_messages"},
                         {"id": "m", "supported_reasoning_efforts": ["disabled", "adaptive"], "reasoning_control": "adaptive"},
                         {"id": "m", "supported_reasoning_efforts": ["high"], "reasoning_control": "codex"},
                         {"id": "m", "supported_reasoning_efforts": [], "reasoning_control": "openai"},
                         {"id": "m", "supported_reasoning_efforts": ["high"], "reasoning_control": "other"}]
            for role in ("dm", "builder")
            for requested in SELECT_REQUESTS
        ],
        "select_speed": [
            {"model": spec, "requested": requested, "strict": strict,
             **outcome(lambda m=spec, q=requested, s=strict: select_speed(
                 None if m is None else model_from(m), q, strict=s, scope="once").projection())}
            for spec in [None, {"id": "m", "speed_options": SPEEDS},
                         {"id": "m", "speed_options": [SPEEDS[0]]}, {"id": "m", "speed_options": []}]
            for requested in SPEED_REQUESTS
            for strict in (True, False)
        ],
        "speed_controls": [
            {"control": control, "effective": effective,
             "options": SpeedSelection(effective, effective, control, "s").options()}
            for control in (None, "codex", "openai", "anthropic", "other")
            for effective in (None, "fast")
        ],
        "opencode_headers": [{"session_id": sid, "headers": list(opencode_request_headers(sid).items())}
                             for sid in ("s1", "", "éè", "x" * 300)],
        "without_protocol_headers": [
            {"headers": list(headers.items()), "result": list(without_opencode_protocol_headers(headers).items())}
            for headers in ({}, {"X-OpenCode-Session": "a", "user-agent": "b", "Keep": "c", "x-opencode-client": "d"},
                            {"ſser-Agent": "long s", "USER-AGENT": "upper", "x-opencode-sessio": "near"})
        ],
        "with_protocol_headers": [
            {"options": options, "result": _with_protocol_headers(options, {"x-opencode-session": "S", "User-Agent": "U"})}
            for options in ({}, {"max_tokens": 5}, {"extra_headers": None}, {"extra_headers": "text"},
                            {"extra_headers": {"A": 1, "b": True, "c": None, "d": [1, "x"], "e": {"k": 1.5}, "User-Agent": "old",
                                               "X-OPENCODE-SESSION": "old"}, "tail": 1},
                            {"before": 1, "extra_headers": {"User-Agent": "first"}, "after": 2})
        ],
        "registry": registry_cases(),
    }
    OUTPUT.write_bytes((json.dumps(document, ensure_ascii=True, indent=0) + "\n").encode("ascii"))
    print(f"{len(document['configure'])} configure cases -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
