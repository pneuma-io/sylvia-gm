"""Disposable loopback test transport to the real owner; never reads a user profile."""

import json
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.interfaces.owner_process import _dispatch, _json_value
from tests.test_alpha_f3_package_publication import _clockwork, _install, _surface
from tests.test_campaign_rules_update import campaign, package


def serve(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    database = root / "ui.sqlite3"
    owner = AuthorityOwner.open(database, "synthetic-ui")
    old_record, old_files = _clockwork("1")
    old_record["ui_surfaces"].append(_surface("status", "clockwork.counter"))
    from sylvia_gm.domain.package_library import content_root_for_record

    old_record["content_sha256"] = content_root_for_record(old_record)
    old = _install(owner, ((old_record, old_files),), "old")
    new_record, new_files = package("2")
    new_record["ui_surfaces"].append(_surface("status", "clockwork.counter"))
    new_record["ui_surfaces"][1]["title"] = "Journal"
    new_record["ui_surfaces"][1]["default_placement"] = "window"
    new_record["content_sha256"] = content_root_for_record(new_record)
    _install(owner, ((new_record, new_files),), "new")
    _install(owner, (package("3", panel=False),), "conflict")
    campaign(owner, old)
    campaign(owner, old, "other", state=42)
    frontend = Path(__file__).resolve().parents[1] / "src-tauri" / "frontend"

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(frontend), **kwargs)

        def log_message(self, *args):
            pass

        def do_POST(self):
            nonlocal owner
            try:
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                method, params = request["method"], request.get("params", {})
                if method == "fixture_reopen":
                    owner.close()
                    owner = AuthorityOwner.open(database, "synthetic-ui-reopened")
                    result = {"reopened": True}
                elif method == "fixture_snapshot":
                    result = {
                        "campaign": owner.snapshot("running"),
                        "other": owner.snapshot("other"),
                        "panels": owner.campaign_panels("running"),
                        "lineage": owner.package_lock_lineage("running"),
                    }
                else:
                    if method not in {
                        "campaign_rules_update_options",
                        "preview_campaign_rules_update",
                        "adopt_campaign_rules_update",
                    }:
                        raise ValueError("Unknown synthetic test command")
                    result = _dispatch(
                        None,
                        method,
                        params,
                        application_owner=owner,
                        owner_id="synthetic-ui",
                        provider_resolver=None,
                        model_choices=lambda: (),
                        session_loop=None,
                        storage_root=root,
                    )
                payload = {"result": _json_value(result)}
            except Exception as error:
                payload = {"error": str(error)}
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}"}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        owner.close()


if __name__ == "__main__":
    serve(Path(sys.argv[1]).resolve())
