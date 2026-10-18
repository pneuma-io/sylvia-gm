"""Copy a Plan's authored game state and place the selected party in its declared state location."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from sylvia_gm.domain.authority import JsonValue, StateValue
from sylvia_gm.domain.campaign_plan import ValidatedCampaignPlan
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import canonical_json_bytes


def _bind_placement(value: object, participant_id: str, name: str) -> object:
    """Substitute only explicit participant references in admitted placement data."""
    if isinstance(value, Mapping):
        if set(value) == {"$participant"}:
            return participant_id if value["$participant"] == "id" else name
        return {key: _bind_placement(item, participant_id, name) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_bind_placement(item, participant_id, name) for item in value]
    return value


def _place_party(world, initial_state, party_entities, party_names):
    """Apply ordered Plan slots, preserving world entries and the saved character sources."""
    slots = initial_state.get("party_placements")
    entities = {
        identity: json.loads(canonical_json_bytes(entity))
        for identity, entity in party_entities.items()
    }
    if slots is None:
        return entities
    if len(slots) < len(entities):
        raise CommandRejected("The Plan has too few starting placements for the selected party")
    for (participant_id, entity), slot in zip(entities.items(), slots, strict=False):
        if not isinstance(entity, dict):
            raise CommandRejected("The selected character's game state must be an object")
        name = party_names.get(participant_id, participant_id)
        bound = _bind_placement(slot, participant_id, name)
        entity.update(bound["entity_state"])
        for entry in bound["append"]:
            fields = entry["key"].split(".")
            current = world.get((entry["namespace"], fields[0]))
            for field in fields[1:]:
                current = current.get(field) if isinstance(current, dict) else None
            if not isinstance(current, list):
                raise CommandRejected("A Plan party placement must append to an authored list")
            new = entry["value"]
            if (
                isinstance(new, dict)
                and "id" in new
                and any(isinstance(item, dict) and item.get("id") == new["id"] for item in current)
            ) or new in current:
                raise CommandRejected(
                    "The Plan's world and party placements have conflicting identities"
                )
            current.append(new)
    return entities


def materialize_starting_world(
    plan: ValidatedCampaignPlan,
    *,
    entity_reference: Mapping[str, object] | None,
    party_entities: Mapping[str, JsonValue],
    party_names: Mapping[str, str] | None = None,
) -> tuple[StateValue, ...]:
    """Preserve world actors and adjacent state; reject collisions instead of overwriting either."""
    world = {
        (item["namespace"], item["key"]): json.loads(canonical_json_bytes(item["value"]))
        for item in plan.payload["initial_state"].get("mechanics", ())
    }
    party_entities = _place_party(
        world,
        plan.payload["initial_state"],
        party_entities,
        party_names or {},
    )
    if plan.payload["initial_state"].get("party_placements") and entity_reference is None:
        raise CommandRejected("Party placements require a Ruleset character state location")
    if entity_reference is not None:
        fields = str(entity_reference["key"]).split(".")
        identity = (str(entity_reference["namespace"]), fields[0])
        current = world.setdefault(identity, {})
        for field in fields[1:]:
            if not isinstance(current, dict):
                raise CommandRejected("The Plan's character state location must be an object")
            current = current.setdefault(field, {})
        if not isinstance(current, dict):
            raise CommandRejected("The Plan's character state location must be an object")
        if set(current) & set(party_entities):
            raise CommandRejected("The Plan's world and selected party have conflicting identities")
        current.update(json.loads(canonical_json_bytes(party_entities)))
    return tuple(
        StateValue(namespace, key, value) for (namespace, key), value in sorted(world.items())
    )
