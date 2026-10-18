"""Upgrade former campaign-creation content chats without a return handshake."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from sylvia_gm.application.builder_references import capture_launch_references
from sylvia_gm.domain.authority import SafePayload, owned_payload
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.session import SessionContext, SessionMessageKind
from sylvia_gm.infrastructure.builder_workspace_migration import retain_legacy_workspace

if TYPE_CHECKING:
    from sylvia_gm.application.authority import AuthorityOwner


def migrate_legacy_builder_chats(
    owner: AuthorityOwner,
    storage_root: Path,
    catalog: Sequence[SafePayload],
) -> tuple[str, ...]:
    """Run before owner requests begin, retaining each original launch and shared file snapshot."""
    migrated: list[str] = []
    for context in owner.legacy_builder_contexts():
        session = context.session
        link = next(
            message.content
            for message in context.messages
            if message.content.get("kind") == "creation_subflow_link"
        )
        parent_session_id = str(link["parent_session_id"])
        parent = owner.session_context(parent_session_id)
        transcript_ids = set(link["transcript_message_ids"])
        transcript = [
            message for message in parent.messages if message.message_id in transcript_ids
        ]
        if len(transcript) != len(transcript_ids) or not transcript:
            raise CommandRejected("Legacy Builder launch transcript is unavailable")
        cutoff = max(message.sequence for message in transcript)
        original_context = SessionContext(
            session=parent.session,
            messages=tuple(message for message in parent.messages if message.sequence <= cutoff),
        )
        state = next(
            (
                message.content
                for message in reversed(original_context.messages)
                if message.content.get("kind") == "campaign_interview_state"
            ),
            {},
        )
        messages = {message.message_id: message for message in original_context.messages}
        selections = {}
        for step, message_id in state.get("answers", {}).items():
            message = messages[str(message_id)]
            selections[step] = {
                "text": message.content.get("text", ""),
                "value": message.content.get("interview_value", {}),
            }
        reference_error = None
        try:
            ruleset_id = selections.get("ruleset", {}).get("value", {}).get("candidate_id")
            if ruleset_id is not None:
                # Earlier bundled interviews sometimes saved only a logical Ruleset name.
                # That cannot prove which historical version this chat originally used.
                owner.package_candidate(str(ruleset_id))
            references = capture_launch_references(owner, storage_root, original_context, catalog)
        except CommandRejected:
            references = {"candidate_id": None, "packages": [], "plan": None}
            reference_error = (
                "This older chat has no recoverable exact source versions. Its history and files "
                "remain available; a new Builder with selected sources can validate them for use."
            )
        trigger = messages.get(str(link.get("trigger_message_id")))
        button = trigger.content.get("text", "New content") if trigger else "New content"
        purpose = str(link["purpose"])
        launch = owned_payload(
            {
                "kind": "builder_launch_context",
                "purpose": purpose,
                "launch_request": {
                    "operation_id": f"migrate-{session.session_id}",
                    "campaign_id": session.campaign_id,
                    "parent_session_id": parent_session_id,
                    "session_id": session.session_id,
                    "purpose": purpose,
                    "button_label": button,
                    "construction_revision": state.get("construction_revision", 0),
                },
                "selections": selections,
                "references": references,
                "reference_error": reference_error,
                "interview_transcript": [
                    {
                        "source_message_id": message.message_id,
                        "role": "user" if message.kind is SessionMessageKind.USER else "assistant",
                        "content": message.content.get("text", ""),
                    }
                    for message in transcript
                ],
                "migrated_from": {
                    "campaign_id": session.campaign_id,
                    "parent_session_id": parent_session_id,
                    "selected_skill": session.selected_skill,
                    "scope": list(session.scope),
                },
            }
        )
        context_name = next(
            (
                str(selections[step]["text"])
                for step in ("plan", "setting", "ruleset")
                if step in selections and selections[step]["text"]
            ),
            "Campaign content",
        )
        label = {
            "ruleset": "Ruleset",
            "setting": "Setting",
            "plan": "Campaign Plan",
            "player_character": "Character",
            "companion": "Companion",
        }[purpose]
        retain_legacy_workspace(
            storage_root, campaign_id=session.campaign_id, session_id=session.session_id
        )
        owner.detach_legacy_builder_chat(
            session_id=session.session_id,
            expected_message_count=len(context.messages),
            launch_context=launch,
            display_name=f"{label} · {context_name}"[:120].strip(),
        )
        migrated.append(session.session_id)
    return tuple(migrated)
