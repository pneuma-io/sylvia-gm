"""Loopback browser adapter for the existing supervised owner protocol.

The browser receives projections, never an owner credential or filesystem API.
The child retains all campaign admission, stream leases, recovery and persistence.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import shutil
import socket
import sys
import tempfile
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from aiohttp import web

from sylvia_gm import __version__
from sylvia_gm.infrastructure.launch_handshake import LaunchHandshake

MAX_BYTES = 64 * 1024 * 1024
MAX_UPLOAD_BYTES = 48 * 1024 * 1024
MAX_UPLOAD_PARAMS_BYTES = 16 * 1024
COOKIE = "sylvia_webui"
REMOTE_COOKIE = "__Host-sylvia_webui"
REMOTE_SESSION_SECONDS = 12 * 60 * 60
OWNER_EXIT_TIMEOUT = 15
# Only existing player/application commands. Path-taking native commands are
# handled separately using server-owned upload/export destinations.
COMMANDS = frozenset(
    [
        "campaign_index",
        "ready_campaign_catalog",
        "start_ready_campaign",
        "customize_ready_campaign",
        "campaign_creation_catalog",
        "campaign_party_options",
        "builder_chat_index",
        "create_builder_chat",
        "rename_builder_chat",
        "archive_builder_chat",
        "restore_builder_chat",
        "delete_builder_chat",
        "display_snapshot",
        "player_projection",
        "player_projection_page",
        "campaign_panels",
        "cancel_display_load",
        "action_sequence",
        "compose_action_sequence",
        "execute_action_sequence",
        "player_action_availability",
        "execute_panel_action",
        "preview_panel_action",
        "panel_action_receipt",
        "execute_player_action",
        "preview_player_action",
        "resolve_mechanics_choice",
        "session_context",
        "session_history_page",
        "inspect_provider_thinking",
        "steer_session",
        "package_candidates",
        "inspect_package_candidate",
        "package_candidate_text",
        "compare_package_candidates",
        "rift_template",
        "start_rift_campaign",
        "start_guided_campaign",
        "start_guided_creation_subflow",
        "return_guided_creation_subflow",
        "model_choices",
        "provider_settings",
        "open_provider_setup",
        "discover_provider_models",
        "detect_local_providers",
        "validate_provider_model",
        "save_provider_settings",
        "remove_provider_settings",
        "switch_session_model",
        "switch_session_reasoning",
        "set_default_model",
        "admin_full_audit_status",
        "admin_full_audit_record",
        "set_admin_full_audit",
        "submit_session_input",
        "start_dm_session",
        "interrupt_stream",
        "interrupt_provider_stream",
        "disconnect_stream",
        "disconnect_provider_stream",
        "stream_heartbeat",
        "provider_stream_heartbeat",
        "narration_attempt_status",
        "provider_stream_status",
        "retryable_interruption",
        "manual_retry_session",
        "reconnect_stream",
        "reconnect_provider_stream",
        "confirm_character_card_import",
        "start_import_repair",
        "create_guided_campaign_draft",
        "navigate_guided_campaign_draft",
        "advance_guided_campaign_draft",
        "guided_campaign_contribution",
        "save_reusable_campaign_plan",
        "confirm_new_campaign",
        "begin_campaign_construction",
        "model_guidance",
        "finalize_campaign_construction",
        "catalog",
        "stop_agent_session",
        "resume_agent_session",
        "rename_campaign",
        "archive_campaign",
        "restore_archived_campaign",
        "trash_campaign",
        "restore_trashed_campaign",
        "purge_campaign",
        "campaign_rules_update_options",
        "preview_campaign_rules_update",
        "adopt_campaign_rules_update",
        "delete_reusable_library_entry",
        "campaign_sequel_context",
        "preview_campaign_sequel",
        "append_campaign_sequel",
        "load_desktop_drafts",
        "save_desktop_drafts",
        "record_local_fault",
    ]
)
EXPORTS = {
    "export_campaign_file": ("exports", ".sylvia-gm-campaign"),
    "export_package_candidate_file": ("exports", ".sylvia-gm-package"),
    "export_campaign_plan_file": ("exports", ".sylvia-gm-plan"),
    "backup_authority": ("backups", ".sqlite3"),
}
WRAPPED = frozenset(
    [
        "start_ready_campaign",
        "customize_ready_campaign",
        "compose_action_sequence",
        "execute_action_sequence",
        "campaign_sequel_context",
        "preview_campaign_sequel",
        "append_campaign_sequel",
    ]
)


class WebUIError(RuntimeError):
    """A safe error suitable for the local browser or launch terminal."""

    def __init__(self, message: str, code: str = "webui_unavailable") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RemoteAccess:
    """Explicit tailnet HTTPS origin and a digest of a user-provided access key."""

    origin: str
    key_digest: bytes = field(repr=False)

    @classmethod
    def from_file(cls, origin: str, key_file: Path) -> RemoteAccess:
        # Accept one canonical origin, never URL paths, wildcard hosts or header inference.
        if not re.fullmatch(
            r"https://(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.){2,}ts\.net"
            r"(?::[1-9][0-9]{0,4})?",
            origin,
        ):
            raise WebUIError(
                "Remote origin must be one exact lowercase https://node.tailnet.ts.net "
                "origin, with an optional port and no path."
            )
        parsed = urlsplit(origin)
        try:
            port = parsed.port
        except ValueError:
            raise WebUIError("Remote HTTPS port is invalid.") from None
        if port == 443 or len(parsed.hostname or "") > 253:
            raise WebUIError("Use the canonical HTTPS origin (omit the default :443 port).")
        try:
            with key_file.open("rb") as source:
                raw = source.read(131)
        except OSError:
            raise WebUIError("Cannot read the selected WebUI access-key file.") from None
        matched = re.fullmatch(rb"([A-Za-z0-9_-]{43,128})(?:\r?\n)?", raw)
        if matched is None:
            raise WebUIError(
                "The access-key file must contain 43 to 128 random URL-safe "
                "characters, with an optional final newline."
            )
        return cls(origin, hashlib.sha256(matched[1]).digest())


class OwnerProcess:
    """Multiplex bounded requests over the same private pipes used by the desktop."""

    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root.resolve()
        self.process: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task | None = None
        self.pending: dict[str, asyncio.Future] = {}
        self.write_lock = asyncio.Lock()

    async def start(self) -> None:
        handshake = LaunchHandshake()
        credential = handshake.issue()
        # A frozen executable dispatches its default mode to the owner; Python
        # interpreter flags would be application arguments and cannot import it.
        command = [sys.executable] if getattr(sys, "frozen", False) else [
            sys.executable, "-B", "-m", "sylvia_gm.interfaces.owner_process",
        ]
        self.process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=MAX_BYTES + 1,
        )
        try:
            await self._write(
                {
                    "type": "launch",
                    "secret_hex": credential.secret_hex,
                    "database_path": str(self.data_root / "authority.sqlite3"),
                    "owner_id": "webui-" + secrets.token_hex(12),
                }
            )
            ready = await asyncio.wait_for(self._read(), timeout=120)
            if ready.get("type") != "ready" or ready.get("ok") is not True:
                raise WebUIError("The owner did not become ready.")
            handshake.verify(ready["response_hex"])
        except asyncio.CancelledError:
            try:
                await self.close()
            except WebUIError:
                pass  # Preserve launch cancellation after collecting the child.
            raise
        except Exception:
            try:
                await self.close()
            except WebUIError:
                pass  # The launch failure remains the caller's recovery reason.
            raise WebUIError(
                "Sylvia could not open this data root. If it is already open in another "
                "desktop or WebUI, close that owner first. No second writer was started."
            ) from None
        self.reader = asyncio.create_task(self._receive())

    async def _write(self, payload: dict, future: asyncio.Future | None = None) -> None:
        raw = json.dumps(payload, ensure_ascii=True, allow_nan=False).encode() + b"\n"
        if len(raw) > MAX_BYTES:
            raise WebUIError("The request is too large.", "request_too_large")
        async with self.write_lock:
            if self.process is None or self.process.stdin is None:
                raise WebUIError("The Sylvia owner is stopped.")
            if future is not None:
                if self.reader is None or self.reader.done():
                    raise WebUIError("The Sylvia owner is stopped. Launch it again to continue.")
                # Only requests reaching the pipe can have an uncertain outcome.
                # Register before write: drain may fail after the owner committed.
                self.pending[payload["id"]] = future
            self.process.stdin.write(raw)
            await self.process.stdin.drain()

    async def _read(self) -> dict:
        assert self.process is not None and self.process.stdout is not None
        raw = await self.process.stdout.readline()
        if not raw or len(raw) > MAX_BYTES:
            raise WebUIError("The Sylvia owner disconnected. Saved work is retained.")
        return json.loads(raw)

    async def _receive(self) -> None:
        try:
            while True:
                reply = await self._read()
                future = self.pending.get(reply.get("id"))
                if future is not None and not future.done():
                    future.set_result(reply)
        except (Exception, asyncio.CancelledError):
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(
                        WebUIError(
                            "The Sylvia owner disconnected. Reload to inspect saved work; "
                            "restart Sylvia before reconnecting if its owner stopped. "
                            "This request's outcome is unknown; it was not repeated.",
                            "outcome_unknown",
                        )
                    )

    async def call(self, method: str, params: dict | None = None) -> Any:
        if self.reader is None or self.reader.done():
            raise WebUIError("The Sylvia owner is stopped. Launch it again to continue.")
        identity = secrets.token_hex(16)
        future = asyncio.get_running_loop().create_future()
        try:
            await self._write({"id": identity, "method": method, "params": params or {}}, future)
            reply = await asyncio.shield(future)
            if not reply.get("ok"):
                error = reply.get("error", {})
                raise WebUIError(
                    error.get("message", "The command was rejected."),
                    error.get("code", "command_rejected"),
                )
            return reply.get("result")
        except (BrokenPipeError, ConnectionError):
            raise WebUIError(
                "The Sylvia owner connection failed before confirming the result. "
                "Restart Sylvia, then reconnect and inspect saved work before trying again; "
                "this request was not repeated.",
                "outcome_unknown",
            ) from None
        finally:
            self.pending.pop(identity, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()  # A failed drain may race the reader's disconnect error.

    async def close(self) -> None:
        """Collect the child, reporting nonzero exit or forced cleanup as failed shutdown."""
        process = self.process
        if process is None:
            return
        failure = None
        try:
            if process.returncode is None:
                try:
                    if self.reader is not None and not self.reader.done():
                        await asyncio.wait_for(self.call("shutdown"), timeout=10)
                    elif process.stdin is not None:
                        process.stdin.close()
                    await asyncio.wait_for(process.wait(), timeout=OWNER_EXIT_TIMEOUT)
                except (Exception, asyncio.CancelledError) as error:
                    failure = error
                    if process.returncode is None:
                        try:
                            process.terminate()
                        except ProcessLookupError:
                            pass  # Exit can race the decision to terminate this exact child.
                        await process.wait()
        finally:
            if process.returncode is not None:
                if self.reader is not None:
                    self.reader.cancel()
                    await asyncio.gather(self.reader, return_exceptions=True)
                self.process = None
        if isinstance(failure, asyncio.CancelledError):
            raise failure
        if failure is not None:
            reason = "timed out" if isinstance(failure, TimeoutError) else "did not finish gracefully"
            raise WebUIError(
                f"Sylvia owner shutdown {reason} (exit {process.returncode}). "
                "Saved work is retained; launch again to inspect it.", "owner_close_failed",
            ) from None
        if process.returncode != 0:
            raise WebUIError(
                f"Sylvia owner did not close cleanly (exit {process.returncode}). "
                "Saved work is retained; launch again to inspect it.", "owner_close_failed",
            )


def browser_command(method: str, raw: dict) -> tuple[str, dict]:
    """Adapt the frontend's native argument names without rewriting game payloads."""
    if method in WRAPPED:
        raw = raw.get("command", raw.get("request", raw))
    if not isinstance(raw, dict):
        raise WebUIError("Command parameters must be an object.", "invalid_request")
    params = {re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower(): value for key, value in raw.items()}
    if set(params) & {"source_path", "source_paths", "destination_path", "database_path", "host"}:
        raise WebUIError("Host file paths are unavailable in the browser.", "unsupported")
    if method == "resolve_mechanics_choice":
        params.setdefault("choice_sequence", 0)
    return method, params


class LocalWebUI:
    """Serve one local user's UI; the child remains the only application owner."""

    def __init__(
        self,
        owner: OwnerProcess,
        frontend: Path,
        origin: str,
        *,
        remote: RemoteAccess | None = None,
    ) -> None:
        self.owner = owner
        self.frontend = frontend.resolve()
        if remote is None and not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}", origin):
            raise WebUIError("Non-loopback browser access requires explicit remote authentication.")
        if remote is not None and origin != remote.origin:
            raise WebUIError("Remote authentication and browser origin must match.")
        self.remote = remote
        self.origin = origin
        self.authority = urlsplit(origin).netloc
        self.cookie = REMOTE_COOKIE if remote else COOKIE + "_" + self.authority.rsplit(":", 1)[-1]
        self.session = secrets.token_urlsafe(32)
        self.remote_sessions: dict[str, float] = {}
        self.login_attempts: list[float] = []
        self.stopped = asyncio.Event()
        self.shutdown_task: asyncio.Task | None = None
        self.requests: OrderedDict[str, tuple[str, asyncio.Task]] = OrderedDict()
        self.receipt_sizes: dict[str, int] = {}
        self.downloads: dict[str, Path] = {}
        self.download_directory: tempfile.TemporaryDirectory | None = None
        self.export_lock = asyncio.Lock()
        self.app = web.Application(client_max_size=MAX_BYTES, middlewares=[self.guard])
        self.app.add_routes(
            [
                web.post("/auth/login", self.login),
                web.post("/auth/logout", self.logout),
                web.post("/api/command", self.command),
                web.post("/api/upload/{kind}", self.upload),
                web.post("/api/stop", self.stop),
                web.get("/api/download/{identity}", self.download),
                web.get("/{path:.*}", self.asset),
            ]
        )
        self.app.on_cleanup.append(self.cleanup)

    @web.middleware
    async def guard(self, request: web.Request, handler):
        try:
            response = await self.guarded(request, handler)
        except web.HTTPException as error:
            response = web.Response(status=error.status, headers=error.headers, body=error.body)
        except WebUIError as error:
            response = web.json_response(
                {"ok": False, "error": {"code": error.code, "message": str(error)}}, status=400
            )
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "X-Frame-Options": "DENY",
                "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            }
        )
        return response

    def authenticated(self, request: web.Request) -> bool:
        """Remote sessions expire independently; local bootstrap retains its launch cookie."""
        token = request.cookies.get(self.cookie, "")
        if self.remote is None:
            return secrets.compare_digest(token.encode(), self.session.encode())
        now = time.monotonic()
        self.remote_sessions = {
            key: expiry for key, expiry in self.remote_sessions.items() if expiry > now
        }
        return token in self.remote_sessions

    async def guarded(self, request: web.Request, handler):
        # Raw Host only. Forwarded, X-Forwarded-* and Tailscale identity are never authority.
        if request.headers.getall("Host", []) != [self.authority]:
            raise web.HTTPForbidden(text="This WebUI accepts its exact configured Host only.")
        bootstrap_navigation = (
            request.method == "GET"
            and request.path in {"/", "/app.html"}
            and request.headers.get("Sec-Fetch-Mode") == "navigate"
            and request.headers.get("Sec-Fetch-Dest") == "document"
        )
        if request.headers.get("Sec-Fetch-Site") not in {None, "none", "same-origin"} and not (
            bootstrap_navigation and request.headers.get("Sec-Fetch-Site") == "cross-site"
        ):
            raise web.HTTPForbidden(text="Cross-site control requests are unavailable.")
        origin = request.headers.get("Origin")
        if request.headers.getall("Origin", []) not in ([], [self.origin]):
            raise web.HTTPForbidden(text="Origin rejected.")
        if request.method == "POST" and (
            origin != self.origin or request.headers.getall("X-Sylvia-WebUI", []) != ["1"]
        ):
            raise web.HTTPForbidden(text="A same-origin WebUI request is required.")
        if request.path.startswith("/auth/") and self.remote is None:
            raise web.HTTPNotFound()
        authenticated = self.authenticated(request)
        if self.remote is not None and not authenticated and request.path != "/auth/login":
            if request.method == "GET" and request.path in {"/", "/app.html"}:
                return web.Response(
                    text=(self.frontend / "webui-login.html").read_text(encoding="utf-8"),
                    content_type="text/html",
                )
            raise web.HTTPUnauthorized(text="Sign in to the private WebUI to connect.")
        if request.path.startswith("/api/") and not authenticated:
            raise web.HTTPUnauthorized(text="Open the local WebUI to connect.")
        return await handler(request)

    async def login(self, request: web.Request) -> web.Response:
        """Exchange a user-managed key for a bounded, revocable browser session."""
        assert self.remote is not None
        now = time.monotonic()
        # All requests share a budget: proxy/direct peer or forwarded IPs grant no exemption.
        self.login_attempts = [stamp for stamp in self.login_attempts if stamp > now - 60]
        if len(self.login_attempts) >= 5:
            raise web.HTTPTooManyRequests(
                text="Too many sign-in attempts. Wait one minute.", headers={"Retry-After": "60"}
            )
        self.login_attempts.append(now)
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType()
        raw = await request.clone(client_max_size=1024).read()
        try:
            payload = json.loads(raw)
            key = payload["key"]
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", key):
                raise ValueError
            valid = secrets.compare_digest(
                hashlib.sha256(key.encode()).digest(), self.remote.key_digest
            )
        except (ValueError, TypeError, KeyError):
            valid = False
        if not valid:
            raise web.HTTPUnauthorized(text="Access key rejected.")
        self.authenticated(request)  # Expire old sessions before checking capacity.
        self.remote_sessions.pop(request.cookies.get(self.cookie, ""), None)
        if len(self.remote_sessions) >= 32:
            raise web.HTTPTooManyRequests(
                text="Session limit reached. Sign out or restart the WebUI."
            )
        token = secrets.token_urlsafe(32)
        self.remote_sessions[token] = now + REMOTE_SESSION_SECONDS
        response = web.json_response({"ok": True})
        response.set_cookie(
            self.cookie,
            token,
            secure=True,
            httponly=True,
            samesite="Strict",
            path="/",
            max_age=REMOTE_SESSION_SECONDS,
        )
        return response

    async def logout(self, request: web.Request) -> web.Response:
        """Revoke this browser's session without stopping the shared owner."""
        self.remote_sessions.pop(request.cookies.get(self.cookie, ""), None)
        response = web.json_response({"ok": True, "result": {"status": "signed_out"}})
        response.del_cookie(self.cookie, path="/", secure=True, httponly=True, samesite="Strict")
        return response

    async def asset(self, request: web.Request) -> web.Response:
        path = request.match_info["path"] or "app.html"
        if path == "app.html":
            text = (self.frontend / path).read_text(encoding="utf-8")
            if self.remote is not None:
                text = text.replace("<html", '<html data-webui-remote="true"', 1)
            text = text.replace(
                "</head>", '<link rel="stylesheet" href="webui-mobile.css">\n</head>'
            )
            text = text.replace(
                '<script src="panel-workspace.js">',
                '<script src="webui-bridge.js"></script>\n'
                '<script src="webui-mobile.js"></script>\n<script src="panel-workspace.js">',
            )
            response = web.Response(text=text, content_type="text/html")
            # The per-launch session never enters a URL, script or browser storage.
            if self.remote is None:
                response.set_cookie(self.cookie, self.session, httponly=True, samesite="Strict")
            return response
        target = (self.frontend / path).resolve()
        if (
            not target.is_relative_to(self.frontend)
            or not target.is_file()
            or target.suffix not in {".js", ".css", ".png", ".svg", ".woff2"}
        ):
            raise web.HTTPNotFound()
        return web.Response(
            body=target.read_bytes(),
            content_type={
                ".js": "text/javascript",
                ".css": "text/css",
                ".png": "image/png",
                ".svg": "image/svg+xml",
                ".woff2": "font/woff2",
            }[target.suffix],
        )

    async def command(self, request: web.Request) -> web.Response:
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType()
        try:
            payload = await request.json()
            identity, method, params = payload["id"], payload["method"], payload.get("params", {})
            if (
                not isinstance(identity, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identity)
                or not isinstance(method, str)
                or not isinstance(params, dict)
            ):
                raise ValueError
            digest = hashlib.sha256(
                json.dumps(
                    [method, params],
                    sort_keys=True,
                    allow_nan=False,
                ).encode()
            ).hexdigest()
        except (ValueError, TypeError, KeyError):
            raise WebUIError("Malformed WebUI request.", "invalid_request") from None
        previous = self.requests.get(identity)
        if previous is not None:
            if previous[0] != digest:
                raise WebUIError(
                    "This request identity has different parameters.", "idempotency_conflict"
                )
            task = previous[1]
        else:
            # Completed transport receipts are bounded; durable action receipts
            # and replay protection always remain in the existing owner.
            for key, (_, old) in list(self.requests.items()):
                if len(self.requests) < 128:
                    break
                if old.done():
                    del self.requests[key]
                    self.receipt_sizes.pop(key, None)
            if len(self.requests) >= 128:
                raise WebUIError(
                    "Too many pending requests. Wait or Stop the current work.", "busy"
                )
            task = asyncio.create_task(self.invoke(method, params))
            self.requests[identity] = (digest, task)
            task.add_done_callback(lambda done: self.trim_receipts(identity, done))
        return web.json_response({"ok": True, "result": await asyncio.shield(task)})

    def trim_receipts(self, identity: str, task: asyncio.Task) -> None:
        """Bound transient replay memory; durable mechanics receipts stay owner-owned."""
        if task.cancelled():
            return
        error = task.exception()
        self.receipt_sizes[identity] = 1024 if error else len(json.dumps(task.result()).encode())
        for key, (_, old) in list(self.requests.items()):
            if sum(self.receipt_sizes.values()) <= 16 * 1024 * 1024:
                break
            if old.done():
                self.requests.pop(key, None)
                self.receipt_sizes.pop(key, None)

    async def invoke(self, method: str, raw: dict) -> Any:
        method, params = browser_command(method, raw)
        if method == "application_info":
            return {
                "product_name": "Sylvia GM",
                "version": __version__,
                "release_channel": "source WebUI",
                "signing_status": "unsigned",
            }
        if method == "desktop_update_startup_state":
            return {"pending": False}
        if method == "desktop_update_state":
            return {
                "phase": "unavailable",
                "current_version": __version__,
                "message": "Application updates require the desktop application.",
            }
        if method in EXPORTS:
            return await self.export(method, params)
        if method not in COMMANDS:
            raise WebUIError(
                "This capability requires the desktop application "
                "and is unavailable in this WebUI.",
                "unsupported",
            )
        result = await self.owner.call(method, params)
        if method == "display_snapshot":
            # The browser uses this read only for identity, lifecycle and revisions.
            # State, participants and raw action/event arguments can contain hidden
            # mechanics even when a package supplies safe public panel projections.
            # Play and receipts use the existing dedicated projection/action APIs.
            return {
                key: result[key]
                for key in (
                    "campaign_id",
                    "display_name",
                    "lifecycle",
                    "writer",
                    "revision",
                    "package_lock",
                )
                if key in result
            }
        return result

    async def export(self, method: str, params: dict) -> dict:
        folder, suffix = EXPORTS[method]
        identity = params.get("backup_id" if method == "backup_authority" else "export_id")
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identity):
            raise WebUIError("Choose a valid export identity.", "invalid_request")
        # Keep each handle bound to the bytes it exported, even if a later
        # command reuses the owner's artifact filename. Serialize copy with export.
        async with self.export_lock:
            result = await self.owner.call(
                "backup_database" if method == "backup_authority" else method, params
            )
            if self.download_directory is None:
                self.download_directory = tempfile.TemporaryDirectory(
                    prefix=".webui-download-", dir=self.owner.data_root
                )
            token = secrets.token_urlsafe(24)
            path = Path(self.download_directory.name) / (token + suffix)
            shutil.copyfile(self.owner.data_root / folder / (identity + suffix), path)
            if len(self.downloads) >= 128:
                self.downloads.pop(next(iter(self.downloads))).unlink(missing_ok=True)
            self.downloads[token] = path
        # Drop native artifact paths before handing anything to the browser.
        return {
            "status": "exported",
            "download": "/api/download/" + token,
            "filename": "sylvia" + suffix,
            "file_name": "sylvia" + suffix,
            "export": result.get("export", result) if isinstance(result, dict) else result,
        }

    async def download(self, request: web.Request) -> web.StreamResponse:
        path = self.downloads.get(request.match_info["identity"])
        if path is None or not path.is_file():
            raise web.HTTPNotFound()
        response = web.FileResponse(path)
        response.headers["Content-Disposition"] = f'attachment; filename="sylvia{path.suffix}"'
        return response

    async def _file_command(self, method: str, params: dict) -> Any:
        """Keep selected bytes alive until the already-issued owner operation settles."""
        operation = asyncio.create_task(self.owner.call(method, params))
        try:
            return await asyncio.shield(operation)
        except asyncio.CancelledError:
            # Cancelling HTTP cannot cancel a command already sent through the
            # owner pipe. Finish it before its temporary files leave.
            while not operation.done():
                try:
                    await asyncio.shield(operation)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not operation.cancelled():
                operation.exception()
            raise

    async def upload(self, request: web.Request) -> web.Response:
        kind = request.match_info["kind"]
        if kind not in {"package", "recovery", "builder", "character"}:
            raise web.HTTPNotFound()
        reader = await request.multipart()
        params: dict = {}
        received_params = False
        total = 0
        self.owner.data_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".webui-upload-", dir=self.owner.data_root
        ) as directory:
            paths = []
            async for part in reader:
                if part.name == "params" and part.filename is None:
                    if received_params:
                        raise WebUIError("Upload parameters were repeated.", "invalid_request")
                    received_params = True
                    if part.headers.get("Content-Transfer-Encoding"):
                        raise WebUIError(
                            "Encoded upload parameters are unsupported.", "invalid_request"
                        )
                    text = bytearray()
                    while True:
                        remaining = MAX_UPLOAD_PARAMS_BYTES + 1 - len(text)
                        # aiohttp needs a chunk at least as long as its multipart
                        # boundary. It validates boundaries at <=70 characters;
                        # 128 permits that lookahead even near the field limit.
                        chunk = await part.read_chunk(max(128, remaining))
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_UPLOAD_BYTES:
                            raise web.HTTPRequestEntityTooLarge(
                                max_size=MAX_UPLOAD_BYTES, actual_size=total
                            )
                        text.extend(chunk[:remaining])
                        if len(text) > MAX_UPLOAD_PARAMS_BYTES:
                            raise web.HTTPRequestEntityTooLarge(
                                max_size=MAX_UPLOAD_PARAMS_BYTES, actual_size=len(text)
                            )
                    try:
                        params = json.loads(text)
                    except (ValueError, UnicodeError):
                        raise WebUIError("Invalid upload parameters.", "invalid_request") from None
                    if not isinstance(params, dict):
                        raise WebUIError("Upload parameters must be an object.", "invalid_request")
                    browser_command("upload", params)
                elif part.name == "file" and part.filename:
                    if len(paths) >= (8 if kind == "builder" else 1):
                        raise WebUIError("Too many selected files.", "invalid_request")
                    name = part.filename
                    if (
                        Path(name).name != name
                        or Path(name).is_reserved()
                        or len(name) > 180
                        or name in {".", ".."}
                        or any(c in name for c in '\\/:\x00<>|?*"')
                        or any(ord(c) < 32 for c in name)
                    ):
                        raise WebUIError("Invalid selected filename.", "invalid_request")
                    target = Path(directory) / str(len(paths)) / name
                    target.parent.mkdir()
                    with target.open("xb") as output:
                        while chunk := await part.read_chunk(65536):
                            total += len(chunk)
                            if total > MAX_UPLOAD_BYTES:
                                raise web.HTTPRequestEntityTooLarge(
                                    max_size=MAX_UPLOAD_BYTES, actual_size=total
                                )
                            output.write(chunk)
                    paths.append(target)
                else:
                    raise WebUIError("Unsupported upload field.", "invalid_request")
            if not paths:
                raise WebUIError("Select a file first.", "invalid_request")
            if kind == "builder":
                result = await self._file_command(
                    "ingest_builder_sources",
                    {
                        "session_id": params.get("sessionId"),
                        "ingestion_id": params.get("ingestionId"),
                        "source_paths": [str(p) for p in paths],
                    },
                )
            else:
                method = {
                    "package": "install_package_file",
                    "recovery": "recover_campaign_file",
                    "character": "preview_character_card_file",
                }[kind]
                _, safe = browser_command(method, params)
                result = await self._file_command(method, {**safe, "source_path": str(paths[0])})
                if kind == "recovery":
                    result = {"status": "recovered", "file_name": paths[0].name, "recovery": result}
                elif kind == "package" and result.get("status") != "repairable":
                    result = {
                        "status": "installed",
                        "file_name": paths[0].name,
                        "candidate": result,
                    }
            return web.json_response({"ok": True, "result": result})

    async def stop(self, request: web.Request) -> web.Response:
        if self.shutdown_task is None:
            self.shutdown_task = asyncio.create_task(self.owner.close())
        await asyncio.shield(self.shutdown_task)
        asyncio.get_running_loop().call_later(0.1, self.stopped.set)
        return web.json_response({"ok": True, "result": {"status": "closed"}})

    async def cleanup(self, app: web.Application) -> None:
        self.remote_sessions.clear()
        try:
            await self.owner.close()
        finally:
            for _, task in self.requests.values():
                if not task.done():
                    task.cancel()
            await asyncio.gather(*(task for _, task in self.requests.values()), return_exceptions=True)
            self.downloads.clear()
            if self.download_directory is not None:
                self.download_directory.cleanup()
                self.download_directory = None


async def serve(data_root: Path, port: int = 0, *, remote: RemoteAccess | None = None) -> None:
    """Start explicitly, show the real address, and stop on UI Stop or Ctrl+C."""
    bundle = getattr(sys, "_MEIPASS", None)
    frontend = (
        Path(bundle) / "webui" if bundle is not None
        else Path(__file__).resolve().parents[3] / "src-tauri" / "frontend"
    )
    if remote is not None and port == 0:
        raise WebUIError("Private HTTPS mode requires an explicit nonzero --port for the proxy.")
    if not (frontend / "app.html").is_file():
        raise WebUIError("The WebUI assets are unavailable in this application or source checkout.")
    owner = OwnerProcess(data_root)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", port))
        address = f"http://127.0.0.1:{listener.getsockname()[1]}"
        await owner.start()
        ui = LocalWebUI(owner, frontend, remote.origin if remote else address, remote=remote)
        runner = web.AppRunner(ui.app, access_log=None)
        try:
            await runner.setup()
            await web.SockSite(runner, listener).start()
            if remote:
                print(
                    f"Sylvia private WebUI: {remote.origin}/app.html (access key required)",
                    flush=True,
                )
                print(
                    f"Loopback proxy target: {address}; direct local bootstrap is disabled.",
                    flush=True,
                )
            else:
                print(f"Sylvia WebUI ready: {address}/app.html", flush=True)
            print(
                "Stop from the WebUI or press Ctrl+C. "
                "Closing a tab keeps saved sessions available.",
                flush=True,
            )
            await ui.stopped.wait()
        finally:
            await runner.cleanup()
            await owner.close()
