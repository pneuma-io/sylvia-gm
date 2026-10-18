"""Ingress identity for private provider output; never an ordinary session message."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderPrivateRecord:
    session_id: str
    attempt_id: str
    response_id: str
    turn_id: str
    basis_message_id: str
    source_order: int
    provider_id: str
    model_id: str
    kind: str
    payload: str = field(repr=False)
