"""Selected phase-one shell against the release's actual SRD Lite package and real owner."""

import asyncio
import hashlib
import json
import os
from pathlib import Path

import pytest
from aiohttp.test_utils import TestServer

from sylvia_gm.application.bundled_packages import bundled_srd_archive
from sylvia_gm.config.provider_settings import save_provider_settings, set_default_model_settings
from sylvia_gm.domain.authority import WriterRole
from sylvia_gm.interfaces.webui import LocalWebUI, OwnerProcess, RemoteAccess

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://sylvia.example.ts.net"
KEY = "synthetic-private-route-key-for-tests-only-0123456789"


def seed(root):
    """Create only invocation-owned synthetic data; the configured endpoint cannot spend money."""
    root.mkdir(parents=True, exist_ok=True)
    archive = root.parent / 'actual-srd-lite.sylvia-gm-package'
    archive.write_bytes(bundled_srd_archive())
    os.environ['SRD_LITE_CANDIDATE'] = str(archive)
    from tests.test_lite_panel_combat import case
    model = save_provider_settings(
        root / "config.yaml", provider_id="custom", preset_id="custom",
        display_name="Synthetic offline test", base_url="http://127.0.0.1:9/v1",
        credential="", model_id="synthetic/weekend", model_name="Synthetic offline test",
        context_window=32000, max_output_tokens=4096,
    )
    set_default_model_settings(root / "config.yaml", provider_id=model["provider_id"], model_id=model["canonical_model_id"])
    value = case(root / "authority.sqlite3")
    try:
        value.owner.set_admin_full_audit(True)
        value.owner.create_agent_session(campaign_id="lite", session_id="lite-gm", role=WriterRole.DM,
                                         provider_id=model["provider_id"], model_id=model["canonical_model_id"])
        value.owner.create_builder_chat(provider_id=model["provider_id"], model_id=model["canonical_model_id"],
                                        display_name="Synthetic Builder", session_id="builder-synth")
        assert value.owner.campaign_panels("lite")["player_control"]["actors"]
    finally:
        value.close()
    return {"package_sha256": hashlib.sha256(bundled_srd_archive()).hexdigest(), **model}


@pytest.mark.parametrize("private", [False, True], ids=["localhost", "simulated-private-https"])
def test_actual_lite_phase_one_shell(tmp_path, monkeypatch, private):
    monkeypatch.setenv("SYLVIA_GM_TEST_FULL_AUDIT", "1")
    identity = seed(tmp_path / "data")

    async def check():
        owner = OwnerProcess(tmp_path / "data")
        await owner.start()
        try:
            remote = None
            if private:
                key_file = tmp_path / "selected-synthetic-key.txt"
                key_file.write_text(KEY)
                remote = RemoteAccess.from_file(ORIGIN, key_file)
            ui = LocalWebUI(owner, ROOT / "src-tauri/frontend", ORIGIN if private else "http://127.0.0.1:1", remote=remote)
            async with TestServer(ui.app, host="127.0.0.1") as server:
                backend = str(server.make_url("/")).rstrip("/")
                if not private:
                    ui.origin = backend
                    ui.authority = backend.removeprefix("http://")
                process = await asyncio.create_subprocess_exec(
                    "node", "--test", "tests/weekend_candidate_lite.cjs",
                    env={**os.environ, "SYLVIA_TEST_WEBUI_BACKEND": backend,
                         "SYLVIA_TEST_WEBUI_ORIGIN": ORIGIN if private else backend,
                         "SYLVIA_TEST_WEBUI_PRIVATE": str(int(private))},
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                )
                output, _ = await process.communicate()
                print(output.decode(errors="replace"))
                assert process.returncode == 0
                assert (await owner.call("admin_full_audit_status", {}))["enabled"]
        finally:
            await owner.close()

    print(json.dumps({"private": private, "source": str(ROOT), "synthetic_seed": identity}))
    asyncio.run(check())
