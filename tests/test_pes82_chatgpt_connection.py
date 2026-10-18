"""Subscription connection isolation and managed-login failure boundaries."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from sylvia_gm.provider._chatgpt_account import ChatGPTAccount
from sylvia_gm.provider._codex_rpc import (
    CodexConnection,
    CodexConnectionError,
    isolated_environment,
)


def test_child_environment_excludes_other_logins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in (
        "OPENAI_API_KEY",
        "CODEX_ACCESS_TOKEN",
        "CODEX_API_KEY",
        "CODEX_HOME",
        "ANTHROPIC_API_KEY",
        "SYLVIA_GM_AUTHORITY_DATABASE",
        "HTTP_PROXY",
    ):
        monkeypatch.setenv(name, "private-sentinel")
    child = isolated_environment(tmp_path)
    assert child["CODEX_HOME"] == str(tmp_path)
    assert "private-sentinel" not in child.values()


@pytest.fixture
def rpc_child(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> CodexConnection:
    script = tmp_path / "synthetic_runtime.py"
    script.write_text(
        """
import json, sys, time
def emit(value):
    print(json.dumps(value), flush=True)
for line in sys.stdin:
    msg=json.loads(line)
    method=msg.get('method')
    if method == 'initialize':
        emit({'id': msg['id'], 'result': {'userAgent': 'synthetic'}})
    elif method == 'account/read':
        emit({'id': 800, 'method': 'item/commandExecution/requestApproval',
              'params': {'command': 'DO NOT RUN'}})
        denied=json.loads(sys.stdin.readline())
        emit({'id':msg['id'], 'result': {'denied': denied.get('error',{}).get('code') == -32601}})
    elif method == 'synthetic/error':
        emit({'id':msg['id'], 'error': {'code':-1,'message':'private-sentinel'}})
    elif method == 'synthetic/hang':
        time.sleep(30)
""",
        encoding="utf-8",
    )
    real_popen = subprocess.Popen

    def launch(command: list[str], **kwargs: Any) -> subprocess.Popen[str]:
        assert command[1:3] == ["app-server", "--stdio"]
        assert kwargs["env"]["CODEX_HOME"] == str(tmp_path / "private-profile")
        assert kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs.get("shell", False) is False
        return real_popen([sys.executable, str(script)], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", launch)
    client = CodexConnection(tmp_path / "private-profile", Path(sys.executable))
    yield client
    client.close()


def test_rpc_denies_unsolicited_host_commands(rpc_child: CodexConnection) -> None:
    rpc_child.start()
    assert rpc_child.call("account/read", {}) == {"denied": True}


def test_rpc_errors_do_not_expose_server_text(rpc_child: CodexConnection) -> None:
    rpc_child.start()
    with pytest.raises(CodexConnectionError) as error:
        rpc_child.call("synthetic/error", {})
    assert "private-sentinel" not in str(error.value)


def test_timed_out_connection_can_restart_without_stale_responses(
    rpc_child: CodexConnection,
) -> None:
    rpc_child.start()
    with pytest.raises(CodexConnectionError, match="timed out"):
        rpc_child.call("synthetic/hang", {}, timeout=0.1)
    rpc_child.start()
    assert rpc_child.call("account/read", {}) == {"denied": True}


class AccountRuntime(CodexConnection):
    """Deterministic managed-account protocol, with no token-file access."""

    def __init__(self, home: Path) -> None:
        super().__init__(home)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.account: dict[str, Any] | None = None
        self.events: list[dict[str, Any]] = []
        self.auth_url = "https://auth.openai.com/oauth/authorize?state=synthetic"
        self.model_entry = {"model": "gpt-5.6-sol", "displayName": "GPT-5.6 Sol",
                            "supportedReasoningEfforts": [{"reasoningEffort": "medium"}]}

    def start(self) -> None:
        pass

    def notifications(self) -> list[dict[str, Any]]:
        events, self.events = self.events, []
        return events

    def call(self, method: str, params: dict[str, Any], *, timeout: float = 30) -> dict[str, Any]:
        self.calls.append((method, params))
        if method == "account/read":
            return {"account": self.account}
        if method == "account/login/start":
            assert params == {"type": "chatgpt"}
            return {"type": "chatgpt", "loginId": "our-login", "authUrl": self.auth_url}
        if method == "account/logout":
            self.account = None
        if method == "model/list":
            return {
                "data": [self.model_entry],
                "nextCursor": None,
            }
        return {}


def test_managed_login_status_and_logout_exclude_tokens(tmp_path: Path) -> None:
    runtime = AccountRuntime(tmp_path)
    account = ChatGPTAccount(runtime)
    assert account.status()["signed_in"] is False
    assert account.begin_login()["login_pending"] is True
    runtime.account = {
        "type": "chatgpt",
        "email": "synthetic@example.test",
        "planType": "pro",
        "accessToken": "private-sentinel",
    }
    state = account.status()
    assert state["signed_in"] is True and state["login_pending"] is False
    assert "private-sentinel" not in json.dumps(state)
    assert account.models() == [{"id": "gpt-5.6-sol", "name": "GPT-5.6 Sol",
                                 "verified": False, "unavailable_reason": None,
                                 "supported_reasoning_efforts": ["medium"],
                                 "default_reasoning_effort": None}]
    assert account.logout()["signed_in"] is False


@pytest.mark.parametrize("metadata,reason", [
    ({"inputModalities": ["audio"]}, "text input"),
])
def test_model_list_explains_unsupported_runtime_capabilities(
    tmp_path: Path, metadata: dict[str, object], reason: str,
) -> None:
    runtime = AccountRuntime(tmp_path)
    runtime.account = {"type": "chatgpt"}
    runtime.model_entry.update(metadata)
    model = ChatGPTAccount(runtime).models()[0]
    assert reason in model["unavailable_reason"]
    assert model["verified"] is False


@pytest.mark.parametrize("efforts", [[], ["high", "xhigh", "ultra"]])
def test_model_discovery_preserves_native_efforts_without_requiring_medium(tmp_path, efforts):
    runtime = AccountRuntime(tmp_path)
    runtime.account = {"type": "chatgpt"}
    runtime.model_entry.update({
        "supportedReasoningEfforts": [{"reasoningEffort": value} for value in efforts],
        "defaultReasoningEffort": efforts[-1] if efforts else None,
    })
    model = ChatGPTAccount(runtime).models()[0]
    assert model["unavailable_reason"] is None
    assert model["supported_reasoning_efforts"] == efforts
    assert model["default_reasoning_effort"] == (efforts[-1] if efforts else None)


@pytest.mark.parametrize(
    "url",
    [
        "https://auth.openai.com.evil.test/oauth",
        "http://auth.openai.com/oauth",
        "file:///private",
        "https://user@auth.openai.com/oauth",
        "https://auth.openai.com:444/oauth",
    ],
)
def test_invalid_sign_in_links_cancel_own_login(tmp_path: Path, url: str) -> None:
    runtime = AccountRuntime(tmp_path)
    runtime.auth_url = url
    with pytest.raises(CodexConnectionError, match="sign-in link"):
        ChatGPTAccount(runtime).begin_login()
    assert runtime.calls[-1] == ("account/login/cancel", {"loginId": "our-login"})


def test_failed_login_does_not_expose_error_or_cancel_someone_else(tmp_path: Path) -> None:
    runtime = AccountRuntime(tmp_path)
    account = ChatGPTAccount(runtime)
    account.begin_login()
    runtime.events = [
        {
            "method": "account/login/completed",
            "params": {"loginId": "other-login", "success": False, "error": "private-sentinel"},
        }
    ]
    assert account.status()["login_pending"] is True
    runtime.events = [
        {
            "method": "account/login/completed",
            "params": {"loginId": "our-login", "success": False, "error": "private-sentinel"},
        }
    ]
    state = account.status()
    assert state["login_failed"] is True and state["login_pending"] is False
    assert "private-sentinel" not in json.dumps(state)


def test_api_key_account_is_not_a_subscription_connection(tmp_path: Path) -> None:
    runtime = AccountRuntime(tmp_path)
    runtime.account = {"type": "apiKey"}
    with pytest.raises(CodexConnectionError, match="Sign in"):
        ChatGPTAccount(runtime).models()
    assert all(method != "model/list" for method, _ in runtime.calls)
