"""Construct a provider for the configured transport."""

from __future__ import annotations

from sylvia_gm.provider._base import (
    ModelInfo,
    Provider,
    ProviderConfig,
)


class ProviderFactory:
    @staticmethod
    def create(config: ProviderConfig) -> Provider:
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

        from sylvia_gm.provider._base import PROVIDER_RETRY_DEFAULTS

        transport = config.transport
        from sylvia_gm.provider._protocol_routes import inference_base_url

        base_url = inference_base_url(config.base_url, transport)

        if transport == "claude_code":
            from pathlib import Path

            from sylvia_gm.provider._claude import ClaudeProvider

            if (config.provider_key != "claude_runtime" or not config.runtime_home
                    or config.default_headers
                    or config.api_key):
                raise ValueError(
                    "Claude requires its native runtime connection "
                    "without API credentials"
                )
            return ClaudeProvider(Path(config.runtime_home), model_info)

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
                api_key=config.api_key,
                model_info=model_info,
                default_headers=config.default_headers,
            )
        if transport in {"chat_completions", "responses"}:
            from sylvia_gm.provider._openai_compat import OpenAICompatProvider

            return OpenAICompatProvider(
                base_url=base_url,
                api_key=config.api_key,
                model_info=model_info,
                retry_config=retry_config,
                default_headers=config.default_headers,
            )
        elif transport == "anthropic_messages":
            from sylvia_gm.provider._anthropic import AnthropicProvider

            return AnthropicProvider(
                base_url=base_url,
                api_key=config.api_key,
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
