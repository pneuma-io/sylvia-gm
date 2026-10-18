"""One atomic configuration file; protected values are private even when encrypted."""

from __future__ import annotations

import copy
import os
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from sylvia_gm.config._credential_protection import protect_credentials
from sylvia_gm.config._resolver import ConfigError

CONFIG_LOCK = threading.RLock()


def protected_document(document: Mapping[str, object]) -> dict[str, Any]:
    """Prepare all legacy or newly supplied secrets before publishing any replacement."""
    result = copy.deepcopy(dict(document))
    providers = result.get("providers")
    if not isinstance(providers, dict):
        return result
    for provider in providers.values():
        if not isinstance(provider, dict):
            continue
        key = provider.get("api_key", "")
        headers = provider.get("headers", {})
        if not isinstance(key, str) or not isinstance(headers, dict) or not all(
            isinstance(name, str) and isinstance(value, str) for name, value in headers.items()
        ):
            raise ConfigError("Provider credentials must contain text values")
        if key or headers:
            if "protected_credentials" in provider:
                raise ConfigError("Provider settings contain conflicting credential formats")
            provider["protected_credentials"] = protect_credentials(key, headers)
        if "protected_credentials" in provider:
            provider.pop("api_key", None)
            provider.pop("headers", None)
    return result


def _replace(path: Path, document: Mapping[str, object]) -> None:
    """Write only prepared protected bytes; failure retains the original file."""
    payload = yaml.safe_dump(dict(document), sort_keys=False, allow_unicode=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix="provider-settings-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def write_user_settings(path: Path, document: Mapping[str, object]) -> None:
    """Protect every new secret before creating the atomic replacement file."""
    with CONFIG_LOCK:
        try:
            _replace(path, protected_document(document))
        except (OSError, ConfigError):
            raise ConfigError(
                "Could not protect and save provider settings; the previous file is unchanged"
            ) from None


def read_user_settings(path: Path) -> dict[str, Any]:
    """Migrate legacy credentials atomically; never accept plaintext after a failed migration."""
    with CONFIG_LOCK:
        if not path.exists():
            return {}
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            raise ConfigError("The local provider settings file could not be read") from None
        if document is None:
            return {}
        if not isinstance(document, dict):
            raise ConfigError("The local provider settings file must contain a mapping")
        try:
            protected = protected_document(document)
            if protected != document:
                _replace(path, protected)
        except (OSError, ConfigError):
            raise ConfigError(
                "Saved credential migration did not complete. The original settings are unchanged; "
                "retry after Windows protection and settings-file access are available."
            ) from None
        return protected
