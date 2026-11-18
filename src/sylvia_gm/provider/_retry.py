"""Failover layer: retry with exponential backoff and fallback chain.

Auth errors (401, 403) are NEVER retried — they fail immediately.
Rate limit errors (429) respect Retry-After headers when present.
Connection/timeout errors retry with exponential backoff.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
from collections.abc import Awaitable, Callable
from typing import Any

from sylvia_gm.provider._base import ProviderResponse, RetryConfig
from sylvia_gm.provider._exceptions import (
    AuthenticationError,
    ProviderConnectionError,
    ProviderTimeoutError,
    RateLimitError,
)

logger = logging.getLogger(__name__)

_RETRYABLE_EXCEPTIONS = (
    RateLimitError,
    ProviderTimeoutError,
    ProviderConnectionError,
)


def _build_delay(attempt: int, config: RetryConfig, retry_after: float | None = None) -> float:
    """Calculate the delay for a given retry attempt.

    A valid server Retry-After is a minimum wait, independent of the backoff cap.
    Other errors use exponential backoff with jitter:
    base_delay * backoff_multiplier^(attempt-1) + random jitter (±12.5%).
    """
    if retry_after is not None and math.isfinite(retry_after) and retry_after >= 0:
        return retry_after

    delay = config.base_delay * (config.backoff_multiplier ** (attempt - 1))
    delay = min(delay, config.max_delay)
    # Add jitter: uniform 0–25% of the computed delay
    jitter = random.uniform(0, delay * 0.25)
    return delay + jitter


async def retry_with_backoff(
    fn: Callable[..., Awaitable[ProviderResponse]],
    retry_config: RetryConfig,
    *args: Any,
    **kwargs: Any,
) -> ProviderResponse:
    """Call `fn` with exponential-backoff retry on retryable errors.

    Auth errors (AuthenticationError) propagate immediately without retry.
    RateLimitError uses its retry_after field for the delay.
    ProviderTimeoutError and ProviderConnectionError use exponential backoff.

    Args:
        fn: Async callable returning ProviderResponse.
        retry_config: Retry parameters (max_retries, delays, etc.).
        *args, **kwargs: Forwarded to `fn`.

    Returns:
        The ProviderResponse from a successful call.

    Raises:
        AuthenticationError: Immediately (never retried).
        RateLimitError: After exhausting retries.
        ProviderTimeoutError: After exhausting retries.
        ProviderConnectionError: After exhausting retries.
    """
    last_exception: Exception | None = None

    for attempt in range(1, retry_config.max_retries + 2):  # initial + retries
        try:
            return await fn(*args, **kwargs)
        except AuthenticationError:
            # Never retry auth errors — propagate immediately.
            raise
        except RateLimitError as exc:
            last_exception = exc
            if attempt > retry_config.max_retries:
                break
            delay = _build_delay(attempt, retry_config, exc.retry_after)
            logger.warning(
                "Rate limit hit (attempt %d/%d), retrying in %.1fs",
                attempt,
                retry_config.max_retries + 1,
                delay,
            )
            await asyncio.sleep(delay)
        except (ProviderTimeoutError, ProviderConnectionError) as exc:
            last_exception = exc
            if attempt > retry_config.max_retries:
                break
            delay = _build_delay(attempt, retry_config, getattr(exc, "retry_after", None))
            logger.warning(
                "%s (attempt %d/%d), retrying in %.1fs",
                type(exc).__name__,
                attempt,
                retry_config.max_retries + 1,
                delay,
            )
            await asyncio.sleep(delay)

    # Exhausted all retries.
    assert last_exception is not None
    raise last_exception
