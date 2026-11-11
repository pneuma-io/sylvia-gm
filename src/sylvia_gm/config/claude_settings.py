"""Owner-only setup for Sylvia's own copy of Claude Code and its account-owned sign-in."""

from __future__ import annotations

import atexit
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from sylvia_gm.config.provider_settings import save_provider_settings
from sylvia_gm.provider._claude_install import ClaudeInstaller
from sylvia_gm.provider._claude_runtime import (
    authentication_status,
    find_runtime,
    native_auth_command,
    native_environment,
    process_options,
    runtime_version,
)
from sylvia_gm.provider._exceptions import ProviderConnectionError

_ACTIONS = frozenset({"status", "install", "login", "cancel", "logout", "add_model"})
_SETUP_NEEDED = (
    "Sylvia uses its own copy of Claude Code. Install it to continue; "
    "any Claude Code you already have is left alone."
)


class ClaudeAccount:
    """Retain this owner's setup worker and sign-in child; never retain Claude credentials."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.installer = ClaudeInstaller(home)
        self.login: subprocess.Popen[bytes] | None = None
        self.lock = threading.RLock()
        atexit.register(self.cancel)

    def cancel(self) -> None:
        """Stop our unfinished download and browser sign-in; a completed sign-in is kept."""
        self.installer.cancel()
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
                    env=native_environment(self.home),
                    **process_options(),
                )
            except OSError:
                raise ProviderConnectionError(
                    "Claude Code's sign-in could not start. Try again."
                ) from None

    def logout(self, executable: Path) -> None:
        """Sign Sylvia's profile out through the binary; no other Claude Code is touched."""
        self.cancel()
        try:
            subprocess.run(
                native_auth_command(executable, "logout"),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                env=native_environment(self.home),
                **process_options(),
            )
        except (OSError, subprocess.TimeoutExpired):
            raise ProviderConnectionError("Claude Code could not sign out. Try again.") from None

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
    """Expose an allowlist of safe setup actions and explicit model registration."""
    if not isinstance(action, str) or action not in _ACTIONS:
        raise ProviderConnectionError("This Claude connection action is unavailable.")
    if options is None:
        options = {}
    if not isinstance(options, dict) or set(options) - {"model", "account_type"}:
        raise ProviderConnectionError("Claude connection options are invalid.")
    home = storage_root.resolve() / "claude-runtime"
    with _lock:
        if home not in _accounts:
            _accounts[home] = ClaudeAccount(home)
        account = _accounts[home]
    if action == "cancel":
        account.cancel()
    if action == "install":
        account.installer.start()
    executable = find_runtime(home)
    status: dict[str, object] = {
        "installed": executable is not None,
        "supported": False,
        "version": None,
        "signed_in": False,
        "login_pending": account.pending,
        "auth_method": None,
        "billing": "unknown",
        "executable": str(executable) if executable is not None else None,
        "install": account.installer.progress(),
        "message": _SETUP_NEEDED,
    }
    if executable is None:
        if action in {"login", "logout", "add_model"}:
            raise ProviderConnectionError(_SETUP_NEEDED)
        return status
    try:
        status["version"] = runtime_version(executable, home)
    except ProviderConnectionError as error:
        status["message"] = str(error)
        if action in {"login", "logout", "add_model"}:
            raise
        return status
    status["supported"] = True
    if action == "logout":
        account.logout(executable)
    if action == "login":
        account_type = options.get("account_type", "subscription")
        if account_type not in {"subscription", "api"}:
            raise ProviderConnectionError("Choose a Claude subscription or Console API account.")
        account.begin_login(executable, api_account=account_type == "api")
    status.update(authentication_status(executable, home))
    status["login_pending"] = account.pending
    status["message"] = (
        "Finish sign-in in Claude's browser window."
        if account.pending
        else "Claude is signed in. Usage is billed directly under the selected account's terms."
        if status["signed_in"]
        else "Sign in through Sylvia's copy of Claude Code to continue."
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
            storage_root / "config.yaml",
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
        )
        status["saved_model"] = result["canonical_model_id"]
    return status


def _wait(storage_root: Path, step: str) -> dict[str, object]:
    """Poll the status the app shows until a headless install or sign-in has finished."""
    while True:
        status = claude_settings_action(storage_root, "status")
        install = status["install"]
        assert isinstance(install, dict)
        if step == "install" and install["state"] in {"checking", "downloading", "verifying"}:
            megabytes = [int(install[key] or 0) // 1024**2 for key in ("received", "total")]
            print(
                f"\rClaude Code {install['version'] or ''}: {install['state']}, "
                f"{megabytes[0]} of {megabytes[1]} MB   ",
                end="",
                file=sys.stderr,
                flush=True,
            )
        elif step != "login" or not status["login_pending"]:
            return status
        time.sleep(1)


def main() -> None:
    """Set up the same provider for headless sessions, including SylviaBench."""
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Set up Sylvia's own copy of Claude Code in a Sylvia data directory"
    )
    parser.add_argument(
        "--config", type=Path, required=True, help="Sylvia config.yaml in its owner data directory"
    )
    parser.add_argument(
        "--install", action="store_true", help="download or update Sylvia's copy of Claude Code"
    )
    parser.add_argument(
        "--login", action="store_true", help="sign in through Claude Code's browser page"
    )
    parser.add_argument(
        "--console", action="store_true", help="with --login, use an Anthropic Console account"
    )
    parser.add_argument("--logout", action="store_true", help="sign Sylvia's copy out")
    parser.add_argument("--model")
    parser.add_argument("--context-window", type=int)
    args = parser.parse_args()
    if args.config.name != "config.yaml":
        parser.error("--config must name config.yaml in the selected owner directory")
    root = args.config.parent
    try:
        if args.install:
            claude_settings_action(root, "install")
            install = _wait(root, "install")["install"]
            assert isinstance(install, dict)
            print(file=sys.stderr)
            if install["state"] != "ready":
                parser.exit(1, str(install["message"]) + "\n")
        if args.logout:
            claude_settings_action(root, "logout")
        if args.login:
            account = "api" if args.console else "subscription"
            claude_settings_action(root, "login", {"account_type": account})
            print("Finish sign-in in the browser page Claude Code opened.", file=sys.stderr)
            _wait(root, "login")
        if args.model:
            model = {
                "id": args.model,
                "context_window": args.context_window,
                "max_output_tokens": None,
            }
            result = claude_settings_action(root, "add_model", {"model": model})
        else:
            result = claude_settings_action(root, "status")
    except ProviderConnectionError as error:
        parser.exit(1, str(error) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
