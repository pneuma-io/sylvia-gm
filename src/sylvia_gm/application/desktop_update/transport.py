"""Fixed official HTTPS transport and verification-only download staging."""

from __future__ import annotations

import errno
import hashlib
import os
import re
import socket
import ssl
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, suppress
from email.message import Message
from functools import partial
from http.client import HTTPException, HTTPMessage, HTTPResponse, HTTPSConnection
from pathlib import Path
from typing import IO, Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from .manifest import MANIFEST_LIMIT, OFFICIAL_RELEASES, Manifest, UpdateError

_CHUNK = 256 * 1024
_REDIRECT_HOSTS = frozenset({"github.com", "release-assets.githubusercontent.com"})


def official_request(url: str) -> None:
    if url == OFFICIAL_RELEASES:
        return
    try:
        parsed = urlsplit(url)
        path = unquote(parsed.path, errors="strict")
    except (ValueError, UnicodeError) as error:
        raise UpdateError("untrusted_origin", "The update location is invalid.") from error
    prefix = "/pneuma-io/sylvia-gm/releases/download/"
    tail = path.removeprefix(prefix)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or parsed.query
        or parsed.fragment
        or re.search(r"%(?![0-9A-Fa-f]{2})", parsed.path)
        or not path.startswith(prefix)
        or "\\" in path
        or "\\" in url
        or any(ord(char) <= 32 for char in url)
        or any(ord(char) < 32 for char in path)
        or len(tail.split("/")) != 2
        or any(part in ("", ".", "..") for part in tail.split("/"))
    ):
        raise UpdateError("untrusted_origin", "Only official Sylvia release requests are allowed.")


class OfficialRedirects(HTTPRedirectHandler):
    """Follow only verified HTTPS release origins, never a metadata-selected origin."""

    max_redirections = 5
    max_repeats = 2

    def redirect_request(
        self,
        request: Request,
        response: IO[bytes],
        code: int,
        message: str,
        headers: HTTPMessage,
        new_url: str,
    ) -> Request | None:
        try:
            parsed = urlsplit(new_url)
        except ValueError as error:
            raise UpdateError(
                "unsafe_redirect", "The update service returned an unsafe redirect."
            ) from error
        if (
            parsed.scheme != "https"
            or parsed.netloc not in _REDIRECT_HOSTS
            or parsed.fragment
            or "\\" in new_url
            or any(ord(char) <= 32 for char in new_url)
        ):
            raise UpdateError("unsafe_redirect", "The update service returned an unsafe redirect.")
        if parsed.netloc == "github.com":
            official_request(new_url)
        return super().redirect_request(request, response, code, message, headers, new_url)


class Response(Protocol):
    headers: Message

    def read(self, maximum: int) -> bytes: ...


class Transport(Protocol):
    def open(
        self, url: str, cancel: threading.Event | None = None
    ) -> AbstractContextManager[Response]: ...


class _ConnectionCancellation:
    """Interrupt only sockets created by this request, including buffered trickle reads."""

    def __init__(self, cancel: threading.Event) -> None:
        self.cancel = cancel
        self._finished = threading.Event()
        self._lock = threading.Lock()
        self._sockets: list[socket.socket] = []
        self._thread = threading.Thread(
            target=self._watch, name="sylvia-update-cancel", daemon=True
        )

    @staticmethod
    def _interrupt(connection: socket.socket) -> None:
        # close() may wait for a buffered reader's lock. Shutdown wakes its underlying read.
        with suppress(OSError):  # A completed owned connection needs no interruption.
            connection.shutdown(socket.SHUT_RDWR)

    def bind(self, connection: socket.socket) -> None:
        with self._lock:
            self._sockets.append(connection)
            cancelled = self.cancel.is_set()
        if cancelled:
            self._interrupt(connection)
            _cancelled(self.cancel)

    def _watch(self) -> None:
        while not self._finished.wait(0.02):
            if self.cancel.is_set():
                with self._lock:
                    connections = tuple(self._sockets)
                for connection in connections:
                    self._interrupt(connection)
                return

    def start(self) -> None:
        _cancelled(self.cancel)
        self._thread.start()

    def finish(self) -> None:
        self._finished.set()
        self._thread.join()


class _TrackedConnection(HTTPSConnection):
    def __init__(self, host: str, *, cancellation: _ConnectionCancellation, **kwargs: Any) -> None:
        super().__init__(host, **kwargs)
        self._cancellation = cancellation

    def connect(self) -> None:
        super().connect()
        if self.sock is None:
            raise ConnectionError("The update connection did not open")
        self._cancellation.bind(self.sock)


class _TrackedHttpsHandler(HTTPSHandler):
    def __init__(self, context: ssl.SSLContext, cancellation: _ConnectionCancellation) -> None:
        super().__init__(context=context)
        self._cancellation = cancellation
        self._tls_context = context

    def https_open(self, request: Request) -> HTTPResponse:
        response: HTTPResponse = self.do_open(
            partial(_TrackedConnection, cancellation=self._cancellation), request,
            context=self._tls_context,
        )
        return response


class HttpsTransport:
    def __init__(self) -> None:
        # Normal certificate/hostname validation; no insecure transport or credential override.
        self._context = ssl.create_default_context()

    @contextmanager
    def open(self, url: str, cancel: threading.Event | None = None) -> Iterator[Response]:
        official_request(url)
        cancellation = _ConnectionCancellation(cancel if cancel is not None else threading.Event())
        opener = build_opener(
            _TrackedHttpsHandler(self._context, cancellation), OfficialRedirects()
        )
        request = Request(
            url,
            headers={
                "User-Agent": "Sylvia-GM-updater/1",
                "Accept-Encoding": "identity",
                "Accept": "application/octet-stream",
            },
        )
        cancellation.start()
        try:
            with opener.open(request, timeout=20) as response:
                final = urlsplit(response.geturl())
                if final.scheme != "https" or final.netloc not in _REDIRECT_HOSTS | {
                    "api.github.com"
                }:
                    raise UpdateError(
                        "unsafe_redirect", "The final update location is not trusted."
                    )
                yield response
        except HTTPError as error:
            _cancelled(cancellation.cancel)
            if error.code == 429 or (
                error.code == 403 and error.headers.get("X-RateLimit-Remaining") == "0"
            ):
                raise UpdateError(
                    "rate_limited", "The release service is rate limited. Try again later."
                ) from None
            raise UpdateError(
                "service_unavailable",
                "The official release service is unavailable. Try again later.",
            ) from None
        except (URLError, TimeoutError, ConnectionError, ssl.SSLError):
            _cancelled(cancellation.cancel)
            raise UpdateError(
                "network_unavailable", "The update connection failed. You can retry safely."
            ) from None
        finally:
            cancellation.finish()


def content_length(response: Response) -> int | None:
    headers = response.headers
    encoding = headers.get("Content-Encoding", "identity")
    if encoding.lower() != "identity":
        raise UpdateError("invalid_length", "Encoded update responses are unsupported.")
    lengths = headers.get_all("Content-Length") if hasattr(headers, "get_all") else None
    if lengths is not None and len(lengths) > 1:
        raise UpdateError("invalid_length", "The update response has conflicting lengths.")
    length = headers.get("Content-Length")
    if length is None:
        return None
    if (
        not isinstance(length, str)
        or not length.isascii()
        or not length.isdigit()
        or len(length) > 20
    ):
        raise UpdateError("invalid_length", "The update response length is invalid.")
    return int(length)


def _cancelled(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise UpdateError(
            "cancelled", "The update download was cancelled. The current app is unchanged."
        )


def _read(response: Response, maximum: int, cancel: threading.Event) -> bytes:
    _cancelled(cancel)
    try:
        result = response.read(maximum)
    except (OSError, HTTPException) as error:
        _cancelled(cancel)
        raise UpdateError(
            "network_unavailable", "The update connection was interrupted. You can retry safely."
        ) from error
    _cancelled(cancel)
    return result


def metadata_bytes(transport: Transport, url: str, cancel: threading.Event) -> bytes:
    _cancelled(cancel)
    with transport.open(url, cancel) as response:
        length = content_length(response)
        if length is not None and length > MANIFEST_LIMIT:
            raise UpdateError("metadata_limit", "Release metadata exceeds its size limit.")
        result = bytearray()
        while True:
            _cancelled(cancel)
            chunk = _read(response, min(_CHUNK, MANIFEST_LIMIT - len(result) + 1), cancel)
            if not chunk:
                break
            result.extend(chunk)
            if len(result) > MANIFEST_LIMIT:
                raise UpdateError("metadata_limit", "Release metadata exceeds its size limit.")
        if length is not None and len(result) != length:
            raise UpdateError(
                "download_incomplete", "Release metadata was interrupted. You can retry."
            )
        return bytes(result)


def download_artifact(
    transport: Transport,
    manifest: Manifest,
    stage: Path,
    cancel: threading.Event,
    progress: Callable[[int, int | None], None],
) -> Path:
    """Publish only a complete matching artifact; partial bytes never become executable input."""
    if not stage.is_dir() or stage.is_symlink():
        raise UpdateError("unsafe_staging", "The update staging folder is unavailable.")
    from sylvia_gm.builder._package_materializer import _reject_reparse_chain

    def checked_stage() -> tuple[int, int]:
        try:
            if stage.resolve(strict=True) != stage.absolute():
                raise ValueError("Aliased update directory")
            _reject_reparse_chain(stage, stage)
            value = stage.stat()
            return value.st_dev, value.st_ino
        except (OSError, ValueError) as error:
            raise UpdateError("unsafe_staging", "The update staging folder is unsafe.") from error

    identity = checked_stage()
    partial, complete = stage / "artifact.partial", stage / "artifact.verified"
    if partial.exists() or complete.exists():
        raise UpdateError(
            "staging_exists", "Previous update files need reconciliation before retry."
        )
    _cancelled(cancel)
    try:
        with transport.open(manifest.artifact_url, cancel) as response:
            length = content_length(response)
            if length is not None and length != manifest.artifact_size:
                raise UpdateError(
                    "invalid_length", "The download length does not match the signed release."
                )
            if checked_stage() != identity:
                raise UpdateError(
                    "unsafe_staging", "The update staging folder changed during the request."
                )
            digest, count = hashlib.sha256(), 0
            with partial.open("xb") as stream:
                progress(0, length)
                while True:
                    _cancelled(cancel)
                    chunk = _read(response, min(_CHUNK, manifest.artifact_size - count + 1), cancel)
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > manifest.artifact_size:
                        raise UpdateError(
                            "invalid_length", "The download exceeds its signed length."
                        )
                    if stream.write(chunk) != len(chunk):
                        raise OSError(errno.ENOSPC, "Incomplete staging write")
                    digest.update(chunk)
                    progress(count, length)
                _cancelled(cancel)
                if (
                    count != manifest.artifact_size
                    or digest.hexdigest() != manifest.artifact_sha256
                ):
                    raise UpdateError(
                        "artifact_mismatch", "The download does not match the signed release."
                    )
                stream.flush()
                os.fsync(stream.fileno())
            if complete.exists():
                raise UpdateError("staging_exists", "Update completion files changed unexpectedly.")
            if checked_stage() != identity:
                raise UpdateError(
                    "unsafe_staging", "The update staging folder changed during download."
                )
            partial.rename(complete)
        return complete
    except OSError as error:
        _cancelled(cancel)
        if error.errno == errno.ENOSPC:
            raise UpdateError(
                "disk_full", "There is not enough space to download this update."
            ) from None
        if isinstance(error, PermissionError):
            raise UpdateError(
                "permission_denied", "The update staging folder is not writable."
            ) from None
        raise UpdateError(
            "download_failed", "The update download failed. The current app is unchanged."
        ) from None
