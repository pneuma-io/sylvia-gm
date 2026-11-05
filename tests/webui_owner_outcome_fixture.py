"""Derive HTTP receipts through the real owner transport with a controlled reply pipe.

The synthetic pipe may apply a save before EOF or discard it. No provider is used.
The JSON entry point lets browser tests consume actual guarded host envelopes.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from sylvia_gm.interfaces.webui import OwnerProcess
from tests.test_webui import Browser


async def derive_outcome(committed):
    owner = OwnerProcess(Path.cwd())
    stdout = asyncio.StreamReader()
    saved = []
    writes = []

    class Pipe:
        def write(self, raw):
            request = json.loads(raw)
            writes.append(request)
            if committed:
                saved.append(request["params"])
            stdout.feed_eof()

        async def drain(self):
            await asyncio.sleep(0)

    owner.process = SimpleNamespace(stdin=Pipe(), stdout=stdout)
    owner.reader = asyncio.create_task(owner._receive())
    browser = await Browser().open(owner)
    try:
        params = {"model_id": "synthetic/model"}
        response = await browser.raw("save_provider_settings", params, "one-save")
        envelope = await response.json()
        repeated = await browser.raw("save_provider_settings", params, "one-save")
        assert await repeated.json() == envelope
        conflict = await browser.raw("save_provider_settings", {}, "one-save")
        assert (await conflict.json())["error"]["code"] == "idempotency_conflict"
        assert len(writes) == 1
        assert bool(saved) is committed
        return {"status": response.status, "envelope": envelope,
                "committed": bool(saved), "save_calls": len(writes)}
    finally:
        await owner.reader
        # The test owns an in-memory pipe, with no OS child to collect.
        owner.process = None
        await browser.close()


async def envelopes():
    return {str(committed).lower(): await derive_outcome(committed)
            for committed in (False, True)}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(envelopes())))
