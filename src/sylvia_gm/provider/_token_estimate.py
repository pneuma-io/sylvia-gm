"""Per-model planning estimates learned from reported usage, without retaining requests."""

from __future__ import annotations

import json
import math

from sylvia_gm.provider._base import ModelInfo, Provider
from sylvia_gm.provider._hooks import HookContext


def _size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")).encode("utf-8"))


class TokenEstimateHook:
    """Keep the most conservative observed ratio for each selected route/model pair."""

    def __init__(self) -> None:
        self._bytes_per_token: dict[tuple[str, str], float] = {}

    async def on_request(self, context: HookContext) -> None:
        pass

    async def on_error(self, context: HookContext) -> None:
        pass

    async def on_response(self, context: HookContext) -> None:
        response = context.response
        if (response is None or response.usage_reported is not True
                or not context.request_json or response.usage.prompt_tokens <= 0):
            return
        key = (context.provider_key, context.model_id)
        ratio = _size(context.request_json) / response.usage.prompt_tokens
        self._bytes_per_token[key] = min(ratio, self._bytes_per_token.get(key, ratio))

    def estimate(self, value: object, model: ModelInfo | None) -> int:
        """Reserve 25% above measured usage; absent usage retains the offline estimate."""
        key = (model.provider_key, model.wire_model_id) if model else ("", "")
        ratio = self._bytes_per_token.get(key)
        return math.ceil(_size(value) / (ratio / 1.25 if ratio else 3)) + 32


def token_estimator(provider: Provider) -> TokenEstimateHook:
    """Share one estimator across wrappers and turns using the provider's existing hooks."""
    for hook in provider.hooks.registry.hooks:
        if isinstance(hook, TokenEstimateHook):
            return hook
    estimator = TokenEstimateHook()
    provider.hooks.register(estimator)
    return estimator
