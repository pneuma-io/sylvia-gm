"""ProviderFactory — single entry point for provider construction.

Reads ProviderConfig and builds the correct provider chain:
- SingleProvider if only one endpoint configured
- FallbackChain if fallback list configured
- Always wraps a CredentialPool if multiple keys

Configured provider resolution routes through here.
"""

from __future__ import annotations

import logging

from sylvia_gm.provider._base import (
    ModelInfo,
    Provider,
    ProviderConfig,
)
from sylvia_gm.provider._credentials import Credential, CredentialPool
from sylvia_gm.provider._fallback_chain import FallbackChain

logger = logging.getLogger(__name__)


class ProviderFactory:
    """Creates provider instances from ProviderConfig.

    This is the single entry point for all provider construction.
    ProviderRegistry and configured owner-side resolution route through here.

    Usage::

        config = ProviderConfig(
            provider_key="opencode_go",
            transport="chat_completions",
            base_url="https://opencode.ai/zen/go/v1",
            credentials_list=[{"type": "api_key", "value": "sk-..."}],
            models=[model_info],
            default_model="opencode-go/deepseek-v4",
        )
        provider = ProviderFactory.create(config)
    """

    @staticmethod
    def create(config: ProviderConfig) -> Provider:
        """Build a provider chain from config.

        Returns:
        - SingleProvider if only one endpoint configured
        - FallbackChain if fallback list configured
        - The provider always wraps a CredentialPool
        """
        # 1. Build credential pool
        credentials = (
            [
                Credential(
                    type=c.get("type", "api_key"),
                    value=c.get("value", ""),
                    label=c.get("label", ""),
                )
                for c in config.credentials_list
            ]
            if config.credentials_list
            else [Credential(type="none", value="")]
        )

        pool = CredentialPool(credentials)

        # 2. Determine model info
        model_info = (
            config.models[0]
            if config.models
            else ModelInfo(
                id=config.default_model or "unknown",
                name="Unknown",
                provider_name=config.provider_key,
                provider_key=config.provider_key,
                provider_mode="byok",
                transport=config.transport,  # type: ignore[arg-type]
            )
        )

        # 3. Build the primary provider
        primary = ProviderFactory._build_single_provider(
            config=config,
            credential_pool=pool,
            model_info=model_info,
        )

        # 4. Wrap in FallbackChain if configured
        if config.fallback_providers:
            sub_providers = [ProviderFactory.create(fc) for fc in config.fallback_providers]
            return FallbackChain([primary] + sub_providers)

        return primary

    @staticmethod
    def _build_single_provider(
        config: ProviderConfig,
        credential_pool: CredentialPool,
        model_info: ModelInfo,
    ) -> Provider:
        """Build a single provider instance from transport type."""
        from sylvia_gm.provider._base import PROVIDER_RETRY_DEFAULTS

        transport = config.transport
        from sylvia_gm.provider._protocol_routes import inference_base_url

        base_url = inference_base_url(config.base_url, transport)

        if transport == "codex_app_server":
            from pathlib import Path

            from sylvia_gm.provider._chatgpt import ChatGPTProvider

            if config.provider_key != "chatgpt_subscription" or not config.runtime_home:
                raise ValueError("ChatGPT requires an owner-managed subscription profile")
            return ChatGPTProvider(Path(config.runtime_home), model_info)

        # Resolve retry config: explicit config > provider default > generic default
        retry_config = config.retry_config
        if retry_config is None:
            retry_config = PROVIDER_RETRY_DEFAULTS.get(config.provider_key)

        if transport == "gemini_generate_content":
            from sylvia_gm.provider._gemini import GeminiProvider

            return GeminiProvider(
                base_url=base_url,
                credential_pool=credential_pool,
                model_info=model_info,
                default_headers=config.default_headers,
            )
        if transport in {"chat_completions", "responses"}:
            from sylvia_gm.provider._openai_compat import OpenAICompatProvider

            return OpenAICompatProvider(
                base_url=base_url,
                credential_pool=credential_pool,
                model_info=model_info,
                retry_config=retry_config,
                default_headers=config.default_headers,
            )
        elif transport == "anthropic_messages":
            from sylvia_gm.provider._anthropic import AnthropicProvider

            return AnthropicProvider(
                base_url=base_url,
                credential_pool=credential_pool,
                model_info=model_info,
                timeout=config.timeout,
                max_tokens=config.max_tokens,
                retry_config=retry_config,
                default_headers=config.default_headers,
            )
        else:
            raise ValueError(
                f"Unknown transport '{transport}'. Supported: chat_completions, "
                "responses, anthropic_messages"
            )
