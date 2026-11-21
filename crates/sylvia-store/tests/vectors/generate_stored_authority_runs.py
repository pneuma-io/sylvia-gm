"""Vectors for `_verify_stored_authority`: damaged profiles and what Python's startup says.

Run with the snapshot's own interpreter, from the repository root, after
`generate_package_runs.py` and `generate_session_page_runs.py` (their seeds are the bases):

    .venv/Scripts/python.exe -B crates/sylvia-store/tests/vectors/generate_stored_authority_runs.py

Each case copies a base profile (`riichi`: a new profile with the bundled Riichi closure;
`story`: the session-page profile with a played DM campaign, an action, its state and its
namespace owner), applies its SQL through a second connection with foreign keys off, as damage
would arrive, and opens the profile with `_open_for_application_owner`, which runs the startup
verifiers. It writes `stored_authority/stored_authority_runs.json`; `tests/stored_authority.rs`
replays every case on the Rust store. Reference tooling only.
"""

from __future__ import annotations

import gzip
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3] / "src"))

from sylvia_gm.infrastructure import sqlite_authority as a  # noqa: E402

OUT = HERE / "stored_authority"
BASES = {"riichi": HERE / "packages" / "seed_riichi.sqlite3.gz", "story": HERE / "session_pages" / "seed.sqlite3.gz"}
PRIMARY = "pneuma.riichi-mahjong"

CASES: list[dict[str, object]] = [
    {"name": "riichi_untouched", "base": "riichi", "sql": []},
    {"name": "story_untouched", "base": "story", "sql": []},
    # Held back from the package vectors until this verifier landed.
    {"name": "unbound_campaign", "base": "riichi", "sql": [
        "INSERT INTO campaigns (campaign_id, display_name, lifecycle, writer, revision, lock_hash, current_lock_id) "
        "VALUES ('campaign-unbound', 'Unbound', 'active', 'dm', 0, 'unbound', NULL)"]},
    {"name": "unbound_builder_without_interview", "base": "riichi", "sql": [
        "INSERT INTO campaigns (campaign_id, display_name, lifecycle, writer, revision, lock_hash, current_lock_id) "
        "VALUES ('campaign-unbound', 'Unbound', 'active', 'builder', 0, 'unbound', NULL)"]},
    {"name": "activation_row", "base": "riichi", "sql": [
        "INSERT INTO package_activations (activation_id, campaign_id, candidate_id, old_lock_id, new_lock_id, "
        "migration_id, status, resulting_revision, payload_sha256, result_json) VALUES ('activation-x', "
        "'campaign-x', 'candidate-install-1', 'lock-a', 'lock-b', 'none', 'committed', 1, 'p', '{}')"]},
    {"name": "package_sha_uppercase", "base": "riichi", "sql": [
        "UPDATE packages SET sha256 = upper(sha256) WHERE package_id = '" + PRIMARY + "'"]},
    {"name": "package_capabilities_object", "base": "riichi", "sql": [
        "UPDATE packages SET capabilities_json = '{\"a\":1}' WHERE package_id = '" + PRIMARY + "'"]},
    {"name": "package_capabilities_numbers", "base": "riichi", "sql": [
        "UPDATE packages SET capabilities_json = '[1]' WHERE package_id = '" + PRIMARY + "'"]},
    {"name": "package_capabilities_not_json", "base": "riichi", "sql": [
        "UPDATE packages SET capabilities_json = 'x' WHERE package_id = '" + PRIMARY + "'"]},
    {"name": "dependency_order_gap", "base": "riichi", "sql": [
        "UPDATE package_dependencies SET dependency_order = 5"]},
    {"name": "dependency_kind_blank", "base": "riichi", "sql": [
        "UPDATE package_dependencies SET dependency_kind = ' '"]},
    {"name": "campaign_name_padded", "base": "story", "sql": [
        "UPDATE campaigns SET display_name = ' Story' WHERE campaign_id = 'campaign-story'"]},
    {"name": "campaign_lock_hash", "base": "story", "sql": [
        "UPDATE campaigns SET lock_hash = '[]' WHERE campaign_id = 'campaign-story'"]},
    {"name": "event_attempt_blank", "base": "story", "sql": [
        "UPDATE canonical_events SET attempt_id = ' ' WHERE sequence = "
        "(SELECT MIN(sequence) FROM canonical_events WHERE attempt_id IS NOT NULL)"]},
    {"name": "action_payload_hash_uppercase", "base": "story", "sql": [
        "UPDATE actions SET payload_hash = upper(payload_hash)"]},
    {"name": "state_owner_missing", "base": "story", "bypass_triggers": True, "sql": [
        "DELETE FROM campaign_state_namespace_owners"]},
    {"name": "state_owner_without_state", "base": "story", "bypass_triggers": True, "sql": [
        "INSERT INTO campaign_state_namespace_owners SELECT campaign_id, 'other.namespace', owner_package_id "
        "FROM campaign_state_namespace_owners LIMIT 1"]},
    {"name": "state_owner_foreign", "base": "story", "bypass_triggers": True, "sql": [
        "UPDATE campaign_state_namespace_owners SET owner_package_id = 'other.package'"]},
    {"name": "state_trigger_dropped", "base": "story", "sql": [
        "DROP TRIGGER campaign_state_owner_immutable"]},
    {"name": "foreign_key_orphan", "base": "story", "sql": [
        "INSERT INTO turns (campaign_id, turn_id, status, created_revision) VALUES ('campaign-gone', 'turn-x', 'open', 0)"]},
]


def outcome(path: Path) -> dict[str, object]:
    try:
        store = a._open_for_application_owner(path)
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}
    store.close()
    return {"ok": None}


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    results = []
    with tempfile.TemporaryDirectory(dir=HERE, ignore_cleanup_errors=True) as scratch:
        for case in CASES:
            folder = Path(scratch) / str(case["name"])
            folder.mkdir()
            path = folder / "authority.sqlite3"
            path.write_bytes(gzip.decompress(BASES[str(case["base"])].read_bytes()))
            connection = sqlite3.connect(path, isolation_level=None)
            try:
                connection.execute("PRAGMA foreign_keys = OFF")
                statements = list(case["sql"])  # type: ignore[call-overload]
                if case.pop("bypass_triggers", False):
                    # Damage the ownership triggers forbid: drop them, then restore their exact SQL.
                    triggers = connection.execute(
                        "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' "
                        "AND tbl_name IN ('campaign_state', 'campaign_state_namespace_owners') ORDER BY name"
                    ).fetchall()
                    statements = ([f"DROP TRIGGER {name}" for name, _ in triggers] + statements
                                  + [sql for _, sql in triggers])
                for statement in statements:
                    connection.execute(statement)
            finally:
                connection.close()
            results.append({**case, "sql": statements, "outcome": outcome(path)})
    record = {"python": sys.version.split()[0], "cases": results}
    (OUT / "stored_authority_runs.json").write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
