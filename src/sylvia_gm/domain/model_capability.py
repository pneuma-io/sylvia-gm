"""Evidence-bound, provider-neutral model capability guidance.

Capability placement is deliberately separate from provider catalogs.  The only
admitted placement inputs are an exact model identity and reproducible benchmark
evidence.  Provider, price, parameter count, context window, and marketing labels
are neither stored nor accepted by this domain surface.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from sylvia_gm.domain.authority import validate_authority_identity

MODEL_CAPABILITY_REGISTRY_VERSION: Final = "sylvia.capability.registry.v1"


@dataclass(frozen=True)
class CapabilityTier:
    """One stable member of the ordered Sylvia GM capability rubric."""

    tier_id: str
    rank: int
    label: str


CAPABILITY_TIERS: Final = tuple(
    CapabilityTier(
        tier_id=f"sylvia.capability.t{rank:02d}",
        rank=rank,
        label=f"Capability tier {rank}",
    )
    for rank in range(10)
)
_TIERS_BY_ID: Final = MappingProxyType({tier.tier_id: tier for tier in CAPABILITY_TIERS})


def validate_capability_tier_id(value: object) -> str:
    """Return one admitted stable tier identity or fail closed."""

    tier_id = validate_authority_identity(value, "Model capability tier")
    if tier_id not in _TIERS_BY_ID:
        raise ValueError("Model capability tier is unsupported")
    return tier_id


@dataclass(frozen=True)
class ModelCapabilityPlacement:
    """One exact model placement backed only by empirical benchmark evidence."""

    model_id: str
    tier_id: str
    benchmark_suite_id: str
    benchmark_suite_version: str
    benchmark_suite_sha256: str
    result_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "model_id",
            validate_authority_identity(self.model_id, "Model identity"),
        )
        object.__setattr__(self, "tier_id", validate_capability_tier_id(self.tier_id))
        object.__setattr__(
            self,
            "benchmark_suite_id",
            validate_authority_identity(self.benchmark_suite_id, "Benchmark suite identity"),
        )
        object.__setattr__(
            self,
            "benchmark_suite_version",
            validate_authority_identity(
                self.benchmark_suite_version,
                "Benchmark suite version",
            ),
        )
        for field_name, label in (
            ("benchmark_suite_sha256", "Benchmark suite SHA-256"),
            ("result_sha256", "Benchmark result SHA-256"),
        ):
            object.__setattr__(
                self,
                field_name,
                validate_authority_identity(
                    getattr(self, field_name),
                    label,
                    sha256=True,
                ),
            )

    @property
    def evidence_sha256(self) -> str:
        payload = {
            "benchmark_suite_id": self.benchmark_suite_id,
            "benchmark_suite_sha256": self.benchmark_suite_sha256,
            "benchmark_suite_version": self.benchmark_suite_version,
            "model_id": self.model_id,
            "result_sha256": self.result_sha256,
            "tier_id": self.tier_id,
        }
        return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


class ModelGuidanceStatus(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    UNRATED = "unrated"
    BELOW = "below"
    MEETS = "meets"


@dataclass(frozen=True)
class ModelGuidanceDecision:
    """Owner-computable warning decision for one exact model/ruleset pairing."""

    registry_version: str
    model_id: str
    minimum_tier_id: str | None
    placement_tier_id: str | None
    placement_evidence_sha256: str | None
    status: ModelGuidanceStatus
    warning_code: str | None
    override_accepted: bool

    def __post_init__(self) -> None:
        if self.registry_version != MODEL_CAPABILITY_REGISTRY_VERSION:
            raise ValueError("Model guidance registry version is unsupported")
        object.__setattr__(
            self,
            "model_id",
            validate_authority_identity(self.model_id, "Model identity"),
        )
        if self.minimum_tier_id is None:
            if (
                self.placement_tier_id is not None
                or self.placement_evidence_sha256 is not None
                or self.status is not ModelGuidanceStatus.NOT_APPLICABLE
                or self.warning_code is not None
                or self.override_accepted
            ):
                raise ValueError("No-rules model guidance decision is inconsistent")
            return

        minimum = validate_capability_tier_id(self.minimum_tier_id)
        object.__setattr__(self, "minimum_tier_id", minimum)
        if self.status is ModelGuidanceStatus.UNRATED:
            if (
                self.placement_tier_id is not None
                or self.placement_evidence_sha256 is not None
                or self.warning_code != "model_capability_unrated"
            ):
                raise ValueError("Unrated model guidance decision is inconsistent")
            return
        if self.status not in (ModelGuidanceStatus.BELOW, ModelGuidanceStatus.MEETS):
            raise ValueError("Ruleset model guidance status is inconsistent")
        if self.placement_tier_id is None or self.placement_evidence_sha256 is None:
            raise ValueError("Rated model guidance evidence is incomplete")
        placement = validate_capability_tier_id(self.placement_tier_id)
        evidence = validate_authority_identity(
            self.placement_evidence_sha256,
            "Model capability placement evidence SHA-256",
            sha256=True,
        )
        object.__setattr__(self, "placement_tier_id", placement)
        object.__setattr__(self, "placement_evidence_sha256", evidence)
        below = _TIERS_BY_ID[placement].rank < _TIERS_BY_ID[minimum].rank
        expected_status = ModelGuidanceStatus.BELOW if below else ModelGuidanceStatus.MEETS
        expected_warning = "model_below_ruleset_minimum" if below else None
        if self.status is not expected_status or self.warning_code != expected_warning:
            raise ValueError("Rated model guidance comparison is inconsistent")
        if self.override_accepted and not below:
            raise ValueError("Model guidance override is not applicable")

    @property
    def warning_required(self) -> bool:
        return self.status in (ModelGuidanceStatus.BELOW, ModelGuidanceStatus.UNRATED)

    @property
    def may_continue(self) -> bool:
        return not self.warning_required or self.override_accepted


class ModelCapabilityRegistry:
    """Immutable exact-model registry used by owner-side guidance decisions."""

    def __init__(self, placements: tuple[ModelCapabilityPlacement, ...]) -> None:
        by_model: dict[str, ModelCapabilityPlacement] = {}
        for placement in placements:
            if placement.model_id in by_model:
                raise ValueError("Model capability registry contains a duplicate model identity")
            by_model[placement.model_id] = placement
        self._placements = MappingProxyType(by_model)

    def placement(self, model_id: str) -> ModelCapabilityPlacement | None:
        exact_model_id = validate_authority_identity(model_id, "Model identity")
        return self._placements.get(exact_model_id)

    def assess(
        self,
        *,
        model_id: str,
        minimum_tier_id: str | None,
        override_accepted: bool,
    ) -> ModelGuidanceDecision:
        exact_model_id = validate_authority_identity(model_id, "Model identity")
        if not isinstance(override_accepted, bool):
            raise ValueError("Model guidance override must be a boolean")
        if minimum_tier_id is None:
            return ModelGuidanceDecision(
                registry_version=MODEL_CAPABILITY_REGISTRY_VERSION,
                model_id=exact_model_id,
                minimum_tier_id=None,
                placement_tier_id=None,
                placement_evidence_sha256=None,
                status=ModelGuidanceStatus.NOT_APPLICABLE,
                warning_code=None,
                override_accepted=False,
            )

        exact_minimum = validate_capability_tier_id(minimum_tier_id)
        placement = self._placements.get(exact_model_id)
        if placement is None:
            status = ModelGuidanceStatus.UNRATED
            warning_code = "model_capability_unrated"
            placement_tier_id = None
            placement_evidence_sha256 = None
        elif _TIERS_BY_ID[placement.tier_id].rank < _TIERS_BY_ID[exact_minimum].rank:
            status = ModelGuidanceStatus.BELOW
            warning_code = "model_below_ruleset_minimum"
            placement_tier_id = placement.tier_id
            placement_evidence_sha256 = placement.evidence_sha256
        else:
            status = ModelGuidanceStatus.MEETS
            warning_code = None
            placement_tier_id = placement.tier_id
            placement_evidence_sha256 = placement.evidence_sha256

        warning_required = status in (ModelGuidanceStatus.BELOW, ModelGuidanceStatus.UNRATED)
        return ModelGuidanceDecision(
            registry_version=MODEL_CAPABILITY_REGISTRY_VERSION,
            model_id=exact_model_id,
            minimum_tier_id=exact_minimum,
            placement_tier_id=placement_tier_id,
            placement_evidence_sha256=placement_evidence_sha256,
            status=status,
            warning_code=warning_code,
            override_accepted=override_accepted and warning_required,
        )


EMPTY_MODEL_CAPABILITY_REGISTRY: Final = ModelCapabilityRegistry(())


def model_guidance_payload(decision: ModelGuidanceDecision) -> dict[str, object]:
    """Return the canonical safe payload persisted with a handoff."""

    return {
        "minimum_tier_id": decision.minimum_tier_id,
        "model_id": decision.model_id,
        "override_accepted": decision.override_accepted,
        "placement_evidence_sha256": decision.placement_evidence_sha256,
        "placement_tier_id": decision.placement_tier_id,
        "registry_version": decision.registry_version,
        "status": decision.status.value,
        "warning_code": decision.warning_code,
    }


def model_guidance_from_payload(value: object) -> ModelGuidanceDecision:
    """Reconstruct and validate one exact persisted guidance decision."""

    if not isinstance(value, Mapping) or set(value) != {
        "minimum_tier_id",
        "model_id",
        "override_accepted",
        "placement_evidence_sha256",
        "placement_tier_id",
        "registry_version",
        "status",
        "warning_code",
    }:
        raise ValueError("Stored model guidance payload is invalid")
    nullable_text = (
        "minimum_tier_id",
        "placement_evidence_sha256",
        "placement_tier_id",
        "warning_code",
    )
    if any(value[key] is not None and not isinstance(value[key], str) for key in nullable_text):
        raise ValueError("Stored model guidance payload is invalid")
    if (
        not isinstance(value["registry_version"], str)
        or not isinstance(value["model_id"], str)
        or not isinstance(value["status"], str)
        or not isinstance(value["override_accepted"], bool)
    ):
        raise ValueError("Stored model guidance payload is invalid")
    try:
        status = ModelGuidanceStatus(value["status"])
    except ValueError as error:
        raise ValueError("Stored model guidance status is invalid") from error
    return ModelGuidanceDecision(
        registry_version=value["registry_version"],
        model_id=value["model_id"],
        minimum_tier_id=value["minimum_tier_id"],
        placement_tier_id=value["placement_tier_id"],
        placement_evidence_sha256=value["placement_evidence_sha256"],
        status=status,
        warning_code=value["warning_code"],
        override_accepted=value["override_accepted"],
    )


def model_guidance_payload_sha256(decision: ModelGuidanceDecision) -> str:
    """Bind every durable decision input and user override to one identity."""

    return hashlib.sha256(_canonical_bytes(model_guidance_payload(decision))).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
