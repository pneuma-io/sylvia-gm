"""Generate reference vectors for sylvia_provider::factory (PR 3b).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe crates/sylvia-provider/tests/vectors/generate_factory_vectors.py

Writes `factory_vectors.json`: `ProviderFactory.create(config)` over provider configurations.
For a Chat Completions or Responses model it records the `OpenAICompatProvider` it builds (base
URL, model, retry policy, timeouts, default headers); the native Claude, ChatGPT, Gemini and
Anthropic providers are replaced by stand-ins that record which class Python chose. A failure
records its class and text. Credentials are disposable test values. Reference tooling only;
retired with the Python source.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from sylvia_gm.provider import _anthropic, _chatgpt, _claude, _gemini
from sylvia_gm.provider._base import ModelInfo, ProviderConfig, RetryConfig
from sylvia_gm.provider._factory import ProviderFactory
from sylvia_gm.provider._openai_compat import OpenAICompatProvider

OUTPUT = Path(__file__).parent / "factory_vectors.json"


def stand_in(name: str):
    class StandIn:
        def __init__(self, *args, **kwargs) -> None:
            self.kind = name

    return StandIn


_claude.ClaudeProvider = stand_in("ClaudeProvider")
_chatgpt.ChatGPTProvider = stand_in("ChatGPTProvider")
_gemini.GeminiProvider = stand_in("GeminiProvider")
_anthropic.AnthropicProvider = stand_in("AnthropicProvider")


def model(model_id: str, transport: str) -> ModelInfo:
    return ModelInfo(id=model_id, name="M", provider_name="P", provider_key="p", provider_mode="byok", transport=transport)


def retry(config) -> dict:
    record = asdict(config)
    record["retryable_statuses"] = sorted(record["retryable_statuses"])
    return record


CASES = []
for key in ("openai", "opencode_go", "openrouter", "anthropic", "ollama", "sylvia_cloud", "lm_studio"):
    CASES.append({"provider_key": key, "transport": "chat_completions", "base_url": "https://api.example.test/v1",
                  "api_key": "sk-disposable", "models": [["x/m", "chat_completions"]]})
CASES += [
    {"provider_key": "custom", "transport": "chat_completions", "base_url": "https://opencode.ai/zen/v1",
     "default_headers": [["X-B", "2"], ["X-A", "1"]], "models": [["custom/m", "chat_completions"]]},
    {"provider_key": "custom", "transport": "chat_completions", "base_url": "https://opencode.ai/zen/go/v1", "models": []},
    {"provider_key": "custom", "transport": "chat_completions", "base_url": "https://opencode.ai/zen/go/v1", "models": [],
     "default_model": "custom/named"},
    {"provider_key": "custom", "transport": "chat_completions", "base_url": "http://[::1/v1", "models": [["c/m", "chat_completions"]]},
    {"provider_key": "custom", "transport": "chat_completions", "base_url": "http://h:99999/v1", "models": [["c/m", "chat_completions"]]},
    {"provider_key": "custom", "transport": "chat_completions", "base_url": "http://bücher.example/v1", "models": [["c/m", "chat_completions"]]},
    {"provider_key": "custom", "transport": "chat_completions", "base_url": "", "models": [["c/m", "chat_completions"]]},
    {"provider_key": "custom", "transport": "chat_completions", "base_url": "not a url", "models": [["c/m", "chat_completions"]]},
    {"provider_key": "openrouter", "transport": "chat_completions", "base_url": "https://openrouter.ai/api/v1",
     "retry": {"max_retries": 7, "base_delay": 0.5, "max_delay": 9.0, "backoff_multiplier": 3.0, "retryable_statuses": [500]},
     "models": [["openrouter/m", "chat_completions"]]},
    {"provider_key": "openai", "transport": "responses", "base_url": "https://api.openai.com/v1", "models": [["openai/m", "responses"]]},
    {"provider_key": "opencode_zen", "transport": "responses", "base_url": "https://opencode.ai/zen/v1", "models": [["z/m", "responses"]]},
    {"provider_key": "anthropic", "transport": "anthropic_messages", "base_url": "https://api.anthropic.com/v1", "models": [["a/m", "anthropic_messages"]]},
    {"provider_key": "gemini", "transport": "gemini_generate_content", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
     "models": [["g/m", "gemini_generate_content"]]},
    {"provider_key": "claude_runtime", "transport": "claude_code", "base_url": "https://claude.ai", "runtime_home": "/r",
     "models": [["c/m", "claude_code"]]},
    {"provider_key": "claude_runtime", "transport": "claude_code", "base_url": "https://claude.ai", "runtime_home": "", "models": []},
    {"provider_key": "claude_runtime", "transport": "claude_code", "base_url": "https://claude.ai", "runtime_home": "/r", "api_key": "k", "models": []},
    {"provider_key": "claude_runtime", "transport": "claude_code", "base_url": "https://claude.ai", "runtime_home": "/r",
     "default_headers": [["X", "1"]], "models": []},
    {"provider_key": "other", "transport": "claude_code", "base_url": "https://claude.ai", "runtime_home": "/r", "models": []},
    {"provider_key": "chatgpt_subscription", "transport": "codex_app_server", "base_url": "https://chatgpt.com", "runtime_home": "/r", "models": []},
    {"provider_key": "chatgpt_subscription", "transport": "codex_app_server", "base_url": "https://chatgpt.com", "runtime_home": "", "models": []},
    {"provider_key": "other", "transport": "codex_app_server", "base_url": "https://chatgpt.com", "runtime_home": "/r", "models": []},
    {"provider_key": "custom", "transport": "pigeon", "base_url": "https://h.test/v1", "models": []},
    {"provider_key": "custom", "transport": "claude_code", "base_url": "http://[::1", "runtime_home": "/r", "models": []},
]


def build(case: dict):
    config = ProviderConfig(
        provider_key=case["provider_key"], transport=case["transport"], base_url=case["base_url"],
        runtime_home=case.get("runtime_home", ""), default_headers=dict(case.get("default_headers", [])),
        api_key=case.get("api_key", ""), models=[model(m, t) for m, t in case["models"]],
        default_model=case.get("default_model", ""),
        retry_config=None if "retry" not in case else RetryConfig(
            **{**case["retry"], "retryable_statuses": frozenset(case["retry"]["retryable_statuses"])}),
    )
    provider = ProviderFactory.create(config)
    if isinstance(provider, OpenAICompatProvider):
        return {"kind": "OpenAICompatProvider", "base_url": provider._base_url, "model_id": provider.model_info.id,
                "model_name": provider.model_info.name, "model_transport": provider.model_info.transport,
                "retry": retry(provider._retry_config), "timeouts": asdict(provider._timeout_config),
                "default_headers": list(provider._default_headers.items())}
    return {"kind": provider.kind}


def main() -> int:
    records = []
    for case in CASES:
        try:
            records.append({"config": case, "value": build(case)})
        except Exception as error:  # noqa: BLE001
            records.append({"config": case, "error": {"class": type(error).__name__, "message": str(error)}})
    OUTPUT.write_bytes((json.dumps({"cases": records}, ensure_ascii=True, indent=0) + "\n").encode("ascii"))
    print(f"{len(records)} cases -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
