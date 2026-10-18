"""Owner commands for managed account sign-in and subscription model selection."""

from __future__ import annotations

import atexit
import threading
import webbrowser
from pathlib import Path

from sylvia_gm.config.provider_settings import (
    provider_settings,
    refresh_subscription_reasoning,
    save_provider_settings,
)
from sylvia_gm.provider._chatgpt_account import ChatGPTAccount
from sylvia_gm.provider._codex_rpc import CodexConnection, CodexConnectionError

_accounts: dict[Path, ChatGPTAccount] = {}
_lock = threading.RLock()


def _account(storage_root: Path) -> ChatGPTAccount:
    """Use one account controller per owner profile, never the user's ordinary Codex home."""
    home = storage_root.resolve() / "chatgpt-runtime"
    with _lock:
        if home not in _accounts:
            connection = CodexConnection(home)
            _accounts[home] = ChatGPTAccount(connection)
            atexit.register(connection.close)
        return _accounts[home]


def refresh_saved_subscription_reasoning(storage_root: Path) -> bool:
    """Refresh legacy saved models through discovery, without login prompts or inference.

    An unavailable runtime/account leaves support unknown. Explicit discovery in Settings or
    the next owner startup can try again; failure never invents an effort list.
    """
    try:
        models = _account(storage_root).models()
    except CodexConnectionError:
        return False
    refresh_subscription_reasoning(storage_root / "config.yaml", models)
    return True


def chatgpt_settings_action(
    storage_root: Path,
    action: object,
    model: object = None,
) -> dict[str, object]:
    """Expose a small allowlist of account actions; credentials never cross this boundary."""
    if not isinstance(action, str) or action not in {
        "status",
        "login",
        "cancel",
        "logout",
        "models",
        "add_model",
    }:
        raise CodexConnectionError("This ChatGPT connection action is unavailable.")
    account = _account(storage_root)
    if action == "login":
        login = account.begin_login()
        if not webbrowser.open(str(login["auth_url"]), new=2):
            account.cancel_login()
            raise CodexConnectionError(
                "The sign-in browser could not open. Check your default browser."
            )
        return {"login_pending": True, "signed_in": False}
    if action == "cancel":
        account.cancel_login()
        return account.status()
    if action == "logout":
        return account.logout()
    status = account.status()
    if action in {"models", "add_model"}:
        models = account.models()
        refresh_subscription_reasoning(storage_root / "config.yaml", models)
        status["models"] = models
        if action == "add_model":
            if not isinstance(model, dict) or set(model) != {
                "id",
                "context_window",
                "max_output_tokens",
            }:
                raise CodexConnectionError("Choose a model and supply its capacity settings.")
            selected = next((item for item in models if item["id"] == model["id"]), None)
            if selected is None:
                raise CodexConnectionError(
                    "This model is not advertised for this ChatGPT connection."
                )
            if selected.get("unavailable_reason"):
                raise CodexConnectionError(str(selected["unavailable_reason"]))
            configured = provider_settings(storage_root / "config.yaml")["configured"]
            prior: dict[str, object] = next(
                (
                    saved_model
                    for provider in (configured if isinstance(configured, list) else [])
                    if provider["provider_id"] == "chatgpt_subscription"
                    for saved_model in provider["models"]
                    if saved_model["id"] == selected["id"]
                ),
                {},
            )
            runtime = selected.get("runtime_context_window")
            runtime_source = selected.get("runtime_context_source")
            runtime_observed = selected.get("runtime_context_observed_at")
            prior_runtime = prior.get("runtime_context_window")
            if isinstance(prior_runtime, int) and (
                runtime is None
                or prior.get("runtime_context_source") == "runtime_reported" and prior_runtime < runtime
            ):
                runtime = prior_runtime
                runtime_source = prior.get("runtime_context_source")
                runtime_observed = prior.get("runtime_context_observed_at")
            output_reference = selected.get("output_reference_tokens") or prior.get("declared_max_output_tokens")
            capacity = model["context_window"] if model["context_window"] is not None else runtime
            output = model["max_output_tokens"] if model["max_output_tokens"] is not None else output_reference
            if (
                model["context_window"] is not None and (
                    isinstance(model["context_window"], bool)
                    or not isinstance(model["context_window"], int)
                    or model["context_window"] < 512
                )
                or
                any(isinstance(value, bool) or not isinstance(value, int)
                    or value <= 0 or value > 10_000_000
                    for value in (capacity, output) if value is not None)
            ):
                raise CodexConnectionError(
                    "Enter positive token capacities, with room for input as well as the reply."
                )
            saved = save_provider_settings(
                storage_root / "config.yaml",
                provider_id="chatgpt_subscription",
                preset_id="chatgpt_subscription",
                base_url="https://chatgpt.com",
                credential=None,
                model_id=selected["id"],
                model_name=f"{selected.get('name') or selected['id']} (ChatGPT)",
                context_window=None,
                declared_context_window=prior.get("declared_context_window"),
                declared_context_source=prior.get("declared_context_source"),
                runtime_context_window=runtime,
                runtime_context_source=runtime_source,
                runtime_context_observed_at=runtime_observed,
                configured_context_window=model["context_window"],
                max_output_tokens=model["max_output_tokens"],
                declared_max_output_tokens=output_reference,
                transport="codex_app_server",
                supports_streaming=True,
                supports_tools=True,
                supported_reasoning_efforts=selected.get("supported_reasoning_efforts"),
                default_reasoning_effort=selected.get("default_reasoning_effort"),
            )
            status["saved_model"] = saved["canonical_model_id"]
    return status
