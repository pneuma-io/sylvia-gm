"""Private HTTPS policy at the real HTTP boundary; no Tailscale service is started."""

import asyncio
import hashlib
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from sylvia_gm.interfaces.webui import (
    REMOTE_SESSION_SECONDS,
    LocalWebUI,
    OwnerProcess,
    RemoteAccess,
    WebUIError,
    serve,
)

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://sylvia.example.ts.net"
KEY = "synthetic-private-route-key-for-tests-only-0123456789"
HEADERS = {"Host": "sylvia.example.ts.net", "Origin": ORIGIN, "X-Sylvia-WebUI": "1"}


class SelectedOwner:
    """Record whether a request reaches application or filesystem authority."""

    def __init__(self, root):
        self.data_root = root
        self.calls = []

    async def call(self, method, params):
        self.calls.append((method, params))
        return {"selected": len(self.calls)}

    async def close(self):
        pass


@asynccontextmanager
async def private_ui(tmp_path, owner=None, *, expire_sessions=False):
    key_file = tmp_path / "selected-key.txt"
    key_file.write_text(KEY)
    ui = LocalWebUI(
        owner or SelectedOwner(tmp_path),
        ROOT / "src-tauri/frontend",
        ORIGIN,
        remote=RemoteAccess.from_file(ORIGIN, key_file),
    )
    if expire_sessions:
        # Fixture-only clock control behind the real authentication/Origin middleware.
        async def expire(request):
            ui.remote_sessions[request.cookies[ui.cookie]] = time.monotonic() - 1
            return web.json_response({"expired": True})

        ui.app.router.add_post("/__test__/expire-session", expire)
    async with TestServer(ui.app, host="127.0.0.1") as server, aiohttp.ClientSession() as client:
        yield ui, server, client
    assert server.closed and not ui.remote_sessions


async def sign_in(ui, server, client):
    response = await client.post(server.make_url("/auth/login"), headers=HEADERS, json={"key": KEY})
    assert response.status == 200
    cookie = response.cookies[ui.cookie]
    assert cookie["secure"] and cookie["httponly"]
    assert cookie["samesite"] == "Strict" and cookie["path"] == "/"
    assert int(cookie["max-age"]) == REMOTE_SESSION_SECONDS
    assert not cookie["domain"]
    return {**HEADERS, "Cookie": f"{ui.cookie}={cookie.value}"}


def test_private_route_requires_login_even_when_backend_is_reached_directly(tmp_path):
    async def check():
        async with private_ui(tmp_path) as (ui, server, client):
            response = await client.get(server.make_url("/app.html"), headers=HEADERS)
            assert response.status == 200
            assert "Access key" in await response.text()
            assert "Set-Cookie" not in response.headers
            for method, path in [
                ("GET", "/app.js"),
                ("GET", "/api/download/anything"),
                ("POST", "/api/command"),
                ("POST", "/api/upload/builder"),
                ("POST", "/api/stop"),
                ("POST", "/auth/logout"),
            ]:
                response = await client.request(method, server.make_url(path), headers=HEADERS)
                assert response.status == 401
                assert response.headers["Cache-Control"] == "no-store"
            assert not ui.owner.calls
            headers = await sign_in(ui, server, client)
            response = await client.get(server.make_url("/app.html"), headers=headers)
            html = await response.text()
            assert response.status == 200 and 'data-webui-remote="true"' in html
            assert KEY not in html and ui.session not in html
            assert "Set-Cookie" not in response.headers
            payload = {"id": "selected-replay", "method": "campaign_index"}
            first = await client.post(
                server.make_url("/api/command"), headers=headers, json=payload
            )
            repeated = await client.post(
                server.make_url("/api/command"), headers=headers, json=payload
            )
            assert first.status == repeated.status == 200
            assert await first.json() == await repeated.json()
            assert len(ui.owner.calls) == 1
            response = await client.post(
                server.make_url("/api/command"),
                headers=headers,
                json={**payload, "params": {"changed": True}},
            )
            assert response.status == 400
            assert (await response.json())["error"]["code"] == "idempotency_conflict"
            for method, params in [
                ("install_package_file", {"sourcePath": "private.txt"}),
                ("shutdown", {}),
            ]:
                response = await client.post(
                    server.make_url("/api/command"),
                    headers=headers,
                    json={"id": method, "method": method, "params": params},
                )
                assert response.status == 400
            assert len(ui.owner.calls) == 1

    asyncio.run(check())


@pytest.mark.parametrize(
    "changed",
    [
        {"Host": "other.example.ts.net"},
        {"Host": "127.0.0.1:8000"},
        {"Host": "sylvia.example.ts.net.evil.invalid"},
        {"Origin": "https://other.example.ts.net"},
        {"Origin": "http://sylvia.example.ts.net"},
        {"Origin": "null"},
        {"Origin": ORIGIN + ":8443"},
        {"Origin": None},
        {"X-Sylvia-WebUI": None},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
    ],
)
def test_private_controls_and_login_reject_wrong_host_origin_and_cross_site(tmp_path, changed):
    async def check():
        async with private_ui(tmp_path) as (ui, server, client):
            headers = {**await sign_in(ui, server, client), **changed}
            headers = {key: value for key, value in headers.items() if value is not None}
            for path in [
                "/auth/login",
                "/auth/logout",
                "/api/command",
                "/api/stop",
                "/api/upload/builder",
            ]:
                response = await client.post(
                    server.make_url(path), headers=headers, json={"key": KEY}
                )
                assert response.status == 403
                assert "Set-Cookie" not in response.headers
            assert not ui.owner.calls

    asyncio.run(check())


def test_forwarded_headers_and_loopback_cookie_cannot_bypass_remote_auth(tmp_path):
    async def check():
        async with private_ui(tmp_path) as (ui, server, client):
            spoofed = {
                "Forwarded": 'host="sylvia.example.ts.net";proto=https',
                "X-Forwarded-Host": "sylvia.example.ts.net",
                "X-Forwarded-Proto": "https",
                "X-Forwarded-For": "100.64.0.1",
                "Tailscale-User-Login": "owner@example.test",
                "Cookie": f"sylvia_webui_0={ui.session}; {ui.cookie}={ui.session}",
            }
            response = await client.get(server.make_url("/app.html"), headers=spoofed)
            assert response.status == 403
            response = await client.post(
                server.make_url("/api/command"), headers={**HEADERS, **spoofed}, json={}
            )
            assert response.status == 401
            response = await client.get(
                server.make_url("/app.html"),
                headers={
                    **HEADERS,
                    "Sec-Fetch-Site": "cross-site",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Dest": "document",
                },
            )
            assert response.status == 200 and "Access key" in await response.text()
            assert "Set-Cookie" not in response.headers
            for site in ["same-site", "cross-site"]:
                response = await client.get(
                    server.make_url("/app.html"),
                    headers={
                        **HEADERS,
                        "Sec-Fetch-Site": site,
                        "Sec-Fetch-Mode": "no-cors",
                        "Sec-Fetch-Dest": "iframe",
                    },
                )
                assert response.status == 403
            for name, value in [("Host", HEADERS["Host"]), ("Origin", ORIGIN)]:
                # ClientSession normalizes Host; raw TCP preserves ambiguous duplicate headers.
                reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
                body = json.dumps({"key": KEY}).encode()
                raw_headers = "\r\n".join(f"{key}: {value}" for key, value in HEADERS.items())
                writer.write(
                    (
                        f"POST /auth/login HTTP/1.1\r\n{raw_headers}\r\n{name}: {value}\r\n"
                        f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n"
                        "Connection: close\r\n\r\n"
                    ).encode()
                    + body
                )
                await writer.drain()
                status = (await reader.readline()).split()[1]
                writer.close()
                await writer.wait_closed()
                assert status in {b"400", b"403"}
            assert not ui.owner.calls

    asyncio.run(check())


def test_sign_out_expiry_and_session_capacity(tmp_path):
    async def check():
        async with private_ui(tmp_path) as (ui, server, client):
            first = await sign_in(ui, server, client)
            second = await sign_in(ui, server, client)
            assert first["Cookie"] != second["Cookie"]
            response = await client.post(server.make_url("/auth/logout"), headers=first)
            assert response.status == 200
            assert response.cookies[ui.cookie]["max-age"] == "0"
            assert response.cookies[ui.cookie]["secure"]
            response = await client.get(server.make_url("/app.js"), headers=first)
            assert response.status == 401
            response = await client.get(server.make_url("/app.js"), headers=second)
            assert response.status == 200
            ui.remote_sessions = {token: time.monotonic() - 1 for token in ui.remote_sessions}
            response = await client.get(server.make_url("/app.js"), headers=second)
            assert response.status == 401 and not ui.remote_sessions
            ui.remote_sessions = {f"selected-{i}": time.monotonic() + 60 for i in range(32)}
            response = await client.post(
                server.make_url("/auth/login"), headers=HEADERS, json={"key": KEY}
            )
            assert response.status == 429 and len(ui.remote_sessions) == 32

    asyncio.run(check())


def test_login_body_budget_and_attempt_budget_ignore_forwarded_ips(tmp_path):
    async def check():
        async with private_ui(tmp_path) as (ui, server, client):
            response = await client.post(
                server.make_url("/auth/login"), headers=HEADERS, json={"key": "x" * 2000}
            )
            assert response.status == 413
            for payload in [[], None, {"key": 1}, {"key": "incorrect"}]:
                response = await client.post(
                    server.make_url("/auth/login"),
                    headers={**HEADERS, "Content-Type": "application/json"},
                    data=json.dumps(payload),
                )
                assert response.status == 401
            response = await client.post(
                server.make_url("/auth/login"),
                headers={**HEADERS, "X-Forwarded-For": "another-ip"},
                json={"key": KEY},
            )
            assert response.status == 429 and response.headers["Retry-After"] == "60"
            assert not ui.remote_sessions
            ui.login_attempts = [time.monotonic() - 61]
            await sign_in(ui, server, client)

    asyncio.run(check())


def test_parallel_login_requests_share_the_attempt_budget(tmp_path):
    async def check():
        async with private_ui(tmp_path) as (ui, server, client):
            responses = await asyncio.gather(
                *(
                    client.post(
                        server.make_url("/auth/login"),
                        headers={**HEADERS, "X-Forwarded-For": f"synthetic-{index}"},
                        json={"key": "incorrect"},
                    )
                    for index in range(10)
                )
            )
            assert sorted(response.status for response in responses) == [401] * 5 + [429] * 5
            assert len(ui.login_attempts) == 5 and not ui.remote_sessions

    asyncio.run(check())


@pytest.mark.parametrize(
    "arguments",
    [
        ["--webui-remote-origin", ORIGIN],
        ["--webui", "--webui-remote-origin", ORIGIN],
        ["--webui", "--webui-access-key-file", "not-read.txt"],
    ],
)
def test_cli_rejects_incomplete_remote_configuration_before_starting_owner(monkeypatch, arguments):
    from sylvia_gm.main import main

    monkeypatch.setattr("sys.argv", ["sylvia-gm", *arguments])
    with pytest.raises(SystemExit) as exited:
        main()
    assert exited.value.code == 2


def test_empty_remote_origin_cannot_silently_select_local_mode(tmp_path, monkeypatch):
    from sylvia_gm.main import main

    key_file = tmp_path / "selected-key.txt"
    key_file.write_text(KEY)
    monkeypatch.setattr(
        "sys.argv",
        [
            "sylvia-gm",
            "--webui",
            "--data-root",
            str(tmp_path / "unopened"),
            "--port",
            "8765",
            "--webui-remote-origin",
            "",
            "--webui-access-key-file",
            str(key_file),
        ],
    )
    with pytest.raises(SystemExit) as exited:
        main()
    assert exited.value.code == 1
    assert not (tmp_path / "unopened").exists()


@pytest.mark.parametrize(
    "origin",
    [
        "http://sylvia.example.ts.net",
        "https://*.example.ts.net",
        "https://sylvia.example.ts.net/",
        "https://sylvia.example.ts.net/path",
        "https://sylvia.example.ts.net?x",
        "https://other.invalid",
        "https://user@sylvia.example.ts.net",
        "https://sylvia.example.ts.net:443",
        "https://sylvia.example.ts.net:65536",
        "https://sylvia.example.ts.net#x",
    ],
)
def test_remote_configuration_rejects_noncanonical_or_nonprivate_origins(tmp_path, origin):
    with pytest.raises(WebUIError):
        RemoteAccess.from_file(origin, tmp_path / "not-read")


def test_remote_configuration_requires_selected_key_and_fixed_loopback_port(tmp_path):
    key_file = tmp_path / "key.txt"
    with pytest.raises(WebUIError, match="Cannot read"):
        RemoteAccess.from_file(ORIGIN, key_file)
    for key in ["short", "x" * 129, "x" * 43 + " ", "x" * 43 + "\nextra", "x" * 43 + "\n" * 1000]:
        key_file.write_text(key)
        with pytest.raises(WebUIError, match="access-key file"):
            RemoteAccess.from_file(ORIGIN, key_file)
    key_file.write_text(KEY + "\n")
    policy = RemoteAccess.from_file(ORIGIN + ":8443", key_file)
    assert policy.key_digest == hashlib.sha256(KEY.encode()).digest()
    assert KEY not in repr(policy)
    with pytest.raises(WebUIError, match="nonzero"):
        asyncio.run(serve(tmp_path, remote=policy))
    with pytest.raises(WebUIError, match="explicit remote"):
        LocalWebUI(SelectedOwner(tmp_path), ROOT / "src-tauri/frontend", ORIGIN)


@pytest.mark.parametrize(
    "helper", ["webui_private_proxy.cjs", "webui_expiry_recovery.cjs", "webui_logout_loss.cjs"]
)
def test_real_owner_private_proxy_browser_layouts(tmp_path, monkeypatch, helper):
    """Headless desktop/phone layouts through a simulated HTTPS proxy and real owner."""
    monkeypatch.setenv("SYLVIA_GM_TEST_FULL_AUDIT", "1")

    async def check():
        owner = OwnerProcess(tmp_path)
        await owner.start()
        try:
            async with private_ui(
                tmp_path, owner, expire_sessions=helper == "webui_expiry_recovery.cjs"
            ) as (ui, server, _client):
                process = await asyncio.create_subprocess_exec(
                    "node",
                    "--test",
                    "tests/" + helper,
                    env={
                        **os.environ,
                        "SYLVIA_TEST_WEBUI_BACKEND": str(server.make_url("/")).rstrip("/"),
                    },
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                try:
                    output, _ = await asyncio.wait_for(process.communicate(), 120)
                finally:
                    if process.returncode is None:
                        process.terminate()
                        await process.wait()
                print(output.decode(errors="replace"))
                assert process.returncode == 0
                if helper == "webui_private_proxy.cjs":
                    assert owner.process is None  # Phone-sized Stop reached the owner.
        finally:
            await owner.close()
        assert owner.process is None

    asyncio.run(check())
