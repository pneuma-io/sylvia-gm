"""Loopback transport uses the real owner, packages, receipts and saved sessions."""

import asyncio
import json
import os
import secrets
from pathlib import Path

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.interfaces.webui import LocalWebUI, OwnerProcess, WebUIError, browser_command
from tests.test_player_actions import _create, _request
from tests.test_tactical_grid_ui import _installed_archive

ROOT = Path(__file__).resolve().parents[1]


class Browser:
    """An HTTP client with the same origin, cookie and command envelope as the browser."""

    async def open(self, owner):
        self.ui = LocalWebUI(owner, ROOT / "src-tauri/frontend", "http://127.0.0.1:0")
        self.server = TestServer(self.ui.app, host="127.0.0.1")
        await self.server.start_server()
        self.url = str(self.server.make_url("/")).rstrip("/")
        self.ui.origin = self.url
        self.ui.authority = self.url.removeprefix("http://")
        self.client = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        self.headers = {"Origin": self.url, "X-Sylvia-WebUI": "1"}
        response = await self.client.get(self.url + "/app.html")
        assert response.status == 200
        html = await response.text()
        assert "webui-bridge.js" in html
        assert self.ui.session not in html
        assert "HttpOnly" in response.headers["Set-Cookie"]
        return self

    async def raw(self, method, params=None, identity=None):
        return await self.client.post(
            self.url + "/api/command",
            headers=self.headers,
            json={
                "id": identity or secrets.token_hex(12),
                "method": method,
                "params": params or {},
            },
        )

    async def call(self, method, params=None, identity=None):
        response = await self.raw(method, params, identity)
        value = await response.json()
        assert response.status == 200, value
        assert value["ok"], value
        return value["result"]

    async def close(self):
        await self.client.close()
        await self.server.close()


def test_command_adapter_preserves_nested_game_choices_and_rejects_host_paths():
    value = {"campaignId": "c", "selection": {"actor": "a", "equipment_id": "e"}}
    assert browser_command("execute_panel_action", value) == (
        "execute_panel_action",
        {"campaign_id": "c", "selection": value["selection"]},
    )
    assert browser_command("start_ready_campaign", {"command": {"template_id": "p"}})[1] == {
        "template_id": "p",
    }
    with pytest.raises(WebUIError, match="Host file paths"):
        browser_command("install_package_file", {"sourcePath": "unrelated.txt"})


def test_browser_real_owner_action_replay_export_reopen_and_origin_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("SYLVIA_GM_TEST_FULL_AUDIT", "1")
    # Uses the existing admitted synthetic spatial package, without changing TD or its fixtures.
    with AuthorityOwner.open(tmp_path / "authority.sqlite3", "seed") as owner:
        _create(owner)

    async def check():
        owner = OwnerProcess(tmp_path)
        await owner.start()
        browser = await Browser().open(owner)
        try:
            response = await browser.client.get(
                browser.url + "/app.html",
                headers={
                    "Sec-Fetch-Site": "cross-site",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Dest": "document",
                },
            )
            assert response.status == 200  # Following a link may open the public shell.
            response = await browser.client.get(
                browser.url + "/app.html",
                headers={
                    "Sec-Fetch-Site": "cross-site",
                    "Sec-Fetch-Mode": "no-cors",
                    "Sec-Fetch-Dest": "iframe",
                },
            )
            assert response.status == 403
            async with aiohttp.ClientSession() as stranger:
                response = await stranger.post(
                    browser.url + "/api/command",
                    headers=browser.headers,
                    json={"id": "bad", "method": "campaign_index"},
                )
                assert response.status == 401
            for headers in (
                {"Origin": "https://other.invalid", "X-Sylvia-WebUI": "1"},
                {"Origin": browser.url},
                {**browser.headers, "Host": "other.invalid"},
                {**browser.headers, "Sec-Fetch-Site": "cross-site"},
            ):
                response = await browser.client.post(
                    browser.url + "/api/command",
                    headers=headers,
                    json={"id": "bad", "method": "campaign_index"},
                )
                assert response.status == 403
            for method, params in (
                ("shutdown", {}),
                ("install_package_file", {"sourcePath": "x"}),
                ("desktop_update_install", {}),
                ("open_admin_audit_window", {}),
            ):
                response = await browser.raw(method, params)
                assert response.status == 400
                assert (await response.json())["error"]["code"] == "unsupported"
            assert (await browser.call("admin_full_audit_status"))["enabled"]
            initial = await browser.call("display_snapshot", {"campaignId": "campaign"})
            request = _request()
            first = await browser.call("execute_panel_action", request, "same-http-request")
            repeated = await browser.call("execute_panel_action", request, "same-http-request")
            assert repeated == first
            # A second HTTP identity still reaches the owner's durable operation receipt.
            replay = await browser.call("execute_panel_action", request)
            assert replay == first
            current = await browser.call("display_snapshot", {"campaignId": "campaign"})
            assert current["revision"] == initial["revision"] + 1
            response = await browser.raw(
                "execute_panel_action", {**request, "operation_id": "other"}, "same-http-request"
            )
            assert (await response.json())["error"]["code"] == "idempotency_conflict"
            response = await browser.raw(
                "execute_panel_action", {**request, "operation_id": "stale"}
            )
            assert response.status == 400
            assert await browser.call("display_snapshot", {"campaignId": "campaign"}) == current
            # Actual launch seeding includes the ordinary Lite package, without activating Full.
            packages = await browser.call("package_candidates")
            assert "sylvia.srd-lite.bg3" in json.dumps(packages)
            exported = await browser.call(
                "export_campaign_file",
                {
                    "exportId": "web-export",
                    "operationId": "web-export",
                    "campaignId": "campaign",
                },
            )
            assert "artifact_path" not in exported
            assert str(tmp_path) not in json.dumps(exported)
            response = await browser.client.get(browser.url + exported["download"])
            archive = await response.read()
            assert archive.startswith(b"PK")
            chat = await browser.call(
                "create_builder_chat",
                {
                    "operationId": "browser-chat",
                    "providerId": "synthetic",
                    "modelId": "synthetic",
                    "displayName": "Browser saved Builder",
                },
            )
            session_id = chat["session"]["session_id"]
            draft = {
                "format_version": 1,
                "sessions": [
                    {
                        "session_id": session_id,
                        "text": "Unsent browser draft",
                        "ooc_text": "",
                        "ooc_open": False,
                        "questions": [],
                    }
                ],
            }
            await browser.call("save_desktop_drafts", {"drafts": draft})
            conflict = OwnerProcess(tmp_path)
            with pytest.raises(WebUIError, match="already open"):
                await conflict.start()
            assert await browser.call("display_snapshot", {"campaignId": "campaign"}) == current
            response = await browser.client.post(browser.url + "/api/stop", headers=browser.headers)
            assert response.status == 200
            assert (await response.json())["result"]["status"] == "closed"
            assert owner.process is None
            await asyncio.wait_for(browser.ui.stopped.wait(), 1)
        finally:
            await browser.close()
        reopened = OwnerProcess(tmp_path)
        await reopened.start()
        browser = await Browser().open(reopened)
        try:
            assert await browser.call("display_snapshot", {"campaignId": "campaign"}) == current
            assert (await browser.call("load_desktop_drafts"))["sessions"][0][
                "text"
            ] == "Unsent browser draft"
            assert any(
                row["session"]["session_id"] == session_id
                for row in await browser.call("builder_chat_index")
            )
            # Browser upload supplies bytes; it cannot select an arbitrary server path.
            form = aiohttp.FormData()
            form.add_field("params", json.dumps({"installId": "bad-package"}))
            form.add_field("file", b"not a package", filename="bad.sylvia-gm-package")
            response = await browser.client.post(
                browser.url + "/api/upload/package", headers=browser.headers, data=form
            )
            assert response.status == 400
            assert await browser.call("display_snapshot", {"campaignId": "campaign"}) == current
            form = aiohttp.FormData()
            form.add_field("params", json.dumps({"installId": "selected-package"}))
            form.add_field("file", _installed_archive(), filename="selected.sylvia-gm-package")
            response = await browser.client.post(
                browser.url + "/api/upload/package", headers=browser.headers, data=form
            )
            installed = (await response.json())["result"]
            assert response.status == 200 and installed["status"] == "installed"
            assert "candidate" in installed
            form = aiohttp.FormData()
            form.add_field(
                "params", json.dumps({"sessionId": session_id, "ingestionId": "reference"})
            )
            form.add_field(
                "file", b"Synthetic selected Builder reference.", filename="reference.txt"
            )
            response = await browser.client.post(
                browser.url + "/api/upload/builder", headers=browser.headers, data=form
            )
            assert response.status == 200
            assert (await response.json())["result"]["source_count"] == 1
        finally:
            await browser.close()
        assert owner.process is None and reopened.process is None

        recovered_owner = OwnerProcess(tmp_path / "recovered")
        await recovered_owner.start()
        browser = await Browser().open(recovered_owner)
        try:
            form = aiohttp.FormData()
            form.add_field("params", json.dumps({"recoveryId": "selected-recovery"}))
            form.add_field("file", archive, filename="selected.sylvia-gm-campaign")
            response = await browser.client.post(
                browser.url + "/api/upload/recovery", headers=browser.headers, data=form
            )
            recovery = (await response.json())["result"]
            assert response.status == 200 and recovery["status"] == "recovered"
            assert recovery["recovery"]["campaign_id"] == "campaign"
            assert await browser.call("display_snapshot", {"campaignId": "campaign"}) == current
        finally:
            await browser.close()

    asyncio.run(check())


def test_browser_stream_reconnect_stop_and_saved_manual_retry(tmp_path, monkeypatch):
    # Test-only child runs the production owner with existing scripted-provider fixtures.
    # No real provider, credentials, external calls or model subprocess is used.
    original = asyncio.create_subprocess_exec
    code = (
        "import sys; "
        "from tests.test_alpha_f6e_owner_session_transport import "
        "_FailThenRetryProvider,_ControlledBlockingBuilderProvider,_HarnessResolver; "
        "from sylvia_gm.interfaces.owner_process import run; "
        "p=(_FailThenRetryProvider() if sys.argv[1]=='retry' "
        "else _ControlledBlockingBuilderProvider()); "
        "raise SystemExit(run(sys.stdin,sys.stdout,provider_resolver=_HarnessResolver(p)))"
    )
    mode = "block"

    async def launch(*args, **kwargs):
        return await original(args[0], "-B", "-c", code, mode, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join((str(ROOT / "src"), str(ROOT))))
    monkeypatch.setenv("SYLVIA_GM_TEST_FULL_AUDIT", "1")

    async def check():
        nonlocal mode
        owner = OwnerProcess(tmp_path)
        await owner.start()
        browser = await Browser().open(owner)
        try:
            chat = await browser.call(
                "create_builder_chat",
                {
                    "operationId": "stream-chat",
                    "providerId": "test.transport",
                    "modelId": "test.transport/model0",
                    "displayName": "Scripted stream",
                },
            )
            sid = chat["session"]["session_id"]
            submit = asyncio.create_task(
                browser.call(
                    "submit_session_input",
                    {
                        "sessionId": sid,
                        "turnId": "stream-turn",
                        "attemptId": "stream-attempt",
                        "messageId": "stream-message",
                        "userText": "Synthetic stream control.",
                    },
                )
            )
            for _ in range(100):
                context = await browser.call("session_context", {"sessionId": sid})
                if (context.get("activity") or {}).get("attempt_id") == "stream-attempt":
                    break
                await asyncio.sleep(0.02)
            heartbeat = await browser.call(
                "provider_stream_heartbeat",
                {
                    "campaignId": sid,
                    "attemptId": "stream-attempt",
                    "attachmentGeneration": None,
                },
            )
            generation = heartbeat["attachment_generation"]
            await browser.call(
                "disconnect_provider_stream",
                {
                    "campaignId": sid,
                    "attemptId": "stream-attempt",
                    "attachmentGeneration": generation,
                },
            )
            reconnect = await browser.call(
                "reconnect_provider_stream",
                {
                    "campaignId": sid,
                    "attemptId": "stream-attempt",
                    "attachmentGeneration": generation,
                },
            )
            assert reconnect["attachment_generation"] == generation + 1
            await browser.call(
                "interrupt_provider_stream", {"campaignId": sid, "attemptId": "stream-attempt"}
            )
            with pytest.raises(AssertionError):
                await submit
            context = await browser.call("session_context", {"sessionId": sid})
            assert context["activity"]["status"] == "stopped"
            assert context["session"]["status"] == "active"
        finally:
            await browser.close()

        mode = "retry"
        owner = OwnerProcess(tmp_path / "retry")
        await owner.start()
        browser = await Browser().open(owner)
        try:
            catalog = await browser.call("ready_campaign_catalog")
            template = next(row for row in catalog if row["id"] == "a-debt-in-iron")
            started = await browser.call(
                "start_ready_campaign",
                {
                    "command": {
                        "operation_id": "browser-lite",
                        "template_id": template["id"],
                        "expected_content_sha256": template["content_sha256"],
                        "display_name": "Browser Lite",
                        "provider_id": "test.transport",
                        "model_id": "test.transport/model0",
                        "model_guidance_override": True,
                    }
                },
            )
            campaign, sid = started["campaign_id"], started["dm_session_id"]
            response = await browser.raw(
                "submit_session_input",
                {
                    "sessionId": sid,
                    "turnId": started["dm_turn_id"],
                    "attemptId": "failed-browser",
                    "messageId": "browser-user",
                    "userText": "Continue.",
                },
            )
            assert response.status == 400
            basis = await browser.call(
                "retryable_interruption",
                {
                    "campaignId": campaign,
                    "failedAttemptId": "failed-browser",
                },
            )
            assert basis["retryable"]
            result = await browser.call(
                "manual_retry_session",
                {
                    "sessionId": sid,
                    "interruptionId": basis["interruption_id"],
                    "retryId": "browser-retry",
                },
                "same-retry-request",
            )
            assert not result["replayed"]
            projection = await browser.call("player_projection", {"campaignId": campaign})
            assert "Recovered narration." in json.dumps(projection)
            assert "INCOMPLETE PROSE" not in json.dumps(projection)
            assert (
                await browser.call(
                    "manual_retry_session",
                    {
                        "sessionId": sid,
                        "interruptionId": basis["interruption_id"],
                        "retryId": "browser-retry",
                    },
                    "same-retry-request",
                )
                == result
            )
            assert await browser.call("player_projection", {"campaignId": campaign}) == projection
            context = await browser.call("session_context", {"sessionId": sid})
            assert sum(row["kind"] == "user" for row in context["messages"]) == 1
            audit = await browser.call("admin_full_audit_status")
            assert audit["enabled"] and audit["trace_count"] > 0
            # Inspect the selected fixture's exact request/output without printing
            # or writing raw Full Audit into test reports or public evidence.
            records = [
                await browser.call("admin_full_audit_record", {"index": index})
                for index in range(audit["trace_count"])
            ]
            assert all(
                row.get("trace") is not None
                or row.get("unavailable_reason") == "record_too_large_for_live_view"
                for row in records
            )
            # The browser retains the native live-view bound. Exact oversized
            # traces use the existing owner-only export in selected disposable
            # test data, never an arbitrary browser-supplied path.
            audit_path = tmp_path / "retry" / "selected-full-audit.json"
            await owner.call(
                "export_admin_full_audit", {"destination_path": str(audit_path.resolve())}
            )
            captured = audit_path.read_text(encoding="utf-8")
            if "Continue." not in captured or "Recovered narration." not in captured:
                pytest.fail(
                    "Selected scripted owner Audit did not retain its exact input and output."
                )
        finally:
            await browser.close()

    asyncio.run(check())
