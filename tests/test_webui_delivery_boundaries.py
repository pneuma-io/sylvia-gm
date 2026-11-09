"""Focused regressions for ownership and artifact defects found during delivery review."""

import asyncio
import json

import pytest

from sylvia_gm.interfaces.webui import OwnerProcess
from tests.test_webui import Browser


def test_browser_snapshot_exposes_metadata_without_raw_game_state(tmp_path):
    private = {
        "campaign_id": "synthetic", "display_name": "Synthetic", "lifecycle": "active",
        "writer": "dm", "revision": 7, "package_lock": {"entries": []},
        "state": {"example.world": {"hidden_node": "private-awareness"}},
        "participants": [{"data": {"secret": "private-awareness"}}],
        "actions": [{"parameters": {"node": "hidden_node"}}],
        "events": [{"safe_payload": {"parameters": {"node": "hidden_node"}}}],
        "turns": [{"private": "private-awareness"}],
    }
    original = json.dumps(private, sort_keys=True)

    class SnapshotOwner:
        data_root = tmp_path

        async def call(self, method, params):
            assert method == "display_snapshot" and params == {"campaign_id": "synthetic"}
            return private

        async def close(self):
            pass

    async def check():
        browser = await Browser().open(SnapshotOwner())
        try:
            result = await browser.call("display_snapshot", {"campaignId": "synthetic"})
            assert result == {key: private[key] for key in (
                "campaign_id", "display_name", "lifecycle", "writer", "revision", "package_lock",
            )}
            assert "hidden_node" not in json.dumps(result)
            assert "private-awareness" not in json.dumps(result)
            assert json.dumps(private, sort_keys=True) == original
        finally:
            await browser.close()
        assert browser.server.closed

    asyncio.run(check())


def test_cancelled_launch_reaps_the_spawned_child(tmp_path, monkeypatch):
    original = asyncio.create_subprocess_exec
    children = []

    async def launch(*args, **kwargs):
        # This child waits for EOF without completing the owner handshake.
        child = await original(
            args[0],
            "-B",
            "-c",
            "import sys; sys.stdin.readline(); list(sys.stdin)",
            **kwargs,
        )
        children.append(child)
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)

    async def check():
        owner = OwnerProcess(tmp_path)
        starting = asyncio.create_task(owner.start())
        try:
            while owner.process is None:
                await asyncio.sleep(0.01)
            starting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await starting
            assert children[0].returncode is not None, "Cancelled startup left a live child"
            assert owner.process is None
        finally:
            await owner.close()
            print(
                json.dumps(
                    {
                        "cancelled_launch_child_pid": children[0].pid,
                        "exit_code_after_cleanup": children[0].returncode,
                    }
                )
            )

    asyncio.run(check())


def test_download_handles_keep_the_exact_export_bytes(tmp_path):
    class ExportOwner:
        data_root = tmp_path
        count = 0

        async def call(self, method, params):
            assert method == "export_campaign_file"
            self.count += 1
            target = self.data_root / "exports" / (params["export_id"] + ".sylvia-gm-campaign")
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(f"selected-export-{self.count}".encode())
            return {"export": {"ordinal": self.count}, "artifact_path": str(target)}

        async def close(self):
            pass

    async def check():
        browser = await Browser().open(ExportOwner())
        try:
            first = await browser.call("export_campaign_file", {"exportId": "same-file"})
            second = await browser.call("export_campaign_file", {"exportId": "same-file"})
            async with browser.client.get(browser.url + first["download"]) as response:
                assert await response.read() == b"selected-export-1"
            async with browser.client.get(browser.url + second["download"]) as response:
                assert await response.read() == b"selected-export-2"
        finally:
            await browser.close()
        assert browser.server.closed
        assert browser.ui.download_directory is None
        assert not list(tmp_path.glob(".webui-download-*"))
        print(
            json.dumps({"released_http_server": browser.url, "download_snapshots_released": True})
        )

    asyncio.run(check())
