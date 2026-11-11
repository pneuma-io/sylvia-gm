"""Owner-only setup for Claude's native executable and account-owned authentication."""

from __future__ import annotations

import atexit
import re
import subprocess
import threading
from pathlib import Path

from sylvia_gm.config._user_settings import read_user_settings
from sylvia_gm.config.provider_settings import save_provider_settings
from sylvia_gm.provider._claude_runtime import (
    MINIMUM_VERSION_TEXT,
    authentication_status,
    find_runtime,
    native_auth_command,
    native_environment,
    process_options,
    runtime_version,
)
from sylvia_gm.provider._exceptions import ProviderConnectionError


class ClaudeAccount:
    """Retain only this owner's native sign-in child; never retain Claude credentials."""

    def __init__(self) -> None:
        self.login: subprocess.Popen[bytes] | None = None
        self.lock = threading.RLock()
        atexit.register(self.cancel)

    def cancel(self) -> None:
        """Cancel our in-progress browser sign-in without logging out another session."""
        with self.lock:
            if self.login is not None and self.login.poll() is None:
                self.login.terminate()
                try:
                    self.login.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.login.kill()
                    self.login.wait(timeout=3)
            self.login = None

    def begin_login(self, executable: Path, *, api_account: bool = False) -> None:
        """Use the binary's own browser flow and keep its console output out of Sylvia."""
        with self.lock:
            if self.login is not None and self.login.poll() is None:
                return
            try:
                self.login = subprocess.Popen(
                    [
                        *native_auth_command(executable, "login"),
                        *(["--console"] if api_account else []),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=native_environment(),
                    **process_options(),
                )
            except OSError:
                raise ProviderConnectionError(
                    "Claude's native sign-in could not start. Run claude auth login in a terminal."
                ) from None

    @property
    def pending(self) -> bool:
        return self.login is not None and self.login.poll() is None


_accounts: dict[Path, ClaudeAccount] = {}
_lock = threading.RLock()


def claude_settings_action(
    storage_root: Path,
    action: object,
    options: object = None,
) -> dict[str, object]:
    """Expose an allowlist of safe connection actions and explicit model registration."""
    if not isinstance(action, str) or action not in {"status", "login", "cancel", "add_model"}:
        raise ProviderConnectionError("This Claude connection action is unavailable.")
    if options is None:
        options = {}
    if not isinstance(options, dict) or set(options) - {"executable", "model", "account_type"}:
        raise ProviderConnectionError("Claude connection options are invalid.")
    config_path = storage_root / "config.yaml"
    saved = read_user_settings(config_path).get("providers", {}).get("claude_runtime", {})
    executable_option = options.get("executable", saved.get("runtime_executable", ""))
    if not isinstance(executable_option, str):
        raise ProviderConnectionError("Choose an absolute path to the Claude executable.")
    with _lock:
        key = storage_root.resolve()
        if key not in _accounts:
            _accounts[key] = ClaudeAccount()
        account = _accounts[key]
    if action == "cancel":
        account.cancel()
    executable = find_runtime(executable_option or None)
    status: dict[str, object] = {
        "installed": executable is not None,
        "supported": False,
        "version": None,
        "signed_in": False,
        "login_pending": account.pending,
        "auth_method": None,
        "billing": "unknown",
        "executable": str(executable) if executable is not None else None,
        "message": "Install the official Claude Code runtime. "
        "Sylvia does not install or update it.",
    }
    if executable is None:
        if action in {"login", "add_model"}:
            raise ProviderConnectionError(str(status["message"]))
        return status
    try:
        status["version"] = runtime_version(executable)
    except ProviderConnectionError:
        status["message"] = (
            f"Choose the official Claude Code runtime, version {MINIMUM_VERSION_TEXT} or later."
        )
        if action in {"login", "add_model"}:
            raise ProviderConnectionError(str(status["message"])) from None
        return status
    status["supported"] = True
    if action == "login":
        account_type = options.get("account_type", "subscription")
        if account_type not in {"subscription", "api"}:
            raise ProviderConnectionError("Choose a Claude subscription or Console API account.")
        account.begin_login(executable, api_account=account_type == "api")
    status.update(authentication_status(executable))
    status["login_pending"] = account.pending
    status["message"] = (
        "Finish sign-in in Claude's browser window."
        if account.pending
        else "Claude is signed in. Usage is billed directly under the selected account's terms."
        if status["signed_in"]
        else "Sign in using the Claude runtime to continue."
    )
    if action == "add_model":
        if not status["signed_in"]:
            raise ProviderConnectionError("Sign in through Claude before adding a runtime model.")
        model = options.get("model")
        if (
            not isinstance(model, dict)
            or set(model) != {"id", "context_window", "max_output_tokens"}
            or not isinstance(model["id"], str)
            or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._\[\]-]{0,127}", model["id"])
        ):
            raise ProviderConnectionError("Enter an official Claude model alias or model ID.")
        if model["max_output_tokens"] is not None:
            raise ProviderConnectionError("The Claude runtime manages the response allowance.")
        capacity = model["context_window"]
        if capacity is not None and (
            type(capacity) is not int or not 512 <= capacity <= 10_000_000
        ):
            raise ProviderConnectionError(
                "Enter a positive context limit of at least 512 tokens, or leave it blank."
            )
        result = save_provider_settings(
            config_path,
            provider_id="claude_runtime",
            preset_id="claude_runtime",
            base_url="https://claude.ai",
            credential=None,
            model_id=model["id"],
            model_name=f"{model['id']} (Claude)",
            context_window=None,
            configured_context_window=capacity,
            max_output_tokens=None,
            transport="claude_code",
            supports_streaming=True,
            supports_tools=True,
            runtime_executable=str(executable),
        )
        status["saved_model"] = result["canonical_model_id"]
    return status


def main() -> None:
    """Register the same provider for headless sessions, including SylviaBench."""
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Connect Sylvia to the user's native Claude runtime"
    )
    parser.add_argument(
        "--config", type=Path, required=True, help="Sylvia config.yaml in its owner data directory"
    )
    parser.add_argument("--executable")
    parser.add_argument("--model")
    parser.add_argument("--context-window", type=int)
    args = parser.parse_args()
    if args.config.name != "config.yaml":
        parser.error("--config must name config.yaml in the selected owner directory")
    options: dict[str, object] = {}
    if args.executable is not None:
        options["executable"] = args.executable
    if args.model:
        options["model"] = {
            "id": args.model,
            "context_window": args.context_window,
            "max_output_tokens": None,
        }
    try:
        result = claude_settings_action(
            args.config.parent, "add_model" if args.model else "status", options
        )
    except ProviderConnectionError as error:
        parser.exit(1, str(error) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
