"""Disposable source WebUI with real owner dispatch for Character File transfer journeys.

The source library holds a saved copy of the actual SRD Lite Last Cart Through Player Character,
its exact packages and an imported PNG card.
The recipient starts empty. Image generation has no adapter, so transfer cannot create images.
"""

import argparse
import asyncio
import io
import json
import os
from pathlib import Path

from aiohttp import web

from sylvia_gm.agent._session_loop import CampaignSessionLoop
from sylvia_gm.application.character_bundle import read_character_bundle
from sylvia_gm.application.character_card_import import save_character_card_import
from sylvia_gm.application.character_transfer import (
    export_character_source,
    import_character_source,
)
from sylvia_gm.application.desktop_update.drafts import DraftStore
from sylvia_gm.application.image_art import ImageArt
from sylvia_gm.application.ready_campaigns import ready_campaign_catalog, resolve_ready_campaign
from sylvia_gm.infrastructure.launch_handshake import derive_owner_response
from sylvia_gm.interfaces.local_api import LocalCommandBus
from sylvia_gm.interfaces.local_owner import LocalOwnerSupervisor
from sylvia_gm.interfaces.owner_process import _dispatch, _json_value
from sylvia_gm.interfaces.webui import LocalWebUI, WebUIError
from tests.test_character_art_import import card_png


class Composition:
    def __init__(self, root, package_out=None):
        self.data_root = root
        self.closed = False
        self.supervisor = LocalOwnerSupervisor()
        credential = self.supervisor.acquire_owner(root / "authority.sqlite3", "character-webui")
        self.supervisor.complete_handshake(derive_owner_response(credential.secret_hex))
        self.owner = self.supervisor.application_owner()
        self.owner.set_admin_full_audit(True)
        self.art = ImageArt(self.owner)
        self.owner._image_art = self.art
        self.seed = {}
        if package_out is not None:
            ready = next(
                row for row in ready_campaign_catalog() if row["id"] == "last-cart-through"
            )
            selected, party = resolve_ready_campaign(
                self.owner, root, ready["id"], ready["content_sha256"]
            )
            with package_out.open("xb") as stream:
                self.owner.export_package_candidate(selected.candidate.candidate_id, stream)
            # Save the prepared party member as an ordinary library entry, not a built-in starter.
            member = next(item for item in party if item.role == "player_character")
            archive = io.BytesIO()
            export_character_source(self.owner, root, member, target=archive)
            bundle = read_character_bundle(io.BytesIO(archive.getvalue()))
            saved = import_character_source(
                self.owner,
                root,
                io.BytesIO(archive.getvalue()),
                expected_content_sha256=bundle.content_sha256,
            )["character"]
            card = save_character_card_import(root, operation_id="card", source=card_png())
            self.seed = {
                "playable": {"kind": saved["kind"], "id": saved["id"], "name": saved["name"]},
                "profile": {"kind": "character_profile", "id": card["id"], "name": card["name"]},
            }
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
                        owner_id="character-webui",
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
        raise RuntimeError("No model provider is selected in this source test")

    async def close(self):
        if self.closed:
            return
        (self.data_root / "receipt.json").write_text(
            json.dumps(
                {
                    "image_adapters": len(self.art.adapters),
                    "image_requests": len(self.art._futures),
                    "audit_enabled": self.owner.admin_full_audit_status()["enabled"],
                }
            ),
            encoding="utf-8",
        )
        await asyncio.to_thread(self.supervisor.shutdown)
        self.closed = True


async def serve(root, package_out):
    root.mkdir(parents=True, exist_ok=False)
    owner = Composition(root, package_out)
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
    (root / "server.json").write_text(
        json.dumps({"url": ui.origin, "pid": os.getpid(), "seed": owner.seed})
    )
    print(ui.origin, flush=True)
    try:
        await ui.stopped.wait()
    finally:
        await owner.close()
        await runner.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--package-out", type=Path)
    arguments = parser.parse_args()
    asyncio.run(serve(arguments.data_root.resolve(), arguments.package_out))
