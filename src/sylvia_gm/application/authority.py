"""Application owner, catalog, admission, and action orchestration for F1."""

from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
import threading
import uuid
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import BinaryIO, TypeVar, cast

import yaml

from sylvia_gm.application.actor_decisions import (
    ActorDecisionRequest,
    project_actor_decision,
    public_actor_observation,
)
from sylvia_gm.application.campaign_inventory import (
    generic_inventory_surface,
    inventory_holdings,
    prepare_inventory_payload,
)
from sylvia_gm.application.decision_scope import (
    DecisionScopeProjectionError,
    player_viewer_ids,
    project_campaign_preparation_scope,
    project_model_decision_scope,
    project_player_control_scope,
)
from sylvia_gm.application.interview_content import resolve_interview_content
from sylvia_gm.application.knowledge_search import (
    KnowledgeReference,
    bounded_search_page,
    knowledge_terms,
    rank_references,
)
from sylvia_gm.application.launch_compatibility import player_ui_surfaces, require_active_ruleset
from sylvia_gm.application.map_segment_preview import segment_preview
from sylvia_gm.application.network_graph import (
    NetworkGraphError,
    bind_physical_network_surfaces,
    project_network_graph,
    resolve_network_action_parameters,
    visible_physical_entity_ids,
)
from sylvia_gm.application.package_library import (
    MigrationFunction,
    declaration_admitted,
    dependency_only_manifest_rebind,
    install_package_archive,
    project_surfaces,
    projection_bytes,
    run_migration_outside_sqlite,
    state_predicates_match,
)
from sylvia_gm.application.package_materialization import (
    NormalizedPackageDraft,
    materialize_package_archive,
)
from sylvia_gm.application.package_runtime import (
    PACKAGE_RUNTIME_BYTE_LIMIT,
    PACKAGE_RUNTIME_PATH,
    PackageRuntimeError,
    materialize_package_mechanic,
    materialize_player_action_checker,
    materialize_player_action_previewer,
    package_model_decision_boundary,
    package_tool_decision_title,
    package_tool_description,
    package_tool_model_behavior,
    package_tool_player_controls,
)
from sylvia_gm.application.piece_table import (
    PieceTableError,
    project_piece_table,
    resolve_piece_action_parameters,
)
from sylvia_gm.application.player_actions import (
    PLAYER_ACTION_ORIGIN,
    PlayerActionChecker,
    PlayerActionPreviewer,
    PlayerControls,
    player_action_origins,
    player_action_records,
    player_action_request_matches,
    player_control_origin,
    resolve_inventory_action_parameters,
    resolve_party_action_parameters,
    retain_player_action_origin,
    validate_player_control,
)
from sylvia_gm.application.reference_text import (
    REFERENCE_CONTENT_BYTE_LIMIT,
    REFERENCE_REPLY_BYTE_LIMIT,
    matching_text_excerpts,
    text_excerpt,
)
from sylvia_gm.application.result_cards import project_result_card
from sylvia_gm.application.schema_admission import (
    validate_json_value,
    validate_parameters,
    validate_references,
)
from sylvia_gm.application.session import SessionApplication
from sylvia_gm.application.tactical_grid import (
    TacticalGridError,
    coordinate_actor_reference,
    player_known_spatial_scene,
    project_coordinate_map,
    project_tactical_grid,
    resolve_surface_source,
    resolve_tactical_action_parameters,
)
from sylvia_gm.domain.authority import (
    ActionReceipt,
    ActionRecord,
    ActionStatus,
    AwaitingChoiceReceipt,
    CampaignConfirmation,
    CampaignConstructionBasis,
    CampaignConstructionBegun,
    CampaignConstructionCompleted,
    CampaignContinuityCommand,
    CampaignContinuityReceipt,
    CampaignDisplaySnapshot,
    CampaignExportResult,
    CampaignLifecycleResult,
    CampaignPurgeResult,
    CampaignRecord,
    CampaignRecoveryResult,
    CampaignSnapshot,
    CampaignState,
    CanonicalEvent,
    CanonicalEventType,
    CanonicalReplay,
    FrozenJsonValue,
    FrozenPayload,
    GuidedCampaignDraft,
    GuidedCreationSubflow,
    InspectorTraceSink,
    JsonValue,
    LifecycleState,
    ManualRetryResult,
    Mechanic,
    MechanicContext,
    MechanicOutcome,
    MechanicsChoiceAnswer,
    MechanicsChoiceOption,
    MechanicsChoiceRequest,
    MechanicsChoiceResolution,
    ModelDecisionBoundary,
    ModelDecisionOwner,
    ModelToolBehavior,
    NarrationInterruption,
    NarrationStream,
    PackageDependency,
    PackageLock,
    PackageLockEntry,
    PackageRole,
    Participant,
    ParticipantArtifact,
    ParticipantMutation,
    ParticipantValidationStatus,
    PendingMechanicsAction,
    PlayerProjection,
    PlayerProjectionKind,
    ProseChannel,
    RetryNarrationInput,
    SafePayload,
    StateMutation,
    StateValue,
    TurnStatus,
    WriterRole,
    canonical_payload_is_safe,
    lifecycle_writer_is_legal,
    mechanic_outcome_is_safe,
    owned_json_value,
    owned_payload,
    response_blocks_text,
    thaw_json_value,
    validate_authority_identity,
    validate_response_blocks,
)
from sylvia_gm.domain.campaign_ending import (
    check_ending_progress,
    ending_progress_id,
    intended_endings,
)
from sylvia_gm.domain.campaign_plan import CAMPAIGN_PLAN_FORMAT_VERSION
from sylvia_gm.domain.errors import (
    CommandRejected,
    IdempotencyConflict,
    RulesetPreconditionRejected,
    StaleRevision,
    WrongOwner,
    WrongWriter,
)
from sylvia_gm.domain.interview_contribution import (
    INTERVIEW_CONTRIBUTION_BYTE_LIMIT,
    INTERVIEW_CONTRIBUTION_PATH,
    InterviewContribution,
    parse_interview_contribution_bytes,
    validate_interview_contribution_answers,
)
from sylvia_gm.domain.inventory import (
    INVENTORY_ENTITY_KIND,
    INVENTORY_RECORD_ID,
)
from sylvia_gm.domain.model_capability import (
    EMPTY_MODEL_CAPABILITY_REGISTRY,
    ModelCapabilityRegistry,
    ModelGuidanceDecision,
    model_guidance_payload_sha256,
)
from sylvia_gm.domain.package_library import (
    CAMPAIGN_SETTING_KNOWLEDGE_BYTE_LIMIT,
    CAMPAIGN_SETTING_KNOWLEDGE_PATH,
    GAMEPLAY_SUPPORT_PATH,
    ActivationCommand,
    ActivationResult,
    PackageBlobReadReceipt,
    PackageCandidate,
    PackageLockRecord,
    PackageStagingRecord,
    PackageSurfaceProjection,
    ParticipantArtifactDeclaration,
    ReclamationResult,
    StatePredicate,
    ToolDeclaration,
    activation_payload_sha256,
    campaign_setting_entries,
    canonical_json_bytes,
    parse_campaign_setting_knowledge_bytes,
    parse_canonical_json_bytes,
    parse_gameplay_support_bytes,
)
from sylvia_gm.domain.panel_layout import (
    PANEL_LAYOUT_BYTE_LIMIT,
    PANEL_LAYOUT_PATH,
    parse_panel_layout,
)
from sylvia_gm.domain.provider_records import ProviderPrivateRecord
from sylvia_gm.domain.session import (
    BUILDER_SKILLS,
    BuilderChatRecord,
    BuilderDMHandoff,
    BuilderEvidence,
    CampaignPlan,
    ChildEvidence,
    EvidenceSourceKind,
    PlanAcceptanceStatus,
    PlanValidationOutcome,
    SessionActivityBasis,
    SessionContext,
    SessionHistoryPage,
    SessionMessage,
    SessionMessageKind,
    SessionRecord,
    SessionStatus,
    builder_dm_handoff_payload_sha256,
    campaign_plan_payload_sha256,
)
from sylvia_gm.infrastructure import campaign_archive
from sylvia_gm.infrastructure.package_archive import (
    PACKAGE_MEMBER_BYTE_LIMIT,
    PACKAGE_MEMBER_COUNT_LIMIT,
)
from sylvia_gm.infrastructure.sqlite_authority import (
    CampaignIndexRecord,
    SQLiteAuthorityStore,
    _open_for_application_owner,
)

__all__ = ("mechanic_outcome_is_safe",)

_Snapshot = TypeVar("_Snapshot", CampaignSnapshot, CampaignDisplaySnapshot)

_RULESET_GM_OPERATING_GUIDE_PATH = "prompts/gm-operating-guide.md"
_RULESET_GM_OPERATING_GUIDE_BYTE_LIMIT = 64 * 1024
_CAMPAIGN_KNOWLEDGE_SOURCES = frozenset(
    {"campaign_state", "campaign_plan", "campaign_setting", "participant", "ruleset"}
)
_PRIVATE_PREPARATION_HEADINGS = frozenset(
    {"encounter", "encounters", "hook", "hooks", "secret", "secrets"}
)


def _markdown_sections(value: str) -> tuple[tuple[str, str, str], ...]:
    """Return independently addressable Markdown heading sections."""

    lines = value.splitlines()
    headings: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match is not None:
            headings.append((index, len(match.group(1)), match.group(2).strip()))
    sections: list[tuple[str, str, str]] = []
    ends = [len(lines)] * len(headings)
    parents: list[int] = []
    for index, (start, level, _heading) in enumerate(headings):
        while parents and headings[parents[-1]][1] >= level:
            ends[parents.pop()] = start
        parents.append(index)
    for (start, _level, heading), end in zip(headings, ends, strict=True):
        slug = re.sub(r"[^a-z0-9]+", "-", heading.casefold()).strip("-")
        sections.append((heading, slug, "\n".join(lines[start:end]).strip()))
    return tuple(sections)


def _select_markdown_section(value: str, fragment: str) -> tuple[str, str]:
    """Resolve one unique Markdown heading fragment or reject the exact read."""

    normalized = re.sub(r"[^a-z0-9]+", "-", fragment.casefold()).strip("-")
    matches = tuple(
        (heading, content)
        for heading, slug, content in _markdown_sections(value)
        if slug == normalized
    )
    if len(matches) != 1:
        raise CommandRejected("Campaign reference section is unavailable")
    return matches[0]


def _markdown_section_references(
    value: str,
    *,
    path: str,
    document_private: bool,
) -> tuple[tuple[dict[str, str], ...], int]:
    """Bound heading metadata independently of the admitted source and return its total count."""

    sections = _markdown_sections(value)
    references: list[dict[str, str]] = []
    counts = Counter(slug for _, slug, _ in sections)
    total = 0
    for heading, slug, content in sections:
        if len(sections) > 1 and content == value.strip():
            continue
        total += 1
        if len(references) >= 100 or len(slug) > 200 or counts[slug] != 1:
            continue
        heading_key = heading.casefold().rstrip("s")
        private = document_private or heading_key in {
            item.rstrip("s") for item in _PRIVATE_PREPARATION_HEADINGS
        }
        reference = {
            "heading": heading[:200],
            "path": f"{path}#{slug}",
            "visibility": ("gm_private_preparation" if private else "campaign_reference"),
        }
        if len(canonical_json_bytes([*references, reference])) <= 8 * 1024:
            references.append(reference)
    return tuple(references), total


def _read_structured_campaign_plan(root: Mapping[str, object], path: str) -> object:
    """Read an exact section; an act read includes its linked completion requirements."""

    current: object = root
    segments = [item for item in path.split("/") if item]
    for segment in segments:
        if isinstance(current, Mapping):
            if segment not in current:
                raise CommandRejected("Campaign Plan reference path is unavailable")
            current = current[segment]
            continue
        if isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
            selected = next(
                (
                    item
                    for item in current
                    if isinstance(item, Mapping) and item.get("id") == segment
                ),
                None,
            )
            if selected is None:
                raise CommandRejected("Campaign Plan reference path is unavailable")
            current = selected
            continue
        raise CommandRejected("Campaign Plan reference path is unavailable")
    if len(segments) == 2 and segments[0] == "structure" and isinstance(current, Mapping):
        declared = root.get("objectives")
        required_ids = current.get("objectives")
        if (
            not isinstance(declared, Sequence)
            or isinstance(declared, (str, bytes, bytearray))
            or not isinstance(required_ids, Sequence)
            or isinstance(required_ids, (str, bytes, bytearray))
        ):
            raise CommandRejected("Campaign act completion references are unavailable")
        objectives = {
            item["id"]: item
            for item in declared
            if isinstance(item, Mapping) and isinstance(item.get("id"), str)
        }
        ending = root.get("ending")
        structure = root.get("structure")
        final_act = isinstance(structure, Sequence) and bool(structure) and current == structure[-1]
        ongoing_final = (
            final_act and isinstance(ending, Mapping) and ending.get("mode") == "ongoing"
        )
        if (not required_ids and not ongoing_final) or any(
            not isinstance(item, str) or item not in objectives for item in required_ids
        ):
            raise CommandRejected("Campaign act completion references are unavailable")
        return {
            "act": current,
            "required_objectives": [objectives[item] for item in required_ids],
            "completion_rule": (
                "This final act is ongoing. Do not resolve it or invent a campaign ending."
                if ongoing_final else
                "All of this act's linked objectives are required. All completion conditions "
                "of each objective must be met by played facts, including its closing event; "
                "choose one alternative only where a condition explicitly permits it. "
                "Side work neither replaces a required objective nor adds another prerequisite. "
                "An authored failure ending may resolve the act; deferring or retiring work "
                "alone does not mean that the act was completed."
            ),
            **({"campaign_ending": ending} if final_act and isinstance(ending, Mapping) else {}),
        }
    return current


def _state_reference(state: Mapping[str, JsonValue], reference: Mapping[str, object]) -> object:
    """Read one Ruleset-declared namespaced state path without game-specific knowledge."""

    namespace = str(reference["namespace"])
    key = str(reference["key"])
    exact = f"{namespace}.{key}"
    if exact in state:
        return state[exact]
    root, separator, remainder = key.partition(".")
    current: object = state.get(f"{namespace}.{root}")
    if not separator:
        return current
    for field in remainder.split("."):
        if not isinstance(current, Mapping) or field not in current:
            return None
        current = current[field]
    return current


_GUIDED_CAMPAIGN_STEPS = (
    "ruleset",
    "setting",
    "plan",
    "ruleset_contribution",
    "setting_contribution",
    "plan_contribution",
    "name",
    "participants",
    "boundaries_preferences",
    "specific_requests",
    "review",
)
_GUIDED_CAMPAIGN_QUESTIONS = {
    "ruleset": (
        "Choose a Ruleset for this campaign, or choose No Ruleset for narrative-only play."
    ),
    "ruleset_contribution": "Answer the selected Ruleset's campaign setup questions.",
    "setting": "Choose a compatible Campaign Setting.",
    "setting_contribution": "Answer the selected Campaign Setting's setup questions.",
    "plan": "Choose a compatible Campaign Plan.",
    "plan_contribution": "Answer the selected Campaign Plan's setup questions.",
    "name": "Review or edit the suggested campaign name.",
    "participants": "Choose or create the participants required by this campaign.",
    "boundaries_preferences": "State any hard Boundaries and softer Preferences.",
    "specific_requests": "Add any Specific Requests, or choose No additional requests.",
    "review": "Review the spoiler-free campaign summary before choosing Ready to Begin.",
}
_GUIDED_SUBFLOW_STEPS = {
    "ruleset": "ruleset",
    "setting": "setting",
    "plan": "plan",
    "player_character": "participants",
    "companion": "participants",
    "campaign_customization": "specific_requests",
    "adaptive_interpretation": "",
}
_GUIDED_SUBFLOW_SKILLS = {
    "ruleset": "BUILD RULESET",
    "setting": "BUILD CAMPAIGN SETTING",
    "plan": "BUILD CAMPAIGN PLAN",
    "player_character": "BUILD CAMPAIGN PLAN",
    "companion": "BUILD CAMPAIGN PLAN",
    "campaign_customization": "BUILD CAMPAIGN PLAN",
    "adaptive_interpretation": "BUILD CAMPAIGN PLAN",
}
_MAX_GUIDED_VISIBLE_ANSWER_CHARS = 131_072
_GUIDED_CONTRIBUTION_STEPS = {
    "ruleset_contribution": ("ruleset", "ruleset"),
    "setting_contribution": ("setting", "campaign_setting"),
    "plan_contribution": ("plan", "campaign_plan"),
}


@dataclass(frozen=True)
class CatalogTool:
    """One exact package-bound mechanic; execution receives no persistence handle."""

    package_id: str
    package_version: str
    package_sha256: str
    name: str
    capability: str
    input_schema: Mapping[str, object]
    execute: Mechanic
    description: str = ""
    model_behavior: ModelToolBehavior = ModelToolBehavior.OPTIONAL
    model_behavior_state_predicates: tuple[StatePredicate, ...] = ()
    player_controls: PlayerControls = PlayerControls()
    check_player_action: PlayerActionChecker | None = None
    preview_player_action: PlayerActionPreviewer | None = None
    decision_title: str = ""

    def __post_init__(self) -> None:
        validate_authority_identity(self.package_id, "Catalog package identity")
        validate_authority_identity(self.package_version, "Catalog package version")
        validate_authority_identity(self.package_sha256, "Catalog package SHA-256", sha256=True)
        validate_authority_identity(self.name, "Catalog tool name")
        validate_authority_identity(self.capability, "Catalog capability")


@dataclass(frozen=True)
class CampaignCreate:
    display_name: str
    writer: WriterRole
    package_lock: PackageLock
    turn_id: str
    participants: tuple[Participant, ...] = ()
    initial_state: tuple[StateValue, ...] = ()
    lifecycle: LifecycleState = LifecycleState.ACTIVE
    campaign_id: str | None = None
    source_handoff_id: str | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class CampaignSessionIndexEntry:
    """One persistent top-level agent chat shown within a campaign."""

    session_id: str
    role: str
    status: str
    provider_id: str
    model_id: str
    selected_skill: str | None = None
    model_guidance: ModelGuidanceDecision | None = None


@dataclass(frozen=True)
class CampaignIndexEntry:
    """Immutable product-safe campaign rail projection."""

    campaign_id: str
    display_name: str
    lifecycle: str
    writer: str
    revision: int
    ruleset_summary: str
    package_summary: str
    package_count: int
    playable: bool
    availability: str
    ruleset_id: str | None = None
    ruleset_label: str = "No rules"
    campaign_setting_id: str | None = None
    campaign_setting_label: str = "No campaign setting"
    session_activity_ordinal: int = 0
    turn_id: str | None = None
    turn_status: str | None = None
    session_id: str | None = None
    session_status: str | None = None
    provider_id: str | None = None
    model_id: str | None = None
    sessions: tuple[CampaignSessionIndexEntry, ...] = ()


@dataclass(frozen=True)
class ActionCommand:
    campaign_id: str
    action_id: str
    idempotency_key: str
    owner_id: str
    writer: WriterRole
    expected_revision: int
    turn_id: str
    package_id: str
    tool_name: str
    parameters: SafePayload
    initiating_session_id: str | None = None


@dataclass(frozen=True)
class ResolveMechanicsChoiceCommand:
    campaign_id: str
    action_id: str
    resolution_id: str
    owner_id: str
    expected_revision: int
    choice_id: str
    option_id: str
    session_id: str | None = None
    choice_sequence: int = 0


@dataclass(frozen=True)
class _CampaignCreationSnapshot:
    package_lock: PackageLock
    participants: tuple[Participant, ...]
    initial_state: tuple[StateValue, ...]


@dataclass
class _NarrationAttempt:
    campaign_id: str
    attempt_id: str
    turn_id: str
    cancel: Callable[[], None]
    last_action_id: str | None
    channel: ProseChannel | None = None
    buffer: str = ""
    response_blocks: tuple[SafePayload, ...] | None = None
    ordinal: int = 0
    disconnect_deadline: float | None = None
    terminal: bool = False
    cancelled: bool = False
    interruption_id: str | None = None
    retry_basis_id: str | None = None
    work_registered: bool = False


def audit_export_save_error(error: OSError) -> str:
    """Explain recognized storage failures without exposing paths or exception payloads."""

    windows_error = getattr(error, "winerror", None)
    if error.errno == errno.ENOSPC or windows_error in {39, 112}:
        return (
            "Audit export could not be saved: not enough space. Free space or choose another drive."
        )
    if error.errno in {errno.EACCES, errno.EPERM, errno.EROFS} or windows_error in {5, 32, 33}:
        return (
            "Audit export could not be saved: the destination is not writable. "
            "Choose a writable folder or close applications using the destination file."
        )
    if error.errno in {errno.ENOENT, errno.ENOTDIR, errno.ENODEV} or windows_error in {3, 15, 21}:
        return (
            "Audit export could not be saved: the destination is unavailable. "
            "Choose an available folder or drive."
        )
    return "Audit export file could not be saved. Try another destination."


class AuthorityOwner:
    """The sole application-layer owner of campaign-changing authority."""

    def __init__(
        self,
        store: SQLiteAuthorityStore,
        owner_id: str,
        *,
        disconnect_grace_seconds: float | None = None,
        model_capability_registry: ModelCapabilityRegistry | None = None,
        content_storage_root: Path | None = None,
    ) -> None:
        validate_authority_identity(owner_id, "Application owner identity")
        if disconnect_grace_seconds is not None and (
            not isinstance(disconnect_grace_seconds, (int, float))
            or isinstance(disconnect_grace_seconds, bool)
            or not math.isfinite(disconnect_grace_seconds)
            or disconnect_grace_seconds <= 0
        ):
            raise ValueError("Disconnect grace must be positive and finite")
        self._store = store
        self._content_storage_root = content_storage_root
        self._owner_id = owner_id
        self._owner_session_id = f"owner-session-{uuid.uuid4()}"
        self._disconnect_grace_seconds = (
            None if disconnect_grace_seconds is None else float(disconnect_grace_seconds)
        )
        self._model_capability_registry = (
            EMPTY_MODEL_CAPABILITY_REGISTRY
            if model_capability_registry is None
            else model_capability_registry
        )
        self._tools: dict[tuple[str, str, str, str], CatalogTool] = {}
        self._runtime_payloads: dict[tuple[str, str, str], bytes] = {}
        self._tools_lock = threading.RLock()
        self._lanes: dict[str, threading.RLock] = {}
        self._lanes_lock = threading.Lock()
        self._narration_lock = threading.RLock()
        self._attempts: dict[tuple[str, str], _NarrationAttempt] = {}
        self._pending_tools: dict[tuple[str, str], PlayerProjection] = {}
        self._inspector_trace: list[object] = []
        self._mechanics_diagnostics: list[SafePayload] = []
        self._admin_full_audit_enabled = False
        self._lifecycle_condition = threading.Condition()
        self._lifecycle = "open"
        self._active_operations = 0
        self._work_condition = threading.Condition()
        self._campaign_work_counts: dict[str, int] = {}
        self._direct_action_campaigns: set[str] = set()
        self._campaign_transitions: set[str] = set()
        self._sessions = SessionApplication(store)

    @classmethod
    def open(
        cls,
        database_path: Path,
        owner_id: str,
        *,
        disconnect_grace_seconds: float | None = None,
        model_capability_registry: ModelCapabilityRegistry | None = None,
    ) -> AuthorityOwner:
        validate_authority_identity(owner_id, "Application owner identity")
        return cls(
            _open_for_application_owner(database_path),
            owner_id,
            disconnect_grace_seconds=disconnect_grace_seconds,
            model_capability_registry=model_capability_registry,
            content_storage_root=database_path.resolve().parent,
        )

    def model_guidance(
        self,
        *,
        campaign_id: str,
        model_id: str,
        override_accepted: bool = False,
    ) -> ModelGuidanceDecision:
        """Assess an exact selected model against the campaign's primary ruleset."""

        with self._operation():
            validate_authority_identity(campaign_id, "Campaign identity")
            lock = self._store.current_package_lock(campaign_id)
            return self._model_guidance_for_lock(
                lock_id=lock.lock_id,
                model_id=model_id,
                override_accepted=override_accepted,
            )

    def _model_guidance_for_lock(
        self,
        *,
        lock_id: str,
        model_id: str,
        override_accepted: bool,
    ) -> ModelGuidanceDecision:
        manifest = self._store.primary_package_manifest(lock_id)
        minimum = None if manifest is None else manifest.minimum_model_capability_tier
        return self._model_capability_registry.assess(
            model_id=model_id,
            minimum_tier_id=minimum,
            override_accepted=override_accepted,
        )

    def register_tool(self, tool: CatalogTool) -> None:
        with self._operation():
            self._register_tool(tool)

    def _register_tool(self, tool: CatalogTool) -> None:
        captured_schema: Mapping[str, object] = owned_payload(tool.input_schema)
        declaration = self._store.declared_package_tool(
            tool.package_id,
            tool.package_version,
            tool.package_sha256,
            tool.name,
        )
        if (
            declaration is None
            or declaration.capability != tool.capability
            or canonical_json_bytes(declaration.input_schema)
            != canonical_json_bytes(captured_schema)
        ):
            raise CommandRejected(
                "Catalog tool schema or declaration does not match the published package",
                code="catalog_declaration_mismatch",
            )
        identity = (
            tool.package_id,
            tool.package_version,
            tool.package_sha256,
            tool.name,
        )
        with self._tools_lock:
            if identity in self._tools:
                raise ValueError("Catalog tool identity is already registered")
            validate_parameters({}, _schema_without_required(captured_schema))
            self._tools[identity] = CatalogTool(
                package_id=tool.package_id,
                package_version=tool.package_version,
                package_sha256=tool.package_sha256,
                name=tool.name,
                capability=tool.capability,
                input_schema=captured_schema,
                execute=tool.execute,
                description=tool.description,
                model_behavior=tool.model_behavior,
                model_behavior_state_predicates=tool.model_behavior_state_predicates,
                player_controls=tool.player_controls,
                check_player_action=tool.check_player_action,
                preview_player_action=tool.preview_player_action,
            )

    def _package_tool(
        self,
        package: PackageLockEntry,
        declaration: object,
    ) -> CatalogTool | None:
        """Return a registered tool or materialize it from exact admitted bytes."""

        from sylvia_gm.domain.package_library import ToolDeclaration

        if not isinstance(declaration, ToolDeclaration):
            raise TypeError("Package declaration has an unexpected type")
        identity = (
            package.package_id,
            package.version,
            package.sha256,
            declaration.name,
        )
        with self._tools_lock:
            existing = self._tools.get(identity)
            if existing is not None:
                return existing
            runtime_identity = (package.package_id, package.version, package.sha256)
            runtime_bytes = self._runtime_payloads.get(runtime_identity)
            if runtime_bytes is None:
                runtime_bytes = self._store.package_file_bytes(
                    package.package_id,
                    package.version,
                    package.sha256,
                    PACKAGE_RUNTIME_PATH,
                    maximum_bytes=PACKAGE_RUNTIME_BYTE_LIMIT,
                )
                if runtime_bytes is not None:
                    # Package identities are immutable. Reuse verified bytes for
                    # sibling tools; changed content has a distinct exact identity.
                    if len(self._runtime_payloads) >= 16:
                        self._runtime_payloads.pop(next(iter(self._runtime_payloads)))
                    self._runtime_payloads[runtime_identity] = runtime_bytes
            if runtime_bytes is None:
                return None
            try:
                mechanic = materialize_package_mechanic(
                    package_id=package.package_id,
                    package_version=package.version,
                    package_sha256=package.sha256,
                    declaration=declaration,
                    runtime_bytes=runtime_bytes,
                )
                description = package_tool_description(
                    package_id=package.package_id,
                    declaration=declaration,
                    runtime_bytes=runtime_bytes,
                )
                model_behavior, model_behavior_state_predicates = package_tool_model_behavior(
                    package_id=package.package_id,
                    declaration=declaration,
                    runtime_bytes=runtime_bytes,
                )
                decision_title = package_tool_decision_title(
                    package_id=package.package_id, declaration=declaration,
                    runtime_bytes=runtime_bytes,
                )
                player_controls = package_tool_player_controls(
                    package_id=package.package_id,
                    declaration=declaration,
                    runtime_bytes=runtime_bytes,
                )
                check_player_action = materialize_player_action_checker(
                    package_id=package.package_id, declaration=declaration,
                    runtime_bytes=runtime_bytes,
                )
                preview_player_action = materialize_player_action_previewer(
                    package_id=package.package_id, declaration=declaration,
                    runtime_bytes=runtime_bytes,
                )
            except PackageRuntimeError:
                return None
            captured_schema: Mapping[str, object] = owned_payload(declaration.input_schema)
            validate_parameters({}, _schema_without_required(captured_schema))
            materialized = CatalogTool(
                package_id=package.package_id,
                package_version=package.version,
                package_sha256=package.sha256,
                name=declaration.name,
                capability=declaration.capability,
                input_schema=captured_schema,
                execute=mechanic,
                description=description,
                decision_title=decision_title,
                model_behavior=model_behavior,
                model_behavior_state_predicates=model_behavior_state_predicates,
                player_controls=player_controls,
                check_player_action=check_player_action,
                preview_player_action=preview_player_action,
            )
            self._tools[identity] = materialized
            return materialized

    def create_agent_session(
        self,
        *,
        campaign_id: str,
        role: WriterRole,
        provider_id: str,
        model_id: str,
        session_id: str | None = None,
        parent_session_id: str | None = None,
        selected_skill: str | None = None,
    ) -> SessionRecord:
        with self._operation(), self._campaign_work(campaign_id):
            return self._sessions.create_agent_session(
                campaign_id=campaign_id,
                role=role,
                provider_id=provider_id,
                model_id=model_id,
                session_id=session_id,
                parent_session_id=parent_session_id,
                selected_skill=selected_skill,
            )

    def create_builder_chat(
        self,
        *,
        provider_id: str,
        model_id: str,
        display_name: str = "New chat",
        session_id: str | None = None,
    ) -> BuilderChatRecord:
        with self._operation():
            return self._sessions.create_builder_chat(
                provider_id=provider_id,
                model_id=model_id,
                display_name=display_name,
                session_id=session_id,
            )

    def builder_chat_index(self) -> tuple[BuilderChatRecord, ...]:
        with self._operation():
            return self._sessions.builder_chat_index()

    def legacy_builder_contexts(self) -> tuple[SessionContext, ...]:
        """Read the old reusable-content children for the owner-startup migration."""
        with self._operation():
            return self._store.legacy_builder_contexts()

    def detach_legacy_builder_chat(
        self, *, session_id: str, expected_message_count: int,
        launch_context: SafePayload, display_name: str,
    ) -> None:
        """Retain a former content child as an ordinary independent saved conversation."""
        with self._operation(), self._session_campaign_work(session_id):
            self._store.detach_legacy_builder_chat(
                session_id=session_id, expected_message_count=expected_message_count,
                launch_context=launch_context, display_name=display_name,
            )

    def rename_builder_chat(
        self, session_id: str, display_name: str, *, automatic: bool = False
    ) -> BuilderChatRecord:
        with self._operation():
            return self._sessions.rename_builder_chat(session_id, display_name, automatic=automatic)

    def delete_builder_chat(self, session_id: str, *, confirmation: str) -> BuilderChatRecord:
        """Permanently delete one idle standalone Builder chat after exact confirmation."""

        try:
            validate_authority_identity(session_id, "Builder chat identity")
        except (TypeError, ValueError) as error:
            raise CommandRejected(
                "Builder chat identity is invalid", code="invalid_authority_identity"
            ) from error
        if session_id in {".", ".."} or any(mark in session_id for mark in ("/", "\\", ":")):
            raise CommandRejected(
                "Builder chat identity is invalid", code="invalid_authority_identity"
            )
        if confirmation != f"permanently delete {session_id}":
            raise CommandRejected(
                "Builder chat deletion confirmation is not exact",
                code="builder_chat_delete_confirmation_inexact",
            )
        lane = self._lane(f"builder-chat:{session_id}")
        if not lane.acquire(blocking=False):
            raise CommandRejected(
                "Builder chat has active work; deletion is not queued",
                code="builder_chat_work_in_progress",
            )
        try:
            with self._operation():
                return self._sessions.delete_builder_chat(session_id)
        finally:
            lane.release()

    def set_builder_chat_archived(self, session_id: str, *, archived: bool) -> BuilderChatRecord:
        """Archive an idle independent chat using its durable stopped session lifecycle.

        Unlike Stop generation, this closes admission until explicit restoration. Holding the
        same nonblocking work lane prevents silently cancelling or waiting behind live tools.
        """
        lane = self._lane(f"builder-chat:{session_id}")
        if not lane.acquire(blocking=False):
            raise CommandRejected(
                "Finish or stop this Builder's work before archiving it",
                code="builder_chat_work_in_progress",
            )
        try:
            with self._operation():
                context = self._sessions.context(session_id)
                if context.session.campaign_id is not None:
                    raise CommandRejected(
                        "Archive the campaign to archive its Interview or Builder"
                    )
                target = SessionStatus.STOPPED if archived else SessionStatus.ACTIVE
                if context.session.status not in {SessionStatus.ACTIVE, SessionStatus.STOPPED}:
                    raise CommandRejected(
                        "Resolve this Builder's session state before archiving it"
                    )
                if context.session.status is not target:
                    if archived:
                        self._sessions.update_status(session_id, target)
                    else:
                        self._store.resume_agent_session(session_id)
                return next(
                    chat for chat in self._sessions.builder_chat_index()
                    if chat.session.session_id == session_id
                )
        finally:
            lane.release()

    def update_agent_session_reasoning(
        self, session_id: str, *, effort: str | None, once: str | None,
    ) -> SessionRecord:
        """Change idle-session preferences without changing campaign state or history."""
        with self._operation(), self._session_campaign_work(session_id):
            return self._sessions.update_reasoning(session_id, effort=effort, once=once)

    def update_agent_session_model(
        self,
        session_id: str,
        *,
        provider_id: str,
        model_id: str,
    ) -> SessionRecord:
        """Switch an idle session while retaining its durable conversation history."""

        with self._operation(), self._session_campaign_work(session_id):
            return self._sessions.update_model(
                session_id,
                provider_id=provider_id,
                model_id=model_id,
            )

    def create_builder_child_session(
        self,
        *,
        parent_session_id: str,
        provider_id: str,
        model_id: str,
        selected_skill: str,
        scope: tuple[str, ...],
        session_id: str | None = None,
    ) -> SessionRecord:
        with self._operation(), self._session_campaign_work(parent_session_id):
            return self._sessions.create_child(
                parent_session_id=parent_session_id,
                provider_id=provider_id,
                model_id=model_id,
                selected_skill=selected_skill,
                scope=scope,
                session_id=session_id,
            )

    def return_builder_child_evidence(
        self,
        *,
        child_session_id: str,
        evidence_id: str,
        content_sha256: str,
        safe_summary: str,
        claims: tuple[str, ...],
    ) -> ChildEvidence:
        with self._operation(), self._session_campaign_work(child_session_id):
            return self._sessions.return_child_evidence(
                child_session_id=child_session_id,
                evidence_id=evidence_id,
                content_sha256=content_sha256,
                safe_summary=safe_summary,
                claims=claims,
            )

    def session_activity_basis(
        self, session_id: str, attempt_id: str | None = None,
    ) -> SessionActivityBasis:
        """Get activity recovery facts without reading a conversation transcript."""
        with self._operation():
            return self._store.session_activity_basis(session_id, attempt_id)

    def append_private_provider_record(self, record: ProviderPrivateRecord) -> None:
        """Persist provider ingress without entering campaign memory or ordinary history."""
        with self._operation():
            self._store.append_private_provider_record(record)

    def finish_private_provider_attempt(
        self, session_id: str, attempt_id: str, *, completed: bool,
    ) -> None:
        """Retain terminal identity separately from completed prose and Full Audit."""
        with self._operation():
            self._store.finish_private_provider_attempt(session_id, attempt_id, completed=completed)

    def private_provider_index(self, session_id: str) -> list[dict[str, object]]:
        """Expose availability and correlation only; no thinking preview."""
        with self._operation():
            return self._store.private_provider_index(session_id)

    def inspect_provider_thinking(
        self, session_id: str, attempt_id: str, response_id: str,
    ) -> str | None:
        """Serve an explicit inspection request for actual provider-supplied text."""
        with self._operation():
            return self._store.inspect_provider_thinking(session_id, attempt_id, response_id)

    def provider_continuation(
        self, session_id: str, response_id: str, *, provider_id: str, model_id: str,
    ) -> Mapping[str, object] | None:
        """Retrieve a selected provider exchange without exposing it to ordinary display reads."""
        with self._operation():
            return self._store.provider_continuation(
                session_id, response_id, provider_id=provider_id, model_id=model_id,
            )

    def actor_context(
        self, session_id: str, context_id: str, *, provider_id: str, model_id: str,
    ) -> Mapping[str, object] | None:
        """Load only the selected participant's private provider-compatible history."""
        with self._operation():
            return self._store.actor_context(
                session_id, context_id, provider_id=provider_id, model_id=model_id,
            )

    def session_history_page(
        self, session_id: str, *, before_sequence: int | None = None, limit: int = 50,
    ) -> SessionHistoryPage:
        """Read display history without constructing the agent's complete context."""
        with self._operation():
            return self._sessions.history_page(
                session_id, before_sequence=before_sequence, limit=limit,
            )

    def session_context(self, session_id: str) -> SessionContext:
        with self._operation():
            context = self._sessions.context(session_id)
            if context.session.campaign_id is None:
                return context
            links = tuple(
                message
                for message in context.messages
                if message.kind is SessionMessageKind.EVIDENCE
                and message.content.get("kind") == "creation_subflow_link"
            )
            if not links:
                return context
            link = links[-1]
            parent_session_id = link.content.get("parent_session_id")
            transcript_ids = link.content.get("transcript_message_ids")
            if (
                not isinstance(parent_session_id, str)
                or not isinstance(transcript_ids, Sequence)
                or isinstance(transcript_ids, (str, bytes, bytearray))
            ):
                raise CommandRejected("Creation Subflow transcript reference is invalid")
            parent = self._sessions.context(parent_session_id)
            by_id = {message.message_id: message for message in parent.messages}
            transcript: list[JsonValue] = []
            for message_id in transcript_ids:
                source = by_id.get(str(message_id))
                if source is None or source.kind not in (
                    SessionMessageKind.TALK,
                    SessionMessageKind.USER,
                ):
                    raise CommandRejected("Creation Subflow transcript source is unavailable")
                transcript.append(
                    {
                        "source_message_id": source.message_id,
                        "role": ("assistant" if source.kind is SessionMessageKind.TALK else "user"),
                        "content": str(source.content.get("text", "")),
                    }
                )
            enriched = SessionMessage(
                session_id=link.session_id,
                sequence=link.sequence,
                message_id=link.message_id,
                kind=link.kind,
                content=owned_payload({**dict(link.content), "interview_transcript": transcript}),
                canonical_sequence=link.canonical_sequence,
            )
            return SessionContext(
                session=context.session,
                messages=tuple(
                    enriched if message.message_id == link.message_id else message
                    for message in context.messages
                ),
            )

    def record_campaign_scene(
        self,
        *,
        session_id: str,
        scene_id: str,
        act_id: str | None,
        summary: str,
        location_id: str | None,
        basis_message_ids: tuple[str, ...],
        turn_id: str | None = None,
    ) -> CampaignContinuityReceipt:
        """Commit the current fictional situation to campaign-owned long-term memory."""

        with self._operation(), self._session_campaign_work(session_id):
            context, plan = self._optional_dm_structured_plan(session_id)
            try:
                validate_authority_identity(scene_id, "Campaign scene update identity")
            except (TypeError, ValueError) as error:
                raise CommandRejected(
                    "Campaign scene field 'scene_id' is invalid",
                    code="campaign_scene_invalid",
                ) from error
            if act_id is not None:
                try:
                    validate_authority_identity(act_id, "Campaign scene act identity")
                except (TypeError, ValueError) as error:
                    raise CommandRejected(
                        "Campaign scene field 'act_id' is invalid",
                        code="campaign_scene_invalid",
                    ) from error
                structure = self._retained_preparation(
                    context.session.campaign_id, plan
                ).get("structure")
                scene_act_ids = {
                    str(item["id"])
                    for item in (
                        structure
                        if isinstance(structure, Sequence)
                        and not isinstance(structure, (str, bytes, bytearray))
                        else ()
                    )
                    if isinstance(item, Mapping) and isinstance(item.get("id"), str)
                }
                if act_id not in scene_act_ids:
                    raise CommandRejected(
                        "Campaign scene field 'act_id' is unavailable",
                        code="campaign_scene_invalid",
                    )
            if location_id is not None:
                try:
                    validate_authority_identity(location_id, "Campaign scene location identity")
                except (TypeError, ValueError) as error:
                    raise CommandRejected(
                        "Campaign scene field 'location_id' is invalid",
                        code="campaign_scene_invalid",
                    ) from error
            if not summary or summary != summary.strip() or len(summary) > 2_000:
                raise CommandRejected(
                    "Campaign scene field 'summary' is invalid",
                    code="campaign_scene_invalid",
                )
            try:
                if not basis_message_ids or len(set(basis_message_ids)) != len(basis_message_ids):
                    raise ValueError("Campaign scene provenance is invalid")
                for message_id in basis_message_ids:
                    validate_authority_identity(message_id, "Campaign scene provenance")
            except (TypeError, ValueError) as error:
                raise CommandRejected("Campaign scene provenance is invalid") from error
            known_messages = {message.message_id for message in context.messages}
            if not set(basis_message_ids) <= known_messages:
                raise CommandRejected("Campaign scene provenance is unavailable")
            campaign_id = context.session.campaign_id
            assert campaign_id is not None
            snapshot = self._store.snapshot(campaign_id)
            resolved_turn_id = self._continuity_turn(snapshot, turn_id)
            return self._store.commit_campaign_continuity(
                CampaignContinuityCommand(
                    campaign_id=campaign_id,
                    operation_id=scene_id,
                    session_id=session_id,
                    expected_revision=snapshot.revision,
                    kind="scene",
                    record_id="current_scene",
                    status="active",
                    payload=owned_payload(
                        {
                            "act_id": act_id,
                            "location_id": location_id,
                            "summary": summary,
                            "origin": "gm_commit",
                        }
                    ),
                    basis_message_ids=basis_message_ids,
                    turn_id=resolved_turn_id,
                )
            )

    def record_campaign_secret_reveal(
        self,
        *,
        session_id: str,
        reveal_id: str,
        secret_id: str,
        audience: tuple[str, ...],
        basis_message_ids: tuple[str, ...],
        turn_id: str | None = None,
    ) -> CampaignContinuityReceipt:
        """Commit who now knows one prepared or improvised secret."""

        with self._operation(), self._session_campaign_work(session_id):
            context, plan = self._dm_structured_plan(session_id)
            try:
                validate_authority_identity(reveal_id, "Campaign secret reveal identity")
                validate_authority_identity(secret_id, "Campaign secret identity")
                if not audience or len(set(audience)) != len(audience):
                    raise ValueError("Campaign secret audience is invalid")
                for identity in audience:
                    validate_authority_identity(identity, "Campaign secret audience")
                if not basis_message_ids or len(set(basis_message_ids)) != len(basis_message_ids):
                    raise ValueError("Campaign secret reveal provenance is invalid")
                for message_id in basis_message_ids:
                    validate_authority_identity(message_id, "Campaign secret reveal provenance")
            except (TypeError, ValueError) as error:
                raise CommandRejected("Campaign secret reveal is invalid") from error
            secrets = {
                str(item["id"]): item
                for item in self._campaign_secret_catalog(context.session.campaign_id, plan)
                if isinstance(item, Mapping) and isinstance(item.get("id"), str)
            }
            secret = secrets.get(secret_id)
            if secret is None:
                raise CommandRejected("Campaign secret is not declared")
            known_messages = {message.message_id for message in context.messages}
            if not set(basis_message_ids) <= known_messages:
                raise CommandRejected("Campaign secret reveal provenance is unavailable")
            campaign_id = context.session.campaign_id
            assert campaign_id is not None
            snapshot = self._store.snapshot(campaign_id)
            return self._store.commit_campaign_continuity(
                CampaignContinuityCommand(
                    campaign_id=campaign_id,
                    operation_id=reveal_id,
                    session_id=session_id,
                    expected_revision=snapshot.revision,
                    kind="reveal",
                    record_id=secret_id,
                    status="active",
                    payload=owned_payload(
                        {
                            "secret_id": secret_id,
                            "audience": list(audience),
                            "origin": "gm_commit",
                        }
                    ),
                    basis_message_ids=basis_message_ids,
                    turn_id=self._continuity_turn(snapshot, turn_id),
                )
            )

    def record_campaign_memory(
        self,
        *,
        session_id: str,
        memory_id: str,
        fact_id: str,
        category: str,
        text: str,
        visibility: str,
        known_by: tuple[str, ...],
        basis_message_ids: tuple[str, ...],
        subjects: tuple[str, ...] = (),
        status: str = "active",
        turn_id: str | None = None,
    ) -> CampaignContinuityReceipt:
        """Commit one durable campaign fact without promoting unrevealed secrets."""

        with self._operation(), self._session_campaign_work(session_id):
            context, plan = self._optional_dm_structured_plan(session_id)
            try:
                validate_authority_identity(memory_id, "Campaign memory identity")
                validate_authority_identity(fact_id, "Campaign memory fact identity")
                if category not in {
                    "decision",
                    "relationship",
                    "discovery",
                    "promise",
                    "consequence",
                    "continuity",
                }:
                    raise ValueError("Campaign memory category is invalid")
                if visibility not in {"player_known", "gm_private"}:
                    raise ValueError("Campaign memory visibility is invalid")
                if not text or text != text.strip() or len(text) > 2_000:
                    raise ValueError("Campaign memory text is invalid")
                if len(set(known_by)) != len(known_by):
                    raise ValueError("Campaign memory knowledge is invalid")
                for identity in known_by:
                    validate_authority_identity(identity, "Campaign memory knowledge")
                if len(set(subjects)) != len(subjects):
                    raise ValueError("Campaign memory subjects are invalid")
                for identity in subjects:
                    validate_authority_identity(identity, "Campaign memory subject")
                if status not in {"active", "retired"}:
                    raise ValueError("Campaign memory status is invalid")
                if not basis_message_ids or len(set(basis_message_ids)) != len(basis_message_ids):
                    raise ValueError("Campaign memory provenance is invalid")
                for message_id in basis_message_ids:
                    validate_authority_identity(message_id, "Campaign memory provenance")
            except (TypeError, ValueError) as error:
                raise CommandRejected("Campaign memory is invalid") from error
            known_messages = {message.message_id for message in context.messages}
            if not set(basis_message_ids) <= known_messages:
                raise CommandRejected("Campaign memory provenance is unavailable")
            campaign_id = context.session.campaign_id
            assert campaign_id is not None
            snapshot = self._store.snapshot(campaign_id)
            revealed_to_player = {
                secret_id
                for secret_id, audience_value in snapshot.continuity.reveals.items()
                if isinstance(audience_value, Sequence)
                and not isinstance(audience_value, (str, bytes, bytearray))
                and "player" in audience_value
            }
            protected_facts = {
                str(secret["fact"]): str(secret["id"])
                for secret in self._campaign_secret_catalog(context.session.campaign_id, plan)
                if isinstance(secret, Mapping)
                and isinstance(secret.get("id"), str)
                and isinstance(secret.get("fact"), str)
            }
            protected_secret = protected_facts.get(text)
            if (
                visibility == "player_known"
                and protected_secret is not None
                and protected_secret not in revealed_to_player
            ):
                raise CommandRejected("Campaign memory cannot reveal a protected secret")
            return self._store.commit_campaign_continuity(
                CampaignContinuityCommand(
                    campaign_id=campaign_id,
                    operation_id=memory_id,
                    session_id=session_id,
                    expected_revision=snapshot.revision,
                    kind="fact",
                    record_id=fact_id,
                    status=status,
                    payload=owned_payload(
                        {
                            "category": category,
                            "text": text,
                            "visibility": visibility,
                            "known_by": list(known_by),
                            "subjects": list(subjects),
                            "origin": "gm_commit",
                        }
                    ),
                    basis_message_ids=basis_message_ids,
                    turn_id=self._continuity_turn(snapshot, turn_id),
                )
            )

    def record_campaign_entity(
        self,
        *,
        session_id: str,
        update_id: str,
        entity_id: str,
        entity_kind: str,
        name: str,
        attributes: Mapping[str, JsonValue],
        visibility: str,
        known_by: tuple[str, ...],
        basis_message_ids: tuple[str, ...],
        source_ref: str | None = None,
        status: str = "active",
        turn_id: str | None = None,
    ) -> CampaignContinuityReceipt:
        """Commit campaign-specific truth about a person, place, group, or object."""

        with self._operation(), self._session_campaign_work(session_id):
            context = self._active_dm_context(session_id)
            try:
                for value, label in (
                    (update_id, "Campaign entity update"),
                    (entity_id, "Campaign entity"),
                    (entity_kind, "Campaign entity kind"),
                ):
                    validate_authority_identity(value, label)
                if source_ref is not None:
                    validate_authority_identity(source_ref, "Campaign entity source")
                if not name or name != name.strip() or len(name) > 300:
                    raise ValueError("Campaign entity name is invalid")
                if visibility not in {"player_known", "gm_private"}:
                    raise ValueError("Campaign entity visibility is invalid")
                if status not in {"active", "retired"}:
                    raise ValueError("Campaign entity status is invalid")
                if len(set(known_by)) != len(known_by):
                    raise ValueError("Campaign entity knowledge is invalid")
                for identity in known_by:
                    validate_authority_identity(identity, "Campaign entity knowledge")
                if not basis_message_ids or len(set(basis_message_ids)) != len(basis_message_ids):
                    raise ValueError("Campaign entity provenance is invalid")
                for message_id in basis_message_ids:
                    validate_authority_identity(message_id, "Campaign entity provenance")
                admitted_attributes = owned_payload(dict(attributes))
            except (TypeError, ValueError) as error:
                raise CommandRejected("Campaign entity update is invalid") from error
            known_messages = {message.message_id for message in context.messages}
            if not set(basis_message_ids) <= known_messages:
                raise CommandRejected("Campaign entity provenance is unavailable")
            campaign_id = context.session.campaign_id
            assert campaign_id is not None
            snapshot = self._store.snapshot(campaign_id)
            return self._store.commit_campaign_continuity(
                CampaignContinuityCommand(
                    campaign_id=campaign_id,
                    operation_id=update_id,
                    session_id=session_id,
                    expected_revision=snapshot.revision,
                    kind="entity",
                    record_id=entity_id,
                    status=status,
                    payload=owned_payload(
                        {
                            "entity_kind": entity_kind,
                            "name": name,
                            "attributes": admitted_attributes,
                            "visibility": visibility,
                            "known_by": list(known_by),
                            "source_ref": source_ref,
                            "origin": "gm_commit",
                        }
                    ),
                    basis_message_ids=basis_message_ids,
                    turn_id=self._continuity_turn(snapshot, turn_id),
                )
            )

    def record_campaign_plan_progress(
        self,
        *,
        session_id: str,
        update_id: str,
        plan_item_id: str,
        progress_status: str,
        summary: str,
        basis_message_ids: tuple[str, ...],
        turn_id: str | None = None,
    ) -> CampaignContinuityReceipt:
        """Bookmark actual play against private preparation without scripting a scene."""

        with self._operation(), self._session_campaign_work(session_id):
            context, _plan = self._dm_structured_plan(session_id)
            try:
                validate_authority_identity(update_id, "Plan Progress update")
                validate_authority_identity(plan_item_id, "Campaign Plan item")
                if progress_status not in {
                    "introduced",
                    "engaged",
                    "deferred",
                    "resolved",
                    "retired",
                }:
                    raise ValueError("Plan Progress status is invalid")
                if not summary or summary != summary.strip() or len(summary) > 2_000:
                    raise ValueError("Plan Progress summary is invalid")
                if not basis_message_ids or len(set(basis_message_ids)) != len(basis_message_ids):
                    raise ValueError("Plan Progress provenance is invalid")
                for message_id in basis_message_ids:
                    validate_authority_identity(message_id, "Plan Progress provenance")
            except (TypeError, ValueError) as error:
                raise CommandRejected("Plan Progress update is invalid") from error
            known_messages = {message.message_id for message in context.messages}
            if not set(basis_message_ids) <= known_messages:
                raise CommandRejected("Plan Progress provenance is unavailable")
            campaign_id = context.session.campaign_id
            assert campaign_id is not None
            snapshot = self._store.snapshot(campaign_id)
            try:
                check_ending_progress(_plan, snapshot.continuity.plan_progress, ({
                    "plan_item_id": plan_item_id, "status": progress_status,
                },))
            except ValueError as error:
                raise CommandRejected(str(error), code="campaign_ending_not_ready") from error
            return self._store.commit_campaign_continuity(
                CampaignContinuityCommand(
                    campaign_id=campaign_id,
                    operation_id=update_id,
                    session_id=session_id,
                    expected_revision=snapshot.revision,
                    kind="plan_progress",
                    record_id=plan_item_id,
                    status="active",
                    payload=owned_payload(
                        {
                            "plan_item_id": plan_item_id,
                            "status": progress_status,
                            "summary": summary,
                            "origin": "gm_commit",
                        }
                    ),
                    basis_message_ids=basis_message_ids,
                    turn_id=self._continuity_turn(snapshot, turn_id),
                )
            )

    def update_campaign_file(
        self,
        *,
        session_id: str,
        update_id: str,
        updates: Sequence[Mapping[str, object]],
        basis_message_ids: tuple[str, ...],
        turn_id: str | None = None,
    ) -> SafePayload:
        """Validate and admit one all-or-nothing batch of GM-owned Campaign File notes."""

        with self._operation(), self._session_campaign_work(session_id):
            context, plan = self._optional_dm_structured_plan(session_id)
            all_updates = tuple(updates)
            character_updates = tuple(
                item for item in all_updates if item.get("operation") == "update_character"
            )
            inventory_updates = tuple(
                item for item in all_updates if item.get("operation") == "update_inventory"
            )
            if any(item.get("operation") == "prepared_inventory" for item in all_updates):
                raise CommandRejected("Internal inventory operations cannot be submitted")
            admitted_updates = tuple(
                item for item in all_updates
                if item.get("operation") not in {"update_character", "update_inventory"}
            )
            try:
                validate_authority_identity(update_id, "Campaign File update")
                if not all_updates or len(all_updates) > 64:
                    raise ValueError("Campaign File update count is invalid")
                if not basis_message_ids or len(set(basis_message_ids)) != len(basis_message_ids):
                    raise ValueError("Campaign File provenance is invalid")
                for message_id in basis_message_ids:
                    validate_authority_identity(message_id, "Campaign File provenance")
            except (TypeError, ValueError) as error:
                raise CommandRejected(
                    "Campaign File update is invalid",
                    code="campaign_file_update_invalid",
                ) from error
            known_messages = {message.message_id for message in context.messages}
            if not set(basis_message_ids) <= known_messages:
                raise CommandRejected(
                    "Campaign File provenance is unavailable",
                    code="campaign_file_provenance_unavailable",
                )
            campaign_id = context.session.campaign_id
            assert campaign_id is not None
            snapshot = self._store.snapshot(campaign_id)
            resolved_turn_id = self._continuity_turn(snapshot, turn_id)
            inventory_revision = snapshot.revision
            if inventory_updates:
                if self._inventory_tool(campaign_id) is not None:
                    raise CommandRejected(
                        "Use the active Ruleset inventory; fallback is unavailable"
                    )
                inventory_revision, inventory_payload = prepare_inventory_payload(
                    snapshot, inventory_updates, operation_id=f"{update_id}-1",
                    request_hash=hashlib.sha256(canonical_json_bytes({
                        "updates": list(all_updates), "session_id": session_id,
                        "basis_message_ids": list(basis_message_ids),
                    })).hexdigest(),
                    pending_entities=tuple(
                        item for item in admitted_updates
                        if item.get("operation") == "record_entity"
                    ),
                )
                admitted_updates = (
                    {"operation": "prepared_inventory", "payload": inventory_payload},
                    *admitted_updates,
                )
            character_command = None
            if character_updates:
                tool = self._character_update_tool(campaign_id)
                if tool is None:
                    raise CommandRejected("Character bookkeeping is unavailable")
                expected_revision = character_updates[0].get("expected_revision")
                if type(expected_revision) is not int or expected_revision < 0:
                    raise CommandRejected("Character update revision is invalid")
                if inventory_updates and expected_revision != inventory_revision:
                    raise CommandRejected("Character and inventory updates must share one revision")
                participants: set[str] = set()
                admitted_participants = {
                    participant.participant_id for participant in snapshot.participants
                    if participant.package_id == tool.package_id
                }
                mechanical_updates = []
                for item in character_updates:
                    if set(item) != {"operation", "participant_id", "expected_revision", "fields"}:
                        raise CommandRejected("Character update fields are invalid")
                    if item["expected_revision"] != expected_revision:
                        raise CommandRejected("Character updates must share one revision")
                    participant_id = item["participant_id"]
                    if (
                        not isinstance(participant_id, str)
                        or participant_id not in admitted_participants
                        or participant_id in participants
                    ):
                        raise CommandRejected("Character update identity is invalid or duplicated")
                    participants.add(participant_id)
                    mechanical_updates.append({
                        "participant_id": participant_id, "fields": item["fields"],
                    })
                character_command = ActionCommand(
                    campaign_id=campaign_id,
                    action_id="character-" + hashlib.sha256(canonical_json_bytes({
                        "update_id": update_id,
                        "session_id": session_id,
                        "basis_message_ids": list(basis_message_ids),
                        "updates": list(all_updates),
                    })).hexdigest(),
                    idempotency_key=update_id,
                    owner_id=self._owner_id,
                    writer=WriterRole.DM,
                    expected_revision=expected_revision,
                    turn_id=resolved_turn_id,
                    package_id=tool.package_id,
                    tool_name=tool.name,
                    parameters=owned_payload({"updates": mechanical_updates}),
                    initiating_session_id=session_id,
                )
            secrets = {
                str(item["id"]): item
                for item in self._campaign_secret_catalog(campaign_id, plan)
                if isinstance(item, Mapping) and isinstance(item.get("id"), str)
            }
            revealed_to_player = {
                secret_id
                for secret_id, audience_value in snapshot.continuity.reveals.items()
                if isinstance(audience_value, Sequence)
                and not isinstance(audience_value, (str, bytes, bytearray))
                and "player" in audience_value
            }
            for update in admitted_updates:
                if update.get("operation") != "reveal_secret":
                    continue
                audience = update.get("audience")
                secret_id = update.get("secret_id")
                if (
                    isinstance(secret_id, str)
                    and isinstance(audience, Sequence)
                    and not isinstance(audience, (str, bytes, bytearray))
                    and "player" in audience
                ):
                    revealed_to_player.add(secret_id)
            protected_facts = {
                str(secret["fact"]): str(secret_id)
                for secret_id, secret in secrets.items()
                if isinstance(secret.get("fact"), str)
            }
            plan_items: set[str] = set()
            plan_items.update(ending_progress_id(item) for item in intended_endings(plan))
            retained_preparation = self._retained_preparation(campaign_id, plan)
            for collection_name in ("objectives", "structure"):
                raw_collection = retained_preparation.get(collection_name, ())
                if not isinstance(raw_collection, Sequence) or isinstance(
                    raw_collection, (str, bytes, bytearray)
                ):
                    continue
                plan_items.update(
                    str(item["id"])
                    for item in raw_collection
                    if isinstance(item, Mapping) and isinstance(item.get("id"), str)
                )
            structure = retained_preparation.get("structure")
            scene_act_ids = {
                str(item["id"])
                for item in (
                    structure
                    if isinstance(structure, Sequence)
                    and not isinstance(structure, (str, bytes, bytearray))
                    else ()
                )
                if isinstance(item, Mapping) and isinstance(item.get("id"), str)
            }
            commands: list[CampaignContinuityCommand] = []
            changed_records: set[tuple[str, str]] = set()
            current_records: dict[tuple[str, str], SafePayload] = {}
            persisted_operation_ids = {
                record.operation_id
                for record in snapshot.continuity.records
                if record.operation_id is not None
            }
            replaying_update = bool(character_updates or inventory_updates) or any(
                f"{update_id}-{index + 1}" in persisted_operation_ids
                for index in range(len(admitted_updates))
            )
            for record in snapshot.continuity.records:
                record_key = (record.kind, record.record_id)
                if record.status == "active":
                    current_records[record_key] = record.payload
                else:
                    current_records.pop(record_key, None)
            for index, raw_update in enumerate(admitted_updates):
                update = dict(raw_update)
                operation = update.get("operation")
                operation_id = f"{update_id}-{index + 1}"
                try:
                    if operation == "prepared_inventory":
                        kind = "entity"
                        record_id = INVENTORY_RECORD_ID
                        payload = update["payload"]
                    elif operation == "record_memory":
                        required = {
                            "operation",
                            "fact_id",
                            "category",
                            "text",
                            "visibility",
                            "known_by",
                        }
                        if set(update) not in (required, required | {"subjects"}):
                            raise ValueError("Campaign memory fields are invalid")
                        fact_id = validate_authority_identity(
                            update.get("fact_id"), "Campaign memory fact"
                        )
                        category = update.get("category")
                        if category not in {
                            "decision",
                            "relationship",
                            "discovery",
                            "promise",
                            "consequence",
                            "continuity",
                        }:
                            raise ValueError("Campaign memory category is invalid")
                        text = update.get("text")
                        if (
                            not isinstance(text, str)
                            or not text
                            or text != text.strip()
                            or len(text) > 2_000
                        ):
                            raise ValueError("Campaign memory text is invalid")
                        visibility = update.get("visibility")
                        if visibility not in {"player_known", "gm_private"}:
                            raise ValueError("Campaign memory visibility is invalid")
                        known_by = update.get("known_by")
                        subjects = update.get("subjects", [])
                        if (
                            not isinstance(known_by, Sequence)
                            or isinstance(known_by, (str, bytes, bytearray))
                            or not isinstance(subjects, Sequence)
                            or isinstance(subjects, (str, bytes, bytearray))
                            or len(set(known_by)) != len(known_by)
                            or len(set(subjects)) != len(subjects)
                        ):
                            raise ValueError("Campaign memory knowledge is invalid")
                        for identity in (*known_by, *subjects):
                            validate_authority_identity(identity, "Campaign memory subject")
                        protected_secret = protected_facts.get(text)
                        if (
                            visibility == "player_known"
                            and protected_secret is not None
                            and protected_secret not in revealed_to_player
                        ):
                            raise CommandRejected(
                                "Campaign memory cannot reveal a protected secret"
                            )
                        kind = "fact"
                        record_id = fact_id
                        payload = owned_payload(
                            {
                                "category": category,
                                "text": text,
                                "visibility": visibility,
                                "known_by": list(known_by),
                                "subjects": list(subjects),
                                "origin": "gm_commit",
                            }
                        )
                    elif operation == "record_entity":
                        required = {
                            "operation",
                            "entity_id",
                            "entity_kind",
                            "name",
                            "attributes",
                            "visibility",
                            "known_by",
                        }
                        if set(update) not in (required, required | {"source_ref"}):
                            raise ValueError("Campaign entity fields are invalid")
                        record_id = validate_authority_identity(
                            update.get("entity_id"), "Campaign entity"
                        )
                        entity_kind = validate_authority_identity(
                            update.get("entity_kind"), "Campaign entity kind"
                        )
                        if record_id == INVENTORY_RECORD_ID or entity_kind == INVENTORY_ENTITY_KIND:
                            raise CommandRejected("Use inventory_updates for fallback holdings")
                        name = update.get("name")
                        if (
                            not isinstance(name, str)
                            or not name
                            or name != name.strip()
                            or len(name) > 300
                        ):
                            raise ValueError("Campaign entity name is invalid")
                        attributes = update.get("attributes")
                        if not isinstance(attributes, Mapping):
                            raise ValueError("Campaign entity attributes are invalid")
                        if any(p.participant_id == record_id for p in snapshot.participants):
                            bookkeeping = self._character_update_tool(campaign_id)
                            if bookkeeping is not None:
                                editable = bookkeeping.input_schema["properties"]["updates"][
                                    "items"
                                ]["properties"]["fields"]["properties"]
                                if set(attributes) & set(editable):
                                    raise CommandRejected(
                                        "Use character_updates for editable Ruleset fields; "
                                        "narrative attributes do not change character holdings",
                                        code="character_fields_require_bookkeeping",
                                    )
                        visibility = update.get("visibility")
                        if visibility not in {"player_known", "gm_private"}:
                            raise ValueError("Campaign entity visibility is invalid")
                        known_by = update.get("known_by")
                        if (
                            not isinstance(known_by, Sequence)
                            or isinstance(known_by, (str, bytes, bytearray))
                            or len(set(known_by)) != len(known_by)
                        ):
                            raise ValueError("Campaign entity knowledge is invalid")
                        for identity in known_by:
                            validate_authority_identity(identity, "Campaign entity knowledge")
                        source_ref = update.get("source_ref")
                        if source_ref is not None:
                            validate_authority_identity(source_ref, "Campaign entity source")
                        kind = "entity"
                        payload = owned_payload(
                            {
                                "entity_kind": entity_kind,
                                "name": name,
                                "attributes": owned_payload(dict(attributes)),
                                "visibility": visibility,
                                "known_by": list(known_by),
                                "source_ref": source_ref,
                                "origin": "gm_commit",
                            }
                        )
                    elif operation == "update_scene":
                        if set(update) not in (
                            {"operation", "summary"},
                            {"operation", "summary", "act_id"},
                            {"operation", "summary", "location_id"},
                            {"operation", "summary", "act_id", "location_id"},
                        ):
                            raise ValueError("Campaign scene fields are invalid")
                        summary = update.get("summary")
                        if (
                            not isinstance(summary, str)
                            or not summary
                            or summary != summary.strip()
                            or len(summary) > 2_000
                        ):
                            raise ValueError("Campaign scene summary is invalid")
                        act_id = update.get("act_id")
                        location_id = update.get("location_id")
                        if act_id is not None:
                            validate_authority_identity(act_id, "Campaign scene act")
                            if act_id not in scene_act_ids:
                                raise ValueError("Campaign scene act is unavailable")
                        if location_id is not None:
                            validate_authority_identity(location_id, "Campaign scene location")
                        kind = "scene"
                        record_id = "current_scene"
                        payload = owned_payload(
                            {
                                "act_id": act_id,
                                "location_id": location_id,
                                "summary": summary,
                                "origin": "gm_commit",
                            }
                        )
                    elif operation == "reveal_secret":
                        if set(update) != {"operation", "secret_id", "audience"}:
                            raise ValueError("Campaign reveal fields are invalid")
                        record_id = validate_authority_identity(
                            update.get("secret_id"), "Campaign secret"
                        )
                        audience = update.get("audience")
                        if (
                            record_id not in secrets
                            or not isinstance(audience, Sequence)
                            or isinstance(audience, (str, bytes, bytearray))
                            or not audience
                            or len(set(audience)) != len(audience)
                        ):
                            raise ValueError("Campaign secret reveal is invalid")
                        for identity in audience:
                            validate_authority_identity(identity, "Campaign secret audience")
                        kind = "reveal"
                        payload = owned_payload(
                            {
                                "secret_id": record_id,
                                "audience": list(audience),
                                "origin": "gm_commit",
                            }
                        )
                    elif operation == "update_plan_progress":
                        if set(update) != {
                            "operation",
                            "plan_item_id",
                            "progress_status",
                            "progress_summary",
                        }:
                            raise ValueError("Plan Progress fields are invalid")
                        record_id = validate_authority_identity(
                            update.get("plan_item_id"), "Campaign Plan item"
                        )
                        if not plan_items or record_id not in plan_items:
                            raise ValueError("Campaign Plan item is unavailable")
                        progress_status = update.get("progress_status")
                        if progress_status not in {
                            "introduced",
                            "engaged",
                            "deferred",
                            "resolved",
                            "retired",
                        }:
                            raise ValueError("Plan Progress status is invalid")
                        progress_summary = update.get("progress_summary")
                        if (
                            not isinstance(progress_summary, str)
                            or not progress_summary
                            or progress_summary != progress_summary.strip()
                            or len(progress_summary) > 2_000
                        ):
                            raise ValueError("Plan Progress summary is invalid")
                        kind = "plan_progress"
                        payload = owned_payload(
                            {
                                "plan_item_id": record_id,
                                "status": progress_status,
                                "summary": progress_summary,
                                "origin": "gm_commit",
                            }
                        )
                    else:
                        raise ValueError("Campaign File operation is unavailable")
                except CommandRejected:
                    raise
                except (TypeError, ValueError) as error:
                    raise CommandRejected(
                        f"Campaign File update {index + 1} is invalid",
                        code="campaign_file_update_invalid",
                    ) from error
                record_key = (kind, record_id)
                if record_key in changed_records:
                    raise CommandRejected(
                        "Campaign File update changes one record more than once",
                        code="campaign_file_duplicate_record",
                    )
                changed_records.add(record_key)
                if not replaying_update and kind == "reveal":
                    current_audience = snapshot.continuity.reveals.get(record_id, ())
                    proposed_audience = payload.get("audience", ())
                    if (
                        not isinstance(current_audience, Sequence)
                        or isinstance(current_audience, (str, bytes, bytearray))
                        or not isinstance(proposed_audience, Sequence)
                        or isinstance(proposed_audience, (str, bytes, bytearray))
                    ):
                        raise CommandRejected(
                            "Campaign File reveal audience is invalid",
                            code="campaign_file_update_invalid",
                        )
                    if set(proposed_audience) <= set(current_audience):
                        continue
                elif not replaying_update:
                    current_payload = current_records.get(record_key)
                    if current_payload is not None and canonical_json_bytes(
                        {key: value for key, value in current_payload.items() if key != "origin"}
                    ) == canonical_json_bytes(
                        {key: value for key, value in payload.items() if key != "origin"}
                    ):
                        continue
                commands.append(
                    CampaignContinuityCommand(
                        campaign_id=campaign_id,
                        operation_id=operation_id,
                        session_id=session_id,
                        expected_revision=(
                            character_command.expected_revision + 1
                            if character_command is not None else inventory_revision
                        ) + len(commands),
                        kind=kind,
                        record_id=record_id,
                        status="active",
                        payload=payload,
                        basis_message_ids=basis_message_ids,
                        turn_id=resolved_turn_id,
                    )
                )
            try:
                check_ending_progress(plan, snapshot.continuity.plan_progress, tuple(
                    command.payload for command in commands if command.kind == "plan_progress"
                ))
            except ValueError as error:
                raise CommandRejected(str(error), code="campaign_ending_not_ready") from error
            if character_command is not None:
                try:
                    receipt = self._execute_action(
                        character_command,
                        campaign_file_commands=tuple(commands),
                        campaign_file_update=True,
                    )
                except (OSError, sqlite3.Error) as error:
                    raise CommandRejected(
                        "Character bookkeeping could not be saved; nothing was changed",
                        code="campaign_file_save_failed",
                    ) from error
                if (
                    not isinstance(receipt, ActionReceipt)
                    or receipt.status is not ActionStatus.COMMITTED
                ):
                    raise CommandRejected("Character bookkeeping was not committed")
                return owned_payload({
                    "status": "campaign_file_updated",
                    "operation_id": update_id,
                    "change_count": len(character_updates) + len(commands),
                    "character_updates": [
                        {"participant_id": item["participant_id"], "fields": item["fields"]}
                        for item in character_updates
                    ],
                    "resulting_revision": receipt.resulting_revision + len(commands),
                })
            if not commands:
                return owned_payload(
                    {
                        "status": "campaign_file_unchanged",
                        "operation_id": update_id,
                        "change_count": 0,
                        "campaign_revision": snapshot.revision,
                        "state_changed": False,
                    }
                )
            try:
                receipts = self._store.commit_campaign_continuity_batch(commands)
            except (OSError, sqlite3.Error) as error:
                if not inventory_updates:
                    raise
                raise CommandRejected(
                    "Inventory and campaign notes could not be saved; nothing was changed",
                    code="campaign_file_save_failed",
                ) from error
            return owned_payload(
                {
                    "status": "campaign_file_updated",
                    "operation_id": update_id,
                    "change_count": len(receipts),
                    "changes": [
                        {
                            "operation_id": receipt.operation_id,
                            "record_id": receipt.record_id,
                            "kind": receipt.kind,
                            "resulting_revision": receipt.resulting_revision,
                        }
                        for receipt in receipts
                    ],
                    "resulting_revision": receipts[-1].resulting_revision,
                }
            )

    @staticmethod
    def _continuity_turn(snapshot: CampaignSnapshot, requested_turn_id: str | None) -> str:
        """Resolve the one open TURN that owns a continuity admission."""

        if requested_turn_id is not None:
            validate_authority_identity(requested_turn_id, "Campaign continuity turn")
            if snapshot.turns.get(requested_turn_id) is not TurnStatus.OPEN:
                raise CommandRejected("Campaign continuity TURN is not open")
            return requested_turn_id
        open_turns = tuple(
            identity for identity, status in snapshot.turns.items() if status is TurnStatus.OPEN
        )
        if len(open_turns) != 1:
            raise CommandRejected("Campaign continuity requires exactly one open TURN")
        return open_turns[0]

    def _active_dm_context(self, session_id: str) -> SessionContext:
        """Return one active campaign-bound DM context without requiring optional preparation."""

        context = self._sessions.context(session_id)
        if (
            context.session.role is not WriterRole.DM
            or context.session.status is not SessionStatus.ACTIVE
            or context.session.campaign_id is None
        ):
            raise CommandRejected("Campaign narrative state requires an active DM session")
        return context

    def _optional_dm_structured_plan(
        self, session_id: str
    ) -> tuple[SessionContext, Mapping[str, object]]:
        """Return optional structured preparation without making it a Campaign File prerequisite."""

        context = self._active_dm_context(session_id)
        assert context.session.campaign_id is not None
        accepted = self._store.accepted_campaign_plan(context.session.campaign_id)
        if accepted is None:
            return context, {}
        if "campaign_plan" not in accepted.content:
            return context, {}
        plan = accepted.content["campaign_plan"]
        if not isinstance(plan, Mapping):
            raise CommandRejected("Campaign narrative state requires a structured plan")
        return context, cast(Mapping[str, object], plan)

    def _retained_preparation(
        self, campaign_id: str, plan: Mapping[str, object],
    ) -> Mapping[str, object]:
        """Keep earlier opportunities usable while the active adventure owns its ending gates."""
        current = self._store.accepted_campaign_plan(campaign_id)
        if (current is None or not isinstance(current.content.get("sequel"), Mapping)
                or canonical_json_bytes(plan) != canonical_json_bytes(current.content["campaign_plan"])):
            return plan
        result = dict(plan)
        adventures = self._store.campaign_adventures(campaign_id)
        for field in ("structure", "objectives", "secrets"):
            values: list[object] = []
            setting_fact_ids: set[object] = set()
            for adventure in adventures:
                story = cast(Mapping[str, object], adventure.content["campaign_plan"])
                for item in cast(Sequence[object], story.get(field, ())):
                    if isinstance(item, Mapping) and "setting_fact_id" in item:
                        if item["setting_fact_id"] in setting_fact_ids:
                            continue
                        setting_fact_ids.add(item["setting_fact_id"])
                    values.append(item)
            result[field] = values
        return owned_payload(result)

    def _dm_structured_plan(self, session_id: str) -> tuple[SessionContext, Mapping[str, object]]:
        """Return one active DM context and its required exact accepted structured Plan."""

        context, plan = self._optional_dm_structured_plan(session_id)
        if not plan:
            raise CommandRejected("Campaign narrative state requires an accepted plan")
        return context, plan

    def _campaign_setting_knowledge(
        self,
        campaign_id: str,
        plan: Mapping[str, object],
    ) -> tuple[tuple[SafePayload, ...], tuple[str, ...]]:
        """Read the optional exact Setting knowledge artifact from the locked package."""

        bindings = plan.get("bindings")
        binding = bindings.get("campaign_setting") if isinstance(bindings, Mapping) else None
        if not isinstance(binding, Mapping):
            return (), ()
        package_id = binding.get("id")
        version = binding.get("version")
        content_sha256 = binding.get("content_sha256")
        if not all(isinstance(item, str) for item in (package_id, version, content_sha256)):
            raise CommandRejected("Campaign Setting knowledge binding is invalid")
        snapshot = self._store.snapshot(campaign_id)
        if not any(
            (entry.package_id, entry.version, entry.sha256) == (package_id, version, content_sha256)
            for entry in snapshot.package_lock.entries
        ):
            replacements = tuple(
                entry
                for entry in snapshot.package_lock.entries
                if entry.package_id == package_id and entry.version == version
            )
            historical = any(
                (entry.package_id, entry.version, entry.sha256)
                == (package_id, version, content_sha256)
                for record in self._store.package_lock_lineage(campaign_id)
                for entry in record.package_lock.entries
            )
            if len(replacements) != 1 or not historical:
                raise CommandRejected("Campaign Setting knowledge binding is stale")
            original = self._store.exact_package_manifest(
                str(package_id), str(version), str(content_sha256)
            )
            replacement = self._store.exact_package_manifest(
                str(package_id), str(version), replacements[0].sha256
            )
            if not dependency_only_manifest_rebind(
                original.canonical_json, replacement.canonical_json
            ):
                raise CommandRejected("Campaign Setting knowledge binding is stale")
            # Read the equivalent current artifact without rewriting the accepted
            # Plan, historical lock, Builder handoff or previously pinned package.
            content_sha256 = replacements[0].sha256
        value = self._store.package_file_bytes(
            str(package_id),
            str(version),
            str(content_sha256),
            CAMPAIGN_SETTING_KNOWLEDGE_PATH,
            maximum_bytes=CAMPAIGN_SETTING_KNOWLEDGE_BYTE_LIMIT,
        )
        if value is None:
            return (), ()
        try:
            knowledge = parse_campaign_setting_knowledge_bytes(value)
        except ValueError as error:
            raise CommandRejected("Campaign Setting knowledge is invalid") from error
        return (
            tuple(
                owned_payload(
                    {
                        "id": fact.identity,
                        "fact": fact.fact,
                        "known_by": list(fact.known_by),
                        "source": "campaign_setting",
                        "source_path": fact.source_path,
                        "selected_by_plan": False,
                        "reveal": None,
                    }
                )
                for fact in knowledge.facts
            ),
            knowledge.private_references,
        )

    def _campaign_secret_catalog(
        self,
        campaign_id: str | None,
        plan: Mapping[str, object],
    ) -> tuple[SafePayload, ...]:
        """Merge inline Plan secrets with exact fact references from the Setting."""

        if campaign_id is None:
            raise CommandRejected("Campaign secret catalog requires campaign authority")
        plan = self._retained_preparation(campaign_id, plan)
        setting_facts, _private_references = self._campaign_setting_knowledge(campaign_id, plan)
        remaining = {str(item["id"]): item for item in setting_facts}
        merged: list[SafePayload] = []
        raw_secrets = plan.get("secrets")
        if isinstance(raw_secrets, Sequence) and not isinstance(
            raw_secrets, (str, bytes, bytearray)
        ):
            for index, raw_secret in enumerate(raw_secrets):
                if isinstance(raw_secret, str):
                    merged.append(
                        owned_payload(
                            {
                                "id": f"legacy-secret-{index}",
                                "fact": raw_secret,
                                "known_by": [],
                                "source": "campaign_plan",
                                "source_path": None,
                                "selected_by_plan": True,
                                "reveal": None,
                            }
                        )
                    )
                    continue
                if not isinstance(raw_secret, Mapping):
                    continue
                secret_id = raw_secret.get("id")
                if not isinstance(secret_id, str):
                    continue
                setting_fact_id = raw_secret.get("setting_fact_id")
                if isinstance(setting_fact_id, str):
                    base = remaining.pop(setting_fact_id, None)
                    if base is None or setting_fact_id != secret_id:
                        raise CommandRejected(
                            "Campaign Plan references unavailable Campaign Setting knowledge"
                        )
                    merged.append(
                        owned_payload(
                            {
                                **dict(base),
                                "selected_by_plan": True,
                                "reveal": raw_secret.get("reveal"),
                            }
                        )
                    )
                    continue
                if secret_id in remaining:
                    raise CommandRejected(
                        "Campaign Plan secret collides with Campaign Setting knowledge"
                    )
                merged.append(
                    owned_payload(
                        {
                            **dict(raw_secret),
                            "source": "campaign_plan",
                            "source_path": None,
                            "selected_by_plan": True,
                        }
                    )
                )
        merged.extend(remaining[identity] for identity in sorted(remaining))
        return tuple(merged)

    def campaign_knowledge_catalog(self, session_id: str) -> SafePayload:
        """Project the merged private facts and private Setting reference index."""

        with self._operation():
            context, plan = self._optional_dm_structured_plan(session_id)
            campaign_id = context.session.campaign_id
            assert campaign_id is not None
            secrets = self._campaign_secret_catalog(campaign_id, plan)
            _setting_facts, private_references = self._campaign_setting_knowledge(campaign_id, plan)
            return owned_payload(
                {
                    "secrets": list(secrets),
                    "private_setting_references": list(private_references),
                }
            )

    def campaign_knowledge_catalog_for_plan(
        self,
        campaign_id: str,
        plan: Mapping[str, object],
    ) -> SafePayload:
        """Project the exact opening knowledge before the DM session is admitted."""

        with self._operation():
            secrets = self._campaign_secret_catalog(campaign_id, plan)
            _setting_facts, private_references = self._campaign_setting_knowledge(campaign_id, plan)
            return owned_payload(
                {
                    "secrets": list(secrets),
                    "private_setting_references": list(private_references),
                }
            )

    def _character_update_tool(self, campaign_id: str) -> CatalogTool | None:
        """Select the active Ruleset's bookkeeping mechanic behind the shared interface."""
        tools = [
            tool for tool in self._catalog(campaign_id, include_character_updates=True)
            if "x-sylvia-gm-character-update" in tool.input_schema
        ]
        if len(tools) > 1:
            raise CommandRejected("Character bookkeeping declaration is ambiguous")
        return tools[0] if tools else None

    def _character_update_options(self, campaign_id: str) -> SafePayload:
        """Expose the exact package schema and current revision, never inferred editable fields."""
        tool = self._character_update_tool(campaign_id)
        if tool is None:
            return {}
        schema = tool.input_schema["properties"]["updates"]["items"]
        return owned_payload({
            "character_update_schema": {
                **schema,
                "properties": {
                    **schema["properties"],
                    "expected_revision": {"type": "integer", "minimum": 0},
                },
                "required": [*schema["required"], "expected_revision"],
            },
            "campaign_revision": self._store.snapshot(campaign_id).revision,
        })

    def _inventory_tool(self, campaign_id: str) -> ToolDeclaration | None:
        """Inventory selection survives temporary mechanic, turn and writer restrictions."""
        self._ensure_campaign_not_transitioning(campaign_id)
        declarations = self._store.package_catalog_basis(campaign_id)[-1]
        tools = [
            tool for _, tool in declarations
            if "x-sylvia-gm-inventory" in tool.input_schema
        ]
        if len(tools) > 1:
            raise CommandRejected("Ruleset inventory declaration is ambiguous")
        return tools[0] if tools else None

    def campaign_narrative_options(self, session_id: str) -> SafePayload:
        """Return the exact narrative-state mutations available to one DM session."""

        with self._operation():
            context, plan = self._optional_dm_structured_plan(session_id)
            campaign_id = context.session.campaign_id
            assert campaign_id is not None
            preparation = self._retained_preparation(campaign_id, plan)
            structure = preparation.get("structure")
            acts = (
                [
                    item
                    for item in structure
                    if isinstance(item, Mapping) and isinstance(item.get("id"), str)
                ]
                if isinstance(structure, Sequence)
                and not isinstance(structure, (str, bytes, bytearray))
                else []
            )
            campaign_id = context.session.campaign_id
            assert campaign_id is not None
            snapshot = self._store.snapshot(campaign_id)
            scene_act_ids = [str(item["id"]) for item in acts]
            objectives = preparation.get("objectives")
            objective_ids = [
                str(item["id"])
                for item in (
                    objectives
                    if isinstance(objectives, Sequence)
                    and not isinstance(objectives, (str, bytes, bytearray))
                    else ()
                )
                if isinstance(item, Mapping) and isinstance(item.get("id"), str)
            ]
            plan_item_ids = list(dict.fromkeys((
                *objective_ids, *scene_act_ids,
                *(ending_progress_id(item) for item in intended_endings(plan)),
            )))
            revealed_to_player = {
                secret_id
                for secret_id, audience in snapshot.continuity.reveals.items()
                if isinstance(audience, Sequence)
                and not isinstance(audience, (str, bytes, bytearray))
                and "player" in audience
            }
            eligible_secrets: list[str] = []
            for secret in self._campaign_secret_catalog(context.session.campaign_id, plan):
                if not isinstance(secret, Mapping) or not isinstance(secret.get("id"), str):
                    continue
                secret_id = str(secret["id"])
                if secret_id not in revealed_to_player:
                    eligible_secrets.append(secret_id)
            operations = [
                "record_memory",
                "record_entity",
                "update_scene",
            ]
            if plan_item_ids:
                operations.append("update_plan_progress")
            if eligible_secrets:
                operations.append("reveal_secret")
            character_options = self._character_update_options(campaign_id)
            if character_options:
                operations.append("update_character")
            if self._inventory_tool(campaign_id) is None:
                operations.append("update_inventory")
            return owned_payload(
                {
                    **character_options,
                    "campaign_revision": snapshot.revision,
                    "operations": operations,
                    "scene_act_ids": scene_act_ids,
                    "plan_item_ids": plan_item_ids,
                    "eligible_secret_ids": eligible_secrets,
                }
            )

    @contextmanager
    def builder_session_work(self, session_id: str) -> Iterator[SessionContext]:
        """Hold one active Builder chat operation without claiming gameplay authority."""

        with self._operation():
            initial = self._sessions.context(session_id)
            campaign_id = initial.session.campaign_id
            if campaign_id is None:
                with self._lane(f"builder-chat:{session_id}"):
                    context = self._sessions.context(session_id)
                    if context.session.campaign_id is not None:
                        raise CommandRejected("Builder chat campaign association changed")
                    if context.session.status is not SessionStatus.ACTIVE:
                        raise CommandRejected("Agent session is not active")
                    if context.session.role is not WriterRole.BUILDER:
                        raise CommandRejected("Full Builder tools require a Builder session")
                    yield context
                return
            with self._campaign_work(campaign_id):
                context = self._sessions.context(session_id)
                if context.session.campaign_id != campaign_id:
                    raise CommandRejected("Agent session campaign identity changed")
                if context.session.status is not SessionStatus.ACTIVE:
                    raise CommandRejected("Agent session is not active")
                if context.session.role is not WriterRole.BUILDER:
                    raise CommandRejected("Full Builder tools require a Builder session")
                yield context

    @contextmanager
    def agent_session_work(self, session_id: str) -> Iterator[SessionContext]:
        """Hold the existing campaign-work admission for one live session operation."""

        with self._operation():
            initial = self._sessions.context(session_id)
            campaign_id = initial.session.campaign_id
            if campaign_id is None:
                raise CommandRejected("DM sessions require campaign authority")
            with self._campaign_work(campaign_id):
                context = self._sessions.context(session_id)
                if context.session.campaign_id != campaign_id:
                    raise CommandRejected("Agent session campaign identity changed")
                if context.session.status is not SessionStatus.ACTIVE:
                    raise CommandRejected("Agent session is not active")
                snapshot = self._store.snapshot(campaign_id)
                if snapshot.writer is not context.session.role:
                    raise CommandRejected("Agent session no longer holds campaign writer authority")
                yield context

    def stop_agent_session(self, session_id: str) -> SessionRecord:
        with self._operation(), self._session_campaign_work(session_id):
            context = self._sessions.context(session_id)
            if context.session.campaign_id is not None:
                self._reject_pending_mechanics_choice(context.session.campaign_id)
            return self._sessions.update_status(session_id, SessionStatus.STOPPED)

    def resume_agent_session(self, session_id: str) -> SessionRecord:
        """Resume an explicitly selected stopped writer session."""

        with self._operation():
            return self._store.resume_agent_session(session_id)

    def record_builder_evidence(
        self,
        *,
        session_id: str,
        evidence_id: str,
        source_kind: str,
        content_sha256: str,
        source_locator: str | None,
        safe_excerpt: str,
        claims: tuple[str, ...],
    ) -> BuilderEvidence:
        with self._operation(), self._session_campaign_work(session_id):
            return self._sessions.record_evidence(
                session_id=session_id,
                evidence_id=evidence_id,
                source_kind=source_kind,
                content_sha256=content_sha256,
                source_locator=source_locator,
                safe_excerpt=safe_excerpt,
                claims=claims,
            )

    def require_builder_human_review(
        self,
        *,
        session_id: str,
        review_id: str,
        unsupported_capabilities: tuple[str, ...],
        contradictions: tuple[str, ...],
    ) -> SessionRecord:
        with self._operation(), self._session_campaign_work(session_id):
            return self._sessions.require_human_review(
                session_id=session_id,
                review_id=review_id,
                unsupported_capabilities=unsupported_capabilities,
                contradictions=contradictions,
            )

    def _validate_required_plan_state(self, campaign_id: str, content: SafePayload) -> None:
        """Recheck new mandatory declarations against this campaign's exact retained Ruleset."""
        from sylvia_gm.application.plan_state_admission import validate_plan_state
        from sylvia_gm.domain.content_validation import ContentShapeError

        lock = self._store.current_package_lock(campaign_id)
        manifest = self._store.primary_package_manifest(lock.lock_id)
        if manifest is None or not any(item.required for item in manifest.state_schemas):
            return
        payload = content.get("campaign_plan", {})
        try:
            validate_plan_state(payload if isinstance(payload, Mapping) else {}, manifest)
        except ContentShapeError as error:
            raise CommandRejected(
                "Campaign Plan is missing or violates required Ruleset declarations",
                code="plan_required_state_invalid", diagnostics=error.diagnostics,
            ) from error

    def admit_campaign_plan(
        self,
        *,
        plan_id: str,
        campaign_id: str,
        builder_session_id: str,
        expected_revision: int,
        expected_package_lock_id: str,
        content: SafePayload,
        content_sha256: str,
        provenance_evidence_ids: tuple[str, ...],
        validation_outcome: str,
        acceptance_status: str,
    ) -> CampaignPlan:
        """Admit one exact accepted plan through the existing campaign revision authority."""

        with self._operation(), self._lane(campaign_id):
            if "sequel" in content:
                raise CommandRejected("Live adventure additions require explicit sequel review")
            try:
                captured_content = owned_payload(content)
                validation = PlanValidationOutcome(validation_outcome)
                acceptance = PlanAcceptanceStatus(acceptance_status)
                validate_authority_identity(plan_id, "Campaign Plan identity")
                validate_authority_identity(campaign_id, "Campaign identity")
                validate_authority_identity(builder_session_id, "Builder session identity")
                validate_authority_identity(
                    expected_package_lock_id,
                    "Campaign Plan expected package lock identity",
                )
                validate_authority_identity(
                    content_sha256,
                    "Campaign Plan content SHA-256",
                    sha256=True,
                )
                if expected_revision < 0:
                    raise ValueError("Expected campaign revision cannot be negative")
                provenance = tuple(provenance_evidence_ids)
                if not provenance:
                    raise ValueError("Campaign Plan requires explicit provenance")
                for evidence_id in provenance:
                    validate_authority_identity(evidence_id, "Campaign Plan provenance evidence")
            except (TypeError, ValueError) as error:
                raise CommandRejected(
                    "Campaign Plan admission payload is invalid",
                    code="invalid_campaign_plan",
                ) from error
            actual_content_sha256 = hashlib.sha256(
                canonical_json_bytes(captured_content)
            ).hexdigest()
            if actual_content_sha256 != content_sha256:
                raise CommandRejected(
                    "Campaign Plan content identity does not match",
                    code="campaign_plan_content_mismatch",
                )
            payload_sha256 = campaign_plan_payload_sha256(
                plan_id=plan_id,
                campaign_id=campaign_id,
                source_session_id=builder_session_id,
                expected_revision=expected_revision,
                expected_package_lock_id=expected_package_lock_id,
                content_sha256=content_sha256,
                content=captured_content,
                provenance_evidence_ids=provenance,
                validation_outcome=validation,
                acceptance_status=acceptance,
            )
            existing = self._store.campaign_plan_hit(plan_id)
            if existing is not None:
                if existing.payload_sha256 != payload_sha256:
                    raise IdempotencyConflict("Campaign Plan identity payload does not match")
                return existing
            if acceptance is PlanAcceptanceStatus.ACCEPTED:
                self._validate_required_plan_state(campaign_id, captured_content)
            return self._store.commit_campaign_plan(
                plan_id=plan_id,
                campaign_id=campaign_id,
                source_session_id=builder_session_id,
                expected_revision=expected_revision,
                expected_package_lock_id=expected_package_lock_id,
                content_sha256=content_sha256,
                content=captured_content,
                provenance_evidence_ids=provenance,
                validation_outcome=validation,
                acceptance_status=acceptance,
                payload_sha256=payload_sha256,
            )

    def preview_campaign_sequel(
        self, *, campaign_id: str, session_id: str, source_id: str,
    ) -> SafePayload:
        """Read a candidate and its continuity review without adopting content."""
        from sylvia_gm.application.campaign_sequels import preview

        with self._operation(), self._lane(campaign_id):
            return owned_payload(preview(self, campaign_id, session_id, source_id))

    def campaign_sequel_context(self, *, campaign_id: str, session_id: str) -> SafePayload:
        """Capture selected-campaign facts for an independent, read-only authoring prompt."""
        with self._operation(), self._lane(campaign_id):
            context = self._active_dm_context(session_id)
            if context.session.campaign_id != campaign_id:
                raise CommandRejected("The selected GM does not belong to this campaign")
            snapshot = self._store.snapshot(campaign_id)
            accepted = self.accepted_campaign_plan(campaign_id)
            if accepted is None:
                raise CommandRejected("This campaign has no accepted authored adventure")
            return owned_payload({
                "campaign_name": snapshot.display_name,
                "accepted_plan": accepted.content.get("campaign_plan"),
                "current_scene": snapshot.continuity.current_scene,
                "facts": list(snapshot.continuity.facts),
                "plan_progress": list(snapshot.continuity.plan_progress),
                "participants": [{"id": item.participant_id, "data": item.data}
                                 for item in snapshot.participants],
                "ending": self.session_history_page(session_id).campaign_ending,
                "recent_history": [
                    {"kind": item.kind.value,
                     "excerpt": canonical_json_bytes(item.content).decode("utf-8")[:4000]}
                    for item in self.session_history_page(session_id, limit=64).messages
                    if item.kind in {SessionMessageKind.USER, SessionMessageKind.TALK,
                                     SessionMessageKind.CANONICAL_REFERENCE,
                                     SessionMessageKind.STEERING}
                ],
                "source_references": [
                    {"plan_id": row.plan_id, "content_sha256": row.content_sha256,
                     "source_id": row.content.get("sequel", {}).get("source_id")}
                    for row in self._store.campaign_adventures(campaign_id)
                ],
            })

    def append_campaign_sequel(
        self, *, campaign_id: str, session_id: str, operation_id: str, source_id: str,
        expected_revision: int, preview_sha256: str, review: object,
    ) -> SafePayload:
        """Append reviewed story preparation through the current GM owner, once."""
        from sylvia_gm.application.campaign_sequels import append

        with self._operation():
            return owned_payload(append(
                self, campaign_id=campaign_id, session_id=session_id, operation_id=operation_id,
                source_id=source_id, expected_revision=expected_revision,
                preview_sha256=preview_sha256, review=review,
            ))

    def campaign_plan(self, plan_id: str) -> CampaignPlan:
        with self._operation():
            plan = self._store.campaign_plan_hit(plan_id)
            if plan is None:
                raise CommandRejected("Campaign Plan is unavailable")
            return plan

    def accepted_campaign_plan(self, campaign_id: str) -> CampaignPlan:
        with self._operation():
            plan = self._store.accepted_campaign_plan(campaign_id)
            if plan is None:
                raise CommandRejected("Accepted Campaign Plan is unavailable")
            return plan

    def campaign_adventures(self, campaign_id: str) -> tuple[CampaignPlan, ...]:
        """Read the validated accepted sequence, excluding superseded Builder drafts."""
        with self._operation():
            return self._store.campaign_adventures(campaign_id)

    def handoff_builder_to_dm(
        self,
        *,
        handoff_id: str,
        campaign_id: str,
        builder_session_id: str,
        dm_session_id: str,
        dm_provider_id: str,
        dm_model_id: str,
        expected_writer: WriterRole,
        expected_revision: int,
        expected_package_lock_id: str,
        expected_package_candidate_id: str | None,
        plan_id: str,
        expected_plan_revision: int,
        expected_plan_content_sha256: str,
        completed_turn_id: str,
        model_guidance_override: bool = False,
    ) -> BuilderDMHandoff:
        """Atomically transfer one existing quiescent campaign from Builder to DM."""

        with self._operation(), self._lane(campaign_id):
            try:
                for value, label in (
                    (handoff_id, "Builder-to-DM handoff identity"),
                    (campaign_id, "Campaign identity"),
                    (builder_session_id, "Builder session identity"),
                    (dm_session_id, "DM session identity"),
                    (dm_provider_id, "DM provider identity"),
                    (dm_model_id, "DM model identity"),
                    (expected_package_lock_id, "Expected package lock identity"),
                    (plan_id, "Campaign Plan identity"),
                    (completed_turn_id, "Completed TURN identity"),
                ):
                    validate_authority_identity(value, label)
                if expected_package_candidate_id is not None:
                    validate_authority_identity(
                        expected_package_candidate_id, "Expected package candidate identity"
                    )
                validate_authority_identity(
                    expected_plan_content_sha256,
                    "Expected Campaign Plan content SHA-256",
                    sha256=True,
                )
                if expected_revision < 0 or expected_plan_revision <= 0:
                    raise ValueError("Handoff expected revisions are invalid")
            except (TypeError, ValueError) as error:
                raise CommandRejected(
                    "Builder-to-DM handoff request is invalid",
                    code="invalid_builder_dm_handoff",
                ) from error
            existing = self._store.handoff_hit(handoff_id)
            guidance = (
                existing.model_guidance
                if existing is not None
                else self._model_guidance_for_lock(
                    lock_id=expected_package_lock_id,
                    model_id=dm_model_id,
                    override_accepted=model_guidance_override,
                )
            )
            payload_sha256 = builder_dm_handoff_payload_sha256(
                handoff_id=handoff_id,
                campaign_id=campaign_id,
                builder_session_id=builder_session_id,
                dm_session_id=dm_session_id,
                dm_provider_id=dm_provider_id,
                dm_model_id=dm_model_id,
                expected_writer=expected_writer,
                expected_revision=expected_revision,
                expected_package_lock_id=expected_package_lock_id,
                expected_package_candidate_id=expected_package_candidate_id,
                plan_id=plan_id,
                expected_plan_revision=expected_plan_revision,
                expected_plan_content_sha256=expected_plan_content_sha256,
                completed_turn_id=completed_turn_id,
                model_guidance_sha256=model_guidance_payload_sha256(guidance),
            )
            if existing is not None:
                if existing.payload_sha256 != payload_sha256:
                    raise IdempotencyConflict(
                        "Builder-to-DM handoff identity payload does not match"
                    )
                return existing
            if not guidance.may_continue:
                raise CommandRejected(
                    "Selected model capability guidance requires explicit confirmation",
                    code=guidance.warning_code or "model_guidance_confirmation_required",
                )
            self._validate_required_plan_state(campaign_id, self.campaign_plan(plan_id).content)
            self._reserve_campaign_transition(campaign_id)
            try:
                return self._store.commit_builder_dm_handoff(
                    handoff_id=handoff_id,
                    campaign_id=campaign_id,
                    builder_session_id=builder_session_id,
                    dm_session_id=dm_session_id,
                    dm_provider_id=dm_provider_id,
                    dm_model_id=dm_model_id,
                    expected_writer=expected_writer,
                    expected_revision=expected_revision,
                    expected_package_lock_id=expected_package_lock_id,
                    expected_package_candidate_id=expected_package_candidate_id,
                    plan_id=plan_id,
                    expected_plan_revision=expected_plan_revision,
                    expected_plan_content_sha256=expected_plan_content_sha256,
                    completed_turn_id=completed_turn_id,
                    model_guidance=guidance,
                    payload_sha256=payload_sha256,
                )
            finally:
                self._release_campaign_transition(campaign_id)

    def builder_dm_handoff(self, handoff_id: str) -> BuilderDMHandoff:
        with self._operation():
            handoff = self._store.handoff_hit(handoff_id)
            if handoff is None:
                raise CommandRejected("Builder-to-DM handoff is unavailable")
            return handoff

    def admit_archive(self, *, campaign_id: str, expected_revision: int) -> CampaignSnapshot:
        """Check only the F4 active-work Archive admission seam; mutate no lifecycle."""

        with self._operation(), self._lane(campaign_id):
            try:
                validate_authority_identity(campaign_id, "Campaign identity")
                if expected_revision < 0:
                    raise ValueError("Archive expected revision must be non-negative")
            except ValueError as error:
                raise CommandRejected("Archive admission request is invalid") from error
            self._reserve_campaign_transition(campaign_id)
            try:
                return self._store.assert_archive_admissible(
                    campaign_id=campaign_id,
                    expected_revision=expected_revision,
                )
            finally:
                self._release_campaign_transition(campaign_id)

    def campaign_ending_choice_text(
        self, session_id: str, message_id: str, choice: SafePayload,
    ) -> str:
        """Admit a real player's ending button and produce its explicit OOC instruction."""
        with self._operation(), self._session_campaign_work(session_id):
            ending = self.session_history_page(session_id).campaign_ending
            selected = choice.get("choice")
            if (ending is None or set(choice) != {"ending_id", "choice"}
                    or choice.get("ending_id") != ending["ending_id"]
                    or selected not in {"continue", "finish"}):
                raise CommandRejected("This campaign ending is unavailable")
            previous = next((item for item in self.session_context(session_id).messages
                             if item.message_id == message_id), None)
            if ending["choice"] is not None and (
                previous is None or previous.content.get("campaign_ending_choice") != choice
            ):
                raise CommandRejected(
                    "An ending choice is already saved; use OOC to change direction"
                )
            if selected == "continue":
                if not ending["open_world"]:
                    raise CommandRejected("This ending does not support open-world continuation")
                return "Continue adventuring in this world."
            return "Finish the story."

    def append_agent_message(
        self,
        *,
        session_id: str,
        message_id: str,
        kind: SessionMessageKind,
        content: SafePayload,
    ) -> SessionMessage:
        with self._operation(), self._session_campaign_work(session_id):
            return self._sessions.append_message(
                session_id=session_id,
                message_id=message_id,
                kind=kind,
                content=content,
            )

    def append_live_steering_message(
        self,
        *,
        session_id: str,
        message_id: str,
        kind: SessionMessageKind,
        content: SafePayload,
    ) -> SessionMessage:
        """Persist steering-bound prose without reacquiring an independent chat lane."""

        if kind not in {
            SessionMessageKind.SAFE_THINK,
            SessionMessageKind.TALK,
            SessionMessageKind.STEERING,
        }:
            raise TypeError("Live steering persistence only accepts safe prose or steering")
        with self._operation():
            context = self._sessions.context(session_id)
            if context.session.status is not SessionStatus.ACTIVE:
                raise CommandRejected("Agent session is not active")
            if context.session.role not in {WriterRole.BUILDER, WriterRole.DM}:
                raise CommandRejected("Live steering requires an agent writer session")
            return self._sessions.append_message(
                session_id=session_id,
                message_id=message_id,
                kind=kind,
                content=content,
            )

    def append_session_canonical_references(
        self, session_id: str, events: tuple[CanonicalEvent, ...]
    ) -> tuple[SessionMessage, ...]:
        with self._operation(), self._session_campaign_work(session_id):
            return self._sessions.append_canonical_references(session_id, events)

    def confirm_new_campaign(
        self,
        *,
        operation_id: str,
        display_name: str,
        package_lock: PackageLock,
        turn_id: str,
        package_candidate_id: str | None = None,
        builder_session_id: str | None = None,
        builder_provider_id: str | None = None,
        builder_model_id: str | None = None,
        builder_selected_skill: str = "BUILD CAMPAIGN PLAN",
        participants: tuple[Participant, ...] = (),
        initial_state: tuple[StateValue, ...] = (),
    ) -> CampaignConfirmation:
        """Create the stable campaign aggregate before either Builder lane begins."""

        with self._operation():
            builder_session: SessionRecord | None = None
            try:
                for value, label in (
                    (operation_id, "Campaign operation identity"),
                    (display_name, "Campaign display name"),
                    (turn_id, "TURN identity"),
                ):
                    validate_authority_identity(value, label)
                if package_candidate_id is not None:
                    validate_authority_identity(
                        package_candidate_id, "Campaign package candidate identity"
                    )
                session_values = (
                    builder_session_id,
                    builder_provider_id,
                    builder_model_id,
                )
                if any(value is not None for value in session_values):
                    if (
                        builder_session_id is None
                        or builder_provider_id is None
                        or builder_model_id is None
                    ):
                        raise ValueError("Builder session metadata must be complete")
                    for value, label in (
                        (builder_session_id, "Builder session identity"),
                        (builder_provider_id, "Builder provider identity"),
                        (builder_model_id, "Builder model identity"),
                    ):
                        validate_authority_identity(value, label)
                    if builder_selected_skill not in BUILDER_SKILLS:
                        raise ValueError("Builder selected skill is unsupported")
                captured = _capture_campaign_creation(
                    CampaignCreate(
                        display_name=display_name,
                        writer=WriterRole.BUILDER,
                        package_lock=package_lock,
                        turn_id=turn_id,
                        participants=participants,
                        initial_state=initial_state,
                    )
                )
            except (TypeError, ValueError) as error:
                raise CommandRejected(
                    "Campaign confirmation authority is invalid",
                    code="invalid_authority_identity",
                ) from error
            semantic_values: dict[str, JsonValue] = {
                "display_name": display_name,
                "package_lock": owned_json_value(_package_lock_payload(captured.package_lock)),
                "turn_id": turn_id,
            }
            if captured.participants:
                semantic_values["participants"] = [
                    _participant_context_payload(participant)
                    for participant in sorted(
                        captured.participants,
                        key=lambda item: item.participant_id,
                    )
                ]
            if captured.initial_state:
                semantic_values["initial_state"] = [
                    {
                        "namespace": item.namespace,
                        "key": item.key,
                        "value": item.value,
                    }
                    for item in sorted(
                        captured.initial_state,
                        key=lambda item: (item.namespace, item.key),
                    )
                ]
            if package_candidate_id is not None:
                semantic_values["package_candidate_id"] = package_candidate_id
            if builder_session_id is not None:
                assert builder_provider_id is not None and builder_model_id is not None
                semantic_values["builder_session"] = {
                    "session_id": builder_session_id,
                    "provider_id": builder_provider_id,
                    "model_id": builder_model_id,
                    "selected_skill": builder_selected_skill,
                }
            semantic_payload = _campaign_operation_payload(
                operation_id=operation_id,
                operation_kind="confirm_new_campaign",
                values=semantic_values,
            )
            payload_sha256 = hashlib.sha256(canonical_json_bytes(semantic_payload)).hexdigest()
            existing = self._store.campaign_operation_hit(
                operation_id=operation_id,
                operation_kind="confirm_new_campaign",
                payload_sha256=payload_sha256,
            )
            if existing is not None:
                if not isinstance(existing, CampaignConfirmation):
                    raise IdempotencyConflict("Campaign operation result kind does not match")
                return existing
            self._validate_campaign_inputs(captured)
            campaign = CampaignRecord(
                campaign_id=str(uuid.uuid4()),
                display_name=display_name,
                lifecycle=LifecycleState.ACTIVE,
                writer=WriterRole.BUILDER,
                revision=0,
            )
            if builder_session_id is not None:
                assert builder_provider_id is not None and builder_model_id is not None
                builder_session = SessionRecord(
                    session_id=builder_session_id,
                    campaign_id=campaign.campaign_id,
                    role=WriterRole.BUILDER,
                    provider_id=builder_provider_id,
                    model_id=builder_model_id,
                    status=SessionStatus.ACTIVE,
                    selected_skill=builder_selected_skill,
                )
            return self._store.confirm_new_campaign(
                operation_id=operation_id,
                semantic_payload=semantic_payload,
                payload_sha256=payload_sha256,
                campaign=campaign,
                package_lock=captured.package_lock,
                turn_id=turn_id,
                builder_session=builder_session,
                participants=captured.participants,
                initial_state=captured.initial_state,
            )

    def create_guided_campaign_draft(
        self,
        *,
        operation_id: str,
        display_name: str,
        builder_session_id: str,
        builder_provider_id: str,
        builder_model_id: str,
    ) -> GuidedCampaignDraft:
        """Create the stable unbound Campaign Aggregate before the first interview answer."""

        with self._operation():
            try:
                for value, label in (
                    (operation_id, "Campaign operation identity"),
                    (display_name, "Campaign display name"),
                    (builder_session_id, "Builder session identity"),
                    (builder_provider_id, "Builder provider identity"),
                    (builder_model_id, "Builder model identity"),
                ):
                    validate_authority_identity(value, label)
            except (TypeError, ValueError) as error:
                raise CommandRejected(
                    "Guided Campaign Draft authority is invalid",
                    code="invalid_authority_identity",
                ) from error
            campaign_id = (
                "campaign-draft-" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]
            )
            session = SessionRecord(
                session_id=builder_session_id,
                campaign_id=campaign_id,
                role=WriterRole.BUILDER,
                provider_id=builder_provider_id,
                model_id=builder_model_id,
                status=SessionStatus.ACTIVE,
                selected_skill="BUILD CAMPAIGN PLAN",
            )
            campaign = CampaignRecord(
                campaign_id=campaign_id,
                display_name=display_name,
                lifecycle=LifecycleState.ACTIVE,
                writer=WriterRole.BUILDER,
                revision=0,
            )
            return self._store.create_guided_campaign_draft(
                operation_id=operation_id,
                campaign=campaign,
                builder_session=session,
            )

    def _guided_answer_value(self, session_id: str, step: str) -> Mapping[str, object] | None:
        """Resolve one accepted Interview answer from its canonical User message."""

        context = self._sessions.context(session_id)
        states = tuple(
            message
            for message in context.messages
            if message.kind is SessionMessageKind.EVIDENCE
            and message.content.get("kind") == "campaign_interview_state"
        )
        if not states:
            return None
        references = states[-1].content.get("answers")
        if not isinstance(references, Mapping):
            return None
        message_id = references.get(step)
        message = next(
            (
                item
                for item in context.messages
                if item.message_id == message_id and item.kind is SessionMessageKind.USER
            ),
            None,
        )
        value = None if message is None else message.content.get("interview_value")
        return cast(Mapping[str, object], value) if isinstance(value, Mapping) else None

    def _package_interview_contribution(
        self,
        *,
        candidate_id: str,
        owner_kind: str,
        package_id: str | None = None,
    ) -> InterviewContribution | None:
        """Read one hash-listed package sidecar, failing closed when it is malformed."""

        try:
            candidate = self.package_candidate(candidate_id)
        except CommandRejected as error:
            if error.code == "candidate_unavailable":
                return None
            raise
        entries = tuple(
            entry
            for entry in candidate.package_lock.entries
            if (
                (owner_kind == "ruleset" and entry.role is PackageRole.PRIMARY)
                or (owner_kind == "campaign_setting" and entry.package_id == package_id)
            )
        )
        if len(entries) != 1:
            return None
        entry = entries[0]
        paths = self.package_file_paths(
            entry.package_id,
            entry.version,
            entry.sha256,
            maximum_files=4_096,
        )
        if INTERVIEW_CONTRIBUTION_PATH not in paths:
            return None
        raw = self._store.package_file_bytes(
            entry.package_id,
            entry.version,
            entry.sha256,
            INTERVIEW_CONTRIBUTION_PATH,
            maximum_bytes=INTERVIEW_CONTRIBUTION_BYTE_LIMIT,
        )
        if raw is None:
            raise CommandRejected(
                "Selected artifact Interview Contribution is unavailable",
                code="interview_contribution_unavailable",
            )
        try:
            return parse_interview_contribution_bytes(
                raw,
                expected_owner_kind=owner_kind,
            )
        except ValueError as error:
            raise CommandRejected(
                f"Selected {owner_kind.replace('_', ' ')} Interview Contribution is invalid",
                code="invalid_interview_contribution",
            ) from error

    def guided_campaign_contribution(
        self,
        *,
        builder_session_id: str,
        contribution_step: str,
        selection_overrides: Mapping[str, Mapping[str, object]] | None = None,
    ) -> SafePayload | None:
        """Project the exact selected artifact's optional restricted question group."""

        if contribution_step not in _GUIDED_CONTRIBUTION_STEPS:
            raise CommandRejected("Campaign Interview Contribution step is unsupported")
        _, owner_kind = _GUIDED_CONTRIBUTION_STEPS[contribution_step]
        selections = {
            step: value for step in ("ruleset", "setting", "plan")
            if (value := self._guided_answer_value(builder_session_id, step)) is not None
        }
        selections.update(selection_overrides or {})
        content = resolve_interview_content(self, self._content_storage_root, selections)
        if owner_kind == "campaign_plan":
            return content.plan_contribution
        if content.candidate is None:
            return None
        contribution = self._package_interview_contribution(
            candidate_id=content.candidate.candidate_id,
            owner_kind=owner_kind,
            package_id=content.setting_id,
        )
        return None if contribution is None else contribution.as_payload()

    def _guided_contribution_object(
        self,
        *,
        builder_session_id: str,
        contribution_step: str,
        selection_overrides: Mapping[str, Mapping[str, object]] | None = None,
    ) -> InterviewContribution | None:
        payload = self.guided_campaign_contribution(
            builder_session_id=builder_session_id,
            contribution_step=contribution_step,
            selection_overrides=selection_overrides,
        )
        if payload is None:
            return None
        return parse_interview_contribution_bytes(
            canonical_json_bytes(payload),
            expected_owner_kind=_GUIDED_CONTRIBUTION_STEPS[contribution_step][1],
        )

    def _next_guided_question(
        self, session_id: str, current_step: str, answer: SafePayload,
    ) -> tuple[str, str]:
        """Choose content first, then ask required questions from its saved copies."""
        overrides = {current_step: answer}
        ruleset_answer = (
            answer if current_step == "ruleset"
            else self._guided_answer_value(session_id, "ruleset")
        )
        if ruleset_answer is not None and ruleset_answer.get("candidate_id") is None:
            if current_step == "ruleset":
                return "name", _GUIDED_CAMPAIGN_QUESTIONS["name"]
            if current_step == "name":
                return "boundaries_preferences", _GUIDED_CAMPAIGN_QUESTIONS[
                    "boundaries_preferences"
                ]
        if current_step in {"plan", *_GUIDED_CONTRIBUTION_STEPS}:
            for required_step in ("ruleset", "setting", "plan"):
                if required_step not in overrides and self._guided_answer_value(
                    session_id, required_step,
                ) is None:
                    return required_step, _GUIDED_CAMPAIGN_QUESTIONS[required_step]
            for step in _GUIDED_CONTRIBUTION_STEPS:
                contribution = self._guided_contribution_object(
                    builder_session_id=session_id, contribution_step=step,
                    selection_overrides=overrides,
                )
                if contribution is None:
                    continue
                # Later answers are detached by this save, including on an exact retry.
                precedes_current = (
                    _GUIDED_CAMPAIGN_STEPS.index(step)
                    < _GUIDED_CAMPAIGN_STEPS.index(current_step)
                )
                previous = (
                    answer if step == current_step else
                    self._guided_answer_value(session_id, step) if precedes_current else None
                )
                expected = hashlib.sha256(
                    canonical_json_bytes(contribution.as_payload()),
                ).hexdigest()
                if previous is not None and previous.get("contribution_sha256") == expected:
                    try:
                        validate_interview_contribution_answers(
                            contribution,
                            {key: value for key, value in previous.items()
                             if key != "contribution_sha256"},
                        )
                    except ValueError:
                        pass
                    else:
                        continue
                return step, contribution.question_text()
            return "name", _GUIDED_CAMPAIGN_QUESTIONS["name"]
        step = _GUIDED_CAMPAIGN_STEPS[_GUIDED_CAMPAIGN_STEPS.index(current_step) + 1]
        return step, _GUIDED_CAMPAIGN_QUESTIONS[step]

    def navigate_guided_campaign_draft(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        builder_session_id: str,
        expected_construction_revision: int,
        target_step: str,
    ) -> GuidedCampaignDraft:
        """Save a revisited question without treating it as a changed Interview answer."""
        with self._operation(), self._campaign_work(campaign_id):
            try:
                for value, label in (
                    (operation_id, "Campaign operation identity"),
                    (campaign_id, "Campaign identity"),
                    (builder_session_id, "Builder session identity"),
                ):
                    validate_authority_identity(value, label)
                if target_step not in _GUIDED_CAMPAIGN_STEPS:
                    raise ValueError("Campaign Interview step is invalid")
                if (
                    not isinstance(expected_construction_revision, int)
                    or isinstance(expected_construction_revision, bool)
                    or expected_construction_revision < 0
                ):
                    raise ValueError("Construction revision is invalid")
            except (TypeError, ValueError) as error:
                raise CommandRejected("Campaign Interview navigation is invalid") from error
            return self._store.navigate_guided_campaign_draft(
                operation_id=operation_id, campaign_id=campaign_id,
                builder_session_id=builder_session_id,
                expected_construction_revision=expected_construction_revision,
                target_step=target_step, steps=_GUIDED_CAMPAIGN_STEPS,
            )

    def advance_guided_campaign_draft(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        builder_session_id: str,
        expected_construction_revision: int,
        current_step: str,
        visible_answer: str,
        answer: SafePayload,
    ) -> GuidedCampaignDraft:
        """Persist one genuine interview answer before exposing the next question."""

        with self._operation(), self._campaign_work(campaign_id):
            try:
                validate_authority_identity(operation_id, "Campaign operation identity")
                validate_authority_identity(campaign_id, "Campaign identity")
                validate_authority_identity(builder_session_id, "Builder session identity")
                if current_step not in _GUIDED_CAMPAIGN_STEPS[:-1]:
                    raise ValueError("Guided Campaign step is not answerable")
                if (
                    not isinstance(expected_construction_revision, int)
                    or expected_construction_revision < 0
                ):
                    raise ValueError("Construction revision is invalid")
                if (
                    not isinstance(visible_answer, str)
                    or not visible_answer.strip()
                    or len(visible_answer) > _MAX_GUIDED_VISIBLE_ANSWER_CHARS
                ):
                    raise ValueError("Visible answer is invalid")
                captured_answer = owned_payload(answer)
            except (TypeError, ValueError) as error:
                raise CommandRejected(
                    "Guided Campaign answer is invalid",
                    code="invalid_campaign_interview_answer",
                ) from error
            if current_step in _GUIDED_CONTRIBUTION_STEPS:
                contribution = self._guided_contribution_object(
                    builder_session_id=builder_session_id,
                    contribution_step=current_step,
                )
                if contribution is None:
                    raise CommandRejected(
                        "Selected artifact has no Interview Contribution",
                        code="interview_contribution_unavailable",
                    )
                try:
                    captured_answer = validate_interview_contribution_answers(
                        contribution,
                        captured_answer,
                    )
                    captured_answer = owned_payload({
                        **captured_answer,
                        "contribution_sha256": hashlib.sha256(
                            canonical_json_bytes(contribution.as_payload()),
                        ).hexdigest(),
                    })
                except ValueError as error:
                    raise CommandRejected(
                        "Campaign Interview Contribution answer is invalid",
                        code="invalid_interview_contribution_answer",
                    ) from error
            step_index = _GUIDED_CAMPAIGN_STEPS.index(current_step)
            next_step, next_question = self._next_guided_question(
                builder_session_id, current_step, captured_answer,
            )
            return self._store.advance_guided_campaign_draft(
                operation_id=operation_id,
                campaign_id=campaign_id,
                builder_session_id=builder_session_id,
                expected_construction_revision=expected_construction_revision,
                current_step=current_step,
                visible_answer=visible_answer,
                answer=captured_answer,
                preceding_steps=_GUIDED_CAMPAIGN_STEPS[:step_index],
                next_step=next_step,
                next_question=next_question,
            )

    def bind_guided_campaign_basis(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        display_name: str,
        package_lock: PackageLock,
        turn_id: str,
        builder_session_id: str,
        package_candidate_id: str | None = None,
        participants: tuple[Participant, ...] = (),
        initial_state: tuple[StateValue, ...] = (),
    ) -> CampaignConfirmation:
        """Bind exact content to an existing unbound Draft without replacing its identity."""

        with self._operation(), self._campaign_work(campaign_id):
            try:
                for value, label in (
                    (operation_id, "Campaign operation identity"),
                    (campaign_id, "Campaign identity"),
                    (display_name, "Campaign display name"),
                    (turn_id, "TURN identity"),
                    (builder_session_id, "Builder session identity"),
                ):
                    validate_authority_identity(value, label)
                if package_candidate_id is not None:
                    validate_authority_identity(
                        package_candidate_id, "Campaign package candidate identity"
                    )
                session = self._sessions.context(builder_session_id).session
                if (
                    session.campaign_id != campaign_id
                    or session.role is not WriterRole.BUILDER
                    or session.status is not SessionStatus.ACTIVE
                ):
                    raise ValueError("Guided Campaign Builder session is unavailable")
                captured = _capture_campaign_creation(
                    CampaignCreate(
                        display_name=display_name,
                        writer=WriterRole.BUILDER,
                        package_lock=package_lock,
                        turn_id=turn_id,
                        participants=participants,
                        initial_state=initial_state,
                    )
                )
            except (TypeError, ValueError) as error:
                raise CommandRejected(
                    "Guided Campaign binding authority is invalid",
                    code="invalid_authority_identity",
                ) from error
            semantic_values: dict[str, JsonValue] = {
                "display_name": display_name,
                "package_lock": owned_json_value(_package_lock_payload(captured.package_lock)),
                "turn_id": turn_id,
                "builder_session": {
                    "session_id": session.session_id,
                    "provider_id": session.provider_id,
                    "model_id": session.model_id,
                    "selected_skill": session.selected_skill or "BUILD CAMPAIGN PLAN",
                },
            }
            if captured.participants:
                semantic_values["participants"] = [
                    _participant_context_payload(participant)
                    for participant in sorted(
                        captured.participants, key=lambda item: item.participant_id
                    )
                ]
            if captured.initial_state:
                semantic_values["initial_state"] = [
                    {"namespace": item.namespace, "key": item.key, "value": item.value}
                    for item in sorted(
                        captured.initial_state, key=lambda item: (item.namespace, item.key)
                    )
                ]
            if package_candidate_id is not None:
                semantic_values["package_candidate_id"] = package_candidate_id
            semantic_payload = _campaign_operation_payload(
                operation_id=operation_id,
                operation_kind="confirm_new_campaign",
                values=semantic_values,
            )
            payload_sha256 = hashlib.sha256(canonical_json_bytes(semantic_payload)).hexdigest()
            existing = self._store.campaign_operation_hit(
                operation_id=operation_id,
                operation_kind="confirm_new_campaign",
                payload_sha256=payload_sha256,
            )
            if existing is not None:
                if not isinstance(existing, CampaignConfirmation):
                    raise IdempotencyConflict("Campaign operation result kind does not match")
                return existing
            self._validate_campaign_inputs(captured)
            required_interview_steps = {
                "ruleset",
                "setting",
                "plan",
                "name",
                "participants",
                "boundaries_preferences",
                "specific_requests",
            }
            ruleset_answer = self._guided_answer_value(builder_session_id, "ruleset")
            if (
                not captured.package_lock.entries
                and ruleset_answer is not None
                and ruleset_answer.get("candidate_id") is None
            ):
                required_interview_steps.difference_update({"setting", "plan", "participants"})
            for contribution_step in _GUIDED_CONTRIBUTION_STEPS:
                if (
                    self._guided_contribution_object(
                        builder_session_id=builder_session_id,
                        contribution_step=contribution_step,
                    )
                    is not None
                ):
                    required_interview_steps.add(contribution_step)
            return self._store.bind_guided_campaign_basis(
                operation_id=operation_id,
                semantic_payload=semantic_payload,
                payload_sha256=payload_sha256,
                campaign_id=campaign_id,
                display_name=display_name,
                package_lock=captured.package_lock,
                turn_id=turn_id,
                builder_session_id=builder_session_id,
                participants=captured.participants,
                initial_state=captured.initial_state,
                required_interview_steps=frozenset(required_interview_steps),
            )

    def start_interview_builder_chat(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        parent_session_id: str,
        expected_construction_revision: int,
        purpose: str,
        visible_answer: str,
        child_session_id: str,
        trigger_message_id: str | None = None,
        references: SafePayload | None = None,
    ) -> GuidedCreationSubflow:
        """Launch reusable-content work as an independent saved Builder conversation."""

        with self._operation(), self._campaign_work(campaign_id):
            for value, label in (
                (operation_id, "Builder launch operation"),
                (campaign_id, "Campaign identity"),
                (parent_session_id, "Campaign Interview session"),
                (child_session_id, "Builder chat identity"),
            ):
                validate_authority_identity(value, label)
            if (
                purpose not in {"ruleset", "setting", "plan", "player_character", "companion"}
                or trigger_message_id is not None
                or not isinstance(expected_construction_revision, int)
                or isinstance(expected_construction_revision, bool)
                or expected_construction_revision < 0
                or not isinstance(visible_answer, str)
                or not visible_answer.strip()
                or len(visible_answer) > _MAX_GUIDED_VISIBLE_ANSWER_CHARS
            ):
                raise CommandRejected("Builder launch context is invalid")
            parent_context = self._sessions.context(parent_session_id)
            parent = parent_context.session
            if (
                parent.campaign_id != campaign_id
                or parent.role is not WriterRole.BUILDER
                or parent.status is not SessionStatus.ACTIVE
            ):
                raise CommandRejected("Campaign Interview session is unavailable")
            expected_step = _GUIDED_SUBFLOW_STEPS[purpose]
            current_state = next(
                (
                    message.content for message in reversed(parent_context.messages)
                    if message.kind is SessionMessageKind.EVIDENCE
                    and message.content.get("kind") == "campaign_interview_state"
                ),
                {},
            )
            if purpose == "plan" and current_state.get("current_step") == "participants":
                selected_plan = self._guided_answer_value(parent_session_id, "plan")
                retained_plan = references.get("plan") if references is not None else None
                if (
                    not isinstance(retained_plan, Mapping)
                    or not selected_plan
                    or not selected_plan.get("plan_id")
                    or retained_plan.get("selection_id") != selected_plan["plan_id"]
                ):
                    raise CommandRejected("Character completion needs the selected Campaign Plan")
                expected_step = "participants"
            child = SessionRecord(
                session_id=child_session_id,
                campaign_id=None,
                role=WriterRole.BUILDER,
                provider_id=parent.provider_id,
                model_id=parent.model_id,
            )
            return self._store.start_interview_builder_chat(
                operation_id=operation_id,
                campaign_id=campaign_id,
                parent_session_id=parent_session_id,
                expected_construction_revision=expected_construction_revision,
                purpose=purpose,
                expected_step=expected_step,
                visible_answer=visible_answer.strip(),
                child_session=child,
                references=references,
            )

    def start_guided_creation_subflow(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        parent_session_id: str,
        expected_construction_revision: int,
        purpose: str,
        visible_answer: str,
        child_session_id: str,
        trigger_message_id: str | None = None,
    ) -> GuidedCreationSubflow:
        """Suspend one interview question and open its linked Builder chat."""

        with self._operation(), self._campaign_work(campaign_id):
            try:
                for value, label in (
                    (operation_id, "Campaign operation identity"),
                    (campaign_id, "Campaign identity"),
                    (parent_session_id, "Parent Builder session identity"),
                    (child_session_id, "Creation Subflow session identity"),
                ):
                    validate_authority_identity(value, label)
                if purpose not in _GUIDED_SUBFLOW_STEPS:
                    raise ValueError("Creation Subflow purpose is unsupported")
                if (
                    not isinstance(expected_construction_revision, int)
                    or expected_construction_revision < 0
                ):
                    raise ValueError("Construction revision is invalid")
                if (
                    not isinstance(visible_answer, str)
                    or not visible_answer.strip()
                    or len(visible_answer) > _MAX_GUIDED_VISIBLE_ANSWER_CHARS
                ):
                    raise ValueError("Creation Subflow trigger is invalid")
                parent_context = self._sessions.context(parent_session_id)
                parent = parent_context.session
                if (
                    parent.campaign_id != campaign_id
                    or parent.role is not WriterRole.BUILDER
                    or parent.status is not SessionStatus.ACTIVE
                ):
                    raise ValueError("Parent Campaign Interview session is unavailable")
                expected_step = _GUIDED_SUBFLOW_STEPS[purpose]
                if trigger_message_id is not None:
                    validate_authority_identity(
                        trigger_message_id, "Creation Subflow trigger message identity"
                    )
                if purpose == "adaptive_interpretation":
                    states = tuple(
                        message.content
                        for message in parent_context.messages
                        if message.kind is SessionMessageKind.EVIDENCE
                        and message.content.get("kind") == "campaign_interview_state"
                    )
                    current_step = None if not states else states[-1].get("current_step")
                    if trigger_message_id is None or current_step not in _GUIDED_CAMPAIGN_STEPS:
                        raise ValueError("Adaptive interpretation trigger is unavailable")
                    trigger = next(
                        (
                            message
                            for message in parent_context.messages
                            if message.message_id == trigger_message_id
                            and message.kind is SessionMessageKind.USER
                            and str(message.content.get("interview_step", "")).endswith(
                                "_contribution"
                            )
                        ),
                        None,
                    )
                    if trigger is None:
                        raise ValueError("Adaptive interpretation requires the exact raw answer")
                    trigger_step = str(trigger.content["interview_step"])
                    contribution = self._guided_contribution_object(
                        builder_session_id=parent_session_id,
                        contribution_step=trigger_step,
                    )
                    answer_value = trigger.content.get("interview_value")
                    if contribution is None or not isinstance(answer_value, Mapping):
                        raise ValueError("Adaptive Interview Contribution is unavailable")
                    validated = validate_interview_contribution_answers(
                        contribution,
                        answer_value,
                    )
                    accepted_values = validated["answers"]
                    if not isinstance(accepted_values, Mapping):
                        raise ValueError("Interview Contribution answers are invalid")
                    if not any(
                        question["adaptive"] and str(question["id"]) in accepted_values
                        for question in contribution.questions
                    ):
                        raise ValueError("Interview Contribution does not require interpretation")
                    expected_step = str(current_step)
                if purpose == "campaign_customization":
                    states = tuple(
                        message.content
                        for message in parent_context.messages
                        if message.kind is SessionMessageKind.EVIDENCE
                        and message.content.get("kind") == "campaign_interview_state"
                    )
                    current_step = None if not states else states[-1].get("current_step")
                    if current_step not in {"specific_requests", "review"}:
                        raise ValueError(
                            "Campaign customization is unavailable at this interview step"
                        )
                    expected_step = str(current_step)
            except (TypeError, ValueError) as error:
                raise CommandRejected(
                    "Creation Subflow authority is invalid",
                    code="invalid_creation_subflow",
                ) from error
            child = SessionRecord(
                session_id=child_session_id,
                campaign_id=campaign_id,
                role=WriterRole.BUILDER,
                provider_id=parent.provider_id,
                model_id=parent.model_id,
                status=SessionStatus.ACTIVE,
                selected_skill=_GUIDED_SUBFLOW_SKILLS[purpose],
            )
            return self._store.start_guided_creation_subflow(
                operation_id=operation_id,
                campaign_id=campaign_id,
                parent_session_id=parent_session_id,
                expected_construction_revision=expected_construction_revision,
                purpose=purpose,
                expected_step=expected_step,
                visible_answer=visible_answer.strip(),
                child_session=child,
                trigger_message_id=trigger_message_id,
            )

    def return_guided_creation_subflow(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        parent_session_id: str,
        child_session_id: str,
        expected_construction_revision: int,
        use_result: bool,
    ) -> GuidedCreationSubflow:
        """Return from a linked Builder chat, optionally admitting its validated result."""

        with self._operation(), self._campaign_work(campaign_id):
            try:
                for value, label in (
                    (operation_id, "Campaign operation identity"),
                    (campaign_id, "Campaign identity"),
                    (parent_session_id, "Parent Builder session identity"),
                    (child_session_id, "Creation Subflow session identity"),
                ):
                    validate_authority_identity(value, label)
                if (
                    not isinstance(expected_construction_revision, int)
                    or expected_construction_revision < 0
                    or not isinstance(use_result, bool)
                ):
                    raise ValueError("Creation Subflow return basis is invalid")
            except (TypeError, ValueError) as error:
                raise CommandRejected(
                    "Creation Subflow return authority is invalid",
                    code="invalid_creation_subflow_return",
                ) from error
            return self._store.return_guided_creation_subflow(
                operation_id=operation_id,
                campaign_id=campaign_id,
                parent_session_id=parent_session_id,
                child_session_id=child_session_id,
                expected_construction_revision=expected_construction_revision,
                use_result=use_result,
                guided_steps=_GUIDED_CAMPAIGN_STEPS,
                guided_questions=_GUIDED_CAMPAIGN_QUESTIONS,
                next_steps_after_created_result={
                    "ruleset": "setting",
                    "setting": "plan",
                    "plan": "name",
                    "player_character": "participants",
                    "companion": "participants",
                },
            )

    def begin_campaign_construction(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        lane: str,
        expected_revision: int,
        canonical_input: SafePayload,
    ) -> CampaignConstructionBegun:
        """Bind one lane to the confirmation basis without replacing campaign identity."""

        with self._operation():
            try:
                validate_authority_identity(operation_id, "Campaign operation identity")
                validate_authority_identity(campaign_id, "Campaign identity")
                if lane not in {"quick_start", "customize"}:
                    raise ValueError("Construction lane is unsupported")
                if (
                    not isinstance(expected_revision, int)
                    or isinstance(expected_revision, bool)
                    or expected_revision < 0
                ):
                    raise ValueError("Expected revision must be a nonnegative integer")
                captured_input = owned_payload(canonical_input)
            except (TypeError, ValueError) as error:
                raise CommandRejected(
                    "Campaign construction input is invalid",
                    code="construction_input_invalid",
                ) from error
            if not canonical_payload_is_safe(captured_input):
                raise CommandRejected(
                    "Campaign construction input contains a reserved canonical channel",
                    code="unsafe_canonical_payload",
                )
            semantic_payload = _campaign_operation_payload(
                operation_id=operation_id,
                operation_kind="begin_campaign_construction",
                values={
                    "campaign_id": campaign_id,
                    "lane": lane,
                    "canonical_input": captured_input,
                },
            )
            payload_sha256 = hashlib.sha256(canonical_json_bytes(semantic_payload)).hexdigest()
            existing = self._store.campaign_operation_hit(
                operation_id=operation_id,
                operation_kind="begin_campaign_construction",
                payload_sha256=payload_sha256,
            )
            if existing is not None:
                if not isinstance(existing, CampaignConstructionBegun):
                    raise IdempotencyConflict("Campaign operation result kind does not match")
                return existing
            with self._campaign_work(campaign_id), self._lane(campaign_id):
                return self._store.begin_campaign_construction(
                    operation_id=operation_id,
                    semantic_payload=semantic_payload,
                    payload_sha256=payload_sha256,
                    campaign_id=campaign_id,
                    lane=lane,
                    expected_revision=expected_revision,
                    canonical_input=captured_input,
                )

    def complete_campaign_construction(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        expected_revision: int,
        canonical_plan: SafePayload,
    ) -> CampaignConstructionCompleted:
        """Complete the begun construction on its exact TURN/lock/revision basis."""

        with self._operation():
            try:
                validate_authority_identity(operation_id, "Campaign operation identity")
                validate_authority_identity(campaign_id, "Campaign identity")
                if (
                    not isinstance(expected_revision, int)
                    or isinstance(expected_revision, bool)
                    or expected_revision < 0
                ):
                    raise ValueError("Expected revision must be a nonnegative integer")
                captured_plan = owned_payload(canonical_plan)
            except (TypeError, ValueError) as error:
                raise CommandRejected(
                    "Campaign construction plan is invalid",
                    code="construction_plan_invalid",
                ) from error
            if not canonical_payload_is_safe(captured_plan):
                raise CommandRejected(
                    "Campaign construction plan contains a reserved canonical channel",
                    code="unsafe_canonical_payload",
                )
            semantic_payload = _campaign_operation_payload(
                operation_id=operation_id,
                operation_kind="complete_campaign_construction",
                values={"campaign_id": campaign_id, "canonical_plan": captured_plan},
            )
            payload_sha256 = hashlib.sha256(canonical_json_bytes(semantic_payload)).hexdigest()
            existing = self._store.campaign_operation_hit(
                operation_id=operation_id,
                operation_kind="complete_campaign_construction",
                payload_sha256=payload_sha256,
            )
            if existing is not None:
                if not isinstance(existing, CampaignConstructionCompleted):
                    raise IdempotencyConflict("Campaign operation result kind does not match")
                return existing
            with self._campaign_work(campaign_id), self._lane(campaign_id):
                return self._store.complete_campaign_construction(
                    operation_id=operation_id,
                    semantic_payload=semantic_payload,
                    payload_sha256=payload_sha256,
                    campaign_id=campaign_id,
                    expected_revision=expected_revision,
                    canonical_plan=captured_plan,
                )

    def finalize_campaign_construction(
        self,
        *,
        campaign_id: str,
        dm_provider_id: str,
        dm_model_id: str,
        plan_artifact_text: str,
        structured_plan: SafePayload | None = None,
        model_guidance_override: bool = False,
    ) -> tuple[CampaignConstructionCompleted, CampaignPlan, BuilderDMHandoff]:
        """Materialize, admit, checkpoint, and atomically hand one construction to DM."""

        validate_authority_identity(campaign_id, "Campaign identity")
        validate_authority_identity(dm_provider_id, "DM provider identity")
        validate_authority_identity(dm_model_id, "DM model identity")
        basis: CampaignConstructionBasis = self._store.campaign_construction_basis(campaign_id)
        lock = self.current_package_lock(campaign_id)
        if lock.lock_id != basis.package_lock_id:
            raise CommandRejected("Campaign construction package lock basis has drifted")
        if lock.package_lock.entries:
            if basis.package_candidate_id is None:
                raise CommandRejected(
                    "Campaign construction package candidate basis is unavailable"
                )
            candidate = self.package_candidate(basis.package_candidate_id)
            if (
                candidate.package_lock != lock.package_lock
                or candidate.closure_sha256 != lock.closure_sha256
            ):
                raise CommandRejected("Campaign construction package candidate basis is inexact")
        elif basis.package_candidate_id is not None:
            raise CommandRejected("No-rules construction cannot bind a package candidate")

        if not isinstance(plan_artifact_text, str) or not plan_artifact_text.strip():
            raise CommandRejected("Campaign plan file is empty or unusable")
        if structured_plan is None:
            plan_content = owned_payload({"campaign_plan_markdown": plan_artifact_text})
        else:
            captured_plan = owned_payload(structured_plan)
            bindings = captured_plan.get("bindings")
            if (
                captured_plan.get("format_id") != "sylvia-gm-campaign-plan"
                or captured_plan.get("format_version") != CAMPAIGN_PLAN_FORMAT_VERSION
                or not isinstance(bindings, Mapping)
            ):
                raise CommandRejected("Structured Campaign Plan format is invalid")
            primary = next(
                (entry for entry in lock.package_lock.entries if entry.role is PackageRole.PRIMARY),
                None,
            )
            settings = campaign_setting_entries(lock.package_lock)
            expected_ruleset = (
                None
                if primary is None
                else {
                    "id": primary.package_id,
                    "version": primary.version,
                    "content_sha256": primary.sha256,
                }
            )
            expected_setting = (
                None
                if len(settings) != 1
                else {
                    "id": settings[0].package_id,
                    "version": settings[0].version,
                    "content_sha256": settings[0].sha256,
                }
            )
            if (
                bindings.get("ruleset") != expected_ruleset
                or bindings.get("campaign_setting") != expected_setting
                or bindings.get("package_closure_sha256") != lock.closure_sha256
            ):
                raise CommandRejected("Structured Campaign Plan package bindings are stale")
            plan_content = owned_payload(
                {
                    "artifact_content_sha256": hashlib.sha256(
                        canonical_json_bytes(captured_plan)
                    ).hexdigest(),
                    "campaign_plan": captured_plan,
                    "campaign_plan_markdown": plan_artifact_text,
                    "package_lock_id": lock.lock_id,
                }
            )
        self._validate_required_plan_state(campaign_id, plan_content)
        content_sha256 = hashlib.sha256(canonical_json_bytes(plan_content)).hexdigest()
        suffix = hashlib.sha256(campaign_id.encode("utf-8")).hexdigest()[:24]
        completion_id = f"construction-complete-{suffix}"
        evidence_id = f"construction-evidence-{suffix}"
        plan_id = f"campaign-plan-{suffix}"
        handoff_id = f"builder-dm-handoff-{suffix}"
        dm_session_id = f"session-dm-{suffix}"

        builder_context = self._sessions.context(basis.builder_session_id)
        guided_construction = any(
            message.kind is SessionMessageKind.EVIDENCE
            and message.content.get("kind") == "campaign_interview_state"
            for message in builder_context.messages
        )
        if guided_construction:
            guidance = self._model_guidance_for_lock(
                lock_id=lock.lock_id,
                model_id=dm_model_id,
                override_accepted=model_guidance_override,
            )
            if not guidance.may_continue:
                raise CommandRejected(
                    "Selected model capability guidance requires explicit confirmation",
                    code=guidance.warning_code or "model_guidance_confirmation_required",
                )
            completion_payload = _campaign_operation_payload(
                operation_id=completion_id,
                operation_kind="complete_campaign_construction",
                values={"campaign_id": campaign_id, "canonical_plan": plan_content},
            )
            completion_payload_sha256 = hashlib.sha256(
                canonical_json_bytes(completion_payload)
            ).hexdigest()
            completion_revision = basis.begin_revision + 1
            plan_payload_sha256 = campaign_plan_payload_sha256(
                plan_id=plan_id,
                campaign_id=campaign_id,
                source_session_id=basis.builder_session_id,
                expected_revision=completion_revision,
                expected_package_lock_id=lock.lock_id,
                content_sha256=content_sha256,
                content=plan_content,
                provenance_evidence_ids=(evidence_id,),
                validation_outcome=PlanValidationOutcome.VALID,
                acceptance_status=PlanAcceptanceStatus.ACCEPTED,
            )
            handoff_expected_revision = completion_revision + 1
            handoff_payload_sha256 = builder_dm_handoff_payload_sha256(
                handoff_id=handoff_id,
                campaign_id=campaign_id,
                builder_session_id=basis.builder_session_id,
                dm_session_id=dm_session_id,
                dm_provider_id=dm_provider_id,
                dm_model_id=dm_model_id,
                expected_writer=WriterRole.BUILDER,
                expected_revision=handoff_expected_revision,
                expected_package_lock_id=lock.lock_id,
                expected_package_candidate_id=basis.package_candidate_id,
                plan_id=plan_id,
                expected_plan_revision=1,
                expected_plan_content_sha256=content_sha256,
                completed_turn_id=basis.authority_turn_id,
                model_guidance_sha256=model_guidance_payload_sha256(guidance),
            )
            evidence = BuilderEvidence(
                evidence_id=evidence_id,
                source_kind=EvidenceSourceKind.USER_PROVIDED,
                content_sha256=content_sha256,
                source_locator=None,
                safe_excerpt="Owner-materialized guided Campaign Plan.",
                claims=("construction-input", "package-lock", "participant-artifacts"),
            )
            dm_turn_id = f"turn-dm-{suffix}"
            completed, plan, handoff, _ = self._store.commit_guided_campaign_start(
                completion_id=completion_id,
                completion_semantic_payload=completion_payload,
                completion_payload_sha256=completion_payload_sha256,
                campaign_id=campaign_id,
                expected_begin_revision=basis.begin_revision,
                canonical_plan=plan_content,
                evidence=evidence,
                plan_id=plan_id,
                plan_content_sha256=content_sha256,
                plan_content=plan_content,
                plan_payload_sha256=plan_payload_sha256,
                handoff_id=handoff_id,
                dm_session_id=dm_session_id,
                dm_provider_id=dm_provider_id,
                dm_model_id=dm_model_id,
                expected_package_candidate_id=basis.package_candidate_id,
                model_guidance=guidance,
                handoff_payload_sha256=handoff_payload_sha256,
                dm_turn_id=dm_turn_id,
            )
            return completed, plan, handoff

        try:
            existing_handoff = self.builder_dm_handoff(handoff_id)
        except CommandRejected:
            existing_handoff = None
        if existing_handoff is not None:
            if basis.completion_revision is None or basis.canonical_representation_sha256 is None:
                raise CommandRejected("Completed construction receipt basis is unavailable")
            existing_plan = self.campaign_plan(plan_id)
            if existing_plan.content_sha256 != content_sha256:
                raise CommandRejected("Campaign plan file changed after DM startup")
            completed = CampaignConstructionCompleted(
                campaign_id=campaign_id,
                lane=basis.lane,
                canonical_representation_sha256=basis.canonical_representation_sha256,
                resulting_revision=basis.completion_revision,
            )
            replayed_handoff = self.handoff_builder_to_dm(
                handoff_id=handoff_id,
                campaign_id=campaign_id,
                builder_session_id=basis.builder_session_id,
                dm_session_id=dm_session_id,
                dm_provider_id=dm_provider_id,
                dm_model_id=dm_model_id,
                expected_writer=WriterRole.BUILDER,
                expected_revision=existing_plan.resulting_campaign_revision,
                expected_package_lock_id=lock.lock_id,
                expected_package_candidate_id=basis.package_candidate_id,
                plan_id=existing_plan.plan_id,
                expected_plan_revision=existing_plan.plan_revision,
                expected_plan_content_sha256=existing_plan.content_sha256,
                completed_turn_id=basis.authority_turn_id,
                model_guidance_override=model_guidance_override,
            )
            return completed, existing_plan, replayed_handoff

        guidance = self._model_guidance_for_lock(
            lock_id=lock.lock_id,
            model_id=dm_model_id,
            override_accepted=model_guidance_override,
        )
        if not guidance.may_continue:
            raise CommandRejected(
                "Selected model capability guidance requires explicit confirmation",
                code=guidance.warning_code or "model_guidance_confirmation_required",
            )

        completed = self.complete_campaign_construction(
            operation_id=completion_id,
            campaign_id=campaign_id,
            expected_revision=basis.begin_revision,
            canonical_plan=plan_content,
        )
        self.record_builder_evidence(
            session_id=basis.builder_session_id,
            evidence_id=evidence_id,
            source_kind="user_provided",
            content_sha256=content_sha256,
            source_locator=None,
            safe_excerpt=(
                "Builder-authored campaign plan file."
                if plan_artifact_text is not None
                else "Owner-materialized campaign construction plan."
            ),
            claims=("construction-input", "package-lock", "participant-artifacts"),
        )
        plan = self.admit_campaign_plan(
            plan_id=plan_id,
            campaign_id=campaign_id,
            builder_session_id=basis.builder_session_id,
            expected_revision=completed.resulting_revision,
            expected_package_lock_id=lock.lock_id,
            content=plan_content,
            content_sha256=content_sha256,
            provenance_evidence_ids=(evidence_id,),
            validation_outcome="valid",
            acceptance_status="accepted",
        )
        if not self._store.turn_is_completed(campaign_id, basis.authority_turn_id):
            self.complete_turn(campaign_id, basis.authority_turn_id)
        handoff = self.handoff_builder_to_dm(
            handoff_id=handoff_id,
            campaign_id=campaign_id,
            builder_session_id=basis.builder_session_id,
            dm_session_id=dm_session_id,
            dm_provider_id=dm_provider_id,
            dm_model_id=dm_model_id,
            expected_writer=WriterRole.BUILDER,
            expected_revision=plan.resulting_campaign_revision,
            expected_package_lock_id=lock.lock_id,
            expected_package_candidate_id=basis.package_candidate_id,
            plan_id=plan.plan_id,
            expected_plan_revision=plan.plan_revision,
            expected_plan_content_sha256=plan.content_sha256,
            completed_turn_id=basis.authority_turn_id,
            model_guidance_override=model_guidance_override,
        )
        return completed, plan, handoff

    def campaign_construction_basis(self, campaign_id: str) -> CampaignConstructionBasis:
        """Read a begun construction basis so an interrupted guided Start can resume."""

        validate_authority_identity(campaign_id, "Campaign identity")
        with self._operation():
            return self._store.campaign_construction_basis(campaign_id)

    def create_campaign(self, command: CampaignCreate) -> CampaignRecord:
        with self._operation():
            return self._create_campaign(command)

    def _create_campaign(self, command: CampaignCreate) -> CampaignRecord:
        captured = _capture_campaign_creation(command)
        try:
            validate_authority_identity(command.display_name, "Campaign display name")
            validate_authority_identity(command.turn_id, "TURN identity")
            if command.campaign_id is not None:
                validate_authority_identity(command.campaign_id, "Campaign identity")
            if command.source_handoff_id is not None:
                validate_authority_identity(command.source_handoff_id, "Campaign handoff identity")
            if command.session_id is not None:
                validate_authority_identity(command.session_id, "Campaign session identity")
            if (command.source_handoff_id is None) != (command.session_id is None):
                raise ValueError("Campaign session and handoff identities must be paired")
        except ValueError as error:
            raise CommandRejected(
                "Campaign authority identity is invalid",
                code="invalid_authority_identity",
            ) from error
        if command.source_handoff_id is not None or command.session_id is not None:
            raise CommandRejected(
                "F4 handoff applies only to an existing campaign",
                code="existing_campaign_handoff_only",
            )
        if not lifecycle_writer_is_legal(command.lifecycle, command.writer):
            raise CommandRejected(
                "Campaign lifecycle and writer are incompatible",
                code="invalid_lifecycle_writer",
            )
        campaign = CampaignRecord(
            campaign_id=command.campaign_id or str(uuid.uuid4()),
            display_name=command.display_name,
            lifecycle=command.lifecycle,
            writer=command.writer,
            revision=0,
        )
        self._validate_campaign_inputs(captured)
        self._store.create_campaign(
            campaign,
            captured.package_lock,
            command.turn_id,
            captured.participants,
            captured.initial_state,
        )
        return campaign

    def catalog(self, campaign_id: str) -> tuple[CatalogTool, ...]:
        with self._operation():
            return self._catalog(campaign_id)

    def ruleset_gm_operating_guide(self, campaign_id: str) -> str | None:
        """Read the exact optional GM operating guide from the locked primary Ruleset."""

        with self._operation():
            snapshot = self._store.snapshot(campaign_id)
            primary = next(
                (
                    entry
                    for entry in snapshot.package_lock.entries
                    if entry.role is PackageRole.PRIMARY
                ),
                None,
            )
            if primary is None:
                return None
            value = self._store.package_file_bytes(
                primary.package_id,
                primary.version,
                primary.sha256,
                _RULESET_GM_OPERATING_GUIDE_PATH,
                maximum_bytes=_RULESET_GM_OPERATING_GUIDE_BYTE_LIMIT,
            )
        if value is None:
            return None
        try:
            guide = value.decode("utf-8")
        except UnicodeDecodeError as error:
            raise CommandRejected("Ruleset GM operating guide is not valid UTF-8") from error
        if not guide.strip():
            raise CommandRejected("Ruleset GM operating guide is empty")
        return guide

    def _catalog(
        self, campaign_id: str, *, include_character_updates: bool = False
    ) -> tuple[CatalogTool, ...]:
        self._ensure_campaign_not_transitioning(campaign_id)
        lock, state, turn_status, writer, lifecycle, declarations = (
            self._store.package_catalog_basis(campaign_id)
        )
        if lifecycle != LifecycleState.ACTIVE.value:
            return ()
        capabilities = frozenset(
            capability for entry in lock.package_lock.entries for capability in entry.capabilities
        )
        available: list[CatalogTool] = []
        for entry, declaration in declarations:
            if (
                "x-sylvia-gm-character-update" in declaration.input_schema
                and not include_character_updates
            ):
                continue
            # Shared bookkeeping remains readable during combat; execution still performs
            # ordinary predicate admission in _admitted_tool before any mutation.
            if not (
                include_character_updates
                and "x-sylvia-gm-character-update" in declaration.input_schema
            ) and not declaration_admitted(
                declaration,
                state=state,
                turn_status=turn_status,
                writer=writer,
                declared_capabilities=capabilities,
            ):
                continue
            tool = self._package_tool(entry, declaration)
            if (
                tool is not None
                and tool.capability == declaration.capability
                and tool.capability in entry.capabilities
            ):
                effective_behavior = (
                    tool.model_behavior
                    if state_predicates_match(tool.model_behavior_state_predicates, state)
                    else ModelToolBehavior.OPTIONAL
                )
                available.append(replace(tool, model_behavior=effective_behavior))
        return tuple(
            sorted(
                available,
                key=lambda item: (
                    item.package_id,
                    item.package_version,
                    item.package_sha256,
                    item.name,
                ),
            )
        )

    def _decision_scope_declaration(
        self, snapshot: CampaignState | CampaignDisplaySnapshot,
    ) -> Mapping[str, object] | None:
        """Read actor-control metadata from the exact currently locked primary package."""

        primary = next(
            (
                entry
                for entry in snapshot.package_lock.entries
                if entry.role is PackageRole.PRIMARY
            ),
            None,
        )
        if primary is None:
            return None
        # Catalog compilation already verified these exact immutable bytes. A local
        # bytes reference stays valid even if the bounded cache later evicts it.
        runtime_bytes = self._runtime_payloads.get(
            (primary.package_id, primary.version, primary.sha256)
        )
        if runtime_bytes is None:
            runtime_bytes = self._store.package_file_bytes(
                primary.package_id,
                primary.version,
                primary.sha256,
                PACKAGE_RUNTIME_PATH,
                maximum_bytes=PACKAGE_RUNTIME_BYTE_LIMIT,
            )
        if runtime_bytes is None:
            return None
        try:
            declared = package_model_decision_boundary(
                package_id=primary.package_id,
                runtime_bytes=runtime_bytes,
            )
        except PackageRuntimeError as error:  # pragma: no cover - admitted package invariant
            raise CommandRejected("Ruleset decision boundary is invalid") from error
        return declared

    def _player_control_scope(self, snapshot: CampaignState) -> SafePayload | None:
        """Project safe player actor identities using the same Ruleset scope as GM context."""

        declared = self._decision_scope_declaration(snapshot)
        if declared is None:
            return None
        try:
            return project_player_control_scope(snapshot, declared)
        except DecisionScopeProjectionError as error:
            raise CommandRejected(str(error)) from error

    def campaign_preparation_boundary(self, campaign_id: str) -> ModelDecisionBoundary | None:
        """Read only declared starting events; do not project unrelated ordinary turn state."""
        with self._operation():
            snapshot = self._store.snapshot(campaign_id)
            declared = self._decision_scope_declaration(snapshot)
            if declared is None or "preparation" not in declared:
                return None
            try:
                return project_campaign_preparation_scope(snapshot, declared)
            except DecisionScopeProjectionError as error:
                raise CommandRejected(str(error)) from error

    def model_decision_boundary(self, campaign_id: str) -> ModelDecisionBoundary | None:
        """Project the current Ruleset-declared Player or GM decision boundary."""

        with self._operation():
            snapshot = self._store.snapshot(campaign_id)
            declared = self._decision_scope_declaration(snapshot)
            if declared is None:
                return None
            try:
                return project_model_decision_scope(snapshot, declared)
            except DecisionScopeProjectionError as error:
                raise CommandRejected(str(error)) from error

    def execute_action(self, command: ActionCommand) -> ActionReceipt | AwaitingChoiceReceipt:
        """Run a GM tool action through the campaign's serialized authority path."""

        with self._operation(), self._campaign_work(command.campaign_id):
            return self._execute_action(command)

    def resolve_mechanics_choice(self, command: ResolveMechanicsChoiceCommand) -> ActionReceipt:
        with self._operation():
            pending = self._store.pending_mechanics_action(command.campaign_id)
            work = (
                self._direct_action_work
                if pending is not None and pending.player_action_origin is not None
                else self._campaign_work
            )
            with work(command.campaign_id):
                return self._resolve_mechanics_choice(command)

    def _resolve_mechanics_choice(
        self, command: ResolveMechanicsChoiceCommand,
    ) -> ActionReceipt | AwaitingChoiceReceipt:
        try:
            if (isinstance(command.choice_sequence, bool)
                    or not isinstance(command.choice_sequence, int)
                    or not 0 <= command.choice_sequence < 64):
                raise ValueError("Mechanics choice sequence is invalid")
            for value, label in (
                (command.campaign_id, "Campaign identity"),
                (command.action_id, "Action identity"),
                (command.resolution_id, "Mechanics choice resolution identity"),
                (command.owner_id, "Application owner identity"),
                (command.choice_id, "Mechanics choice identity"),
                (command.option_id, "Mechanics choice option identity"),
            ):
                validate_authority_identity(value, label)
            if command.session_id is not None:
                validate_authority_identity(command.session_id, "Agent session identity")
        except ValueError as error:
            raise CommandRejected(
                "Mechanics choice authority identity is invalid",
                code="invalid_authority_identity",
            ) from error
        with self._lane(command.campaign_id):
            if command.owner_id != self._owner_id:
                raise WrongOwner("Command owner does not hold this application authority")
            replay = self._store.mechanics_choice_resolution_receipt(
                campaign_id=command.campaign_id,
                resolution_id=command.resolution_id,
                action_id=command.action_id,
                choice_id=command.choice_id,
                option_id=command.option_id,
                session_id=command.session_id,
                choice_sequence=command.choice_sequence,
            )
            if replay is not None:
                return replay
            pending = self._store.pending_mechanics_action(command.campaign_id)
            if pending is None or pending.action_id != command.action_id:
                raise CommandRejected(
                    "Mechanics choice reservation is unavailable",
                    code="mechanics_choice_unavailable",
                )
            if (
                pending.basis_revision != command.expected_revision
                or len(pending.prior_answers) != command.choice_sequence
                or pending.choice.choice_id != command.choice_id
                or command.option_id not in {option.option_id for option in pending.choice.options}
                or pending.initiating_session_id != command.session_id
            ):
                raise IdempotencyConflict(
                    "Mechanics choice resolution does not match its reservation"
                )
            basis = self._store.action_basis(command.campaign_id, pending.turn_id)
            package = self._locked_package(pending.package_id, basis.package_lock)
            if (
                package.version != pending.package_version
                or package.sha256 != pending.package_sha256
                or basis.campaign.revision != pending.basis_revision
                or basis.campaign.writer is not pending.writer
            ):
                raise StaleRevision("Mechanics choice basis has drifted")
            synthetic = ActionCommand(
                campaign_id=pending.campaign_id,
                action_id=pending.action_id,
                idempotency_key="pending-choice-resolution",
                owner_id=command.owner_id,
                writer=pending.writer,
                expected_revision=pending.basis_revision,
                turn_id=pending.turn_id,
                package_id=pending.package_id,
                tool_name=pending.tool_name,
                parameters=pending.parameters,
                initiating_session_id=pending.initiating_session_id,
            )
            self._admit(synthetic, basis.campaign, basis.turn_status)
            tool = self._admitted_tool(
                synthetic,
                basis.package_lock,
                state=basis.state,
                turn_status=basis.turn_status,
                writer=basis.campaign.writer,
            )
            validate_parameters(pending.parameters, tool.input_schema)
            validate_references(
                pending.parameters,
                tool.input_schema,
                participant_ids=basis.participant_ids,
            )
            context = MechanicContext(
                campaign_id=pending.campaign_id,
                turn_id=pending.turn_id,
                action_id=pending.action_id,
                state=_freeze_payload(basis.state),
                parameters=_freeze_payload(pending.parameters),
                participants=_freeze_payload(
                    {
                        participant.participant_id: _participant_context_payload(participant)
                        for participant in basis.participants
                    }
                ),
                choice_resolution=MechanicsChoiceResolution(
                    choice_id=command.choice_id,
                    option_id=command.option_id,
                    expected_request=pending.choice,
                    prior_answers=pending.prior_answers,
                ),
            )
            try:
                live_outcome = tool.execute(context)
                outcome = _owned_mechanic_outcome(live_outcome)
                if not mechanic_outcome_is_safe(outcome):
                    raise TypeError("Resolved mechanic outcome is not canonical-safe")
                if not self._store.mechanic_mutations_are_owned(
                    campaign_id=pending.campaign_id,
                    package_id=pending.package_id,
                    package_version=pending.package_version,
                    package_sha256=pending.package_sha256,
                    outcome=outcome,
                ):
                    raise TypeError("Mechanic outcome violates state ownership")
            except Exception as error:
                raise CommandRejected(
                    "Mechanics choice resolution failed without consuming its reservation",
                    code="mechanics_choice_resolution_failed",
                ) from error
            if outcome.choice is not None:
                return self._store.advance_mechanics_choice(
                    pending,
                    MechanicsChoiceAnswer(command.resolution_id, pending.choice,
                                          command.option_id, command.session_id),
                    outcome.choice,
                )
            origin = pending.player_action_origin
            if origin is None and command.session_id is not None:
                preparation = self.campaign_preparation_boundary(command.campaign_id)
                if (preparation is not None and preparation.preparation is not None
                        and pending.package_id == preparation.preparation["package_id"]
                        and pending.tool_name == preparation.preparation["intent"]):
                    # The actual choice click is the player event; the model's earlier
                    # proposal is not relabelled as player speech or a direct action.
                    origin = owned_payload({**player_control_origin({
                        "kind": "mechanics_choice", "action_id": pending.action_id,
                        "choice_id": command.choice_id, "choice_sequence": command.choice_sequence,
                        "option_id": command.option_id, "resolution_id": command.resolution_id,
                    }, expected_revision=pending.basis_revision, selection={},
                        session_id=command.session_id),
                        "policy": {"confirmation": "required", "response": "automatic"}})
            return self._store.commit_action(
                campaign_id=pending.campaign_id,
                action_id=pending.action_id,
                idempotency_key="pending-choice-resolution",
                payload_hash=pending.payload_hash,
                expected_revision=pending.basis_revision,
                writer=pending.writer,
                turn_id=pending.turn_id,
                package_id=pending.package_id,
                package_version=pending.package_version,
                package_sha256=pending.package_sha256,
                tool_name=pending.tool_name,
                parameters=pending.parameters,
                outcome=retain_player_action_origin(outcome, origin),
                status=ActionStatus.COMMITTED,
                choice_resolution_id=command.resolution_id,
                choice_resolution=context.choice_resolution,
                choice_resolution_session_id=command.session_id,
            )

    def _execute_action(
        self, command: ActionCommand, *, player_action_origin: SafePayload | None = None,
        campaign_file_commands: Sequence[CampaignContinuityCommand] = (),
        campaign_file_update: bool = False,
    ) -> ActionReceipt | AwaitingChoiceReceipt:
        try:
            for value, label in (
                (command.campaign_id, "Campaign identity"),
                (command.action_id, "Action identity"),
                (command.idempotency_key, "Idempotency identity"),
                (command.owner_id, "Application owner identity"),
                (command.turn_id, "TURN identity"),
                (command.package_id, "Package identity"),
                (command.tool_name, "Tool name"),
            ):
                validate_authority_identity(value, label)
            if command.initiating_session_id is not None:
                validate_authority_identity(
                    command.initiating_session_id,
                    "Initiating agent session identity",
                )
        except ValueError as error:
            raise CommandRejected(
                "Command authority identity is invalid",
                code="invalid_authority_identity",
            ) from error
        parameters = _canonical_parameter_snapshot(command.parameters)
        if not canonical_payload_is_safe(parameters):
            raise CommandRejected(
                "Tool parameters contain a reserved canonical channel",
                code="unsafe_canonical_payload",
            )
        with self._lane(command.campaign_id):
            if command.owner_id != self._owner_id:
                raise WrongOwner("Command owner does not hold this application authority")
            basis = self._store.action_basis(command.campaign_id, command.turn_id)
            package = self._locked_package(command.package_id, basis.package_lock)
            semantic_hash = _semantic_hash(
                command,
                parameters,
                package_version=package.version,
                package_sha256=package.sha256,
            )
            existing = self._store.idempotency_hit(command.campaign_id, command.idempotency_key)
            if existing is not None:
                if existing.payload_hash != semantic_hash:
                    raise IdempotencyConflict("Idempotency key payload does not match")
                return existing.receipt
            pending_hit = self._store.pending_mechanics_hit(
                command.campaign_id,
                command.idempotency_key,
            )
            if pending_hit is not None:
                if pending_hit.payload_hash != semantic_hash:
                    raise IdempotencyConflict("Idempotency key payload does not match")
                return _awaiting_choice_receipt(pending_hit.pending)
            campaign_pending = self._store.pending_mechanics_action(command.campaign_id)
            if campaign_pending is not None:
                if (
                    campaign_pending.action_id == command.action_id
                    and campaign_pending.payload_hash == semantic_hash
                ):
                    return _awaiting_choice_receipt(campaign_pending)
                raise CommandRejected(
                    "Campaign is awaiting a mechanics choice",
                    code="mechanics_choice_pending",
                )
            existing_action = self._store.action_hit(command.campaign_id, command.action_id)
            if existing_action is not None:
                if existing_action.payload_hash != semantic_hash:
                    raise IdempotencyConflict("Action identity payload does not match")
                return self._store.bind_transport_alias(
                    campaign_id=command.campaign_id,
                    action_id=command.action_id,
                    idempotency_key=command.idempotency_key,
                    payload_hash=semantic_hash,
                )
            boundary = self.campaign_preparation_boundary(command.campaign_id)
            if boundary is not None and boundary.preparation is not None:
                preparation = boundary.preparation
                normal_event = (command.package_id == preparation["package_id"]
                                and command.tool_name == preparation["intent"]
                                and parameters.get(str(preparation["actor_parameter"])) == boundary.actor_id)
                unchanged_review = campaign_file_update and not campaign_file_commands
                if not normal_event and not unchanged_review:
                    raise CommandRejected(
                        "Complete the queued starting character choices before play.",
                        code="campaign_preparation_pending",
                    )
            self._admit(command, basis.campaign, basis.turn_status)
            tool = self._admitted_tool(
                command,
                basis.package_lock,
                state=basis.state,
                turn_status=basis.turn_status,
                writer=basis.campaign.writer,
            )
            validate_parameters(parameters, tool.input_schema)
            validate_references(
                parameters,
                tool.input_schema,
                participant_ids=basis.participant_ids,
            )
            context = MechanicContext(
                campaign_id=command.campaign_id,
                turn_id=command.turn_id,
                action_id=command.action_id,
                state=_freeze_payload(basis.state),
                parameters=_freeze_payload(parameters),
                participants=_freeze_payload(
                    {
                        participant.participant_id: _participant_context_payload(participant)
                        for participant in basis.participants
                    }
                ),
            )
            pending_key = (command.campaign_id, command.action_id)
            with self._narration_lock:
                self._pending_tools[pending_key] = PlayerProjection(
                    kind=PlayerProjectionKind.PENDING_TOOL,
                    sequence=None,
                    safe_payload=owned_payload(
                        {
                            "package_id": command.package_id,
                            "package_version": package.version,
                            "package_sha256": package.sha256,
                            "tool_name": command.tool_name,
                            "parameters": parameters,
                        }
                    ),
                    collapsed_by_default=True,
                    provisional=True,
                    action_id=command.action_id,
                )
            try:
                try:
                    live_outcome = tool.execute(context)
                    outcome = _owned_mechanic_outcome(live_outcome)
                    if not mechanic_outcome_is_safe(outcome):
                        raise TypeError("Mechanic outcome is not canonical-safe")
                    if not self._store.mechanic_mutations_are_owned(
                        campaign_id=command.campaign_id,
                        package_id=command.package_id,
                        package_version=package.version,
                        package_sha256=package.sha256,
                        outcome=outcome,
                    ):
                        raise TypeError("Mechanic outcome violates state ownership")
                except RulesetPreconditionRejected:
                    raise
                except Exception:
                    outcome = _safe_tool_failure_outcome()
                    status = ActionStatus.FAILED
                else:
                    status = ActionStatus.COMMITTED
                if campaign_file_update and (
                    status is not ActionStatus.COMMITTED or outcome.choice is not None
                ):
                    raise CommandRejected("Character bookkeeping failed; nothing was saved")
                if outcome.choice is not None:
                    pending = self._store.reserve_mechanics_choice(
                        campaign_id=command.campaign_id,
                        action_id=command.action_id,
                        idempotency_key=command.idempotency_key,
                        payload_hash=semantic_hash,
                        expected_revision=command.expected_revision,
                        writer=command.writer,
                        turn_id=command.turn_id,
                        package_id=command.package_id,
                        package_version=package.version,
                        package_sha256=package.sha256,
                        tool_name=command.tool_name,
                        parameters=parameters,
                        initiating_session_id=command.initiating_session_id,
                        choice=outcome.choice,
                        player_action_origin=player_action_origin,
                    )
                    return _awaiting_choice_receipt(pending)
                return self._store.commit_action(
                    campaign_id=command.campaign_id,
                    action_id=command.action_id,
                    idempotency_key=command.idempotency_key,
                    payload_hash=semantic_hash,
                    expected_revision=command.expected_revision,
                    writer=command.writer,
                    turn_id=command.turn_id,
                    package_id=command.package_id,
                    package_version=package.version,
                    package_sha256=package.sha256,
                    tool_name=command.tool_name,
                    parameters=parameters,
                    outcome=retain_player_action_origin(outcome, player_action_origin),
                    status=status,
                    campaign_file_commands=campaign_file_commands,
                )
            finally:
                with self._narration_lock:
                    self._pending_tools.pop(pending_key, None)

    def install_package_archive(self, source: BinaryIO, *, install_id: str) -> PackageCandidate:
        with self._operation():
            return install_package_archive(self._store, source, install_id=install_id)

    def publish_builder_package_candidate(
        self,
        *,
        builder_session_id: str,
        archive: BinaryIO,
        install_id: str,
    ) -> PackageCandidate:
        """Cross the existing F3 publication seam from an active Builder session."""

        with self._operation():
            session = self._store.session_context(builder_session_id).session
            if (
                session.role is not WriterRole.BUILDER
                or session.status is not SessionStatus.ACTIVE
                or session.parent_session_id is not None
                or (
                    session.campaign_id is not None
                    and session.selected_skill
                    not in {"BUILD RULESET", "BUILD CAMPAIGN SETTING", "BUILD CAMPAIGN PLAN"}
                )
            ):
                raise CommandRejected("Builder package publication source is not authoritative")
            if session.campaign_id is not None:
                campaign = self._store.snapshot(session.campaign_id)
                if (
                    campaign.lifecycle is not LifecycleState.ACTIVE
                    or campaign.writer is not WriterRole.BUILDER
                ):
                    raise CommandRejected("Builder package publication campaign is unavailable")
            return install_package_archive(self._store, archive, install_id=install_id)

    def package_candidate(self, candidate_id: str) -> PackageCandidate:
        with self._operation():
            return self._store.package_candidate(candidate_id)

    def package_label(self, package_id: str, version: str, content_sha256: str) -> str:
        """Read a retained package's title without parsing its runtime or loading its files."""
        with self._operation():
            manifest = self._store.exact_package_manifest(package_id, version, content_sha256)
            record = parse_canonical_json_bytes(manifest.canonical_json.encode("utf-8"))
            return str(record["metadata"]["label"])

    def package_record(self, package_id: str, version: str, content_sha256: str) -> SafePayload:
        """Read one exact installed package record, containing no campaign or session data."""
        with self._operation():
            manifest = self._store.exact_package_manifest(package_id, version, content_sha256)
            return owned_payload(
                parse_canonical_json_bytes(manifest.canonical_json.encode("utf-8"))
            )

    def package_participant_declarations(
        self, candidate_id: str
    ) -> tuple[ParticipantArtifactDeclaration, ...]:
        with self._operation():
            return self._store.package_participant_declarations(candidate_id)

    def exact_participant_declarations(
        self, package_id: str, version: str, content_sha256: str
    ) -> tuple[ParticipantArtifactDeclaration, ...]:
        """Read character mechanics only from the selected contained Ruleset's exact manifest."""
        with self._operation():
            return self._store.exact_participant_declarations(package_id, version, content_sha256)

    def adapt_participant_artifact(
        self,
        *,
        source_campaign_id: str,
        participant_id: str,
        destination_package_id: str,
        destination_package_version: str,
        destination_package_sha256: str,
        destination_kind: str,
        canonical_data: SafePayload,
        operation_id: str,
    ) -> ParticipantArtifact:
        """Validate and durably record one replay-safe Builder adaptation operation."""

        with (
            self._operation(),
            self._campaign_work(source_campaign_id),
            self._lane(source_campaign_id),
        ):
            try:
                for value, label in (
                    (source_campaign_id, "Source campaign identity"),
                    (participant_id, "Participant identity"),
                    (destination_package_id, "Destination package identity"),
                    (destination_package_version, "Destination package version"),
                    (destination_kind, "Participant kind"),
                    (operation_id, "Adaptation operation identity"),
                ):
                    validate_authority_identity(value, label)
                validate_authority_identity(
                    destination_package_sha256,
                    "Destination package SHA-256",
                    sha256=True,
                )
            except ValueError as error:
                raise CommandRejected("Participant adaptation identity is invalid") from error
            source = next(
                (
                    participant
                    for participant in self._store.snapshot(source_campaign_id).participants
                    if participant.participant_id == participant_id
                ),
                None,
            )
            if source is None:
                raise CommandRejected("Participant adaptation source is unavailable")
            captured_data = owned_payload(canonical_data)
            if not canonical_payload_is_safe(captured_data):
                raise CommandRejected("Participant adaptation data is unsafe")
            declarations = self._store.exact_participant_declarations(
                destination_package_id,
                destination_package_version,
                destination_package_sha256,
            )
            declaration = next(
                (item for item in declarations if item.kind == destination_kind),
                None,
            )
            if declaration is None:
                raise CommandRejected("Destination package participant schema is unavailable")
            validate_json_value(
                captured_data,
                declaration.schema,
                label=f"Participant {participant_id}",
            )
            return self._store.commit_participant_adaptation(
                operation_id=operation_id,
                source_campaign_id=source_campaign_id,
                participant_id=source.participant_id,
                destination_package_id=destination_package_id,
                destination_package_version=destination_package_version,
                destination_package_sha256=destination_package_sha256,
                destination_kind=destination_kind,
                destination_schema_id=declaration.schema_id,
                destination_schema_sha256=declaration.schema_sha256,
                data=captured_data,
            )

    def package_candidates(self) -> tuple[PackageCandidate, ...]:
        with self._operation():
            return self._store.package_candidates()

    def inspect_package_candidate(self, candidate_id: str) -> SafePayload:
        """Return static installed package metadata and file trees, never campaign data."""

        with self._operation():
            candidate = self._store.package_candidate(candidate_id)
            packages: list[dict[str, object]] = []
            for entry in candidate.package_lock.entries:
                manifest = self._store.exact_package_manifest(
                    entry.package_id, entry.version, entry.sha256
                )
                record = parse_canonical_json_bytes(manifest.canonical_json.encode("utf-8"))
                if not isinstance(record, Mapping):  # pragma: no cover - admitted manifest
                    raise CommandRejected("Installed package manifest is unavailable")
                support_counts: dict[str, int] = {}
                support_records: list[dict[str, object]] = []
                support_bytes = self._store.package_file_bytes(
                    entry.package_id,
                    entry.version,
                    entry.sha256,
                    GAMEPLAY_SUPPORT_PATH,
                    maximum_bytes=2 * 1024 * 1024,
                )
                if support_bytes is not None:
                    support = parse_gameplay_support_bytes(support_bytes)
                    for item in support.records:
                        support_counts[item.gameplay_resolution] = (
                            support_counts.get(item.gameplay_resolution, 0) + 1
                        )
                        support_records.append(
                            {
                                "id": item.identity,
                                "reference_availability": item.reference_availability,
                                "gameplay_resolution": item.gameplay_resolution,
                                "mechanic_family": item.mechanic_family,
                                "deterministic_part": item.deterministic_part,
                                "gm_part": item.gm_part,
                            }
                        )
                private_mechanics: list[str] = []
                procedures: list[str] = []
                runtime_bytes = self._store.package_file_bytes(
                    entry.package_id,
                    entry.version,
                    entry.sha256,
                    PACKAGE_RUNTIME_PATH,
                    maximum_bytes=PACKAGE_RUNTIME_BYTE_LIMIT,
                )
                if runtime_bytes is not None:
                    runtime = yaml.safe_load(runtime_bytes.decode("utf-8"))
                    if isinstance(runtime, Mapping):
                        runtime_tools = runtime.get("tools")
                        public = {tool.name for tool in manifest.tools}
                        if isinstance(runtime_tools, Mapping):
                            private_mechanics = sorted(set(map(str, runtime_tools)) - public)
                        runtime_procedures = runtime.get("procedures")
                        if isinstance(runtime_procedures, Mapping):
                            procedures = sorted(map(str, runtime_procedures))
                packages.append(
                    {
                        "id": entry.package_id,
                        "version": entry.version,
                        "content_sha256": entry.sha256,
                        "role": entry.role.value,
                        "load_order": entry.load_order,
                        "label": manifest.metadata["label"],
                        "publisher": manifest.provenance["publisher"],
                        "provenance": dict(manifest.provenance),
                        "compatibility": dict(manifest.compatibility),
                        "archive_format": manifest.format_version,
                        "dependencies": [
                            {
                                "namespace": dependency.namespace,
                                "kind": dependency.kind,
                                "id": dependency.package_id,
                                "version": dependency.version,
                                "content_sha256": dependency.content_sha256,
                            }
                            for dependency in manifest.dependencies
                        ],
                        "capabilities": list(manifest.capabilities),
                        "public_actions": [
                            {
                                "name": tool.name,
                                "capability": tool.capability,
                                "input_schema": dict(tool.input_schema),
                                "state_predicates": [
                                    {
                                        "namespace": predicate.namespace,
                                        "key": predicate.key,
                                        "equals": thaw_json_value(predicate.equals),
                                        "negated": predicate.negated,
                                    }
                                    for predicate in tool.state_predicates
                                ],
                                "turn_statuses": list(tool.turn_statuses),
                                "writers": list(tool.writers),
                            }
                            for tool in manifest.tools
                        ],
                        "private_mechanics": private_mechanics,
                        "procedures": procedures,
                        "participant_artifacts": [
                            {
                                "kind": artifact.kind,
                                "schema_id": artifact.schema_id,
                                "schema_sha256": artifact.schema_sha256,
                                "schema": dict(artifact.schema),
                            }
                            for artifact in manifest.participant_artifacts
                        ],
                        "prompt_fragments": [item.identity for item in manifest.prompt_fragments],
                        "ui_surfaces": [item.identity for item in manifest.ui_surfaces],
                        "support_counts": support_counts,
                        "support_records": support_records,
                        "files": [
                            {
                                "path": item.path,
                                "bytes": item.byte_count,
                                "sha256": item.sha256,
                            }
                            for item in manifest.files
                        ],
                        "artifact_links": [],
                    }
                )
            return owned_payload(
                {
                    "candidate_id": candidate.candidate_id,
                    "status": candidate.status.value,
                    "closure_sha256": candidate.closure_sha256,
                    "packages": packages,
                }
            )

    def package_candidate_text(
        self,
        candidate_id: str,
        package_id: str,
        portable_path: str,
        *,
        maximum_bytes: int = 256 * 1024,
    ) -> SafePayload:
        """Read one admitted safe text file selected from a candidate's exact closure."""

        notice_name = portable_path.rsplit("/", 1)[-1].casefold()
        if notice_name not in {"license", "copyright"} and not portable_path.casefold().endswith(
            (".yaml", ".yml", ".json", ".md", ".txt")
        ):
            raise CommandRejected(
                "Installed package file is export-only", code="package_preview_unsupported"
            )
        with self._operation():
            candidate = self._store.package_candidate(candidate_id)
            entry = next(
                (item for item in candidate.package_lock.entries if item.package_id == package_id),
                None,
            )
            if entry is None:
                raise CommandRejected(
                    "Installed package is unavailable", code="package_unavailable"
                )
            value = self._store.package_file_bytes(
                entry.package_id,
                entry.version,
                entry.sha256,
                portable_path,
                maximum_bytes=maximum_bytes,
            )
        if value is None:
            raise CommandRejected(
                "Installed package file is unavailable", code="package_file_unavailable"
            )
        try:
            content = value.decode("utf-8")
        except UnicodeDecodeError as error:
            raise CommandRejected(
                "Installed package file is not UTF-8", code="package_preview_unsupported"
            ) from error
        return owned_payload(
            {
                "candidate_id": candidate_id,
                "package_id": package_id,
                "path": portable_path,
                "bytes": len(value),
                "sha256": hashlib.sha256(value).hexdigest(),
                "content": content,
            }
        )

    def export_package_candidate(self, candidate_id: str, target: BinaryIO) -> SafePayload:
        """Write the deterministic exact installed closure as a portable package archive."""

        with self._operation():
            candidate = self._store.package_candidate(candidate_id)
            drafts: list[NormalizedPackageDraft] = []
            for entry in candidate.package_lock.entries:
                manifest = self._store.exact_package_manifest(
                    entry.package_id, entry.version, entry.sha256
                )
                parsed = parse_canonical_json_bytes(manifest.canonical_json.encode("utf-8"))
                if not isinstance(parsed, Mapping):  # pragma: no cover - admitted manifest
                    raise CommandRejected("Installed package manifest is unavailable")
                record = dict(parsed)
                record.pop("files", None)
                record.pop("content_sha256", None)
                payloads: dict[str, bytes] = {}
                for file in manifest.files:
                    value = self._store.package_file_bytes(
                        entry.package_id,
                        entry.version,
                        entry.sha256,
                        file.path,
                        maximum_bytes=file.byte_count,
                    )
                    if value is None:
                        raise CommandRejected(
                            "Installed package file is unavailable",
                            code="package_file_unavailable",
                        )
                    payloads[file.path] = value
                drafts.append(NormalizedPackageDraft(record, payloads, manifest.format_version))
        with materialize_package_archive(tuple(drafts)) as materialized:
            if (
                materialized.package_lock != candidate.package_lock
                or materialized.closure_sha256 != candidate.closure_sha256
            ):
                raise CommandRejected(
                    "Installed package closure could not be reproduced",
                    code="package_export_inexact",
                )
            digest = hashlib.sha256()
            byte_count = 0
            while chunk := materialized.archive.read(64 * 1024):
                target.write(chunk)
                digest.update(chunk)
                byte_count += len(chunk)
        return owned_payload(
            {
                "candidate_id": candidate_id,
                "closure_sha256": candidate.closure_sha256,
                "archive_sha256": digest.hexdigest(),
                "byte_count": byte_count,
            }
        )

    def compare_package_candidates(self, left_id: str, right_id: str) -> SafePayload:
        """Compare exact manifests, dependency identities, and admitted file hashes."""

        left = self.inspect_package_candidate(left_id)
        right = self.inspect_package_candidate(right_id)

        def package_index(value: SafePayload) -> dict[str, Mapping[str, object]]:
            raw_packages = value.get("packages")
            if not isinstance(raw_packages, Sequence) or isinstance(
                raw_packages, (str, bytes, bytearray)
            ):
                raise CommandRejected("Installed package comparison data is invalid")
            indexed: dict[str, Mapping[str, object]] = {}
            for raw_package in raw_packages:
                if not isinstance(raw_package, Mapping) or not isinstance(
                    raw_package.get("id"), str
                ):
                    raise CommandRejected("Installed package comparison data is invalid")
                indexed[str(raw_package["id"])] = raw_package
            return indexed

        def file_index(value: Mapping[str, object]) -> dict[str, str]:
            raw_files = value.get("files")
            if not isinstance(raw_files, Sequence) or isinstance(
                raw_files, (str, bytes, bytearray)
            ):
                raise CommandRejected("Installed package comparison data is invalid")
            indexed: dict[str, str] = {}
            for raw_file in raw_files:
                if (
                    not isinstance(raw_file, Mapping)
                    or not isinstance(raw_file.get("path"), str)
                    or not isinstance(raw_file.get("sha256"), str)
                ):
                    raise CommandRejected("Installed package comparison data is invalid")
                indexed[str(raw_file["path"])] = str(raw_file["sha256"])
            return indexed

        left_packages = package_index(left)
        right_packages = package_index(right)
        package_ids = sorted(set(left_packages) | set(right_packages))
        changes: list[dict[str, object]] = []
        for package_id in package_ids:
            left_package = left_packages.get(package_id)
            right_package = right_packages.get(package_id)
            if left_package is None or right_package is None:
                changes.append(
                    {
                        "package_id": package_id,
                        "change": "added" if left_package is None else "removed",
                        "changed_files": [],
                    }
                )
                continue
            left_files = file_index(left_package)
            right_files = file_index(right_package)
            changed_files = sorted(
                path
                for path in set(left_files) | set(right_files)
                if left_files.get(path) != right_files.get(path)
            )
            if left_package["content_sha256"] != right_package["content_sha256"] or changed_files:
                changes.append(
                    {
                        "package_id": package_id,
                        "change": "changed",
                        "left_version": left_package["version"],
                        "right_version": right_package["version"],
                        "left_content_sha256": left_package["content_sha256"],
                        "right_content_sha256": right_package["content_sha256"],
                        "changed_files": changed_files,
                    }
                )
        return owned_payload(
            {
                "left_candidate_id": left_id,
                "right_candidate_id": right_id,
                "same_closure": left["closure_sha256"] == right["closure_sha256"],
                "changes": changes,
            }
        )

    def package_staging(self, install_id: str) -> PackageStagingRecord:
        with self._operation():
            return self._store.package_staging(install_id)

    def current_package_lock(self, campaign_id: str) -> PackageLockRecord:
        with self._operation():
            return self._store.current_package_lock(campaign_id)

    def package_lock_lineage(self, campaign_id: str) -> tuple[PackageLockRecord, ...]:
        with self._operation():
            return self._store.package_lock_lineage(campaign_id)

    def package_file_paths(
        self,
        package_id: str,
        version: str,
        content_sha256: str,
        *,
        maximum_files: int = 512,
    ) -> tuple[str, ...]:
        """List one exact package's files for a bounded read-only application query."""

        with self._operation():
            return self._store.package_file_paths(
                package_id,
                version,
                content_sha256,
                maximum_files=maximum_files,
            )

    def package_text_file(
        self,
        package_id: str,
        version: str,
        content_sha256: str,
        portable_path: str,
        *,
        maximum_bytes: int = 64 * 1024,
    ) -> str:
        """Read one exact package file as bounded UTF-8 without exposing storage paths."""

        with self._operation():
            value = self._store.package_file_bytes(
                package_id,
                version,
                content_sha256,
                portable_path,
                maximum_bytes=maximum_bytes,
            )
        if value is None:
            raise CommandRejected("Package source path is unavailable")
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError as error:
            raise CommandRejected("Package source is not valid UTF-8") from error

    def search_campaign_knowledge(
        self,
        campaign_id: str,
        *,
        query: str,
        sources: Sequence[str] | None = None,
        limit: int = 5,
        offset: int = 0,
    ) -> SafePayload:
        """Discover bounded exact references without creating another source of truth."""

        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 8:
            raise CommandRejected(
                "Campaign knowledge parameter 'limit' must be an integer from 1 to 8",
                code="campaign_knowledge_limit_invalid",
            )
        try:
            validate_authority_identity(query, "Campaign knowledge query")
            if len(query) > 500:
                raise ValueError("Campaign knowledge query is too long")
            requested_sources = tuple(sources or sorted(_CAMPAIGN_KNOWLEDGE_SOURCES))
            if (
                not requested_sources
                or len(set(requested_sources)) != len(requested_sources)
                or not set(requested_sources) <= _CAMPAIGN_KNOWLEDGE_SOURCES
            ):
                raise ValueError("Campaign knowledge sources are invalid")
        except (TypeError, ValueError) as error:
            raise CommandRejected(
                "Campaign knowledge search is invalid",
                code="campaign_knowledge_search_invalid",
            ) from error

        terms = knowledge_terms(query)
        with self._operation():
            snapshot = self._store.snapshot(campaign_id)
            candidates: list[KnowledgeReference] = []
            unavailable_references: list[dict[str, str]] = []
            unavailable_count = 0

            def add_match(
                *,
                source: str,
                path: str,
                kind: str,
                label: str,
                visibility: str,
                body: str,
                provenance: Mapping[str, object],
                identity: str | None = None,
            ) -> None:
                public_provenance = dict(provenance)
                heading = public_provenance.get("heading")
                if isinstance(heading, str) and len(heading) > 200:
                    public_provenance["heading"] = heading[:200]
                    public_provenance["heading_truncated"] = True
                candidates.append(
                    KnowledgeReference(
                        result={
                            "source": source,
                            "path": path,
                            "kind": kind,
                            "label": label[:200],
                            **({"label_truncated": True} if len(label) > 200 else {}),
                            "visibility": visibility,
                            "provenance": public_provenance,
                        },
                        identity=f"{identity or path} {label}"
                        if len(label) > 200
                        else identity or path,
                        body=body,
                    )
                )

            if "campaign_state" in requested_sources:
                for item in snapshot.continuity.facts:
                    record_id = item.get("record_id")
                    if not isinstance(record_id, str):
                        continue
                    is_entity = isinstance(item.get("entity_kind"), str)
                    label_value = item.get("name") if is_entity else record_id
                    label = str(label_value) if isinstance(label_value, str) else record_id
                    visibility_value = item.get("visibility")
                    visibility = (
                        str(visibility_value) if isinstance(visibility_value, str) else "gm_private"
                    )
                    add_match(
                        source="campaign_state",
                        path=record_id,
                        kind="entity" if is_entity else "fact",
                        label=label,
                        visibility=visibility,
                        body=canonical_json_bytes(item).decode("utf-8"),
                        provenance={
                            "campaign_id": campaign_id,
                            "campaign_revision": snapshot.revision,
                            "record_id": record_id,
                        },
                    )

            if "participant" in requested_sources:
                for participant in snapshot.participants:
                    raw_name = participant.data.get("name")
                    label = (
                        str(raw_name) if isinstance(raw_name, str) else participant.participant_id
                    )
                    add_match(
                        source="participant",
                        path=participant.participant_id,
                        kind="character_file",
                        label=label,
                        visibility="gm_private_character_file",
                        body=canonical_json_bytes(participant.data).decode("utf-8"),
                        provenance={
                            "campaign_id": campaign_id,
                            "participant_id": participant.participant_id,
                            "package_id": participant.package_id,
                            "package_version": participant.package_version,
                            "package_sha256": participant.package_sha256,
                        },
                    )

            accepted = self._store.accepted_campaign_plan(campaign_id)
            structured = accepted.content.get("campaign_plan") if accepted is not None else None
            plan = cast(Mapping[str, object], structured) if isinstance(structured, Mapping) else {}
            if accepted is not None and plan and "campaign_plan" in requested_sources:
                for key, value in plan.items():
                    path = f"structured/{key}"
                    add_match(
                        source="campaign_plan",
                        path=path,
                        kind="plan_section",
                        label=key.replace("_", " ").title(),
                        visibility="gm_private_preparation",
                        body=canonical_json_bytes(value).decode("utf-8"),
                        provenance={
                            "campaign_id": campaign_id,
                            "plan_id": accepted.plan_id,
                            "content_sha256": accepted.content_sha256,
                            "section": key,
                        },
                    )
                    if isinstance(value, Sequence) and not isinstance(
                        value, (str, bytes, bytearray)
                    ):
                        for item in value:
                            if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
                                continue
                            item_id = str(item["id"])
                            raw_label = item.get("title", item.get("name", item_id))
                            label = str(raw_label) if isinstance(raw_label, str) else item_id
                            add_match(
                                source="campaign_plan",
                                path=f"{path}/{item_id}",
                                kind="plan_item",
                                label=label,
                                visibility="gm_private_preparation",
                                body=canonical_json_bytes(item).decode("utf-8"),
                                provenance={
                                    "campaign_id": campaign_id,
                                    "plan_id": accepted.plan_id,
                                    "content_sha256": accepted.content_sha256,
                                    "section": key,
                                    "item_id": item_id,
                                },
                            )
                readable = accepted.content.get("campaign_plan_markdown")
                if isinstance(readable, str):
                    readable_sections = _markdown_sections(readable)
                    slug_counts = Counter(slug for _, slug, _ in readable_sections)
                    for heading, slug, content in readable_sections:
                        if slug_counts[slug] != 1:
                            continue
                        if len(readable_sections) > 1 and content == readable.strip():
                            continue
                        add_match(
                            source="campaign_plan",
                            path=f"readable#{slug}",
                            kind="plan_section",
                            label=heading,
                            visibility="gm_private_preparation",
                            body=content,
                            provenance={
                                "campaign_id": campaign_id,
                                "plan_id": accepted.plan_id,
                                "content_sha256": accepted.content_sha256,
                                "heading": heading,
                            },
                        )

            bindings = plan.get("bindings") if plan else None
            private_setting_references: set[str] = set()
            if plan and "campaign_setting" in requested_sources:
                _facts, private_references = self._campaign_setting_knowledge(campaign_id, plan)
                private_setting_references.update(private_references)
            for source in ("campaign_setting", "ruleset"):
                if source not in requested_sources or not isinstance(bindings, Mapping):
                    continue
                binding = bindings.get(source)
                if not isinstance(binding, Mapping):
                    continue
                identity = (
                    binding.get("id"),
                    binding.get("version"),
                    binding.get("content_sha256"),
                )
                if not all(isinstance(item, str) for item in identity):
                    continue
                package_entries = [
                    entry
                    for entry in snapshot.package_lock.entries
                    if (entry.package_id, entry.version, entry.sha256) == identity
                ]
                if source == "ruleset" and package_entries:
                    ruleset_entry = package_entries[0]
                    dependency_identities = {
                        (dependency.package_id, dependency.version, dependency.sha256)
                        for dependency in ruleset_entry.dependencies
                        if dependency.kind == "rules_reference"
                        or dependency.namespace == "rules_reference"
                    }
                    package_entries.extend(
                        entry
                        for entry in snapshot.package_lock.entries
                        if (entry.package_id, entry.version, entry.sha256) in dependency_identities
                    )
                for entry in package_entries:
                    paths = self._store.package_file_paths(
                        entry.package_id,
                        entry.version,
                        entry.sha256,
                        maximum_files=PACKAGE_MEMBER_COUNT_LIMIT,
                    )
                    for path in paths:
                        if Path(path).suffix.casefold() not in {
                            ".json",
                            ".md",
                            ".txt",
                            ".yaml",
                            ".yml",
                        }:
                            continue
                        try:
                            value = self._store.package_file_bytes(
                                entry.package_id,
                                entry.version,
                                entry.sha256,
                                path,
                                maximum_bytes=PACKAGE_MEMBER_BYTE_LIMIT,
                            )
                        except CommandRejected as error:
                            unavailable_count += 1
                            if len(unavailable_references) < 8:
                                unavailable_references.append(
                                    {
                                        "source": source,
                                        "path": path,
                                        "code": error.code or "campaign_reference_unavailable",
                                    }
                                )
                            continue
                        if value is None:
                            continue
                        try:
                            text = value.decode("utf-8")
                        except UnicodeDecodeError:
                            unavailable_count += 1
                            if len(unavailable_references) < 8:
                                unavailable_references.append(
                                    {
                                        "source": source,
                                        "path": path,
                                        "code": "campaign_reference_not_utf8",
                                    }
                                )
                            continue
                        package_provenance = {
                            "campaign_id": campaign_id,
                            "package_id": entry.package_id,
                            "package_version": entry.version,
                            "package_sha256": entry.sha256,
                            "source_path": path,
                        }
                        sections = (
                            _markdown_sections(text)
                            if len(value) <= REFERENCE_CONTENT_BYTE_LIMIT and path.endswith(".md")
                            else ()
                        )
                        if len(value) > REFERENCE_CONTENT_BYTE_LIMIT or any(
                            len(heading) > 200 or len(slug) > 200 for heading, slug, _ in sections
                        ):
                            for text_offset, excerpt in matching_text_excerpts(text, query, limit):
                                add_match(
                                    source=source,
                                    path=f"{path}#text/{text_offset}",
                                    kind="package_excerpt",
                                    label=Path(path).stem.replace("-", " ").title(),
                                    visibility=(
                                        "gm_private_preparation"
                                        if source == "campaign_setting"
                                        else "campaign_reference"
                                    ),
                                    body=excerpt,
                                    provenance={**package_provenance, "text_offset": text_offset},
                                    identity=path,
                                )
                            continue
                        if not sections:
                            add_match(
                                source=source,
                                path=path,
                                kind="package_reference",
                                label=Path(path).stem.replace("-", " ").title(),
                                visibility=(
                                    "gm_private_preparation"
                                    if path in private_setting_references
                                    else "campaign_reference"
                                ),
                                body=text,
                                provenance=package_provenance,
                            )
                            continue
                        path_terms = set(knowledge_terms(Path(path).stem.replace("-", " ")))
                        slug_counts = Counter(slug for _, slug, _ in sections)
                        for heading, slug, content in sections:
                            if slug_counts[slug] != 1:
                                continue
                            if len(sections) > 1 and content == text.strip():
                                continue
                            heading_key = heading.casefold().rstrip("s")
                            visibility = (
                                "gm_private_preparation"
                                if path in private_setting_references
                                or heading_key
                                in {item.rstrip("s") for item in _PRIVATE_PREPARATION_HEADINGS}
                                else "campaign_reference"
                            )
                            if visibility == "gm_private_preparation" and set(terms) <= path_terms:
                                continue
                            add_match(
                                source=source,
                                path=f"{path}#{slug}",
                                kind="authored_section",
                                label=heading,
                                visibility=visibility,
                                body=content,
                                provenance={**package_provenance, "heading": heading},
                                identity=f"{path} {heading}",
                            )

            matches = rank_references(query, candidates)
            return owned_payload(
                bounded_search_page(
                    {
                        "query": query,
                        "sources": list(requested_sources),
                        "campaign_revision": snapshot.revision,
                        "unavailable_count": unavailable_count,
                        "unavailable_references": unavailable_references,
                        "authority": (
                            "Discovery returns references only. Read the selected exact reference; "
                            "Campaign State remains current truth."
                        ),
                    },
                    matches,
                    offset=offset,
                    limit=limit,
                    maximum_bytes=REFERENCE_REPLY_BYTE_LIMIT,
                )
            )

    def read_campaign_reference(
        self,
        campaign_id: str,
        *,
        source: str,
        path: str,
        maximum_bytes: int = 64 * 1024,
    ) -> SafePayload:
        """Read one bounded reference from the campaign's exact admitted authority."""

        if (
            not isinstance(maximum_bytes, int)
            or isinstance(maximum_bytes, bool)
            or not 1 <= maximum_bytes <= REFERENCE_REPLY_BYTE_LIMIT
        ):
            raise CommandRejected(
                "Reference reply limit must be from 1 to 65536 bytes",
                code="campaign_reference_limit_invalid",
            )
        with self._operation():
            snapshot = self._store.snapshot(campaign_id)
            if source == "engine_format":
                if path != "spatial_scene":
                    raise CommandRejected("Engine format reference is unavailable")
                from sylvia_gm.domain.content_definitions import get_content_definition

                content_bytes = canonical_json_bytes(get_content_definition(path))
                if len(content_bytes) > maximum_bytes:
                    raise CommandRejected("Engine format reference exceeds the read bound")
                return owned_payload(
                    {
                        "source": source,
                        "path": path,
                        "campaign_id": campaign_id,
                        "content": content_bytes.decode("utf-8"),
                        "content_sha256": hashlib.sha256(content_bytes).hexdigest(),
                        "provenance": {"owner": "sylvia.application", "definition": path},
                        "precedence": (
                            "Installed engine input format only; this is not campaign state "
                            "or authority to invent actors, positions, or outcomes."
                        ),
                    }
                )
            if source == "campaign_state":
                if path == "current":
                    projected: object = {
                        "current_scene": snapshot.continuity.current_scene,
                        "facts_and_entities": list(snapshot.continuity.facts),
                        "reveals": snapshot.continuity.reveals,
                        "plan_progress": list(snapshot.continuity.plan_progress),
                        "ruleset_owned_values": self.desktop_snapshot(
                            campaign_id, display_only=True,
                        ).state,
                    }
                elif path == "inventory":
                    inventory_tool = self._inventory_tool(campaign_id)
                    inventory_binding = (
                        cast(SafePayload, inventory_tool.input_schema["x-sylvia-gm-inventory"])
                        if inventory_tool is not None else None
                    )
                    mechanical_inventory = (
                        inventory_binding is not None
                        and inventory_binding.get("format") == "mechanics"
                    )
                    projected = {
                        "mode": "ruleset" if inventory_tool is not None else "fallback",
                        "campaign_revision": snapshot.revision,
                        "holdings": list(inventory_holdings(snapshot))
                        if inventory_tool is None else None,
                        "ruleset_inventory": inventory_binding,
                        **({"update_tools": inventory_binding["tools"]}
                           if mechanical_inventory and inventory_binding is not None else {}),
                        "meaning": (
                            "Ruleset-owned inventory. Read the declared Campaign State or "
                            "participant current_state and use its Ruleset tools to change it. "
                            "Fallback inventory updates are unavailable."
                            if mechanical_inventory else
                            "Committed inventory. Query participant current_state "
                            "for Ruleset fields."
                        ),
                    }
                else:
                    projected = next(
                        (
                            fact
                            for fact in snapshot.continuity.facts
                            if fact.get("record_id") == path
                        ),
                        None,
                    )
                    if projected is None:
                        raise CommandRejected("Campaign State reference is unavailable")
                content = canonical_json_bytes(projected).decode("utf-8")
                if len(content.encode("utf-8")) > maximum_bytes:
                    raise CommandRejected(
                        "Campaign State reference exceeds the read bound",
                        code="campaign_reference_too_large",
                    )
                return owned_payload(
                    {
                        "source": source,
                        "path": path,
                        "campaign_id": campaign_id,
                        "campaign_revision": snapshot.revision,
                        "content": content,
                        "precedence": (
                            "Campaign State is current campaign truth and overrides Plan "
                            "preparation and Setting defaults"
                        ),
                    }
                )
            if source == "participant":
                participant = next(
                    (item for item in snapshot.participants if item.participant_id == path),
                    None,
                )
                if participant is None:
                    raise CommandRejected("Participant reference is unavailable")
                selector_summary: str | None = None
                authored_profile: str | None = None
                setting_data: SafePayload = {}
                profile_status = "sparse"
                source_character_file_id: str | None = None
                party_role = participant.data.get("party_role")
                try:
                    construction = self._store.campaign_construction_basis(campaign_id)
                except CommandRejected:
                    construction = None
                selected_party = (
                    construction.canonical_input.get("selected_party")
                    if construction is not None
                    and isinstance(construction.canonical_input, Mapping)
                    else None
                )
                if isinstance(selected_party, Sequence) and not isinstance(
                    selected_party, (str, bytes, bytearray)
                ):
                    selected_record = next(
                        (
                            item
                            for item in selected_party
                            if isinstance(item, Mapping)
                            and item.get("participant_id") == participant.participant_id
                        ),
                        None,
                    )
                    if isinstance(selected_record, Mapping) and isinstance(
                        selected_record.get("summary"), str
                    ):
                        selector_summary = str(selected_record["summary"])
                    if isinstance(selected_record, Mapping):
                        party_role = selected_record.get("party_role", party_role)
                    character_file = (
                        selected_record.get("character_file")
                        if isinstance(selected_record, Mapping)
                        else None
                    )
                    if isinstance(character_file, Mapping):
                        source_id = character_file.get("source_id")
                        stored_status = character_file.get("profile_status")
                        stored_profile = character_file.get("authored_profile")
                        stored_setting_data = character_file.get("setting_data")
                        if isinstance(stored_setting_data, Mapping):
                            setting_data = stored_setting_data
                        if isinstance(source_id, str) and source_id:
                            source_character_file_id = source_id
                        if stored_status == "authored" and isinstance(stored_profile, str):
                            profile_status = "authored"
                            authored_profile = stored_profile
                character_file = {
                    "participant_id": participant.participant_id,
                    "party_role": party_role,
                    "profile": {
                        "status": profile_status,
                        "authored_text": authored_profile,
                    },
                    "setting_data": setting_data,
                    "ruleset": {
                        "schema_kind": participant.kind,
                        "starting_data": participant.data,
                        "meaning": (
                            "Ruleset-compatible starting data. Opaque identifiers establish only "
                            "their exact stored values, not unwritten prose biography."
                        ),
                    },
                    **(
                        {
                            "selector": {
                                "summary": selector_summary,
                                "meaning": (
                                    "Short setup-catalog aid, not a substitute for authored "
                                    "biography."
                                ),
                            }
                        }
                        if selector_summary is not None
                        else {}
                    ),
                    **(
                        {"source_character_file_id": source_character_file_id}
                        if source_character_file_id is not None
                        else {}
                    ),
                }
                bookkeeping = self._character_update_tool(campaign_id)
                if bookkeeping is not None:
                    target = bookkeeping.input_schema["x-sylvia-gm-character-update"]
                    current = self._store.snapshot(campaign_id)
                    entity = current.state.get(
                        f"{target['namespace']}.{target['key']}", {}
                    ).get(participant.participant_id)
                    character_file["current_state"] = {
                        "campaign_revision": current.revision,
                        "fields": entity,
                        "update_schema": self._character_update_options(campaign_id)[
                            "character_update_schema"
                        ],
                        "meaning": "Current campaign holdings; starting_data is not live state.",
                    }
                content = canonical_json_bytes(character_file).decode("utf-8")
                if len(content.encode("utf-8")) > maximum_bytes:
                    raise CommandRejected("Participant reference exceeds the read bound")
                return owned_payload(
                    {
                        "source": source,
                        "path": path,
                        "campaign_id": campaign_id,
                        "content": content,
                        "precedence": (
                            "Campaign State and authoritative engine state override Character "
                            "File values for this campaign"
                        ),
                    }
                )
            accepted = self._store.accepted_campaign_plan(campaign_id)
            if accepted is None:
                raise CommandRejected("Campaign reference source requires an accepted plan")
            if source == "campaign_plan" and path == "adventures":
                adventures = self._store.campaign_adventures(campaign_id)
                content = canonical_json_bytes([
                    {"number": index + 1, "plan_id": item.plan_id,
                     "content_sha256": item.content_sha256,
                     "title": item.content["campaign_plan"]["title"],
                     "source_id": item.content.get("sequel", {}).get("source_id"),
                     "structure": item.content["campaign_plan"]["structure"]}
                    for index, item in enumerate(adventures)
                ]).decode("utf-8")
                if len(content.encode("utf-8")) > maximum_bytes:
                    raise CommandRejected("Adventure sequence exceeds the read bound")
                return owned_payload({"source": source, "path": path, "content": content,
                                      "campaign_id": campaign_id,
                                      "visibility": "gm_private_preparation"})
            if source == "campaign_plan" and path.startswith("adventures/"):
                match = re.fullmatch(r"adventures/([1-9][0-9]*)/(structured(?:/.*)?|markdown)", path)
                adventures = self._store.campaign_adventures(campaign_id)
                if match is None or int(match[1]) > len(adventures):
                    raise CommandRejected("Retained adventure reference is unavailable")
                accepted = adventures[int(match[1]) - 1]
                path = "readable" if match[2] == "markdown" else match[2]
            structured = accepted.content.get("campaign_plan")
            if source == "campaign_plan":
                if path.startswith("structured") and not isinstance(structured, Mapping):
                    raise CommandRejected("Campaign reference requires a structured plan")
                if path == "structured":
                    content = canonical_json_bytes(structured).decode("utf-8")
                    selection: Mapping[str, object] | None = None
                elif path.startswith("structured/"):
                    selected = _read_structured_campaign_plan(
                        cast(Mapping[str, object], structured),
                        path.removeprefix("structured/"),
                    )
                    content = canonical_json_bytes(selected).decode("utf-8")
                    selection = {
                        "kind": "structured_section",
                        "path": path.removeprefix("structured/"),
                    }
                elif path == "readable":
                    readable = accepted.content.get("campaign_plan_markdown")
                    if not isinstance(readable, str):
                        raise CommandRejected("Readable Campaign Plan is unavailable")
                    content = readable
                    selection = None
                elif path.startswith("readable/page/"):
                    readable = accepted.content.get("campaign_plan_markdown")
                    page = path.removeprefix("readable/page/")
                    if not isinstance(readable, str) or not re.fullmatch(r"[0-9]{1,10}", page):
                        raise CommandRejected("Readable Campaign Plan page is unavailable")
                    offset = int(page) * 16_000
                    if offset >= len(readable):
                        raise CommandRejected("Readable Campaign Plan page is past the end")
                    content = readable[offset:offset + 16_000]
                    selection = {
                        "kind": "text_page", "offset": offset,
                        "total_characters": len(readable),
                        "next_path": (
                            f"readable/page/{int(page) + 1}"
                            if offset + len(content) < len(readable) else None
                        ),
                    }
                elif path.startswith("readable#"):
                    readable = accepted.content.get("campaign_plan_markdown")
                    if not isinstance(readable, str):
                        raise CommandRejected("Readable Campaign Plan is unavailable")
                    heading, content = _select_markdown_section(
                        readable, path.removeprefix("readable#")
                    )
                    selection = {"kind": "markdown_section", "heading": heading}
                else:
                    raise CommandRejected("Campaign Plan reference path is unavailable")
                if len(content.encode("utf-8")) > maximum_bytes:
                    raise CommandRejected("Campaign Plan reference exceeds the read bound")
                return owned_payload(
                    {
                        "source": source,
                        "path": path,
                        "campaign_id": campaign_id,
                        "plan_id": accepted.plan_id,
                        "content_sha256": accepted.content_sha256,
                        "content": content,
                        **({"selection": selection} if selection is not None else {}),
                        "visibility": "gm_private_preparation",
                        "precedence": "committed state overrides plan projections",
                    }
                )
            bindings = structured.get("bindings")
            binding_name = {
                "campaign_setting": "campaign_setting",
                "ruleset": "ruleset",
            }.get(source)
            binding = (
                bindings.get(binding_name)
                if binding_name is not None and isinstance(bindings, Mapping)
                else None
            )
            if not isinstance(binding, Mapping):
                raise CommandRejected("Campaign package reference source is unavailable")
            package_id = binding.get("id")
            version = binding.get("version")
            content_sha256 = binding.get("content_sha256")
            if not all(isinstance(item, str) for item in (package_id, version, content_sha256)):
                raise CommandRejected("Campaign package reference binding is invalid")
            exact_entry = next(
                (
                    entry
                    for entry in snapshot.package_lock.entries
                    if (entry.package_id, entry.version, entry.sha256)
                    == (package_id, version, content_sha256)
                ),
                None,
            )
            if exact_entry is None:
                raise CommandRejected("Campaign package reference binding is stale")
            selected_entry = exact_entry
            portable_path, separator, fragment = path.partition("#")
            value = self._store.package_file_bytes(
                selected_entry.package_id,
                selected_entry.version,
                selected_entry.sha256,
                portable_path,
                maximum_bytes=PACKAGE_MEMBER_BYTE_LIMIT,
            )
            if value is None and source == "ruleset":
                reference_dependencies = tuple(
                    dependency
                    for dependency in exact_entry.dependencies
                    if dependency.kind == "rules_reference"
                    or dependency.namespace == "rules_reference"
                )
                if len(reference_dependencies) == 1:
                    dependency = reference_dependencies[0]
                    selected_entry = next(
                        (
                            entry
                            for entry in snapshot.package_lock.entries
                            if (entry.package_id, entry.version, entry.sha256)
                            == (
                                dependency.package_id,
                                dependency.version,
                                dependency.sha256,
                            )
                        ),
                        selected_entry,
                    )
                    if selected_entry is not exact_entry:
                        value = self._store.package_file_bytes(
                            selected_entry.package_id,
                            selected_entry.version,
                            selected_entry.sha256,
                            portable_path,
                            maximum_bytes=PACKAGE_MEMBER_BYTE_LIMIT,
                        )
            if value is None:
                raise CommandRejected("Campaign package reference path is unavailable")
            try:
                content = value.decode("utf-8")
            except UnicodeDecodeError as error:
                raise CommandRejected("Campaign package reference is not UTF-8") from error
            private_references: Sequence[str] = ()
            if not isinstance(structured, Mapping):
                raise CommandRejected("Campaign reference requires a structured plan")
            if source == "campaign_setting":
                _setting_facts, private_references = self._campaign_setting_knowledge(
                    campaign_id,
                    cast(Mapping[str, object], structured),
                )
            selection = None
            selected_heading: str | None = None
            available_sections: tuple[dict[str, str], ...] = ()
            available_section_count = 0
            excerpt_metadata: dict[str, object] = {}
            if separator and fragment.startswith("text/"):
                if re.fullmatch(r"text/(0|[1-9][0-9]{0,9})", fragment) is None:
                    raise CommandRejected(
                        "Reference text offset is unavailable",
                        code="campaign_reference_offset_invalid",
                    )
                excerpt = text_excerpt(content, int(fragment[5:]), maximum_bytes=maximum_bytes)
                content = excerpt["content"]
                following = excerpt["next_offset"]
                excerpt_metadata = {
                    "selection": excerpt["selection"],
                    "truncated": excerpt["truncated"],
                    "next_path": f"{portable_path}#text/{following}"
                    if following is not None
                    else None,
                }
            elif separator:
                selected_heading, content = _select_markdown_section(content, fragment)
                selection = {"kind": "markdown_section", "heading": selected_heading}
            elif source == "campaign_setting" and portable_path.startswith("locations/"):
                selected_overview = next(
                    (
                        (section_heading, section_content)
                        for section_heading, slug, section_content in _markdown_sections(content)
                        if slug == "overview"
                    ),
                    None,
                )
                if selected_overview is not None:
                    selected_heading, content = selected_overview
                    selection = {
                        "kind": "markdown_section",
                        "heading": selected_heading,
                        "default": True,
                    }
                    available_sections, available_section_count = _markdown_section_references(
                        value.decode("utf-8"),
                        path=portable_path,
                        document_private=portable_path in private_references,
                    )
            content_budget = min(maximum_bytes, REFERENCE_CONTENT_BYTE_LIMIT)
            if not excerpt_metadata and len(content.encode("utf-8")) > content_budget:
                if selection is not None:
                    raise CommandRejected(
                        "Reference section exceeds the reply bound; "
                        f"read {portable_path}#text/0 or search for a bounded excerpt",
                        code="campaign_reference_too_large",
                    )
                excerpt = text_excerpt(content, maximum_bytes=content_budget)
                content = excerpt["content"]
                following = excerpt["next_offset"]
                excerpt_metadata = {
                    "selection": excerpt["selection"],
                    "truncated": excerpt["truncated"],
                    "next_path": f"{portable_path}#text/{following}"
                    if following is not None
                    else None,
                }
            heading_key = (
                selected_heading.casefold().rstrip("s") if selected_heading is not None else ""
            )
            visibility = (
                "gm_private_preparation"
                if source == "campaign_setting"
                and (
                    portable_path in private_references
                    or bool(excerpt_metadata)
                    or heading_key in {item.rstrip("s") for item in _PRIVATE_PREPARATION_HEADINGS}
                )
                else "campaign_reference"
            )
            payload = owned_payload(
                {
                    "source": source,
                    "path": path,
                    "campaign_id": campaign_id,
                    "package": {
                        "id": selected_entry.package_id,
                        "version": selected_entry.version,
                        "content_sha256": selected_entry.sha256,
                    },
                    "content": content,
                    **excerpt_metadata,
                    **({"selection": selection} if selection is not None else {}),
                    **(
                        {
                            "available_sections": list(available_sections),
                            "available_section_count": available_section_count,
                            "available_sections_truncated": available_section_count
                            > len(available_sections),
                        }
                        if available_section_count
                        else {}
                    ),
                    "visibility": visibility,
                    **(
                        {
                            "interpretation": (
                                "Reusable Setting baseline, not current Campaign State. Examples "
                                "describe what can occur; they do not establish a current object, "
                                "route, event, or prior action."
                            )
                        }
                        if source == "campaign_setting"
                        else {}
                    ),
                    "precedence": (
                        "committed state overrides Setting lore"
                        if source == "campaign_setting"
                        else "MCP mechanics and committed state override Ruleset prose"
                    ),
                }
            )
            if len(canonical_json_bytes(payload)) > REFERENCE_REPLY_BYTE_LIMIT:
                raise CommandRejected(
                    "Reference reply metadata exceeds its byte bound; "
                    "use an exact bounded text excerpt",
                    code="campaign_reference_too_large",
                )
            return payload

    def consume_package_blob(
        self, blob_sha256: str, consumer: Callable[[bytes], None]
    ) -> PackageBlobReadReceipt:
        with self._operation():
            return self._store.consume_package_blob(blob_sha256, consumer)

    def add_package_recovery_root(self, root_id: str, blob_sha256: str) -> None:
        with self._operation():
            self._store.add_package_recovery_root(root_id, blob_sha256)

    def remove_package_recovery_root(self, root_id: str) -> None:
        with self._operation():
            self._store.remove_package_recovery_root(root_id)

    def reclaim_package_bytes(self) -> ReclamationResult:
        with self._operation():
            return self._store.reclaim_package_bytes()

    def open_turn(self, campaign_id: str, turn_id: str) -> None:
        """Open one new TURN after the previous TURN reached a checkpoint."""

        validate_authority_identity(campaign_id, "Campaign identity")
        validate_authority_identity(turn_id, "TURN identity")
        with self._operation(), self._campaign_work(campaign_id):
            self._reject_pending_mechanics_choice(campaign_id)
            self._store.open_turn(campaign_id, turn_id)

    def current_open_turn(self, campaign_id: str) -> str | None:
        """Project the exact currently open TURN without client inference."""

        validate_authority_identity(campaign_id, "Campaign identity")
        with self._operation():
            return self._store.current_open_turn(campaign_id)

    def complete_turn(self, campaign_id: str, turn_id: str) -> None:
        with self._operation(), self._campaign_work(campaign_id):
            self._store.complete_turn(campaign_id, turn_id)

    def package_surfaces(self, campaign_id: str) -> PackageSurfaceProjection:
        with self._operation():
            lock = self._store.current_package_lock(campaign_id).package_lock
            active_capabilities = frozenset(
                capability for entry in lock.entries for capability in entry.capabilities
            )
            prompts_raw, ui_raw = self._store.package_surfaces_for_current_lock(campaign_id)
            return project_surfaces(
                prompts_raw,
                ui_raw,
                active_capabilities=active_capabilities,
            )

    def campaign_panels(
        self, campaign_id: str, *, after_revision: int | None = None,
    ) -> SafePayload:
        """Project package-declared panels from canonical campaign records only."""

        with self._operation():
            snapshot = self._store.display_snapshot(
                campaign_id, actions_after_revision=after_revision,
            )
            if after_revision is not None and (
                type(after_revision) is not int or after_revision < 0
                or after_revision > snapshot.revision
            ):
                raise CommandRejected("Shot presentation revision is invalid")
            lock = self._store.current_package_lock(campaign_id).package_lock
            active_capabilities = frozenset(
                capability for entry in lock.entries for capability in entry.capabilities
            )
            prompts_raw, ui_raw = self._store.package_surfaces_for_current_lock(campaign_id)
            surfaces = project_surfaces(
                prompts_raw,
                ui_raw,
                active_capabilities=active_capabilities,
            ).ui_surfaces

            surfaces = player_ui_surfaces(surfaces, lock)

            def player_safe_source(source: object) -> bool:
                """Keep legacy installed packages from projecting private Plan data to players."""

                if not isinstance(source, Mapping):
                    return False
                kind = source.get("kind")
                if kind == "campaign_plan":
                    return False
                if kind == "object":
                    fields = source.get("fields")
                    return isinstance(fields, Mapping) and all(
                        player_safe_source(child) for child in fields.values()
                    )
                return kind in {"state", "participants", "literal"}

            projected_surfaces: list[dict[str, object]] = []
            for surface_spec in surfaces:
                source = surface_spec.get("source")
                if not player_safe_source(source):
                    continue
                data = resolve_surface_source(
                    source,
                    state=snapshot.state,
                    participants=snapshot.participants,
                )
                if surface_spec.get("renderer") == "piece_table":
                    viewer_ids = player_viewer_ids(
                        snapshot, self._decision_scope_declaration(snapshot),
                    )
                    try:
                        data = project_piece_table(
                            data, viewer_ids=viewer_ids,
                            action_ids=frozenset(action["id"] for action in surface_spec["actions"]),
                        )
                    except PieceTableError:
                        projected_surfaces.append({
                            **{key: thaw_json_value(value) for key, value in surface_spec.items()
                               if key != "source"},
                            "data": {"unavailable_reason": "The saved table cannot be displayed."},
                            "actions": [],
                        })
                        continue
                    surface_spec = owned_payload({
                        key: value for key, value in surface_spec.items() if key != "source"
                    })
                if surface_spec.get("renderer") in {"tactical_grid", "coordinate_map"}:
                    if (surface_spec.get("renderer") == "coordinate_map" and isinstance(data, Mapping)
                            and "enabled" in data and data["enabled"] is not True):
                        continue
                    try:
                        data = (project_coordinate_map(
                                    data, actor_reference=coordinate_actor_reference(surface_spec.get('actions')),
                                )
                                if surface_spec.get("renderer") == "coordinate_map"
                                else project_tactical_grid(data))
                    except TacticalGridError:
                        if not isinstance(data, Mapping) or data.get("scene") is None:
                            continue
                        projected_surfaces.append({
                            **cast(Mapping[str, object], thaw_json_value(surface_spec)),
                            "data": {
                                "unavailable_reason": (
                                    "The saved map data is invalid. "
                                    "This campaign needs a corrected map."
                                ),
                            },
                            "actions": [],
                        })
                        continue
                projected_surfaces.append(
                    {
                        **cast(Mapping[str, object], thaw_json_value(surface_spec)),
                        "data": data,
                    }
                )
            visible_physical_entities = visible_physical_entity_ids(projected_surfaces)
            safe_surfaces: list[dict[str, object]] = []
            for projected_surface in projected_surfaces:
                if projected_surface.get("renderer") == "network_graph":
                    try:
                        projected_surface["data"] = project_network_graph(
                            projected_surface.get("data"),
                            visible_physical_entities=visible_physical_entities,
                        )
                    except NetworkGraphError:
                        continue
                safe_surfaces.append(projected_surface)
            linked_surfaces = bind_physical_network_surfaces(safe_surfaces)
            with self._work_condition:
                busy = bool(self._campaign_work_counts.get(campaign_id, 0))
            actions_available = (
                not busy
                and snapshot.writer is WriterRole.DM
                and snapshot.lifecycle is LifecycleState.ACTIVE
                and self._store.current_open_turn(campaign_id) is not None
                and self._store.pending_mechanics_action(campaign_id) is None
            )
            available_tools = [
                tool for tool in self._catalog(campaign_id) if tool.player_controls.available
            ]
            response_available = actions_available
            control_scope = self._player_control_scope(snapshot)
            actions_available = actions_available and (
                control_scope is None or control_scope["available"] is True
            )
            def project_control(action: SafePayload) -> SafePayload:
                """Expose the declared actor parameter and current fixed-actor availability."""

                tools = [tool for tool in available_tools if tool.name == action.get("intent")]
                bindings = action.get("bindings", {})
                actor_parameter = (
                    tools[0].player_controls.actor_parameter if len(tools) == 1 else None
                )
                actor_parameter = actor_parameter or next(
                    (key for key, value in bindings.items() if value == "actor"), None,
                )
                available = actions_available and len(tools) == 1
                if available and actor_parameter not in bindings:
                    try:
                        validate_player_control(
                            control_scope, action.get("params", {}), actor_parameter,
                        )
                    except CommandRejected:
                        available = False
                return owned_payload({
                    **action, "available": available, "actor_parameter": actor_parameter,
                    "response_policy": tools[0].player_controls.response if len(tools) == 1 else None,
                    "checks_required": len(tools) == 1 and tools[0].check_player_action is not None,
                    "map_preview_enabled": bool(action.get("spatial_preview")) or (
                        len(tools) == 1 and tools[0].player_controls.map_preview is not None),
                })

            linked_surfaces = [
                {
                    **surface,
                    "actions": [project_control(action) for action in surface.get("actions", ())],
                }
                for surface in linked_surfaces
            ]
            pending_responses = self.player_action_context(campaign_id)
            response_record = pending_responses[-1] if pending_responses else None
            response_action = None if response_record is None else {
                "action_id": response_record["action_id"],
                "pending_count": len(pending_responses),
                "session_id": response_record["origin"].get("session_id"),
                "turn_id": response_record["turn_id"],
                "automatic": response_record["origin"].get("policy", {}).get("response")
                == "automatic",
                "available": response_available,
            }
            primary = next(
                (entry for entry in lock.entries if entry.role is PackageRole.PRIMARY), None,
            )
            starting_layout = None
            inventory_tool = self._inventory_tool(campaign_id)
            inventory_binding = None if inventory_tool is None else cast(
                SafePayload, inventory_tool.input_schema["x-sylvia-gm-inventory"],
            )
            specialised_inventory = any(
                surface.get("renderer") == "inventory"
                or (
                    inventory_binding is not None
                    and surface["id"] == inventory_binding.get("surface_id")
                )
                for surface in linked_surfaces
            )
            if inventory_binding is not None and inventory_binding.get("format") == "mechanics":
                if not any(
                    surface["id"] == inventory_binding["surface_id"] for surface in linked_surfaces
                ):
                    raise CommandRejected("The Ruleset's declared inventory surface is unavailable")
                specialised_inventory = True
            if not specialised_inventory:
                linked_surfaces.append(generic_inventory_surface(
                    snapshot, self._store.current_campaign_entities(campaign_id),
                    binding=inventory_binding,
                    state_target=None if inventory_tool is None else inventory_tool.input_schema[
                        "x-sylvia-gm-character-update"
                    ],
                ))
            if primary is not None:
                layout_bytes = self._store.package_file_bytes(
                    primary.package_id, primary.version, primary.sha256,
                    PANEL_LAYOUT_PATH, maximum_bytes=PANEL_LAYOUT_BYTE_LIMIT,
                )
                if layout_bytes is not None:
                    starting_layout = parse_panel_layout(layout_bytes, ui_raw)
            return owned_payload(
                {
                    "campaign_id": snapshot.campaign_id,
                    "revision": snapshot.revision,
                    "direct_actions_available": actions_available,
                    "ruleset_id": primary.package_id if primary is not None else None,
                    "layout": starting_layout,
                    "player_control": control_scope,
                    "response_action": response_action,
                    "surfaces": linked_surfaces,
                    "shot_effects": [
                        {
                            "action_id": action.action_id,
                            "revision": action.resulting_revision,
                            "shots": action.result["shot_effects"].get("shots", ()),
                            "unavailable": action.result["shot_effects"].get("unavailable", False),
                        }
                        for action in snapshot.actions
                        if after_revision is not None
                        and action.resulting_revision > after_revision
                        and action.status is ActionStatus.COMMITTED
                        and isinstance(action.result.get("shot_effects"), Mapping)
                    ],
                }
            )

    def panel_action_receipt(
        self, *, campaign_id: str, surface_id: str, panel_action_id: str, operation_id: str,
        expected_revision: int, selection: SafePayload | None = None,
        session_id: str | None = None,
    ) -> ActionReceipt | AwaitingChoiceReceipt | None:
        """Recover a matching direct receipt without preview, execution, dice or narration."""
        origin = player_control_origin(
            {"kind": "panel", "surface_id": surface_id, "action_id": panel_action_id},
            expected_revision=expected_revision, selection=selection, session_id=session_id)
        with self._operation():
            snapshot = self._store.snapshot(campaign_id)
            if session_id is not None and self._active_dm_context(session_id).session.campaign_id != campaign_id:
                raise CommandRejected("Player action belongs to another campaign")
            existing = self._store.action_hit(campaign_id, operation_id)
            if existing is not None:
                if not player_action_request_matches(player_action_origins(snapshot).get(operation_id), origin):
                    raise IdempotencyConflict("Player action identity payload does not match")
                return existing.receipt
            pending = self._store.pending_mechanics_action(campaign_id)
            if pending is not None and pending.action_id == operation_id:
                if not player_action_request_matches(pending.player_action_origin, origin):
                    raise IdempotencyConflict("Player action identity payload does not match")
                return _awaiting_choice_receipt(pending)
            return None

    def execute_panel_action(
        self, *, campaign_id: str, surface_id: str, panel_action_id: str, operation_id: str,
        expected_revision: int, selection: SafePayload | None = None,
        session_id: str | None = None, confirmation: str | None = None,
    ) -> ActionReceipt | AwaitingChoiceReceipt:
        """Execute the current declared action, requiring its exact preview when selected."""

        return cast(ActionReceipt | AwaitingChoiceReceipt, self.player_action(
            campaign_id=campaign_id,
            source={"kind": "panel", "surface_id": surface_id, "action_id": panel_action_id},
            operation_id=operation_id, expected_revision=expected_revision, selection=selection,
            session_id=session_id, confirmation=confirmation,
        ))

    def preview_panel_action(
        self, *, campaign_id: str, surface_id: str, panel_action_id: str, operation_id: str,
        expected_revision: int, selection: SafePayload | None = None,
        session_id: str | None = None,
    ) -> SafePayload:
        """Prepare a read-only proposal bound to the exact action, selection and revision."""

        return cast(SafePayload, self.player_action(
            campaign_id=campaign_id,
            source={"kind": "panel", "surface_id": surface_id, "action_id": panel_action_id},
            operation_id=operation_id, expected_revision=expected_revision, selection=selection,
            session_id=session_id, preview_only=True,
        ))

    def _resolve_panel_control(
        self, snapshot: CampaignState, source: SafePayload, selection: SafePayload | None,
        *, known_map: bool = False,
    ) -> tuple[CatalogTool, SafePayload, str, str | None]:
        """Read a current panel declaration and bind only its advertised selection fields."""

        campaign_id = snapshot.campaign_id
        surface_id = source["surface_id"]
        panel_action_id = source["action_id"]
        lock = self._store.current_package_lock(campaign_id).package_lock
        active_capabilities = frozenset(
            capability for entry in lock.entries for capability in entry.capabilities
        )
        prompts_raw, ui_raw = self._store.package_surfaces_for_current_lock(campaign_id)
        surfaces = project_surfaces(
            prompts_raw,
            ui_raw,
            active_capabilities=active_capabilities,
        ).ui_surfaces
        surfaces = player_ui_surfaces(surfaces, lock)
        surface = next((item for item in surfaces if item.get("id") == surface_id), None)
        if surface is None:
            raise CommandRejected(
                "Panel action is unavailable", code="panel_action_unavailable"
            )
        raw_actions = surface.get("actions")
        actions = raw_actions if isinstance(raw_actions, Sequence) else ()
        action = next(
            (
                item
                for item in actions
                if isinstance(item, Mapping) and item.get("id") == panel_action_id
            ),
            None,
        )
        if action is None:
            raise CommandRejected(
                "Panel action is unavailable", code="panel_action_unavailable"
            )
        intent = action.get("intent")
        if not isinstance(intent, str):
            raise CommandRejected("Panel action is invalid", code="panel_action_invalid")
        matching_tools = tuple(
            tool for tool in self._catalog(campaign_id) if tool.name == intent
        )
        if len(matching_tools) != 1:
            raise CommandRejected(
                "Panel mechanic is unavailable", code="panel_action_unavailable"
            )
        fixed_parameters = action.get("params")
        if not isinstance(fixed_parameters, Mapping):
            raise CommandRejected("Panel action is invalid", code="panel_action_invalid")
        bindings = action.get("bindings")
        if bindings is None:
            if selection is not None and selection:
                raise CommandRejected("Panel action is invalid", code="panel_action_invalid")
            parameters = owned_payload(fixed_parameters)
        elif surface.get("renderer") == "inventory":
            source_data = resolve_surface_source(surface.get("source"), state=snapshot.state, participants=snapshot.participants)
            parameters = resolve_inventory_action_parameters(action, selection, source_data)
        elif surface.get("renderer") == "piece_table":
            source_data = resolve_surface_source(
                surface.get("source"), state=snapshot.state, participants=snapshot.participants,
            )
            try:
                table = project_piece_table(
                    source_data,
                    viewer_ids=player_viewer_ids(snapshot, self._decision_scope_declaration(snapshot)),
                    action_ids=frozenset(item["id"] for item in surface["actions"]),
                )
                parameters = resolve_piece_action_parameters(action, selection, table)
            except PieceTableError as error:
                raise CommandRejected(
                    "Panel action selection is invalid", code="panel_action_invalid",
                ) from error
        elif surface.get("renderer") in {"party", "unit_list"}:
            source_data = resolve_surface_source(
                surface.get("source"), state=snapshot.state, participants=snapshot.participants,
            )
            parameters = resolve_party_action_parameters(
                action, selection, source_data,
                allow_entity_ids=surface.get("renderer") == "unit_list",
            )
        elif surface.get("renderer") in {"tactical_grid", "coordinate_map"}:
            source_data = resolve_surface_source(
                surface.get("source"),
                state=snapshot.state,
                participants=snapshot.participants,
            )
            try:
                tactical_projection = (project_coordinate_map(
                                           source_data, actor_reference=coordinate_actor_reference(surface.get('actions')),
                                       )
                                       if surface.get("renderer") == "coordinate_map"
                                       else project_tactical_grid(source_data))
                parameters = resolve_tactical_action_parameters(
                    action,
                    selection,
                    tactical_projection,
                    authoritative_scene=(
                        player_known_spatial_scene(source_data.get("scene")) if known_map
                        else source_data.get("scene")
                    ),
                )
            except TacticalGridError as error:
                raise CommandRejected(
                    "Panel action selection is invalid",
                    code="panel_action_invalid",
                ) from error
        elif surface.get("renderer") == "network_graph":
            source_data = resolve_surface_source(
                surface.get("source"),
                state=snapshot.state,
                participants=snapshot.participants,
            )
            try:
                network_projection = project_network_graph(source_data)
                scope = self._player_control_scope(snapshot)
                parameters = resolve_network_action_parameters(
                    action,
                    selection,
                    network_projection,
                    controlled_actor_ids=frozenset(
                        actor["id"] for actor in scope["actors"]
                    ) if scope is not None and scope["available"] is True else frozenset(),
                )
            except NetworkGraphError as error:
                raise CommandRejected(
                    "Panel action selection is invalid",
                    code="panel_action_invalid",
                ) from error
        else:
            raise CommandRejected("Panel action is invalid", code="panel_action_invalid")
        actor_parameter = next(
            (key for key, value in bindings.items() if value == "actor"), None,
        ) if isinstance(bindings, Mapping) else None
        return (
            matching_tools[0], parameters, str(action.get("label", panel_action_id)),
            matching_tools[0].player_controls.actor_parameter or actor_parameter,
        )

    def _resolve_card_control(
        self, snapshot: CampaignSnapshot | CampaignDisplaySnapshot,
        source: SafePayload, selection: SafePayload | None,
    ) -> tuple[CatalogTool, SafePayload, str, str | None]:
        """Read an immutable saved button and validate its request against the current package."""

        if selection:
            raise CommandRejected("Result-card actions do not accept replacement parameters")
        original = next(
            (action for action in snapshot.actions if action.action_id == source["action_id"]),
            None,
        )
        card = original.result.get("result_card") if original is not None else None
        actions = card.get("actions") if isinstance(card, Mapping) else None
        index = source["button_index"]
        if (not isinstance(card, Mapping) or type(card.get("version")) is not int
                or card.get("version") != 1
                or not isinstance(actions, Sequence) or len(actions) > 8 or index >= len(actions)):
            raise CommandRejected("The saved card action is unavailable")
        action = actions[index]
        if not isinstance(action, Mapping) or not isinstance(action.get("parameters"), Mapping):
            raise CommandRejected("The saved card action is unavailable")
        tools = tuple(
            tool for tool in self._catalog(snapshot.campaign_id)
            if tool.name == action.get("tool") and tool.package_id == original.package_id
        )
        if len(tools) != 1:
            raise CommandRejected("The saved card mechanic is currently unavailable")
        return (
            tools[0], owned_payload(action["parameters"]), str(action.get("label", "Action")),
            tools[0].player_controls.actor_parameter,
        )

    def _card_control_readiness(
        self, snapshot: CampaignState, original: ActionRecord | None,
        tools: Mapping[tuple[str, str], CatalogTool], control_scope: SafePayload | None,
    ) -> list[SafePayload]:
        """Refresh button admission separately from its immutable displayed result."""

        card = original.result.get("result_card") if original is not None else None
        actions = card.get("actions") if isinstance(card, Mapping) else None
        if (not isinstance(card, Mapping) or type(card.get("version")) is not int
                or card.get("version") != 1
                or not isinstance(actions, Sequence) or len(actions) > 8):
            return []
        available = []
        for action in actions:
            if not isinstance(action, Mapping) or not isinstance(action.get("tool"), str):
                available.append({"available": False, "reason": "This action is unavailable"})
                continue
            tool = tools.get((original.package_id, action.get("tool")))
            parameters = action.get("parameters")
            if (tool is None or not tool.player_controls.available
                    or not isinstance(parameters, Mapping)):
                available.append({"available": False, "reason": "This action is unavailable"})
                continue
            try:
                validate_parameters(parameters, tool.input_schema)
                validate_references(
                    parameters, tool.input_schema,
                    participant_ids={item.participant_id for item in snapshot.participants},
                )
                validate_player_control(
                    control_scope, parameters, tool.player_controls.actor_parameter,
                )
                self._check_player_action(tool, parameters, snapshot)
            except CommandRejected as error:
                available.append({"available": False, "reason": str(error)})
            else:
                available.append({"available": True, "reason": None})
        return available

    def _check_player_action(
        self, tool: CatalogTool, parameters: SafePayload, snapshot: CampaignState,
    ) -> None:
        """Apply package checks at projection and immediately before execution."""

        if tool.check_player_action is None:
            return
        reason = tool.check_player_action(parameters, snapshot.state, {
            participant.participant_id: _participant_context_payload(participant)
            for participant in snapshot.participants
        })
        if reason is not None:
            raise CommandRejected(reason, code="player_action_unavailable")

    def player_action_availability(
        self, *, campaign_id: str, expected_revision: int, requests: Sequence[SafePayload],
    ) -> SafePayload:
        """Batch control checks without reserving actions, rolling dice, or changing state."""

        if (not isinstance(requests, (list, tuple)) or len(requests) > 256
                or any(not isinstance(item, Mapping) or set(item) != {"source", "selection"}
                       for item in requests)):
            raise CommandRejected("Player control check requests are invalid")
        with self._operation():
            requested_action_ids = tuple(
                request["source"]["action_id"] for request in requests
                if isinstance(request["source"], Mapping)
                and request["source"].get("kind") != "panel"
                and isinstance(request["source"].get("action_id"), str)
            )
            snapshot = self._store.display_snapshot(campaign_id, action_ids=requested_action_ids)
            if snapshot.revision != expected_revision:
                raise StaleRevision("Player control checks expected an obsolete campaign revision")
            with self._work_condition:
                busy = bool(self._campaign_work_counts.get(campaign_id, 0))
            scope = self._player_control_scope(snapshot)
            base_reason = (
                "Sylvia is responding" if busy else
                "Gameplay is unavailable in this session"
                if snapshot.writer is not WriterRole.DM
                or snapshot.lifecycle is not LifecycleState.ACTIVE
                or self._store.current_open_turn(campaign_id) is None else
                "Resolve the pending mechanics choice first"
                if self._store.pending_mechanics_action(campaign_id) is not None else None
            )
            results = []
            for request in requests:
                reason = base_reason
                presentation = {}
                if reason is None:
                    try:
                        source, selection = request["source"], request["selection"]
                        player_control_origin(
                            source, expected_revision=expected_revision,
                            selection=selection, session_id=None,
                        )
                        tool, parameters, _, actor_parameter = (
                            self._resolve_panel_control(snapshot, source, selection)
                            if source["kind"] == "panel"
                            else self._resolve_card_control(snapshot, source, selection)
                        )
                        if not tool.player_controls.available:
                            raise CommandRejected("This Ruleset's player controls are unavailable")
                        validate_parameters(parameters, tool.input_schema)
                        validate_references(
                            parameters, tool.input_schema,
                            participant_ids={item.participant_id for item in snapshot.participants},
                        )
                        validate_player_control(scope, parameters, actor_parameter)
                        self._check_player_action(tool, parameters, snapshot)
                        presentation = self._player_action_preview(
                            tool, parameters, snapshot, source, selection,
                        ) or {}
                        if tool.player_controls.map_preview is None:
                            presentation = self._player_action_preview(
                                tool, parameters, snapshot, source, selection,
                            ) or {}
                    except CommandRejected as error:
                        reason = str(error)
                    except (TypeError, ValueError, KeyError):
                        reason = "Choose the required action selections"
                results.append({"available": reason is None, "reason": reason, **presentation})
            return owned_payload({
                "campaign_id": campaign_id, "revision": snapshot.revision, "results": results,
            })

    def _player_action_preview(
        self, tool: CatalogTool, parameters: SafePayload, snapshot: CampaignState,
        source: SafePayload, selection: SafePayload | None,
    ) -> SafePayload | None:
        """Bind display-only paths to known geometry; commitment keeps the full-scene parameters."""

        if source["kind"] == "panel" and selection:
            lock = self._store.current_package_lock(snapshot.campaign_id).package_lock
            prompts, ui = self._store.package_surfaces_for_current_lock(snapshot.campaign_id)
            surfaces = project_surfaces(prompts, ui, active_capabilities=frozenset(
                capability for entry in lock.entries for capability in entry.capabilities)).ui_surfaces
            surface = next((row for row in surfaces if row['id'] == source['surface_id']), None)
            action = next((row for row in surface.get('actions', ()) if row['id'] == source['action_id']), None) if surface else None
            if action and action.get('spatial_preview'):
                data = resolve_surface_source(surface['source'], state=snapshot.state, participants=snapshot.participants)
                data = {**data, 'scene': player_known_spatial_scene(data['scene'])}
                return segment_preview({**surface, 'action': action}, data, selection)
        if tool.preview_player_action is None:
            return None
        try:
            if source["kind"] == "panel":
                _, parameters, _, _ = self._resolve_panel_control(
                    snapshot, source, selection, known_map=True,
                )
            return tool.preview_player_action(parameters, snapshot.state, {
                participant.participant_id: _participant_context_payload(participant)
                for participant in snapshot.participants
            })
        except (CommandRejected, TypeError, ValueError, KeyError):
            return None

    def player_action(self, **kwargs):
        """Declared player action through the shared campaign-work fence."""
        with self._operation(), self._direct_action_work(kwargs['campaign_id']):
            return self._player_action_in_owned_work(**kwargs)

    def action_sequence(self, *, session_id, sequence_id=None):
        from .action_sequences import read
        return read(self, session_id=session_id, sequence_id=sequence_id)

    def action_sequence_response_results(self, *, session_id, basis_message_id):
        from .action_sequences import response_results
        return response_results(self, session_id=session_id, basis_message_id=basis_message_id)

    def compose_action_sequence(self, *, campaign_id, session_id, sequence_id,
                                expected_revision, steps, expected_version=0):
        from .action_sequences import compose
        return compose(self, campaign_id=campaign_id, session_id=session_id,
                       sequence_id=sequence_id, expected_revision=expected_revision,
                       steps=steps, expected_version=expected_version)

    def execute_action_sequence(self, *, campaign_id, session_id, sequence_id,
                                expected_version, confirmation=None):
        from .action_sequences import execute
        return execute(self, campaign_id=campaign_id, session_id=session_id,
                       sequence_id=sequence_id, expected_version=expected_version,
                       confirmation=confirmation)

    def _compose_action_sequence_from_agent(self, **kwargs):
        from .action_sequences import compose
        return compose(self, **kwargs, from_agent=True)

    def _execute_action_sequence_from_agent(self, **kwargs):
        from .action_sequences import execute
        return execute(self, **kwargs, from_agent=True)

    def _player_action_in_owned_work(
        self,
        *,
        campaign_id: str,
        source: SafePayload,
        operation_id: str,
        expected_revision: int,
        selection: SafePayload | None = None,
        session_id: str | None = None,
        confirmation: str | None = None,
        preview_only: bool = False,
    ) -> ActionReceipt | AwaitingChoiceReceipt | SafePayload:
        """Preview or execute a declared player control through the shared action transaction."""

        for value, label in (
            (campaign_id, "Campaign identity"),
            (operation_id, "Panel action operation identity"),
        ):
            validate_authority_identity(value, label)
        origin = player_control_origin(
            source, expected_revision=expected_revision, selection=selection, session_id=session_id,
        )
        snapshot = self._store.snapshot(campaign_id)
        if snapshot.writer is not WriterRole.DM:
            raise CommandRejected("Player actions require GM campaign authority")
        if session_id is not None:
            context = self._active_dm_context(session_id)
            if context.session.campaign_id != campaign_id:
                raise CommandRejected("Player action belongs to another campaign")
        existing = self._store.action_hit(campaign_id, operation_id)
        if existing is not None:
            if not player_action_request_matches(
                player_action_origins(snapshot).get(operation_id), origin,
            ):
                raise IdempotencyConflict("Player action identity payload does not match")
            if preview_only:
                raise CommandRejected("This player action has already resolved")
            self._append_player_action_references(session_id, existing.receipt)
            return existing.receipt
        pending = self._store.pending_mechanics_action(campaign_id)
        if pending is not None and pending.action_id == operation_id:
            if not player_action_request_matches(pending.player_action_origin, origin):
                raise IdempotencyConflict("Player action identity payload does not match")
            if preview_only:
                raise CommandRejected("This player action is awaiting a choice")
            return _awaiting_choice_receipt(pending)
        if snapshot.revision != expected_revision:
            raise StaleRevision("Panel action expected an obsolete campaign revision")
        if pending is not None:
            raise CommandRejected("Resolve the pending mechanics choice first")
        tool, parameters, label, actor_parameter = (
            self._resolve_panel_control(snapshot, source, selection)
            if source["kind"] == "panel"
            else self._resolve_card_control(snapshot, source, selection)
        )
        turn_id = self._store.current_open_turn(campaign_id)
        if turn_id is None:
            raise CommandRejected("Panel action requires an open turn", code="turn_unavailable")
        policy = tool.player_controls
        if not policy.available:
            raise CommandRejected(
                "This Ruleset's player controls are unavailable",
                code="panel_action_unavailable",
            )
        validate_parameters(parameters, tool.input_schema)
        basis = self._store.action_basis(campaign_id, turn_id)
        validate_references(
            parameters, tool.input_schema, participant_ids=basis.participant_ids,
        )
        control_scope = self._player_control_scope(snapshot)
        validate_player_control(control_scope, parameters, actor_parameter)
        self._check_player_action(tool, parameters, snapshot)
        policy_values = {"confirmation": policy.confirmation, "response": policy.response}
        confirmation_token = hashlib.sha256(canonical_json_bytes({
            "operation_id": operation_id, "origin": origin, "parameters": parameters,
            "package_sha256": tool.package_sha256, "tool": tool.name, "policy": policy_values,
        })).hexdigest()
        if preview_only:
            presentation = self._player_action_preview(
                tool, parameters, snapshot, source, selection,
            )
            # Confirm the resolved actor even when a saved card has no live selection.
            actors = cast(Sequence[SafePayload], (control_scope or {}).get("actors", []))
            actor = next((
                item for item in actors
                if actor_parameter is not None
                and item["id"] == parameters.get(actor_parameter)
            ), None)
            return owned_payload({
                "campaign_id": campaign_id, "revision": expected_revision,
                "label": label,
                "selection": selection or {},
                "actor": actor,
                "confirmation_required": policy.confirmation == "required",
                "confirmation": confirmation_token,
                "response": policy.response,
                "preview": project_result_card(
                    policy.preview, parameters=parameters, public_context={},
                ),
                **(presentation or {}),
            })
        if policy.confirmation == "required" and confirmation != confirmation_token:
            raise CommandRejected(
                "Preview and confirm the current action first",
                code="action_confirmation_required",
            )
        origin = owned_payload({**origin, "policy": policy_values})
        receipt = self._execute_action(
            ActionCommand(
                campaign_id=campaign_id,
                action_id=operation_id,
                idempotency_key=operation_id,
                owner_id=self._owner_id,
                writer=snapshot.writer,
                expected_revision=expected_revision,
                turn_id=turn_id,
                package_id=tool.package_id,
                tool_name=tool.name,
                parameters=owned_payload(parameters),
                initiating_session_id=session_id,
            ),
            player_action_origin=origin,
        )
        if isinstance(receipt, ActionReceipt):
            self._append_player_action_references(session_id, receipt)
        return receipt

    def _append_player_action_references(
        self, session_id: str | None, receipt: ActionReceipt,
    ) -> None:
        """Place one direct action's public call and result in its initiating session."""

        if session_id is None:
            return
        snapshot = self._store.snapshot(receipt.campaign_id)
        events = tuple(
            event for event in snapshot.events
            if event.action_id == receipt.action_id
            and event.event_type in {CanonicalEventType.TOOL_CALL, CanonicalEventType.TOOL_RESULT}
        )
        self._sessions.append_canonical_references(session_id, events)

    def player_action_response_context(
        self, campaign_id: str, basis: Mapping[str, object],
    ) -> tuple[SafePayload, ...]:
        """Read exactly the saved outcomes assigned to one response, including retries."""

        with self._operation():
            snapshot = self._store.snapshot(campaign_id)
            identities = basis.get("player_action_ids")
            if not isinstance(identities, Sequence) or isinstance(identities, (str, bytes, bytearray)):
                # Older saved requests contain the canonical boundary instead of IDs.
                boundary = basis.get("canonical_start_sequence")
                if not isinstance(boundary, int):
                    identities = (basis.get("player_action_id"),)
                else:
                    previous = max((event.sequence for event in snapshot.events
                                    if event.event_type is CanonicalEventType.TALK
                                    and event.sequence <= boundary), default=0)
                    identities = tuple(event.action_id for event in snapshot.events
                                       if previous < event.sequence <= boundary)
            return tuple(record for record in player_action_records(snapshot)
                         if record["action_id"] in identities)

    def _player_response_reservations(
        self, campaign_id: str,
    ) -> tuple[set[str], set[str]]:
        """Find application-owned response outcomes and narration attempt identities."""

        reserved: set[str] = set()
        attempts: set[str] = set()
        for context in self._store.campaign_session_contexts(campaign_id):
            requests = {message.message_id: message for message in context.messages
                        if message.kind is SessionMessageKind.EVIDENCE
                        and message.content.get("kind") == "player_action_response"}
            for message in requests.values():
                reserved.update(str(row["action_id"]) for row in
                                self.player_action_response_context(campaign_id, message.content))
                if isinstance(message.content.get("attempt_id"), str):
                    attempts.add(message.content["attempt_id"])
            for message in context.messages:
                if (message.kind is SessionMessageKind.EVIDENCE
                    and message.content.get("kind") in {
                        "player_action_response_retry", "gm_turn_activity"}
                    and message.content.get("basis_message_id") in requests
                    and isinstance(message.content.get("attempt_id"), str)):
                    attempts.add(message.content["attempt_id"])
        from .action_sequences import response_reservations
        queue_reserved, queue_attempts = response_reservations(self, campaign_id)
        reserved.update(queue_reserved)
        attempts.update(queue_attempts)
        return reserved, attempts

    def player_action_context(self, campaign_id: str) -> tuple[SafePayload, ...]:
        """Supply outcomes still awaiting their own GM response."""

        with self._operation():
            snapshot = self._store.snapshot(campaign_id)
            reserved, response_attempts = self._player_response_reservations(campaign_id)
            last_response = max((event.sequence for event in snapshot.events
                                 if event.event_type is CanonicalEventType.TALK
                                 and event.attempt_id not in response_attempts), default=0)
            recent_actions = {event.action_id for event in snapshot.events
                              if event.sequence > last_response}
            return tuple(record for record in player_action_records(snapshot)
                         if record["action_id"] in recent_actions
                         and record["action_id"] not in reserved)

    def player_action_response_basis(
        self, session_id: str, action_id: str, *, automatic: bool = False,
    ) -> SafePayload:
        """Bind a response request to the saved player action in its own GM session."""

        with self._operation():
            context = self._active_dm_context(session_id)
            snapshot = self._store.snapshot(context.session.campaign_id)
            record = next(
                (
                    item for item in player_action_records(snapshot)
                    if item["action_id"] == action_id
                ),
                None,
            )
            if record is None or record["origin"].get("session_id") not in (None, session_id):
                raise CommandRejected("The saved player action is unavailable to this session")
            policy = record["origin"].get("policy", {})
            if automatic and policy.get("response") != "automatic":
                raise CommandRejected("The Ruleset did not request an automatic response")
            reserved, _ = self._player_response_reservations(snapshot.campaign_id)
            if action_id in reserved:
                raise CommandRejected(
                    "A response was already requested for this action; use Retry response "
                    "if it was interrupted",
                    code="player_action_response_started",
                )
            if action_id not in {
                item["action_id"] for item in self.player_action_context(snapshot.campaign_id)
            }:
                raise CommandRejected("Sylvia has already responded to this action")
            return record

    def catalog_definition_bytes(self, campaign_id: str) -> bytes:
        tools = self.catalog(campaign_id)
        return canonical_json_bytes(
            [
                {
                    "package_id": tool.package_id,
                    "version": tool.package_version,
                    "content_sha256": tool.package_sha256,
                    "name": tool.name,
                    "capability": tool.capability,
                    "input_schema": tool.input_schema,
                }
                for tool in tools
            ]
        )

    def package_surface_bytes(self, campaign_id: str) -> bytes:
        return projection_bytes(self.package_surfaces(campaign_id))

    def activate_package(
        self, command: ActivationCommand, migrate: MigrationFunction
    ) -> ActivationResult:
        with self._operation():
            self._reject_pending_mechanics_choice(command.campaign_id)
            payload_sha256 = activation_payload_sha256(command)
            existing = self._store.activation_hit(command.activation_id)
            if existing is not None:
                if existing[0] != payload_sha256:
                    raise IdempotencyConflict("Activation identity payload does not match")
                return existing[1]
            candidate = self._store.package_candidate(command.candidate_id)
            self._reserve_campaign_transition(command.campaign_id)
            try:
                with self._lane(command.campaign_id):
                    revision, current, coherent_candidate, state, completed = (
                        self._store.activation_basis(command.campaign_id, command.candidate_id)
                    )
                    if revision != command.expected_revision:
                        raise StaleRevision("Activation expected revision is stale")
                    if current.lock_id != command.expected_lock_id:
                        raise CommandRejected("Activation expected lock is stale")
                    if not completed:
                        raise CommandRejected("Activation requires a completed TURN")
                    if coherent_candidate.closure_sha256 != candidate.closure_sha256:
                        raise CommandRejected("Activation candidate changed")
                    migrated_state = run_migration_outside_sqlite(state, migrate)
                    return self._store.commit_package_activation(
                        campaign_id=command.campaign_id,
                        activation_id=command.activation_id,
                        candidate_id=command.candidate_id,
                        payload_sha256=payload_sha256,
                        expected_revision=command.expected_revision,
                        expected_lock_id=command.expected_lock_id,
                        migration_id=command.migration_id,
                        migrated_state=migrated_state,
                    )
            finally:
                self._release_campaign_transition(command.campaign_id)

    def snapshot(self, campaign_id: str) -> CampaignSnapshot:
        with self._operation():
            return self._store.snapshot(campaign_id)

    def desktop_snapshot(
        self, campaign_id: str, *, display_only: bool = False,
    ) -> CampaignSnapshot | CampaignDisplaySnapshot:
        """Keep package-declared engine state and its mutation history off the desktop wire."""
        with self._operation():
            snapshot = (self._store.display_snapshot(campaign_id) if display_only
                        else self._store.snapshot(campaign_id))
            snapshot = self._project_private_tool_inputs(snapshot)
            private_keys = self._engine_private_keys(snapshot.package_lock)
            safe_state = {
                key: value for key, value in snapshot.state.items() if key not in private_keys
            }
            if isinstance(snapshot, CampaignSnapshot):
                return replace(snapshot, state=safe_state, mutations=tuple(
                    item for item in snapshot.mutations
                    if f"{item.namespace}.{item.key}" not in private_keys
                ))
            return replace(snapshot, state=safe_state)

    def _project_private_tool_inputs(
        self, snapshot: _Snapshot,
    ) -> _Snapshot:
        """Keep canonical input bytes in storage and omit hidden-game arguments from readers."""
        private_keys = self._engine_private_keys(snapshot.package_lock)
        private_packages = {entry.package_id for entry in snapshot.package_lock.entries
                            if any(key.startswith(entry.package_id + ".") for key in private_keys)}
        actions = tuple(replace(action, parameters=owned_payload({}))
                        if action.package_id in private_packages else action
                        for action in snapshot.actions)
        events = tuple(replace(event, safe_payload=owned_payload({
                           **event.safe_payload, "parameters": {},
                       }))
                       if event.event_type is CanonicalEventType.TOOL_CALL
                       and event.safe_payload.get("package_id") in private_packages else event
                       for event in snapshot.events)
        return replace(snapshot, actions=actions, events=events)

    def _engine_private_keys(self, lock: PackageLock) -> frozenset[str]:
        keys: set[str] = set()
        for entry in lock.entries:
            manifest = self._store.exact_package_manifest(
                entry.package_id, entry.version, entry.sha256,
            )
            if manifest is not None:
                keys.update(
                    f"{entry.package_id}.{item.key}" for item in manifest.state_schemas
                    if item.visibility == "engine"
                )
        return frozenset(keys)

    def model_context_snapshot(
        self, campaign_id: str,
    ) -> tuple[CampaignDisplaySnapshot, ModelDecisionBoundary | None]:
        """Project public state and a private game's permitted actor from one revision."""
        with self._operation():
            snapshot = self._store.display_snapshot(campaign_id)
            private_keys = self._engine_private_keys(snapshot.package_lock)
            visible = replace(snapshot, state={
                key: value for key, value in snapshot.state.items() if key not in private_keys
            })
            boundary = None
            if private_keys:
                declared = self._decision_scope_declaration(snapshot)
                if declared is not None:
                    try:
                        boundary = project_model_decision_scope(snapshot, declared)
                        isolated = declared.get("isolated_participant")
                        if (isinstance(isolated, Mapping) and boundary is not None
                                and boundary.owner is ModelDecisionOwner.GM):
                            boundary = replace(
                                boundary,
                                actor_state=public_actor_observation(snapshot, isolated),
                                eligible_actors=(),
                            )
                    except DecisionScopeProjectionError as error:
                        raise CommandRejected(str(error)) from error
            return visible, boundary

    def model_context_owner(self, campaign_id: str) -> SafePayload | None:
        """Identify the GM's native history owner when a package opts into isolated participants."""
        with self._operation():
            snapshot = self._store.display_snapshot(campaign_id)
            declared = self._decision_scope_declaration(snapshot)
            if declared is None or "isolated_participant" not in declared:
                return None
            primary = next(entry for entry in snapshot.package_lock.entries
                           if entry.role is PackageRole.PRIMARY)
            return owned_payload({"kind": "gm", "campaign_id": campaign_id,
                                  "package_id": primary.package_id,
                                  "package_version": primary.version,
                                  "package_sha256": primary.sha256})

    def actor_decision_request(
        self, campaign_id: str, *, turn_id: str,
    ) -> ActorDecisionRequest | None:
        """Read one opted-in actor's observation and legal offers from an authority revision."""
        with self._operation():
            snapshot = self._store.display_snapshot(campaign_id)
            declared = self._decision_scope_declaration(snapshot)
            if declared is None or "isolated_participant" not in declared:
                return None
            if (snapshot.lifecycle is not LifecycleState.ACTIVE
                    or snapshot.writer is not WriterRole.DM
                    or snapshot.turns.get(turn_id) is not TurnStatus.OPEN):
                raise CommandRejected("Isolated participant turn is unavailable")
            boundary = project_model_decision_scope(snapshot, declared)
            primary = next(entry for entry in snapshot.package_lock.entries
                           if entry.role is PackageRole.PRIMARY)
            schemas = {tool.name: tool.input_schema for tool in self._catalog(campaign_id)
                       if tool.package_id == primary.package_id
                       and tool.package_sha256 == primary.sha256}
            return project_actor_decision(
                snapshot, boundary, declared["isolated_participant"], turn_id=turn_id,
                tool_schemas=schemas,
            )

    def actor_decision_command(
        self, request: ActorDecisionRequest, *, choice_id: str, session_id: str, owner_id: str,
        provider_id: str | None = None, model_id: str | None = None,
    ) -> ActionCommand:
        """Bind an offered identity to the current actor; never accept model-supplied arguments."""
        with self._operation():
            current = self.actor_decision_request(request.campaign_id, turn_id=request.turn_id)
            if current is None or current != request:
                raise StaleRevision("Isolated participant decision is stale")
            session = self._store.session_activity_basis(session_id).session
            if (session.campaign_id != request.campaign_id or session.role is not WriterRole.DM
                    or session.status.value != "active"
                    or (provider_id is not None and session.provider_id != provider_id)
                    or (model_id is not None and session.model_id != model_id)):
                raise CommandRejected("Isolated participant session changed")
            choice = next(
                (action for action in request.actions if action.identity == choice_id), None,
            )
            if choice is None:
                raise CommandRejected("Isolated participant choice is not offered")
            identity = "actor-action-" + request.decision_id
            return ActionCommand(
                campaign_id=request.campaign_id, action_id=identity, idempotency_key=identity,
                turn_id=request.turn_id, owner_id=owner_id, writer=WriterRole.DM,
                expected_revision=request.revision, package_id=request.package_id,
                tool_name=choice.operation, parameters=choice.arguments,
                initiating_session_id=session_id,
            )

    def campaign_index(self) -> tuple[CampaignIndexEntry, ...]:
        """Project the coherent store index through the normal owner lifecycle."""

        with self._operation():
            return tuple(
                self._campaign_index_entry(record) for record in self._store.campaign_index()
            )

    @staticmethod
    def _campaign_index_entry(record: CampaignIndexRecord) -> CampaignIndexEntry:
        campaign = record.campaign
        package_count = 0 if record.package_lock is None else len(record.package_lock.entries)
        package_labels = dict(record.package_labels)
        ruleset_id: str | None = None
        ruleset_label = "No rules"
        campaign_setting_id: str | None = None
        campaign_setting_label = "No campaign setting"
        turn_id: str | None = None
        turn_status: TurnStatus | None = None
        session_id: str | None = None
        session_status: SessionStatus | None = None
        provider_id: str | None = None
        model_id: str | None = None
        open_turns = tuple(
            (candidate_turn_id, candidate_status)
            for candidate_turn_id, candidate_status in record.turns
            if candidate_status is TurnStatus.OPEN
        )
        supported = (
            campaign.lifecycle is LifecycleState.ACTIVE
            and campaign.writer is WriterRole.DM
            and record.package_lock is not None
            and len(open_turns) == 1
        )
        if len(open_turns) == 1:
            turn_id, turn_status = open_turns[0]
        elif record.turns:
            turn_id, turn_status = record.turns[-1]
        writer_sessions = tuple(
            session for session in record.top_level_sessions if session.role is campaign.writer
        )
        if len(writer_sessions) == 1:
            session = writer_sessions[0]
            session_id = session.session_id
            session_status = session.status
            provider_id = session.provider_id
            model_id = session.model_id
            supported = supported and (
                session.campaign_id == campaign.campaign_id
                and session.role is WriterRole.DM
                and bool(session.provider_id)
                and bool(session.model_id)
                and session.status is SessionStatus.ACTIVE
                and session.parent_session_id is None
                and session.selected_skill is None
            )
        if record.package_lock is None:
            ruleset_summary = "Not selected"
            package_summary = "Draft Campaign"
            ruleset_label = "Not selected"
            campaign_setting_label = "Not selected"
        elif package_count == 0:
            ruleset_summary = "No rules"
            package_summary = "No packages"
        else:
            ruleset_summary = "Package-defined rules"
            package_summary = f"{package_count} packages"
            primary = next(
                (
                    entry
                    for entry in record.package_lock.entries
                    if entry.role is PackageRole.PRIMARY
                ),
                None,
            )
            if primary is not None:
                ruleset_id = primary.package_id
                ruleset_label = package_labels.get(primary.package_id, primary.package_id)
                setting = next(iter(campaign_setting_entries(record.package_lock)), None)
                if setting is not None:
                    campaign_setting_id = setting.package_id
                    campaign_setting_label = package_labels.get(
                        setting.package_id, setting.package_id
                    )
        sessions = tuple(
            CampaignSessionIndexEntry(
                session_id=session.session_id,
                role=session.role.value,
                status=session.status.value,
                provider_id=session.provider_id,
                model_id=session.model_id,
                selected_skill=session.selected_skill,
                model_guidance=next(
                    (
                        handoff.model_guidance
                        for handoff in record.handoffs
                        if handoff.dm_session_id == session.session_id
                    ),
                    None,
                ),
            )
            for session in record.top_level_sessions
        )
        return CampaignIndexEntry(
            campaign_id=campaign.campaign_id,
            display_name=(
                f"{record.interview_name} · Interview"
                if record.interview_name else campaign.display_name
            ),
            lifecycle=campaign.lifecycle.value,
            writer=campaign.writer.value,
            revision=campaign.revision,
            ruleset_summary=ruleset_summary,
            package_summary=package_summary,
            package_count=package_count,
            playable=supported,
            availability=(
                "available"
                if supported
                else "draft"
                if campaign.lifecycle is LifecycleState.ACTIVE
                and campaign.writer is WriterRole.BUILDER
                else "unavailable"
            ),
            ruleset_id=ruleset_id,
            ruleset_label=ruleset_label,
            campaign_setting_id=campaign_setting_id,
            campaign_setting_label=campaign_setting_label,
            session_activity_ordinal=record.session_activity_ordinal,
            turn_id=turn_id,
            turn_status=None if turn_status is None else turn_status.value,
            session_id=session_id,
            session_status=None if session_status is None else session_status.value,
            provider_id=provider_id,
            model_id=model_id,
            sessions=sessions,
        )

    def campaign_lifecycle(self, campaign_id: str) -> CampaignSnapshot:
        """Inspect the current durable reversible lifecycle state."""

        with self._operation():
            return self._store.campaign_lifecycle(campaign_id)

    def backup_database(self, target: Path) -> None:
        """Delegate one coherent online database backup through the sole owner."""

        with self._operation():
            self._store.backup_database(target)

    @property
    def export_campaign(self) -> Callable[..., CampaignExportResult]:
        """Return the bound W11 export callable without touching store or schema state."""

        return self._export_campaign

    def _export_campaign(
        self,
        operation_id: str,
        campaign_id: str,
        target: Path,
        *,
        include_thinking: bool = False,
    ) -> CampaignExportResult:
        """Publish one self-contained outbound W11 campaign archive."""

        validate_authority_identity(operation_id, "Operation id")
        validate_authority_identity(campaign_id, "Campaign id")
        with self._operation():
            return self._store.export_campaign(
                campaign_id, target, include_thinking=include_thinking,
            )

    def recover_campaign(
        self,
        *,
        recovery_id: str,
        source: Path,
    ) -> CampaignRecoveryResult:
        """Recover one sealed portable campaign through the sole authority writer."""

        validate_authority_identity(recovery_id, "Recovery identity")
        with self._operation(), campaign_archive.stage_campaign_archive(source) as staged:
            replay = self._store.campaign_recovery_hit(
                recovery_id=recovery_id,
                staged=staged,
            )
            if replay is not None:
                return replay
            with self._lane(staged.campaign_id):
                return self._store.recover_campaign(
                    recovery_id=recovery_id,
                    staged=staged,
                )

    def rename_campaign(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        display_name: str,
        expected_revision: int,
    ) -> CampaignLifecycleResult:
        return self._lifecycle_operation(
            operation_id=operation_id,
            operation_kind="rename_campaign",
            campaign_id=campaign_id,
            expected_revision=expected_revision,
            display_name=display_name,
        )

    def archive_campaign(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        expected_revision: int,
        designated_session_id: str | None = None,
    ) -> CampaignLifecycleResult:
        return self._lifecycle_operation(
            operation_id=operation_id,
            operation_kind="archive_campaign",
            campaign_id=campaign_id,
            expected_revision=expected_revision,
            designated_session_id=designated_session_id,
        )

    def restore_archived_campaign(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        expected_revision: int,
        session_id: str | None = None,
    ) -> CampaignLifecycleResult:
        return self._lifecycle_operation(
            operation_id=operation_id,
            operation_kind="restore_campaign",
            campaign_id=campaign_id,
            expected_revision=expected_revision,
            designated_session_id=session_id,
        )

    def restore_trashed_campaign(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        expected_revision: int,
    ) -> CampaignLifecycleResult:
        return self._lifecycle_operation(
            operation_id=operation_id,
            operation_kind="restore_campaign",
            campaign_id=campaign_id,
            expected_revision=expected_revision,
        )

    def trash_campaign(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        expected_revision: int,
        designated_session_id: str | None = None,
    ) -> CampaignLifecycleResult:
        return self._lifecycle_operation(
            operation_id=operation_id,
            operation_kind="trash_campaign",
            campaign_id=campaign_id,
            expected_revision=expected_revision,
            designated_session_id=designated_session_id,
        )

    def purge_campaign(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        expected_revision: int,
        confirmation: str,
        prepare_external_purge: Callable[[], None] | None = None,
        rollback_external_purge: Callable[[], None] | None = None,
    ) -> CampaignPurgeResult:
        """Permanently delete one tombstoned campaign after exact confirmation."""

        with self._operation():
            try:
                validate_authority_identity(operation_id, "Campaign operation identity")
                validate_authority_identity(campaign_id, "Campaign identity")
                validate_authority_identity(confirmation, "Campaign purge confirmation")
                if (
                    not isinstance(expected_revision, int)
                    or isinstance(expected_revision, bool)
                    or expected_revision < 0
                ):
                    raise ValueError("Expected revision must be a nonnegative integer")
            except (TypeError, ValueError) as error:
                raise CommandRejected("Campaign purge identity is invalid") from error
            semantic_payload = _campaign_operation_payload(
                operation_id=operation_id,
                operation_kind="purge_campaign",
                values={"campaign_id": campaign_id, "confirmation": confirmation},
            )
            payload_sha256 = hashlib.sha256(canonical_json_bytes(semantic_payload)).hexdigest()
            existing = self._store.campaign_operation_hit(
                operation_id=operation_id,
                operation_kind="purge_campaign",
                payload_sha256=payload_sha256,
            )
            if existing is not None:
                if not isinstance(existing, CampaignPurgeResult):
                    raise IdempotencyConflict("Campaign operation result kind does not match")
                return existing
            if confirmation != f"permanently purge {campaign_id}":
                raise CommandRejected("Campaign purge confirmation is not exact")
            with self._lane(campaign_id):
                existing = self._store.campaign_operation_hit(
                    operation_id=operation_id,
                    operation_kind="purge_campaign",
                    payload_sha256=payload_sha256,
                )
                if existing is not None:
                    if not isinstance(existing, CampaignPurgeResult):
                        raise IdempotencyConflict("Campaign operation result kind does not match")
                    return existing
                self._reject_pending_mechanics_choice(campaign_id)
                self._reserve_campaign_transition(campaign_id)
                external_prepared = False
                try:
                    self._store.preflight_campaign_purge(
                        operation_id=operation_id,
                        semantic_payload=semantic_payload,
                        campaign_id=campaign_id,
                        expected_revision=expected_revision,
                        confirmation=confirmation,
                    )
                    if prepare_external_purge is not None:
                        prepare_external_purge()
                        external_prepared = True
                    return self._store.commit_campaign_purge(
                        operation_id=operation_id,
                        semantic_payload=semantic_payload,
                        payload_sha256=payload_sha256,
                        campaign_id=campaign_id,
                        expected_revision=expected_revision,
                        confirmation=confirmation,
                    )
                except Exception as error:
                    outcome_uncertain = (
                        getattr(error, "code", None) == "campaign_purge_commit_outcome_uncertain"
                    )
                    if (
                        external_prepared
                        and not outcome_uncertain
                        and rollback_external_purge is not None
                    ):
                        rollback_external_purge()
                    raise
                finally:
                    self._release_campaign_transition(campaign_id)

    def campaign_purge_receipt_exists(self, operation_id: str, campaign_id: str) -> bool:
        """Return whether the exact operation durably committed this campaign purge."""

        with self._operation():
            try:
                validate_authority_identity(operation_id, "Campaign operation identity")
                validate_authority_identity(campaign_id, "Campaign identity")
            except (TypeError, ValueError) as error:
                raise CommandRejected("Campaign purge identity is invalid") from error
            semantic_payload = _campaign_operation_payload(
                operation_id=operation_id,
                operation_kind="purge_campaign",
                values={
                    "campaign_id": campaign_id,
                    "confirmation": f"permanently purge {campaign_id}",
                },
            )
            payload_sha256 = hashlib.sha256(canonical_json_bytes(semantic_payload)).hexdigest()
            existing = self._store.campaign_operation_hit(
                operation_id=operation_id,
                operation_kind="purge_campaign",
                payload_sha256=payload_sha256,
            )
            if existing is None:
                return False
            if not isinstance(existing, CampaignPurgeResult):
                raise IdempotencyConflict("Campaign operation result kind does not match")
            return True

    def _lifecycle_operation(
        self,
        *,
        operation_id: str,
        operation_kind: str,
        campaign_id: str,
        expected_revision: int,
        display_name: str | None = None,
        designated_session_id: str | None = None,
    ) -> CampaignLifecycleResult:
        with self._operation():
            self._reject_pending_mechanics_choice(campaign_id)
            try:
                validate_authority_identity(operation_id, "Campaign operation identity")
                validate_authority_identity(campaign_id, "Campaign identity")
                if display_name is not None:
                    validate_authority_identity(display_name, "Campaign display name")
                if designated_session_id is not None:
                    validate_authority_identity(
                        designated_session_id, "Designated campaign session identity"
                    )
                if (
                    not isinstance(expected_revision, int)
                    or isinstance(expected_revision, bool)
                    or expected_revision < 0
                ):
                    raise ValueError("Expected revision must be a nonnegative integer")
            except (TypeError, ValueError) as error:
                raise CommandRejected("Campaign lifecycle identity is invalid") from error
            values: dict[str, object] = {"campaign_id": campaign_id}
            if display_name is not None:
                values["display_name"] = display_name
            if operation_kind in {"archive_campaign", "restore_campaign"}:
                values["designated_session_id"] = designated_session_id
            semantic_payload = _campaign_operation_payload(
                operation_id=operation_id,
                operation_kind=operation_kind,
                values=values,
            )
            payload_sha256 = hashlib.sha256(canonical_json_bytes(semantic_payload)).hexdigest()
            existing = self._store.campaign_operation_hit(
                operation_id=operation_id,
                operation_kind=operation_kind,
                payload_sha256=payload_sha256,
            )
            if existing is not None:
                if not isinstance(existing, CampaignLifecycleResult):
                    raise IdempotencyConflict("Campaign operation result kind does not match")
                return existing
            with self._lane(campaign_id):
                existing = self._store.campaign_operation_hit(
                    operation_id=operation_id,
                    operation_kind=operation_kind,
                    payload_sha256=payload_sha256,
                )
                if existing is not None:
                    if not isinstance(existing, CampaignLifecycleResult):
                        raise IdempotencyConflict("Campaign operation result kind does not match")
                    return existing
                self._reserve_campaign_transition(campaign_id)
                try:
                    return self._store.commit_lifecycle_operation(
                        operation_id=operation_id,
                        operation_kind=operation_kind,
                        semantic_payload=semantic_payload,
                        payload_sha256=payload_sha256,
                        campaign_id=campaign_id,
                        expected_revision=expected_revision,
                        display_name=display_name,
                        designated_session_id=designated_session_id,
                    )
                finally:
                    self._release_campaign_transition(campaign_id)

    def _reject_pending_mechanics_choice(self, campaign_id: str) -> None:
        if self._store.pending_mechanics_action(campaign_id) is not None:
            raise CommandRejected(
                "Campaign is awaiting a mechanics choice",
                code="mechanics_choice_pending",
            )

    @property
    def owner_session_id(self) -> str:
        return self._owner_session_id

    def start_narration_attempt(
        self,
        *,
        campaign_id: str,
        attempt_id: str,
        turn_id: str,
        cancel: Callable[[], None],
        last_action_id: str | None = None,
        interruption_id: str | None = None,
    ) -> None:
        with self._operation():
            self._begin_campaign_work(campaign_id)
            try:
                self._start_narration_attempt(
                    campaign_id=campaign_id,
                    attempt_id=attempt_id,
                    turn_id=turn_id,
                    cancel=cancel,
                    last_action_id=last_action_id,
                    interruption_id=interruption_id,
                    work_registered=True,
                )
            except BaseException:
                self._finish_campaign_work(campaign_id)
                raise

    def _start_narration_attempt(
        self,
        *,
        campaign_id: str,
        attempt_id: str,
        turn_id: str,
        cancel: Callable[[], None],
        last_action_id: str | None = None,
        interruption_id: str | None = None,
        failure_interruption_id: str | None = None,
        work_registered: bool = False,
    ) -> None:
        if self._disconnect_grace_seconds is None:
            raise CommandRejected("Narration disconnect grace was not injected")
        for value, label in (
            (campaign_id, "Campaign identity"),
            (attempt_id, "Narration attempt identity"),
            (turn_id, "TURN identity"),
        ):
            validate_authority_identity(value, label)
        if not callable(cancel):
            raise TypeError("Narration cancellation handle must be callable")
        snapshot = self._store.snapshot(campaign_id)
        if turn_id not in snapshot.turns:
            raise CommandRejected("TURN is unavailable")
        if last_action_id is not None:
            receipt = self._store.action_receipt(campaign_id, last_action_id)
            action = next(item for item in snapshot.actions if item.action_id == receipt.action_id)
            if action.turn_id != turn_id:
                raise CommandRejected("Stored action result belongs to another TURN")
        key = (campaign_id, attempt_id)
        with self._narration_lock:
            previous_attempt = self._attempts.get(key)
            if previous_attempt is not None and (
                not previous_attempt.terminal or interruption_id is None
            ):
                raise IdempotencyConflict("Narration attempt identity already exists")
            if any(
                item.campaign_id == campaign_id and not item.terminal
                for item in self._attempts.values()
            ):
                raise CommandRejected("Campaign already has live narration")
            existing_ordinals = tuple(
                event.attempt_ordinal for event in snapshot.events if event.attempt_id == attempt_id
            )
            if existing_ordinals != tuple(range(len(existing_ordinals))):
                raise CommandRejected("Stored narration attempt chronology is invalid")
            if interruption_id is None and existing_ordinals:
                raise IdempotencyConflict("Narration attempt already has canonical output")
            next_ordinal = 0 if interruption_id is not None else len(existing_ordinals)
            self._attempts[key] = _NarrationAttempt(
                campaign_id=campaign_id,
                attempt_id=attempt_id,
                turn_id=turn_id,
                cancel=cancel,
                last_action_id=last_action_id,
                ordinal=next_ordinal,
                interruption_id=failure_interruption_id,
                retry_basis_id=interruption_id,
                work_registered=work_registered,
            )

    def emit_narration_token(
        self,
        campaign_id: str,
        attempt_id: str,
        channel: ProseChannel,
        token: str,
        *,
        response_blocks: Sequence[SafePayload] | None = None,
    ) -> None:
        with self._operation():
            if not isinstance(channel, ProseChannel):
                raise CommandRejected("Narration channel is not player-safe")
            if not isinstance(token, str):
                raise TypeError("Narration token must be text")
            key = (campaign_id, attempt_id)
            with self._narration_lock:
                attempt = self._live_attempt(key)
                if attempt.disconnect_deadline is not None:
                    raise CommandRejected("Disconnected narration cannot accept tokens")
                if attempt.channel is not None and attempt.channel is not channel:
                    self._flush_attempt_buffer(attempt)
                if attempt.response_blocks is not None and response_blocks is None:
                    raise CommandRejected("Completed response blocks cannot accept more text")
                if response_blocks is not None:
                    if channel is not ProseChannel.TALK:
                        raise CommandRejected("Response blocks are valid only for TALK")
                    try:
                        admitted_blocks = validate_response_blocks(response_blocks)
                    except ValueError as error:
                        raise CommandRejected("Response blocks are not player-safe") from error
                    if response_blocks_text(admitted_blocks) != token:
                        raise CommandRejected("Response block fallback text does not match")
                    if attempt.buffer or attempt.response_blocks is not None:
                        raise CommandRejected("Completed response blocks cannot be streamed twice")
                    attempt.response_blocks = admitted_blocks
                attempt.channel = channel
                attempt.buffer += token

    def flush_narration(self, campaign_id: str, attempt_id: str) -> None:
        """Commit the current safe prose buffer before a chronological tool call."""

        with self._operation(), self._narration_lock:
            attempt = self._live_attempt((campaign_id, attempt_id))
            if attempt.disconnect_deadline is not None:
                raise CommandRejected("Disconnected narration cannot flush")
            if attempt.buffer:
                self._flush_attempt_buffer(attempt)

    def bind_narration_action(self, campaign_id: str, attempt_id: str, action_id: str) -> None:
        """Associate the latest committed action result with an active F2 attempt."""

        with self._operation(), self._narration_lock:
            attempt = self._live_attempt((campaign_id, attempt_id))
            receipt = self._store.action_receipt(campaign_id, action_id)
            snapshot = self._store.snapshot(campaign_id)
            action = next(item for item in snapshot.actions if item.action_id == receipt.action_id)
            if action.turn_id != attempt.turn_id:
                raise CommandRejected("Stored action result belongs to another TURN")
            attempt.last_action_id = action_id

    def complete_narration_attempt(
        self, campaign_id: str, attempt_id: str
    ) -> tuple[CanonicalEvent, ...]:
        with self._operation():
            return self._complete_narration_attempt(campaign_id, attempt_id)

    def _complete_narration_attempt(
        self, campaign_id: str, attempt_id: str
    ) -> tuple[CanonicalEvent, ...]:
        key = (campaign_id, attempt_id)
        with self._narration_lock:
            attempt = self._live_attempt(key)
            if attempt.disconnect_deadline is not None:
                raise CommandRejected("Disconnected narration cannot complete")
            if attempt.retry_basis_id is None:
                self._flush_attempt_buffer(attempt)
            else:
                if attempt.channel is ProseChannel.TALK and attempt.buffer:
                    self._store.append_retry_resolution(
                        campaign_id=campaign_id,
                        interruption_id=attempt.retry_basis_id,
                        retry_id=attempt_id,
                        turn_id=attempt.turn_id,
                        ordinal=attempt.ordinal,
                        text=attempt.buffer,
                        response_blocks=attempt.response_blocks,
                    )
                    attempt.ordinal += 1
                    attempt.buffer = ""
                elif attempt.last_action_id is not None:
                    snapshot = self._store.snapshot(campaign_id)
                    terminal_events = tuple(
                        event
                        for event in snapshot.events
                        if event.action_id == attempt.last_action_id
                    )
                    if not terminal_events:
                        raise CommandRejected("Retry action result is unavailable")
                    self._store.resolve_interruption(
                        campaign_id=campaign_id,
                        interruption_id=attempt.retry_basis_id,
                        retry_id=attempt_id,
                        resolved_sequence=terminal_events[-1].sequence,
                    )
                else:
                    raise CommandRejected(
                        "A successful Retry must produce TALK or an action result"
                    )
            attempt.terminal = True
            if attempt.work_registered:
                attempt.work_registered = False
                self._finish_campaign_work(campaign_id)
            snapshot = self._store.snapshot(campaign_id)
            return tuple(event for event in snapshot.events if event.attempt_id == attempt_id)

    def disconnect_narration(self, campaign_id: str, attempt_id: str, *, now: float) -> float:
        with self._operation():
            if not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now):
                raise ValueError("Disconnect time must be finite")
            if self._disconnect_grace_seconds is None:
                raise CommandRejected("Narration disconnect grace was not injected")
            with self._narration_lock:
                attempt = self._live_attempt((campaign_id, attempt_id))
                if attempt.disconnect_deadline is None:
                    attempt.disconnect_deadline = float(now) + self._disconnect_grace_seconds
                return attempt.disconnect_deadline

    def reconnect_narration(
        self,
        campaign_id: str,
        attempt_id: str,
        *,
        now: float,
        acknowledged_sequence: int,
    ) -> CanonicalReplay:
        with self._operation():
            if not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now):
                raise ValueError("Reconnect time must be finite")
            with self._narration_lock:
                attempt = self._live_attempt((campaign_id, attempt_id))
                deadline = attempt.disconnect_deadline
                if deadline is not None:
                    if not float(now) < deadline:
                        self._expire_narration_locked(attempt)
                        raise CommandRejected("Narration reconnect deadline elapsed")
                    attempt.disconnect_deadline = None
            return self._canonical_replay(campaign_id, acknowledged_sequence)

    def narration_attempt_status(self, campaign_id: str, attempt_id: str) -> str:
        """Project one in-memory narration attempt's transport lifecycle."""

        with self._operation(), self._narration_lock:
            attempt = self._attempt((campaign_id, attempt_id))
            if attempt.terminal:
                return "completed" if attempt.interruption_id is None else "interrupted"
            if attempt.disconnect_deadline is not None:
                return "disconnected"
            return "active"

    def expire_narration(
        self, campaign_id: str, attempt_id: str, *, now: float
    ) -> NarrationInterruption:
        with self._operation(), self._narration_lock:
            if not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now):
                raise ValueError("Expiry time must be finite")
            attempt = self._attempt((campaign_id, attempt_id))
            if attempt.terminal:
                if attempt.interruption_id is None:
                    raise CommandRejected("Narration attempt completed successfully")
                return self._store.interruption(campaign_id, attempt.interruption_id)
            if attempt.disconnect_deadline is None or now < attempt.disconnect_deadline:
                raise CommandRejected("Narration grace has not expired")
            return self._expire_narration_locked(attempt)

    def interrupt_narration(self, campaign_id: str, attempt_id: str) -> NarrationInterruption:
        """Interrupt one F2 attempt immediately; cancellation remains exact-once."""

        with self._operation(), self._narration_lock:
            attempt = self._attempt((campaign_id, attempt_id))
            if attempt.terminal:
                if attempt.interruption_id is None:
                    raise CommandRejected("Narration attempt completed successfully")
                return self._store.interruption(campaign_id, attempt.interruption_id)
            return self._expire_narration_locked(attempt)

    def _expire_narration_locked(self, attempt: _NarrationAttempt) -> NarrationInterruption:
        attempt.terminal = True
        if attempt.work_registered:
            attempt.work_registered = False
            self._finish_campaign_work(attempt.campaign_id)
        attempt.buffer = ""
        attempt.channel = None
        if attempt.interruption_id is None:
            attempt.interruption_id = f"interruption-{uuid.uuid4()}"
        from .action_sequences import last_attempt_commit
        queued_action = last_attempt_commit(self, attempt.campaign_id, attempt.attempt_id)
        if queued_action is not None:
            attempt.last_action_id = queued_action
        record = self._store.record_interruption(
            campaign_id=attempt.campaign_id,
            interruption_id=attempt.interruption_id,
            turn_id=attempt.turn_id,
            last_action_id=attempt.last_action_id,
            failed_attempt_id=attempt.attempt_id,
        )
        attempt.interruption_id = record.interruption_id
        if not attempt.cancelled:
            attempt.cancelled = True
            attempt.cancel()
        return record

    def saved_action_receipt(self, campaign_id: str, action_id: str) -> ActionReceipt:
        """Read a committed result for a redirected narration without executing it."""

        with self._operation():
            return self._store.action_receipt(campaign_id, action_id)

    def redirect_narration(
        self,
        *,
        session_id: str,
        message_id: str,
        content: SafePayload,
        interruption_id: str,
    ) -> NarrationInterruption:
        """Accept new player guidance for stopped work without discarding saved results."""

        with self._operation(), self._session_campaign_work(session_id):
            context = self._sessions.context(session_id)
            campaign_id = context.session.campaign_id
            if campaign_id is None or context.session.role is not WriterRole.DM:
                raise CommandRejected("Only a GM session can redirect campaign narration")
            interruption = self._store.interruption(campaign_id, interruption_id)
            if not self._can_redirect_interruption(interruption, context):
                raise CommandRejected("Only stopped narration accepts replacement input")
            if content.get("turn_id") != interruption.turn_id:
                raise CommandRejected("Replacement input belongs to another turn")
            attempt_id = content.get("attempt_id")
            if not isinstance(attempt_id, str):
                raise CommandRejected("Replacement input requires an attempt identity")
            self._store.redirect_interruption(
                session_id=session_id, message_id=message_id, content=content,
                interruption=interruption, attempt_id=attempt_id,
            )
            return interruption

    @staticmethod
    def _can_redirect_interruption(
        interruption: NarrationInterruption, context: SessionContext,
    ) -> bool:
        """Recognize saved cancellation or a replacement accepted before a restart."""

        return interruption.retryable and any(
            (
                message.content.get("kind") == "session_attempt_terminal"
                and message.content.get("attempt_id") == interruption.failed_attempt_id
                and message.content.get("failure_category") == "user_cancelled"
            ) or (
                message.kind is SessionMessageKind.USER
                and message.content.get("redirect_interruption_id") == interruption.interruption_id
                and message.content.get("attempt_id") == interruption.resolving_retry_id
            )
            for message in context.messages
        )

    def retryable_interruption(
        self, campaign_id: str, *, failed_attempt_id: str
    ) -> NarrationInterruption | None:
        """Project one durable retry basis without exposing the persistence handle."""

        with self._operation():
            try:
                validate_authority_identity(campaign_id, "Campaign identity")
                validate_authority_identity(failed_attempt_id, "Failed narration attempt identity")
            except (TypeError, ValueError) as error:
                raise CommandRejected("Narration retry identity is invalid") from error
            matches = tuple(
                interruption
                for interruption in self._store.interruptions(campaign_id)
                if interruption.failed_attempt_id == failed_attempt_id and interruption.retryable
            )
            if len(matches) > 1:
                raise CommandRejected("Narration retry chronology is ambiguous")
            return matches[0] if matches else None

    def narration_interruption(
        self,
        campaign_id: str,
        interruption_id: str,
    ) -> NarrationInterruption:
        """Return one durable safe retry basis by its exact identity."""

        with self._operation():
            return self._store.interruption(campaign_id, interruption_id)

    def canonical_replay(self, campaign_id: str, *, acknowledged_sequence: int) -> CanonicalReplay:
        with self._operation():
            return self._canonical_replay(campaign_id, acknowledged_sequence)

    def _canonical_replay(self, campaign_id: str, acknowledged_sequence: int) -> CanonicalReplay:
        if acknowledged_sequence < 0:
            raise ValueError("Acknowledged canonical sequence cannot be negative")
        snapshot = self._store.snapshot(campaign_id)
        snapshot = self._project_private_tool_inputs(snapshot)
        high_water = snapshot.events[-1].sequence if snapshot.events else 0
        return CanonicalReplay(
            canonical=tuple(
                event for event in snapshot.events if event.sequence > acknowledged_sequence
            ),
            provisional=self._provisional_projection(campaign_id),
            high_water=high_water,
        )

    def _interruption_saved_changes(
        self, snapshot: CampaignSnapshot, interruption: NarrationInterruption
    ) -> bool:
        """Join the interrupted input to saved records, excluding failed and prior actions."""

        action_ids = {interruption.last_action_id} if interruption.last_action_id else set()
        for context in self._store.campaign_session_contexts(snapshot.campaign_id):
            terminal = next(
                (
                    message for message in context.messages
                    if message.content.get("kind") == "session_attempt_terminal"
                    and message.content.get("attempt_id") == interruption.failed_attempt_id
                ),
                None,
            )
            basis = next(
                (
                    message for message in context.messages
                    if (
                        message.kind is SessionMessageKind.USER
                        or message.content.get("kind") in {
                            "campaign_start", "player_action_response",
                        }
                    )
                    and (
                        message.content.get("attempt_id") == interruption.failed_attempt_id
                        or (terminal is not None
                            and message.message_id == terminal.content.get("message_id"))
                    )
                ),
                None,
            )
            if basis is None:
                continue
            if any(
                basis.message_id in record.basis_message_ids
                for record in snapshot.continuity.records
            ):
                return True
            start_sequence = basis.content.get("canonical_start_sequence")
            next_input = next(
                (
                    message for message in context.messages
                    if message.sequence > basis.sequence
                    and (message.kind is SessionMessageKind.USER
                         or message.content.get("kind") == "player_action_response")
                ),
                None,
            )
            end_sequence = (
                next_input.content.get("canonical_start_sequence")
                if next_input is not None else None
            )
            if isinstance(start_sequence, int) and not isinstance(start_sequence, bool):
                action_ids.update(
                    event.action_id for event in snapshot.events
                    if event.sequence > start_sequence
                    and (not isinstance(end_sequence, int) or event.sequence <= end_sequence)
                    and event.turn_id == interruption.turn_id
                    and event.action_id is not None
                )
            # TURN identities can span several player inputs. Only this input's evidence and
            # canonical references qualify, including automatic engine transitions and retries.
            stop_sequence = next(
                (
                    message.sequence for message in context.messages
                    if message.sequence > basis.sequence
                    and (message.kind is SessionMessageKind.USER
                         or message.content.get("kind") == "player_action_response")
                ),
                float("inf"),
            )
            sequences = {
                message.canonical_sequence for message in context.messages
                if basis.sequence < message.sequence < stop_sequence
                and message.canonical_sequence is not None
            }
            action_ids.update(
                event.action_id for event in snapshot.events
                if event.sequence in sequences and event.action_id is not None
            )
            action_ids.update(
                str(message.content["action_id"]) for message in context.messages
                if message.content.get("kind") == "provider_mechanics_action_contract"
                and message.content.get("basis_message_id") == basis.message_id
                and isinstance(message.content.get("action_id"), str)
            )
        return any(
            action.action_id in action_ids and action.status is ActionStatus.COMMITTED
            for action in snapshot.actions
        )

    def player_projection(self, campaign_id: str) -> tuple[PlayerProjection, ...]:
        with self._operation():
            return self._project_player_snapshot(self._store.snapshot(campaign_id))

    def player_projection_page(
        self, campaign_id: str, *, after_sequence: int | None = None,
    ) -> tuple[PlayerProjection, ...]:
        """Enrich visible chronology and live controls without loading older history."""
        if after_sequence is not None and (type(after_sequence) is not int or after_sequence < 0):
            raise CommandRejected("Player history sequence is invalid")
        with self._operation():
            return self._project_player_snapshot(self._store.display_snapshot(
                campaign_id, event_after=after_sequence,
            ))

    def display_snapshot(self, campaign_id: str) -> CampaignDisplaySnapshot:
        """Read current display state without historical action and event payloads."""
        with self._operation():
            return self._store.display_snapshot(campaign_id)

    def _project_player_snapshot(
        self, snapshot: CampaignSnapshot | CampaignDisplaySnapshot,
    ) -> tuple[PlayerProjection, ...]:
        snapshot = self._project_private_tool_inputs(snapshot)
        campaign_id = snapshot.campaign_id
        with self._work_condition:
            controls_idle = not self._campaign_work_counts.get(campaign_id, 0)
        controls_available = (
            controls_idle and snapshot.writer is WriterRole.DM
            and self._store.current_open_turn(campaign_id) is not None
            and self._store.pending_mechanics_action(campaign_id) is None
        )
        tools_by_name = {
            (tool.package_id, tool.name): tool for tool in self._catalog(campaign_id)
        } if controls_available else {}
        actions_by_id = {action.action_id: action for action in snapshot.actions}
        control_scope = self._player_control_scope(snapshot)
        projected: list[PlayerProjection] = []
        tool_calls_by_action: dict[str, SafePayload] = {}
        bookkeeping_tools: dict[tuple[str, str, str, str], bool] = {}
        for event in snapshot.events:
            if event.event_type is CanonicalEventType.TOOL_CALL and event.action_id is not None:
                tool_calls_by_action[event.action_id] = event.safe_payload
                continue
            if event.event_type is CanonicalEventType.THINK:
                projected.append(
                    PlayerProjection(
                        kind=PlayerProjectionKind.THINK,
                        sequence=event.sequence,
                        text=str(event.safe_payload["text"]),
                        safe_payload=event.safe_payload,
                        collapsed_by_default=True,
                        attempt_id=event.attempt_id,
                    )
                )
            elif event.event_type is CanonicalEventType.TALK:
                projected.append(
                    PlayerProjection(
                        kind=PlayerProjectionKind.TALK,
                        sequence=event.sequence,
                        text=str(event.safe_payload["text"]),
                        safe_payload=event.safe_payload,
                        attempt_id=event.attempt_id,
                    )
                )
            elif event.event_type is CanonicalEventType.TOOL_RESULT:
                safe_payload = dict(event.safe_payload)
                call = tool_calls_by_action.get(event.action_id or "")
                if call is not None:
                    tool_name = call.get("tool_name")
                    parameters = call.get("parameters")
                    if isinstance(tool_name, str) and tool_name:
                        safe_payload["tool_name"] = tool_name
                        # Retained package declarations identify internal bookkeeping receipts;
                        # the model's enclosing campaign write already owns their activity entry.
                        action = actions_by_id.get(event.action_id or "")
                        if action is not None:
                            identity = (
                                action.package_id, action.package_version,
                                action.package_sha256, action.tool_name,
                            )
                            if identity not in bookkeeping_tools:
                                declaration = self._store.declared_package_tool(*identity)
                                bookkeeping_tools[identity] = declaration is not None and (
                                    "x-sylvia-gm-character-update" in declaration.input_schema
                                )
                            if bookkeeping_tools[identity]:
                                safe_payload["routine_activity"] = "character_update"
                    if isinstance(parameters, Mapping):
                        safe_payload["parameters"] = dict(parameters)
                card_controls = self._card_control_readiness(
                    snapshot, actions_by_id.get(event.action_id), tools_by_name, control_scope,
                )
                projected.append(
                    PlayerProjection(
                        kind=PlayerProjectionKind.TOOL_CARD,
                        sequence=event.sequence,
                        safe_payload=owned_payload({
                            **safe_payload,
                            "action_id": event.action_id,
                            "result_card_action_availability": [
                                control["available"] for control in card_controls
                            ],
                            "result_card_action_reasons": [
                                control["reason"] for control in card_controls
                            ],
                        }),
                        collapsed_by_default=True,
                        action_id=event.action_id,
                    )
                )
        pending_choice = self._store.pending_mechanics_action(campaign_id)
        if pending_choice is not None:
            projected.append(
                PlayerProjection(
                    kind=PlayerProjectionKind.MECHANICS_CHOICE,
                    sequence=None,
                    safe_payload=owned_payload(
                        {
                            "action_id": pending_choice.action_id,
                            "basis_revision": pending_choice.basis_revision,
                            "turn_id": pending_choice.turn_id,
                            "choice_id": pending_choice.choice.choice_id,
                            **({"choice_sequence": len(pending_choice.prior_answers)}
                               if pending_choice.prior_answers else {}),
                            "prompt": pending_choice.choice.prompt,
                            "options": [
                                {
                                    "option_id": option.option_id,
                                    "label": option.label,
                                }
                                for option in pending_choice.choice.options
                            ],
                        }
                    ),
                    collapsed_by_default=False,
                    provisional=False,
                    action_id=pending_choice.action_id,
                )
            )
        projected.extend(self._provisional_projection(campaign_id))
        active_retry_ids = {
            attempt.attempt_id
            for attempt in self._attempts.values()
            if attempt.campaign_id == campaign_id and not attempt.terminal
        }
        recovery_snapshot = snapshot if isinstance(snapshot, CampaignSnapshot) else None
        for interruption in self._store.interruptions(campaign_id):
            if (
                interruption.retryable
                and interruption.resolving_retry_id not in active_retry_ids
            ):
                if recovery_snapshot is None:
                    recovery_snapshot = self._store.snapshot(campaign_id)
                projected.append(
                    PlayerProjection(
                        kind=PlayerProjectionKind.RETRY,
                        sequence=None,
                        text="Narration interrupted—Retry",
                        safe_payload=owned_payload(
                            {
                                "interruption_id": interruption.interruption_id,
                                "failed_attempt_id": interruption.failed_attempt_id,
                                "resolving_retry_id": interruption.resolving_retry_id,
                                "can_redirect": any(
                                    self._can_redirect_interruption(interruption, context)
                                    for context in self._store.campaign_session_contexts(
                                        campaign_id,
                                    )
                                ),
                                "saved_changes": self._interruption_saved_changes(
                                recovery_snapshot, interruption,
                                ),
                            }
                        ),
                        collapsed_by_default=False,
                        attempt_id=interruption.failed_attempt_id,
                    )
                )
        return tuple(projected)

    def _provisional_projection(self, campaign_id: str) -> tuple[PlayerProjection, ...]:
        with self._narration_lock:
            pending = tuple(
                item
                for (item_campaign_id, _), item in self._pending_tools.items()
                if item_campaign_id == campaign_id
            )
            prose = tuple(
                PlayerProjection(
                    kind=(
                        PlayerProjectionKind.THINK
                        if attempt.channel is ProseChannel.SAFE_THINK
                        else PlayerProjectionKind.TALK
                    ),
                    sequence=None,
                    text=attempt.buffer,
                    safe_payload=owned_payload({"text": attempt.buffer}),
                    collapsed_by_default=attempt.channel is ProseChannel.SAFE_THINK,
                    provisional=True,
                    attempt_id=attempt.attempt_id,
                )
                for attempt in self._attempts.values()
                if attempt.campaign_id == campaign_id
                and not attempt.terminal
                and attempt.buffer
                and attempt.channel is not None
            )
            return (*pending, *prose)

    def record_inspector_trace(
        self,
        *,
        owner_session_id: str,
        opted_in: bool,
        sink: InspectorTraceSink,
        payload: object,
    ) -> None:
        with self._operation():
            if (
                opted_in is not True
                or owner_session_id != self._owner_session_id
                or sink is not InspectorTraceSink.INSPECTOR
            ):
                raise CommandRejected("Private trace sink admission denied")
            with self._narration_lock:
                self._inspector_trace.append(payload)

    def inspector_traces(self, owner_session_id: str) -> tuple[object, ...]:
        with self._operation():
            if owner_session_id != self._owner_session_id:
                raise CommandRejected("Inspector owner session does not match")
            with self._narration_lock:
                return tuple(self._inspector_trace)

    def set_admin_full_audit(self, enabled: bool) -> SafePayload:
        """Enable current-process raw provider tracing after an explicit local admin action."""

        if not isinstance(enabled, bool):
            raise CommandRejected("Admin Full Audit setting must be boolean")
        with self._operation(), self._narration_lock:
            if self._admin_full_audit_enabled != enabled:
                self._inspector_trace.clear()
            self._admin_full_audit_enabled = enabled
            return owned_payload({"enabled": enabled, "trace_count": len(self._inspector_trace)})

    def admin_full_audit_enabled(self) -> bool:
        """Return whether the current owner process accepts raw provider traces."""

        with self._operation(), self._narration_lock:
            return self._admin_full_audit_enabled

    def record_admin_full_audit(self, payload: object) -> None:
        """Capture one raw provider exchange only while the explicit admin mode is enabled."""

        with self._operation(), self._narration_lock:
            if self._admin_full_audit_enabled:
                self._inspector_trace.append(owned_json_value(payload))

    def record_mechanics_diagnostic(
        self,
        *,
        session_id: str | None,
        turn_id: str,
        action_id: str,
        tool_name: str,
        rejection_code: str,
        validation_field_paths: Sequence[str],
        state_changed: bool,
    ) -> None:
        """Capture bounded metadata-only mechanics evidence for local installed testing."""

        diagnostic = owned_payload(
            {
                "kind": "mechanics_rejection",
                "privacy": "safe_metadata_only",
                "session_id": session_id,
                "turn_id": turn_id,
                "action_id": action_id,
                "tool_name": tool_name,
                "rejection_code": rejection_code,
                "validation_field_paths": list(validation_field_paths),
                "state_changed": state_changed,
            }
        )
        with self._operation(), self._narration_lock:
            self._mechanics_diagnostics.append(diagnostic)
            del self._mechanics_diagnostics[:-200]

    def admin_full_audit_status(self) -> SafePayload:
        """Project bounded Full Audit status without transporting raw provider traces."""

        with self._operation(), self._narration_lock:
            return owned_payload(
                {
                    "enabled": self._admin_full_audit_enabled,
                    "trace_count": len(self._inspector_trace),
                    "safe_mechanics_diagnostics": [
                        thaw_json_value(item) for item in self._mechanics_diagnostics
                    ],
                }
            )

    def admin_full_audit_record(self, index: int) -> SafePayload:
        """Project one exact trace so the desktop bridge never carries the full session at once."""

        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise CommandRejected("Admin Full Audit record index must be nonnegative")
        with self._operation(), self._narration_lock:
            if index >= len(self._inspector_trace):
                raise CommandRejected("Admin Full Audit record is unavailable")
            return owned_payload(
                {"index": index, "trace": thaw_json_value(self._inspector_trace[index])}
            )

    def export_admin_full_audit(self, destination: Path) -> SafePayload:
        """Atomically export one immutable Audit snapshot without crossing the desktop pipe."""

        with self._operation(), self._narration_lock:
            enabled = self._admin_full_audit_enabled
            traces = tuple(self._inspector_trace)
            safe_diagnostics = tuple(self._mechanics_diagnostics)

        temporary_path: Path | None = None
        try:
            destination.parent.mkdir(parents=False, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(b'{\n  "enabled": ')
                temporary.write(b"true" if enabled else b"false")
                trace_header = f',\n  "trace_count": {len(traces)},\n  "traces": ['
                temporary.write(trace_header.encode())
                for index, trace in enumerate(traces):
                    temporary.write(b"\n" if index == 0 else b",\n")
                    encoded = json.dumps(
                        thaw_json_value(trace),
                        ensure_ascii=False,
                        allow_nan=False,
                        indent=2,
                    ).encode("utf-8")
                    temporary.write(b"    " + encoded.replace(b"\n", b"\n    "))
                if traces:
                    temporary.write(b"\n  ")
                temporary.write(b'],\n  "safe_mechanics_diagnostics": [')
                for index, diagnostic in enumerate(safe_diagnostics):
                    temporary.write(b"\n" if index == 0 else b",\n")
                    encoded = json.dumps(
                        thaw_json_value(diagnostic),
                        ensure_ascii=False,
                        allow_nan=False,
                        indent=2,
                    ).encode("utf-8")
                    temporary.write(b"    " + encoded.replace(b"\n", b"\n    "))
                if safe_diagnostics:
                    temporary.write(b"\n  ")
                temporary.write(b"]\n}\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            exported_bytes = temporary_path.stat().st_size
            os.replace(temporary_path, destination)
        except (OSError, TypeError, ValueError) as error:
            if temporary_path is not None:
                # Best-effort cleanup must not replace the actionable original save failure.
                with suppress(OSError):
                    temporary_path.unlink(missing_ok=True)
            if isinstance(error, (TypeError, ValueError)):
                raise CommandRejected("Audit export could not serialize exact records") from error
            raise CommandRejected(audit_export_save_error(error)) from error
        return owned_payload(
            {
                "status": "exported",
                "trace_count": len(traces),
                "bytes": exported_bytes,
            }
        )

    def admin_full_audit_projection(self) -> SafePayload:
        """Project current-process traces to the trusted local admin interface."""

        with self._operation(), self._narration_lock:
            return owned_payload(
                {
                    "enabled": self._admin_full_audit_enabled,
                    "trace_count": len(self._inspector_trace),
                    "traces": [thaw_json_value(item) for item in self._inspector_trace],
                    "safe_mechanics_diagnostics": [
                        thaw_json_value(item) for item in self._mechanics_diagnostics
                    ],
                }
            )

    def manual_retry(
        self,
        *,
        campaign_id: str,
        interruption_id: str,
        retry_id: str,
        narrator: Callable[[RetryNarrationInput, NarrationStream], None],
        cancel: Callable[[], None] = lambda: None,
        on_attempt_started: Callable[[], None] = lambda: None,
    ) -> ManualRetryResult:
        with self._operation(), self._campaign_work(campaign_id):
            reserved = self._store.reserve_retry(campaign_id, interruption_id, retry_id)
            snapshot = self._store.snapshot(campaign_id)
            action_receipt = (
                None
                if reserved.last_action_id is None
                else self._store.action_receipt(campaign_id, reserved.last_action_id)
            )
            high_water = snapshot.events[-1].sequence if snapshot.events else 0
            retry_input = RetryNarrationInput(
                campaign_id=campaign_id,
                interruption_id=interruption_id,
                retry_id=retry_id,
                failed_attempt_id=reserved.failed_attempt_id,
                canonical_high_water=high_water,
                turn_id=reserved.turn_id,
                action_receipt=action_receipt,
            )
            if not reserved.retryable:
                return ManualRetryResult(
                    input=retry_input,
                    canonical=tuple(
                        event for event in snapshot.events if event.attempt_id == retry_id
                    ),
                    replayed=True,
                )
            self._start_narration_attempt(
                campaign_id=campaign_id,
                attempt_id=retry_id,
                turn_id=reserved.turn_id,
                cancel=cancel,
                last_action_id=reserved.last_action_id,
                interruption_id=interruption_id,
                failure_interruption_id=(
                    interruption_id if reserved.failed_attempt_id == retry_id else None
                ),
            )
            stream = NarrationStream(
                lambda channel, token, **metadata: self.emit_narration_token(
                    campaign_id, retry_id, channel, token, **metadata
                )
            )
            try:
                on_attempt_started()
                narrator(retry_input, stream)
                canonical = self._complete_narration_attempt(campaign_id, retry_id)
            except BaseException:
                with self._narration_lock:
                    attempt = self._attempt((campaign_id, retry_id))
                    if not attempt.terminal:
                        self._expire_narration_locked(attempt)
                raise
            return ManualRetryResult(
                input=retry_input,
                canonical=tuple(canonical),
                replayed=False,
            )

    def _flush_attempt_buffer(self, attempt: _NarrationAttempt) -> None:
        if not attempt.buffer or attempt.channel is None:
            attempt.buffer = ""
            return
        self._store.append_narration_segment(
            campaign_id=attempt.campaign_id,
            turn_id=attempt.turn_id,
            attempt_id=attempt.attempt_id,
            ordinal=attempt.ordinal,
            channel=attempt.channel,
            text=attempt.buffer,
            response_blocks=attempt.response_blocks,
        )
        attempt.ordinal += 1
        attempt.buffer = ""
        attempt.response_blocks = None

    def _attempt(self, key: tuple[str, str]) -> _NarrationAttempt:
        attempt = self._attempts.get(key)
        if attempt is None:
            raise CommandRejected("Narration attempt is unavailable")
        return attempt

    def _live_attempt(self, key: tuple[str, str]) -> _NarrationAttempt:
        attempt = self._attempt(key)
        if attempt.terminal:
            raise CommandRejected("Narration attempt is terminal")
        return attempt

    def _admit(
        self,
        command: ActionCommand,
        campaign: CampaignRecord,
        turn_status: TurnStatus,
    ) -> None:
        if campaign.lifecycle is not LifecycleState.ACTIVE:
            raise CommandRejected("Campaign lifecycle blocks mutation", code="lifecycle_inactive")
        if campaign.writer is not command.writer:
            raise WrongWriter("Command writer is not authoritative")
        if campaign.revision != command.expected_revision:
            raise StaleRevision("Expected campaign revision is stale")
        if turn_status is not TurnStatus.OPEN:
            raise CommandRejected("TURN does not admit actions")

    def _admitted_tool(
        self,
        command: ActionCommand,
        package_lock: PackageLock,
        *,
        state: Mapping[str, JsonValue],
        turn_status: TurnStatus,
        writer: WriterRole,
    ) -> CatalogTool:
        package = self._locked_package(command.package_id, package_lock)
        declaration = self._store.declared_package_tool(
            package.package_id,
            package.version,
            package.sha256,
            command.tool_name,
        )
        tool = None if declaration is None else self._package_tool(package, declaration)
        if (
            tool is None
            or declaration is None
            or tool.capability not in package.capabilities
            or not declaration_admitted(
                declaration,
                state=state,
                turn_status=turn_status.value,
                writer=writer.value,
                declared_capabilities=frozenset(package.capabilities),
            )
        ):
            raise CommandRejected(
                "Package tool is inactive or unavailable", code="tool_unavailable"
            )
        return tool

    @staticmethod
    def _locked_package(package_id: str, package_lock: PackageLock) -> PackageLockEntry:
        package = next(
            (entry for entry in package_lock.entries if entry.package_id == package_id),
            None,
        )
        if package is None:
            raise CommandRejected(
                "Package tool is inactive or unavailable", code="tool_unavailable"
            )
        return package

    def _validate_campaign_inputs(self, captured: _CampaignCreationSnapshot) -> None:
        for entry in captured.package_lock.entries:
            if entry.role is PackageRole.PRIMARY:
                require_active_ruleset(entry.package_id)
        campaign_schema_is_active = self._store.campaign_schema_is_active()
        legacy_participants_are_admitted = self._store.legacy_participants_are_admitted(
            captured.package_lock,
            captured.participants,
        )
        packages = {
            (entry.package_id, entry.version, entry.sha256): entry
            for entry in captured.package_lock.entries
        }
        package_versions = {(item[0], item[1]) for item in packages}
        participant_ids: set[str] = set()
        for participant in captured.participants:
            if participant.participant_id in participant_ids:
                raise ValueError("Participant identities must be unique within a campaign")
            participant_ids.add(participant.participant_id)
            if participant.validation_status is ParticipantValidationStatus.VALIDATED:
                package_sha256 = participant.package_sha256
                if package_sha256 is None:
                    raise CommandRejected("Validated participant package proof is incomplete")
                package_identity = (
                    participant.package_id,
                    participant.package_version,
                    package_sha256,
                )
                if package_identity not in packages:
                    raise CommandRejected(
                        "Participant exact package identity must be pinned by the campaign lock",
                        code="participant_package_unlocked",
                    )
                declarations = self._store.exact_participant_declarations(
                    participant.package_id,
                    participant.package_version,
                    package_sha256,
                )
                declaration = next(
                    (item for item in declarations if item.kind == participant.kind),
                    None,
                )
                if declaration is None:
                    raise CommandRejected(
                        "Participant package does not declare its artifact kind",
                        code="participant_schema_unavailable",
                    )
                if (
                    participant.schema_id != declaration.schema_id
                    or participant.schema_sha256 != declaration.schema_sha256
                ):
                    raise CommandRejected(
                        "Participant exact schema identity does not match its package declaration",
                        code="participant_schema_mismatch",
                    )
                validate_json_value(
                    participant.data,
                    declaration.schema,
                    label=f"Participant {participant.participant_id}",
                )
                if not self._store.participant_adaptation_is_admitted(participant):
                    raise CommandRejected(
                        "Cross-schema participant reuse requires a durable adaptation operation",
                        code="participant_adaptation_required",
                    )
            elif campaign_schema_is_active and not legacy_participants_are_admitted:
                raise CommandRejected(
                    "New v5 participants require exact validated package and schema proof",
                    code="participant_exact_proof_required",
                )
            elif (participant.package_id, participant.package_version) not in package_versions:
                raise ValueError("Participant package context must be locked by the campaign")
        state_keys = {(item.namespace, item.key) for item in captured.initial_state}
        if len(state_keys) != len(captured.initial_state):
            raise ValueError("Initial state keys must be unique")
        if any(
            not canonical_payload_is_safe(participant.data) for participant in captured.participants
        ):
            raise CommandRejected(
                "Participant data contains a reserved canonical channel",
                code="unsafe_canonical_payload",
            )
        if any(
            not canonical_payload_is_safe({item.namespace: {item.key: item.value}})
            for item in captured.initial_state
        ):
            raise CommandRejected(
                "Initial state contains a reserved canonical channel",
                code="unsafe_canonical_payload",
            )

    def _first_turn(self, campaign_id: str) -> str:
        snapshot = self._store.snapshot(campaign_id)
        if not snapshot.turns:
            raise CommandRejected("Campaign has no TURN")
        return next(iter(snapshot.turns))

    @contextmanager
    def _session_campaign_work(self, session_id: str) -> Iterator[None]:
        """Register session-keyed mutation work on its authoritative campaign."""

        initial = self._sessions.context(session_id)
        campaign_id = initial.session.campaign_id
        if campaign_id is None:
            with self._lane(f"builder-chat:{session_id}"):
                current = self._sessions.context(session_id)
                if current.session.campaign_id is not None:
                    raise CommandRejected("Builder chat campaign association changed")
                yield
            return
        with self._campaign_work(campaign_id):
            current = self._sessions.context(session_id)
            if current.session.campaign_id != campaign_id:
                raise CommandRejected("Agent session campaign identity changed")
            yield

    def _begin_campaign_work(self, campaign_id: str) -> None:
        try:
            validate_authority_identity(campaign_id, "Campaign identity")
        except (TypeError, ValueError) as error:
            raise CommandRejected(
                "Campaign authority identity is invalid",
                code="invalid_authority_identity",
            ) from error
        with self._work_condition:
            if campaign_id in self._direct_action_campaigns:
                raise CommandRejected(
                    "A player action is still resolving", code="direct_action_in_progress"
                )
            if campaign_id in self._campaign_transitions:
                raise CommandRejected(
                    "Campaign package transition blocks new work",
                    code="package_transition_in_progress",
                )
            snapshot = self._store.snapshot(campaign_id)
            if snapshot.lifecycle is not LifecycleState.ACTIVE:
                raise CommandRejected(
                    "Campaign lifecycle blocks new work", code="lifecycle_inactive"
                )
            self._campaign_work_counts[campaign_id] = (
                self._campaign_work_counts.get(campaign_id, 0) + 1
            )

    def _finish_campaign_work(self, campaign_id: str) -> None:
        with self._work_condition:
            count = self._campaign_work_counts.get(campaign_id, 0)
            if count <= 1:
                self._campaign_work_counts.pop(campaign_id, None)
            else:
                self._campaign_work_counts[campaign_id] = count - 1
            self._work_condition.notify_all()

    @contextmanager
    def _campaign_work(self, campaign_id: str) -> Iterator[None]:
        self._begin_campaign_work(campaign_id)
        try:
            yield
        finally:
            self._finish_campaign_work(campaign_id)

    @contextmanager
    def _direct_action_work(self, campaign_id: str) -> Iterator[None]:
        """Admit a player click only while idle, without queueing or interrupting GM work."""

        with self._work_condition:
            if self._campaign_work_counts.get(campaign_id, 0):
                raise CommandRejected(
                    "Player actions are unavailable while campaign work is running",
                    code="campaign_work_in_progress",
                )
            self._begin_campaign_work(campaign_id)
            self._direct_action_campaigns.add(campaign_id)
        try:
            yield
        finally:
            with self._work_condition:
                self._direct_action_campaigns.discard(campaign_id)
                self._finish_campaign_work(campaign_id)

    def _reserve_campaign_transition(self, campaign_id: str) -> None:
        with self._work_condition:
            if campaign_id in self._campaign_transitions:
                raise CommandRejected("Campaign package transition is already in progress")
            if self._campaign_work_counts.get(campaign_id, 0):
                raise CommandRejected(
                    "Campaign has active work; activation is not queued",
                    code="campaign_work_in_progress",
                )
            self._campaign_transitions.add(campaign_id)

    def _release_campaign_transition(self, campaign_id: str) -> None:
        with self._work_condition:
            self._campaign_transitions.discard(campaign_id)
            self._work_condition.notify_all()

    def _ensure_campaign_not_transitioning(self, campaign_id: str) -> None:
        with self._work_condition:
            if campaign_id in self._campaign_transitions:
                raise CommandRejected(
                    "Campaign package transition blocks admission",
                    code="package_transition_in_progress",
                )

    def _lane(self, campaign_id: str) -> threading.RLock:
        with self._lanes_lock:
            return self._lanes.setdefault(campaign_id, threading.RLock())

    @contextmanager
    def _operation(self) -> Iterator[None]:
        with self._lifecycle_condition:
            if self._lifecycle != "open":
                raise RuntimeError("Application authority owner is closed")
            self._active_operations += 1
        try:
            yield
        finally:
            with self._lifecycle_condition:
                self._active_operations -= 1
                if self._active_operations == 0:
                    self._lifecycle_condition.notify_all()

    def _require_open(self) -> None:
        with self._lifecycle_condition:
            if self._lifecycle != "open":
                raise RuntimeError("Application authority owner is closed")

    def close(self) -> None:
        with self._lifecycle_condition:
            if self._lifecycle == "closed":
                return
            if self._lifecycle == "closing":
                while self._lifecycle != "closed":
                    self._lifecycle_condition.wait()
                return
            self._lifecycle = "closing"
            while self._active_operations:
                self._lifecycle_condition.wait()
        try:
            with self._narration_lock:
                for attempt in tuple(self._attempts.values()):
                    if not attempt.terminal:
                        self._expire_narration_locked(attempt)
                self._inspector_trace.clear()
                self._attempts.clear()
                self._pending_tools.clear()
            self._store.close()
        finally:
            with self._lifecycle_condition:
                self._lifecycle = "closed"
                self._lifecycle_condition.notify_all()

    def __enter__(self) -> AuthorityOwner:
        self._require_open()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _safe_tool_failure_outcome() -> MechanicOutcome:
    return MechanicOutcome(
        result=owned_payload({"status": "failed", "code": "tool_execution_failed"})
    )


def _awaiting_choice_receipt(pending: PendingMechanicsAction) -> AwaitingChoiceReceipt:
    return AwaitingChoiceReceipt(
        campaign_id=pending.campaign_id,
        action_id=pending.action_id,
        basis_revision=pending.basis_revision,
        turn_id=pending.turn_id,
        choice=pending.choice,
        choice_sequence=len(pending.prior_answers),
    )


def _owned_mechanic_outcome(outcome: object) -> MechanicOutcome:
    """Capture the complete live mechanic return exactly once into Sylvia GM ownership."""

    if not isinstance(outcome, MechanicOutcome):
        raise TypeError("Mechanic did not return a MechanicOutcome")
    result = owned_payload(outcome.result)
    if any(not isinstance(value, tuple) for value in (
        outcome.mutations, outcome.consequences, outcome.participant_mutations,
    )):
        raise TypeError("Mechanic outcome collections must be tuples")
    mutations: list[StateMutation] = []
    for mutation in outcome.mutations:
        if not isinstance(mutation, StateMutation):
            raise TypeError("Mechanic mutation is malformed")
        if not isinstance(mutation.namespace, str) or not isinstance(mutation.key, str):
            raise TypeError("Mechanic mutation identity is malformed")
        validate_authority_identity(mutation.namespace, "Mechanic mutation namespace")
        validate_authority_identity(mutation.key, "Mechanic mutation key")
        mutations.append(
            StateMutation(
                namespace=mutation.namespace,
                key=mutation.key,
                value=owned_json_value(mutation.value),
            )
        )
    participant_mutations: list[ParticipantMutation] = []
    for mutation in outcome.participant_mutations:
        if not isinstance(mutation, ParticipantMutation):
            raise TypeError("Participant mutation is malformed")
        validate_authority_identity(mutation.participant_id, "Participant mutation identity")
        participant_mutations.append(ParticipantMutation(
            mutation.participant_id, owned_payload(mutation.data),
        ))
    consequences: list[SafePayload] = []
    for consequence in outcome.consequences:
        captured = owned_payload(consequence)
        kind = captured.get("kind")
        if kind in (PLAYER_ACTION_ORIGIN, "participant_updated"):
            raise TypeError("Mechanic cannot declare authority-owned provenance")
        if kind is not None:
            validate_authority_identity(kind, "Mechanic consequence kind")
        consequences.append(captured)
    choice = outcome.choice
    if choice is not None:
        if not isinstance(choice, MechanicsChoiceRequest):
            raise TypeError("Mechanic choice request is malformed")
        choice = MechanicsChoiceRequest(
            choice_id=choice.choice_id,
            prompt=choice.prompt,
            options=tuple(
                MechanicsChoiceOption(option_id=option.option_id, label=option.label)
                for option in choice.options
            ),
        )
    return MechanicOutcome(
        result=result,
        mutations=tuple(mutations),
        participant_mutations=tuple(participant_mutations),
        consequences=tuple(consequences),
        choice=choice,
    )


def _capture_campaign_creation(command: CampaignCreate) -> _CampaignCreationSnapshot:
    """Own every caller collection once before validating campaign semantics."""

    try:
        entry_values = tuple(
            (
                entry.package_id,
                entry.version,
                entry.sha256,
                entry.role,
                entry.load_order,
                tuple(
                    PackageDependency(
                        dependency.package_id,
                        dependency.version,
                        dependency.sha256,
                        dependency.kind,
                        dependency.namespace,
                    )
                    for dependency in entry.dependencies
                ),
                tuple(entry.capabilities),
            )
            for entry in command.package_lock.entries
        )
        participant_values = tuple(
            (
                participant.participant_id,
                participant.kind,
                participant.package_id,
                participant.package_version,
                owned_payload(participant.data),
                participant.package_sha256,
                participant.schema_id,
                participant.schema_sha256,
                participant.validation_status,
                participant.adaptation_id,
            )
            for participant in command.participants
        )
        state_values = tuple(
            (
                state_value.namespace,
                state_value.key,
                owned_json_value(state_value.value),
            )
            for state_value in command.initial_state
        )
        package_lock = PackageLock(
            entries=tuple(
                PackageLockEntry(
                    package_id=package_id,
                    version=version,
                    sha256=sha256,
                    role=role,
                    load_order=load_order,
                    dependencies=dependencies,
                    capabilities=capabilities,
                )
                for (
                    package_id,
                    version,
                    sha256,
                    role,
                    load_order,
                    dependencies,
                    capabilities,
                ) in entry_values
            )
        )
    except (TypeError, ValueError) as error:
        raise CommandRejected(
            "Campaign creation values are invalid",
            code="unsafe_canonical_payload",
        ) from error
    return _CampaignCreationSnapshot(
        package_lock=package_lock,
        participants=tuple(Participant(*values) for values in participant_values),
        initial_state=tuple(StateValue(*values) for values in state_values),
    )


def _schema_without_required(schema: Mapping[str, object]) -> Mapping[str, object]:
    return {**schema, "required": []}


def _canonical_parameter_snapshot(parameters: SafePayload) -> dict[str, JsonValue]:
    """Own one deep JSON-compatible parameter value for admission through persistence."""

    snapshot = _snapshot_json_value(parameters)
    if not isinstance(snapshot, dict):  # pragma: no cover - SafePayload is mapping-shaped
        raise CommandRejected("Tool parameters must be an object")
    return snapshot


def _snapshot_json_value(value: object) -> JsonValue:
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise CommandRejected(
                "Tool parameters contain a non-string key", code="unsafe_canonical_payload"
            )
        return {key: _snapshot_json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_snapshot_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise CommandRejected(
        "Tool parameters are not JSON-compatible", code="unsafe_canonical_payload"
    )


def _freeze_payload(payload: Mapping[str, JsonValue]) -> FrozenPayload:
    return MappingProxyType({key: _freeze_json_value(value) for key, value in payload.items()})


def _participant_context_payload(participant: Participant) -> dict[str, JsonValue]:
    """Project one detached participant value into a read-only mechanic context."""

    return {
        "participant_id": participant.participant_id,
        "kind": participant.kind,
        "package_id": participant.package_id,
        "package_version": participant.package_version,
        "package_sha256": participant.package_sha256,
        "schema_id": participant.schema_id,
        "schema_sha256": participant.schema_sha256,
        "validation_status": participant.validation_status.value,
        "adaptation_id": participant.adaptation_id,
        "data": participant.data,
    }


def _freeze_json_value(value: JsonValue) -> FrozenJsonValue:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_json_value(item) for key, item in value.items()})
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze_json_value(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("Mechanic parameters are not JSON-compatible")


def _campaign_operation_payload(
    *,
    operation_id: str,
    operation_kind: str,
    values: Mapping[str, object],
) -> SafePayload:
    """Own one canonical semantic operation identity; revisions stay admission-only."""

    return owned_payload(
        {
            "operation_id": operation_id,
            "operation_kind": operation_kind,
            **values,
        }
    )


def _package_lock_payload(package_lock: PackageLock) -> list[dict[str, object]]:
    """Represent the complete exact lock without campaign- or lane-local identity."""

    return [
        {
            "package_id": entry.package_id,
            "version": entry.version,
            "sha256": entry.sha256,
            "role": entry.role.value,
            "load_order": entry.load_order,
            "dependencies": [
                {
                    "package_id": dependency.package_id,
                    "version": dependency.version,
                    "sha256": dependency.sha256,
                    "kind": dependency.kind,
                    "namespace": dependency.namespace,
                }
                for dependency in entry.dependencies
            ],
            "capabilities": list(entry.capabilities),
        }
        for entry in package_lock.entries
    ]


def _semantic_hash(
    command: ActionCommand,
    parameters: SafePayload,
    *,
    package_version: str,
    package_sha256: str,
) -> str:
    payload: dict[str, JsonValue] = {
        "campaign_id": command.campaign_id,
        "action_id": command.action_id,
        "writer": command.writer.value,
        "expected_revision": command.expected_revision,
        "turn_id": command.turn_id,
        "package_id": command.package_id,
        "package_version": package_version,
        "package_sha256": package_sha256,
        "tool_name": command.tool_name,
        "parameters": dict(parameters),
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
