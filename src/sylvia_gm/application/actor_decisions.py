"""Project a Ruleset's isolated participant decision without assembling a GM prompt."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast

from sylvia_gm.application.schema_admission import validate_parameters
from sylvia_gm.domain.authority import (
    CampaignState,
    ModelDecisionBoundary,
    ModelDecisionOwner,
    PackageRole,
    SafePayload,
    owned_payload,
    thaw_json_value,
)
from sylvia_gm.domain.errors import CommandRejected


@dataclass(frozen=True)
class ActorDecisionAction:
    identity: str
    label: str
    operation: str
    arguments: SafePayload = field(repr=False)


@dataclass(frozen=True)
class ActorDecisionRequest:
    campaign_id: str
    turn_id: str
    package_id: str
    package_version: str
    package_sha256: str
    actor_id: str
    actor_name: str
    revision: int
    decision_id: str
    instructions: str = field(repr=False)
    persona: str = field(repr=False)
    observation: SafePayload = field(repr=False)
    actions: tuple[ActorDecisionAction, ...] = field(repr=False)

    def observation_payload(self) -> dict[str, object]:
        """Expose only the immutable envelope's permitted observation and offered choices."""
        return {
            "campaign_id": self.campaign_id,
            "turn_id": self.turn_id,
            "package": {
                "id": self.package_id,
                "version": self.package_version,
                "sha256": self.package_sha256,
            },
            "actor": {"id": self.actor_id, "name": self.actor_name, "persona": self.persona},
            "revision": self.revision,
            "decision_id": self.decision_id,
            "observation": thaw_json_value(self.observation),
            "actions": [
                {
                    "id": action.identity,
                    "label": action.label,
                    "operation": action.operation,
                    "arguments": thaw_json_value(action.arguments),
                }
                for action in self.actions
            ],
        }


def _path(value: object, path: Sequence[str]) -> object:
    for key in path:
        if not isinstance(value, Mapping) or key not in value:
            raise CommandRejected("Ruleset actor observation is unavailable")
        value = value[key]
    return value


def public_actor_observation(
    snapshot: CampaignState, declaration: Mapping[str, object]
) -> SafePayload:
    """Read only the Ruleset-declared public view; never infer publicity from a private actor."""
    reference = cast(Mapping[str, str], declaration["public_observation"])
    namespace, key = reference["namespace"], reference["key"]
    exact = f"{namespace}.{key}"
    value: object
    if exact in snapshot.state:
        value = snapshot.state[exact]
    else:
        root, _, rest = key.partition(".")
        value = _path(snapshot.state.get(f"{namespace}.{root}"), rest.split(".") if rest else ())
    if not isinstance(value, Mapping):
        raise CommandRejected("Ruleset public actor observation must be an object")
    return owned_payload(value)


def project_actor_decision(
    snapshot: CampaignState,
    boundary: ModelDecisionBoundary | None,
    declaration: Mapping[str, object],
    *,
    turn_id: str,
    tool_schemas: Mapping[str, Mapping[str, object]],
) -> ActorDecisionRequest | None:
    """Bind one participant's offered operations and observation to the same authority revision."""
    if boundary is None or boundary.owner is not ModelDecisionOwner.GM:
        return None
    actor_id = boundary.actor_id
    if not isinstance(actor_id, str) or not actor_id:
        raise CommandRejected("Isolated participant requires one active actor")
    primary = next(
        entry for entry in snapshot.package_lock.entries if entry.role is PackageRole.PRIMARY
    )
    observation = _path(boundary.actor_state, cast(Sequence[str], declaration["observation_path"]))
    persona = (
        _path(boundary.actor_state, cast(Sequence[str], declaration["persona_path"]))
        if "persona_path" in declaration
        else ""
    )
    rows = _path(boundary.actor_state, cast(Sequence[str], declaration["actions_path"]))
    if (
        not isinstance(observation, Mapping)
        or not isinstance(persona, str)
        or len(persona.encode("utf-8")) > 8192
        or not isinstance(rows, Sequence)
        or isinstance(rows, (str, bytes))
        or not 1 <= len(rows) <= 256
    ):
        raise CommandRejected("Ruleset isolated participant observation or actions are invalid")
    actions = []
    identities = set()
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {"id", "label", "operation", "arguments"}:
            raise CommandRejected("Ruleset isolated participant offer is invalid")
        if (
            any(
                not isinstance(row[key], str) or not row[key] or len(row[key].encode()) > 256
                for key in ("id", "label", "operation")
            )
            or row["id"] in identities
            or row["operation"] not in tool_schemas
            or not isinstance(row["arguments"], Mapping)
        ):
            raise CommandRejected("Ruleset isolated participant operation is unavailable")
        arguments = owned_payload(row["arguments"])
        validate_parameters(arguments, tool_schemas[row["operation"]])
        actions.append(ActorDecisionAction(row["id"], row["label"], row["operation"], arguments))
        identities.add(row["id"])
    observation = owned_payload(observation)
    request = ActorDecisionRequest(
        snapshot.campaign_id,
        turn_id,
        primary.package_id,
        primary.version,
        primary.sha256,
        actor_id,
        boundary.actor_name or actor_id,
        snapshot.revision,
        "",
        cast(str, declaration["instructions"]),
        persona,
        observation,
        tuple(actions),
    )
    body = json.dumps(
        {**request.observation_payload(), "instructions": request.instructions},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    if len(body) > 128 * 1024:
        raise CommandRejected("Ruleset isolated participant envelope exceeds its bound")
    from dataclasses import replace

    identity = json.dumps(
        [
            request.campaign_id,
            request.turn_id,
            request.package_id,
            request.package_version,
            request.package_sha256,
            request.actor_id,
            request.revision,
        ],
        separators=(",", ":"),
    ).encode()
    return replace(request, decision_id=hashlib.sha256(identity).hexdigest())
