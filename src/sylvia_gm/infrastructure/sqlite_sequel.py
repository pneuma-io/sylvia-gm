"""Atomic, revision-fenced admission of reviewed authored campaign sequels."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping

from sylvia_gm.domain.authority import WriterRole
from sylvia_gm.domain.campaign_sequel import (
    MAX_ADVENTURES,
    digest,
    json_object,
    prepare_story,
    review_payload,
)
from sylvia_gm.domain.errors import CommandRejected, IdempotencyConflict, StaleRevision, WrongWriter
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.domain.session import CampaignPlan, SessionMessageKind, SessionStatus


def adventure_rows(connection: sqlite3.Connection, campaign_id: str) -> tuple[sqlite3.Row, ...]:
    """Follow only the accepted append lineage; earlier Builder drafts are not adventures."""
    row = connection.execute(
        "SELECT * FROM campaign_plans WHERE campaign_id = ? "
        "AND acceptance_status = 'accepted' ORDER BY plan_revision DESC LIMIT 1",
        (campaign_id,),
    ).fetchone()
    result: list[sqlite3.Row] = []
    seen = set()
    while row is not None:
        if row["plan_id"] in seen or len(result) >= MAX_ADVENTURES:
            raise ValueError("Campaign adventure lineage is invalid or exceeds its bound")
        seen.add(row["plan_id"])
        result.append(row)
        metadata = json.loads(row["content_json"]).get("sequel")
        if not isinstance(metadata, Mapping):
            break
        previous = connection.execute(
            "SELECT * FROM campaign_plans WHERE plan_id = ? AND campaign_id = ?",
            (metadata["previous_plan_id"], campaign_id),
        ).fetchone()
        if previous is None or previous["plan_revision"] >= row["plan_revision"]:
            raise ValueError("Campaign adventure predecessor is unavailable")
        row = previous
    return tuple(reversed(result))


def verify_sequel(plan: CampaignPlan, previous: CampaignPlan) -> None:
    """Verify exact retained source, reviewed mapping and predecessor without library access."""
    metadata = json_object(plan.content["sequel"])
    previous_story = json_object(previous.content["campaign_plan"])
    previous_metadata = json_object(previous.content.get("sequel", {}))
    basis, request = metadata["preview"], metadata["request"]
    review_payload(request["review"])
    if (
        metadata["previous_plan_id"] != previous.plan_id
        or plan.campaign_id != previous.campaign_id
        or plan.package_lock_id != previous.package_lock_id
        or basis["previous_plan_sha256"] != previous.content_sha256
        or basis["previous_plan_id"] != previous.plan_id
        or basis["expected_revision"] != plan.resulting_campaign_revision - 1
        or request["expected_revision"] != basis["expected_revision"]
        or request["source_id"] != basis["source_id"]
        or metadata["source_id"] != basis["source_id"]
        or request["session_id"] != plan.source_session_id
        or basis["session_id"] != plan.source_session_id
        or basis["campaign_id"] != plan.campaign_id
        or basis["number"] != previous_metadata.get("preview", {}).get("number", 1) + 1
        or plan.plan_revision != previous.plan_revision + 1
        or request["preview_sha256"] != digest(basis)
        or digest(metadata["source_plan"]) != basis["source_plan_sha256"]
        or digest(plan.content["campaign_plan_markdown"]) != basis["source_markdown_sha256"]
        or metadata["source_plan"]["bindings"] != previous_story["bindings"]
    ):
        raise ValueError("Campaign sequel source or review identity is invalid")
    if (
        plan.plan_id
        != "campaign-sequel-"
        + digest(
            {
                "campaign": plan.campaign_id,
                "operation": request["operation_id"],
            }
        )[:32]
    ):
        raise ValueError("Campaign sequel request identity is invalid")
    prepared, mapping = prepare_story(metadata["source_plan"], basis["number"])
    expected = dict(json_object(prepared))
    expected["boundaries"] = list(
        dict.fromkeys(
            (
                *previous_story.get("boundaries", ()),
                *json_object(prepared).get("boundaries", ()),
            )
        )
    )
    if (
        digest(expected) != digest(plan.content["campaign_plan"])
        or dict(metadata["identity_mapping"]) != mapping
    ):
        raise ValueError("Campaign sequel changed its reviewed preparation or identity mapping")


def commit_sequel(connection: sqlite3.Connection, plan: CampaignPlan) -> CampaignPlan:
    """Commit preparation and its application evidence together; never write live game state."""
    from sylvia_gm.infrastructure import sqlite_session

    metadata = json_object(plan.content["sequel"])
    basis, request = metadata["preview"], metadata["request"]
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = sqlite_session.read_plan(connection, plan.plan_id)
        if existing is not None:
            if existing.payload_sha256 != plan.payload_sha256:
                raise IdempotencyConflict("Campaign sequel request identity has different content")
            connection.rollback()
            return existing
        campaign = connection.execute(
            "SELECT lifecycle, writer, revision, current_lock_id "
            "FROM campaigns WHERE campaign_id = ?",
            (plan.campaign_id,),
        ).fetchone()
        if campaign is None or campaign["lifecycle"] != "active":
            raise CommandRejected("Campaign lifecycle blocks appending an adventure")
        if campaign["writer"] != WriterRole.DM.value:
            raise WrongWriter("Only a campaign in play can receive an adventure")
        if campaign["revision"] != basis["expected_revision"]:
            raise StaleRevision("Campaign changed; review the adventure again")
        if campaign["current_lock_id"] != plan.package_lock_id:
            raise StaleRevision("Campaign content binding changed; review again")
        rows = adventure_rows(connection, plan.campaign_id)
        if not rows or rows[-1]["plan_id"] != metadata["previous_plan_id"]:
            raise StaleRevision("The accepted adventure changed; review again")
        if len(rows) + 1 != basis["number"]:
            raise StaleRevision("The adventure sequence changed; review again")
        previous = sqlite_session.plan_from_row(rows[-1])
        verify_sequel(plan, previous)
        session = sqlite_session.read_session(connection, plan.source_session_id)
        handoff = connection.execute(
            "SELECT dm_session_id FROM builder_dm_handoffs WHERE campaign_id = ?",
            (plan.campaign_id,),
        ).fetchone()
        if (
            session.campaign_id != plan.campaign_id
            or session.role is not WriterRole.DM
            or session.status is not SessionStatus.ACTIVE
            or handoff is None
            or handoff["dm_session_id"] != session.session_id
        ):
            raise WrongWriter("The active GM session must own the adventure append")
        sequence = connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) FROM agent_session_messages WHERE session_id = ?",
            (session.session_id,),
        ).fetchone()[0]
        if sequence != basis["latest_message_sequence"]:
            raise StaleRevision("Campaign narration changed; review its ending and future again")
        if (
            connection.execute(
                "SELECT 1 FROM pending_mechanics_actions WHERE campaign_id = ?",
                (plan.campaign_id,),
            ).fetchone()
            is not None
        ):
            raise CommandRejected("Resolve the pending game action before appending an adventure")
        if (
            connection.execute(
                "SELECT 1 FROM narration_interruptions "
                "WHERE campaign_id = ? AND status = 'retryable'",
                (plan.campaign_id,),
            ).fetchone()
            is not None
        ):
            raise CommandRejected("Resolve the interrupted GM reply before appending an adventure")
        evidence_id = plan.provenance_evidence_ids[0]
        sqlite_session.append_message(
            connection,
            session_id=session.session_id,
            message_id=evidence_id,
            kind=SessionMessageKind.EVIDENCE,
            content={
                "kind": "campaign_sequel_adoption",
                "plan_id": plan.plan_id,
                "request_sha256": digest(request),
            },
        )
        connection.execute(
            "INSERT INTO campaign_plans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                plan.plan_id,
                plan.campaign_id,
                plan.package_lock_id,
                plan.source_session_id,
                plan.content_sha256,
                canonical_json_bytes(plan.content).decode(),
                canonical_json_bytes(list(plan.provenance_evidence_ids)).decode(),
                plan.validation_outcome.value,
                plan.acceptance_status.value,
                plan.plan_revision,
                plan.resulting_campaign_revision,
                plan.payload_sha256,
            ),
        )
        updated = connection.execute(
            "UPDATE campaigns SET revision = ? WHERE campaign_id = ? AND revision = ? "
            "AND lifecycle = 'active' AND writer = 'dm' AND current_lock_id = ?",
            (
                plan.resulting_campaign_revision,
                plan.campaign_id,
                basis["expected_revision"],
                plan.package_lock_id,
            ),
        )
        if updated.rowcount != 1:
            raise StaleRevision("Campaign sequel revision fence rejected admission")
        connection.execute(
            "INSERT INTO campaign_revisions "
            "(campaign_id, revision, action_id, activation_id, plan_id) "
            "VALUES (?, ?, NULL, NULL, ?)",
            (plan.campaign_id, plan.resulting_campaign_revision, plan.plan_id),
        )
        connection.commit()
        return plan
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
