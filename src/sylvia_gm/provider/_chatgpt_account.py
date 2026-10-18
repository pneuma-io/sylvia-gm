"""Managed ChatGPT sign-in with safe owner-to-renderer account projections."""

from __future__ import annotations

import threading
from urllib.parse import urlsplit

from sylvia_gm.provider._codex_rpc import CodexConnection, CodexConnectionError
from sylvia_gm.provider._chatgpt_model_facts import subscription_model_facts


class ChatGPTAccount:
    """Keep login lifecycle separate from model selection and campaign persistence."""

    def __init__(self, connection: CodexConnection) -> None:
        self.connection = connection
        self._login_id: str | None = None
        self._login_failed = False
        self._lock = threading.RLock()

    def status(self) -> dict[str, object]:
        """Read account status without returning tokens, raw errors or auth file content."""
        with self._lock:
            self.connection.start()
            for event in self.connection.notifications():
                params = event.get("params", {})
                if (
                    event.get("method") == "account/login/completed"
                    and isinstance(params, dict)
                    and self._login_id is not None
                    and params.get("loginId") == self._login_id
                ):
                    self._login_id = None
                    self._login_failed = params.get("success") is not True
            reply = self.connection.call("account/read", {"refreshToken": False})
            account = reply.get("account")
            signed_in = isinstance(account, dict) and account.get("type") == "chatgpt"
            details = account if isinstance(account, dict) and signed_in else {}
            if signed_in:
                self._login_id = None
                self._login_failed = False
            return {
                "signed_in": signed_in,
                "login_pending": self._login_id is not None,
                "login_failed": self._login_failed,
                "plan_type": details.get("planType")
                if isinstance(details.get("planType"), str)
                else None,
                "email": details.get("email") if isinstance(details.get("email"), str) else None,
            }

    def begin_login(self) -> dict[str, object]:
        """Start browser consent using only the runtime's managed ChatGPT flow."""
        with self._lock:
            self.connection.start()
            if self._login_id is not None:
                self.cancel_login()
            result = self.connection.call("account/login/start", {"type": "chatgpt"})
            login_id, url = result.get("loginId"), result.get("authUrl")
            if isinstance(login_id, str) and login_id:
                self._login_id = login_id
            try:
                parsed = urlsplit(url) if isinstance(url, str) else None
                valid = (
                    parsed is not None
                    and parsed.scheme == "https"
                    and parsed.hostname in {"auth.openai.com", "auth.chatgpt.com"}
                    and not parsed.username
                    and not parsed.password
                    and parsed.port in {None, 443}
                )
            except ValueError:
                valid = False
            if result.get("type") != "chatgpt" or not self._login_id or not valid:
                if self._login_id:
                    self.cancel_login()
                raise CodexConnectionError(
                    "The Codex runtime returned an unsupported sign-in link."
                )
            self._login_failed = False
            return {"auth_url": url, "login_pending": True}

    def cancel_login(self) -> dict[str, object]:
        """Cancel only the login attempt created by this Sylvia account controller."""
        with self._lock:
            if self._login_id is not None:
                self.connection.call("account/login/cancel", {"loginId": self._login_id})
                self._login_id = None
            self._login_failed = False
            return {"login_pending": False}

    def logout(self) -> dict[str, object]:
        """Remove only Sylvia's managed login; never touch the normal Codex profile."""
        with self._lock:
            self.connection.start()
            self.cancel_login()
            self.connection.call("account/logout", {})
            return self.status()

    def models(self) -> list[dict[str, object]]:
        """List advertised models after sign-in; discovery is not inference verification."""
        with self._lock:
            if not self.status()["signed_in"]:
                raise CodexConnectionError("Sign in to ChatGPT before finding subscription models.")
            models: list[dict[str, object]] = []
            cursor: str | None = None
            seen: set[str] = set()
            for _ in range(20):
                reply = self.connection.call("model/list", {"cursor": cursor, "limit": 100})
                data = reply.get("data")
                if not isinstance(data, list):
                    raise CodexConnectionError(
                        "The Codex runtime returned an unsupported model list."
                    )
                for item in data:
                    if not isinstance(item, dict) or not isinstance(item.get("model"), str):
                        continue
                    efforts = item.get("supportedReasoningEfforts", [])
                    supported = list(dict.fromkeys(
                        effort["reasoningEffort"]
                        for effort in efforts if isinstance(effort, dict)
                        and isinstance(effort.get("reasoningEffort"), str)
                        and effort["reasoningEffort"]
                    )) if isinstance(efforts, list) else []
                    suggested = item.get("defaultReasoningEffort")
                    modalities = item.get("inputModalities", ["text", "image"])
                    supports_text = isinstance(modalities, list) and "text" in modalities
                    unavailable = (
                        "This model does not advertise text input."
                        if not supports_text else None
                    )
                    models.append(
                        {
                            "id": item["model"],
                            "name": item.get("displayName", item["model"]),
                            "verified": False,
                            "unavailable_reason": unavailable,
                            "supported_reasoning_efforts": supported,
                            "default_reasoning_effort": (
                                suggested if suggested in supported else None
                            ),
                        }
                    )
                cursor = reply.get("nextCursor")
                if cursor is None:
                    facts = subscription_model_facts(
                        self.connection.home, {str(model["id"]) for model in models},
                    )
                    for model in models:
                        model.update(facts.get(str(model["id"]), {}))
                    return models
                if not isinstance(cursor, str) or cursor in seen:
                    break
                seen.add(cursor)
            raise CodexConnectionError("The Codex runtime did not finish listing models.")
