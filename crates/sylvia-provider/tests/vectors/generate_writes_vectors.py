"""Generate reference vectors for the provider settings writes (config C4).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe crates/sylvia-provider/tests/vectors/generate_writes_vectors.py

Writes `writes_vectors.json`. Each case starts from a settings file (or none) and runs a script
of writes: `save_provider_settings`, `remove_provider_settings`, `set_default_model_settings`,
`set_chat_defaults_settings`, `set_session_speed_settings`, `set_session_model_once_settings`,
`clear_session_model_once_settings` and `refresh_subscription_reasoning`. For every step it
records the reply (as the owner sends it) or the failure's class and text, and the settings
file's exact text afterwards. Protection is the deterministic fake seal of the other provider
vectors and the clock is fixed; every credential is a disposable test value. Reference tooling
only; retired with the Python source.
"""

from __future__ import annotations

import datetime as real_datetime
import json
import os
import tempfile
from pathlib import Path

from sylvia_gm.config import _credential_protection as protection
from sylvia_gm.config import provider_settings as settings
from sylvia_gm.config._resolver import ConfigError
from sylvia_gm.interfaces.owner_process import _json_value

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


SEALED = envelope_yaml("sk-old", {"X-Old": "1"})
BROKEN = "{version: 1, protection: nope, data: AAAA}"

SAVE_ARGS = ("provider_id", "base_url", "credential", "model_id", "model_name", "context_window", "max_output_tokens")


def save(base=None, **over):
    return {"op": "save", "params": {**(base or {}), **over}}


def run(op: dict, path: Path):
    kind = op["op"]
    params = dict(op.get("params", {}))
    if kind == "save":
        for name in SAVE_ARGS:
            params.setdefault(name, None)
        return settings.save_provider_settings(path, **params)
    if kind == "remove":
        settings.remove_provider_settings(path, params.get("provider_id"))
        return None
    if kind == "default":
        return settings.set_default_model_settings(path, provider_id=params.get("provider_id"), model_id=params.get("model_id"))
    if kind == "chat_defaults":
        return settings.set_chat_defaults_settings(path, provider_id=params.get("provider_id"), model_id=params.get("model_id"),
                                                   reasoning_effort=params.get("reasoning_effort"), speed=params.get("speed"))
    if kind == "speed":
        return settings.set_session_speed_settings(path, session_id=params.get("session_id"), speed=params.get("speed"))
    if kind == "once":
        return settings.set_session_model_once_settings(path, session_id=params.get("session_id"),
                                                        provider_id=params.get("provider_id"), model_id=params.get("model_id"))
    if kind == "clear_once":
        return settings.clear_session_model_once_settings(
            path, session_id=params.get("session_id"), expected_provider_id=params.get("provider_id"),
            expected_model_id=params.get("model_id"))
    if kind == "refresh":
        return settings.refresh_subscription_reasoning(path, params["models"])
    raise AssertionError(kind)


GPT = {"provider_id": "openai", "preset_id": "openai", "credential": "sk-1", "model_id": "gpt-6-sol", "transport": "responses"}
LOCAL = {"provider_id": "lm_studio", "preset_id": "lm_studio", "model_id": "qwen", "model_name": "Qwen"}
CUSTOM = {"preset_id": "custom", "display_name": "My Service", "base_url": "https://example.test/v1/", "credential": "k",
          "model_id": "m1"}
CODEX = {"provider_id": "chatgpt_subscription", "preset_id": "chatgpt_subscription", "model_id": "gpt-5.5",
         "transport": "codex_app_server"}
CLAUDE = {"provider_id": "claude_runtime", "preset_id": "claude_runtime", "model_id": "claude-opus-4-6",
          "transport": "claude_code"}
OLD_FILE = ("providers:\n  openai:\n    enabled: true\n    preset_id: openai\n    protected_credentials: " + SEALED + "\n"
            "    models:\n    - {id: gpt-6-sol, name: Sol, builder_output_tokens: 1000, configured_context_window: 200000,"
            " configured_context_observed_at: '2026-01-01'}\n    - {id: old, context_window: 9000}\n    - not-a-row\n"
            "default_model: {provider_id: openai, model_id: openai/gpt-6-sol}\n"
            "session_model_once: {s1: {provider_id: openai, model_id: openai/gpt-6-sol}, s2: {provider_id: x, model_id: y}}\n"
            "session_speed: {a: fast, b: flex}\nchat_defaults: {reasoning_effort: high, speed: null}\n")

CASES = [
    {"name": "gpt-fresh", "user": None, "ops": [save(GPT), save(GPT, max_output_tokens=500), save(GPT, transport="chat_completions")]},
    {"name": "gpt-update", "user": OLD_FILE, "ops": [
        save(**{**GPT, "credential": None}), save(**{**GPT, "credential": "", "model_id": "old", "max_output_tokens": 2000}),
        save(**{**GPT, "credential": None, "model_id": "old", "context_window": 600}),
        save(**{**GPT, "credential": None, "model_id": "new", "transport": None, "builder_output_tokens": 70}),
        save(**{**GPT, "credential": None, "headers": {"X-New": "v"}}),
    ]},
    {"name": "broken-credentials", "user": "providers:\n  openai:\n    enabled: true\n    protected_credentials: " + BROKEN + "\n    models: []\n",
     "ops": [save(**{**GPT, "credential": None}), save(**{**GPT, "credential": "  sk-new  "}), save(**{**GPT, "credential": None})]},
    {"name": "local", "user": None, "ops": [
        save(LOCAL), save(LOCAL, context_window=8192), save(LOCAL, max_output_tokens=4096, declared_context_window=4096),
        save(LOCAL, runtime_context_window=32768, runtime_context_source="runtime_reported", runtime_context_observed_at="t1",
             configured_context_window=16384, declared_context_window="", declared_max_output_tokens=8000),
        save(LOCAL, base_url="  http://127.0.0.1:4321/v1//  ", context_observed_at="t2", declared_context_window=65536,
             declared_context_source="provider_metadata"),
        save(LOCAL, model_name=0, supports_streaming=True, supports_tools=1),
        save(LOCAL, builder_output_tokens=0), save(LOCAL, max_output_tokens=10_000_001), save(LOCAL, context_window=511),
        save(LOCAL, context_window=True), save(LOCAL, max_output_tokens=9000, declared_context_window=8192),
    ]},
    {"name": "custom", "user": None, "ops": [
        save(CUSTOM), save(CUSTOM, display_name="MY SERVICE"), save(CUSTOM, display_name="other", base_url="https://other.test"),
        save(CUSTOM, base_url="ftp://x.test"), save(CUSTOM, base_url="https://user@x.test"), save(CUSTOM, base_url="https://x.test/?q=1"),
        save(CUSTOM, base_url="https://x.test/#f"), save(CUSTOM, base_url="https://[::1/v1"), save(CUSTOM, base_url="https:///v1"),
        save(CUSTOM, display_name="x" * 121), save(CUSTOM, display_name="a\tb"), save(CUSTOM, provider_id="Bad"),
        save(CUSTOM, provider_id="custom"), save(CUSTOM, provider_id="mine"), save(CUSTOM, credential=None),
        save(CUSTOM, model_id="  "), save(CUSTOM, model_id=5), save(CUSTOM, model_id="a" * 257),
        save(CUSTOM, model_name="n" * 129), save(CUSTOM, transport="pigeon"), save(CUSTOM, transport=""),
        save(CUSTOM, headers={"X-A": "1", "x-a": "2"}), save(CUSTOM, headers={"Bad Name": "1"}), save(CUSTOM, headers={"X": ""}),
        save(CUSTOM, headers={"X": "a\nb"}), save(CUSTOM, headers={"X": "a\tb"}), save(CUSTOM, headers=["X"]),
        save(CUSTOM, headers={f"H{i}": "v" for i in range(17)}), save(CUSTOM, headers={"X": "v" * 1025}),
    ]},
    {"name": "real-collision", "user": "providers:\n  custom_25dbafce3a82f915: {base_url: 'https://different.test'}\n",
     "ops": [save(CUSTOM)]},
    {"name": "same-identity", "user": "providers:\n  custom_25dbafce3a82f915: {base_url: 'https://example.test/v1///'}\n",
     "ops": [save(CUSTOM)]},
    {"name": "collision", "user": None, "ops": [save(CUSTOM), {"op": "speed", "params": {"session_id": "s", "speed": "fast"}}]},
    {"name": "presets", "user": None, "ops": [
        save(provider_id="openrouter", preset_id="openrouter", credential="k", model_id="x", base_url="https://elsewhere.test"),
        save(provider_id="opencode_go", preset_id="opencode_go", credential="k", model_id="minimax-m3", base_url="https://opencode.ai/zen/go/v1/"),
        save(provider_id="opencode_go", preset_id="opencode_go", credential="k", model_id="deepseek-v4"),
        save(provider_id="groq", preset_id="groq", credential="k", model_id="openai/gpt-oss-120b"),
        save(provider_id="deepseek", preset_id="deepseek", credential="k", model_id="deepseek-flash", max_output_tokens=100),
        save(provider_id="x", preset_id="nope", model_id="m"), save(provider_id="x", preset_id=None, model_id="m"),
        save(provider_id="openai", preset_id="", credential="k", model_id="gpt-6-luna"),
        save(provider_id="gemini", preset_id="gemini", credential="k", model_id="gemini-3-pro"),
    ]},
    {"name": "codex", "user": None, "ops": [
        save(CODEX), save(CODEX, credential="k"), save(CODEX, headers={"X": "1"}),
        save(CODEX, supported_reasoning_efforts=["low", "high"], default_reasoning_effort="high",
             service_tiers=[{"id": "priority", "name": " Priority ", "description": "d"}]),
        save(CODEX, max_output_tokens=100), save(CODEX, supported_reasoning_efforts=["Bad"]),
        save(CODEX, service_tiers="fast"), save(**{**CODEX, "transport": "responses"}),
        save(provider_id="openai", preset_id="openai", credential="k", model_id="x", transport="codex_app_server"),
        {"op": "refresh", "params": {"models": [{"id": "gpt-5.5", "supported_reasoning_efforts": ["medium"], "default_reasoning_effort": "medium",
                                                 "service_tiers": [{"id": "flex", "name": "Flex"}]}]}},
        {"op": "refresh", "params": {"models": [{"id": "gpt-5.5", "supported_reasoning_efforts": ["medium"], "default_reasoning_effort": "medium",
                                                 "service_tiers": [{"id": "flex", "name": "Flex"}]}]}},
        {"op": "refresh", "params": {"models": [{"id": "gpt-5.5", "service_tiers": "bad"}]}},
        {"op": "refresh", "params": {"models": []}},
        {"op": "refresh", "params": {"models": [{"id": "gpt-5.5", "supported_reasoning_efforts": ["Bad"]}]}},
    ]},
    {"name": "refresh-legacy", "user": "providers:\n  chatgpt_subscription:\n    models:\n    - {id: a, transport: codex_app_server}\n"
     "    - {id: b, transport: codex_app_server, service_tiers: []}\n    - {id: c, transport: responses}\n    - 7\n",
     "ops": [{"op": "refresh", "params": {"models": []}}, {"op": "refresh", "params": {"models": [{"id": "b"}, {"id": 3}]}}]},
    {"name": "refresh-none", "user": "providers: {}\n", "ops": [{"op": "refresh", "params": {"models": []}}]},
    {"name": "claude", "user": None, "ops": [
        save(CLAUDE), save(CLAUDE, credential="k"), save(CLAUDE, max_output_tokens=100),
        save(**{**CLAUDE, "provider_id": "claude_two"}), save(**{**CLAUDE, "transport": "chat_completions"}),
    ]},
    {"name": "defaults-and-sessions", "user": OLD_FILE, "ops": [
        {"op": "default", "params": {"provider_id": "openai", "model_id": "openai/old"}},
        {"op": "default", "params": {"provider_id": "openai", "model_id": "openai/missing"}},
        {"op": "default", "params": {"provider_id": "Open", "model_id": "x"}},
        {"op": "default", "params": {"provider_id": "openai", "model_id": ""}},
        {"op": "chat_defaults", "params": {"provider_id": "openai", "model_id": "openai/old", "reasoning_effort": "low", "speed": None}},
        {"op": "chat_defaults", "params": {"provider_id": "openai", "model_id": "openai/old", "reasoning_effort": None, "speed": None}},
        {"op": "chat_defaults", "params": {"provider_id": "openai", "model_id": "openai/old", "reasoning_effort": "Bad", "speed": None}},
        {"op": "chat_defaults", "params": {"provider_id": "openai", "model_id": "openai/nope", "reasoning_effort": None, "speed": "fast"}},
        {"op": "speed", "params": {"session_id": "a", "speed": "fast"}},
        {"op": "speed", "params": {"session_id": "c", "speed": None}},
        {"op": "speed", "params": {"session_id": "a", "speed": "flex"}},
        {"op": "speed", "params": {"session_id": "b", "speed": None}},
        {"op": "speed", "params": {"session_id": " a", "speed": None}},
        {"op": "speed", "params": {"session_id": "a", "speed": "Fast"}},
        {"op": "speed", "params": {"session_id": "a", "speed": None}},
        {"op": "once", "params": {"session_id": "s3", "provider_id": "openai", "model_id": "openai/old"}},
        {"op": "once", "params": {"session_id": "s3", "provider_id": "openai", "model_id": "openai/zzz"}},
        {"op": "once", "params": {"session_id": "", "provider_id": "openai", "model_id": "openai/old"}},
        {"op": "clear_once", "params": {"session_id": "s1", "provider_id": "openai", "model_id": "openai/old"}},
        {"op": "clear_once", "params": {"session_id": "s1", "provider_id": "openai", "model_id": "openai/gpt-6-sol"}},
        {"op": "clear_once", "params": {"session_id": "", "provider_id": "openai", "model_id": "openai/gpt-6-sol"}},
        {"op": "remove", "params": {"provider_id": "openai"}},
        {"op": "remove", "params": {"provider_id": "x"}},
        {"op": "remove", "params": {"provider_id": "X"}},
        {"op": "clear_once", "params": {"session_id": "s2", "provider_id": "x", "model_id": "y"}},
    ]},
    {"name": "speed-limit", "user": "session_speed: {" + ", ".join(f"s{i}: fast" for i in range(500)) + "}\n",
     "ops": [{"op": "speed", "params": {"session_id": "new", "speed": "flex"}}, {"op": "speed", "params": {"session_id": "s7", "speed": None}}]},
    {"name": "bad-document", "user": "providers: [1]\nsession_model_once: 5\n", "ops": [
        save(CUSTOM), {"op": "remove", "params": {"provider_id": "x"}},
        {"op": "once", "params": {"session_id": "s", "provider_id": "x", "model_id": "x/y"}},
        {"op": "clear_once", "params": {"session_id": "s", "provider_id": "x", "model_id": "y"}},
    ]},
    {"name": "bad-existing", "user": "providers:\n  custom_a: 5\n  lm_studio: {models: 5}\n  openai: null\n", "ops": [
        save(CUSTOM, provider_id="custom_a"), save(LOCAL), save(GPT),
    ]},
    {"name": "bad-builder", "user": "providers:\n  lm_studio:\n    models:\n    - {id: qwen, builder_output_tokens: 0}\n", "ops": [save(LOCAL)]},
]


def outcome(op: dict, path: Path) -> dict:
    try:
        value = run(op, path)
        record = {"value": _json_value(value)}
    except ConfigError as error:
        record = {"error": {"class": "ConfigError", "message": str(error)}}
    except Exception as error:  # noqa: BLE001
        record = {"error": {"class": type(error).__name__, "message": str(error)}}
    record["file"] = path.read_text(encoding="utf-8") if path.exists() else None
    return record


def main() -> int:
    for name in list(os.environ):
        if name.upper().startswith("SYLVIA_GM_"):
            del os.environ[name]
    records = []
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "config.yaml"
        for case in CASES:
            if path.exists():
                path.unlink()
            if case["user"] is not None:
                path.write_bytes(case["user"].encode("utf-8"))
            steps = [{"op": op, **outcome(op, path)} for op in case["ops"]]
            records.append(tokens({"name": case["name"], "user": case["user"], "steps": steps}))
    output = HERE / "writes_vectors.json"
    output.write_bytes((json.dumps({"now": FIXED_NOW, "cases": records}, ensure_ascii=True, indent=0) + "\n").encode("ascii"))
    print(f"{sum(len(case['steps']) for case in records)} steps -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
