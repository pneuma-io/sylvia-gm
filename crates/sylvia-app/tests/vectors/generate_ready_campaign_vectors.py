"""The prepared-campaign inventory read through Python's own code.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_ready_campaign_vectors.py

`ready_campaigns.ready_campaign_catalog()` runs on the shipped `ready-campaigns.json` and on
damaged copies of it. Each case is either raw bytes, or a list of JSON edits (`set` or `delete`
at a JSON pointer) applied to the parsed shipped inventory, so the Rust test can make the same
copy without storing it. Each outcome is the catalog (whole for the shipped file, its canonical
SHA-256 otherwise) or the exception's class and message. It writes
`ready_campaigns/catalog_vectors.json`; `tests/ready_campaign_vectors.rs` replays every case.
The owner commands themselves are proven by the `ready-campaigns` comparison session.
Reference tooling only.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[3]
sys.path.insert(0, str(REPOSITORY / "src"))

from sylvia_gm.application import ready_campaigns  # noqa: E402
from sylvia_gm.domain.package_library import canonical_json_bytes  # noqa: E402

OUT = HERE / "ready_campaigns"
SHIPPED = REPOSITORY / "src" / "sylvia_gm" / "campaign" / "ready-campaigns.json"
ZERO = "0" * 64


def cases(shipped: dict) -> list[dict[str, object]]:
    first_id = shipped["campaigns"][0]["id"]
    small = {"role": "companion", "required_character_id": "nobody"}

    def edits(name: str, *changes: list[object]) -> dict[str, object]:
        return {"name": name, "edits": [list(change) for change in changes]}

    return [
        edits("shipped"),
        {"name": "too large", "raw_pad": 1024 * 1024},
        {"name": "not JSON", "raw": "not json"},
        edits("format version 2", ["set", "/format_version", 2]),
        edits("format version true", ["set", "/format_version", True]),
        edits("format version 1.0", ["set", "/format_version", 1.0]),
        edits("provenance not an object", ["set", "/provenance", []]),
        edits("three campaigns", ["delete", "/campaigns/3"]),
        edits("campaigns not a list", ["set", "/campaigns", {}]),
        edits("a row with another key", ["set", "/campaigns/0/extra", 1]),
        edits("a row without its party", ["delete", "/campaigns/0/party"]),
        edits("an empty party", ["set", "/campaigns/0/party", []]),
        edits("seven in a party", ["set", "/campaigns/0/party", [small] * 7]),
        edits("a member not an object", ["set", "/campaigns/0/party/0", "member"]),
        edits("an identity not a string", ["set", "/campaigns/0/id", 7]),
        edits("bindings not an object", ["set", "/campaigns/0/bindings", []]),
        edits("duplicate identities", ["set", "/campaigns/1/id", first_id]),
        edits("an unknown adventure", ["set", "/campaigns/0/id", "no-such-adventure"]),
        edits("a changed Plan", ["set", "/campaigns/2/plan_content_sha256", ZERO]),
        edits("changed bindings", ["set", "/campaigns/2/bindings/package_closure_sha256", ZERO]),
        edits("a public preview", ["set", "/campaigns/1/party/0/character", None]),
        edits("a character without its summary", ["delete", "/campaigns/1/party/1/character/summary"]),
        edits("a character without its state", ["delete", "/campaigns/1/party/1/character/entity_state"]),
        edits("a member without its role", ["delete", "/campaigns/1/party/1/role"]),
        edits("a participant not an object", ["set", "/campaigns/1/party/1/character/participant", [1]]),
    ]


def edited(shipped: dict, changes: list[list[object]]) -> dict:
    value = copy.deepcopy(shipped)
    for change in changes:
        action, pointer = change[0], str(change[1])
        parts = [part for part in pointer.split("/")[1:]]
        parent = value
        for part in parts[:-1]:
            parent = parent[int(part)] if isinstance(parent, list) else parent[part]
        last = parts[-1]
        if action == "set":
            if isinstance(parent, list):
                parent[int(last)] = change[2]
            else:
                parent[last] = change[2]
        elif isinstance(parent, list):
            del parent[int(last)]
        else:
            del parent[last]
    return value


def main() -> None:
    OUT.mkdir(exist_ok=True)
    raw = SHIPPED.read_bytes()
    shipped = json.loads(raw)
    recorded = []
    with tempfile.TemporaryDirectory() as scratch:
        folder = Path(scratch)
        ready_campaigns.files = lambda _package: folder  # type: ignore[assignment]
        for case in cases(shipped):
            if "raw" in case:
                data = str(case["raw"]).encode("utf-8")
            elif "raw_pad" in case:
                data = raw + b" " * int(case["raw_pad"])
            else:
                data = json.dumps(edited(shipped, case["edits"]), ensure_ascii=False).encode("utf-8")
            (folder / "ready-campaigns.json").write_bytes(data)
            try:
                catalog = ready_campaigns.ready_campaign_catalog()
                payload = json.loads(json.dumps(catalog))
                outcome: dict[str, object] = (
                    {"ok": payload} if case["name"] == "shipped"
                    else {"ok_sha256": hashlib.sha256(canonical_json_bytes(payload)).hexdigest()}
                )
            except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
                outcome = {"error": type(error).__name__, "message": str(error)}
            recorded.append({**case, "outcome": outcome})
    record = {"python": ".".join(map(str, sys.version_info[:2])), "cases": recorded}
    (OUT / "catalog_vectors.json").write_bytes(
        (json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
