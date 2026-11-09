"""Download fixtures preserve live files; one owned socket pair exercises real buffering."""

import errno
import hashlib
import io
import socket
import ssl
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from email.message import Message
from http.client import HTTPResponse
from urllib.request import Request

import pytest

from sylvia_gm.application.desktop_update.manifest import MANIFEST_LIMIT, UpdateError
from sylvia_gm.application.desktop_update.transport import (
    HttpsTransport,
    OfficialRedirects,
    _ConnectionCancellation,
    _TrackedConnection,
    _TrackedHttpsHandler,
    download_artifact,
    metadata_bytes,
    official_request,
)
from tests.test_desktop_update_manifest import manifest_fixture, parse


class FixtureTransport:
    def __init__(self, data, length=None, cancel=None):
        self.data, self.length, self.cancel = data, length, cancel
        self.closed = False

    @contextmanager
    def open(self, url, cancel=None):
        stream = io.BytesIO(self.data)
        headers = Message()
        if self.length is not None:
            headers["Content-Length"] = str(self.length)

        cancel = self.cancel

        class Response:
            def read(self, maximum):
                result = stream.read(maximum)
                if cancel is not None:
                    cancel.set()
                return result

        response = Response()
        response.headers = headers
        try:
            yield response
        finally:
            stream.close()
            self.closed = True


def download_case(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    (live / "sylvia-gm.exe").write_bytes(b"working application")
    (live / "campaign.sqlite3").write_bytes(b"campaign unchanged")
    stage = tmp_path / "owned-stage"
    stage.mkdir()
    raw = b"non-executable download fixture" * 100
    manifest = replace(
        parse(manifest_fixture()),
        artifact_size=len(raw),
        artifact_sha256=hashlib.sha256(raw).hexdigest(),
    )
    return live, stage, raw, manifest


@pytest.mark.parametrize("known_length", [False, True])
def test_only_complete_matching_download_is_published_with_truthful_progress(
    tmp_path, known_length
):
    live, stage, raw, manifest = download_case(tmp_path)
    before = {p.name: p.read_bytes() for p in live.iterdir()}
    transport = FixtureTransport(raw, len(raw) if known_length else None)
    progress = []
    target = download_artifact(
        transport,
        manifest,
        stage,
        threading.Event(),
        lambda count, total: progress.append((count, total)),
    )
    assert target.name == "artifact.verified" and target.read_bytes() == raw
    assert not (stage / "artifact.partial").exists()
    assert progress[0] == (0, len(raw) if known_length else None)
    assert progress[-1] == (len(raw), len(raw) if known_length else None)
    assert transport.closed and {p.name: p.read_bytes() for p in live.iterdir()} == before


@pytest.mark.parametrize("failure", ["cancel", "truncated", "extra", "tampered", "header"])
def test_failed_or_cancelled_download_never_becomes_apply_input(tmp_path, failure):
    live, stage, raw, manifest = download_case(tmp_path)
    before = {p.name: p.read_bytes() for p in live.iterdir()}
    event = threading.Event()
    supplied = {"truncated": raw[:-1], "extra": raw + b"x", "tampered": b"x" + raw[1:]}.get(
        failure, raw
    )
    transport = FixtureTransport(
        supplied,
        len(raw) + 1 if failure == "header" else None,
        event if failure == "cancel" else None,
    )
    with pytest.raises(UpdateError):
        download_artifact(transport, manifest, stage, event, lambda *_: None)
    assert not (stage / "artifact.verified").exists()
    assert transport.closed and {p.name: p.read_bytes() for p in live.iterdir()} == before


def test_previous_partial_requires_reconciliation_and_is_never_overwritten(tmp_path):
    _, stage, raw, manifest = download_case(tmp_path)
    partial = stage / "artifact.partial"
    partial.write_bytes(b"previous interrupted bytes")
    with pytest.raises(UpdateError) as caught:
        download_artifact(
            FixtureTransport(raw), manifest, stage, threading.Event(), lambda *_: None
        )
    assert caught.value.code == "staging_exists"
    assert partial.read_bytes() == b"previous interrupted bytes"


def test_disk_full_during_download_keeps_live_files_and_never_publishes_partial(
    tmp_path, monkeypatch
):
    live, stage, raw, manifest = download_case(tmp_path)
    before = {path.name: path.read_bytes() for path in live.iterdir()}
    original_open = type(stage).open

    def disk_full(path, *args, **kwargs):
        if path == stage / "artifact.partial":
            raise OSError(errno.ENOSPC, "controlled disk-full fixture")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(type(stage), "open", disk_full)
    with pytest.raises(UpdateError) as rejected:
        download_artifact(
            FixtureTransport(raw), manifest, stage, threading.Event(), lambda *_: None
        )
    assert rejected.value.code == "disk_full"
    assert not (stage / "artifact.verified").exists()
    assert {path.name: path.read_bytes() for path in live.iterdir()} == before


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/a",
        "file:///C:/update.exe",
        "https://example.test/update.exe",
        "https://api.github.com/repos/another/repo/releases",
    ],
)
def test_transport_does_not_offer_an_arbitrary_url_capability(url):
    with pytest.raises(UpdateError):
        official_request(url)


@pytest.mark.parametrize(
    "target",
    [
        "http://release-assets.githubusercontent.com/a",
        "https://release-assets.githubusercontent.com.evil.test/a",
        "file:///C:/update.exe",
        "https://github.com/another/repo/releases/download/v1/a.exe",
    ],
)
def test_unsafe_redirects_reject_before_creating_a_followup_request(target):
    original = Request("https://github.com/pneuma-io/sylvia-gm/releases/download/v1/a.exe")
    with pytest.raises(UpdateError):
        OfficialRedirects().redirect_request(original, None, 302, "Found", {}, target)


def test_official_release_cdn_redirect_keeps_https_without_adding_credentials():
    original = Request("https://github.com/pneuma-io/sylvia-gm/releases/download/v1/a.exe")
    request = OfficialRedirects().redirect_request(
        original,
        None,
        302,
        "Found",
        {},
        "https://release-assets.githubusercontent.com/github-production-release-asset/1/a",
    )
    assert request.full_url.startswith("https://release-assets.githubusercontent.com/")
    assert not request.has_header("Authorization") and not request.has_header("Cookie")


def test_metadata_limit_is_enforced_while_reading_without_content_length():
    transport = FixtureTransport(b"x" * (MANIFEST_LIMIT + 1))
    with pytest.raises(UpdateError) as caught:
        metadata_bytes(transport, "fixture-only", threading.Event())
    assert caught.value.code == "metadata_limit" and transport.closed


@pytest.mark.parametrize("kind", ["metadata", "artifact"])
def test_cancellation_interrupts_actual_buffered_trickle_without_server_release(tmp_path, kind):
    live, stage, _, manifest = download_case(tmp_path)
    before = {path.name: path.read_bytes() for path in live.iterdir()}
    reading, trickling, done = threading.Event(), threading.Event(), threading.Event()
    cancel, cleanup = threading.Event(), threading.Event()
    errors, transferred, sender_errors = [], [], []
    receiver, sender = socket.socketpair()
    receiver.settimeout(20)
    sender.settimeout(2)

    def send_slowly():
        try:
            sender.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 524288\r\n\r\n")
            while not cleanup.wait(0.002):
                sender.sendall(b"x")
                transferred.append(1)
                trickling.set()
        except OSError:
            sender_errors.append("owned socket interrupted")

    producer = threading.Thread(target=send_slowly)
    producer.start()

    class SocketTransport:
        @contextmanager
        def open(self, _url, signal=None):
            assert signal is cancel
            watcher = _ConnectionCancellation(signal)
            response = HTTPResponse(receiver)
            watcher.bind(receiver)
            watcher.start()
            try:
                response.begin()
                # urllib closes its connection's socket object while the HTTP file retains it.
                receiver.close()
                reading.set()
                yield response
            finally:
                response.close()
                watcher.finish()

    def consume():
        try:
            if kind == "metadata":
                metadata_bytes(SocketTransport(), "owned-fixture", cancel)
            else:
                fixture = replace(manifest, artifact_size=524288)
                download_artifact(SocketTransport(), fixture, stage, cancel, lambda *_: None)
        except UpdateError as error:
            errors.append(error.code)
        finally:
            done.set()

    consumer = threading.Thread(target=consume)
    consumer.start()
    try:
        assert reading.wait(3) and trickling.wait(3)
        started = time.monotonic()
        cancel.set()
        assert done.wait(2), "cancel must interrupt the real buffered read without server release"
        assert time.monotonic() - started < 2
        assert errors == ["cancelled"]
        assert len(transferred) < 4096, "must not drain the 256KiB requested buffer after cancel"
        assert not (stage / "artifact.verified").exists()
        assert {path.name: path.read_bytes() for path in live.iterdir()} == before
    finally:
        cleanup.set()
        sender.close()
        receiver.close()
        consumer.join(3)
        producer.join(3)
    assert not consumer.is_alive() and not producer.is_alive()


def test_interruptible_transport_keeps_normal_certificate_and_hostname_verification():
    context = HttpsTransport()._context
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_https_handler_binds_each_connection_to_its_cancel_scope_without_tls_override(monkeypatch):
    from urllib.request import HTTPSHandler

    scope = _ConnectionCancellation(threading.Event())
    context = HttpsTransport()._context
    observed = []

    def connect(_handler, factory, request, **kwargs):
        connection = factory(request.host, timeout=20, **kwargs)
        assert isinstance(connection, _TrackedConnection)
        assert connection._cancellation is scope
        assert connection._context is context
        assert connection._context.check_hostname
        observed.append(request.full_url)
        return "controlled response"

    monkeypatch.setattr(HTTPSHandler, "do_open", connect)
    handler = _TrackedHttpsHandler(context, scope)
    assert handler.https_open(Request("https://github.com/")) == "controlled response"
    assert observed == ["https://github.com/"]
