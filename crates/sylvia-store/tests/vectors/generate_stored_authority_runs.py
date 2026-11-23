"""Vectors for `_verify_stored_authority`: damaged profiles and what Python's startup says.

Run with the snapshot's own interpreter, from the repository root, after
`generate_package_runs.py` and `generate_session_page_runs.py` (their seeds are the bases):

    .venv/Scripts/python.exe -B crates/sylvia-store/tests/vectors/generate_stored_authority_runs.py

Each case copies a base profile (`riichi`: a new profile with the bundled Riichi closure;
`story`: the session-page profile with a played DM campaign, an action, its state and its
namespace owner; `cast`: the Riichi profile with validated participants and an adaptation, for
`verify_f5_campaign_authority`; `recovered`: a new profile into which Python's owner recovered
the story campaign from its own portable export, for `verify_recovery_receipts`), applies its SQL through a second connection with foreign keys off, as damage
would arrive (CHECK constraints off), and opens the profile with `_open_for_application_owner`, which runs the startup
verifiers; on the `cast` base it also calls `verify_f5_campaign_authority` directly. It writes `stored_authority/stored_authority_runs.json`; `tests/stored_authority.rs`
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
BASES = {
    "riichi": HERE / "packages" / "seed_riichi.sqlite3.gz",
    "story": HERE / "session_pages" / "seed.sqlite3.gz",
    # A No Ruleset campaign confirmed, begun and completed, and a confirmed interview campaign.
    "constructed": HERE / "campaigns" / "seed.sqlite3.gz",
}


def payload_edit(connection: sqlite3.Connection, operation_id: str, column: str, change) -> str:  # noqa: ANN001
    """SQL replacing one operation's stored JSON with `change(value)`, as Python's store would
    write it: canonical text, and for the semantic payload its SHA-256 too. The SQL is recorded,
    so the Rust test replays the same bytes."""
    import hashlib

    from sylvia_gm.domain.package_library import canonical_json_bytes

    row = connection.execute(
        f"SELECT {column}_json FROM campaign_operations WHERE operation_id = ?", (operation_id,)).fetchone()
    value = change(json.loads(row[0]))
    text = a._dump(value).replace("'", "''")
    digest = (f", semantic_payload_sha256 = '{hashlib.sha256(canonical_json_bytes(value)).hexdigest()}'"
              if column == "semantic_payload" else "")
    return f"UPDATE campaign_operations SET {column}_json = '{text}'{digest} WHERE operation_id = '{operation_id}'"


def edited(value: dict, **changes: object) -> dict:
    """`value` with `changes` applied; a change to `DROP` removes the key."""
    result = dict(value)
    for key, item in changes.items():
        if item is DROP:
            result.pop(key, None)
        else:
            result[key] = item
    return result


DROP = object()


def children_base(folder: Path) -> bytes:
    """The story profile plus a completed Builder child that returned its evidence, made by
    Python's store (written as `stored_authority/children.sqlite3.gz`)."""
    from sylvia_gm.domain.authority import WriterRole
    from sylvia_gm.domain.session import ChildEvidence, SessionRecord, SessionStatus

    path = folder / "children" / "authority.sqlite3"
    path.parent.mkdir()
    path.write_bytes(gzip.decompress(BASES["story"].read_bytes()))
    store = a._open_for_application_owner(path)
    try:
        store.create_builder_child_session(SessionRecord(
            session_id="child-1", campaign_id="campaign-build", role=WriterRole.BUILDER,
            provider_id="synthetic.provider", model_id="synthetic.provider/model", status=SessionStatus.ACTIVE,
            parent_session_id="builder-2", selected_skill="BUILD RULESET", scope=("rules",)))
        store.return_builder_child_evidence(ChildEvidence(
            evidence_id="evidence-1", child_session_id="child-1", parent_session_id="builder-2",
            content_sha256="b" * 64, safe_summary="Rules drafted.", claims=("rules-ready",)))
    finally:
        store.close()
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    return path.read_bytes()


def lifecycle_base(folder: Path, name: str, kinds: tuple[str, ...]) -> bytes:
    """The story profile with the played campaign moved through `kinds` by Python's store
    (written as `stored_authority/<name>.sqlite3.gz`)."""
    import hashlib

    from sylvia_gm.application.authority import _campaign_operation_payload
    from sylvia_gm.domain.package_library import canonical_json_bytes

    path = folder / name / "authority.sqlite3"
    path.parent.mkdir()
    path.write_bytes(gzip.decompress(BASES["story"].read_bytes()))
    store = a._open_for_application_owner(path)
    try:
        revision = 8
        for index, kind in enumerate(kinds):
            values: dict[str, object] = {"campaign_id": "campaign-story"}
            designated = "dm-1" if kind == "archive_campaign" else None
            if kind in {"archive_campaign", "restore_campaign"}:
                values["designated_session_id"] = designated
            payload = _campaign_operation_payload(
                operation_id=f"life-{index}", operation_kind=kind, values=values)
            store.commit_lifecycle_operation(
                operation_id=f"life-{index}", operation_kind=kind, semantic_payload=payload,
                payload_sha256=hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
                campaign_id="campaign-story", expected_revision=revision, designated_session_id=designated)
            revision += 1
    finally:
        store.close()
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    return path.read_bytes()


def failed_base(folder: Path) -> bytes:
    """The story profile plus a failed action whose one consequence is the owner's player-action
    marker, committed by Python's store (written as `stored_authority/failed.sqlite3.gz`)."""
    from sylvia_gm.application.player_actions import PLAYER_ACTION_ORIGIN, player_control_origin
    from sylvia_gm.domain.authority import ActionStatus, MechanicOutcome, WriterRole

    path = folder / "failed" / "authority.sqlite3"
    path.parent.mkdir()
    path.write_bytes(gzip.decompress(BASES["story"].read_bytes()))
    store = a._open_for_application_owner(path)
    try:
        connection = store._connect()
        try:
            package_id, version, sha256 = connection.execute(
                "SELECT package_id, package_version, package_sha256 FROM actions WHERE action_id = 'action-1'"
            ).fetchone()
        finally:
            connection.close()
        revision = store.snapshot("campaign-story").revision
        parameters = {"ability": "strength", "dc": 18}
        origin = player_control_origin(
            {"kind": "result_card", "action_id": "action-1", "button_index": 0},
            expected_revision=revision, selection={}, session_id=None)
        marker = {"kind": PLAYER_ACTION_ORIGIN,
                  "origin": {**origin, "policy": {"confirmation": "none", "response": "on_request"}}}
        payload_hash = a._stored_action_hash(
            campaign_id="campaign-story", action_id="action-2", writer="dm", expected_revision=revision,
            turn_id="turn-2", package_id=package_id, package_version=version, package_sha256=sha256,
            tool_name="roll_check", parameters=parameters)
        store.commit_action(
            campaign_id="campaign-story", action_id="action-2", idempotency_key="key-2", payload_hash=payload_hash,
            expected_revision=revision, writer=WriterRole.DM, turn_id="turn-2", package_id=package_id,
            package_version=version, package_sha256=sha256, tool_name="roll_check", parameters=parameters,
            outcome=MechanicOutcome(result={"success": False}, mutations=(), consequences=(marker,)),
            status=ActionStatus.FAILED)
    finally:
        store.close()
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    return path.read_bytes()


def cast_base(folder: Path) -> bytes:
    """The Riichi profile with participants, made by Python's owner and store (written as
    `stored_authority/cast.sqlite3.gz`): a Builder campaign with `aki`, an adaptation `adapt-1`
    of `aki` into the same exact player schema, and a DM campaign with the adapted `aki` and with
    `bo`, whose one action renames `aki` (a `participant_updated` consequence)."""
    from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
    from sylvia_gm.domain.authority import (
        ActionStatus,
        MechanicOutcome,
        Participant,
        ParticipantMutation,
        ParticipantValidationStatus,
        WriterRole,
    )

    path = folder / "cast" / "authority.sqlite3"
    path.parent.mkdir()
    path.write_bytes(gzip.decompress(BASES["riichi"].read_bytes()))
    with AuthorityOwner.open(path, "stored-authority-cast") as owner:
        lock = owner.package_candidate("candidate-install-1").package_lock
        primary = next(entry for entry in lock.entries if entry.package_id == PRIMARY)
        declaration = owner.exact_participant_declarations(primary.package_id, primary.version, primary.sha256)[0]

        def player(participant_id: str, name: str, adaptation_id: str | None = None) -> Participant:
            return Participant(
                participant_id=participant_id, kind="player", package_id=primary.package_id,
                package_version=primary.version, package_sha256=primary.sha256,
                schema_id=declaration.schema_id, schema_sha256=declaration.schema_sha256,
                validation_status=ParticipantValidationStatus.VALIDATED,
                data={"name": name, "control": "player"}, adaptation_id=adaptation_id)

        owner.create_campaign(CampaignCreate(
            campaign_id="campaign-workshop", display_name="Workshop", writer=WriterRole.BUILDER,
            package_lock=lock, turn_id="turn-workshop", participants=(player("aki", "Aki"),)))
        owner.adapt_participant_artifact(
            source_campaign_id="campaign-workshop", participant_id="aki",
            destination_package_id=primary.package_id, destination_package_version=primary.version,
            destination_package_sha256=primary.sha256, destination_kind="player",
            canonical_data={"name": "Aki", "control": "player"}, operation_id="adapt-1")
        owner.create_campaign(CampaignCreate(
            campaign_id="campaign-table", display_name="Table", writer=WriterRole.DM, package_lock=lock,
            turn_id="turn-table", participants=(player("aki", "Aki", "adapt-1"), player("bo", "Bo"))))
    store = a._open_for_application_owner(path)
    try:
        values = {
            "campaign_id": "campaign-table", "action_id": "action-1",
            "expected_revision": store.snapshot("campaign-table").revision, "turn_id": "turn-table",
            "package_id": primary.package_id, "package_version": primary.version,
            "package_sha256": primary.sha256, "tool_name": "rename", "parameters": {"note": "renamed"}}
        store.commit_action(
            **values, writer=WriterRole.DM, idempotency_key="key-1",
            payload_hash=a._stored_action_hash(**values, writer="dm"),
            outcome=MechanicOutcome(result={"renamed": True}, participant_mutations=(
                ParticipantMutation(participant_id="aki", data={"name": "Akira", "control": "player"}),)),
            status=ActionStatus.COMMITTED)
    finally:
        store.close()
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    return path.read_bytes()


def recovered_base(folder: Path) -> bytes:
    """A new profile into which Python's owner recovered the story campaign from the portable
    archive it exported (written as `stored_authority/recovered.sqlite3.gz`)."""
    from sylvia_gm.application.authority import AuthorityOwner

    source = folder / "recovered-source" / "authority.sqlite3"
    source.parent.mkdir()
    source.write_bytes(gzip.decompress(BASES["story"].read_bytes()))
    archive = folder / "story.sylvia-gm-campaign"
    with AuthorityOwner.open(source, "stored-authority-export") as owner:
        owner.export_campaign("export-1", "campaign-story", archive)
    path = folder / "recovered" / "authority.sqlite3"
    path.parent.mkdir()
    with AuthorityOwner.open(path, "stored-authority-recover") as owner:
        owner.prepare_new_campaign_storage()
        owner.recover_campaign(recovery_id="recovery-1", source=archive)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    return path.read_bytes()


def receipt_payload_sql(connection: sqlite3.Connection, change) -> list[str]:  # noqa: ANN001
    """SQL replacing `recovery-1`'s semantic payload with `change(payload)`, canonical, with its
    SHA-256 recomputed, so the damage reaches the checks after the hash."""
    import hashlib

    from sylvia_gm.domain.package_library import canonical_json_bytes

    row = connection.execute(
        "SELECT semantic_payload_json FROM campaign_recovery_receipts WHERE recovery_id = 'recovery-1'").fetchone()
    encoded = canonical_json_bytes(change(json.loads(row[0])))
    value = encoded.decode("utf-8").replace("'", "''")
    return [f"UPDATE campaign_recovery_receipts SET semantic_payload_json = '{value}', "
            f"semantic_payload_sha256 = '{hashlib.sha256(encoded).hexdigest()}' WHERE recovery_id = 'recovery-1'"]


def receipt_result_sql(result_text: str) -> list[str]:
    value = result_text.replace("'", "''")
    return [f"UPDATE campaign_recovery_receipts SET result_json = '{value}' WHERE recovery_id = 'recovery-1'"]


def provenance(change):  # noqa: ANN001, ANN201
    """A change to the payload's `migration_provenance`."""
    def apply(payload: dict) -> dict:
        return {**payload, "migration_provenance": change(payload["migration_provenance"])}
    return apply


def with_purge_extension(value: dict) -> dict:
    from sylvia_gm.infrastructure import campaign_archive

    names = campaign_archive.SCHEMA_EXTENSION_FIELDS
    extensions = [*value["schema_extensions"], dict(zip(names, campaign_archive.PURGE_SCHEMA_EXTENSION_PROVENANCE))]
    return {**value, "schema_extensions": sorted(extensions, key=lambda item: item["extension_name"])}


def adaptation_sql(connection: sqlite3.Connection, **changes: object) -> list[str]:
    """SQL changing `adapt-1`'s columns, with its payload SHA-256 recomputed as Python's store
    would write it, so the damage reaches the checks after the hash."""
    from sylvia_gm.infrastructure import sqlite_campaign

    connection.row_factory = sqlite3.Row
    try:
        row = dict(connection.execute("SELECT * FROM participant_adaptations WHERE operation_id = 'adapt-1'").fetchone())
    finally:
        connection.row_factory = None
    row.update(changes)
    data = json.loads(str(row["data_json"]))
    digest = sqlite_campaign.adaptation_payload_sha256(
        source_campaign_id=row["source_campaign_id"], participant_id=row["participant_id"],
        destination_package_id=row["destination_package_id"],
        destination_package_version=row["destination_package_version"],
        destination_package_sha256=row["destination_package_sha256"], destination_kind=row["destination_kind"],
        destination_schema_id=row["destination_schema_id"],
        destination_schema_sha256=row["destination_schema_sha256"], data=data)
    assignments = ", ".join(f"{column} = '{str(value).replace(chr(39), chr(39) * 2)}'" for column, value in changes.items())
    return [f"UPDATE participant_adaptations SET {assignments}, payload_sha256 = '{digest}' "
            "WHERE operation_id = 'adapt-1'"]


def history_sql(connection: sqlite3.Connection, **changes: object) -> list[str]:
    """SQL changing `aki`'s rename consequence in both places it is stored."""
    row = connection.execute("SELECT payload_json FROM consequences WHERE campaign_id = 'campaign-table'").fetchone()
    value = json.loads(row[0])
    for key, item in changes.items():
        value[key] = item
    text = a._dump(value).replace("'", "''")
    return [
        f"UPDATE consequences SET payload_json = '{text}' WHERE campaign_id = 'campaign-table'",
        f"UPDATE canonical_events SET safe_payload_json = '{text}' "
        "WHERE campaign_id = 'campaign-table' AND event_type = 'consequence'",
    ]


def marker_sql(connection: sqlite3.Connection, change) -> list[str]:  # noqa: ANN001
    """SQL changing the failed action's marker in both places it is stored: its consequence row
    and its canonical consequence event."""
    row = connection.execute(
        "SELECT payload_json FROM consequences WHERE action_id = 'action-2'").fetchone()
    text = a._dump(change(json.loads(row[0]))).replace("'", "''")
    return [
        f"UPDATE consequences SET payload_json = '{text}' WHERE action_id = 'action-2'",
        f"UPDATE canonical_events SET safe_payload_json = '{text}' "
        "WHERE action_id = 'action-2' AND event_type = 'consequence'",
    ]


def origin(**changes: object):  # noqa: ANN201
    """A change to the marker's `origin`."""
    return lambda marker: edited(marker, origin=edited(marker["origin"], **changes))


LIFECYCLE_BASES = {"archived": ("archive_campaign",), "trashed": ("archive_campaign", "trash_campaign")}
PRIMARY = "pneuma.riichi-mahjong"
# The story campaign's events: 1 and 2 are TALK prose (attempts att-1, att-2), 3 to 8 are action-1.
STORY = "campaign_id = 'campaign-story'"
CHECKS_OFF = "PRAGMA ignore_check_constraints = ON"
BLOCKS = '{"response_blocks":[{"kind":"narration","text":"I dive. The sea answers."}],"text":"I dive. The sea answers."}'

TABLE_BO = "campaign_id = 'campaign-table' AND participant_id = 'bo'"
TABLE_AKI = "campaign_id = 'campaign-table' AND participant_id = 'aki'"
QUARANTINE_BO = ("UPDATE participants SET validation_status = 'quarantined', schema_id = NULL, "
                 f"schema_sha256 = NULL WHERE {TABLE_BO}")
DECLARATIONS = "package_participant_artifacts"
OF_PRIMARY = f"WHERE package_id = '{PRIMARY}'"
PLAYER_AKI = {"name": "Aki", "control": "player"}

# `verify_f5_campaign_authority`, on the `cast` base.
F5_CASES: list[dict[str, object]] = [
    {"name": "cast_untouched", "base": "cast", "sql": []},
    {"name": "f5_declaration_schema_id", "base": "cast", "sql": [f"UPDATE {DECLARATIONS} SET schema_id = 'other' {OF_PRIMARY}"]},
    {"name": "f5_declaration_schema_json", "base": "cast", "sql": [f"UPDATE {DECLARATIONS} SET schema_json = '{{}}' {OF_PRIMARY}"]},
    {"name": "f5_declaration_order", "base": "cast", "sql": [f"UPDATE {DECLARATIONS} SET artifact_order = 1 {OF_PRIMARY}"]},
    {"name": "f5_declaration_order_text", "base": "cast", "sql": [f"UPDATE {DECLARATIONS} SET artifact_order = '0' {OF_PRIMARY}"]},
    {"name": "f5_declaration_extra", "base": "cast", "sql": [
        f"INSERT INTO package_participant_artifacts SELECT package_id, version, content_sha256, 1, 'dealer', "
        f"schema_id, schema_sha256, schema_json FROM {DECLARATIONS} {OF_PRIMARY}"]},
    {"name": "f5_declarations_missing", "base": "cast", "sql": [f"DELETE FROM {DECLARATIONS} {OF_PRIMARY}"]},
    {"name": "f5_adaptation_operation_padded", "base": "cast", "sql": [
        "UPDATE participant_adaptations SET operation_id = ' adapt-1'"]},
    {"name": "f5_adaptation_source_kind_blank", "base": "cast", "sql": [
        "UPDATE participant_adaptations SET source_kind = ' '"]},
    {"name": "f5_adaptation_destination_schema_padded", "base": "cast", "sql": [
        "UPDATE participant_adaptations SET destination_schema_id = destination_schema_id || ' '"]},
    {"name": "f5_adaptation_payload_uppercase", "base": "cast", "sql": [
        "UPDATE participant_adaptations SET payload_sha256 = upper(payload_sha256)"]},
    {"name": "f5_adaptation_source_data_sha_short", "base": "cast", "sql": [
        "UPDATE participant_adaptations SET source_data_sha256 = 'abc'"]},
    {"name": "f5_adaptation_source_schema_padded", "base": "cast", "sql": [
        "UPDATE participant_adaptations SET source_schema_id = ' x'"]},
    {"name": "f5_adaptation_source_schema_sha_uppercase", "base": "cast", "sql": [
        "UPDATE participant_adaptations SET source_schema_sha256 = upper(source_schema_sha256)"]},
    {"name": "f5_adaptation_source_schema_null", "base": "cast", "sql": [
        "UPDATE participant_adaptations SET source_schema_id = NULL"]},
    {"name": "f5_adaptation_source_kind_other", "base": "cast", "sql": [
        "UPDATE participant_adaptations SET source_kind = 'dealer'"]},
    {"name": "f5_adaptation_source_missing", "base": "cast", "sql": [
        "UPDATE participant_adaptations SET source_campaign_id = 'campaign-gone'"]},
    {"name": "f5_adaptation_source_data_changed", "base": "cast", "sql": [
        "UPDATE participants SET data_json = '{\"control\":\"player\",\"name\":\"Aiko\"}' "
        "WHERE campaign_id = 'campaign-workshop'"]},
    {"name": "f5_adaptation_source_data_not_object", "base": "cast", "sql": [
        "UPDATE participants SET data_json = '[]' WHERE campaign_id = 'campaign-workshop'"]},
    {"name": "f5_adaptation_source_data_sha_other", "base": "cast", "sql": [
        "UPDATE participant_adaptations SET source_data_sha256 = '" + "a" * 64 + "'"]},
    {"name": "f5_adaptation_payload_other", "base": "cast", "sql": [
        "UPDATE participant_adaptations SET payload_sha256 = '" + "b" * 64 + "'"]},
    {"name": "f5_adaptation_data_not_object", "base": "cast", "sql": [],
     "computed": [lambda connection: adaptation_sql(connection, data_json="[]")]},
    {"name": "f5_adaptation_data_extra_member", "base": "cast", "sql": [],
     "computed": [lambda connection: adaptation_sql(
         connection, data_json=a._dump({**PLAYER_AKI, "x": 1}))]},
    {"name": "f5_adaptation_destination_kind_other", "base": "cast", "sql": [],
     "computed": [lambda connection: adaptation_sql(connection, destination_kind="dealer")]},
    {"name": "f5_adaptation_destination_schema_sha_other", "base": "cast", "sql": [],
     "computed": [lambda connection: adaptation_sql(connection, destination_schema_sha256="c" * 64)]},
    {"name": "f5_adaptation_destination_schema_id_other", "base": "cast", "sql": [],
     "computed": [lambda connection: adaptation_sql(connection, destination_schema_id="other.schema")]},
    {"name": "f5_participant_status_unknown", "base": "cast", "sql": [
        f"UPDATE participants SET validation_status = 'pending' WHERE {TABLE_BO}"]},
    {"name": "f5_participant_kind_padded", "base": "cast", "sql": [
        f"UPDATE participants SET kind = ' player' WHERE {TABLE_BO}"]},
    {"name": "f5_participant_data_not_object", "base": "cast", "sql": [
        f"UPDATE participants SET data_json = '[]' WHERE {TABLE_BO}"]},
    {"name": "f5_participant_package_sha_null", "base": "cast", "sql": [
        f"UPDATE participants SET package_sha256 = NULL WHERE {TABLE_BO}"]},
    {"name": "f5_quarantined_claims_schema", "base": "cast", "sql": [
        f"UPDATE participants SET validation_status = 'quarantined' WHERE {TABLE_BO}"]},
    {"name": "f5_quarantined_claims_adaptation", "base": "cast", "sql": [
        f"UPDATE participants SET validation_status = 'quarantined', schema_id = NULL, schema_sha256 = NULL "
        f"WHERE {TABLE_AKI}"]},
    {"name": "f5_quarantined_without_evidence", "base": "cast", "sql": [QUARANTINE_BO]},
    {"name": "f5_quarantined_with_evidence", "base": "cast", "sql": [
        QUARANTINE_BO, "INSERT INTO participant_quarantines VALUES ('campaign-table', 'bo', 'exact_schema_proof_unavailable')"]},
    {"name": "f5_quarantined_blank_reason", "base": "cast", "sql": [
        QUARANTINE_BO, "INSERT INTO participant_quarantines VALUES ('campaign-table', 'bo', '')"]},
    {"name": "f5_validated_with_quarantine", "base": "cast", "sql": [
        "INSERT INTO participant_quarantines VALUES ('campaign-table', 'bo', 'exact_schema_proof_unavailable')"]},
    {"name": "f5_quarantine_orphaned", "base": "cast", "sql": [
        "INSERT INTO participant_quarantines VALUES ('campaign-table', 'zed', 'exact_schema_proof_unavailable')"]},
    {"name": "f5_participant_schema_other", "base": "cast", "sql": [
        f"UPDATE participants SET schema_id = 'other.schema' WHERE {TABLE_BO}"]},
    {"name": "f5_participant_schema_sha_other", "base": "cast", "sql": [
        f"UPDATE participants SET schema_sha256 = '{'d' * 64}' WHERE {TABLE_BO}"]},
    {"name": "f5_participant_package_sha_other", "base": "cast", "sql": [
        f"UPDATE participants SET package_sha256 = '{'e' * 64}' WHERE {TABLE_BO}"]},
    {"name": "f5_participant_data_invalid", "base": "cast", "sql": [
        f"UPDATE participants SET data_json = '{{\"control\":\"player\",\"name\":\"\"}}' WHERE {TABLE_BO}"]},
    {"name": "f5_participant_adaptation_unknown", "base": "cast", "sql": [
        f"UPDATE participants SET adaptation_id = 'adapt-2' WHERE {TABLE_AKI}"]},
    {"name": "f5_participant_adaptation_padded", "base": "cast", "sql": [
        f"UPDATE participants SET adaptation_id = 'adapt-1 ' WHERE {TABLE_AKI}"]},
    {"name": "f5_participant_adaptation_other_participant", "base": "cast", "sql": [
        f"UPDATE participants SET adaptation_id = 'adapt-1' WHERE {TABLE_BO}"]},
    {"name": "f5_history_before_other", "base": "cast", "sql": [],
     "computed": [lambda connection: history_sql(connection, before={"name": "Ako", "control": "player"})]},
    {"name": "f5_history_after_other", "base": "cast", "sql": [],
     "computed": [lambda connection: history_sql(connection, after={"name": "Akiko", "control": "player"})]},
    {"name": "f5_history_before_not_object", "base": "cast", "sql": [],
     "computed": [lambda connection: history_sql(connection, before="Aki")]},
    {"name": "f5_history_other_participant", "base": "cast", "sql": [],
     "computed": [lambda connection: history_sql(connection, participant_id="bo")]},
]

RECEIPT = "campaign_recovery_receipts WHERE recovery_id = 'recovery-1'"
STORY_RESULT = ('{"campaign_id":"campaign-story","captured_revision":8,"archive_sha256":"%s","byte_count":%s}')

# `sqlite_recovery.verify_recovery_receipts`, on the `recovered` base.
RECOVERY_CASES: list[dict[str, object]] = [
    {"name": "recovered_untouched", "base": "recovered", "sql": []},
    {"name": "receipt_recovery_id_padded", "base": "recovered", "sql": [
        f"UPDATE {RECEIPT.split(' WHERE ')[0]} SET recovery_id = ' recovery-1'"]},
    {"name": "receipt_archive_sha_uppercase", "base": "recovered", "sql": [
        f"UPDATE campaign_recovery_receipts SET archive_sha256 = upper(archive_sha256)"]},
    {"name": "receipt_archive_sha_other", "base": "recovered", "sql": [
        f"UPDATE campaign_recovery_receipts SET archive_sha256 = '{'a' * 64}'"]},
    {"name": "receipt_payload_sha_other", "base": "recovered", "sql": [
        f"UPDATE campaign_recovery_receipts SET semantic_payload_sha256 = '{'b' * 64}'"]},
    {"name": "receipt_payload_not_canonical", "base": "recovered", "sql": [
        "UPDATE campaign_recovery_receipts SET semantic_payload_json = replace(semantic_payload_json, ':', ': ')"]},
    {"name": "receipt_payload_not_json", "base": "recovered", "sql": [
        "UPDATE campaign_recovery_receipts SET semantic_payload_json = 'x'"]},
    {"name": "receipt_payload_format_version", "base": "recovered", "sql": [],
     "computed": [lambda connection: receipt_payload_sql(connection, lambda payload: {**payload, "format_version": 2})]},
    {"name": "receipt_payload_operation_kind", "base": "recovered", "sql": [],
     "computed": [lambda connection: receipt_payload_sql(
         connection, lambda payload: {**payload, "operation_kind": "export_campaign"})]},
    {"name": "receipt_payload_revision_other", "base": "recovered", "sql": [],
     "computed": [lambda connection: receipt_payload_sql(connection, lambda payload: {**payload, "captured_revision": 7})]},
    {"name": "receipt_payload_revision_float", "base": "recovered", "sql": [],
     "computed": [lambda connection: receipt_payload_sql(connection, lambda payload: {**payload, "captured_revision": 8.0})]},
    {"name": "receipt_payload_revision_true", "base": "recovered", "sql": [],
     "computed": [lambda connection: receipt_payload_sql(connection, lambda payload: {**payload, "format_version": True})]},
    {"name": "receipt_payload_extra_member", "base": "recovered", "sql": [],
     "computed": [lambda connection: receipt_payload_sql(connection, lambda payload: {**payload, "note": "x"})]},
    {"name": "receipt_payload_schema_claim_other", "base": "recovered", "sql": [],
     "computed": [lambda connection: receipt_payload_sql(
         connection, lambda payload: {**payload, "source_authority_schema_identity_sha256": "c" * 64})]},
    {"name": "receipt_payload_schema_version_11", "base": "recovered", "sql": [],
     "computed": [lambda connection: receipt_payload_sql(
         connection, lambda payload: {**payload, "source_authority_schema_version": 11})]},
    {"name": "receipt_provenance_without_extensions", "base": "recovered", "sql": [],
     "computed": [lambda connection: receipt_payload_sql(
         connection, provenance(lambda value: {**value, "schema_extensions": []}))]},
    {"name": "receipt_provenance_with_purge_extension", "base": "recovered", "sql": [],
     "computed": [lambda connection: receipt_payload_sql(connection, provenance(with_purge_extension))]},
    {"name": "receipt_provenance_reordered_members", "base": "recovered", "sql": [],
     "computed": [lambda connection: receipt_payload_sql(connection, provenance(
         lambda value: {**value, "schema_migrations": [
             {"sql_sha256": item["sql_sha256"], "applied_name": item["applied_name"], "version": item["version"]}
             for item in value["schema_migrations"]]}))]},
    {"name": "receipt_provenance_version_true", "base": "recovered", "sql": [],
     "computed": [lambda connection: receipt_payload_sql(connection, provenance(
         lambda value: {**value, "schema_migrations": [
             {**item, "version": True} if item["version"] == 1 else item
             for item in value["schema_migrations"]]}))]},
    {"name": "receipt_provenance_migration_missing", "base": "recovered", "sql": [],
     "computed": [lambda connection: receipt_payload_sql(connection, provenance(
         lambda value: {**value, "schema_migrations": value["schema_migrations"][:-1]}))]},
    {"name": "receipt_result_not_json", "base": "recovered", "sql": receipt_result_sql("x")},
    {"name": "receipt_result_list", "base": "recovered", "sql": receipt_result_sql("[]")},
    {"name": "receipt_result_member_missing", "base": "recovered", "sql": receipt_result_sql(
        '{"campaign_id":"campaign-story"}')},
    {"name": "receipt_result_revision_text", "base": "recovered", "sql": [], "computed": [
        lambda connection: receipt_result_sql(STORY_RESULT.replace('"captured_revision":8', '"captured_revision":"8"') % (
            connection.execute(f"SELECT archive_sha256 FROM {RECEIPT}").fetchone()[0],
            connection.execute(f"SELECT archive_byte_count FROM {RECEIPT}").fetchone()[0]))]},
    {"name": "receipt_result_revision_nan", "base": "recovered", "sql": [], "computed": [
        lambda connection: receipt_result_sql(STORY_RESULT.replace('"captured_revision":8', '"captured_revision":NaN') % (
            connection.execute(f"SELECT archive_sha256 FROM {RECEIPT}").fetchone()[0],
            connection.execute(f"SELECT archive_byte_count FROM {RECEIPT}").fetchone()[0]))]},
    {"name": "receipt_result_reordered", "base": "recovered", "sql": [], "computed": [
        lambda connection: receipt_result_sql(
            '{"captured_revision":8,"campaign_id":"campaign-story","archive_sha256":"%s","byte_count":%s}' % (
                connection.execute(f"SELECT archive_sha256 FROM {RECEIPT}").fetchone()[0],
                connection.execute(f"SELECT archive_byte_count FROM {RECEIPT}").fetchone()[0]))]},
    {"name": "receipt_row_revision_other", "base": "recovered", "sql": [
        "UPDATE campaign_recovery_receipts SET captured_revision = 9"]},
    {"name": "receipt_row_byte_count_text", "base": "recovered", "sql": [
        "UPDATE campaign_recovery_receipts SET archive_byte_count = CAST(archive_byte_count AS TEXT) || ' '"]},
    {"name": "receipt_row_byte_count_word", "base": "recovered", "sql": [
        "UPDATE campaign_recovery_receipts SET archive_byte_count = 'many'"]},
    {"name": "receipt_campaign_other", "base": "recovered", "sql": [
        "UPDATE campaign_recovery_receipts SET campaign_id = 'campaign-other'"]},
]

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
    # Part 2: the session, plan and handoff graph.
    {"name": "children_untouched", "base": "children", "sql": []},
    {"name": "message_chronology_gap", "base": "story", "sql": [
        "UPDATE agent_session_messages SET sequence = 70 WHERE session_id = 'dm-1' AND message_id = 'u-3'"]},
    {"name": "reference_content_changed", "base": "story", "sql": [
        "UPDATE agent_session_messages SET content_json = replace(content_json, 'The sea answers.', 'Silence.') "
        "WHERE message_id = 'canonical-1'"]},
    {"name": "reference_event_missing", "base": "story", "sql": [
        "UPDATE agent_session_messages SET canonical_sequence = 999 WHERE message_id = 'canonical-1'"]},
    {"name": "plan_revision_gap", "base": "story", "sql": ["UPDATE campaign_plans SET plan_revision = 2"]},
    {"name": "plan_content_changed", "base": "story", "sql": [
        "UPDATE campaign_plans SET content_json = replace(content_json, 'The Drowned Bell', 'The Dry Bell')"]},
    {"name": "plan_provenance_missing", "base": "story", "sql": [
        "UPDATE campaign_plans SET provenance_json = '[\"ev-missing\"]'"]},
    {"name": "plan_payload_identity", "base": "story", "sql": [
        "UPDATE campaign_plans SET payload_sha256 = '" + "a" * 64 + "'"]},
    {"name": "plan_revision_row_missing", "base": "story", "sql": [
        "UPDATE campaign_revisions SET plan_id = NULL WHERE plan_id = 'plan-1'"]},
    {"name": "handoff_turn_open", "base": "story", "sql": [
        "UPDATE turns SET status = 'open' WHERE turn_id = 'turn-build'"]},
    {"name": "handoff_payload_identity", "base": "story", "sql": [
        "UPDATE builder_dm_handoffs SET payload_sha256 = '" + "b" * 64 + "'"]},
    {"name": "handoff_revision_row_missing", "base": "story", "sql": [
        "UPDATE campaign_revisions SET handoff_id = NULL WHERE handoff_id = 'handoff-1'"]},
    {"name": "child_return_authoritative", "base": "children", "sql": [
        "UPDATE agent_session_messages SET content_json = replace(content_json, '\"authoritative\":false', "
        "'\"authoritative\":true') WHERE message_id = 'evidence-1'"]},
    {"name": "event_payload_not_object", "base": "story", "sql": [
        "UPDATE canonical_events SET safe_payload_json = '[]' WHERE sequence = 1"]},
    # Part 3: `_verify_campaign_authority`. Writer and lifecycle.
    {"name": "writer_illegal_for_lifecycle", "base": "story", "sql": [
        f"UPDATE campaigns SET writer = 'none' WHERE {STORY}"]},
    {"name": "lifecycle_unknown", "base": "story", "sql": [
        f"UPDATE campaigns SET lifecycle = 'paused' WHERE {STORY}"]},
    # Revisions and their authorities.
    {"name": "campaign_revision_short", "base": "story", "sql": [
        f"UPDATE campaigns SET revision = 7 WHERE {STORY}"]},
    {"name": "revision_gap", "base": "story", "sql": [
        f"UPDATE campaign_revisions SET revision = 9 WHERE {STORY} AND revision = 8"]},
    {"name": "revision_text", "base": "story", "sql": [
        f"UPDATE campaign_revisions SET revision = 'eight' WHERE {STORY} AND revision = 8"]},
    {"name": "revision_zero_with_authority", "base": "story", "sql": [
        f"UPDATE campaign_revisions SET continuity_operation_id = 'op-1' WHERE {STORY} AND revision = 0"]},
    {"name": "revision_two_authorities", "base": "story", "sql": [
        f"UPDATE campaign_revisions SET action_id = 'action-1' WHERE {STORY} AND revision = 7"]},
    {"name": "revision_authorities_swapped", "base": "story", "sql": [
        f"UPDATE campaign_revisions SET continuity_operation_id = CASE revision WHEN 7 THEN 'op-5' ELSE 'op-4' END "
        f"WHERE {STORY} AND revision IN (7, 8)"]},
    {"name": "revision_operation_missing", "base": "story", "sql": [
        "DELETE FROM campaign_operations WHERE operation_id = 'op-5'"]},
    # Turns, participants and state.
    {"name": "second_open_turn", "base": "story", "sql": [
        "INSERT INTO turns (campaign_id, turn_id, status, created_revision) VALUES ('campaign-build', 'turn-c', 'open', 0)"]},
    {"name": "turn_status_unknown", "base": "story", "sql": [
        "UPDATE turns SET status = 'paused' WHERE turn_id = 'turn-b'"]},
    {"name": "turn_from_the_future", "base": "story", "sql": [
        "UPDATE turns SET created_revision = 1 WHERE turn_id = 'turn-b'"]},
    {"name": "turn_created_text", "base": "story", "sql": [
        "UPDATE turns SET created_revision = 'zero' WHERE turn_id = 'turn-b'"]},
    {"name": "participant_foreign_package", "base": "story", "sql": [
        "INSERT INTO participants (campaign_id, participant_id, kind, package_id, package_version, data_json) "
        "SELECT campaign_id, 'hero', 'character', 'other.package', '1.0.0', '{}' FROM campaigns WHERE " + STORY]},
    {"name": "state_value_private_channel", "base": "story", "bypass_triggers": True, "sql": [
        f"UPDATE campaign_state SET value_json = '{{\"private_reasoning\":1}}' WHERE {STORY}"]},
    {"name": "state_value_not_canonical", "base": "story", "bypass_triggers": True, "sql": [
        f"UPDATE campaign_state SET value_json = '{{\"total\": 15}}' WHERE {STORY}"]},
    {"name": "state_drifted_from_mutations", "base": "story", "bypass_triggers": True, "sql": [
        f"UPDATE campaign_state SET value_json = '{{\"total\":16}}' WHERE {STORY}"]},
    # Canonical events and prose.
    {"name": "event_sequence_gap", "base": "story", "sql": [
        f"UPDATE canonical_events SET sequence = 9 WHERE {STORY} AND sequence = 8"]},
    {"name": "event_type_unknown", "base": "story", "sql": [
        f"UPDATE canonical_events SET event_type = 'whisper' WHERE {STORY} AND sequence = 2"]},
    {"name": "event_visibility_unknown", "base": "story", "sql": [
        f"UPDATE canonical_events SET visibility = 'secret' WHERE {STORY} AND sequence = 2"]},
    {"name": "talk_as_think", "base": "story", "sql": [
        f"UPDATE canonical_events SET event_type = 'think' WHERE {STORY} AND sequence = 2"]},
    {"name": "talk_with_action", "base": "story", "sql": [
        f"UPDATE canonical_events SET action_id = 'action-1' WHERE {STORY} AND sequence = 2"]},
    {"name": "talk_internal", "base": "story", "sql": [
        f"UPDATE canonical_events SET visibility = 'internal_safe' WHERE {STORY} AND sequence = 2"]},
    {"name": "talk_extra_member", "base": "story", "sql": [
        f"UPDATE canonical_events SET safe_payload_json = '{{\"text\":\"I dive.\",\"tone\":1}}' WHERE {STORY} AND sequence = 2"]},
    {"name": "talk_empty_text", "base": "story", "sql": [
        f"UPDATE canonical_events SET safe_payload_json = '{{\"text\":\"\"}}' WHERE {STORY} AND sequence = 2"]},
    {"name": "talk_response_blocks", "base": "story", "sql": [
        f"UPDATE canonical_events SET safe_payload_json = '{BLOCKS}' WHERE {STORY} AND sequence = 2"]},
    {"name": "talk_response_blocks_disagree", "base": "story", "sql": [
        f"UPDATE canonical_events SET safe_payload_json = '{BLOCKS.replace('I dive. The', 'I swim. The', 1)}' "
        f"WHERE {STORY} AND sequence = 2"]},
    {"name": "talk_response_blocks_invalid", "base": "story", "sql": [
        f"UPDATE canonical_events SET safe_payload_json = '{BLOCKS.replace('narration', 'aside')}' "
        f"WHERE {STORY} AND sequence = 2"]},
    {"name": "think_response_blocks", "base": "story", "sql": [
        f"UPDATE canonical_events SET event_type = 'think', safe_payload_json = '{BLOCKS}' "
        f"WHERE {STORY} AND sequence = 2"]},
    {"name": "prose_ordinal_gap", "base": "story", "sql": [
        f"UPDATE canonical_events SET attempt_ordinal = 1 WHERE {STORY} AND sequence = 2"]},
    {"name": "prose_ordinal_negative", "base": "story", "sql": [
        f"UPDATE canonical_events SET attempt_ordinal = -1 WHERE {STORY} AND sequence = 2"]},
    {"name": "prose_ordinal_text", "base": "story", "sql": [
        f"UPDATE canonical_events SET attempt_ordinal = 'first' WHERE {STORY} AND sequence = 2"]},
    {"name": "prose_attempts_merged", "base": "story", "sql": [
        f"UPDATE canonical_events SET attempt_id = 'att-1', attempt_ordinal = 1 WHERE {STORY} AND sequence = 2"]},
    {"name": "prose_turn_unknown", "base": "story", "sql": [
        f"UPDATE canonical_events SET turn_id = 'turn-z' WHERE {STORY} AND sequence = 2"]},
    {"name": "action_event_player_safe", "base": "story", "sql": [
        f"UPDATE canonical_events SET visibility = 'player_safe' WHERE {STORY} AND sequence = 3"]},
    {"name": "action_event_with_attempt", "base": "story", "sql": [
        f"UPDATE canonical_events SET attempt_id = 'att-9' WHERE {STORY} AND sequence = 3"]},
    {"name": "action_event_unknown_action", "base": "story", "sql": [
        f"UPDATE canonical_events SET action_id = 'action-9' WHERE {STORY} AND sequence = 3"]},
    {"name": "action_events_reordered", "base": "story", "sql": [
        f"UPDATE canonical_events SET event_type = CASE sequence WHEN 6 THEN 'consequence' ELSE 'state_mutation' END "
        f"WHERE {STORY} AND sequence IN (6, 7)"]},
    {"name": "action_event_other_turn", "base": "story", "sql": [
        f"UPDATE canonical_events SET turn_id = 'turn-build' WHERE {STORY} AND sequence = 5"]},
    # Actions, their payloads, mutations, consequences and receipts.
    {"name": "action_writer_builder", "base": "story", "sql": ["UPDATE actions SET writer = 'builder'"]},
    {"name": "action_hash_other", "base": "story", "sql": ["UPDATE actions SET payload_hash = '" + "a" * 64 + "'"]},
    {"name": "action_unpinned", "base": "story", "sql": ["UPDATE actions SET lock_id = NULL"]},
    {"name": "action_pinned_elsewhere", "base": "story", "sql": [
        "UPDATE actions SET package_sha256 = '" + "c" * 64 + "'"]},
    {"name": "action_revisions_disagree", "base": "story", "sql": ["UPDATE actions SET expected_revision = 4"]},
    {"name": "action_status_unknown", "base": "story", "sql": ["UPDATE actions SET status = 'pending'"]},
    {"name": "action_failed", "base": "story", "sql": ["UPDATE actions SET status = 'failed'"]},
    {"name": "action_result_changed", "base": "story", "sql": [
        "UPDATE actions SET result_json = '{\"success\":true,\"total\":16}'"]},
    {"name": "action_result_float_total", "base": "story", "sql": [
        "UPDATE actions SET result_json = '{\"success\":true,\"total\":15.0}'"]},
    {"name": "action_result_integer_success", "base": "story", "sql": [
        "UPDATE actions SET result_json = '{\"success\":1,\"total\":15}'"]},
    {"name": "action_result_not_canonical", "base": "story", "sql": [
        "UPDATE actions SET result_json = '{\"total\":15,\"success\":true}'"]},
    {"name": "action_parameters_not_object", "base": "story", "sql": ["UPDATE actions SET payload_json = '[]'"]},
    {"name": "mutation_ordinal_gap", "base": "story", "sql": ["UPDATE mutations SET ordinal = 1"]},
    {"name": "mutation_before_changed", "base": "story", "sql": [
        "UPDATE mutations SET before_json = '{\"total\":1}'"]},
    {"name": "mutation_after_changed", "base": "story", "sql": [
        "UPDATE mutations SET after_json = '{\"total\":16}'"]},
    {"name": "mutation_key_changed", "base": "story", "sql": ["UPDATE mutations SET state_key = 'first'"]},
    {"name": "consequence_changed", "base": "story", "sql": [
        "UPDATE consequences SET payload_json = '{\"kind\":\"check_rolled\",\"total\":16}'"]},
    {"name": "consequence_kind_blank", "base": "story", "sql": [
        "UPDATE consequences SET payload_json = '{\"kind\":\" \",\"total\":15}'",
        f"UPDATE canonical_events SET safe_payload_json = '{{\"kind\":\" \",\"total\":15}}' WHERE {STORY} AND sequence = 7"]},
    {"name": "consequence_kind_number", "base": "story", "sql": [
        "UPDATE consequences SET payload_json = '{\"kind\":5,\"total\":15}'"]},
    {"name": "consequence_ordinal_gap", "base": "story", "sql": ["UPDATE consequences SET ordinal = 2"]},
    {"name": "receipt_missing", "base": "story", "sql": ["DELETE FROM idempotency_records"]},
    {"name": "receipt_status_other", "base": "story", "sql": ["UPDATE idempotency_records SET status = 'failed'"]},
    {"name": "receipt_status_unknown", "base": "story", "sql": [
        "UPDATE idempotency_records SET status = 'maybe'"]},
    {"name": "receipt_result_float_total", "base": "story", "sql": [
        "UPDATE idempotency_records SET result_json = '{\"success\":true,\"total\":15.0}'"]},
    {"name": "receipt_result_changed", "base": "story", "sql": [
        "UPDATE idempotency_records SET result_json = '{\"success\":false,\"total\":15}'"]},
    {"name": "receipt_revision_other", "base": "story", "sql": ["UPDATE idempotency_records SET resulting_revision = 5"]},
    {"name": "receipt_second_key", "base": "story", "sql": [
        "INSERT INTO idempotency_records SELECT campaign_id, 'key-2', payload_hash, action_id, status, result_json, "
        "resulting_revision FROM idempotency_records"]},
    # Narration interruptions.
    {"name": "interruption_retryable", "base": "story", "sql": [
        "INSERT INTO narration_interruptions VALUES ('campaign-story', 'int-1', 'turn-2', 'action-1', 'att-2', "
        "'retryable', NULL, NULL)"]},
    {"name": "interruption_other_turn_action", "base": "story", "sql": [
        "INSERT INTO narration_interruptions VALUES ('campaign-story', 'int-1', 'turn-build', 'action-1', 'att-2', "
        "'retryable', NULL, NULL)"]},
    {"name": "interruption_retryable_with_sequence", "base": "story", "sql": [
        "INSERT INTO narration_interruptions VALUES ('campaign-story', 'int-1', 'turn-2', NULL, 'att-1', "
        "'retryable', 'att-2', 2)"]},
    {"name": "interruption_resolved", "base": "story", "sql": [
        "INSERT INTO narration_interruptions VALUES ('campaign-story', 'int-1', 'turn-2', NULL, 'att-1', "
        "'resolved', 'att-2', 2)"]},
    {"name": "interruption_resolved_without_sequence", "base": "story", "sql": [
        "INSERT INTO narration_interruptions VALUES ('campaign-story', 'int-1', 'turn-2', NULL, 'att-1', "
        "'resolved', 'att-2', NULL)"]},
    {"name": "interruption_resolved_on_other_attempt", "base": "story", "sql": [
        "INSERT INTO narration_interruptions VALUES ('campaign-story', 'int-1', 'turn-2', NULL, 'att-2', "
        "'resolved', 'att-2', 1)"]},
    {"name": "interruption_resolved_on_action_event", "base": "story", "sql": [
        "INSERT INTO narration_interruptions VALUES ('campaign-story', 'int-1', 'turn-2', NULL, 'att-1', "
        "'resolved', 'att-2', 3)"]},
    {"name": "interruption_resolved_without_retry_events", "base": "story", "sql": [
        "INSERT INTO narration_interruptions VALUES ('campaign-story', 'int-1', 'turn-2', NULL, 'att-1', "
        "'resolved', 'att-7', 2)"]},
    {"name": "interruption_status_unknown", "base": "story", "sql": [
        "INSERT INTO narration_interruptions VALUES ('campaign-story', 'int-1', 'turn-2', NULL, 'att-1', "
        "'parked', NULL, NULL)"]},
    # Lifecycle state, transitions and the writer sequence.
    {"name": "lifecycle_state_missing", "base": "story", "sql": [
        f"DELETE FROM campaign_lifecycle_state WHERE {STORY}"]},
    {"name": "lifecycle_retained_writer_other", "base": "story", "sql": [
        f"UPDATE campaign_lifecycle_state SET retained_active_writer = 'builder' WHERE {STORY}"]},
    {"name": "lifecycle_active_with_prior", "base": "story", "sql": [
        f"UPDATE campaign_lifecycle_state SET tombstone_prior_lifecycle = 'active' WHERE {STORY}"]},
    {"name": "handoff_basis_other", "base": "story", "sql": ["UPDATE builder_dm_handoffs SET expected_revision = 0"]},
    {"name": "archived_untouched", "base": "archived", "sql": []},
    {"name": "trashed_untouched", "base": "trashed", "sql": []},
    {"name": "archived_writer_dm", "base": "archived", "sql": [f"UPDATE campaigns SET writer = 'dm' WHERE {STORY}"]},
    {"name": "archived_lifecycle_active", "base": "archived", "sql": [
        f"UPDATE campaigns SET lifecycle = 'active', writer = 'dm' WHERE {STORY}"]},
    {"name": "archived_transition_from_other", "base": "archived", "sql": [
        "UPDATE campaign_lifecycle_transitions SET from_lifecycle = 'archived'"]},
    {"name": "archived_transition_basis_other", "base": "archived", "sql": [
        "UPDATE campaign_lifecycle_transitions SET basis_revision = 3"]},
    {"name": "archived_transition_from_builder", "base": "archived", "sql": [
        "UPDATE campaign_lifecycle_transitions SET from_writer = 'builder'"]},
    {"name": "archived_transition_to_unknown", "base": "archived", "sql": [
        "UPDATE campaign_lifecycle_transitions SET to_writer = 'ghost'"]},
    {"name": "archived_transition_revision_text", "base": "archived", "sql": [
        "UPDATE campaign_lifecycle_transitions SET basis_revision = 'eight'"]},
    {"name": "archived_retained_writer_none", "base": "archived", "sql": [
        f"UPDATE campaign_lifecycle_state SET retained_active_writer = 'none' WHERE {STORY}"]},
    {"name": "trashed_prior_missing", "base": "trashed", "sql": [
        f"UPDATE campaign_lifecycle_state SET tombstone_prior_lifecycle = NULL WHERE {STORY}"]},
    {"name": "trashed_prior_writer_missing", "base": "trashed", "sql": [
        f"UPDATE campaign_lifecycle_state SET tombstone_prior_writer = NULL WHERE {STORY}"]},
    {"name": "trashed_prior_other", "base": "trashed", "sql": [
        f"UPDATE campaign_lifecycle_state SET tombstone_prior_lifecycle = 'tombstoned' WHERE {STORY}"]},
    {"name": "trashed_transitions_out_of_order", "base": "trashed", "sql": [
        "UPDATE campaign_lifecycle_transitions SET from_lifecycle = 'active' WHERE to_lifecycle = 'tombstoned'"]},
    # Part 4: `_verify_construction_authority`. Operation receipts and their results.
    {"name": "constructed_untouched", "base": "constructed", "sql": []},
    {"name": "operation_kind_other", "base": "constructed", "sql": [
        "UPDATE campaign_operations SET operation_kind = 'rename_campaign' WHERE operation_id = 'begin-ruleless'"]},
    {"name": "operation_hash_other", "base": "constructed", "sql": [
        "UPDATE campaign_operations SET semantic_payload_sha256 = '" + "a" * 64 + "' WHERE operation_id = 'begin-ruleless'"]},
    {"name": "operation_payload_unsafe", "base": "constructed", "sql": [], "edits": [
        ("begin-ruleless", "semantic_payload", lambda p: edited(p, canonical_input={"private_reasoning": 1}))]},
    {"name": "operation_payload_kind_other", "base": "constructed", "sql": [], "edits": [
        ("begin-ruleless", "semantic_payload", lambda p: edited(p, operation_kind="rename_campaign"))]},
    {"name": "result_revision_other", "base": "constructed", "sql": [], "edits": [
        ("begin-ruleless", "result", lambda r: edited(r, resulting_revision=5))]},
    {"name": "result_revision_float", "base": "constructed", "sql": [], "edits": [
        ("begin-ruleless", "result", lambda r: edited(r, resulting_revision=1.0))]},
    {"name": "result_lane_other", "base": "constructed", "sql": [], "edits": [
        ("begin-ruleless", "result", lambda r: edited(r, lane="speedrun"))]},
    {"name": "result_lane_differs_from_construction", "base": "constructed", "sql": [], "edits": [
        ("begin-ruleless", "result", lambda r: edited(r, lane="quick_start"))]},
    {"name": "confirmation_result_extra_key", "base": "constructed", "sql": [], "edits": [
        ("confirm-interview", "result", lambda r: edited(r, note="x"))]},
    {"name": "completion_result_sha_uppercase", "base": "constructed", "sql": [], "edits": [
        ("construction-complete-4a4b97133864fe33934ec185", "result",
         lambda r: edited(r, canonical_representation_sha256=r["canonical_representation_sha256"].upper()))]},
    {"name": "completion_result_without_sha", "base": "constructed", "sql": [], "edits": [
        ("construction-complete-4a4b97133864fe33934ec185", "result",
         lambda r: edited(r, canonical_representation_sha256=DROP))]},
    {"name": "completion_result_sha_number", "base": "constructed", "sql": [], "edits": [
        ("construction-complete-4a4b97133864fe33934ec185", "result",
         lambda r: edited(r, canonical_representation_sha256=7))]},
    {"name": "result_not_object", "base": "constructed", "sql": [
        "UPDATE campaign_operations SET result_json = '[]' WHERE operation_id = 'begin-ruleless'"]},
    {"name": "operation_basis_turn_missing", "base": "constructed", "sql": [
        "UPDATE campaign_operations SET basis_turn_id = 'turn-gone' WHERE operation_id = 'begin-ruleless'"]},
    {"name": "operation_basis_after_result", "base": "constructed", "sql": [
        "UPDATE campaign_operations SET basis_revision = 3 WHERE operation_id = 'begin-ruleless'"]},
    {"name": "operation_orphaned", "base": "constructed", "sql": [
        "INSERT INTO campaign_operations (operation_id, operation_kind, campaign_id, semantic_payload_json, "
        "semantic_payload_sha256, result_json, basis_turn_id, basis_lock_id, basis_revision, resulting_revision) "
        "SELECT 'confirm-spare', operation_kind, campaign_id, semantic_payload_json, semantic_payload_sha256, "
        "result_json, basis_turn_id, basis_lock_id, basis_revision, resulting_revision "
        "FROM campaign_operations WHERE operation_id = 'confirm-interview'"], "edits": [
        ("confirm-spare", "semantic_payload", lambda p: edited(p, operation_id="confirm-spare"))]},
    # Saved construction inputs.
    {"name": "construction_begin_revision_other", "base": "constructed", "sql": [
        "UPDATE campaign_constructions SET begin_revision = 2 WHERE begin_operation_id = 'begin-ruleless'"]},
    {"name": "construction_begin_revision_text", "base": "constructed", "sql": [
        "UPDATE campaign_constructions SET begin_revision = 'one' WHERE begin_operation_id = 'begin-ruleless'"]},
    {"name": "construction_input_changed", "base": "constructed", "sql": [
        "UPDATE campaign_constructions SET canonical_input_json = '{\"fixture\":\"other\"}' "
        "WHERE begin_operation_id = 'begin-ruleless'"]},
    {"name": "construction_input_not_canonical", "base": "constructed", "sql": [
        "UPDATE campaign_constructions SET canonical_input_json = '{\"fixture\": \"campaign-index\"}' "
        "WHERE begin_operation_id = 'begin-ruleless'"]},
    {"name": "construction_plan_changed", "base": "constructed", "sql": [
        "UPDATE campaign_constructions SET canonical_plan_json = '{\"campaign_plan_markdown\":\"# Other\\n\"}' "
        "WHERE begin_operation_id = 'begin-ruleless'"]},
    {"name": "construction_representation_other", "base": "constructed", "sql": [
        "UPDATE campaign_constructions SET canonical_representation_sha256 = '" + "b" * 64 + "' "
        "WHERE begin_operation_id = 'begin-ruleless'"]},
    {"name": "construction_lane_other", "base": "constructed", "sql": [
        "UPDATE campaign_constructions SET lane = 'quick_start' WHERE begin_operation_id = 'begin-ruleless'"]},
    {"name": "unbegun_with_lane", "base": "constructed", "sql": [
        "UPDATE campaign_constructions SET lane = 'customize' WHERE confirmation_operation_id = 'confirm-interview'"]},
    {"name": "incomplete_with_plan", "base": "constructed", "sql": [
        "UPDATE campaign_constructions SET completion_operation_id = NULL WHERE begin_operation_id = 'begin-ruleless'"]},
    {"name": "construction_confirmation_revision_other", "base": "constructed", "sql": [
        "UPDATE campaign_constructions SET confirmation_revision = 1 WHERE confirmation_operation_id = 'confirm-interview'"]},
    # The signed confirmation payload.
    {"name": "confirmation_display_name_edited", "base": "constructed", "sql": [], "edits": [
        ("confirm-interview", "semantic_payload", lambda p: edited(p, display_name="Another Name"))]},
    {"name": "confirmation_display_name_blank", "base": "constructed", "sql": [], "edits": [
        ("confirm-interview", "semantic_payload", lambda p: edited(p, display_name=" "))]},
    {"name": "confirmation_display_name_missing", "base": "constructed", "sql": [], "edits": [
        ("confirm-interview", "semantic_payload", lambda p: edited(p, display_name=DROP))]},
    {"name": "confirmation_turn_other", "base": "constructed", "sql": [], "edits": [
        ("confirm-interview", "semantic_payload", lambda p: edited(p, turn_id="turn-other"))]},
    {"name": "confirmation_lock_payload_changed", "base": "constructed", "sql": [], "edits": [
        ("confirm-ruleless", "semantic_payload", lambda p: edited(p, package_lock=[{"package_id": "pneuma.other"}]))]},
    {"name": "confirmation_extra_member", "base": "constructed", "sql": [], "edits": [
        ("confirm-interview", "semantic_payload", lambda p: edited(p, note="x"))]},
    {"name": "confirmation_participants_signed", "base": "constructed", "sql": [], "edits": [
        ("confirm-interview", "semantic_payload", lambda p: edited(p, participants=[]))]},
    {"name": "confirmation_participants_differ", "base": "constructed", "sql": [], "edits": [
        ("confirm-interview", "semantic_payload", lambda p: edited(p, participants=[{"participant_id": "hero"}]))]},
    {"name": "confirmation_initial_state_list", "base": "constructed", "sql": [], "edits": [
        ("confirm-interview", "semantic_payload", lambda p: edited(p, initial_state=[]))]},
    {"name": "confirmation_initial_state_object", "base": "constructed", "sql": [], "edits": [
        ("confirm-interview", "semantic_payload", lambda p: edited(p, initial_state={}))]},
    {"name": "confirmation_candidate_number", "base": "constructed", "sql": [], "edits": [
        ("confirm-interview", "semantic_payload", lambda p: edited(p, package_candidate_id=5))]},
    {"name": "confirmation_candidate_unknown", "base": "constructed", "sql": [], "edits": [
        ("confirm-interview", "semantic_payload", lambda p: edited(p, package_candidate_id="candidate-gone"))]},
    {"name": "confirmation_builder_not_object", "base": "constructed", "sql": [], "edits": [
        ("confirm-interview", "semantic_payload", lambda p: edited(p, builder_session="builder-interview"))]},
    {"name": "confirmation_builder_skill_other", "base": "constructed", "sql": [], "edits": [
        ("confirm-interview", "semantic_payload",
         lambda p: edited(p, builder_session=edited(p["builder_session"], selected_skill="BUILD RULESET")))]},
    {"name": "confirmation_builder_skill_unknown", "base": "constructed", "sql": [], "edits": [
        ("confirm-interview", "semantic_payload",
         lambda p: edited(p, builder_session=edited(p["builder_session"], selected_skill="BUILD CASTLE")))]},
    {"name": "confirmation_builder_session_unknown", "base": "constructed", "sql": [], "edits": [
        ("confirm-interview", "semantic_payload",
         lambda p: edited(p, builder_session=edited(p["builder_session"], session_id="builder-gone")))]},
    {"name": "confirmation_builder_provider_padded", "base": "constructed", "sql": [], "edits": [
        ("confirm-interview", "semantic_payload",
         lambda p: edited(p, builder_session=edited(p["builder_session"], provider_id=" synthetic.provider")))]},
    {"name": "confirmation_builder_model_number", "base": "constructed", "sql": [], "edits": [
        ("confirm-interview", "semantic_payload",
         lambda p: edited(p, builder_session=edited(p["builder_session"], model_id=3)))]},
    {"name": "confirmation_builder_dropped", "base": "constructed", "sql": [], "edits": [
        ("confirm-interview", "semantic_payload", lambda p: edited(p, builder_session=DROP))]},
    # Continuity and lifecycle receipts.
    {"name": "continuity_session_dropped", "base": "story", "sql": [], "edits": [
        ("op-1", "semantic_payload", lambda p: edited(p, session_id=DROP))]},
    {"name": "continuity_status_other", "base": "story", "sql": [], "edits": [
        ("op-1", "semantic_payload", lambda p: edited(p, status="withdrawn"))]},
    {"name": "continuity_result_record_other", "base": "story", "sql": [], "edits": [
        ("op-1", "result", lambda r: edited(r, record_id="record-other"))]},
    {"name": "continuity_resulting_two_ahead", "base": "story", "sql": [
        "UPDATE campaign_operations SET basis_revision = 1 WHERE operation_id = 'op-1'"]},
    {"name": "archived_transition_session_dropped", "base": "archived", "sql": [
        "UPDATE campaign_lifecycle_transitions SET designated_session_id = NULL"]},
    {"name": "archived_result_writer_other", "base": "archived", "sql": [], "edits": [
        ("life-0", "result", lambda r: edited(r, writer="dm"))]},
    {"name": "archived_result_lifecycle_unknown", "base": "archived", "sql": [], "edits": [
        ("life-0", "result", lambda r: edited(r, lifecycle="gone"))]},
    {"name": "archived_result_session_number", "base": "archived", "sql": [], "edits": [
        ("life-0", "result", lambda r: edited(r, session_id=5))]},
    {"name": "archived_result_extra_key", "base": "archived", "sql": [], "edits": [
        ("life-0", "result", lambda r: edited(r, note="x"))]},
    {"name": "archived_operation_basis_other", "base": "archived", "sql": [
        "UPDATE campaign_operations SET basis_revision = 7 WHERE operation_id = 'life-0'"]},
    # A failed action's one consequence must be the owner's player-action marker.
    {"name": "failed_untouched", "base": "failed", "sql": []},
    {"name": "failed_marker_revision_other", "base": "failed", "sql": [], "markers": [origin(expected_revision=7)]},
    {"name": "failed_marker_revision_true", "base": "failed", "sql": [], "markers": [origin(expected_revision=True)]},
    {"name": "failed_marker_revision_float", "base": "failed", "sql": [], "markers": [origin(expected_revision=8.0)]},
    {"name": "failed_marker_policy_unknown", "base": "failed", "sql": [], "markers": [
        origin(policy={"confirmation": "none", "response": "never"})]},
    {"name": "failed_marker_policy_extra", "base": "failed", "sql": [], "markers": [
        origin(policy={"confirmation": "required", "response": "automatic", "note": "x"})]},
    {"name": "failed_marker_policy_required", "base": "failed", "sql": [], "markers": [
        origin(policy={"confirmation": "required", "response": "automatic"})]},
    {"name": "failed_marker_button_out_of_range", "base": "failed", "sql": [], "markers": [origin(button_index=9)]},
    {"name": "failed_marker_session_padded", "base": "failed", "sql": [], "markers": [origin(session_id=" dm-1")]},
    {"name": "failed_marker_session_named", "base": "failed", "sql": [], "markers": [origin(session_id="dm-1")]},
    {"name": "failed_marker_selection_list", "base": "failed", "sql": [], "markers": [origin(selection=[])]},
    {"name": "failed_marker_extra_member", "base": "failed", "sql": [], "markers": [origin(note="x")]},
    {"name": "failed_marker_panel_control", "base": "failed", "sql": [], "markers": [
        origin(control="panel", surface_id="surface-1", panel_action_id="act-1",
               source_action_id=DROP, button_index=DROP)]},
    {"name": "failed_marker_kind_other", "base": "failed", "sql": [], "markers": [
        lambda marker: edited(marker, kind="check_failed")]},
    {"name": "failed_marker_without_origin", "base": "failed", "sql": [], "markers": [
        lambda marker: edited(marker, origin=DROP)]},
    {"name": "failed_marker_replaced", "base": "failed", "sql": [], "markers": [
        lambda marker: {"kind": "check_rolled", "total": 3}]},
    {"name": "failed_marker_origin_list", "base": "failed", "sql": [], "markers": [
        lambda marker: edited(marker, origin=[])]},
    *F5_CASES,
    *RECOVERY_CASES,
]


def outcome(path: Path) -> dict[str, object]:
    try:
        store = a._open_for_application_owner(path)
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}
    store.close()
    return {"ok": None}


def f5_outcome(path: Path) -> dict[str, object]:
    """`verify_f5_campaign_authority` called directly, since startup reports any refusal as
    "Authority stored integrity could not be verified"."""
    from sylvia_gm.infrastructure import sqlite_campaign

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        sqlite_campaign.verify_f5_campaign_authority(connection)
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "message": str(error)}
    finally:
        connection.close()
    return {"ok": None}


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    results = []
    with tempfile.TemporaryDirectory(dir=HERE, ignore_cleanup_errors=True) as scratch:
        bases = {name: gzip.decompress(path.read_bytes()) for name, path in BASES.items()}
        bases["children"] = children_base(Path(scratch))
        (OUT / "children.sqlite3.gz").write_bytes(gzip.compress(bases["children"], mtime=0))
        bases["failed"] = failed_base(Path(scratch))
        (OUT / "failed.sqlite3.gz").write_bytes(gzip.compress(bases["failed"], mtime=0))
        bases["cast"] = cast_base(Path(scratch))
        bases["recovered"] = recovered_base(Path(scratch))
        (OUT / "recovered.sqlite3.gz").write_bytes(gzip.compress(bases["recovered"], mtime=0))
        (OUT / "cast.sqlite3.gz").write_bytes(gzip.compress(bases["cast"], mtime=0))
        for name, kinds in LIFECYCLE_BASES.items():
            bases[name] = lifecycle_base(Path(scratch), name, kinds)
            (OUT / f"{name}.sqlite3.gz").write_bytes(gzip.compress(bases[name], mtime=0))
        for case in CASES:
            folder = Path(scratch) / str(case["name"])
            folder.mkdir()
            path = folder / "authority.sqlite3"
            path.write_bytes(bases[str(case["base"])])
            connection = sqlite3.connect(path, isolation_level=None)
            try:
                connection.execute("PRAGMA foreign_keys = OFF")
                # Damage arrives past the tables' CHECK constraints too; the verifiers must see it.
                statements = [CHECKS_OFF, *case["sql"]]  # type: ignore[misc]
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
                # Payload edits read the damaged row, so they follow the case's SQL.
                for edit in case.pop("edits", ()):  # type: ignore[attr-defined]
                    statements.append(payload_edit(connection, *edit))
                    connection.execute(statements[-1])
                for change in case.pop("markers", ()):  # type: ignore[attr-defined]
                    for statement in marker_sql(connection, change):
                        statements.append(statement)
                        connection.execute(statement)
                for build in case.pop("computed", ()):  # type: ignore[attr-defined]
                    for statement in build(connection):
                        statements.append(statement)
                        connection.execute(statement)
            finally:
                connection.close()
            # The verifier's own outcome first: startup may write to the profile it opens.
            f5 = {"f5": f5_outcome(path)} if case["base"] == "cast" else {}
            results.append({**case, "sql": statements, "outcome": outcome(path), **f5})
    record = {"python": sys.version.split()[0], "cases": results}
    (OUT / "stored_authority_runs.json").write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
