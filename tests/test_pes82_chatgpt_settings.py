"""Subscription discovery saves a separate connection without replacing an existing model."""

from pathlib import Path

import pytest
import yaml

from sylvia_gm.agent._provider_resolver import ConfiguredProviderResolver
from sylvia_gm.config import chatgpt_settings
from sylvia_gm.config.provider_settings import (
    discover_provider_models,
    save_provider_settings,
    set_default_model_settings,
    validate_provider_model,
)
from sylvia_gm.provider._chatgpt import ChatGPTProvider
from sylvia_gm.provider._codex_rpc import CodexConnectionError
from sylvia_gm.domain.authority import WriterRole
from sylvia_gm.domain.session import SessionRecord


class SyntheticAccount:
    def status(self) -> dict[str, object]:
        return {"signed_in": True}

    def models(self) -> list[dict[str, object]]:
        return [{"id": "gpt-5.6-sol"}, {"id": "another-advertised-model"}]


@pytest.mark.parametrize("unavailable", [False, True])
def test_ordinary_catalog_load_refreshes_legacy_effort_without_inference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unavailable: bool,
) -> None:
    path = tmp_path / "config.yaml"
    save_provider_settings(
        path, provider_id="chatgpt_subscription", preset_id="chatgpt_subscription",
        base_url="https://chatgpt.com", credential=None, model_id="gpt-5.6-sol",
        model_name="Saved subscription", context_window=262144, max_output_tokens=8192,
        transport="codex_app_server",
    )
    before = yaml.safe_load(path.read_text())
    calls = []

    class DiscoveryOnly:
        def models(self):
            calls.append("models")
            if unavailable:
                raise CodexConnectionError("Synthetic runtime unavailable")
            return [{"id": "gpt-5.6-sol", "supported_reasoning_efforts": ["low", "high", "xhigh"],
                     "default_reasoning_effort": "low"}]

    monkeypatch.setattr(chatgpt_settings, "_account", lambda _: DiscoveryOnly())
    resolver = ConfiguredProviderResolver(path)
    choice = resolver.model_choices()[0]
    assert resolver.model_choices()[0] == choice
    assert calls == ["models"]
    after = yaml.safe_load(path.read_text())
    if unavailable:
        assert choice["reasoning_mode_defaults"] == {"dm": None, "builder": None}
        assert choice["supported_reasoning_efforts"] is None
        assert after == before
    else:
        assert choice["reasoning_mode_defaults"] == {"dm": "high", "builder": "xhigh"}
        before["providers"]["chatgpt_subscription"]["models"][0].update(
            supported_reasoning_efforts=["low", "high", "xhigh"], default_reasoning_effort="low",
        )
        assert after == before
        assert ConfiguredProviderResolver(path).model_choices()[0] == choice
        assert calls == ["models"], "saved capabilities need no repeated startup discovery"


@pytest.mark.parametrize(
    "model_id,capacity,output",
    [
        ("gpt-5.6-sol", 262_144, 128_000),
        ("another-advertised-model", 400_000, 32_000),
    ],
)
def test_add_model_preserves_connection_and_default_and_resolves_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model_id: str,
    capacity: int,
    output: int,
) -> None:
    path = tmp_path / "config.yaml"
    save_provider_settings(
        path,
        provider_id="openrouter",
        preset_id="openrouter",
        base_url="https://openrouter.ai/api/v1",
        credential="synthetic-key",
        model_id="synthetic/m3",
        model_name="Existing model",
        context_window=524_288,
        max_output_tokens=8192,
        transport="chat_completions",
    )
    set_default_model_settings(path, provider_id="openrouter", model_id="openrouter/synthetic/m3")
    before = yaml.safe_load(path.read_text())
    monkeypatch.setattr(chatgpt_settings, "_account", lambda _: SyntheticAccount())
    result = chatgpt_settings.chatgpt_settings_action(
        tmp_path,
        "add_model",
        {
            "id": model_id,
            "context_window": capacity,
            "max_output_tokens": output,
        },
    )
    after = yaml.safe_load(path.read_text())
    assert after["providers"]["openrouter"] == before["providers"]["openrouter"]
    assert after["default_model"] == before["default_model"]
    assert result["saved_model"] == f"chatgpt-subscription/{model_id}"
    resolver = ConfiguredProviderResolver(path)
    model = resolver(SessionRecord(
        session_id="subscription-setup", campaign_id=None, role=WriterRole.BUILDER,
        provider_id="chatgpt_subscription", model_id=str(result["saved_model"]),
    ))
    assert isinstance(model, ChatGPTProvider)
    assert model.home == tmp_path / "chatgpt-runtime"
    assert model.model_info.wire_model_id == model_id
    assert model.model_info.configured_context_window == capacity
    assert model.model_info.max_output_tokens == output
    assert model.model_info.runtime_context_window is None
    assert model.model_info.declared_context_window is None


def test_unavailable_sol_does_not_write_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = SyntheticAccount()
    monkeypatch.setattr(account, "models", lambda: [{"id": "other-model"}])
    monkeypatch.setattr(chatgpt_settings, "_account", lambda _: account)
    with pytest.raises(CodexConnectionError, match="not advertised"):
        chatgpt_settings.chatgpt_settings_action(
            tmp_path,
            "add_model",
            {
                "id": "gpt-5.6-sol",
                "context_window": 262_144,
                "max_output_tokens": 128_000,
            },
        )
    assert not (tmp_path / "config.yaml").exists()


@pytest.mark.parametrize(
    "model",
    [
        None,
        {},
        {"id": "gpt-5.6-sol"},
        {"id": "gpt-5.6-sol", "context_window": True, "max_output_tokens": 10},
        {"id": "gpt-5.6-sol", "context_window": 100, "max_output_tokens": 100},
    ],
)
def test_missing_or_invalid_capacity_does_not_guess_sol_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model: object,
) -> None:
    monkeypatch.setattr(chatgpt_settings, "_account", lambda _: SyntheticAccount())
    with pytest.raises(CodexConnectionError):
        chatgpt_settings.chatgpt_settings_action(tmp_path, "add_model", model)
    assert not (tmp_path / "config.yaml").exists()


@pytest.mark.parametrize("validate", [False, True])
def test_subscription_cannot_use_generic_http_controls(tmp_path: Path, validate: bool) -> None:
    from sylvia_gm.config import ConfigError

    with pytest.raises(ConfigError, match="ChatGPT subscription"):
        if validate:
            validate_provider_model(
                tmp_path / "config.yaml",
                provider_id="chatgpt_subscription",
                base_url="https://chatgpt.com",
                credential="",
                model_id="gpt-5.6-sol",
                transport="codex_app_server",
            )
        else:
            discover_provider_models(
                tmp_path / "config.yaml",
                provider_id="chatgpt_subscription",
                base_url="https://chatgpt.com",
                credential="",
            )
