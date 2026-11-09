"""Real multipart HTTP checks without an executing game owner or user data."""

import asyncio
from pathlib import Path

import aiohttp
import pytest
from aiohttp.multipart import BodyPartReader

from sylvia_gm.interfaces import webui
from tests.test_webui import Browser


class SelectedOwner:
    def __init__(self, root):
        self.data_root = root
        self.calls = []
        self.closed = False

    async def call(self, method, params):
        self.calls.append((method, params))
        assert method == "ingest_builder_sources"
        assert [Path(p).read_bytes() for p in params["source_paths"]] == [b"selected bytes"]
        return {"source_count": 1}

    async def close(self):
        self.closed = True


@pytest.mark.parametrize("file_first", [False, True])
def test_oversized_params_stop_streaming_at_the_field_bound(tmp_path, monkeypatch, file_first):
    original = BodyPartReader.read_chunk
    returned = []

    async def measured(part, size=BodyPartReader.chunk_size):
        value = await original(part, size)
        if part.name == "params":
            returned.append(len(value))
        return value

    monkeypatch.setattr(BodyPartReader, "read_chunk", measured)

    async def check():
        owner = SelectedOwner(tmp_path)
        browser = await Browser().open(owner)
        try:
            form = aiohttp.FormData()
            if file_first:
                form.add_field("file", b"selected bytes", filename="selected.txt")
            form.add_field("params", '{"padding":"' + "x" * (2 * 1024 * 1024) + '"}')
            if not file_first:
                form.add_field("file", b"selected bytes", filename="selected.txt")
            response = await browser.client.post(
                browser.url + "/api/upload/builder", headers=browser.headers, data=form
            )
            assert response.status == 413
            await response.read()
            assert sum(returned) <= webui.MAX_UPLOAD_PARAMS_BYTES + 1
            assert owner.calls == []
            assert not list(tmp_path.glob(".webui-upload-*"))
        finally:
            await browser.close()
        assert browser.server.closed and owner.closed

    asyncio.run(check())


def test_valid_limit_sized_params_upload_and_cleanup(tmp_path):
    async def check():
        owner = SelectedOwner(tmp_path)
        browser = await Browser().open(owner)
        try:
            params = '{"sessionId":"chosen","ingestionId":"chosen"}'
            form = aiohttp.FormData()
            form.add_field("params", params + " " * (webui.MAX_UPLOAD_PARAMS_BYTES - len(params)))
            form.add_field("file", b"selected bytes", filename="selected.txt")
            response = await browser.client.post(
                browser.url + "/api/upload/builder", headers=browser.headers, data=form
            )
            assert response.status == 200
            assert (await response.json())["result"] == {"source_count": 1}
            assert len(owner.calls) == 1
            assert owner.calls[0][1]["session_id"] == "chosen"
            assert not list(tmp_path.glob(".webui-upload-*"))
        finally:
            await browser.close()
        assert owner.closed and browser.server.closed

    asyncio.run(check())


def test_params_after_a_file_still_count_toward_aggregate_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, "MAX_UPLOAD_BYTES", 64)

    async def check():
        owner = SelectedOwner(tmp_path)
        browser = await Browser().open(owner)
        try:
            form = aiohttp.FormData()
            form.add_field("file", b"selected bytes", filename="selected.txt")
            form.add_field("params", '{"padding":"' + "x" * 64 + '"}')
            response = await browser.client.post(
                browser.url + "/api/upload/builder", headers=browser.headers, data=form
            )
            assert response.status == 413
            await response.read()
            assert owner.calls == []
            assert not list(tmp_path.glob(".webui-upload-*"))
        finally:
            await browser.close()
        assert owner.closed and browser.server.closed

    asyncio.run(check())


def test_cancelled_http_handler_keeps_uploaded_bytes_until_owner_finishes(tmp_path, monkeypatch):
    original = webui.LocalWebUI.upload
    handler_tasks = []

    async def observed_handler(ui, request):
        handler_tasks.append(asyncio.current_task())
        return await original(ui, request)

    monkeypatch.setattr(webui.LocalWebUI, "upload", observed_handler)

    async def check():
        entered = asyncio.Event()
        release = asyncio.Event()

        class HeldOwner(SelectedOwner):
            async def call(self, method, params):
                self.paths = [Path(path) for path in params["source_paths"]]
                entered.set()
                await release.wait()
                result = await super().call(method, params)
                self.read_completed = True
                return result

        owner = HeldOwner(tmp_path)
        browser = await Browser().open(owner)
        form = aiohttp.FormData()
        form.add_field("params", '{"sessionId":"chosen","ingestionId":"chosen"}')
        form.add_field("file", b"selected bytes", filename="selected.txt")
        request = asyncio.create_task(browser.client.post(
            browser.url + "/api/upload/builder", headers=browser.headers, data=form
        ))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            handler_tasks[0].cancel()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert all(path.exists() for path in owner.paths)
            handler_tasks[0].cancel()
            await asyncio.sleep(0)
            assert all(path.exists() for path in owner.paths)
            release.set()
            await asyncio.gather(request, return_exceptions=True)
            assert owner.read_completed
            assert len(owner.calls) == 1
            assert not list(tmp_path.glob(".webui-upload-*"))
        finally:
            release.set()
            await asyncio.gather(request, return_exceptions=True)
            await browser.close()
        assert owner.closed and browser.server.closed

    asyncio.run(check())
