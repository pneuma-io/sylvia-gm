"""Executable local authority owner for the Tauri desktop host.

The child owns :class:`LocalOwnerSupervisor` and therefore the sole SQLite
writer.  Its inherited stdin/stdout pipes are a private command channel: the
renderer never receives the launch secret, authority path, or pipe handles.

Protocol
--------
The first bounded JSON line is a launch envelope containing the ephemeral
secret, authority path, and owner identity.  The child proves possession with
the existing HMAC transcript, opens the owner, completes its internal launch
handshake, and emits one ``ready`` response.  Subsequent lines are allowlisted
request envelopes and receive exactly one JSON response each.
"""

from __future__ import annotations

from sylvia_gm.application.launch_compatibility import ARCHIVED_RULESET_IDS

import base64
import binascii
import dataclasses
import functools
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import threading
import time
import zipfile
from collections.abc import Callable, Iterator, Mapping, Sequence, Set
from contextlib import ExitStack, nullcontext
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, BinaryIO, TextIO, cast

from sylvia_gm.agent._builder_session import (
    BuilderTurnResult,
    CampaignContentToolAuthority,
    persist_builder_turn,
    stream_builder_turn,
)
from sylvia_gm.agent._provider_resolver import (
    ConfiguredProviderResolver,
    ProviderResolver,
)
from sylvia_gm.agent._provider_session import ProviderSessionAdapter
from sylvia_gm.agent._session_loop import (
    CampaignSessionLoop,
    ProviderControl,
    ProviderEvent,
    SessionInputKind,
    SessionProviderInput,
    SessionRunRequest,
)
from sylvia_gm.agent._stateless_mcp import ModelMechanicsTool
from sylvia_gm.application.authority import (
    ActionCommand,
    AuthorityOwner,
    ResolveMechanicsChoiceCommand,
    audit_export_save_error,
)
from sylvia_gm.application.builder_references import (
    candidate_for_references,
    capture_launch_references,
    launch_candidate,
    launch_references,
    reference_reader,
)
from sylvia_gm.application.builder_session_migration import migrate_legacy_builder_chats
from sylvia_gm.application.bundled_campaign_plans import (
    known_builtin_public_summary,
    selected_campaign_plan,
)
from sylvia_gm.application.bundled_packages import (
    bundled_campaign_content_catalog,
    bundled_rift_archive,
    bundled_rift_authority,
    bundled_rift_campaign_plan,
    bundled_rift_participant_declaration,
    bundled_srd_archive,
    bundled_srd_version,
)
from sylvia_gm.application.campaign_library_selection import (
    materialize_library_party,
    participant_catalog_payload,
    plan_with_interview_intent,
    require_completed_plan_characters,
    resolve_campaign_library_selection,
)
from sylvia_gm.application.campaign_plan_sources import (
    CampaignPlanToolAuthority,
    campaign_plan_sources,
    validate_retained_campaign_plan,
)
from sylvia_gm.application.character_profiles import (
    character_profile_catalog,
    load_character_profile,
    profile_from_snapshot,
    save_character_profile,
)
from sylvia_gm.application.compatible_character_copy import (
    authored_character_state,
    copy_compatible_character,
)
from sylvia_gm.application.import_repair import materialize_repair_draft
from sylvia_gm.application.import_repair_sessions import (
    offer_import_repair,
    start_import_repair_chat,
)
from sylvia_gm.application.library_sources import LibrarySourceReader
from sylvia_gm.application.plan_bundle import (
    PLAN_BUNDLE_BYTE_LIMIT,
    export_plan_bundle,
    import_plan_bundle,
)
from sylvia_gm.application.plan_snapshot import retained_plan_snapshot, validate_plan_snapshot
from sylvia_gm.application.reusable_participants import (
    ReusableParticipant,
    compatible_reusable_participants,
    delete_reusable_participant,
    save_reusable_participant,
)
from sylvia_gm.application.reusable_plans import (
    delete_reusable_campaign_plan,
    load_reusable_campaign_plan,
    reusable_campaign_plan_catalog,
    save_reusable_campaign_plan,
    save_reusable_rift_plan,
)
from sylvia_gm.builder._campaign_plan_materializer import (
    CampaignPlanDraft,
    publish_campaign_plan_files,
)
from sylvia_gm.builder._plan_draft_check import check_plan_draft
from sylvia_gm.builder._plan_snapshot_materializer import materialize_plan_snapshot_draft
from sylvia_gm.builder._setting_draft_check import check_setting_draft
from sylvia_gm.builder._setting_materializer import materialize_setting_draft
from sylvia_gm.campaign.rift import (
    RIFT_RULESET_ID,
    materialize_rift_selection,
    rift_public_template,
    validate_rift_party_handoff,
    validate_rift_template,
)
from sylvia_gm.config._resolver import ConfigError
from sylvia_gm.config.provider_settings import (
    clear_session_model_once_settings,
    detect_local_providers,
    discover_provider_models,
    open_provider_setup,
    provider_settings,
    remove_provider_settings,
    require_configured_model,
    save_provider_settings,
    session_model_once_settings,
    set_default_model_settings,
    set_session_model_once_settings,
    validate_provider_model,
)
from sylvia_gm.domain.authority import (
    PackageLock,
    PackageLockEntry,
    PackageRole,
    SafePayload,
    WriterRole,
    owned_payload,
)
from sylvia_gm.domain.campaign_plan import (
    PackageBinding,
    ValidatedCampaignPlan,
    validate_campaign_plan,
)
from sylvia_gm.domain.errors import (
    AuthorityError,
    CommandRejected,
    IdempotencyConflict,
    RulesetDiagnostic,
)
from sylvia_gm.domain.package_library import (
    CandidateStatus,
    PackageCandidate,
    ParticipantArtifactDeclaration,
    campaign_setting_entries,
    canonical_json_bytes,
)
from sylvia_gm.domain.session import (
    SessionContext,
    SessionMessage,
    SessionMessageKind,
    SessionRecord,
    SessionStatus,
)
from sylvia_gm.infrastructure.builder_source_ingestion import (
    MAX_SOURCE_FILES,
    finalize_builder_source_purge,
    finalize_existing_builder_source_purge,
    ingest_builder_sources,
    reconcile_builder_source_purges,
    restore_builder_sources_after_failed_purge,
    rollback_builder_source_batch,
    stage_builder_sources_for_purge,
)
from sylvia_gm.infrastructure.launch_handshake import derive_owner_response
from sylvia_gm.interfaces._display_reads import DisplayReads
from sylvia_gm.interfaces._session_reasoning import (
    reasoning_state,
    selected_model_info,
    validate_model_preferences,
)
from sylvia_gm.interfaces.local_api import LocalCommandBus
from sylvia_gm.interfaces.local_owner import LocalOwnerSupervisor
from sylvia_gm.provider._admin_audit import AdminAuditedProvider
from sylvia_gm.provider._context_fit import (
    GM_MODE_MINIMUM_CONTEXT_TOKENS,
    ContextFittingProvider,
)
from sylvia_gm.provider._exceptions import (
    AuthenticationError,
    ContextCompactionError,
    IncompleteProviderStreamError,
    InvalidRequestError,
    InvalidResponseError,
    InvalidToolArgumentsError,
    ModelCapacityError,
    ModelNotFoundError,
    ProviderConnectionError,
    ProviderContinuationRequiredError,
    ProviderPausedError,
    ProviderRefusalError,
    ProviderError,
    ProviderOutputLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
    TokenLimitError,
)
from sylvia_gm.provider._reasoning import bind_session_reasoning

_MAX_MESSAGE_BYTES = 67_108_864
_MAX_PACKAGE_BYTES = 48 * 1024 * 1024
_MAX_USER_TEXT_CHARS = 131_072
_MAX_CAMPAIGN_PLAN_BYTES = 1024 * 1024
_MAX_RUNTIME_DIAGNOSTIC_BYTES = 5 * 1024 * 1024
_MAX_ADMIN_AUDIT_VIEW_BYTES = 256 * 1024
_TEST_FULL_AUDIT_ENV = "SYLVIA_GM_TEST_FULL_AUDIT"


class OwnerProtocolError(ValueError):
    """A malformed or unsupported private owner-process message."""


def _runtime_diagnostic_path(storage_root: Path) -> Path:
    """Return the private, machine-local runtime journal path."""

    return storage_root / "diagnostics" / "runtime.jsonl"


def _prepare_runtime_diagnostic(path: Path) -> None:
    """Create the journal directory and rotate one bounded previous journal."""

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size >= _MAX_RUNTIME_DIAGNOSTIC_BYTES:
            previous = path.with_name("runtime.previous.jsonl")
            path.replace(previous)
    except OSError:
        # Diagnostics must never prevent Sylvia from launching or serving a request.
        return


def _write_runtime_diagnostic(
    path: Path,
    *,
    event: str,
    operation: str | None = None,
    outcome: str | None = None,
    error_code: str | None = None,
    error_type: str | None = None,
    duration_ms: float | None = None,
) -> None:
    """Append one metadata-only diagnostic without campaign or provider content."""

    record: dict[str, object] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "event": event,
    }
    if operation is not None:
        record["operation"] = (
            operation if re.fullmatch(r"[a-z][a-z0-9_]{0,79}", operation) else "invalid_operation"
        )
    if outcome is not None:
        record["outcome"] = outcome
    if error_code is not None:
        record["error_code"] = (
            error_code
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,79}", error_code)
            else "unknown_error"
        )
    if error_type is not None:
        record["error_type"] = (
            error_type
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,79}", error_type)
            else "UnknownError"
        )
    if duration_ms is not None:
        record["duration_ms"] = round(duration_ms, 1)
    try:
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n")
    except OSError:
        # Diagnostics are best effort and are never part of campaign authority.
        return


def _guided_start_selection_input(payload: Mapping[str, object]) -> dict[str, object]:
    """Compare selected content without participant IDs generated for each Start attempt."""
    selection = dict(payload)
    party = selection.get("selected_party")
    if isinstance(party, Sequence) and not isinstance(party, (str, bytes)):
        selection["selected_party"] = [
            {key: value for key, value in member.items() if key != "participant_id"}
            if isinstance(member, Mapping) else member
            for member in party
        ]
    return selection


def _campaign_plan_text(storage_root: Path, campaign_id: str) -> str:
    path = storage_root / "campaigns" / campaign_id / "campaign-plan.md"
    try:
        if not path.is_file():
            raise CommandRejected("Campaign plan file is missing")
        if path.stat().st_size > _MAX_CAMPAIGN_PLAN_BYTES:
            raise CommandRejected("Campaign plan file exceeds the usability limit")
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as error:
        raise CommandRejected("Campaign plan file is not valid UTF-8") from error
    except OSError as error:
        raise CommandRejected("Campaign plan file is not readable") from error
    if not text.strip():
        raise CommandRejected("Campaign plan file is empty or unusable")
    return text


def _campaign_plan_payload(storage_root: Path, campaign_id: str) -> SafePayload | None:
    """Read an optional structured Campaign Plan beside its readable rendering."""

    path = storage_root / "campaigns" / campaign_id / "campaign-plan.json"
    if not path.exists():
        return None
    try:
        if not path.is_file() or path.stat().st_size > _MAX_CAMPAIGN_PLAN_BYTES:
            raise CommandRejected("Structured Campaign Plan exceeds the usability limit")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CommandRejected("Structured Campaign Plan is not readable") from error
    if not isinstance(value, dict):
        raise CommandRejected("Structured Campaign Plan must be an object")
    return cast(SafePayload, value)


def _builder_workspace(storage_root: Path, session: object) -> Path:
    """Resolve a Builder workspace without manufacturing a Campaign Aggregate."""

    session_id = getattr(session, "session_id", None)
    campaign_id = getattr(session, "campaign_id", None)
    if not isinstance(session_id, str) or not session_id:
        raise OwnerProtocolError("Builder session identity is unavailable")
    if campaign_id is None:
        return storage_root / "builder-chats" / session_id
    if not isinstance(campaign_id, str) or not campaign_id:
        raise OwnerProtocolError("Builder campaign identity is invalid")
    return storage_root / "campaigns" / campaign_id


def _campaign_plan_tool_authority(
    owner: AuthorityOwner,
    campaign_id: str,
) -> CampaignPlanToolAuthority | None:
    """Bind structured Plan tools when the campaign has a Ruleset/Setting closure."""

    return campaign_plan_sources(owner, owner.current_package_lock(campaign_id).package_lock)



def _campaign_content_tool_authority(
    owner: AuthorityOwner, storage_root: Path | None = None
) -> CampaignContentToolAuthority:
    """Project bundled, installed and saved local content for Builder discovery."""

    items: list[SafePayload] = [
        cast(SafePayload, dict(item)) for item in (
            bundled_campaign_content_catalog() if storage_root is None
            else _campaign_creation_catalog(storage_root, owner)
        )
    ]
    known = {
        (str(item.get("kind")), str(item.get("id")), str(item.get("version", ""))) for item in items
    }
    for candidate in owner.package_candidates():
        setting_identities = {
            (entry.package_id, entry.version, entry.sha256)
            for entry in campaign_setting_entries(candidate.package_lock)
        }
        for entry in candidate.package_lock.entries:
            kind = "ruleset" if entry.role is PackageRole.PRIMARY else "package"
            if (entry.package_id, entry.version, entry.sha256) in setting_identities:
                kind = "campaign_setting"
            identity = (kind, entry.package_id, entry.version)
            if identity in known:
                continue
            known.add(identity)
            items.append(
                {
                    "kind": kind,
                    "id": entry.package_id,
                    "version": entry.version,
                    "title": entry.package_id,
                    "candidate_id": candidate.candidate_id,
                    "status": candidate.status.value,
                }
            )
    return CampaignContentToolAuthority(items=tuple(items))


_SEMANTIC_VERSION = re.compile(
    r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)


def _package_version_priority(
    version: str,
) -> tuple[int, int, int, int, int, tuple[tuple[int, str], ...], str]:
    """Order ordinary semantic versions while retaining a deterministic fallback."""

    matched = _SEMANTIC_VERSION.fullmatch(version)
    if matched is None:
        return (0, 0, 0, 0, 0, (), version.casefold())
    prerelease = matched.group(4)
    prerelease_key = tuple(
        (0, f"{int(part):020d}") if part.isdigit() else (1, part.casefold())
        for part in (() if prerelease is None else prerelease.split("."))
    )
    return (
        1,
        int(matched.group(1)),
        int(matched.group(2) or 0),
        int(matched.group(3) or 0),
        1 if prerelease is None else 0,
        prerelease_key,
        version.casefold(),
    )


def _preferred_campaign_candidates(
    candidates: Sequence[PackageCandidate],
) -> tuple[PackageCandidate, ...]:
    """Choose one campaign candidate per Ruleset by version, then install recency."""

    preferred: dict[str, tuple[PackageCandidate, str]] = {}
    for candidate in candidates:
        if candidate.status not in (CandidateStatus.VALIDATED, CandidateStatus.ACTIVATED):
            continue
        primary = next(
            (
                entry
                for entry in candidate.package_lock.entries
                if entry.role is PackageRole.PRIMARY
            ),
            None,
        )
        if primary is None:
            continue
        current = preferred.get(primary.package_id)
        primary_priority = _package_version_priority(primary.version)
        if current is None or primary_priority > _package_version_priority(current[1]):
            preferred[primary.package_id] = (candidate, primary.version)
    return tuple(item[0] for item in preferred.values())


def _unavailable_library_content(
    kind: str, artifact_id: str, error: Exception,
) -> dict[str, Any]:
    """Describe a rejected saved item without exposing malformed payloads or parser text."""
    label = "Saved Campaign Plan" if kind == "campaign_plan" else "Saved character"
    return {
        "kind": "unavailable_content",
        "content_kind": kind,
        "id": artifact_id,
        "title": f"{label} unavailable",
        "summary": (
            "The saved files do not meet their content requirements. Repair a copy in Builder."
        ),
        "validation_errors": [
            diagnostic.as_payload() for diagnostic in getattr(error, "diagnostics", ())[:8]
            if isinstance(diagnostic, RulesetDiagnostic)
        ],
    }


def _campaign_creation_catalog(
    storage_root: Path, application_owner: AuthorityOwner
) -> tuple[SafePayload, ...]:
    """Project logical campaign choices with exact compatible content bindings."""

    items: list[dict[str, Any]] = [dict(item) for item in bundled_campaign_content_catalog()]
    candidates = tuple(
        candidate for candidate in application_owner.package_candidates()
        if not any(entry.role is PackageRole.PRIMARY and entry.package_id in ARCHIVED_RULESET_IDS
                   for entry in candidate.package_lock.entries)
    )
    unavailable: dict[tuple[str, str], dict[str, Any]] = {}

    def record_invalid(kind: str, artifact_id: str, error: Exception) -> None:
        unavailable[(kind, artifact_id)] = _unavailable_library_content(kind, artifact_id, error)

    try:
        items.extend(dict(item.catalog_payload()) for item in character_profile_catalog(
            storage_root, on_invalid=lambda identity, error: record_invalid(
                "character_profile", identity, error,
            ),
        ))
    except (OSError, ValueError) as error:
        record_invalid("character_profile", "character-profile-library", error)

    try:
        for retained in reusable_campaign_plan_catalog(
            storage_root,
            validate_plan=lambda payload, text: validate_retained_campaign_plan(
                application_owner, payload, text, candidates=candidates,
            ),
            on_invalid=lambda artifact_id, error: record_invalid(
                "campaign_plan", artifact_id, error,
            ),
        ):
            projected = dict(retained.catalog_payload())
            public_summary = known_builtin_public_summary(retained.plan)
            if public_summary is not None:
                projected["summary"] = public_summary
            items.append(projected)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise CommandRejected(
            "Reusable Campaign Plan library contains an invalid artifact",
            code="reusable_campaign_plan_invalid",
        ) from error
    try:
        items.extend(
            dict(participant_catalog_payload(application_owner, participant))
            for participant in _reusable_participant_catalog(
                storage_root, application_owner, candidates=candidates,
                on_invalid=lambda artifact_id, error: record_invalid(
                    "participant", artifact_id, error,
                ),
            )
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise CommandRejected(
            "Reusable participant library contains an invalid artifact",
            code="reusable_participant_invalid",
        ) from error
    # Portable built-in Plans own exact world snapshots. Offer those worlds before
    # installation; the Interview admits the selected snapshot through normal checks.
    public_plans = [item for item in items if item.get("kind") == "campaign_plan" and item.get("ruleset") and item.get("campaign_setting")]
    for plan_item in public_plans:
        rule, world = plan_item["ruleset"], plan_item["campaign_setting"]
        choice = next((item for item in items if item.get("kind") == "ruleset" and item.get("id") == rule["id"]), None)
        if choice is None:
            choice = {"kind": "ruleset", **dict(rule), "title": rule["id"], "selection_id": rule["id"], "compatible_settings": ()}
            items.append(choice)
        settings = tuple(choice.get("compatible_settings", ()))
        if not any(binding["id"] == world["id"] for binding in settings):
            choice["compatible_settings"] = settings + ({**dict(world), "candidate_id": None, "ruleset": dict(rule)},)
    # A catalogue may replace the historical starter with portable Plans. The
    # legacy binding adjustment is needed only when that Plan is inventoried.
    base_authority = bundled_rift_authority()
    bundled_ruleset = base_authority.ruleset.as_payload()
    bundled_setting = base_authority.setting.as_payload()
    bundled_plan = next((item for item in items if item.get("kind") == "campaign_plan"
                         and item.get("setup_id") == "the-rift-awakening"), None)
    if bundled_plan is not None:
        bundled_ruleset = cast(dict[str, object], bundled_plan["ruleset"])
        bundled_setting = cast(dict[str, object], bundled_plan["campaign_setting"])
    for item in items:
        if item.get("kind") == "ruleset" and item.get("id") == bundled_ruleset["id"]:
            item["content_sha256"] = bundled_ruleset["content_sha256"]
            item["selection_id"] = item["id"]
            settings = tuple(item.get("compatible_settings", ()))
            binding = {**bundled_setting, "candidate_id": None, "ruleset": dict(bundled_ruleset)}
            item["compatible_settings"] = tuple(
                existing for existing in settings if existing.get("id") != bundled_setting["id"]
            ) + (binding,)
        elif item.get("kind") == "campaign_setting" and item.get("id") == bundled_setting["id"]:
            item["content_sha256"] = bundled_setting["content_sha256"]

    ruleset_indexes = {
        str(item["id"]): index for index, item in enumerate(items) if item.get("kind") == "ruleset"
    }
    known_settings = {
        (str(item["id"]), str(item["version"]), str(item.get("content_sha256", "")))
        for item in items
        if item.get("kind") == "campaign_setting"
    }
    for candidate in _preferred_campaign_candidates(candidates):
        primary = next(
            (
                entry
                for entry in candidate.package_lock.entries
                if entry.role is PackageRole.PRIMARY
            ),
            None,
        )
        if primary is None:
            continue
        existing_index = ruleset_indexes.get(primary.package_id)
        candidate_priority = _package_version_priority(primary.version)
        if existing_index is not None:
            existing_version = str(items[existing_index].get("version", "0"))
            existing_priority = _package_version_priority(existing_version)
            if candidate_priority < existing_priority:
                continue
        setting_identities = [
            (entry.package_id, entry.version, entry.sha256)
            for entry in campaign_setting_entries(candidate.package_lock)
        ]
        if (
            not setting_identities and existing_index is not None
            and candidate_priority <= existing_priority
        ):
            continue
        if existing_index is not None and candidate_priority == existing_priority:
            existing_primary_sha = str(items[existing_index].get("content_sha256", ""))
            existing_setting_identities = {
                (
                    str(binding.get("id", "")),
                    str(binding.get("version", "")),
                    str(binding.get("content_sha256", "")),
                )
                for binding in items[existing_index].get("compatible_settings", ())
                if isinstance(binding, Mapping)
            }
            primary_bytes_conflict = bool(
                existing_primary_sha and primary.sha256 != existing_primary_sha
            )
            dependency_bytes_conflict = bool(
                existing_setting_identities
                and not existing_setting_identities.issubset(set(setting_identities))
            )
            if primary_bytes_conflict or dependency_bytes_conflict:
                # Package versions are immutable identities. Persisted equal-version bytes that
                # no longer match the current bundle may still belong to an exact existing
                # campaign lock, but they cannot replace the bundle for a new Draft.
                continue
        setting_bindings: list[SafePayload] = []
        for identity in setting_identities:
            setting_bindings.append(
                {
                    "id": identity[0],
                    "version": identity[1],
                    "content_sha256": identity[2],
                    "candidate_id": candidate.candidate_id,
                }
            )
            if identity not in known_settings:
                items.append(
                    {
                        "kind": "campaign_setting",
                        "id": identity[0],
                        "version": identity[1],
                        "content_sha256": identity[2],
                        "title": application_owner.package_label(*identity),
                    }
                )
                known_settings.add(identity)
        choice: dict[str, Any] = {
            "kind": "ruleset",
            "id": primary.package_id,
            "version": primary.version,
            "content_sha256": primary.sha256,
            "title": (
                str(items[existing_index].get("title") or primary.package_id)
                if existing_index is not None
                else application_owner.package_label(
                    primary.package_id, primary.version, primary.sha256
                )
            ),
            "selection_id": (
                primary.package_id
                if ((primary.package_id, primary.version, primary.sha256) == (
                    bundled_ruleset["id"], bundled_ruleset["version"], bundled_ruleset["content_sha256"],
                ) or (existing_index is not None
                      and items[existing_index].get("selection_id") == primary.package_id
                      and items[existing_index].get("content_sha256") == primary.sha256))
                else candidate.candidate_id
            ),
            "compatible_settings": tuple(setting_bindings),
            "selection_aliases": [
                item.candidate_id for item in candidates
                if any(
                    entry.role is PackageRole.PRIMARY
                    and (entry.package_id, entry.version, entry.sha256) == (
                        primary.package_id, primary.version, primary.sha256
                    )
                    for entry in item.package_lock.entries
                )
            ],
        }
        if existing_index is None:
            ruleset_indexes[primary.package_id] = len(items)
            items.append(choice)
        else:
            items[existing_index] = choice

    # A Setting owns its Ruleset copy. Discover every such Setting independently of
    # which source Ruleset version is currently preferred in the first picker.
    inherited_rulesets = {
        entry.package_id for candidate in candidates
        if any(dep.kind == "ruleset_snapshot"
               for member in candidate.package_lock.entries for dep in member.dependencies)
        for entry in candidate.package_lock.entries if entry.role is PackageRole.PRIMARY
    }
    snapshots: dict[str, dict[str, dict[str, Any]]] = {}
    for candidate in candidates:
        primary = next(
            entry for entry in candidate.package_lock.entries if entry.role is PackageRole.PRIMARY
        )
        if primary.package_id not in inherited_rulesets:
            continue
        for setting in campaign_setting_entries(candidate.package_lock):
            if (
                primary.package_id == bundled_ruleset["id"]
                and setting.package_id == bundled_setting["id"]
                and _package_version_priority(setting.version) <= _package_version_priority(
                    str(bundled_setting["version"]),
                )
                and setting.sha256 != bundled_setting["content_sha256"]
            ):
                continue
            ruleset_settings = snapshots.setdefault(primary.package_id, {})
            previous = ruleset_settings.get(setting.package_id)
            if previous is not None and _package_version_priority(setting.version) <= (
                _package_version_priority(str(previous["version"]))
            ):
                continue
            ruleset_settings[setting.package_id] = {
                "id": setting.package_id,
                "version": setting.version,
                "content_sha256": setting.sha256,
                "candidate_id": candidate.candidate_id,
                "ruleset": {
                    "id": primary.package_id,
                    "version": primary.version,
                    "content_sha256": primary.sha256,
                },
            }
            identity = (setting.package_id, setting.version, setting.sha256)
            if identity not in known_settings:
                items.append({
                    "kind": "campaign_setting", "id": setting.package_id,
                    "version": setting.version, "content_sha256": setting.sha256,
                    "title": application_owner.package_label(
                        setting.package_id, setting.version, setting.sha256
                    ),
                })
                known_settings.add(identity)
    for ruleset_id, settings in snapshots.items():
        choice = items[ruleset_indexes[ruleset_id]]
        choice["compatible_settings"] = tuple(settings.values()) + tuple(
            binding for binding in choice.get("compatible_settings", ())
            if binding["id"] not in settings
        )
        choice["selection_aliases"] = list(dict.fromkeys([
            *choice.get("selection_aliases", []),
            *(candidate.candidate_id for candidate in candidates
              if any(entry.role is PackageRole.PRIMARY and entry.package_id == ruleset_id
                     for entry in candidate.package_lock.entries)),
        ]))
    valid_identities = {str(item.get("id")) for item in items}
    items.extend(
        item for (_, artifact_id), item in unavailable.items()
        if artifact_id not in valid_identities
    )
    return tuple(cast(SafePayload, item) for item in items)


def _rift_participant_authority() -> tuple[PackageLockEntry, ParticipantArtifactDeclaration]:
    """Return the exact bundled Rift package identity and participant schema."""

    binding = bundled_rift_authority().ruleset
    return (
        PackageLockEntry(
            package_id=binding.package_id,
            version=binding.version,
            sha256=binding.content_sha256,
            role=PackageRole.PRIMARY,
            load_order=0,
            dependencies=(),
            capabilities=(),
        ),
        bundled_rift_participant_declaration(),
    )


def _rift_reusable_participants(
    storage_root: Path,
    *,
    on_invalid: Callable[[str, Exception], None] | None = None,
) -> tuple[ReusableParticipant, ...]:
    """Load participants compatible with the bundled Severed Lands/Rift binding."""

    if not (storage_root / "participants").exists():
        return ()
    package, declaration = _rift_participant_authority()
    campaign_setting = bundled_rift_authority().setting
    return compatible_reusable_participants(
        storage_root,
        package=package,
        declaration=declaration,
        campaign_setting=campaign_setting,
        legacy_campaign_setting=campaign_setting,
        on_invalid=on_invalid,
    )


def _reusable_participant_catalog(
    storage_root: Path, owner: AuthorityOwner,
    *,
    candidates: Sequence[PackageCandidate] | None = None,
    on_invalid: Callable[[str, Exception], None] | None = None,
) -> tuple[ReusableParticipant, ...]:
    """Discover library characters using the exact schemas of installed Rulesets and Settings."""
    if not (storage_root / "participants").exists():
        return ()
    found = {
        item.artifact_id: item for item in _rift_reusable_participants(
            storage_root, on_invalid=on_invalid,
        )
    }
    inspected: set[tuple[str, str, str, str, str]] = set()
    available_candidates = owner.package_candidates() if candidates is None else candidates
    for candidate in available_candidates:
        primary = next(
            (entry for entry in candidate.package_lock.entries
             if entry.role is PackageRole.PRIMARY),
            None,
        )
        if primary is None:
            continue
        settings = campaign_setting_entries(candidate.package_lock)
        for declaration in owner.exact_participant_declarations(
            primary.package_id, primary.version, primary.sha256
        ):
            for setting in settings:
                key = (primary.sha256, declaration.schema_id, declaration.schema_sha256,
                       setting.package_id, setting.sha256)
                if key in inspected:
                    continue
                inspected.add(key)
                sources = campaign_plan_sources(
                    owner, candidate.package_lock, setting_id=setting.package_id
                )
                if sources is None:
                    continue
                for item in compatible_reusable_participants(
                    storage_root,
                    package=primary,
                    declaration=declaration,
                    campaign_setting=PackageBinding(
                        setting.package_id, setting.version, setting.sha256
                    ),
                    setting_character_schema=sources.setting_character_schema,
                    on_invalid=on_invalid,
                ):
                    found[item.artifact_id] = item
    return tuple(found[key] for key in sorted(found))


def _admitted_dm_campaign_plan(
    owner: AuthorityOwner,
    *,
    campaign_id: str,
    session_id: str,
) -> tuple[str, Mapping[str, object] | None]:
    suffix = hashlib.sha256(campaign_id.encode("utf-8")).hexdigest()[:24]
    try:
        plan = owner.campaign_plan(f"campaign-plan-{suffix}")
        handoff = owner.builder_dm_handoff(f"builder-dm-handoff-{suffix}")
    except CommandRejected as error:
        raise CommandRejected("DM session requires an admitted Builder campaign plan") from error
    if (
        plan.campaign_id != campaign_id
        or handoff.campaign_id != campaign_id
        or handoff.plan_id != plan.plan_id
        or handoff.dm_session_id != session_id
    ):
        raise CommandRejected("DM session does not match the admitted campaign plan")
    current = owner.accepted_campaign_plan(campaign_id)
    cursor = current
    seen = set()
    while isinstance(cursor.content.get("sequel"), Mapping):
        if cursor.plan_id in seen or len(seen) >= 32:
            raise CommandRejected("Campaign adventure lineage is invalid")
        seen.add(cursor.plan_id)
        cursor = owner.campaign_plan(cursor.content["sequel"]["previous_plan_id"])
        if cursor.campaign_id != campaign_id:
            raise CommandRejected("Campaign adventure lineage belongs to another campaign")
    if cursor.plan_id != plan.plan_id:
        raise CommandRejected("Current adventure is not descended from the admitted handoff")
    plan = current
    admitted_text = plan.content.get("campaign_plan_markdown")
    if not isinstance(admitted_text, str) or not admitted_text.strip():
        raise CommandRejected("Admitted campaign plan content is unavailable")
    if len(admitted_text.encode("utf-8")) > _MAX_CAMPAIGN_PLAN_BYTES:
        raise CommandRejected("Admitted campaign plan exceeds the usability limit")
    structured = plan.content.get("campaign_plan")
    from sylvia_gm.domain.campaign_sequel import sequel_markdown

    return sequel_markdown(plan.content), structured if isinstance(structured, Mapping) else None


def _read_message(stream: TextIO) -> dict[str, Any] | None:
    line = stream.readline(_MAX_MESSAGE_BYTES + 1)
    if line == "":
        return None
    if len(line.encode("utf-8")) > _MAX_MESSAGE_BYTES or not line.endswith("\n"):
        raise OwnerProtocolError("Owner message exceeds the bounded line protocol")
    try:
        value = json.loads(line)
    except json.JSONDecodeError as error:
        raise OwnerProtocolError("Owner message is not valid JSON") from error
    if not isinstance(value, dict):
        raise OwnerProtocolError("Owner message must be a JSON object")
    return value


def _write_message(stream: TextIO, payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    stream.write(encoded + "\n")
    stream.flush()


def _json_value(value: Any) -> Any:
    """Convert an authority projection into strict JSON-compatible values."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _json_value(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, (Mapping, MappingProxyType)):
        if value.get("kind") == "provider_tool_results":
            value = {**value, "results": [
                {**item, "content": '{"status":"session_history_read"}'}
                if isinstance(item, Mapping) and item.get("name") == "read_session_history"
                else item for item in value.get("results", ())
            ]}
        if value.get("kind") == "provider_tool_calls":
            assistant = value.get("assistant")
            if isinstance(assistant, Mapping):
                value = {**value, "assistant": {**assistant, "tool_calls": [
                    {**call, "function": {**call["function"], "arguments": "{}"}}
                    if isinstance(call, Mapping) and isinstance(call.get("function"), Mapping)
                    and call["function"].get("name") == "read_session_history"
                    else call for call in assistant.get("tool_calls", ())
                ]}}
        if value.get("kind") == "working_context_checkpoint":
            # Continuation notes can contain GM-private facts; only the marker crosses the wire.
            return {"kind": "working_context_checkpoint",
                    "checkpoint_id": value.get("checkpoint_id"),
                    "covered_exchanges": value.get("covered_exchanges"),
                    "text": "Context compacted · saved conversation is kept"}
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, Set):
        projected = [_json_value(item) for item in value]
        return sorted(
            projected,
            key=lambda item: json.dumps(
                item, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ),
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_value(item) for item in value]
    raise TypeError(f"Owner projection type is not JSON-compatible: {type(value).__name__}")


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise OwnerProtocolError(f"{key} must be a nonempty string")
    return value


def _required_nonnegative_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise OwnerProtocolError(f"{key} must be a nonnegative integer")
    return value


def _optional_nonnegative_int(payload: Mapping[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    return _required_nonnegative_int(payload, key)


def _optional_text(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    return _required_text(payload, key)


def _optional_payload(payload: Mapping[str, Any], key: str) -> SafePayload | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise OwnerProtocolError(f"{key} must be an object")
    try:
        return owned_payload(value)
    except (TypeError, ValueError) as error:
        raise OwnerProtocolError(f"{key} must be canonical JSON") from error


def _required_artifact_id(payload: Mapping[str, Any], key: str) -> str:
    value = _required_text(payload, key)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value) is None:
        raise OwnerProtocolError(f"{key} is not a safe artifact identity")
    return value


def _optional_bool(payload: Mapping[str, Any], key: str, *, default: bool = False) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise OwnerProtocolError(f"{key} must be a boolean")
    return value


def _candidate_package_lock(bus: LocalCommandBus, candidate_id: str) -> PackageLock:
    candidate: Any = bus.package_candidate(candidate_id)
    package_lock: PackageLock = candidate.package_lock
    return package_lock


def _safe_candidate_projection(candidate: PackageCandidate) -> SafePayload:
    return {
        "candidate_id": candidate.candidate_id,
        "status": candidate.status.value,
        "closure_sha256": candidate.closure_sha256,
        "packages": [
            {
                "id": entry.package_id,
                "version": entry.version,
                "content_sha256": entry.sha256,
            }
            for entry in candidate.package_lock.entries
        ],
        "validation_report": _json_value(candidate.validation_report),
        "activation": "not_performed",
    }


def _seed_campaign_plan(
    storage_root: Path,
    campaign_id: str,
    text: str,
    structured_plan: SafePayload | None = None,
) -> None:
    """Create the authored plan once without overwriting later Builder customization."""

    encoded = text.encode("utf-8")
    if not encoded or len(encoded) > _MAX_CAMPAIGN_PLAN_BYTES:
        raise CommandRejected("Campaign plan file exceeds the usability limit")
    target = storage_root / "campaigns" / campaign_id / "campaign-plan.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if not target.is_file():
            raise CommandRejected("Campaign plan path is not a regular file")
    else:
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".campaign-plan-",
                suffix=".tmp",
                dir=target.parent,
                delete=False,
            ) as temporary:
                temporary.write(encoded)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, target)
        except OSError as error:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise CommandRejected("Campaign plan file could not be created") from error
    if structured_plan is None:
        return
    structured_target = target.with_suffix(".json")
    structured_bytes = canonical_json_bytes(structured_plan)
    if len(structured_bytes) > _MAX_CAMPAIGN_PLAN_BYTES:
        raise CommandRejected("Structured Campaign Plan exceeds the usability limit")
    if structured_target.exists():
        if not structured_target.is_file():
            raise CommandRejected("Structured Campaign Plan path is not a regular file")
        return
    try:
        structured_target.write_bytes(structured_bytes)
    except OSError as error:
        raise CommandRejected("Structured Campaign Plan could not be created") from error


def _resolve_rift_library_selection(
    owner: AuthorityOwner, storage_root: Path, *, ruleset_selection: str,
    setting_id: str, plan_id: str,
) -> tuple[PackageCandidate, CampaignPlanToolAuthority, ValidatedCampaignPlan, dict | None]:
    """Use a Rift Plan's retained sources, including its authored party template when present."""
    if plan_id == "the-rift-awakening":
        plan = bundled_rift_campaign_plan()
    else:
        saved = load_reusable_campaign_plan(
            storage_root, plan_id,
            validate_plan=lambda payload, text: validate_retained_campaign_plan(
                owner, payload, text,
            ),
        )
        if saved.setup_id != "the-rift-awakening":
            raise CommandRejected("The selected Plan does not use Rift campaign setup")
        plan = saved.plan
    bindings = plan.payload["bindings"]
    if setting_id != bindings["campaign_setting"]["id"]:
        raise CommandRejected("The selected Plan belongs to another Campaign Setting")
    if ruleset_selection != bindings["ruleset"]["id"]:
        selected = owner.package_candidate(ruleset_selection)
        if not any(
            entry.role is PackageRole.PRIMARY and entry.package_id == bindings["ruleset"]["id"]
            for entry in selected.package_lock.entries
        ):
            raise CommandRejected("The selected Plan belongs to another Ruleset")
    candidate = next(
        (item for item in owner.package_candidates()
         if item.closure_sha256 == bindings["package_closure_sha256"]),
        None,
    )
    if candidate is None:
        if bindings["package_closure_sha256"] != bundled_rift_authority().closure_sha256:
            raise CommandRejected("The selected Plan's saved packages are unavailable")
        archive = bundled_rift_archive()
        candidate = owner.install_package_archive(
            io.BytesIO(archive),
            install_id=f"install-bundled-rift-{hashlib.sha256(archive).hexdigest()[:24]}",
        )
    sources = campaign_plan_sources(owner, candidate.package_lock, setting_id=setting_id)
    if sources is None:
        raise CommandRejected("The selected Plan's Setting is unavailable")
    template_path = "campaigns/the-rift-awakening/party-template.json"
    if template_path not in sources.setting_paths and any(
        dependency.kind == "ruleset_snapshot"
        for entry in candidate.package_lock.entries for dependency in entry.dependencies
    ):
        raise CommandRejected("The selected Rift Plan has no contained party template")
    template = (
        validate_rift_template(json.loads(sources.read_setting(template_path)))
        if template_path in sources.setting_paths else None
    )
    return candidate, sources, plan, template


def _start_rift_campaign(
    bus: LocalCommandBus,
    application_owner: AuthorityOwner,
    storage_root: Path,
    *,
    operation_id: str,
    campaign_id: str,
    builder_session_id: str,
    ruleset_selection: str,
    setting_id: str,
    plan_id: str,
    display_name: str,
    lane: str,
    archetype_id: str,
    companion_ids: tuple[str, ...],
    customization_notes: str,
    provider_id: str,
    model_id: str,
    construction_intent: SafePayload | None = None,
) -> SafePayload:
    """Compose The Rift through the ordinary package and construction authorities."""

    candidate, rift_authority, selected_plan, party_template = _resolve_rift_library_selection(
        application_owner, storage_root, ruleset_selection=ruleset_selection,
        setting_id=setting_id, plan_id=plan_id,
    )

    try:
        existing_basis = application_owner.campaign_construction_basis(campaign_id)
    except CommandRejected as error:
        if str(error) not in {
            "Confirmed campaign construction is unavailable",
            "Campaign construction has not begun",
        }:
            raise
    else:
        current_lock = application_owner.current_package_lock(campaign_id)
        primary = next(
            (
                entry
                for entry in current_lock.package_lock.entries
                if entry.role is PackageRole.PRIMARY
            ),
            None,
        )
        if (
            existing_basis.builder_session_id != builder_session_id
            or existing_basis.display_name != display_name
            or existing_basis.lane != lane
            or primary is None
            or primary.package_id != RIFT_RULESET_ID
        ):
            raise CommandRejected(
                "The interrupted Rift Start basis differs from the current selections"
            )
        return {
            "campaign_id": campaign_id,
            "resulting_revision": existing_basis.begin_revision,
            "builder_session_id": builder_session_id,
            "template_id": "the-rift-awakening",
            "lane": existing_basis.lane,
        }

    primary = next(
        (
            entry
            for entry in candidate.package_lock.entries
            if entry.role is PackageRole.PRIMARY and entry.package_id == RIFT_RULESET_ID
        ),
        None,
    )
    declaration = next(
        (
            item
            for item in (application_owner.exact_participant_declarations(
                primary.package_id, primary.version, primary.sha256
            ) if primary is not None else ())
            if item.kind == "player_character"
        ),
        None,
    )
    if primary is None or declaration is None:
        raise CommandRejected("The bundled D&D campaign package is unavailable")
    suffix = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]
    selected_plan_payload = selected_plan.payload
    starting_party_policy = selected_plan_payload.get("starting_party")
    if not isinstance(starting_party_policy, Mapping):
        raise CommandRejected("The Rift Campaign Plan starting party is invalid")
    selection = materialize_rift_selection(
        participant_namespace=suffix,
        lane=lane,
        archetype_id=archetype_id,
        companion_ids=companion_ids,
        customization_notes=customization_notes,
        package=primary,
        participant_declaration=declaration,
        starting_party_policy=starting_party_policy,
        template=party_template,
        selected_plan=selected_plan,
        setting_character_schema=rift_authority.setting_character_schema,
        reusable_participants=compatible_reusable_participants(
            storage_root,
            package=primary,
            declaration=declaration,
            campaign_setting=rift_authority.setting,
            legacy_campaign_setting=bundled_rift_authority().setting,
            setting_character_schema=rift_authority.setting_character_schema,
        ),
    )
    intent = owned_payload(construction_intent or {})
    plan_payload = json.loads(canonical_json_bytes(selected_plan_payload))
    if not isinstance(plan_payload, dict):
        raise CommandRejected("The Rift Campaign Plan is invalid")
    boundaries = intent.get("boundaries")
    preferences = intent.get("preferences")
    specific_requests = intent.get("specific_requests")
    contributions = intent.get("contributions")
    accepted_lines: list[str] = []
    if isinstance(boundaries, str) and boundaries.strip():
        plan_boundaries = plan_payload.get("boundaries")
        if not isinstance(plan_boundaries, list):
            raise CommandRejected("The Rift Campaign Plan boundaries are invalid")
        plan_boundaries.append(f"Player Boundary: {boundaries.strip()}")
        accepted_lines.append(f"Hard Boundaries: {boundaries.strip()}")
    guidance = plan_payload.get("gm_guidance")
    if not isinstance(guidance, dict) or not isinstance(guidance.get("instructions"), str):
        raise CommandRejected("The Rift Campaign Plan guidance is invalid")
    guidance_lines: list[str] = []
    if isinstance(preferences, str) and preferences.strip():
        guidance_lines.append(f"Player Preferences: {preferences.strip()}")
        accepted_lines.append(f"Preferences: {preferences.strip()}")
    if isinstance(specific_requests, str) and specific_requests.strip():
        guidance_lines.append(f"Player Specific Requests: {specific_requests.strip()}")
        accepted_lines.append(f"Specific Requests: {specific_requests.strip()}")
    if isinstance(contributions, Mapping):
        for contribution_step, contribution_answer in sorted(contributions.items()):
            if not isinstance(contribution_answer, Mapping):
                continue
            answer_values = contribution_answer.get("answers")
            if not isinstance(answer_values, Mapping):
                continue
            rendered = ", ".join(f"{key}={value}" for key, value in sorted(answer_values.items()))
            if rendered:
                guidance_lines.append(
                    f"Accepted {str(contribution_step).replace('_', ' ')}: {rendered}"
                )
                accepted_lines.append(
                    f"{str(contribution_step).replace('_', ' ').title()}: {rendered}"
                )
    if guidance_lines:
        guidance["instructions"] = (
            str(guidance["instructions"]).rstrip() + "\n" + "\n".join(guidance_lines)
        )
    plan_markdown = selection.campaign_plan_markdown
    if accepted_lines:
        plan_markdown += (
            "\n\n## Accepted Campaign Construction\n\n"
            + "\n".join(f"- {line}" for line in accepted_lines)
            + "\n"
        )
    validated_plan = validate_campaign_plan(
        plan_payload,
        readable_markdown=plan_markdown,
        expected_ruleset=rift_authority.ruleset,
        expected_setting=rift_authority.setting,
        expected_closure_sha256=rift_authority.closure_sha256,
        setting_paths=rift_authority.setting_paths,
        setting_knowledge_ids=rift_authority.setting_knowledge_ids,
    )
    selection = dataclasses.replace(
        selection,
        campaign_plan=validated_plan.payload,
        campaign_plan_markdown=validated_plan.readable_markdown,
    )
    validated_starting_party = validated_plan.payload.get("starting_party")
    if not isinstance(validated_starting_party, Mapping):  # pragma: no cover - validated format
        raise CommandRejected("The validated Campaign Plan starting party is unavailable")
    validate_rift_party_handoff(selection, validated_starting_party)
    application_owner.append_agent_message(
        session_id=builder_session_id,
        message_id=f"ready-to-begin-{suffix}",
        kind=SessionMessageKind.USER,
        content={
            "text": "Ready to Begin",
            "interview_step": "ready",
            "interview_value": {"action": "ready_to_begin"},
        },
    )
    confirmation = bus.bind_guided_campaign_basis(
        operation_id=f"confirm-rift-{suffix}",
        campaign_id=campaign_id,
        display_name=display_name,
        package_lock=candidate.package_lock,
        turn_id=f"turn-construction-rift-{suffix}",
        builder_session_id=builder_session_id,
        package_candidate_id=candidate.candidate_id,
        participants=selection.participants,
        initial_state=selection.initial_state,
    )
    _seed_campaign_plan(
        storage_root,
        confirmation.campaign_id,
        selection.campaign_plan_markdown,
        selection.campaign_plan,
    )
    begun = bus.begin_campaign_construction(
        operation_id=f"begin-rift-{suffix}",
        campaign_id=confirmation.campaign_id,
        lane=lane,
        expected_revision=confirmation.resulting_revision,
        canonical_input=selection.canonical_input,
    )
    return {
        "campaign_id": confirmation.campaign_id,
        "resulting_revision": begun.resulting_revision,
        "builder_session_id": builder_session_id,
        "template_id": "the-rift-awakening",
        "lane": lane,
    }


def _publish_builder_package_candidate(
    owner: AuthorityOwner,
    builder_session_id: str,
    archive: BinaryIO,
    *,
    install_id: str,
    expected_closure_sha256: str,
) -> PackageCandidate:
    """Publish once; identical Builder retries return the already retained package."""
    candidate_id = f"candidate-{install_id}"
    existing = None
    try:
        existing = owner.package_candidate(candidate_id)
    except CommandRejected as error:
        if error.code != "candidate_unavailable":
            raise
    else:
        if existing.closure_sha256 != expected_closure_sha256:
            raise IdempotencyConflict("Builder publication identity payload does not match")
    candidate = existing or owner.publish_builder_package_candidate(
        builder_session_id=builder_session_id, archive=archive, install_id=install_id,
    )
    if candidate.closure_sha256 != expected_closure_sha256:
        raise IdempotencyConflict("Builder publication result does not match materialized closure")
    return candidate


def _install_plan_snapshot(owner: AuthorityOwner, snapshot: bytes, expected_closure: str) -> None:
    """Install a validated Plan's inherited bytes once across repeated imports."""
    install_id = f"plan-snapshot-{hashlib.sha256(snapshot).hexdigest()[:32]}"
    try:
        candidate = owner.package_candidate(f"candidate-{install_id}")
    except CommandRejected as error:
        if error.code != "candidate_unavailable":
            raise
        candidate = owner.install_package_archive(io.BytesIO(snapshot), install_id=install_id)
    if candidate.closure_sha256 != expected_closure:
        raise IdempotencyConflict("Imported Plan package snapshot does not match its saved closure")


def _publish_builder_ruleset_candidate(
    owner: AuthorityOwner,
    builder_session_id: str,
    archive: BinaryIO,
    *,
    install_id: str,
    expected_closure_sha256: str,
) -> SafePayload:
    candidate = _publish_builder_package_candidate(
        owner, builder_session_id, archive, install_id=install_id,
        expected_closure_sha256=expected_closure_sha256,
    )
    if owner.session_context(builder_session_id).session.campaign_id is None:
        inspection = owner.inspect_package_candidate(candidate.candidate_id)
        primary = next(item for item in inspection["packages"] if item["role"] == "primary")
        owner.rename_builder_chat(
            builder_session_id, f"{primary['label']} · Ruleset"[:120].strip(), automatic=True,
        )
    return _safe_candidate_projection(candidate)


def _launch(
    envelope: Mapping[str, Any],
) -> tuple[LocalOwnerSupervisor, LocalCommandBus, str, str, Path]:
    if envelope.get("type") != "launch":
        raise OwnerProtocolError("The first owner message must be a launch envelope")
    secret_hex = _required_text(envelope, "secret_hex")
    database_path = Path(_required_text(envelope, "database_path")).expanduser().resolve()
    owner_id = _required_text(envelope, "owner_id")
    database_path.parent.mkdir(parents=True, exist_ok=True)

    external_response = derive_owner_response(secret_hex)
    supervisor = LocalOwnerSupervisor()
    try:
        internal = supervisor.acquire_owner(database_path, owner_id)
        supervisor.complete_handshake(derive_owner_response(internal.secret_hex))
    except BaseException:
        supervisor.shutdown()
        raise
    return (
        supervisor,
        LocalCommandBus(supervisor),
        external_response,
        owner_id,
        database_path.parent,
    )


def _preinstall_bundled_srd(bus: LocalCommandBus) -> None:
    """Idempotently install the bundled SRD before a fresh owner reports ready."""

    version = bundled_srd_version()
    raw_candidates = bus.package_candidates()
    if not isinstance(raw_candidates, Sequence):
        raise RuntimeError("Package candidate projection is invalid")
    for candidate in cast(Sequence[PackageCandidate], raw_candidates):
        if any(
            entry.package_id == RIFT_RULESET_ID and entry.version == version
            for entry in candidate.package_lock.entries
        ):
            return
    archive = bundled_srd_archive()
    install_seed = hashlib.sha256(f"{RIFT_RULESET_ID}\0{version}".encode()).hexdigest()
    bus.install_package(
        io.BytesIO(archive),
        install_id=f"install-bundled-srd-{install_seed[:24]}",
    )


def _selection_from_reference(
    choices: Sequence[Mapping[str, object]], reference: str
) -> dict[str, str]:
    """Resolve an exact or uniquely named configured model without guessing."""

    folded = reference.casefold()
    matches: list[dict[str, str]] = []
    for choice in choices:
        provider_id = choice.get("provider_id")
        model_id = choice.get("model_id")
        model_name = choice.get("model_name")
        if not isinstance(provider_id, str) or not isinstance(model_id, str):
            continue
        provider_prefix = provider_id.replace("_", "-") + "/"
        upstream_id = (
            model_id[len(provider_prefix) :] if model_id.startswith(provider_prefix) else model_id
        )
        candidates = {model_id.casefold(), upstream_id.casefold()}
        if isinstance(model_name, str):
            candidates.add(model_name.casefold())
        if folded in candidates:
            matches.append({"provider_id": provider_id, "model_id": model_id})
    unique = {(match["provider_id"], match["model_id"]): match for match in matches}
    if len(unique) != 1:
        raise CommandRejected(
            "Model reference is unavailable or ambiguous; use the configured picker"
        )
    return next(iter(unique.values()))


def _switch_session_model(
    *,
    application_owner: AuthorityOwner,
    session_loop: CampaignSessionLoop,
    config_path: Path,
    session_id: str,
    provider_id: object,
    model_id: object,
    mode: str,
    choices: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Apply one idle-session model switch with Hermes-compatible scope."""

    session_loop.assert_idle(session_id)
    selection = require_configured_model(
        config_path,
        provider_id=provider_id,
        model_id=model_id,
    )
    prior = application_owner.session_context(session_id).session
    validate_model_preferences(
        dataclasses.replace(
            prior, provider_id=selection["provider_id"], model_id=selection["model_id"],
        ),
        choices, once=mode == "once",
    )
    if mode == "once":
        set_session_model_once_settings(
            config_path,
            session_id=session_id,
            provider_id=selection["provider_id"],
            model_id=selection["model_id"],
        )
        return {
            "status": "staged_once",
            "session": application_owner.session_context(session_id).session,
            "once": selection,
        }
    if mode not in {"current", "global"}:
        raise OwnerProtocolError("Model switch mode is unsupported")
    updated = application_owner.update_agent_session_model(
        session_id,
        provider_id=selection["provider_id"],
        model_id=selection["model_id"],
    )
    if mode == "global":
        try:
            set_default_model_settings(
                config_path,
                provider_id=selection["provider_id"],
                model_id=selection["model_id"],
            )
        except BaseException:
            application_owner.update_agent_session_model(
                session_id,
                provider_id=prior.provider_id,
                model_id=prior.model_id,
            )
            raise
    pending = session_model_once_settings(config_path, session_id=session_id)
    if pending is not None:
        clear_session_model_once_settings(
            config_path,
            session_id=session_id,
            expected_provider_id=pending["provider_id"],
            expected_model_id=pending["model_id"],
        )
    return {
        "status": "switched_global" if mode == "global" else "switched",
        "session": updated,
        "once": None,
        "default_model": provider_settings(config_path).get("default_model"),
    }


def _switch_session_reasoning(
    *, application_owner: AuthorityOwner, session_loop: CampaignSessionLoop,
    config_path: Path, session_id: str, effort: object, mode: str,
    choices: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Validate saved and next-reply preferences at the same idle-session boundary as models."""
    from sylvia_gm.provider._reasoning import select_reasoning

    session_loop.assert_idle(session_id)
    if mode not in {"current", "once"}:
        raise OwnerProtocolError("Reasoning scope must be current chat or next reply")
    if effort is not None and not isinstance(effort, str):
        raise OwnerProtocolError("Reasoning effort must be a native setting or null")
    prior = application_owner.session_context(session_id).session
    pending_model = session_model_once_settings(config_path, session_id=session_id)
    saved = (
        (None if effort == "mode_default" else effort)
        if mode == "current" else prior.reasoning_effort
    )
    once = effort if mode == "once" else prior.reasoning_effort_once
    updated = dataclasses.replace(prior, reasoning_effort=saved, reasoning_effort_once=once)
    if mode == "current":
        select_reasoning(selected_model_info(updated, choices), updated.role.value, saved)
    state = reasoning_state(updated, choices, pending_model)
    # Clearing one stale preference must remain possible when both saved values became
    # unsupported. A valid chat change may leave an older once choice for a separate reset.
    if not state["valid"] and not (
        (mode == "once" and effort is None)
        or (mode == "current" and prior.reasoning_effort_once is not None)
    ):
        raise CommandRejected(str(state["error"]))
    result = application_owner.update_agent_session_reasoning(session_id, effort=saved, once=once)
    return {"status": "staged_once" if mode == "once" else "switched",
            "session": result, "reasoning_state": state}


def _model_slash_command(user_text: str) -> tuple[str | None, str] | None:
    """Parse the bounded `/model [reference] [--once|--global]` command."""

    parts = user_text.split()
    if not parts or parts[0].casefold() != "/model":
        return None
    if len(parts) > 3:
        raise OwnerProtocolError("Model command accepts one model and one optional mode")
    flags = [part for part in parts[1:] if part.startswith("--")]
    references = [part for part in parts[1:] if not part.startswith("--")]
    if len(flags) > 1 or len(references) > 1:
        raise OwnerProtocolError("Model command is ambiguous")
    mode = "current"
    if flags:
        if flags[0] == "--once":
            mode = "once"
        elif flags[0] == "--global":
            mode = "global"
        else:
            raise OwnerProtocolError("Model command mode is unsupported")
    return (references[0] if references else None, mode)


def _context_fitted_provider(
    provider: Any,
    *,
    gm_mode: bool = False,
) -> ContextFittingProvider:
    """Apply the one request-fit resolver used by every live Builder and GM call path."""

    model = getattr(provider, "model_info", None)
    minimum_context = (
        GM_MODE_MINIMUM_CONTEXT_TOKENS
        if gm_mode and getattr(model, "provider_mode", None) in {"local", "ollama"}
        else None
    )
    return ContextFittingProvider(provider, minimum_context_window=minimum_context)


def _preflight_guided_start(
    *,
    application_owner: AuthorityOwner,
    bus: LocalCommandBus,
    provider_resolver: ProviderResolver,
    owner_id: str,
    campaign_id: str,
    provider_id: str,
    model_id: str,
    plan_text: str,
    structured_plan: SafePayload | None,
    gm_system_prompt: str | None = None,
) -> None:
    """Prove the mandatory opening request fits before a guided handoff commits."""

    campaign = next(
        entry for entry in application_owner.campaign_index() if entry.campaign_id == campaign_id
    )
    builder_session = next(session for session in campaign.sessions if session.role == "builder")
    builder_context = application_owner.session_context(builder_session.session_id)
    if not any(
        message.kind is SessionMessageKind.EVIDENCE
        and message.content.get("kind") == "campaign_interview_state"
        for message in builder_context.messages
    ):
        return
    ready = next(
        (
            message
            for message in reversed(builder_context.messages)
            if message.kind is SessionMessageKind.USER
            and message.content.get("interview_step") == "ready"
            and message.content.get("text") == "Ready to Begin"
        ),
        None,
    )
    if ready is None:
        raise CommandRejected("Ready to Begin input is unavailable")
    preflight_session = SessionRecord(
        session_id=(
            "session-preflight-" + hashlib.sha256(campaign_id.encode("utf-8")).hexdigest()[:24]
        ),
        campaign_id=campaign_id,
        role=WriterRole.DM,
        provider_id=provider_id,
        model_id=model_id,
        status=SessionStatus.ACTIVE,
    )
    preflight_history = SessionContext(
        session=preflight_session,
        messages=(
            SessionMessage(
                session_id=preflight_session.session_id,
                sequence=1,
                message_id="preflight-ready-reference",
                kind=SessionMessageKind.EVIDENCE,
                content={
                    "kind": "ready_to_begin_reference",
                    "source_session_id": builder_session.session_id,
                    "source_message_id": ready.message_id,
                },
            ),
            SessionMessage(
                session_id=preflight_session.session_id,
                sequence=2,
                message_id="preflight-campaign-start",
                kind=SessionMessageKind.EVIDENCE,
                content={"kind": "campaign_start", "event": "campaign_start"},
            ),
        ),
    )
    preflight_provider_base = provider_resolver(preflight_session)
    if application_owner.admin_full_audit_enabled():
        preflight_provider_base = AdminAuditedProvider(
            preflight_provider_base,
            sink=application_owner.record_admin_full_audit,
            session_id=preflight_session.session_id,
            role=preflight_session.role.value,
            provider_id=provider_id,
            model_id=model_id,
            selection_mode="start_preflight",
        )
    preflight_provider = _context_fitted_provider(
        bind_session_reasoning(preflight_provider_base, preflight_session), gm_mode=True,
    )
    preflight_knowledge = (
        application_owner.campaign_knowledge_catalog_for_plan(
            campaign_id,
            structured_plan,
        )
        if isinstance(structured_plan, Mapping)
        else None
    )
    adapter = ProviderSessionAdapter(
        application_owner,
        preflight_provider,
        owner_id=owner_id,
        gm_system_prompt=gm_system_prompt,
        campaign_plan_text=plan_text,
        campaign_plan=structured_plan,
        campaign_knowledge=preflight_knowledge,
    )
    raw_catalog = bus.catalog(campaign_id)
    if not isinstance(raw_catalog, Sequence):
        raise CommandRejected("Campaign tool catalog is invalid")
    catalog = tuple(cast(Sequence[ModelMechanicsTool], raw_catalog))
    provider_input = SessionProviderInput(
        session=preflight_session,
        history=preflight_history,
        catalog=catalog,
        control=ProviderControl(),
    )
    messages = adapter._messages(provider_input)  # noqa: SLF001
    tools, _ = adapter._provider_tools(catalog)  # noqa: SLF001
    preflight_provider.admit_request(
        messages,
        tools,
        max_tokens=getattr(preflight_provider.model_info, "max_output_tokens", 4096),
    )


def _generation_session(method: str, params: Mapping[str, Any]) -> str | None:
    """Identify provider work before queueing it; /model is only a settings command."""
    if method not in {"submit_session_input", "manual_retry_session", "start_dm_session"}:
        return None
    if (
        method == "submit_session_input" and isinstance(params.get("user_text"), str)
        and params.get("ooc_text") is None and _model_slash_command(params["user_text"]) is not None
    ):
        return None
    return _required_text(params, "session_id")


def _dispatch(
    bus: LocalCommandBus,
    method: str,
    params: Mapping[str, Any],
    *,
    application_owner: AuthorityOwner,
    owner_id: str,
    provider_resolver: ProviderResolver,
    model_choices: Callable[[], Sequence[Mapping[str, object]]],
    session_loop: CampaignSessionLoop,
    storage_root: Path,
    reload_provider_resolver: Callable[[], None] | None = None,
    gm_system_prompt: str | None = None,
    provider_work_reserved: bool = False,
) -> Any:
    """Keep a reply's selection stable from provider resolution to terminal cleanup."""
    invoke = functools.partial(
        _dispatch_selected, bus, method, params, application_owner=application_owner,
        owner_id=owner_id, model_choices=model_choices, session_loop=session_loop,
        storage_root=storage_root, reload_provider_resolver=reload_provider_resolver,
        gm_system_prompt=gm_system_prompt,
    )
    session_id = _generation_session(method, params)
    if session_id is None:
        return invoke(provider_resolver=provider_resolver)
    captured: SessionRecord | None = None

    def resolve(session: SessionRecord) -> Any:
        nonlocal captured
        captured = session
        return provider_resolver(session)

    preparation = (
        nullcontext() if provider_work_reserved else session_loop.provider_request_work(session_id)
    )
    with preparation:
        try:
            return invoke(provider_resolver=resolve)
        finally:
            if captured is not None and captured.reasoning_effort_once is not None:
                application_owner.update_agent_session_reasoning(
                    session_id, effort=captured.reasoning_effort, once=None,
                )


def _dispatch_selected(
    bus: LocalCommandBus,
    method: str,
    params: Mapping[str, Any],
    *,
    application_owner: AuthorityOwner,
    owner_id: str,
    provider_resolver: ProviderResolver,
    model_choices: Callable[[], Sequence[Mapping[str, object]]],
    session_loop: CampaignSessionLoop,
    storage_root: Path,
    reload_provider_resolver: Callable[[], None] | None = None,
    gm_system_prompt: str | None = None,
) -> Any:
    if method == "handshake_status":
        return bus.handshake_status()
    if method == "campaign_index":
        return bus.campaign_index()
    if method == "campaign_creation_catalog":
        return _campaign_creation_catalog(storage_root, application_owner)
    if method == "delete_reusable_library_entry":
        kind = _required_text(params, "kind")
        artifact_id = _required_text(params, "artifact_id")
        expected_content_sha256 = _required_text(params, "expected_content_sha256")
        confirmation = _required_text(params, "confirmation")
        try:
            if kind in {"player_character", "companion"}:
                deleted = delete_reusable_participant(
                    storage_root,
                    artifact_id,
                    expected_content_sha256=expected_content_sha256,
                    confirmation=confirmation,
                )
            elif kind == "campaign_plan":
                deleted = delete_reusable_campaign_plan(
                    storage_root,
                    artifact_id,
                    expected_content_sha256=expected_content_sha256,
                    confirmation=confirmation,
                )
            else:
                raise CommandRejected(
                    "Only saved local Characters, Companions, and Campaign Plans "
                    "can be deleted here",
                    code="reusable_library_kind_unsupported",
                )
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise CommandRejected(
                str(error) or "Saved library entry could not be deleted",
                code="reusable_library_delete_rejected",
            ) from error
        return {"artifact_id": artifact_id, "deleted": deleted}
    if method == "builder_chat_index":
        return application_owner.builder_chat_index()
    if method == "start_import_repair":
        provider_id = _optional_text(params, "provider_id")
        model_id = _optional_text(params, "model_id")
        if provider_id is None or model_id is None:
            default = provider_settings(storage_root / "config.yaml").get("default_model")
            if not isinstance(default, Mapping):
                raise CommandRejected("Configure a default model before creating a chat")
            provider_id = _required_text(default, "provider_id")
            model_id = _required_text(default, "model_id")
        return start_import_repair_chat(
            application_owner, storage_root,
            repair_id=_required_text(params, "repair_id"),
            operation_id=_required_text(params, "operation_id"),
            provider_id=provider_id, model_id=model_id,
        )
    if method == "create_builder_chat":
        operation_id = _required_text(params, "operation_id")
        session_id = (
            "session-builder-" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]
        )
        provider_id = _optional_text(params, "provider_id")
        model_id = _optional_text(params, "model_id")
        if provider_id is None or model_id is None:
            default = provider_settings(storage_root / "config.yaml").get("default_model")
            if not isinstance(default, Mapping):
                raise CommandRejected("Configure a default model before creating a chat")
            provider_id = _required_text(default, "provider_id")
            model_id = _required_text(default, "model_id")
        return application_owner.create_builder_chat(
            session_id=session_id,
            provider_id=provider_id,
            model_id=model_id,
            display_name=_optional_text(params, "display_name") or "New chat",
        )
    if method == "rename_builder_chat":
        return application_owner.rename_builder_chat(
            _required_text(params, "session_id"),
            _required_text(params, "display_name"),
        )
    if method == "delete_builder_chat":
        return application_owner.delete_builder_chat(
            _required_text(params, "session_id"),
            confirmation=_required_text(params, "confirmation"),
        )
    if method in {"archive_builder_chat", "restore_builder_chat"}:
        return session_loop.set_builder_chat_archived(
            _required_text(params, "session_id"), archived=method == "archive_builder_chat"
        )
    if method == "snapshot":
        return bus.snapshot(_required_text(params, "campaign_id"))
    if method == "display_snapshot":
        return application_owner.display_snapshot(_required_text(params, "campaign_id"))
    if method == "player_projection":
        return bus.player_projection(_required_text(params, "campaign_id"))
    if method == "player_projection_page":
        return application_owner.player_projection_page(
            _required_text(params, "campaign_id"),
            after_sequence=_optional_nonnegative_int(params, "after_sequence"),
        )
    if method == "campaign_panels":
        return application_owner.campaign_panels(
            _required_text(params, "campaign_id"),
            after_revision=_optional_nonnegative_int(params, "after_revision"),
        )
    if method == "action_sequence":
        return application_owner.action_sequence(
            session_id=_required_text(params, "session_id"),
            sequence_id=_optional_text(params, "sequence_id"),
        )
    if method in {"compose_action_sequence", "execute_action_sequence"}:
        arguments = {
            "campaign_id": _required_text(params, "campaign_id"),
            "session_id": _required_text(params, "session_id"),
            "sequence_id": _required_text(params, "sequence_id"),
            "expected_version": _required_nonnegative_int(params, "expected_version"),
        }
        if method == "compose_action_sequence":
            return application_owner.compose_action_sequence(
                **arguments,
                expected_revision=_required_nonnegative_int(params, "expected_revision"),
                steps=params.get("steps"),
            )
        return application_owner.execute_action_sequence(
            **arguments, confirmation=_optional_text(params, "confirmation"),
        )
    if method == "player_action_availability":
        return application_owner.player_action_availability(
            campaign_id=_required_text(params, "campaign_id"),
            expected_revision=_required_nonnegative_int(params, "expected_revision"),
            requests=params.get("requests"),
        )
    if method in {"preview_player_action", "execute_player_action"}:
        source = params.get("source")
        selection = params.get("selection")
        if (not isinstance(source, dict)
                or (selection is not None and not isinstance(selection, dict))):
            raise OwnerProtocolError("Player control source and selection must be objects")
        return application_owner.player_action(
            campaign_id=_required_text(params, "campaign_id"),
            session_id=_required_text(params, "session_id"),
            operation_id=_required_text(params, "operation_id"),
            expected_revision=_required_nonnegative_int(params, "expected_revision"),
            source=source, selection=selection,
            confirmation=_optional_text(params, "confirmation"),
            preview_only=method == "preview_player_action",
        )
    if method in {"preview_panel_action", "execute_panel_action", "panel_action_receipt"}:
        action_arguments = {
            "campaign_id": _required_text(params, "campaign_id"),
            "surface_id": _required_text(params, "surface_id"),
            "panel_action_id": _required_text(params, "panel_action_id"),
            "operation_id": _required_text(params, "operation_id"),
            "expected_revision": _required_nonnegative_int(params, "expected_revision"),
            "selection": _optional_payload(params, "selection"),
            "session_id": _optional_text(params, "session_id"),
        }
        if method == "panel_action_receipt":
            return application_owner.panel_action_receipt(**action_arguments)
        if method == "preview_panel_action":
            return application_owner.preview_panel_action(**action_arguments)
        return application_owner.execute_panel_action(
            **action_arguments, confirmation=_optional_text(params, "confirmation"),
        )
    if method in {"campaign_sequel_context", "preview_campaign_sequel", "append_campaign_sequel"}:
        arguments = {
            "campaign_id": _required_text(params, "campaign_id"),
            "session_id": _required_text(params, "session_id"),
        }
        if method == "campaign_sequel_context":
            return application_owner.campaign_sequel_context(**arguments)
        arguments["source_id"] = _required_text(params, "source_id")
        if method == "preview_campaign_sequel":
            return application_owner.preview_campaign_sequel(**arguments)
        return application_owner.append_campaign_sequel(
            **arguments, operation_id=_required_text(params, "operation_id"),
            expected_revision=_required_nonnegative_int(params, "expected_revision"),
            preview_sha256=_required_text(params, "preview_sha256"), review=params.get("review"),
        )
    if method == "package_candidates":
        return bus.package_candidates()
    if method == "inspect_package_candidate":
        return bus.inspect_package_candidate(_required_text(params, "candidate_id"))
    if method == "package_candidate_text":
        return bus.package_candidate_text(
            _required_text(params, "candidate_id"),
            _required_text(params, "package_id"),
            _required_text(params, "path"),
        )
    if method == "compare_package_candidates":
        return bus.compare_package_candidates(
            _required_text(params, "left_candidate_id"),
            _required_text(params, "right_candidate_id"),
        )
    if method == "rift_template":
        try:
            participants = _rift_reusable_participants(storage_root)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
            raise CommandRejected(
                "Reusable participant library contains an invalid artifact",
                code="reusable_participant_invalid",
            ) from error
        return rift_public_template(reusable_participants=participants)
    if method == "campaign_party_options":
        ruleset_selection = _required_text(params, "ruleset_selection")
        setting_id = _required_text(params, "setting_id")
        plan_id = _required_text(params, "plan_id")
        catalog_plan = next((
            item for item in _campaign_creation_catalog(storage_root, application_owner)
            if item.get("kind") == "campaign_plan" and item.get("id") == plan_id
        ), None)
        if catalog_plan is not None and catalog_plan.get("setup_id") == "the-rift-awakening":
            candidate, sources, _plan, template = _resolve_rift_library_selection(
                application_owner, storage_root, ruleset_selection=ruleset_selection,
                setting_id=setting_id, plan_id=plan_id,
            )
            primary = next(
                entry for entry in candidate.package_lock.entries
                if entry.role is PackageRole.PRIMARY
            )
            declaration = next(
                item for item in application_owner.exact_participant_declarations(
                    primary.package_id, primary.version, primary.sha256,
                ) if item.kind == "player_character"
            )
            return rift_public_template(
                reusable_participants=compatible_reusable_participants(
                    storage_root, package=primary, declaration=declaration,
                    campaign_setting=sources.setting,
                    legacy_campaign_setting=bundled_rift_authority().setting,
                    setting_character_schema=sources.setting_character_schema,
                ),
                template=template,
            )
        return resolve_campaign_library_selection(
            application_owner, storage_root, candidate_id=ruleset_selection,
            setting_id=setting_id, plan_id=plan_id,
        ).party_options()
    if method == "model_choices":
        return tuple(model_choices())
    if method == "provider_settings":
        return provider_settings(storage_root / "config.yaml")
    if method == "open_provider_setup":
        return open_provider_setup(params.get("preset_id"))
    if method == "chatgpt_connection":
        from sylvia_gm.config.chatgpt_settings import chatgpt_settings_action
        from sylvia_gm.provider._codex_rpc import CodexConnectionError

        if params.get("action") == "add_model" and reload_provider_resolver is None:
            raise OwnerProtocolError("Provider settings reload is unavailable")
        try:
            result = chatgpt_settings_action(
                storage_root, params.get("action"), params.get("model"),
            )
        except CodexConnectionError as error:
            raise OwnerProtocolError(str(error)) from None
        if params.get("action") == "add_model" and reload_provider_resolver is not None:
            reload_provider_resolver()
        return result
    if method == "admin_full_audit_status":
        return application_owner.admin_full_audit_status()
    if method == "admin_full_audit_record":
        index = _required_nonnegative_int(params, "index")
        record = application_owner.admin_full_audit_record(index)
        encoded = json.dumps(
            _json_value(record),
            ensure_ascii=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(encoded) > _MAX_ADMIN_AUDIT_VIEW_BYTES:
            return {
                "index": index,
                "trace": None,
                "unavailable_reason": "record_too_large_for_live_view",
                "bytes": len(encoded),
            }
        return record
    if method == "export_admin_full_audit":
        selected = Path(_required_text(params, "destination_path"))
        if not selected.is_absolute() or not selected.name:
            raise OwnerProtocolError("Audit export destination must be an absolute file path")
        try:
            parent = selected.parent.resolve(strict=True)
            destination = parent / selected.name
            regular_file = not destination.exists() or destination.is_file()
        except OSError as error:
            raise CommandRejected(audit_export_save_error(error)) from error
        if not regular_file:
            raise OwnerProtocolError(
                "Audit export destination is not a regular file. "
                "Choose a file in a writable folder."
            )
        return application_owner.export_admin_full_audit(destination)
    if method == "set_admin_full_audit":
        return application_owner.set_admin_full_audit(_optional_bool(params, "enabled"))
    if method == "detect_local_providers":
        return detect_local_providers(storage_root / "config.yaml")
    if method == "discover_provider_models":
        return discover_provider_models(
            storage_root / "config.yaml",
            provider_id=params.get("provider_id"),
            preset_id=params.get("preset_id"),
            base_url=params.get("base_url"),
            credential=params.get("credential"),
            headers=params.get("headers"),
            query=params.get("query"),
            offset=params.get("offset", 0),
            limit=params.get("limit", 100),
        )
    if method == "validate_provider_model":
        return validate_provider_model(
            storage_root / "config.yaml",
            provider_id=params.get("provider_id"),
            preset_id=params.get("preset_id"),
            base_url=params.get("base_url"),
            credential=params.get("credential"),
            model_id=params.get("model_id"),
            transport=params.get("transport"),
            headers=params.get("headers"),
        )
    if method == "save_provider_settings":
        if reload_provider_resolver is None:
            raise OwnerProtocolError("Provider settings reload is unavailable")
        saved_provider = save_provider_settings(
            storage_root / "config.yaml",
            provider_id=params.get("provider_id"),
            preset_id=params.get("preset_id"),
            display_name=params.get("display_name"),
            base_url=params.get("base_url"),
            credential=params.get("credential"),
            model_id=params.get("model_id"),
            model_name=params.get("model_name"),
            context_window=params.get("context_window"),
            max_output_tokens=params.get("max_output_tokens"),
            declared_max_output_tokens=params.get("declared_max_output_tokens"),
            builder_output_tokens=params.get("builder_output_tokens"),
            declared_context_window=params.get("declared_context_window"),
            declared_context_source=params.get("declared_context_source"),
            runtime_context_window=params.get("runtime_context_window"),
            runtime_context_source=params.get("runtime_context_source"),
            configured_context_window=params.get("configured_context_window"),
            context_observed_at=params.get("context_observed_at"),
            declared_context_observed_at=params.get("declared_context_observed_at"),
            runtime_context_observed_at=params.get("runtime_context_observed_at"),
            configured_context_observed_at=params.get("configured_context_observed_at"),
            transport=params.get("transport"),
            headers=params.get("headers"),
            supports_streaming=params.get("supports_streaming"),
            supports_tools=params.get("supports_tools"),
        )
        reload_provider_resolver()
        return saved_provider
    if method == "remove_provider_settings":
        if reload_provider_resolver is None:
            raise OwnerProtocolError("Provider settings reload is unavailable")
        remove_provider_settings(storage_root / "config.yaml", params.get("provider_id"))
        reload_provider_resolver()
        return {"status": "removed"}
    if method == "set_default_model":
        return set_default_model_settings(
            storage_root / "config.yaml",
            provider_id=params.get("provider_id"),
            model_id=params.get("model_id"),
        )
    if method == "switch_session_model":
        session_id = _required_text(params, "session_id")
        return _switch_session_model(
            application_owner=application_owner,
            session_loop=session_loop,
            config_path=storage_root / "config.yaml",
            session_id=session_id,
            provider_id=params.get("provider_id"),
            model_id=params.get("model_id"),
            mode=_optional_text(params, "mode") or "current",
            choices=model_choices(),
        )
    if method == "switch_session_reasoning":
        if "effort" not in params:
            raise OwnerProtocolError("Reasoning selection requires effort")
        return _switch_session_reasoning(
            application_owner=application_owner, session_loop=session_loop,
            config_path=storage_root / "config.yaml",
            session_id=_required_text(params, "session_id"),
            effort=params["effort"], mode=_optional_text(params, "mode") or "current",
            choices=model_choices(),
        )
    if method == "inspect_provider_thinking":
        return {"text": application_owner.inspect_provider_thinking(
            _required_text(params, "session_id"), _required_text(params, "attempt_id"),
            _required_text(params, "response_id"),
        )}
    if method in {"session_context", "session_history_page"}:
        session_id = _required_text(params, "session_id")
        activity = session_loop.builder_activity(session_id)
        # Read chronology after its activity basis so a completed result includes its final prose.
        page_fields = {}
        if method == "session_history_page":
            history_page = application_owner.session_history_page(
                session_id, before_sequence=params.get("before_sequence"),
                limit=params.get("limit", 50),
            )
            page_fields = {
                "next_before_sequence": history_page.next_before_sequence,
                "latest_sequence": history_page.latest_sequence,
                "latest_user_sequence": history_page.latest_user_sequence,
                "launch_messages": history_page.launch_messages,
                "latest_canonical_sequence": history_page.latest_canonical_sequence,
                "act_markers": history_page.act_markers,
                "campaign_ending": history_page.campaign_ending,
                "campaign_endings": history_page.campaign_endings,
            }
            display_session = history_page.session
            display_messages = history_page.messages
        else:
            context = application_owner.session_context(session_id)
            display_session = context.session
            display_messages = context.messages
        settings = provider_settings(storage_root / "config.yaml")
        pending_model = session_model_once_settings(
            storage_root / "config.yaml", session_id=session_id,
        )
        return {
            "session": display_session,
            "messages": display_messages,
            "thinking": application_owner.private_provider_index(session_id),
            **page_fields,
            "provisional": session_loop.provisional_session_projection(session_id),
            "activity": activity,
            "context_status_hint": session_loop.context_status_hint(session_id),
            "reasoning_state": reasoning_state(display_session, model_choices(), pending_model),
            "model_state": {
                "current": {
                    "provider_id": display_session.provider_id,
                    "model_id": display_session.model_id,
                },
                "once": pending_model,
                "default_model": settings.get("default_model"),
            },
        }
    if method == "ingest_builder_sources":
        session_id = _required_text(params, "session_id")
        ingestion_id = _required_artifact_id(params, "ingestion_id")
        raw_paths = params.get("source_paths")
        if (
            not isinstance(raw_paths, list)
            or not 1 <= len(raw_paths) <= MAX_SOURCE_FILES
            or any(not isinstance(item, str) or not item for item in raw_paths)
        ):
            raise OwnerProtocolError("source_paths must contain one to eight selected paths")
        with application_owner.builder_session_work(session_id) as builder_context:
            workspace = _builder_workspace(storage_root, builder_context.session)
            batch = ingest_builder_sources(
                workspace,
                tuple(Path(item) for item in raw_paths),
                batch_id=ingestion_id,
            )
            safe_sources = [source.safe_projection() for source in batch.sources]
            try:
                message = application_owner.append_agent_message(
                    session_id=session_id,
                    message_id=(
                        "builder-sources-"
                        + hashlib.sha256(ingestion_id.encode("utf-8")).hexdigest()[:24]
                    ),
                    kind=SessionMessageKind.EVIDENCE,
                    content=cast(
                        SafePayload,
                        {
                            "tool_name": "attached_local_sources",
                            "result": {
                                "status": "ingested",
                                "source_count": len(safe_sources),
                                "sources": safe_sources,
                                "authoritative": False,
                            },
                        },
                    ),
                )
            except Exception:
                rollback_builder_source_batch(batch)
                raise
        return {
            "status": "ingested",
            "source_count": len(safe_sources),
            "sources": safe_sources,
            "message_sequence": message.sequence,
        }
    if method == "steer_session":
        steering_text = _required_text(params, "text")
        if len(steering_text) > _MAX_USER_TEXT_CHARS:
            raise OwnerProtocolError("text exceeds the session steering limit")
        session_loop.steer(
            _required_text(params, "session_id"),
            steering_text,
            message_id=_required_text(params, "message_id"),
        )
        return {"status": "accepted"}
    if method == "submit_session_input":
        ending_choice = params.get("campaign_ending_choice")
        if ending_choice is not None and (
            not isinstance(ending_choice, Mapping)
            or set(ending_choice) != {"ending_id", "choice"}
            or not isinstance(ending_choice.get("ending_id"), str)
            or ending_choice.get("choice") not in {"continue", "finish"}
        ):
            raise OwnerProtocolError("Campaign ending choice is invalid")
        builder_start = _optional_bool(params, "builder_start")
        player_action_id = _optional_text(params, "player_action_id")
        automatic_response = _optional_bool(params, "automatic_response")
        if automatic_response and player_action_id is None:
            raise OwnerProtocolError("Automatic response requires its saved player action")
        if builder_start and player_action_id is not None:
            raise OwnerProtocolError("Builder start cannot respond to a gameplay action")
        raw_user_text = params.get("user_text", "")
        if not isinstance(raw_user_text, str):
            raise OwnerProtocolError("user_text must be text")
        user_text = raw_user_text
        raw_ooc_text = params.get("ooc_text")
        if raw_ooc_text is not None and not isinstance(raw_ooc_text, str):
            raise OwnerProtocolError("ooc_text must be text")
        ooc_text = raw_ooc_text if isinstance(raw_ooc_text, str) and raw_ooc_text.strip() else None
        if (not user_text.strip() and ooc_text is None
                and not builder_start and not player_action_id and ending_choice is None):
            raise OwnerProtocolError("user_text or ooc_text must be nonempty")
        if player_action_id is not None and (user_text or ooc_text is not None):
            raise OwnerProtocolError("Action responses do not contain player speech")
        if builder_start and (user_text or ooc_text is not None):
            raise OwnerProtocolError("Builder start does not accept invented player input")
        if len(user_text) > _MAX_USER_TEXT_CHARS:
            raise OwnerProtocolError("user_text exceeds the session input limit")
        if ooc_text is not None and len(ooc_text) > _MAX_USER_TEXT_CHARS:
            raise OwnerProtocolError("ooc_text exceeds the session input limit")
        session_id = _required_text(params, "session_id")
        model_command = _model_slash_command(user_text) if ooc_text is None else None
        if model_command is not None:
            reference, mode = model_command
            if reference is None:
                session_loop.assert_idle(session_id)
                return {"status": "model_picker_requested", "mode": mode}
            selection = _selection_from_reference(model_choices(), reference)
            return _switch_session_model(
                application_owner=application_owner,
                session_loop=session_loop,
                config_path=storage_root / "config.yaml",
                session_id=session_id,
                provider_id=selection["provider_id"],
                model_id=selection["model_id"],
                mode=mode,
                choices=model_choices(),
            )
        context = application_owner.session_context(session_id)
        if ending_choice is not None and (
            context.session.role is not WriterRole.DM or builder_start or player_action_id
            or user_text or ooc_text or params.get("redirect_interruption_id")
        ):
            raise OwnerProtocolError("Ending choices require an idle GM ending without other input")
        if builder_start and context.session.role is not WriterRole.BUILDER:
            raise OwnerProtocolError("builder_start is available only to Builder sessions")
        if context.session.role is WriterRole.BUILDER and ooc_text is not None:
            raise OwnerProtocolError("ooc_text is available only to DM sessions")
        turn_id = (
            _required_text(params, "turn_id")
            if context.session.campaign_id is not None
            else (_optional_text(params, "turn_id") or f"builder-chat-{session_id}")
        )
        if player_action_id is not None:
            basis = application_owner.player_action_response_basis(
                session_id, player_action_id, automatic=automatic_response,
            )
            if basis["turn_id"] != turn_id:
                raise OwnerProtocolError("The saved player action belongs to another turn")
        pending_once = session_model_once_settings(
            storage_root / "config.yaml", session_id=session_id
        )

        def consume_pending_once() -> None:
            if pending_once is None:
                return
            clear_session_model_once_settings(
                storage_root / "config.yaml",
                session_id=session_id,
                expected_provider_id=pending_once["provider_id"],
                expected_model_id=pending_once["model_id"],
            )

        effective_session = (
            context.session
            if pending_once is None
            else dataclasses.replace(
                context.session,
                provider_id=pending_once["provider_id"],
                model_id=pending_once["model_id"],
            )
        )
        try:
            provider = provider_resolver(effective_session)
        except BaseException:
            consume_pending_once()
            raise
        if application_owner.admin_full_audit_enabled():
            provider = AdminAuditedProvider(
                provider,
                sink=application_owner.record_admin_full_audit,
                session_id=session_id,
                role=effective_session.role.value,
                provider_id=effective_session.provider_id,
                model_id=effective_session.model_id,
                selection_mode="once" if pending_once is not None else "session",
            )
        provider = _context_fitted_provider(
            bind_session_reasoning(provider, effective_session),
            gm_mode=effective_session.role is WriterRole.DM,
        )
        if user_text.strip() == "/compact" and ooc_text is None:
            from sylvia_gm.agent._builder_session import _provider_history
            from sylvia_gm.agent._working_context import compact_saved_messages

            if context.session.role is WriterRole.BUILDER and context.session.campaign_id:
                basis = application_owner.campaign_construction_basis(context.session.campaign_id)
                if basis.builder_session_id == session_id and basis.completion_revision is None:
                    raise OwnerProtocolError(
                        "The campaign Interview keeps its exact transcript. "
                        "Compaction is unavailable."
                    )

            def compact_provider(provider_input):
                if context.session.role is WriterRole.BUILDER:
                    messages = _provider_history(provider_input.history)
                else:
                    adapter = ProviderSessionAdapter(application_owner, provider, owner_id=owner_id)
                    messages = adapter._messages(
                        provider_input, campaign_has_mechanics=False, tools_available=False,
                    )
                yield from compact_saved_messages(
                    provider, provider_input.history, messages, provider_input.control,
                )

            try:
                session_loop.run_compaction(
                    session_id, _required_text(params, "attempt_id"), compact_provider,
                )
                return {"session_id": session_id, "status": "context_compacted",
                        "messages": (), "interruption": None}
            finally:
                consume_pending_once()
        if context.session.role is WriterRole.BUILDER:
            from sylvia_gm.agent._builder_title import TITLE_LIMIT, opening_title

            fallback_title = None
            request_title = False
            if context.session.campaign_id is None:
                current_chat = next(
                    (
                        chat
                        for chat in application_owner.builder_chat_index()
                        if chat.session.session_id == session_id
                    ),
                    None,
                )
                if current_chat is not None and current_chat.display_name == "New chat":
                    title = opening_title(user_text)
                    if title:
                        application_owner.rename_builder_chat(session_id, title, automatic=True)
                        fallback_title = title
                        request_title = len(" ".join(user_text.split())) > TITLE_LIMIT and not any(
                            message.kind in {SessionMessageKind.USER, SessionMessageKind.TALK}
                            or message.content.get("kind") == "builder_title_preference"
                            for message in context.messages
                        )
            allow_public_web = _optional_bool(params, "allow_public_web")
            request = SessionRunRequest(
                session_id=session_id,
                turn_id=turn_id,
                attempt_id=_required_text(params, "attempt_id"),
                message_id=_required_text(params, "message_id"),
                user_text=user_text,
                input_kind=(
                    SessionInputKind.BUILDER_START if builder_start else SessionInputKind.USER
                ),
            )
            campaign_plan_authority = None
            if context.session.campaign_id is not None and context.session.selected_skill in {
                None,
                "BUILD CAMPAIGN PLAN",
            }:
                campaign_plan_authority = _campaign_plan_tool_authority(
                    application_owner,
                    context.session.campaign_id,
                )
            participant_purpose = next(
                (
                    str(message.content.get("purpose"))
                    for message in context.messages
                    if message.kind is SessionMessageKind.EVIDENCE
                    and message.content.get("kind") in {
                        "creation_subflow_link", "builder_launch_context"
                    }
                    and message.content.get("purpose") in {"player_character", "companion"}
                ),
                None,
            )
            participant_ruleset_selection = None
            participant_setting_selection = None
            if participant_purpose is not None:
                link = next(
                    message.content
                    for message in context.messages
                    if message.kind is SessionMessageKind.EVIDENCE
                    and message.content.get("kind") in {
                        "creation_subflow_link", "builder_launch_context"
                    }
                    and (context.session.campaign_id is not None
                         or message.content.get("kind") == "builder_launch_context")
                )
                selections = link.get("selections")
                if isinstance(selections, Mapping):
                    ruleset = selections.get("ruleset", {})
                    setting = selections.get("setting", {})
                    ruleset_value = ruleset.get("value", {}) if isinstance(ruleset, Mapping) else {}
                    setting_value = setting.get("value", {}) if isinstance(setting, Mapping) else {}
                    if isinstance(ruleset_value, Mapping):
                        participant_ruleset_selection = ruleset_value.get("candidate_id")
                    if isinstance(setting_value, Mapping):
                        participant_setting_selection = setting_value.get("setting_id")
                parent_session_id = link.get("parent_session_id")
                if isinstance(parent_session_id, str):
                    parent_context = application_owner.session_context(parent_session_id)
                    parent_states = tuple(
                        message
                        for message in parent_context.messages
                        if message.kind is SessionMessageKind.EVIDENCE
                        and message.content.get("kind") == "campaign_interview_state"
                    )
                    parent_state = parent_states[-1].content if parent_states else {}
                    answers = parent_state.get("answers")
                    ruleset_message_id = (
                        answers.get("ruleset") if isinstance(answers, Mapping) else None
                    )
                    ruleset_message = next(
                        (
                            message
                            for message in parent_context.messages
                            if message.message_id == ruleset_message_id
                        ),
                        None,
                    )
                    ruleset_value = (
                        None
                        if ruleset_message is None
                        else ruleset_message.content.get("interview_value")
                    )
                    if isinstance(ruleset_value, Mapping):
                        participant_ruleset_selection = ruleset_value.get("candidate_id")
                    setting_message_id = (
                        answers.get("setting") if isinstance(answers, Mapping) else None
                    )
                    setting_message = next(
                        (
                            message
                            for message in parent_context.messages
                            if message.message_id == setting_message_id
                        ),
                        None,
                    )
                    setting_value = (
                        None
                        if setting_message is None
                        else setting_message.content.get("interview_value")
                    )
                    if isinstance(setting_value, Mapping):
                        participant_setting_selection = setting_value.get("setting_id")
            rift_authority = bundled_rift_authority()
            participant_authority = (
                None
                if participant_purpose is None
                or participant_ruleset_selection != RIFT_RULESET_ID
                or participant_setting_selection != rift_authority.setting.package_id
                else _rift_participant_authority()
            )
            participant_campaign_setting = (
                rift_authority.setting if participant_authority is not None else None
            )
            participant_package = (
                None if participant_authority is None else participant_authority[0]
            )
            participant_declarations = (
                () if participant_authority is None else (participant_authority[1],)
            )
            participant_setting_schema = None
            saved_references = launch_references(context)
            from sylvia_gm.agent._builder_source_tool import (
                builder_setting_source_tool,
                builder_source_tool,
            )
            from sylvia_gm.application.builder_source_selection import BuilderSourceSelection

            source_selection = BuilderSourceSelection(
                application_owner, storage_root, context,
                lambda: _campaign_creation_catalog(storage_root, application_owner),
            )
            library_sources = None
            if saved_references is not None:
                # Independent chats always retain their original package and Setting choices.
                participant_package = None
                participant_declarations = ()
                participant_campaign_setting = None
                candidate = launch_candidate(application_owner, context)
                setting_reference = next(
                    (item for item in saved_references.get("packages", [])
                     if item.get("prefix") == "setting"),
                    None,
                )
                if candidate is not None and context.session.campaign_id is None:
                    primary = next(
                        entry for entry in candidate.package_lock.entries
                        if entry.role is PackageRole.PRIMARY
                    )
                    library_sources = LibrarySourceReader(
                        application_owner, primary.package_id,
                        str(setting_reference["id"]) if setting_reference is not None else None,
                    )
                if candidate is not None and setting_reference is not None:
                    if context.session.selected_skill in {None, "BUILD CAMPAIGN PLAN"}:
                        campaign_plan_authority = campaign_plan_sources(
                            application_owner, candidate.package_lock,
                            setting_id=str(setting_reference["id"]),
                        )
                    if participant_purpose is not None:
                        participant_package = next(
                            (entry for entry in candidate.package_lock.entries
                             if entry.role is PackageRole.PRIMARY),
                            None,
                        )
                        participant_declarations = (
                            application_owner.exact_participant_declarations(
                                participant_package.package_id,
                                participant_package.version,
                                participant_package.sha256,
                            ) if participant_package is not None else ()
                        )
                        participant_campaign_setting = PackageBinding(
                            str(setting_reference["id"]), str(setting_reference["version"]),
                            str(setting_reference["content_sha256"]),
                        )
                        participant_sources = campaign_plan_sources(
                            application_owner, candidate.package_lock,
                            setting_id=participant_campaign_setting.package_id,
                        )
                        participant_setting_schema = (
                            participant_sources.setting_character_schema
                            if participant_sources is not None else None
                        )
            builder_result: BuilderTurnResult | None = None
            repair_character: tuple[str, str] | None = None
            for message in context.messages:
                receipt = message.content.get("result")
                if (
                    message.kind is SessionMessageKind.EVIDENCE
                    and message.content.get("kind") == "builder_tool_result"
                    and message.content.get("tool_name") == "open_character_draft"
                    and isinstance(receipt, Mapping)
                    and receipt.get("success") is True
                    and isinstance(receipt.get("source_artifact_id"), str)
                    and isinstance(receipt.get("source_content_sha256"), str)
                ):
                    repair_character = (
                        str(receipt["source_artifact_id"]),
                        str(receipt["source_content_sha256"]),
                    )

            def resolve_source_profile(requested: SafePayload | None) -> SafePayload | None:
                """Bind provenance to retained or verified content, never model text."""
                if requested is not None and (
                    not isinstance(requested, Mapping)
                    or set(requested) != {"artifact_id", "content_sha256"}
                    or any(not isinstance(value, str) or not value for value in requested.values())
                ):
                    raise CommandRejected(
                        "Supply the source profile's exact identity and hash",
                        code="character_profile_invalid",
                    )
                pinned = (saved_references or {}).get("character_profile")
                provenance = None
                if pinned is not None:
                    provenance = profile_from_snapshot(pinned).provenance()
                elif repair_character is not None:
                    original = next(
                        (
                            item
                            for item in _reusable_participant_catalog(
                                storage_root,
                                application_owner,
                            )
                            if item.artifact_id == repair_character[0]
                            and item.content_sha256 == repair_character[1]
                        ),
                        None,
                    )
                    if original is None:
                        raise CommandRejected(
                            "Reopen the saved character before repairing it", code="stale_character"
                        )
                    provenance = original.source_profile
                if requested is None:
                    return provenance
                if provenance is not None and all(
                    requested[key] == provenance[key] for key in requested
                ):
                    return provenance
                if pinned is not None:
                    raise CommandRejected(
                        "The profile differs from this chat's original source",
                        code="stale_character_profile",
                    )
                return load_character_profile(
                    storage_root,
                    str(requested["artifact_id"]),
                    str(requested["content_sha256"]),
                ).provenance()

            def publish_profile(data: bytes) -> SafePayload:
                """Save unbound content without selecting or mutating a campaign."""
                from sylvia_gm.application.character_profiles import validate_character_profile

                canonical = canonical_json_bytes(validate_character_profile(data))
                saved = save_character_profile(
                    storage_root,
                    operation_id=f"{session_id}:{hashlib.sha256(canonical).hexdigest()}",
                    source=data,
                )
                return saved.catalog_payload()

            def import_profile(data: bytes) -> SafePayload:
                from sylvia_gm.application.character_card_import import save_character_card_import
                return save_character_card_import(storage_root, operation_id=f"{session_id}:card:{hashlib.sha256(data).hexdigest()}", source=data)

            from sylvia_gm.agent._character_profile_tools import character_profile_tools

            profile_tools = (
                character_profile_tools(
                    _builder_workspace(storage_root, context.session),
                    save=publish_profile,
                    import_source=import_profile,
                    read=lambda identity, digest: load_character_profile(
                        storage_root, identity, digest
                    ),
                )
                if context.session.campaign_id is None
                else ()
            )

            def publish_ruleset_candidate(
                archive: BinaryIO,
                *,
                install_id: str,
                expected_closure_sha256: str,
            ) -> SafePayload:
                return _publish_builder_ruleset_candidate(
                    application_owner,
                    session_id,
                    archive,
                    install_id=install_id,
                    expected_closure_sha256=expected_closure_sha256,
                )

            def publish_participant(
                *,
                role: str,
                summary: str,
                authored_profile: str,
                participant: SafePayload,
                entity_state: SafePayload,
                schema_id: str | None = None,
                setting_data: SafePayload | None = None,
                source_profile: SafePayload | None = None,
            ) -> SafePayload:
                if (
                    participant_package is None
                    or participant_campaign_setting is None
                    or role != participant_purpose
                ):
                    raise CommandRejected("Participant publication is unavailable")
                declarations = tuple(
                    declaration for declaration in participant_declarations
                    if schema_id is None or declaration.schema_id == schema_id
                )
                if len(declarations) != 1:
                    raise CommandRejected("Choose one of this Ruleset's participant schema IDs")
                declaration = declarations[0]
                source_profile = resolve_source_profile(source_profile)
                content_basis = canonical_json_bytes(
                    {
                        "session_id": session_id,
                        "role": role,
                        "summary": summary,
                        "authored_profile": authored_profile,
                        "participant": participant,
                        "entity_state": entity_state,
                        "setting_data": setting_data,
                        "source_profile": source_profile,
                    }
                )
                operation_suffix = hashlib.sha256(content_basis).hexdigest()[:24]
                saved = save_reusable_participant(
                    storage_root,
                    operation_id=f"builder-participant-{operation_suffix}",
                    role=role,
                    summary=summary,
                    authored_profile=authored_profile,
                    participant=participant,
                    entity_state=entity_state,
                    package=participant_package,
                    declaration=declaration,
                    campaign_setting=participant_campaign_setting,
                    setting_data=setting_data,
                    setting_character_schema=participant_setting_schema,
                    source_profile=source_profile,
                )
                if context.session.campaign_id is None:
                    label = "Character" if role == "player_character" else "Companion"
                    application_owner.rename_builder_chat(
                        session_id, f"{saved.name} · {label}"[:120].strip(), automatic=True
                    )
                return participant_catalog_payload(application_owner, saved)

            def read_participant(
                *, artifact_id: str, expected_content_sha256: str, schema_id: str | None = None,
            ) -> SafePayload:
                """Read an exact library source for explicit adaptation, without publishing it."""
                nonlocal repair_character
                if participant_package is None or participant_campaign_setting is None:
                    raise CommandRejected("Character editing needs this chat's saved Setting")
                source = next((
                    item for item in _reusable_participant_catalog(storage_root, application_owner)
                    if item.artifact_id == artifact_id and item.role == participant_purpose
                ), None)
                if source is None:
                    raise CommandRejected("The source character is unavailable for this role")
                if source.content_sha256 != expected_content_sha256:
                    raise CommandRejected(
                        "The source character changed; refresh the catalog", code="stale_character"
                    )
                declarations = tuple(
                    item for item in participant_declarations
                    if schema_id is None or item.schema_id == schema_id
                )
                if len(declarations) != 1:
                    raise CommandRejected("Choose one of this Ruleset's participant schema IDs")
                repair_character = (source.artifact_id, source.content_sha256)
                return {
                    "schema_id": declarations[0].schema_id,
                    "summary": source.summary, "authored_profile": source.authored_profile,
                    "participant": dict(source.participant),
                    "entity_state": dict(authored_character_state(
                        source.entity_state, declarations[0]
                    )),
                    "setting_data": dict(source.setting_data),
                }

            def reuse_participant(
                *, artifact_id: str, expected_content_sha256: str, schema_id: str | None = None,
            ) -> SafePayload:
                if participant_package is None or participant_campaign_setting is None:
                    raise CommandRejected("Character reuse needs this chat's saved Setting")
                source = next((
                    item for item in _reusable_participant_catalog(storage_root, application_owner)
                    if item.artifact_id == artifact_id and item.role == participant_purpose
                ), None)
                if source is None:
                    raise CommandRejected("The source character is unavailable for this role")
                declarations = tuple(
                    item for item in participant_declarations
                    if schema_id is None or item.schema_id == schema_id
                )
                if len(declarations) != 1:
                    raise CommandRejected("Choose one of this Ruleset's participant schema IDs")
                basis = canonical_json_bytes({
                    "session_id": session_id, "source": artifact_id,
                    "content_sha256": expected_content_sha256,
                    "setting": participant_campaign_setting.as_payload(),
                    "schema_id": declarations[0].schema_id,
                })
                saved = copy_compatible_character(
                    storage_root, source=source, expected_content_sha256=expected_content_sha256,
                    operation_id="builder-character-copy-" + hashlib.sha256(basis).hexdigest()[:32],
                    package=participant_package, declaration=declarations[0],
                    campaign_setting=participant_campaign_setting,
                    setting_character_schema=participant_setting_schema,
                )
                if context.session.campaign_id is None:
                    label = "Character" if saved.role == "player_character" else "Companion"
                    application_owner.rename_builder_chat(
                        session_id, f"{saved.name} · {label}"[:120].strip(), automatic=True
                    )
                return {
                    **dict(participant_catalog_payload(application_owner, saved)),
                    "source_artifact_id": source.artifact_id,
                }

            completing_required_characters = any(
                message.kind is SessionMessageKind.EVIDENCE
                and message.content.get("kind") == "builder_launch_context"
                and message.content.get("purpose") == "plan"
                and message.content.get("interview_step") == "participants"
                for message in context.messages
            )

            def selected_plan_authority() -> CampaignPlanToolAuthority | None:
                """Use an explicit plain-chat Setting immediately, retaining contextual bindings."""
                if not source_selection.selectable:
                    return campaign_plan_authority
                references = source_selection.references
                setting = next((item for item in (references or {}).get("packages", ())
                                if item.get("prefix") == "setting"), None)
                source = source_selection.candidate()
                if setting is None or source is None:
                    return None
                return campaign_plan_sources(
                    application_owner, source.package_lock, setting_id=str(setting["id"]),
                )

            def check_plan() -> SafePayload:
                source = source_selection.candidate()
                selected_authority = selected_plan_authority()
                if source is None or selected_authority is None:
                    raise CommandRejected("Plan checking needs this chat's saved Setting")
                return check_plan_draft(
                    application_owner, _builder_workspace(storage_root, context.session),
                    candidate_id=source.candidate_id, expected_authority=selected_authority,
                    require_complete_characters=completing_required_characters,
                )

            def check_setting(*, package_id: str, version: str, title: str) -> SafePayload:
                source = source_selection.candidate()
                if source is None or context.session.campaign_id is not None:
                    raise CommandRejected("Setting checking needs this chat's saved Ruleset")
                return check_setting_draft(
                    application_owner, _builder_workspace(storage_root, context.session),
                    candidate_id=source.candidate_id,
                    package_id=package_id, version=version, title=title,
                )

            def publish_plan() -> tuple[CampaignPlanDraft, SafePayload]:
                selected_authority = selected_plan_authority()
                if selected_authority is None or context.session.campaign_id is not None:
                    raise CommandRejected("Independent Campaign Plan saving is unavailable")
                source = source_selection.candidate()
                if source is None:
                    raise CommandRejected("Plan publication needs this chat's saved Setting")
                workspace = _builder_workspace(storage_root, context.session)
                with materialize_plan_snapshot_draft(
                    application_owner, workspace, candidate_id=source.candidate_id,
                    expected_authority=selected_authority,
                ) as materialized:
                    effective_candidate = source
                    if materialized.closure_sha256 != source.closure_sha256:
                        suffix = hashlib.sha256(
                            f"{session_id}:{materialized.closure_sha256}".encode()
                        ).hexdigest()[:32]
                        # Runtime admission precedes saving a selectable Plan.
                        effective_candidate = _publish_builder_package_candidate(
                            application_owner, session_id, materialized.archive,
                            install_id=f"builder-plan-snapshot-{suffix}",
                            expected_closure_sha256=materialized.closure_sha256,
                        )
                    materialized.archive.seek(0)
                    plan = materialized.plan
                    if completing_required_characters:
                        require_completed_plan_characters(
                            application_owner, effective_candidate.candidate_id, plan,
                        )
                    saved = save_reusable_campaign_plan(
                        storage_root,
                        operation_id=f"builder-plan-{session_id}-{plan.content_sha256}",
                        source_payload=plan.payload,
                        source_markdown=plan.readable_markdown,
                        validate_plan=lambda payload, text: validate_retained_campaign_plan(
                            application_owner, payload, text,
                        ),
                        package_snapshot=materialized.archive.read(),
                    )
                published = publish_campaign_plan_files(workspace, saved.plan)
                application_owner.rename_builder_chat(
                    session_id, f"{saved.title} · Campaign Plan"[:120].strip(), automatic=True
                )
                return published, saved.catalog_payload()

            def publish_setting(*, package_id: str, version: str, title: str) -> SafePayload:
                source = source_selection.candidate()
                if source is None or context.session.campaign_id is not None:
                    raise CommandRejected("Setting publication needs this chat's saved Ruleset")
                with materialize_setting_draft(
                    application_owner, _builder_workspace(storage_root, context.session),
                    candidate_id=source.candidate_id,
                    package_id=package_id, version=version, title=title,
                ) as materialized:
                    suffix = hashlib.sha256(
                        f"{session_id}:{materialized.closure_sha256}".encode()
                    ).hexdigest()[:32]
                    installed = _publish_builder_package_candidate(
                        application_owner, session_id, materialized.archive,
                        install_id=f"builder-setting-{suffix}",
                        expected_closure_sha256=materialized.closure_sha256,
                    )
                setting = campaign_setting_entries(installed.package_lock)[0]
                application_owner.rename_builder_chat(
                    session_id, f"{title} · Campaign Setting"[:120].strip(), automatic=True,
                )
                return {
                    "kind": "campaign_setting", "id": setting.package_id,
                    "version": setting.version, "content_sha256": setting.sha256,
                    "title": title, "candidate_id": installed.candidate_id,
                }

            repair_context = next((
                message.content for message in context.messages
                if message.kind is SessionMessageKind.EVIDENCE
                and message.content.get("kind") == "import_repair_context"
            ), None)

            def run_import_repair(*, publishing: bool) -> SafePayload:
                if repair_context is None or context.session.campaign_id is not None:
                    raise CommandRejected("This chat has no selected import repair")
                with materialize_repair_draft(
                    _builder_workspace(storage_root, context.session),
                    expected_kind=repair_context["import_kind"],
                ) as materialized:
                    if not publishing:
                        return {
                            "status": "valid_draft", "published": False, "state_changed": False,
                            "closure_sha256": materialized.closure_sha256,
                        }
                    materialized.archive.seek(0)
                    suffix = hashlib.sha256(
                        f"{session_id}:{materialized.closure_sha256}".encode()
                    ).hexdigest()[:32]
                    installed = _publish_builder_package_candidate(
                        application_owner, session_id, materialized.archive,
                        install_id=f"builder-repair-{suffix}",
                        expected_closure_sha256=materialized.closure_sha256,
                    )
                    if materialized.plan is not None:
                        materialized.archive.seek(0)
                        snapshot = materialized.archive.read()
                        plan = materialized.plan
                        saved = save_reusable_campaign_plan(
                            storage_root,
                            operation_id=f"builder-repair-{session_id}-{plan.content_sha256}",
                            source_payload=plan.payload, source_markdown=plan.readable_markdown,
                            validate_plan=lambda payload, text: validate_plan_snapshot(
                                snapshot, payload, text,
                            ),
                            package_snapshot=snapshot,
                            setup_id=materialized.setup_id,
                            interview_contribution=materialized.interview_contribution,
                        )
                        receipt = saved.catalog_payload()
                    else:
                        receipt = {
                            "kind": "package", "candidate_id": installed.candidate_id,
                            "closure_sha256": installed.closure_sha256,
                        }
                    return {"status": "published", "published": True, **dict(receipt)}

            from sylvia_gm.agent._import_repair_tools import import_repair_tools

            repair_tools = import_repair_tools(
                lambda: run_import_repair(publishing=False),
                lambda: run_import_repair(publishing=True),
            ) if repair_context is not None and context.session.campaign_id is None else ()

            def builder_provider(
                provider_input: SessionProviderInput,
            ) -> Iterator[ProviderEvent]:
                nonlocal builder_result

                def complete(result: BuilderTurnResult) -> None:
                    nonlocal builder_result
                    builder_result = result

                yield from stream_builder_turn(
                    provider,
                    context=provider_input.history,
                    message_id=request.message_id,
                    user_text=request.user_text,
                    workspace=_builder_workspace(storage_root, context.session),
                    control=provider_input.control,
                    complete=complete,
                    request_title=request_title,
                    continuation_reader=lambda response_id: application_owner.provider_continuation(
                        provider_input.session.session_id, response_id,
                        provider_id=provider_input.session.provider_id,
                        model_id=provider_input.session.model_id,
                    ),
                    publish_ruleset_candidate=publish_ruleset_candidate,
                    publish_participant=(
                        publish_participant if participant_declarations else None
                    ),
                    participant_schemas={
                        declaration.schema_id: declaration.schema
                        for declaration in participant_declarations
                    },
                    participant_declarations=participant_declarations,
                    setting_character_schema=participant_setting_schema,
                    reuse_participant=reuse_participant if participant_declarations else None,
                    read_participant=read_participant if participant_declarations else None,
                    campaign_plan_authority=(selected_plan_authority if source_selection.selectable
                                             else campaign_plan_authority),
                    publish_campaign_plan=(
                        publish_plan if context.session.campaign_id is None else None
                    ),
                    publish_campaign_setting=(
                        publish_setting if (source_selection.selectable or (
                            saved_references is not None
                            and saved_references.get("candidate_id") is not None))
                        and context.session.campaign_id is None else None
                    ),
                    check_campaign_plan=(
                        check_plan if (campaign_plan_authority is not None
                                       or source_selection.selectable)
                        and context.session.campaign_id is None else None
                    ),
                    check_campaign_setting=(
                        check_setting if (source_selection.selectable or (
                            saved_references is not None
                            and saved_references.get("candidate_id") is not None))
                        and context.session.campaign_id is None else None
                    ),
                    repair_tools=repair_tools,
                    profile_tools=profile_tools,
                    resolve_source_profile=resolve_source_profile,
                    campaign_content_authority=CampaignContentToolAuthority(
                        items=_campaign_content_tool_authority(
                            application_owner, storage_root
                        ).items,
                        refresh=lambda: _campaign_content_tool_authority(
                            application_owner, storage_root
                        ).items,
                    ),
                    references=(source_selection.reader if source_selection.selectable
                                else reference_reader(application_owner, provider_input.history)),
                    source_tools=(builder_source_tool(source_selection),
                                  builder_setting_source_tool(source_selection))
                    if source_selection.selectable else (),
                    library_sources=library_sources,
                    allow_public_web=allow_public_web,
                    application_start=builder_start,
                )

            def persist_builder_result() -> tuple[object, ...]:
                if builder_result is None:
                    raise RuntimeError("Builder provider completed without a result")
                return persist_builder_turn(
                    application_owner,
                    session_id=session_id,
                    result=builder_result,
                )

            try:
                appended = session_loop.run_builder(
                    request,
                    builder_provider,
                    persist_builder_result,
                )
                if builder_result is None:  # pragma: no cover - generator contract defense
                    raise RuntimeError("Builder provider completed without a result")
                if builder_result.suggested_title:
                    # Publication-derived names and explicit user renames take precedence.
                    current_chat = next(
                        (chat for chat in application_owner.builder_chat_index()
                         if chat.session.session_id == session_id), None,
                    )
                    if current_chat is not None and current_chat.display_name == fallback_title:
                        application_owner.rename_builder_chat(
                            session_id, builder_result.suggested_title, automatic=True,
                        )
                return {
                    "session_id": session_id,
                    "tool_calls_made": builder_result.tool_calls_made,
                    "messages": appended,
                    "interruption": None,
                }
            finally:
                consume_pending_once()
        if _optional_bool(params, "allow_public_web"):
            raise OwnerProtocolError("allow_public_web is available only to Builder sessions")
        if context.session.campaign_id is None:
            raise OwnerProtocolError("DM sessions require campaign authority")
        campaign_plan_text, campaign_plan = _admitted_dm_campaign_plan(
            application_owner,
            campaign_id=context.session.campaign_id,
            session_id=session_id,
        )
        adapter = ProviderSessionAdapter(
            application_owner,
            provider,
            owner_id=owner_id,
            gm_system_prompt=gm_system_prompt,
            campaign_plan_text=campaign_plan_text,
            campaign_plan=campaign_plan,
        )
        try:
            return session_loop.run(
                SessionRunRequest(
                    session_id=session_id,
                    turn_id=turn_id,
                    attempt_id=_required_text(params, "attempt_id"),
                    message_id=_required_text(params, "message_id"),
                    user_text=user_text,
                    ooc_text=ooc_text,
                    campaign_ending_choice=ending_choice,
                    input_kind=(
                        SessionInputKind.PLAYER_ACTION_RESPONSE
                        if player_action_id is not None else SessionInputKind.USER
                    ),
                    player_action_id=player_action_id,
                    automatic_response=automatic_response,
                    redirect_interruption_id=_optional_text(params, "redirect_interruption_id"),
                ),
                adapter.for_turn(turn_id),
            )
        finally:
            consume_pending_once()
    if method == "interrupt_stream":
        return bus.interrupt_stream(
            _required_text(params, "campaign_id"),
            _required_text(params, "attempt_id"),
        )
    if method == "interrupt_provider_stream":
        bus.interrupt_provider_stream(
            _required_text(params, "campaign_id"),
            _required_text(params, "attempt_id"),
        )
        return {"status": "interrupted"}
    if method == "retryable_interruption":
        return bus.retryable_interruption(
            _required_text(params, "campaign_id"),
            failed_attempt_id=_required_text(params, "failed_attempt_id"),
        )
    if method == "manual_retry_session":
        session_id = _required_text(params, "session_id")
        context = application_owner.session_context(session_id)
        pending_once = session_model_once_settings(
            storage_root / "config.yaml", session_id=session_id
        )
        effective_session = (
            context.session
            if pending_once is None
            else dataclasses.replace(
                context.session,
                provider_id=pending_once["provider_id"],
                model_id=pending_once["model_id"],
            )
        )
        try:
            provider = provider_resolver(effective_session)
        except BaseException:
            if pending_once is not None:
                clear_session_model_once_settings(
                    storage_root / "config.yaml",
                    session_id=session_id,
                    expected_provider_id=pending_once["provider_id"],
                    expected_model_id=pending_once["model_id"],
                )
            raise
        if application_owner.admin_full_audit_enabled():
            provider = AdminAuditedProvider(
                provider,
                sink=application_owner.record_admin_full_audit,
                session_id=session_id,
                role=effective_session.role.value,
                provider_id=effective_session.provider_id,
                model_id=effective_session.model_id,
                selection_mode="once" if pending_once is not None else "session",
            )
        provider = _context_fitted_provider(
            bind_session_reasoning(provider, effective_session),
            gm_mode=effective_session.role is WriterRole.DM,
        )
        control = ProviderControl()
        retry_plan_text: str | None = None
        retry_plan: Mapping[str, object] | None = None
        campaign_knowledge: SafePayload | None = None
        if effective_session.role is WriterRole.DM and effective_session.campaign_id is not None:
            retry_plan_text, retry_plan = _admitted_dm_campaign_plan(
                application_owner,
                campaign_id=effective_session.campaign_id,
                session_id=session_id,
            )
            if retry_plan is not None:
                campaign_knowledge = application_owner.campaign_knowledge_catalog_for_plan(
                    effective_session.campaign_id,
                    retry_plan,
                )
        adapter = ProviderSessionAdapter(
            application_owner,
            provider,
            owner_id=owner_id,
            gm_system_prompt=gm_system_prompt,
            campaign_plan_text=retry_plan_text,
            campaign_plan=retry_plan,
            campaign_knowledge=campaign_knowledge,
        )
        retry_provider_input = SessionProviderInput(
            session=effective_session,
            history=context,
            catalog=(
                tuple(
                    cast(
                        Sequence[ModelMechanicsTool],
                        bus.catalog(effective_session.campaign_id),
                    )
                )
                if effective_session.campaign_id is not None
                else ()
            ),
            control=control,
        )
        try:
            return session_loop.retry(
                session_id,
                interruption_id=_required_text(params, "interruption_id"),
                retry_id=_required_text(params, "retry_id"),
                narrator=adapter.for_retry(
                    effective_session.model_id,
                    provider_input=retry_provider_input,
                    control=control,
                ),
                provider_factory=adapter.for_turn,
                cancel=control.cancel,
                control=control,
            )
        finally:
            if pending_once is not None:
                clear_session_model_once_settings(
                    storage_root / "config.yaml",
                    session_id=session_id,
                    expected_provider_id=pending_once["provider_id"],
                    expected_model_id=pending_once["model_id"],
                )
    if method == "disconnect_stream":
        return {
            "disconnect_deadline": bus.disconnect_stream(
                _required_text(params, "campaign_id"),
                _required_text(params, "attempt_id"),
                expected_generation=_required_nonnegative_int(params, "attachment_generation"),
            )
        }
    if method == "disconnect_provider_stream":
        return {
            "disconnect_deadline": bus.disconnect_provider_stream(
                _required_text(params, "campaign_id"),
                _required_text(params, "attempt_id"),
                expected_generation=_optional_nonnegative_int(params, "attachment_generation"),
            )
        }
    if method == "stream_heartbeat":
        attachment_deadline, attachment_generation = bus.heartbeat_stream(
            _required_text(params, "campaign_id"),
            _required_text(params, "attempt_id"),
            expected_generation=_optional_nonnegative_int(params, "attachment_generation"),
        )
        return {
            "attachment_deadline": attachment_deadline,
            "attachment_generation": attachment_generation,
        }
    if method == "provider_stream_heartbeat":
        attachment_deadline, attachment_generation = bus.heartbeat_provider_stream(
            _required_text(params, "campaign_id"),
            _required_text(params, "attempt_id"),
            expected_generation=_optional_nonnegative_int(params, "attachment_generation"),
        )
        return {
            "attachment_deadline": attachment_deadline,
            "attachment_generation": attachment_generation,
        }
    if method == "narration_attempt_status":
        return {
            "status": bus.narration_attempt_status(
                _required_text(params, "campaign_id"),
                _required_text(params, "attempt_id"),
            )
        }
    if method == "provider_stream_status":
        session_id = _required_text(params, "campaign_id")
        attempt_id = _required_text(params, "attempt_id")
        activity = session_loop.builder_activity(session_id, attempt_id=attempt_id)
        if activity is not None and activity["status"] != "active":
            return activity
        return {
            "status": bus.provider_stream_status(
                session_id,
                attempt_id,
            )
        }
    if method == "reconnect_stream":
        return bus.reconnect_stream(
            campaign_id=_required_text(params, "campaign_id"),
            attempt_id=_required_text(params, "attempt_id"),
            acknowledged_sequence=_required_nonnegative_int(params, "acknowledged_sequence"),
            expected_generation=_required_nonnegative_int(params, "attachment_generation"),
            listener=lambda _channel, _token: None,
        )
    if method == "reconnect_provider_stream":
        return {
            "attachment_generation": bus.reconnect_provider_stream(
                _required_text(params, "campaign_id"),
                _required_text(params, "attempt_id"),
                expected_generation=_optional_nonnegative_int(params, "attachment_generation"),
            )
        }
    if method == "install_package":
        archive_text = _required_text(params, "archive_base64")
        try:
            archive_bytes = base64.b64decode(archive_text, validate=True)
        except (binascii.Error, ValueError) as error:
            raise OwnerProtocolError("archive_base64 is invalid") from error
        if len(archive_bytes) > _MAX_PACKAGE_BYTES:
            raise OwnerProtocolError("Package archive exceeds the desktop import limit")
        return bus.install_package(
            io.BytesIO(archive_bytes),
            install_id=_required_text(params, "install_id"),
        )
    if method in ("preview_character_card_file", "confirm_character_card_import"):
        from sylvia_gm.application.character_card_import import stage_card_preview, confirm_card_preview
        try:
            if method == "preview_character_card_file":
                return stage_card_preview(storage_root, Path(_required_text(params, "source_path")))
            return confirm_card_preview(storage_root, _required_text(params, "source_sha256"))
        except (ValueError, OSError) as error:
            raise CommandRejected(str(error), code="character_card_invalid") from error

    if method == "install_package_file":
        source = Path(_required_text(params, "source_path")).expanduser().resolve()
        try:
            if not source.is_file():
                raise CommandRejected("Selected package file is unavailable")
            is_plan = source.suffix.lower() == ".sylvia-gm-plan"
            maximum = PLAN_BUNDLE_BYTE_LIMIT if is_plan else _MAX_PACKAGE_BYTES
            if source.stat().st_size > maximum:
                raise CommandRejected("Package archive exceeds the desktop import limit")
            with source.open("rb") as archive_stream:
                try:
                    if is_plan:
                        return import_plan_bundle(
                            storage_root, archive_stream,
                            install_snapshot=lambda snapshot, expected: _install_plan_snapshot(
                                application_owner, snapshot, expected,
                            ),
                        ).catalog_payload()
                    return bus.install_package(
                        archive_stream,
                        install_id=_required_text(params, "install_id"),
                    )
                except (AuthorityError, ValueError) as error:
                    repair = offer_import_repair(
                        storage_root, source,
                        install_id=_required_text(params, "install_id"), error=error,
                    )
                    if repair is not None:
                        return repair
                    raise
        except (ValueError, zipfile.BadZipFile) as error:
            raise CommandRejected(
                "Content archive could not be admitted. Check its format and content, "
                "then export a new copy using Store or Deflate ZIP compression."
            ) from error
        except OSError as error:
            raise CommandRejected("Selected package file is unavailable") from error

    if method == "start_rift_campaign":
        raw_companion_ids = params.get("companion_ids")
        if (
            not isinstance(raw_companion_ids, list)
            or len(raw_companion_ids) > 5
            or any(not isinstance(item, str) or not item for item in raw_companion_ids)
        ):
            raise OwnerProtocolError("companion_ids must contain zero to five identities")
        customization_notes = params.get("customization_notes", "")
        if not isinstance(customization_notes, str):
            raise OwnerProtocolError("customization_notes must be a string")
        construction_intent = params.get("construction_intent", {})
        if not isinstance(construction_intent, dict):
            raise OwnerProtocolError("construction_intent must be an object")
        return _start_rift_campaign(
            bus,
            application_owner,
            storage_root,
            operation_id=_required_text(params, "operation_id"),
            campaign_id=_required_text(params, "campaign_id"),
            builder_session_id=_required_text(params, "builder_session_id"),
            ruleset_selection=_required_text(params, "ruleset_selection"),
            setting_id=_required_text(params, "setting_id"),
            plan_id=_required_text(params, "plan_id"),
            display_name=_required_text(params, "display_name"),
            lane=_required_text(params, "lane"),
            archetype_id=_required_text(params, "archetype_id"),
            companion_ids=tuple(raw_companion_ids),
            customization_notes=customization_notes,
            provider_id=_required_text(params, "provider_id"),
            model_id=_required_text(params, "model_id"),
            construction_intent=cast(SafePayload, construction_intent),
        )

    if method == "start_guided_campaign":
        operation_id = _required_text(params, "operation_id")
        campaign_id = _required_text(params, "campaign_id")
        builder_session_id = _required_text(params, "builder_session_id")
        candidate_id = params.get("candidate_id")
        selection = None
        if params.get("plan_id") is not None:
            selection = resolve_campaign_library_selection(
                application_owner, storage_root,
                candidate_id=_required_text(params, "candidate_id"),
                setting_id=_required_text(params, "setting_id"),
                plan_id=_required_text(params, "plan_id"),
            )
            candidate_id = selection.candidate.candidate_id
        if candidate_id is None:
            package_lock = PackageLock(entries=())
        elif isinstance(candidate_id, str) and candidate_id:
            package_lock = _candidate_package_lock(bus, candidate_id)
        else:
            raise OwnerProtocolError("candidate_id must be a nonempty string or null")
        premise = params.get("premise", "")
        if not isinstance(premise, str) or len(premise) > _MAX_USER_TEXT_CHARS:
            raise OwnerProtocolError("premise must be a bounded string")
        canonical_input = {"premise": premise.strip()} if premise.strip() else {}
        participants = ()
        initial_state = ()
        selected_plan = None
        plan_id = params.get("plan_id")
        if plan_id is not None:
            if not isinstance(candidate_id, str):
                raise OwnerProtocolError("A saved Plan requires an installed Ruleset")
            raw_companions = params.get("companion_ids", [])
            if (
                not isinstance(raw_companions, list)
                or len(raw_companions) > 64
                or any(not isinstance(item, str) or not item for item in raw_companions)
            ):
                raise OwnerProtocolError("companion_ids must contain bounded character identities")
            intent = params.get("construction_intent") or {}
            if not isinstance(intent, dict):
                raise OwnerProtocolError("construction_intent must be an object")
            assert selection is not None
            participants, initial_state, selected_party = materialize_library_party(
                application_owner, selection, operation_id=operation_id,
                player_character_id=_required_text(params, "player_character_id"),
                companion_ids=tuple(raw_companions),
            )
            selected_plan = plan_with_interview_intent(selection, cast(SafePayload, intent))
            canonical_input.update(
                {
                    "plan_id": selection.plan.artifact_id,
                    "plan_content_sha256": selection.plan.content_sha256,
                    "setting_id": selection.sources.setting.package_id,
                    "player_character": {"archetype_id": params["player_character_id"]},
                    "companion_ids": raw_companions,
                    "starting_party_policy": selected_plan.payload["starting_party"],
                    "selected_party": selected_party,
                    "construction_intent": intent,
                }
            )
        elif params.get("player_character_id") or params.get("companion_ids"):
            raise CommandRejected("Selected characters require a concrete Campaign Plan")
        display_name = _required_text(params, "display_name")
        try:
            existing_basis = application_owner.campaign_construction_basis(campaign_id)
        except CommandRejected as error:
            if str(error) not in {
                "Confirmed campaign construction is unavailable",
                "Campaign construction has not begun",
            }:
                raise
        else:
            if (
                existing_basis.builder_session_id != builder_session_id
                or existing_basis.display_name != display_name
                or existing_basis.package_candidate_id != candidate_id
                or _guided_start_selection_input(existing_basis.canonical_input)
                != _guided_start_selection_input(canonical_input)
            ):
                raise CommandRejected(
                    "The interrupted guided Start basis differs from the current selections"
                )
            return {
                "campaign_id": campaign_id,
                "builder_session_id": builder_session_id,
                "resulting_revision": existing_basis.begin_revision,
            }
        application_owner.append_agent_message(
            session_id=builder_session_id,
            message_id=(
                "guided-ready-" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]
            ),
            kind=SessionMessageKind.USER,
            content={
                "text": "Ready to Begin",
                "interview_step": "ready",
                "interview_value": {"action": "ready_to_begin"},
            },
        )
        confirmation = bus.bind_guided_campaign_basis(
            operation_id=f"{operation_id}-bind",
            campaign_id=campaign_id,
            display_name=display_name,
            package_lock=package_lock,
            turn_id=f"turn-construction-{hashlib.sha256(operation_id.encode('utf-8')).hexdigest()[:24]}",
            builder_session_id=builder_session_id,
            package_candidate_id=candidate_id,
            participants=participants,
            initial_state=initial_state,
        )
        if selected_plan is not None:
            _seed_campaign_plan(
                storage_root, campaign_id,
                selected_plan.readable_markdown, selected_plan.payload,
            )
        elif candidate_id is None:
            _seed_campaign_plan(
                storage_root, campaign_id,
                f"# {display_name}\n\n"
                "This is an open-ended narrative sandbox with no Ruleset or game mechanics. "
                "Use the player's setup below as the starting brief. When it leaves the "
                "character or opening situation unspecified, ask the player before establishing "
                "those details. Preserve player choices and boundaries.\n\n"
                f"## Player setup\n\n{premise.strip() or 'No starting premise supplied.'}\n",
            )
        begun = bus.begin_campaign_construction(
            operation_id=f"{operation_id}-begin",
            campaign_id=campaign_id,
            lane="customize" if premise.strip() else "quick_start",
            expected_revision=confirmation.resulting_revision,
            canonical_input=canonical_input,
        )
        return {
            "campaign_id": campaign_id,
            "builder_session_id": builder_session_id,
            "resulting_revision": begun.resulting_revision,
        }

    if method == "create_guided_campaign_draft":
        if "builder_session_id" in params:
            raise OwnerProtocolError("builder_session_id is owner-assigned")
        operation_id = _required_text(params, "operation_id")
        builder_session_id = (
            "session-guided-" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]
        )
        draft = bus.create_guided_campaign_draft(
            operation_id=operation_id,
            display_name=_required_text(params, "display_name"),
            builder_session_id=builder_session_id,
            builder_provider_id=_required_text(params, "provider_id"),
            builder_model_id=_required_text(params, "model_id"),
        )
        return {
            "campaign_id": draft.campaign_id,
            "builder_session_id": draft.builder_session_id,
            "current_step": draft.current_step,
            "construction_revision": draft.construction_revision,
            "resulting_revision": draft.resulting_revision,
            "answer_message_id": draft.answer_message_id,
        }
    if method == "navigate_guided_campaign_draft":
        draft = bus.navigate_guided_campaign_draft(
            operation_id=_required_text(params, "operation_id"),
            campaign_id=_required_text(params, "campaign_id"),
            builder_session_id=_required_text(params, "builder_session_id"),
            expected_construction_revision=_required_nonnegative_int(
                params, "expected_construction_revision"
            ),
            target_step=_required_text(params, "target_step"),
        )
        return {
            "campaign_id": draft.campaign_id,
            "builder_session_id": draft.builder_session_id,
            "current_step": draft.current_step,
            "construction_revision": draft.construction_revision,
            "resulting_revision": draft.resulting_revision,
        }
    if method == "advance_guided_campaign_draft":
        answer = params.get("answer")
        if not isinstance(answer, dict):
            raise OwnerProtocolError("answer must be an object")
        draft = bus.advance_guided_campaign_draft(
            operation_id=_required_text(params, "operation_id"),
            campaign_id=_required_text(params, "campaign_id"),
            builder_session_id=_required_text(params, "builder_session_id"),
            expected_construction_revision=_required_nonnegative_int(
                params, "expected_construction_revision"
            ),
            current_step=_required_text(params, "current_step"),
            visible_answer=_required_text(params, "visible_answer"),
            answer=answer,
        )
        return {
            "campaign_id": draft.campaign_id,
            "builder_session_id": draft.builder_session_id,
            "current_step": draft.current_step,
            "construction_revision": draft.construction_revision,
            "resulting_revision": draft.resulting_revision,
            "answer_message_id": draft.answer_message_id,
        }
    if method == "guided_campaign_contribution":
        return application_owner.guided_campaign_contribution(
            builder_session_id=_required_text(params, "builder_session_id"),
            contribution_step=_required_text(params, "contribution_step"),
        )
    if method == "save_reusable_campaign_plan":
        operation_id = _required_text(params, "operation_id")
        campaign_id = _required_text(params, "campaign_id")
        builder_session_id = _required_text(params, "builder_session_id")
        expected_revision = _required_nonnegative_int(params, "expected_construction_revision")
        context = application_owner.session_context(builder_session_id)
        if context.session.campaign_id != campaign_id:
            raise CommandRejected("Reusable Campaign Plan source session is unavailable")
        states = tuple(
            message
            for message in context.messages
            if message.kind is SessionMessageKind.EVIDENCE
            and message.content.get("kind") == "campaign_interview_state"
        )
        state = states[-1].content if states else None
        subflows = None if state is None else state.get("subflows")
        derived = isinstance(subflows, Sequence) and any(
            isinstance(subflow, Mapping)
            and subflow.get("used") is True
            and subflow.get("purpose") in {"plan", "campaign_customization"}
            for subflow in subflows
        )
        if (
            state is None
            or state.get("current_step") != "review"
            or state.get("construction_revision") != expected_revision
            or state.get("active_subflow") is not None
            or not derived
        ):
            raise CommandRejected(
                "Only a current reviewed campaign-local Derived Plan can be saved for reuse",
                code="reusable_campaign_plan_source_unavailable",
            )
        payload = _campaign_plan_payload(storage_root, campaign_id)
        if payload is None:
            raise CommandRejected("Derived structured Campaign Plan is unavailable")
        markdown = _campaign_plan_text(storage_root, campaign_id)
        try:
            reusable = save_reusable_rift_plan(
                storage_root,
                operation_id=operation_id,
                title=_required_text(params, "title"),
                source_payload=payload,
                source_markdown=markdown,
                package_snapshot=retained_plan_snapshot(application_owner, payload),
            )
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
            raise CommandRejected(
                "Derived Campaign Plan could not be generalized safely",
                code="reusable_campaign_plan_invalid",
            ) from error
        return reusable.catalog_payload()
    if method == "start_guided_creation_subflow":
        operation_id = _required_text(params, "operation_id")
        purpose = _required_text(params, "purpose")
        profile_id = _optional_text(params, "character_profile_id")
        profile_hash = _optional_text(params, "character_profile_sha256")
        if (profile_id is None) != (profile_hash is None) or (
            profile_id is not None and purpose != "companion"
        ):
            raise CommandRejected(
                "Choose one exact general profile for a Companion adaptation",
                code="character_profile_invalid",
            )
        child_session_id = (
            "session-subflow-" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]
        )
        launch = (
            application_owner.start_interview_builder_chat
            if purpose in {"ruleset", "setting", "plan", "player_character", "companion"}
            else bus.start_guided_creation_subflow
        )
        reference_options = {}
        if purpose in {"ruleset", "setting", "plan", "player_character", "companion"}:
            existing = next(
                (
                    chat
                    for chat in application_owner.builder_chat_index()
                    if chat.session.session_id == child_session_id
                ),
                None,
            )
            reference_options["references"] = (
                launch_references(application_owner.session_context(child_session_id))
                if existing is not None
                else capture_launch_references(
                    application_owner,
                    storage_root,
                    application_owner.session_context(_required_text(params, "parent_session_id")),
                    _campaign_creation_catalog(storage_root, application_owner),
                )
            )
            retained = cast(dict[str, Any], reference_options["references"] or {})
            pinned = retained.get("character_profile")
            if existing is not None:
                actual = profile_from_snapshot(pinned) if pinned is not None else None
                if (actual is None and profile_id is not None) or (
                    actual is not None
                    and (actual.artifact_id != profile_id or actual.content_sha256 != profile_hash)
                ):
                    raise CommandRejected(
                        "This launch already selected a different source profile",
                        code="stale_character_profile",
                    )
            elif profile_id is not None:
                profile = load_character_profile(storage_root, profile_id, profile_hash)
                selected_candidate = candidate_for_references(application_owner, retained)
                profile_primary = (
                    next(
                        (
                            item
                            for item in selected_candidate.package_lock.entries
                            if item.role is PackageRole.PRIMARY
                        ),
                        None,
                    )
                    if selected_candidate
                    else None
                )
                declarations = (
                    application_owner.exact_participant_declarations(
                        profile_primary.package_id,
                        profile_primary.version,
                        profile_primary.sha256,
                    )
                    if profile_primary is not None
                    else ()
                )
                profile_plan = retained.get("plan")
                profile_policy = (
                    profile_plan.get("payload", {}).get("starting_party", {})
                    if isinstance(profile_plan, Mapping)
                    else {}
                )
                maximum = profile_policy.get("custom_companions", {}).get("maximum", 0)
                profile_parent = application_owner.session_context(
                    _required_text(params, "parent_session_id")
                )
                profile_parent_state = cast(
                    dict[str, Any],
                    next(
                        (
                            message.content
                            for message in reversed(profile_parent.messages)
                            if message.content.get("kind") == "campaign_interview_state"
                        ),
                        {},
                    ),
                )
                answer_id = profile_parent_state.get("answers", {}).get("participants")
                profile_selection = cast(
                    dict[str, Any],
                    next(
                        (
                            message.content.get("interview_value", {})
                            for message in profile_parent.messages
                            if message.message_id == answer_id
                        ),
                        {},
                    ),
                )
                required_ids = {
                    item["id"] for item in profile_policy.get("required_companions", [])
                }
                custom_count = len(set(profile_selection.get("companion_ids", [])) - required_ids)
                if (
                    not any(item.entity_state_schema is not None for item in declarations)
                    or not any(
                        item.get("prefix") == "setting" for item in retained.get("packages", [])
                    )
                    or not isinstance(maximum, int)
                    or maximum <= custom_count
                ):
                    raise CommandRejected(
                        "Select a Plan with an available Custom Companion slot "
                        "and character creation rules",
                        code="character_profile_target_unavailable",
                    )
                reference_options["references"] = {
                    **retained,
                    "character_profile": profile.snapshot(),
                }
        subflow = launch(
            operation_id=operation_id,
            campaign_id=_required_text(params, "campaign_id"),
            parent_session_id=_required_text(params, "parent_session_id"),
            expected_construction_revision=_required_nonnegative_int(
                params, "expected_construction_revision"
            ),
            purpose=purpose,
            visible_answer=_required_text(params, "visible_answer"),
            child_session_id=child_session_id,
            trigger_message_id=_optional_text(params, "trigger_message_id"),
            **reference_options,
        )
        return _json_value(subflow)
    if method == "return_guided_creation_subflow":
        subflow = bus.return_guided_creation_subflow(
            operation_id=_required_text(params, "operation_id"),
            campaign_id=_required_text(params, "campaign_id"),
            parent_session_id=_required_text(params, "parent_session_id"),
            child_session_id=_required_text(params, "child_session_id"),
            expected_construction_revision=_required_nonnegative_int(
                params, "expected_construction_revision"
            ),
            use_result=_optional_bool(params, "use_result"),
        )
        return _json_value(subflow)
    if method == "confirm_new_campaign":
        if "builder_session_id" in params:
            raise OwnerProtocolError("builder_session_id is owner-assigned")
        operation_id = _required_text(params, "operation_id")
        builder_session_id = (
            "session-builder-" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]
        )
        candidate_id = params.get("candidate_id")
        if candidate_id is None:
            package_lock = PackageLock(entries=())
        elif isinstance(candidate_id, str) and candidate_id:
            package_lock = _candidate_package_lock(bus, candidate_id)
        else:
            raise OwnerProtocolError("candidate_id must be a nonempty string or null")
        confirmation = bus.confirm_new_campaign(
            operation_id=operation_id,
            display_name=_required_text(params, "display_name"),
            package_lock=package_lock,
            turn_id=_required_text(params, "turn_id"),
            package_candidate_id=candidate_id,
            builder_session_id=builder_session_id,
            builder_provider_id=_required_text(params, "provider_id"),
            builder_model_id=_required_text(params, "model_id"),
            builder_selected_skill=(
                _optional_text(params, "builder_skill") or "BUILD CAMPAIGN PLAN"
            ),
        )
        return {
            "campaign_id": confirmation.campaign_id,
            "resulting_revision": confirmation.resulting_revision,
            "builder_session_id": builder_session_id,
        }
    if method == "begin_campaign_construction":
        construction_input = params.get("canonical_input")
        if not isinstance(construction_input, dict):
            raise OwnerProtocolError("canonical_input must be an object")
        operation_id = _required_text(params, "operation_id")
        campaign_id = _required_text(params, "campaign_id")
        lane = _required_text(params, "lane")
        begun = bus.begin_campaign_construction(
            operation_id=operation_id,
            campaign_id=campaign_id,
            lane=lane,
            expected_revision=_required_nonnegative_int(params, "expected_revision"),
            canonical_input=construction_input,
        )
        campaign = next(
            entry
            for entry in application_owner.campaign_index()
            if entry.campaign_id == campaign_id
        )
        builder_session = next(
            session for session in campaign.sessions if session.role == "builder"
        )
        application_owner.append_agent_message(
            session_id=builder_session.session_id,
            message_id=(
                "construction-brief-"
                + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]
            ),
            kind=SessionMessageKind.EVIDENCE,
            content={
                "kind": "campaign_construction_brief",
                "lane": lane,
                "input": construction_input,
            },
        )
        return begun
    if method == "complete_campaign_construction":
        canonical_plan = params.get("canonical_plan")
        if not isinstance(canonical_plan, dict):
            raise OwnerProtocolError("canonical_plan must be an object")
        return bus.complete_campaign_construction(
            operation_id=_required_text(params, "operation_id"),
            campaign_id=_required_text(params, "campaign_id"),
            expected_revision=_required_nonnegative_int(params, "expected_revision"),
            canonical_plan=canonical_plan,
        )
    if method == "model_guidance":
        guidance: Any = bus.model_guidance(
            campaign_id=_required_text(params, "campaign_id"),
            model_id=_required_text(params, "model_id"),
            override_accepted=_optional_bool(params, "override_accepted"),
        )
        return {
            **_json_value(guidance),
            "warning_required": guidance.warning_required,
            "may_continue": guidance.may_continue,
        }
    if method == "finalize_campaign_construction":
        campaign_id = _required_text(params, "campaign_id")
        dm_provider_id = _required_text(params, "dm_provider_id")
        dm_model_id = _required_text(params, "dm_model_id")
        plan_text = _campaign_plan_text(storage_root, campaign_id)
        structured_plan = _campaign_plan_payload(storage_root, campaign_id)
        _preflight_guided_start(
            application_owner=application_owner,
            bus=bus,
            provider_resolver=provider_resolver,
            owner_id=owner_id,
            campaign_id=campaign_id,
            provider_id=dm_provider_id,
            model_id=dm_model_id,
            plan_text=plan_text,
            structured_plan=structured_plan,
            gm_system_prompt=gm_system_prompt,
        )
        construction, plan, handoff = bus.finalize_campaign_construction(
            campaign_id=campaign_id,
            dm_provider_id=dm_provider_id,
            dm_model_id=dm_model_id,
            plan_artifact_text=plan_text,
            structured_plan=structured_plan,
            model_guidance_override=_optional_bool(
                params,
                "model_guidance_override",
            ),
        )
        dm_turn_id = application_owner.current_open_turn(campaign_id)
        if dm_turn_id is None:
            suffix = hashlib.sha256(campaign_id.encode("utf-8")).hexdigest()[:24]
            dm_turn_id = f"turn-dm-{suffix}"
            bus.open_turn(campaign_id, dm_turn_id)
        return {
            "construction": construction,
            "plan": plan,
            "handoff": handoff,
            "dm_turn_id": dm_turn_id,
            "dm_attempt_id": (
                "attempt-campaign-start-"
                + hashlib.sha256(campaign_id.encode("utf-8")).hexdigest()[:24]
            ),
        }
    if method == "start_dm_session":
        campaign_id = _required_text(params, "campaign_id")
        session_id = _required_text(params, "session_id")
        turn_id = _required_text(params, "turn_id")
        dm_context = application_owner.session_context(session_id)
        if (
            dm_context.session.role is not WriterRole.DM
            or dm_context.session.campaign_id != campaign_id
            or application_owner.current_open_turn(campaign_id) != turn_id
        ):
            raise CommandRejected("Campaign start session authority does not match")
        has_start_event = any(
            message.kind is SessionMessageKind.EVIDENCE
            and message.content.get("kind") == "campaign_start"
            for message in dm_context.messages
        )
        has_opening_narration = any(
            message.kind is SessionMessageKind.CANONICAL_REFERENCE
            and message.content.get("event_type") == "talk"
            for message in dm_context.messages
        )
        if has_start_event and has_opening_narration:
            return {"status": "already_started", "session_id": session_id}
        provider = provider_resolver(dm_context.session)
        if application_owner.admin_full_audit_enabled():
            provider = AdminAuditedProvider(
                provider,
                sink=application_owner.record_admin_full_audit,
                session_id=session_id,
                role=dm_context.session.role.value,
                provider_id=dm_context.session.provider_id,
                model_id=dm_context.session.model_id,
                selection_mode="session",
            )
        provider = _context_fitted_provider(
            bind_session_reasoning(provider, dm_context.session), gm_mode=True,
        )
        opening_plan_text, opening_plan = _admitted_dm_campaign_plan(
            application_owner,
            campaign_id=campaign_id,
            session_id=session_id,
        )
        adapter = ProviderSessionAdapter(
            application_owner,
            provider,
            owner_id=owner_id,
            gm_system_prompt=gm_system_prompt,
            campaign_plan_text=opening_plan_text,
            campaign_plan=opening_plan,
        )
        suffix = hashlib.sha256(campaign_id.encode("utf-8")).hexdigest()[:24]
        return session_loop.run(
            SessionRunRequest(
                session_id=session_id,
                turn_id=turn_id,
                attempt_id=f"attempt-campaign-start-{suffix}",
                message_id=f"message-campaign-start-{suffix}",
                user_text="",
                input_kind=SessionInputKind.CAMPAIGN_START,
            ),
            adapter.for_turn(turn_id),
        )
    if method == "catalog":
        catalog: Any = bus.catalog(_required_text(params, "campaign_id"))
        return [
            {
                "package_id": tool.package_id,
                "package_version": tool.package_version,
                "package_sha256": tool.package_sha256,
                "name": tool.name,
                "capability": tool.capability,
                "input_schema": tool.input_schema,
            }
            for tool in catalog
        ]
    if method == "execute_action":
        parameters = params.get("parameters", {})
        if not isinstance(parameters, dict):
            raise OwnerProtocolError("parameters must be an object")
        return bus.execute_action(
            ActionCommand(
                campaign_id=_required_text(params, "campaign_id"),
                action_id=_required_text(params, "action_id"),
                idempotency_key=_required_text(params, "idempotency_key"),
                owner_id=owner_id,
                writer=WriterRole(_required_text(params, "writer")),
                expected_revision=_required_nonnegative_int(params, "expected_revision"),
                turn_id=_required_text(params, "turn_id"),
                package_id=_required_text(params, "package_id"),
                tool_name=_required_text(params, "tool_name"),
                parameters=parameters,
                initiating_session_id=(
                    None
                    if params.get("session_id") is None
                    else _required_text(params, "session_id")
                ),
            )
        )
    if method == "resolve_mechanics_choice":
        return bus.resolve_mechanics_choice(
            ResolveMechanicsChoiceCommand(
                campaign_id=_required_text(params, "campaign_id"),
                action_id=_required_text(params, "action_id"),
                resolution_id=_required_text(params, "resolution_id"),
                owner_id=owner_id,
                expected_revision=_required_nonnegative_int(params, "expected_revision"),
                choice_id=_required_text(params, "choice_id"),
                option_id=_required_text(params, "option_id"),
                choice_sequence=_required_nonnegative_int({"choice_sequence": params.get("choice_sequence", 0)}, "choice_sequence"),
                session_id=(
                    None
                    if params.get("session_id") is None
                    else _required_text(params, "session_id")
                ),
            )
        )
    if method == "open_turn":
        bus.open_turn(
            _required_text(params, "campaign_id"),
            _required_text(params, "turn_id"),
        )
        return {"status": "open"}
    if method == "complete_turn":
        bus.complete_turn(
            _required_text(params, "campaign_id"),
            _required_text(params, "turn_id"),
        )
        return {"status": "completed"}
    if method == "stop_agent_session":
        session_id = _required_text(params, "session_id")
        context = application_owner.session_context(session_id)
        stopped = session_loop.stop_agent_session(session_id)
        if context.session.role is WriterRole.DM:
            stopped_campaign_id = context.session.campaign_id
            if stopped_campaign_id is None:
                raise OwnerProtocolError("DM sessions require campaign authority")
            open_turn_id = application_owner.current_open_turn(stopped_campaign_id)
            if open_turn_id is not None:
                application_owner.complete_turn(stopped_campaign_id, open_turn_id)
        return stopped
    if method == "resume_agent_session":
        return bus.resume_agent_session(_required_text(params, "session_id"))
    if method == "rename_campaign":
        return bus.rename_campaign(
            operation_id=_required_text(params, "operation_id"),
            campaign_id=_required_text(params, "campaign_id"),
            display_name=_required_text(params, "display_name"),
            expected_revision=_required_nonnegative_int(params, "expected_revision"),
        )
    if method == "archive_campaign":
        return bus.archive_campaign(
            operation_id=_required_text(params, "operation_id"),
            campaign_id=_required_text(params, "campaign_id"),
            expected_revision=_required_nonnegative_int(params, "expected_revision"),
            designated_session_id=_optional_text(params, "designated_session_id"),
        )
    if method == "restore_archived_campaign":
        return bus.restore_archived_campaign(
            operation_id=_required_text(params, "operation_id"),
            campaign_id=_required_text(params, "campaign_id"),
            expected_revision=_required_nonnegative_int(params, "expected_revision"),
            session_id=_optional_text(params, "session_id"),
        )
    if method == "trash_campaign":
        return bus.trash_campaign(
            operation_id=_required_text(params, "operation_id"),
            campaign_id=_required_text(params, "campaign_id"),
            expected_revision=_required_nonnegative_int(params, "expected_revision"),
            designated_session_id=_optional_text(params, "designated_session_id"),
        )
    if method == "restore_trashed_campaign":
        return bus.restore_trashed_campaign(
            operation_id=_required_text(params, "operation_id"),
            campaign_id=_required_text(params, "campaign_id"),
            expected_revision=_required_nonnegative_int(params, "expected_revision"),
        )
    if method == "purge_campaign":
        campaign_id = _required_text(params, "campaign_id")
        operation_id = _required_text(params, "operation_id")
        workspace = storage_root / "campaigns" / campaign_id
        purge_stage = None

        def prepare_external_purge() -> None:
            nonlocal purge_stage
            purge_stage = stage_builder_sources_for_purge(
                workspace,
                campaign_id=campaign_id,
                operation_id=operation_id,
            )

        def rollback_external_purge() -> None:
            if purge_stage is not None:
                restore_builder_sources_after_failed_purge(purge_stage)

        purge_result = application_owner.purge_campaign(
            operation_id=operation_id,
            campaign_id=campaign_id,
            expected_revision=_required_nonnegative_int(params, "expected_revision"),
            confirmation=_required_text(params, "confirmation"),
            prepare_external_purge=prepare_external_purge,
            rollback_external_purge=rollback_external_purge,
        )
        if purge_stage is None:
            finalize_existing_builder_source_purge(
                workspace,
                campaign_id=campaign_id,
                operation_id=operation_id,
            )
        else:
            finalize_builder_source_purge(purge_stage)
        return purge_result
    if method == "backup_database":
        backup_id = _required_artifact_id(params, "backup_id")
        target = storage_root / "backups" / f"{backup_id}.sqlite3"
        target.parent.mkdir(parents=True, exist_ok=True)
        bus.backup_database(target)
        return {"backup_id": backup_id, "status": "created"}
    if method == "export_campaign_plan_file":
        export_id = _required_artifact_id(params, "export_id")
        plan_id = _required_text(params, "plan_id")
        expected = _required_text(params, "expected_content_sha256")
        selected = next(
            (item for item in _campaign_creation_catalog(storage_root, application_owner)
             if item.get("kind") == "campaign_plan" and item.get("id") == plan_id),
            None,
        )
        if selected is None:
            # A retained built-in saved reference can be exported without adding
            # it to the public new-campaign catalogue. Its exact digest still gates export.
            from sylvia_gm.application.bundled_campaign_plans import bundled_plan
            retained = bundled_plan(plan_id)
            if retained is not None and retained.setup_id is not None:
                selected = dict(retained.catalog_payload())
                selected.pop("reusable", None)
                selected["bundled"] = True
        if selected is None or selected.get("content_sha256") != expected:
            raise CommandRejected("Selected Campaign Plan changed; refresh its library entry")
        target = storage_root / "exports" / f"{export_id}.sylvia-gm-plan"
        target.parent.mkdir(parents=True, exist_ok=True)
        if selected.get("reusable"):
            saved = load_reusable_campaign_plan(
                storage_root, plan_id,
                validate_plan=lambda payload, text: validate_retained_campaign_plan(
                    application_owner, payload, text,
                ),
            )
            plan = saved.plan
        else:
            saved = selected_campaign_plan(storage_root, plan_id)
            plan = saved.plan
        if selected.get("reusable") and saved.package_snapshot is not None:
            with target.open("wb") as stream:
                receipt = export_plan_bundle(
                    storage_root, saved.artifact_id,
                    expected_content_sha256=saved.content_sha256, target=stream,
                )
        else:
            # Capture an older Plan's retained sources without changing its library entry.
            snapshot = saved.package_snapshot if saved.package_snapshot is not None else (
                retained_plan_snapshot(application_owner, plan.payload)
            )
            with tempfile.TemporaryDirectory(prefix=".plan-export-", dir=storage_root) as temporary:
                export_root = Path(temporary)
                portable = save_reusable_campaign_plan(
                    export_root, operation_id=f"portable-{plan_id}",
                    source_payload=plan.payload, source_markdown=plan.readable_markdown,
                    validate_plan=lambda payload, text: validate_plan_snapshot(
                        snapshot, payload, text,
                    ),
                    package_snapshot=snapshot,
                    setup_id=selected.get("setup_id"),
                    interview_contribution=(
                        saved.interview_contribution if saved is not None
                        else selected.get("interview_contribution")
                    ),
                )
                with target.open("wb") as stream:
                    receipt = export_plan_bundle(
                        export_root, portable.artifact_id,
                        expected_content_sha256=portable.content_sha256, target=stream,
                    )
        return {"export": receipt, "artifact_path": str(target.resolve(strict=True))}
    if method in {"export_package_candidate", "export_package_candidate_file"}:
        export_id = _required_artifact_id(params, "export_id")
        target = storage_root / "exports" / f"{export_id}.sylvia-gm-package"
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as stream:
            result = bus.export_package_candidate(
                _required_text(params, "candidate_id"),
                stream,
            )
        if method == "export_package_candidate_file":
            return {"export": result, "artifact_path": str(target.resolve(strict=True))}
        return result
    if method in {"export_campaign", "export_campaign_file"}:
        export_id = _required_artifact_id(params, "export_id")
        target = storage_root / "exports" / f"{export_id}.sylvia-gm-campaign"
        target.parent.mkdir(parents=True, exist_ok=True)
        result = bus.export_campaign(
            _required_text(params, "operation_id"),
            _required_text(params, "campaign_id"),
            target,
            include_thinking=_optional_bool(params, "include_thinking"),
        )
        if method == "export_campaign_file":
            return {"export": result, "artifact_path": str(target.resolve(strict=True))}
        return result
    if method == "recover_campaign":
        export_id = _required_artifact_id(params, "export_id")
        source = storage_root / "exports" / f"{export_id}.sylvia-gm-campaign"
        return bus.recover_campaign(
            recovery_id=_required_text(params, "recovery_id"),
            source=source,
        )
    if method == "recover_campaign_file":
        source = Path(_required_text(params, "source_path")).resolve(strict=True)
        if source.suffix != ".sylvia-gm-campaign" or not source.is_file():
            raise OwnerProtocolError("Selected recovery source is not a Sylvia GM campaign archive")
        return bus.recover_campaign(
            recovery_id=_required_text(params, "recovery_id"),
            source=source,
        )
    raise OwnerProtocolError(f"Unsupported owner method: {method}")


def run(
    input_stream: TextIO,
    output_stream: TextIO,
    *,
    provider_resolver: ProviderResolver | None = None,
    gm_system_prompt: str | None = None,
) -> int:
    supervisor: LocalOwnerSupervisor | None = None
    bus: LocalCommandBus | None = None
    workers: set[threading.Thread] = set()
    workers_lock = threading.Lock()
    write_lock = threading.Lock()
    closing = threading.Event()
    resolver_lock = threading.Lock()
    diagnostic_lock = threading.Lock()
    configured_resolver = provider_resolver
    owner_config_path: Path | None = None
    diagnostic_path: Path | None = None
    display_reads: DisplayReads | None = None

    def resolve_provider(session: Any) -> Any:
        nonlocal configured_resolver
        with resolver_lock:
            if configured_resolver is None:
                configured_resolver = ConfiguredProviderResolver(owner_config_path)
            resolver = configured_resolver
        return resolver(session)

    def list_model_choices() -> Sequence[Mapping[str, object]]:
        nonlocal configured_resolver
        with resolver_lock:
            if configured_resolver is None:
                configured_resolver = ConfiguredProviderResolver(owner_config_path)
            resolver = configured_resolver
        choices = getattr(resolver, "model_choices", None)
        if not callable(choices):
            raise OwnerProtocolError("Provider resolver does not expose model choices")
        result: Any = choices()
        if not isinstance(result, Sequence):
            raise OwnerProtocolError("Provider resolver returned invalid model choices")
        return result

    def reload_configured_provider() -> None:
        nonlocal configured_resolver
        if owner_config_path is None:
            raise OwnerProtocolError("Provider settings are unavailable before owner launch")
        replacement = ConfiguredProviderResolver(owner_config_path)
        with resolver_lock:
            configured_resolver = replacement

    try:
        launch = _read_message(input_stream)
        if launch is None:
            raise OwnerProtocolError("Owner launch envelope is missing")
        supervisor, bus, external_response, owner_id, storage_root = _launch(launch)
        diagnostic_path = _runtime_diagnostic_path(storage_root)
        _prepare_runtime_diagnostic(diagnostic_path)
        _write_runtime_diagnostic(diagnostic_path, event="owner_started")
        owner_config_path = storage_root / "config.yaml"
        application_owner = supervisor.application_owner()
        _preinstall_bundled_srd(bus)
        if application_owner.legacy_builder_contexts():
            migrate_legacy_builder_chats(
                application_owner, storage_root,
                _campaign_creation_catalog(storage_root, application_owner),
            )
        if os.environ.get(_TEST_FULL_AUDIT_ENV) == "1":
            application_owner.set_admin_full_audit(True)
        reconcile_builder_source_purges(
            storage_root,
            {entry.campaign_id for entry in application_owner.campaign_index()},
            application_owner.campaign_purge_receipt_exists,
        )
        session_loop = CampaignSessionLoop(
            application_owner,
            on_narration_started=supervisor.track_narration_attempt,
            on_narration_finished=supervisor.release_narration_attempt,
            on_provider_work_started=supervisor.track_provider_work,
            on_provider_work_finished=supervisor.release_provider_work,
        )
        _write_message(
            output_stream,
            {"ok": True, "type": "ready", "response_hex": external_response},
        )
        external_response = ""  # drop the proof after the parent receives it

        def write_reply(reply: Mapping[str, Any]) -> None:
            if closing.is_set():
                return
            with write_lock:
                _write_message(output_stream, reply)

        def execute_request(
            request_id: str,
            method: str,
            params: Mapping[str, Any],
            preparation: ExitStack | None = None,
        ) -> None:
            started = time.monotonic()
            error_code: str | None = None
            error_type: str | None = None
            try:
                result = _dispatch(
                    bus,
                    method,
                    params,
                    application_owner=application_owner,
                    owner_id=owner_id,
                    provider_resolver=resolve_provider,
                    model_choices=list_model_choices,
                    session_loop=session_loop,
                    storage_root=storage_root,
                    reload_provider_resolver=reload_configured_provider,
                    gm_system_prompt=gm_system_prompt,
                    provider_work_reserved=preparation is not None,
                )
                reply = {"id": request_id, "ok": True, "result": _json_value(result)}
            except (
                AuthorityError,
                ConfigError,
                OwnerProtocolError,
                ProviderError,
                ValueError,
            ) as error:
                is_provider_error = isinstance(error, ProviderError)
                code = (
                    "provider_error"
                    if is_provider_error
                    else getattr(error, "code", "command_rejected")
                )
                error_code = str(code)
                error_type = type(error).__name__
                if isinstance(error, ContextCompactionError):
                    code = error_code = "context_compaction_failed"
                    message = str(error)
                elif isinstance(error, TokenLimitError):
                    message = str(error)
                elif isinstance(error, ProviderContinuationRequiredError):
                    message = (
                        "The model requires a native continuation that Sylvia cannot perform "
                        "automatically. Previously saved work is kept. Choose a smaller step "
                        "or another model."
                    )
                elif isinstance(error, ProviderRefusalError):
                    message = (
                        "The model declined this request. Previously saved work is kept. "
                        "Revise the request or choose another model."
                    )
                elif isinstance(error, ProviderPausedError):
                    message = (
                        "The provider paused for a server-tool continuation that Sylvia does "
                        "not support. Previously saved work is kept. Choose a model or API "
                        "configuration that does not require server tools."
                    )
                elif isinstance(error, ProviderOutputLimitError):
                    message = (
                        "The model reached its response limit. Saved work is kept. "
                        "Continue with a smaller step or increase the supported response budget."
                    )
                elif isinstance(error, ProviderTimeoutError):
                    message = "The selected provider timed out while responding"
                elif isinstance(error, ModelCapacityError):
                    message = (
                        "The selected model is at capacity. Saved work is kept. "
                        "Try again later or choose another model for this chat."
                    )
                elif isinstance(error, AuthenticationError):
                    message = (
                        "The provider rejected authentication. Sign in again or check the API key "
                        "for this connection in Settings. Saved work is kept."
                    )
                elif isinstance(error, RateLimitError):
                    message = (
                        "The provider reported a rate or usage limit. Check your provider's "
                        "allowance or try again later. Saved work is kept."
                    )
                    delay = error.retry_after
                    if type(delay) in (int, float) and 0 < delay < 604800:
                        message += f" Retry delay: {delay:g} seconds."
                elif isinstance(error, ModelNotFoundError):
                    message = (
                        "The selected model was not found or is unavailable to this connection. "
                        "Check the model selection in Settings. Saved work is kept."
                    )
                elif isinstance(error, ProviderConnectionError):
                    message = (
                        "Sylvia could not connect to the provider. Check the connection address, "
                        "network, and whether your local model server is running, then retry. "
                        "Saved work is kept."
                    )
                elif isinstance(error, InvalidRequestError):
                    message = (
                        "The provider rejected the request. Check this connection's model and "
                        "API settings. Previously saved work is kept. If it repeats, report "
                        "the action you were taking."
                    )
                elif isinstance(error, IncompleteProviderStreamError):
                    message = (
                        "The provider's response ended before completion. Previously saved work "
                        "is kept. Retry or continue the interrupted action."
                    )
                elif isinstance(error, InvalidToolArgumentsError):
                    message = (
                        "The model returned invalid tool arguments, so Sylvia could not apply "
                        "that operation. Previously saved work is kept. Retry or ask the model "
                        "to continue with a smaller step."
                    )
                elif isinstance(error, InvalidResponseError):
                    message = (
                        "The provider returned an invalid response that Sylvia could not use. "
                        "Previously saved work is kept. Retry; if it repeats, check the "
                        "connection's API format and model support in Settings."
                    )
                elif isinstance(error, ProviderUnavailableError):
                    message = (
                        "The provider is unavailable or could not complete the response. "
                        "Previously saved work is kept. Try again later."
                    )
                elif is_provider_error:
                    message = (
                        "The provider could not complete the request; no specific cause was "
                        "reported. Previously saved work is kept. Retry, and report the action "
                        "you were taking if it repeats."
                    )
                else:
                    message = str(error)
                reply = {
                    "id": request_id,
                    "ok": False,
                    "error": {
                        "code": str(code),
                        "message": message,
                        "diagnostics": [
                            diagnostic.as_payload()
                            for diagnostic in getattr(error, "diagnostics", ())
                        ],
                    },
                }
            except Exception as error:  # keep the sole owner alive on internal faults
                error_code = "internal_error"
                error_type = type(error).__name__
                print(
                    f"Sylvia GM owner command failed: {type(error).__name__}",
                    file=sys.stderr,
                )
                reply = {
                    "id": request_id,
                    "ok": False,
                    "error": {
                        "code": "internal_error",
                        "message": (
                            "Sylvia encountered an internal error, not a confirmed provider "
                            "failure. Previously saved work is kept. Try the action again; "
                            "if it repeats, report this error with the action you were taking."
                        ),
                    },
                }
            finally:
                if preparation is not None:
                    preparation.close()
            if diagnostic_path is not None:
                with diagnostic_lock:
                    _write_runtime_diagnostic(
                        diagnostic_path,
                        event="command_completed",
                        operation=method,
                        outcome="error" if error_code is not None else "ok",
                        error_code=error_code,
                        error_type=error_type,
                        duration_ms=(time.monotonic() - started) * 1000,
                    )
            write_reply(reply)

        def execute_worker(
            request_id: str,
            method: str,
            params: Mapping[str, Any],
            preparation: ExitStack | None = None,
        ) -> None:
            try:
                execute_request(request_id, method, params, preparation)
            finally:
                if preparation is not None:
                    preparation.close()
                with workers_lock:
                    workers.discard(threading.current_thread())

        def display_cancelled(request_id: str) -> None:
            write_reply({"id": request_id, "ok": False, "error": {
                "code": "display_load_cancelled",
                "message": "This display load is no longer selected",
            }})

        display_reads = DisplayReads(display_cancelled)

        while True:
            request = _read_message(input_stream)
            if request is None:
                return 0
            request_id = request.get("id")
            if not isinstance(request_id, str) or not request_id:
                raise OwnerProtocolError("Owner request id must be a nonempty string")
            method = _required_text(request, "method")
            params = request.get("params", {})
            if not isinstance(params, dict):
                raise OwnerProtocolError("Owner request params must be an object")
            if method == "shutdown":
                write_reply({"id": request_id, "ok": True, "result": {"status": "closed"}})
                return 0
            if method == "cancel_display_load":
                display_reads.cancel(_required_text(params, "load_id"))
                write_reply({"id": request_id, "ok": True, "result": {"status": "cancelled"}})
                continue
            if method in {
                "display_snapshot", "player_projection", "player_projection_page",
                "campaign_panels",
            }:
                load_id = params.get("load_id")
                if load_id is not None and (
                    not isinstance(load_id, str) or not 1 <= len(load_id) <= 128
                ):
                    raise OwnerProtocolError("Display load identity must be a bounded string")
                display_reads.submit(request_id, load_id, functools.partial(
                    execute_request, request_id, method, params,
                ))
                continue
            if method in {
                "start_dm_session",
                "submit_session_input",
                "manual_retry_session",
            }:
                preparation = ExitStack()
                try:
                    session_id = _generation_session(method, params)
                    if session_id is not None:
                        preparation.enter_context(session_loop.provider_request_work(session_id))
                except (AuthorityError, OwnerProtocolError, ValueError) as error:
                    preparation.close()
                    write_reply({"id": request_id, "ok": False, "error": {
                        "code": getattr(error, "code", "command_rejected"), "message": str(error),
                    }})
                    continue
                worker = threading.Thread(
                    target=execute_worker,
                    args=(request_id, method, params, preparation),
                    name=f"sylvia-gm-session-{request_id[:24]}",
                    daemon=True,
                )
                with workers_lock:
                    workers.add(worker)
                try:
                    worker.start()
                except BaseException:
                    preparation.close()
                    with workers_lock:
                        workers.discard(worker)
                    raise
                continue
            execute_request(request_id, method, params)
    finally:
        closing.set()
        if display_reads is not None:
            display_reads.close()
        if diagnostic_path is not None:
            with diagnostic_lock:
                _write_runtime_diagnostic(diagnostic_path, event="owner_stopped")
        if supervisor is not None:
            supervisor.shutdown()
        with workers_lock:
            active_workers = tuple(workers)
        for worker in active_workers:
            worker.join(timeout=5.0)
        if bus is not None:
            bus.close()


def main() -> int:
    """Serve the desktop's UTF-8 pipes independently of the Windows text encoding."""
    if len(sys.argv) > 1 and sys.argv[1] == "--ruleset-test-worker":
        from sylvia_gm.interfaces.ruleset_test_worker import main as test_worker_main
        return test_worker_main(sys.argv[2:])
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="strict")
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
        return run(sys.stdin, sys.stdout)
    except (AuthorityError, OwnerProtocolError, ValueError) as error:
        # Startup errors go to stderr because no request id exists yet. Never
        # include the launch envelope, credential, or authority path.
        print(f"Sylvia GM owner startup failed: {type(error).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
