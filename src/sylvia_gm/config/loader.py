"""Load and merge configuration from YAML files and environment variables.

Load order (last wins):
1. Default config.yaml (shipped with the package)
2. User config file (if specified via argument)
3. Environment variables (SYLVIA_GM_ prefix)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from sylvia_gm.config._credential_protection import UNAVAILABLE_MESSAGE, unprotect_credentials
from sylvia_gm.config._env import apply_env_overrides
from sylvia_gm.config._merge import deep_merge
from sylvia_gm.config._resolver import ConfigError, resolve_vars
from sylvia_gm.config._user_settings import read_user_settings


# ---------------------------------------------------------------------------
# Default config path — resolved *at import time* relative to this file
# ---------------------------------------------------------------------------
def _default_config_path() -> Path:
    """Locate the shipped defaults in a checkout or PyInstaller bundle."""

    bundle_root = getattr(sys, "_MEIPASS", None)
    if isinstance(bundle_root, str) and bundle_root:
        return Path(bundle_root) / "config" / "config.yaml"
    return Path(__file__).resolve().parent.parent.parent.parent / "config" / "config.yaml"


_DEFAULT_CONFIG_PATH = _default_config_path()


# ============================================================================
# Pydantic config models (frozen — immutable after construction)
# ============================================================================


class AppConfig(BaseModel):
    """Application metadata."""

    model_config = ConfigDict(frozen=True)

    name: str = "Sylvia GM"
    version: str = "0.3.0-alpha.1"
    data_dir: str = "data"
    campaigns_dir: str = "campaigns"


class LoggingConfig(BaseModel):
    """Logging configuration."""

    model_config = ConfigDict(frozen=True)

    level: str = "INFO"
    format: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    file: str = "data/sylvia-gm.log"


class ProviderDef(BaseModel):
    """Per-provider configuration.

    ``mode`` discriminates between ``byok`` and ``ollama`` providers.
    Ollama ignores ``api_key`` but needs ``base_url``; byok requires ``api_key``.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    preset_id: str = ""
    mode: str = ""  # "byok" | "ollama"
    name: str = ""
    base_url: str = ""
    api_key: SecretStr = SecretStr("")
    headers: dict[str, SecretStr] = Field(default_factory=dict)
    protected_credentials: SecretStr | None = Field(default=None, repr=False, exclude=True)
    credential_overrides: SecretStr | None = Field(default=None, repr=False, exclude=True)
    credential_error: str | None = None
    models: list[dict[str, Any]] = []


def provider_credentials(definition: ProviderDef) -> tuple[str, dict[str, str]]:
    """Unlock only the selected connection; unavailable secrets never become empty requests."""
    if definition.credential_error:
        raise ConfigError(UNAVAILABLE_MESSAGE)
    if definition.protected_credentials is not None:
        try:
            envelope = json.loads(definition.protected_credentials.get_secret_value())
        except (ValueError, TypeError):
            raise ConfigError(UNAVAILABLE_MESSAGE) from None
        key, headers = unprotect_credentials(envelope)
        if definition.credential_overrides is not None:
            overrides = json.loads(definition.credential_overrides.get_secret_value())
            key = overrides.get("api_key", key)
            headers = overrides.get("headers", headers)
        return key, headers
    return definition.api_key.get_secret_value(), {
        name: value.get_secret_value() for name, value in definition.headers.items()
    }


class RulesetConfig(BaseModel):
    """Ruleset configuration."""

    model_config = ConfigDict(frozen=True)

    default: str = ""
    path: str = "rulesets"


class TTSConfig(BaseModel):
    """Text-to-speech configuration (T5.1)."""

    model_config = ConfigDict(frozen=True)

    provider: str = "edge"
    enabled: bool = False
    voice: str = "en-US-AriaNeural"
    voices: dict[str, str] = Field(
        default_factory=lambda: {
            "dm": "en-US-AriaNeural",
            "npc_female": "en-US-JennyNeural",
            "npc_male": "en-US-GuyNeural",
        }
    )


class SylviaGMConfig(BaseModel):
    """Top-level Sylvia GM configuration — frozen after loading."""

    model_config = ConfigDict(frozen=True)

    app: AppConfig = Field(default_factory=AppConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    providers: dict[str, ProviderDef] = Field(default_factory=dict)
    ruleset: RulesetConfig = Field(default_factory=RulesetConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)


# ============================================================================
# Public API
# ============================================================================


def load_config(
    user_config_path: Path | str | None = None,
    *,
    load_dotenv_file: bool = True,
    dotenv_path: Path | str | None = None,
    decrypt_credentials: bool = True,
) -> SylviaGMConfig:
    """Load and merge configuration.

    Args:
        user_config_path:
            Path to a user-provided YAML config that overrides defaults.
            Pass ``None`` to skip user config entirely.
        load_dotenv_file:
            When *True* (the default) a ``.env`` file is loaded
            before any ``${VAR}`` resolution.
        dotenv_path:
            Explicit path to a ``.env`` file.  When *None* the project-root
            ``.env`` is tried first, then CWD discovery.

    Returns:
        A frozen :class:`SylviaGMConfig` instance.

    Raises:
        FileNotFoundError: If the *default* config file is missing.
        ConfigError: If a ``${VAR}`` reference cannot be resolved or the
            assembled configuration is invalid.
    """
    # 1. Load .env file (if requested)
    if load_dotenv_file:
        _load_dotenv_safe(dotenv_path)

    # 2. Load default config YAML
    default_dict = _load_yaml(_DEFAULT_CONFIG_PATH)

    # 3. Load user config (optional)
    user_dict: dict[str, Any] = {}
    if user_config_path is not None:
        user_path = Path(user_config_path)
        if user_path.exists():
            try:
                user_dict = read_user_settings(user_path)
            except yaml.YAMLError as exc:
                raise ConfigError(f"Failed to parse user config at {user_path}: {exc}") from exc

    # 4. Deep-merge: user overrides default
    merged_dict = deep_merge(default_dict, user_dict)

    # 5. Resolve ${VAR} references
    merged_dict = resolve_vars(merged_dict)

    # 6. Apply SYLVIA_GM_ env-var overrides
    merged_dict = apply_env_overrides(merged_dict)

    # Keep protected material out of validation errors and ordinary model serialization.
    # Desktop catalog/resolver construction leaves it sealed until a selected operation.
    providers = merged_dict.get("providers", {})
    if not isinstance(providers, dict):
        raise ConfigError("Provider settings must contain a mapping")
    environment_providers = apply_env_overrides({}).get("providers", {})
    for provider_id, provider in providers.items():
        if not isinstance(provider, dict) or "protected_credentials" not in provider:
            continue
        envelope = provider["protected_credentials"]
        provider["api_key"], provider["headers"] = "", {}
        environment = (
            environment_providers.get(provider_id, {})
            if isinstance(environment_providers, dict) else {}
        )
        overrides = {
            name: environment[name] for name in ("api_key", "headers") if name in environment
        } if isinstance(environment, dict) else {}
        if ("api_key" in overrides and not isinstance(overrides["api_key"], str)) or (
            "headers" in overrides and (
                not isinstance(overrides["headers"], dict) or not all(
                    isinstance(name, str) and isinstance(value, str)
                    for name, value in overrides["headers"].items()
                )
            )
        ):
            raise ConfigError("Provider credential environment overrides must contain text values")
        provider["credential_overrides"] = SecretStr(json.dumps(overrides))
        if decrypt_credentials:
            try:
                provider["api_key"], provider["headers"] = unprotect_credentials(envelope)
                provider.update(overrides)
            except ConfigError:
                provider["credential_error"] = UNAVAILABLE_MESSAGE
        try:
            provider["protected_credentials"] = SecretStr(json.dumps(envelope))
        except (ValueError, TypeError, RecursionError):
            provider["protected_credentials"] = SecretStr("null")
            provider["credential_error"] = UNAVAILABLE_MESSAGE

    # 7. Build Pydantic model (validates, coerces types, freezes)
    try:
        config = SylviaGMConfig(**merged_dict)
    except Exception:
        raise ConfigError("Invalid configuration; check provider and model settings") from None

    return config


# ============================================================================
# Internal helpers
# ============================================================================


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file, returning a dict (or empty dict for empty files)."""
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"Config file {path} must contain a mapping, got {type(data).__name__}")
    return data


def _load_dotenv_safe(dotenv_path: Path | str | None = None) -> None:
    """Load ``.env``, ignoring missing files.

    Args:
        dotenv_path: Explicit path to a ``.env`` file.  When *None* the
            project-root ``.env`` is tried first, then ``load_dotenv()``
            discovery from ``CWD``.
    """
    try:
        if dotenv_path is not None:
            env_file = Path(dotenv_path)
            if env_file.exists():
                load_dotenv(dotenv_path=env_file)
        else:
            project_root = _DEFAULT_CONFIG_PATH.parent.parent
            env_file = project_root / ".env"
            if env_file.exists():
                load_dotenv(dotenv_path=env_file)
            else:
                load_dotenv()  # CWD discovery
    except Exception:
        pass  # .env is optional
