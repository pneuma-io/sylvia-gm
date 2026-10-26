"""Controlled protocol fixtures qualify lifecycle safety, not signed native installation."""

import hashlib
import io
import json
import threading
from contextlib import contextmanager
from email.message import Message

import pytest

from sylvia_gm.application.desktop_update.controller import UpdateController
from sylvia_gm.application.desktop_update.manifest import OFFICIAL_RELEASES, UpdateError, Version
from sylvia_gm.application.desktop_update.storage import UpdateStore
from tests.test_desktop_update_manifest import accept_stub, envelope, manifest_fixture


class ReleaseFixture:
    def __init__(self):
        self.signed = manifest_fixture()
        self.artifact = b"lifecycle protocol fixture"
        self.signed["artifact"].update(
            size=len(self.artifact),
            sha256=hashlib.sha256(self.artifact).hexdigest(),
        )
        self.url = (
            "https://github.com/pneuma-io/sylvia-gm/releases/download/"
            "v0.3.0-alpha.2/sylvia-gm-0.3.0-alpha.2-windows-x86_64-portable-update.json"
        )
        self.index = [
            {
                "draft": False,
                "prerelease": True,
                "tag_name": "v0.3.0-alpha.2",
                "assets": [{"name": self.url.rsplit("/", 1)[-1], "browser_download_url": self.url}],
            }
        ]
        self.calls = []
        self.hold = None
        self.entered = threading.Event()

    @contextmanager
    def open(self, url, cancel=None):
        self.calls.append(url)
        raw = {
            OFFICIAL_RELEASES: json.dumps(self.index).encode(),
            self.url: envelope(self.signed),
            self.signed["artifact"]["url"]: self.artifact,
        }[url]
        hold = self.hold
        entered = self.entered
        headers = Message()
        headers["Content-Length"] = str(len(raw))
        stream = io.BytesIO(raw)

        class Response:
            def read(self, maximum):
                entered.set()
                if hold is not None:
                    assert hold.wait(5), "fixture release missing"
                return stream.read(maximum)

        response = Response()
        response.headers = headers
        try:
            yield response
        finally:
            stream.close()


def controller(tmp_path, fixture, **overrides):
    return UpdateController(
        root=tmp_path / ".application-updates",
        current=Version.parse("0.3.0-alpha.1"),
        target=overrides.get("target", "windows-x86_64"),
        distribution="portable",
        transport=fixture,
        verify=accept_stub,
    )


def finished(update):
    update._thread.join(5)
    assert not update._thread.is_alive()
    return update.state()


def available(update):
    initial = update.check()
    result = finished(update)
    assert result["phase"] == "available", result
    return initial["operation_id"]


def test_explicit_check_download_defer_and_restart_never_apply_or_replay(tmp_path):
    fixture = ReleaseFixture()
    update = controller(tmp_path, fixture)
    assert update.state()["phase"] == "idle" and fixture.calls == []
    identity = available(update)
    assert len(fixture.calls) == 2
    assert update.state()["notes"] == fixture.signed["notes"]
    update.download(identity)
    state = finished(update)
    assert state["phase"] == "verified"
    assert state["received"] == state["total"] == len(fixture.artifact)
    update.defer(identity)
    calls = list(fixture.calls)
    reopened = controller(tmp_path, fixture)
    assert reopened.state()["phase"] == "deferred"
    assert fixture.calls == calls
    assert (update.store.stage(identity) / "artifact.verified").read_bytes() == fixture.artifact


def test_cancel_keeps_worker_reserved_until_read_finishes_and_rejects_stale_operation(tmp_path):
    fixture = ReleaseFixture()
    update = controller(tmp_path, fixture)
    identity = available(update)
    fixture.hold = threading.Event()
    fixture.entered.clear()
    update.download(identity)
    assert fixture.entered.wait(5)
    update.cancel(identity)
    with pytest.raises(UpdateError) as busy:
        update.check()
    assert busy.value.code == "update_busy"
    fixture.hold.set()
    assert finished(update)["phase"] == "cancelled"
    assert not (update.store.stage(identity) / "artifact.verified").exists()
    fixture.hold = None
    available(update)
    with pytest.raises(UpdateError) as stale:
        update.download(identity)
    assert stale.value.code == "stale_operation"


@pytest.mark.parametrize(
    "phase", ["checking", "downloading", "closing", "applying", "awaiting_restart"]
)
def test_interrupted_receipt_has_no_automatic_io_or_retry(tmp_path, phase):
    store = UpdateStore(tmp_path / ".application-updates")
    identity, _ = store.create()
    store.save(identity, phase, "0.3.0-alpha.2")
    fixture = ReleaseFixture()
    reopened = controller(tmp_path, fixture)
    expected = "interrupted" if phase in {"checking", "downloading"} else "recovery_required"
    assert reopened.state()["phase"] == expected
    assert fixture.calls == []
    if expected == "recovery_required":
        with pytest.raises(UpdateError) as blocked:
            reopened.check()
        assert blocked.value.code == "recovery_required"


def test_reopen_reverifies_target_instead_of_trusting_stored_verified_phase(tmp_path):
    fixture = ReleaseFixture()
    update = controller(tmp_path, fixture)
    identity = available(update)
    update.download(identity)
    finished(update)
    reopened = controller(tmp_path, fixture, target="windows-aarch64")
    assert reopened.state()["phase"] == "error"
    assert reopened.state()["error_code"] == "wrong_target"


@pytest.mark.parametrize("failure", ["missing", "duplicate", "wrong_url", "unsigned", "version"])
def test_newest_invalid_manifest_is_a_clear_failure_without_fallback(tmp_path, failure):
    fixture = ReleaseFixture()
    if failure == "missing":
        fixture.index[0]["assets"] = []
    elif failure == "duplicate":
        fixture.index.append(fixture.index[0])
    elif failure == "wrong_url":
        fixture.index[0]["assets"][0]["browser_download_url"] = "https://example.com/release"
    elif failure == "unsigned":
        fixture.signed["owner_version"] = "other"
    else:
        fixture.signed["version"] = "0.3.0-alpha.3"
        fixture.signed["owner_version"] = "0.3.0-alpha.3"
        fixture.signed["artifact"]["url"] = fixture.signed["artifact"]["url"].replace(
            "alpha.2", "alpha.3"
        )
    update = controller(tmp_path, fixture)
    update.check()
    assert finished(update)["phase"] == "error"
    assert not any("-portable.zip" in url for url in fixture.calls)


def test_journal_contains_only_safe_update_metadata(tmp_path):
    fixture = ReleaseFixture()
    update = controller(tmp_path, fixture)
    available(update)
    row = json.loads((update.store.root / "current.json").read_bytes())
    assert set(row) == {"format_version", "operation_id", "phase", "version", "error_code"}
    assert row["version"] == fixture.signed["version"]


def test_repeated_current_checks_do_not_exhaust_retention_limit_or_lose_recovery(tmp_path):
    fixture = ReleaseFixture()
    update = controller(tmp_path, fixture)
    retained, stage = update.store.create()
    (stage / "previous-binary.fixture").write_bytes(b"retained recovery bytes")
    update.store.save(retained, "recovery_required", "0.3.0-alpha.2")
    fixture.index = []
    # Use the existing live controller; the separately retained operation is never touched.
    for _ in range(100):
        update.check()
        assert finished(update)["phase"] == "current"
    assert {path.name for path in update.store.root.iterdir()} == {"current.json", retained}
    assert (stage / "previous-binary.fixture").read_bytes() == b"retained recovery bytes"
    reopened = controller(tmp_path, fixture)
    assert reopened.state()["phase"] == "current"
    assert len(fixture.calls) == 100


def test_empty_failed_checks_can_retry_beyond_cap_but_download_partials_are_preserved(tmp_path):
    fixture = ReleaseFixture()
    fixture.index[0]["assets"] = []
    update = controller(tmp_path, fixture)
    for _ in range(70):
        update.check()
        assert finished(update)["phase"] == "error"
    assert [path.name for path in update.store.root.iterdir()] == ["current.json"]
    identity, stage = update.store.create()
    (stage / "artifact.partial").write_bytes(b"never execute this retained partial")
    update.store.save(identity, "cancelled", None)
    update.store.prune_empty_terminal(identity)
    assert (stage / "artifact.partial").read_bytes() == b"never execute this retained partial"
    update.store.save(identity, "recovery_required", "0.3.0-alpha.2")
    update.store.prune_empty_terminal(identity)
    assert stage.exists()
    with pytest.raises(UpdateError) as refused:
        controller(tmp_path, fixture).check()
    assert refused.value.code == "recovery_required"
