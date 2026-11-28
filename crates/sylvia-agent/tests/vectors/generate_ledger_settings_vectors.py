"""Write ledger_settings_vectors.json: the context ledger's settings, seal, contract and tag check.

Run from the repository root with the snapshot's interpreter:

    .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_ledger_settings_vectors.py

Every value is Python's own answer from `agent/_context_ledger.py`:

- `settings`: for each `SYLVIA_GM_TOOL_LIST` / `SYLVIA_GM_CONTEXT_MODE` value, transport,
  provider key and model id, `tool_list_setting`, `stable_tool_list`, `fixed_tool_list`,
  `deferred_tool_list` and `context_mode`;
- `seals`: `host_seal` and `seal_prefix` of session identities;
- `contracts`: `ledger_contract(seal)` whole;
- `tag_checks`: `check_fixed_blocks_tagged` on canonical lists (each message with its tags),
  `ok` or the exception's class and message;
- `markers`: `journal_marker` and `native_marker` (with `_stub_marker`) of saved records, each
  answer or the exception's class and message.

Reference tooling for the port; retired with the Python source.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.agent import _context_ledger as ledger  # noqa: E402

TOOL_LISTS = [None, "", "stable", " FIXED ", "Deferred", "rounds", "other", "ſtable", "deferred\t"]
MODES = [None, "ledger", " Legacy", "fast"]
ROUTES = [
    (None, None),
    ("chat_completions", "openai"),
    ("responses", "openai"),
    ("claude_code", "claude-subscription"),
    ("codex_app_server", "chatgpt-subscription"),
    ("anthropic_messages", "anthropic"),
    ("anthropic_messages", "openrouter"),
    ("anthropic_messages", None),
    ("gemini_generate_content", "google"),
]
MODELS = [None, "", "claude-opus-5-5", "claude-haiku-4-5", "claude-opus-4", "anthropic/claude-fable-5-2", "gpt-6.1"]


def settings() -> list[dict[str, object]]:
    cases = []
    for tool_list in TOOL_LISTS:
        for mode in MODES:
            environ = {}
            if tool_list is not None:
                environ[ledger.TOOL_LIST_ENV] = tool_list
            if mode is not None:
                environ[ledger.MODE_ENV] = mode
            for transport, provider_key in ROUTES:
                route = {"transport": transport, "provider_key": provider_key}
                # Only a registered deferral route reads the model; elsewhere two ids suffice.
                claude = transport == "claude_code" or provider_key == "anthropic"
                for model_id in MODELS if claude else MODELS[:1] + MODELS[-1:]:
                    cases.append({
                        "environ": environ,
                        "transport": transport,
                        "provider_key": provider_key,
                        "model_id": model_id,
                        "tool_list_setting": ledger.tool_list_setting(environ, **route),
                        "stable_tool_list": ledger.stable_tool_list(environ, **route),
                        "fixed_tool_list": ledger.fixed_tool_list(environ),
                        "deferred_tool_list": ledger.deferred_tool_list(
                            environ, **route, model_id=model_id),
                        "context_mode": ledger.context_mode(environ, **route),
                    })
    return cases


def seals() -> list[dict[str, str]]:
    return [
        {"session_id": session_id, "seal": ledger.host_seal(session_id),
         "prefix": ledger.seal_prefix(ledger.host_seal(session_id))}
        for session_id in ["", "dm-session-1", "séance-\U0001f3b2", "x" * 300]
    ]


def check(messages: list[dict[str, object]]) -> object:
    canonical = [ledger.Tagged(item["message"], item["tags"]) for item in messages]
    try:
        ledger.check_fixed_blocks_tagged(canonical)
    except Exception as error:  # noqa: BLE001 - the class is the vector
        return {"error": type(error).__name__, "message": str(error)}
    return "ok"


def entry(role: str, content: object, **tags: object) -> dict[str, object]:
    return {"message": {"role": role, "content": content}, "tags": tags}


TAG_CASES = {
    "empty": [],
    "tagged contract": [entry("system", "SYLVIA_GM_CONTEXT_CONTRACT v1", **{ledger.STABLE_KEY: True})],
    "untagged contract": [entry("system", "Any system text")],
    "contract falsy tag": [entry("system", "x", **{ledger.STABLE_KEY: 0})],
    "contract empty tag": [entry("system", "x", **{ledger.STABLE_KEY: ""})],
    "contract truthy string tag": [entry("system", "x", **{ledger.STABLE_KEY: "yes"})],
    "tag as a key": [{"message": {"role": "system", "content": "x", ledger.STABLE_KEY: True},
                      "tags": {}}],
    "tag key false, attribute true": [
        {"message": {"role": "system", "content": "x", ledger.STABLE_KEY: False},
         "tags": {ledger.STABLE_KEY: True}}],
    "user first": [entry("user", "SYLVIA_GM_RESPONSE_FORMAT")],
    "no role first": [{"message": {"content": "x"}, "tags": {}}],
    "later untagged fixed block": [
        entry("system", "contract", **{ledger.STABLE_KEY: True}),
        entry("user", "hello"),
        entry("system", "SYLVIA_GM_RULESET_OPERATING_GUIDE (fixed)"),
    ],
    "later untagged host record": [
        entry("system", "contract", **{ledger.STABLE_KEY: True}),
        entry("system", "SYLVIA_GM_FRESH_CAMPAIGN_STATE (data)"),
    ],
    "response format without space": [
        entry("system", "contract", **{ledger.STABLE_KEY: True}),
        entry("system", "SYLVIA_GM_RESPONSE_FORMATTING"),
    ],
    "ledger contract prefix needs its space": [
        entry("system", "contract", **{ledger.STABLE_KEY: True}),
        entry("system", "SYLVIA_GM_CONTEXT_LEDGER_CONTRACT(no space)"),
        entry("system", "SYLVIA_GM_CONTEXT_LEDGER_CONTRACT x"),
    ],
    "fixed block as a user message": [
        entry("system", "contract", **{ledger.STABLE_KEY: True}),
        entry("user", "SYLVIA_GM_CONTEXT_CONTRACT x"),
    ],
    "list content": [
        entry("system", "contract", **{ledger.STABLE_KEY: True}),
        entry("system", [{"type": "text", "text": "SYLVIA_GM_CONTEXT_CONTRACT x"}]),
    ],
    "missing content": [
        entry("system", "contract", **{ledger.STABLE_KEY: True}),
        {"message": {"role": "system"}, "tags": {}},
    ],
    "untagged first, fixed later": [
        entry("system", "x"),
        entry("system", "SYLVIA_GM_CONTEXT_CONTRACT x"),
    ],
}


def mark(function, value: dict[str, object]) -> object:
    try:
        return {"ok": function(value)}
    except Exception as error:  # noqa: BLE001 - the class is the vector
        return {"error": type(error).__name__, "message": str(error)}


COUNTED = [None, [], [1, 2], {}, {"a": 1}, "", "ab\U0001f3b2", 0, 5, 2.5, False, True]


def markers() -> list[dict[str, object]]:
    values: list[dict[str, object]] = [
        {},
        {"omitted_from_export": True, "kind": "gm_wire_journal", "entries": [1]},
        {"omitted_from_export": True},
        {"omitted_from_export": 1, "kind": "x"},
        {"omitted_from_export": "true", "kind": "x"},
        {"journal_record_id": "j-1", "epoch": "e", "generation": 2, "base_count": 3,
         "native_record_id": "n-1", "boundary": 4, "status": "collected", "extra": "private"},
    ]
    for counted in COUNTED:
        values.append({"entries": counted, "records": counted})
    return [{"value": value, "journal": mark(ledger.journal_marker, value),
             "native": mark(ledger.native_marker, value)} for value in values]


def main() -> None:
    contract_seals = [ledger.host_seal("dm-session-1"), "0" * 12]
    vectors = {
        "settings": settings(),
        "seals": seals(),
        "contracts": [{"seal": seal, "text": ledger.ledger_contract(seal)} for seal in contract_seals],
        "tag_checks": [{"name": name, "messages": messages, "outcome": check(messages)}
                       for name, messages in TAG_CASES.items()],
        "markers": markers(),
    }
    out = Path(__file__).with_name("ledger_settings_vectors.json")
    out.write_bytes((json.dumps(vectors, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
