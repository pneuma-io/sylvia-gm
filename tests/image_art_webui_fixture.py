"""Disposable source WebUI with real owner/dispatch and a scripted image adapter.

Full Audit is enabled before actions. No provider authentication or native UI is used.
"""

import argparse
import asyncio
import io
import json
import os
from pathlib import Path

from aiohttp import web

from sylvia_gm.agent._session_loop import CampaignSessionLoop
from sylvia_gm.application.authority import CampaignCreate
from sylvia_gm.application.desktop_update.drafts import DraftStore
from sylvia_gm.application.image_art import ImageArt
from sylvia_gm.domain.authority import StateValue, WriterRole
from sylvia_gm.infrastructure.launch_handshake import derive_owner_response
from sylvia_gm.interfaces.local_api import LocalCommandBus
from sylvia_gm.interfaces.local_owner import LocalOwnerSupervisor
from sylvia_gm.interfaces.owner_process import _dispatch, _json_value
from sylvia_gm.interfaces.webui import LocalWebUI, WebUIError
from tests.test_image_art import Images
from tests.test_tactical_grid_ui import _installed_archive, _scene


class Composition:
    def __init__(self, root):
        self.data_root = root
        self.closed = False
        self.supervisor = LocalOwnerSupervisor()
        credential = self.supervisor.acquire_owner(root / "authority.sqlite3", "image-art-webui")
        self.supervisor.complete_handshake(derive_owner_response(credential.secret_hex))
        self.owner = self.supervisor.application_owner()
        self.owner.set_admin_full_audit(True)
        self.adapter = Images()
        from sylvia_gm.config.image_settings import ImageSettings

        self.owner._image_art = ImageArt(
            self.owner, adapters=(self.adapter,), settings=ImageSettings(root).read
        )
        candidate = self.owner.install_package_archive(
            io.BytesIO(_installed_archive()), install_id="image-ui-package"
        )
        self.owner.create_campaign(
            CampaignCreate(
                campaign_id="image-ui",
                display_name="Image art test",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id="seed-image-ui",
                initial_state=(
                    StateValue(
                        "synthetic.coordinate",
                        "state",
                        {
                            "spatial_scene": _scene(),
                            "objectives": [],
                            "selection": {},
                            "preview": None,
                            "last_actor": None,
                            "last_destination": None,
                        },
                    ),
                ),
            )
        )
        self.owner.complete_turn("image-ui", "seed-image-ui")
        self.owner.create_agent_session(
            campaign_id="image-ui",
            role=WriterRole.DM,
            provider_id="test",
            model_id="test",
            session_id="image-gm",
        )
        self.owner.create_builder_chat(
            provider_id="test", model_id="test", session_id="image-builder"
        )
        self.bus = LocalCommandBus(self.supervisor)
        self.drafts = DraftStore(root)
        self.loop = CampaignSessionLoop(self.owner)

    async def call(self, method, params=None):
        def dispatch():
            try:
                if method == "load_desktop_drafts":
                    return self.drafts.load()
                if method == "save_desktop_drafts":
                    return self.drafts.save(params["drafts"])
                return _json_value(
                    _dispatch(
                        self.bus,
                        method,
                        params or {},
                        application_owner=self.owner,
                        owner_id="image-art-webui",
                        provider_resolver=self.no_provider,
                        model_choices=lambda: (),
                        session_loop=self.loop,
                        storage_root=self.data_root,
                    )
                )
            except Exception as error:
                raise WebUIError(str(error), getattr(error, "code", "command_rejected")) from None

        return await asyncio.to_thread(dispatch)

    @staticmethod
    def no_provider(_session):
        raise RuntimeError("Only the scripted image provider is selected in this source test")

    async def close(self):
        if self.closed:
            return
        (self.data_root / "receipt.json").write_text(
            json.dumps(
                {
                    "submissions": len(self.adapter.calls),
                    "reference_counts": [len(call.reference_images) for call in self.adapter.calls],
                    "audit_enabled": self.owner.admin_full_audit_status()["enabled"],
                }
            ),
            encoding="utf-8",
        )
        await asyncio.to_thread(self.supervisor.shutdown)
        self.closed = True


async def serve(root):
    root.mkdir(parents=True, exist_ok=False)
    owner = Composition(root)
    ui = LocalWebUI(
        owner, Path(__file__).resolve().parents[1] / "src-tauri/frontend", "http://127.0.0.1:0"
    )
    runner = web.AppRunner(ui.app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    ui.origin = f"http://127.0.0.1:{port}"
    ui.authority = f"127.0.0.1:{port}"
    (root / "server.json").write_text(json.dumps({"url": ui.origin, "pid": os.getpid()}))
    print(ui.origin, flush=True)
    try:
        await ui.stopped.wait()
    finally:
        await owner.close()
        await runner.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    asyncio.run(serve(parser.parse_args().data_root.resolve()))
