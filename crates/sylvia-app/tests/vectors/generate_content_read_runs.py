"""The GM context's content reads: what Python's owner answers for every campaign and session.

Run with the snapshot's own interpreter, from the repository root, after the store generators
whose profiles are the bases:

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_content_read_runs.py

Three Python-made profiles: the Riichi participant profile (`cast`: a DM campaign and a Builder
campaign on Riichi Mahjong, whose runtime declares an isolated participant and whose package
carries a GM operating guide), the campaign-reads profile (`campaigns`: No Ruleset DM campaigns,
a Builder draft and a synthetic-package campaign) and the session-page profile
(`session_pages`: the story campaign with an accepted plan and its DM session).

For every campaign: `ruleset_gm_operating_guide`, `catalog` (recorded by tool names; the Rust
port builds no tool yet, so a non-empty one is refused there) and `model_context_owner`. For
every session, and one that does not exist: `campaign_knowledge_catalog`. And
`campaign_knowledge_catalog_for_plan` for several plans on the story and Riichi campaigns:
empty, legacy and inline secrets (with entries Python skips), a Setting reference with no
Setting knowledge, a binding to a locked package without a knowledge file, a stale binding and
an invalid one. Values are plain JSON; a failure is its class, code and message. It writes
`content_reads/content_read_runs.json`; `tests/content_reads.rs` replays every read on the Rust
store. Reference tooling only.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[3]
sys.path.insert(0, str(REPOSITORY / "src"))

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402

OUT = HERE / "content_reads"
STORE = REPOSITORY / "crates" / "sylvia-store" / "tests" / "vectors"
BASES = {
    "cast": STORE / "stored_authority" / "cast.sqlite3.gz",
    "campaigns": STORE / "campaigns" / "seed.sqlite3.gz",
    "session_pages": STORE / "session_pages" / "seed.sqlite3.gz",
}

SECRETS = [
    "The lighthouse keeper is the smuggler.",
    {"id": "secret-1", "fact": "The vault opens at dawn.", "known_by": ["npc-mara"], "reveal": {"when": "act-2"}},
    5,
    {"fact": "no identity"},
    {"id": 7, "fact": "numeric identity"},
]


def plans(lock: list[tuple[str, str, str]]) -> list[dict[str, object]]:
    found = [{"name": "empty", "plan": {}}, {"name": "secrets", "plan": {"secrets": SECRETS}},
             {"name": "secrets_not_a_list", "plan": {"secrets": "one secret"}},
             {"name": "setting_reference_unavailable",
              "plan": {"secrets": [{"id": "fact-1", "setting_fact_id": "fact-1"}]}},
             {"name": "binding_invalid", "plan": {"bindings": {"campaign_setting": {"id": "x", "version": 1}}}},
             {"name": "binding_not_a_mapping", "plan": {"bindings": {"campaign_setting": "x"}}},
             {"name": "binding_stale", "plan": {"bindings": {"campaign_setting": {
                 "id": "pneuma.unknown-setting", "version": "1.0.0", "content_sha256": "0" * 64}}}}]
    for package_id, version, sha256 in lock:
        found.append({"name": f"binding_locked_{package_id}", "plan": {
            "bindings": {"campaign_setting": {"id": package_id, "version": version, "content_sha256": sha256}},
            "secrets": SECRETS}})
    return found


def plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [plain(item) for item in value]
    return value


def outcome(read) -> dict[str, object]:  # noqa: ANN001
    try:
        return {"ok": plain(read())}
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    runs: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        for base, source in BASES.items():
            database = Path(scratch) / base / "authority.sqlite3"
            database.parent.mkdir()
            database.write_bytes(gzip.decompress(source.read_bytes()))
            connection = sqlite3.connect(database)
            campaigns = [row[0] for row in connection.execute("SELECT campaign_id FROM campaigns ORDER BY campaign_id")]
            sessions = [row[0] for row in connection.execute("SELECT session_id FROM agent_sessions ORDER BY session_id")]
            connection.close()
            with AuthorityOwner.open(database, "content-reads") as owner:
                for campaign_id in campaigns:
                    runs.append({"base": base, "read": "ruleset_gm_operating_guide", "campaign_id": campaign_id,
                                 "outcome": outcome(lambda: owner.ruleset_gm_operating_guide(campaign_id))})
                    runs.append({"base": base, "read": "catalog", "campaign_id": campaign_id,
                                 "outcome": outcome(lambda: [tool.name for tool in owner.catalog(campaign_id)])})
                    runs.append({"base": base, "read": "model_context_owner", "campaign_id": campaign_id,
                                 "outcome": outcome(lambda: owner.model_context_owner(campaign_id))})
                for session_id in [*sessions, "session-missing"]:
                    runs.append({"base": base, "read": "campaign_knowledge_catalog", "session_id": session_id,
                                 "outcome": outcome(lambda: owner.campaign_knowledge_catalog(session_id))})
                for campaign_id in campaigns:
                    lock = [(entry.package_id, entry.version, entry.sha256)
                            for entry in owner.snapshot(campaign_id).package_lock.entries]
                    for case in plans(lock):
                        runs.append({"base": base, "read": "campaign_knowledge_catalog_for_plan",
                                     "campaign_id": campaign_id, "case": case["name"], "plan": case["plan"],
                                     "outcome": outcome(lambda: owner.campaign_knowledge_catalog_for_plan(
                                         campaign_id, case["plan"]))})
    record = {"python": sys.version.split()[0], "runs": runs}
    (OUT / "content_read_runs.json").write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
