"""Observe Sylvia's provider boundary without editing prompts, tools or provider responses."""

from __future__ import annotations

import asyncio
import json
import re
from contextvars import ContextVar
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .contracts import Setup, digest

MAX_REQUESTS = 16
REQUEST_SECONDS = 300
LONG_RESET_SECONDS = 60


class BenchmarkRequestLimitError(RuntimeError):
    """The benchmark's logical request budget ended, independently of Sylvia or the model."""


class BenchmarkRequestTimeoutError(TimeoutError):
    """One logical provider request exceeded the benchmark's configured deadline."""


def configuration(setup: Setup) -> dict[str, Any] | None:
    """Bind selected connection/model metadata without retaining credentials or their hashes."""
    if setup.kind == "scripted":
        return None
    from sylvia_gm.config.provider_settings import provider_settings

    if setup.owner_config is None:
        raise ValueError("Live provenance requires an explicit owner configuration")
    safe = provider_settings(Path(setup.owner_config))
    provider = next((p for p in safe["configured"] if p["provider_id"] == setup.provider_id), None)
    if provider is None:
        raise ValueError("The selected benchmark provider is not enabled")
    model = next((m for m in provider["models"] if m["canonical_id"] == setup.model_id), None)
    if model is None:
        raise ValueError("The exact selected benchmark model is not configured")
    endpoint = urlsplit(provider["base_url"])
    host = endpoint.hostname or ""
    if ":" in host:
        host = "[" + host + "]"
    if endpoint.port is not None:
        host += ":" + str(endpoint.port)
    return {
        "provider_id": setup.provider_id,
        "preset_id": provider["preset_id"],
        "endpoint_without_credentials": urlunsplit((endpoint.scheme, host, endpoint.path, "", "")),
        "model": model,
    }


class ObservedProvider:
    def __init__(self, provider: Any, observer: ObservedResolver) -> None:
        self.provider = provider
        self.observer = observer

    def __getattr__(self, name: str) -> Any:
        return getattr(self.provider, name)

    def request(self, messages: Any, tools: Any, kwargs: Any) -> dict[str, Any]:
        if len(self.observer.requests) >= MAX_REQUESTS:
            error = BenchmarkRequestLimitError("SylviaBench per-run provider request limit reached")
            self.observer.failure(error)
            raise error
        calls = {c.get("id"): c for m in messages for c in m.get("tool_calls", [])}
        for message in messages:
            if message.get("role") != "tool":
                continue
            try:
                payload = json.loads(message.get("content", "{}"))
            except (ValueError, TypeError):
                continue
            if isinstance(payload, dict) and (
                payload.get("status") in {"rejected", "refused", "error"}
                or payload.get("code")
                or payload.get("error")
            ):
                record = {"call": calls.get(message.get("tool_call_id")), "refusal": payload}
                if record not in self.observer.rejections:
                    self.observer.rejections.append(record)
        tool_schemas = {
            tool.get("function", tool).get("name", str(index)): digest(tool)
            for index, tool in enumerate(tools or [])
        }
        stage = (
            "gm"
            if any(
                re.fullmatch(r"(?:tool_[0-9a-f]{12}_)?update_campaign_file", name)
                for name in tool_schemas
            )
            else "preparation"
        )
        if any(
            str(m.get("content", "")).startswith("Create a concise continuation note")
            for m in messages
        ):
            stage = "compaction"
        first_gm = stage == "gm" and not any(r["stage"] == "gm" for r in self.observer.requests)
        row = {
            "stage": stage,
            "messages_sha256": digest(messages),
            "tools_sha256": digest(tools),
            "message_count": len(messages),
            "tool_count": len(tools or []),
            "requested": {
                key: kwargs[key]
                for key in ("model_id", "max_tokens", "reasoning_effort", "temperature")
                if key in kwargs
            },
        }
        if first_gm:
            row["message_fingerprints"] = [
                {"role": message.get("role"), "sha256": digest(message)} for message in messages
            ]
            row["tool_fingerprints"] = tool_schemas
        self.observer.requests.append(row)
        return row

    @staticmethod
    def response(row: dict[str, Any], response: Any) -> None:
        # Several Sylvia adapters echo the requested ID here even when the service omits it.
        row["adapter_model"] = getattr(response, "model", None) or None
        row["observed_reasoning"] = getattr(response, "provider_reported_reasoning_effort", None)
        row["finish_reason"] = getattr(response, "finish_reason", None)
        if getattr(response, "usage_reported", None) is True:
            row["usage"] = asdict(response.usage)

    async def generate(self, messages: Any, tools: Any = None, **kwargs: Any) -> Any:
        row = self.request(messages, tools, kwargs)
        token = self.observer.active.set(row)
        try:
            timer = asyncio.timeout(self.observer.request_seconds)
            async with timer:
                response = await self.provider.generate(messages, tools=tools, **kwargs)
            self.response(row, response)
            return response
        except Exception as error:
            if isinstance(error, TimeoutError) and timer.expired():
                error = BenchmarkRequestTimeoutError("Benchmark per-request deadline reached")
            self.observer.failure(error)
            raise error
        finally:
            self.observer.active.reset(token)

    async def generate_stream(self, messages: Any, tools: Any = None, **kwargs: Any) -> Any:
        row = self.request(messages, tools, kwargs)
        token = self.observer.active.set(row)
        stream = self.provider.generate_stream(messages, tools=tools, **kwargs)
        try:
            timer = asyncio.timeout(self.observer.request_seconds)
            async with timer:
                async for chunk in stream:
                    effort = getattr(chunk, "provider_reported_reasoning_effort", None)
                    if effort is not None:
                        row["observed_reasoning"] = effort
                    if chunk.finish_reason is not None:
                        row["finish_reason"] = chunk.finish_reason
                    yield chunk
        except Exception as error:
            if isinstance(error, TimeoutError) and timer.expired():
                error = BenchmarkRequestTimeoutError("Benchmark per-request deadline reached")
            self.observer.failure(error)
            raise error
        finally:
            try:
                await stream.aclose()
            finally:
                self.observer.active.reset(token)


class ObservedResolver:
    def __init__(self, resolver: Any) -> None:
        self.resolver = resolver
        self.requests: list[dict[str, Any]] = []
        self.selections: list[dict[str, Any]] = []
        self.request_seconds = REQUEST_SECONDS
        self.failures: list[dict[str, Any]] = []
        self.rejections: list[dict[str, Any]] = []
        self.active: ContextVar[dict[str, Any] | None] = ContextVar(
            "sylviabench_provider_request", default=None
        )

    def model_choices(self) -> Any:
        return self.resolver.model_choices()

    def failure(self, error: Exception) -> None:
        """Keep safe cause metadata; never persist arbitrary provider exception messages."""
        from sylvia_gm.provider._exceptions import AuthenticationError, RateLimitError

        category = getattr(getattr(error, "category", None), "value", None)
        budget = isinstance(error, (BenchmarkRequestLimitError, BenchmarkRequestTimeoutError))
        retry_after = getattr(error, "retry_after", None)
        self.failures.append(
            {
                "type": type(error).__name__,
                "cause": "benchmark_fault"
                if budget
                else "service_failure"
                if isinstance(error, (AuthenticationError, RateLimitError))
                or category in {"provider_unavailable", "transient"}
                else "unknown",
                "retry_after_seconds": retry_after
                if isinstance(retry_after, (int, float))
                else None,
                "pause_batch": isinstance(error, RateLimitError)
                and (retry_after is None or retry_after > LONG_RESET_SECONDS),
            }
        )

    def __call__(self, session: Any) -> ObservedProvider:
        provider = self.resolver(session)
        hooks = getattr(provider, "hooks", None)
        if hooks is not None:
            hooks.register(self)
        selected = next(
            (
                row
                for row in self.model_choices()
                if row["model_id"] == session.model_id and row["provider_id"] == session.provider_id
            ),
            None,
        )
        if selected is not None and selected not in self.selections:
            self.selections.append(selected)
        return ObservedProvider(provider, self)

    async def on_request(self, context: Any) -> None:
        """Capture only the runtime's received model selection, never raw protocol content."""
        row, payload = self.active.get(), context.request_json
        if row is None or not isinstance(payload, dict):
            return
        if (
            context.provider_key == "chatgpt_subscription"
            and payload.get("direction") == "receive"
            and payload.get("method") == "thread/start/result"
        ):
            model = payload.get("params", {}).get("model")
            if isinstance(model, str) and model:
                row["confirmed_model"] = model
                row["identity_source"] = "Codex app-server thread/start result"
        if (
            context.provider_key == "claude_runtime"
            and payload.get("method") == "claude/native_step"
        ):
            row["identity_source"] = "Claude native runtime validated model ID"

    async def on_response(self, context: Any) -> None:
        """Sylvia's existing response hook includes streaming usage when the adapter reports it."""
        row = self.active.get()
        if row is not None and context.response is not None:
            ObservedProvider.response(row, context.response)
            if context.provider_key == "claude_runtime" and context.response.model:
                row["confirmed_model"] = context.response.model

    async def on_error(self, context: Any) -> None:
        if context.error is not None:
            self.failure(context.error)

    def confirmed(self, field: str) -> str | None:
        values = [r.get(field) for r in self.requests]
        if values and all(isinstance(v, str) and v for v in values) and len(set(values)) == 1:
            return values[0]
        return None

    def usage(self) -> dict[str, Any] | None:
        reported = [r["usage"] for r in self.requests if "usage" in r]
        if not reported:
            return None
        return {
            "reported_requests": len(reported),
            "requests": len(self.requests),
            "coverage": "complete" if len(reported) == len(self.requests) else "partial",
            "tokens": {
                key: sum(r[key] for r in reported)
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            },
            "cost": None,
            "note": "Adapter-reported requests only; unreported transport retries are unknown.",
        }

    def summary(self) -> dict[str, Any]:
        identities = {(r["provider_id"], r["model_id"]) for r in self.selections}
        return {
            "selections": self.selections,
            "final_selections": [
                r for r in self.model_choices() if (r["provider_id"], r["model_id"]) in identities
            ],
            "requests": self.requests,
            "max_requests": MAX_REQUESTS,
            "request_deadline_seconds": self.request_seconds,
            "failures": self.failures,
            "boundary": "Sylvia provider API, before transport serialization",
            "transport_retries": "Sylvia provider retry policy; not counted as new requests",
        }
