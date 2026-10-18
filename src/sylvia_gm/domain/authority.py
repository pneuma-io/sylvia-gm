"""Ruleset-neutral F1 campaign authority contracts."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeAlias, overload


@dataclass(frozen=True)
class MechanicsChoiceAnswer:
    """One authorized answer retained while the same deterministic action continues."""

    resolution_id: str
    request: MechanicsChoiceRequest
    option_id: str
    session_id: str | None = None


JsonScalar: TypeAlias = str | int | float | bool | None


class FrozenJsonDict(Mapping[str, "FrozenJsonValue"]):
    """Tuple-backed immutable JSON object with mapping equality and readable repr."""

    __slots__ = ("__items",)

    def __init__(self, items: Iterable[tuple[str, FrozenJsonValue]]) -> None:
        self.__items = tuple(items)

    def __getitem__(self, key: str) -> FrozenJsonValue:
        for item_key, value in self.__items:
            if item_key == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self.__items)

    def __len__(self) -> int:
        return len(self.__items)

    def __repr__(self) -> str:
        return repr(dict(self.items()))

    def __setitem__(self, key: str, value: object) -> None:
        raise TypeError("Frozen JSON objects are immutable")

    def __delitem__(self, key: str) -> None:
        raise TypeError("Frozen JSON objects are immutable")

    def clear(self) -> None:
        raise TypeError("Frozen JSON objects are immutable")

    def pop(self, key: str, default: object = None) -> FrozenJsonValue:
        raise TypeError("Frozen JSON objects are immutable")

    def popitem(self) -> tuple[str, FrozenJsonValue]:
        raise TypeError("Frozen JSON objects are immutable")

    def setdefault(self, key: str, default: object = None) -> FrozenJsonValue:
        raise TypeError("Frozen JSON objects are immutable")

    def update(self, *args: object, **kwargs: object) -> None:
        raise TypeError("Frozen JSON objects are immutable")

    def __ior__(self, other: object) -> FrozenJsonDict:
        raise TypeError("Frozen JSON objects are immutable")


class FrozenJsonList(Sequence["FrozenJsonValue"]):
    """Tuple-backed immutable JSON array with sequence equality and readable repr."""

    __slots__ = ("__items",)

    def __init__(self, items: Iterable[FrozenJsonValue]) -> None:
        self.__items = tuple(items)

    @overload
    def __getitem__(self, index: int) -> FrozenJsonValue: ...

    @overload
    def __getitem__(self, index: slice) -> FrozenJsonList: ...

    def __getitem__(self, index: int | slice) -> FrozenJsonValue | FrozenJsonList:
        if isinstance(index, slice):
            return FrozenJsonList(self.__items[index])
        return self.__items[index]

    def __len__(self) -> int:
        return len(self.__items)

    def __repr__(self) -> str:
        return repr(list(self.__items))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Sequence) and not isinstance(other, (str, bytes, bytearray)):
            return tuple(self) == tuple(other)
        return False

    def __setitem__(self, key: int | slice, value: object) -> None:
        raise TypeError("Frozen JSON arrays are immutable")

    def __delitem__(self, key: int | slice) -> None:
        raise TypeError("Frozen JSON arrays are immutable")

    def append(self, value: object) -> None:
        raise TypeError("Frozen JSON arrays are immutable")

    def clear(self) -> None:
        raise TypeError("Frozen JSON arrays are immutable")

    def extend(self, values: Iterable[object]) -> None:
        raise TypeError("Frozen JSON arrays are immutable")

    def insert(self, index: int, value: object) -> None:
        raise TypeError("Frozen JSON arrays are immutable")

    def pop(self, index: int = -1) -> FrozenJsonValue:
        raise TypeError("Frozen JSON arrays are immutable")

    def remove(self, value: object) -> None:
        raise TypeError("Frozen JSON arrays are immutable")

    def reverse(self) -> None:
        raise TypeError("Frozen JSON arrays are immutable")

    def sort(self, *args: object, **kwargs: object) -> None:
        raise TypeError("Frozen JSON arrays are immutable")

    def __iadd__(self, values: Iterable[object]) -> FrozenJsonList:
        raise TypeError("Frozen JSON arrays are immutable")

    def __imul__(self, count: int) -> FrozenJsonList:
        raise TypeError("Frozen JSON arrays are immutable")


FrozenJsonValue: TypeAlias = (
    JsonScalar
    | FrozenJsonList
    | FrozenJsonDict
    | tuple["FrozenJsonValue", ...]
    | Mapping[str, "FrozenJsonValue"]
)
JsonValue: TypeAlias = (
    JsonScalar
    | list["JsonValue"]
    | dict[str, "JsonValue"]
    | FrozenJsonList
    | FrozenJsonDict
    | tuple["JsonValue", ...]
    | Mapping[str, "JsonValue"]
)
SafePayload: TypeAlias = Mapping[str, JsonValue]
FrozenPayload: TypeAlias = Mapping[str, FrozenJsonValue]


def validate_authority_identity(
    value: object,
    label: str,
    *,
    sha256: bool = False,
) -> str:
    """Return one nonblank authoritative identity or fail with safe domain validation."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-whitespace string")
    if value != value.strip():
        raise ValueError(f"{label} must not have leading or trailing whitespace")
    if sha256 and (
        len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{label} must be 64 lowercase hexadecimal characters")
    return value


def owned_json_value(value: object) -> FrozenJsonValue:
    """Capture one recursively owned, immutable, JSON-compatible semantic value."""

    if isinstance(value, Mapping):
        captured: list[tuple[str, FrozenJsonValue]] = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings")
            captured.append((key, owned_json_value(item)))
        return FrozenJsonDict(captured)
    if isinstance(value, (list, FrozenJsonList)):
        return FrozenJsonList(owned_json_value(item) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise TypeError("Value is not JSON-compatible")


def owned_payload(value: object) -> SafePayload:
    """Capture an immutable JSON object, rejecting all other top-level shapes."""

    captured = owned_json_value(value)
    if not isinstance(captured, FrozenJsonDict):
        raise TypeError("Safe payload must be a JSON object")
    return captured


def thaw_json_value(value: object) -> JsonValue:
    """Materialize immutable owned JSON as plain dict/list values for serialization."""

    if isinstance(value, Mapping):
        return {str(key): thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [thaw_json_value(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise TypeError("Value is not JSON-compatible")


class LifecycleState(StrEnum):
    """F1 lifecycle states that affect command admission."""

    ACTIVE = "active"
    ARCHIVED = "archived"
    TOMBSTONED = "tombstoned"


class WriterRole(StrEnum):
    """The sole campaign-changing role."""

    BUILDER = "builder"
    DM = "dm"
    NONE = "none"


class ParticipantValidationStatus(StrEnum):
    """Whether one participant has exact package/schema proof or is quarantined."""

    VALIDATED = "validated"
    QUARANTINED = "quarantined"


def lifecycle_writer_is_legal(lifecycle: LifecycleState, writer: WriterRole) -> bool:
    """Enforce the F1 writer matrix without permitting contradictory authority."""

    if lifecycle is LifecycleState.ACTIVE:
        return writer in (WriterRole.BUILDER, WriterRole.DM)
    return writer is WriterRole.NONE


class PackageRole(StrEnum):
    """Exact lock membership role."""

    PRIMARY = "primary"
    DEPENDENCY = "dependency"


class TurnStatus(StrEnum):
    """F1-sized durable TURN lifecycle."""

    OPEN = "open"
    COMPLETED = "completed"


class ActionStatus(StrEnum):
    """Terminal F1 action outcomes."""

    COMMITTED = "committed"
    FAILED = "failed"


class CanonicalEventType(StrEnum):
    """Chronological records in the sole campaign event sequence."""

    THINK = "think"
    TALK = "talk"
    ACTION_DECLARED = "action_declared"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    STATE_MUTATION = "state_mutation"
    CONSEQUENCE = "consequence"
    ACTION_COMMITTED = "action_committed"
    ACTION_FAILED = "action_failed"


class EventVisibility(StrEnum):
    """Safe canonical visibility; private provider output has no canonical value."""

    INTERNAL_SAFE = "internal_safe"
    PLAYER_SAFE = "player_safe"


class ProseChannel(StrEnum):
    """The only prose channels admitted to canonical campaign chronology."""

    SAFE_THINK = "safe_think"
    TALK = "talk"


class ModelToolBehavior(StrEnum):
    """Ruleset-authored guidance for how the GM treats one available mechanic."""

    OPTIONAL = "optional"
    REQUIRED_ENGINE_TRANSITION = "required_engine_transition"
    # Requires a Ruleset choice; never execute it with guessed or empty inputs.
    REQUIRED_RULESET_DECISION = "required_ruleset_decision"
    LEAVES_PLAYER_INTENT_PENDING = "leaves_player_intent_pending"
    RESOLVES_PLAYER_INTENT = "resolves_player_intent"
    SUPPORTS_PLAYER_INTENT = "supports_player_intent"


class ModelDecisionOwner(StrEnum):
    """Who supplies the next genuine gameplay decision."""

    PLAYER = "player"
    GM = "gm"


class InspectorTraceSink(StrEnum):
    """Trace sink identities used by deny-by-default ingress admission."""

    INSPECTOR = "inspector"
    LOGGER = "logger"
    CANONICAL = "canonical"
    EXPORT = "export"


class PlayerProjectionKind(StrEnum):
    """Ruleset-neutral player projection item kinds."""

    THINK = "think"
    TALK = "talk"
    PENDING_TOOL = "pending_tool"
    MECHANICS_CHOICE = "mechanics_choice"
    TOOL_CARD = "tool_card"
    RETRY = "retry"


@dataclass(frozen=True)
class PackageDependency:
    package_id: str
    version: str
    sha256: str
    kind: str
    namespace: str = "legacy"

    def __post_init__(self) -> None:
        validate_authority_identity(self.package_id, "Dependency package identity")
        validate_authority_identity(self.version, "Dependency version")
        validate_authority_identity(self.sha256, "Dependency SHA-256", sha256=True)
        validate_authority_identity(self.kind, "Dependency kind")
        validate_authority_identity(self.namespace, "Dependency namespace")


@dataclass(frozen=True)
class PackageLockEntry:
    package_id: str
    version: str
    sha256: str
    role: PackageRole
    load_order: int
    dependencies: tuple[PackageDependency, ...]
    capabilities: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "dependencies", tuple(self.dependencies))
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        validate_authority_identity(self.package_id, "Package identity")
        validate_authority_identity(self.version, "Package version")
        validate_authority_identity(self.sha256, "Package SHA-256", sha256=True)
        for capability in self.capabilities:
            validate_authority_identity(capability, "Package capability")
        if self.load_order < 0:
            raise ValueError("Package load order cannot be negative")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("Package capabilities must be unique")
        dependency_ids = [dependency.package_id for dependency in self.dependencies]
        if len(set(dependency_ids)) != len(dependency_ids):
            raise ValueError("A package cannot repeat logical dependency membership")


@dataclass(frozen=True)
class PackageLock:
    entries: tuple[PackageLockEntry, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", tuple(self.entries))
        if self.entries and sum(entry.role is PackageRole.PRIMARY for entry in self.entries) != 1:
            raise ValueError(
                "A nonempty campaign package lock requires exactly one primary package"
            )
        package_ids = [entry.package_id for entry in self.entries]
        if len(set(package_ids)) != len(package_ids):
            raise ValueError("A logical package may have only one exact identity in a lock")
        orders = tuple(entry.load_order for entry in self.entries)
        if len(set(orders)) != len(self.entries):
            raise ValueError("Package load order must be unique")
        if orders != tuple(sorted(orders)):
            raise ValueError("Package entries must use canonical load order")
        identities = {(entry.package_id, entry.version, entry.sha256) for entry in self.entries}
        for entry in self.entries:
            for dependency in entry.dependencies:
                identity = (dependency.package_id, dependency.version, dependency.sha256)
                if identity not in identities:
                    raise ValueError("Every exact dependency must be present in the lock closure")


@dataclass(frozen=True)
class Participant:
    participant_id: str
    kind: str
    package_id: str
    package_version: str
    data: SafePayload
    package_sha256: str | None = None
    schema_id: str | None = None
    schema_sha256: str | None = None
    validation_status: ParticipantValidationStatus = ParticipantValidationStatus.QUARANTINED
    adaptation_id: str | None = None

    def __post_init__(self) -> None:
        validate_authority_identity(self.participant_id, "Participant identity")
        validate_authority_identity(self.kind, "Participant kind")
        validate_authority_identity(self.package_id, "Participant package identity")
        validate_authority_identity(self.package_version, "Participant package version")
        try:
            status = ParticipantValidationStatus(self.validation_status)
        except ValueError as error:
            raise ValueError("Participant validation status is invalid") from error
        object.__setattr__(self, "validation_status", status)
        if self.package_sha256 is not None:
            validate_authority_identity(
                self.package_sha256, "Participant package SHA-256", sha256=True
            )
        if self.schema_id is not None:
            validate_authority_identity(self.schema_id, "Participant schema identity")
        if self.schema_sha256 is not None:
            validate_authority_identity(
                self.schema_sha256, "Participant schema SHA-256", sha256=True
            )
        if self.adaptation_id is not None:
            validate_authority_identity(self.adaptation_id, "Participant adaptation identity")
        if status is ParticipantValidationStatus.VALIDATED and (
            self.package_sha256 is None or self.schema_id is None or self.schema_sha256 is None
        ):
            raise ValueError("Validated participants require exact package and schema identity")


@dataclass(frozen=True)
class ParticipantArtifact(Participant):
    """Explicit F5 name for one generic participant artifact."""


@dataclass(frozen=True)
class StateValue:
    namespace: str
    key: str
    value: JsonValue

    def __post_init__(self) -> None:
        validate_authority_identity(self.namespace, "State namespace")
        validate_authority_identity(self.key, "State key")


@dataclass(frozen=True)
class StateMutation:
    namespace: str
    key: str
    value: JsonValue


@dataclass(frozen=True)
class ParticipantMutation:
    """Replace live character data without changing its exact package or schema."""

    participant_id: str
    data: SafePayload


@dataclass(frozen=True)
class MechanicContext:
    """Immutable data-only input; it deliberately exposes no database authority."""

    campaign_id: str
    turn_id: str
    action_id: str
    state: FrozenPayload
    parameters: FrozenPayload
    participants: FrozenPayload = field(default_factory=lambda: FrozenJsonDict(()))
    choice_resolution: MechanicsChoiceResolution | None = None


@dataclass(frozen=True)
class MechanicsChoiceOption:
    """One bounded package-authored option shown by the trusted owner UI."""

    option_id: str
    label: str


@dataclass(frozen=True)
class MechanicsChoiceRequest:
    """A rules-neutral post-result choice emitted by one deterministic mechanic."""

    choice_id: str
    prompt: str
    options: tuple[MechanicsChoiceOption, ...]


@dataclass(frozen=True)
class MechanicsChoiceResolution:
    """The sole owner-authorized input injected when a pending mechanic resumes."""

    choice_id: str
    option_id: str
    expected_request: MechanicsChoiceRequest
    prior_answers: tuple[MechanicsChoiceAnswer, ...] = ()


@dataclass(frozen=True)
class AwaitingChoiceReceipt:
    """Durable nonterminal mechanics reservation projected without action ambiguity."""

    campaign_id: str
    action_id: str
    basis_revision: int
    turn_id: str
    choice: MechanicsChoiceRequest
    choice_sequence: int = 0

    @property
    def status(self) -> str:
        return "awaiting_choice"


@dataclass(frozen=True)
class PendingMechanicsAction:
    """Exact immutable basis needed to deterministically reconstruct one mechanic."""

    campaign_id: str
    action_id: str
    turn_id: str
    basis_revision: int
    writer: WriterRole
    package_id: str
    package_version: str
    package_sha256: str
    lock_id: str
    tool_name: str
    parameters: SafePayload
    payload_hash: str
    initiating_session_id: str | None
    choice: MechanicsChoiceRequest
    descriptor_sha256: str
    player_action_origin: SafePayload | None = None
    prior_answers: tuple[MechanicsChoiceAnswer, ...] = ()


@dataclass(frozen=True)
class MechanicOutcome:
    result: SafePayload
    mutations: tuple[StateMutation, ...] = ()
    consequences: tuple[SafePayload, ...] = ()
    choice: MechanicsChoiceRequest | None = None
    participant_mutations: tuple[ParticipantMutation, ...] = ()


def _is_reserved_canonical_channel(name: str) -> bool:
    """Match private channel families after separator and camel-case normalization.

    This is deliberately structural: ordinary values are never content-filtered, and
    neutral keys such as ``stack`` and ``exception`` are not private channels.
    """

    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    tokens = tuple(token for token in re.split(r"[^a-z0-9]+", separated.casefold()) if token)
    compact = "".join(tokens)
    private_qualifiers = ("private", "raw", "provider", "chain", "hidden", "internal")
    if "reasoning" in compact and any(qualifier in compact for qualifier in private_qualifiers):
        return True
    if "thought" in compact and any(qualifier in compact for qualifier in private_qualifiers):
        return True
    if "monologue" in compact and any(qualifier in compact for qualifier in private_qualifiers):
        return True
    if "scratchpad" in compact:
        return True
    if any(family in compact for family in ("modelresponse", "modeloutput")) and any(
        qualifier in compact for qualifier in private_qualifiers
    ):
        return True
    if "chainofthought" in compact or "thoughtchain" in compact:
        return True
    if "diagnostic" in compact:
        return True
    if "traceback" in compact:
        return True
    if "stacktrace" in compact or "tracestack" in compact:
        return True
    return "trace" in compact and any(
        qualifier in compact for qualifier in ("inspector", "private")
    )


def canonical_payload_is_safe(value: object) -> bool:
    """Reject private/internal structural channels without filtering ordinary text."""

    if isinstance(value, Mapping):
        return all(
            isinstance(key, str)
            and not _is_reserved_canonical_channel(key)
            and canonical_payload_is_safe(item)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return all(canonical_payload_is_safe(item) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return True
    return isinstance(value, float) and math.isfinite(value)


def mechanic_outcome_is_safe(outcome: MechanicOutcome) -> bool:
    """Validate every successful mechanic output before any canonical write."""

    if not isinstance(outcome.result, Mapping):
        return False
    if "mechanics_choice_answers" in outcome.result:
        return False
    if any(not isinstance(value, tuple) for value in (
        outcome.mutations, outcome.consequences, outcome.participant_mutations,
    )):
        return False
    if not canonical_payload_is_safe(outcome.result):
        return False
    if outcome.choice is not None:
        choice = outcome.choice
        if (
            not isinstance(choice, MechanicsChoiceRequest)
            or not isinstance(choice.choice_id, str)
            or not isinstance(choice.prompt, str)
            or not isinstance(choice.options, tuple)
            or not 2 <= len(choice.options) <= 16
            or len(choice.prompt.encode("utf-8")) > 4096
        ):
            return False
        try:
            validate_authority_identity(choice.choice_id, "Mechanics choice identity")
        except ValueError:
            return False
        option_ids: set[str] = set()
        for option in choice.options:
            if (
                not isinstance(option, MechanicsChoiceOption)
                or not isinstance(option.option_id, str)
                or not isinstance(option.label, str)
                or not option.label
                or len(option.label.encode("utf-8")) > 1024
            ):
                return False
            try:
                validate_authority_identity(option.option_id, "Mechanics option identity")
            except ValueError:
                return False
            if option.option_id in option_ids:
                return False
            option_ids.add(option.option_id)
        if outcome.mutations or outcome.consequences or outcome.participant_mutations:
            return False
    participant_ids: set[str] = set()
    for mutation in outcome.participant_mutations:
        if (
            not isinstance(mutation, ParticipantMutation)
            or not isinstance(mutation.participant_id, str)
            or not mutation.participant_id
            or mutation.participant_id in participant_ids
            or not isinstance(mutation.data, Mapping)
            or not canonical_payload_is_safe(mutation.data)
        ):
            return False
        participant_ids.add(mutation.participant_id)
    status = outcome.result.get("status")
    if status is not None and not isinstance(status, str):
        return False
    for consequence in outcome.consequences:
        if not isinstance(consequence, Mapping) or not canonical_payload_is_safe(consequence):
            return False
        kind = consequence.get("kind")
        if kind == "participant_updated":
            return False
        if kind is not None and (not isinstance(kind, str) or not kind):
            return False
    for mutation in outcome.mutations:
        if not isinstance(mutation, StateMutation):
            return False
        if not isinstance(mutation.namespace, str) or not isinstance(mutation.key, str):
            return False
        if (
            _is_reserved_canonical_channel(mutation.namespace)
            or _is_reserved_canonical_channel(mutation.key)
            or not canonical_payload_is_safe(mutation.value)
        ):
            return False
    return True


Mechanic = Callable[[MechanicContext], MechanicOutcome]


@dataclass(frozen=True)
class CampaignRecord:
    campaign_id: str
    display_name: str
    lifecycle: LifecycleState
    writer: WriterRole
    revision: int


@dataclass(frozen=True)
class CampaignConfirmation:
    """Durable result of confirming NEW CAMPAIGN before Builder lane work."""

    campaign_id: str
    resulting_revision: int


@dataclass(frozen=True)
class GuidedCampaignDraft:
    """Stable unbound campaign identity and deterministic interview entry."""

    campaign_id: str
    builder_session_id: str
    current_step: str
    construction_revision: int
    resulting_revision: int
    answer_message_id: str | None = None


@dataclass(frozen=True)
class GuidedCreationSubflow:
    """One explicit Builder chat linked to a suspended Campaign Interview question."""

    campaign_id: str
    parent_session_id: str
    child_session_id: str
    purpose: str
    current_step: str
    status: str
    construction_revision: int


@dataclass(frozen=True)
class CampaignConstructionBegun:
    """Durable result of binding one Builder lane to a confirmed campaign."""

    campaign_id: str
    lane: str
    resulting_revision: int


@dataclass(frozen=True)
class CampaignConstructionCompleted:
    """Durable lane-audited, lane-neutral campaign construction identity."""

    campaign_id: str
    lane: str
    canonical_representation_sha256: str
    resulting_revision: int


@dataclass(frozen=True)
class CampaignConstructionBasis:
    """Owner-only durable materialization basis for construction finalization."""

    campaign_id: str
    display_name: str
    lane: str
    canonical_input: SafePayload
    canonical_plan: SafePayload | None
    authority_turn_id: str
    package_lock_id: str
    package_candidate_id: str | None
    builder_session_id: str
    begin_revision: int
    completion_revision: int | None
    canonical_representation_sha256: str | None


@dataclass(frozen=True, slots=True)
class CampaignLifecycleResult:
    """Durable receipt for one reversible campaign metadata/lifecycle operation."""

    campaign_id: str
    lifecycle: LifecycleState
    writer: WriterRole
    resulting_revision: int
    session_id: str | None = None

    @property
    def revision(self) -> int:
        """Expose the campaign revision spelling used by snapshot-shaped callers."""

        return self.resulting_revision


@dataclass(frozen=True, slots=True)
class CampaignPurgeResult:
    """Content-free durable result of one explicit irreversible campaign purge."""

    campaign_id: str
    resulting_revision: int


@dataclass(frozen=True, slots=True)
class CampaignExportResult:
    """Path-free immutable identity of one completely published portable archive."""

    campaign_id: str
    captured_revision: int
    archive_sha256: str
    byte_count: int


@dataclass(frozen=True, slots=True)
class CampaignRecoveryResult:
    """Path-free immutable result of one exact portable campaign recovery."""

    campaign_id: str
    captured_revision: int
    archive_sha256: str
    byte_count: int


@dataclass(frozen=True)
class CanonicalEvent:
    sequence: int
    event_type: CanonicalEventType
    turn_id: str
    action_id: str | None
    safe_payload: SafePayload
    visibility: EventVisibility = EventVisibility.INTERNAL_SAFE
    attempt_id: str | None = None
    attempt_ordinal: int | None = None


@dataclass(frozen=True)
class PlayerProjection:
    """One immutable player-safe canonical or live provisional projection."""

    kind: PlayerProjectionKind
    sequence: int | None
    text: str | None = None
    safe_payload: SafePayload = field(default_factory=lambda: FrozenJsonDict(()))
    collapsed_by_default: bool = False
    provisional: bool = False
    action_id: str | None = None
    attempt_id: str | None = None


@dataclass(frozen=True)
class CanonicalReplay:
    """Durable replay and separately identified current live provisional state."""

    canonical: tuple[CanonicalEvent, ...]
    provisional: tuple[PlayerProjection, ...]
    high_water: int


@dataclass(frozen=True)
class NarrationStream:
    """Synthetic F2 narrator stream; provider integration remains outside F2."""

    _emit: Callable[..., None]

    def emit(
        self,
        channel: ProseChannel,
        token: str,
        *,
        response_blocks: Sequence[SafePayload] | None = None,
    ) -> None:
        if response_blocks is None:
            self._emit(channel, token)
        else:
            self._emit(channel, token, response_blocks=response_blocks)


def validate_response_blocks(value: object) -> tuple[SafePayload, ...]:
    """Validate and own one ordered player-safe GM response block list."""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)) or not value:
        raise ValueError("Response blocks must be a non-empty sequence")
    blocks: list[SafePayload] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("Each response block must be an object")
        kind = item.get("kind")
        text = item.get("text")
        expected = (
            {"kind", "speaker_label", "text"}
            if kind == "npc_dialogue"
            else {"kind", "text"}
        )
        if (
            not isinstance(kind, str)
            or kind not in {"narration", "npc_dialogue", "ooc"}
            or set(item) != expected
            or not isinstance(text, str)
            or not text.strip()
        ):
            raise ValueError("Response block fields are invalid")
        if kind == "npc_dialogue" and (
            not isinstance(item.get("speaker_label"), str)
            or not str(item["speaker_label"]).strip()
        ):
            raise ValueError("NPC dialogue requires a safe speaker label")
        blocks.append(dict(item))
    return tuple(blocks)


def response_blocks_text(blocks: Sequence[SafePayload]) -> str:
    """Render the stable labelled-text fallback for player-safe response blocks."""

    validated = validate_response_blocks(blocks)
    return "\n\n".join(
        f"{block['speaker_label']}: {block['text']}"
        if block["kind"] == "npc_dialogue"
        else f"OOC — Sylvia: {block['text']}"
        if block["kind"] == "ooc"
        else str(block["text"])
        for block in validated
    )


@dataclass(frozen=True)
class MutationRecord:
    action_id: str
    ordinal: int
    namespace: str
    key: str
    before: JsonValue
    after: JsonValue


@dataclass(frozen=True)
class ConsequenceRecord:
    action_id: str
    ordinal: int
    payload: SafePayload


@dataclass(frozen=True)
class ActionReceipt:
    campaign_id: str
    action_id: str
    status: ActionStatus
    result: SafePayload
    resulting_revision: int


MechanicsExecutionReceipt: TypeAlias = ActionReceipt | AwaitingChoiceReceipt


@dataclass(frozen=True)
class NarrationInterruption:
    campaign_id: str
    interruption_id: str
    turn_id: str
    last_action_id: str | None
    failed_attempt_id: str
    retryable: bool
    resolving_retry_id: str | None = None
    resolved_sequence: int | None = None


@dataclass(frozen=True)
class RetryNarrationInput:
    """Immutable safe input for one explicit manual narration retry."""

    campaign_id: str
    interruption_id: str
    retry_id: str
    failed_attempt_id: str
    canonical_high_water: int
    turn_id: str
    action_receipt: ActionReceipt | None


@dataclass(frozen=True)
class ManualRetryResult:
    input: RetryNarrationInput
    canonical: tuple[CanonicalEvent, ...]
    replayed: bool


@dataclass(frozen=True)
class ActionRecord:
    action_id: str
    turn_id: str
    status: ActionStatus
    expected_revision: int
    resulting_revision: int
    writer: WriterRole
    package_id: str
    package_version: str
    package_sha256: str
    tool_name: str
    parameters: SafePayload
    result: SafePayload


@dataclass(frozen=True)
class CampaignContinuityRecord:
    """One immutable change to campaign-owned fictional continuity."""

    entry_id: str
    record_id: str
    kind: str
    status: str
    payload: SafePayload
    basis_message_ids: tuple[str, ...]
    admitted_revision: int
    operation_id: str | None = None


@dataclass(frozen=True)
class CampaignContinuityView:
    """Materialized current continuity plus its immutable campaign history."""

    current_scene: SafePayload | None = None
    facts: tuple[SafePayload, ...] = ()
    reveals: SafePayload = field(default_factory=lambda: FrozenJsonDict(()))
    plan_progress: tuple[SafePayload, ...] = ()
    records: tuple[CampaignContinuityRecord, ...] = ()


@dataclass(frozen=True)
class CampaignContinuityCommand:
    """Owner-authoritative request to change one Campaign State continuity record."""

    campaign_id: str
    operation_id: str
    session_id: str
    expected_revision: int
    kind: str
    record_id: str
    status: str
    payload: SafePayload
    basis_message_ids: tuple[str, ...]
    turn_id: str


@dataclass(frozen=True)
class CampaignContinuityReceipt:
    """Replayable result of one committed Campaign State continuity change."""

    campaign_id: str
    operation_id: str
    record_id: str
    kind: str
    resulting_revision: int


@dataclass(frozen=True)
class CampaignState:
    """Current campaign authority shared by complete snapshots and display reads."""

    campaign_id: str
    display_name: str
    lifecycle: LifecycleState
    writer: WriterRole
    revision: int
    package_lock: PackageLock
    state: Mapping[str, JsonValue]
    participants: tuple[Participant, ...]


@dataclass(frozen=True)
class CampaignDisplaySnapshot(CampaignState):
    """Current state with only requested display history; never an engine snapshot."""

    actions: tuple[ActionRecord, ...]
    events: tuple[CanonicalEvent, ...]
    turns: Mapping[str, TurnStatus]


@dataclass(frozen=True)
class CampaignSnapshot(CampaignState):
    actions: tuple[ActionRecord, ...]
    events: tuple[CanonicalEvent, ...]
    mutations: tuple[MutationRecord, ...]
    consequences: tuple[ConsequenceRecord, ...]
    turns: Mapping[str, TurnStatus] = field(default_factory=dict)
    continuity: CampaignContinuityView = field(default_factory=CampaignContinuityView)


@dataclass(frozen=True)
class ModelDecisionActor:
    """One owner-safe actor included in a package-declared decision scope."""

    actor_id: str
    actor_name: str
    actor_state: SafePayload


@dataclass(frozen=True)
class ModelDecisionBoundary:
    """Ruleset-neutral projection of the current actor or side decision scope."""

    owner: ModelDecisionOwner
    actor_id: str | None
    actor_name: str | None
    actor_state: SafePayload
    campaign_revision: int
    active_side_id: str | None = None
    focused_actor_id: str | None = None
    eligible_actors: tuple[ModelDecisionActor, ...] = ()
    preparation: SafePayload | None = None
