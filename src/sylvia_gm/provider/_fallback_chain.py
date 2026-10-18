"""Fallback chain provider that sequences multiple sub-providers.

Tries each provider in order for: rate_limit, provider_unavailable,
context_overflow, transient (timeout), and unknown errors.

EXPLICIT AUTH GUARD: AuthenticationError and InvalidResponseError
are NEVER retried or failed-over — they propagate immediately.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from sylvia_gm.provider._base import Provider, ProviderResponse, ProviderStreamChunk
from sylvia_gm.provider._exceptions import (
    AuthenticationError,
    InvalidResponseError,
    ProviderUnavailableError,
)

logger = logging.getLogger(__name__)


class FallbackChain(Provider):
    """A provider that tries a sequence of sub-providers in order.

    Configuration example::

        fallback:
          - provider: opencode_go
            transport: chat_completions
            base_url: https://opencode.ai/zen/go/v1
            credentials:
              - type: api_key
                value: "${OPENCODE_API_KEY}"
            models:
              - opencode-go/deepseek-v4
          - provider: openrouter
            transport: chat_completions
            base_url: https://openrouter.ai/api/v1
            credentials:
              - type: api_key
                value: "${OPENROUTER_API_KEY}"
            models:
              - openrouter/deepseek-v4

    Fallback decision matrix:
    - AUTH_FAILURE (401, 403): DO NOT fallback. Raise immediately.
    - INVALID_REQUEST (400, 422): DO NOT fallback. Raise immediately.
    - RATE_LIMIT (429, 529): Try next credential in pool first.
      If pool exhausted, fall back to next provider.
    - PROVIDER_UNAVAILABLE (502, 503): Fall back immediately.
    - CONTEXT_OVERFLOW: Fall back (next provider may have larger window).
    - TRANSIENT (timeout): Retry same provider once, then fall back.
    - Unknown: Fall back (safety net).

    Hook semantics:
        FallbackChain does NOT have its own HookManager. Sub-provider
        hooks fire naturally when FallbackChain calls provider.generate()
        internally. retry_attempt is passed via _retry_attempt kwarg
        (zero-based: 0 = first attempt, 1 = first fallback, etc.).
    """

    def __init__(self, providers: list[Provider]) -> None:
        """Initialise the fallback chain.

        Args:
            providers: Ordered list of sub-providers to try.
        """
        super().__init__()
        if not providers:
            raise ValueError("FallbackChain requires at least one provider")
        self._providers = providers

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        timeout: float = 60.0,
        **kwargs: object,
    ) -> ProviderResponse:
        """Try each sub-provider in order until one succeeds.

        Auth errors and invalid requests propagate immediately.
        Other errors fall through to the next provider.
        """
        last_error: Exception | None = None

        for idx, provider in enumerate(self._providers):
            try:
                return await provider.generate(
                    messages,
                    tools=tools,
                    stream=stream,
                    timeout=timeout,
                    _retry_attempt=idx,
                    **kwargs,
                )
            except AuthenticationError:
                # ─── EXPLICIT AUTH GUARD ───
                # Auth failures mean the user's key/account is bad.
                # Do NOT fallback — a bad Anthropic key doesn't mean
                # OpenRouter will work for the same user.
                raise
            except InvalidResponseError:
                # Malformed request — the request itself is broken.
                # Falling back to a different provider won't fix it.
                raise
            except Exception as exc:
                logger.warning(
                    "FallbackChain: provider %d/%d failed (%s), trying next",
                    idx + 1,
                    len(self._providers),
                    type(exc).__name__,
                )
                last_error = exc
                continue

        # All providers exhausted
        raise ProviderUnavailableError(
            f"All {len(self._providers)} providers exhausted. "
            f"Last error: {last_error}"
        ) from last_error

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        """Stream from the first available sub-provider.

        Falls back on error to next provider in chain.
        """
        last_error: Exception | None = None

        for idx, provider in enumerate(self._providers):
            try:
                async for chunk in provider.generate_stream(
                    messages,
                    tools=tools,
                    timeout=timeout,
                    _retry_attempt=idx,
                    **kwargs,
                ):
                    yield chunk
                return  # success — stream completed
            except AuthenticationError:
                raise
            except InvalidResponseError:
                raise
            except Exception as exc:
                logger.warning(
                    "FallbackChain stream: provider %d/%d failed (%s), trying next",
                    idx + 1,
                    len(self._providers),
                    type(exc).__name__,
                )
                last_error = exc
                continue

        raise ProviderUnavailableError(
            f"All {len(self._providers)} providers exhausted during stream. "
            f"Last error: {last_error}"
        ) from last_error

    async def close(self) -> None:
        """Release resources from all sub-providers."""
        for provider in self._providers:
            await provider.close()
