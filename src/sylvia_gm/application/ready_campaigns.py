"""Load immutable prepared parties and start copies through ordinary campaign authority."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, replace
from importlib.resources import files
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any, cast

from sylvia_gm.application.bundled_campaign_plans import bundled_plan
from sylvia_gm.application.campaign_library_selection import (
    CampaignLibrarySelection,
    materialize_library_party,
    resolve_campaign_plan_selection,
)
from sylvia_gm.application.compatible_character_copy import authored_character_state
from sylvia_gm.application.decision_scope import project_campaign_preparation_scope
from sylvia_gm.application.package_runtime import (
    PACKAGE_RUNTIME_BYTE_LIMIT,
    PACKAGE_RUNTIME_PATH,
    package_model_decision_boundary,
)
from sylvia_gm.application.reusable_participants import (
    ReusableParticipant,
    parse_setting_character,
    save_reusable_participant,
)
from sylvia_gm.builder._package_materializer import _reject_reparse_chain
from sylvia_gm.domain.authority import (
    CampaignState,
    LifecycleState,
    PackageRole,
    Participant,
    SafePayload,
    StateValue,
    WriterRole,
)
from sylvia_gm.domain.campaign_plan import ValidatedCampaignPlan
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.domain.session import SessionMessageKind

if TYPE_CHECKING:
    from sylvia_gm.application.authority import AuthorityOwner


def _entries() -> tuple[dict[str, Any], ...]:
    """Read packaged sources exclusively, including in a frozen installation."""
    raw = files("sylvia_gm.campaign").joinpath("ready-campaigns.json").read_bytes()
    if len(raw) > 1024 * 1024:
        raise ValueError("Prepared campaign inventory is too large")
    payload = json.loads(raw)
    if (
        not isinstance(payload, dict)
        or payload.get("format_version") != 1
        or not isinstance(payload.get("provenance"), dict)
    ):
        raise ValueError("Prepared campaign inventory is incompatible")
    entries = payload.get("campaigns")
    if not isinstance(entries, list) or len(entries) != 4:
        raise ValueError("Prepared campaign inventory is incomplete")
    if any(
        not isinstance(row, dict)
        or set(row) != {"id", "plan_content_sha256", "bindings", "party"}
        or not isinstance(row["id"], str)
        or not isinstance(row["bindings"], dict)
        or not isinstance(row["party"], list)
        or not 1 <= len(row["party"]) <= 6
        or any(not isinstance(member, dict) for member in row["party"])
        for row in entries
    ):
        raise ValueError("Prepared campaign inventory is invalid")
    if len({row["id"] for row in entries}) != len(entries):
        raise ValueError("Prepared campaign identities conflict")
    return tuple(
        {
            **row,
            "content_sha256": hashlib.sha256(
                canonical_json_bytes(
                    {
                        "campaign": row,
                        "provenance": payload["provenance"],
                    }
                )
            ).hexdigest(),
        }
        for row in entries
    )


def _entry(template_id: str, expected_content_sha256: str | None = None) -> dict[str, Any]:
    row = next((row for row in _entries() if row["id"] == template_id), None)
    if row is None:
        raise ValueError("The prepared campaign is unavailable")
    if expected_content_sha256 is not None and row["content_sha256"] != expected_content_sha256:
        raise ValueError("The prepared campaign changed; refresh its preview before starting")
    plan = bundled_plan(template_id)
    if (
        plan is None
        or plan.content_sha256 != row["plan_content_sha256"]
        or canonical_json_bytes(plan.plan.payload["bindings"])
        != canonical_json_bytes(row["bindings"])
    ):
        raise ValueError("The prepared party requires its exact bundled adventure and packages")
    return row


def ready_campaign_catalog() -> tuple[dict[str, Any], ...]:
    """Expose public scenario and character previews without library discovery or provider work."""
    result = []
    for inventory in _entries():
        row = _entry(inventory["id"])
        plan = bundled_plan(row["id"])
        assert plan is not None
        party = []
        for member in row["party"]:
            character = member.get("character")
            if character is None:
                # Tavin's preview is deliberately public; the actual file is re-admitted at Start.
                party.append(
                    {
                        "name": "Tavin Bell",
                        "role": member["role"],
                        "level": 1,
                        "classes": [{"id": "fighter", "level": 1}],
                        "summary": (
                            "The adventure's included Fighter, "
                            "with his authored equipment and history."
                        ),
                    }
                )
            else:
                party.append(
                    {
                        "name": character["participant"]["name"],
                        "role": member["role"],
                        "level": character["participant"]["level"],
                        "classes": character["participant"]["classes"],
                        "summary": character["summary"],
                        "profile": character["authored_profile"],
                        "hp": character["entity_state"]["max_hp"],
                        "armor_class": character["entity_state"]["armor_class"],
                    }
                )
        result.append(
            {
                "id": row["id"],
                "content_sha256": row["content_sha256"],
                "title": plan.title,
                "summary": plan.summary,
                "party": party,
            }
        )
    return tuple(result)


def resolve_ready_campaign(
    owner: AuthorityOwner, storage_root: Path, template_id: str, expected_content_sha256: str
) -> tuple[CampaignLibrarySelection, tuple[ReusableParticipant, ...]]:
    """Admit only the explicit party against the exact retained Plan and character declarations."""
    try:
        row = _entry(template_id, expected_content_sha256)
        selection = resolve_campaign_plan_selection(
            owner,
            storage_root,
            candidate_id=row["bindings"]["ruleset"]["id"],
            setting_id=row["bindings"]["campaign_setting"]["id"],
            plan_id=template_id,
        )
        primary = next(
            e for e in selection.candidate.package_lock.entries if e.role is PackageRole.PRIMARY
        )
        declaration = next(
            d
            for d in owner.exact_participant_declarations(
                primary.package_id,
                primary.version,
                primary.sha256,
            )
            if d.kind == "player_character"
        )
        required = {item.artifact_id: item for item in selection.required_participants}
        party = []
        for member in row["party"]:
            if "required_character_id" in member:
                character = required[member["required_character_id"]]
                if character.role != member["role"]:
                    raise ValueError("Prepared character role is incompatible")
            else:
                source = canonical_json_bytes(member["character"])
                if hashlib.sha256(source).hexdigest() != member["content_sha256"]:
                    raise ValueError("Prepared Character File content hash is invalid")
                character = parse_setting_character(
                    source.decode("utf-8"),
                    character_id=member["character"]["id"],
                    role=member["role"],
                    package=primary,
                    declaration=declaration,
                    campaign_setting=selection.sources.setting,
                    setting_character_schema=selection.sources.setting_character_schema,
                )
            party.append(character)
        if (
            not party
            or party[0].role != "player_character"
            or any(p.role != "companion" for p in party[1:])
            or len({p.artifact_id for p in party}) != len(party)
        ):
            raise ValueError("Prepared party identities or roles are invalid")
        return replace(
            selection, participants=tuple(p for p in party if p.artifact_id not in required)
        ), tuple(party)
    except OSError as error:
        raise CommandRejected(
            "Prepared campaign content could not be read. Retry resumes the same start.",
            code="ready_campaign_unavailable",
        ) from error
    except (UnicodeError, ValueError, KeyError, TypeError, StopIteration) as error:
        raise CommandRejected(
            "The prepared campaign is unavailable or incompatible. "
            "Refresh its preview or use custom setup.",
            code="ready_campaign_invalid",
        ) from error


def _validate_prepared_state(
    owner: AuthorityOwner,
    selection: CampaignLibrarySelection,
    participants: tuple[Participant, ...],
    initial_state: tuple[StateValue, ...],
) -> None:
    """Check package-owned preparation without clearing queues or changing starting state."""
    primary = next(
        e for e in selection.candidate.package_lock.entries if e.role is PackageRole.PRIMARY
    )
    runtime = owner.package_text_file(
        primary.package_id,
        primary.version,
        primary.sha256,
        PACKAGE_RUNTIME_PATH,
        maximum_bytes=PACKAGE_RUNTIME_BYTE_LIMIT,
    )
    declared = package_model_decision_boundary(
        package_id=primary.package_id, runtime_bytes=runtime.encode()
    )
    preview = CampaignState(
        campaign_id="ready-preview",
        display_name=selection.plan.title,
        lifecycle=LifecycleState.ACTIVE,
        writer=WriterRole.BUILDER,
        revision=0,
        package_lock=selection.candidate.package_lock,
        participants=participants,
        state={f"{item.namespace}.{item.key}": item.value for item in initial_state},
    )
    if declared is not None and project_campaign_preparation_scope(preview, declared) is not None:
        raise CommandRejected(
            "The prepared party still requires character preparation",
            code="ready_campaign_preparation_pending",
        )


def _check_plan_copy_path(storage_root: Path, target: Path) -> None:
    """Reject linked ancestors and paths outside the selected storage before file IO."""
    storage_root = storage_root.absolute()
    target = target.absolute()
    try:
        if not target.is_relative_to(storage_root):
            raise ValueError("Prepared Plan copy leaves application storage")
        current = target
        while not os.path.lexists(current) and current != storage_root:
            current = current.parent
        _reject_reparse_chain(storage_root, current)
        if not target.resolve().is_relative_to(storage_root.resolve()):
            raise ValueError("Prepared Plan copy leaves application storage")
    except (OSError, ValueError, CommandRejected) as error:
        raise CommandRejected(
            "The campaign is saved, but its Plan copy directory is unavailable or contains links",
            code="ready_campaign_copy_invalid",
        ) from error


def _copy_ready_plan(storage_root: Path, campaign_id: str, plan: ValidatedCampaignPlan) -> None:
    """Make owned convenience copies without overwriting edits or following linked directories."""
    root = storage_root / "campaigns" / campaign_id
    payloads = (
        ("campaign-plan.json", canonical_json_bytes(plan.payload)),
        ("campaign-plan.md", plan.readable_markdown.encode()),
    )
    for name, _ in payloads:
        _check_plan_copy_path(storage_root, root / name)
    root.mkdir(parents=True, exist_ok=True)
    for name, data in payloads:
        target = root / name
        _check_plan_copy_path(storage_root, target)
        if not target.exists():
            with tempfile.NamedTemporaryFile(
                dir=root, prefix=".ready-plan-", delete=False
            ) as output:
                temporary = Path(output.name)
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            try:
                _check_plan_copy_path(storage_root, target)
                os.link(temporary, target)
            except FileExistsError:
                pass
            finally:
                temporary.unlink()


def start_ready_campaign(
    owner: AuthorityOwner,
    storage_root: Path,
    *,
    operation_id: str,
    template_id: str,
    expected_content_sha256: str,
    display_name: str,
    provider_id: str,
    model_id: str,
    model_guidance_override: bool = False,
) -> dict[str, Any]:
    """Start or resume one independent prepared copy without resolving or calling a provider."""
    suffix = hashlib.sha256(operation_id.encode()).hexdigest()[:24]
    confirmation_id = f"ready-confirm-{suffix}"
    with owner.ready_campaign_start_work(confirmation_id):
        try:
            return _start_ready_campaign(
                owner, storage_root, operation_id=operation_id, template_id=template_id,
                expected_content_sha256=expected_content_sha256, display_name=display_name,
                provider_id=provider_id, model_id=model_id,
                model_guidance_override=model_guidance_override,
            )
        except CommandRejected as error:
            if error.code != "ready_campaign_invalid":
                raise
            if not owner.reject_uncreated_ready_campaign(confirmation_id):
                # An earlier reply may have been lost after creation; never attest absence.
                raise
            raise CommandRejected(
                "The prepared template is unavailable or changed; no campaign was created. "
                "Review the refreshed choices or use custom setup.",
                code="ready_campaign_not_created",
            ) from error


def _start_ready_campaign(
    owner: AuthorityOwner,
    storage_root: Path,
    *,
    operation_id: str,
    template_id: str,
    expected_content_sha256: str,
    display_name: str,
    provider_id: str,
    model_id: str,
    model_guidance_override: bool,
) -> dict[str, Any]:
    """Admit the explicit copy while the prepared-start reconciliation lane is held."""
    started = perf_counter()
    selection, party = resolve_ready_campaign(
        owner, storage_root, template_id, expected_content_sha256
    )
    participants, initial_state, selected_party = materialize_library_party(
        owner,
        selection,
        operation_id=operation_id,
        player_character_id=party[0].artifact_id,
        companion_ids=tuple(p.artifact_id for p in party[1:]),
    )
    _validate_prepared_state(owner, selection, participants, initial_state)
    resolved = perf_counter()
    suffix = hashlib.sha256(operation_id.encode()).hexdigest()[:24]
    builder_session_id = f"ready-source-{suffix}"
    confirmed = owner.confirm_new_campaign(
        operation_id=f"ready-confirm-{suffix}",
        display_name=display_name,
        package_lock=selection.candidate.package_lock,
        turn_id=f"ready-construction-{suffix}",
        package_candidate_id=selection.candidate.candidate_id,
        builder_session_id=builder_session_id,
        builder_provider_id=provider_id,
        builder_model_id=model_id,
        participants=participants,
        initial_state=initial_state,
    )
    campaign_id = confirmed.campaign_id
    campaign_suffix = hashlib.sha256(campaign_id.encode()).hexdigest()[:24]
    handoff_id = f"builder-dm-handoff-{campaign_suffix}"
    dm_turn_id = f"turn-dm-{campaign_suffix}"
    try:
        handoff = owner.builder_dm_handoff(handoff_id)
    except CommandRejected:
        owner.append_agent_message(
            session_id=builder_session_id,
            message_id=f"ready-selection-{suffix}",
            kind=SessionMessageKind.USER,
            content={
                "text": f"Start {selection.plan.title} with its prepared party.",
                "ready_campaign_id": template_id,
                "ready_campaign_sha256": expected_content_sha256,
            },
        )
        owner.begin_campaign_construction(
            operation_id=f"ready-begin-{suffix}",
            campaign_id=campaign_id,
            lane="quick_start",
            expected_revision=confirmed.resulting_revision,
            canonical_input=cast(
                SafePayload,
                {
                    "plan_id": template_id,
                    "plan_content_sha256": selection.plan.content_sha256,
                    "setting_id": selection.sources.setting.package_id,
                    "ready_campaign_sha256": expected_content_sha256,
                    "selected_party": selected_party,
                },
            ),
        )
        guidance = owner.model_guidance(
            campaign_id=campaign_id, model_id=model_id, override_accepted=model_guidance_override
        )
        if not guidance.may_continue:
            return {
                "campaign_id": campaign_id,
                "local_ready": False,
                "model_guidance": asdict(guidance),
            }
        _, _, handoff = owner.finalize_campaign_construction(
            campaign_id=campaign_id,
            dm_provider_id=provider_id,
            dm_model_id=model_id,
            plan_artifact_text=selection.plan.plan.readable_markdown,
            structured_plan=selection.plan.plan.payload,
            model_guidance_override=model_guidance_override,
        )
    if dm_turn_id not in owner.snapshot(campaign_id).turns:
        owner.open_turn(campaign_id, dm_turn_id)
    # The owner retains the accepted Plan in SQLite. Files are independent convenience copies;
    # ordinary reopening and first play use the accepted content even after interrupted file IO.
    _copy_ready_plan(storage_root, campaign_id, selection.plan.plan)
    return {
        "campaign_id": campaign_id,
        "dm_session_id": handoff.dm_session_id,
        "dm_turn_id": dm_turn_id,
        "dm_attempt_id": f"attempt-campaign-start-{campaign_suffix}",
        "local_ready": True,
        "template_id": template_id,
        "timing_ms": {
            "resolve_party": (resolved - started) * 1000,
            "local_start": (perf_counter() - started) * 1000,
        },
    }


def customize_ready_campaign(
    owner: AuthorityOwner,
    storage_root: Path,
    *,
    operation_id: str,
    template_id: str,
    expected_content_sha256: str,
) -> dict[str, Any]:
    """Save independent editable party files for the ordinary custom campaign flow."""
    selection, party = resolve_ready_campaign(
        owner, storage_root, template_id, expected_content_sha256
    )
    primary = next(
        e for e in selection.candidate.package_lock.entries if e.role is PackageRole.PRIMARY
    )
    declaration = next(
        d
        for d in owner.exact_participant_declarations(
            primary.package_id,
            primary.version,
            primary.sha256,
        )
        if d.kind == "player_character"
    )
    copies = tuple(
        save_reusable_participant(
            storage_root,
            operation_id=f"{operation_id}-{index}",
            role=item.role,
            summary=item.summary,
            authored_profile=cast(str, item.authored_profile),
            participant=item.participant,
            entity_state=authored_character_state(item.entity_state, declaration),
            package=primary,
            declaration=declaration,
            campaign_setting=selection.sources.setting,
            setting_data=item.setting_data,
            setting_character_schema=selection.sources.setting_character_schema,
        )
        for index, item in enumerate(party)
    )
    return {"plan_id": template_id, "characters": [item.catalog_payload() for item in copies]}
