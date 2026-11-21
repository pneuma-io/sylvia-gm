"""Generate reference vectors for the read-only provider settings (config C2).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe crates/sylvia-provider/tests/vectors/generate_settings_vectors.py

Writes `settings_vectors.json`: `provider_settings` replies (as the owner sends them, through
`_json_value`) over settings files, `_discovery_model` over service catalogue rows,
`resolve_context_capacity`, `require_configured_model` and the session readers. Protection is
the same deterministic fake seal as `generate_config_vectors.py`, and the clock is fixed; every
credential is a disposable test value. Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import datetime as real_datetime
import json
import os
import sys
import tempfile
from pathlib import Path

from sylvia_gm.config import _credential_protection as protection
from sylvia_gm.config import provider_settings as settings
from sylvia_gm.config._resolver import ConfigError
from sylvia_gm.interfaces.owner_process import _json_value
from sylvia_gm.provider._context_capacity import resolve_context_capacity

HERE = Path(__file__).parent
FIXED_NOW = "2026-11-21T08:00:00.123456+00:00"


def fake_seal(value: bytes, *, decrypt: bool) -> bytes:
    if decrypt:
        if not value.startswith(b"FAKE"):
            raise ConfigError(protection.UNAVAILABLE_MESSAGE)
        return value[4:][::-1]
    return b"FAKE" + value[::-1]


protection._platform_seal = fake_seal


class FixedDatetime(real_datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return real_datetime.datetime.fromisoformat(FIXED_NOW)


settings.datetime = FixedDatetime

TOKENS = {protection.UNAVAILABLE_MESSAGE: "<UNAVAILABLE>", protection._PROTECTION: "<PROTECTION>"}


def tokens(value):
    text = json.dumps(value, ensure_ascii=False)
    for original, token in TOKENS.items():
        text = text.replace(json.dumps(original, ensure_ascii=False)[1:-1], token)
    return json.loads(text)


def envelope_yaml(key: str, headers: dict) -> str:
    envelope = protection.protect_credentials(key, headers)
    return "{" + ", ".join(f"{k}: {json.dumps(v)}" for k, v in envelope.items()) + "}"


SEALED = envelope_yaml("sk-disposable", {"X-A": "1", "X-B": "2"})
SEALED_EMPTY = envelope_yaml("", {})

DOCUMENTS = [
    ("none", None),
    ("empty", ""),
    ("one-openai", "providers:\n  openai:\n    enabled: true\n    protected_credentials: " + SEALED + "\n"
     "    models:\n    - {id: gpt-6-sol, name: GPT-6 Sol, declared_context_window: 1050000, declared_context_source: model_catalog,"
     " runtime_context_window: 1050000, runtime_context_source: provider_metadata, max_output_tokens: 128000, transport: responses}\n"),
    ("legacy-window", "providers:\n  lm_studio:\n    enabled: true\n    models:\n    - {id: qwen, context_window: 32768}\n"),
    ("configured-cap", "providers:\n  ollama:\n    enabled: true\n    base_url: http://127.0.0.1:11434/v1\n    models:\n"
     "    - {id: m, runtime_context_window: 8192, configured_context_window: 16384, configured_context_observed_at: '2026-01-01'}\n"
     "    - {id: n, declared_context_window: 4096, configured_context_window: 2048}\n"),
    ("empty-window", "providers:\n  lm_studio:\n    enabled: true\n    models:\n    - {id: m, declared_context_window: ''}\n"),
    ("bad-window-type", "providers:\n  lm_studio:\n    enabled: true\n    models:\n    - {id: m, runtime_context_window: big}\n"),
    ("bad-window-bool", "providers:\n  lm_studio:\n    enabled: true\n    models:\n    - {id: m, configured_context_window: true}\n"),
    ("bad-window-range", "providers:\n  lm_studio:\n    enabled: true\n    models:\n    - {id: m, declared_context_window: 100}\n"),
    ("window-too-big", "providers:\n  lm_studio:\n    enabled: true\n    models:\n    - {id: m, context_window: 20000000}\n"),
    ("sources", "providers:\n  lm_studio:\n    enabled: true\n    models:\n    - {id: m, declared_context_window: 4096, declared_context_source: nonsense,"
     " runtime_context_window: 2048, runtime_context_source: runtime_reported, context_observed_at: '2026-02-02',"
     " runtime_context_observed_at: ''}\n"),
    ("no-id", "providers:\n  lm_studio:\n    enabled: true\n    models:\n    - {name: Nameless}\n    - {id: 0}\n    - {id: 7, name: Seven}\n"),
    ("custom", "providers:\n  custom_ab12:\n    enabled: true\n    preset_id: custom\n    name: Mine\n    mode: custom\n"
     "    base_url: http://localhost:9/v1\n    api_key: ''\n    models:\n    - {id: x, transport: claude_code, configured_output_tokens: 99}\n"),
    ("preset-from-raw", "providers:\n  my_groq:\n    enabled: true\n    preset_id: groq\n    base_url: https://api.groq.com/openai/v1\n"),
    ("unknown-preset", "providers:\n  my_thing:\n    enabled: true\n    preset_id: nope\n    base_url: http://h/v1\n"),
    ("disabled", "providers:\n  openai:\n    enabled: false\n    models: [{id: gpt-5}]\n"),
    ("unavailable", "providers:\n  openai:\n    enabled: true\n    protected_credentials: {version: 1, protection: other, data: x}\n"),
    ("empty-sealed-key", "providers:\n  openai:\n    enabled: true\n    protected_credentials: " + SEALED_EMPTY + "\n"),
    ("default-model-ok", "providers:\n  openai:\n    enabled: true\n    models: [{id: gpt-5}]\ndefault_model: {provider_id: openai, model_id: openai/gpt-5}\n"),
    ("default-model-missing", "providers:\n  openai:\n    enabled: true\n    models: [{id: gpt-5}]\ndefault_model: {provider_id: openai, model_id: openai/gpt-4}\n"),
    ("default-model-underscore", "providers:\n  lm_studio:\n    enabled: true\n    models: [{id: q}]\ndefault_model: {provider_id: lm_studio, model_id: lm-studio/q}\n"),
    ("default-model-bad", "default_model: [1]\n"),
    ("chat-defaults", "chat_defaults: {reasoning_effort: high, speed: fast}\n"),
    ("chat-defaults-bad", "chat_defaults: {reasoning_effort: High, speed: 'a b'}\n"),
    ("chat-defaults-long", "chat_defaults: {reasoning_effort: " + "a" * 33 + ", speed: " + "b" * 32 + "}\n"),
    ("chat-defaults-not-mapping", "chat_defaults: high\n"),
    ("row-extras", "providers:\n  openai:\n    enabled: true\n    models:\n    - {id: gpt-5, supported_reasoning_efforts: [low, high], default_reasoning_effort: low,"
     " service_tiers: [flex], supports_tools: true, supports_streaming: false, documented_source: s, documented_at: '2026-09-23',"
     " output_allowance_source: o, builder_output_tokens: 5, declared_max_output_tokens: 7}\n"),
    ("row-date", "providers:\n  openai:\n    enabled: true\n    models:\n    - {id: gpt-5, documented_at: 2026-09-23}\n"),
    ("row-set", "providers:\n  openai:\n    enabled: true\n    models:\n    - {id: gpt-5, service_tiers: !!set {b, a}}\n"),
    ("row-nan", "providers:\n  openai:\n    enabled: true\n    models:\n    - {id: gpt-5, supports_tools: .nan}\n"),
    ("sorted-ids", "providers:\n  zai:\n    enabled: true\n  anthropic:\n    enabled: true\n  b_custom:\n    enabled: true\n    base_url: http://b/v1\n"),
]

DISCOVERY_ROWS = [
    {}, {"id": 5}, {"id": "  "}, {"id": "x" * 257}, {"id": "a\tb"}, {"id": " padded "},
    {"id": "m"}, {"id": "m", "name": "  Shown  "}, {"id": "m", "name": "  "}, {"id": "m", "display_name": "Disp"},
    {"id": "m", "name": None, "display_name": "Disp"},
    {"id": "m", "context_length": 8192}, {"id": "m", "context_window": 8192}, {"id": "m", "context_length": 100},
    {"id": "m", "context_length": True}, {"id": "m", "context_length": None, "context_window": 9000},
    {"id": "m", "top_provider": {"context_length": 4096, "max_completion_tokens": 1024}},
    {"id": "m", "max_completion_tokens": 0}, {"id": "m", "max_output_tokens": 2048}, {"id": "m", "max_tokens": 3000},
    {"id": "m", "max_input_tokens": 200000, "max_tokens": 64000}, {"id": "m", "max_context_length": 128000},
    {"id": "m", "supported_parameters": ["tools", "stream"]}, {"id": "m", "supported_parameters": ["temperature"]},
    {"id": "m", "supported_parameters": "tools"}, {"id": "m", "supported_parameters": [1, "tool_choice"]},
    {"id": "m", "capabilities": {"completion_chat": False}}, {"id": "m", "capabilities": {"function_calling": True}},
    {"id": "m", "capabilities": {"function_calling": "yes"}}, {"id": "m", "type": "chat"}, {"id": "m", "type": "image"},
    {"id": "m", "type": None}, {"id": "m", "available_on_current_plan": False}, {"id": "m", "available_on_current_plan": 0},
    {"id": "m", "transport": "responses"}, {"id": "m", "transport": "bogus"}, {"id": "m", "transport": 3},
    {"id": "jev-1"}, {"id": "JEV-2"}, {"id": "gpt-6-sol"}, {"id": "gpt-6-sol", "context_length": 100000},
    {"id": "gpt-5.5"}, {"id": "o3-mini"}, {"id": "claude-opus-5-5"}, {"id": "gemini-3"}, {"id": "qwen3.8-max"},
    {"id": "minimax-m3"}, {"id": "openai/gpt-oss-120b"}, {"id": "deepseek-flash"},
]
DISCOVERY_PRESETS = [
    "openai", "anthropic", "mistral", "together", "featherless", "opencode_go", "opencode_zen", "lm_studio",
    "groq", "deepseek", "custom", "openrouter", "claude_runtime",
]

CAPACITY_CASES = [
    {}, {"declared_context_window": 8192}, {"runtime_context_window": 4096, "runtime_context_source": "runtime_reported"},
    {"declared_context_window": 2048, "declared_context_source": "model_catalog", "runtime_context_window": 4096,
     "runtime_context_source": "provider_metadata"},
    {"runtime_context_window": 4096, "configured_context_window": 4096},
    {"runtime_context_window": 8192, "configured_context_window": 4096, "observed_at": "t"},
    {"configured_context_window": 4096, "declared_context_window": 2048, "declared_context_source": "bogus"},
    {"legacy_context_window": 1000}, {"legacy_context_window": 1000, "configured_context_window": 2000},
    {"declared_context_window": 10, "declared_observed_at": "", "observed_at": "o"},
    {"runtime_context_window": 10, "runtime_observed_at": "r", "observed_at": "o"},
    {"configured_context_window": 10, "configured_observed_at": "c"},
]

REQUIRE_CASES = [
    ("openai", "openai/gpt-5"), ("openai", "openai/gpt-4"), ("Openai", "openai/gpt-5"), ("openai", " openai/gpt-5"),
    ("openai", ""), ("openai", "x" * 513), (5, "openai/gpt-5"), ("openai", None), ("a" * 65, "m"), ("lm_studio", "lm-studio/q"),
]


def reply(function):
    try:
        projected = _json_value(function())
        # The owner's `_write_message` then refuses non-finite floats.
        json.dumps(projected, allow_nan=False)
        return {"value": tokens(projected)}
    except ConfigError as error:
        return {"error": {"class": "ConfigError", "message": tokens(str(error))}}
    except Exception as error:  # noqa: BLE001
        return {"error": {"class": type(error).__name__, "message": tokens(str(error))}}


def main() -> int:
    for name in list(os.environ):
        if name.upper().startswith("SYLVIA_GM_"):
            del os.environ[name]
    documents = []
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "config.yaml"
        for name, text in DOCUMENTS:
            if path.exists():
                path.unlink()
            if text is not None:
                path.write_bytes(text.encode("utf-8"))
            record = {"name": name, "user": None if text is None else tokens(text)}
            record["provider_settings"] = reply(lambda: settings.provider_settings(path))
            record["session_speed"] = [
                reply(lambda sid=sid: settings.session_speed_settings(path, session_id=sid)) for sid in ("s1", "", 5)
            ]
            documents.append(record)
        require = []
        path.write_bytes(
            ("providers:\n  openai:\n    enabled: true\n    models: [{id: gpt-5}, {id: 3}, 'x']\n"
             "  lm_studio:\n    models: [{id: q}]\n"
             "session_speed: {s1: fast, s2: 'NO', s3: 5}\n"
             "session_model_once: {s1: {provider_id: openai, model_id: openai/gpt-5}, s2: {provider_id: openai, model_id: nope},"
             " s3: [1], s4: {provider_id: 9}}\n").encode()
        )
        for provider_id, model_id in REQUIRE_CASES:
            require.append({
                "provider_id": provider_id, "model_id": model_id,
                **reply(lambda p=provider_id, m=model_id: settings.require_configured_model(path, provider_id=p, model_id=m)),
            })
        sessions = []
        for sid in ("s1", "s2", "s3", "s4", "s5", "", None):
            sessions.append({
                "session_id": sid,
                "speed": reply(lambda s=sid: settings.session_speed_settings(path, session_id=s)),
                "model_once": reply(lambda s=sid: settings.session_model_once_settings(path, session_id=s)),
            })
    discovery = []
    for preset_id in DISCOVERY_PRESETS:
        for row in DISCOVERY_ROWS:
            discovery.append({"preset_id": preset_id, "row": row, **reply(lambda r=row, p=preset_id: settings._discovery_model(r, preset_id=p))})
    capacity = [{"inputs": case, **reply(lambda c=case: resolve_context_capacity(**c).projection())} for case in CAPACITY_CASES]
    document = {
        "python": sys.version.split()[0], "platform": sys.platform, "now": FIXED_NOW,
        "documents": documents, "require": require, "sessions": sessions, "discovery": discovery, "capacity": capacity,
    }
    (HERE / "settings_vectors.json").write_bytes((json.dumps(document, ensure_ascii=True, indent=0) + "\n").encode("ascii"))
    print(f"{len(documents)} documents, {len(discovery)} discovery rows -> {HERE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
