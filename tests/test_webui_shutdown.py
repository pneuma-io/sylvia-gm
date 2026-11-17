"""HTTP Stop must report actual clean collection, not just the owner's acknowledgement."""

import asyncio
import tempfile
from pathlib import Path

import pytest

from sylvia_gm.interfaces import webui
from tests.test_webui import Browser


class Child:
    """A deterministic process stand-in separating acknowledgement, exit and forced collection."""

    def __init__(self, code, already_exited=False):
        self.returncode = code if already_exited else None
        self.code = code
        self.terminated = False
        self.release = asyncio.Event()

    async def wait(self):
        if self.code is None and not self.terminated:
            await self.release.wait()
        await asyncio.sleep(0)
        self.returncode = self.code if self.code is not None else 0
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.release.set()


@pytest.mark.parametrize(
    ("code", "already_exited"), [(0, False), (23, False), (23, True), (None, False)],
    ids=["clean", "acknowledged-exit-23", "already-exited-23", "acknowledged-stuck"],
)
def test_stop_requires_clean_exit_and_replays_the_same_shutdown_result(tmp_path, monkeypatch, code, already_exited):
    monkeypatch.setattr(webui, "OWNER_EXIT_TIMEOUT", 0.05, raising=False)
    retained = tmp_path / "retained-synthetic-data.txt"
    retained.write_text("keep this synthetic data")

    async def check():
        child = Child(code, already_exited)
        owner = webui.OwnerProcess(tmp_path)
        owner.process = child
        owner.reader = asyncio.create_task(asyncio.Event().wait())
        reader = owner.reader
        calls = []

        async def acknowledge(method, params=None):
            calls.append(method)
            assert method == "shutdown"
            return {"status": "closed"}

        owner.call = acknowledge
        browser = await Browser().open(owner)
        try:
            response = await browser.client.post(browser.url + "/api/stop", headers=browser.headers)
            result = await response.json()
            clean = code == 0
            assert response.status == (200 if clean else 400), result
            if clean:
                assert result == {"ok": True, "result": {"status": "closed"}}
            else:
                assert result["ok"] is False
                assert result["error"]["code"] == "owner_close_failed"
                assert "Saved work is retained" in result["error"]["message"]
                assert ("exit 23" if code == 23 else "timed out") in result["error"]["message"]
            repeated = await browser.client.post(browser.url + "/api/stop", headers=browser.headers)
            assert repeated.status == response.status
            assert await repeated.json() == result
            assert calls == ([] if already_exited else ["shutdown"]), "Stop retries observe the original close"
            assert child.terminated is (code is None)
            assert child.returncode == (0 if code is None else code)
            assert owner.process is None, "the collected child handle is released"
            assert reader.done(), "the reply reader is collected"
            await asyncio.sleep(0.12)
            assert browser.ui.stopped.is_set() is clean, "failed close cannot announce successful Stop"
            assert retained.read_text() == "keep this synthetic data"
        finally:
            await browser.close()
        assert browser.server.closed

    asyncio.run(check())


def test_cleanup_releases_receipts_and_downloads_even_when_owner_exit_is_abnormal(tmp_path):
    async def check():
        owner = webui.OwnerProcess(tmp_path)
        owner.process = Child(23, already_exited=True)
        ui = webui.LocalWebUI(owner, Path(__file__).resolve().parents[1] / "src-tauri/frontend", "http://127.0.0.1:1")
        pending = asyncio.create_task(asyncio.Event().wait())
        ui.requests["retained-request"] = ("same-payload", pending)
        ui.download_directory = tempfile.TemporaryDirectory(dir=tmp_path)
        download_root = Path(ui.download_directory.name)
        ui.downloads["selected-export"] = (download_root / "export.bin", "export.bin")
        with pytest.raises(webui.WebUIError, match="exit 23"):
            await ui.cleanup(ui.app)
        assert owner.process is None
        assert pending.cancelled()
        assert ui.downloads == {}
        assert ui.download_directory is None
        assert not download_root.exists()

    asyncio.run(check())
