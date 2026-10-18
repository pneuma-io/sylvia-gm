"""Let a plain Builder bind a Ruleset and compatible Setting, retaining exact snapshots."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from sylvia_gm.application.builder_references import (
    candidate_for_references,
    capture_content_references,
    launch_references,
    reader_for_references,
)
from sylvia_gm.builder._package_snapshots import ruleset_copy
from sylvia_gm.domain.authority import SafePayload, WriterRole
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import CandidateStatus
from sylvia_gm.domain.session import SessionContext, SessionMessageKind

if TYPE_CHECKING:
    from sylvia_gm.application.authority import AuthorityOwner


class BuilderSourceSelection:
    """Keep a turn's selection usable immediately and recover it from its saved tool receipt."""

    def __init__(
        self, owner: AuthorityOwner, storage_root: Path, context: SessionContext,
        catalog: Callable[[], Sequence[SafePayload]],
    ) -> None:
        self.owner = owner
        self.storage_root = storage_root
        self.catalog = catalog
        self.references = launch_references(context)
        self.selectable = (
            context.session.role is WriterRole.BUILDER
            and context.session.campaign_id is None
            and not any(
                message.kind is SessionMessageKind.EVIDENCE
                and message.content.get("kind") in {
                    "builder_launch_context", "import_repair_context",
                }
                for message in context.messages
            )
        )

    def select(self, ruleset_id: str) -> SafePayload:
        """Select an advertised Ruleset, rejecting replacement of an existing binding."""
        if not self.selectable:
            raise CommandRejected("This Builder keeps its original content selection")
        catalog = self.catalog()
        if not any(
            item.get("kind") == "ruleset"
            and (ruleset_id == item.get("selection_id", item.get("id"))
                 or ruleset_id in item.get("selection_aliases", ()))
            for item in catalog
        ):
            raise CommandRejected("Choose a Ruleset returned by discover_campaign_content")
        references = capture_content_references(
            self.owner, self.storage_root, catalog, ruleset_id=ruleset_id,
        )
        candidate = candidate_for_references(self.owner, references)
        if candidate is None or candidate.status not in {
            CandidateStatus.VALIDATED, CandidateStatus.ACTIVATED,
        }:
            raise CommandRejected("Selected Builder Ruleset has not passed validation")
        if self.references is not None and self.references != references:
            raise CommandRejected("This Builder already selected a different source snapshot")
        self.references = references
        return {"success": True, "references": references}

    def candidate(self):
        """Resolve the exact chosen package closure for validation and publication."""
        return candidate_for_references(self.owner, self.references)

    def select_setting(self, candidate_id: str | None, setting_id: str) -> SafePayload:
        """Extend a plain chat's Ruleset binding with one explicit compatible Setting snapshot."""
        if not self.selectable:
            raise CommandRejected("This Builder keeps its original content selection")
        catalog = self.catalog()
        ruleset = next((item for item in catalog if item.get("kind") == "ruleset" and any(
            setting.get("id") == setting_id and setting.get("candidate_id") == candidate_id
            for setting in item.get("compatible_settings", ())
        )), None)
        if ruleset is None:
            raise CommandRejected("Choose an exact Setting returned by discover_campaign_content")
        references = capture_content_references(
            self.owner, self.storage_root, catalog,
            ruleset_id=ruleset.get("selection_id", ruleset["id"]),
            setting_id=setting_id, setting_candidate_id=candidate_id,
        )
        candidate = candidate_for_references(self.owner, references)
        if candidate is None or candidate.status not in {
            CandidateStatus.VALIDATED, CandidateStatus.ACTIVATED,
        }:
            raise CommandRejected("Selected Builder Setting has not passed validation")
        if self.references is not None:
            previous = self.references.get("packages", ())
            incoming = references.get("packages", ())
            if any(item.get("prefix") == "setting" for item in previous):
                if self.references != references:
                    raise CommandRejected(
                        "This Builder already selected a different Setting snapshot"
                    )
            else:
                # A Ruleset may come from an older Setting closure. Retain its exact mechanics,
                # not that unselected world's files; use the same boundary as Setting publication.
                source = self.candidate()
                required_ids = {
                    draft.record["id"] for draft in ruleset_copy(self.owner, source.candidate_id)
                }
                if any(item not in incoming for item in previous if item["id"] in required_ids):
                    raise CommandRejected(
                        "This Builder already selected a different source snapshot"
                    )
        self.references = references
        return {"success": True, "references": references}

    def reader(self):
        """Expose the selected reference paths after selection, including within the same turn."""
        return reader_for_references(self.owner, self.references)
