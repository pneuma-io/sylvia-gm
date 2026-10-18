"""Provider registry: model resolution, provider caching, and auto-discovery.

Canonical model IDs use the format ``provider_key/model_name``
(e.g. ``opencode-go/deepseek-v4``). The registry resolves these to
a concrete Provider instance, cached by (provider_key, model_info.id).

Short names (e.g. ``\"deepseek-v4\"``) resolve only when exactly one catalog
entry matches. Ambiguous names raise ModelNotFoundError with disambiguation help.

ModelDiscovery queries provider endpoints to augment the hardcoded catalog
at runtime.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass

import httpx

from sylvia_gm.provider._base import (
    ModelInfo,
    Provider,
    ProviderConfig,
)
from sylvia_gm.provider._exceptions import ModelNotFoundError
from sylvia_gm.provider._models import build_catalog

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredModel:
    """A model discovered from a provider's /v1/models endpoint."""

    model_id: str  # provider's ID for this model
    display_name: str = ""  # human-readable
    context_window: int | None = None  # no context fact is invented by generic discovery
    supports_tools: bool = True
    supports_streaming: bool = True
    supports_reasoning: bool = False
    cost: dict[str, float] | None = None  # per 1k tokens {input, output}


class ModelDiscovery:
    """Query provider endpoints to discover available models.

    Uses GET /v1/models (OpenAI-compatible) or provider-specific
    discovery mechanisms.

    Cache behavior:
    - Cache key: (base_url, credential hash)
    - TTL: 1 hour (configurable via cache_ttl)
    - Cache hit: return cached models if not expired
    - Cache miss: query endpoint, cache result, return
    - On error: return empty list; caller falls back to hardcoded registry
    """

    def __init__(self, cache_ttl: float = 3600.0) -> None:
        self._cache: dict[str, tuple[float, list[DiscoveredModel]]] = {}
        self._cache_ttl = cache_ttl

    async def discover(
        self,
        base_url: str,
        transport: str,
        credential_value: str,
        client: httpx.AsyncClient | None = None,
    ) -> list[DiscoveredModel]:
        """Return list of available models with capabilities.

        Args:
            base_url: Provider's base URL.
            transport: "chat_completions" or "anthropic_messages".
            credential_value: API key for the request.
            client: Optional shared httpx client.
        """
        cache_key = f"{base_url}:{hash(credential_value)}"

        # Check cache
        if cache_key in self._cache:
            timestamp, models = self._cache[cache_key]
            if time.monotonic() - timestamp < self._cache_ttl:
                return models

        # Query endpoint
        try:
            if transport == "chat_completions":
                models = await self._discover_openai(base_url, client, credential_value)
            elif transport == "anthropic_messages":
                # Anthropic has no list-models endpoint
                return []
            else:
                return []

            # Cache result
            self._cache[cache_key] = (time.monotonic(), models)
            return models
        except Exception:
            # Discovery failed — caller falls back to hardcoded registry
            logger.debug("Model discovery failed for %s", base_url, exc_info=True)
            return []

    async def _discover_openai(
        self,
        base_url: str,
        client: httpx.AsyncClient | None,
        credential_value: str,
    ) -> list[DiscoveredModel]:
        """Query GET /v1/models on an OpenAI-compatible endpoint."""
        models_url = base_url.rstrip("/") + "/models"
        headers = {"Authorization": f"Bearer {credential_value}"}

        close_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=30.0)

        try:
            resp = await client.get(models_url, headers=headers)
            if resp.status_code != 200:
                return []
            data = resp.json()
            items = data.get("data", [])
            discovered = []
            for item in items:
                discovered.append(
                    DiscoveredModel(
                        model_id=item.get("id", ""),
                        display_name=item.get("id", ""),
                    )
                )
            return discovered
        finally:
            if close_client and client is not None:
                await client.aclose()

    async def invalidate(self, base_url: str, credential_value: str) -> None:
        """Force cache invalidation (e.g., after config change)."""
        cache_key = f"{base_url}:{hash(credential_value)}"
        self._cache.pop(cache_key, None)

    def merge_catalog(
        self,
        discovered: list[DiscoveredModel],
        provider_key: str,
        provider_name: str,
    ) -> list[ModelInfo]:
        """Merge discovered models into ModelInfo format."""
        result = []
        for dm in discovered:
            result.append(
                ModelInfo(
                    id=f"{provider_key}/{dm.model_id}",
                    name=dm.display_name or dm.model_id,
                    provider_name=provider_name,
                    provider_key=provider_key,
                    provider_mode="byok",
                    context_window=None,
                    declared_context_window=dm.context_window,
                    declared_context_source=(
                        "provider_metadata" if dm.context_window is not None else "unknown"
                    ),
                    context_window_source="unknown",
                )
            )
        return result


class ProviderRegistry:
    """Resolves model IDs to Provider instances.

    Provider instances are created lazily via ProviderFactory and
    cached by (provider_key, model_info.id). Config (base_url, api_key) must be
    registered before resolution.
    """

    def __init__(self, catalog: list[ModelInfo] | None = None) -> None:
        """Initialise the registry with an optional model catalog.

        Args:
            catalog: List of ModelInfo entries. An omitted or empty list uses
                     the current catalog returned by ``build_catalog()``.
        """
        self._catalog: dict[str, ModelInfo] = {}
        self._by_short_name: dict[str, list[ModelInfo]] = defaultdict(list)
        self._provider_configs: dict[str, dict[str, object]] = {}
        self._providers: dict[tuple[str, str], Provider] = {}
        self._discovery = ModelDiscovery()
        self._discovered_models: dict[str, list[ModelInfo]] = {}

        for entry in catalog or build_catalog():
            self._catalog[entry.id] = entry
            short = entry.id.split("/", 1)[-1] if "/" in entry.id else entry.id
            self._by_short_name[short].append(entry)

    # ── configuration ──────────────────────────────────────────────────

    def configure_provider(
        self,
        provider_key: str,
        base_url: str,
        api_key: str,
        headers: dict[str, str] | None = None,
        runtime_home: str = "",
    ) -> None:
        """Register connection details for a provider.

        Args:
            provider_key: Config key matching ModelInfo.provider_key
                          (e.g. ``\"opencode_go\"``, ``\"ollama\"``).
            base_url: API base URL.
            api_key: API key or pass-through (e.g. ``\"ollama\"`` for Ollama).
            headers: Optional additional request headers.
        """
        # Configuration changes must not reuse clients built with older connection
        # details, and each model has its own immutable provider identity.
        for cache_key in tuple(self._providers):
            if cache_key[0] == provider_key:
                self._providers.pop(cache_key, None)
        self._provider_configs[provider_key] = {
            "base_url": base_url,
            "api_key": api_key,
            "headers": dict(headers or {}),
            "runtime_home": runtime_home,
        }

    def configure_providers(self, configs: dict[str, dict[str, object]]) -> None:
        """Register multiple provider configs at once.

        Args:
            configs: Mapping of provider_key to connection fields. base_url is
                     required; api_key defaults to an empty string. A dict-valued
                     headers field is forwarded. Other fields, including
                     credentials and models, are ignored by this helper.
        """
        for key, cfg in configs.items():
            raw_headers = cfg.get("headers")
            self.configure_provider(
                provider_key=key,
                base_url=str(cfg["base_url"]),
                api_key=str(cfg.get("api_key", "")),
                headers=raw_headers if isinstance(raw_headers, dict) else None,
            )

    # ── resolution ─────────────────────────────────────────────────────

    def resolve(self, model_id: str) -> tuple[Provider, ModelInfo]:
        """Resolve a model ID to a (Provider, ModelInfo) pair.

        Canonical IDs (``provider_key/model_name``) are resolved directly.
        Short names (``model_name``) require exactly one catalog match.

        Args:
            model_id: Canonical or short model identifier.

        Returns:
            A tuple of (Provider instance, ModelInfo).

        Raises:
            ModelNotFoundError: If the model is not found or the short name
                                is ambiguous.
        """
        info = self._resolve_model_info(model_id)
        # T5.5: Log provider resolution
        logger.debug(
            "provider_request provider=%s model=%s",
            info.provider_key,
            model_id,
        )
        provider = self._get_or_create_provider(info.provider_key, info)
        return provider, info

    def _resolve_model_info(self, model_id: str) -> ModelInfo:
        """Resolve a model ID string to ModelInfo metadata."""
        # Try exact (canonical) match first.
        if model_id in self._catalog:
            return self._catalog[model_id]

        # Try discovered models
        for models in self._discovered_models.values():
            for m in models:
                if m.id == model_id:
                    return m

        # Try short-name match.
        candidates = self._by_short_name.get(model_id, [])
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            ids = [c.id for c in candidates]
            raise ModelNotFoundError(
                f"Ambiguous model name '{model_id}'. Use a fully qualified ID: {', '.join(ids)}"
            )
        raise ModelNotFoundError(
            f"Model '{model_id}' not found in registry. "
            f"Available models: {', '.join(sorted(self._catalog.keys()))}"
        )

    def _get_or_create_provider(self, provider_key: str, model_info: ModelInfo) -> Provider:
        """Return a cached Provider or create one via ProviderFactory.

        Routes through ProviderFactory instead of hardcoding OpenAICompatProvider.
        """
        cache_key = (provider_key, model_info.id)
        if cache_key in self._providers:
            return self._providers[cache_key]

        cfg = self._provider_configs.get(provider_key)
        if cfg is None:
            raise ModelNotFoundError(
                f"No configuration registered for provider '{provider_key}'. "
                f"Call configure_provider() first."
            )

        # Build ProviderConfig and route through factory
        from sylvia_gm.provider._factory import ProviderFactory

        configured_headers = cfg.get("headers")
        header_items = configured_headers.items() if isinstance(configured_headers, dict) else ()
        config = ProviderConfig(
            provider_key=provider_key,
            transport=model_info.transport,  # API shape selected by this model's metadata
            base_url=str(cfg["base_url"]),
            runtime_home=str(cfg.get("runtime_home", "")),
            default_headers={str(key): str(value) for key, value in header_items},
            credentials_list=[{"type": "api_key", "value": str(cfg["api_key"])}],
            models=[model_info],
            default_model=model_info.id,
        )
        provider = ProviderFactory.create(config)
        self._providers[cache_key] = provider
        return provider

    # ── auto-discovery ────────────────────────────────────────────────

    async def discover_models(
        self,
        provider_key: str,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> list[ModelInfo]:
        """Discover models from a provider endpoint and merge into catalog.

        Returns discovered models as ModelInfo. Cached with TTL.
        On failure, returns empty list (caller falls back to hardcoded catalog).
        """
        cfg = self._provider_configs.get(provider_key, {})
        url = base_url or str(cfg.get("base_url", ""))
        key = api_key or str(cfg.get("api_key", ""))

        if not url:
            logger.warning("No base URL configured for %s — cannot discover", provider_key)
            return []

        discovered = await self._discovery.discover(
            base_url=url,
            transport="chat_completions",
            credential_value=key,
        )

        if discovered:
            provider_name = str(cfg.get("provider_name", provider_key))
            models = self._discovery.merge_catalog(discovered, provider_key, provider_name)
            self._discovered_models[provider_key] = models
            return models

        return []

    # ── introspection ──────────────────────────────────────────────────

    def list_models(self, provider_key: str | None = None) -> list[ModelInfo]:
        """List all registered models, optionally filtered by provider_key."""
        if provider_key is None:
            return list(self._catalog.values())
        return [m for m in self._catalog.values() if m.provider_key == provider_key]

    def list_providers(self) -> list[str]:
        """List all configured provider keys."""
        return list(self._provider_configs.keys())

    def get_recommended(self) -> list[ModelInfo]:
        """Return models marked as recommended."""
        return [m for m in self._catalog.values() if m.recommended]

    @property
    def model_count(self) -> int:
        """Total number of registered models."""
        return len(self._catalog)
