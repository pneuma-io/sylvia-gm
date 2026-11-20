"""The real owner keeps the Stop and resume contract fixtures the browser journeys rely on.

tests/stop_resume/contract.cjs holds fixtures that the scripted owner of the browser journeys also
passes. Here they run against the production owner through the WebUI command adapter, with an
offline scripted GM (tests/stop_resume_owner_fixture.py). No real provider or network is used.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

from aiohttp.test_utils import TestServer

from sylvia_gm.interfaces.webui import LocalWebUI
from tests.stop_resume_owner_fixture import journey_owner, start_no_ruleset_campaign

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "tests/stop_resume/contract.cjs"


def _fixture_campaigns() -> list[str]:
    text = CONTRACT.read_text(encoding="utf-8")
    names = re.search(r"const CAMPAIGNS = \[([^\]]*)\]", text)
    assert names, "contract.cjs names its campaigns in CAMPAIGNS"
    return re.findall(r"'([^']+)'", names.group(1))


def test_real_owner_keeps_the_stop_and_resume_contract_fixtures(tmp_path: Path) -> None:
    async def check() -> None:
        async with journey_owner(tmp_path) as owner:
            for name in _fixture_campaigns():
                await start_no_ruleset_campaign(owner.call, name)
            ui = LocalWebUI(owner, ROOT / "src-tauri/frontend", "http://127.0.0.1:0")
            async with TestServer(ui.app, host="127.0.0.1") as server:
                url = str(server.make_url("/")).rstrip("/")
                ui.origin = url
                ui.authority = url.removeprefix("http://")
                process = await asyncio.create_subprocess_exec(
                    "node", "--test", "tests/stop_resume_owner_contract.cjs",
                    cwd=ROOT, env={**os.environ, "SYLVIA_TEST_OWNER_URL": url},
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                )
                assert process.stdout is not None
                lines: list[str] = []

                async def collect() -> None:
                    async for line in process.stdout:
                        lines.append(line.decode(errors="replace"))

                reader = asyncio.create_task(collect())
                try:
                    await asyncio.wait_for(process.wait(), 180)
                finally:
                    if process.returncode is None:
                        process.kill()
                        await process.wait()
                    await reader
                    # Printed even on a timeout, so a stalled fixture shows its last command.
                    print("".join(lines))
                assert process.returncode == 0

    asyncio.run(check())
