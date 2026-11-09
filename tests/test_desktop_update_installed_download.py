"""NSIS retention uses a labelled signature verifier stub and inert transfer bytes, no network."""

import json

import pytest

from sylvia_gm.application.desktop_update.controller import UpdateController
from sylvia_gm.application.desktop_update.manifest import OFFICIAL_RELEASES, Version
from tests.test_desktop_update_installed import release
from tests.test_desktop_update_lifecycle import available, finished
from tests.test_desktop_update_manifest import accept_stub, envelope
from tests.test_desktop_update_transport import FixtureTransport


def fixture(tmp_path):
    current = Version.parse("0.3.0-alpha.1")
    signed, manifest, artifact, _ = release("0.3.0-alpha.2", "new")
    old_signed, previous, old_artifact, _ = release(current.text, "old")

    def manifest_url(version):
        return (
            f"https://github.com/pneuma-io/sylvia-gm/releases/download/v{version}/"
            + f"sylvia-gm-{version}-windows-x86_64-installed-update.json"
        )

    url, previous_url = manifest_url(manifest.version.text), manifest_url(current.text)

    class Transport:
        def __init__(self):
            self.calls, self.roles = [], []
            self.responses = {
                OFFICIAL_RELEASES: json.dumps(
                    [
                        {
                            "draft": False,
                            "prerelease": True,
                            "tag_name": "v" + manifest.version.text,
                            "assets": [
                                {"name": url.rsplit("/", 1)[-1], "browser_download_url": url}
                            ],
                        }
                    ]
                ).encode(),
                url: envelope(signed),
                previous_url: envelope(old_signed),
                manifest.artifact_url: artifact,
                previous.artifact_url: old_artifact,
            }

        def open(self, address, cancel=None):
            self.calls.append(address)
            self.roles.append(update.state()["transfer_kind"])
            raw = self.responses[address]
            return FixtureTransport(raw, length=len(raw)).open(address, cancel)

    transport = Transport()
    update = UpdateController(
        root=tmp_path / "updates",
        current=current,
        target="windows-x86_64",
        distribution="installed",
        transport=transport,
        verify=accept_stub,
    )
    return update, transport, manifest, previous, previous_url, old_signed


def test_explicit_download_retains_verified_current_installer_before_successor(tmp_path):
    update, transport, manifest, previous, previous_url, _ = fixture(tmp_path)
    identity = available(update)
    assert len(transport.calls) == 2
    assert previous_url not in transport.calls
    update.download(identity)
    assert finished(update)["phase"] == "verified"
    assert transport.calls[-3:] == [previous_url, previous.artifact_url, manifest.artifact_url]
    assert transport.roles[-3:] == ["previous_installer", "previous_installer", "update"]
    stage = update.store.stage(identity)
    assert (stage / "previous-installer/artifact.verified").read_bytes() == transport.responses[
        previous.artifact_url
    ]
    calls = list(transport.calls)
    reopened = UpdateController(
        root=update.store.root,
        current=update.current,
        target=update.target,
        distribution="installed",
        transport=transport,
        verify=accept_stub,
    )
    assert reopened.state()["phase"] == "verified" and reopened.state()["downloaded"]
    assert transport.calls == calls


@pytest.mark.parametrize("failure", ["wrong_recovery_version", "tampered_previous_artifact"])
def test_unusable_recovery_never_downloads_successor_or_becomes_apply_ready(tmp_path, failure):
    update, transport, manifest, previous, previous_url, old_signed = fixture(tmp_path)
    identity = available(update)
    if failure == "wrong_recovery_version":
        old_signed.update(version="0.3.0-alpha.0", owner_version="0.3.0-alpha.0")
        transport.responses[previous_url] = envelope(old_signed)
    else:
        transport.responses[previous.artifact_url] = b"tampered previous installer"
    update.download(identity)
    state = finished(update)
    assert state["phase"] == "error" and not state["downloaded"]
    assert manifest.artifact_url not in transport.calls
    assert not (update.store.stage(identity) / "artifact.verified").exists()


def test_reopen_reverifies_retained_current_installer_before_downloaded_state(tmp_path):
    update, transport, _, _, _, _ = fixture(tmp_path)
    identity = available(update)
    update.download(identity)
    assert finished(update)["phase"] == "verified"
    artifact = update.store.stage(identity) / "previous-installer/artifact.verified"
    artifact.write_bytes(b"changed recovery")
    calls = list(transport.calls)
    reopened = UpdateController(
        root=update.store.root,
        current=update.current,
        target=update.target,
        distribution="installed",
        transport=transport,
        verify=accept_stub,
    )
    assert reopened.state()["phase"] == "error"
    assert reopened.state()["error_code"] == "artifact_mismatch"
    assert transport.calls == calls and artifact.read_bytes() == b"changed recovery"
