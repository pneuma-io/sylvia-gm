"""Generate reference vectors for sylvia_agent::provider_resolver (config C3).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe crates/sylvia-agent/tests/vectors/generate_resolver_vectors.py

Writes `resolver_vectors.json`: for each settings file, `ConfiguredProviderResolver(path)` and
its `model_choices()` or its failure, then `resolver(session)` for a list of sessions (the
failure, or the provider configuration the factory was given, the OpenCode headers it was
wrapped with, and `model_choices()` afterwards). Protection is the deterministic fake seal of
the provider vectors, the environment has no `SYLVIA_GM_*` names, the factory records instead of
building, and the two runtime hooks (ChatGPT refresh, local discovery) answer from the case.
Every credential is a disposable test value. Reference tooling only; retired with the Python
source.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from sylvia_gm.agent import _provider_resolver as resolver_module
from sylvia_gm.config import _credential_protection as protection
from sylvia_gm.config._resolver import ConfigError
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.session import SessionRecord, WriterRole
from sylvia_gm.provider import _factory
from sylvia_gm.provider._request_metadata import SessionBoundRequestMetadataProvider

OUTPUT = Path(__file__).parent / "resolver_vectors.json"


def fake_seal(value: bytes, *, decrypt: bool) -> bytes:
    if decrypt:
        if not value.startswith(b"FAKE"):
            raise ConfigError(protection.UNAVAILABLE_MESSAGE)
        return value[4:][::-1]
    return b"FAKE" + value[::-1]


protection._platform_seal = fake_seal


def envelope_yaml(key: str, headers: dict) -> str:
    envelope = protection.protect_credentials(key, headers)
    return "{" + ", ".join(f"{k}: {json.dumps(v)}" for k, v in envelope.items()) + "}"


SEALED = envelope_yaml("sk-disposable", {"X-Keep": "1", "User-Agent": "static", "x-opencode-client": "c"})
BROKEN = "{version: 1, protection: nope, data: AAAA}"


class Recorded:
    def __init__(self, config) -> None:
        self.config = config
        self.model_info = config.models[0]
        self.hooks = None


BUILT: list = []


def create(config):
    BUILT.append(config)
    return Recorded(config)


_factory.ProviderFactory.create = staticmethod(create)

HOOKS = {"refresh": False, "discovery": None, "refresh_calls": [], "discovery_calls": []}


def refresh(storage_root):
    HOOKS["refresh_calls"].append(storage_root.name)
    return HOOKS["refresh"]


def discover(path, **request):
    HOOKS["discovery_calls"].append({key: request[key] for key in sorted(request) if key != "credential"})
    answer = HOOKS["discovery"]
    if isinstance(answer, str):
        raise ConfigError(answer)
    return answer if answer is not None else {"models": []}


resolver_module.refresh_saved_subscription_reasoning = refresh
resolver_module.discover_provider_models = discover


TOKENS = {protection.UNAVAILABLE_MESSAGE: "<UNAVAILABLE>", protection._PROTECTION: "<PROTECTION>"}


def tokens(value):
    """Platform wording as tokens, so one vector file serves Windows and Linux."""
    text = json.dumps(value, ensure_ascii=False)
    for original, token in TOKENS.items():
        text = text.replace(json.dumps(original, ensure_ascii=False)[1:-1], token)
    return json.loads(text)


def failure(error: BaseException) -> dict:
    record = {"class": type(error).__name__, "message": str(error)}
    if isinstance(error, CommandRejected):
        record["code"] = error.code
    return {"error": record}


def config_record(config) -> dict:
    return {"provider_key": config.provider_key, "transport": config.transport, "base_url": config.base_url,
            "runtime_home_name": Path(config.runtime_home).name if config.runtime_home else "",
            "default_headers": list(config.default_headers.items()), "api_key": config.api_key,
            "models": [model.id for model in config.models], "default_model": config.default_model}


M = "    models:\n"
CASES = [
    {"name": "none", "user": None, "sessions": [["s", "dm", "openai", "openai/gpt-5"]]},
    {"name": "openai-catalogue-free", "user": "providers:\n  openai:\n    enabled: true\n",
     "sessions": []},
    {"name": "openai", "user": "providers:\n  openai:\n    enabled: true\n    protected_credentials: " + SEALED + "\n" + M +
     "    - {id: gpt-5.2, name: GPT 5.2, transport: responses, declared_context_window: 400000, max_output_tokens: 128000}\n"
     "    - {id: gpt-5, transport: chat_completions}\n"
     "    - {id: gpt-4o, name: Four O, service_tiers: [{id: fast, name: ' Fast ', description: x}], supported_reasoning_efforts: [low], default_reasoning_effort: low}\n",
     "sessions": [["s1", "dm", "openai", "openai/gpt-5.2"], ["s1", "builder", "openai", "openai/gpt-5"],
                  ["s1", "dm", "openai", "openai/nope"], ["s1", "dm", "anthropic", "openai/gpt-5"],
                  ["s1", "dm", "openai", "gpt-5"]]},
    {"name": "opencode", "user": "providers:\n  opencode_go:\n    enabled: true\n    preset_id: opencode_go\n    protected_credentials: " + SEALED + "\n" + M +
     "    - {id: deepseek-v4}\n    - {id: opencode-go/deepseek-v3}\n    - {id: minimax-m3, transport: anthropic_messages, name: M3}\n"
     "  custom_zen:\n    enabled: true\n    preset_id: opencode_zen\n    base_url: https://opencode.ai/zen/v1\n" + M + "    - {id: q}\n",
     "sessions": [["s1", "dm", "opencode_go", "opencode-go/deepseek-v4"], ["é", "dm", "opencode_go", "opencode-go/minimax-m3"],
                  ["s2", "dm", "opencode_go", "opencode-go/deepseek-v3"], ["s3", "dm", "custom_zen", "custom-zen/q"]]},
    {"name": "broken-credentials", "user": "providers:\n  openai:\n    enabled: true\n    protected_credentials: " + BROKEN + "\n" + M + "    - {id: gpt-5}\n",
     "sessions": [["s", "dm", "openai", "openai/gpt-5"]]},
    {"name": "local", "user": "providers:\n  lm_studio:\n    enabled: true\n" + M +
     "    - {id: qwen, declared_context_window: 32768, configured_context_window: 16384, context_observed_at: '2026-01-01'}\n"
     "    - {id: big, context_window: 8192}\n"
     "  ollama:\n    enabled: true\n    mode: ollama\n    name: Ollama\n    base_url: http://127.0.0.1:11434/v1\n",
     "discovery": {"models": [{"id": "other"}, "x", {"id": "qwen", "runtime_context_window": 24000, "runtime_context_source": "runtime_reported",
                              "runtime_context_observed_at": "2026-11-21T00:00:00+00:00", "declared_context_window": True}]},
     "sessions": [["s", "builder", "lm_studio", "lm-studio/qwen"], ["s", "dm", "lm_studio", "lm-studio/qwen"],
                  ["s", "dm", "ollama", "ollama/llama3.1:8b"], ["s", "dm", "lm_studio", "lm-studio/big"]]},
    {"name": "local-discovery-refused", "user": "providers:\n  lm_studio:\n    enabled: true\n" + M + "    - {id: qwen, runtime_context_window: 9000}\n",
     "discovery": "Discovery refused", "sessions": [["s", "dm", "lm_studio", "lm-studio/qwen"]]},
    {"name": "subscriptions", "user": "providers:\n  chatgpt_subscription:\n    enabled: true\n    base_url: https://chatgpt.com\n" + M +
     "    - {id: gpt-5.5, transport: codex_app_server, service_tiers: [{id: priority, name: Priority}]}\n"
     "    - {id: gpt-5.4, transport: codex_app_server, supported_reasoning_efforts: [low, high], default_reasoning_effort: high}\n"
     "  claude_runtime:\n    enabled: true\n    base_url: https://claude.ai\n" + M +
     "    - {id: claude-opus-4-6, transport: claude_code, max_output_tokens: 64000}\n",
     "refresh": True,
     "sessions": [["s", "dm", "chatgpt_subscription", "chatgpt-subscription/gpt-5.5"],
                  ["s", "dm", "claude_runtime", "claude-runtime/claude-opus-4-6"],
                  ["s", "dm", "claude_runtime", "claude-runtime/claude-opus-4-6"]]},
    {"name": "anthropic", "user": "providers:\n  anthropic:\n    enabled: true\n    base_url: https://api.anthropic.com/v1\n" + M +
     "    - {id: claude-sonnet-4-5}\n    - {id: claude-opus-4-6, transport: anthropic_messages}\n"
     "  minimax:\n    enabled: true\n    base_url: https://api.minimax.io/anthropic/v1\n    name: ''\n    mode: ''\n" + M +
     "    - {id: MiniMax-M3, transport: anthropic_messages}\n",
     "sessions": [["s", "dm", "anthropic", "anthropic/claude-sonnet-4-5"]]},
    {"name": "catalogue-defaults", "user": "providers:\n  openrouter:\n    enabled: true\n    base_url: https://openrouter.ai/api/v1\n"
     "  anthropic:\n    enabled: true\n    base_url: https://api.anthropic.com\n", "sessions": []},
    {"name": "sort-order", "user": "providers:\n  b_one:\n    enabled: true\n    name: École\n    base_url: http://h\n" + M +
     "    - {id: z, name: alpha}\n    - {id: y, name: Alpha}\n    - {id: x, name: ßeta}\n    - {id: w, name: SSeta}\n"
     "  a_two:\n    enabled: true\n    name: école\n    base_url: http://h\n" + M + "    - {id: v, name: alpha}\n"
     "  a-two:\n    enabled: true\n    name: Z\n    base_url: http://h\n" + M + "    - {id: v, name: clash}\n",
     "sessions": [["s", "dm", "a_two", "a-two/v"], ["s", "dm", "a-two", "a-two/v"]]},
    {"name": "no-base-url", "user": "providers:\n  custom:\n    enabled: true\n" + M + "    - {id: m}\n", "sessions": []},
    {"name": "bad-url", "user": "providers:\n  custom:\n    enabled: true\n    base_url: 'https://[::1/v1'\n" + M + "    - {id: m}\n", "sessions": []},
    {"name": "bad-port", "user": "providers:\n  custom:\n    enabled: true\n    base_url: 'https://h:99999/v1'\n" + M + "    - {id: m}\n", "sessions": []},
    {"name": "http-bad-port", "user": "providers:\n  custom:\n    enabled: true\n    base_url: 'http://h:99999/v1'\n" + M + "    - {id: m}\n", "sessions": []},
    {"name": "empty-models", "user": "providers:\n  custom:\n    enabled: true\n    base_url: http://h\n", "sessions": []},
    {"name": "disabled-ignored", "user": "providers:\n  custom:\n    enabled: false\n" + M + "    - {id: ' bad'}\n", "sessions": []},
]

ROW_ERRORS = [
    "{id: m, extra: 1, another: 2}", "{id: ' m'}", "{id: ''}", "{id: 5}", "{name: n}", "{id: \"a\\tb\"}",
    "{id: " + "a" * 257 + "}", "{id: " + "a" * 256 + "}", "{id: m, name: " + "n" * 129 + "}", "{id: m, name: null}",
    "{id: m, declared_context_window: 511}", "{id: m, declared_context_window: 10000001}", "{id: m, declared_context_window: 512.0}",
    "{id: m, declared_context_window: true}", "{id: m, declared_context_window: ''}", "{id: m, runtime_context_window: '9000'}",
    "{id: m, configured_context_window: 100}", "{id: m, context_window: 100}", "{id: m, context_window: 100, effective_context_window: 1}",
    "{id: m, max_output_tokens: 0}", "{id: m, max_output_tokens: 600, declared_context_window: 512}",
    "{id: m, max_output_tokens: 600, declared_context_window: 512, transport: claude_code}",
    "{id: m, declared_context_window: 1000}", "{id: m, max_output_tokens: '', declared_context_window: 1000}",
    "{id: m, max_output_tokens: null, transport: codex_app_server}", "{id: m, declared_max_output_tokens: 100}",
    "{id: m, declared_max_output_tokens: 0}", "{id: m, configured_output_tokens: x}", "{id: m, builder_output_tokens: 5000}",
    "{id: m, builder_output_tokens: 4096}", "{id: m, builder_output_tokens: 0}", "{id: m, max_output_tokens: 100, builder_output_tokens: 101}",
    "{id: m, transport: carrier_pigeon}", "{id: m, transport: [chat_completions]}", "{id: m, supports_streaming: 1}",
    "{id: m, supports_tools: 'yes'}", "{id: m, supports_tools: null, supports_streaming: false}",
    "{id: m, supported_reasoning_efforts: [Low]}", "{id: m, default_reasoning_effort: low}",
    "{id: m, supported_reasoning_efforts: [low], default_reasoning_effort: high}", "{id: m, service_tiers: [{id: standard, name: S}]}",
    "{id: m, service_tiers: 'fast'}", "{id: m, declared_context_source: bogus, runtime_context_window: 9000, runtime_context_source: runtime_reported}",
    "{id: m, declared_context_observed_at: 5, context_observed_at: '2026'}", "{id: m, context_window_source: x, documented_at: y, output_allowance_source: z}",
    "{id: m, max_output_tokens: 10000001}",
]
for index, row in enumerate(ROW_ERRORS):
    CASES.append({"name": f"row-{index}", "user": "providers:\n  custom:\n    enabled: true\n    base_url: https://api.openai.com/v1\n" + M + "    - " + row + "\n",
                  "sessions": []})
CASES.append({"name": "duplicate", "user": "providers:\n  openai:\n    enabled: true\n" + M + "    - {id: gpt-5}\n    - {id: openai/gpt-5}\n", "sessions": []})
CASES.append({"name": "duplicate-identity", "user": "providers:\n  custom:\n    enabled: true\n    base_url: http://h\n" + M +
              "    - {id: x}\n    - {id: x}\n", "sessions": []})
CASES.append({"name": "duplicate-known", "user": "providers:\n  ollama:\n    enabled: true\n    base_url: http://h\n" + M +
              "    - {id: ollama/mistral:7b}\n    - {id: ollama/mistral:7b, name: Again}\n", "sessions": []})
CASES.append({"name": "known-by-wire", "user": "providers:\n  ollama:\n    enabled: true\n    base_url: http://h\n" + M +
              "    - {id: 'llama3.1:8b'}\n    - {id: 'ollama/qwen2.5:7b', name: Q, supports_tools: true}\n", "sessions": []})


def main() -> int:
    for name in list(os.environ):
        if name.upper().startswith("SYLVIA_GM_"):
            del os.environ[name]
    records = []
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder) / "owner-root"
        root.mkdir()
        path = root / "config.yaml"
        for case in CASES:
            if path.exists():
                path.unlink()
            if case["user"] is not None:
                path.write_bytes(case["user"].encode("utf-8"))
            HOOKS.update(refresh=case.get("refresh", False), discovery=case.get("discovery"), refresh_calls=[], discovery_calls=[])
            BUILT.clear()
            record = {"name": case["name"], "user": case["user"], "refresh": HOOKS["refresh"], "discovery": case.get("discovery"),
                      "sessions": case["sessions"]}
            try:
                resolver = resolver_module.ConfiguredProviderResolver(path)
            except Exception as error:  # noqa: BLE001
                record.update(failure(error))
                record["refresh_calls"] = HOOKS["refresh_calls"]
                records.append(tokens(record))
                continue
            record["subscription_models"] = sorted(resolver.subscription_models)
            record["claude_code_models"] = sorted(resolver.claude_code_models)
            record["model_choices"] = list(resolver.model_choices())
            outcomes = []
            for session_id, role, provider_id, model_id in case["sessions"]:
                session = SessionRecord(session_id=session_id, campaign_id="c1" if role == "dm" else None, role=WriterRole(role),
                                        provider_id=provider_id, model_id=model_id)
                BUILT.clear()
                try:
                    provider = resolver(session)
                except Exception as error:  # noqa: BLE001
                    outcomes.append(failure(error))
                    continue
                wrapped = isinstance(provider, SessionBoundRequestMetadataProvider)
                inner = provider._provider if wrapped else provider
                outcomes.append({
                    "built": [config_record(config) for config in BUILT],
                    "config": config_record(inner.config),
                    "headers": list(provider._headers.items()) if wrapped else None,
                    "model_choices": list(resolver.model_choices()),
                })
            record["outcomes"] = outcomes
            record["refresh_calls"] = HOOKS["refresh_calls"]
            record["discovery_calls"] = HOOKS["discovery_calls"]
            records.append(tokens(record))
    OUTPUT.write_bytes((json.dumps({"cases": records}, ensure_ascii=True, indent=0) + "\n").encode("ascii"))
    print(f"{len(records)} cases -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
