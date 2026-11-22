"""Generate reference vectors for sylvia_app::builder_session_migration.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_builder_migration_vectors.py

The bases are lane 5's legacy Builder fixtures (`crates/sylvia-store/tests/vectors/legacy_builder`:
an open, a returned and a stopped legacy character chat, with their storage files). For each,
Python's `migrate_legacy_builder_chats` runs on a fresh copy with three catalogs:
- the owner's own (`_campaign_creation_catalog`, recorded here);
- an empty catalog;
- one with only a ruleset row.
Then the open base runs with stored message contents changed (`MUTATIONS`, applied with SQL as
`(session, message, edit)`): every link purpose, transcript ids that are missing, repeated,
empty, unhashable or not a list, unknown and missing triggers and parents, interview states with
answers that are not a mapping or name a missing message, Ruleset answers whose candidate is
unknown or whose value is not a mapping, and names that are empty, long or not text.
The recorded outcome holds the migrated ids, the store calls the migration made (retain and
detach, with the full launch context) and the storage tree afterwards. The tree skips what lane
5's store vectors skip: the database, the owner's pre-migration copies, and the catalog's starters.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from sylvia_gm.application import builder_session_migration  # noqa: E402
from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402
from sylvia_gm.domain.package_library import canonical_json_bytes  # noqa: E402
from sylvia_gm.interfaces.owner_process import _campaign_creation_catalog  # noqa: E402

OUTPUT = Path(__file__).with_name("builder_migration_vectors.json")
FIXTURES = "crates/sylvia-store/tests/vectors/legacy_builder"
NL = chr(10)


def thaw(value):
    return json.loads(canonical_json_bytes(value))


def skipped(relative):
    return relative.startswith(("authority.sqlite3", "migration-sources", "bundled-srd-starters"))


def tree(root):
    entries = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if skipped(relative):
            continue
        entries.append([relative, "dir", None] if path.is_dir()
                       else [relative, "file", hashlib.sha256(path.read_bytes()).hexdigest()])
    return entries


def prepare(work, name, case, bases):
    root = work / case
    root.mkdir()
    (root / "authority.sqlite3").write_bytes(gzip.decompress((ROOT / FIXTURES / f"base-{name}.sqlite3.gz").read_bytes()))
    for relative, text in bases[name]["files"].items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_bytes(text.encode("utf-8"))
    return root


LINK = ("old-character", "subflow-link-a72491647b2cfcd1afb38adc")
STATE = ("source-interview", "guided-state-d37b8d6d0c3772c650343449")
RULESET = ("source-interview", "guided-answer-7768e81d1c9400ad55fe385d")
SETTING = ("source-interview", "guided-answer-fdf325ad2d04139e30246c08")
PLAN = ("source-interview", "guided-answer-2315e64accc4db169430b3c3")
INTRO = "guided-intro-b0e839ee734b4e6692d72706"


def setting(key, value):
    def edit(content):
        content[key] = value
    return edit


def removing(key):
    return lambda content: content.pop(key)


def extending(key, *items):
    return lambda content: content[key].extend(items)


def answer(step, message_id):
    return lambda content: content["answers"].update({step: message_id})


def keeping(count):
    return lambda content: content.update(transcript_message_ids=content["transcript_message_ids"][:count])


MUTATIONS = {
    **{f"purpose_{purpose}": [(*LINK, setting("purpose", purpose))]
       for purpose in ("ruleset", "setting", "plan", "companion")},
    "transcript_ghost": [(*LINK, extending("transcript_message_ids", "ghost-message"))],
    "transcript_repeated": [(*LINK, extending("transcript_message_ids", INTRO,
                                              "subflow-trigger-a72491647b2cfcd1afb38adc"))],
    "transcript_empty": [(*LINK, setting("transcript_message_ids", []))],
    "transcript_unhashable": [(*LINK, extending("transcript_message_ids", [1]))],
    "transcript_mapping_item": [(*LINK, extending("transcript_message_ids", {"a": 1}))],
    "transcript_number_item": [(*LINK, extending("transcript_message_ids", 7))],
    "transcript_text": [(*LINK, setting("transcript_message_ids", INTRO))],
    "transcript_number": [(*LINK, setting("transcript_message_ids", 5))],
    "transcript_null": [(*LINK, setting("transcript_message_ids", None))],
    "transcript_keys": [(*LINK, setting("transcript_message_ids", {INTRO: 1}))],
    "transcript_missing": [(*LINK, removing("transcript_message_ids"))],
    "transcript_first_three": [(*LINK, keeping(3))],
    "transcript_first_seven": [(*LINK, keeping(7))],
    "trigger_missing": [(*LINK, removing("trigger_message_id"))],
    "trigger_unknown": [(*LINK, setting("trigger_message_id", "nobody"))],
    "trigger_null": [(*LINK, setting("trigger_message_id", None))],
    "trigger_untexted": [(*LINK, setting("trigger_message_id", "guided-state-b0e839ee734b4e6692d72706"))],
    "parent_unknown": [(*LINK, setting("parent_session_id", "nobody"))],
    "parent_missing": [(*LINK, removing("parent_session_id"))],
    "parent_number": [(*LINK, setting("parent_session_id", 12))],
    "state_answers_list": [(*STATE, setting("answers", ["ruleset"]))],
    "state_answers_null": [(*STATE, setting("answers", None))],
    "state_answers_text": [(*STATE, setting("answers", "ruleset"))],
    "state_answers_missing": [(*STATE, removing("answers"))],
    "state_answer_ghost": [(*STATE, answer("extra", "ghost"))],
    "state_answer_number": [(*STATE, answer("extra", 7))],
    "state_answer_repeat": [(*STATE, answer("extra", "guided-answer-7768e81d1c9400ad55fe385d"))],
    "state_revision_missing": [(*STATE, removing("construction_revision"))],
    "state_revision_text": [(*STATE, setting("construction_revision", "four"))],
    "state_other_kind": [(*STATE, setting("kind", "something_else"))],
    "ruleset_unknown_candidate": [(*RULESET, setting("interview_value", {"candidate_id": "candidate-missing"}))],
    "ruleset_number_candidate": [(*RULESET, setting("interview_value", {"candidate_id": 12}))],
    "ruleset_null_candidate": [(*RULESET, setting("interview_value", {"candidate_id": None}))],
    "ruleset_no_candidate": [(*RULESET, setting("interview_value", {}))],
    "ruleset_logical_name": [(*RULESET, setting("interview_value", {"ruleset_id": "sylvia-gm.conformance.signal"}))],
    "ruleset_value_list": [(*RULESET, setting("interview_value", ["candidate-install-character-signal"]))],
    "ruleset_value_null": [(*RULESET, setting("interview_value", None))],
    "ruleset_value_missing": [(*RULESET, removing("interview_value"))],
    "setting_unknown": [(*SETTING, setting("interview_value", {"setting_id": "nowhere"}))],
    "names_empty": [(*RULESET, setting("text", "")), (*SETTING, setting("text", "")), (*PLAN, setting("text", ""))],
    "names_plan_falsy": [(*PLAN, setting("text", 0)), (*SETTING, setting("text", "Signal Setting"))],
    "names_plan_number": [(*PLAN, setting("text", 5))],
    "names_plan_missing": [(*PLAN, removing("text"))],
    "names_long": [(*PLAN, setting("text", "x" * 100 + " " * 30))],
    "names_dense": [(*PLAN, setting("text", "Dense" + chr(0x2728) * 195))],
    "names_wide": [(*PLAN, setting("text", (chr(0xdc) + "n" + chr(0xef) + "c" + chr(0xf8) + "d" + chr(0xe9)
                                              + " " + chr(0x2728) + " ") * 14))],
    "names_padded": [(*PLAN, setting("text", "  Padded plan  "))],
}


def mutate(root, edits):
    """Apply `edits`; the stored contents they leave, for the Rust side to write back."""
    stored_edits = []
    connection = sqlite3.connect(root / "authority.sqlite3")
    try:
        with connection:
            for session_id, message_id, edit in edits:
                (stored,) = connection.execute(
                    "SELECT content_json FROM agent_session_messages WHERE session_id = ? AND message_id = ?",
                    (session_id, message_id)).fetchone()
                content = json.loads(stored)
                edit(content)
                text = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                connection.execute(
                    "UPDATE agent_session_messages SET content_json = ? WHERE session_id = ? AND message_id = ?",
                    (text, session_id, message_id))
                stored_edits.append([session_id, message_id, text])
    finally:
        connection.close()
    return stored_edits


def run(root, catalog):
    calls = []
    original_retain = builder_session_migration.retain_legacy_workspace

    def recording_retain(storage_root, *, campaign_id, session_id):
        calls.append(["retain", campaign_id, session_id])
        return original_retain(storage_root, campaign_id=campaign_id, session_id=session_id)

    builder_session_migration.retain_legacy_workspace = recording_retain
    try:
        with AuthorityOwner.open(root / "authority.sqlite3", "migrate") as owner:
            original_detach = owner.detach_legacy_builder_chat

            def recording_detach(**kwargs):
                calls.append(["detach", kwargs["session_id"], kwargs["expected_message_count"],
                              thaw(kwargs["launch_context"]), kwargs["display_name"]])
                return original_detach(**kwargs)

            owner.detach_legacy_builder_chat = recording_detach
            if catalog is None:
                catalog = thaw(_campaign_creation_catalog(root, owner))
            try:
                result = ["ok", list(builder_session_migration.migrate_legacy_builder_chats(owner, root, catalog))]
            except Exception as error:  # every class is behaviour
                result = [type(error).__name__, str(error), getattr(error, "code", None)]
    finally:
        builder_session_migration.retain_legacy_workspace = original_retain
    return catalog, {"result": result, "calls": calls, "tree": tree(root)}


def main() -> None:
    bases = json.loads((ROOT / FIXTURES / "store_runs.json").read_text(encoding="utf-8"))["bases"]
    cases = []
    with tempfile.TemporaryDirectory() as scratch:
        work = Path(scratch)
        catalogs = {"empty": []}
        for name in bases:
            root = prepare(work, name, f"own-{name}", bases)
            catalog, recorded = run(root, None)
            catalogs[f"own-{name}"] = catalog
            cases.append({"base": name, "catalog": f"own-{name}", **recorded})
            ruleset_rows = [row for row in catalog if row.get("kind") == "ruleset"][:1]
            assert ruleset_rows
            catalogs[f"ruleset_only-{name}"] = ruleset_rows
            for label in ("empty", f"ruleset_only-{name}"):
                root = prepare(work, name, f"{label}-{name}", bases)
                _, recorded = run(root, catalogs[label])
                cases.append({"base": name, "catalog": label, **recorded})
        for label, edits in MUTATIONS.items():
            root = prepare(work, "open", f"mutated-{label}", bases)
            stored_edits = mutate(root, edits)
            _, recorded = run(root, catalogs["own-open"])
            cases.append({"base": "open", "catalog": "own-open", "mutation": label, "edits": stored_edits, **recorded})
    vectors = {"python": ".".join(sys.version.split()[0].split(".")[:2]), "fixtures": FIXTURES,
               "catalogs": catalogs, "cases": cases}
    OUTPUT.write_text(json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + NL, encoding="utf-8", newline=NL)
    for case in cases:
        detach = [call for call in case["calls"] if call[0] == "detach"]
        print(case["base"], case["catalog"], case.get("mutation"), ascii(case["result"])[:110],
              [call[0] for call in case["calls"]], [(call[3]["reference_error"] or "")[:12] for call in detach],
              ascii([call[4] for call in detach])[:60])


if __name__ == "__main__":
    main()
