"""Optional, invocation-owned test cache for already validated runtime documents."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from sylvia_gm.runtime._runtime import RulesetRuntime

_CACHE_VERSION = 1
_MAX_CACHE_BYTES = 64 * 1024 * 1024


def cached_runtime(
    *,
    package_id: str,
    runtime_bytes: bytes,
    authoring: bool,
    load: Callable[[], RulesetRuntime],
) -> RulesetRuntime:
    """Reuse JSON from this test run; ordinary product loads never enable the cache."""
    configured = os.environ.get("SYLVIA_RUNTIME_CACHE_DIR")
    if not configured:
        return load()
    identity = {
        "version": _CACHE_VERSION,
        "package_id": package_id,
        "runtime_sha256": hashlib.sha256(runtime_bytes).hexdigest(),
        "authoring": authoring,
    }
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    directory = Path(configured)
    path = directory / f"{key}.json"
    try:
        if path.stat().st_size <= _MAX_CACHE_BYTES:
            document = json.loads(path.read_bytes())
            raw: Any = document["ruleset"]
            encoded = json.dumps(
                raw, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
            if (
                document["identity"] == identity
                and document["payload_sha256"] == hashlib.sha256(encoded).hexdigest()
                and isinstance(raw, dict)
            ):
                return RulesetRuntime(None, raw)
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        # A missing, corrupt or unavailable optional cache falls back to full admission.
        pass
    runtime = load()
    temporary: str | None = None
    try:
        raw = runtime._ruleset
        encoded = json.dumps(raw, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        document = json.dumps({
            "identity": identity,
            "payload_sha256": hashlib.sha256(encoded).hexdigest(),
            "ruleset": raw,
        }, separators=(",", ":"), allow_nan=False).encode()
        if len(document) <= _MAX_CACHE_BYTES:
            directory.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{key}-", suffix=".tmp", dir=directory
            )
            with os.fdopen(descriptor, "wb") as output:
                output.write(document)
            os.replace(temporary, path)
            temporary = None
    except (OSError, ValueError, TypeError, RecursionError):
        # Tests still exercise the fresh runtime if their disposable cache cannot be written.
        pass
    finally:
        if temporary is not None:
            with suppress(OSError):
                Path(temporary).unlink(missing_ok=True)
    return runtime
