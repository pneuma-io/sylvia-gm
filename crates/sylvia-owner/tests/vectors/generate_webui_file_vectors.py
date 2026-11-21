"""Generate reference vectors for the WebUI host's exports, downloads and uploads.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-owner/tests/vectors/generate_webui_file_vectors.py

The real owner cannot export or install yet in Rust, so both hosts get the same stand-in owner
(`STANDIN` below, mirrored by `tests/webui_files.rs`). It writes a fixed export file, records what
each upload hands it, and answers fixed results. Here Python's `LocalWebUI` runs under aiohttp's
`TestServer` with the stand-in in process; every step is a raw HTTP/1.1 request on its own
connection, recorded as status, reason, headers and body.

Run-specific values are normalized the same way in both languages: the port becomes `<port>`,
the session cookie `<cookie>`, each download handle `<token:N>`, and the temporary upload and
download folders `<upload>` and `<downloads>`. Each new download copy gets the fixed
modification time `PINNED_NS`, so `ETag` and `Last-Modified` reproduce. `Date` and `Server`
(difference 11) are left out. This script is reference tooling for the port and is retired with
the Python source.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / "src"))

from aiohttp.test_utils import TestServer  # noqa: E402

from sylvia_gm.interfaces.webui import LocalWebUI  # noqa: E402

OUTPUT = HERE / "webui_file_vectors.json"
PINNED_NS = 1_800_000_000_123_456_700
BOUNDARY = "sylvia-vector-boundary"
SUFFIXES = {
    "export_campaign_file": ("exports", ".sylvia-gm-campaign"),
    "export_package_candidate_file": ("exports", ".sylvia-gm-package"),
    "export_campaign_plan_file": ("exports", ".sylvia-gm-plan"),
    "backup_database": ("backups", ".sqlite3"),
}
PAST = "Mon, 01 Jan 2024 00:00:00 GMT"
FUTURE = "Fri, 01 Jan 2100 00:00:00 GMT"
PINNED_DATE = "Fri, 15 Jan 2027 08:00:01 GMT"  # ceil(PINNED_NS / 1e9) as Last-Modified shows it


def export_bytes(method: str, identity: str) -> bytes:
    """The stand-in's export: 1,000 deterministic bytes."""
    seed = f"{method}:{identity}:".encode()
    return (seed * (1000 // len(seed) + 1))[:1000]


def relative_source(data_root: Path, path: str) -> str:
    text = Path(path).relative_to(data_root).as_posix()
    return re.sub(r"^\.webui-upload-[^/]+", "<upload>", text)


class StandIn:
    """The stand-in owner. `tests/webui_files.rs` answers exactly the same way."""

    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root.resolve()
        self.calls: list = []

    async def call(self, method, params=None):
        params = dict(params or {})
        record = {"method": method, "params": {}}
        for key, value in params.items():
            if key == "source_path":
                record["params"][key] = relative_source(self.data_root, value)
                raw = Path(value).read_bytes()
                record["file"] = {"name": Path(value).name, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
            elif key == "source_paths":
                record["params"][key] = [relative_source(self.data_root, item) for item in value]
                record["files"] = [hashlib.sha256(Path(item).read_bytes()).hexdigest() for item in value]
            else:
                record["params"][key] = value
        self.calls.append(record)
        if method in SUFFIXES:
            identity = params.get("backup_id" if method == "backup_database" else "export_id")
            if identity == "rejected":
                return {"__error__": ["command_rejected", "The stand-in refused this export."]}
            folder, suffix = SUFFIXES[method]
            if identity != "missing":
                (self.data_root / folder).mkdir(parents=True, exist_ok=True)
                (self.data_root / folder / f"{identity}{suffix}").write_bytes(export_bytes(method, identity))
            return {"export": {"id": identity, "method": method}, "path": "not for the browser"}
        if method == "install_package_file":
            if "repair" in record["file"]["name"]:
                return {"status": "repairable", "reason": "stand-in"}
            return {"status": "ready", "package": record["file"]["name"]}
        if method == "recover_campaign_file":
            return {"campaign_id": "recovered-1"}
        if method == "preview_character_card_file":
            if record["file"]["name"].endswith(".json"):
                return {"__error__": ["invalid_card", "The stand-in refuses JSON cards."]}
            return {"name": "Stand-in Character"}
        if method == "ingest_builder_sources":
            return {"accepted": len(params.get("source_paths", []))}
        return {"__error__": ["unsupported", "The stand-in does not answer " + method + "."]}

    async def close(self):
        self.calls.append({"method": "shutdown", "params": {}})


class ErrorOwner(StandIn):
    """Turn the stand-in's error marker into the owner's rejection, as the pipe reply does."""

    async def call(self, method, params=None):
        from sylvia_gm.interfaces.webui import WebUIError

        result = await super().call(method, params)
        if isinstance(result, dict) and "__error__" in result:
            code, message = result["__error__"]
            raise WebUIError(message, code)
        return result


def multipart(parts) -> bytes:
    body = b""
    for part in parts:
        disposition = f'form-data; name="{part["name"]}"'
        if "filename" in part:
            disposition += f'; filename="{part["filename"]}"'
        body += f"--{BOUNDARY}\r\nContent-Disposition: {disposition}\r\n".encode()
        for name, value in part.get("headers", []):
            body += f"{name}: {value}\r\n".encode()
        body += b"\r\n" + base64.b64decode(part["b64"]) + b"\r\n"
    return body + f"--{BOUNDARY}--\r\n".encode()


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def steps():
    command = lambda body: {"method": "POST", "path": "/api/command", "json": body,  # noqa: E731
                            "headers": [["Origin", "${origin}"], ["X-Sylvia-WebUI", "1"], ["Cookie", "${cookie}"]]}
    get = lambda *headers, path="${download}", method="GET": {  # noqa: E731
        "method": method, "path": path, "headers": [["Cookie", "${cookie}"], *headers]}
    upload = lambda kind, parts: {"method": "POST", "path": f"/api/upload/{kind}", "multipart": parts,  # noqa: E731
                                  "headers": [["Origin", "${origin}"], ["X-Sylvia-WebUI", "1"], ["Cookie", "${cookie}"]]}
    yield {"name": "bootstrap", "method": "GET", "path": "/app.html", "headers": [], "save_cookie": True}
    yield {"name": "export a campaign", **command({"id": "x1", "method": "export_campaign_file",
                                                  "params": {"exportId": "e1", "campaignId": "c1"}})}
    yield {"name": "download", **get()}
    yield {"name": "download again", **get()}
    yield {"name": "head", **get(method="HEAD")}
    yield {"name": "first ten bytes", **get(["Range", "bytes=0-9"])}
    yield {"name": "last sixteen bytes", **get(["Range", "bytes=-16"])}
    yield {"name": "a tail longer than the file", **get(["Range", "bytes=-5000"])}
    yield {"name": "from byte 990", **get(["Range", "bytes=990-"])}
    yield {"name": "an end past the file", **get(["Range", "bytes=995-2000"])}
    yield {"name": "a start past the file", **get(["Range", "bytes=5000-"])}
    yield {"name": "a start at the end", **get(["Range", "bytes=1000-1001"])}
    yield {"name": "an end before the start", **get(["Range", "bytes=9-3"])}
    yield {"name": "two ranges", **get(["Range", "bytes=0-1,4-5"])}
    yield {"name": "another unit", **get(["Range", "items=0-1"])}
    yield {"name": "an empty range", **get(["Range", "bytes=-"])}
    yield {"name": "head of a range", **get(["Range", "bytes=0-9"], method="HEAD")}
    yield {"name": "if-none-match the etag", **get(["If-None-Match", "${etag}"])}
    yield {"name": "if-none-match any", **get(["If-None-Match", "*"])}
    yield {"name": "if-none-match the weak etag", **get(["If-None-Match", "W/${etag}"])}
    yield {"name": "if-none-match in a list", **get(["If-None-Match", '"other", ${etag}'])}
    yield {"name": "if-none-match another", **get(["If-None-Match", '"other"'])}
    yield {"name": "if-none-match garbage first", **get(["If-None-Match", 'garbage, ${etag}'])}
    yield {"name": "if-match another", **get(["If-Match", '"other"'])}
    yield {"name": "if-match the etag", **get(["If-Match", "${etag}"])}
    yield {"name": "if-match the weak etag", **get(["If-Match", "W/${etag}"])}
    yield {"name": "if-match any", **get(["If-Match", "*"])}
    yield {"name": "if-match empty", **get(["If-Match", ""])}
    yield {"name": "if-modified-since the future", **get(["If-Modified-Since", FUTURE])}
    yield {"name": "if-modified-since the past", **get(["If-Modified-Since", PAST])}
    yield {"name": "if-modified-since the same second", **get(["If-Modified-Since", PINNED_DATE])}
    yield {"name": "if-modified-since nonsense", **get(["If-Modified-Since", "yesterday"])}
    yield {"name": "if-modified-since with an offset", **get(["If-Modified-Since", "Fri, 01 Jan 2100 00:00:00 +0500"])}
    yield {"name": "if-modified-since two-digit year", **get(["If-Modified-Since", "Friday, 01-Jan-99 00:00:00 GMT"])}
    yield {"name": "if-modified-since asctime", **get(["If-Modified-Since", "Fri Jan  1 00:00:00 2100"])}
    yield {"name": "if-modified-since an impossible day", **get(["If-Modified-Since", "Sat, 31 Feb 2100 00:00:00 GMT"])}
    yield {"name": "if-modified-since but none-match differs", **get(["If-Modified-Since", FUTURE],
                                                                       ["If-None-Match", '"other"'])}
    yield {"name": "if-unmodified-since the past", **get(["If-Unmodified-Since", PAST])}
    yield {"name": "if-unmodified-since the future", **get(["If-Unmodified-Since", FUTURE])}
    yield {"name": "if-unmodified-since past with if-match", **get(["If-Unmodified-Since", PAST],
                                                                     ["If-Match", "${etag}"])}
    yield {"name": "if-range in the past", **get(["Range", "bytes=0-9"], ["If-Range", PAST])}
    yield {"name": "if-range in the future", **get(["Range", "bytes=0-9"], ["If-Range", FUTURE])}
    yield {"name": "if-range an etag", **get(["Range", "bytes=0-9"], ["If-Range", "${etag}"])}
    yield {"name": "accepting compression", **get(["Accept-Encoding", "gzip, br"])}
    yield {"name": "an unknown handle", **get(path="/api/download/unknown")}
    yield {"name": "back up the database", **command({"id": "x2", "method": "backup_authority",
                                                     "params": {"backupId": "b1"}})}
    yield {"name": "download the backup", **get()}
    yield {"name": "export a plan", **command({"id": "x3", "method": "export_campaign_plan_file",
                                              "params": {"exportId": "p1", "campaignId": "c1"}})}
    yield {"name": "download the plan", **get()}
    yield {"name": "export a package", **command({"id": "x4", "method": "export_package_candidate_file",
                                                 "params": {"exportId": "k1", "packageId": "pkg"}})}
    yield {"name": "download the package", **get()}
    yield {"name": "export the campaign again", **command({"id": "x5", "method": "export_campaign_file",
                                                          "params": {"exportId": "e1", "campaignId": "c1"}})}
    yield {"name": "the first download still works", **get(path="${first_download}")}
    yield {"name": "an export the owner refuses", **command({"id": "x6", "method": "export_campaign_file",
                                                            "params": {"exportId": "rejected"}})}
    yield {"name": "an export that leaves no file", **command({"id": "x7", "method": "export_campaign_file",
                                                              "params": {"exportId": "missing"}})}
    yield {"name": "an export without an identity", **command({"id": "x8", "method": "export_campaign_file",
                                                              "params": {"campaignId": "c1"}})}
    package = b"PK\x03\x04 stand-in package bytes"
    yield {"name": "upload a package", **upload("package", [
        {"name": "params", "b64": b64(json.dumps({"sourceKind": "file"}).encode())},
        {"name": "file", "filename": "Rift.sylvia-gm-package", "b64": b64(package)}])}
    yield {"name": "upload a package needing repair", **upload("package", [
        {"name": "file", "filename": "repair-me.sylvia-gm-package", "b64": b64(package)}])}
    yield {"name": "upload a recovery", **upload("recovery", [
        {"name": "file", "filename": "old.sylvia-gm-campaign", "b64": b64(b"campaign bytes")},
        {"name": "params", "b64": b64(json.dumps({"campaignName": "Rift"}).encode())}])}
    yield {"name": "upload a character card", **upload("character", [
        {"name": "file", "filename": "card.png", "headers": [["Content-Type", "image/png"]], "b64": b64(b"\x89PNG card")}])}
    yield {"name": "upload builder sources", **upload("builder", [
        {"name": "params", "b64": b64(json.dumps({"sessionId": "s1", "ingestionId": "i1"}).encode())},
        {"name": "file", "filename": "notes.md", "b64": b64(b"# Notes\n")},
        {"name": "file", "filename": "map.txt", "b64": b64(b"a map")}])}
    yield {"name": "an upload the owner refuses", **upload("character", [
        {"name": "params", "b64": b64(json.dumps({"method": "nothing"}).encode())},
        {"name": "file", "filename": "card.json", "b64": b64(b"{}")}])}
    yield {"name": "stop", **command({"id": "x9", "method": "application_info"}), "path": "/api/stop", "json": None}


def request_bytes(step, values) -> bytes:
    def fill(text: str) -> str:
        for key, value in values.items():
            text = text.replace("${" + key + "}", value)
        return text

    method, path = step["method"], fill(step["path"])
    lines = [f"{method} {path} HTTP/1.1", f"Host: {values['authority']}"]
    lines += [f"{name}: {fill(value)}" for name, value in step["headers"]]
    body = b""
    if step.get("json") is not None:
        body = json.dumps(step["json"]).encode()
        lines.append("Content-Type: application/json")
    elif "multipart" in step:
        body = multipart(step["multipart"])
        lines.append(f"Content-Type: multipart/form-data; boundary={BOUNDARY}")
    if method == "POST" or body:
        lines.append(f"Content-Length: {len(body)}")
    lines.append("Connection: close")
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + body


async def send(port: int, raw: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(raw)
    await writer.drain()
    data = await reader.read()
    writer.close()
    return data


def parse_response(data: bytes):
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    _, status, reason = lines[0].split(" ", 2)
    headers = []
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers.append([name.strip().lower(), value.strip()])
    if any(name == "transfer-encoding" and "chunked" in value for name, value in headers):
        decoded, rest = b"", body
        while rest:
            size_line, _, rest = rest.partition(b"\r\n")
            size = int(size_line.split(b";")[0], 16)
            if size == 0:
                break
            decoded, rest = decoded + rest[:size], rest[size + 2:]
        body = decoded
    return int(status), reason, headers, body


def describe(status, reason, headers, body, values, aliases):
    def clean(text: str) -> str:
        text = text.replace(values["authority"], "127.0.0.1:<port>").replace("sylvia_webui_" + values["port"], "sylvia_webui_<port>")
        for token, name in aliases.items():
            text = text.replace(token, name)
        return text

    shown = []
    for name, value in sorted(headers):
        if name in ("date", "server", "connection", "keep-alive"):
            continue
        if name == "set-cookie":
            value = re.sub(r"=([^;]*)", "=<cookie>", value, count=1)
        shown.append([clean(name), clean(value)])
    content_type = next((value for name, value in headers if name == "content-type"), "")
    if content_type.startswith("application/json"):
        shown_body = {"json": json.loads(clean(body.decode("utf-8")))}
    elif content_type.startswith("text/") and len(body) <= 4096:
        shown_body = {"text": clean(body.decode("utf-8"))}
    else:
        shown_body = {"size": len(body), "sha256": hashlib.sha256(body).hexdigest(),
                      "start": body[:24].decode("latin-1")}
    return {"status": status, "reason": reason, "headers": shown, "body": shown_body}


def listing(data_root: Path, aliases) -> list:
    rows = []
    for path in sorted(data_root.rglob("*")):
        text = path.relative_to(data_root).as_posix()
        text = re.sub(r"^\.webui-download-[^/]+", "<downloads>", text)
        text = re.sub(r"^\.webui-upload-[^/]+", "<upload>", text)
        for token, name in aliases.items():
            text = text.replace(token, name)
        rows.append([text, "dir" if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest()])
    return sorted(rows)


async def record():
    folder = Path(tempfile.mkdtemp(prefix="sylvia-webui-files-")).resolve()
    try:
        data_root = folder / "data"
        data_root.mkdir()
        owner = ErrorOwner(data_root)
        ui = LocalWebUI(owner, ROOT / "src-tauri/frontend", "http://127.0.0.1:0")
        results = []
        async with TestServer(ui.app, host="127.0.0.1") as server:
            port = str(server.port)
            ui.origin = f"http://127.0.0.1:{port}"
            ui.authority = f"127.0.0.1:{port}"
            ui.cookie = "sylvia_webui_" + port
            values = {"origin": ui.origin, "authority": ui.authority, "port": port, "cookie": ""}
            aliases: dict[str, str] = {}
            pinned: set[Path] = set()
            for step in steps():
                calls_before = len(owner.calls)
                data = await send(server.port, request_bytes(step, values))
                status, reason, headers, body = parse_response(data)
                if step.get("save_cookie"):
                    cookie = next(value for name, value in headers if name == "set-cookie")
                    values["cookie"] = cookie.split(";")[0]
                if headers and any(name == "content-type" and value.startswith("application/json")
                                   for name, value in headers):
                    reply = json.loads(body)
                    download = (reply.get("result") or {}).get("download") if isinstance(reply, dict) else None
                    if download:
                        token = download.rsplit("/", 1)[1]
                        aliases.setdefault(token, f"<token:{len(aliases) + 1}>")
                        values.setdefault("first_download", download)
                        values["download"] = download
                # Pin each new download copy's modification time.
                for path in ui.downloads.values():
                    if path not in pinned and path.exists():
                        os.utime(path, ns=(PINNED_NS, PINNED_NS))
                        pinned.add(path)
                etag = next((value for name, value in headers if name == "etag"), None)
                if etag:
                    values["etag"] = etag
                results.append({"name": step["name"], "response": describe(status, reason, headers, body, values, aliases),
                                "owner": owner.calls[calls_before:]})
            before_cleanup = listing(data_root, aliases)
        after_cleanup = listing(data_root, aliases)
        return {"steps": results, "files_before_cleanup": before_cleanup, "files_after_cleanup": after_cleanup}
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def main() -> None:
    vectors = {"python": sys.version.split()[0], "pinned_ns": PINNED_NS, "boundary": BOUNDARY,
               "steps": list(steps()), "recorded": asyncio.run(record())}
    OUTPUT.write_text(json.dumps(vectors, indent=1, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
