"""Resolve model context capacity without confusing maxima, runtime limits, and user caps."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ContextCapacitySource = Literal[
    "runtime_reported",
    "user_configured",
    "provider_metadata",
    "model_catalog",
    "legacy_unverified",
    "unknown",
]

CONTEXT_CAPACITY_SOURCES: frozenset[str] = frozenset(
    {
        "runtime_reported",
        "user_configured",
        "provider_metadata",
        "model_catalog",
        "legacy_unverified",
        "unknown",
    }
)


@dataclass(frozen=True)
class ResolvedContextCapacity:
    """Distinct context facts plus the safe capacity used for request admission."""

    declared_context_window: int | None
    declared_context_source: ContextCapacitySource
    runtime_context_window: int | None
    runtime_context_source: ContextCapacitySource
    configured_context_window: int | None
    effective_context_window: int | None
    effective_context_source: ContextCapacitySource
    declared_observed_at: str | None
    runtime_observed_at: str | None
    configured_observed_at: str | None
    configured_runtime_mismatch: bool
    configured_declared_mismatch: bool

    def projection(self) -> dict[str, object]:
        """Return a renderer/config-safe projection with compatibility aliases."""

        return {
            "declared_context_window": self.declared_context_window,
            "declared_context_source": self.declared_context_source,
            "runtime_context_window": self.runtime_context_window,
            "runtime_context_source": self.runtime_context_source,
            "configured_context_window": self.configured_context_window,
            "effective_context_window": self.effective_context_window,
            "effective_context_source": self.effective_context_source,
            "declared_context_observed_at": self.declared_observed_at,
            "runtime_context_observed_at": self.runtime_observed_at,
            "configured_context_observed_at": self.configured_observed_at,
            # Compatibility for older renderer/config records that carried one probe timestamp.
            "context_observed_at": self.runtime_observed_at or self.declared_observed_at,
            "configured_runtime_mismatch": self.configured_runtime_mismatch,
            "configured_declared_mismatch": self.configured_declared_mismatch,
            # Compatibility only. New code must name the effective value explicitly.
            "context_window": self.effective_context_window,
            "context_window_source": self.effective_context_source,
        }


def _source(value: object, *, fallback: ContextCapacitySource) -> ContextCapacitySource:
    if isinstance(value, str) and value in CONTEXT_CAPACITY_SOURCES:
        return value  # type: ignore[return-value]
    return fallback


def resolve_context_capacity(
    *,
    declared_context_window: int | None = None,
    declared_context_source: object = "unknown",
    runtime_context_window: int | None = None,
    runtime_context_source: object = "unknown",
    configured_context_window: int | None = None,
    legacy_context_window: int | None = None,
    observed_at: str | None = None,
    declared_observed_at: str | None = None,
    runtime_observed_at: str | None = None,
    configured_observed_at: str | None = None,
) -> ResolvedContextCapacity:
    """Resolve a safe effective capacity while preserving every input fact.

    A declared model maximum can clamp a known runtime/configured/legacy value, but it does not by
    itself prove how much context a local server actually allocated. A user configuration can lower
    an observed runtime limit and can provide an explicit limit when runtime discovery is
    unavailable; it can never enlarge a smaller observed or declared hard cap.
    """

    declared_source = _source(declared_context_source, fallback="unknown")
    runtime_source = _source(runtime_context_source, fallback="unknown")
    candidates: list[tuple[int, ContextCapacitySource]] = []
    if runtime_context_window is not None:
        candidates.append((runtime_context_window, runtime_source))
    if configured_context_window is not None:
        candidates.append((configured_context_window, "user_configured"))
    if not candidates and legacy_context_window is not None:
        candidates.append((legacy_context_window, "legacy_unverified"))

    effective: int | None = None
    effective_source: ContextCapacitySource = "unknown"
    if candidates:
        effective, effective_source = min(candidates, key=lambda item: item[0])
        if declared_context_window is not None and declared_context_window < effective:
            effective = declared_context_window
            effective_source = declared_source

    return ResolvedContextCapacity(
        declared_context_window=declared_context_window,
        declared_context_source=(
            declared_source if declared_context_window is not None else "unknown"
        ),
        runtime_context_window=runtime_context_window,
        runtime_context_source=(
            runtime_source if runtime_context_window is not None else "unknown"
        ),
        configured_context_window=configured_context_window,
        effective_context_window=effective,
        effective_context_source=effective_source,
        declared_observed_at=(
            declared_observed_at or observed_at
            if declared_context_window is not None
            else None
        ),
        runtime_observed_at=(
            runtime_observed_at or observed_at
            if runtime_context_window is not None
            else None
        ),
        configured_observed_at=(
            configured_observed_at if configured_context_window is not None else None
        ),
        configured_runtime_mismatch=(
            configured_context_window is not None
            and runtime_context_window is not None
            and configured_context_window > runtime_context_window
        ),
        configured_declared_mismatch=(
            configured_context_window is not None
            and declared_context_window is not None
            and configured_context_window > declared_context_window
        ),
    )
