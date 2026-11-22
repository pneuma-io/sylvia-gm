"""Generate reference vectors for sylvia-provider's ChatGPT account (lane 5, PR 5).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-provider/tests/vectors/generate_chatgpt_vectors.py

Recorded: `datetime.fromisoformat` over a battery of strings (`isoformat_battery.py`);
`subscription_model_facts` over model catalogs at a fixed time; `ChatGPTAccount` and
`chatgpt_settings_action` run on Python's real `CodexConnection` against `chatgpt_standin.py`,
a scripted Codex app-server (the interpreter is the runtime, the stand-in its `app-server`
script); their results, errors, the lines the stand-in received, the account changes
announced, the browser links opened and the config file written.

Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import datetime as real_datetime
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from isoformat_battery import battery  # noqa: E402

from sylvia_gm.config import _credential_protection as protection  # noqa: E402
from sylvia_gm.config import chatgpt_settings  # noqa: E402
from sylvia_gm.config import provider_settings as settings  # noqa: E402
from sylvia_gm.config._resolver import ConfigError  # noqa: E402
from sylvia_gm.provider import _chatgpt_model_facts, _codex_rpc  # noqa: E402
from sylvia_gm.provider._chatgpt_account import ChatGPTAccount  # noqa: E402
from sylvia_gm.provider._codex_rpc import CodexConnection, CodexConnectionError  # noqa: E402

OUTPUT = HERE / "chatgpt_vectors.json"
STANDIN = HERE / "chatgpt_standin.py"
FIXED_NOW = "2026-11-23T09:30:00.123456+00:00"


def isoformat_cases() -> list[dict]:
    out = []
    for text in battery():
        try:
            parsed = real_datetime.datetime.fromisoformat(text)
        except ValueError:
            out.append({"text": text, "parsed": None})
            continue
        offset = parsed.utcoffset()
        out.append({"text": text, "parsed": parsed.isoformat(),
                    "offset_micros": None if offset is None else offset // real_datetime.timedelta(microseconds=1)})
    return out


# ── Model facts ──────────────────────────────────────────────────────────────────────────

class FixedDatetime(real_datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return real_datetime.datetime.fromisoformat(FIXED_NOW)


_chatgpt_model_facts.datetime = FixedDatetime
settings.datetime = FixedDatetime


def fake_seal(value: bytes, *, decrypt: bool) -> bytes:
    if decrypt:
        if not value.startswith(b"FAKE"):
            raise ConfigError(protection.UNAVAILABLE_MESSAGE)
        return value[4:][::-1]
    return b"FAKE" + value[::-1]


protection._platform_seal = fake_seal

ROW = {"slug": "gpt-6-sol", "context_window": 400000, "effective_context_window_percent": 95}


def catalog(fetched=FIXED_NOW, rows=None, **extra):
    return {"client_version": "0.99.0", "fetched_at": fetched, "models": [ROW] if rows is None else rows, **extra}


def facts_cases() -> list[dict]:
    ids = ["gpt-6-sol", "gpt-6-luna", "codex-mini"]

    def case(name, content, model_ids=ids):
        return {"name": name, "content": content, "ids": model_ids}

    dump = json.dumps
    return [
        case("no-catalog", None),
        case("fresh", dump(catalog())),
        case("utf16", json.dumps(catalog()).encode("utf-16").hex()),
        case("a-minute-ahead", dump(catalog("2026-11-23T09:34:00+00:00"))),
        case("five-minutes-ahead", dump(catalog("2026-11-23T09:35:00.123456+00:00"))),
        case("over-five-minutes-ahead", dump(catalog("2026-11-23T09:35:00.123457+00:00"))),
        case("a-day-old", dump(catalog("2026-11-22T09:30:00.123456+00:00"))),
        case("over-a-day-old", dump(catalog("2026-11-22T09:30:00.123455+00:00"))),
        case("offset", dump(catalog("2026-11-23T11:30:00+02:00"))),
        case("naive", dump(catalog("2026-11-23T09:30:00"))),
        case("week-date", dump(catalog("2026-W48-1T09:30Z"))),
        case("bad-date", dump(catalog("yesterday"))),
        case("date-not-text", dump(catalog(5))),
        case("no-date", dump({"client_version": "1", "models": [ROW]})),
        case("no-version", dump({"fetched_at": FIXED_NOW, "models": [ROW]})),
        case("version-not-text", dump(catalog(client_version=1))),
        case("not-a-dict", dump([1])),
        case("models-not-list", dump(catalog(rows={"a": 1}))),
        case("rows", dump(catalog(rows=[
            5, {"slug": "other", "context_window": 1, "effective_context_window_percent": 1},
            {"slug": "gpt-6-luna", "context_window": 1000, "effective_context_window_percent": 33},
            {"slug": "codex-mini", "context_window": 1, "effective_context_window_percent": 50},
            {"slug": "gpt-6-sol", "context_window": True, "effective_context_window_percent": 95},
            {"slug": "gpt-6-sol", "context_window": 10_000_001, "effective_context_window_percent": 95},
            {"slug": "gpt-6-sol", "context_window": 400000.0, "effective_context_window_percent": 95},
            {"slug": "gpt-6-sol", "context_window": 400000, "effective_context_window_percent": 101},
            {"slug": "gpt-6-sol", "context_window": 400000, "effective_context_window_percent": 50},
            {"slug": 7, "context_window": 400000, "effective_context_window_percent": 50},
        ]))),
        case("unhashable-slug", dump(catalog(rows=[
            {"slug": "gpt-6-luna", "context_window": 1000, "effective_context_window_percent": 50},
            {"slug": ["x"], "context_window": 1, "effective_context_window_percent": 1}, ROW]))),
        case("not-json", "{nope"),
        case("too-large", "@too-large"),
        case("empty-ids", dump(catalog()), []),
    ]


def catalog_bytes(case: dict) -> bytes | None:
    content = case["content"]
    if content is None:
        return None
    if case["name"] == "utf16":
        return bytes.fromhex(content)
    if content == "@too-large":
        return b" " * (8 * 1024 * 1024 + 1)
    return content.encode("utf-8")


def run_facts(case: dict, base: Path) -> dict:
    home = base / f"facts-{case['name']}"
    home.mkdir(parents=True)
    data = catalog_bytes(case)
    if data is not None:
        (home / "models_cache.json").write_bytes(data)
    found = _chatgpt_model_facts.subscription_model_facts(home, set(case["ids"]))
    return {**case, "facts": {key: found[key] for key in sorted(found)}}


# ── The account, on a real connection to the stand-in ────────────────────────────────────

CHANGES: list[str] = []
_codex_rpc.add_account_change_listener(lambda home: CHANGES.append("changed"))

SIGNED_IN = {"account": {"type": "chatgpt", "planType": "plus", "email": "player@example.com", "token": "secret"}}
SIGNED_OUT = {"account": None}


def read(result=SIGNED_OUT, notify=()):
    return {"method": "account/read", "result": result, "notify": list(notify)}


def login_start(url="https://auth.openai.com/oauth/authorize?x=1", login_id="L1", kind="chatgpt", notify=()):
    return {"method": "account/login/start", "result": {"type": kind, "loginId": login_id, "authUrl": url},
            "notify": list(notify)}


def completed(success=True, login_id="L1"):
    return {"method": "account/login/completed", "params": {"loginId": login_id, "success": success}}


CANCEL = {"method": "account/login/cancel", "result": {}}
LOGOUT = {"method": "account/logout", "result": {}}
ITEMS = [
    {"model": "gpt-6-sol", "displayName": "GPT-6 Sol",
     "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "high"}, {"reasoningEffort": "low"},
                                   {"reasoningEffort": ""}, {"x": 1}, "medium"],
     "defaultReasoningEffort": "high",
     "serviceTiers": [{"id": "fast", "name": "Fast", "description": "Quicker."}, {"id": "flex", "name": "Flex"},
                      {"id": "", "name": "x"}, {"id": "y"}, 5],
     "inputModalities": ["text", "image"]},
    {"model": "gpt-6-luna", "defaultReasoningEffort": "max", "supportedReasoningEfforts": "low"},
    {"model": "image-only", "inputModalities": ["image"]},
    {"model": "odd", "inputModalities": "text", "displayName": None},
    "not-a-dict", {"displayName": "no id"}, {"model": 5},
]


def model_list(items=ITEMS, cursor=None):
    return {"method": "model/list", "result": {"data": items, "nextCursor": cursor}}


def account_cases() -> list[dict]:
    return [
        {"name": "status-signed-out", "steps": [read()], "ops": ["status"]},
        {"name": "status-signed-in", "steps": [read(SIGNED_IN)], "ops": ["status"]},
        {"name": "status-odd-account", "steps": [read({"account": {"type": "apiKey"}}), read({"account": "x"}),
                                                 read({"account": {"type": "chatgpt", "planType": 5}})],
         "ops": ["status", "status", "status"]},
        {"name": "login-completes", "steps": [login_start(notify=[completed()]), read(SIGNED_IN)],
         "ops": ["begin_login", "status"]},
        {"name": "login-pending-then-fails", "steps": [login_start(), read(), read(notify=[completed(False)]), read()],
         "ops": ["begin_login", "status", "status", "status"]},
        {"name": "completion-for-another-login",
         "steps": [login_start(notify=[completed(True, "L9"), completed(False, "L1")]), read()],
         "ops": ["begin_login", "status"]},
        {"name": "odd-notifications", "steps": [login_start(notify=[
            {"method": "account/login/completed", "params": "x"}, {"method": "other", "params": {"loginId": "L1"}},
            {"method": "account/login/completed"}]), read()], "ops": ["begin_login", "status"]},
        {"name": "login-twice", "steps": [login_start(), CANCEL, login_start(login_id="L2")],
         "ops": ["begin_login", "begin_login"]},
        {"name": "login-http-link", "steps": [login_start("http://auth.openai.com/x"), CANCEL],
         "ops": ["begin_login", "cancel_login"]},
        {"name": "login-other-host", "steps": [login_start("https://evil.example/x"), CANCEL], "ops": ["begin_login"]},
        {"name": "login-user-info", "steps": [login_start("https://a@auth.openai.com/x"), CANCEL], "ops": ["begin_login"]},
        {"name": "login-port", "steps": [login_start("https://auth.chatgpt.com:8443/x"), CANCEL,
                                         login_start("https://AUTH.CHATGPT.COM:443/x")],
         "ops": ["begin_login", "begin_login"]},
        {"name": "login-bad-port", "steps": [login_start("https://auth.openai.com:99999/x"), CANCEL],
         "ops": ["begin_login"]},
        {"name": "login-no-id", "steps": [login_start(login_id="")], "ops": ["begin_login"]},
        {"name": "login-wrong-type", "steps": [login_start(kind="apiKey"), CANCEL], "ops": ["begin_login"]},
        {"name": "cancel-without-login", "steps": [], "ops": ["cancel_login"]},
        {"name": "logout", "steps": [login_start(), CANCEL, LOGOUT, read()], "ops": ["begin_login", "logout"]},
        {"name": "models", "steps": [read(SIGNED_IN), model_list(ITEMS[:3], "c1"), model_list(ITEMS[3:])],
         "ops": ["models"], "catalog": catalog(rows=[ROW, {"slug": "image-only", "context_window": 1000,
                                                             "effective_context_window_percent": 90}])},
        {"name": "models-signed-out", "steps": [read()], "ops": ["models"]},
        {"name": "models-bad-list", "steps": [read(SIGNED_IN), {"method": "model/list", "result": {"data": {}}}],
         "ops": ["models"]},
        {"name": "models-cursor-repeats", "steps": [read(SIGNED_IN), model_list([], "c1"), model_list([], "c1")],
         "ops": ["models"]},
        {"name": "models-cursor-not-text", "steps": [read(SIGNED_IN), model_list([], 5)], "ops": ["models"]},
        {"name": "runtime-rejects", "steps": [{"method": "account/read", "error": {"code": 1, "message": "no"}}],
         "ops": ["status"]},
        {"name": "unsupported-reply", "steps": [{"method": "account/read", "result": [1]}], "ops": ["status"]},
    ]


def standin_home(home: Path, steps: list, catalog_value=None) -> Path:
    (home / "workspace").mkdir(parents=True)
    shutil.copy(STANDIN, home / "workspace" / "app-server")
    (home / "standin.json").write_text(json.dumps({"steps": steps}), encoding="utf-8")
    if catalog_value is not None:
        (home / "models_cache.json").write_text(json.dumps(catalog_value), encoding="utf-8")
    return home


def log_of(home: Path) -> list:
    path = home / "standin-log.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def run_account(case: dict, base: Path) -> dict:
    home = standin_home((base / case["name"] / "chatgpt-runtime").resolve(), case["steps"], case.get("catalog"))
    connection = CodexConnection(home, executable=Path(sys.executable))
    account = ChatGPTAccount(connection)
    CHANGES.clear()
    results = []
    for op in case["ops"]:
        try:
            results.append({"value": getattr(account, op)()})
        except CodexConnectionError as exc:
            results.append({"error": [type(exc).__name__, str(exc)]})
    connection.close()
    return {**case, "results": results, "changes": len(CHANGES), "log": log_of(home)}


# ── The settings action ──────────────────────────────────────────────────────────────────

OPENED: list[str] = []
BROWSER = {"opens": True}


def fake_open(url: str) -> bool:
    OPENED.append(url)
    return BROWSER["opens"]


chatgpt_settings.open_in_browser = fake_open
chatgpt_settings.CodexConnection = lambda home: CodexConnection(home, executable=Path(sys.executable))

ADD = {"id": "gpt-6-sol", "context_window": None, "max_output_tokens": None}
LISTED = [read(SIGNED_IN), read(SIGNED_IN), model_list(ITEMS[:3])]


def settings_cases() -> list[dict]:
    return [
        {"name": "unknown-action", "steps": [], "calls": [["delete", None], [5, None]]},
        {"name": "status", "steps": [read(SIGNED_IN)], "calls": [["status", None]]},
        {"name": "login", "steps": [login_start()], "calls": [["login", None]]},
        {"name": "login-browser-fails", "browser": False, "steps": [login_start(), CANCEL], "calls": [["login", None]]},
        {"name": "cancel", "steps": [read()], "calls": [["cancel", None]]},
        {"name": "logout", "steps": [LOGOUT, read()], "calls": [["logout", None]]},
        {"name": "models", "steps": LISTED, "calls": [["models", None]]},
        {"name": "add-model", "steps": LISTED, "calls": [["add_model", ADD]], "catalog": catalog(rows=[ROW])},
        {"name": "add-model-capacities", "steps": LISTED * 4,
         "calls": [["add_model", {**ADD, "context_window": 200000, "max_output_tokens": 64000}],
                   ["add_model", {**ADD, "context_window": 511}],
                   ["add_model", {**ADD, "max_output_tokens": 10_000_001}],
                   ["add_model", {**ADD, "context_window": 300000.0}]]},
        {"name": "add-model-refused", "steps": LISTED * 3,
         "calls": [["add_model", {"id": "gpt-6-sol"}], ["add_model", {**ADD, "id": "nope"}],
                   ["add_model", {**ADD, "id": "image-only"}]]},
        {"name": "add-then-refresh", "steps": LISTED + LISTED + [read(SIGNED_IN), read(SIGNED_IN),
                                                                model_list([{**ITEMS[0], "serviceTiers": []}])],
         "calls": [["add_model", ADD], ["add_model", {**ADD, "context_window": 100000}], ["models", None]]},
        # A smaller window the runtime reported earlier is kept over the catalog's.
        {"name": "prior-reported-window", "steps": LISTED, "calls": [["add_model", ADD]],
         "catalog": catalog(rows=[ROW]), "seed": seeded("250000", "runtime_reported")},
        # With no catalog figure, the saved one is kept whatever its source.
        {"name": "prior-catalog-window", "steps": LISTED, "calls": [["add_model", ADD]],
         "seed": seeded("250000", "model_catalog")},
        # A smaller saved catalog figure does not replace the current catalog's.
        {"name": "prior-smaller-catalog-window", "steps": LISTED, "calls": [["add_model", ADD]],
         "catalog": catalog(rows=[ROW]), "seed": seeded("250000", "model_catalog")},
        # A larger reported window does not replace the catalog's.
        {"name": "prior-larger-window", "steps": LISTED, "calls": [["add_model", ADD]],
         "catalog": catalog(rows=[ROW]), "seed": seeded("500000", "runtime_reported")},
        # A saved legacy model without speed options gains them from the listing.
        {"name": "refresh-legacy", "steps": LISTED, "calls": [["models", None]],
         "seed": seeded("null", "unknown").replace("      service_tiers: []\n", "")},
    ]


def seeded(window: str, source: str) -> str:
    """A saved ChatGPT model with a runtime window from `source`."""
    return f"""providers:
  chatgpt_subscription:
    enabled: true
    preset_id: chatgpt_subscription
    mode: subscription
    name: ChatGPT subscription
    base_url: https://chatgpt.com
    api_key: ''
    headers: {{}}
    models:
    - id: gpt-6-sol
      name: GPT-6 Sol (ChatGPT)
      declared_context_window: 1050000
      declared_context_source: model_catalog
      runtime_context_window: {window}
      runtime_context_source: {source}
      runtime_context_observed_at: '2026-11-20T08:00:00+00:00'
      declared_max_output_tokens: 100000
      transport: codex_app_server
      supports_streaming: true
      supports_tools: true
      service_tiers: []
"""


def run_settings(case: dict, base: Path) -> dict:
    root = (base / f"settings-{case['name']}" / "owner")
    root.mkdir(parents=True)
    root = root.resolve()
    home = standin_home(root / "chatgpt-runtime", case["steps"], case.get("catalog"))
    if case.get("seed") is not None:
        (root / "config.yaml").write_text(case["seed"], encoding="utf-8", newline="\n")
    BROWSER["opens"] = case.get("browser", True)
    OPENED.clear()
    CHANGES.clear()
    results = []
    for action, model in case["calls"]:
        try:
            results.append({"value": chatgpt_settings.chatgpt_settings_action(root, action, model)})
        except CodexConnectionError as exc:
            results.append({"error": [type(exc).__name__, str(exc)]})
    account = chatgpt_settings._accounts.pop(home, None)
    if account is not None:
        account.connection.close()
    config = root / "config.yaml"
    return {**case, "results": results, "opened": list(OPENED), "changes": len(CHANGES), "log": log_of(home),
            "config": config.read_text(encoding="utf-8") if config.exists() else None}


def main() -> int:
    if sys.platform != "win32":
        raise SystemExit("Generate these vectors on Windows, where the stand-in's interpreter is an .exe.")
    base = Path(tempfile.mkdtemp(prefix="sylvia-chatgpt-"))
    try:
        vectors = {
            "python": sys.version.split()[0], "now": FIXED_NOW, "isoformat": isoformat_cases(),
            "facts": [run_facts(case, base) for case in facts_cases()],
            "accounts": [run_account(case, base) for case in account_cases()],
            "settings": [run_settings(case, base) for case in settings_cases()],
        }
    finally:
        shutil.rmtree(base, ignore_errors=True)
    OUTPUT.write_text(json.dumps(vectors, indent=1, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
