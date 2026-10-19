"""Campaign-owned continuity records and their materialized current view."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from typing import cast

from sylvia_gm.domain.authority import (
    CampaignContinuityRecord,
    CampaignContinuityView,
    SafePayload,
    owned_payload,
    thaw_json_value,
)
from sylvia_gm.domain.errors import SchemaIdentityError
from sylvia_gm.infrastructure.sqlite_sequel import adventure_rows

MIGRATION_9_NAME = "campaign_state_continuity"
MIGRATION_9_SQL = """
ALTER TABLE campaign_revisions
ADD COLUMN continuity_operation_id TEXT REFERENCES campaign_operations(operation_id);
CREATE TABLE campaign_continuity_records (
    campaign_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    entry_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('scene', 'fact', 'entity', 'reveal', 'plan_progress')),
    status TEXT NOT NULL CHECK (status IN ('active', 'retired')),
    payload_json TEXT NOT NULL,
    basis_message_ids_json TEXT NOT NULL,
    operation_id TEXT,
    admitted_revision INTEGER NOT NULL CHECK (admitted_revision >= 0),
    PRIMARY KEY (campaign_id, sequence),
    UNIQUE (campaign_id, entry_id),
    UNIQUE (operation_id),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (operation_id) REFERENCES campaign_operations(operation_id)
);
CREATE INDEX campaign_continuity_record_lookup
ON campaign_continuity_records(campaign_id, kind, record_id, sequence);
"""
MIGRATION_9_SHA256 = hashlib.sha256(MIGRATION_9_SQL.strip().encode("utf-8")).hexdigest()


def _dump(value: object) -> str:
    return json.dumps(
        thaw_json_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _load_mapping(value: object) -> dict[str, object] | None:
    try:
        loaded = json.loads(str(value))
    except (TypeError, ValueError):
        return None
    return dict(loaded) if isinstance(loaded, Mapping) else None


def _next_sequence(connection: sqlite3.Connection, campaign_id: str) -> int:
    return int(
        connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM campaign_continuity_records "
            "WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()[0]
    )


def append_record(
    connection: sqlite3.Connection,
    *,
    campaign_id: str,
    entry_id: str,
    record_id: str,
    kind: str,
    status: str,
    payload: Mapping[str, object],
    basis_message_ids: Sequence[str],
    operation_id: str | None,
    admitted_revision: int,
) -> None:
    """Append one immutable continuity change inside the caller's transaction."""

    connection.execute(
        "INSERT INTO campaign_continuity_records "
        "(campaign_id, sequence, entry_id, record_id, kind, status, payload_json, "
        "basis_message_ids_json, operation_id, admitted_revision) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            campaign_id,
            _next_sequence(connection, campaign_id),
            entry_id,
            record_id,
            kind,
            status,
            _dump(dict(payload)),
            _dump(list(basis_message_ids)),
            operation_id,
            admitted_revision,
        ),
    )


def _initial_entry_id(campaign_id: str, record_id: str, kind: str) -> str:
    digest = hashlib.sha256(f"{campaign_id}\0{kind}\0{record_id}".encode()).hexdigest()[:32]
    return f"initial-{digest}"


def seed_initial_state(
    connection: sqlite3.Connection,
    *,
    campaign_id: str,
    plan: Mapping[str, object],
    admitted_revision: int,
) -> None:
    """Materialize accepted initial Plan facts without promoting future preparation."""

    if connection.execute(
        "SELECT 1 FROM campaign_continuity_records WHERE campaign_id = ? LIMIT 1",
        (campaign_id,),
    ).fetchone() is not None:
        return
    structure = plan.get("structure")
    first_act_id = None
    if isinstance(structure, Sequence) and not isinstance(structure, (str, bytes, bytearray)):
        first_act = next((item for item in structure if isinstance(item, Mapping)), None)
        if isinstance(first_act, Mapping) and isinstance(first_act.get("id"), str):
            first_act_id = str(first_act["id"])
    starting_location = plan.get("starting_location")
    if isinstance(starting_location, Mapping):
        scene_payload = {
            "act_id": first_act_id,
            "location_id": starting_location.get("id"),
            "starting_location": dict(starting_location),
            "summary": None,
            "origin": "accepted_initial_state",
        }
        append_record(
            connection,
            campaign_id=campaign_id,
            entry_id=_initial_entry_id(campaign_id, "current_scene", "scene"),
            record_id="current_scene",
            kind="scene",
            status="active",
            payload=scene_payload,
            basis_message_ids=(),
            operation_id=None,
            admitted_revision=admitted_revision,
        )
    initial_state = plan.get("initial_state")
    facts = initial_state.get("facts") if isinstance(initial_state, Mapping) else None
    if not isinstance(facts, Sequence) or isinstance(facts, (str, bytes, bytearray)):
        return
    for index, raw_fact in enumerate(facts):
        if isinstance(raw_fact, str):
            fact_text = raw_fact
            known_by = ["player"]
            record_id = f"initial-fact-{index}"
        elif isinstance(raw_fact, Mapping) and isinstance(raw_fact.get("fact"), str):
            fact_text = str(raw_fact["fact"])
            raw_known_by = raw_fact.get("known_by", [])
            known_by = (
                [str(item) for item in raw_known_by if isinstance(item, str)]
                if isinstance(raw_known_by, Sequence)
                and not isinstance(raw_known_by, (str, bytes, bytearray))
                else []
            )
            record_id = (
                str(raw_fact["id"])
                if isinstance(raw_fact.get("id"), str)
                else f"initial-fact-{index}"
            )
        else:
            continue
        payload = {
            "category": "continuity",
            "text": fact_text,
            "visibility": "player_known" if "player" in known_by else "gm_private",
            "known_by": known_by,
            "subjects": [],
            "origin": "accepted_initial_state",
        }
        append_record(
            connection,
            campaign_id=campaign_id,
            entry_id=_initial_entry_id(campaign_id, record_id, "fact"),
            record_id=record_id,
            kind="fact",
            status="active",
            payload=payload,
            basis_message_ids=(),
            operation_id=None,
            admitted_revision=admitted_revision,
        )


def _backfill_legacy_messages(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT session.campaign_id, message.session_id, message.message_id, "
        "message.content_json FROM agent_session_messages AS message "
        "JOIN agent_sessions AS session ON session.session_id = message.session_id "
        "WHERE session.role = 'dm' AND session.campaign_id IS NOT NULL "
        "AND message.kind = 'evidence' ORDER BY message.rowid"
    )
    for row in rows:
        content = _load_mapping(row["content_json"])
        if content is None:
            continue
        legacy_kind = content.get("kind")
        if legacy_kind == "campaign_scene" and isinstance(content.get("act_id"), str):
            kind = "scene"
            record_id = "current_scene"
            payload = {
                "act_id": content.get("act_id"),
                "location_id": content.get("location_id"),
                "summary": content.get("summary"),
                "origin": "legacy_session_evidence",
            }
        elif legacy_kind == "campaign_memory" and isinstance(content.get("fact_id"), str):
            kind = "fact"
            record_id = str(content["fact_id"])
            payload = {
                "category": content.get("category"),
                "text": content.get("text"),
                "visibility": content.get("visibility"),
                "known_by": content.get("known_by", []),
                "subjects": content.get("subjects", []),
                "origin": "legacy_session_evidence",
            }
        elif legacy_kind == "campaign_secret_reveal" and isinstance(
            content.get("secret_id"), str
        ):
            kind = "reveal"
            record_id = str(content["secret_id"])
            payload = {
                "secret_id": record_id,
                "audience": content.get("audience", []),
                "origin": "legacy_session_evidence",
            }
        else:
            continue
        campaign_id = str(row["campaign_id"])
        legacy_key = f"{row['session_id']}\0{row['message_id']}"
        entry_id = f"legacy-{hashlib.sha256(legacy_key.encode()).hexdigest()[:32]}"
        basis = content.get("basis_message_ids", [])
        basis_ids = (
            [str(item) for item in basis if isinstance(item, str)]
            if isinstance(basis, Sequence) and not isinstance(basis, (str, bytes, bytearray))
            else []
        )
        revision = int(
            connection.execute(
                "SELECT revision FROM campaigns WHERE campaign_id = ?", (campaign_id,)
            ).fetchone()[0]
        )
        append_record(
            connection,
            campaign_id=campaign_id,
            entry_id=entry_id,
            record_id=record_id,
            kind=kind,
            status="active",
            payload=payload,
            basis_message_ids=basis_ids,
            operation_id=None,
            admitted_revision=revision,
        )


def apply_migration(connection: sqlite3.Connection) -> None:
    """Install Campaign continuity and materialize accepted initial and legacy state."""

    for statement in MIGRATION_9_SQL.split(";"):
        if statement.strip():
            connection.execute(statement)
    for row in connection.execute(
        "SELECT plan.campaign_id, plan.content_json, plan.resulting_campaign_revision "
        "FROM campaign_plans AS plan JOIN ("
        "SELECT campaign_id, MAX(plan_revision) AS plan_revision FROM campaign_plans "
        "WHERE acceptance_status = 'accepted' GROUP BY campaign_id"
        ") AS accepted ON accepted.campaign_id = plan.campaign_id "
        "AND accepted.plan_revision = plan.plan_revision ORDER BY plan.campaign_id"
    ):
        content = _load_mapping(row["content_json"])
        structured = content.get("campaign_plan") if content is not None else None
        if isinstance(structured, Mapping):
            seed_initial_state(
                connection,
                campaign_id=str(row["campaign_id"]),
                plan=cast(Mapping[str, object], structured),
                admitted_revision=int(row["resulting_campaign_revision"]),
            )
    _backfill_legacy_messages(connection)


def read_view(connection: sqlite3.Connection, campaign_id: str) -> CampaignContinuityView:
    records: list[CampaignContinuityRecord] = []
    current_scene: SafePayload | None = None
    current_facts: dict[str, SafePayload] = {}
    current_entities: dict[str, SafePayload] = {}
    current_progress: dict[str, SafePayload] = {}
    reveal_audiences: dict[str, set[str]] = {}
    for row in connection.execute(
        "SELECT * FROM campaign_continuity_records WHERE campaign_id = ? ORDER BY sequence",
        (campaign_id,),
    ):
        payload_raw = _load_mapping(row["payload_json"])
        try:
            basis_raw = json.loads(str(row["basis_message_ids_json"]))
        except (TypeError, ValueError) as error:
            raise SchemaIdentityError("Campaign continuity provenance is invalid") from error
        if payload_raw is None or not isinstance(basis_raw, list) or not all(
            isinstance(item, str) for item in basis_raw
        ):
            raise SchemaIdentityError("Campaign continuity record is invalid")
        payload = owned_payload(payload_raw)
        kind = str(row["kind"])
        record_id = str(row["record_id"])
        status = str(row["status"])
        records.append(
            CampaignContinuityRecord(
                entry_id=str(row["entry_id"]),
                record_id=record_id,
                kind=kind,
                status=status,
                payload=payload,
                basis_message_ids=tuple(str(item) for item in basis_raw),
                admitted_revision=int(row["admitted_revision"]),
                operation_id=(
                    None if row["operation_id"] is None else str(row["operation_id"])
                ),
            )
        )
        if kind == "scene":
            current_scene = payload if status == "active" else None
        elif kind == "fact":
            if status == "active":
                current_facts[record_id] = owned_payload({
                    **payload_raw, "record_id": record_id, "record_kind": kind,
                    "admitted_revision": int(row["admitted_revision"]),
                })
            else:
                current_facts.pop(record_id, None)
        elif kind == "entity":
            if status == "active":
                current_entities[record_id] = owned_payload(
                    {
                        **payload_raw, "record_id": record_id, "record_kind": kind,
                        "admitted_revision": int(row["admitted_revision"]),
                    }
                )
            else:
                current_entities.pop(record_id, None)
        elif kind == "plan_progress":
            if status == "active":
                current_progress[record_id] = owned_payload(
                    {"record_id": record_id, **payload_raw}
                )
            else:
                current_progress.pop(record_id, None)
        elif kind == "reveal" and status == "active":
            audience = payload_raw.get("audience", [])
            if isinstance(audience, Sequence) and not isinstance(
                audience, (str, bytes, bytearray)
            ):
                reveal_audiences.setdefault(record_id, set()).update(
                    str(item) for item in audience if isinstance(item, str)
                )
    facts = tuple(current_facts[key] for key in sorted(current_facts)) + tuple(
        current_entities[key] for key in sorted(current_entities)
    )
    return CampaignContinuityView(
        current_scene=current_scene,
        facts=facts,
        reveals=owned_payload(
            {key: sorted(value) for key, value in sorted(reveal_audiences.items())}
        ),
        plan_progress=tuple(current_progress[key] for key in sorted(current_progress)),
        records=tuple(records),
    )


def _act_marker_anchor(
    connection: sqlite3.Connection, session_id: str, basis_json: str,
) -> str | None:
    """Place a chapter marker after saved narration for its committed response basis."""
    basis = connection.execute(
        "SELECT MAX(sequence) FROM agent_session_messages WHERE session_id = ? "
        "AND message_id IN (SELECT value FROM json_each(?))", (session_id, basis_json),
    ).fetchone()[0]
    if basis is None:
        return None
    next_input = connection.execute(
        "SELECT MIN(sequence) FROM agent_session_messages WHERE session_id = ? "
        "AND sequence > ? AND (kind = 'user' OR json_extract(content_json, '$.kind') "
        "IN ('campaign_start', 'player_action_response'))", (session_id, basis),
    ).fetchone()[0]
    row = connection.execute(
        "SELECT message_id FROM agent_session_messages WHERE session_id = ? "
        "AND sequence >= ? AND (? IS NULL OR sequence < ?) "
        "AND kind = 'canonical_reference' "
        "AND json_extract(content_json, '$.event_type') = 'talk' "
        "ORDER BY sequence DESC LIMIT 1", (session_id, basis, next_input, next_input),
    ).fetchone()
    return str(row[0]) if row is not None else None


def read_act_markers(
    connection: sqlite3.Connection, session_id: str, campaign_id: str,
) -> tuple[SafePayload, ...]:
    """Project saved chapter boundaries as ordinals, never private Plan titles or summaries.

    Completion requires an explicit resolved act. Beginning requires a saved scene in that act;
    a future lead's introduced progress is insufficient. Existing records provide durability and
    replay identity, so displaying a chapter never writes state or advances the story.
    """
    structure = [act for row in adventure_rows(connection, campaign_id)
                 for act in json.loads(row['content_json']).get('campaign_plan', {}).get('structure', ())]
    if not isinstance(structure, list):
        return ()
    ordinals = {
        act["id"]: index + 1 for index, act in enumerate(structure)
        if isinstance(act, dict) and isinstance(act.get("id"), str)
    }
    begun: set[str] = set()
    completed: set[str] = set()
    markers: list[SafePayload] = []
    anchors: dict[str, str | None] = {}
    for row in connection.execute(
        "SELECT kind, record_id, operation_id, basis_message_ids_json, "
        "json_extract(payload_json, '$.act_id') AS act_id "
        "FROM campaign_continuity_records WHERE campaign_id = ? AND status = 'active' "
        "AND (kind = 'scene' OR (kind = 'plan_progress' "
        "AND json_extract(payload_json, '$.status') = 'resolved')) ORDER BY sequence",
        (campaign_id,),
    ):
        is_scene = row["kind"] == "scene"
        act_id = row["act_id"] if is_scene else row["record_id"]
        if act_id not in ordinals:
            continue
        seen = begun if is_scene else completed
        if act_id in seen:
            continue
        # The initial seeded scene establishes the first act without a synthetic announcement.
        if row["operation_id"] is None:
            if is_scene:
                seen.add(act_id)
            continue
        basis_json = str(row["basis_message_ids_json"])
        if basis_json not in anchors:
            anchors[basis_json] = _act_marker_anchor(connection, session_id, basis_json)
        anchor = anchors[basis_json]
        if anchor is None:
            continue
        seen.add(act_id)
        status = "begins" if is_scene else "complete"
        ordinal = ordinals[act_id]
        markers.append(owned_payload({
            "marker_id": f"act-{ordinal}-{status}",
            "after_message_id": anchor,
            "act_number": ordinal,
            "status": status,
        }))
    return tuple(markers)


def read_campaign_ending(
    connection: sqlite3.Connection, session_id: str, campaign_id: str,
    *, plan_row: sqlite3.Row | None = None, act_offset: int = 0,
) -> SafePayload | None:
    """Project one earned ending and its user choice without leaking private ending prose."""
    from sylvia_gm.domain.campaign_ending import (
        ending_progress_id,
        intended_endings,
        required_ending_items,
        required_story_items,
        resolved_progress,
    )

    if plan_row is None:
        adventures = adventure_rows(connection, campaign_id)
        if not adventures:
            return None
        plan_row = adventures[-1]
        act_offset = sum(len(json.loads(row['content_json']).get('campaign_plan', {}).get('structure', ()))
                         for row in adventures[:-1])
    plan = json.loads(plan_row['content_json']).get('campaign_plan')
    if not isinstance(plan, dict):
        return None
    endings = {ending_progress_id(item): item for item in intended_endings(plan)}
    if not endings:
        return None
    rows = connection.execute(
        "SELECT record_id, payload_json, basis_message_ids_json FROM campaign_continuity_records "
        "WHERE campaign_id = ? AND kind = 'plan_progress' AND status = 'active' ORDER BY sequence",
        (campaign_id,),
    ).fetchall()
    progress = [json.loads(item["payload_json"]) for item in rows]
    resolved = resolved_progress(progress)
    reached = next((item for item in reversed(rows) if item["record_id"] in endings
                    and item["record_id"] in resolved), None)
    if reached is None:
        return None
    ending = endings[reached["record_id"]]
    if not required_ending_items(plan, ending) <= resolved:
        return None
    first = next(item for item in rows if item["record_id"] == reached["record_id"]
                 and json.loads(item["payload_json"]).get("status") == "resolved")
    anchor = _act_marker_anchor(connection, session_id, str(first["basis_message_ids_json"]))
    if anchor is None:
        return None
    choice_row = connection.execute(
        "SELECT json_extract(content_json, '$.campaign_ending_choice.choice') "
        "FROM agent_session_messages WHERE session_id = ? AND kind = 'user' "
        "AND json_extract(content_json, '$.campaign_ending_choice.ending_id') = ? "
        "ORDER BY sequence DESC LIMIT 1", (session_id, ending["id"]),
    ).fetchone()
    return owned_payload({
        "ending_id": ending["id"], "after_message_id": anchor,
        "act_number": act_offset + len(plan["structure"]) if required_story_items(plan) <= resolved else None,
        "plan_title": plan["title"],
        "open_world": ending["open_world"], "has_after_story": ending["after_story"] is not None,
        "choice": choice_row[0] if choice_row is not None else None,
    })


def read_campaign_endings(
    connection: sqlite3.Connection, session_id: str, campaign_id: str,
) -> tuple[SafePayload, ...]:
    """Retain previous completion cards and choices while the next adventure is active."""
    adventures = adventure_rows(connection, campaign_id)
    result = []
    offset = 0
    for index, row in enumerate(adventures):
        ending = read_campaign_ending(connection, session_id, campaign_id,
                                      plan_row=row, act_offset=offset)
        if ending is not None:
            result.append(owned_payload({**ending, **({'historical': True}
                          if index < len(adventures) - 1 else {})}))
        offset += len(json.loads(row['content_json']).get('campaign_plan', {}).get('structure', ()))
    return tuple(result)


def verify_authority(connection: sqlite3.Connection) -> None:
    """Fail closed on malformed continuity records or broken operation ownership."""

    campaigns = {
        str(row[0])
        for row in connection.execute(
            "SELECT DISTINCT campaign_id FROM campaign_continuity_records ORDER BY campaign_id"
        )
    }
    for campaign_id in campaigns:
        view = read_view(connection, campaign_id)
        sequences = tuple(
            int(row[0])
            for row in connection.execute(
                "SELECT sequence FROM campaign_continuity_records WHERE campaign_id = ? "
                "ORDER BY sequence",
                (campaign_id,),
            )
        )
        if sequences != tuple(range(1, len(sequences) + 1)):
            raise SchemaIdentityError("Campaign continuity sequence is invalid")
        del view
    orphan = connection.execute(
        "SELECT 1 FROM campaign_continuity_records AS record "
        "LEFT JOIN campaign_operations AS operation "
        "ON operation.operation_id = record.operation_id "
        "WHERE record.operation_id IS NOT NULL AND (operation.operation_id IS NULL "
        "OR operation.operation_kind != 'commit_campaign_continuity' "
        "OR operation.campaign_id != record.campaign_id "
        "OR operation.resulting_revision != record.admitted_revision) LIMIT 1"
    ).fetchone()
    if orphan is not None:
        raise SchemaIdentityError("Campaign continuity operation authority is invalid")
