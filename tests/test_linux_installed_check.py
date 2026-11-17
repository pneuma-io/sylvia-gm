"""The Linux installed-application check: its stand-in model service, guards and report.

The check itself needs a Linux machine with a hidden display and the release AppImage (see
docs/operations/linux-release-checklist.md, section 5). These cases cover the plain-Python
parts that decide what it may start and what it reports, on every system and without a display.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from scripts.qa import linux_installed_check as check
from scripts.qa import standin_model_endpoint as standin

KEY = "test-key-1234"
CAMPAIGN_FILE_ALIAS = "tool_0123456789ab_update_campaign_file"


@pytest.fixture
def service(tmp_path):
    log = tmp_path / "requests.jsonl"
    server = standin.make_server(0, KEY, str(log))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", log
    server.shutdown()
    server.server_close()


def call(base, path, body=None, key=KEY):
    request = urllib.request.Request(
        base + path,
        data=None if body is None else json.dumps(body).encode(),
        method="GET" if body is None else "POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.headers.get("Content-Type", ""), response.read()


def tool(name):
    return {"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}


def stream_chunks(raw):
    events = [line[6:] for line in raw.decode().splitlines() if line.startswith("data: ")]
    assert events[-1] == "[DONE]"
    return [json.loads(event) for event in events[:-1]]


def test_standin_lists_one_model_with_a_large_context_and_refuses_a_wrong_key(service):
    base, log = service
    _type, body = call(base, "/v1/models")
    (model,) = json.loads(body)["data"]
    assert model["id"] == standin.MODEL
    # A player turn offers about a hundred tools; it is sent only if it fits.
    assert model["context_length"] >= 1_000_000
    with pytest.raises(urllib.error.HTTPError) as refused:
        call(base, "/v1/models", key="wrong")
    assert refused.value.code == 401
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert [row["key_matched"] for row in rows] == [True, False]


def test_standin_narrates_a_request_without_tools(service):
    base, _log = service
    _type, body = call(base, "/v1/chat/completions", {
        "model": standin.MODEL, "messages": [{"role": "user", "content": "Begin."}]})
    choice = json.loads(body)["choices"][0]
    assert choice["finish_reason"] == "stop"
    assert choice["message"]["content"] == standin.NARRATION
    assert check.NARRATION_MARK in standin.NARRATION


def test_standin_ends_a_player_turn_with_an_unchanged_campaign_file_call(service):
    base, _log = service
    request = {"model": standin.MODEL, "tools": [tool("tool_x_roll"), tool(CAMPAIGN_FILE_ALIAS)],
               "messages": [{"role": "user", "content": "I wait."}]}
    _type, body = call(base, "/v1/chat/completions", request)
    choice = json.loads(body)["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    (tool_call,) = choice["message"]["tool_calls"]
    assert tool_call["function"]["name"] == CAMPAIGN_FILE_ALIAS
    arguments = json.loads(tool_call["function"]["arguments"])
    assert arguments["state_change"] == "unchanged"
    assert arguments["state_change_summary"]
    assert arguments["revised_player_facing_response"] == [
        {"kind": "narration", "text": standin.TURN_REPLY}]
    assert check.TURN_REPLY_MARK in standin.TURN_REPLY
    # After the tool result, a further request only needs prose.
    request["messages"].append({"role": "tool", "tool_call_id": "standin-call-1", "content": "{}"})
    _type, body = call(base, "/v1/chat/completions", request)
    assert json.loads(body)["choices"][0]["message"]["content"] == standin.TURN_REPLY


def test_standin_streams_text_and_tool_calls_that_reassemble(service):
    base, _log = service
    _type, raw = call(base, "/v1/chat/completions", {
        "model": standin.MODEL, "stream": True, "messages": [{"role": "user", "content": "Hi"}]})
    chunks = stream_chunks(raw)
    text = "".join(chunk["choices"][0]["delta"].get("content") or ""
                   for chunk in chunks if chunk["choices"])
    assert text == standin.NARRATION
    assert chunks[-1]["choices"] == [] and chunks[-1]["usage"]["total_tokens"] > 0

    content_type, raw = call(base, "/v1/chat/completions", {
        "model": standin.MODEL, "stream": True, "tools": [tool(CAMPAIGN_FILE_ALIAS)],
        "messages": [{"role": "user", "content": "Hi"}]})
    assert content_type.startswith("text/event-stream")
    chunks = stream_chunks(raw)
    pieces = [call_part for chunk in chunks if chunk["choices"]
              for call_part in chunk["choices"][0]["delta"].get("tool_calls") or []]
    assert pieces[0]["function"]["name"] == CAMPAIGN_FILE_ALIAS
    arguments = json.loads("".join(piece["function"]["arguments"] for piece in pieces))
    assert arguments["state_change"] == "unchanged"
    finishes = [chunk["choices"][0]["finish_reason"] for chunk in chunks if chunk["choices"]]
    assert finishes[-1] == "tool_calls"


def test_standin_log_records_facts_but_no_message_content(service):
    base, log = service
    secret = "words-the-log-must-never-hold"
    call(base, "/v1/chat/completions", {
        "model": standin.MODEL, "tools": [tool(CAMPAIGN_FILE_ALIAS)],
        "messages": [{"role": "user", "content": secret}]})
    text = log.read_text()
    assert secret not in text and KEY not in text
    (row,) = [json.loads(line) for line in text.splitlines()]
    assert row["tools"] == 1 and row["messages"] == 1 and row["reply"] == "tool_call"


def test_clean_path_drops_windows_folders_and_path_problems_finds_them():
    path = "/usr/bin:/mnt/c/Windows/system32::/mnt/c/tools/bin:/bin"
    assert check.clean_path(path) == "/usr/bin:/bin"
    assert check.path_problems(path) == ["/mnt/c/Windows/system32", "/mnt/c/tools/bin"]
    assert check.path_problems(check.clean_path(path)) == []


@pytest.mark.runs_on("linux", reason="A symbolic link into /mnt/ is a POSIX path")
def test_path_problems_follows_a_link_into_windows_folders(tmp_path):
    link = tmp_path / "bin"
    link.symlink_to("/mnt/c/Windows")
    assert check.path_problems(f"/usr/bin:{link}") == [str(link)]


def environment(tmp_path, **overrides):
    base = {
        "PATH": "/usr/bin:/mnt/c/Windows", "LANG": "en_GB.UTF-8", "WAYLAND_DISPLAY": "wayland-0",
        "XDG_DATA_HOME": "/elsewhere", "SYLVIA_GM_AUTHORITY_DATABASE": "/real.sqlite3",
        "OPENAI_API_KEY": "real-key", "DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:real",
    }
    env = check.child_environment(base, home=tmp_path / "home", runtime=tmp_path / "run",
                                  display=":90", dbus_address="unix:path=/tmp/bus",
                                  marker="m1", extra_path=["/opt/tools"])
    env.update(overrides)
    return env


def test_child_environment_is_allow_listed_and_points_at_the_hidden_display(tmp_path):
    env = environment(tmp_path)
    for name in ("WAYLAND_DISPLAY", "XDG_DATA_HOME", "SYLVIA_GM_AUTHORITY_DATABASE",
                 "OPENAI_API_KEY"):
        assert name not in env
    assert env["PATH"] == "/opt/tools:/usr/bin"
    assert env["DISPLAY"] == ":90" and env["GDK_BACKEND"] == "x11"
    assert env["WEBKIT_DISABLE_COMPOSITING_MODE"] == "1"
    assert env["HOME"] == str(tmp_path / "home")
    assert env["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/tmp/bus"
    assert env["LANG"] == "en_GB.UTF-8"
    assert env[check.MARKER_VARIABLE] == "m1"
    check.guard_environment(env, real_home=str(tmp_path / "real-home"))


@pytest.mark.parametrize(("overrides", "complaint"), [
    ({"WAYLAND_DISPLAY": "wayland-0"}, "WAYLAND_DISPLAY"),
    ({"GDK_BACKEND": "wayland"}, "GDK_BACKEND"),
    ({"DISPLAY": ":0"}, "display"),
    ({"DISPLAY": "localhost:90"}, "display"),
    ({"PATH": "/usr/bin:/mnt/c/Windows"}, "Windows folders"),
    ({"XDG_CONFIG_HOME": "/x"}, "XDG_CONFIG_HOME"),
    ({"SYLVIA_GM_DATA": "/x"}, "SYLVIA_GM_"),
])
def test_guard_refuses_an_unsafe_environment(tmp_path, overrides, complaint):
    with pytest.raises(check.GuardError, match=complaint):
        check.guard_environment(environment(tmp_path, **overrides),
                                real_home=str(tmp_path / "real-home"))


def test_guard_refuses_the_real_home_folder(tmp_path):
    env = environment(tmp_path)
    with pytest.raises(check.GuardError, match="real home"):
        check.guard_environment(env, real_home=env["HOME"])


def test_missing_programs_names_every_tool_on_an_empty_path(tmp_path):
    assert check.missing_programs(str(tmp_path)) == list(check.REQUIRED_PROGRAMS)


def test_step_plan_numbers_the_checklist_steps_and_needs_only_known_steps():
    steps = check.step_plan()
    assert [step.number for step in steps] == list(range(1, 14))
    numbers = {step.number for step in steps}
    for step in steps:
        assert set(step.needs) <= numbers and step.number not in step.needs
        assert step.status == check.NOT_RUN


def finished(statuses):
    steps = check.step_plan()[: len(statuses)]
    for step, status in zip(steps, statuses, strict=True):
        step.status = status
    return steps


def test_report_passes_only_when_no_step_failed_and_one_ran():
    assert check.exit_code(check.summarise(finished([check.PASSED, check.NOT_RUN]))) == 0
    assert check.exit_code(check.summarise(finished([check.PASSED, check.FAILED]))) == 1
    assert check.exit_code(check.summarise(finished([check.NOT_RUN, check.NOT_RUN]))) == 1


def test_report_json_and_markdown_carry_each_step(tmp_path):
    steps = finished([check.PASSED, check.FAILED, check.NOT_RUN])
    steps[0].facts["data_folder_mode"] = "0o700"
    steps[0].screenshots.append("screenshots/01-home.png")
    steps[0].seconds = 12.34
    steps[1].reason = "the stand-in | never saw the key"
    steps[1].facts["dictation_page"] = "Dictation\n\nNot available in the Linux app yet."
    report = check.build_report(steps, {"appimage": "/x/Sylvia.AppImage", "method": "WebDriver"})
    assert report["format"] == check.REPORT_FORMAT and report["version"] == check.REPORT_VERSION
    assert report["summary"] == {"counts": {"passed": 1, "failed": 1, "not run": 1},
                                 "passed": False}
    assert [step["status"] for step in report["steps"]] == ["passed", "failed", "not run"]
    assert report["steps"][0]["seconds"] == 12.3
    assert report["not_covered"]
    json.dumps(report)
    text = check.render_markdown(report)
    assert "**Result: FAILED**: 1 passed, 1 failed, 1 not run." in text
    assert "| 2 | " in text and "the stand-in \\| never saw the key" in text
    assert "[screenshots/01-home.png](screenshots/01-home.png)" in text
    assert "- data_folder_mode: 0o700" in text
    assert "- dictation_page: Dictation / Not available in the Linux app yet." in text
    assert "## Not covered by this check" in text


def test_files_containing_finds_a_plain_key_only_where_it_is(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "config.yaml").write_text("api_key: encrypted-blob\n")
    leaked = tmp_path / "a" / "leak.json"
    leaked.write_text('{"key": "standin-abc"}')
    assert check.files_containing(tmp_path, b"standin-abc") == [leaked]


def test_made_up_card_is_a_complete_version_two_card():
    card = check.made_up_card()
    assert card["spec"] == "chara_card_v2" and card["spec_version"] == "2.0"
    assert card["data"]["name"] == check.CARD_NAME
    assert {"name", "description", "personality", "scenario", "first_mes", "mes_example"} <= set(
        card["data"])


def test_main_refuses_a_folder_that_is_not_empty_before_starting_anything(tmp_path):
    output = tmp_path / "report"
    output.mkdir()
    (output / "keep.txt").write_text("earlier evidence")
    assert check.main(["--appimage", str(tmp_path / "missing.AppImage"),
                       "--output", str(output)]) == 2
    assert sorted(os.listdir(output)) == ["keep.txt"]


@pytest.mark.runs_on("linux", reason="Windows lets SO_REUSEADDR bind over a listening port")
def test_port_is_free_only_while_nothing_listens():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.listen()
        # A connection closed by this side waits in TIME_WAIT, as after a finished run.
        client = socket.create_connection(("127.0.0.1", port))
        accepted, _ = listener.accept()
        accepted.close()
        client.close()
        assert not check.port_is_free(port)
    assert check.port_is_free(port)


def test_display_number_reads_only_local_displays():
    assert check.display_number(":90") == 90
    with pytest.raises(check.GuardError):
        check.display_number("host:90")


def test_the_scripts_hold_no_local_paths():
    for name in ("linux_installed_check.py", "standin_model_endpoint.py"):
        text = (Path(check.__file__).parent / name).read_text(encoding="utf-8")
        for fragment in ("/home/", "D:\\", "/mnt/d", "/mnt/c"):
            assert fragment not in text, (name, fragment)
