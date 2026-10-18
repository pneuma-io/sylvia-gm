"""Request admission with explicit provider-managed subscription limits."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from sylvia_gm.provider._base import (
    Provider,
    ProviderResponse,
    ProviderStreamChunk,
    closing_provider_stream,
)
from sylvia_gm.provider._exceptions import TokenLimitError
from sylvia_gm.provider._output_policy import output_allowance

TokenCountPrecision = Literal[
    "authoritative_count",
    "conservative_bound",
    "estimate",
    "unavailable",
]

GM_MODE_MINIMUM_CONTEXT_TOKENS = 131_000


@dataclass(frozen=True)
class RequestTokenMeasurement:
    """Honest preflight measurement for one exact prospective provider request."""

    precision: TokenCountPrecision
    source: str
    request_layer: str
    request_bytes: int
    request_sha256: str
    input_tokens: int | None
    input_token_bound: int | None
    output_token_reserve: int
    required_tokens: int | None
    required_token_bound: int | None
    effective_context_capacity: int | None
    capacity_source: str
    admission_decision: Literal["admitted", "rejected", "runtime_deferred"]
    provider_transformation_overhead: str
    output_budget_mode: Literal["enforced", "runtime_managed"] = "enforced"

    def projection(self) -> dict[str, object]:
        """Return the stable Audit vocabulary without relabelling a bound as a count."""

        return {
            "precision": self.precision,
            "source": self.source,
            "request_layer": self.request_layer,
            "request_bytes": self.request_bytes,
            "request_sha256": self.request_sha256,
            "input_tokens": self.input_tokens,
            "input_token_bound": self.input_token_bound,
            "output_token_reserve": self.output_token_reserve,
            "required_tokens": self.required_tokens,
            "required_token_bound": self.required_token_bound,
            "effective_context_capacity": self.effective_context_capacity,
            "capacity_source": self.capacity_source,
            "admission_decision": self.admission_decision,
            "provider_transformation_overhead": self.provider_transformation_overhead,
            "output_budget_mode": self.output_budget_mode,
            # Compatibility keys for existing diagnostic consumers. Their names remain explicit.
            "method": self.source,
            "input_token_upper_bound": self.input_token_bound,
            "required_token_upper_bound": self.required_token_bound,
            "context_window": self.effective_context_capacity,
        }


def _canonical_request_bytes(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    options: Mapping[str, object],
) -> bytes:
    """Serialize the complete Sylvia-owned request representation deterministically."""

    return json.dumps(
        {"messages": messages, "tools": tools or [], "options": dict(options)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _canonical_value_bytes(value: object) -> bytes:
    """Serialize a provider-client request representation deterministically for measurement."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _provider_capability(provider: Provider, name: str) -> object | None:
    """Find one explicit capability through Sylvia's transparent provider wrappers."""

    current: object | None = provider
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        candidate = getattr(current, name, None)
        if callable(candidate):
            return candidate
        current = getattr(current, "_provider", None)
    return None


class ContextFittingProvider(Provider):
    """Check ordinary transports locally; let Codex admit its own assembled requests."""

    def __init__(
        self,
        provider: Provider,
        *,
        minimum_context_window: int | None = None,
    ) -> None:
        super().__init__()
        self._provider = provider
        self.model_info = getattr(provider, "model_info", None)
        self.hooks = provider.hooks
        self._audit_context: dict[str, object] = {}
        self._minimum_context_window = minimum_context_window

    @property
    def runtime_managed(self) -> bool:
        """Codex owns output controls and the final input representation."""

        return getattr(self.model_info, "transport", None) == "codex_app_server"

    def set_audit_context(self, context: Mapping[str, object]) -> None:
        self._audit_context = {str(key): value for key, value in context.items()}

    def _admit(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        options: Mapping[str, object],
    ) -> RequestTokenMeasurement:
        model = self.model_info
        # Synthetic/in-process test providers intentionally have no selected wire model.
        # Configured desktop providers always carry ModelInfo from the resolver.
        if model is None:
            return RequestTokenMeasurement(
                precision="unavailable",
                source="unselected_in_process_provider",
                request_layer="neutral_application_request",
                request_bytes=0,
                request_sha256=hashlib.sha256(b"").hexdigest(),
                input_tokens=None,
                input_token_bound=None,
                output_token_reserve=0,
                required_tokens=None,
                required_token_bound=None,
                effective_context_capacity=None,
                capacity_source="unknown",
                admission_decision="admitted",
                provider_transformation_overhead="unavailable",
            )
        context_window = getattr(model, "context_window", None)
        output_limit = output_allowance(model) or 0
        valid_context_window = (
            context_window
            if isinstance(context_window, int) and context_window > 0
            else None
        )
        runtime_context_window = getattr(model, "runtime_context_window", None)
        valid_runtime_context_window = (
            runtime_context_window
            if isinstance(runtime_context_window, int)
            and not isinstance(runtime_context_window, bool)
            and runtime_context_window > 0
            else None
        )
        minimum_context_satisfied = (
            self._minimum_context_window is None
            or (
                valid_runtime_context_window is not None
                and valid_runtime_context_window >= self._minimum_context_window
                and valid_context_window is not None
                and valid_context_window >= self._minimum_context_window
            )
        )
        requested_output = options.get("max_tokens", options.get("max_output_tokens", output_limit))
        if (
            isinstance(requested_output, bool)
            or not isinstance(requested_output, int)
            or requested_output <= 0
        ):
            requested_output = output_limit
        if requested_output > output_limit and not self.runtime_managed:
            raise TokenLimitError(
                "Requested output reserve exceeds the configured output token limit. "
                "The Campaign Draft was preserved."
            )
        output_reserve = 0 if self.runtime_managed else requested_output

        preview = _provider_capability(self._provider, "provider_request_representation")
        request_layer = "neutral_application_request"
        transformation_overhead = "covered_by_neutral_utf8_structural_bound"
        if callable(preview):
            representation = preview(messages, tools, options)
            request_bytes = _canonical_value_bytes(representation)
            request_layer = "provider_client_request_representation"
            transformation_overhead = "included_in_measured_provider_representation"
        else:
            request_bytes = _canonical_request_bytes(messages, tools, options)
        if self.runtime_managed:
            transformation_overhead = "downstream_runtime_input_not_exposed"

        counter = _provider_capability(self._provider, "count_request_tokens")
        input_tokens: int | None = None
        input_upper_bound: int | None = None
        precision: TokenCountPrecision
        source: str
        if callable(counter):
            counted = counter(
                representation
                if callable(preview)
                else {"messages": messages, "tools": tools or [], "options": dict(options)}
            )
            if isinstance(counted, int) and not isinstance(counted, bool) and counted >= 0:
                input_tokens = counted
                precision = "authoritative_count"
                source = "selected_model_authoritative_counter"
            else:
                raise TokenLimitError(
                    "The selected model's token counter returned an invalid result; the request "
                    "was not sent and the Campaign Draft was preserved."
                )
        else:
            # One token per UTF-8 byte is a deliberately conservative upper bound. It is not an
            # estimate of actual usage and must never be displayed as an exact token count.
            input_upper_bound = len(request_bytes)
            precision = "conservative_bound"
            source = "conservative_utf8_upper_bound"

        required_tokens = (
            input_tokens + output_reserve if input_tokens is not None else None
        )
        required_upper_bound = (
            input_upper_bound + output_reserve if input_upper_bound is not None else None
        )
        required = required_tokens if required_tokens is not None else required_upper_bound
        decision: Literal["admitted", "rejected", "runtime_deferred"] = (
            "runtime_deferred" if self.runtime_managed or valid_context_window is None else "admitted"
            if required is not None
            and valid_context_window is not None
            and required <= valid_context_window
            and minimum_context_satisfied
            else "rejected"
        )
        measurement = RequestTokenMeasurement(
            precision=precision,
            source=source,
            request_layer=request_layer,
            request_bytes=len(request_bytes),
            request_sha256=hashlib.sha256(request_bytes).hexdigest(),
            input_tokens=input_tokens,
            input_token_bound=input_upper_bound,
            output_token_reserve=output_reserve,
            required_tokens=required_tokens,
            required_token_bound=required_upper_bound,
            effective_context_capacity=valid_context_window,
            capacity_source=str(getattr(model, "context_window_source", "unknown")),
            admission_decision=decision,
            provider_transformation_overhead=transformation_overhead,
            output_budget_mode="runtime_managed" if self.runtime_managed else "enforced",
        )
        report = {
            **measurement.projection(),
            "declared_context_window": getattr(model, "declared_context_window", None),
            "declared_context_source": getattr(model, "declared_context_source", "unknown"),
            "runtime_context_window": getattr(model, "runtime_context_window", None),
            "runtime_context_source": getattr(model, "runtime_context_source", "unknown"),
            "configured_context_window": getattr(model, "configured_context_window", None),
            "declared_context_observed_at": getattr(
                model, "declared_context_observed_at", None
            ),
            "runtime_context_observed_at": getattr(
                model, "runtime_context_observed_at", None
            ),
            "configured_context_observed_at": getattr(
                model, "configured_context_observed_at", None
            ),
            "context_observed_at": getattr(model, "context_observed_at", None),
            "minimum_context_window": self._minimum_context_window,
            "minimum_context_satisfied": minimum_context_satisfied,
        }
        recorder = _provider_capability(self._provider, "record_context_fit")
        if callable(recorder):
            recorder({**self._audit_context, "context_fit": report})
        setter = getattr(self._provider, "set_audit_context", None)
        if callable(setter):
            setter({**self._audit_context, "context_fit": report})
        self._audit_context = {}
        if not minimum_context_satisfied and self._minimum_context_window is not None:
            model_name = str(getattr(model, "name", None) or getattr(model, "id", "Unknown"))
            model_id = str(getattr(model, "id", "unknown"))
            required_label = f"{self._minimum_context_window:,}"
            if valid_runtime_context_window is None:
                detected = "its loaded runtime is unknown"
            else:
                effective_label = (
                    f"{valid_context_window:,}"
                    if valid_context_window is not None
                    else "unknown"
                )
                detected = (
                    f"it reports {valid_runtime_context_window:,} loaded-runtime tokens and "
                    f"{effective_label} effective tokens"
                )
            raise TokenLimitError(
                f"Local GM model '{model_name}' ({model_id}) cannot start or resume because "
                f"{detected}; {required_label} tokens are required. Reload a local model with "
                f"at least {required_label} context tokens. In Settings > Models, expand "
                "Connection and choose Add another model, then Find models. Select the loaded "
                "model again and use Add model, then Manual Retry. The request was not sent."
            )
        if decision == "rejected":
            label = "count" if precision == "authoritative_count" else "upper bound"
            raise TokenLimitError(
                "Complete request cannot be shown to fit the selected model "
                f"({required:,} token {label} including reply reserve; "
                f"{valid_context_window:,} available). Choose a larger-context model or reduce "
                "the current bounded answer. The Campaign Draft was preserved."
            )
        return measurement

    def admit_request(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **options: object,
    ) -> RequestTokenMeasurement:
        """Preflight a prospective request without contacting the provider."""

        return self._admit(messages, tools, options)

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        timeout: float = 60.0,
        **kwargs: object,
    ) -> ProviderResponse:
        if self.runtime_managed:
            kwargs = {key: value for key, value in kwargs.items()
                      if key not in {"max_tokens", "max_output_tokens"}}
        if (not self.runtime_managed
                and "max_tokens" not in kwargs and "max_output_tokens" not in kwargs):
            output_limit = output_allowance(self.model_info)
            if isinstance(output_limit, int) and output_limit > 0:
                kwargs = {**kwargs, "max_tokens": output_limit}
        options = {"stream": stream, "timeout": timeout, **kwargs}
        self._admit(messages, tools, options)
        return await self._provider.generate(
            messages,
            tools=tools,
            stream=stream,
            timeout=timeout,
            **kwargs,
        )

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        if self.runtime_managed:
            kwargs = {key: value for key, value in kwargs.items()
                      if key not in {"max_tokens", "max_output_tokens"}}
        if (not self.runtime_managed
                and "max_tokens" not in kwargs and "max_output_tokens" not in kwargs):
            output_limit = output_allowance(self.model_info)
            if isinstance(output_limit, int) and output_limit > 0:
                kwargs = {**kwargs, "max_tokens": output_limit}
        options = {"stream": True, "timeout": timeout, **kwargs}
        self._admit(messages, tools, options)
        async with closing_provider_stream(self._provider.generate_stream(
            messages,
            tools=tools,
            timeout=timeout,
            **kwargs,
        )) as stream:
            async for chunk in stream:
                yield chunk

    async def close(self) -> None:
        await self._provider.close()
