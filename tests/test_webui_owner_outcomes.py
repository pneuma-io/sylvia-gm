"""Dispatch uncertainty must remain distinct from requests never sent or refused."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sylvia_gm.interfaces.webui import OwnerProcess, WebUIError
from sylvia_gm.interfaces import webui
from tests.webui_owner_outcome_fixture import derive_outcome


@pytest.mark.parametrize("committed", [False, True])
def test_dispatched_save_eof_is_unknown_in_real_http_receipt(committed):
    result = asyncio.run(derive_outcome(committed))
    assert result["status"] == 400
    assert result["envelope"]["error"]["code"] == "outcome_unknown"
    assert result["save_calls"] == 1
    assert result["committed"] is committed


def test_waiting_request_is_not_dispatched_after_reader_disconnects():
    async def check():
        owner = OwnerProcess(Path.cwd())
        stdout = asyncio.StreamReader()
        owner.reader = asyncio.create_task(owner._receive())
        writes = []
        async def drain():
            pass
        owner.process = SimpleNamespace(
            stdin=SimpleNamespace(write=writes.append, drain=drain), stdout=stdout)
        await owner.write_lock.acquire()
        call = asyncio.create_task(owner.call("save_provider_settings"))
        await asyncio.sleep(0)
        stdout.feed_eof()
        await owner.reader
        owner.write_lock.release()
        with pytest.raises(WebUIError) as error:
            await call
        assert error.value.code == "webui_unavailable"
        assert writes == []
        assert owner.pending == {}
    asyncio.run(check())


def test_drain_failure_after_write_is_unknown_and_pending_future_is_collected():
    async def check():
        owner = OwnerProcess(Path.cwd())
        owner.reader = asyncio.create_task(asyncio.Event().wait())
        writes = []
        class Pipe:
            def write(self, raw):
                writes.append(raw)
            async def drain(self):
                raise BrokenPipeError("synthetic failed drain")
        owner.process = SimpleNamespace(stdin=Pipe())
        try:
            with pytest.raises(WebUIError) as error:
                await owner.call("save_provider_settings")
            assert error.value.code == "outcome_unknown"
            assert len(writes) == 1
            assert owner.pending == {}
        finally:
            owner.reader.cancel()
            await asyncio.gather(owner.reader, return_exceptions=True)
    asyncio.run(check())


@pytest.mark.parametrize("stopped", [False, True])
def test_pre_dispatch_refusals_are_definite_without_pipe_writes(monkeypatch, stopped):
    monkeypatch.setattr(webui, "MAX_BYTES", 256)
    async def check():
        owner = OwnerProcess(Path.cwd())
        writes = []
        owner.process = SimpleNamespace(stdin=SimpleNamespace(write=writes.append))
        if not stopped:
            owner.reader = asyncio.create_task(asyncio.Event().wait())
        try:
            with pytest.raises(WebUIError) as error:
                await owner.call("save_provider_settings", {"model_id": "x" * 512})
            assert error.value.code == ("webui_unavailable" if stopped else "request_too_large")
            assert writes == []
            assert owner.pending == {}
        finally:
            if owner.reader:
                owner.reader.cancel()
                await asyncio.gather(owner.reader, return_exceptions=True)
    asyncio.run(check())


def test_explicit_owner_refusal_keeps_the_definite_category():
    async def check():
        owner = OwnerProcess(Path.cwd())
        stdout = asyncio.StreamReader()
        writes = []
        class Pipe:
            def write(self, raw):
                request = json.loads(raw)
                writes.append(request)
                stdout.feed_data(json.dumps({"id": request["id"], "ok": False,
                    "error": {"code": "command_rejected", "message":
                        "Could not protect and save provider settings; the previous file is unchanged"}}).encode() + b"\n")
            async def drain(self):
                await asyncio.sleep(0)
        owner.process = SimpleNamespace(stdin=Pipe(), stdout=stdout)
        owner.reader = asyncio.create_task(owner._receive())
        try:
            with pytest.raises(WebUIError, match="previous file is unchanged") as error:
                await owner.call("save_provider_settings")
            assert error.value.code == "command_rejected"
            assert len(writes) == 1
            assert owner.pending == {}
        finally:
            owner.reader.cancel()
            await asyncio.gather(owner.reader, return_exceptions=True)
    asyncio.run(check())
