"""Function-level vectors for the Plan readers: lineage, chapter markers, endings and seeding.

Run with the snapshot's own interpreter, from the repository root, after
`generate_session_page_runs.py` (whose seed is the base here):

    .venv/Scripts/python.exe -B crates/sylvia-store/tests/vectors/generate_plan_reader_vectors.py

Python's startup verifier admits only reviewed sequels and well-formed Plans, so these cases do
not go through the store. Each case starts from a base database, applies its SQL with foreign
keys off, and calls Python's module functions directly on that connection:
`sqlite_sequel.adventure_rows`, `sqlite_continuity.read_act_markers`, `read_campaign_ending(s)`,
`seed_initial_state` and `apply_migration`. The bases are the session-page seed (`story`) and a
schema-8 database made by `generate_schema_fixtures.build` (`v8`, written here as
`plan_readers/v8.sqlite3.gz`). After a call that writes, the outcome is the continuity records it
left. `tests/plan_readers.rs` replays every case on the Rust functions. Reference tooling only.
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
sys.path.insert(0, str(HERE))

from generate_schema_fixtures import build  # noqa: E402

from sylvia_gm.infrastructure import sqlite_continuity, sqlite_sequel  # noqa: E402

OUT = HERE / "plan_readers"
STORY, DM = "campaign-story", "dm-1"
LOCK = "(SELECT package_lock_id FROM campaign_plans WHERE plan_id = 'plan-1')"


def dumps(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def plan(plan_id: str, content: object, revision: int, campaign: str = STORY, lock: str = LOCK) -> str:
    return (
        "INSERT INTO campaign_plans VALUES ("
        f"{quote(plan_id)}, {quote(campaign)}, {lock}, 'builder-1', '{'c' * 64}', "
        f"{quote(content if isinstance(content, str) else dumps(content))}, '[\"ev-plan\"]', 'valid', "
        f"'accepted', {revision}, {revision + 100}, '{'d' * 64}')"
    )


def message(sequence: int, message_id: str, kind: str, content: object, canonical: int | None = None) -> str:
    return (
        "INSERT INTO agent_session_messages VALUES ("
        f"'{DM}', {sequence}, {quote(message_id)}, {quote(kind)}, {quote(dumps(content))}, "
        f"{'NULL' if canonical is None else canonical})"
    )


def record(sequence: int, kind: str, record_id: str, payload: object, basis: str, operation: str | None) -> str:
    return (
        "INSERT INTO campaign_continuity_records VALUES ("
        f"'{STORY}', {sequence}, {quote('entry-' + str(sequence))}, {quote(record_id)}, {quote(kind)}, 'active', "
        f"{quote(payload if isinstance(payload, str) else dumps(payload))}, {quote(basis)}, "
        f"{'NULL' if operation is None else quote(operation)}, 50)"
    )


def update_plan_1(campaign_plan: object) -> str:
    """Replace plan-1's `campaign_plan`."""
    return update_plan_1_content({"campaign_plan": campaign_plan})


def update_plan_1_content(content: object) -> str:
    return f"UPDATE campaign_plans SET content_json = {quote(dumps(content))} WHERE plan_id = 'plan-1'"


def story_plan(**changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "title": "The Drowned Bell",
        "bindings": {"ruleset": None, "campaign_setting": None},
        "structure": [{"id": "act-1", "objectives": ["obj-1"]}, {"id": "act-2"}],
        "ending": {"mode": "finite", "outcomes": [
            {"id": "end-quiet", "open_world": False, "after_story": None, "requires_all_acts": True},
            {"id": "end-loud", "open_world": True, "after_story": "The town remembers.", "requires_all_acts": False}]},
    }
    base.update(changes)
    for key in [key for key, value in base.items() if value is None and key != "bindings"]:
        del base[key]
    return base


SEQUEL = {
    "campaign_plan": {
        "title": "The Second Tide",
        "structure": [{"id": "act-3"}, {"id": "act-4"}],
        "ending": {"mode": "finite", "outcomes": [
            {"id": "end-open", "open_world": True, "after_story": "The sea keeps its bell.",
             "requires_all_acts": False}]},
    },
    "sequel": {"previous_plan_id": "plan-1"},
}
SEQUEL_PLAY = [
    plan("plan-2", SEQUEL, 2),
    message(8, "u-4", "user", {"text": "I follow the second tide."}),
    message(9, "canonical-99", "canonical_reference", {"event_type": "talk", "safe_payload": {"text": "On."},
                                                       "visibility": "player"}, 99),
    record(20, "scene", "current_scene", {"act_id": "act-3"}, '["u-4"]', "op-6"),
    record(21, "plan_progress", "ending:end-open", {"plan_item_id": "ending:end-open", "status": "resolved"},
           '["u-4"]', "op-7"),
]
READERS = [
    {"fn": "read_act_markers"}, {"fn": "read_campaign_ending"}, {"fn": "read_campaign_endings"},
    {"fn": "adventure_rows"},
]


def chain(length: int) -> list[str]:
    """Plans 2..length, each the sequel of the one before."""
    return [plan(f"plan-{n}", {"campaign_plan": {"structure": [{"id": f"act-x{n}"}]},
                                "sequel": {"previous_plan_id": f"plan-{n - 1}"}}, n)
            for n in range(2, length + 1)]


def seed(campaign: str, plan_value: object, revision: int = 7) -> dict[str, object]:
    return {"fn": "seed_initial_state", "campaign_id": campaign, "plan": plan_value, "admitted_revision": revision}


FULL_PLAN = {
    "bindings": {"ruleset": None, "campaign_setting": None},
    "structure": ["note", {"title": "no id"}, {"id": "act-1"}],
    "starting_location": {"id": "harbour", "name": "The harbour"},
    "initial_state": {
        "entities": [{"id": "keeper", "kind": "npc", "source_ref": {"id": "keeper", "path": "entities/keeper"},
                      "state": {"name": "The Keeper", "known_by": ["player"], "mood": "wary"}}],
        "facts": ["The bell rang.", {"fact": "Known two ways.", "known_by": ["player", "gm", 3]},
                  {"fact": "Odd knowledge.", "known_by": "player", "id": 5}, {"nofact": 1}, 4,
                  {"fact": "GM only.", "id": "fact-gm", "known_by": ["gm"]}],
    },
}

CASES: list[dict[str, object]] = [
    {"name": "story", "base": "story", "sql": [], "calls": READERS + [
        {"fn": "adventure_rows", "campaign_id": "campaign-build"},
        {"fn": "adventure_rows", "campaign_id": "campaign-missing"}]},
    {"name": "sequel", "base": "story", "sql": SEQUEL_PLAY, "calls": READERS},
    {"name": "sequel_choice", "base": "story", "sql": SEQUEL_PLAY + [
        message(10, "u-5", "user", {"campaign_ending_choice": {"ending_id": "end-open", "choice": 3}})],
     "calls": READERS},
    {"name": "lineage_self", "base": "story",
     "sql": [plan("plan-2", {"campaign_plan": {}, "sequel": {"previous_plan_id": "plan-2"}}, 2)], "calls": READERS},
    {"name": "lineage_reordered", "base": "story",
     "sql": [plan("plan-2", {"campaign_plan": {}}, 2),
             update_plan_1_content({"campaign_plan": {}, "sequel": {"previous_plan_id": "plan-2"}}),
             "UPDATE campaign_plans SET plan_revision = 3 WHERE plan_id = 'plan-1'"],
     "calls": [{"fn": "adventure_rows"}]},
    {"name": "lineage_missing_key", "base": "story",
     "sql": [plan("plan-2", {"campaign_plan": {}, "sequel": {}}, 2)], "calls": [{"fn": "adventure_rows"}]},
    {"name": "lineage_list_identity", "base": "story",
     "sql": [plan("plan-2", {"campaign_plan": {}, "sequel": {"previous_plan_id": ["plan-1"]}}, 2)],
     "calls": [{"fn": "adventure_rows"}]},
    {"name": "lineage_number_identity", "base": "story",
     "sql": [plan("plan-2", {"campaign_plan": {}, "sequel": {"previous_plan_id": 1}}, 2)],
     "calls": [{"fn": "adventure_rows"}]},
    {"name": "lineage_at_bound", "base": "story", "sql": chain(32), "calls": READERS},
    {"name": "lineage_over_bound", "base": "story", "sql": chain(33), "calls": [{"fn": "adventure_rows"}]},
    {"name": "content_not_object", "base": "story",
     "sql": ["UPDATE campaign_plans SET content_json = '[1,2]' WHERE plan_id = 'plan-1'"], "calls": READERS},
    {"name": "content_invalid_json", "base": "story",
     "sql": ["UPDATE campaign_plans SET content_json = '{\"campaign_plan\": ' WHERE plan_id = 'plan-1'"],
     "calls": READERS},
    {"name": "plan_not_object", "base": "story", "sql": [update_plan_1("a story")], "calls": READERS},
    {"name": "structure_string", "base": "story",
     "sql": [update_plan_1(story_plan(structure="act"))], "calls": READERS},
    {"name": "structure_object", "base": "story",
     "sql": [update_plan_1(story_plan(structure={"act-1": 1, "act-2": 2}))], "calls": READERS},
    {"name": "structure_number", "base": "story", "sql": [update_plan_1(story_plan(structure=5))], "calls": READERS},
    {"name": "structure_duplicates", "base": "story",
     "sql": [update_plan_1(story_plan(structure=[{"id": "act-1"}, {"id": "act-1"}, "x", {"id": "act-2"}, {"id": 4}]))],
     "calls": READERS},
    {"name": "ending_missing_title", "base": "story", "sql": [update_plan_1(story_plan(title=None))], "calls": READERS},
    {"name": "ending_ongoing", "base": "story",
     "sql": [update_plan_1(story_plan(ending={"mode": "ongoing"}))], "calls": READERS},
    {"name": "ending_no_anchor", "base": "story",
     "sql": ["UPDATE campaign_continuity_records SET basis_message_ids_json = '[\"nope\"]' "
             "WHERE record_id = 'ending:end-quiet'"], "calls": READERS},
    {"name": "ending_progress_elsewhere", "base": "story", "sql": [
        record(30, "plan_progress", "other", {"plan_item_id": "ending:end-loud", "status": "resolved"}, '["u-2"]', "op-a"),
        record(31, "plan_progress", "ending:end-loud", {"plan_item_id": "zzz", "status": "open"}, '["u-2"]', "op-b")],
     "calls": READERS},
    {"name": "ending_unmet_requirements", "base": "story",
     "sql": ["UPDATE campaign_continuity_records SET status = 'retired' WHERE record_id = 'obj-1'"], "calls": READERS},
    {"name": "markers_odd_rows", "base": "story", "sql": [
        record(40, "scene", "current_scene", {"act_id": 2}, '["u-2"]', "op-c"),
        record(41, "plan_progress", "act-9", {"plan_item_id": "act-9", "status": "resolved"}, '["u-2"]', "op-d"),
        record(42, "scene", "current_scene", {"act_id": "act-2"}, "[not json", "op-e")],
     "calls": [{"fn": "read_act_markers"}]},
    {"name": "markers_bad_basis", "base": "story", "sql": [
        "DELETE FROM campaign_continuity_records WHERE kind = 'plan_progress'",
        "UPDATE campaign_continuity_records SET basis_message_ids_json = '[not json' WHERE operation_id = 'op-3'"],
     "calls": [{"fn": "read_act_markers"}]},
    {"name": "seeding", "base": "story", "sql": [], "calls": [
        seed("seed-full", FULL_PLAN), seed("seed-full", {"starting_location": {"id": "elsewhere"}}),
        seed("seed-empty", {}),
        seed("seed-string-structure", {"structure": "act", "starting_location": {"name": "No id"}}),
        seed("seed-facts-string", {"initial_state": {"facts": "abc"}, "starting_location": {}}),
        seed("seed-state-list", {"initial_state": ["facts"]}),
        seed("seed-duplicate-ids", {"initial_state": {"facts": [{"fact": "a", "id": "same"}, {"fact": "b", "id": "same"}]}}),
        seed("seed-entity-no-kind", {"bindings": {"ruleset": None, "campaign_setting": None}, "initial_state": {
            "entities": [{"id": "x", "state": {"name": "X"}}], "facts": ["after"]}}),
        seed("seed-bindings-partial", {"bindings": {"ruleset": None}, "initial_state": {
            "entities": [{"id": "x", "kind": "npc", "state": {"name": "X"}}]}}),
    ]},
    {"name": "migration", "base": "v8", "sql": [
        "INSERT INTO campaign_plans VALUES ('m-a1', 'm-a', 'lock', 'builder', 'c', '{\"note\":1}', '[]', 'valid', 'accepted', 1, 1, 'd')",
        "INSERT INTO campaign_plans VALUES ('m-a2', 'm-a', 'lock', 'builder', 'c', " + quote(dumps({"campaign_plan": FULL_PLAN})) + ", '[]', 'valid', 'accepted', 2, 2, 'd')",
        "INSERT INTO campaign_plans VALUES ('m-b1', 'm-b', 'lock', 'builder', 'c', '{\"campaign_plan\":\"text\"}', '[]', 'valid', 'accepted', 1, 4, 'd')",
        "INSERT INTO campaign_plans VALUES ('m-c1', 'm-c', 'lock', 'builder', 'c', '{\"campaign_plan\":', '[]', 'valid', 'accepted', 1, 5, 'd')",
        "INSERT INTO campaign_plans VALUES ('m-d2', 'm-d', 'lock', 'builder', 'c', " + quote(dumps({"campaign_plan": {"initial_state": {"facts": ["d2"]}}})) + ", '[]', 'valid', 'accepted', 2, 9, 'd')",
        "INSERT INTO campaign_plans VALUES ('m-d1', 'm-d', 'lock', 'builder', 'c', " + quote(dumps({"campaign_plan": {"initial_state": {"facts": ["d1"]}}})) + ", '[]', 'valid', 'accepted', 1, 8, 'd')",
     ], "calls": [{"fn": "apply_migration"}]},
]


def records(connection: sqlite3.Connection) -> list[list[object]]:
    return [list(row) for row in connection.execute(
        "SELECT campaign_id, sequence, entry_id, record_id, kind, status, payload_json, "
        "basis_message_ids_json, operation_id, admitted_revision FROM campaign_continuity_records "
        "ORDER BY campaign_id, sequence")]


def call(connection: sqlite3.Connection, item: dict[str, object]) -> dict[str, object]:
    fn = str(item["fn"])
    campaign = str(item.get("campaign_id", STORY))
    try:
        if fn == "adventure_rows":
            value: object = [str(row["plan_id"]) for row in sqlite_sequel.adventure_rows(connection, campaign)]
        elif fn == "read_act_markers":
            value = [dict(marker) for marker in sqlite_continuity.read_act_markers(connection, DM, campaign)]
        elif fn == "read_campaign_ending":
            ending = sqlite_continuity.read_campaign_ending(connection, DM, campaign)
            value = None if ending is None else dict(ending)
        elif fn == "read_campaign_endings":
            value = [dict(ending) for ending in sqlite_continuity.read_campaign_endings(connection, DM, campaign)]
        elif fn == "seed_initial_state":
            try:
                sqlite_continuity.seed_initial_state(
                    connection, campaign_id=campaign, plan=item["plan"],  # type: ignore[arg-type]
                    admitted_revision=int(item["admitted_revision"]))  # type: ignore[arg-type]
            except Exception as error:  # noqa: BLE001 - rows written before the failure stay
                return {"error": type(error).__name__, "message": str(error), "records": records(connection)}
            value = records(connection)
        elif fn == "apply_migration":
            sqlite_continuity.apply_migration(connection)
            value = records(connection)
        else:
            raise AssertionError(fn)
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "message": str(error)}
    return {"ok": json.loads(json.dumps(value))}


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    with tempfile.TemporaryDirectory(dir=HERE, ignore_cleanup_errors=True) as scratch:
        (Path(scratch) / "v8").mkdir()
        v8 = build(Path(scratch) / "v8", "v8").read_bytes()
        (OUT / "v8.sqlite3.gz").write_bytes(gzip.compress(v8, mtime=0))
        bases = {"story": gzip.decompress((HERE / "session_pages" / "seed.sqlite3.gz").read_bytes()), "v8": v8}
        results = []
        for case in CASES:
            database = Path(scratch) / f"{case['name']}.sqlite3"
            database.write_bytes(bases[str(case["base"])])
            connection = sqlite3.connect(database, isolation_level=None)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA foreign_keys = OFF")
                for statement in case["sql"]:  # type: ignore[union-attr]
                    connection.execute(statement)
                outcomes = [call(connection, item) for item in case["calls"]]  # type: ignore[union-attr]
            finally:
                connection.close()
            results.append({**case, "outcomes": outcomes})
    record_ = {"python": sys.version.split()[0], "cases": results}
    (OUT / "plan_readers.json").write_bytes((json.dumps(record_, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
