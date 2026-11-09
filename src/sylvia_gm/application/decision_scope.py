"""Ruleset-neutral projection of package-owned model decision scopes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from sylvia_gm.domain.authority import (
    CampaignDisplaySnapshot,
    CampaignState,
    JsonValue,
    ModelDecisionActor,
    ModelDecisionBoundary,
    ModelDecisionOwner,
    Participant,
    SafePayload,
    owned_payload,
)

_ScopeSnapshot = CampaignState | CampaignDisplaySnapshot


class DecisionScopeProjectionError(ValueError):
    """A validated declaration could not be projected from authoritative state."""


def _state_reference(
    state: Mapping[str, JsonValue], reference: Mapping[str, object]
) -> object:
    namespace = str(reference["namespace"])
    key = str(reference["key"])
    exact = f"{namespace}.{key}"
    if exact in state:
        return state[exact]
    root, separator, remainder = key.partition(".")
    current: object = state.get(f"{namespace}.{root}")
    if not separator:
        return current
    for field in remainder.split("."):
        if not isinstance(current, Mapping) or field not in current:
            return None
        current = current[field]
    return current


def _actor_name(
    actor_id: str,
    actor_state: SafePayload,
    participants: Sequence[Participant],
) -> str:
    participant = next(
        (item for item in participants if item.participant_id == actor_id),
        None,
    )
    participant_name = participant.data.get("name") if participant is not None else None
    entity_name = actor_state.get("name")
    if isinstance(participant_name, str) and participant_name.strip():
        return participant_name
    if isinstance(entity_name, str) and entity_name.strip():
        return entity_name
    return actor_id


def _actor_projection(
    actor_id: str,
    entities: Mapping[object, object],
    participants: Sequence[Participant],
) -> ModelDecisionActor:
    actor_value = entities.get(actor_id)
    if not isinstance(actor_value, Mapping):
        raise DecisionScopeProjectionError("Ruleset eligible actor state is unavailable")
    actor_state = owned_payload(actor_value)
    return ModelDecisionActor(
        actor_id=actor_id,
        actor_name=_actor_name(actor_id, actor_state, participants),
        actor_state=actor_state,
    )


def _project_side_scope(
    snapshot: _ScopeSnapshot,
    declared: Mapping[str, object],
) -> ModelDecisionBoundary | None:
    owner_value = _state_reference(
        snapshot.state,
        cast(Mapping[str, object], declared["decision_owner"]),
    )
    try:
        owner = ModelDecisionOwner(str(owner_value))
    except ValueError as error:
        raise DecisionScopeProjectionError("Ruleset decision owner is invalid") from error

    active_side = _state_reference(
        snapshot.state,
        cast(Mapping[str, object], declared["active_side"]),
    )
    if active_side is None:
        return None
    if not isinstance(active_side, str) or not active_side:
        raise DecisionScopeProjectionError("Ruleset active decision side is invalid")

    eligible_value = _state_reference(
        snapshot.state,
        cast(Mapping[str, object], declared["eligible_actors"]),
    )
    if (
        not isinstance(eligible_value, Sequence)
        or isinstance(eligible_value, (str, bytes, bytearray))
        or len(eligible_value) > 128
        or any(not isinstance(item, str) or not item for item in eligible_value)
        or len(set(eligible_value)) != len(eligible_value)
    ):
        raise DecisionScopeProjectionError("Ruleset eligible actor set is invalid")

    entities = _state_reference(
        snapshot.state,
        cast(Mapping[str, object], declared["actor_entities"]),
    )
    if not isinstance(entities, Mapping):
        raise DecisionScopeProjectionError("Ruleset decision actor state is unavailable")
    eligible_actors = tuple(
        _actor_projection(cast(str, actor_id), entities, snapshot.participants)
        for actor_id in eligible_value
    )

    focused_actor = _state_reference(
        snapshot.state,
        cast(Mapping[str, object], declared["focused_actor"]),
    )
    if focused_actor is not None and (
        not isinstance(focused_actor, str)
        or focused_actor not in cast(Sequence[str], eligible_value)
    ):
        raise DecisionScopeProjectionError("Ruleset focused actor is not eligible")
    actor_id = focused_actor
    if actor_id is None and eligible_actors:
        actor_id = eligible_actors[0].actor_id
    selected = next(
        (item for item in eligible_actors if item.actor_id == actor_id),
        None,
    )
    return ModelDecisionBoundary(
        owner=owner,
        actor_id=actor_id,
        actor_name=None if selected is None else selected.actor_name,
        actor_state=owned_payload({}) if selected is None else selected.actor_state,
        campaign_revision=snapshot.revision,
        active_side_id=active_side,
        focused_actor_id=focused_actor,
        eligible_actors=eligible_actors,
    )


def _project_legacy_actor_boundary(
    snapshot: _ScopeSnapshot,
    declared: Mapping[str, object],
) -> ModelDecisionBoundary | None:
    actor_id = _state_reference(
        snapshot.state,
        cast(Mapping[str, object], declared["active_actor"]),
    )
    if not isinstance(actor_id, str) or not actor_id:
        return None
    entities = _state_reference(
        snapshot.state,
        cast(Mapping[str, object], declared["actor_entities"]),
    )
    actor_value = entities.get(actor_id, {}) if isinstance(entities, Mapping) else {}
    actor_state = (
        owned_payload(actor_value)
        if isinstance(actor_value, Mapping)
        else owned_payload({})
    )
    control = cast(Mapping[str, object], declared["participant_control"])
    participant_id = (actor_state.get(str(control["actor_participant_field"]))
                      if "actor_participant_field" in control else actor_id)
    participant = next(
        (item for item in snapshot.participants if item.participant_id == participant_id), None,
    )
    owner = ModelDecisionOwner(str(declared["default_owner"]))
    if participant is not None and participant.data.get(str(control["field"])) in cast(
        Sequence[object], control["player_values"]
    ):
        owner = ModelDecisionOwner.PLAYER
    actor_name = _actor_name(actor_id, actor_state, snapshot.participants)
    actor = ModelDecisionActor(actor_id, actor_name, actor_state)
    return ModelDecisionBoundary(
        owner=owner,
        actor_id=actor_id,
        actor_name=actor_name,
        actor_state=actor_state,
        campaign_revision=snapshot.revision,
        focused_actor_id=actor_id,
        eligible_actors=(actor,),
    )


def project_campaign_preparation_scope(
    snapshot: _ScopeSnapshot, declared: Mapping[str, object],
) -> ModelDecisionBoundary | None:
    """Project pending package-owned character events before ordinary play decisions."""
    preparation = declared.get("preparation")
    if not isinstance(preparation, Mapping):
        return None
    actor_reference = cast(Mapping[str, object], declared["actor_entities"])
    entities = _state_reference(snapshot.state, actor_reference)
    if not isinstance(entities, Mapping):
        return None
    actors = []
    events = []
    for participant in snapshot.participants:
        if participant.package_id != actor_reference["namespace"]:
            continue
        entity = entities.get(participant.participant_id)
        if not isinstance(entity, Mapping):
            continue
        queue = entity.get(str(preparation["queue_field"]))
        if queue is None:
            continue
        if (not isinstance(queue, Sequence) or isinstance(queue, (str, bytes, bytearray))
                or len(queue) > 64 or not all(isinstance(event, Mapping) for event in queue)):
            raise DecisionScopeProjectionError("Ruleset character preparation queue is invalid")
        if not queue:
            continue
        actor = _actor_projection(participant.participant_id, entities, snapshot.participants)
        actors.append(actor)
        events.append({"actor_id": actor.actor_id, "actor_name": actor.actor_name,
                       "events": list(queue)})
    if not actors:
        return None
    first = actors[0]
    return ModelDecisionBoundary(
        owner=ModelDecisionOwner.PLAYER, actor_id=first.actor_id, actor_name=first.actor_name,
        actor_state=first.actor_state, campaign_revision=snapshot.revision,
        focused_actor_id=first.actor_id, eligible_actors=tuple(actors),
        preparation=owned_payload({**preparation, "events": events,
                                   "package_id": actor_reference["namespace"]}),
    )


def project_model_decision_scope(
    snapshot: _ScopeSnapshot,
    declared: Mapping[str, object],
) -> ModelDecisionBoundary | None:
    """Project either the side-scope declaration or its legacy actor form."""

    preparation = project_campaign_preparation_scope(snapshot, declared)
    if preparation is not None:
        return preparation
    if "eligible_actors" in declared:
        return _project_side_scope(snapshot, declared)
    return _project_legacy_actor_boundary(snapshot, declared)


def project_player_control_scope(
    snapshot: _ScopeSnapshot, declared: Mapping[str, object],
) -> SafePayload:
    """Expose controlled actor identities, without exposing the GM's actor state."""

    boundary = project_model_decision_scope(snapshot, declared)
    if boundary is not None:
        if boundary.preparation is not None:
            return owned_payload({"available": False, "focused_actor_id": None, "actors": [],
                                  "preparation": boundary.preparation})
        player_owned = boundary.owner is ModelDecisionOwner.PLAYER
        return owned_payload({
            "available": player_owned,
            "focused_actor_id": boundary.focused_actor_id if player_owned else None,
            "actors": [
                {"id": actor.actor_id, "name": actor.actor_name}
                for actor in boundary.eligible_actors
            ] if player_owned else [],
        })
    control = declared.get("participant_control")
    if isinstance(control, Mapping):
        # An inactive encounter has no active actor. The existing Ruleset role declaration still
        # identifies which party members the player controls outside that encounter.
        return owned_payload({
            "available": True, "focused_actor_id": None,
            "actors": [
                {"id": participant.participant_id,
                 "name": str(participant.data.get("name", participant.participant_id))}
                for participant in snapshot.participants
                if participant.data.get(str(control["field"])) in control["player_values"]
            ],
        })
    return owned_payload({"available": False, "focused_actor_id": None, "actors": []})


def player_viewer_ids(
    snapshot: _ScopeSnapshot, declared: Mapping[str, object] | None,
) -> frozenset[str]:
    """Identify the player's participants independently of whose turn is active.

    Viewing a participant's private presentation does not authorize acting as them.
    Only the admitted Ruleset role declaration and canonical participants select it.
    """

    control = declared.get("participant_control") if declared is not None else None
    if not isinstance(control, Mapping):
        return frozenset()
    return frozenset(
        participant.participant_id for participant in snapshot.participants
        if participant.data.get(str(control["field"])) in control["player_values"]
    )


__all__ = (
    "DecisionScopeProjectionError",
    "project_model_decision_scope",
    "project_campaign_preparation_scope",
    "project_player_control_scope",
    "player_viewer_ids",
)
