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
from collections.abc import Callable, Mapping, Sequence, Set
from contextlib import ExitStack, nullcontext
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, BinaryIO, TextIO, cast

from sylvia_gm.agent._builder_session import (
    CampaignContentToolAuthority,
)
from sylvia_gm.agent._provider_resolver import (
    ConfiguredProviderResolver,
    ProviderResolver,
)
from sylvia_gm.agent._provider_session import ProviderSessionAdapter
from sylvia_gm.agent._session_loop import (
    CampaignSessionLoop,
    ProviderControl,
    SessionProviderInput,
)
from sylvia_gm.agent._stateless_mcp import ModelMechanicsTool
from sylvia_gm.application.authority import (
    AuthorityOwner,
)
from sylvia_gm.application.builder_session_migration import migrate_legacy_builder_chats
from sylvia_gm.application.bundled_campaign_plans import (
    bundled_plan,
    known_builtin_public_summary,
)
from sylvia_gm.application.bundled_packages import (
    BUNDLED_RIICHI_PACKAGES,
    bundled_campaign_content_catalog,
    bundled_rift_archive_for_closure,
    bundled_rift_authority,
    bundled_rift_participant_declaration,
    bundled_riichi_archive,
    bundled_srd_archive,
    bundled_srd_version,
)
from sylvia_gm.application.bundled_starter_characters import available_reusable_participants
from sylvia_gm.application.campaign_library_selection import (
    participant_catalog_payload,
)
from sylvia_gm.application.campaign_plan_sources import (
    CampaignPlanToolAuthority,
    campaign_plan_sources,
    validate_retained_campaign_plan,
)
from sylvia_gm.application.character_profiles import (
    character_profile_catalog,
)
from sylvia_gm.application.launch_compatibility import ARCHIVED_RULESET_IDS
from sylvia_gm.application.reusable_participants import (
    ReusableParticipant,
    compatible_reusable_participants,
)
from sylvia_gm.application.reusable_plans import (
    load_reusable_campaign_plan,
    reusable_campaign_plan_catalog,
)
from sylvia_gm.campaign.rift import (
    RIFT_RULESET_ID,
    materialize_rift_selection,
    validate_rift_party_handoff,
    validate_rift_template,
)
from sylvia_gm.config._resolver import ConfigError
from sylvia_gm.config.provider_settings import (
    clear_session_model_once_settings,
    provider_settings,
    require_configured_model,
    session_model_once_settings,
    set_default_model_settings,
    set_session_model_once_settings,
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
    reconcile_builder_source_purges,
)
from sylvia_gm.infrastructure.launch_handshake import derive_owner_response
from sylvia_gm.interfaces._display_reads import DisplayReads
from sylvia_gm.interfaces._local_diagnostics import LocalDiagnostics
from sylvia_gm.interfaces._session_reasoning import (
    reasoning_state,
    selected_model_info,
    validate_model_preferences,
)
from sylvia_gm.interfaces.commands import DispatchContext
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
    ProviderError,
    ProviderOutputLimitError,
    ProviderPausedError,
    ProviderRefusalError,
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
_MAX_ADMIN_AUDIT_VIEW_BYTES = 256 * 1024
_TEST_FULL_AUDIT_ENV = "SYLVIA_GM_TEST_FULL_AUDIT"


class OwnerProtocolError(ValueError):
    """A malformed or unsupported private owner-process message."""


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
    storage_root: Path, application_owner: AuthorityOwner, *, include_participants: bool = True,
) -> tuple[SafePayload, ...]:
    """Project exact content; setup can defer character construction until party selection."""

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
        participants = _reusable_participant_catalog(
            storage_root, application_owner, candidates=candidates,
            include_starters=include_participants,
            on_invalid=lambda artifact_id, error: record_invalid(
                "participant", artifact_id, error,
            ),
        )
        if include_participants:
            items.extend(
                dict(participant_catalog_payload(application_owner, participant))
                for participant in participants
            )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise CommandRejected(
            "Reusable participant library contains an invalid artifact",
            code="reusable_participant_invalid",
        ) from error
    # Portable built-in Plans own exact world snapshots. Offer those worlds before
    # installation; the Interview admits the selected snapshot through normal checks.
    public_plans = (
        [
        item
        for item in items
        if item.get(
        "kind") == "campaign_plan" and item.get("ruleset") and item.get("campaign_setting")]
    )
    for plan_item in public_plans:
        rule, world = plan_item["ruleset"], plan_item["campaign_setting"]
        choice = (
            next(
            (
            item
            for item in items
            if item.get("kind") == "ruleset" and item.get("id") == rule["id"]), None)
        )
        if choice is None:
            choice = (
                {
                "kind": "ruleset",
                **dict(
                rule), "title": rule["id"], "selection_id": rule["id"], "compatible_settings": ()}
            )
            installed = next((entry for candidate in candidates
                              for entry in candidate.package_lock.entries
                              if (entry.package_id, entry.version, entry.sha256) == (
                                  rule["id"], rule["version"], rule["content_sha256"],
                              )), None)
            if installed is not None:
                choice["title"] = application_owner.package_label(
                    installed.package_id, installed.version, installed.sha256,
                )
            items.append(choice)
        settings = tuple(choice.get("compatible_settings", ()))
        if not any(binding["id"] == world["id"] for binding in settings):
            choice["compatible_settings"] = (
                settings + ({**dict(world), "candidate_id": None, "ruleset": dict(rule)},)
            )
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
                    bundled_ruleset[
                    "id"], bundled_ruleset["version"], bundled_ruleset["content_sha256"],
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
    include_starters: bool = True,
) -> tuple[ReusableParticipant, ...]:
    """Load participants compatible with the bundled Severed Lands/Rift binding."""

    package, declaration = _rift_participant_authority()
    campaign_setting = bundled_rift_authority().setting
    discover = (
        available_reusable_participants if include_starters else compatible_reusable_participants
    )
    return discover(
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
    include_starters: bool = True,
) -> tuple[ReusableParticipant, ...]:
    """Validate saved characters even when setup defers built-in roster construction."""
    if not include_starters:
        library_root = storage_root / "participants"
        if not library_root.exists() or next(library_root.iterdir(), None) is None:
            return ()
    discover = (
        available_reusable_participants if include_starters else compatible_reusable_participants
    )
    found = {
        item.artifact_id: item for item in _rift_reusable_participants(
            storage_root, on_invalid=on_invalid, include_starters=include_starters,
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
                for item in discover(
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
    adventures = owner.campaign_adventures(campaign_id)
    if not adventures or adventures[0].plan_id != plan.plan_id:
        raise CommandRejected("Current adventure is not descended from the admitted handoff")
    plan = adventures[-1]
    admitted_text = plan.content.get("campaign_plan_markdown")
    if not isinstance(admitted_text, str) or not admitted_text.strip():
        raise CommandRejected("Admitted campaign plan content is unavailable")
    if len(admitted_text.encode("utf-8")) > _MAX_CAMPAIGN_PLAN_BYTES:
        raise CommandRejected("Admitted campaign plan exceeds the usability limit")
    structured = plan.content.get("campaign_plan")
    from sylvia_gm.domain.campaign_sequel import sequel_markdown, sequel_preparation

    continuation = sequel_preparation(plan.content)
    if continuation is not None and isinstance(structured, Mapping):
        # This application projection is not a change to the authored or accepted Plan bytes.
        structured = {**structured, "_app_adventure_continuation": continuation}

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
        retained = bundled_plan(plan_id)
        if retained is None:
            raise CommandRejected("The selected Rift Plan is unavailable")
        plan = retained.plan
        archive = retained.package_snapshot
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
        archive = saved.package_snapshot
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
        if archive is None:
            archive = bundled_rift_archive_for_closure(bindings["package_closure_sha256"])
            if archive is None:
                raise CommandRejected("The selected Plan's saved packages are unavailable")
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
        reusable_participants=available_reusable_participants(
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
    *, before_migration: Callable[[], None] | None = None,
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
        internal = supervisor.acquire_owner(
            database_path, owner_id, before_migration=before_migration,
        )
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


def _preinstall_bundled_riichi(bus: LocalCommandBus) -> None:
    """Add the shipped closure once, retaining installed content and same-version edits."""

    raw_candidates = bus.package_candidates()
    if not isinstance(raw_candidates, Sequence):
        raise RuntimeError("Package candidate projection is invalid")
    required = set(BUNDLED_RIICHI_PACKAGES)
    for candidate in cast(Sequence[PackageCandidate], raw_candidates):
        installed = {(entry.package_id, entry.version) for entry in candidate.package_lock.entries}
        if required <= installed:
            return
    archive = bundled_riichi_archive()
    bus.install_package(
        io.BytesIO(archive),
        install_id=f"install-bundled-riichi-{hashlib.sha256(archive).hexdigest()[:24]}",
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
    from .commands.registry import COMMAND_REGISTRY

    ctx = DispatchContext(
        application_owner=application_owner,
        bus=bus,
        gm_system_prompt=gm_system_prompt,
        method=method,
        model_choices=model_choices,
        owner_id=owner_id,
        provider_resolver=provider_resolver,
        reload_provider_resolver=reload_provider_resolver,
        session_loop=session_loop,
        storage_root=storage_root,
    )
    command = COMMAND_REGISTRY.get(method)
    if command is None or command.handler is None:
        raise OwnerProtocolError(f'Unsupported owner method: {method}')
    return command.handler(ctx, params)


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
    configured_resolver = provider_resolver
    owner_config_path: Path | None = None
    diagnostics: LocalDiagnostics | None = None
    display_reads: DisplayReads | None = None
    desktop_updates: Any = None
    desktop_drafts: Any = None
    update_installation: Any = None
    update_startup: Any = None
    update_start_confirmed = False

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
        from sylvia_gm.application.desktop_update.manifest import UpdateError
        from sylvia_gm.application.desktop_update.startup import owner_startup_context

        def validate_update_startup() -> None:
            nonlocal update_installation, update_startup
            update_installation, update_startup = owner_startup_context(launch)

        try:
            validate_update_startup()
            supervisor, bus, external_response, owner_id, storage_root = _launch(
                launch, before_migration=validate_update_startup,
            )
        except UpdateError as startup_error:
            if (
                startup_error.code == "update_recovery_required"
                and launch.get("desktop_update_context") is not None
            ):
                from sylvia_gm.application.desktop_update.recovery_service import run_recovery

                return run_recovery(launch, input_stream, output_stream)
            _write_message(output_stream, {
                "ok": False, "type": "update_recovery_required",
                "error": {"code": "update_recovery_required", "message": (
                    "The interrupted update must be reconciled before opening saved data."
                )},
            })
            return 2  # No authority acquisition, schema migration or provider work occurred.
        diagnostics = LocalDiagnostics(storage_root)
        owner_config_path = storage_root / "config.yaml"
        application_owner = supervisor.application_owner()
        application_owner.prepare_new_campaign_storage()
        _preinstall_bundled_srd(bus)
        _preinstall_bundled_riichi(bus)
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
            nonlocal desktop_updates, desktop_drafts, update_start_confirmed
            started = time.monotonic()
            error_code: str | None = None
            error_type: str | None = None
            try:
                if method == "desktop_update_confirm_startup":
                    if set(params) != {"host"} or update_startup is None:
                        raise OwnerProtocolError("No update startup is awaiting confirmation")
                    expected = {
                        "version": update_startup.version,
                        "target": update_startup.installation.target,
                        "distribution": update_startup.installation.distribution,
                    }
                    if params["host"] != expected:
                        raise OwnerProtocolError("The native startup context changed")
                    already_confirmed = update_start_confirmed
                    update_start_confirmed = update_startup.confirm()
                    if (
                        update_start_confirmed and not already_confirmed
                        and desktop_updates is not None
                    ):
                        desktop_updates.startup_completed()
                    result = {
                        "status": "confirmed" if update_start_confirmed else "waiting_for_helper",
                    }
                elif method in {"load_desktop_drafts", "save_desktop_drafts"}:
                    if desktop_drafts is None:
                        from sylvia_gm.application.desktop_update.drafts import DraftStore

                        desktop_drafts = DraftStore(storage_root)
                    if method == "load_desktop_drafts" and not params:
                        result = desktop_drafts.load()
                    elif method == "save_desktop_drafts" and set(params) == {"drafts"}:
                        result = desktop_drafts.save(params["drafts"])
                    else:
                        raise OwnerProtocolError("Unsupported draft storage request")
                elif method in {
                    "desktop_update_state", "desktop_update_check", "desktop_update_download",
                    "desktop_update_cancel", "desktop_update_defer",
                    "desktop_update_prepare", "desktop_update_handoff",
                }:
                    if desktop_updates is None:
                        from sylvia_gm.application.desktop_update.service import (
                            DesktopUpdateService,
                        )

                        desktop_updates = DesktopUpdateService(
                            storage_root, installation=update_installation,
                            backup_database=bus.backup_database,
                            reserved=lambda identity: (
                                not closing.is_set()
                                and supervisor.desktop_update_operation() == identity
                            ),
                            release=supervisor.abort_desktop_update,
                        )
                    result = desktop_updates.handle(method.removeprefix("desktop_update_"), params)
                elif method == "record_local_fault":
                    result = diagnostics.client_fault(params.get("category"), params.get("delta"))
                elif method == "preview_local_report":
                    result = diagnostics.preview(
                        kind=params.get("kind"), reproduction=params.get("reproduction", ""),
                        feedback=params.get("feedback", ""),
                    )
                elif method == "cancel_local_report":
                    diagnostics.cancel(_required_text(params, "preview_id"))
                    result = {"status": "cancelled"}
                elif method == "save_local_report":
                    destination = Path(_required_text(params, "destination_path"))
                    if not destination.is_absolute() or not destination.name:
                        raise OwnerProtocolError("Choose a local report file.")
                    result = diagnostics.save(_required_text(params, "preview_id"), destination)
                else:
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
                    "Sylvia GM owner command failed: internal_error",
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
            diagnostic_id = diagnostics.record(
                "command_completed", operation=method,
                outcome="error" if error_code is not None else "ok",
                error_code=error_code, error_type=error_type,
                duration_ms=(time.monotonic() - started) * 1000,
            )
            if error_code is not None:
                reply["error"]["diagnostic_id"] = diagnostic_id
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
            if update_startup is not None and not update_start_confirmed and method not in {
                "desktop_update_confirm_startup", "desktop_update_state", "shutdown",
                "load_desktop_drafts", "save_desktop_drafts", "campaign_index",
                "builder_chat_index", "provider_settings", "model_choices",
                "display_snapshot", "player_projection", "player_projection_page",
                "campaign_panels", "cancel_display_load", "record_local_fault",
            }:
                write_reply({"id": request_id, "ok": False, "error": {
                    "code": "update_startup_unconfirmed",
                    "message": "Wait for the updated application to confirm startup.",
                }})
                continue
            if supervisor.desktop_update_operation() is not None and method not in {
                "shutdown", "desktop_update_state", "desktop_update_abort",
                "desktop_update_reserve", "desktop_update_shutdown",
                "desktop_update_prepare", "desktop_update_defer", "desktop_update_handoff",
                "load_desktop_drafts", "save_desktop_drafts",
                "cancel_display_load", "campaign_index", "builder_chat_index",
            }:
                write_reply({"id": request_id, "ok": False, "error": {
                    "code": "update_in_progress",
                    "message": (
                        "Update preparation has reserved this app. "
                        "Finish or cancel it before changing saved work."
                    ),
                }})
                continue
            if method in {
                "desktop_update_reserve", "desktop_update_abort", "desktop_update_shutdown",
            }:
                try:
                    from sylvia_gm.application.desktop_update.storage import operation_id

                    if set(params) != {"operation_id"}:
                        raise OwnerProtocolError("Unsupported update lifecycle request")
                    identity = operation_id(params["operation_id"])
                    if method == "desktop_update_abort":
                        if desktop_updates is not None:
                            desktop_updates.abort_preparation(identity)
                        supervisor.abort_desktop_update(identity)
                        result = {"status": "aborted"}
                    else:
                        with workers_lock:
                            if (
                                workers or not display_reads.idle()
                                or (desktop_updates is not None and desktop_updates.preparing())
                            ):
                                raise CommandRejected(
                                    "Finish admitted work and loading before updating",
                                    code="update_busy",
                                )
                            if method == "desktop_update_reserve":
                                supervisor.begin_desktop_update(identity)
                                result = {"status": "reserved"}
                            elif supervisor.desktop_update_operation() == identity:
                                if desktop_updates is not None:
                                    desktop_updates.arm_shutdown(identity)
                                result = {"status": "closed"}
                            else:
                                raise CommandRejected(
                                    "This update reservation is stale",
                                    code="stale_update_operation",
                                )
                    write_reply({"id": request_id, "ok": True, "result": result})
                    if result["status"] == "closed":
                        return 0  # finally closes an idle owner; no provider cancellation/replay.
                except (AuthorityError, OwnerProtocolError, ValueError) as error:
                    write_reply({"id": request_id, "ok": False, "error": {
                        "code": getattr(error, "code", "command_rejected"), "message": str(error),
                    }})
                continue
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
                "ready_campaign_catalog",
                "start_ready_campaign",
                "customize_ready_campaign",
            }:
                preparation = ExitStack()
                try:
                    session_id = _generation_session(method, params)
                    if session_id is not None:
                        preparation.enter_context(session_loop.provider_request_work(session_id))
                except (AuthorityError, OwnerProtocolError, ValueError) as error:
                    preparation.close()
                    diagnostics.record(
                        "request_rejected", operation=method, outcome="error",
                        error_code="command_rejected", error_type=type(error).__name__,
                    )
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
    except BaseException:
        if diagnostics is not None:
            diagnostics.record("owner_failed", outcome="error", error_code="internal_error")
        raise
    finally:
        closing.set()
        if desktop_updates is not None:
            desktop_updates.close()
        if display_reads is not None:
            display_reads.close()
        if supervisor is not None:
            supervisor.shutdown()
        with workers_lock:
            active_workers = tuple(workers)
        for worker in active_workers:
            worker.join(timeout=5.0)
        if bus is not None:
            bus.close()
        if diagnostics is not None:
            diagnostics.close()


def main() -> int:
    """Serve the desktop's UTF-8 pipes independently of the Windows text encoding."""
    if len(sys.argv) > 1 and sys.argv[1] == "--ruleset-test-worker":
        from sylvia_gm.interfaces.ruleset_test_worker import main as test_worker_main
        return test_worker_main(sys.argv[2:])
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="strict")
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
        return run(sys.stdin, sys.stdout)
    except Exception:
        # Startup errors go to stderr because no request id exists yet. Never
        # include the launch envelope, credential, or authority path.
        print("Sylvia GM owner startup failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    # Topic handlers must share the protocol's exception identity under python -m.
    sys.modules["sylvia_gm.interfaces.owner_process"] = sys.modules[__name__]
    raise SystemExit(main())
