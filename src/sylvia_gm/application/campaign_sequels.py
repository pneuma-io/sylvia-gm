"""Select, review and append authored preparation through the campaign owner."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from sylvia_gm.application.bundled_campaign_plans import selected_campaign_plan
from sylvia_gm.domain.authority import LifecycleState, owned_payload, validate_authority_identity
from sylvia_gm.domain.campaign_sequel import (
    MAX_ADVENTURES,
    digest,
    json_object,
    prepare_story,
    review_payload,
)
from sylvia_gm.domain.errors import CommandRejected, IdempotencyConflict, StaleRevision
from sylvia_gm.domain.session import (
    CampaignPlan,
    PlanAcceptanceStatus,
    PlanValidationOutcome,
    campaign_plan_payload_sha256,
)

if TYPE_CHECKING:
    from sylvia_gm.application.authority import AuthorityOwner


def _receipt(plan: CampaignPlan) -> dict[str, Any]:
    return {
        "plan_id": plan.plan_id,
        "campaign_id": plan.campaign_id,
        "resulting_revision": plan.resulting_campaign_revision,
        "adventure_number": json_object(plan.content["sequel"])["preview"]["number"],
    }


def preview(
    owner: AuthorityOwner, campaign_id: str, session_id: str, source_id: str
) -> dict[str, Any]:
    """Prepare an exact, read-only review; source snapshots must match the retained content."""
    context = owner._active_dm_context(session_id)
    if context.session.campaign_id != campaign_id:
        raise CommandRejected("Choose the active campaign GM session")
    snapshot = owner._store.snapshot(campaign_id)
    if snapshot.lifecycle is not LifecycleState.ACTIVE:
        raise CommandRejected("Restore this campaign before appending an adventure")
    previous = owner._store.accepted_campaign_plan(campaign_id)
    if previous is None or not isinstance(previous.content.get("campaign_plan"), Mapping):
        raise CommandRejected("This campaign has no structured accepted adventure")
    ending = owner.session_history_page(session_id).campaign_ending
    if ending is None:
        raise CommandRejected("Complete the current authored adventure before adding the next one")
    if ending["open_world"] is not True:
        raise CommandRejected("This ending does not support later adventures with this party")
    owner._reject_pending_mechanics_choice(campaign_id)
    if (
        any(item.retryable for item in owner._store.interruptions(campaign_id))
        or owner._store.pending_player_response(campaign_id) is not None
    ):
        raise CommandRejected("Resolve the interrupted GM reply before appending an adventure")
    rows = owner._store.campaign_adventures(campaign_id)
    if len(rows) >= MAX_ADVENTURES:
        raise CommandRejected("This campaign has reached the authored adventure limit")
    if owner._content_storage_root is None:
        raise CommandRejected("The local adventure library is unavailable")
    try:
        source = selected_campaign_plan(owner._content_storage_root, source_id)
    except (OSError, ValueError) as error:
        raise CommandRejected(
            "The selected adventure or its exact source closure is unavailable"
        ) from error
    original = json_object(previous.content["campaign_plan"])
    source_plan = json_object(source.plan.payload)
    if (
        source_plan["bindings"] != original["bindings"]
        or previous.package_lock_id != owner._store.current_package_lock(campaign_id).lock_id
    ):
        raise CommandRejected(
            "The adventure uses a different Setting or Ruleset snapshot. "
            "Author a compatible copy in Builder; appending cannot update campaign rules."
        )
    if any(
        json_object(row.content.get("sequel", {})).get("preview", {}).get("source_sha256")
        == source.content_sha256
        for row in rows
    ):
        raise CommandRejected("This exact authored adventure has already been appended")
    party = source_plan["starting_party"]
    old_party = original.get("starting_party", {})
    if party["player_character"]["mode"] == "fixed" and party["player_character"] != old_party.get(
        "player_character"
    ):
        raise CommandRejected("This adventure requires a different fixed Player Character")
    old_required = old_party.get("required_companions", ())
    if any(item not in old_required for item in party["required_companions"]):
        raise CommandRejected("This adventure requires companions not admitted by the current Plan")
    try:
        prepared, mapping = prepare_story(source.plan.payload, len(rows) + 1)
    except ValueError as error:
        raise CommandRejected(str(error)) from error
    prepared_view = json_object(prepared)
    used: set[str] = set()
    private_facts: set[str] = set()
    for row in rows:
        old = json_object(row.content["campaign_plan"])
        private_facts.update(
            item if isinstance(item, str) else str(item["fact"])
            for item in old.get("secrets", ())
            if isinstance(item, str) or (isinstance(item, Mapping) and "fact" in item)
        )
        for field in ("structure", "objectives", "secrets"):
            used.update(str(item["id"]) for item in old.get(field, ()) if isinstance(item, Mapping))
        used.update(str(item["id"]) for item in old.get("ending", {}).get("outcomes", ()))
    if used.intersection(mapping.values()):
        raise CommandRejected("Prepared identities still collide; author distinct IDs in Builder")
    if any(
        (item if isinstance(item, str) else str(item.get("fact", ""))) in private_facts
        for item in source_plan.get("secrets", ())
    ):
        raise CommandRejected(
            "This source repeats a retained private fact as a new secret. "
            "Adapt that declaration in Builder so earlier knowledge is not locked again."
        )
    basis = {
        "campaign_id": campaign_id,
        "session_id": session_id,
        "expected_revision": snapshot.revision,
        "latest_message_sequence": context.messages[-1].sequence if context.messages else 0,
        "previous_plan_id": previous.plan_id,
        "previous_plan_sha256": previous.content_sha256,
        "source_id": source_id,
        "source_sha256": source.content_sha256,
        "source_plan_sha256": digest(source.plan.payload),
        "source_markdown_sha256": digest(source.plan.readable_markdown),
        "number": len(rows) + 1,
    }
    return {
        "basis": basis,
        "preview_sha256": digest(basis),
        "title": source.title,
        "summary": source.summary,
        "identity_mapping": mapping,
        "acts": [
            {
                "id": item["id"],
                "number": sum(
                    len(json_object(row.content["campaign_plan"])["structure"]) for row in rows
                )
                + index
                + 1,
                "title": item["title"],
            }
            for index, item in enumerate(prepared_view["structure"])
        ],
        "participants": [
            {"id": item.participant_id, "name": item.data.get("name", item.participant_id)}
            for item in snapshot.participants
        ],
        "current_scene": snapshot.continuity.current_scene,
        "retained_facts": list(snapshot.continuity.facts),
        "ending": ending,
        "boundaries": list(original.get("boundaries", ())),
        "source_plan": source.plan.payload,
        "source_markdown": source.plan.readable_markdown,
        "source_initial_state": source.plan.payload["initial_state"],
        "source_bindings": source.plan.payload["bindings"],
        "warning": "Starting characters, location, facts and mechanical setup "
        "are preparation only. "
        "They will not reset this campaign. Review contradictions in Builder "
        "before appending. Already narrated events and futures must remain true.",
    }


def append(
    owner: AuthorityOwner,
    *,
    campaign_id: str,
    session_id: str,
    operation_id: str,
    source_id: str,
    expected_revision: int,
    preview_sha256: str,
    review: object,
) -> dict[str, Any]:
    """Revalidate and commit a reviewed selection without granting Builder live authority."""
    try:
        if (
            not isinstance(expected_revision, int)
            or isinstance(expected_revision, bool)
            or expected_revision < 0
        ):
            raise ValueError("Expected campaign revision must be a nonnegative integer")
        validate_authority_identity(operation_id, "Adventure append request")
        validate_authority_identity(preview_sha256, "Adventure preview SHA-256", sha256=True)
        accepted_review = review_payload(review)
    except (TypeError, ValueError) as error:
        raise CommandRejected(str(error)) from error
    request = owned_payload(
        {
            "operation_id": operation_id,
            "source_id": source_id,
            "expected_revision": expected_revision,
            "preview_sha256": preview_sha256,
            "review": accepted_review,
            "session_id": session_id,
        }
    )
    plan_id = "campaign-sequel-" + digest({"campaign": campaign_id, "operation": operation_id})[:32]
    hit = owner._store.campaign_plan_hit(plan_id)
    if hit is not None:
        if (
            hit.campaign_id != campaign_id
            or json_object(hit.content["sequel"])["request"] != request
        ):
            raise IdempotencyConflict("Adventure append request was reused with different content")
        return _receipt(hit)
    with owner._direct_action_work(campaign_id), owner._lane(campaign_id):
        current = preview(owner, campaign_id, session_id, source_id)
        if expected_revision != current["basis"]["expected_revision"] or (
            preview_sha256 != current["preview_sha256"]
        ):
            raise StaleRevision("Campaign, narration or selected adventure changed; review again")
        previous = owner._store.accepted_campaign_plan(campaign_id)
        assert previous is not None
        prepared, mapping = prepare_story(current["source_plan"], current["basis"]["number"])
        prepared_view = json_object(prepared)
        payload = dict(prepared_view)
        payload["boundaries"] = list(
            dict.fromkeys(
                (
                    *json_object(previous.content["campaign_plan"]).get("boundaries", ()),
                    *prepared_view.get("boundaries", ()),
                )
            )
        )
        content = owned_payload(
            {
                "campaign_plan": payload,
                "campaign_plan_markdown": current["source_markdown"],
                "sequel": {
                    "previous_plan_id": previous.plan_id,
                    "source_id": source_id,
                    "source_plan": current["source_plan"],
                    "identity_mapping": mapping,
                    "preview": current["basis"],
                    "request": request,
                },
            }
        )
        content_sha256 = digest(content)
        evidence_id = plan_id + "-review"
        parameters: dict[str, Any] = {
            "plan_id": plan_id,
            "campaign_id": campaign_id,
            "source_session_id": session_id,
            "expected_revision": expected_revision,
            "expected_package_lock_id": previous.package_lock_id,
            "content_sha256": content_sha256,
            "content": content,
            "provenance_evidence_ids": (evidence_id,),
            "validation_outcome": PlanValidationOutcome.VALID,
            "acceptance_status": PlanAcceptanceStatus.ACCEPTED,
        }
        payload_sha256 = campaign_plan_payload_sha256(**parameters)
        parameters.pop("expected_revision")
        parameters["package_lock_id"] = parameters.pop("expected_package_lock_id")
        plan = CampaignPlan(
            **parameters,
            plan_revision=previous.plan_revision + 1,
            resulting_campaign_revision=expected_revision + 1,
            payload_sha256=payload_sha256,
        )
        return _receipt(owner._store.commit_campaign_sequel(plan))
