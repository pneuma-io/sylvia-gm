"""Generate reference vectors for sylvia-provider's kept Claude Code processes, the native
transcript and the Claude settings action (lane 5).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-provider/tests/vectors/generate_kept_vectors.py

The frozen `provider/_claude_kept.py`, `provider/_claude_native.py` and
`config/claude_settings.py` run unchanged. Recorded:

- `process_settings`, `extends` and `continuations`: the switch and the continuation rules;
- `pools`: scripted `KeptProcesses` runs (keep, take, release, generation, models), the
  processes named by letter;
- `native`: `collect`, `collect_answer`, `wait_for_flush`, `session_files`, `delete_session`,
  `remove_debug_logs` and `sweep` over folders written for each case (modification times set);
- `settings`: `claude_settings_action` with the runtime's answers supplied (`find_runtime`,
  `runtime_version`, `authentication_status` and the sign-in child are stand-ins whose
  behaviour each case states), the config file it writes, and the fake seal and fixed clock of
  lane 4's `generate_writes_vectors.py`.

Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import datetime as real_datetime
import json
import os
import sys
import tempfile
import types
from pathlib import Path

from sylvia_gm.config import _credential_protection as protection
from sylvia_gm.config import claude_settings
from sylvia_gm.config import provider_settings as settings
from sylvia_gm.config._resolver import ConfigError
from sylvia_gm.provider import _claude_kept, _claude_native
from sylvia_gm.provider._claude_kept import ContinuationMismatchError, KeptProcess, KeptProcesses
from sylvia_gm.provider._exceptions import ProviderConnectionError

HERE = Path(__file__).parent
OUTPUT = HERE / "kept_vectors.json"
FIXED_NOW = "2026-11-21T08:00:00.123456+00:00"
SEAL = "[host-seal abc123] "
CONTINUE = "SYLVIA_GM_CONTINUE (application invocation, not player speech): Continue."


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

# ── Continuation ─────────────────────────────────────────────────────────────────────────

USER = {"role": "user", "content": "I open the door."}
REPLY_TEXT = {"role": "assistant", "content": [{"type": "text", "text": "It creaks."}]}
CALL = {"role": "assistant", "content": [{"type": "text", "text": "Rolling."},
                                         {"type": "tool_use", "id": "toolu_1", "name": "roll", "input": {"n": 2}}]}


def result(*extra, content="7", is_error=None, call_id="toolu_1"):
    block = {"type": "tool_result", "tool_use_id": call_id, "content": content}
    if is_error is not None:
        block["is_error"] = is_error
    return {"role": "user", "content": [block, *extra]}


def sealed(text):
    return {"type": "text", "text": SEAL + text}


def continuation_cases() -> list[dict]:
    text_state = {"held": [USER], "spoken": "It creaks.", "pending": None, "call_id": "", "call_arguments": None}
    call_state = {"held": [USER], "spoken": "Rolling.", "pending": {"request_id": "r1"}, "call_id": "toolu_1",
                  "call_arguments": {"n": 2, "why": "Sylvia narrates."}}
    cases = [
        ("prompt", text_state, [USER, REPLY_TEXT, {"role": "user", "content": "I step in."}]),
        ("prompt-two-users", text_state, [USER, REPLY_TEXT, {"role": "user", "content": "A"},
                                          {"role": "user", "content": [{"type": "text", "text": "B"}]}]),
        ("prompt-skips-continue", text_state, [USER, REPLY_TEXT, {"role": "user", "content": [
            {"type": "text", "text": SEAL + CONTINUE}, {"type": "text", "text": "Go"}]}]),
        ("prompt-only-continue", text_state, [USER, REPLY_TEXT, {"role": "user", "content": SEAL + CONTINUE}]),
        ("no-tail", text_state, [USER]),
        ("tail-starts-user", text_state, [USER, USER]),
        ("text-differs", text_state, [USER, {"role": "assistant", "content": "Other."}, USER]),
        ("reply-with-call", text_state, [USER, {"role": "assistant", "content": [
            {"type": "text", "text": "It creaks."}, {"type": "tool_use", "id": "x"}]}, USER]),
        ("no-user-after", text_state, [USER, REPLY_TEXT]),
        ("assistant-after", text_state, [USER, REPLY_TEXT, REPLY_TEXT]),
        ("image-block", text_state, [USER, REPLY_TEXT, {"role": "user", "content": [{"type": "image"}]}]),
        ("content-not-blocks", text_state, [USER, {"role": "assistant", "content": 5}, USER]),
        ("answer", call_state, [USER, CALL, result()]),
        ("answer-with-sealed", call_state, [USER, CALL, result(sealed("You see a goblin."))]),
        ("answer-error", call_state, [USER, CALL, result(is_error=True)]),
        ("answer-blocks", call_state, [USER, CALL, result(content=[{"type": "text", "text": "7"}, {"type": "text", "text": "!"}])]),
        ("answer-image", call_state, [USER, CALL, result(content=[{"type": "image"}])]),
        ("answer-later-sealed", call_state, [USER, CALL, result(), {"role": "user", "content": SEAL + "Later."}]),
        ("answer-narration-copy", call_state, [USER, CALL, result(), {"role": "assistant", "content": "Sylvia narrates."},
                                               {"role": "user", "content": [sealed("x")]}]),
        ("answer-narration-other", call_state, [USER, CALL, result(), {"role": "assistant", "content": "New words."}]),
        ("answer-player-speech", call_state, [USER, CALL, result(), {"role": "user", "content": "I run!"}]),
        ("answer-continue-skipped", call_state, [USER, CALL, result({"type": "text", "text": SEAL + CONTINUE})]),
        ("answer-wrong-call", call_state, [USER, CALL, result(call_id="toolu_2")]),
        ("answer-two-calls", call_state, [USER, {"role": "assistant", "content": [
            {"type": "text", "text": "Rolling."}, {"type": "tool_use", "id": "toolu_1"}, {"type": "tool_use", "id": "toolu_1"}]}, result()]),
        ("answer-missing", call_state, [USER, CALL]),
        ("answer-empty-blocks", call_state, [USER, CALL, {"role": "user", "content": []}]),
        ("answer-not-result", call_state, [USER, CALL, {"role": "user", "content": "plain"}]),
        ("answer-extra-image", call_state, [USER, CALL, result({"type": "image"})]),
        ("text-not-string", text_state, [USER, {"role": "assistant", "content": [{"type": "text", "text": 5}]}, USER]),
        ("message-not-dict", text_state, [USER, "nonsense"]),
        ("cache-control-ignored", {**text_state, "held": [{"role": "user", "content": [
            {"type": "text", "text": "x", "cache_control": {"type": "ephemeral"}}]}]},
         [{"role": "user", "content": [{"type": "text", "text": "x"}]}, REPLY_TEXT, USER]),
    ]
    out = []
    for name, state, history in cases:
        kept = types.SimpleNamespace(**state)
        try:
            kind, value = _claude_kept.continuation(kept, history, CONTINUE)
            record = {"delivery": [kind, value]}
        except ContinuationMismatchError as exc:
            record = {"mismatch": exc.reason}
        except Exception as exc:  # noqa: BLE001
            record = {"raw": [type(exc).__name__, str(exc)]}
        out.append({"name": name, "state": state, "history": history, **record})
    return out


def extends_cases() -> list[dict]:
    a = {"role": "user", "content": [{"type": "text", "text": "x", "cache_control": {"type": "ephemeral"}}]}
    b = {"role": "user", "content": [{"type": "text", "text": "x"}]}
    cases = [([b, USER], [a]), ([b], [a]), ([USER, b], [a]), ([{"n": 1}, USER], [{"n": 1.0}]),
             ([{"n": True}, USER], [{"n": 1}]), ([{"b": 1, "a": 2}, USER], [{"a": 2, "b": 1}]), ([USER], [])]
    return [{"history": h, "held": held, "extends": _claude_kept._extends(h, held)} for h, held in cases]


# ── Pools ────────────────────────────────────────────────────────────────────────────────

class FakeConnection:
    def __init__(self, log, name):
        self.log, self.name = log, name

    def close(self):
        self.log.append(self.name)


def pool_scripts() -> list[dict]:
    H1, H2 = "home-1", "home-2"
    h = [{"role": "user", "content": "a"}]
    h2 = h + [{"role": "assistant", "content": "b"}, {"role": "user", "content": "c"}]
    return [
        {"name": "keep-and-take", "limit": 4, "idle": 1800, "ops": [
            ["keep", "A", H1, "m", "s", "t", h, 0], ["take", H1, "m", "s", "t", h2], ["len"]]},
        {"name": "take-no-extension-closes-earlier-forms", "limit": 4, "idle": 1800, "ops": [
            ["keep", "A", H1, "m", "s", "t", h2, 0], ["keep", "B", H1, "m", "other", "t", h2, 0],
            ["take", H1, "m", "s", "t", h], ["len"], ["models"]]},
        {"name": "other-home-closes", "limit": 4, "idle": 1800, "ops": [
            ["keep", "A", H1, "m", "s", "t", h, 0], ["take", H2, "m", "s", "t", h2], ["len"]]},
        {"name": "limit-evicts-oldest", "limit": 2, "idle": 1800, "ops": [
            ["keep", "A", H1, "m1", "s", "t", h, 0], ["keep", "B", H1, "m2", "s", "t", h, 0],
            ["keep", "C", H1, "m3", "s", "t", h, 0], ["len"], ["models"]]},
        {"name": "limit-zero", "limit": 0, "idle": 1800, "ops": [["keep", "A", H1, "m", "s", "t", h, 0], ["len"]]},
        {"name": "release-moves-generation", "limit": 4, "idle": 1800, "ops": [
            ["keep", "A", H1, "m", "s", "t", h, 0], ["generation", H1], ["release", H1, None],
            ["generation", H1], ["keep", "B", H1, "m", "s", "t", h, 0], ["keep", "C", H1, "m", "s", "t", h, 1], ["len"]]},
        {"name": "release-model", "limit": 4, "idle": 1800, "ops": [
            ["keep", "A", H1, "m1", "s", "t", h, 0], ["keep", "B", H1, "m2", "s", "t", h, 0],
            ["release", H1, "m1"], ["models"], ["generation", H1]]},
        {"name": "release-all", "limit": 4, "idle": 1800, "ops": [
            ["keep", "A", H1, "m", "s", "t", h, 0], ["keep", "B", H2, "m", "s", "t", h, 0], ["release", None, None],
            ["generation", H1], ["generation", H2], ["len"]]},
        {"name": "newest-extension-wins", "limit": 4, "idle": 1800, "ops": [
            ["keep", "A", H1, "m", "s", "t", h, 0], ["keep", "B", H1, "m", "s", "t", h, 0], ["take", H1, "m", "s", "t", h2], ["len"]]},
        {"name": "tools-differ", "limit": 4, "idle": 1800, "ops": [
            ["keep", "A", H1, "m", "s", "t", h, 0], ["take", H1, "m", "s", "other-tools", h2], ["len"]]},
    ]


def run_pool(script: dict) -> list:
    pool = KeptProcesses(limit=script["limit"], idle_seconds=script["idle"])
    named: dict[str, KeptProcess] = {}
    out = []
    for op in script["ops"]:
        if op[0] == "keep":
            _, name, home, model, system, tools, held, generation = op
            kept = KeptProcess(connection=FakeConnection([], name), home=Path(home), model=model, system=system,
                               tools_key=tools, held=held, session_id="s", step=Path("step"), config=Path("cfg"), cache=None)
            named[name] = kept
            out.append(sorted(next(n for n, k in named.items() if k is each) for each in pool.keep(kept, generation)))
        elif op[0] == "take":
            _, home, model, system, tools, history = op
            chosen, close = pool.take(Path(home), model, system, tools, history)
            name_of = {id(k): n for n, k in named.items()}
            out.append([name_of.get(id(chosen)) if chosen else None, sorted(name_of[id(k)] for k in close)])
        elif op[0] == "release":
            released = pool.release(Path(op[1]) if op[1] else None, op[2])
            name_of = {id(k): n for n, k in named.items()}
            out.append(sorted(name_of[id(k)] for k in released))
        elif op[0] == "generation":
            out.append(pool.generation(Path(op[1])))
        elif op[0] == "models":
            out.append(sorted(pool.models()))
        elif op[0] == "len":
            out.append(len(pool))
    pool.close_all()
    return out


# ── Native transcript ────────────────────────────────────────────────────────────────────

PROMPT = [{"type": "text", "text": "Go"}]
SID = "0f8fad5b-d9cb-469f-a165-70867728950e"


def rows_text(rows) -> str:
    return "".join((row if isinstance(row, str) else json.dumps(row)) + "\n" for row in rows)


def native_cases() -> list[dict]:
    prompt_row = {"type": "user", "message": {"role": "user", "content": PROMPT}}
    attachment = {"type": "attachment", "attachment": {"kind": "env"}, "rendered": "Env: x", "renderedRole": "user"}
    answer = {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "7"}]}}
    round_assistant = {"type": "assistant", "message": {"id": "msg_round", "type": "message", "role": "assistant",
                                                        "model": "claude", "content": [{"type": "thinking", "thinking": "x"},
                                                                                       {"type": "tool_use", "id": "ts", "name": "ToolSearch"}],
                                                        "usage": {"input_tokens": 1}, "stop_reason": "tool_use"}}
    round_user = {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "ts"}], "extra": 1}}
    final = {"type": "assistant", "message": {"id": "msg_final", "content": [{"type": "text", "text": "done"}]}}
    return [
        {"name": "collected", "rows": [{"type": "summary"}, prompt_row, attachment, {"type": "attachment", "rendered": ""},
                                       {"type": "api-request"}], "op": "collect"},
        {"name": "rounds", "rows": [prompt_row, attachment, round_assistant, round_user,
                                    {"type": "assistant", "message": {"content": [{"type": "thinking"}]}}, final, attachment],
         "op": "collect", "final": "msg_final"},
        {"name": "missing-prompt", "rows": [attachment], "op": "collect"},
        {"name": "cli-inserted", "rows": [{"type": "assistant", "message": {"model": "<synthetic>"}}, prompt_row], "op": "collect"},
        {"name": "unreadable-line", "rows": [prompt_row, "{oops"], "op": "collect"},
        {"name": "not-objects", "rows": [prompt_row, "[1]"], "op": "collect"},
        {"name": "attachment-not-dict", "rows": [prompt_row, {"type": "attachment", "attachment": 5, "rendered": "x"}], "op": "collect"},
        {"name": "no-file", "rows": None, "op": "collect"},
        {"name": "answer", "rows": [prompt_row, answer, attachment, {"type": "assistant", "message": {}}, attachment], "op": "answer"},
        {"name": "answer-rounds", "rows": [answer, round_assistant, round_user, final], "op": "answer", "final": "msg_final", "requests": 0},
        {"name": "answer-waits-for-requests", "rows": [answer, {"type": "api-request"}], "op": "answer", "requests": 2},
        {"name": "answer-missing", "rows": [prompt_row], "op": "answer"},
        {"name": "answer-skips-bad-lines", "rows": ["{partial", answer, attachment], "op": "answer"},
        {"name": "flush-bookkeeping", "rows": [prompt_row, {"type": "api-request-start"}], "op": "flush"},
        {"name": "flush-requests", "rows": [prompt_row, {"type": "api-request"}, {"type": "api-request"}], "op": "flush", "requests": 2},
        {"name": "flush-quiet", "rows": [prompt_row], "op": "flush"},
        {"name": "flush-no-prompt", "rows": [attachment], "op": "flush"},
        {"name": "flush-no-file", "rows": None, "op": "flush"},
    ]


def run_native(case: dict, folder: Path) -> dict:
    config = folder / "claude-runtime" / "profile"
    project = config / "projects" / "-workspace"
    project.mkdir(parents=True)
    path = project / f"{SID}.jsonl"
    if case["rows"] is not None:
        path.write_text(rows_text(case["rows"]), encoding="utf-8")
    op = case["op"]
    if op == "collect":
        found = _claude_native.collect(path if case["rows"] is not None else None, PROMPT,
                                       final_message_id=case.get("final"))
    elif op == "answer":
        found = _claude_native.collect_answer(config, SID, "toolu_1", limit=0.1, final_message_id=case.get("final"),
                                              requests=case.get("requests"))
    else:
        return {"flushed": _claude_native.wait_for_flush(config, SID, PROMPT, limit=0.5, requests=case.get("requests"))}
    return {"status": found.status, "records": list(found.records), "reason": found.reason, "answer": found.answer}


def cleanup_cases(folder: Path) -> dict:
    home = folder / "claude-runtime"
    config = home / "profile"
    steps = home / "steps"
    now = 2_000_000_000.0
    old, fresh = now - 3600, now - 60
    layout = {
        f"profile/projects/-w/{SID}.jsonl": old,
        f"profile/projects/-w/{SID}/tool.txt": old,
        "profile/projects/-w/11111111-2222-3333-4444-555555555555.jsonl": fresh,
        "profile/projects/-w/AAAAAAAA-2222-3333-4444-555555555555.jsonl": old,
        "profile/projects/-w/not-a-session.jsonl": old,
        "profile/projects/-w/22222222-2222-3333-4444-555555555555.jsonl": old,
        "steps/step-old/file.txt": old,
        "steps/step-fresh/file.txt": fresh,
        "steps/step-active/file.txt": old,
        "cache/claude-cli-nodejs/-w/mcp-logs-x/log.txt": old,
        "cache/claude-cli-nodejs/-w/other/log.txt": old,
    }
    for relative, mtime in layout.items():
        target = home / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")
    for relative, mtime in layout.items():
        target = home / relative
        while target != home:
            os.utime(target, (mtime, mtime))
            target = target.parent
    listing = sorted(p.relative_to(config).as_posix() for p in _claude_native.session_files(config, SID))
    swept = _claude_native.sweep(config, steps, {"step-active", "22222222-2222-3333-4444-555555555555"}, home, now=now)
    after_sweep = sorted(p.relative_to(home).as_posix() for p in home.rglob("*") if p.is_file())
    logs = _claude_native.remove_debug_logs(home / "cache", home)
    outside = _claude_native.delete_session(folder / "elsewhere", SID, home)
    deleted = _claude_native.delete_session(config, "22222222-2222-3333-4444-555555555555", home)
    after = sorted(p.relative_to(home).as_posix() for p in home.rglob("*") if p.is_file())
    return {"layout": layout, "now": now, "session_files": listing, "swept": swept, "after_sweep": after_sweep,
            "debug_logs": logs, "outside": outside, "deleted": deleted, "after": after}


# ── Settings ─────────────────────────────────────────────────────────────────────────────

DAMAGED = "Sylvia's copy of Claude Code is damaged. Install it again in Settings > Accounts."


def settings_cases() -> list[dict]:
    signed_in = {"signed_in": True, "auth_method": "claude.ai", "billing": "subscription"}
    signed_out = {"signed_in": False, "auth_method": "none", "billing": "unknown"}
    model = lambda **over: {"id": "claude-opus-5-5", "context_window": None, "max_output_tokens": None, **over}  # noqa: E731
    return [
        {"name": "not-installed", "runtime": None, "calls": [["status", None], ["login", None], ["add_model", None], ["cancel", None]]},
        {"name": "bad-action", "runtime": None, "calls": [["dance", None], [5, None]]},
        {"name": "bad-options", "runtime": None, "calls": [["status", {"other": 1}], ["status", [1]], ["status", {"model": 1}]]},
        {"name": "damaged", "runtime": "damaged", "calls": [["status", None], ["logout", None]]},
        {"name": "signed-out", "runtime": signed_out, "calls": [["status", None], ["add_model", {"model": model()}]]},
        {"name": "signed-in", "runtime": signed_in, "calls": [["status", None]]},
        {"name": "login", "runtime": signed_out, "calls": [["login", None], ["status", None], ["login", {"account_type": "api"}],
                                                          ["cancel", None], ["status", None]]},
        {"name": "login-console", "runtime": signed_out, "calls": [["login", {"account_type": "api"}], ["cancel", None]]},
        {"name": "login-bad-account", "runtime": signed_out, "calls": [["login", {"account_type": "team"}]]},
        {"name": "logout", "runtime": signed_in, "calls": [["logout", None]]},
        {"name": "add-model", "runtime": signed_in, "calls": [
            ["add_model", {"model": model()}],
            ["add_model", {"model": model(id="opus[1m]", context_window=200000)}],
            ["add_model", {"model": model(id="my-model", context_window=512)}]]},
        {"name": "add-model-refusals", "runtime": signed_in, "calls": [
            ["add_model", {}], ["add_model", {"model": "x"}], ["add_model", {"model": {"id": "x"}}],
            ["add_model", {"model": model(id="-bad")}], ["add_model", {"model": model(id="a" * 129)}],
            ["add_model", {"model": model(id="café")}], ["add_model", {"model": model(id=5)}],
            ["add_model", {"model": model(max_output_tokens=10)}], ["add_model", {"model": model(context_window=511)}],
            ["add_model", {"model": model(context_window=True)}], ["add_model", {"model": model(context_window=1.5e3)}],
            ["add_model", {"model": model(context_window=10_000_001)}]]},
    ]


class FakeLogin:
    def __init__(self):
        self.done = False

    def poll(self):
        return 0 if self.done else None

    def terminate(self):
        self.done = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.done = True


def run_settings(case: dict, folder: Path) -> dict:
    root = folder / "owner"
    root.mkdir()
    runtime = case["runtime"]
    launched = []
    claude_settings._accounts.clear()
    original = {name: getattr(claude_settings, name) for name in
                ("find_runtime", "runtime_version", "authentication_status", "release_kept_processes")}
    original_popen, original_run = claude_settings.subprocess.Popen, claude_settings.subprocess.run

    def popen(command, **kwargs):
        launched.append(["login", command[-1]])
        return FakeLogin()

    def run(command, **kwargs):
        launched.append(["run", command[-1]])

    def version(executable, home):
        if runtime == "damaged":
            raise ProviderConnectionError(DAMAGED)
        return "2.1.400"

    claude_settings.find_runtime = lambda home: None if runtime is None else home / "install" / "2.1.400" / "claude.exe"
    claude_settings.runtime_version = version
    claude_settings.authentication_status = lambda executable, home: dict(runtime)
    claude_settings.release_kept_processes = lambda *args, **kwargs: launched.append(["release"]) or 0
    claude_settings.subprocess.Popen = popen
    claude_settings.subprocess.run = run
    results = []
    try:
        for action, options in case["calls"]:
            try:
                status = claude_settings.claude_settings_action(root, action, options)
                if status.get("executable"):
                    status["executable"] = "<executable>"
                results.append({"status": status})
            except ProviderConnectionError as exc:
                results.append({"error": str(exc)})
    finally:
        for name, value in original.items():
            setattr(claude_settings, name, value)
        claude_settings.subprocess.Popen, claude_settings.subprocess.run = original_popen, original_run
        for account in claude_settings._accounts.values():
            account.cancel()
        claude_settings._accounts.clear()
    config = root / "config.yaml"
    return {**case, "results": results, "launched": launched,
            "config": config.read_text(encoding="utf-8") if config.exists() else None}


def main() -> int:
    for name in list(os.environ):
        if name.upper().startswith("SYLVIA_GM_"):
            del os.environ[name]
    native = []
    for case in native_cases():
        with tempfile.TemporaryDirectory() as folder:
            native.append({**case, "rows": None if case["rows"] is None else rows_text(case["rows"]),
                           **run_native(case, Path(folder))})
    with tempfile.TemporaryDirectory() as folder:
        cleanup = cleanup_cases(Path(folder))
    setting_records = []
    for case in settings_cases():
        with tempfile.TemporaryDirectory() as folder:
            setting_records.append(run_settings(case, Path(folder)))
    vectors = {
        "python": sys.version.split()[0],
        "now": FIXED_NOW,
        "continue_text": CONTINUE,
        "process_settings": {value: _claude_kept.process_setting({"SYLVIA_CLAUDE_PROCESS": value})
                             for value in ["", "persistent", "per-step", " Per_Step ", "STEP", "kept", "steps"]},
        "extends": extends_cases(),
        "continuations": continuation_cases(),
        "pools": [{**script, "results": run_pool(script)} for script in pool_scripts()],
        "prompt": PROMPT,
        "session_id": SID,
        "native": native,
        "cleanup": cleanup,
        "settings": setting_records,
    }
    OUTPUT.write_text(json.dumps(vectors, indent=1, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
