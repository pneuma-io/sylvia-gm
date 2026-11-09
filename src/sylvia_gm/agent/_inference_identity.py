"""Opaque routing identities for application-owned inference contexts."""

import hashlib
import json
from collections.abc import Mapping


def inference_context_id(owner: Mapping[str, object], model: object) -> str:
    """Exclude request revisions and user-facing names; change on compatibility changes."""
    identity = {
        "owner": dict(owner),
        "provider": getattr(model, "provider_key", None),
        "model": getattr(model, "id", None),
        "transport": getattr(model, "transport", None),
        "wire_model": getattr(model, "wire_model_id", None),
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return "sylvia-" + hashlib.sha256(encoded).hexdigest()
