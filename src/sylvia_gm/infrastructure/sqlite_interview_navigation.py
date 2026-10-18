"""Save Interview navigation independently of the player's accepted answers."""

from __future__ import annotations

import hashlib
import sqlite3

from sylvia_gm.domain.authority import GuidedCampaignDraft, WriterRole, owned_payload
from sylvia_gm.domain.errors import CommandRejected, IdempotencyConflict, StaleRevision
from sylvia_gm.domain.session import SessionMessageKind, SessionStatus
from sylvia_gm.infrastructure import sqlite_session


def navigate(
    connection: sqlite3.Connection,
    *,
    operation_id: str,
    campaign_id: str,
    builder_session_id: str,
    expected_construction_revision: int,
    target_step: str,
    steps: tuple[str, ...],
) -> GuidedCampaignDraft:
    """Revisit retained questions without answering again or skipping unfinished questions."""
    context = sqlite_session.read_context(connection, builder_session_id)
    if (
        context.session.campaign_id != campaign_id
        or context.session.role is not WriterRole.BUILDER
        or context.session.status is not SessionStatus.ACTIVE
    ):
        raise CommandRejected("Campaign Interview session is unavailable")
    request = {
        "operation_id": operation_id, "campaign_id": campaign_id,
        "builder_session_id": builder_session_id,
        "expected_construction_revision": expected_construction_revision,
        "target_step": target_step,
    }
    suffix = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]
    message_id = f"guided-navigation-{suffix}"
    replay = next((m for m in context.messages if m.message_id == message_id), None)
    if replay is not None:
        if replay.content.get("navigation_request") != request:
            raise IdempotencyConflict("Campaign Interview navigation identity does not match")
        revision = int(replay.content["construction_revision"])
    else:
        state = next((m.content for m in reversed(context.messages)
                      if m.kind is SessionMessageKind.EVIDENCE
                      and m.content.get("kind") == "campaign_interview_state"), {})
        if (
            state.get("phase") != "draft"
            or state.get("construction_revision") != expected_construction_revision
            or state.get("active_subflow") is not None
        ):
            raise StaleRevision("Campaign Interview changed; reopen it before changing steps")
        current_step = state.get("current_step")
        frontier = state.get("navigation_frontier", current_step)
        # An accepted adaptive Builder result can advance the saved cursor too.
        if current_step in steps and frontier in steps:
            frontier = steps[max(steps.index(current_step), steps.index(frontier))]
        if (
            current_step not in steps
            or frontier not in steps
            or steps.index(target_step) > steps.index(frontier)
            or (target_step not in state.get("answers", {}) and target_step != frontier)
        ):
            raise CommandRejected("Campaign Interview can only revisit its saved questions")
        question = next((m for m in reversed(context.messages)
                         if m.kind is SessionMessageKind.TALK
                         and m.content.get("interview_step") == target_step), None)
        if question is None:
            raise CommandRejected("The saved Campaign Interview question is unavailable")
        revision = expected_construction_revision + 1
        sqlite_session.append_message(
            connection, session_id=builder_session_id, message_id=message_id,
            kind=SessionMessageKind.EVIDENCE,
            content=owned_payload({
                **state, "current_step": target_step, "construction_revision": revision,
                "navigation_frontier": frontier,
                "current_question_message_id": question.message_id,
                "last_operation_id": operation_id, "navigation_request": request,
            }),
        )
    return GuidedCampaignDraft(
        campaign_id=campaign_id, builder_session_id=builder_session_id,
        current_step=target_step, construction_revision=revision,
        resulting_revision=connection.execute(
            "SELECT revision FROM campaigns WHERE campaign_id = ?", (campaign_id,),
        ).fetchone()[0],
    )
